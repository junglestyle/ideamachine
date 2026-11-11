-- Retire the seeded archive (decision 0012): the 36 ideas seeded from ChatGPT's summary of my ChatGPT Idea Machine,
-- origin 'archive', and the observations `im seed-observations` wrote straight onto them, stayed behind in the
-- archived database (im_v1) at the cutover (2026-11-11). The rebuilt database reads the archive with extraction
-- instead (`im import-chatgpt`, 055), so no idea has origin 'archive' and every archive observation is a capture's,
-- with no idea of its own. ideas.archive_ref stays: Lattice still reads it from pub.ideas.

ALTER TABLE im.ideas DROP CONSTRAINT ideas_origin_check;
ALTER TABLE im.ideas ADD CONSTRAINT ideas_origin_check CHECK (origin IN ('captured', 'mine'));

-- As in 036 for what I typed adding an idea (it belongs to the idea), and as the importer writes an archive
-- observation (it belongs to its capture's cluster, through the capture, so it has no idea).
ALTER TABLE im.observations
  DROP CONSTRAINT observations_speech_or_not,
  ADD CONSTRAINT observations_speech_or_not CHECK (
    (source = 'speech' AND idea_id IS NULL AND source_ref IS NULL)
    OR (source <> 'speech' AND (idea_id IS NOT NULL) = (source = 'typed') AND quote IS NOT NULL
        AND said_at IS NOT NULL AND said_by IS NOT NULL AND conversation IS NOT NULL AND source_ref IS NOT NULL
        AND segment_ids = '{}'));

-- Same columns as 043; only ideas I added show without evidence.
CREATE OR REPLACE VIEW pub.ideas AS
SELECT d.idea_id, x.title, x.statement, d.origin, d.archive_ref, d.notes, d.created_at,
       count(ev.item_id) AS n_evidence,
       min(ev.said_at) AS first_said, max(ev.said_at) AS last_said,
       count(*) FILTER (WHERE ev.verdict IN ('keep', 'star')) AS kept,
       count(*) FILTER (WHERE ev.verdict = 'discard') AS discarded,
       coalesce((SELECT array_agg(t.name ORDER BY t.name) FROM im.idea_themes it JOIN im.themes t USING (theme_id)
                 WHERE it.idea_id = d.idea_id), '{}') AS themes,
       coalesce((SELECT f.kind = 'star' FROM pub.feedback_events f
                 WHERE f.idea_id = d.idea_id AND f.kind IN ('star', 'unstar') ORDER BY f.event_id DESC LIMIT 1),
                false) AS starred,
       x.claude_title, x.claude_statement, x.my_note,
       (x.title IS DISTINCT FROM x.claude_title OR x.statement IS DISTINCT FROM x.claude_statement) AS corrected
FROM im.ideas d
JOIN im.idea_text x USING (idea_id)
LEFT JOIN pub.evidence ev ON ev.idea_id = d.idea_id
GROUP BY d.idea_id, x.title, x.statement, x.claude_title, x.claude_statement, x.my_note
HAVING d.origin = 'mine'
    OR count(*) FILTER (WHERE ev.item_id IS NOT NULL AND ev.verdict IS DISTINCT FROM 'discard') > 0;

-- Same columns as 043; an idea I added is canonical until I say otherwise.
CREATE OR REPLACE VIEW im.idea_kept AS
SELECT d.idea_id,
       CASE WHEN e.kind = 'idea_keep' THEN e.payload->>'as'
            WHEN e.kind = 'idea_unkeep' THEN NULL
            WHEN d.origin = 'mine' THEN 'canonical'
            WHEN (SELECT f.kind = 'star' FROM pub.feedback_events f
                  WHERE f.idea_id = d.idea_id AND f.kind IN ('star', 'unstar')
                  ORDER BY f.event_id DESC LIMIT 1) THEN 'pinned' END AS kept_as
FROM im.ideas d
LEFT JOIN LATERAL (SELECT f.kind, f.payload FROM pub.feedback_events f
                   WHERE f.idea_id = d.idea_id AND f.kind IN ('idea_keep', 'idea_unkeep')
                   ORDER BY f.event_id DESC LIMIT 1) e ON true;

-- Same columns as 033; a card for every cluster with a current capture, plus the ideas I added.
CREATE OR REPLACE VIEW pub.cards AS
WITH links AS (
  SELECT x.idea_id, count(*) AS links
  FROM pub.card_links l CROSS JOIN LATERAL (VALUES (l.a), (l.b)) x(idea_id)
  GROUP BY x.idea_id
), seen AS (
  SELECT ce.idea_id,
         count(DISTINCT coalesce(ce.observation_id, ce.item_id)) AS observations,
         count(DISTINCT coalesce(ce.observation_id, ce.item_id)) FILTER (WHERE ce.speaker = 'me') AS by_me,
         count(DISTINCT coalesce(ce.observation_id, ce.item_id)) FILTER (WHERE ce.speaker = 'me_diar') AS by_me_diar,
         count(DISTINCT coalesce(ce.observation_id, ce.item_id)) FILTER (WHERE ce.speaker = 'not_me') AS by_others,
         count(DISTINCT ce.conversation_id) AS conversations,
         min(ce.said_at) AS first_said, max(ce.said_at) AS last_said
  FROM pub.card_evidence ce GROUP BY ce.idea_id
)
SELECT d.idea_id, x.title, x.statement, d.origin, m.maturity, m.kept_as, x.my_note,
       coalesce(s.observations, 0) AS observations, coalesce(s.by_me, 0) AS by_me,
       coalesce(s.by_me_diar, 0) AS by_me_diar, coalesce(s.by_others, 0) AS by_others,
       coalesce(s.conversations, 0) AS conversations, s.first_said, coalesce(s.last_said, d.created_at) AS last_seen,
       coalesce(l.links, 0) AS links, p.idea_id IS NOT NULL AS proposed
FROM im.ideas d
JOIN im.idea_text x USING (idea_id)
JOIN im.idea_maturity m USING (idea_id)
LEFT JOIN seen s ON s.idea_id = d.idea_id
LEFT JOIN links l ON l.idea_id = d.idea_id
LEFT JOIN (SELECT DISTINCT idea_id FROM pub.proposals) p ON p.idea_id = d.idea_id
WHERE d.origin = 'mine' OR s.idea_id IS NOT NULL;
