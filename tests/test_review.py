"""Reviewing captures through feedback events: events in, verdicts out, and what the review queue shows meanwhile."""

import json

import psycopg
import pytest

from im import extract, feedback

from helpers import table



def as_web_role(admin, sql, *args):
    """Runs SQL as the web view's role, lattice_app."""
    with admin.transaction():
        admin.execute("SET LOCAL ROLE lattice_app")
        return admin.execute(sql, args or None).fetchall() if sql.lstrip().upper().startswith("SELECT") \
            else admin.execute(sql, args or None)


def test_a_discard_hides_the_capture_now_and_becomes_a_verdict_on_the_next_run(seeded_lattice, admin):
    pipe = seeded_lattice
    queue = extract.review_items(pipe)
    assert len(queue) == 3
    (lora,) = [r[0] for r in queue if "LoRa" in r[2]]
    as_web_role(admin, """INSERT INTO pub.feedback_events (kind, item_id, payload) VALUES ('item_discard', %s, %s)""",
                lora, json.dumps({"note": "already in my notes"}))
    assert lora not in [r[0] for r in extract.review_items(pipe)] and len(extract.review_items(pipe)) == 2
    assert feedback.apply_item_feedback(pipe) == {"discard": 1}
    assert feedback.apply_item_feedback(pipe) == {}
    assert table(pipe, "SELECT verdict, note FROM im.item_verdicts WHERE item_id = %s", lora) == \
        [("discard", "already in my notes")]
    # The idea created from it had no other evidence, so it stops showing.
    assert not table(pipe, "SELECT 1 FROM pub.ideas WHERE title = 'Something new'")


def test_a_star_is_a_verdict_the_evidence_shows(seeded_lattice, admin):
    pipe = seeded_lattice
    (item,) = [r[0] for r in table(pipe, "SELECT item_id FROM im.items WHERE quote LIKE %s", "%coffee%")]
    as_web_role(admin, "INSERT INTO pub.feedback_events (kind, item_id) VALUES ('item_star', %s)", item)
    assert feedback.apply_item_feedback(pipe) == {"star": 1}
    assert table(pipe, "SELECT verdict FROM pub.evidence WHERE item_id = %s", item) == [("star",)]


def test_the_web_role_only_appends(seeded_lattice, admin):
    (item,) = [r[0] for r in table(seeded_lattice, "SELECT item_id FROM im.items LIMIT 1")]
    for sql in ["UPDATE pub.feedback_events SET kind = 'item_keep'", "DELETE FROM pub.feedback_events",
                "SELECT 1 FROM im.item_verdicts"]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            as_web_role(admin, sql)
    with pytest.raises(psycopg.errors.CheckViolation):
        as_web_role(admin, "INSERT INTO pub.feedback_events (kind) VALUES ('item_keep')")   # an item event names it
    with pytest.raises(psycopg.errors.CheckViolation):
        as_web_role(admin, "INSERT INTO pub.feedback_events (kind, item_id) VALUES ('star', %s)", item)


def test_a_resent_verdict_is_recorded_once(seeded_lattice, admin):
    pipe = seeded_lattice
    (item,) = [r[0] for r in table(pipe, "SELECT item_id FROM im.items WHERE quote LIKE %s", "%coffee%")]
    for kind in ["item_keep", "item_keep", "item_star", "item_keep"]:   # the second keep is a resend
        as_web_role(admin, "INSERT INTO pub.feedback_events (kind, item_id) VALUES (%s, %s)", kind, item)
    assert feedback.apply_item_feedback(pipe) == {"keep": 2, "repeat": 1, "star": 1}
    assert table(pipe, "SELECT verdict FROM im.item_verdicts WHERE item_id = %s ORDER BY verdict_id", item) == \
        [("keep",), ("star",), ("keep",)]


def test_review_says_how_each_verdict_was_made(seeded_lattice, admin):
    pipe = seeded_lattice
    spot, *rest = sorted(r[0] for r in extract.review_items(pipe))
    event = "INSERT INTO pub.feedback_events (kind, item_id, payload) VALUES (%s, %s, %s)"
    as_web_role(admin, event, "item_discard", spot, json.dumps({"audit": True}))
    for i in rest:
        as_web_role(admin, event, "item_keep", i, json.dumps({"bulk": True}))
    as_web_role(admin, event, "item_keep", rest[0], json.dumps({"bulk": True}))   # a resend
    as_web_role(admin, event, "item_keep", rest[0], json.dumps({}))   # then read and kept on its own: not a repeat
    assert feedback.apply_item_feedback(pipe) == {"discard": 1, "keep": len(rest) + 1, "repeat": 1}
    hows = table(pipe, "SELECT item_id, how FROM im.item_verdicts ORDER BY verdict_id")
    assert hows == [(spot, "audit"), *[(i, "bulk") for i in rest], (rest[0], "one")]
    shown = dict(as_web_role(admin, "SELECT item_id, how FROM pub.evidence WHERE item_id = ANY(%s)", [spot, *rest]))
    assert shown == {spot: "audit", rest[0]: "one", **{i: "bulk" for i in rest[1:]}}   # the latest verdict's
