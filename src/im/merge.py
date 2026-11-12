"""Merging two clusters that make one point (`im merge KEEP OTHER`, migration 060).

OTHER is merged into KEEP, and the merge is recorded in im.idea_merges. KEEP's title, statement and note stay as they
are; OTHER's, as shown, go into the record, and its row stays with my events on it, so nothing I wrote is lost and a
merge could be undone.

- Captures: OTHER's move to KEEP as `same_as`, judged_by MERGED, and the match decisions that named OTHER name KEEP,
  so a later read of the same speech follows its observation into KEEP. Typed observations move too.
- Connections and lineage: OTHER's are re-pointed to KEEP, except an embedding stand-in (lattice.relate rebuilds
  KEEP's), one between the two (it disappears), and one where KEEP already has a connection or a judge's verdict
  (KEEP's stands). A kind's direction follows its pair. My links and unlinks on OTHER count for KEEP
  (im.my_link_events). Every connection verdict on OTHER goes: the connection judge nominates KEEP's new neighbours
  on its own.
- Merge review: a row whose clusters are now all one is resolved by this merge (`merge_id`).
- How I kept OTHER, or my mark that it's sensitive, carries to KEEP as a feedback event (source MERGED) when KEEP had
  neither.
- OTHER's derived rows go: embedding, position, weights, sensitivity tags, theme check, an open proposal. Its theme
  memberships move to KEEP, unless I unfiled KEEP from that theme. KEEP's weight is recomputed by the next run, its
  sensitivity tags when its text as tagged (with its first quotes) changed, and its edges' citations now.

From then on OTHER has no text (im.idea_text) and isn't kept (im.idea_kept): it's no card, no matcher candidate and
not embedded, so no capture can join it.
"""

import json
import uuid

from im import citations

MERGED = "merge"


def _id(conn, ref: str):
    try:
        idea = uuid.UUID(ref)
    except ValueError:
        raise ValueError(f"{ref} is not a cluster id") from None
    if not conn.execute("SELECT 1 FROM im.ideas WHERE idea_id = %s", (idea,)).fetchone():
        raise ValueError(f"no cluster {ref}")
    into = conn.execute("SELECT keep_id FROM im.idea_merges WHERE other_id = %s", (idea,)).fetchone()
    if into:
        raise ValueError(f"{ref} was already merged into {into[0]}")
    return idea


def _repoint_connections(conn, keep, other) -> int:
    """OTHER's connections, as KEEP's, with their kinds; then OTHER's are gone. Returns how many moved."""
    moved = 0
    for a, b, method, confidence, cited, decided_at in conn.execute(
            """SELECT a, b, method, confidence, cited_observation_ids, decided_at FROM im.connections
               WHERE %(o)s IN (a, b) AND %(k)s NOT IN (a, b) AND method NOT LIKE 'embedding:%%'""",
            {"o": other, "k": keep}).fetchall():
        x = b if a == other else a
        new = conn.execute(
            """INSERT INTO im.connections (a, b, method, confidence, cited_observation_ids, decided_at)
               SELECT least(%(k)s, %(x)s), greatest(%(k)s, %(x)s), %(m)s, %(c)s, %(cited)s, %(at)s
               WHERE NOT EXISTS (SELECT 1 FROM im.connection_verdicts v
                                 WHERE v.a = least(%(k)s, %(x)s) AND v.b = greatest(%(k)s, %(x)s))
               ON CONFLICT (a, b) DO NOTHING RETURNING a, b""",
            {"k": keep, "x": x, "m": method, "c": confidence, "cited": cited, "at": decided_at}).fetchone()
        if not new:
            continue
        moved += 1
        flip = (a == other) != (new[0] == keep)   # OTHER's side was a, KEEP's is b, or the other way round
        conn.execute(
            """INSERT INTO im.connection_kinds (a, b, kind, direction, method, decided_at)
               SELECT %s, %s, kind,
                      CASE WHEN %s THEN CASE direction WHEN 'a>b' THEN 'b>a' WHEN 'b>a' THEN 'a>b' ELSE direction END
                           ELSE direction END,
                      method, decided_at
               FROM im.connection_kinds WHERE a = %s AND b = %s""", (*new, flip, a, b))
    conn.execute("DELETE FROM im.connections WHERE %s IN (a, b)", (other,))
    return moved


def _repoint_lineage(conn, keep, other) -> int:
    moved = conn.execute(
        """INSERT INTO im.lineage (from_id, to_id, kind, method, cited_observation_ids, created_at)
           SELECT CASE WHEN from_id = %(o)s THEN %(k)s ELSE from_id END,
                  CASE WHEN to_id = %(o)s THEN %(k)s ELSE to_id END, kind, method, cited_observation_ids, created_at
           FROM im.lineage WHERE %(o)s IN (from_id, to_id) AND %(k)s NOT IN (from_id, to_id)
           ON CONFLICT DO NOTHING""", {"o": other, "k": keep}).rowcount
    conn.execute("DELETE FROM im.lineage WHERE %s IN (from_id, to_id)", (other,))
    return moved


