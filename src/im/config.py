"""Segmentation config. Defaults here; override with a TOML file named by IM_CONFIG.

Every value that changes episode boundaries goes into stage_version, so a
config change is a version change and re-derives episodes.
"""

import os
import tomllib
from dataclasses import dataclass, fields

HEURISTIC = "heuristic-2"


@dataclass(frozen=True)
class SegmentConfig:
    gap_s: float = 60.0              # G: silence longer than this always splits
    mode_gap_s: float = 5.0          # a self/other switch splits only across a pause at least this long
    min_monologue_s: float = 30.0    # a run of my own speech this long becomes its own episode

    @property
    def stage_version(self) -> str:
        params = ";".join(f"{f.name}={getattr(self, f.name):g}" for f in fields(self))
        return f"segment/{HEURISTIC};{params}"


@dataclass(frozen=True)
class TriageConfig:
    backends: tuple[str, ...] = ("laya", "fallback")  # run by `im run`; fallback only once trained
    laya_checkpoint: str = "english"     # english | multilingual | typed-decisions
    laya_max_len: int = 8192             # tokens of state; longer episodes are truncated (and say so)
    threads: int | None = None           # torch CPU threads; None = torch's default
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    max_episodes_per_run: int | None = None   # bound a run's time; the rest wait for the next run


@dataclass(frozen=True)
class Config:
    segment: SegmentConfig = SegmentConfig()
    triage: TriageConfig = TriageConfig()


def load() -> Config:
    path = os.environ.get("IM_CONFIG")
    if not path:
        return Config()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    triage = data.get("triage", {})
    if "backends" in triage:
        triage["backends"] = tuple(triage["backends"])
    return Config(segment=SegmentConfig(**data.get("segment", {})), triage=TriageConfig(**triage))
