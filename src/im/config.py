"""Segmentation config. Defaults here; override with a TOML file named by IM_CONFIG.

Every value that changes episode boundaries goes into stage_version, so a
config change is a version change and re-derives episodes.
"""

import os
import tomllib
from dataclasses import dataclass, fields

HEURISTIC = "heuristic-3"  # 3: episode identity covers how its speakers would be pseudonymized


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
    backends: tuple[str, ...] = ()         # run by `im run`: llm | laya | logreg (logreg once trained)
    llm_model: str = "gpt-oss:20b"         # an Ollama model
    llm_url: str = "http://localhost:11434"
    llm_think: str | None = "low"          # reasoning effort for models that think; None to leave it off
    llm_num_ctx: int = 16384

    laya_checkpoint: str = "english"     # english | multilingual | typed-decisions
    laya_max_len: int = 4096             # tokens of state; longer episodes are truncated (and say so).
                                         # Memory grows steeply with length on CPU: ~5 GB at 4k, ~8 GB at 7k.
    threads: int | None = None           # torch CPU threads; None = torch's default
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    max_episodes_per_run: int | None = None   # bound a run's time; the rest wait for the next run


@dataclass(frozen=True)
class ExtractConfig:
    enabled: bool = True
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    prompt: str = "v1"                   # extract.LIVE; a new prompt re-reads every episode, so trial it first
    interests: str = ""                  # my interests, for prompts with a slot for them (v2); keep it out of the repo
    monthly_cap_usd: float = 20.0        # every paid request stops once the month's egress spend reaches this
    max_episodes_per_run: int | None = None


@dataclass(frozen=True)
class ModelConfig:
    """The model a stage asks, and its effort: [match], [themes], [scores], [sensitivity], [vocab], [digest]. Both go
    into the stage's version: a change re-asks what that stage keys on its version (every score, tag and vocab check;
    every idea that fits no theme); the matcher and the digest use it from then on."""
    model: str = "claude-opus-5-5"
    effort: str = "low"


@dataclass(frozen=True)
class LatticeConfig:
    enabled: bool = True
    embedding_model: str = "BAAI/bge-small-en-v1.5"   # local, CPU
    max_items_per_run: int | None = None


@dataclass(frozen=True)
class WeightConfig:
    """weight-1 (docs/design/m2-observations-candidates-ideas.md). Any change is a new formula version: its weights
    are written beside the old ones. Preview a change with `im tune`."""
    recurrence_minutes: float = 30.0   # two of my observations in one conversation this far apart count twice
    others: float = 0.25               # per observation by diarization or not me...
    others_cap: int = 4                # ...counting at most this many, so a crowd can't outweigh me
    canonical_link: float = 0.5        # per link to a canonical idea
    canonical: float = 2.0             # when the cluster is itself a canonical idea
    proposals_per_week: int = 10
    repropose_margin: float = 1.0      # a dismissed candidate comes back once its weight has grown this much

    @property
    def formula_version(self) -> str:
        params = ";".join(f"{f.name}={getattr(self, f.name):g}" for f in fields(self)
                          if f.name not in ("proposals_per_week", "repropose_margin"))
        return f"weight-1;{params}"


@dataclass(frozen=True)
class EgressConfig:
    my_names: tuple[str, ...] = ()       # what people call me: pseudonymized under policy C, and the model told it's me
    names: tuple[str, ...] = ()          # other names to pseudonymize under policy C, beyond Hearsay's and GLiNER's


@dataclass(frozen=True)
class Config:
    segment: SegmentConfig = SegmentConfig()
    triage: TriageConfig = TriageConfig()
    extract: ExtractConfig = ExtractConfig()
    lattice: LatticeConfig = LatticeConfig()
    match: ModelConfig = ModelConfig()          # the lattice's matcher
    themes: ModelConfig = ModelConfig()
    scores: ModelConfig = ModelConfig()
    sensitivity: ModelConfig = ModelConfig()
    vocab: ModelConfig = ModelConfig()
    digest: ModelConfig = ModelConfig()
    weight: WeightConfig = WeightConfig()
    egress: EgressConfig = EgressConfig()


def load() -> Config:
    path = os.environ.get("IM_CONFIG")
    if not path:
        return Config()
    with open(path, "rb") as f:
        data = tomllib.load(f)
    triage = data.get("triage", {})
    if "backends" in triage:
        triage["backends"] = tuple(triage["backends"])
    egress = {k: tuple(v) for k, v in data.get("egress", {}).items()}
    models = {k: ModelConfig(**data.get(k, {}))
              for k in ("match", "themes", "scores", "sensitivity", "vocab", "digest")}
    return Config(segment=SegmentConfig(**data.get("segment", {})), triage=TriageConfig(**triage),
                  extract=ExtractConfig(**data.get("extract", {})), lattice=LatticeConfig(**data.get("lattice", {})),
                  weight=WeightConfig(**data.get("weight", {})), egress=EgressConfig(**egress), **models)
