-- Ranking v2, fitted to my first 135 comparisons (2026-10-13). Without stars (36 of my 37 were made during the same
-- session, so they share the comparisons' judgment), v1 picked my winner 41% of the time: below chance, because
-- recurrence pointed the wrong way (in all 17 pairs where it differed, I picked the idea said less often) and 93 of
-- 122 pairs had identical rubric totals. What predicted my choices:
-- - Archive ideas beat captures in all 49 mixed pairs. Partly that's loyalty to ideas I already knew were mine, so
--   the bonus goes to *developed* ideas instead: the archive's, ones I added, and captures I've worked on (noted,
--   corrected, or linked by hand).
-- - Between captures, generality (20 to 10) and outside value (12 to 4) picked the winner; novelty and compression
--   slightly picked the loser. So they count double and single.
-- On those comparisons v2 picks my winner 76% of the time (61% between captures); judging the same pair twice, I
-- agreed with myself 15 of 19 times, about the ceiling.
--
-- score (0-1) = 0.65 x rubric + 0.25 x developed + 0.1 x my star, where
-- rubric = (2 generality + novelty + compression + 2 outside_value - 6) / 24. Recurrence stays as a column for
-- display, out of the score. (Same columns plus `developed` at the end, so OR REPLACE works.)
CREATE OR REPLACE VIEW pub.idea_scores AS
SELECT d.idea_id,
       round((0.65 * r.rubric + 0.25 * dv.developed::int + 0.1 * d.starred::int)::numeric, 3) AS score,
       round(r.rubric::numeric, 3) AS rubric, s.generality, s.novelty, s.compression, s.outside_value,
       round(least(1, ln(greatest(d.n_evidence, 1)) / ln(4))::numeric, 3) AS recurrence, s.why, s.rubric_version,
       dv.developed
FROM pub.ideas d
LEFT JOIN im.idea_scores s USING (idea_id)
LEFT JOIN LATERAL (SELECT (2 * s.generality + s.novelty + s.compression + 2 * s.outside_value - 6) / 24.0 AS rubric) r
  ON true
LEFT JOIN LATERAL (SELECT d.origin IN ('archive', 'mine') OR d.corrected OR d.my_note IS NOT NULL
                          OR EXISTS (SELECT 1 FROM pub.connections c WHERE c.kind = 'mine' AND d.idea_id IN (c.a, c.b))
                          AS developed) dv ON true;

-- Pairs v2: each answer should teach something. Only ideas alike in being developed or not (archive against capture
-- is settled), and only pairs whose rubric criteria differ (v1's neighbours mostly had identical criteria). Partners
-- come from up to 5 places down the ranking among such ideas; otherwise as before: never compared first, then mixed
-- criteria, then close scores, nearest the top first, nothing compared in the last 30 days. (Same columns.)
CREATE OR REPLACE VIEW pub.compare_pairs AS
WITH ranked AS (
  SELECT s.idea_id, s.score, s.developed, s.generality, s.novelty, s.compression, s.outside_value,
         greatest(s.generality, s.novelty, s.compression, s.outside_value)
           - least(s.generality, s.novelty, s.compression, s.outside_value) AS spread,
         row_number() OVER (PARTITION BY s.developed ORDER BY s.score DESC, s.idea_id) AS rk
  FROM pub.idea_scores s WHERE s.score IS NOT NULL
), compared AS (
  SELECT f.idea_id AS id FROM pub.feedback_events f WHERE f.kind = 'idea_compare'
  UNION SELECT f.other_idea_id FROM pub.feedback_events f WHERE f.kind = 'idea_compare'
), candidates AS (
  SELECT a.idea_id, b.idea_id AS other_idea_id, a.rk, a.developed,
         CASE WHEN a.idea_id NOT IN (SELECT id FROM compared) OR b.idea_id NOT IN (SELECT id FROM compared)
                THEN 'never_compared'
              WHEN a.spread >= 3 OR b.spread >= 3 THEN 'mixed_criteria'
              ELSE 'close_scores' END AS reason,
         row_number() OVER (PARTITION BY a.idea_id ORDER BY b.rk) AS nth
  FROM ranked a JOIN ranked b ON b.developed = a.developed AND b.rk BETWEEN a.rk + 1 AND a.rk + 5
  WHERE (a.generality, a.novelty, a.compression, a.outside_value)
          IS DISTINCT FROM (b.generality, b.novelty, b.compression, b.outside_value)
    AND NOT EXISTS (
      SELECT 1 FROM pub.feedback_events f
      WHERE f.kind = 'idea_compare' AND f.at > now() - interval '30 days'
        AND least(f.idea_id, f.other_idea_id) = least(a.idea_id, b.idea_id)
        AND greatest(f.idea_id, f.other_idea_id) = greatest(a.idea_id, b.idea_id))
)
SELECT idea_id, other_idea_id, reason FROM candidates WHERE nth <= 2
ORDER BY CASE reason WHEN 'never_compared' THEN 0 WHEN 'mixed_criteria' THEN 1 ELSE 2 END, developed, rk, other_idea_id
LIMIT 20;
