-- Who said a capture follows the speech, not the extraction (docs/design/m1-segment-identity.md). Naming a voice
-- no longer re-derives episodes, so a label stored at extraction would go stale. items.said_by keeps what was
-- recorded then; said_by_segment_id names the cited segment whose speaker it is, and readers take that segment's
-- current label. A label Claude made up (no cited segment has it) stays as recorded.

ALTER TABLE im.items ADD COLUMN said_by_segment_id uuid NULL;

-- The first cited segment whose speaker matches. In production every item had one (2026-10-27, 414 of 414).
UPDATE im.items i SET said_by_segment_id = (
  SELECT u.seg FROM unnest(i.source_segment_ids) WITH ORDINALITY AS u(seg, n)
  JOIN im.source_segments s ON s.segment_id = u.seg
  WHERE s.speaker_label = i.said_by
  ORDER BY u.n LIMIT 1);

CREATE VIEW im.item_said_by AS
SELECT i.item_id, coalesce(s.speaker_label, i.said_by) AS said_by
FROM im.items i
LEFT JOIN im.source_segments s ON s.segment_id = i.said_by_segment_id;

-- Same columns as 021 and 011, with said_by from the speech.
CREATE OR REPLACE VIEW pub.evidence AS
SELECT ev.idea_id, i.item_id, ev.relation, i.kind, w.said_by, i.quote, i.gist, i.confidence,
       e.started_at AS said_at, e.episode_id, v.verdict, v.how
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.item_said_by w USING (item_id)
JOIN im.episodes e ON e.episode_id = i.episode_id
LEFT JOIN LATERAL (SELECT verdict, how FROM im.item_verdicts WHERE item_id = i.item_id
                   ORDER BY decided_at DESC, verdict_id DESC LIMIT 1) v ON true
WHERE e.current;

CREATE OR REPLACE VIEW pub.review_items AS
SELECT i.item_id, i.kind, w.said_by, i.quote, i.gist, i.themes, i.confidence, e.started_at AS said_at, e.episode_id,
       (SELECT x.title FROM im.idea_evidence ev JOIN im.idea_text x USING (idea_id)
        WHERE ev.item_id = i.item_id LIMIT 1) AS idea_title,
       (SELECT ev.idea_id FROM im.idea_evidence ev WHERE ev.item_id = i.item_id LIMIT 1) AS idea_id
FROM im.items i
JOIN im.item_said_by w USING (item_id)
JOIN im.episodes e ON e.episode_id = i.episode_id
WHERE e.current
  AND NOT EXISTS (SELECT 1 FROM im.item_verdicts v WHERE v.item_id = i.item_id)
  AND NOT EXISTS (SELECT 1 FROM pub.feedback_events f WHERE f.item_id = i.item_id);
