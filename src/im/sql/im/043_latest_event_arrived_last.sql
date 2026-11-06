-- My latest event is the one that arrived last: the highest event_id. `at` says when it was made, for windows such
-- as the digest's week; it doesn't order. Corrections, notes, sensitivity marks, links, vocabulary and digest
-- responses already went by event_id, and theme edits apply in that order as they arrive (024's trigger). Stars
-- and keeps went by `at`, so two events in one transaction tied (now() is the transaction's start), and an event
-- Lattice stamped itself could lose to one that arrived before it (review 2026-11-05: star then unstar in one
-- transaction showed starred in Lattice and not kept in the neutral view). Where `at` follows arrival, as when
-- every event takes its default, nothing shown changes.

-- Same columns as 023; only `starred` changes.
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
HAVING d.origin IN ('archive', 'mine')
    OR count(*) FILTER (WHERE ev.item_id IS NOT NULL AND ev.verdict IS DISTINCT FROM 'discard') > 0;

-- Same columns as 031.
CREATE OR REPLACE VIEW im.idea_kept AS
SELECT d.idea_id,
       CASE WHEN e.kind = 'idea_keep' THEN e.payload->>'as'
            WHEN e.kind = 'idea_unkeep' THEN NULL
            WHEN d.origin IN ('archive', 'mine') THEN 'canonical'
            WHEN (SELECT f.kind = 'star' FROM pub.feedback_events f
                  WHERE f.idea_id = d.idea_id AND f.kind IN ('star', 'unstar')
                  ORDER BY f.event_id DESC LIMIT 1) THEN 'pinned' END AS kept_as
FROM im.ideas d
LEFT JOIN LATERAL (SELECT f.kind, f.payload FROM pub.feedback_events f
                   WHERE f.idea_id = d.idea_id AND f.kind IN ('idea_keep', 'idea_unkeep')
                   ORDER BY f.event_id DESC LIMIT 1) e ON true;
