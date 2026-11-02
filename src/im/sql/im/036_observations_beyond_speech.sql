-- Observations that aren't speech (M2 design, Edges and after): what I said into the ChatGPT Idea Machine (the
-- archive's source, read from its export by `im seed-observations`), and what I typed when adding an idea
-- (observations.assign_stage, from the idea_add event). Each belongs to its idea directly: no capture, no segments,
-- so forgetting (which works on segments) never reaches it. It holds its own words, time and speaker.
--
-- source_ref names where it came from, once: e.g. chatgpt:<conversation id>:<message time>, or idea_add:<event id>.
-- conversation groups them for recurrence: the ChatGPT conversation, or the event for a typed idea.
ALTER TABLE im.observations
  ADD COLUMN source        text NOT NULL DEFAULT 'speech' CHECK (source IN ('speech', 'archive', 'typed')),
  ADD COLUMN idea_id       uuid NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  ADD COLUMN quote         text NULL,
  ADD COLUMN said_at       timestamptz NULL,
  ADD COLUMN said_by       text NULL CHECK (said_by IN ('me', 'not_me')),
  ADD COLUMN conversation  text NULL,
  ADD COLUMN source_ref    text NULL UNIQUE,
  ADD CONSTRAINT observations_speech_or_not CHECK (
    (source = 'speech' AND idea_id IS NULL AND source_ref IS NULL)
    OR (source <> 'speech' AND idea_id IS NOT NULL AND quote IS NOT NULL AND said_at IS NOT NULL
        AND said_by IS NOT NULL AND conversation IS NOT NULL AND source_ref IS NOT NULL
        AND segment_ids = '{}'));
CREATE INDEX ON im.observations (idea_id) WHERE idea_id IS NOT NULL;

-- Who said an observation: a speech one as before (its latest capture's, preferring a current episode), one that
-- isn't speech as recorded.
CREATE OR REPLACE VIEW im.observation_speaker AS
SELECT * FROM (
  SELECT DISTINCT ON (i.observation_id) i.observation_id, w.speaker, w.cites_me
  FROM im.items i
  JOIN im.item_speaker w USING (item_id)
  JOIN im.episodes e USING (episode_id)
  WHERE i.observation_id IS NOT NULL
  ORDER BY i.observation_id, e.current DESC, i.created_at DESC, i.item_id) speech
UNION ALL
SELECT o.observation_id, o.said_by, o.said_by = 'me' FROM im.observations o WHERE o.source <> 'speech';

CREATE VIEW im.idea_observations AS
-- Every observation of a cluster, speech or not: (idea, observation, source, speaker, said at, conversation).
-- Speech ones only through current captures, as weight and the cards count them.
SELECT ev.idea_id, i.observation_id, 'speech'::text AS source, o.speaker, min(s.started_at) AS said_at,
       min(s.conversation_id) AS conversation
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
JOIN im.observation_speaker o USING (observation_id)
JOIN im.source_segments s ON s.segment_id = ANY(i.source_segment_ids)
GROUP BY ev.idea_id, i.observation_id, o.speaker
UNION ALL
SELECT o.idea_id, o.observation_id, o.source, o.said_by, o.said_at, o.conversation
FROM im.observations o WHERE o.source <> 'speech';

-- The neutral view's quotes: the current captures as before, then the observations that aren't speech (no item, no
-- gist: the quote is my own words). `source` is new, at the end.
CREATE OR REPLACE VIEW pub.card_evidence AS
SELECT ev.idea_id, i.item_id, i.observation_id, w.speaker, i.quote, i.gist, e.started_at AS said_at,
       (SELECT min(s.conversation_id) FROM im.source_segments s WHERE s.segment_id = ANY(i.source_segment_ids))
         AS conversation_id,
       'speech'::text AS source
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
JOIN im.item_speaker w USING (item_id)
UNION ALL
SELECT o.idea_id, NULL, o.observation_id, o.said_by, o.quote, NULL, o.said_at, o.conversation, o.source
FROM im.observations o WHERE o.source <> 'speech';
