-- Names GLiNER found in each segment, for policy C (decision 0008): found once per segment and finder, not once per
-- process per run. A segment whose text changed is looked at again (text_sha256); a forgotten one takes its names
-- with it. Filled by whichever box runs GLiNER (the NAS's CPU, or eeyore's GPU through corral).
CREATE TABLE im.segment_names (
  segment_id   uuid NOT NULL REFERENCES im.source_segments(segment_id) ON DELETE CASCADE,
  finder       text NOT NULL,            -- model@threshold, as in the policy version
  text_sha256  text NOT NULL,
  names        text[] NOT NULL,
  found_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (segment_id, finder)
);
