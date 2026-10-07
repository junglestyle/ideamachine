"""Import Hearsay's utterance stream into im.source_* (ROADMAP §3.3).

Reads the stream directory and never writes to it. Conversations whose
`revision` changed are re-read whole. Each file is checked against its
revision, and one that doesn't match (Hearsay was mid-rewrite) is left for
the next run.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

FORMAT_VERSIONS = {1}
STAGE_VERSION = "import/stream-v2"  # v2: affect isn't part of a segment's identity
SCHEMA_VERSION = 2
SEGMENT_NS = uuid.UUID("0b8f4f5e-6a43-4d1f-9a3c-2f7e9d1c5b60")
# Annotations Hearsay can rescore without the speech changing: kept on the segment, not part of its identity.
NOT_IDENTITY = {"utterance_id", "affect"}


class StreamError(Exception):
    """The stream can't be imported as it is. The run fails rather than skipping data."""


@dataclass(frozen=True)
class Utterance:
    segment_id: uuid.UUID
    conversation_id: str
    utterance_id: str
    started_at: datetime
    ended_at: datetime
    text: str
    speaker_kind: str
    speaker_name: str | None
    speaker_label: str
    speaker_basis: str | None
    is_self: bool | None
    speaker_conf: float | None
    asr_confidence: float | None
    arousal: float | None
    input_hash: str


def _time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_utterance(line: str, conversation_id: str) -> Utterance:
    rec = json.loads(line)
    if rec["conversation_id"] != conversation_id:
        raise StreamError(f"{rec['utterance_id']} is in the file for {conversation_id}")
    content = {k: v for k, v in rec.items() if k not in NOT_IDENTITY}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    speaker, conf = rec["speaker"], rec["speaker_confidence"]
    arousal = (rec.get("affect") or {}).get("arousal")
    kind = speaker["kind"]
    if kind not in ("owner", "person", "anonymous", "stranger", "unknown"):
        raise StreamError(f"{rec['utterance_id']}: unknown speaker kind {kind!r}")
    return Utterance(
        segment_id=uuid.uuid5(SEGMENT_NS, f"{conversation_id}|{digest}"),
        conversation_id=conversation_id,
        utterance_id=rec["utterance_id"],
        started_at=_time(rec["start"]),
        ended_at=_time(rec["end"]),
        text=rec["text"],
        speaker_kind=kind,
        speaker_name=speaker["name"],
        speaker_label="me" if kind == "owner" else speaker["name"] or speaker["label"] or kind,
        speaker_basis=conf["basis"],
        # Hearsay already thresholds owner for precision; unknown fails closed.
        is_self=True if kind == "owner" else None if kind == "unknown" else False,
        speaker_conf=conf["owner_similarity"],
        asr_confidence=rec["text_confidence"],
        arousal=None if arousal is None else float(arousal),
        input_hash=digest,
    )


def read_index(stream_dir: Path) -> dict:
    try:
        index = json.loads((stream_dir / "index.json").read_text())
    except FileNotFoundError:
        raise StreamError(f"no index.json in {stream_dir}") from None
    version = index.get("format_version")
    if version not in FORMAT_VERSIONS:
        raise StreamError(f"stream format_version {version!r} isn't one this importer knows ({FORMAT_VERSIONS})")
    return index


def read_conversation(stream_dir: Path, entry: dict) -> list[Utterance] | None:
    """The conversation's utterances, or None if its file doesn't match the index revision."""
    try:
        data = (stream_dir / entry["file"]).read_bytes()
    except FileNotFoundError:
        return None
    if hashlib.sha256(data).hexdigest()[:16] != entry["revision"]:
        return None
    try:
        return [parse_utterance(line, entry["conversation_id"]) for line in data.decode().splitlines() if line]
    except (KeyError, ValueError, TypeError) as e:
        raise StreamError(f"{entry['file']}: {e!r}") from e


