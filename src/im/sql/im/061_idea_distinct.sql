-- The merge screen (`im merges`, B9m): idea_distinct on two clusters is my mark that they make distinct points, so
-- the screen stops listing them as a possible duplicate (`im merge --distinct A B`). It changes nothing else: the
-- pair may still be connected, and I can still merge it.
ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_kind_check;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_kind_check CHECK (kind IN (
  'star', 'unstar', 'keep', 'discard', 'link', 'unlink', 'pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme',
  'item_keep', 'item_discard', 'item_star', 'idea_correct', 'idea_note', 'idea_compare', 'digest_star',
  'digest_dismiss', 'idea_add', 'merge_theme', 'split_theme', 'file_idea', 'unfile_idea', 'idea_sensitive',
  'theme_sensitive', 'vocab_add', 'vocab_remove', 'idea_keep', 'idea_unkeep', 'idea_dismiss', 'idea_distinct'));
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_idea_distinct CHECK (
  kind <> 'idea_distinct' OR (idea_id IS NOT NULL AND other_idea_id IS NOT NULL AND idea_id <> other_idea_id));
