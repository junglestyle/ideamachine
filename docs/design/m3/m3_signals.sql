-- M3, second pass (docs/ROADMAP-v2.md, M3). Read-only.
--   docker exec -i ideamachine-db psql -U postgres -d im < ~/review/m3_signals.sql > ~/review/m3_signals.out
--
-- m3_calibrate.sql showed weight-1 can't rank candidates: 382 of 386 have one recurrence, so nearly every candidate
-- weighs 1 and the order is the recency tie-break. Its labels were also mostly bulk keeps (pos rate ~0.87
-- everywhere). This pass:
-- - counts only verdicts I made by reading the capture (how 'one' or 'audit', migration 020), not bulk keeps;
-- - adds signals that vary: the rubric Claude already scores every idea on (im.idea_scores, migration 017), weighted
--   as ranking v2 fitted to my comparisons (migration 025), each criterion alone, and extraction confidence;
-- - checks comparisons between captures only, since archive beat capture in every mixed pair (025) and that
--   swamps the rest.

\pset footer off
\set ON_ERROR_STOP on

CREATE TEMP TABLE c AS
WITH verdicts AS (
  SELECT DISTINCT ON (v.item_id) v.item_id, v.verdict, v.how FROM im.item_verdicts v
  ORDER BY v.item_id, v.decided_at DESC, v.verdict_id DESC
), items AS (
  SELECT ev.idea_id, count(*) AS captures, max(i.confidence) AS confidence,
         count(*) FILTER (WHERE vd.verdict IN ('keep', 'star') AND vd.how <> 'bulk') AS judged_keep,
         count(*) FILTER (WHERE vd.verdict = 'discard' AND vd.how <> 'bulk') AS judged_discard,
         count(*) FILTER (WHERE vd.how = 'bulk') AS bulk
  FROM im.idea_evidence ev JOIN im.items i USING (item_id)
  JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
  LEFT JOIN verdicts vd USING (item_id)
  GROUP BY ev.idea_id
)
SELECT d.idea_id, d.origin, m.maturity, w.weight,
       (w.signals->>'recurrences')::int AS recurrences, (w.signals->>'others')::int AS others,
       s.generality, s.novelty, s.compression, s.outside_value,
       (2 * s.generality + s.novelty + s.compression + 2 * s.outside_value - 6) / 24.0 AS rubric,
       it.confidence, coalesce(it.captures, 0) AS captures, coalesce(it.judged_keep, 0) AS judged_keep,
       coalesce(it.judged_discard, 0) AS judged_discard, coalesce(it.bulk, 0) AS bulk,
       EXISTS (SELECT 1 FROM pub.feedback_events f WHERE f.idea_id = d.idea_id AND f.kind IN ('star', 'idea_keep', 'link',
               'idea_note', 'idea_correct')) AS acted_on,
       EXISTS (SELECT 1 FROM pub.feedback_events f WHERE f.idea_id = d.idea_id AND f.kind = 'idea_dismiss') AS dismissed
FROM im.ideas d
JOIN im.idea_maturity m USING (idea_id)
JOIN im.weights w ON w.idea_id = d.idea_id
 AND w.formula_version = (SELECT formula_version FROM im.weights ORDER BY computed_at DESC LIMIT 1)
LEFT JOIN im.idea_scores s ON s.idea_id = d.idea_id
LEFT JOIN items it ON it.idea_id = d.idea_id;

-- pos: I kept or starred a capture after reading it, or acted on the idea. neg: I discarded after reading and
-- kept nothing, or dismissed the proposal. Bulk keeps label nothing.
ALTER TABLE c ADD COLUMN label text;
UPDATE c SET label = CASE WHEN judged_keep > 0 OR acted_on THEN 'pos'
                          WHEN judged_discard > 0 OR dismissed THEN 'neg' END;

-- Every signal as one long table, so each check below runs over all of them the same way.
CREATE TEMP TABLE sig AS
SELECT c.idea_id, c.origin, c.maturity, c.label, x.signal, x.v
FROM c CROSS JOIN LATERAL (VALUES
  ('weight', c.weight), ('recurrences', c.recurrences::float), ('others', c.others::float),
  ('rubric_v2', c.rubric), ('generality', c.generality::float), ('novelty', c.novelty::float),
  ('compression', c.compression::float), ('outside_value', c.outside_value::float),
  ('confidence', c.confidence::float), ('captures', c.captures::float)) x(signal, v);

-- 1. How my verdicts were made, latest per item.
SELECT 'verdicts by how' AS check, verdict, how, count(*) AS items
FROM (SELECT DISTINCT ON (item_id) verdict, how FROM im.item_verdicts ORDER BY item_id, decided_at DESC, verdict_id DESC) x
GROUP BY 1, 2, 3 ORDER BY 2, 3;

