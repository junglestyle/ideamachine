-- The re-read gate (docs/install.md): a filer check's version in its two parts, so a new filer (its prompt, model or
-- effort) can be told from a new set of themes. An idea that fits no theme is filed again when the set changes (its
-- input), and held when only the filer did, until `im reread themes`.
-- - filer_version: themes.filer_version, the prompt, schema, model and effort; theme_set: themes.theme_set, the
--   themes it was filed against. NULL: checked before this migration; the next file stage fills in the ones checked
--   under the current version, and an older one is filed again as before.
-- - egress_id: the request that checked it, for what holding it saves (the latest request under its version logged
--   before it was checked, for the ones checked before this migration).

ALTER TABLE im.theme_checks
  ADD COLUMN filer_version text NULL,
  ADD COLUMN theme_set text NULL,
  ADD COLUMN egress_id bigint NULL REFERENCES im.egress_log(egress_id) ON DELETE SET NULL;

UPDATE im.theme_checks c SET egress_id = (
  SELECT g.egress_id FROM im.egress_log g
  WHERE g.prompt_version = 'themes-file:' || c.themes_version AND g.created_at <= c.checked_at
  ORDER BY g.created_at DESC, g.egress_id DESC LIMIT 1);