def read_forgotten(stream_dir: Path) -> list[dict]:
    try:
        entries = json.loads((stream_dir / "forgotten.json").read_text())
    except FileNotFoundError:
        # format_version 1 always has it. Without the deletion signal, don't import at all.
        raise StreamError(f"no forgotten.json in {stream_dir}") from None
    for e in entries:
        if not (e.get("start") and e.get("end") and e.get("forgotten_at")):
            raise StreamError(f"forgotten entry without start/end/forgotten_at: {e!r}")
    return entries


def _members(conn, conversation_id: str) -> dict:
    rows = conn.execute(
        """SELECT m.segment_id, m.utterance_id, s.started_at, s.ended_at
           FROM im.source_members m JOIN im.source_segments s USING (segment_id)
           WHERE m.conversation_id = %s""", (conversation_id,)).fetchall()
    return {r[0]: r[1:] for r in rows}


def _insert_segments(conn, utts: list[Utterance]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO im.source_segments (segment_id, conversation_id, started_at, ended_at, text,
                 speaker_kind, speaker_name, speaker_label, speaker_basis, is_self, speaker_conf,
                 asr_confidence, arousal, schema_version, stage_version, input_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (segment_id) DO NOTHING""",
            [(u.segment_id, u.conversation_id, u.started_at, u.ended_at, u.text, u.speaker_kind,
              u.speaker_name, u.speaker_label, u.speaker_basis, u.is_self, u.speaker_conf,
              u.asr_confidence, u.arousal, SCHEMA_VERSION, STAGE_VERSION, u.input_hash) for u in utts])


def _link(conn, pairs: list[tuple], method: str) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO im.source_supersessions (old_segment_id, new_segment_id, method)
               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
            [(old, new, method) for old, new in pairs])


def _replace_conversation(conn, cid: str, entry: dict | None, utts: list[Utterance], stored) -> tuple:
    """Make `utts` the conversation's members. Returns (arrived, unmatched gone) for overlap linking."""
    by_id = {}
    for u in utts:
        by_id.setdefault(u.segment_id, u)  # identical content twice collapses to one segment
    old = _members(conn, cid)
    arrived = [u for sid, u in by_id.items() if sid not in old]
    gone = {sid: v for sid, v in old.items() if sid not in by_id}

    _insert_segments(conn, arrived)
    # Same transcript, same utterance_id, different content (e.g. a speaker was named).
    same_transcript = entry is not None and stored is not None and stored[1] == entry["transcript_revision"]
    arrived_by_uid = {u.utterance_id: u for u in arrived}
    unmatched = []
    id_links = []
    for sid, (uid, start, end) in gone.items():
        if same_transcript and uid in arrived_by_uid:
            id_links.append((sid, arrived_by_uid[uid].segment_id))
        else:
            unmatched.append((sid, start, end))
    _link(conn, id_links, "utterance_id")

    if gone:
        conn.execute("DELETE FROM im.source_members WHERE conversation_id = %s AND segment_id = ANY(%s)",
                     (cid, list(gone)))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO im.source_members (conversation_id, segment_id, utterance_id) VALUES (%s, %s, %s)",
            [(cid, u.segment_id, u.utterance_id) for u in arrived])
        # Renumbered but unchanged utterances keep their segment and get their new utterance_id.
        cur.executemany(
            """UPDATE im.source_members SET utterance_id = %s
               WHERE conversation_id = %s AND segment_id = %s AND utterance_id <> %s""",
            [(u.utterance_id, cid, sid, u.utterance_id) for sid, u in by_id.items() if sid in old])
        # Affect can be (re)scored without the speech changing, so it follows the latest import.
        cur.executemany(
            "UPDATE im.source_segments SET arousal = %s WHERE segment_id = %s AND arousal IS DISTINCT FROM %s",
            [(u.arousal, sid, u.arousal) for sid, u in by_id.items()])

    if entry is None:
        conn.execute("DELETE FROM im.source_conversations WHERE conversation_id = %s", (cid,))
    else:
        conn.execute(
            """INSERT INTO im.source_conversations (conversation_id, revision, transcript_revision)
               VALUES (%s, %s, %s)
               ON CONFLICT (conversation_id) DO UPDATE SET revision = excluded.revision,
                 transcript_revision = excluded.transcript_revision, imported_at = now()""",
            (cid, entry["revision"], entry["transcript_revision"]))
    return arrived, unmatched


def _places(entry: dict) -> str:
    """The index entry's places as JSON, checked: [{"name", "start", "end"}] in order."""
    places = entry.get("places", [])
    for p in places:
        try:
            ok = isinstance(p["name"], str) and _time(p["start"]) <= _time(p["end"])
        except (KeyError, ValueError, TypeError, AttributeError):
            ok = False
        if not ok:
            raise StreamError(f"{entry['conversation_id']}: bad place {p!r}")
    return json.dumps(places, ensure_ascii=False)


def _apply_forgotten(conn, entries: list[dict]) -> set[str]:
    """Purge every segment version whose time overlaps a forgotten span. Returns affected conversations.

    Matching is by time only: utterance_ids are renumbered across transcript
    revisions, so an old id can name different speech in another version.
    """
    affected = set()
    for e in entries:
        rows = conn.execute(
            """SELECT segment_id, conversation_id FROM im.source_segments
               WHERE started_at < %s AND ended_at > %s""", (_time(e["end"]), _time(e["start"]))).fetchall()
        if not rows:
            continue
        ids = [r[0] for r in rows]
        affected |= {r[1] for r in rows}
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO im.source_tombstones (segment_id, forgotten_at, reason) VALUES (%s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                [(i, _time(e["forgotten_at"]), e.get("reason")) for i in ids])
        conn.execute("DELETE FROM im.source_supersessions WHERE old_segment_id = ANY(%s) OR new_segment_id = ANY(%s)",
                     (ids, ids))
        conn.execute("DELETE FROM im.source_members WHERE segment_id = ANY(%s)", (ids,))
        conn.execute("DELETE FROM im.source_segments WHERE segment_id = ANY(%s)", (ids,))
    return affected


def import_stream(conn, stream_dir: Path) -> tuple[set[str], dict]:
    """Bring im.source_* up to date with the stream. Returns the conversations whose members changed."""
    stream_dir = Path(stream_dir)
    index = read_index(stream_dir)
    forgotten = read_forgotten(stream_dir)
    entries = {e["conversation_id"]: e for e in index["conversations"]}
    stored = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT conversation_id, revision, transcript_revision FROM im.source_conversations")}

    changed, arrived_all, unmatched_all, raced = set(), [], [], 0
    for cid, entry in entries.items():
        if cid in stored and stored[cid][0] == entry["revision"]:
            continue
        utts = [] if entry["file"] is None else read_conversation(stream_dir, entry)
        if utts is None:
            raced += 1
            continue
        arrived, unmatched = _replace_conversation(conn, cid, entry, utts, stored.get(cid))
        if arrived or unmatched:
            changed.add(cid)
        arrived_all += arrived
        unmatched_all += unmatched
    for cid in stored.keys() - entries.keys():
        arrived, unmatched = _replace_conversation(conn, cid, None, [], stored[cid])
        changed.add(cid)
        unmatched_all += unmatched

    # Re-transcribed, split, merged or vanished: link to whatever new speech overlaps in time.
    _link(conn, [(old, u.segment_id) for old, start, end in unmatched_all for u in arrived_all
                 if u.started_at < end and start < u.ended_at], "time_overlap")

    # Taps and places live in the index, not the conversation file, so they can change without a new revision.
    for cid, entry in entries.items():
        taps, places = [_time(t) for t in entry.get("taps", [])], _places(entry)
        conn.execute("""UPDATE im.source_conversations SET taps = %s::timestamptz[], places = %s::jsonb
                        WHERE conversation_id = %s
                          AND (taps, places) IS DISTINCT FROM (%s::timestamptz[], %s::jsonb)""",
                     (taps, places, cid, taps, places))

    changed |= _apply_forgotten(conn, forgotten)
    return changed, {"conversations_changed": len(changed), "segments_new": len(arrived_all),
                     "segments_left": len(unmatched_all), "raced": raced}
