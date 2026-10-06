-- Correcting ideas, and notes on every verdict (ROADMAP Phase 2).
--
-- Corrections are human input. They live only in pub.feedback_events (idea_correct, idea_note) and are read from
-- there directly, so Lattice shows them at once. Each field takes its latest correction; an empty value reverts to
-- Claude's original, which im.ideas keeps.

ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_kind_check;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_kind_check CHECK (kind IN (
  'star', 'unstar', 'keep', 'discard', 'link', 'unlink', 'pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme',
  'item_keep', 'item_discard', 'item_star', 'idea_correct', 'idea_note'));
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_idea_kinds CHECK (
  kind NOT IN ('star', 'unstar', 'idea_correct', 'idea_note') OR idea_id IS NOT NULL);

-- Every idea with the text that counts: my latest correction of each field, else Claude's (or the archive's).
CREATE VIEW im.idea_text AS
SELECT d.idea_id, d.title AS claude_title, d.statement AS claude_statement,
       coalesce((SELECT nullif(f.payload->>'title', '') FROM pub.feedback_events f
                 WHERE f.idea_id = d.idea_id AND f.kind = 'idea_correct' AND f.payload ? 'title'
                 ORDER BY f.event_id DESC LIMIT 1), d.title) AS title,
       coalesce((SELECT nullif(f.payload->>'statement', '') FROM pub.feedback_events f
                 WHERE f.idea_id = d.idea_id AND f.kind = 'idea_correct' AND f.payload ? 'statement'
                 ORDER BY f.event_id DESC LIMIT 1), d.statement) AS statement,
       (SELECT nullif(f.payload->>'note', '') FROM pub.feedback_events f
        WHERE f.idea_id = d.idea_id AND f.kind = 'idea_note' ORDER BY f.event_id DESC LIMIT 1) AS my_note,
       (SELECT max(f.at) FROM pub.feedback_events f
        WHERE f.idea_id = d.idea_id AND f.kind IN ('idea_correct', 'idea_note')) AS corrected_at
FROM im.ideas d;

-- pub.ideas with the effective text, plus the originals and my note (new columns go at the end).
CREATE OR REPLACE VIEW pub.ideas AS
SELECT d.idea_id, x.title, x.statement, d.origin, d.archive_ref, d.notes, d.created_at,
       count(ev.item_id) AS n_evidence,
       min(ev.said_at) AS first_said, max(ev.said_at) AS last_said,
       count(*) FILTER (WHERE ev.verdict IN ('keep', 'star')) AS kept,
       count(*) FILTER (WHERE ev.verdict = 'discard') AS discarded,
       coalesce((SELECT array_agg(t.name ORDER BY t.name) FROM im.idea_themes it JOIN im.themes t USING (theme_id)
                 WHERE it.idea_id = d.idea_id), '{}') AS themes,
       coalesce((SELECT f.kind = 'star' FROM pub.feedback_events f
                 WHERE f.idea_id = d.idea_id AND f.kind IN ('star', 'unstar') ORDER BY f.at DESC LIMIT 1), false)
         AS starred,
       x.claude_title, x.claude_statement, x.my_note,
       (x.title IS DISTINCT FROM x.claude_title OR x.statement IS DISTINCT FROM x.claude_statement) AS corrected
FROM im.ideas d
JOIN im.idea_text x USING (idea_id)
LEFT JOIN pub.evidence ev ON ev.idea_id = d.idea_id
GROUP BY d.idea_id, x.title, x.statement, x.claude_title, x.claude_statement, x.my_note
HAVING d.origin = 'archive' OR count(*) FILTER (WHERE ev.item_id IS NOT NULL AND ev.verdict IS DISTINCT FROM 'discard') > 0;

-- The review queue names the capture's idea by id too (at the end), so a correction can be made from the card.
CREATE OR REPLACE VIEW pub.review_items AS
SELECT i.item_id, i.kind, i.said_by, i.quote, i.gist, i.themes, i.confidence, e.started_at AS said_at, e.episode_id,
       (SELECT x.title FROM im.idea_evidence ev JOIN im.idea_text x USING (idea_id)
        WHERE ev.item_id = i.item_id LIMIT 1) AS idea_title,
       (SELECT ev.idea_id FROM im.idea_evidence ev WHERE ev.item_id = i.item_id LIMIT 1) AS idea_id
FROM im.items i
JOIN im.episodes e ON e.episode_id = i.episode_id
WHERE e.current
  AND NOT EXISTS (SELECT 1 FROM im.item_verdicts v WHERE v.item_id = i.item_id)
  AND NOT EXISTS (SELECT 1 FROM pub.feedback_events f WHERE f.item_id = i.item_id);
