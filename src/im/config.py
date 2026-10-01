"""Segmentation config. Defaults here; override with a TOML file named by IM_CONFIG.

Every value that changes episode boundaries goes into stage_version, so a
config change is a version change and re-derives episodes.
"""

import os
import tomllib
from dataclasses import dataclass, fields

HEURISTIC = "heuristic-1"


@dataclass(frozen=True)
class SegmentConfig:
    gap_s: float = 60.0              # G: silence longer than this always splits
    mode_gap_s: float = 5.0          # a self/other switch splits only across a pause at least this long
    min_monologue_s: float = 30.0    # a run of my own speech this long becomes its own episode
    self_conf_min: float = 0.6       # is_self counts only at or above this speaker_conf (fail closed)

    @property
    def stage_version(self) -> str:
        params = ";".join(f"{f.name}={getattr(self, f.name):g}" for f in fields(self))
        return f"segment/{HEURISTIC};{params}"


@dataclass(frozen=True)
class Config:
    segment: SegmentConfig = SegmentConfig()
    ingest_window_s: float = 600.0   # trailing re-read window for the seq cursor (ROADMAP §3.1)


def load() -> Config:
    path = os.environ.get("IM_CONFIG")
    if not path:
        return Config()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return Config(
        segment=SegmentConfig(**data.get("segment", {})),
        **{k: v for k, v in data.items() if k != "segment"},
    )
