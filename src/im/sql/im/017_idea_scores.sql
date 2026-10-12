-- Surfacing (ROADMAP Phase 2, slice 3 as redirected 2026-10-12): rank ideas by signals that don't rest on my
-- keep/discard tags, which agree with themselves only 64% of the time when I judge the same capture twice.

-- A fixed rubric Claude applies to each idea's current text (my corrections included), 1-5 per criterion.
-- Re-scored when the text or the rubric changes (input_hash, rubric_version).
CREATE TABLE im.idea_scores (
  idea_id         uuid PRIMARY KEY REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  generality      smallint NOT NULL CHECK (generality BETWEEN 1 AND 5),
  novelty         smallint NOT NULL CHECK (novelty BETWEEN 1 AND 5),
  compression     smallint NOT NULL CHECK (compression BETWEEN 1 AND 5),
  outside_value   smallint NOT NULL CHECK (outside_value BETWEEN 1 AND 5),
  why             text NOT NULL,
  rubric_version  text NOT NULL,
  input_hash      text NOT NULL,
  scored_at       timestamptz NOT NULL DEFAULT now()
);

-- What Lattice ranks by. score (0-1) = 0.7 x rubric + 0.2 x recurrence + 0.1 x my star, where rubric is the mean
-- of the four criteria scaled to 0-1, and recurrence is how many captures on current episodes back the idea
-- (1 -> 0, 2 -> 0.5, 4 or more -> 1). An idea without a rubric score yet has a NULL score.
CREATE VIEW pub.idea_scores AS
SELECT d.idea_id,
       round((0.7 * r.rubric + 0.2 * least(1, ln(greatest(d.n_evidence, 1)) / ln(4)) + 0.1 * d.starred::int)::numeric,
             3) AS score,
       round(r.rubric::numeric, 3) AS rubric, s.generality, s.novelty, s.compression, s.outside_value,
       round(least(1, ln(greatest(d.n_evidence, 1)) / ln(4))::numeric, 3) AS recurrence, s.why, s.rubric_version
FROM pub.ideas d
LEFT JOIN im.idea_scores s USING (idea_id)
LEFT JOIN LATERAL (SELECT (s.generality + s.novelty + s.compression + s.outside_value - 4) / 16.0 AS rubric) r ON true;

GRANT SELECT ON pub.idea_scores TO lattice_app;

-- Run status: the scoring stage's stop reason is a warning too. (Same columns, so OR REPLACE works.)
CREATE OR REPLACE VIEW pub.runs AS
SELECT r.run_id, r.started_at, r.finished_at,
       r.stats->>'error' AS error,
       array_remove(ARRAY[r.stats->'extract'->>'stopped', r.stats->'lattice'->>'stopped', r.stats->'themes'->>'stopped',
                          r.stats->'themes'->'file'->>'stopped', r.stats->'themes'->'propose'->>'stopped',
                          r.stats->'scores'->>'stopped'], NULL)
         || coalesce(ARRAY(SELECT jsonb_array_elements_text(r.stats->'triage_notes')), '{}') AS warnings,
       coalesce((r.stats->>'conversations_changed')::int, 0) AS conversations_changed,
       coalesce((r.stats->'extract'->>'read')::int, 0) AS episodes_read,
       coalesce((r.stats->'extract'->>'items')::int, 0) AS captured,
       coalesce((r.stats->'extract'->>'refused')::int, 0) AS refused,
       (SELECT coalesce(sum(g.cost_usd), 0) FROM im.egress_log g
        WHERE g.created_at BETWEEN r.started_at AND coalesce(r.finished_at, now())) AS cost_usd,
       (r.stats->>'monthly_cap_usd')::numeric AS monthly_cap_usd
FROM im.runs r
WHERE r.command = 'run' AND r.started_at > now() - interval '14 days';
