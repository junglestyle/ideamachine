-- Echoes (docs/design/m6-emanation.md) shows why two cards are connected, and later views show the pairs the judge
-- turned down. pub.card_links has a connection's kinds but not the judge's direction, its reason, or the structure it
-- named when the two are analogous; and a `none` is in no pub view at all. This is every verdict the connection judge
-- reached (im.connection_verdicts, 059) on two cards, as it stands: a is the lower id, and `a>b` reads "a supports
-- (or elaborates) b". `why` and `analogy` are the judge's words, and may paraphrase what was said.
CREATE VIEW pub.card_judgments AS
SELECT v.a, v.b, v.verdict, v.direction, nullif(v.analogy, '') AS analogy, v.why, v.belongs, v.method
FROM im.connection_verdicts v
WHERE v.a IN (SELECT idea_id FROM pub.cards) AND v.b IN (SELECT idea_id FROM pub.cards);

GRANT SELECT ON pub.card_judgments TO lattice_app;
