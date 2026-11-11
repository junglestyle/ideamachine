-- Lattice, the old presentation app, and its MCP listener left the deploy on 2026-11-11 (docs/deploy.md). What only
-- it read goes: its review queue and the capture's context lines (010), the run status and month's spend (012, 022),
-- its map positions (015; the sky reads pub.card_positions), the neighbours and search by meaning (026; the web view
-- reads pub.card_neighbours), and the columns of pub.ideas only Lattice showed: the archive's reference (always NULL
-- since 057), the notes, last said, kept and discarded counts, and what Claude first heard beside my corrections
-- (im.idea_text keeps them all). The MCP server M5 builds here adds the views its tools need.
--
-- The web view's role keeps the name lattice_app: 008 to 052 grant to it by name, so a new database needs it under
-- that name while they run, and this pipeline role can't rename a role.

DROP FUNCTION pub.ideas_near(vector, int);
DROP VIEW pub.idea_neighbours, pub.idea_positions, pub.review_items, pub.item_context, pub.runs, pub.spend;

-- A view's columns can only be added to, so pub.ideas is made again, with what's built on it, as before.
DROP VIEW pub.demo_themes, pub.demo_card_evidence, pub.demo_cards, pub.demo_hidden_ideas, pub.sensitivity,
  pub.digests, pub.connections, pub.ideas;

-- The archive's references and theme origin, unused since the cutover (057): no idea or theme comes from it.
ALTER TABLE im.ideas DROP COLUMN archive_ref;
ALTER TABLE im.themes DROP COLUMN archive_ref;
ALTER TABLE im.themes DROP CONSTRAINT themes_origin_check;
ALTER TABLE im.themes ADD CONSTRAINT themes_origin_check CHECK (origin IN ('clustered', 'mine'));

-- As in 057, with only the columns the pipeline reads (the digest, theme filing, `im lattice`).
CREATE VIEW pub.ideas AS
SELECT d.idea_id, x.title, x.statement, d.origin, d.created_at,
       count(ev.item_id) AS n_evidence, min(ev.said_at) AS first_said,
       coalesce((SELECT array_agg(t.name ORDER BY t.name) FROM im.idea_themes it JOIN im.themes t USING (theme_id)
                 WHERE it.idea_id = d.idea_id), '{}') AS themes,
       coalesce((SELECT f.kind = 'star' FROM pub.feedback_events f
                 WHERE f.idea_id = d.idea_id AND f.kind IN ('star', 'unstar') ORDER BY f.event_id DESC LIMIT 1),
                false) AS starred
FROM im.ideas d
JOIN im.idea_text x USING (idea_id)
LEFT JOIN pub.evidence ev ON ev.idea_id = d.idea_id
GROUP BY d.idea_id, x.title, x.statement
HAVING d.origin = 'mine'
    OR count(*) FILTER (WHERE ev.item_id IS NOT NULL AND ev.verdict IS DISTINCT FROM 'discard') > 0;

-- As in 045.
CREATE VIEW pub.connections AS
SELECT s.a, s.b, s.kind, coalesce(s.confidence, 1)::real AS weight, s.method
FROM im.shown_connections s
WHERE s.a IN (SELECT idea_id FROM pub.ideas) AND s.b IN (SELECT idea_id FROM pub.ideas)
UNION ALL
SELECT l.from_id, l.to_id, l.kind, 1::real, l.method
FROM im.lineage l
WHERE l.from_id IN (SELECT idea_id FROM pub.ideas) AND l.to_id IN (SELECT idea_id FROM pub.ideas);

-- As in 022.
CREATE VIEW pub.digests AS
SELECT d.digest_id, d.week_start, d.period_start, d.period_end, d.summary, d.written_at,
       coalesce((SELECT jsonb_agg(jsonb_build_object(
                   'point', p.ord, 'kind', p.kind, 'title', p.title, 'body', p.body,
                   'idea_ids', to_jsonb(ARRAY(SELECT u FROM unnest(p.idea_ids) u
                                              WHERE u IN (SELECT idea_id FROM pub.ideas))),
                   'mine', (SELECT replace(f.kind, 'digest_', '') FROM pub.feedback_events f
                            WHERE f.kind IN ('digest_star', 'digest_dismiss')
                              AND f.payload->>'digest_id' = d.digest_id::text AND f.payload->>'point' = p.ord::text
                            ORDER BY f.event_id DESC LIMIT 1)) ORDER BY p.ord)
                 FROM im.digest_points p WHERE p.digest_id = d.digest_id), '[]') AS points
FROM im.digests d;

-- As in 052.
CREATE VIEW pub.sensitivity AS
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

-- The web view's role reads all of pub, as before.
GRANT SELECT ON pub.ideas, pub.connections, pub.digests, pub.sensitivity, pub.demo_hidden_ideas, pub.demo_cards,
  pub.demo_card_evidence, pub.demo_themes TO lattice_app;