def _carry_marks(conn, keep, other) -> list[int]:
    """How I kept OTHER, and my mark that it's sensitive, as events on KEEP when KEEP has neither."""
    kept = dict(conn.execute("SELECT idea_id, kept_as FROM im.idea_kept WHERE idea_id IN (%s, %s)", (keep, other)))
    marks = dict(conn.execute(
        """SELECT DISTINCT ON (idea_id) idea_id, (payload->>'sensitive')::boolean FROM pub.feedback_events
           WHERE kind = 'idea_sensitive' AND idea_id IN (%s, %s) ORDER BY idea_id, event_id DESC""", (keep, other)))
    events = []
    if kept[other] and not kept[keep]:
        events.append(("idea_keep", {"as": kept[other]}))
    if marks.get(other) and keep not in marks:
        events.append(("idea_sensitive", {"sensitive": True}))
    return [conn.execute("""INSERT INTO pub.feedback_events (kind, idea_id, payload, source)
                            VALUES (%s, %s, %s, %s) RETURNING event_id""",
                         (kind, keep, json.dumps(payload), MERGED)).fetchone()[0] for kind, payload in events]


def merge(conn, keep_ref: str, other_ref: str) -> dict:
    """Merge cluster OTHER into KEEP. Raises ValueError, changing nothing, for the same id twice, an id that's no
    cluster, or a cluster already merged away."""
    with conn.transaction():
        keep, other = _id(conn, keep_ref), _id(conn, other_ref)
        if keep == other:
            raise ValueError("a cluster can't be merged into itself")
        title, statement, note = conn.execute(
            "SELECT title, statement, my_note FROM im.idea_text WHERE idea_id = %s", (other,)).fetchone()
        events = _carry_marks(conn, keep, other)
        evidence = [{"item_id": str(i), "relation": r, "judged_by": j} for i, r, j in conn.execute(
            "SELECT item_id, relation, judged_by FROM im.idea_evidence WHERE idea_id = %s ORDER BY item_id", (other,))]
        matched = [r[0] for r in conn.execute(
            "UPDATE im.item_matches SET idea_id = %s WHERE idea_id = %s RETURNING item_id", (keep, other))]
        observations = [r[0] for r in conn.execute(
            "UPDATE im.observations SET idea_id = %s WHERE idea_id = %s RETURNING observation_id", (keep, other))]
        (merge_id,) = conn.execute(
            """INSERT INTO im.idea_merges (other_id, keep_id, evidence, matched_item_ids, observation_ids, title,
                                           statement, my_note, event_ids)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING merge_id""",
            (other, keep, json.dumps(evidence), matched, observations, title, statement, note, events)).fetchone()
        conn.execute("""INSERT INTO im.idea_evidence (idea_id, item_id, relation, judged_by)
                        SELECT %s, item_id, 'same_as', %s FROM im.idea_evidence WHERE idea_id = %s
                        ON CONFLICT DO NOTHING""", (keep, MERGED, other))
        conn.execute("DELETE FROM im.idea_evidence WHERE idea_id = %s", (other,))
        connections = _repoint_connections(conn, keep, other)
        lineage = _repoint_lineage(conn, keep, other)
        verdicts = conn.execute("DELETE FROM im.connection_verdicts WHERE %s IN (a, b)", (other,)).rowcount
        conn.execute("""INSERT INTO im.idea_themes (idea_id, theme_id, origin)
                        SELECT %(k)s, t.theme_id, t.origin FROM im.idea_themes t
                        WHERE t.idea_id = %(o)s AND NOT EXISTS (SELECT 1 FROM im.idea_theme_blocks b
                                                                WHERE b.idea_id = %(k)s AND b.theme_id = t.theme_id)
                        ON CONFLICT DO NOTHING""", {"k": keep, "o": other})
        for table in ("idea_themes", "theme_checks", "idea_embeddings", "idea_positions", "weights",
                      "idea_sensitivity"):
            conn.execute(f"DELETE FROM im.{table} WHERE idea_id = %s", (other,))
        conn.execute("DELETE FROM im.proposals WHERE idea_id = %s AND outcome IS NULL", (other,))
        resolved = conn.execute(
            """UPDATE im.merge_review r SET merge_id = %s
               WHERE r.merge_id IS NULL AND %s = ANY(r.idea_ids)
                 AND (SELECT count(DISTINCT coalesce(m.into_id, u)) FROM unnest(r.idea_ids) u
                      LEFT JOIN im.merged_into m ON m.idea_id = u) = 1""", (merge_id, other)).rowcount
        citations.cite(conn)
    return {"merge_id": merge_id, "kept": str(keep), "merged_away": str(other), "captures": len(evidence),
            "observations": len(observations), "connections_moved": connections, "lineage_moved": lineage,
            "verdicts_dropped": verdicts, "reviews_resolved": resolved, "marks_carried": len(events),
            "merged_away_text": {"title": title, "statement": statement, "my_note": note}}
