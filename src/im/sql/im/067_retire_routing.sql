-- Retire V1's routing (decision 0014): router v3, `im review`, `im label`, `im eval`, `im train`, `im project` and the
-- triage backends are gone, and what only they wrote goes with them: the routes (006), the triage answers, episode
-- embeddings and trained classifiers (004), and the hand labels and projects registry (003). The attention queue (066)
-- does what the review list was for.
--
-- Labels and projects are human input, never dropped to rebuild (003). The rebuild carried none over (decision 0012),
-- and I haven't labeled since; if any are there, this stops before dropping anything, so they can be exported first.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM im.labels) OR EXISTS (SELECT 1 FROM im.projects) THEN
    RAISE EXCEPTION 'im.labels or im.projects holds rows: export them before migration 067 drops them';
  END IF;
END $$;

DROP TABLE im.routes, im.triage, im.episode_embeddings, im.classifiers, im.labels, im.projects;
