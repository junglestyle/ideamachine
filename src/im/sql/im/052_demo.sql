-- Demo mode, for any presentation layer (decision 0012): the views that carry words again, with nothing sensitive
-- said (pub.demo_*). Anything pub.sensitivity counts as sensitive (any tag, unless my mark says otherwise) keeps its
-- place but loses its words:
-- - a card stays, with its counts, dates, maturity and edges, so the map keeps its shape, but its title, statement and
--   my note are NULL and `redacted` says why;
-- - its quotes and gists aren't listed at all, and neither is a sensitive theme.
-- Whether a presentation blanks a redacted card or leaves it out is its own choice (a map blanks it, a list leaves it
-- out); nothing sensitive is in these views to show either way. Structure (pub.card_links, card_lineage,
-- card_positions, card_neighbours) carries no words, so demo mode reads it as it is.

-- First, pub.sensitivity covers every card too, not only pub.ideas: a card also shows a cluster whose every capture
-- I discarded (033), and im.sensitivity tags those now. Otherwise as in 051.
CREATE OR REPLACE VIEW pub.sensitivity AS
WITH idea_marks AS (
  SELECT DISTINCT ON (f.idea_id) f.idea_id AS id, (f.payload->>'sensitive')::boolean AS sensitive
  FROM pub.feedback_events f WHERE f.kind = 'idea_sensitive' ORDER BY f.idea_id, f.event_id DESC
), theme_marks AS (
  SELECT DISTINCT ON (f.theme_id) f.theme_id AS id, (f.payload->>'sensitive')::boolean AS sensitive
  FROM pub.feedback_events f WHERE f.kind = 'theme_sensitive' AND f.theme_id IS NOT NULL
  ORDER BY f.theme_id, f.event_id DESC
), shown AS (
  SELECT idea_id FROM pub.ideas UNION SELECT idea_id FROM pub.cards
), both_kinds AS (
  SELECT 'idea' AS kind, d.idea_id AS id, m.sensitive AS mine, s.categories
  FROM shown d LEFT JOIN idea_marks m ON m.id = d.idea_id LEFT JOIN im.idea_sensitivity s USING (idea_id)
  UNION ALL
  SELECT 'theme', t.theme_id, m.sensitive, s.categories
  FROM im.themes t LEFT JOIN theme_marks m ON m.id = t.theme_id LEFT JOIN im.theme_sensitivity s USING (theme_id)
)
SELECT kind, id, coalesce(mine, cardinality(categories) > 0) AS sensitive, coalesce(categories, '{}') AS categories,
       CASE WHEN mine IS NOT NULL THEN 'owner' ELSE 'model' END AS "by"
FROM both_kinds
WHERE mine IS NOT NULL OR cardinality(categories) > 0;

CREATE VIEW pub.demo_hidden_ideas AS
SELECT id AS idea_id FROM pub.sensitivity WHERE kind = 'idea' AND sensitive;

CREATE VIEW pub.demo_cards AS
SELECT c.idea_id,
       CASE WHEN h.idea_id IS NULL THEN c.title END AS title,
       CASE WHEN h.idea_id IS NULL THEN c.statement END AS statement,
       c.origin, c.maturity, c.kept_as,
       CASE WHEN h.idea_id IS NULL THEN c.my_note END AS my_note,
       c.observations, c.by_me, c.by_me_diar, c.by_others, c.conversations, c.first_said, c.last_seen, c.links,
       c.proposed, h.idea_id IS NOT NULL AS redacted
FROM pub.cards c LEFT JOIN pub.demo_hidden_ideas h USING (idea_id);

CREATE VIEW pub.demo_card_evidence AS
SELECT e.* FROM pub.card_evidence e WHERE e.idea_id NOT IN (SELECT idea_id FROM pub.demo_hidden_ideas);

CREATE VIEW pub.demo_themes AS
SELECT t.* FROM pub.themes t
WHERE t.theme_id NOT IN (SELECT id FROM pub.sensitivity WHERE kind = 'theme' AND sensitive);

GRANT SELECT ON pub.demo_hidden_ideas, pub.demo_cards, pub.demo_card_evidence, pub.demo_themes TO lattice_app;
