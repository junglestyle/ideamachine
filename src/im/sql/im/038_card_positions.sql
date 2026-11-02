-- The sky (docs/design/m6-emanation.md; decision 0005, amended 2026-11-02): one stable map of every card, the neutral
-- view's clusters, not only the ideas Lattice shows. lattice.place now places every card; Lattice's own
-- pub.idea_positions is unchanged.
CREATE VIEW pub.card_positions AS
SELECT p.idea_id, p.x, p.y FROM im.idea_positions p
WHERE p.idea_id IN (SELECT idea_id FROM pub.cards);

GRANT SELECT ON pub.card_positions TO lattice_app;
