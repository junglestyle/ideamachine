"""My vocabulary for Hearsay: rare words from my corrections, my archive and misheard quotes; I approve or remove."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from im import db, vocab

from helpers import table
from test_review import as_lattice


class FakeFinder:
    """Says quote 1 of every idea mentioning 'Zigbee' misheard it as 'soil moisture'."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        text = kw["messages"][0]["content"]
        if text.startswith("Words:"):   # the archive pick: names only
            answer = {"words": [w for w in text.splitlines()[1:] if w[:1].isupper()]}
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(answer))],
                                   stop_reason="end_turn", model="claude-opus-5-5", _request_id="r",
                                   usage=SimpleNamespace(input_tokens=100, output_tokens=20))
        out = [{"idea": int(block.split(".")[0]), "term": "Zigbee", "heard_as": "soil moisture", "quote": 1}
               for block in text.split("Idea ")[1:] if "Zigbee" in block.split("\n")[0]]
        out.append({"idea": 1, "term": "Zigbee", "heard_as": "not in any quote", "quote": 1})   # refused
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({"repairs": out}))],
                               stop_reason="end_turn", model="claude-opus-5-5", _request_id="r",
                               usage=SimpleNamespace(input_tokens=400, output_tokens=60))


def vocab_rows(admin):
    return {t: (s, st, h) for t, s, h, st in as_lattice(admin, "SELECT term, source, heard_as, status FROM pub.vocab")}


def test_rare_words():
    assert vocab.rare_words("Gurnard's gnomon, moderation in sauce; GURNARD again") == ["Gurnard", "gnomon"]


def test_candidates_approval_and_the_file(seeded_lattice, admin, tmp_path):
    pipe = seeded_lattice
    pipe.execute("""INSERT INTO im.ideas (title, statement, origin, archive_ref)
                    VALUES ('Gurnard on attention', 'Self-remembering as definitionally divided.', 'archive', 'x#9')""")
    lora = table(pipe, "SELECT idea_id FROM pub.ideas WHERE title = 'Something new'")[0][0]
    pipe.execute("UPDATE im.ideas SET statement = 'Sensors could report over Zigbee.' WHERE idea_id = %s", (lora,))
    coffee = table(pipe, "SELECT idea_id FROM pub.ideas WHERE statement LIKE '%%coffee%%'")[0][0]
    as_lattice(admin, "INSERT INTO pub.feedback_events (kind, idea_id, payload) VALUES ('idea_correct', %s, %s)",
               coffee, json.dumps({"statement": "The coffee order is the real meeting, as Kolmogorov knew."}))

    claude, path = FakeFinder(), tmp_path / "vocab.json"
    stats = vocab.refresh(pipe, claude, path=str(path))
    assert stats["repair"]["repairs"] == 1 and stats["written"]
    rows = vocab_rows(admin)
    assert rows["Kolmogorov"] == ("correction", "in", None)
    assert rows["Zigbee"] == ("repair", "suggested", "soil moisture")
    assert rows["Gurnard"] == ("archive", "suggested", None) and "definitionally" not in rows   # not picked
    assert json.loads(path.read_text()) == {"format_version": 1,
                                            "terms": [{"term": "Kolmogorov", "source": "correction"}]}
    assert oct(path.stat().st_mode & 0o777) == "0o644"

    event = "INSERT INTO pub.feedback_events (kind, payload) VALUES (%s, %s)"
    as_lattice(admin, event, "vocab_add", json.dumps({"term": "zigbee", "source": "repair"}))
    as_lattice(admin, event, "vocab_remove", json.dumps({"term": "GURNARD"}))
    as_lattice(admin, event, "vocab_add", json.dumps({"term": "Bandoneon"}))
    again = vocab.refresh(pipe, claude, path=str(path))
    assert again["repair"]["asked"] == 0 and again["archive"]["asked"] is False   # checked and picked already
    rows = vocab_rows(admin)
    assert rows["zigbee"] == ("repair", "in", "soil moisture") and rows["Gurnard"][1] == "removed"
    assert rows["Bandoneon"] == ("added", "in", None)
    assert json.loads(path.read_text())["terms"] == [
        {"term": "Bandoneon", "source": "added"}, {"term": "Kolmogorov", "source": "correction"},
        {"term": "zigbee", "source": "repair", "heard_as": "soil moisture"}]
    assert vocab.refresh(pipe, claude, path=str(path))["written"] is False   # unchanged: not rewritten

    for bad in [{}, {"term": " "}, {"term": "x" * 61}, {"term": "two\nlines"}, {"term": "ok", "source": "guess"}]:
        with pytest.raises(psycopg.errors.CheckViolation):
            as_lattice(admin, event, "vocab_add", json.dumps(bad))
    with pytest.raises(psycopg.errors.CheckViolation):
        as_lattice(admin, event, "vocab_remove", json.dumps({"term": "ok", "source": "added"}))


def test_the_tests_leave_the_files_the_environment_names_alone(tmp_path):
    """A developer's shell exports the deployment's IM_* lines: `im run` in a test mustn't write Hearsay's real
    vocabulary, nor read the real config (a $5 cap here)."""
    from conftest import _dsns

    _, pipe_test, admin_dev = _dsns()
    hearsays, config = tmp_path / "vocab.json", tmp_path / "im.toml"
    hearsays.write_text(json.dumps({"format_version": 1, "terms": [{"term": "Corral", "source": "added"}]}))
    config.write_text("[extract]\nmonthly_cap_usd = 5.0\n")
    before = hearsays.read_text()
    env = os.environ | {db.DEV_ADMIN_ENV: admin_dev, db.PIPELINE_ENV: pipe_test,
                        "IM_VOCAB_PATH": str(hearsays), "IM_CONFIG": str(config)}
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_run_status.py",
                          "tests/test_extract.py::test_im_run_goes_on_without_claude"],
                         cwd=Path(__file__).parent.parent, env=env, capture_output=True, text=True)
    assert hearsays.read_text() == before
    assert out.returncode == 0 and "skipped" not in out.stdout, out.stdout
