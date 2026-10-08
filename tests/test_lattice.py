"""The idea lattice: seeding, matching captures into ideas, the pub contract, and forgetting."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import psycopg
import pytest

from im import extract, lattice

from helpers import run, table
from test_extract import FakeClaude
from test_triage import HashEmbedder

ARCHIVE = """Current idea archive:

1. “A solitary unsatisfying grape.”
   Possible associations: disproportionate disappointment.

2. “Signal may not be true, but it is always correct.”
   Theme: truth versus correctness. Connects to #1.

3. “The coffee order is the real meeting.”
   Theme: office rituals.

Important emerging clusters:

A. Models and signals:
#2, #3.
Core recurring concepts: signal versus truth.

B. Jokes:
#1.
Compact formulations.
"""
REAL_SEED = Path.home() / ".local/share/ideamachine/seeds/chatgpt-archive.md"


class FakeMatcher:
    """Decides by keyword: 'coffee' items are the same as the coffee idea, 'build' items evolve the signal
    idea, everything else is new. Records every request."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        text = kw["messages"][0]["content"]
        item, cands = text.split("Candidates:")
        numbered = {line.split(". ", 1)[1].lower(): int(line.split(".")[0]) for line in cands.strip().splitlines()}
        pick = lambda word: next(n for c, n in numbered.items() if word in c)  # noqa: E731
        if "coffee" in item.lower():
            answer = {"decision": "same_as", "candidate": pick("coffee"), "title": "", "statement": ""}
        elif "build" in item.lower():
            answer = {"decision": "evolves", "candidate": pick("signal"), "title": "Broken builds as signal",
                      "statement": "A broken build is a signal about the system."}
        else:
            answer = {"decision": "new", "candidate": 0, "title": "Something new", "statement": "A new thing."}
        content = [SimpleNamespace(type="text", text=json.dumps(answer))]
        return SimpleNamespace(content=content, stop_reason="end_turn", model="claude-opus-5-5", _request_id="r",
                               usage=SimpleNamespace(input_tokens=300, output_tokens=50))


def items_for(text):
    """Captures: the coffee line (said by anon A), the build line (me), the LoRa idea (me)."""
    out = []
    for line in text.splitlines():
        if not line.startswith("["):
            continue
        n, rest = line[1:].split("] ", 1)
        who, said = rest.split(": ", 1)
        if "coffee" in said or "build broke" in said or "LoRa" in said:
            out.append({"kind": "idea", "said_by": who, "quote": said, "lines": [int(n)], "gist": said,
                        "themes": [], "confidence": 0.6})
    return out


@pytest.fixture
def seeded(pipe, tmp_path):
    path = tmp_path / "archive.md"
    path.write_text(ARCHIVE)
    lattice.seed_archive(pipe, path)
    return pipe


def lat_setup(conn, cfg, stream):
    """Captures extracted and matched into the seeded lattice."""
    run(conn, cfg, stream)
    extract.run_stage(conn, FakeClaude(items_for))
    lattice.match_stage(conn, FakeMatcher(), HashEmbedder())
    return conn


@pytest.fixture
def seeded_lattice(seeded, cfg, stream):
    return lat_setup(seeded, cfg, stream)


def test_parse_and_seed(seeded, tmp_path):
    entries, clusters = lattice.parse_archive(ARCHIVE)
    assert [e["n"] for e in entries] == [1, 2, 3] and entries[1]["notes"].startswith("Theme: truth")
    assert [(c["letter"], c["members"]) for c in clusters] == [("A", [2, 3]), ("B", [1])]
    again = lattice.seed_archive(seeded, tmp_path / "archive.md")
    assert again["added"] == 0
    assert table(seeded, "SELECT count(*) FROM pub.ideas") == [(3,)]
    assert table(seeded, "SELECT count(*) FROM pub.connections WHERE kind = 'archive'") == [(1,)]
    assert table(seeded, "SELECT name, pinned, n_ideas FROM pub.themes ORDER BY name") == \
        [("Jokes", True, 1), ("Models and signals", True, 2)]


