"""Observations (M2, docs/design/m2-observations-candidates-ideas.md): one thing Claude derived from one stretch of
speech, outliving re-extraction.

Each run gives every new item an observation. An item can join an observation when one of its items cites a
segment the item's own segments descend from (supersession links, as `feedback.carry_verdicts` follows them). It
joins when its quote matches every capture already there (`quote_score` >= CARRY_MIN_SCORE), the best such
observation if there are several. That covers a re-read of re-transcribed or re-keyed speech, a reused read, and
Claude capturing the same line twice in one read. Otherwise the item starts a new observation.

Every capture, not any one: Claude sometimes stitches two passages into one quote with "...", and that quote
matches each passage, so matching any one capture chained two points into one observation (B5, 2026-11-01).
"""

from im import citations
from im.feedback import CARRY_MIN_SCORE, quote_score

# The segments an item's segments descend from, and the segments themselves.
_ANCESTRY = """WITH RECURSIVE a(seg) AS (
                 SELECT unnest(%s::uuid[])
                 UNION SELECT x.old_segment_id FROM a JOIN im.source_supersessions x ON x.new_segment_id = a.seg)"""


def assign_stage(conn) -> dict:
    with conn.transaction():
        # Forgetting and `im reset` delete items; an observation left with none goes too.
        dropped = conn.execute("""DELETE FROM im.observations o
                                  WHERE NOT EXISTS (SELECT 1 FROM im.items i WHERE i.observation_id = o.observation_id)
                               """).rowcount
        items = conn.execute("""SELECT item_id, quote, source_segment_ids, created_at FROM im.items
                                WHERE observation_id IS NULL ORDER BY created_at, item_id""").fetchall()
        if dropped:
            citations.uncite_missing(conn)
        stats = {"new": 0, "joined": 0, "dropped": dropped}
        for item_id, quote, segs, created_at in items:
            members: dict = {}
            for o, q in conn.execute(
                    _ANCESTRY + """
                       SELECT i.observation_id, i.quote FROM im.items i
                       WHERE i.observation_id IN (
                         SELECT c.observation_id FROM im.items c
                         WHERE c.source_segment_ids && ARRAY(SELECT seg FROM a))""", (segs,)):
                members.setdefault(o, []).append(q)
            best = max(((min(quote_score(quote, q) for q in qs), str(o), o) for o, qs in members.items()),
                       default=None)
            if best and best[0] >= CARRY_MIN_SCORE:
                observation = best[2]
                stats["joined"] += 1
            else:
                (observation,) = conn.execute(
                    """INSERT INTO im.observations (segment_ids, first_seen) VALUES (%s, %s)
                       RETURNING observation_id""", (segs, created_at)).fetchone()
                stats["new"] += 1
            conn.execute("UPDATE im.items SET observation_id = %s WHERE item_id = %s", (observation, item_id))
        if items:
            citations.cite(conn)   # a new observation can be what an edge's end was made from
    return stats
