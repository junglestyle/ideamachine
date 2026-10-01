-- Idea Machine's own schema. Derived tables are disposable: change them by
-- dropping and rebuilding, not by migrating data (ROADMAP §6).

-- Poll cursor per upstream table.
CREATE TABLE im.ingest_state (
  name        text PRIMARY KEY,         -- e.g. 'hearsay.segments'
  last_seq    bigint NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- One row per `im run` / `im reset`.
CREATE TABLE im.runs (
  run_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  command      text NOT NULL,
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz NULL,
  stats        jsonb NULL
);

-- Episodes are content-addressed: episode_id = uuid5(stage_version, input_hash),
-- so the same inputs and heuristic always give the same episode.
-- Superseded episodes are retired (current = false), not deleted. Only a
-- tombstone or `im reset` deletes them.
CREATE TABLE im.episodes (
  episode_id      uuid PRIMARY KEY,
  session_id      text NOT NULL,
  source          text NOT NULL,
  started_at      timestamptz NOT NULL,
  ended_at        timestamptz NOT NULL,
  kind            text NOT NULL,        -- monologue | conversation | others_only
  n_segments      integer NOT NULL,
  n_speakers      integer NOT NULL,     -- distinct speaker_label
  self_segments   integer NOT NULL,     -- segments attributed to me (fail-closed)
  current         boolean NOT NULL DEFAULT true,
  retired_at      timestamptz NULL,
  schema_version  integer NOT NULL,
  stage_version   text NOT NULL,
  input_hash      text NOT NULL,        -- sha256 over the sorted segment_ids
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (current = (retired_at IS NULL))
);
CREATE INDEX ON im.episodes (session_id) WHERE current;

CREATE TABLE im.episode_segments (
  episode_id  uuid NOT NULL REFERENCES im.episodes(episode_id) ON DELETE CASCADE,
  segment_id  uuid NOT NULL,            -- hearsay.segments.segment_id
  ord         integer NOT NULL,
  PRIMARY KEY (episode_id, segment_id)
);
CREATE INDEX ON im.episode_segments (segment_id);
