-- Mishearings (decision 0012): words the transcription got wrong, as (heard, meant). Extraction v5 reports each fix
-- its reading makes (im.items.mishearings); those are provisional. I confirm or reject a pair, or add one of my own,
-- confirmed. A rejected pair stays rejected however often it's proposed again. Pairs match case-insensitively.
-- The context line of a later read lists the confirmed and provisional pairs whose heard words occur in its
-- transcript, as hints: an episode already read isn't read again for a new one.

CREATE TABLE im.mishearing_decisions (
  heard       text NOT NULL CHECK (btrim(heard) <> ''),
  meant       text NOT NULL CHECK (btrim(meant) <> ''),
  status      text NOT NULL CHECK (status IN ('confirmed', 'rejected')),
  decided_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX mishearing_decisions_pair ON im.mishearing_decisions (lower(heard), lower(meant));

-- Every pair: proposed by captures on active episodes (`seen` of them), decided by me, or both. A decided pair keeps
-- my spelling; a proposed one its first.
CREATE VIEW im.mishearings AS
WITH proposed AS (
  SELECT lower(m->>'heard') AS heard_key, lower(m->>'meant') AS meant_key,
         (array_agg(m->>'heard' ORDER BY i.created_at))[1] AS heard,
         (array_agg(m->>'meant' ORDER BY i.created_at))[1] AS meant,
         count(DISTINCT i.item_id) AS seen
  FROM im.items i
  JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
  CROSS JOIN LATERAL jsonb_array_elements(coalesce(i.mishearings, '[]'::jsonb)) m
  WHERE btrim(coalesce(m->>'heard', '')) <> '' AND btrim(coalesce(m->>'meant', '')) <> ''
  GROUP BY 1, 2
)
SELECT coalesce(d.heard, p.heard) AS heard, coalesce(d.meant, p.meant) AS meant,
       coalesce(d.status, 'provisional') AS status, coalesce(p.seen, 0) AS seen, d.decided_at
FROM proposed p
FULL JOIN im.mishearing_decisions d ON lower(d.heard) = p.heard_key AND lower(d.meant) = p.meant_key;
