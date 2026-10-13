-- How I made each verdict, from Lattice's lower-touch review ("check 5, keep the rest"): 'one' read and judged on
-- its own, 'audit' a spot check Lattice picked across Idea Machine's confidence (an unbiased sample of the queue),
-- 'bulk' kept with the rest without being read. A bulk keep is a real keep (the idea stays in the lattice), but
-- tuning against my judgment counts only 'one' and 'audit'. Carried forward with the verdict.
ALTER TABLE im.item_verdicts ADD COLUMN how text NOT NULL DEFAULT 'one'
  CONSTRAINT item_verdicts_how_check CHECK (how IN ('one', 'audit', 'bulk'));
