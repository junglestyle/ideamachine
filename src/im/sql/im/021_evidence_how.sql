-- pub.evidence.how: how my current verdict on a capture was made ('one', 'audit', 'bulk'; NULL with no verdict),
-- so Lattice can resurface captures I kept unread once their idea turns out to matter. A later verdict wins, so a
-- real verdict after a bulk keep turns it to 'one'. Appended last, so existing SELECTs are unaffected.
CREATE OR REPLACE VIEW pub.evidence AS
SELECT ev.idea_id, i.item_id, ev.relation, i.kind, i.said_by, i.quote, i.gist, i.confidence,
       e.started_at AS said_at, e.episode_id, v.verdict, v.how
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.episodes e ON e.episode_id = i.episode_id
LEFT JOIN LATERAL (SELECT verdict, how FROM im.item_verdicts WHERE item_id = i.item_id
                   ORDER BY decided_at DESC, verdict_id DESC LIMIT 1) v ON true
WHERE e.current;
