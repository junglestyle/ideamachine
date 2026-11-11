-- Archive captures: what I typed into the ChatGPT Idea Machine, read by the same extraction as my speech and matched
-- into the same clusters, in the order I said things. A capture of the archive is an im.items row with no episode
-- and no segments. Its own facts (when I sent the message, the conversation, that it's mine, whether I edited it
-- before the import) are on its observation, source 'archive', which it's linked to from the start. Its cluster
-- membership goes through im.idea_evidence, as a spoken capture's does, so the observation has no idea_id. The
-- archive observations `im seed-observations` wrote straight onto the seeded archive ideas keep theirs.

ALTER TABLE im.items
  ALTER COLUMN episode_id DROP NOT NULL,
  ADD CONSTRAINT items_speech_or_archive CHECK (episode_id IS NOT NULL OR source_segment_ids = '{}');

-- As in 036, except that an archive observation may have no idea: it's a capture's, and belongs to the capture's
-- cluster. `edited`: I changed the message's text before it was read (the capture quotes what I changed it to).
ALTER TABLE im.observations
  DROP CONSTRAINT observations_speech_or_not,
  ADD CONSTRAINT observations_speech_or_not CHECK (
    (source = 'speech' AND idea_id IS NULL AND source_ref IS NULL)
    OR (source <> 'speech' AND (idea_id IS NOT NULL OR source = 'archive') AND quote IS NOT NULL
        AND said_at IS NOT NULL AND said_by IS NOT NULL AND conversation IS NOT NULL AND source_ref IS NOT NULL
        AND segment_ids = '{}')),
  ADD COLUMN edited boolean NOT NULL DEFAULT false,
  ADD CONSTRAINT observations_edited_archive CHECK (NOT edited OR source = 'archive');

-- Stretches of the archive extraction has read, including those where it found nothing, as im.extractions is for
-- episodes. source_ref names the stretch, e.g. chatgpt:<conversation id>:<its first message's time>.
CREATE TABLE im.archive_reads (
  source_ref              text NOT NULL,
  model                   text NOT NULL,
  prompt_version          text NOT NULL,
  privacy_policy_version  text NOT NULL,
  input_hash              text NOT NULL,
  egress_id               bigint NOT NULL REFERENCES im.egress_log(egress_id),
  n_items                 integer NOT NULL,
  created_at              timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source_ref, model, prompt_version, privacy_policy_version)
);

-- Same columns as 030. An archive capture is said by whoever its observation says.
CREATE OR REPLACE VIEW im.item_speaker AS
SELECT i.item_id,
       CASE WHEN i.episode_id IS NULL THEN coalesce(o.said_by, 'not_me')
            WHEN s.speaker_kind = 'owner' AND s.speaker_basis IN ('voice', 'named', 'channel') THEN 'me'
            WHEN s.speaker_kind = 'owner' THEN 'me_diar'
            ELSE 'not_me' END AS speaker,
       CASE WHEN i.episode_id IS NULL THEN coalesce(o.said_by = 'me', false)
            ELSE EXISTS (SELECT 1 FROM im.source_segments c
                         WHERE c.segment_id = ANY(i.source_segment_ids) AND c.speaker_kind = 'owner'
                           AND c.speaker_basis IN ('voice', 'named', 'channel')) END AS cites_me
FROM im.items i
LEFT JOIN im.source_segments s ON s.segment_id = i.said_by_segment_id
LEFT JOIN im.observations o ON o.observation_id = i.observation_id AND i.episode_id IS NULL;

-- Same columns as 031. A capture on a current episode, or of the archive, makes a candidate.
CREATE OR REPLACE VIEW im.idea_maturity AS
SELECT d.idea_id,
       CASE WHEN k.kept_as IS NOT NULL THEN 'idea'
            WHEN EXISTS (SELECT 1 FROM im.idea_evidence ev JOIN im.items i USING (item_id)
                         LEFT JOIN im.episodes e ON e.episode_id = i.episode_id
                         JOIN im.observation_speaker o USING (observation_id)
                         WHERE ev.idea_id = d.idea_id AND o.speaker = 'me'
                           AND (e.current OR i.episode_id IS NULL)) THEN 'candidate'
            ELSE 'observation' END AS maturity,
       k.kept_as
FROM im.ideas d
JOIN im.idea_kept k USING (idea_id);

-- Same columns as 036. Archive captures' observations through their captures, as recorded; observations that
-- belong to an idea directly (typed, and the seeded archive's) as before.
CREATE OR REPLACE VIEW im.idea_observations AS
SELECT ev.idea_id, i.observation_id, 'speech'::text AS source, o.speaker, min(s.started_at) AS said_at,
       min(s.conversation_id) AS conversation
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
JOIN im.observation_speaker o USING (observation_id)
JOIN im.source_segments s ON s.segment_id = ANY(i.source_segment_ids)
GROUP BY ev.idea_id, i.observation_id, o.speaker
UNION ALL
SELECT ev.idea_id, o.observation_id, o.source, o.said_by, o.said_at, o.conversation
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.observations o ON o.observation_id = i.observation_id
WHERE i.episode_id IS NULL
UNION ALL
SELECT o.idea_id, o.observation_id, o.source, o.said_by, o.said_at, o.conversation
FROM im.observations o WHERE o.source <> 'speech' AND o.idea_id IS NOT NULL;

-- Same columns as 048. Archive captures with their quote and gist, when and where their observation says; no arousal.
CREATE OR REPLACE VIEW pub.card_evidence AS
SELECT ev.idea_id, i.item_id, i.observation_id, w.speaker, i.quote, i.gist, e.started_at AS said_at,
       (SELECT min(s.conversation_id) FROM im.source_segments s WHERE s.segment_id = ANY(i.source_segment_ids))
         AS conversation_id,
       'speech'::text AS source, a.arousal
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
JOIN im.item_speaker w USING (item_id)
LEFT JOIN im.observation_arousal a ON a.observation_id = i.observation_id
UNION ALL
SELECT ev.idea_id, i.item_id, i.observation_id, w.speaker, i.quote, i.gist, o.said_at, o.conversation, o.source,
       NULL::real
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.observations o ON o.observation_id = i.observation_id
JOIN im.item_speaker w USING (item_id)
WHERE i.episode_id IS NULL
UNION ALL
SELECT o.idea_id, NULL, o.observation_id, o.said_by, o.quote, NULL, o.said_at, o.conversation, o.source, NULL::real
FROM im.observations o WHERE o.source <> 'speech' AND o.idea_id IS NOT NULL;

-- Same columns as 037. An archive capture counts as evidence too, said when its observation says; it has no episode.
CREATE OR REPLACE VIEW pub.evidence AS
SELECT DISTINCT ON (ev.idea_id, coalesce(i.observation_id, i.item_id))
       ev.idea_id, i.item_id, ev.relation, i.kind, w.said_by, i.quote, i.gist, i.confidence,
       coalesce(e.started_at, o.said_at) AS said_at, e.episode_id, v.verdict, v.how
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.item_said_by w USING (item_id)
LEFT JOIN im.episodes e ON e.episode_id = i.episode_id
LEFT JOIN im.observations o ON o.observation_id = i.observation_id AND i.episode_id IS NULL
LEFT JOIN im.capture_verdict v ON v.item_id = i.item_id
WHERE e.current OR i.episode_id IS NULL
ORDER BY ev.idea_id, coalesce(i.observation_id, i.item_id),
         EXISTS (SELECT 1 FROM im.item_verdicts x WHERE x.item_id = i.item_id) DESC,
         ev.relation = 'origin' DESC, i.created_at, i.item_id;
