"""Synthetic Hearsay utterance streams for development and tests.

Mirrors hearsay/stream.py (format_version 1): conversations are lists of
turns as transcribed. Turns filed as _noise (speaker None) count toward
utterance numbering and transcript_revision but are left out of the stream,
as in Hearsay. FixtureStream's methods apply the corrections Hearsay makes
after the fact: naming a speaker, re-transcribing, filing a turn as noise,
splitting or merging conversations, and forgetting.

Writes only to directories it created (marked with MARKER), so it can never
overwrite a real stream.
"""

import copy
import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

FORMAT_VERSION = 1
MARKER = ".im-fixture-stream"
T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Speaker:
    kind: str
    name: str | None = None
    label: str | None = None
    basis: str = "none"
    similarity: float | None = None


ME = Speaker("owner", basis="voice", similarity=0.82)
UNKNOWN = Speaker("unknown")


def person(name: str) -> Speaker:
    return Speaker("person", name=name, basis="named", similarity=0.11)


def anon(label: str) -> Speaker:
    return Speaker("anonymous", label=label, basis="cluster", similarity=0.14)


@dataclass(frozen=True)
class Turn:
    start: float                 # seconds after T0
    end: float
    text: str
    speaker: Speaker | None      # None: filed as _noise, so not in the stream
    diar: str = "SPEAKER_00"
    conf: float = 0.9


@dataclass
class Conversation:
    turns: list[Turn]
    open: bool = False
    taps: list[str] = field(default_factory=list)


