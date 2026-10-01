"""Synthetic Hearsay data for development and tests.

Writes into the local hearsay stand-in schema (src/im/sql/hearsay_standin/),
always through an admin connection to a local database (db.assert_local).
This module and test setup are the only code that writes to hearsay.*.
IDs are uuid5s of fixture names, so loading twice changes nothing.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from im import migrate

FIXTURE_NS = uuid.UUID("a7d3e1f0-2b4c-4e8a-9c61-0d5f7e3b9a12")
T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
ALICE = uuid.uuid5(FIXTURE_NS, "person:alice")
BOB = uuid.uuid5(FIXTURE_NS, "person:bob")


def sid(name: str) -> uuid.UUID:
    return uuid.uuid5(FIXTURE_NS, f"segment:{name}")


@dataclass(frozen=True)
class FSeg:
    name: str
    session_id: str
    start: float                    # seconds after T0
    dur: float
    speaker_label: str
    is_self: bool | None
    text: str
    speaker_conf: float | None = 0.9
    speaker_id: uuid.UUID | None = None

    @property
    def segment_id(self) -> uuid.UUID:
        return sid(self.name)


def _me(name, session, start, dur, text, conf=0.9):
    return FSeg(name, session, start, dur, "SPEAKER_00", True, text, conf)


def _alice(name, session, start, dur, text):
    return FSeg(name, session, start, dur, "SPEAKER_01", False, text, 0.8, ALICE)


def _bob(name, session, start, dur, text):
    return FSeg(name, session, start, dur, "SPEAKER_01", False, text, 0.8, BOB)


def _unknown(name, session, start, dur, text, label="SPEAKER_02"):
    return FSeg(name, session, start, dur, label, None, text, None)


# Session times are spread out so sessions never overlap; offsets in seconds.
BASE: list[FSeg] = [
    # Multi-party: me, Alice and an unknown third voice; a 3-minute silence; a second conversation.
    _me("conv-1", "fx-conv", 0, 6, "Did you see the build broke again?"),
    _alice("conv-2", "fx-conv", 7, 5, "Yeah, the cache key changed."),
    _unknown("conv-3", "fx-conv", 13, 3, "Who's got the coffee order?"),
    _me("conv-4", "fx-conv", 17, 8, "We should pin the cache key to the lockfile hash."),
    _alice("conv-5", "fx-conv", 26, 4, "I can do that after lunch."),
    _me("conv-6", "fx-conv", 31, 3, "Great, thanks."),
    _alice("conv-7", "fx-conv", 214, 5, "Back to the release plan?"),
    _me("conv-8", "fx-conv", 220, 9, "Let's cut the release Thursday and skip the beta."),
    _alice("conv-9", "fx-conv", 230, 4, "Fine by me."),
    _me("conv-10", "fx-conv", 235, 2, "Decided then."),

    # Self-monologue, an 8 s pause, a conversation with Bob, 2 minutes of silence, another monologue.
    _me("mono-1", "fx-mono", 3600, 12, "Idea: the garden sensors could report soil moisture over LoRa."),
    _me("mono-2", "fx-mono", 3613, 15, "Battery life is the hard part, maybe a solar trickle charger."),
    _me("mono-3", "fx-mono", 3629, 10, "Check what the cheap ESP32 boards draw in deep sleep."),
    _me("mono-4", "fx-mono", 3640, 8, "Write that down for the weekend."),
    _bob("mono-5", "fx-mono", 3656, 4, "Are you talking to yourself again?"),
    _me("mono-6", "fx-mono", 3661, 5, "Thinking out loud about the garden thing."),
    _bob("mono-7", "fx-mono", 3667, 6, "I've got a spare solar panel you can have."),
    _me("mono-8", "fx-mono", 3674, 3, "Maybe, not sure.", conf=0.3),  # low confidence: fails closed to not-me
    _me("mono-9", "fx-mono", 3800, 14, "Another thought: the backup job should verify restores weekly."),
    _me("mono-10", "fx-mono", 3815, 12, "A restore that's never been tested isn't a backup."),
    _me("mono-11", "fx-mono", 3828, 9, "Add a task for that."),

    # Nobody identified: a TV in the background.
    _unknown("tv-1", "fx-tv", 7200, 5, "And now the weather.", "SPEAKER_00"),
    _unknown("tv-2", "fx-tv", 7206, 6, "Expect rain later in the week.", "SPEAKER_00"),
    _unknown("tv-3", "fx-tv", 7213, 4, "Back to you in the studio.", "SPEAKER_01"),

    # Corrections land here: a split, a merge, a re-attribution (below).
    _me("edit-1", "fx-edits", 10800, 5, "Let's review the budget."),
    _alice("edit-2", "fx-edits", 10806, 10, "The hosting line doubled. I think we should move off the managed database."),
    _me("edit-3", "fx-edits", 10817, 4, "Agreed."),
    _me("edit-4", "fx-edits", 11000, 4, "One more thing about the"),
    _me("edit-5", "fx-edits", 11004.5, 4, "on-call rotation for next month."),
    _unknown("edit-6", "fx-edits", 11009, 4, "I can take the first week."),

    # Something said that has to be forgotten.
    _me("forget-1", "fx-forget", 14400, 5, "Quick note before the meeting."),
    _me("forget-2", "fx-forget", 14406, 6, "Private detail that must be forgotten."),
    _me("forget-3", "fx-forget", 14413, 5, "Okay, that's it."),
]

# (old segment names, replacement segments)
CORRECTIONS: list[tuple[list[str], list[FSeg]]] = [
    # Split: one segment becomes two, each with its own speaker.
    (["edit-2"], [
        _alice("edit-2a", "fx-edits", 10806, 4, "The hosting line doubled."),
        _me("edit-2b", "fx-edits", 10810.5, 5.5, "I think we should move off the managed database."),
    ]),
    # Merge: two halves of one sentence become one segment.
    (["edit-4", "edit-5"], [
        _me("edit-45", "fx-edits", 11000, 8.5, "One more thing about the on-call rotation for next month."),
    ]),
    # Re-attribution: Hearsay learned who the unknown voice was.
    (["edit-6"], [
        FSeg("edit-6r", "fx-edits", 11009, 4, "SPEAKER_01", False, "I can take the first week.", 0.85, ALICE),
    ]),
]

TOMBSTONES = [("forget-2", "operator request")]


def insert_segments(conn: psycopg.Connection, segs: list[FSeg]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO hearsay.segments (segment_id, source, session_id, started_at, ended_at, text,
                 asr_confidence, language, speaker_label, speaker_id, is_self, speaker_conf, audio_ref, producer)
               VALUES (%s, 'fixture', %s, %s, %s, %s, 0.9, 'en', %s, %s, %s, %s, %s, 'im/fixtures')
               ON CONFLICT (segment_id) DO NOTHING""",
            [(s.segment_id, s.session_id, T0 + timedelta(seconds=s.start),
              T0 + timedelta(seconds=s.start + s.dur), s.text, s.speaker_label, s.speaker_id,
              s.is_self, s.speaker_conf,
              Jsonb({"audio_id": s.session_id, "start_ms": int(s.start * 1000),
                                        "end_ms": int((s.start + s.dur) * 1000)}))
             for s in segs])


def supersede(conn: psycopg.Connection, old: list[str], new: list[FSeg]) -> None:
    """Write replacement segments and link each old one to each new one, as Hearsay would."""
    with conn.transaction():
        insert_segments(conn, new)
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO hearsay.segment_supersessions (old_segment_id, new_segment_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                [(sid(o), n.segment_id) for o in old for n in new])


def tombstone(conn: psycopg.Connection, name: str, reason: str) -> None:
    conn.execute("INSERT INTO hearsay.tombstones (segment_id, reason) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                 (sid(name), reason))


def load(conn: psycopg.Connection) -> dict:
    """Create the stand-in schema if needed, then write the base fixture set."""
    applied = migrate.apply(conn, migrate.HEARSAY_STANDIN)
    with conn.transaction():
        insert_segments(conn, BASE)
    for old, new in CORRECTIONS:
        supersede(conn, old, new)
    for name, reason in TOMBSTONES:
        tombstone(conn, name, reason)
    n = conn.execute("SELECT count(*) FROM hearsay.segments").fetchone()[0]
    return {"standin_migrations": applied, "segments": n}
