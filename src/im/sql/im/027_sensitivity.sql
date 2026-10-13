-- Demo mode in Lattice: ideas and themes I'd hesitate to show in a job interview are tagged, so Lattice can hide
-- them. Categories: sex, gender_politics (gender and sexual politics), political_extremism (fascism, communism and
-- the like), violence, racism, profanity. Claude tags each shown idea from its text (my corrections and notes
-- included) and each theme from its name and description, on the run, again whenever that text changes. My own mark
-- (idea_sensitive, theme_sensitive) wins either way and is read straight from the events, so it shows at once.

CREATE TABLE im.idea_sensitivity (
  idea_id         uuid PRIMARY KEY REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  categories      text[] NOT NULL,          -- empty: Claude found nothing to hide
  input_hash      text NOT NULL,
  tagger_version  text NOT NULL,
  tagged_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE im.theme_sensitivity (
  theme_id        uuid PRIMARY KEY REFERENCES im.themes(theme_id) ON DELETE CASCADE,
  categories      text[] NOT NULL,
  input_hash      text NOT NULL,
  tagger_version  text NOT NULL,
  tagged_at       timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_kind_check;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_kind_check CHECK (kind IN (
  'star', 'unstar', 'keep', 'discard', 'link', 'unlink', 'pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme',
  'item_keep', 'item_discard', 'item_star', 'idea_correct', 'idea_note', 'idea_compare', 'digest_star',
  'digest_dismiss', 'idea_add', 'merge_theme', 'split_theme', 'file_idea', 'unfile_idea', 'idea_sensitive',
  'theme_sensitive'));
-- payload {"sensitive": true | false}. idea_sensitive names its idea (a forgotten idea takes its events with it).
-- theme_sensitive's theme is checked at insert by the theme trigger below, since a deleted theme leaves its events
-- with a NULL theme_id.
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_sensitive CHECK (
  kind NOT IN ('idea_sensitive', 'theme_sensitive') OR (
    coalesce(jsonb_typeof(payload->'sensitive'), '') = 'boolean'
    AND (kind <> 'idea_sensitive' OR idea_id IS NOT NULL)));

CREATE TRIGGER feedback_events_sensitive_theme_named BEFORE INSERT ON pub.feedback_events
  FOR EACH ROW WHEN (NEW.kind = 'theme_sensitive') EXECUTE FUNCTION im.theme_event_names_theme();

-- One row per idea or theme that is hidden in demo mode or that I've marked: by 'owner' when my latest mark decides
-- (categories then Claude's, if any), else 'claude'. Untagged means not sensitive. Only what Lattice shows.
CREATE VIEW pub.sensitivity AS
WITH idea_marks AS (
  SELECT DISTINCT ON (f.idea_id) f.idea_id AS id, (f.payload->>'sensitive')::boolean AS sensitive
  FROM pub.feedback_events f WHERE f.kind = 'idea_sensitive' ORDER BY f.idea_id, f.event_id DESC
), theme_marks AS (
  SELECT DISTINCT ON (f.theme_id) f.theme_id AS id, (f.payload->>'sensitive')::boolean AS sensitive
  FROM pub.feedback_events f WHERE f.kind = 'theme_sensitive' AND f.theme_id IS NOT NULL
  ORDER BY f.theme_id, f.event_id DESC
), both_kinds AS (
  SELECT 'idea' AS kind, d.idea_id AS id, m.sensitive AS mine, s.categories
  FROM pub.ideas d LEFT JOIN idea_marks m ON m.id = d.idea_id LEFT JOIN im.idea_sensitivity s USING (idea_id)
  UNION ALL
  SELECT 'theme', t.theme_id, m.sensitive, s.categories
  FROM im.themes t LEFT JOIN theme_marks m ON m.id = t.theme_id LEFT JOIN im.theme_sensitivity s USING (theme_id)
)
SELECT kind, id, coalesce(mine, cardinality(categories) > 0) AS sensitive, coalesce(categories, '{}') AS categories,
       CASE WHEN mine IS NOT NULL THEN 'owner' ELSE 'claude' END AS "by"
FROM both_kinds
WHERE mine IS NOT NULL OR cardinality(categories) > 0;

GRANT SELECT ON pub.sensitivity TO lattice_app;
