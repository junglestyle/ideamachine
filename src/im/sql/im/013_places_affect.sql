-- Hearsay's places and affect (ROADMAP §3.1). Both are annotations, kept up to date in place like taps.

-- The places I named that the conversation happened at, from the index, in order:
-- [{"name", "start", "end"}]. Naming is retroactive, so they change without a new revision.
ALTER TABLE im.source_conversations ADD COLUMN places jsonb NOT NULL DEFAULT '[]';

-- How animated I sounded (about 0-1, from the audio alone; tone of voice, not sentiment). NULL where
-- Hearsay didn't score the turn. Not part of segment_id, so a rescoring doesn't re-derive anything.
ALTER TABLE im.source_segments ADD COLUMN arousal real NULL;
