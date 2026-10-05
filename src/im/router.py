"""Routing: which episodes come to me (`review`) and which are filed without me (`auto_file`).

Router v1 is deliberately cautious (docs/decisions/0003-router-v1.md): no
backend has beaten the base rates yet, so model output only *adds* episodes
to my review queue, and my own signals (a pendant tap, saying "note to self")
always surface an episode, whatever the model says. Routes are labels stored in
im.routes; nothing is dropped.
"""

import re

from im.triage import EpisodeState, prompt_version, questions, render

SCHEMA_VERSION = 1
STAGE_VERSION = "route/1"
FLAG_KINDS = ("idea", "task", "decision")
FLAG_KEEP = 3
ROUTER_VERSION = f"rules-1;kinds={'|'.join(FLAG_KINDS)};keep>={FLAG_KEEP}"
NOTE_TO_SELF = re.compile(r"\bnote to (my)?self\b", re.IGNORECASE)


def route(state: EpisodeState, answers: dict | None) -> tuple[str, list[str]]:
    reasons = []
    if state.taps:
        reasons.append("tap")
    if any(line.startswith("me:") and NOTE_TO_SELF.search(line) for line in state.transcript.splitlines()):
        reasons.append("note_to_self")
    if answers:
        if answers["kind"]["value"] in FLAG_KINDS:
            reasons.append(f"llm:{answers['kind']['value']}")
        if round(answers["keep_score"]["value"]) >= FLAG_KEEP:
            reasons.append(f"llm:keep>={FLAG_KEEP}")
    return ("review" if reasons else "auto_file"), reasons


def run_stage(conn, backend: str = "llm") -> dict:
    """Route every current episode whose triage by `backend` is up to date. Episodes with a tap or a
    note to self are routed even before triage reaches them. Rewrites a route only when it changed."""
    pv = prompt_version(questions(conn))
    ids = [r[0] for r in conn.execute("SELECT episode_id FROM im.episodes WHERE current")]
    triaged = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        """SELECT episode_id, answers, model_version, input_hash FROM im.triage
           WHERE backend = %s AND prompt_version = %s AND episode_id = ANY(%s)""", (backend, pv, ids))}
    counts = {"auto_file": 0, "review": 0, "waiting_for_triage": 0}
    changed = 0
    with conn.transaction():
        for st in render(conn, ids):
            t = triaged.get(st.episode_id)
            answers, mv = (t[0], t[1]) if t and t[2] == st.input_hash else (None, None)
            r, reasons = route(st, answers)
            if answers is None and r == "auto_file":
                counts["waiting_for_triage"] += 1  # no verdict without the model, unless a note forces one
                continue
            counts[r] += 1
            changed += conn.execute(
                """INSERT INTO im.routes (episode_id, router_version, route, reasons, triage_backend,
                     triage_model_version, prompt_version, schema_version, stage_version, input_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (episode_id, router_version) DO UPDATE
                   SET route = excluded.route, reasons = excluded.reasons, triage_backend = excluded.triage_backend,
                       triage_model_version = excluded.triage_model_version, prompt_version = excluded.prompt_version,
                       input_hash = excluded.input_hash, created_at = now()
                   WHERE (im.routes.route, im.routes.reasons, im.routes.triage_model_version, im.routes.prompt_version,
                          im.routes.input_hash)
                     IS DISTINCT FROM (excluded.route, excluded.reasons, excluded.triage_model_version,
                                       excluded.prompt_version, excluded.input_hash)""",
                (st.episode_id, ROUTER_VERSION, r, reasons, backend if answers else None, mv,
                 pv if answers else None, SCHEMA_VERSION, STAGE_VERSION, st.input_hash)).rowcount
    return {"router_version": ROUTER_VERSION, "changed": changed} | counts


def review_queue(conn) -> list:
    """Episodes routed to review that I haven't labeled yet: my own notes first, then newest."""
    from im.label import labeled_episodes

    done = labeled_episodes(conn)
    rows = conn.execute(
        """SELECT r.episode_id, r.reasons FROM im.routes r JOIN im.episodes e USING (episode_id)
           WHERE e.current AND r.router_version = %s AND r.route = 'review'
           ORDER BY ('tap' = ANY(r.reasons) OR 'note_to_self' = ANY(r.reasons)) DESC, e.started_at DESC""",
        (ROUTER_VERSION,)).fetchall()
    return [(eid, reasons) for eid, reasons in rows if eid not in done]
