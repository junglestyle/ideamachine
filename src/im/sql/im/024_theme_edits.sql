-- Shaping themes from Lattice (ROADMAP Phase 2, slice 4): pin, unpin, rename, reject, and now merge, split, and
-- filing or unfiling one idea by hand. Every theme event applies the moment it's inserted (a trigger), like
-- idea_add, so Lattice's local-first copy never flips back while it waits for the hourly run. The run does the
-- slow parts: the filer files ideas left without a theme, and new themes without a description get one.
--
-- My structure holds. The filer only files ideas with no theme, the proposer only groups ideas with none, and
-- re-filing a corrected idea removes only the filer's and proposer's placements ('assigned', 'proposed'), never
-- the ones I made ('mine'). An idea I unfile is never filed into that theme again (im.idea_theme_blocks).

CREATE TABLE im.idea_theme_blocks (
  idea_id     uuid NOT NULL REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  theme_id    uuid NOT NULL REFERENCES im.themes(theme_id) ON DELETE CASCADE,
  blocked_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (idea_id, theme_id)
);

ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_kind_check;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_kind_check CHECK (kind IN (
  'star', 'unstar', 'keep', 'discard', 'link', 'unlink', 'pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme',
  'item_keep', 'item_discard', 'item_star', 'idea_correct', 'idea_note', 'idea_compare', 'digest_star',
  'digest_dismiss', 'idea_add', 'merge_theme', 'split_theme', 'file_idea', 'unfile_idea'));

-- Shapes (NOT VALID: older rename events from the CLI predate them):
--   every theme event: theme_id (checked by a trigger, below).
--   pin_theme, unpin_theme, reject_theme: nothing more.
--   rename_theme: theme_id; {"name": 1-80 chars}.
--   merge_theme: theme_id (absorbed); {"into": theme id}.
--   split_theme: theme_id (source); {"new_theme_id": uuid Lattice generates, "name": 1-80 chars,
--                "idea_ids": [1-500 idea ids]}.
--   file_idea, unfile_idea: idea_id and theme_id.
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_theme_edits CHECK (
  kind NOT IN ('pin_theme', 'unpin_theme', 'reject_theme', 'rename_theme', 'merge_theme', 'split_theme',
               'file_idea', 'unfile_idea') OR (
    (kind NOT IN ('rename_theme', 'split_theme') OR (
      coalesce(jsonb_typeof(payload->'name'), '') = 'string' AND length(btrim(payload->>'name')) BETWEEN 1 AND 80))
    AND (kind <> 'merge_theme' OR (
      coalesce(jsonb_typeof(payload->'into'), '') = 'string'
      AND payload->>'into' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      AND payload->>'into' <> theme_id::text))
    AND (kind <> 'split_theme' OR (
      coalesce(jsonb_typeof(payload->'new_theme_id'), '') = 'string'
      AND payload->>'new_theme_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      AND coalesce(jsonb_typeof(payload->'idea_ids'), '') = 'array'
      AND jsonb_array_length(payload->'idea_ids') BETWEEN 1 AND 500))
    AND (kind NOT IN ('file_idea', 'unfile_idea') OR idea_id IS NOT NULL)))
  NOT VALID;

-- Every theme event names its theme when it's sent. Not a CHECK: deleting a theme (reject, merge) sets theme_id to
-- NULL on its past events, which outlive it as history. A theme already gone (merged away on another device while
-- this event sat in a queue) isn't an error: the reference is kept in the payload and the event applies as
-- 'no such theme'.
CREATE FUNCTION im.theme_event_names_theme() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, im, pub
AS $$
BEGIN
  IF NEW.theme_id IS NULL THEN
    RAISE EXCEPTION '% needs a theme_id', NEW.kind USING ERRCODE = 'check_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM im.themes WHERE theme_id = NEW.theme_id) THEN
    NEW.payload := NEW.payload || jsonb_build_object('theme_gone', NEW.theme_id);
    NEW.theme_id := NULL;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER feedback_events_theme_named BEFORE INSERT ON pub.feedback_events
  FOR EACH ROW WHEN (NEW.kind IN ('pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme', 'merge_theme',
                                  'split_theme', 'file_idea', 'unfile_idea'))
  EXECUTE FUNCTION im.theme_event_names_theme();

-- Applies one theme event and says what happened. Called by the trigger, and by the run for any event inserted
-- before this migration. Something that no longer applies (a theme merged away on another device, a name another
-- theme has) is recorded, not refused: Lattice's next sync shows the true state.
CREATE FUNCTION im.apply_theme_event(e pub.feedback_events) RETURNS text LANGUAGE plpgsql
SET search_path = pg_catalog, im, pub
AS $$
DECLARE
  t im.themes;
  target im.themes;
  v_name text := btrim(coalesce(e.payload->>'name', ''));
  new_id uuid;
  moved int;
