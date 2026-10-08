-- The map in Lattice (ROADMAP Phase 2, slice 5): where each idea sits, and the links I draw myself.

-- A 2D projection of the idea embeddings, recomputed every run and aligned to the previous run's, so new ideas
-- don't reshuffle the map. Roughly within [-1, 1]. `method` names the projection and the embedding model_version.
CREATE TABLE im.idea_positions (
  idea_id      uuid PRIMARY KEY REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  x            real NOT NULL,
  y            real NOT NULL,
  method       text NOT NULL,
  computed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE VIEW pub.idea_positions AS
SELECT p.idea_id, p.x, p.y, p.method FROM im.idea_positions p
WHERE p.idea_id IN (SELECT idea_id FROM pub.ideas);

-- My links: a link or unlink names two different ideas. NOT VALID: checked for new events, not old ones.
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_link_kinds CHECK (
  kind NOT IN ('link', 'unlink') OR (idea_id IS NOT NULL AND other_idea_id IS NOT NULL AND idea_id <> other_idea_id))
  NOT VALID;

-- pub.connections plus my links, read straight from the events so a link shows at once: one 'mine' row per
-- unordered pair whose latest link/unlink event is a link, with a < b. (Same columns, so OR REPLACE works.)
CREATE OR REPLACE VIEW pub.connections AS
SELECT l.a, l.b, l.kind, l.weight, l.method
FROM im.idea_links l
WHERE l.a IN (SELECT idea_id FROM pub.ideas) AND l.b IN (SELECT idea_id FROM pub.ideas)
UNION ALL
SELECT m.a, m.b, 'mine', 1::real, 'feedback'
FROM (SELECT DISTINCT ON (least(f.idea_id, f.other_idea_id), greatest(f.idea_id, f.other_idea_id))
             least(f.idea_id, f.other_idea_id) AS a, greatest(f.idea_id, f.other_idea_id) AS b, f.kind
      FROM pub.feedback_events f
      WHERE f.kind IN ('link', 'unlink') AND f.idea_id <> f.other_idea_id
      ORDER BY least(f.idea_id, f.other_idea_id), greatest(f.idea_id, f.other_idea_id), f.event_id DESC) m
WHERE m.kind = 'link' AND m.a IN (SELECT idea_id FROM pub.ideas) AND m.b IN (SELECT idea_id FROM pub.ideas);

GRANT SELECT ON pub.idea_positions TO lattice_app;
