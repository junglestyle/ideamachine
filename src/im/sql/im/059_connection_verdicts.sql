-- The connection judge (im.connect, SPEC §5): every verdict it reaches on a pair, so a pair is asked once for its text
-- as shown and the judge's method. A `none` can't live in im.connections, where every row is shown as a connection,
-- so the verdicts have their own table, and lattice.relate never writes a stand-in for a pair that has one.
-- - verdict: what Terra answered (supports, elaborates, tension, related, same, none or unsure), `refused` when it
--   refused, or `screened` when Jev's screen kept the pair from Terra. Only the first five are connections.
-- - direction: Terra's for supports and elaborates, with A the lower id, so `A>B` is `a>b`.
-- - belongs: Jev's probability that the two belong together (jev.QUESTIONS["edge"]), the screen's answer.
-- - input_hash: the sha256 of the pair as shown; method: the judge's (connect-1:…), as on the connection it wrote.
-- - segment_ids: what the requests carried. `why` and `analogy` may paraphrase what was said, so forgetting any of
--   them deletes the verdict (pipeline._purge_tombstoned), and the pair is asked again.
-- - egress_id: Terra's request; Jev's answer is kept in im.jev_answers, under the pair's input_hash.

CREATE TABLE im.connection_verdicts (
  a            uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  b            uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  verdict      text NOT NULL CHECK (verdict IN ('supports', 'elaborates', 'tension', 'related', 'same', 'none',
                                                'unsure', 'refused', 'screened')),
  direction    text NULL CHECK (direction IN ('a>b', 'b>a', 'both')),
  analogy      text NULL,
  why          text NULL,
  belongs      real NOT NULL,
  method       text NOT NULL,
  input_hash   text NOT NULL,
  segment_ids  uuid[] NOT NULL DEFAULT '{}',
  egress_id    bigint NULL REFERENCES im.egress_log(egress_id) ON DELETE SET NULL,
  judged_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (a, b),
  CHECK (a < b),
  CHECK (direction IS NULL OR verdict IN ('supports', 'elaborates'))
);
CREATE INDEX connection_verdicts_b ON im.connection_verdicts (b);
CREATE INDEX ON im.connection_verdicts USING gin (segment_ids);

-- `judged_same`: the connection judge said two clusters are the same idea said twice (`idea_ids` the pair, no
-- capture). Logged for my review, never merged.
ALTER TABLE im.merge_review DROP CONSTRAINT merge_review_kind_check;
ALTER TABLE im.merge_review ADD CONSTRAINT merge_review_kind_check
  CHECK (kind IN ('duplicate', 'false_merge', 'judged_same'));
