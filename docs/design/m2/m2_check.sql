-- M2 snapshot (docs/design/m2-observations-candidates-ideas.md). Read-only: run it before and after the deploy and
-- diff the two outputs. Before the migration, the M2 sections fail (no im.observations yet); that's expected.
--   docker exec -i ideamachine-db psql -U postgres -d im < ~/review/m2_check.sql

\pset footer off

-- Same set before and after: the hash of pub.ideas' ids, and how many.
SELECT 'pub.ideas' AS check, count(*) AS n, md5(string_agg(idea_id::text, ',' ORDER BY idea_id)) AS ids
FROM pub.ideas;

-- Expect no new rows during the run: nothing goes to Claude.
SELECT 'egress_log' AS check, count(*) AS n, max(egress_id) AS last FROM im.egress_log;

\set ON_ERROR_STOP off

-- Expect 0.
SELECT 'current items without an observation' AS check, count(*) AS n
FROM im.items i JOIN im.episodes e USING (episode_id)
WHERE e.current AND i.observation_id IS NULL;

SELECT 'observations' AS check, count(*) AS n,
       count(*) FILTER (WHERE (SELECT count(*) FROM im.items i WHERE i.observation_id = o.observation_id) > 1)
         AS with_several_captures
FROM im.observations o;

-- Current captures by speaker. Expect about me 386, me_diar 26, not_me 115 (2026-10-28).
SELECT 'current captures by speaker' AS check, w.speaker, count(*) AS n
FROM im.items i JOIN im.episodes e USING (episode_id) JOIN im.item_speaker w USING (item_id)
WHERE e.current GROUP BY w.speaker ORDER BY w.speaker;

-- Clusters by maturity. Expect about 326 candidates and 113 observations among captured clusters, and ideas =
-- the archive seed + ideas I added (canonical) + starred ones (pinned).
SELECT 'clusters by maturity' AS check, d.origin, m.maturity, m.kept_as, count(*) AS n
FROM im.idea_maturity m JOIN im.ideas d USING (idea_id)
GROUP BY d.origin, m.maturity, m.kept_as ORDER BY d.origin, m.maturity, m.kept_as;

-- The same, for what Lattice shows (pub.ideas).
SELECT 'pub.ideas by maturity' AS check, m.maturity, m.kept_as, count(*) AS n
FROM pub.ideas p JOIN im.idea_maturity m USING (idea_id)
GROUP BY m.maturity, m.kept_as ORDER BY m.maturity, m.kept_as;
