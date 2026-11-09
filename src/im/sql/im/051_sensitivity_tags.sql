-- Sensitivity tags (decision 0012): eight descriptive tags, profanity, sex, gender_politics, politics, drugs,
-- offensive, violence and private_person, one Jev yes/no each (im.sensitivity), in `categories` as before. Jev's
-- probability for every tag is kept beside them, so the cut can be set from my marks. The old categories
-- (political_extremism, racism) go when each idea or theme is tagged again under the new tagger. Which tags hide is
-- the view's choice: pub.sensitivity lists them, and anything tagged is sensitive unless my mark says otherwise.

ALTER TABLE im.idea_sensitivity ADD COLUMN probabilities jsonb NULL;
ALTER TABLE im.theme_sensitivity ADD COLUMN probabilities jsonb NULL;

-- As in 027, with "by" saying 'model' rather than 'claude' when no mark of mine decides.
CREATE OR REPLACE VIEW pub.sensitivity AS
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
       CASE WHEN mine IS NOT NULL THEN 'owner' ELSE 'model' END AS "by"
FROM both_kinds
WHERE mine IS NOT NULL OR cardinality(categories) > 0;
