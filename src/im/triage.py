"""Triage: answer the triage questions for every current episode (ROADMAP Phase 1).

Backends sit behind one interface (`Backend`), so swapping one for another
is a config change. Answers are stored in one shape for
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
KINDS = {"idea": "An idea or possibility worth remembering, whoever had it.",
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
        "project": {"type": "choice",
                    "instructions": "Which of my projects is this transcript about, or where is it happening?",
                    "criteria": registry | {"none": "None of these projects."}},
        "keep_score": {"type": "score",
                       "instructions": "How much is this transcript worth keeping for later? If I say \"note to self\", "
                                       "or the context says I tapped the pendant, I wanted what I said there kept.",
                       "criteria": KEEP_LEVELS},
    }


def prompt_version(qs: dict) -> str:
    return hashlib.sha256(json.dumps(qs, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class EpisodeState:
    episode_id: object
    transcript: str
    context: str = ""   # when, how long, who spoke: facts a transcript alone doesn't carry
    taps: tuple = ()    # pendant taps during the episode (also in `context`)

    @property
    def input_hash(self) -> str:
        return hashlib.sha256(f"{self.context}\n\n{self.transcript}".encode()).hexdigest()

    def as_state(self) -> dict:
        return {"context": self.context, "transcript": self.transcript}


def render(conn, episode_ids) -> list[EpisodeState]:
    """Each episode as a transcript (`speaker: text` per line) plus a one-line context:
    local date and time of day, duration, and the speakers present."""
    rows = conn.execute(
        """SELECT es.episode_id, s.speaker_label, s.text, s.started_at, s.ended_at
           FROM im.episode_segments es JOIN im.source_segments s USING (segment_id)
           WHERE es.episode_id = ANY(%s) ORDER BY es.episode_id, es.ord""", (list(episode_ids),)).fetchall()
    # A tap keeps what I said from 30 s before it to 30 s after (Hearsay's README).
    taps = {r[0]: r[1] for r in conn.execute(
        """SELECT e.episode_id, array_agg(t ORDER BY t)
           FROM im.episodes e JOIN im.source_conversations c ON c.conversation_id = e.session_id,
                unnest(c.taps) AS t
           WHERE e.episode_id = ANY(%s)
             AND t BETWEEN e.started_at - interval '30 seconds' AND e.ended_at + interval '30 seconds'
           GROUP BY e.episode_id""", (list(episode_ids),))}
    lines: dict = {}
    spans: dict = {}
    speakers: dict = {}
    for eid, speaker, text, start, end in rows:
        lines.setdefault(eid, []).append(f"{speaker}: {text}")
        lo, hi = spans.get(eid, (start, end))
        spans[eid] = (min(lo, start), max(hi, end))
        speakers.setdefault(eid, [])
        if speaker not in speakers[eid]:
            speakers[eid].append(speaker)
    out = []
    for eid in episode_ids:
        if eid not in lines:
            continue
        start, end = (t.astimezone() for t in spans[eid])  # the machine's local time zone
        minutes = max(1, round((end - start).total_seconds() / 60))
        context = (f"{start:%A %Y-%m-%d}, {start:%H:%M}-{end:%H:%M} ({_part_of_day(start.hour)}), "
                   f"{minutes} min. Speakers: {', '.join(speakers[eid])}.")
        if eid in taps:
            context += (" I tapped the pendant at " + ", ".join(f"{t.astimezone():%H:%M:%S}" for t in taps[eid])
                        + " (a tap means: keep what I said around then).")
        out.append(EpisodeState(eid, "\n".join(lines[eid]), context, tuple(taps.get(eid, ()))))
    return out


def _part_of_day(hour: int) -> str:
    return ("night" if hour < 5 else "morning" if hour < 12 else "afternoon" if hour < 17
            else "evening" if hour < 22 else "night")


@dataclass(frozen=True)
class Prediction:
    answers: dict
    state_tokens: int | None = None
    truncated: bool = False


class Backend(Protocol):
    name: str           # llm | laya | logreg
    model: str
    model_version: str

    def predict(self, states: list[EpisodeState], qs: dict) -> list[Prediction]: ...


def _pending(conn, backend: Backend, pv: str, limit: int | None, only=None) -> list[EpisodeState]:
    """Current episodes, newest first, that this backend/model/prompt hasn't answered for their current
    rendering. A changed rendering (a tap, a renamed speaker) gets answered again."""
    ids = [r[0] for r in conn.execute(
        """SELECT episode_id FROM im.episodes WHERE current AND (%s::uuid[] IS NULL OR episode_id = ANY(%s))
           ORDER BY started_at DESC""", (only, only))]
    done = {r[0]: r[1] for r in conn.execute(
        """SELECT episode_id, input_hash FROM im.triage
           WHERE backend = %s AND model_version = %s AND prompt_version = %s AND episode_id = ANY(%s)""",
        (backend.name, backend.model_version, pv, ids))}
    todo = [st for st in render(conn, ids) if done.get(st.episode_id) != st.input_hash]
    return todo[:limit] if limit else todo


def run_stage(conn, backend: Backend, limit: int | None = None, batch: int = 8, only=None) -> dict:
    """Triage current episodes this backend/model/prompt hasn't answered yet, newest first.
    Each batch commits on its own, so an interrupted run keeps what it finished."""
    qs = questions(conn)
    pv = prompt_version(qs)
    todo = _pending(conn, backend, pv, limit, only)
    done, seconds = 0, 0.0
    for i in range(0, len(todo), batch):
        states = todo[i:i + batch]
        t = time.monotonic()
        preds = backend.predict(states, qs)
        per_ms = int((time.monotonic() - t) * 1000 / max(len(states), 1))
        seconds += time.monotonic() - t
        with conn.transaction():
            for st, p in zip(states, preds, strict=True):
                conn.execute(
                    """INSERT INTO im.triage (episode_id, backend, model, model_version, prompt_version, answers,
                         state_tokens, truncated, latency_ms, schema_version, stage_version, input_hash)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (episode_id, backend, model_version, prompt_version) DO UPDATE
                       SET answers = excluded.answers, state_tokens = excluded.state_tokens,
                           truncated = excluded.truncated, latency_ms = excluded.latency_ms,
                           input_hash = excluded.input_hash, created_at = now()""",
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
