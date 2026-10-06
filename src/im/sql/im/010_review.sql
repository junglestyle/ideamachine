-- Reviewing captures in Lattice (ROADMAP Phase 2: the write half of slice 5, the ★ of slice 3).

-- ★: a keep that matters more than the rest.
ALTER TABLE im.item_verdicts DROP CONSTRAINT item_verdicts_verdict_check;
ALTER TABLE im.item_verdicts ADD CONSTRAINT item_verdicts_verdict_check CHECK (verdict IN ('keep', 'discard', 'star'));

-- Feedback events can be about a capture. Forgetting a capture's speech deletes the capture, and its events with it.
ALTER TABLE pub.feedback_events ADD COLUMN item_id uuid NULL REFERENCES im.items(item_id) ON DELETE CASCADE;
ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_kind_check;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_kind_check CHECK (kind IN (
  'star', 'unstar', 'keep', 'discard', 'link', 'unlink', 'pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme',
  'item_keep', 'item_discard', 'item_star'));
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_item_kinds CHECK (
  (kind LIKE 'item_%') = (item_id IS NOT NULL));

-- Captures waiting for me: on current episodes, with no verdict and no verdict event waiting to be applied (so a tap
-- in Lattice hides the capture at once, before Idea Machine's next run applies it). Most confident first.
CREATE VIEW pub.review_items AS
SELECT i.item_id, i.kind, i.said_by, i.quote, i.gist, i.themes, i.confidence, e.started_at AS said_at, e.episode_id,
       (SELECT d.title FROM im.idea_evidence ev JOIN im.ideas d USING (idea_id)
        WHERE ev.item_id = i.item_id LIMIT 1) AS idea_title
FROM im.items i
JOIN im.episodes e ON e.episode_id = i.episode_id
WHERE e.current
  AND NOT EXISTS (SELECT 1 FROM im.item_verdicts v WHERE v.item_id = i.item_id)
  AND NOT EXISTS (SELECT 1 FROM pub.feedback_events f WHERE f.item_id = i.item_id);

-- The conversation around each capture: up to 3 segments before and after the ones it came from, in order.
-- `source` marks the capture's own lines.
CREATE VIEW pub.item_context AS
WITH positions AS (
  SELECT i.item_id, es.ord, (es.segment_id = ANY(i.source_segment_ids)) AS source, es.segment_id
  FROM im.items i JOIN im.episode_segments es ON es.episode_id = i.episode_id
), span AS (
  SELECT item_id, min(ord) FILTER (WHERE source) AS lo, max(ord) FILTER (WHERE source) AS hi
  FROM positions GROUP BY item_id
)
SELECT p.item_id, p.ord, p.source, s.speaker_label AS speaker, s.text
FROM positions p
JOIN span USING (item_id)
JOIN im.source_segments s ON s.segment_id = p.segment_id
WHERE p.ord BETWEEN span.lo - 3 AND span.hi + 3;

-- A captured idea whose every piece of evidence I discarded stops showing. (Same columns, so OR REPLACE works.)
CREATE OR REPLACE VIEW pub.ideas AS
SELECT d.idea_id, d.title, d.statement, d.origin, d.archive_ref, d.notes, d.created_at,
       count(ev.item_id) AS n_evidence,
       min(ev.said_at) AS first_said, max(ev.said_at) AS last_said,
       count(*) FILTER (WHERE ev.verdict IN ('keep', 'star')) AS kept,
       count(*) FILTER (WHERE ev.verdict = 'discard') AS discarded,
       coalesce((SELECT array_agg(t.name ORDER BY t.name) FROM im.idea_themes it JOIN im.themes t USING (theme_id)
                 WHERE it.idea_id = d.idea_id), '{}') AS themes,
       coalesce((SELECT f.kind = 'star' FROM pub.feedback_events f
                 WHERE f.idea_id = d.idea_id AND f.kind IN ('star', 'unstar') ORDER BY f.at DESC LIMIT 1), false)
         AS starred
FROM im.ideas d
LEFT JOIN pub.evidence ev ON ev.idea_id = d.idea_id
GROUP BY d.idea_id
HAVING d.origin = 'archive' OR count(*) FILTER (WHERE ev.item_id IS NOT NULL AND ev.verdict IS DISTINCT FROM 'discard') > 0;

GRANT SELECT ON pub.review_items, pub.item_context TO lattice_app;
