-- Neutral view (migration 033, docs/decisions/0007-neutral-proposals.md). Read-only: run before and after the
-- deploy and diff the two outputs. Before the migration, the pub.cards sections fail (no view yet); that's expected.
--   docker exec -i ideamachine-db psql -U postgres -d im < ~/review/cards_check.sql
--
-- Deploy:
--   1. docker exec ideamachine-db pg_dump -U postgres -Fc im > ~/review/im-pre-cards.dump
--   2. run this file (before), then install/nas.sh, then this file again (after), and diff.
-- Rollback (033 adds views and one function, nothing reads them but im web; no data changes):
--   docker compose -f install/compose.yaml rm -sf web
--   docker exec <tailscale container> tailscale serve --https=8445 off
--   docker exec -i ideamachine-db psql -U postgres -d im <<'SQL'
--   BEGIN;
--   DROP FUNCTION pub.card_neighbours(uuid, int);
--   DROP VIEW pub.cards; DROP VIEW pub.card_links; DROP VIEW pub.card_evidence;
--   DELETE FROM im.schema_migrations WHERE version = 33;
--   COMMIT;
--   SQL
--   then check out the previous commit so the next run doesn't re-apply 033. The dump is the backstop.

\pset footer off

-- Unchanged by the deploy: pub.ideas' ids (Lattice's view keeps its shape), and nothing sent to Claude.
SELECT 'pub.ideas' AS check, count(*) AS n, md5(string_agg(idea_id::text, ',' ORDER BY idea_id)) AS ids
FROM pub.ideas;
SELECT 'egress_log' AS check, count(*) AS n, max(egress_id) AS last FROM im.egress_log;

\set ON_ERROR_STOP off

-- Expect every cluster with a current capture, plus the archive's and mine. Compare with m2_check's
-- "clusters by maturity": captured clusters only on superseded reads are the difference.
SELECT 'cards by maturity' AS check, origin, maturity, coalesce(kept_as, '-') AS kept_as, count(*) AS n
FROM pub.cards GROUP BY 2, 3, 4 ORDER BY 2, 3, 4;

-- What the neutral view shows that Lattice hides: clusters whose every capture I discarded. Expect a few dozen.
SELECT 'cards not in pub.ideas' AS check, c.maturity, count(*) AS n
FROM pub.cards c WHERE c.idea_id NOT IN (SELECT idea_id FROM pub.ideas) GROUP BY 2 ORDER BY 2;

-- Expect 0: a card with no observations and no origin to excuse it.
SELECT 'empty captured cards' AS check, count(*) AS n FROM pub.cards WHERE origin = 'captured' AND observations = 0;

-- The first page of the feed, as im web shows it.
SELECT 'feed' AS check, last_seen::date, maturity, by_me, by_me_diar, by_others, conversations, title
FROM pub.cards WHERE maturity IN ('candidate', 'idea') ORDER BY last_seen DESC, idea_id LIMIT 20;

-- Expect it to run in well under a second.
EXPLAIN ANALYZE SELECT * FROM pub.cards WHERE maturity IN ('candidate', 'idea') ORDER BY last_seen DESC LIMIT 51;
