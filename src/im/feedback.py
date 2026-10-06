"""My verdicts on captures, arriving as pub.feedback_events from Lattice (ROADMAP §3.5).

Each item_keep / item_discard / item_star event becomes a row in im.item_verdicts (the same table `im ideas
--review` writes), with the event's note. Applied events are recorded in im.feedback_applied, so each applies once.
Theme events are applied by im.themes; idea stars are read straight from the events by pub.ideas.
"""

ITEM_KINDS = {"item_keep": "keep", "item_discard": "discard", "item_star": "star"}


def apply_item_feedback(conn) -> dict:
    done: dict = {}
    rows = conn.execute(
        """SELECT f.event_id, f.kind, f.item_id, f.payload FROM pub.feedback_events f
           WHERE f.kind = ANY(%s) AND NOT EXISTS (SELECT 1 FROM im.feedback_applied a WHERE a.event_id = f.event_id)
           ORDER BY f.event_id""", (list(ITEM_KINDS),)).fetchall()
    for event_id, kind, item_id, payload in rows:
        verdict = ITEM_KINDS[kind]
        note = ((payload or {}).get("note") or "").strip()[:500] or None
        with conn.transaction():
            inserted = conn.execute(
                """INSERT INTO im.item_verdicts (item_id, segment_ids, quote, verdict, note)
                   SELECT item_id, source_segment_ids, quote, %s, %s FROM im.items WHERE item_id = %s""",
                (verdict, note, item_id)).rowcount
            result = verdict if inserted else "no such item"
            conn.execute("INSERT INTO im.feedback_applied (event_id, result) VALUES (%s, %s)", (event_id, result))
        done[result] = done.get(result, 0) + 1
    return done
