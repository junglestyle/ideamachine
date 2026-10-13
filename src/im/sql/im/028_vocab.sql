-- My vocabulary for Hearsay: words its transcription should know, so Parakeet hears "goetia" instead of "go Asia".
-- Idea Machine finds candidates on each run and writes the approved list to a file Hearsay reads (IM_VOCAB_PATH):
-- - correction: rare words I put into an idea when correcting Claude's version. Approved automatically.
-- - repair: a rare word in an idea that its quote misheard (heard_as), found by Claude. A suggestion.
-- - archive: rare words in my archive's ideas, which I typed, so they're spelled right. A suggestion.
-- - added: words I type into Lattice's vocabulary list. Approved.
-- I approve or remove with vocab_add / vocab_remove; my latest event per term (any case) wins, and a removed term
-- is never suggested again unless I add it.

CREATE TABLE im.vocab_candidates (
  term_key  text NOT NULL,              -- lower(term)
  term      text NOT NULL,
  source    text NOT NULL CHECK (source IN ('correction', 'repair', 'archive')),
  heard_as  text NULL,                  -- repairs: what the quote says instead
  idea_id   uuid NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  item_id   uuid NULL REFERENCES im.items(item_id) ON DELETE CASCADE,   -- a repair's quote: forgetting takes it
  example   text NULL,
  found_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (term_key, source)
);

-- Which of the archive's rare words Claude picked as ones speech-to-text would get wrong (names, foreign words,
-- coinages, terms of art), for which list of words: picked again only when that list changes.
CREATE TABLE im.vocab_picks (
  words_hash      text PRIMARY KEY,
  picked          text[] NOT NULL,
  finder_version  text NOT NULL,
  picked_at       timestamptz NOT NULL DEFAULT now()
);

-- Ideas Claude has checked for misheard words, against which text and quotes.
CREATE TABLE im.vocab_checks (
  idea_id         uuid PRIMARY KEY REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  input_hash      text NOT NULL,
  finder_version  text NOT NULL,
  checked_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_kind_check;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_kind_check CHECK (kind IN (
  'star', 'unstar', 'keep', 'discard', 'link', 'unlink', 'pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme',
  'item_keep', 'item_discard', 'item_star', 'idea_correct', 'idea_note', 'idea_compare', 'digest_star',
  'digest_dismiss', 'idea_add', 'merge_theme', 'split_theme', 'file_idea', 'unfile_idea', 'idea_sensitive',
  'theme_sensitive', 'vocab_add', 'vocab_remove'));
-- payload {"term": 1-60 chars on one line, "source"?: correction | repair | archive | added (vocab_add only)}.
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_vocab CHECK (
  kind NOT IN ('vocab_add', 'vocab_remove') OR (
    coalesce(jsonb_typeof(payload->'term'), '') = 'string'
    AND length(btrim(payload->>'term')) BETWEEN 1 AND 60 AND payload->>'term' !~ '[\r\n]'
    AND (NOT payload ? 'source' OR (kind = 'vocab_add'
         AND coalesce(payload->>'source', '') IN ('correction', 'repair', 'archive', 'added')))));

-- Every term I've acted on or Idea Machine suggests: status 'in' (goes to Hearsay), 'suggested', or 'removed'.
-- A term found more than one way shows its strongest source: correction, then repair, then archive.
CREATE VIEW pub.vocab AS
WITH mine AS (
  SELECT DISTINCT ON (lower(btrim(f.payload->>'term'))) lower(btrim(f.payload->>'term')) AS term_key,
         btrim(f.payload->>'term') AS term, f.kind, coalesce(f.payload->>'source', 'added') AS source
  FROM pub.feedback_events f WHERE f.kind IN ('vocab_add', 'vocab_remove')
  ORDER BY lower(btrim(f.payload->>'term')), f.event_id DESC
), found AS (
  SELECT DISTINCT ON (c.term_key) c.*
  FROM im.vocab_candidates c
  WHERE c.idea_id IS NULL OR c.idea_id IN (SELECT idea_id FROM pub.ideas)
  ORDER BY c.term_key, CASE c.source WHEN 'correction' THEN 0 WHEN 'repair' THEN 1 ELSE 2 END
)
SELECT coalesce(CASE WHEN m.kind = 'vocab_add' THEN m.term END, c.term, m.term) AS term,
       CASE WHEN m.kind = 'vocab_add' THEN m.source ELSE coalesce(c.source, m.source) END AS source,
       c.heard_as,
       CASE WHEN m.kind = 'vocab_add' THEN 'in' WHEN m.kind = 'vocab_remove' THEN 'removed'
            WHEN c.source = 'correction' THEN 'in' ELSE 'suggested' END AS status,
       c.idea_id, c.example
FROM found c FULL JOIN mine m USING (term_key);

GRANT SELECT ON pub.vocab TO lattice_app;