@pytest.mark.skipif(not REAL_SEED.exists(), reason="the real archive isn't on this machine")
def test_the_real_archive_parses():
    entries, clusters = lattice.parse_archive(REAL_SEED.read_text())
    assert len(entries) == 36 and [e["n"] for e in entries] == list(range(1, 37))
    assert [c["letter"] for c in clusters] == list("ABCDE") and len(clusters[0]["members"]) == 13


def test_matching_new_same_and_evolves(seeded, cfg, stream):
    run(seeded, cfg, stream)
    extract.run_stage(seeded, FakeClaude(items_for))
    claude = FakeMatcher()
    stats = lattice.match_stage(seeded, claude, HashEmbedder())
    assert (stats["new"], stats["same_as"], stats["evolves"]) == (1, 1, 1)
    coffee = table(seeded, """SELECT n_evidence, title FROM pub.ideas WHERE statement LIKE '%%coffee%%'""")
    assert coffee == [(1, "The coffee order is the real meeting.")]
    assert table(seeded, "SELECT said_by FROM pub.evidence ORDER BY said_by") == [("anon A",), ("me",), ("me",)]
    evolved = table(seeded, """SELECT b.statement FROM pub.connections c JOIN pub.ideas a ON a.idea_id = c.a
                               JOIN pub.ideas b ON b.idea_id = c.b WHERE c.kind = 'evolves'""")
    assert evolved == [("\u201cSignal may not be true, but it is always correct.\u201d",)]
    sent = json.dumps([c["messages"] for c in claude.calls])
    assert "anon A" not in sent and "Alice" not in sent and "said by someone else" in sent
    assert lattice.match_stage(seeded, claude, HashEmbedder())["pending"] == 0 and len(claude.calls) == 3


def test_discarded_items_are_skipped(seeded, cfg, stream):
    run(seeded, cfg, stream)
    extract.run_stage(seeded, FakeClaude(items_for))
    for (item_id,) in table(seeded, "SELECT item_id FROM im.items"):
        extract.decide(seeded, item_id, "discard")
    stats = lattice.match_stage(seeded, FakeMatcher(), HashEmbedder())
    assert stats["skipped"] == 3 and table(seeded, "SELECT count(*) FROM pub.ideas") == [(3,)]


def test_lattice_app_reads_pub_and_only_appends_feedback(seeded, admin):
    (idea,) = table(seeded, "SELECT idea_id FROM pub.ideas LIMIT 1")[0]
    with admin.transaction():
        admin.execute("SET LOCAL ROLE lattice_app")
        assert admin.execute("SELECT count(*) FROM pub.ideas").fetchone()[0] == 3
        admin.execute("INSERT INTO pub.feedback_events (kind, idea_id) VALUES ('star', %s)", (idea,))
    for sql in ["SELECT 1 FROM im.items", "SELECT 1 FROM im.ideas",
                "UPDATE pub.feedback_events SET kind = 'unstar'", "DELETE FROM pub.feedback_events"]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with admin.transaction():
                admin.execute("SET LOCAL ROLE lattice_app")
                admin.execute(sql)
    assert table(seeded, "SELECT starred FROM pub.ideas WHERE idea_id = %s", idea) == [(True,)]


def test_forgetting_removes_ideas_born_from_it_and_their_stars(seeded, cfg, stream):
    run(seeded, cfg, stream)
    extract.run_stage(seeded, FakeClaude(items_for))
    lattice.match_stage(seeded, FakeMatcher(), HashEmbedder())
    (lora,) = table(seeded, "SELECT idea_id FROM pub.ideas WHERE title = 'Something new'")[0]
    (archived,) = table(seeded, "SELECT idea_id FROM pub.ideas WHERE origin = 'archive' LIMIT 1")[0]
    for idea in (lora, archived):
        seeded.execute("INSERT INTO pub.feedback_events (kind, idea_id, source) VALUES ('star', %s, 'test')", (idea,))
    stream.forget(3600, 3612)   # the LoRa idea's line
    stream.write(stream.dir)
    run(seeded, cfg, stream)
    assert table(seeded, "SELECT count(*) FROM im.ideas WHERE idea_id = %s", lora) == [(0,)]
    assert table(seeded, "SELECT idea_id FROM pub.feedback_events") == [(archived,)]


