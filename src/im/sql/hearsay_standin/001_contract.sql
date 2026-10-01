-- DEV/TEST ONLY. A stand-in, shaped like the im.source_* tables in
-- docs/ROADMAP.md §3.3, until the stream importer (Phase 1 step 2) fills those
-- from Hearsay's utterance stream and this schema goes away.

CREATE SCHEMA hearsay;

-- Append-only. Corrections are new rows plus supersession links; nothing is UPDATEd or DELETEd.
CREATE TABLE hearsay.segments (
  segment_id      uuid PRIMARY KEY,
  seq             bigint GENERATED ALWAYS AS IDENTITY UNIQUE, -- poll cursor
  source          text NOT NULL,
  session_id      text NOT NULL,
  started_at      timestamptz NOT NULL,
  ended_at        timestamptz NOT NULL,
  text            text NOT NULL,
  asr_confidence  real NULL,
  language        text NULL,
  speaker_label   text NOT NULL,
  speaker_id      uuid NULL,
  is_self         boolean NULL,
  speaker_conf    real NULL,
  audio_ref       jsonb NULL,
  producer        text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON hearsay.segments (created_at);
CREATE INDEX ON hearsay.segments (session_id, started_at);

-- A split is one old row linked to several new rows; a merge is several old rows linked to one new row.
CREATE TABLE hearsay.segment_supersessions (
  old_segment_id  uuid NOT NULL REFERENCES hearsay.segments(segment_id),
  new_segment_id  uuid NOT NULL REFERENCES hearsay.segments(segment_id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (old_segment_id, new_segment_id)
);
CREATE INDEX ON hearsay.segment_supersessions (new_segment_id);

-- Segments that must be forgotten. Idea Machine purges everything derived from them.
CREATE TABLE hearsay.tombstones (
  segment_id  uuid PRIMARY KEY REFERENCES hearsay.segments(segment_id),
  reason      text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- What Idea Machine treats as truth: rows nobody has superseded and that aren't tombstoned.
CREATE VIEW hearsay.current_segments AS
SELECT s.* FROM hearsay.segments s
WHERE NOT EXISTS (SELECT 1 FROM hearsay.segment_supersessions x WHERE x.old_segment_id = s.segment_id)
  AND NOT EXISTS (SELECT 1 FROM hearsay.tombstones t WHERE t.segment_id = s.segment_id);

-- Idea Machine gets SELECT only.
GRANT USAGE ON SCHEMA hearsay TO im_pipeline;
GRANT SELECT ON ALL TABLES IN SCHEMA hearsay TO im_pipeline;
