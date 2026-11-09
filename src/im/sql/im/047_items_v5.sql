-- Captures as extraction v5 makes them (decision 0012): no kind and no confidence, and three fields v1-v3 never
-- had: the reading (what was meant, with mishearings fixed), who a relayed thought is from, and the mishearings the
-- reading fixed, as [{heard, meant}]. `checks` holds the code checks the capture failed (im.capture_checks), empty
-- when it passed. All four are NULL on captures made before v5.

ALTER TABLE im.items
  ALTER COLUMN kind DROP NOT NULL,
  ALTER COLUMN confidence DROP NOT NULL,
  ADD COLUMN reading text NULL,
  ADD COLUMN attributed_to text NULL,
  ADD COLUMN mishearings jsonb NULL,
  ADD COLUMN checks text[] NULL;
