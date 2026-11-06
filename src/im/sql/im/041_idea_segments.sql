-- Forgetting reaches what was made from speech through the speech itself, not through a capture that a re-read or
-- `im reset --stage extract` can delete (review 2026-11-05: forgetting a re-read's speech left its idea behind).

-- A captured idea holds the segment ids of the capture it was created from (lattice.match_stage), as labels and
-- verdicts do. Forgetting any of them deletes the idea; nothing else touches this column. Filled here from each
-- captured idea's origin evidence, every version of it. An idea whose origin capture is already gone has none to
-- go by and keeps '{}'.
ALTER TABLE im.ideas ADD COLUMN segment_ids uuid[] NOT NULL DEFAULT '{}';
UPDATE im.ideas d SET segment_ids = o.segs
FROM (SELECT ev.idea_id, array_agg(DISTINCT s.seg ORDER BY s.seg) AS segs
      FROM im.idea_evidence ev JOIN im.items i USING (item_id)
      CROSS JOIN unnest(i.source_segment_ids) AS s(seg)
      WHERE ev.relation = 'origin' GROUP BY ev.idea_id) o
WHERE o.idea_id = d.idea_id AND d.origin = 'captured';
CREATE INDEX ON im.ideas USING gin (segment_ids);

-- Lattice's events on a capture are ledger: deleting the capture (a re-read that doesn't make it again, a reset)
-- no longer deletes them. Each holds its capture's segment ids, set on insert, so forgetting still finds it once
-- the capture is gone.
ALTER TABLE pub.feedback_events ADD COLUMN item_segment_ids uuid[] NULL;
UPDATE pub.feedback_events f SET item_segment_ids = i.source_segment_ids FROM im.items i WHERE i.item_id = f.item_id;
CREATE INDEX ON pub.feedback_events USING gin (item_segment_ids);

CREATE FUNCTION im.anchor_item_event() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, im, pub
AS $$
BEGIN
  NEW.item_segment_ids := (SELECT i.source_segment_ids FROM im.items i WHERE i.item_id = NEW.item_id);
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION im.anchor_item_event() FROM PUBLIC;
CREATE TRIGGER feedback_events_anchor_item BEFORE INSERT ON pub.feedback_events
  FOR EACH ROW EXECUTE FUNCTION im.anchor_item_event();

-- An event names a capture when it's made (same rule as 010, through the anchor the trigger sets), and keeps its
-- anchor when the capture goes.
ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_item_kinds;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_item_kinds CHECK (
  (kind LIKE 'item_%') = (item_segment_ids IS NOT NULL));
ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_item_id_fkey;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_item_id_fkey
  FOREIGN KEY (item_id) REFERENCES im.items(item_id) ON DELETE SET NULL;

-- A classifier trained on a forgotten label goes when the label does (pipeline._purge_tombstoned). One trained on a
-- label forgotten before now was missed: only forgetting deletes labels, so it's one trained on a label that's gone.
DELETE FROM im.classifiers c
WHERE NOT c.label_ids <@ coalesce((SELECT array_agg(l.label_id) FROM im.labels l), '{}');
