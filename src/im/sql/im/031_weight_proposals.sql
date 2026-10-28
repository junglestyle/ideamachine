-- M2 (docs/design/m2-observations-candidates-ideas.md): what I keep, how much each cluster weighs, and what the
-- system proposes to me. Only I make ideas.

-- idea_keep {"as": "canonical" | "pinned"} makes a cluster an idea, or switches how it's kept; idea_unkeep undoes
-- it; idea_dismiss turns down a proposal (the cluster stays a candidate).
ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_kind_check;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_kind_check CHECK (kind IN (
  'star', 'unstar', 'keep', 'discard', 'link', 'unlink', 'pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme',
  'item_keep', 'item_discard', 'item_star', 'idea_correct', 'idea_note', 'idea_compare', 'digest_star',
  'digest_dismiss', 'idea_add', 'merge_theme', 'split_theme', 'file_idea', 'unfile_idea', 'idea_sensitive',
  'theme_sensitive', 'vocab_add', 'vocab_remove', 'idea_keep', 'idea_unkeep', 'idea_dismiss'));
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_idea_keep CHECK (
  kind NOT IN ('idea_keep', 'idea_unkeep', 'idea_dismiss') OR (
    idea_id IS NOT NULL
    AND (kind <> 'idea_keep' OR payload->>'as' IN ('canonical', 'pinned'))));

-- How each cluster is kept: my latest keep or unkeep, else the starting state (the archive seed and ideas I added
-- are canonical, starred ones pinned; a star is a pin).
CREATE VIEW im.idea_kept AS
SELECT d.idea_id,
       CASE WHEN e.kind = 'idea_keep' THEN e.payload->>'as'
            WHEN e.kind = 'idea_unkeep' THEN NULL
            WHEN d.origin IN ('archive', 'mine') THEN 'canonical'
            WHEN (SELECT f.kind = 'star' FROM pub.feedback_events f
                  WHERE f.idea_id = d.idea_id AND f.kind IN ('star', 'unstar')
                  ORDER BY f.at DESC, f.event_id DESC LIMIT 1) THEN 'pinned' END AS kept_as
FROM im.ideas d
LEFT JOIN LATERAL (SELECT f.kind, f.payload FROM pub.feedback_events f
                   WHERE f.idea_id = d.idea_id AND f.kind IN ('idea_keep', 'idea_unkeep')
                   ORDER BY f.at DESC, f.event_id DESC LIMIT 1) e ON true;

-- Same columns as 030, kept_as now from im.idea_kept, and only current captures make a candidate (a cluster whose
-- evidence is all on superseded reads is out of Lattice, so it isn't proposed).
CREATE OR REPLACE VIEW im.idea_maturity AS
SELECT d.idea_id,
       CASE WHEN k.kept_as IS NOT NULL THEN 'idea'
            WHEN EXISTS (SELECT 1 FROM im.idea_evidence ev JOIN im.items i USING (item_id)
                         JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
                         JOIN im.observation_speaker o USING (observation_id)
                         WHERE ev.idea_id = d.idea_id AND o.speaker = 'me') THEN 'candidate'
            ELSE 'observation' END AS maturity,
       k.kept_as
FROM im.ideas d
JOIN im.idea_kept k USING (idea_id);

-- Graph side: a formula version's weights sit beside the others', so two versions can be compared. Rewritten each
-- run, only where a weight changed. signals: what the formula read.
CREATE TABLE im.weights (
  idea_id          uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  formula_version  text NOT NULL,
  weight           double precision NOT NULL,
  signals          jsonb NOT NULL,
  computed_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (formula_version, idea_id)
);

-- What the system proposed to me, and what I did with it (NULL while open).
CREATE TABLE im.proposals (
  proposal_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  idea_id          uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  weight           double precision NOT NULL,
  formula_version  text NOT NULL,
  made_at          timestamptz NOT NULL DEFAULT now(),
  outcome          text NULL CHECK (outcome IN ('canonical', 'pinned', 'dismissed')),
  decided_at       timestamptz NULL
);
CREATE UNIQUE INDEX ON im.proposals (idea_id) WHERE outcome IS NULL;   -- one open proposal per cluster

-- Open proposals, highest weight first, for Lattice.
CREATE VIEW pub.proposals AS
SELECT p.proposal_id, p.idea_id, x.title, x.statement, p.weight, p.made_at
FROM im.proposals p JOIN im.idea_text x USING (idea_id)
WHERE p.outcome IS NULL;
GRANT SELECT ON pub.proposals TO lattice_app;
