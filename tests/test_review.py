"""Reviewing captures from Lattice: feedback events in, verdicts out, and what Lattice sees meanwhile."""

import json

import psycopg
import pytest

from im import extract, feedback

from helpers import table



def as_lattice(admin, sql, *args):
    with admin.transaction():
        admin.execute("SET LOCAL ROLE lattice_app")
        return admin.execute(sql, args or None).fetchall() if sql.lstrip().upper().startswith("SELECT") \
            else admin.execute(sql, args or None)


def test_a_tap_hides_the_capture_now_and_becomes_a_verdict_on_the_next_run(seeded_lattice, admin):
    pipe = seeded_lattice
    queue = as_lattice(admin, "SELECT item_id, quote FROM pub.review_items ORDER BY confidence DESC, quote")
    assert len(queue) == 3
    (lora,) = [i for i, q in queue if "LoRa" in q]
    as_lattice(admin, """INSERT INTO pub.feedback_events (kind, item_id, payload) VALUES ('item_discard', %s, %s)""",
               lora, json.dumps({"note": "already in my notes"}))
    assert len(as_lattice(admin, "SELECT item_id FROM pub.review_items")) == 2
    assert lora not in [r[0] for r in extract.review_items(pipe)]
    assert feedback.apply_item_feedback(pipe) == {"discard": 1}
    assert feedback.apply_item_feedback(pipe) == {}
    assert table(pipe, "SELECT verdict, note FROM im.item_verdicts WHERE item_id = %s", lora) == \
        [("discard", "already in my notes")]
    # The idea created from it had no other evidence, so it stops showing.
    assert not table(pipe, "SELECT 1 FROM pub.ideas WHERE title = 'Something new'")


def test_a_star_is_a_keep_that_counts(seeded_lattice, admin):
    pipe = seeded_lattice
    (item,) = [r[0] for r in table(pipe, "SELECT item_id FROM im.items WHERE quote LIKE %s", "%coffee%")]
    as_lattice(admin, "INSERT INTO pub.feedback_events (kind, item_id) VALUES ('item_star', %s)", item)
    assert feedback.apply_item_feedback(pipe) == {"star": 1}
    assert table(pipe, "SELECT kept FROM pub.ideas WHERE statement LIKE %s", "%coffee%") == [(1,)]


def test_context_lines_surround_the_capture(seeded_lattice, admin):
    rows = as_lattice(admin, """SELECT c.ord, c.source, c.speaker, c.text FROM pub.item_context c
                                JOIN pub.review_items r USING (item_id) WHERE r.quote LIKE '%%coffee%%'
                                ORDER BY c.ord""")
    assert [r[1] for r in rows].count(True) == 1 and len(rows) == 6   # all of that short episode
    assert any(r[2] == "Alice" for r in rows)   # local names: Lattice is on the tailnet, not sent anywhere


def test_lattice_still_only_appends(seeded_lattice, admin):
    (item,) = [r[0] for r in table(seeded_lattice, "SELECT item_id FROM im.items LIMIT 1")]
    for sql in ["UPDATE pub.feedback_events SET kind = 'item_keep'", "DELETE FROM pub.feedback_events",
                "SELECT 1 FROM im.item_verdicts"]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            as_lattice(admin, sql)
    with pytest.raises(psycopg.errors.CheckViolation):
        as_lattice(admin, "INSERT INTO pub.feedback_events (kind) VALUES ('item_keep')")   # an item event names its item
    with pytest.raises(psycopg.errors.CheckViolation):
        as_lattice(admin, "INSERT INTO pub.feedback_events (kind, item_id) VALUES ('star', %s)", item)
