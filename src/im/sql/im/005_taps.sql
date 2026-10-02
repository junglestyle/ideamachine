-- Pendant taps from Hearsay's index (a single press keeps what I said around it, as a self-note).
ALTER TABLE im.source_conversations ADD COLUMN taps timestamptz[] NOT NULL DEFAULT '{}';
