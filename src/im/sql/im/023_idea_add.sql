-- Ideas I add myself, from Lattice or a claude.ai conversation through the connector (idea_add): origin 'mine'.
-- An added idea is a first-class idea with no evidence: it holds only what I typed or approved, so forgetting
-- has nothing to take from it. idea_correct and idea_note work on it as on any idea.
--
-- Lattice is local-first and generates the idea_id, so the idea must exist the moment the event is accepted, not
-- on the next run: a trigger creates it (and turns link_to into ordinary link events I can unlink later). The run
-- then embeds, places, relates (a near-duplicate gets a 'related' edge, never a merge), files and scores it.
ALTER TABLE im.ideas DROP CONSTRAINT ideas_origin_check;
ALTER TABLE im.ideas ADD CONSTRAINT ideas_origin_check CHECK (origin IN ('archive', 'captured', 'mine'));

ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_kind_check;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_kind_check CHECK (kind IN (
  'star', 'unstar', 'keep', 'discard', 'link', 'unlink', 'pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme',
  'item_keep', 'item_discard', 'item_star', 'idea_correct', 'idea_note', 'idea_compare', 'digest_star',
  'digest_dismiss', 'idea_add'));
-- payload {"title": 1-120 chars, "statement": 1-1000, "note"?: <=1000, "link_to"?: [uuid, ...] at most 10}.
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_idea_add CHECK (
  kind <> 'idea_add' OR (
    idea_id IS NOT NULL
    AND coalesce(jsonb_typeof(payload->'title'), '') = 'string' AND length(btrim(payload->>'title')) BETWEEN 1 AND 120
    AND coalesce(jsonb_typeof(payload->'statement'), '') = 'string'
    AND length(btrim(payload->>'statement')) BETWEEN 1 AND 1000
    AND (NOT payload ? 'note' OR (jsonb_typeof(payload->'note') = 'string' AND length(payload->>'note') <= 1000))
    AND (NOT payload ? 'link_to' OR (jsonb_typeof(payload->'link_to') = 'array'
                                     AND jsonb_array_length(payload->'link_to') <= 10))));

-- BEFORE INSERT, so the idea exists when the event's own foreign key is checked. Runs as the pipeline (it owns
-- im); Lattice can still only append events. A resend finds the idea already there and changes nothing. An id
-- that names an idea I didn't add is refused, so idea_add can't touch captured or archive ideas.
CREATE FUNCTION im.add_idea() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, im, pub
AS $$
DECLARE
  existing text;
  other uuid;
BEGIN
  -- A malformed event is left for the CHECK to refuse (it runs after this trigger; the statement then rolls back).
  IF NEW.idea_id IS NULL OR jsonb_typeof(NEW.payload->'title') IS DISTINCT FROM 'string'
     OR jsonb_typeof(NEW.payload->'statement') IS DISTINCT FROM 'string' THEN
    RETURN NEW;
  END IF;
  SELECT origin INTO existing FROM im.ideas WHERE idea_id = NEW.idea_id;
  IF existing IS NOT NULL THEN
    IF existing <> 'mine' THEN
      RAISE EXCEPTION 'idea_add: % is an existing % idea', NEW.idea_id, existing USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  INSERT INTO im.ideas (idea_id, title, statement, origin, notes, created_at)
  VALUES (NEW.idea_id, btrim(NEW.payload->>'title'), btrim(NEW.payload->>'statement'), 'mine',
          nullif(btrim(coalesce(NEW.payload->>'note', '')), ''), coalesce(NEW.at, now()));
  FOR other IN
    SELECT DISTINCT d.idea_id FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(NEW.payload->'link_to') = 'array'
                                                    THEN NEW.payload->'link_to' ELSE '[]' END) l(id)
    JOIN im.ideas d ON d.idea_id::text = l.id WHERE d.idea_id <> NEW.idea_id   -- unknown ids are skipped
  LOOP
    INSERT INTO pub.feedback_events (at, kind, idea_id, other_idea_id, source)
    VALUES (coalesce(NEW.at, now()), 'link', NEW.idea_id, other, NEW.source);
  END LOOP;
  RETURN NEW;
END $$;

CREATE TRIGGER feedback_events_idea_add BEFORE INSERT ON pub.feedback_events
  FOR EACH ROW WHEN (NEW.kind = 'idea_add') EXECUTE FUNCTION im.add_idea();

-- pub.ideas shows ideas I added (no evidence) beside the archive's and those with captures I haven't discarded.
CREATE OR REPLACE VIEW pub.ideas AS
SELECT d.idea_id, x.title, x.statement, d.origin, d.archive_ref, d.notes, d.created_at,
       count(ev.item_id) AS n_evidence,
       min(ev.said_at) AS first_said, max(ev.said_at) AS last_said,
       count(*) FILTER (WHERE ev.verdict IN ('keep', 'star')) AS kept,
       count(*) FILTER (WHERE ev.verdict = 'discard') AS discarded,
       coalesce((SELECT array_agg(t.name ORDER BY t.name) FROM im.idea_themes it JOIN im.themes t USING (theme_id)
                 WHERE it.idea_id = d.idea_id), '{}') AS themes,
       coalesce((SELECT f.kind = 'star' FROM pub.feedback_events f
                 WHERE f.idea_id = d.idea_id AND f.kind IN ('star', 'unstar') ORDER BY f.at DESC LIMIT 1), false)
         AS starred,
       x.claude_title, x.claude_statement, x.my_note,
       (x.title IS DISTINCT FROM x.claude_title OR x.statement IS DISTINCT FROM x.claude_statement) AS corrected
FROM im.ideas d
JOIN im.idea_text x USING (idea_id)
LEFT JOIN pub.evidence ev ON ev.idea_id = d.idea_id
GROUP BY d.idea_id, x.title, x.statement, x.claude_title, x.claude_statement, x.my_note
HAVING d.origin IN ('archive', 'mine')
    OR count(*) FILTER (WHERE ev.item_id IS NOT NULL AND ev.verdict IS DISTINCT FROM 'discard') > 0;

-- Ranking: adding an idea myself counts like a star (the 0.1), whether or not I also star it. (Same columns.)
CREATE OR REPLACE VIEW pub.idea_scores AS
SELECT d.idea_id,
       round((0.7 * r.rubric + 0.2 * least(1, ln(greatest(d.n_evidence, 1)) / ln(4))
              + 0.1 * (d.starred OR d.origin = 'mine')::int)::numeric, 3) AS score,
       round(r.rubric::numeric, 3) AS rubric, s.generality, s.novelty, s.compression, s.outside_value,
       round(least(1, ln(greatest(d.n_evidence, 1)) / ln(4))::numeric, 3) AS recurrence, s.why, s.rubric_version
FROM pub.ideas d
LEFT JOIN im.idea_scores s USING (idea_id)
LEFT JOIN LATERAL (SELECT (s.generality + s.novelty + s.compression + s.outside_value - 4) / 16.0 AS rubric) r ON true;
