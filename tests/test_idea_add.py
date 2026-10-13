"""Ideas I add myself (idea_add): there at once for Lattice, then embedded, related, placed and ranked on the run."""

import json
import uuid

import psycopg
import pytest

from im import digest, lattice, scores

from helpers import table
from test_digest import WEEK, FakeDigester
from test_lattice import FakeMatcher, HashEmbedder
from test_review import as_lattice
from test_scores import FakeScorer

ADD = "INSERT INTO pub.feedback_events (at, kind, idea_id, payload, source) VALUES (%s, 'idea_add', %s, %s, %s)"
AT = "2026-09-02T12:00:00Z"   # in the fixture week


def add(admin, idea, payload, source="lattice"):
    return as_lattice(admin, ADD, AT, idea, json.dumps(payload), source)


def test_an_added_idea_is_shown_at_once_with_its_links(seeded_lattice, admin):
    pipe = seeded_lattice
    (archived,) = table(pipe, "SELECT idea_id FROM pub.ideas WHERE origin = 'archive' ORDER BY title LIMIT 1")[0]
    idea = uuid.uuid4()
    payload = {"title": "  Calm is a signal  ", "statement": "Others read your calm more than your words.",
               "note": "from a talk with a friend", "link_to": [str(archived), str(uuid.uuid4()), str(idea)]}
    add(admin, idea, payload, "mcp")
    add(admin, idea, payload, "mcp")   # a resend changes nothing
    shown = as_lattice(admin, """SELECT title, origin, notes, n_evidence, starred FROM pub.ideas
                                 WHERE idea_id = %s""", idea)
    assert shown == [("Calm is a signal", "mine", "from a talk with a friend", 0, False)]
    links = as_lattice(admin, "SELECT kind FROM pub.connections WHERE %s IN (a, b)", idea)
    assert links == [("mine",)]   # the unknown id and the idea itself are skipped
    assert table(pipe, "SELECT source FROM pub.feedback_events WHERE kind = 'link'") == [("mcp",)]

    as_lattice(admin, "INSERT INTO pub.feedback_events (kind, idea_id, payload) VALUES ('idea_correct', %s, %s)",
               idea, json.dumps({"title": "Calm is what people read"}))
    assert as_lattice(admin, "SELECT title FROM pub.ideas WHERE idea_id = %s", idea) == [("Calm is what people read",)]


def test_bad_adds_are_refused(seeded_lattice, admin):
    (captured,) = table(seeded_lattice, "SELECT idea_id FROM pub.ideas WHERE origin = 'captured' LIMIT 1")[0]
    ok = {"title": "T", "statement": "S"}
    for idea, payload in [(None, ok), (uuid.uuid4(), {"title": "T"}), (uuid.uuid4(), {"title": " ", "statement": "S"}),
                          (uuid.uuid4(), {"title": "x" * 121, "statement": "S"}),
                          (uuid.uuid4(), {**ok, "note": 5}), (uuid.uuid4(), {**ok, "link_to": "x"}),
                          (uuid.uuid4(), {**ok, "link_to": [str(uuid.uuid4())] * 11}),
                          (captured, ok)]:   # an existing idea I didn't add
        with pytest.raises(psycopg.errors.CheckViolation):
            add(admin, idea, payload)
    assert table(seeded_lattice, "SELECT count(*) FROM im.ideas WHERE origin = 'mine'") == [(0,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_lattice(admin, "INSERT INTO im.ideas (title, statement, origin) VALUES ('t', 's', 'mine')")


def test_the_run_embeds_places_ranks_and_digests_it(seeded_lattice, admin):
    pipe = seeded_lattice
    idea = uuid.uuid4()
    add(admin, idea, {"title": "Coffee runs the meeting", "statement": "The coffee order is the real meeting."})
    stats = lattice.match_stage(pipe, FakeMatcher(), HashEmbedder())   # no new captures
    assert stats["pending"] == 0 and stats["positions"] > 0
    assert table(pipe, "SELECT count(*) FROM pub.idea_positions WHERE idea_id = %s", idea) == [(1,)]
    assert table(pipe, "SELECT count(*) FROM pub.connections WHERE kind = 'related' AND %s IN (a, b)", idea) == [(1,)]

    scores.score_stage(pipe, FakeScorer())
    (score, rubric, recurrence), = table(pipe, "SELECT score::float, rubric::float, recurrence::float "
                                               "FROM pub.idea_scores WHERE idea_id = %s", idea)
    assert recurrence == 0 and score == pytest.approx(0.7 * rubric + 0.1)   # adding it counts like a star

    claude = FakeDigester()
    digest.digest_stage(pipe, claude, week=WEEK)
    text = claude.calls[0]["messages"][0]["content"]
    assert "Added by me this week (1 ideas):" in text and "Coffee runs the meeting" in text
