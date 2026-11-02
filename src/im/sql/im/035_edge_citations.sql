-- Edge citations (M2, docs/design/m2-observations-candidates-ideas.md, Edges): every edge cites the observations it
-- rests on. An edge cites the origin observations of its two ends (lattice.cite, every run): what each idea's
-- statement was made from. Cite what exists: an end with no observation (the archive, an idea I added) adds none, so
-- an edge between two such ideas cites nothing and its `method` says where it came from. Typing waits for Jev (M4).
--
-- Derived, like `related` edges: lattice.cite recomputes the column every run, and forgetting removes the ids it
-- deletes. confidence: the cosine for `related`; NULL where nothing measured one.
ALTER TABLE im.idea_links ADD COLUMN cited_observation_ids uuid[] NOT NULL DEFAULT '{}';
ALTER TABLE im.idea_links ADD COLUMN confidence real NULL;

UPDATE im.idea_links SET confidence = weight WHERE kind = 'related';