-- 2. Coverage with judged labels only, and how many have a rubric score.
SELECT 'coverage' AS check, origin, maturity, count(*) AS clusters, count(rubric) AS scored,
       count(*) FILTER (WHERE label = 'pos') AS pos, count(*) FILTER (WHERE label = 'neg') AS neg,
       count(*) FILTER (WHERE label IS NULL) AS unlabelled
FROM c GROUP BY 1, 2, 3 ORDER BY 2, 3;

-- 3. AUC of each signal, judged pos vs judged neg, over candidates and over every captured cluster. 0.5 is a coin;
--    below it, the signal points the wrong way. n_pos / n_neg: how many it rests on (null signals are left out).
SELECT 'auc' AS check, p.signal, scope,
       count(DISTINCT p.idea_id) AS n_pos, count(DISTINCT n.idea_id) AS n_neg,
       round(avg(CASE WHEN p.v > n.v THEN 1 WHEN p.v = n.v THEN 0.5 ELSE 0 END), 3) AS auc
FROM (VALUES ('candidate'), ('captured')) sc(scope)
JOIN sig p ON p.label = 'pos' AND p.v IS NOT NULL AND p.origin = 'captured'
          AND (sc.scope = 'captured' OR p.maturity = 'candidate')
JOIN sig n ON n.label = 'neg' AND n.v IS NOT NULL AND n.origin = 'captured' AND n.signal = p.signal
          AND (sc.scope = 'captured' OR n.maturity = 'candidate')
GROUP BY p.signal, scope ORDER BY scope, auc DESC;

-- 4. Comparisons between two captures: how often the one I picked has more of each signal. untied: pairs where
--    the signal differs, the only ones it can call.
SELECT 'compare captures' AS check, a.signal, count(*) FILTER (WHERE a.v <> b.v) AS untied,
       round(avg(((f.payload->>'winner' = 'first') = (a.v > b.v))::int) FILTER (WHERE a.v <> b.v), 2) AS agreement
FROM pub.feedback_events f
JOIN sig a ON a.idea_id = f.idea_id AND a.origin = 'captured'
JOIN sig b ON b.idea_id = f.other_idea_id AND b.origin = 'captured' AND b.signal = a.signal
WHERE f.kind = 'idea_compare' AND f.payload->>'winner' IN ('first', 'second')
GROUP BY a.signal ORDER BY agreement DESC NULLS LAST;

-- 5. The ten proposals so far, with what each signal said about them.
SELECT 'proposals' AS check, p.outcome, p.weight, c.rubric::numeric(4, 3) AS rubric_v2, c.generality AS g,
       c.novelty AS n, c.compression AS c, c.outside_value AS o, c.others, c.confidence::numeric(3, 2) AS conf, x.title
FROM im.proposals p JOIN c USING (idea_id) JOIN im.idea_text x USING (idea_id)
ORDER BY p.outcome, c.rubric DESC NULLS LAST;

-- 6. What the top 50 open candidates would be ranked by each signal: my judged labels on them, and how many the
--    two orders share. Candidates already kept or proposed are out, as in weight._eligible.
WITH open AS (
  SELECT * FROM c WHERE maturity = 'candidate'
    AND NOT EXISTS (SELECT 1 FROM im.proposals p WHERE p.idea_id = c.idea_id)
), by_weight AS (SELECT idea_id, label FROM open ORDER BY weight DESC, idea_id LIMIT 50),
   by_rubric AS (SELECT idea_id, label FROM open ORDER BY rubric DESC NULLS LAST, weight DESC, idea_id LIMIT 50)
SELECT 'top 50 open' AS check, 'weight' AS by, count(*) FILTER (WHERE label = 'pos') AS pos,
       count(*) FILTER (WHERE label = 'neg') AS neg, count(*) FILTER (WHERE label IS NULL) AS unlabelled,
       (SELECT count(*) FROM by_weight JOIN by_rubric USING (idea_id)) AS shared
FROM by_weight
UNION ALL
SELECT 'top 50 open', 'rubric_v2', count(*) FILTER (WHERE label = 'pos'), count(*) FILTER (WHERE label = 'neg'),
       count(*) FILTER (WHERE label IS NULL), NULL
FROM by_rubric;

-- 7. Rubric bands over candidates, judged labels only: where a cutoff would sit, if one is worth having.
SELECT 'rubric band' AS check, round((floor(rubric * 10) / 10)::numeric, 1) AS rubric_from, count(*) AS candidates,
       count(*) FILTER (WHERE label = 'pos') AS pos, count(*) FILTER (WHERE label = 'neg') AS neg,
       round(avg((label = 'pos')::int) FILTER (WHERE label IS NOT NULL), 2) AS pos_rate
FROM c WHERE maturity = 'candidate' GROUP BY 2 ORDER BY 2 DESC NULLS LAST;