def test_projection_holds_still_as_ideas_are_added():
    rng = np.random.default_rng(0)
    M = rng.normal(size=(40, 16)) @ np.diag(np.linspace(3, 0.2, 16))
    M /= np.linalg.norm(M, axis=1, keepdims=True)
    P0 = lattice.project(M[:30])
    assert np.abs(P0).max() == pytest.approx(1.0)
    P1 = lattice.project(M, P0, np.arange(30))
    assert (P1[:30] == P0).all() and np.abs(P1).max() <= 1.25   # placed ideas don't move at all
    for i in range(30, 40):   # a new idea lands near the placed idea most like it
        nearest = np.argmax(M[:30] @ M[i])
        assert np.linalg.norm(P1[i] - P0[nearest]) < np.median(np.linalg.norm(P0 - P0[nearest], axis=1))
    flipped = P0 * [-1, 1]   # a relayout keeps whatever orientation the map had
    assert np.abs(lattice.project(M[:30], flipped, np.arange(30), relayout=True) - flipped).max() < 1e-6
    assert lattice.project(M[:1]).tolist() == [[0.0, 0.0]]


def test_every_shown_idea_gets_a_position_lattice_can_read(seeded_lattice, admin):
    pipe = seeded_lattice
    shown = {r[0] for r in table(pipe, "SELECT idea_id FROM pub.ideas")}
    with admin.transaction():
        admin.execute("SET LOCAL ROLE lattice_app")
        pos = admin.execute("SELECT idea_id, x, y, method FROM pub.idea_positions").fetchall()
    assert {r[0] for r in pos} == shown
    assert all(abs(x) <= 1.25 and abs(y) <= 1.25 for _, x, y, _ in pos)
    assert all(m.endswith(f"embedding:{HashEmbedder().model_version}") for *_, m in pos)
    before = {r[0]: (r[1], r[2]) for r in pos}
    assert lattice.place(pipe, HashEmbedder().model_version) == len(shown)
    after = {r[0]: (r[1], r[2]) for r in table(pipe, "SELECT idea_id, x, y FROM im.idea_positions")}
    assert all(np.allclose(after[i], before[i], atol=1e-3) for i in shown)   # same ideas: the map doesn't move


def test_my_links_show_at_once_and_unlinking_removes_them(seeded, admin):
    a, b, c = sorted(r[0] for r in table(seeded, "SELECT idea_id FROM pub.ideas"))

    def lattice_does(sql, *args):
        with admin.transaction():
            admin.execute("SET LOCAL ROLE lattice_app")
            return admin.execute(sql, args or None)

    def mine():
        return lattice_does("SELECT a, b, weight, method FROM pub.connections WHERE kind = 'mine' ORDER BY a, b"
                            ).fetchall()

    link = "INSERT INTO pub.feedback_events (kind, idea_id, other_idea_id) VALUES (%s, %s, %s)"
    lattice_does(link, "link", b, a)
    lattice_does(link, "link", a, b)   # the same pair, either way round
    lattice_does(link, "link", a, c)
    assert mine() == [(a, b, 1.0, "feedback"), (a, c, 1.0, "feedback")]
    lattice_does(link, "unlink", c, a)
    assert mine() == [(a, b, 1.0, "feedback")]
    for args in [("link", a, a), ("unlink", a, None)]:
        with pytest.raises(psycopg.errors.CheckViolation):
            lattice_does(link, *args)
