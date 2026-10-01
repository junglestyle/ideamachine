"""Triage: answer the triage questions for every current episode (ROADMAP Phase 1).

Backends sit behind one interface (`Backend`), so swapping Laya for the
fallback classifier is a config change. Answers are stored in one shape for
every backend:

    {question: {"value": ..., "probabilities": {option: p}, "confidence": p_of_value}}

`value` is a label's answer: the option for `kind` and `project`, P(true)
for `is_self_thinking`, and the expected 1-5 score for `keep_score`.
Triage writes labels; it never filters or hides anything (ROADMAP §2).
"""

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Protocol

from im import projects

SCHEMA_VERSION = 1
STAGE_VERSION = "triage/1"
KEEP_LEVELS = ["Nothing worth keeping.", "A minor detail, unlikely to matter.", "Somewhat useful context.",
               "A useful idea, task or decision.", "Important: I would be upset to lose it."]
KINDS = {"idea": "A new idea or possibility worth remembering.",
         "task": "Something someone needs to do.",
         "decision": "A choice that was made or agreed.",
         "chatter": "Small talk or conversation with nothing to keep.",
         "noise": "Background speech, TV, fragments, or transcription garbage."}


def questions(conn) -> dict:
    """The triage questions as sent to a model. The project options come from the registry,
    so changing the registry changes prompt_version and re-triages."""
    registry = {slug: description + (f" (also called {', '.join(aliases)})" if aliases else "")
                for slug, aliases, description in projects.active(conn)}
    return {
        "is_self_thinking": {
            "type": "noul",
            "instructions": "This is a transcript of speech I recorded; 'me' is me. Am I thinking out loud "
                            "or working an idea through, rather than chatting, coordinating, or listening to others?",
            "criteria": {"false": "Small talk, logistics, other people talking, or background speech.",
                         "true": "I am developing a thought, idea, plan or problem."}},
        "kind": {"type": "choice", "instructions": "What is this transcript mostly?", "criteria": KINDS},
        "project": {"type": "choice", "instructions": "Which of my projects is this transcript about?",
                    "criteria": registry | {"none": "None of these projects."}},
        "keep_score": {"type": "score", "instructions": "How much is this transcript worth keeping for later?",
                       "criteria": KEEP_LEVELS},
    }


def prompt_version(qs: dict) -> str:
    return hashlib.sha256(json.dumps(qs, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class EpisodeState:
    episode_id: object
    transcript: str

    @property
    def input_hash(self) -> str:
        return hashlib.sha256(self.transcript.encode()).hexdigest()

    def as_state(self) -> dict:
        return {"transcript": self.transcript}


def render(conn, episode_ids) -> list[EpisodeState]:
    """Each episode as a plain transcript: `speaker: text` per line."""
    rows = conn.execute(
        """SELECT es.episode_id, s.speaker_label, s.text
           FROM im.episode_segments es JOIN im.source_segments s USING (segment_id)
           WHERE es.episode_id = ANY(%s) ORDER BY es.episode_id, es.ord""", (list(episode_ids),)).fetchall()
    lines: dict = {}
    for eid, speaker, text in rows:
        lines.setdefault(eid, []).append(f"{speaker}: {text}")
    return [EpisodeState(eid, "\n".join(lines[eid])) for eid in episode_ids if eid in lines]


@dataclass(frozen=True)
class Prediction:
    answers: dict
    state_tokens: int | None = None
    truncated: bool = False


class Backend(Protocol):
    name: str           # laya | fallback
    model: str
    model_version: str

    def predict(self, states: list[EpisodeState], qs: dict) -> list[Prediction]: ...


def _pending(conn, backend: Backend, pv: str, limit: int | None, only=None) -> list:
    rows = conn.execute(
        """SELECT e.episode_id FROM im.episodes e
           WHERE e.current AND (%s::uuid[] IS NULL OR e.episode_id = ANY(%s)) AND NOT EXISTS (
             SELECT 1 FROM im.triage t WHERE t.episode_id = e.episode_id AND t.backend = %s
               AND t.model_version = %s AND t.prompt_version = %s)
           ORDER BY e.started_at DESC""" + (" LIMIT %s" if limit else ""),
        (only, only, backend.name, backend.model_version, pv) + ((limit,) if limit else ())).fetchall()
    return [r[0] for r in rows]


def run_stage(conn, backend: Backend, limit: int | None = None, batch: int = 8, only=None) -> dict:
    """Triage current episodes this backend/model/prompt hasn't answered yet, newest first.
    Each batch commits on its own, so an interrupted run keeps what it finished."""
    qs = questions(conn)
    pv = prompt_version(qs)
    todo = _pending(conn, backend, pv, limit, only)
    done, seconds = 0, 0.0
    for i in range(0, len(todo), batch):
        states = render(conn, todo[i:i + batch])
        t = time.monotonic()
        preds = backend.predict(states, qs)
        per_ms = int((time.monotonic() - t) * 1000 / max(len(states), 1))
        seconds += time.monotonic() - t
        with conn.transaction():
            for st, p in zip(states, preds, strict=True):
                conn.execute(
                    """INSERT INTO im.triage (episode_id, backend, model, model_version, prompt_version, answers,
                         state_tokens, truncated, latency_ms, schema_version, stage_version, input_hash)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (st.episode_id, backend.name, backend.model, backend.model_version, pv,
                     json.dumps(p.answers), p.state_tokens, p.truncated, per_ms,
                     SCHEMA_VERSION, STAGE_VERSION, st.input_hash))
        done += len(states)
    return {"backend": backend.name, "model_version": backend.model_version, "prompt_version": pv,
            "triaged": done, "seconds": round(seconds, 1)}


def reset(conn) -> int:
    return conn.execute("DELETE FROM im.triage").rowcount


def normalize(qtype: str, value, probabilities: dict | None) -> dict:
    """One answer in the stored shape, from a backend's raw value and option probabilities."""
    probs = {str(k): round(float(v), 4) for k, v in (probabilities or {}).items()}
    if qtype == "noul":
        p = float(value)
        probs = {"true": round(p, 4), "false": round(1 - p, 4)}
        return {"value": round(p, 4), "probabilities": probs, "confidence": round(max(p, 1 - p), 4)}
    if qtype == "score":
        # Stored on the labels' 1-5 scale.
        return {"value": round(float(value) + 1, 4), "probabilities": probs,
                "confidence": round(max(probs.values()), 4) if probs else None}
    return {"value": value, "probabilities": probs, "confidence": probs.get(str(value))}
