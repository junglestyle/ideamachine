-- Phase 1 step 2: Idea Machine's own copy of Hearsay's utterance stream
-- (ROADMAP §3.3). Replaces the seq cursor on the stand-in hearsay schema.

DROP TABLE im.ingest_state;

-- What was last imported per conversation.
CREATE TABLE im.source_conversations (
  conversation_id      text PRIMARY KEY,
  revision             text NULL,        -- hash of the conversation file; NULL if untranscribed
  transcript_revision  text NULL,
  imported_at          timestamptz NOT NULL DEFAULT now()
);

-- Append-only: every utterance version seen. segment_id is a uuid5 over the
-- conversation_id and the utterance's content without its utterance_id, so an
-- unchanged utterance keeps its id even when Hearsay renumbers it.
-- Rows are deleted only when forgotten.
CREATE TABLE im.source_segments (
  segment_id           uuid PRIMARY KEY,
  conversation_id      text NOT NULL,
  started_at           timestamptz NOT NULL,
  ended_at             timestamptz NOT NULL,
  text                 text NOT NULL,
  speaker_kind         text NOT NULL,    -- owner | person | anonymous | stranger | unknown
  speaker_name         text NULL,
  speaker_label        text NOT NULL,    -- display: me, a name, "anon A", unknown
  speaker_basis        text NULL,        -- voice | diarization | named | cluster | none
  is_self              boolean NULL,     -- true for owner, NULL for unknown, else false
  speaker_conf         real NULL,        -- owner_similarity
  asr_confidence       real NULL,        -- text_confidence
  schema_version       integer NOT NULL,
  stage_version        text NOT NULL,
  input_hash           text NOT NULL,    -- sha256 of the utterance record minus utterance_id
  first_seen_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON im.source_segments (started_at, ended_at);

-- Which segments the latest import of each conversation contains. This, not
-- supersession, decides what is current.
CREATE TABLE im.source_members (
  conversation_id  text NOT NULL,
  segment_id       uuid NOT NULL REFERENCES im.source_segments(segment_id),
  utterance_id     text NOT NULL,        -- as numbered in that import
  PRIMARY KEY (conversation_id, segment_id)
);
CREATE INDEX ON im.source_members (segment_id);

-- For traceability and for carrying labels forward. Many-to-many, so splits and merges fit.
CREATE TABLE im.source_supersessions (
  old_segment_id  uuid NOT NULL REFERENCES im.source_segments(segment_id),
  new_segment_id  uuid NOT NULL REFERENCES im.source_segments(segment_id),
  method          text NOT NULL,         -- utterance_id | time_overlap
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (old_segment_id, new_segment_id)
);
CREATE INDEX ON im.source_supersessions (new_segment_id);

-- Segments purged because of a forgotten.json entry. No text is kept here.
CREATE TABLE im.source_tombstones (
  segment_id    uuid PRIMARY KEY,
  forgotten_at  timestamptz NOT NULL,
  reason        text NULL,
  purged_at     timestamptz NOT NULL DEFAULT now()
);

CREATE VIEW im.current_segments AS
SELECT s.*, m.utterance_id FROM im.source_segments s
JOIN im.source_members m USING (segment_id, conversation_id)
WHERE NOT EXISTS (SELECT 1 FROM im.source_tombstones t WHERE t.segment_id = s.segment_id);
