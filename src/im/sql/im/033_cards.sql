-- The neutral view (docs/decisions/0007-neutral-proposals.md): every cluster that passed the gates, with what's known
-- about it and no score. Unlike pub.ideas and pub.connections, nothing here reads my keep/discard verdicts: a
-- capture I discarded still shows, so the view tells me which gate let it through. Views only; nothing to forget.

-- Each current capture of a cluster: who said it (me, me_diar or not_me; never a name), when, and in which
-- conversation.
CREATE VIEW pub.card_evidence AS
SELECT ev.idea_id, i.item_id, i.observation_id, w.speaker, i.quote, i.gist, e.started_at AS said_at,
       (SELECT min(s.conversation_id) FROM im.source_segments s WHERE s.segment_id = ANY(i.source_segment_ids))
         AS conversation_id
FROM im.idea_evidence ev
JOIN im.items i USING (item_id)
JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
JOIN im.item_speaker w USING (item_id);

-- Every edge, mine included (my latest link/unlink per pair, as in pub.connections), between any two clusters.
CREATE VIEW pub.card_links AS
SELECT l.a, l.b, l.kind, l.method
FROM im.idea_links l
UNION ALL
SELECT m.a, m.b, 'mine', 'feedback'
FROM (SELECT DISTINCT ON (least(f.idea_id, f.other_idea_id), greatest(f.idea_id, f.other_idea_id))
             least(f.idea_id, f.other_idea_id) AS a, greatest(f.idea_id, f.other_idea_id) AS b, f.kind
      FROM pub.feedback_events f
      WHERE f.kind IN ('link', 'unlink') AND f.idea_id <> f.other_idea_id
      ORDER BY least(f.idea_id, f.other_idea_id), greatest(f.idea_id, f.other_idea_id), f.event_id DESC) m
WHERE m.kind = 'link';

-- One card per cluster with a current capture, plus the archive's and the ideas I added. Counts are of
-- observations (a re-capture of the same speech counts once). last_seen orders the feed: when it was last said, or
-- when an idea without captures was made.
CREATE VIEW pub.cards AS
WITH links AS (
  SELECT x.idea_id, count(*) AS links
  FROM pub.card_links l CROSS JOIN LATERAL (VALUES (l.a), (l.b)) x(idea_id)
  GROUP BY x.idea_id
), seen AS (
  SELECT ce.idea_id,
         count(DISTINCT coalesce(ce.observation_id, ce.item_id)) AS observations,
         count(DISTINCT coalesce(ce.observation_id, ce.item_id)) FILTER (WHERE ce.speaker = 'me') AS by_me,
         count(DISTINCT coalesce(ce.observation_id, ce.item_id)) FILTER (WHERE ce.speaker = 'me_diar') AS by_me_diar,
         count(DISTINCT coalesce(ce.observation_id, ce.item_id)) FILTER (WHERE ce.speaker = 'not_me') AS by_others,
         count(DISTINCT ce.conversation_id) AS conversations,
         min(ce.said_at) AS first_said, max(ce.said_at) AS last_said
  FROM pub.card_evidence ce GROUP BY ce.idea_id
)
SELECT d.idea_id, x.title, x.statement, d.origin, m.maturity, m.kept_as, x.my_note,
       coalesce(s.observations, 0) AS observations, coalesce(s.by_me, 0) AS by_me,
       coalesce(s.by_me_diar, 0) AS by_me_diar, coalesce(s.by_others, 0) AS by_others,
       coalesce(s.conversations, 0) AS conversations, s.first_said, coalesce(s.last_said, d.created_at) AS last_seen,
       coalesce(l.links, 0) AS links, p.idea_id IS NOT NULL AS proposed
FROM im.ideas d
JOIN im.idea_text x USING (idea_id)
JOIN im.idea_maturity m USING (idea_id)
LEFT JOIN seen s ON s.idea_id = d.idea_id
LEFT JOIN links l ON l.idea_id = d.idea_id
LEFT JOIN (SELECT DISTINCT idea_id FROM pub.proposals) p ON p.idea_id = d.idea_id
WHERE d.origin IN ('archive', 'mine') OR s.idea_id IS NOT NULL;

-- A card's nearest other cards by meaning, in the current embedding model (as pub.idea_neighbours, over every card).
CREATE FUNCTION pub.card_neighbours(seed uuid, k int DEFAULT 8) RETURNS TABLE (idea_id uuid, similarity real)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, im, pub, public
AS $$
  SELECT b.idea_id, (1 - (b.embedding <=> a.embedding))::real
  FROM im.idea_embeddings a
  JOIN im.idea_embeddings b ON b.model_version = a.model_version AND b.idea_id <> a.idea_id
  WHERE a.idea_id = seed AND a.model_version = pub.embedding_model()
    AND b.idea_id IN (SELECT c.idea_id FROM pub.cards c)
  ORDER BY b.embedding <=> a.embedding, b.idea_id
  LIMIT greatest(1, least(coalesce(k, 8), 50))
$$;

REVOKE ALL ON FUNCTION pub.card_neighbours(uuid, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pub.card_neighbours(uuid, int) TO lattice_app;
GRANT SELECT ON pub.card_evidence, pub.card_links, pub.cards TO lattice_app;
