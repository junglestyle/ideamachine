-- Idea extraction with Claude (docs/decisions/0004-claude-extraction.md).

-- Every request that leaves the box: what went out, under which policy, and which segments it carried.
-- Forgetting deletes `payload` (the text) but keeps `segment_ids`, so `im forgotten --sent` can report it.
CREATE TABLE im.egress_log (
  egress_id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  episode_id              uuid NOT NULL,           -- provenance only; episodes are disposable
  segment_ids             uuid[] NOT NULL,
  model                   text NOT NULL,
  prompt_version          text NOT NULL,
  privacy_policy_version  text NOT NULL,
  payload                 text NULL,               -- exactly what was sent (system prompt excluded); NULL once forgotten
  payload_sha256          text NOT NULL,
  request_id              text NULL,
  stop_reason             text NULL,
  served_by               text NULL,               -- the model that answered (differs after a refusal fallback)
  input_tokens            integer NULL,
  output_tokens           integer NULL,
  cost_usd                numeric(10, 6) NULL,
  error                   text NULL,
  created_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON im.egress_log USING gin (segment_ids);
CREATE INDEX ON im.egress_log (created_at);

-- What Claude captured. Derived: tied to the episode (and purged with it), with the segments each item
-- came from and who said it, resolved locally from the pseudonym Claude saw.
CREATE TABLE im.items (
  item_id                 uuid PRIMARY KEY,        -- uuid5(episode, model, prompt, policy, item index)
  episode_id              uuid NOT NULL REFERENCES im.episodes(episode_id) ON DELETE CASCADE,
  kind                    text NOT NULL,
  said_by                 text NOT NULL,           -- local speaker label (me, a name, anon A); never sent as-is
  quote                   text NOT NULL,
  gist                    text NOT NULL,
  themes                  text[] NOT NULL,
  confidence              real NOT NULL,
  source_segment_ids      uuid[] NOT NULL,
  egress_id               bigint NOT NULL REFERENCES im.egress_log(egress_id),
  model                   text NOT NULL,
  prompt_version          text NOT NULL,
  privacy_policy_version  text NOT NULL,
  schema_version          integer NOT NULL,
  stage_version           text NOT NULL,
  input_hash              text NOT NULL,
  created_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON im.items USING gin (source_segment_ids);

-- Episodes Claude has read, including the ones where it found nothing (no items).
CREATE TABLE im.extractions (
  episode_id              uuid NOT NULL REFERENCES im.episodes(episode_id) ON DELETE CASCADE,
  model                   text NOT NULL,
  prompt_version          text NOT NULL,
  privacy_policy_version  text NOT NULL,
  input_hash              text NOT NULL,
  egress_id               bigint NOT NULL REFERENCES im.egress_log(egress_id),
  n_items                 integer NOT NULL,
  created_at              timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (episode_id, model, prompt_version, privacy_policy_version)
);

-- My verdicts on captured items. Human input: anchored to the item's segments and quote as well as its id,
-- so a verdict can be matched again if the item is re-extracted. Forgetting deletes it.
CREATE TABLE im.item_verdicts (
  verdict_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  item_id         uuid NOT NULL,
  segment_ids     uuid[] NOT NULL,
  quote           text NOT NULL,
  verdict         text NOT NULL CHECK (verdict IN ('keep', 'discard')),
  note            text NULL,
  decided_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON im.item_verdicts (item_id);
CREATE INDEX ON im.item_verdicts USING gin (segment_ids);
