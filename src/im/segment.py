"""Heuristic episode segmentation within one session_id.

1. Split wherever the silence between consecutive segments exceeds gap_s (G).
2. Inside each gap block, a run of my own speech lasting at least
   min_monologue_s becomes its own episode, cut off from the other speakers
   on either side where there is a pause of at least mode_gap_s.

Both rules only look inside a gap block, so a correction can only move
boundaries in the block it lands in.
Speakers fail closed: is_self counts only when true and speaker_conf >= self_conf_min.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from im.config import SegmentConfig

EPISODE_NS = uuid.UUID("6f1c2b0e-9d7a-4c55-8e0e-3b1f5c8a2d41")


@dataclass(frozen=True)
class Seg:
    segment_id: uuid.UUID
    session_id: str
    source: str
    started_at: datetime
    ended_at: datetime
    speaker_label: str
    is_self: bool | None
    speaker_conf: float | None


@dataclass(frozen=True)
class Episode:
    episode_id: uuid.UUID
    input_hash: str
    segments: tuple[Seg, ...]
    self_segments: int

    @property
    def kind(self) -> str:
        if self.self_segments == len(self.segments):
            return "monologue"
        return "others_only" if self.self_segments == 0 else "conversation"


def input_hash(segment_ids) -> str:
    return hashlib.sha256("\n".join(sorted(str(s) for s in segment_ids)).encode()).hexdigest()


def is_me(s: Seg, cfg: SegmentConfig) -> bool:
    return s.is_self is True and s.speaker_conf is not None and s.speaker_conf >= cfg.self_conf_min


def _gap(a_end: datetime, b: Seg) -> float:
    return (b.started_at - a_end).total_seconds()


def _gap_blocks(segs: list[Seg], cfg: SegmentConfig) -> list[list[Seg]]:
    blocks: list[list[Seg]] = []
    end = None
    for s in segs:
        if end is None or _gap(end, s) > cfg.gap_s:
            blocks.append([])
            end = s.ended_at
        blocks[-1].append(s)
        end = max(end, s.ended_at)
    return blocks


def _mode_cuts(block: list[Seg], cfg: SegmentConfig) -> list[list[Seg]]:
    # Runs of consecutive same-class (me / not me) segments.
    runs: list[list[Seg]] = []
    for s in block:
        if runs and is_me(runs[-1][-1], cfg) == is_me(s, cfg):
            runs[-1].append(s)
        else:
            runs.append([s])
    cut_before = set()  # indices into runs
    for i, run in enumerate(runs):
        if not is_me(run[0], cfg):
            continue
        if (max(s.ended_at for s in run) - run[0].started_at).total_seconds() < cfg.min_monologue_s:
            continue
        if i > 0 and _gap(max(s.ended_at for s in runs[i - 1]), run[0]) >= cfg.mode_gap_s:
            cut_before.add(i)
        if i + 1 < len(runs) and _gap(max(s.ended_at for s in run), runs[i + 1][0]) >= cfg.mode_gap_s:
            cut_before.add(i + 1)
    parts: list[list[Seg]] = []
    for i, run in enumerate(runs):
        if i == 0 or i in cut_before:
            parts.append([])
        parts[-1].extend(run)
    return parts


def segment(segs: list[Seg], cfg: SegmentConfig) -> list[Episode]:
    """Group one session's current segments into episodes, in time order."""
    ordered = sorted(segs, key=lambda s: (s.started_at, s.ended_at, str(s.segment_id)))
    episodes = []
    for block in _gap_blocks(ordered, cfg):
        for part in _mode_cuts(block, cfg):
            h = input_hash(s.segment_id for s in part)
            episodes.append(Episode(
                episode_id=uuid.uuid5(EPISODE_NS, f"{cfg.stage_version}|{h}"),
                input_hash=h,
                segments=tuple(part),
                self_segments=sum(is_me(s, cfg) for s in part),
            ))
    return episodes
