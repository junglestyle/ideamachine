-- Weekly synthesis (ROADMAP Phase 2, slice 6): once a week Claude reads what the lattice gained and writes a short
-- digest: what kept coming up, what converges, what matters more than it seemed, projects forming, and what would
-- be worth something to a stranger. Each point cites the ideas it rests on. A week runs Monday to Monday, local
-- time. The digest is derived data: forgetting any speech behind it deletes it (segment_ids), since its text may
-- paraphrase what was forgotten.
CREATE TABLE im.digests (
  digest_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  week_start      date NOT NULL UNIQUE,
  period_start    timestamptz NOT NULL,
  period_end      timestamptz NOT NULL,
  summary         text NOT NULL,
  prompt_version  text NOT NULL,
  segment_ids     uuid[] NOT NULL DEFAULT '{}',
  written_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON im.digests USING gin (segment_ids);

CREATE TABLE im.digest_points (
  digest_id  bigint NOT NULL REFERENCES im.digests(digest_id) ON DELETE CASCADE,
  ord        smallint NOT NULL,
  kind       text NOT NULL CHECK (kind IN ('recurring', 'convergence', 'sleeper', 'project', 'outside_value')),
  title      text NOT NULL,
  body       text NOT NULL,
  idea_ids   uuid[] NOT NULL,
  PRIMARY KEY (digest_id, ord)
);

-- My response to a point: digest_star (this was worth surfacing) or digest_dismiss (it wasn't), payload
-- {"digest_id", "point"}; the latest wins. A third signal, beside comparisons and discards, for tuning what surfaces.
ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_kind_check;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_kind_check CHECK (kind IN (
  'star', 'unstar', 'keep', 'discard', 'link', 'unlink', 'pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme',
  'item_keep', 'item_discard', 'item_star', 'idea_correct', 'idea_note', 'idea_compare', 'digest_star',
  'digest_dismiss'));
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_digest CHECK (
  kind NOT IN ('digest_star', 'digest_dismiss') OR (
    coalesce(jsonb_typeof(payload->'digest_id'), '') = 'number' AND coalesce(jsonb_typeof(payload->'point'), '') = 'number'
    AND (NOT payload ? 'note' OR length(payload->>'note') <= 500)));

-- Digests, newest first, with their points in order. A point's ideas are those still shown (forgetting or my
-- discards can remove one); mine is my latest response to the point: 'star', 'dismiss' or NULL.
CREATE VIEW pub.digests AS
SELECT d.digest_id, d.week_start, d.period_start, d.period_end, d.summary, d.written_at,
       coalesce((SELECT jsonb_agg(jsonb_build_object(
                   'point', p.ord, 'kind', p.kind, 'title', p.title, 'body', p.body,
                   'idea_ids', to_jsonb(ARRAY(SELECT u FROM unnest(p.idea_ids) u
                                              WHERE u IN (SELECT idea_id FROM pub.ideas))),
                   'mine', (SELECT replace(f.kind, 'digest_', '') FROM pub.feedback_events f
                            WHERE f.kind IN ('digest_star', 'digest_dismiss')
                              AND f.payload->>'digest_id' = d.digest_id::text AND f.payload->>'point' = p.ord::text
                            ORDER BY f.event_id DESC LIMIT 1)) ORDER BY p.ord)
                 FROM im.digest_points p WHERE p.digest_id = d.digest_id), '[]') AS points
FROM im.digests d;

GRANT SELECT ON pub.digests TO lattice_app;

-- Run status: the digest stage's stop reason is a warning too. (Same columns, so OR REPLACE works.)
CREATE OR REPLACE VIEW pub.runs AS
SELECT r.run_id, r.started_at, r.finished_at,
       r.stats->>'error' AS error,
       array_remove(ARRAY[r.stats->'extract'->>'stopped', r.stats->'lattice'->>'stopped', r.stats->'themes'->>'stopped',
                          r.stats->'themes'->'file'->>'stopped', r.stats->'themes'->'propose'->>'stopped',
                          r.stats->'scores'->>'stopped', r.stats->'digest'->>'stopped'], NULL)
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
