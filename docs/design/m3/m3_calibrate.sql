-- M3 calibration (docs/ROADMAP-v2.md, M3): does weight rank clusters the way my own labels do? Read-only.
--   docker exec -i ideamachine-db psql -U postgres -d im < ~/review/m3_calibrate.sql > ~/review/m3_calibrate.out
--
-- Weight is read from im.weights (latest formula version) with the signals it was computed from, so this checks
-- what the run stored, not a re-implementation of it. The labels are mine and never feed weight:
--   item verdicts      im.item_verdicts, latest per item: keep / star / discard (retired as a signal 2026-10-28,
--                      used here only to check the formula against)
--   idea events        star/unstar, keep/discard (V1 idea level), link, idea_note/idea_correct
--   comparisons        idea_compare: which of two ideas is better
--   proposal outcomes  im.proposals: canonical / pinned / dismissed (the M3 bar's own label)
-- Every section prints counts beside rates, so a rate on a handful of clusters reads as one.

\pset footer off
\set ON_ERROR_STOP on

CREATE TEMP VIEW v AS
SELECT formula_version FROM im.weights ORDER BY computed_at DESC LIMIT 1;

-- Per cluster: weight-1 and its signals, maturity, and every label I've given it.
CREATE TEMP TABLE c AS
WITH verdicts AS (
  SELECT DISTINCT ON (v.item_id) v.item_id, v.verdict FROM im.item_verdicts v ORDER BY v.item_id, v.decided_at DESC,
         v.verdict_id DESC
), items AS (
  SELECT ev.idea_id,
         count(*) AS captures,
         count(*) FILTER (WHERE vd.verdict = 'keep') AS item_keep,
         count(*) FILTER (WHERE vd.verdict = 'star') AS item_star,
         count(*) FILTER (WHERE vd.verdict = 'discard') AS item_discard
  FROM im.idea_evidence ev JOIN im.items i USING (item_id)
  JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
  LEFT JOIN verdicts vd USING (item_id)
  GROUP BY ev.idea_id
), ev AS (
  SELECT d.idea_id,
         (SELECT f.kind = 'star' FROM pub.feedback_events f WHERE f.idea_id = d.idea_id AND f.kind IN ('star', 'unstar')
          ORDER BY f.at DESC, f.event_id DESC LIMIT 1) AS starred,
         (SELECT f.kind FROM pub.feedback_events f WHERE f.idea_id = d.idea_id AND f.kind IN ('keep', 'discard')
          ORDER BY f.at DESC, f.event_id DESC LIMIT 1) AS idea_verdict,
         EXISTS (SELECT 1 FROM pub.feedback_events f WHERE f.kind = 'link'
                 AND d.idea_id IN (f.idea_id, f.other_idea_id)) AS linked,
         EXISTS (SELECT 1 FROM pub.feedback_events f WHERE f.kind IN ('idea_note', 'idea_correct')
                 AND f.idea_id = d.idea_id) AS noted,
         EXISTS (SELECT 1 FROM pub.feedback_events f WHERE f.kind = 'idea_keep' AND f.idea_id = d.idea_id) AS kept_event,
         EXISTS (SELECT 1 FROM pub.feedback_events f WHERE f.kind = 'idea_dismiss' AND f.idea_id = d.idea_id) AS dismissed
  FROM im.ideas d
)
SELECT d.idea_id, d.origin, m.maturity, m.kept_as,
       w.weight, (w.signals->>'recurrences')::int AS recurrences, (w.signals->>'others')::int AS others,
       (w.signals->>'canonical_links')::int AS canonical_links,
       coalesce(it.captures, 0) AS captures, coalesce(it.item_keep, 0) AS item_keep,
       coalesce(it.item_star, 0) AS item_star, coalesce(it.item_discard, 0) AS item_discard,
       coalesce(ev.starred, false) AS starred, ev.idea_verdict, ev.linked, ev.noted, ev.kept_event, ev.dismissed,
       -- Observed labels, collapsed. pos: any star, keep, link or note I gave it. neg: every verdict on it a discard,
       -- or dismissed, and nothing positive. A cluster with neither is unlabelled.
       (coalesce(ev.starred, false) OR coalesce(it.item_keep, 0) + coalesce(it.item_star, 0) > 0
        OR ev.idea_verdict = 'keep' OR ev.linked OR ev.noted OR ev.kept_event) AS pos,
       (coalesce(it.item_discard, 0) > 0 AND coalesce(it.item_keep, 0) + coalesce(it.item_star, 0) = 0
        OR ev.idea_verdict = 'discard' OR ev.dismissed) AS neg_any
FROM im.ideas d
JOIN im.idea_maturity m USING (idea_id)
LEFT JOIN im.weights w ON w.idea_id = d.idea_id AND w.formula_version = (SELECT formula_version FROM v)
LEFT JOIN items it ON it.idea_id = d.idea_id
JOIN ev ON ev.idea_id = d.idea_id;

ALTER TABLE c ADD COLUMN label text;
UPDATE c SET label = CASE WHEN pos THEN 'pos' WHEN neg_any THEN 'neg' END;

-- 0. What this reads.
SELECT 'formula' AS check, w.formula_version, count(*) AS clusters, max(w.computed_at) AS last_computed
FROM im.weights w GROUP BY w.formula_version ORDER BY max(w.computed_at) DESC;

SELECT 'labels' AS check, kind, count(*) AS events, min(at)::date AS first, max(at)::date AS last
FROM pub.feedback_events
WHERE kind IN ('star', 'unstar', 'keep', 'discard', 'link', 'item_keep', 'item_discard', 'item_star', 'idea_note',
               'idea_correct', 'idea_compare', 'idea_keep', 'idea_unkeep', 'idea_dismiss')
GROUP BY kind ORDER BY kind;

SELECT 'item_verdicts' AS check, verdict, count(*) AS rows, count(DISTINCT item_id) AS items
FROM im.item_verdicts GROUP BY verdict ORDER BY verdict;

-- 1. Coverage: how many clusters each label reaches, by maturity. Unlabelled clusters can't calibrate anything.
SELECT 'coverage' AS check, origin, maturity, coalesce(kept_as, '-') AS kept_as, count(*) AS clusters,
       count(weight) AS weighted, count(*) FILTER (WHERE label = 'pos') AS pos,
       count(*) FILTER (WHERE label = 'neg') AS neg, count(*) FILTER (WHERE label IS NULL) AS unlabelled
FROM c GROUP BY 1, 2, 3, 4 ORDER BY 2, 3, 4;

-- 2. Each signal on its own, over captured clusters: does more of it mean I liked it more?
--    pos_rate = pos / (pos + neg). keep_share = kept or starred captures / captures with a verdict.
SELECT 'by recurrences' AS check, least(recurrences, 4) AS recurrences_4plus, count(*) AS clusters,
       count(*) FILTER (WHERE label = 'pos') AS pos, count(*) FILTER (WHERE label = 'neg') AS neg,
       round(avg((label = 'pos')::int) FILTER (WHERE label IS NOT NULL), 2) AS pos_rate,
       round(sum(item_keep + item_star)::numeric / nullif(sum(item_keep + item_star + item_discard), 0), 2) AS keep_share
FROM c WHERE origin = 'captured' GROUP BY 2 ORDER BY 2;

SELECT 'by others' AS check, least(others, 5) AS others_5plus, count(*) AS clusters,
       count(*) FILTER (WHERE label = 'pos') AS pos, count(*) FILTER (WHERE label = 'neg') AS neg,
       round(avg((label = 'pos')::int) FILTER (WHERE label IS NOT NULL), 2) AS pos_rate,
       round(sum(item_keep + item_star)::numeric / nullif(sum(item_keep + item_star + item_discard), 0), 2) AS keep_share
FROM c WHERE origin = 'captured' GROUP BY 2 ORDER BY 2;

-- Others among clusters with no recurrence of mine: does someone else saying it carry anything on its own?
SELECT 'others | no recurrence' AS check, least(others, 3) AS others_3plus, count(*) AS clusters,
       count(*) FILTER (WHERE label = 'pos') AS pos, count(*) FILTER (WHERE label = 'neg') AS neg,
       round(avg((label = 'pos')::int) FILTER (WHERE label IS NOT NULL), 2) AS pos_rate
FROM c WHERE origin = 'captured' AND recurrences = 0 GROUP BY 2 ORDER BY 2;

SELECT 'by canonical_links' AS check, least(canonical_links, 3) AS links_3plus, count(*) AS clusters,
       count(*) FILTER (WHERE label = 'pos') AS pos, count(*) FILTER (WHERE label = 'neg') AS neg,
       round(avg((label = 'pos')::int) FILTER (WHERE label IS NOT NULL), 2) AS pos_rate,
       round(sum(item_keep + item_star)::numeric / nullif(sum(item_keep + item_star + item_discard), 0), 2) AS keep_share
FROM c WHERE origin = 'captured' GROUP BY 2 ORDER BY 2;

-- 3. Weight as a whole: quintiles of candidates (what proposals draw from), and the top 50 as the M3 bar sees it.
SELECT 'candidate weight quintile' AS check, q, min(weight) AS lo, max(weight) AS hi, count(*) AS clusters,
       count(*) FILTER (WHERE label = 'pos') AS pos, count(*) FILTER (WHERE label = 'neg') AS neg,
       round(avg((label = 'pos')::int) FILTER (WHERE label IS NOT NULL), 2) AS pos_rate
FROM (SELECT *, ntile(5) OVER (ORDER BY weight DESC, idea_id) AS q FROM c WHERE maturity = 'candidate') x
GROUP BY q ORDER BY q;

SELECT 'candidate top-k' AS check, k, count(*) AS clusters, count(*) FILTER (WHERE label = 'pos') AS pos,
       count(*) FILTER (WHERE label = 'neg') AS neg, count(*) FILTER (WHERE label IS NULL) AS unlabelled
FROM (SELECT *, row_number() OVER (ORDER BY weight DESC, idea_id) AS rk FROM c WHERE maturity = 'candidate') x
CROSS JOIN (VALUES (10), (25), (50), (100)) ks(k)
WHERE rk <= k GROUP BY k ORDER BY k;

-- AUC of weight for pos vs neg among captured clusters: the chance a random liked cluster outweighs a random
-- disliked one (ties count half). 0.5 is a coin. Also for each signal alone, to see what carries it.
SELECT 'auc' AS check, s.signal, count(*) FILTER (WHERE p.label = 'pos') AS pairs_pos_side,
       round(avg(CASE WHEN p.x > n.x THEN 1 WHEN p.x = n.x THEN 0.5 ELSE 0 END), 3) AS auc
FROM (VALUES ('weight'), ('recurrences'), ('others'), ('canonical_links'), ('captures')) s(signal)
CROSS JOIN LATERAL (SELECT label, CASE s.signal WHEN 'weight' THEN weight WHEN 'recurrences' THEN recurrences
                                    WHEN 'others' THEN others WHEN 'canonical_links' THEN canonical_links
                                    ELSE captures END AS x
                    FROM c WHERE origin = 'captured' AND label = 'pos') p
CROSS JOIN LATERAL (SELECT CASE s.signal WHEN 'weight' THEN weight WHEN 'recurrences' THEN recurrences
                             WHEN 'others' THEN others WHEN 'canonical_links' THEN canonical_links
                             ELSE captures END AS x
                    FROM c WHERE origin = 'captured' AND label = 'neg') n
GROUP BY s.signal ORDER BY s.signal;

-- 4. Comparisons: how often the idea I picked has the higher weight, ties in weight set aside. Position bias first,
--    since a strong one makes the rest noise.
SELECT 'compare position' AS check, f.payload->>'winner' AS winner, count(*) AS n
FROM pub.feedback_events f WHERE f.kind = 'idea_compare' GROUP BY 2 ORDER BY 2;

SELECT 'compare vs weight' AS check, count(*) AS decided,
       count(*) FILTER (WHERE wa.weight IS NULL OR wb.weight IS NULL) AS unweighted,
       count(*) FILTER (WHERE wa.weight = wb.weight) AS weight_tied,
       count(*) FILTER (WHERE (f.payload->>'winner' = 'first') = (wa.weight > wb.weight) AND wa.weight <> wb.weight)
         AS agrees,
       round(avg(((f.payload->>'winner' = 'first') = (wa.weight > wb.weight))::int)
             FILTER (WHERE wa.weight <> wb.weight), 2) AS agreement,
       round(avg(((f.payload->>'winner' = 'first') = (wa.recurrences > wb.recurrences))::int)
             FILTER (WHERE wa.recurrences <> wb.recurrences), 2) AS agreement_recurrences_only,
       count(*) FILTER (WHERE wa.recurrences <> wb.recurrences) AS recurrences_differ
FROM pub.feedback_events f
LEFT JOIN c wa ON wa.idea_id = f.idea_id
LEFT JOIN c wb ON wb.idea_id = f.other_idea_id
WHERE f.kind = 'idea_compare' AND f.payload->>'winner' IN ('first', 'second');

-- 5. Proposals so far: what I did with them, by the rank they were made at and their weight.
SELECT 'proposal outcomes' AS check, coalesce(p.outcome, 'open') AS outcome, count(*) AS n,
       round(avg(p.weight)::numeric, 2) AS avg_weight, min(p.made_at)::date AS first, max(p.made_at)::date AS last
FROM im.proposals p GROUP BY 2 ORDER BY 2;

SELECT 'proposal outcome by weight' AS check, least(floor(p.weight), 6) AS weight_floor_6plus,
       count(*) FILTER (WHERE p.outcome IN ('canonical', 'pinned')) AS kept,
       count(*) FILTER (WHERE p.outcome = 'dismissed') AS dismissed, count(*) FILTER (WHERE p.outcome IS NULL) AS open
FROM im.proposals p GROUP BY 2 ORDER BY 2;

-- Dismissed proposals with the reason I gave, if any, to read by eye.
SELECT 'dismiss reasons' AS check, x.title, p.weight, f.payload->>'note' AS note
FROM im.proposals p JOIN im.idea_text x USING (idea_id)
LEFT JOIN LATERAL (SELECT f.payload FROM pub.feedback_events f WHERE f.idea_id = p.idea_id AND f.kind = 'idea_dismiss'
                   ORDER BY f.at DESC LIMIT 1) f ON true
WHERE p.outcome = 'dismissed' ORDER BY p.weight DESC;

-- 6. The disagreements, to read by eye: heaviest candidates I disliked, lightest I liked.
SELECT 'heavy but neg' AS check, c.weight, c.recurrences, c.others, c.canonical_links, c.item_discard, x.title
FROM c JOIN im.idea_text x USING (idea_id)
WHERE c.maturity = 'candidate' AND c.label = 'neg' ORDER BY c.weight DESC LIMIT 15;

SELECT 'light but pos' AS check, c.weight, c.recurrences, c.others, c.canonical_links, c.maturity, x.title
FROM c JOIN im.idea_text x USING (idea_id)
WHERE c.origin = 'captured' AND c.label = 'pos' ORDER BY c.weight, c.idea_id LIMIT 15;
