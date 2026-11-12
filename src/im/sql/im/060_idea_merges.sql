-- Merging clusters (`im merge KEEP OTHER`, im.merge): two clusters that make one point become the one I keep. The
-- other's captures, typed observations and match decisions move to it, its connections and lineage are re-pointed,
-- and it stops being a cluster: no card, no text for the matcher or an embedding, no weight. Its row stays, with my
-- events on it, so nothing I wrote is lost and a merge could be undone.
-- - other_id: the cluster merged away, at most once; keep_id: the one it was merged into, which may later be merged
--   away itself. Forgetting the speech either was made from deletes the record with it (pipeline._purge_tombstoned
--   also deletes a cluster merged into a forgotten one).
-- - evidence: the captures moved, as they were in the other cluster: [{item_id, relation, judged_by}]. In the kept
--   one they're `same_as`, judged_by `merge`.
-- - matched_item_ids: the captures whose match decision (im.item_matches) named the other cluster, now the kept one.
-- - observation_ids: the other cluster's typed observations, moved.
-- - title, statement, my_note: the other cluster's as shown when it was merged, my corrections winning.
-- - event_ids: the feedback events the merge made on the kept cluster, carrying how the other was kept (idea_keep)
--   or my mark that it's sensitive (idea_sensitive) when the kept one had neither.

CREATE TABLE im.idea_merges (
  merge_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  other_id          uuid NOT NULL UNIQUE REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  keep_id           uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  merged_at         timestamptz NOT NULL DEFAULT now(),
  evidence          jsonb NOT NULL,
  matched_item_ids  uuid[] NOT NULL,
  observation_ids   uuid[] NOT NULL,
  title             text NOT NULL,
  statement         text NOT NULL,
  my_note           text NULL,
  event_ids         bigint[] NOT NULL DEFAULT '{}',
  CHECK (other_id <> keep_id)
);
CREATE INDEX idea_merges_keep ON im.idea_merges (keep_id);

-- Each cluster merged away, with the cluster it's part of now: the end of its chain of merges.
CREATE VIEW im.merged_into AS
WITH RECURSIVE chain (idea_id, into_id) AS (
  SELECT m.other_id, m.keep_id FROM im.idea_merges m
  UNION ALL
  SELECT c.idea_id, m.keep_id FROM chain c JOIN im.idea_merges m ON m.other_id = c.into_id
)
SELECT c.idea_id, c.into_id FROM chain c
WHERE NOT EXISTS (SELECT 1 FROM im.idea_merges m WHERE m.other_id = c.into_id);

-- A review row is resolved by the merge that made its clusters one.
ALTER TABLE im.merge_review
  ADD COLUMN merge_id bigint NULL REFERENCES im.idea_merges(merge_id) ON DELETE SET NULL;

-- Same columns as 011: only clusters not merged away have text that counts, so the matcher, the embeddings, the
-- cards, pub.ideas, proposals and the sensitivity tags all leave them out.
CREATE OR REPLACE VIEW im.idea_text AS
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
FROM im.ideas d
WHERE NOT EXISTS (SELECT 1 FROM im.idea_merges m WHERE m.other_id = d.idea_id);

-- Same columns as 057: a cluster merged away isn't kept, a candidate or weighed (im.idea_maturity, weight.stage).
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
                   ORDER BY f.event_id DESC LIMIT 1) e ON true
WHERE NOT EXISTS (SELECT 1 FROM im.idea_merges m WHERE m.other_id = d.idea_id);

-- Same columns as 045: my links and unlinks on a cluster merged away count for the cluster it's part of now, my
-- latest per pair deciding, and one between the two merged clusters is dropped.
CREATE OR REPLACE VIEW im.my_link_events AS
SELECT DISTINCT ON (least(x.a, x.b), greatest(x.a, x.b))
       least(x.a, x.b) AS a, greatest(x.a, x.b) AS b, f.kind, f.event_id
FROM pub.feedback_events f
LEFT JOIN im.merged_into ma ON ma.idea_id = f.idea_id
LEFT JOIN im.merged_into mb ON mb.idea_id = f.other_idea_id
CROSS JOIN LATERAL (VALUES (coalesce(ma.into_id, f.idea_id), coalesce(mb.into_id, f.other_idea_id))) x(a, b)
WHERE f.kind IN ('link', 'unlink') AND x.a <> x.b
ORDER BY least(x.a, x.b), greatest(x.a, x.b), f.event_id DESC;