def iso(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _me(s, e, text):
    return Turn(s, e, text, ME, "SPEAKER_00")


def _alice(s, e, text):
    return Turn(s, e, text, person("Alice"), "SPEAKER_01")


def _bob(s, e, text):
    return Turn(s, e, text, person("Bob"), "SPEAKER_01")


def base_conversations() -> dict[str, Conversation]:
    return {
        # Multi-party: me, Alice and an anonymous voice; 90 s of silence; a second conversation.
        "c-conv": Conversation([
            _me(0, 6, "Did you see the build broke again?"),
            _alice(7, 12, "Yeah, the cache key changed."),
            Turn(13, 16, "Who's got the coffee order?", anon("anon A"), "SPEAKER_02"),
            _me(17, 25, "We should pin the cache key to the lockfile hash."),
            _alice(26, 30, "I can do that after lunch."),
            _me(31, 34, "Great, thanks."),
            _alice(124, 129, "Back to the release plan?"),
            _me(130, 139, "Let's cut the release Thursday and skip the beta."),
            _alice(140, 144, "Fine by me."),
            _me(145, 147, "Decided then."),
        ]),
        # A monologue, an 8 s pause, a conversation with Bob (and a noise turn), 2 minutes of silence,
        # another monologue.
        "c-mono": Conversation([
            _me(3600, 3612, "Idea: the garden sensors could report soil moisture over LoRa."),
            _me(3613, 3628, "Battery life is the hard part, maybe a solar trickle charger."),
            _me(3629, 3639, "Check what the cheap ESP32 boards draw in deep sleep."),
            _me(3640, 3648, "Write that down for the weekend."),
            _bob(3656, 3660, "Are you talking to yourself again?"),
            _me(3661, 3666, "Thinking out loud about the garden thing."),
            _bob(3667, 3673, "I've got a spare solar panel you can have."),
            Turn(3674, 3677, "Maybe, not sure.", UNKNOWN, "SPEAKER_00"),
            Turn(3678, 3680, "[dishwasher beeps]", None, "SPEAKER_03"),
            _me(3800, 3814, "Another thought: the backup job should verify restores weekly."),
            _me(3815, 3827, "A restore that's never been tested isn't a backup."),
            _me(3828, 3837, "Add a task for that."),
        ]),
        # Nobody identified: a TV in the background.
        "c-tv": Conversation([
            Turn(7200, 7205, "And now the weather.", UNKNOWN),
            Turn(7206, 7212, "Expect rain later in the week.", UNKNOWN),
            Turn(7213, 7217, "Back to you in the studio.", anon("anon A"), "SPEAKER_01"),
        ]),
        # Where re-transcription lands.
        "c-edits": Conversation([
            _me(10800, 10805, "Let's review the budget."),
            _alice(10806, 10816, "The hosting line doubled. I think we should move off the managed database."),
            _me(10817, 10821, "Agreed."),
            _me(10941, 10945, "One more thing about the"),
            _me(10945.5, 10949.5, "on-call rotation for next month."),
            Turn(10950, 10954, "I can take the first week.", anon("anon A"), "SPEAKER_01"),
        ]),
        # Something said that will be forgotten.
        "c-forget": Conversation([
            _me(14400, 14405, "Quick note before the meeting."),
            _me(14406, 14412, "Private detail that must be forgotten."),
            _me(14413, 14418, "Okay, that's it."),
        ]),
    }


class FixtureStream:
    def __init__(self):
        self.conversations = base_conversations()
        self.forgotten: list[dict] = []

    def copy(self) -> "FixtureStream":
        return copy.deepcopy(self)

    # Corrections, as Hearsay makes them.

    def name_speaker(self, cid: str, label: str, name: str) -> None:
        """The operator names an anonymous voice. Only speaker fields change."""
        conv = self.conversations[cid]
        conv.turns = [replace(t, speaker=person(name)) if t.speaker and t.speaker.label == label else t
                      for t in conv.turns]

    def split_turn(self, cid: str, idx: int, at: float, first: Turn, second: Turn) -> None:
        """Re-transcription splits one turn in two; later utterance_ids shift by one."""
        turns = self.conversations[cid].turns
        assert turns[idx].start <= at <= turns[idx].end
        turns[idx:idx + 1] = [first, second]

    def merge_turns(self, cid: str, idx: int, merged: Turn) -> None:
        """Re-transcription merges a turn with the next one."""
        self.conversations[cid].turns[idx:idx + 2] = [merged]

    def file_as_noise(self, cid: str, idx: int) -> Speaker:
        turn = self.conversations[cid].turns[idx]
        self.conversations[cid].turns[idx] = replace(turn, speaker=None)
        return turn.speaker

    def refile(self, cid: str, idx: int, speaker: Speaker) -> None:
        turn = self.conversations[cid].turns[idx]
        self.conversations[cid].turns[idx] = replace(turn, speaker=speaker)

    def split_conversation(self, cid: str, idx: int, new_cid: str) -> None:
        conv = self.conversations[cid]
        self.conversations[new_cid] = Conversation(conv.turns[idx:])
        conv.turns = conv.turns[:idx]

    def merge_conversations(self, cid: str, other: str) -> None:
        self.conversations[cid].turns += self.conversations.pop(other).turns

    def forget(self, start: float, end: float, reason: str = "operator request") -> None:
        hits = [(cid, f"{cid}:{i:04d}") for cid, conv in self.conversations.items()
                for i, t in enumerate(conv.turns) if t.start < end and start < t.end]
        self.forgotten.append({"forgotten_at": iso(20000), "start": iso(start), "end": iso(end),
                               "conversation_id": hits[0][0] if hits else None,
                               "utterance_ids": [u for _, u in hits], "reason": reason})
        # Hearsay applies forgetting on every reprocess, so the speech leaves the stream too.
        for conv in self.conversations.values():
            conv.turns = [t for t in conv.turns if not (t.start < end and start < t.end)]

    # Rendering, in Hearsay's format.

    def utterances(self, cid: str) -> list[dict]:
        out = []
        for i, t in enumerate(self.conversations[cid].turns):
            if t.speaker is None:
                continue
            out.append({
                "conversation_id": cid,
                "utterance_id": f"{cid}:{i:04d}",
                "start": iso(t.start),
                "end": iso(t.end),
                "speaker": {"kind": t.speaker.kind, "name": t.speaker.name, "label": t.speaker.label},
                "text": t.text,
                "text_confidence": t.conf,
                "speaker_confidence": {"basis": t.speaker.basis, "owner_similarity": t.speaker.similarity},
            })
        return out

    def transcript_revision(self, cid: str) -> str:
        rows = [iso(0)] + [[i, t.start, t.end, t.text, t.diar, t.conf]
                           for i, t in enumerate(self.conversations[cid].turns)]
        return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]

    def write(self, stream_dir: Path) -> dict:
        stream_dir = Path(stream_dir)
        _claim(stream_dir)
        (stream_dir / "conversations").mkdir(exist_ok=True)
        index = []
        for cid, conv in sorted(self.conversations.items(), key=lambda kv: kv[1].turns[0].start):
            utts = self.utterances(cid)
            data = "".join(json.dumps(u, ensure_ascii=False) + "\n" for u in utts).encode()
            name = f"conversations/{cid}.jsonl"
            _write_if_changed(stream_dir / name, data)
            index.append({"conversation_id": cid, "start": iso(conv.turns[0].start),
                          "end": iso(conv.turns[-1].end), "open": conv.open, "transcribed": True,
                          "taps": conv.taps, "utterances": len(utts),
                          "revision": hashlib.sha256(data).hexdigest()[:16],
                          "transcript_revision": self.transcript_revision(cid), "file": name})
        _write_if_changed(stream_dir / "forgotten.json", json.dumps(self.forgotten, indent=2).encode())
        _write_if_changed(stream_dir / "index.json",
                          json.dumps({"format_version": FORMAT_VERSION, "conversations": index}, indent=2).encode())
        current = {e["file"] for e in index}
        for path in (stream_dir / "conversations").glob("*.jsonl"):
            if f"conversations/{path.name}" not in current:
                path.unlink()
        return {"dir": str(stream_dir), "conversations": len(index),
                "utterances": sum(e["utterances"] for e in index), "forgotten": len(self.forgotten)}


def _claim(stream_dir: Path) -> None:
    """Refuse any directory that has content and wasn't made by this module."""
    if stream_dir.exists() and any(stream_dir.iterdir()) and not (stream_dir / MARKER).exists():
        raise SystemExit(f"{stream_dir} isn't empty and isn't a fixture stream; refusing to write")
    stream_dir.mkdir(parents=True, exist_ok=True)
    (stream_dir / MARKER).touch()


def _write_if_changed(path: Path, data: bytes) -> None:
    if path.exists() and path.read_bytes() == data:
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def edited() -> FixtureStream:
    """The base stream after one of every correction, plus a forget."""
    s = FixtureStream()
    s.name_speaker("c-conv", "anon A", "Carol")
    s.split_turn("c-edits", 1, 10810,
                 _alice(10806, 10810, "The hosting line doubled."),
                 _me(10810.5, 10816, "I think we should move off the managed database."))
    s.merge_turns("c-edits", 4, _me(10941, 10949.5, "One more thing about the on-call rotation for next month."))
    s.refile("c-mono", 8, UNKNOWN)
    s.split_conversation("c-mono", 9, "c-mono-b")
    s.merge_conversations("c-edits", "c-forget")
    s.forget(14406, 14412)
    return s


SCENARIOS = ("base", "edited")


def write(stream_dir: Path, scenario: str = "base") -> dict:
    return (FixtureStream() if scenario == "base" else edited()).write(stream_dir)