BEGIN
  SELECT * INTO t FROM im.themes WHERE theme_id = e.theme_id;
  IF NOT FOUND THEN
    -- A split resent after it applied names a source that may be gone; the new theme says it already happened.
    IF e.kind = 'split_theme' AND EXISTS (SELECT 1 FROM im.themes WHERE theme_id = (e.payload->>'new_theme_id')::uuid)
    THEN RETURN 'repeat'; END IF;
    RETURN 'no such theme';
  END IF;
  IF e.kind IN ('rename_theme', 'split_theme') AND (v_name = '' OR EXISTS (
       SELECT 1 FROM im.themes x WHERE lower(x.name) = lower(v_name)
         AND x.theme_id <> CASE WHEN e.kind = 'rename_theme' THEN t.theme_id ELSE (e.payload->>'new_theme_id')::uuid END))
  THEN
    RETURN CASE WHEN v_name = '' THEN 'no name given' ELSE 'name taken' END;
  END IF;

  IF e.kind = 'pin_theme' THEN
    UPDATE im.themes SET pinned = true WHERE theme_id = t.theme_id;
    RETURN 'pinned';
  ELSIF e.kind = 'unpin_theme' THEN
    UPDATE im.themes SET pinned = false WHERE theme_id = t.theme_id;
    RETURN 'unpinned';
  ELSIF e.kind = 'rename_theme' THEN
    UPDATE im.themes SET name = v_name WHERE theme_id = t.theme_id;
    RETURN 'renamed';
  ELSIF e.kind = 'reject_theme' THEN   -- only proposals: pinned themes and the archive's are mine
    IF t.origin <> 'clustered' OR t.pinned THEN RETURN 'not rejected: only unpinned proposals can be'; END IF;
    INSERT INTO im.theme_rejections (name) VALUES (t.name) ON CONFLICT DO NOTHING;
    DELETE FROM im.themes WHERE theme_id = t.theme_id;
    RETURN 'rejected';
  ELSIF e.kind = 'merge_theme' THEN
    SELECT * INTO target FROM im.themes WHERE theme_id = (e.payload->>'into')::uuid;
    IF NOT FOUND THEN RETURN 'no such theme to merge into'; END IF;
    INSERT INTO im.idea_themes (idea_id, theme_id, origin)
    SELECT it.idea_id, target.theme_id, it.origin FROM im.idea_themes it WHERE it.theme_id = t.theme_id
      AND NOT EXISTS (SELECT 1 FROM im.idea_theme_blocks b WHERE b.idea_id = it.idea_id AND b.theme_id = target.theme_id)
    ON CONFLICT DO NOTHING;
    IF t.origin = 'clustered' AND NOT t.pinned THEN   -- a proposal I folded away: don't propose it again
      INSERT INTO im.theme_rejections (name) VALUES (t.name) ON CONFLICT DO NOTHING;
    END IF;
    DELETE FROM im.themes WHERE theme_id = t.theme_id;
    RETURN 'merged';
  ELSIF e.kind = 'split_theme' THEN
    new_id := (e.payload->>'new_theme_id')::uuid;
    IF EXISTS (SELECT 1 FROM im.themes WHERE theme_id = new_id) THEN RETURN 'repeat'; END IF;
    INSERT INTO im.themes (theme_id, name, origin, pinned) VALUES (new_id, v_name, 'mine', true);
    WITH out AS (
      DELETE FROM im.idea_themes it WHERE it.theme_id = t.theme_id
        AND it.idea_id::text IN (SELECT jsonb_array_elements_text(e.payload->'idea_ids'))
      RETURNING it.idea_id)
    INSERT INTO im.idea_themes (idea_id, theme_id, origin) SELECT idea_id, new_id, 'mine' FROM out;
    GET DIAGNOSTICS moved = ROW_COUNT;
    RETURN 'split: ' || moved || ' moved';
  ELSIF e.kind = 'file_idea' THEN
    IF NOT EXISTS (SELECT 1 FROM im.ideas WHERE idea_id = e.idea_id) THEN RETURN 'no such idea'; END IF;
    DELETE FROM im.idea_theme_blocks WHERE idea_id = e.idea_id AND theme_id = t.theme_id;
    INSERT INTO im.idea_themes (idea_id, theme_id, origin) VALUES (e.idea_id, t.theme_id, 'mine')
    ON CONFLICT (idea_id, theme_id) DO UPDATE SET origin = 'mine';
    RETURN 'filed';
  ELSIF e.kind = 'unfile_idea' THEN
    IF NOT EXISTS (SELECT 1 FROM im.ideas WHERE idea_id = e.idea_id) THEN RETURN 'no such idea'; END IF;
    DELETE FROM im.idea_themes WHERE idea_id = e.idea_id AND theme_id = t.theme_id;
    INSERT INTO im.idea_theme_blocks (idea_id, theme_id) VALUES (e.idea_id, t.theme_id) ON CONFLICT DO NOTHING;
    RETURN 'unfiled';
  END IF;
  RETURN 'not a theme event';
END $$;

-- AFTER INSERT: the event's CHECKs have passed and its row exists for im.feedback_applied. Runs as the pipeline
-- (it owns im); Lattice can still only append events.
CREATE FUNCTION im.theme_event_trigger() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, im, pub
AS $$
BEGIN
  INSERT INTO im.feedback_applied (event_id, result) VALUES (NEW.event_id, im.apply_theme_event(NEW));
  RETURN NULL;
END $$;

CREATE TRIGGER feedback_events_theme_edits AFTER INSERT ON pub.feedback_events
  FOR EACH ROW WHEN (NEW.kind IN ('pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme', 'merge_theme',
                                  'split_theme', 'file_idea', 'unfile_idea'))
  EXECUTE FUNCTION im.theme_event_trigger();
