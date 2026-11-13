-- The attention queue (im.attention): what I asked to look at again, by tapping the pendant or saying "note to self".
-- Found in code, never by a model. An item marks lines and changes nothing about them: extraction, captures and cards
-- go on as if it weren't there. Removing one (`im queue remove`) sets removed_at, and that's all it does.
-- - trigger: `tap` or `note_to_self`.
-- - at: the tap's time, or when the line saying "note to self" starts.
-- - conversation_id: where the lines are (for a tap Hearsay lists in two conversations, the first by id).
-- - cue_segment_id: a note's line saying "note to self"; NULL for a tap.
-- - segment_ids: the lines it marks, in the order said, re-anchored to current lines each run.
-- A tap is one item for as long as Hearsay lists its time, whatever its conversation is called after a split or a
-- merge. A note is one item for its line, and for the line a re-transcription makes of it if that still says "note to
-- self" (it descends from the old one through im.source_supersessions). Forgetting deletes a note whose own line is
-- forgotten, and takes forgotten lines out of every other item (pipeline._purge_tombstoned).
CREATE TABLE im.attention_items (
  attention_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  trigger          text NOT NULL CHECK (trigger IN ('tap', 'note_to_self')),
  at               timestamptz NOT NULL,
  conversation_id  text NOT NULL,
  cue_segment_id   uuid NULL,
  segment_ids      uuid[] NOT NULL,
  schema_version   integer NOT NULL,
  stage_version    text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  removed_at       timestamptz NULL,
  CHECK ((trigger = 'note_to_self') = (cue_segment_id IS NOT NULL))
);
CREATE UNIQUE INDEX attention_items_tap ON im.attention_items (at) WHERE trigger = 'tap';
CREATE UNIQUE INDEX attention_items_note ON im.attention_items (cue_segment_id) WHERE trigger = 'note_to_self';
