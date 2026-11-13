"""The attention queue: what I asked to look at again, by tapping the pendant or saying "note to self".

Found in code, never by a model (migration 066). A tap is a time Hearsay lists for a conversation
(`im.source_conversations.taps`); a note is a line of mine that `NOTE_TO_SELF` matches. An item marks lines
and changes nothing about them: extraction, captures and cards go on as if it weren't there, and removing an item
(`im queue remove`) only marks it removed, for good.

Each run (`stage`, in `im run` before anything that sends) makes the items missing and re-anchors the rest to current
lines:

- A tap marks my lines from 30 s before it to 30 s after (Hearsay's window), in every conversation that lists it. It's
  one item for as long as Hearsay lists its time, whatever its conversation is called after a split or a merge.
- A note marks its line and my next lines, up to NOTE_LINES starting within NOTE_SECONDS of it. When a re-transcription
  replaces its line, the new line takes the item over if it descends from the old one (im.source_supersessions) and
  still says "note to self". Otherwise the item stays, showing its lines as first heard, until I remove it.

A tap Hearsay stops listing keeps its item, as last anchored. Removed items are re-anchored like open ones, so a removal
outlives any correction. Items hang off segments, not episodes, so re-segmentation doesn't touch them.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

SCHEMA_VERSION = 1
STAGE_VERSION = "attention/1"
TAP_WINDOW = timedelta(seconds=30)    # what a tap keeps either side of it (Hearsay's README)
NOTE_TO_SELF = re.compile(r"\bnote to (my)?self\b", re.IGNORECASE)   # a note-to-self cue, in one of my lines
NOTE_LINES = 3                        # my lines after a note's own that it marks...
NOTE_SECONDS = timedelta(minutes=2)   # ...if they start within this of it
CONTEXT = timedelta(seconds=30)       # everyone's lines shown either side of what an item marks

# Each tap, the first conversation listing it, and my current lines within its window in any conversation listing it.
_TAPS = """
SELECT t, min(c.conversation_id),
       coalesce(array_agg(s.segment_id ORDER BY s.started_at, s.segment_id)
                FILTER (WHERE s.segment_id IS NOT NULL), '{}')
FROM im.source_conversations c CROSS JOIN LATERAL unnest(c.taps) AS t
LEFT JOIN im.current_segments s ON s.conversation_id = c.conversation_id AND s.is_self
     AND s.started_at <= t + %(window)s AND s.ended_at >= t - %(window)s
GROUP BY t ORDER BY t"""

# Each current line of mine that may say "note to self" (NOTE_TO_SELF decides), with my next lines after it.
_NOTES = """
SELECT c.segment_id, c.conversation_id, c.started_at, c.text,
       ARRAY(SELECT n.segment_id FROM im.current_segments n
             WHERE n.conversation_id = c.conversation_id AND n.is_self
               AND n.started_at > c.started_at AND n.started_at <= c.started_at + %(window)s
             ORDER BY n.started_at, n.segment_id LIMIT %(lines)s)
FROM im.current_segments c
WHERE c.is_self AND c.text ILIKE '%%note to%%'
ORDER BY c.started_at, c.segment_id"""

# The note whose line `seg` descends from, if that line isn't current any more (a note taken over this run has a
# current line again, so it's never taken twice).
_ANCESTOR = """
WITH RECURSIVE a(seg) AS (
  SELECT %s::uuid
  UNION SELECT x.old_segment_id FROM a JOIN im.source_supersessions x ON x.new_segment_id = a.seg)
SELECT i.attention_id FROM im.attention_items i JOIN a ON a.seg = i.cue_segment_id
WHERE i.trigger = 'note_to_self'
  AND NOT EXISTS (SELECT 1 FROM im.current_segments c WHERE c.segment_id = i.cue_segment_id)
