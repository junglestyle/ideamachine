-- Carrying my verdicts forward when a capture is re-extracted (re-transcription, a new episode, a new prompt).
-- A verdict is on an item, and an item belongs to one episode; when Claude captures the same speech again on the
-- episode that replaced it, the verdict is copied to the new item instead of asking me again.

-- Each retired item I'd decided on is settled once: matched to the current item it became.
CREATE TABLE im.verdict_carries (
  from_item_id  uuid PRIMARY KEY,
  to_item_id    uuid NOT NULL,
  score         real NOT NULL,          -- share of the shorter quote's words found in order in the other
  carried       boolean NOT NULL,       -- false: the new item already had a verdict of mine, which stands
  method        text NOT NULL,
  carried_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON im.verdict_carries (to_item_id);

-- A copied verdict points at the one it was copied from, keeps its decided_at and note, and goes with it.
ALTER TABLE im.item_verdicts ADD COLUMN carried_from bigint NULL
  REFERENCES im.item_verdicts(verdict_id) ON DELETE CASCADE;
