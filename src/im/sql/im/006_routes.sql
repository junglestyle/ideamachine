-- Routing (ROADMAP Phase 1). A route is a label: nothing is filtered or hidden.
CREATE TABLE im.routes (
  episode_id            uuid NOT NULL REFERENCES im.episodes(episode_id) ON DELETE CASCADE,
  router_version        text NOT NULL,
  route                 text NOT NULL CHECK (route IN ('auto_file', 'review', 'escalate')),
  reasons               text[] NOT NULL,      -- e.g. {tap, note_to_self, llm:idea, llm:keep>=3}
  triage_backend        text NULL,            -- the triage row the route read, if any
  triage_model_version  text NULL,
  prompt_version        text NULL,
  schema_version        integer NOT NULL,
  stage_version         text NOT NULL,
  input_hash            text NOT NULL,        -- the rendered episode the route was computed for
  created_at            timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (episode_id, router_version)
);

-- Where a label came from: `label` (im label, sampled to cover every kind of episode)
-- or `review` (im review, sampled from what the router flagged). Evals keep them apart.
ALTER TABLE im.labels ADD COLUMN source text NOT NULL DEFAULT 'label' CHECK (source IN ('label', 'review'));
