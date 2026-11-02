-- Evidence is one row per observation, not per capture (2026-11-02). Prompt v3 re-read every episode, and a
-- re-capture joins its observation and that observation's cluster (lattice.carry_by_observation). Counting
-- captures showed the same words twice, inflated n_evidence, offered my verdicts again, and let a cluster I'd
-- discarded back in through its unjudged re-capture. A verdict on any capture of an observation is the
-- observation's. A capture with no observation yet stands for itself.

-- The latest verdict on any capture of each capture's observation.
CREATE VIEW im.capture_verdict AS
SELECT DISTINCT ON (i.item_id) i.item_id, v.verdict, v.how
FROM im.items i
JOIN im.items s ON s.item_id = i.item_id OR s.observation_id = i.observation_id
JOIN im.item_verdicts v ON v.item_id = s.item_id
ORDER BY i.item_id, v.decided_at DESC, v.verdict_id DESC;

-- Same columns as 029. Each observation once per idea: a capture I judged, else the origin, else the first.
CREATE OR REPLACE VIEW pub.evidence AS
SELECT DISTINCT ON (ev.idea_id, coalesce(i.observation_id, i.item_id))
       ev.idea_id, i.item_id, ev.relation, i.kind, w.said_by, i.quote, i.gist, i.confidence,
       e.started_at AS said_at, e.episode_id, v.verdict, v.how
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.item_said_by w USING (item_id)
JOIN im.episodes e ON e.episode_id = i.episode_id
LEFT JOIN im.capture_verdict v ON v.item_id = i.item_id
WHERE e.current
ORDER BY ev.idea_id, coalesce(i.observation_id, i.item_id),
         EXISTS (SELECT 1 FROM im.item_verdicts x WHERE x.item_id = i.item_id) DESC,
         ev.relation = 'origin' DESC, i.created_at, i.item_id;

-- Same columns as 029. Each observation once, and none I've judged or given feedback on through any capture.
CREATE OR REPLACE VIEW pub.review_items AS
SELECT DISTINCT ON (coalesce(i.observation_id, i.item_id))
       i.item_id, i.kind, w.said_by, i.quote, i.gist, i.themes, i.confidence, e.started_at AS said_at, e.episode_id,
       (SELECT x.title FROM im.idea_evidence ev JOIN im.idea_text x USING (idea_id)
        WHERE ev.item_id = i.item_id LIMIT 1) AS idea_title,
       (SELECT ev.idea_id FROM im.idea_evidence ev WHERE ev.item_id = i.item_id LIMIT 1) AS idea_id
FROM im.items i
JOIN im.item_said_by w USING (item_id)
JOIN im.episodes e ON e.episode_id = i.episode_id
WHERE e.current
  AND NOT EXISTS (SELECT 1 FROM im.capture_verdict v WHERE v.item_id = i.item_id)
  AND NOT EXISTS (SELECT 1 FROM im.items s JOIN pub.feedback_events f ON f.item_id = s.item_id
                  WHERE s.item_id = i.item_id OR s.observation_id = i.observation_id)
ORDER BY coalesce(i.observation_id, i.item_id), i.created_at, i.item_id;
