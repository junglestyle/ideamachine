"""Invariant queries. Each returns offending rows; empty means the invariant holds."""

from im.citations import ORIGIN_OBSERVATIONS

# Every current segment is in exactly one current episode.
ORPHANS = """
SELECT c.segment_id, count(e.episode_id) AS current_episodes
FROM im.current_segments c
LEFT JOIN im.episode_segments es ON es.segment_id = c.segment_id
LEFT JOIN im.episodes e ON e.episode_id = es.episode_id AND e.current
GROUP BY c.segment_id
HAVING count(e.episode_id) <> 1
"""

# No current episode holds a segment that isn't current.
STALE = """
SELECT es.episode_id, es.segment_id
FROM im.episode_segments es JOIN im.episodes e USING (episode_id)
WHERE e.current
  AND NOT EXISTS (SELECT 1 FROM im.current_segments c WHERE c.segment_id = es.segment_id)
"""

# Nothing derived refers to a tombstoned segment: no episode, observation or connection verdict holds one, and no idea
# was made from one.
TOMBSTONED = """
SELECT es.episode_id, es.segment_id
FROM im.episode_segments es JOIN im.source_tombstones t USING (segment_id)
UNION ALL
SELECT o.observation_id, t.segment_id
FROM im.observations o JOIN im.source_tombstones t ON t.segment_id = ANY(o.segment_ids)
UNION ALL
SELECT d.idea_id, t.segment_id
FROM im.ideas d JOIN im.source_tombstones t ON t.segment_id = ANY(d.segment_ids)
UNION ALL
SELECT v.a, t.segment_id
FROM im.connection_verdicts v JOIN im.source_tombstones t ON t.segment_id = ANY(v.segment_ids)
"""

# Every edge cites what exists (migration 035): no observation that's gone, and nothing missing when an end has one.
# Edges are connections and lineage (migration 045).
UNCITED = f"""
WITH origin AS ({ORIGIN_OBSERVATIONS}),
edges AS (SELECT a, b, 'connection' AS kind, cited_observation_ids FROM im.connections
          UNION ALL SELECT from_id, to_id, kind, cited_observation_ids FROM im.lineage)
SELECT l.a, l.b, l.kind FROM edges l
WHERE EXISTS (SELECT 1 FROM unnest(l.cited_observation_ids) c
              WHERE NOT EXISTS (SELECT 1 FROM im.observations o WHERE o.observation_id = c))
   OR (cardinality(l.cited_observation_ids) = 0 AND EXISTS (SELECT 1 FROM origin WHERE origin.idea_id IN (l.a, l.b)))
"""

CHECKS = {"orphans": ORPHANS, "stale": STALE, "tombstoned": TOMBSTONED, "uncited": UNCITED}


def run_checks(conn) -> dict[str, list]:
    return {name: conn.execute(sql).fetchall() for name, sql in CHECKS.items()}
