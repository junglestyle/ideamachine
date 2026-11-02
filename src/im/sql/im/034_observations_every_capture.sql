-- B5 (docs/ROADMAP-v2.md, 2026-11-01): an item joins an observation only when its quote matches every capture already
-- there, not any one (im.observations.assign_stage). Matching any one let a quote Claude stitched from two passages
-- with "..." chain both into one observation. Observations are rebuilt by the next run under the new rule; nothing
-- stores an observation id but items (ON DELETE SET NULL), so dropping them all is safe.
DELETE FROM im.observations;
