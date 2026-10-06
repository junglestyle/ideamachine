-- Themes (ROADMAP Phase 2, slice 4): file ideas into my themes, propose new ones, apply my theme feedback.

-- Theme calls send several ideas at once, so they belong to no single episode.
ALTER TABLE im.egress_log ALTER COLUMN episode_id DROP NOT NULL;

-- Which ideas the filer has looked at, against which set of themes. `none`: it fit no theme then; it's looked
-- at again when the themes change. `proposal_seen`: a proposal pass has already considered it.
CREATE TABLE im.theme_checks (
  idea_id         uuid PRIMARY KEY REFERENCES im.ideas(idea_id) ON DELETE CASCADE,
  themes_version  text NOT NULL,
  result          text NOT NULL CHECK (result IN ('assigned', 'none')),
  proposal_seen   boolean NOT NULL DEFAULT false,
  checked_at      timestamptz NOT NULL DEFAULT now()
);

-- Names I've rejected, so they aren't proposed again.
CREATE TABLE im.theme_rejections (
  name         text PRIMARY KEY,
  rejected_at  timestamptz NOT NULL DEFAULT now()
);

-- Feedback events Idea Machine has acted on.
CREATE TABLE im.feedback_applied (
  event_id    bigint PRIMARY KEY REFERENCES pub.feedback_events(event_id) ON DELETE CASCADE,
  result      text NOT NULL,
  applied_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_kind_check;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_kind_check CHECK (kind IN (
  'star', 'unstar', 'keep', 'discard', 'link', 'unlink', 'pin_theme', 'unpin_theme', 'rename_theme', 'reject_theme'));

-- A theme event outlives its theme (a rejection deletes the theme but is history), so it keeps the event and
-- loses the reference. Idea references still cascade: those carry forgotten speech.
ALTER TABLE pub.feedback_events DROP CONSTRAINT feedback_events_theme_id_fkey;
ALTER TABLE pub.feedback_events ADD CONSTRAINT feedback_events_theme_id_fkey
  FOREIGN KEY (theme_id) REFERENCES im.themes(theme_id) ON DELETE SET NULL;

-- Lattice colors themes in a stable order (pinned first, then oldest first), so it needs when each was made.
CREATE OR REPLACE VIEW pub.themes AS
SELECT t.theme_id, t.name, t.description, t.origin, t.pinned,
       (SELECT count(*) FROM im.idea_themes it WHERE it.theme_id = t.theme_id) AS n_ideas,
       t.created_at
FROM im.themes t;
