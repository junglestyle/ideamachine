-- The rubric is retired (decision 0012): nothing scores ideas any more, and nothing here reads the scores. Lattice,
-- the old app, read pub.idea_scores and pub.compare_pairs; it's no longer used. Past idea_compare events stay in
-- the ledger, and the kind stays allowed (018's check), though nothing reads them.
DROP VIEW pub.compare_pairs;
DROP VIEW pub.idea_scores;
DROP TABLE im.idea_scores;
