-- M1 validation snapshot (docs/design/m1-segment-identity.md, "Validation"). Read-only. Run it before the
-- stream-v4 importer's first run and again after, save both outputs, and diff them:
--   docker exec -i ideamachine-db psql -U postgres -d im -At < relink_check.sql > before.txt
-- Lines marked "same" must match between the two runs; "= 0" must be 0 in the second; "≥" must not shrink.

-- ≥: the ledger only grows (egress_log must not grow at all during the run: no requests).
SELECT 'ledger ≥', 'source_segments', count(*) FROM im.source_segments
UNION ALL SELECT 'ledger ≥', 'egress_log', count(*) FROM im.egress_log
UNION ALL SELECT 'ledger ≥', 'item_verdicts', count(*) FROM im.item_verdicts
UNION ALL SELECT 'ledger ≥', 'feedback_events', count(*) FROM pub.feedback_events;

-- same: the speech is the same, whatever its key.
SELECT 'same', 'current speech', count(*),
       md5(string_agg(conversation_id || '|' || started_at || '|' || ended_at || '|' || text, E'\n'
                      ORDER BY conversation_id, started_at, ended_at, text))
FROM im.current_segments;

-- same: every verdict still reaches a current capture of the same speech, with the same latest verdict.
WITH latest AS (
  SELECT DISTINCT ON (v.item_id) v.item_id, v.verdict FROM im.item_verdicts v
  ORDER BY v.item_id, v.decided_at DESC, v.verdict_id DESC)
SELECT 'same', 'current verdicts', count(*),
       md5(string_agg(i.kind || '|' || i.quote || '|' || l.verdict, E'\n' ORDER BY i.kind, i.quote, l.verdict))
FROM latest l JOIN im.items i USING (item_id) JOIN im.episodes e USING (episode_id)
WHERE e.current;

-- = 0: current captures citing speech that isn't current.
SELECT '= 0', 'items citing old segments', count(*)
FROM im.items i JOIN im.episodes e USING (episode_id)
WHERE e.current AND NOT i.source_segment_ids <@ ARRAY(SELECT segment_id FROM im.current_segments);

-- same: the idea set and what Lattice shows about each.
SELECT 'same', 'pub.ideas', count(*),
       md5(string_agg(idea_id || '|' || n_evidence || '|' || kept || '|' || discarded || '|' || starred, E'\n'
                      ORDER BY idea_id))
FROM pub.ideas;

-- ≤ 1.05 after: extractions per current episode under the live prompt.
SELECT '≤ 1.05', 'extractions per current episode',
       round(count(x.*)::numeric / nullif(count(DISTINCT e.episode_id), 0), 3)
FROM im.episodes e LEFT JOIN im.extractions x USING (episode_id)
WHERE e.current;

-- same: hand labels land on the same current speech.
WITH RECURSIVE fwd(label_id, seg) AS (
  SELECT label_id, unnest(segment_ids) FROM im.labels
  UNION
  SELECT f.label_id, x.new_segment_id FROM fwd f JOIN im.source_supersessions x ON x.old_segment_id = f.seg)
SELECT 'same', 'labelled speech', count(*),
       md5(string_agg(f.label_id || '|' || c.started_at || '|' || c.text, E'\n' ORDER BY f.label_id, c.started_at))
FROM fwd f JOIN im.current_segments c ON c.segment_id = f.seg;
