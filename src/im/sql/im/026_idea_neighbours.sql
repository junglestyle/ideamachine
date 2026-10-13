-- "Ideas like this" in Lattice: each idea's nearest ideas by meaning, and search by meaning from typed text.
-- Lattice embeds the query itself with the same model (fastembed's bge-small reproduces these vectors exactly), so
-- Idea Machine only serves the stored idea embeddings. Similarity is cosine, as for the related edges.

-- The model the idea embeddings are in: the one most of them use (a model change re-embeds every idea on the next
-- run, so a mix is brief). Lattice checks it before searching, so a model change turns search off rather than
-- comparing vectors from different models.
CREATE FUNCTION pub.embedding_model() RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, im, pub
AS $$
  SELECT model_version FROM im.idea_embeddings GROUP BY model_version ORDER BY count(*) DESC, model_version LIMIT 1
$$;

-- For every idea Lattice shows, its 10 nearest other shown ideas (rank 1 is the nearest), in the current model.
-- An idea added or corrected since the last run may have no embedding, or an old one, until the run.
CREATE VIEW pub.idea_neighbours AS
WITH cur AS (
  SELECT e.idea_id, e.embedding FROM im.idea_embeddings e
  WHERE e.model_version = pub.embedding_model() AND e.idea_id IN (SELECT idea_id FROM pub.ideas)
)
SELECT a.idea_id, n.other_id, n.similarity, n.rank
FROM cur a
CROSS JOIN LATERAL (
  SELECT b.idea_id AS other_id, (1 - (a.embedding <=> b.embedding))::real AS similarity,
         row_number() OVER (ORDER BY a.embedding <=> b.embedding, b.idea_id)::int AS rank
  FROM cur b WHERE b.idea_id <> a.idea_id
  ORDER BY a.embedding <=> b.embedding, b.idea_id LIMIT 10) n;

-- Search by meaning: the k shown ideas nearest a query vector (unit length, in pub.embedding_model()), nearest first.
-- A vector of the wrong size is refused.
CREATE FUNCTION pub.ideas_near(q vector, k int DEFAULT 10) RETURNS TABLE (idea_id uuid, similarity real)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, im, pub, public
AS $$
DECLARE
  model text := pub.embedding_model();
  dims int := (SELECT vector_dims(e.embedding) FROM im.idea_embeddings e WHERE e.model_version = model LIMIT 1);
BEGIN
  IF dims IS NULL THEN RETURN; END IF;
  IF vector_dims(q) <> dims THEN
    RAISE EXCEPTION 'query vector has % dimensions; % embeddings have %', vector_dims(q), model, dims
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  RETURN QUERY
    SELECT e.idea_id, (1 - (e.embedding <=> q))::real
    FROM im.idea_embeddings e
    WHERE e.model_version = model AND e.idea_id IN (SELECT d.idea_id FROM pub.ideas d)
    ORDER BY e.embedding <=> q, e.idea_id
    LIMIT greatest(1, least(coalesce(k, 10), 50));
END $$;

REVOKE ALL ON FUNCTION pub.embedding_model(), pub.ideas_near(vector, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pub.embedding_model(), pub.ideas_near(vector, int) TO lattice_app;
GRANT SELECT ON pub.idea_neighbours TO lattice_app;
