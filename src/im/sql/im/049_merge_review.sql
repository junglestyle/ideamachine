-- Clusters that may need merging or splitting, logged for my review and never acted on automatically (decision 0012,
-- matcher v2). `duplicate`: a capture made the same point as two or more clusters, so they may be one point (the
-- matcher joins the nearest). `false_merge`: a member's gist scored low against its cluster's statement (Jev's
-- `same`, after the rebuild). `idea_ids` are the clusters, nearest first; `item_id` the capture that showed it.

CREATE TABLE im.merge_review (
  review_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind        text NOT NULL CHECK (kind IN ('duplicate', 'false_merge')),
  idea_ids    uuid[] NOT NULL,
  item_id     uuid NULL REFERENCES im.items(item_id) ON DELETE CASCADE,
  method      text NOT NULL,
  logged_at   timestamptz NOT NULL DEFAULT now()
);
