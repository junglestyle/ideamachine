-- Triage (ROADMAP Phase 1). Derived and disposable: `im reset --stage triage`
-- deletes it. Rows hang off episodes, so they go when their episode is purged.

-- One row per episode and backend/model/prompt. A changed episode is a new
-- episode, so it gets new rows; old ones stay with the retired episode.
CREATE TABLE im.triage (
  episode_id      uuid NOT NULL REFERENCES im.episodes(episode_id) ON DELETE CASCADE,
  backend         text NOT NULL,            -- laya | fallback
  model           text NOT NULL,
  model_version   text NOT NULL,            -- weights revision or trained-model hash
  prompt_version  text NOT NULL,            -- hash of the question set as sent
  answers         jsonb NOT NULL,           -- {question: {value, probabilities, confidence}}
  state_tokens    integer NULL,
  truncated       boolean NOT NULL DEFAULT false,
  latency_ms      integer NULL,
  schema_version  integer NOT NULL,
  stage_version   text NOT NULL,
  input_hash      text NOT NULL,            -- hash of the rendered episode state
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (episode_id, backend, model_version, prompt_version)
);

-- Sentence embeddings of episodes: the fallback classifier's features now,
-- related-episode search in Phase 2.
CREATE TABLE im.episode_embeddings (
  episode_id      uuid NOT NULL REFERENCES im.episodes(episode_id) ON DELETE CASCADE,
  model           text NOT NULL,
  model_version   text NOT NULL,
  embedding       vector NOT NULL,
  schema_version  integer NOT NULL,
  stage_version   text NOT NULL,
  input_hash      text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (episode_id, model, model_version)
);

-- Trained fallback classifiers, stored as plain coefficients (no pickles).
-- Not disposable like triage rows: they're trained from labels on request.
CREATE TABLE im.classifiers (
  model_version   text PRIMARY KEY,         -- hash of embedding model + training labels + params
  embedding_model text NOT NULL,
  label_ids       bigint[] NOT NULL,
  params          jsonb NOT NULL,           -- per question: classes, coef, intercept
  trained_at      timestamptz NOT NULL DEFAULT now()
);
