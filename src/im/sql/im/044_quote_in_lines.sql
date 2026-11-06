-- How much of each capture's quote is on the lines it cites (im.quotes): the share of its words found there, in
-- order, once its citation has grown to the lines they're on. NULL until the run checks it, which it does for every
-- capture on the run after this. Under 0.6 the quote isn't from its lines, and the capture is one to read again.
ALTER TABLE im.items ADD COLUMN quote_in_lines real NULL;
