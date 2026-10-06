"""Themes: filing ideas into my themes, proposing new ones, and applying my theme feedback."""

import json
import re
from types import SimpleNamespace

import pytest

from im import extract, lattice, themes

from helpers import run, table
from test_extract import FakeClaude
from test_lattice import ARCHIVE, FakeMatcher, items_for
from test_triage import HashEmbedder


class FakeThemer:
    """Files ideas mentioning 'signal' or 'build' into the theme named like 'signals', everything else nowhere;
    proposes one theme from the first three loose ideas."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        text = kw["messages"][0]["content"]
        if "fit no theme" in text:
            loose = re.findall(r"^(\d+)\. ", text.split("Ideas that fit no theme:")[1], re.M)
            answer = {"themes": [{"name": "Gadgets", "description": "Things to build.",
                                  "ideas": [int(n) for n in loose[:3]]},
                                 {"name": "Too small", "description": "x", "ideas": [1]}]}
        else:
            letters = dict(re.findall(r"^([A-Z])\. (.+?)(?::|$)", text.split("Ideas:")[0], re.M))
            signal = next((L for L, n in letters.items() if "signal" in n.lower()), None)
            answer = {"assignments": [{"idea": int(n), "themes": [signal] if re.search("signal|build", line, re.I) else []}
                                      for n, line in re.findall(r"^(\d+)\. (.*)$", text.split("Ideas:")[1], re.M)]}
        content = [SimpleNamespace(type="text", text=json.dumps(answer))]
        return SimpleNamespace(content=content, stop_reason="end_turn", model="claude-opus-5-5", _request_id="r",
                               usage=SimpleNamespace(input_tokens=500, output_tokens=100))


@pytest.fixture
def lat(pipe, cfg, stream, tmp_path):
    (tmp_path / "a.md").write_text(ARCHIVE)
    lattice.seed_archive(pipe, tmp_path / "a.md")
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(items_for))
    lattice.match_stage(pipe, FakeMatcher(), HashEmbedder())
    return pipe


def themed(conn, title_like):
    return [n for (n,) in table(conn, """SELECT t.name FROM im.idea_themes it JOIN im.themes t USING (theme_id)
                                         JOIN im.ideas d USING (idea_id) WHERE d.title LIKE %s ORDER BY 1""",
                                 title_like)]


def test_filing_assigns_or_records_none_and_only_reruns_when_themes_change(lat):
    claude = FakeThemer()
    stats = themes.file_stage(lat, claude)
    assert stats["pending"] == 2 and stats["assigned"] == 1 and stats["none"] == 1  # the evolved idea, the new one
    assert themed(lat, "Broken builds%") == ["Models and signals"]
    assert themes.file_stage(lat, claude)["pending"] == 0 and len(claude.calls) == 1
    lat.execute("INSERT INTO im.themes (name, origin) VALUES ('Another', 'mine')")
    assert themes.file_stage(lat, claude)["pending"] == 1  # the one that fit nothing is looked at again


def test_proposals_need_enough_loose_ideas_and_skip_small_groups(lat):
    claude = FakeThemer()
    themes.file_stage(lat, claude)
    assert themes.propose_stage(lat, claude)["proposed"] == 0  # one loose idea is not enough
    for i in range(6):
        lat.execute("""WITH d AS (INSERT INTO im.ideas (title, statement, origin, archive_ref)
                                  VALUES (%s, 'a loose idea', 'archive', %s) RETURNING idea_id)
                       INSERT INTO im.theme_checks (idea_id, themes_version, result) SELECT idea_id, 'x', 'none' FROM d""",
                    (f"Loose {i}", f"t#{i}"))
    stats = themes.propose_stage(lat, claude)
    assert stats["proposed"] == 1
    assert table(lat, "SELECT name, origin, pinned, n_ideas FROM pub.themes WHERE name = 'Gadgets'") == \
        [("Gadgets", "clustered", False, 3)]
    assert themes.propose_stage(lat, claude)["unseen"] == 0  # everything has been considered once


def test_feedback_pins_renames_and_rejects_proposals_only(lat):
    gadgets = lat.execute("INSERT INTO im.themes (name, origin) VALUES ('Gadgets', 'clustered') RETURNING theme_id"
                          ).fetchone()[0]
    archive = themes.find(lat, "models")
    themes.record(lat, "reject_theme", archive)
    themes.record(lat, "rename_theme", gadgets, {"name": "Builds and gadgets"})
    themes.record(lat, "pin_theme", gadgets)
    assert themes.apply_feedback(lat) == {"not rejected: only unpinned proposals can be": 1, "renamed": 1, "pinned": 1}
    assert themes.apply_feedback(lat) == {}
    assert table(lat, "SELECT pinned FROM im.themes WHERE name = 'Builds and gadgets'") == [(True,)]
    other = lat.execute("INSERT INTO im.themes (name, origin) VALUES ('Nope', 'clustered') RETURNING theme_id"
                        ).fetchone()[0]
    themes.record(lat, "reject_theme", other)
    assert themes.apply_feedback(lat) == {"rejected": 1}
    assert table(lat, "SELECT name FROM im.theme_rejections") == [("Nope",)]
    with pytest.raises(ValueError):
        themes.find(lat, "zzz")
