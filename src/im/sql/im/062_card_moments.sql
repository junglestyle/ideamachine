-- Trails and Crossroads (docs/design/m6-emanation.md) need the order thoughts were said in within a conversation.
-- pub.card_evidence times a speech capture by its episode's start, so every capture of an episode shares one time.
-- A moment is the same row timed by the capture's own first cited segment; anything that isn't speech keeps the time
-- its observation says.
CREATE VIEW pub.card_moments AS
SELECT e.idea_id, e.item_id, e.observation_id, e.speaker, e.quote, e.conversation_id, e.source,
       coalesce((SELECT min(s.started_at) FROM im.items i
                 JOIN im.source_segments s ON s.segment_id = ANY(i.source_segment_ids)
                 WHERE i.item_id = e.item_id AND e.source = 'speech'), e.said_at) AS said_at
FROM pub.card_evidence e;

-- Demo mode reads this one: a sensitive card has no moments, as it has no quotes (052).
CREATE VIEW pub.demo_card_moments AS
SELECT m.* FROM pub.card_moments m WHERE m.idea_id NOT IN (SELECT idea_id FROM pub.demo_hidden_ideas);

GRANT SELECT ON pub.card_moments, pub.demo_card_moments TO lattice_app;
