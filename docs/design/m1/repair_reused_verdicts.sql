-- One-off repair after the M1 deploy (2026-10-27). Claude sometimes captures the same line twice; when the episode
-- was re-keyed, both captures were reused, but feedback.carry_verdicts matches by quote and segments, so it gave
-- the verdicts of both to one copy and left the other undecided: 32 captures came back to review.
-- Each one takes the verdicts of a retired twin: the same read (egress_id), kind and quote.
-- No verdict_carries rows: carry_verdicts already settled each twin (from_item_id is the key); carried_from
-- records where the copies came from.
--   docker exec -i ideamachine-db psql -U postgres -d im < ~/review/repair_reused_verdicts.sql
-- It aborts, changing nothing, unless exactly 32 captures are missing their verdicts.
-- Rollback: DELETE FROM im.item_verdicts WHERE verdict_id BETWEEN <first> AND <last>, from its output.

\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE lost ON COMMIT DROP AS
SELECT DISTINCT ON (c.item_id) c.item_id AS to_item, t.item_id AS from_item
FROM im.items c JOIN im.episodes ce ON ce.episode_id = c.episode_id AND ce.current
JOIN im.items t ON t.egress_id = c.egress_id AND t.kind = c.kind AND t.quote = c.quote
JOIN im.episodes te ON te.episode_id = t.episode_id AND NOT te.current
WHERE NOT EXISTS (SELECT 1 FROM im.item_verdicts v WHERE v.item_id = c.item_id)
  AND NOT EXISTS (SELECT 1 FROM pub.feedback_events f WHERE f.item_id = c.item_id)
  AND EXISTS (SELECT 1 FROM im.item_verdicts v WHERE v.item_id = t.item_id)
ORDER BY c.item_id, t.item_id;

-- Twins I judged differently: the copy may have the other twin's verdict. Worth a look if not 0.
WITH latest AS (
  SELECT DISTINCT ON (item_id) item_id, verdict FROM im.item_verdicts ORDER BY item_id, decided_at DESC, verdict_id DESC)
SELECT 'twins judged differently', count(DISTINCT l.to_item)
FROM lost l JOIN im.items c ON c.item_id = l.to_item
JOIN im.items t ON t.egress_id = c.egress_id AND t.kind = c.kind AND t.quote = c.quote AND t.item_id <> c.item_id
JOIN latest a ON a.item_id = l.from_item JOIN latest b ON b.item_id = t.item_id
WHERE a.verdict <> b.verdict;

DO $$
DECLARE n int := (SELECT count(*) FROM lost);
BEGIN
  IF n <> 32 THEN RAISE EXCEPTION 'expected 32 captures missing their verdicts, found %; nothing changed', n; END IF;
END $$;

WITH ins AS (
  INSERT INTO im.item_verdicts (item_id, segment_ids, quote, verdict, note, how, decided_at, carried_from)
  SELECT c.item_id, c.source_segment_ids, c.quote, v.verdict, v.note, v.how, v.decided_at, v.verdict_id
  FROM lost l JOIN im.items c ON c.item_id = l.to_item JOIN im.item_verdicts v ON v.item_id = l.from_item
  ORDER BY v.decided_at, v.verdict_id
  RETURNING verdict_id, item_id)
SELECT 'inserted', count(*) AS verdicts, count(DISTINCT item_id) AS captures, min(verdict_id) AS first,
       max(verdict_id) AS last
FROM ins;

-- Expect 0: no current capture left without its twin's verdict.
SELECT 'still missing', count(*) FROM lost l
WHERE NOT EXISTS (SELECT 1 FROM im.item_verdicts v WHERE v.item_id = l.to_item);

COMMIT;
