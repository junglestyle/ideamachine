-- The idea lattice (ROADMAP Phase 2, slice 1) and what Lattice reads (§3.5).
--
-- Ideas are durable: they carry my feedback, so they aren't dropped and rebuilt like derived tables.
-- Only forgetting deletes one that came from forgotten speech. Items (Claude's captures) are evidence.

CREATE TABLE im.ideas (
  idea_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title        text NOT NULL,
  statement    text NOT NULL,
  origin       text NOT NULL CHECK (origin IN ('archive', 'captured')),
  archive_ref  text NULL UNIQUE,         -- e.g. chatgpt#17, for ideas seeded from an archive
  notes        text NULL,                -- what the archive developed around the idea
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- Which captured items support which idea. `origin`: the item the idea was created from.
CREATE TABLE im.idea_evidence (
  idea_id     uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  item_id     uuid NOT NULL REFERENCES im.items(item_id) ON DELETE CASCADE,
  relation    text NOT NULL CHECK (relation IN ('origin', 'same_as')),
  judged_by   text NOT NULL,             -- the matcher version that decided it
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (idea_id, item_id)
);
CREATE INDEX ON im.idea_evidence (item_id);

-- Every item the matcher has handled, including the ones it skipped (discarded) or attached elsewhere.
CREATE TABLE im.item_matches (
  item_id     uuid PRIMARY KEY REFERENCES im.items(item_id) ON DELETE CASCADE,
  decision    text NOT NULL CHECK (decision IN ('new', 'same_as', 'evolves', 'skipped')),
  idea_id     uuid NULL REFERENCES im.ideas(idea_id) ON DELETE SET NULL,
  judged_by   text NOT NULL,
  egress_id   bigint NULL REFERENCES im.egress_log(egress_id),
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Edges between ideas. `evolves` and `archive` are durable; `related` is derived from embeddings and
-- replaced on every run (method records how).
CREATE TABLE im.idea_links (
  a           uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  b           uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  kind        text NOT NULL CHECK (kind IN ('evolves', 'related', 'archive')),
  weight      real NOT NULL DEFAULT 1,
  method      text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (a, b, kind),
  CHECK (a <> b)
);

CREATE TABLE im.idea_embeddings (
  idea_id        uuid PRIMARY KEY REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  model_version  text NOT NULL,
  embedding      vector NOT NULL,
  input_hash     text NOT NULL
);

CREATE TABLE im.item_embeddings (
  item_id        uuid PRIMARY KEY REFERENCES im.items(item_id) ON DELETE CASCADE,
  model_version  text NOT NULL,
  embedding      vector NOT NULL,
  input_hash     text NOT NULL
);

-- Themes: seeded from the archive's clusters now; clustering and my pins come in slice 4.
CREATE TABLE im.themes (
  theme_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name         text NOT NULL,
  description  text NULL,
  origin       text NOT NULL CHECK (origin IN ('archive', 'clustered', 'mine')),
  archive_ref  text NULL UNIQUE,
  pinned       boolean NOT NULL DEFAULT false,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE im.idea_themes (
  idea_id   uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  theme_id  uuid NOT NULL REFERENCES im.themes(theme_id) ON DELETE CASCADE,
  origin    text NOT NULL,
  PRIMARY KEY (idea_id, theme_id)
);

-- ---------------------------------------------------------------------------------------------------
-- pub: what Lattice sees. Views run with their owner's (im_pipeline's) rights, so lattice_app needs
-- nothing in im.*.

-- The only thing Lattice writes. Append-only for Lattice (INSERT only); Idea Machine owns it and deletes
-- events when forgetting requires it (the idea FKs cascade).
CREATE TABLE pub.feedback_events (
  event_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at             timestamptz NOT NULL DEFAULT now(),
  kind           text NOT NULL CHECK (kind IN ('star', 'unstar', 'keep', 'discard', 'link', 'unlink',
                                               'pin_theme', 'rename_theme')),
  idea_id        uuid NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  other_idea_id  uuid NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  theme_id       uuid NULL REFERENCES im.themes(theme_id) ON DELETE CASCADE,
  payload        jsonb NOT NULL DEFAULT '{}',
  source         text NOT NULL DEFAULT 'lattice'
);

-- Evidence on current episodes only, with who said it and my latest verdict on the item.
CREATE VIEW pub.evidence AS
SELECT ev.idea_id, i.item_id, ev.relation, i.kind, i.said_by, i.quote, i.gist, i.confidence,
       e.started_at AS said_at, e.episode_id,
       (SELECT v.verdict FROM im.item_verdicts v WHERE v.item_id = i.item_id ORDER BY v.decided_at DESC LIMIT 1)
         AS verdict
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.episodes e ON e.episode_id = i.episode_id
WHERE e.current;

-- Archive ideas always; captured ideas while they have evidence on a current episode.
CREATE VIEW pub.ideas AS
SELECT d.idea_id, d.title, d.statement, d.origin, d.archive_ref, d.notes, d.created_at,
       count(ev.item_id) AS n_evidence,
       min(ev.said_at) AS first_said, max(ev.said_at) AS last_said,
       count(*) FILTER (WHERE ev.verdict = 'keep') AS kept,
       count(*) FILTER (WHERE ev.verdict = 'discard') AS discarded,
       coalesce((SELECT array_agg(t.name ORDER BY t.name) FROM im.idea_themes it JOIN im.themes t USING (theme_id)
                 WHERE it.idea_id = d.idea_id), '{}') AS themes,
       coalesce((SELECT f.kind = 'star' FROM pub.feedback_events f
                 WHERE f.idea_id = d.idea_id AND f.kind IN ('star', 'unstar') ORDER BY f.at DESC LIMIT 1), false)
         AS starred
FROM im.ideas d
LEFT JOIN pub.evidence ev ON ev.idea_id = d.idea_id
GROUP BY d.idea_id
HAVING d.origin = 'archive' OR count(ev.item_id) > 0;

CREATE VIEW pub.connections AS
SELECT l.a, l.b, l.kind, l.weight, l.method
FROM im.idea_links l
WHERE l.a IN (SELECT idea_id FROM pub.ideas) AND l.b IN (SELECT idea_id FROM pub.ideas);

CREATE VIEW pub.themes AS
SELECT t.theme_id, t.name, t.description, t.origin, t.pinned,
       (SELECT count(*) FROM im.idea_themes it WHERE it.theme_id = t.theme_id) AS n_ideas
FROM im.themes t;

GRANT USAGE ON SCHEMA pub TO lattice_app;
GRANT SELECT ON pub.evidence, pub.ideas, pub.connections, pub.themes TO lattice_app;
GRANT SELECT, INSERT ON pub.feedback_events TO lattice_app;
