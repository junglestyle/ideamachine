-- Connections, kinds and lineage (decision 0010; docs/design/connections-storage.md). im.idea_links was keyed
-- (a, b, kind), and every reader counted rows, so a pair with an embedding neighbour, an evolution and my link counted
-- three times, and a pair could be stored in both orders. Now:
-- - im.connections: one per pair, a < b. Embedding neighbours and the ChatGPT archive's cross-references stand in
--   until a model decides the pair; a model's decision replaces them (M4).
-- - im.connection_kinds: optional kinds on a connection, each with its own direction and method. Empty until a kind
--   pass writes it.
-- - im.lineage: evolves (and later synthesizes), directed, apart from connections: lenses follow it, weight doesn't.
-- - im.edge_methods: when each method first wrote, as the highest feedback event_id then, so an unlink hides what
--   every method known at the time says about the pair, and a method first used after it can connect the pair again.
-- My links stay events; the views read them at once, my latest per pair deciding.

CREATE TABLE im.connections (
  a                      uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  b                      uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  method                 text NOT NULL,
  confidence             real NULL,
  cited_observation_ids  uuid[] NOT NULL DEFAULT '{}',
  decided_at             timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (a, b),
  CHECK (a < b)
);
CREATE INDEX connections_b ON im.connections (b);

CREATE TABLE im.connection_kinds (
  a           uuid NOT NULL,
  b           uuid NOT NULL,
  kind        text NOT NULL CHECK (kind IN ('supports', 'elaborates', 'tension', 'sympathy')),
  direction   text NULL CHECK (direction IN ('a>b', 'b>a', 'both')),
  method      text NOT NULL,
  decided_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (a, b, kind),
  FOREIGN KEY (a, b) REFERENCES im.connections (a, b) ON DELETE CASCADE,
  CHECK (direction IS NULL OR kind IN ('supports', 'elaborates'))
);

CREATE TABLE im.lineage (
  from_id                uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  to_id                  uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  kind                   text NOT NULL CHECK (kind IN ('evolves', 'synthesizes')),
  method                 text NOT NULL,
  cited_observation_ids  uuid[] NOT NULL DEFAULT '{}',
  created_at             timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (from_id, to_id, kind),
  CHECK (from_id <> to_id)
);
CREATE INDEX lineage_to ON im.lineage (to_id);

CREATE TABLE im.edge_methods (
  method         text PRIMARY KEY,
  seen_event_id  bigint NOT NULL,
  first_used_at  timestamptz NOT NULL DEFAULT now()
);

-- What was stored before this migration was there before any unlink I made: an unlink I already made hides it.
INSERT INTO im.edge_methods (method, seen_event_id)
SELECT DISTINCT method, 0 FROM im.idea_links WHERE kind IN ('related', 'archive');

INSERT INTO im.connections (a, b, method, confidence, cited_observation_ids, decided_at)
SELECT DISTINCT ON (least(l.a, l.b), greatest(l.a, l.b))
       least(l.a, l.b), greatest(l.a, l.b), l.method, l.confidence, l.cited_observation_ids, l.created_at
FROM im.idea_links l
WHERE l.kind IN ('related', 'archive')
ORDER BY least(l.a, l.b), greatest(l.a, l.b), l.kind = 'archive' DESC, l.confidence DESC NULLS LAST;

INSERT INTO im.lineage (from_id, to_id, kind, method, cited_observation_ids, created_at)
SELECT l.a, l.b, 'evolves', l.method, l.cited_observation_ids, l.created_at
FROM im.idea_links l WHERE l.kind = 'evolves';

CREATE FUNCTION im.note_edge_methods() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO im.edge_methods (method, seen_event_id)
  SELECT DISTINCT n.method, (SELECT coalesce(max(f.event_id), 0) FROM pub.feedback_events f) FROM new_rows n
  ON CONFLICT (method) DO NOTHING;
  RETURN NULL;
END $$;
CREATE TRIGGER connections_methods_ins AFTER INSERT ON im.connections
  REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION im.note_edge_methods();
CREATE TRIGGER connections_methods_upd AFTER UPDATE ON im.connections
  REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION im.note_edge_methods();
CREATE TRIGGER connection_kinds_methods_ins AFTER INSERT ON im.connection_kinds
  REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION im.note_edge_methods();
CREATE TRIGGER connection_kinds_methods_upd AFTER UPDATE ON im.connection_kinds
  REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION im.note_edge_methods();

-- My latest link or unlink per unordered pair: the highest event_id (migration 043).
CREATE VIEW im.my_link_events AS
SELECT DISTINCT ON (least(f.idea_id, f.other_idea_id), greatest(f.idea_id, f.other_idea_id))
       least(f.idea_id, f.other_idea_id) AS a, greatest(f.idea_id, f.other_idea_id) AS b, f.kind, f.event_id
FROM pub.feedback_events f
WHERE f.kind IN ('link', 'unlink') AND f.idea_id <> f.other_idea_id
ORDER BY least(f.idea_id, f.other_idea_id), greatest(f.idea_id, f.other_idea_id), f.event_id DESC;

-- The connections shown, one per pair: mine where my latest is a link; otherwise the stored one, unless my latest is
-- an unlink made after its method first wrote.
CREATE VIEW im.shown_connections AS
SELECT e.a, e.b, 'mine'::text AS kind, 'feedback'::text AS method, NULL::real AS confidence
FROM im.my_link_events e
WHERE e.kind = 'link'
UNION ALL
SELECT c.a, c.b, 'related', c.method, c.confidence
FROM im.connections c
LEFT JOIN im.my_link_events e ON e.a = c.a AND e.b = c.b
LEFT JOIN im.edge_methods m ON m.method = c.method
WHERE e.a IS NULL OR (e.kind = 'unlink' AND e.event_id <= coalesce(m.seen_event_id, 0));

-- Lattice's edges, same columns: a connection is `related`, or `mine`; weight is its confidence where one was
-- measured; evolutions come from lineage, as Lattice draws them.
CREATE OR REPLACE VIEW pub.connections AS
SELECT s.a, s.b, s.kind, coalesce(s.confidence, 1)::real AS weight, s.method
FROM im.shown_connections s
WHERE s.a IN (SELECT idea_id FROM pub.ideas) AND s.b IN (SELECT idea_id FROM pub.ideas)
UNION ALL
SELECT l.from_id, l.to_id, l.kind, 1::real, l.method
FROM im.lineage l
WHERE l.from_id IN (SELECT idea_id FROM pub.ideas) AND l.to_id IN (SELECT idea_id FROM pub.ideas);

-- The neutral view's connections between any two clusters, one per pair, with their kinds. (Same columns plus kinds,
-- so OR REPLACE works; pub.cards counts these, so a card's links are its connections.)
CREATE OR REPLACE VIEW pub.card_links AS
SELECT s.a, s.b, s.kind, s.method,
       coalesce((SELECT array_agg(k.kind ORDER BY k.kind) FROM im.connection_kinds k WHERE k.a = s.a AND k.b = s.b),
                '{}') AS kinds
FROM im.shown_connections s;

CREATE VIEW pub.card_lineage AS
SELECT l.from_id, l.to_id, l.kind, l.method FROM im.lineage l;

GRANT SELECT ON pub.card_lineage TO lattice_app;

DROP TABLE im.idea_links;
