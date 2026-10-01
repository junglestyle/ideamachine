"""Invariant queries. Each returns offending rows; empty means the invariant holds."""

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

# Nothing derived refers to a tombstoned segment.
TOMBSTONED = """
SELECT es.episode_id, es.segment_id
FROM im.episode_segments es JOIN im.source_tombstones t USING (segment_id)
"""

CHECKS = {"orphans": ORPHANS, "stale": STALE, "tombstoned": TOMBSTONED}


def run_checks(conn) -> dict[str, list]:
    return {name: conn.execute(sql).fetchall() for name, sql in CHECKS.items()}
