"""Ideas I add myself (idea_add): there at once in pub, then embedded, related, placed and ranked on the run."""

import json
import uuid

import psycopg
import pytest

from im import digest, lattice, observations

from helpers import HashEmbedder, assert_invariants, table
from test_digest import WEEK, FakeDigester
from test_lattice import FakeMatcher
from test_review import as_web_role

ADD = "INSERT INTO pub.feedback_events (at, kind, idea_id, payload, source) VALUES (%s, 'idea_add', %s, %s, %s)"
AT = "2026-09-02T12:00:00Z"   # in the fixture week


def add(admin, idea, payload, source="emanation"):
    return as_web_role(admin, ADD, AT, idea, json.dumps(payload), source)


def test_an_added_idea_is_shown_at_once_with_its_links(seeded_lattice, admin):
    pipe = seeded_lattice
    (earlier,) = table(pipe, "SELECT idea_id FROM pub.ideas WHERE origin = 'mine' ORDER BY title LIMIT 1")[0]
    idea = uuid.uuid4()
    payload = {"title": "  Calm is a signal  ", "statement": "Others read your calm more than your words.",
               "note": "from a talk with a friend", "link_to": [str(earlier), str(uuid.uuid4()), str(idea)]}
    add(admin, idea, payload, "mcp")
    add(admin, idea, payload, "mcp")   # a resend changes nothing
    shown = as_web_role(admin, "SELECT title, origin, n_evidence, starred FROM pub.ideas WHERE idea_id = %s", idea)
    assert shown == [("Calm is a signal", "mine", 0, False)]
    assert table(pipe, "SELECT notes FROM im.ideas WHERE idea_id = %s", idea) == [("from a talk with a friend",)]
    links = as_web_role(admin, "SELECT kind FROM pub.connections WHERE %s IN (a, b)", idea)
    assert links == [("mine",)]   # the unknown id and the idea itself are skipped
    assert table(pipe, "SELECT source FROM pub.feedback_events WHERE kind = 'link'") == [("mcp",)]

    as_web_role(admin, "INSERT INTO pub.feedback_events (kind, idea_id, payload) VALUES ('idea_correct', %s, %s)",
                idea, json.dumps({"title": "Calm is what people read"}))
    assert as_web_role(admin, "SELECT title FROM pub.ideas WHERE idea_id = %s", idea) == [("Calm is what people read",)]


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
    assert table(seeded_lattice, "SELECT count(*) FROM im.ideas WHERE origin = 'mine'") == [(3,)]   # the seeded ones
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_web_role(admin, "INSERT INTO im.ideas (title, statement, origin) VALUES ('t', 's', 'mine')")


def test_the_run_embeds_places_and_digests_it(seeded_lattice, admin):
    pipe = seeded_lattice
    idea = uuid.uuid4()
    add(admin, idea, {"title": "Coffee runs the meeting", "statement": "The coffee order is the real meeting."})
    stats = lattice.match_stage(pipe, FakeMatcher(), HashEmbedder())   # no new captures
    assert stats["pending"] == 0 and stats["positions"] > 0
    assert table(pipe, "SELECT count(*) FROM pub.card_positions WHERE idea_id = %s", idea) == [(1,)]
    assert table(pipe, "SELECT count(*) FROM pub.connections WHERE kind = 'related' AND %s IN (a, b)", idea) == [(1,)]

    claude = FakeDigester()
    digest.digest_stage(pipe, claude, week=WEEK)
    text = claude.calls[0]["messages"][0]["content"]
    assert "Added by me this week (1 ideas):" in text and "Coffee runs the meeting" in text


def test_an_idea_i_add_gets_what_i_typed(pipe, admin):
    idea = uuid.uuid4()
    payload = {"title": "Calm is a signal", "statement": "Others read your calm more than your words."}
    add(admin, idea, payload)
    add(admin, idea, payload)   # a resend: still one
    assert observations.assign_stage(pipe)["typed"] == 1
    assert observations.assign_stage(pipe)["typed"] == 0
    assert table(pipe, "SELECT quote, said_by, source FROM im.observations WHERE idea_id = %s", idea) == [
        ("Others read your calm more than your words.", "me", "typed")]
    assert_invariants(pipe)
