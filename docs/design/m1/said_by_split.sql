-- M1 design doc, open question: how often does an item cite lines from more than one speaker?
-- Read-only. Current items only (episode not retired). Uses source_segments, so retired segments still resolve.
WITH cur AS (
  SELECT i.item_id, i.said_by, i.source_segment_ids
  FROM im.items i JOIN im.episodes e USING (episode_id)
  WHERE e.retired_at IS NULL
), spk AS (
  SELECT c.item_id, c.said_by,
         count(DISTINCT s.speaker_label) AS speakers,
         bool_or(s.speaker_label = c.said_by) AS said_by_cited
  FROM cur c JOIN im.source_segments s ON s.segment_id = ANY (c.source_segment_ids)
  GROUP BY c.item_id, c.said_by
)
SELECT count(*) AS items,
       count(*) FILTER (WHERE speakers > 1) AS multi_speaker,
       round(100.0 * count(*) FILTER (WHERE speakers > 1) / count(*), 1) AS pct_multi,
       count(*) FILTER (WHERE NOT said_by_cited) AS said_by_not_among_cited
FROM spk;
