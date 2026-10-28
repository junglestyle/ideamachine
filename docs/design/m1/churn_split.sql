-- Why did extraction calls happen? Splits every extraction request (and its cost) by cause, so M1 knows whether
-- the repeat spend is naming churn (fixable by a new segment key) or re-transcription churn (needs fuzzy re-link).
-- Read-only. Run as the pipeline role:  psql "$IM_DATABASE_URL" -f churn_split.sql
--
-- A request is classified by what it re-sent, first match wins:
--   identical    the exact payload had already gone out (D2: v1.1's payload-hash reuse would have skipped it)
--   naming       carries a segment that replaced an already-sent one with the same utterance_id and transcript
--                revision (only attribution changed: D1)
--   retranscribe carries a segment that replaced an already-sent one by time overlap (new transcript revision)
--   regrouped    no replaced segments, but some of its segments had already gone out (episode boundaries moved,
--                e.g. an open conversation grew)
--   first        nothing in it had gone out before
-- "both" counts requests that carry naming and re-transcription replacements at once (classified as naming).
-- "open" counts re-transcriptions of speech first sent before its conversation had ended (it was still being
-- recorded, so Hearsay re-transcribed it as it grew). A lower bound: it can't see conversations that ended before
-- the run but were re-transcribed later.
-- Caveat: one supersession hop only, and forgotten segments have lost their supersession links.

\pset footer off
WITH x AS (
  SELECT egress_id, created_at, segment_ids, payload_sha256, coalesce(cost_usd, 0) AS cost
  FROM im.egress_log
  WHERE error IS NULL AND prompt_version IN (SELECT DISTINCT prompt_version FROM im.extractions)
),
first_sent AS (
  SELECT s AS segment_id, min(created_at) AS at FROM x, unnest(segment_ids) s GROUP BY s
),
conv_end AS (
  SELECT conversation_id, max(ended_at) AS ended_at FROM im.current_segments GROUP BY conversation_id
),
flags AS (
  SELECT x.*,
    EXISTS (SELECT 1 FROM x y WHERE y.payload_sha256 = x.payload_sha256 AND y.created_at < x.created_at) AS identical,
    EXISTS (SELECT 1 FROM unnest(x.segment_ids) s
            JOIN im.source_supersessions ss ON ss.new_segment_id = s AND ss.method = 'utterance_id'
            JOIN first_sent f ON f.segment_id = ss.old_segment_id AND f.at < x.created_at) AS naming,
    EXISTS (SELECT 1 FROM unnest(x.segment_ids) s
            JOIN im.source_supersessions ss ON ss.new_segment_id = s AND ss.method = 'time_overlap'
            JOIN first_sent f ON f.segment_id = ss.old_segment_id AND f.at < x.created_at) AS retranscribe,
    EXISTS (SELECT 1 FROM unnest(x.segment_ids) s
            JOIN im.source_supersessions ss ON ss.new_segment_id = s AND ss.method = 'time_overlap'
            JOIN first_sent f ON f.segment_id = ss.old_segment_id AND f.at < x.created_at
            JOIN im.source_segments o ON o.segment_id = ss.old_segment_id
            JOIN conv_end c ON c.conversation_id = o.conversation_id AND f.at < c.ended_at) AS open,
    EXISTS (SELECT 1 FROM unnest(x.segment_ids) s
            JOIN first_sent f ON f.segment_id = s AND f.at < x.created_at) AS regrouped
  FROM x
),
cls AS (
  SELECT *, CASE WHEN identical THEN '1 identical' WHEN naming THEN '2 naming'
                 WHEN retranscribe THEN '3 retranscribe' WHEN regrouped THEN '4 regrouped' ELSE '5 first' END AS cause
  FROM flags
)
SELECT cause, count(*) AS requests, count(*) FILTER (WHERE naming AND retranscribe) AS both,
       count(*) FILTER (WHERE retranscribe AND open) AS open,
       round(sum(cost), 2) AS usd, round(100 * sum(cost) / nullif(sum(sum(cost)) OVER (), 0), 1) AS pct
FROM cls GROUP BY cause ORDER BY cause;

-- The same split by week, to separate the Oct 6-7 backfill from steady state.
WITH x AS (
  SELECT egress_id, created_at, segment_ids, payload_sha256, coalesce(cost_usd, 0) AS cost
  FROM im.egress_log
  WHERE error IS NULL AND prompt_version IN (SELECT DISTINCT prompt_version FROM im.extractions)
),
first_sent AS (
  SELECT s AS segment_id, min(created_at) AS at FROM x, unnest(segment_ids) s GROUP BY s
),
cls AS (
  SELECT x.created_at, x.cost, CASE
    WHEN EXISTS (SELECT 1 FROM x y WHERE y.payload_sha256 = x.payload_sha256 AND y.created_at < x.created_at)
      THEN '1 identical'
    WHEN EXISTS (SELECT 1 FROM unnest(x.segment_ids) s
                 JOIN im.source_supersessions ss ON ss.new_segment_id = s AND ss.method = 'utterance_id'
                 JOIN first_sent f ON f.segment_id = ss.old_segment_id AND f.at < x.created_at) THEN '2 naming'
    WHEN EXISTS (SELECT 1 FROM unnest(x.segment_ids) s
                 JOIN im.source_supersessions ss ON ss.new_segment_id = s AND ss.method = 'time_overlap'
                 JOIN first_sent f ON f.segment_id = ss.old_segment_id AND f.at < x.created_at) THEN '3 retranscribe'
    WHEN EXISTS (SELECT 1 FROM unnest(x.segment_ids) s
                 JOIN first_sent f ON f.segment_id = s AND f.at < x.created_at) THEN '4 regrouped'
    ELSE '5 first' END AS cause
  FROM x
)
SELECT date_trunc('week', created_at)::date AS week, cause, count(*) AS requests, round(sum(cost), 2) AS usd
FROM cls GROUP BY 1, 2 ORDER BY 1, 2;
