-- Trying a candidate extraction prompt on episodes I've already judged, before switching to it (ROADMAP Phase 2,
-- slice 3). What the candidate captured stays here, apart from im.items, so it never reaches my review queue.
-- Every request is in im.egress_log like any other, and counts toward the monthly cap.
CREATE TABLE im.prompt_trials (
  episode_id      uuid NOT NULL REFERENCES im.episodes(episode_id) ON DELETE CASCADE,
  prompt_version  text NOT NULL,
  model           text NOT NULL,
  payload_sha256  text NOT NULL,
  egress_id       bigint NOT NULL REFERENCES im.egress_log(egress_id),
  items           jsonb NOT NULL,          -- [{kind, said_by, quote, gist, confidence, source_segment_ids}]
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (episode_id, prompt_version, model)
);
