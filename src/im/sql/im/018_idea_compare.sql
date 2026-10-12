-- Pairwise comparisons: "which of these two ideas is better?", a steadier signal for tuning the ranking than
-- keep/discard. idea_id is the idea Lattice showed first, other_idea_id the second (so position bias can be
-- measured); payload {"winner": "first" | "second" | "tie", "note"?}. Not used by the ranking yet.
ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_kind_check;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_kind_check CHECK (kind IN (
  'star', 'unstar', 'keep', 'discard', 'link', 'unlink', 'pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme',
  'item_keep', 'item_discard', 'item_star', 'idea_correct', 'idea_note', 'idea_compare'));
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_compare CHECK (
  kind <> 'idea_compare' OR (
    idea_id IS NOT NULL AND other_idea_id IS NOT NULL AND idea_id <> other_idea_id
    AND coalesce(payload->>'winner', '') IN ('first', 'second', 'tie')
    AND (NOT payload ? 'note' OR length(payload->>'note') <= 500)));

-- Up to 20 pairs worth asking about: scored ideas near each other in the ranking (1 or 2 places apart), never one
-- compared in the last 30 days. Ideas never compared come first, then ideas whose rubric criteria disagree (a
-- spread of 3 or more), then the rest, each nearest the top of the ranking first. reason says which.
-- Lattice should choose the display order itself (at random), and record it in idea_id / other_idea_id.
CREATE VIEW pub.compare_pairs AS
WITH ranked AS (
  SELECT s.idea_id, s.score,
         greatest(s.generality, s.novelty, s.compression, s.outside_value)
           - least(s.generality, s.novelty, s.compression, s.outside_value) AS spread,
         row_number() OVER (ORDER BY s.score DESC, s.idea_id) AS rk
  FROM pub.idea_scores s WHERE s.score IS NOT NULL
), compared AS (
  SELECT f.idea_id AS id FROM pub.feedback_events f WHERE f.kind = 'idea_compare'
  UNION SELECT f.other_idea_id FROM pub.feedback_events f WHERE f.kind = 'idea_compare'
), candidates AS (
  SELECT a.idea_id, b.idea_id AS other_idea_id, a.rk,
         CASE WHEN a.idea_id NOT IN (SELECT id FROM compared) OR b.idea_id NOT IN (SELECT id FROM compared)
                THEN 'never_compared'
              WHEN a.spread >= 3 OR b.spread >= 3 THEN 'mixed_criteria'
              ELSE 'close_scores' END AS reason
  FROM ranked a JOIN ranked b ON b.rk BETWEEN a.rk + 1 AND a.rk + 2
  WHERE NOT EXISTS (
    SELECT 1 FROM pub.feedback_events f
    WHERE f.kind = 'idea_compare' AND f.at > now() - interval '30 days'
      AND least(f.idea_id, f.other_idea_id) = least(a.idea_id, b.idea_id)
      AND greatest(f.idea_id, f.other_idea_id) = greatest(a.idea_id, b.idea_id))
)
SELECT idea_id, other_idea_id, reason FROM candidates
ORDER BY CASE reason WHEN 'never_compared' THEN 0 WHEN 'mixed_criteria' THEN 1 ELSE 2 END, rk, other_idea_id
LIMIT 20;

GRANT SELECT ON pub.compare_pairs TO lattice_app;
