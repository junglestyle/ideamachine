-- Retire my vocabulary for Hearsay (028), per decision 0012: the vocabulary sent to Hearsay hurt transcription, and
-- learned mishearings (050), applied when reading, replace it. Nothing reads these any more. Past vocab_add and
-- vocab_remove events stay in the ledger, unread; their payload check goes with the view that read them.
DROP VIEW pub.vocab;
ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_vocab;
DROP TABLE im.vocab_candidates, im.vocab_picks, im.vocab_checks;
