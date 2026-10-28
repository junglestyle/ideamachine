-- M2 (docs/design/m2-observations-candidates-ideas.md): observations, who said them, and how far each cluster has
-- come. An observation is one thing Claude derived from one stretch of speech; items are its captures, and a
-- re-read that captures the same speech joins the same observation (im.observations.assign_stage). A cluster is an
-- im.ideas row; its members are the observations of its evidence items.

-- Ledger side: rebuilt only by the observation stage, never edited by hand. segment_ids are the first capture's.
CREATE TABLE im.observations (
  observation_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  segment_ids     uuid[] NOT NULL,
  first_seen      timestamptz NOT NULL
);
CREATE INDEX ON im.observations USING gin (segment_ids);

ALTER TABLE im.items ADD COLUMN observation_id uuid NULL REFERENCES im.observations(observation_id) ON DELETE SET NULL;
CREATE INDEX ON im.items (observation_id);

-- Who said an item, from its said-by segment's current label (a Hearsay relabel moves it). Owner by voice, name or
-- channel is me; owner on any other basis (diarization, in practice) is me_diar, which counts only alongside me.
-- Anything else is not me: anonymous, stranger, unknown, or no cited segment. cites_me: some cited segment is
-- solidly mine, e.g. a quote that runs across two speakers and starts with me.
CREATE VIEW im.item_speaker AS
SELECT i.item_id,
       CASE WHEN s.speaker_kind = 'owner' AND s.speaker_basis IN ('voice', 'named', 'channel') THEN 'me'
            WHEN s.speaker_kind = 'owner' THEN 'me_diar'
            ELSE 'not_me' END AS speaker,
       EXISTS (SELECT 1 FROM im.source_segments c
               WHERE c.segment_id = ANY(i.source_segment_ids) AND c.speaker_kind = 'owner'
                 AND c.speaker_basis IN ('voice', 'named', 'channel')) AS cites_me
FROM im.items i
LEFT JOIN im.source_segments s ON s.segment_id = i.said_by_segment_id;

-- An observation's speaker is its latest capture's, preferring one on a current episode.
CREATE VIEW im.observation_speaker AS
SELECT DISTINCT ON (i.observation_id) i.observation_id, w.speaker, w.cites_me
FROM im.items i
JOIN im.item_speaker w USING (item_id)
JOIN im.episodes e USING (episode_id)
WHERE i.observation_id IS NOT NULL
ORDER BY i.observation_id, e.current DESC, i.created_at DESC, i.item_id;

-- How far each cluster has come. Only I make ideas: canonical (weighted) or pinned (no weight). Until keep events
-- exist, ideas I added and the archive seed are canonical and starred ones pinned. A cluster with an observation
-- of mine is a candidate (every capture is one Claude judged meaningful); one with none stays an observation.
CREATE VIEW im.idea_maturity AS
SELECT d.idea_id,
       CASE WHEN k.kept_as IS NOT NULL THEN 'idea'
            WHEN EXISTS (SELECT 1 FROM im.idea_evidence ev JOIN im.items i USING (item_id)
                         JOIN im.observation_speaker o USING (observation_id)
                         WHERE ev.idea_id = d.idea_id AND o.speaker = 'me') THEN 'candidate'
            ELSE 'observation' END AS maturity,
       k.kept_as
FROM im.ideas d
CROSS JOIN LATERAL (
  SELECT CASE WHEN d.origin IN ('archive', 'mine') THEN 'canonical'
              WHEN (SELECT f.kind = 'star' FROM pub.feedback_events f
                    WHERE f.idea_id = d.idea_id AND f.kind IN ('star', 'unstar')
                    ORDER BY f.at DESC LIMIT 1) THEN 'pinned' END AS kept_as) k;
