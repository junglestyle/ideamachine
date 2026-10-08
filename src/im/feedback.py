"""My verdicts on captures, arriving as pub.feedback_events from Lattice (ROADMAP §3.5).

Each item_keep / item_discard / item_star event becomes a row in im.item_verdicts (the same table `im ideas
--review` writes), with the event's note. Applied events are recorded in im.feedback_applied, so each applies once.
An event that repeats the item's latest verdict and note adds nothing (a resend from Lattice's offline queue).
Theme events are applied by im.themes; idea stars are read straight from the events by pub.ideas.

Verdicts are carried forward too (`carry_verdicts`): when the episode an item was on is replaced, and Claude
captures the same speech again, my verdicts are copied to the new item instead of asking me again.
"""

import difflib
import re

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
            latest = conn.execute(
                """SELECT verdict, note FROM im.item_verdicts WHERE item_id = %s
                   ORDER BY decided_at DESC, verdict_id DESC LIMIT 1""", (item_id,)).fetchone()
            if latest == (verdict, note):   # e.g. Lattice resent a queued event whose response was lost
                result = "repeat"
            else:
                inserted = conn.execute(
                    """INSERT INTO im.item_verdicts (item_id, segment_ids, quote, verdict, note)
                       SELECT item_id, source_segment_ids, quote, %s, %s FROM im.items WHERE item_id = %s""",
                    (verdict, note, item_id)).rowcount
                result = verdict if inserted else "no such item"
            conn.execute("INSERT INTO im.feedback_applied (event_id, result) VALUES (%s, %s)", (event_id, result))
        done[result] = done.get(result, 0) + 1
    return done


# --- Carrying verdicts forward ----------------------------------------------------------------------------------

CARRY_MIN_SCORE = 0.4   # calibrated on the 2026-10-06 re-transcription: same captures scored >= 0.44, others <= 0.3
CARRY_METHOD = f"segment-descent;quote-containment>={CARRY_MIN_SCORE}"
MIN_WORDS = 4
FILLERS = {"uh", "um", "er", "ah", "hmm", "mm"}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower().replace("'", "").replace("’", "")) if w not in FILLERS]


def quote_score(a: str, b: str) -> float:
    """The share of the shorter quote's words found, in order, in the other. A re-extraction often quotes more or
    less of the same speech, and re-transcription changes fillers and small words. Quotes count as at least
    MIN_WORDS long, so one shared word in a two-word quote isn't a match."""
    A, B = _words(a), _words(b)
    if not A or not B:
        return 0.0
    matched = sum(m.size for m in difflib.SequenceMatcher(None, A, B, autojunk=False).get_matching_blocks())
    return matched / max(min(len(A), len(B)), MIN_WORDS)


def carry_verdicts(conn) -> dict:
    """Copy my verdicts from items no longer on a current episode to the current items they became.

    A retired item's verdict can go to a current item whose source segments descend from the verdict's segments
    (through supersession links) and whose quote matches (`quote_score`). Matches are one-to-one, best first. A new
    item I've already decided on keeps my verdict. Either way the old item is settled and never carried again; one
    with no match yet is tried again on later runs (e.g. its episode hasn't been read by Claude yet).
    """
    rows = conn.execute(
        """SELECT v.item_id, v.verdict_id, v.segment_ids, v.quote, i.kind FROM im.item_verdicts v
           LEFT JOIN im.items i USING (item_id)
           WHERE NOT EXISTS (SELECT 1 FROM im.items c JOIN im.episodes e USING (episode_id)
                             WHERE c.item_id = v.item_id AND e.current)
             AND NOT EXISTS (SELECT 1 FROM im.verdict_carries k WHERE k.from_item_id = v.item_id)
           ORDER BY v.decided_at, v.verdict_id""").fetchall()
    # Items with the same quote and segments are one capture decided on more than once: one source, latest wins.
    groups: dict = {}
    for item_id, verdict_id, segs, quote, kind in rows:
        g = groups.setdefault((quote, tuple(sorted(segs))), {"items": set(), "verdicts": [], "kind": kind})
        g["items"].add(item_id)
        g["verdicts"].append(verdict_id)
    if not groups:
        return {"carried": 0, "already_decided": 0, "waiting": 0}
    keys = list(groups)
    pairs = [(n, seg) for n, (_, segs) in enumerate(keys) for seg in segs]
    cands = conn.execute(
        """WITH RECURSIVE d(n, seg) AS (
             SELECT * FROM unnest(%s::int[], %s::uuid[])
             UNION SELECT d.n, x.new_segment_id FROM d JOIN im.source_supersessions x ON x.old_segment_id = d.seg)
           SELECT DISTINCT d.n, i.item_id, i.quote, i.kind,
                  EXISTS (SELECT 1 FROM im.item_verdicts v WHERE v.item_id = i.item_id)
                    OR EXISTS (SELECT 1 FROM pub.feedback_events f WHERE f.item_id = i.item_id)
           FROM d JOIN im.items i ON i.source_segment_ids @> ARRAY[d.seg]
           JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current""",
        ([n for n, _ in pairs], [s for _, s in pairs])).fetchall()
    scored = []
    for n, item_id, quote, kind, decided in cands:
        score = quote_score(keys[n][0], quote)
        if score >= CARRY_MIN_SCORE:
            scored.append((-score, kind != groups[keys[n]]["kind"], str(item_id), n, item_id, decided))
    stats = {"carried": 0, "already_decided": 0}
    taken, done = set(), set()
    with conn.transaction():
        for neg_score, _, _, n, item_id, decided in sorted(scored):
            if n in done or item_id in taken:
                continue
            done.add(n)
            taken.add(item_id)
            g = groups[keys[n]]
            if not decided:
                conn.execute(
                    """INSERT INTO im.item_verdicts (item_id, segment_ids, quote, verdict, note, decided_at, carried_from)
                       SELECT i.item_id, i.source_segment_ids, i.quote, v.verdict, v.note, v.decided_at, v.verdict_id
                       FROM im.item_verdicts v, im.items i WHERE v.verdict_id = ANY(%s) AND i.item_id = %s
                       ORDER BY v.decided_at, v.verdict_id""", (g["verdicts"], item_id))
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO im.verdict_carries (from_item_id, to_item_id, score, carried, method)
                       VALUES (%s, %s, %s, %s, %s)""",
                    [(old, item_id, -neg_score, not decided, CARRY_METHOD) for old in g["items"]])
            stats["already_decided" if decided else "carried"] += 1
    return stats | {"waiting": len(groups) - len(done)}
