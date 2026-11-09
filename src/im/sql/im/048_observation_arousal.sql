-- Arousal on observations (decision 0012): how animated I sounded saying it, carried from its segments as a fact to
-- show and filter by, never a signal that decides anything. Hearsay scores my turns only, so an observation's arousal
-- is the mean over its segments that have one, and there's none for someone else's speech or for an observation
-- that isn't speech. A view, so a re-measurement (importer: arousal is kept up to date in place) reaches it at once.

CREATE VIEW im.observation_arousal AS
SELECT o.observation_id, avg(s.arousal)::real AS arousal
FROM im.observations o
JOIN im.source_segments s ON s.segment_id = ANY(o.segment_ids)
WHERE s.arousal IS NOT NULL
GROUP BY o.observation_id;

-- The neutral view's quotes, as in 036, with each one's arousal at the end.
CREATE OR REPLACE VIEW pub.card_evidence AS
SELECT ev.idea_id, i.item_id, i.observation_id, w.speaker, i.quote, i.gist, e.started_at AS said_at,
       (SELECT min(s.conversation_id) FROM im.source_segments s WHERE s.segment_id = ANY(i.source_segment_ids))
         AS conversation_id,
       'speech'::text AS source, a.arousal
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
JOIN im.item_speaker w USING (item_id)
LEFT JOIN im.observation_arousal a ON a.observation_id = i.observation_id
UNION ALL
SELECT o.idea_id, NULL, o.observation_id, o.said_by, o.quote, NULL, o.said_at, o.conversation, o.source, NULL::real
FROM im.observations o WHERE o.source <> 'speech';