ORDER BY i.attention_id LIMIT 1"""


def stage(conn) -> dict:
    """Queue every tap and note to self not queued yet, and re-anchor the queued ones to current lines."""
    with conn.transaction():
        # (trigger, key, at, conversation, cue, lines): a tap is keyed by its time, a note by its line.
        found = [("tap", t, t, cid, None, segs) for t, cid, segs in conn.execute(_TAPS, {"window": TAP_WINDOW})]
        found += [("note_to_self", seg, at, cid, seg, [seg, *after]) for seg, cid, at, text, after in
                  conn.execute(_NOTES, {"window": NOTE_SECONDS, "lines": NOTE_LINES}) if NOTE_TO_SELF.search(text)]
        existing = {(trigger, at if trigger == "tap" else cue): (aid, (at, cid, cue, segs, sv))
                    for aid, trigger, at, cid, cue, segs, sv in conn.execute(
                        """SELECT attention_id, trigger, at, conversation_id, cue_segment_id, segment_ids, stage_version
                           FROM im.attention_items""")}
        stats = {"new": 0, "reanchored": 0}
        for trigger, key, at, cid, cue, segs in sorted(found, key=lambda f: (f[2], f[0], str(f[1]))):
            aid, have = existing.get((trigger, key), (None, None))
            if aid is None and trigger == "note_to_self":
                row = conn.execute(_ANCESTOR, (cue,)).fetchone()
                aid = row[0] if row else None
            if aid is None:
                conn.execute(
                    """INSERT INTO im.attention_items (trigger, at, conversation_id, cue_segment_id, segment_ids,
                         schema_version, stage_version)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (trigger, at, cid, cue, segs, SCHEMA_VERSION, STAGE_VERSION))
                stats["new"] += 1
            elif have != (want := (at, cid, cue, segs, STAGE_VERSION)):
                conn.execute(
                    """UPDATE im.attention_items SET at = %s, conversation_id = %s, cue_segment_id = %s,
                         segment_ids = %s, stage_version = %s
                       WHERE attention_id = %s""", (*want, aid))
                stats["reanchored"] += 1
        stats["open"] = conn.execute("SELECT count(*) FROM im.attention_items WHERE removed_at IS NULL").fetchone()[0]
    return stats


@dataclass(frozen=True)
class Line:
    speaker: str
    text: str
    said_at: datetime
    marked: bool   # one of the lines the item marks, not context


@dataclass(frozen=True)
class Item:
    attention_id: int
    trigger: str
    at: datetime
    conversation_id: str
    lines: list[Line]


def open_items(conn) -> list[Item]:
    """Every item not removed, newest first, each with the lines it marks and everyone's lines CONTEXT either side
    (for a tap, either side of its window too, so a tap where I said nothing still shows what was said)."""
    out = []
    for aid, trigger, at, cid, segs in conn.execute(
            """SELECT attention_id, trigger, at, conversation_id, segment_ids FROM im.attention_items
               WHERE removed_at IS NULL ORDER BY at DESC, attention_id DESC""").fetchall():
        window = (at - TAP_WINDOW, at + TAP_WINDOW) if trigger == "tap" else (None, None)
        lines = conn.execute(
            """WITH marked AS (SELECT segment_id, speaker_label, text, started_at, ended_at FROM im.source_segments
                               WHERE segment_id = ANY(%(segs)s)),
                    span AS (SELECT least(min(started_at), %(lo)s::timestamptz) - %(context)s AS lo,
                                    greatest(max(ended_at), %(hi)s::timestamptz) + %(context)s AS hi FROM marked)
               SELECT speaker_label, text, started_at, segment_id = ANY(%(segs)s) FROM (
                 SELECT * FROM marked
                 UNION SELECT c.segment_id, c.speaker_label, c.text, c.started_at, c.ended_at
                 FROM im.current_segments c, span
                 WHERE c.conversation_id = %(cid)s AND c.started_at <= span.hi AND c.ended_at >= span.lo) s
               ORDER BY started_at, segment_id""",
            {"segs": segs, "lo": window[0], "hi": window[1], "context": CONTEXT, "cid": cid}).fetchall()
        out.append(Item(aid, trigger, at, cid, [Line(*r) for r in lines]))
    return out


def listing(items: list[Item]) -> str:
    """`im queue`: each item's id, what made it and when, then its lines, the marked ones flagged."""
    blocks = []
    for it in items:
        head = f"#{it.attention_id}  {it.trigger.replace('_', ' ')}  {it.at.astimezone():%A %Y-%m-%d %H:%M:%S}"
        body = [f"  {'»' if ln.marked else ' '} {ln.speaker}: {ln.text}" for ln in it.lines] or ["    (no lines yet)"]
        blocks.append("\n".join([head, *body]))
    return "\n\n".join([*blocks, f"{len(items)} in the queue"])


def remove(conn, attention_id: int) -> bool:
    """Mark an open item removed. Nothing else changes. False if there's no such open item."""
    return conn.execute(
        "UPDATE im.attention_items SET removed_at = now() WHERE attention_id = %s AND removed_at IS NULL",
        (attention_id,)).rowcount == 1
