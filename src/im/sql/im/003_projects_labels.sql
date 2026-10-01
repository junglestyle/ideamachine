-- Human input. Unlike derived tables, these are migrated carefully and never
-- dropped to rebuild (ROADMAP §6). `im reset` doesn't touch them.

-- Hand-maintained with `im project`.
CREATE TABLE im.projects (
  slug         text PRIMARY KEY CHECK (slug ~ '^[a-z0-9][a-z0-9-]*$'),
  aliases      text[] NOT NULL DEFAULT '{}',
  description  text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  retired_at   timestamptz NULL
);

-- One row per episode I labeled. Anchored to the episode's segments, not its
-- id, so it survives re-derivation: it resolves to current episodes by
-- following im.source_supersessions forward. Deleted only when one of its
-- segments is forgotten.
CREATE TABLE im.labels (
  label_id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  segment_ids             uuid[] NOT NULL CHECK (cardinality(segment_ids) > 0),
  questions_version       text NOT NULL,
  answers                 jsonb NOT NULL,
  note                    text NULL,
  episode_id              uuid NOT NULL,   -- provenance only; episodes are disposable
  episode_stage_version   text NOT NULL,
  labeled_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON im.labels USING gin (segment_ids);
