"""Routing: which episodes come to me (`review`) and which are filed without me (`auto_file`).

Router v2 (docs/decisions/0004-claude-extraction.md): an episode comes to me when Claude captured
something there that I haven't discarded, or when I said "note to self", or tapped the pendant (taps can be
accidental, which is why everything routed can be discarded). Everything else is auto-filed. The local
LLM's flags (router v1, decision 0003) are no longer read: against my labels they were mostly false alarms.
Routes are labels in im.routes; nothing is dropped.
"""

import re

from im.triage import EpisodeState, render

SCHEMA_VERSION = 1
STAGE_VERSION = "route/2"
ITEM_MIN_CONFIDENCE = 0.5
ROUTER_VERSION = f"rules-2;claude-items>={ITEM_MIN_CONFIDENCE};tap;note_to_self"
NOTE_TO_SELF = re.compile(r"\bnote to (my)?self\b", re.IGNORECASE)


def route(state: EpisodeState, items: list[str] | None) -> tuple[str, list[str]]:
    """`items`: kinds of the captured, undiscarded items above the confidence bar (None: not read yet)."""
    reasons = []
    if state.taps:
        reasons.append("tap")
    if any(line.startswith("me:") and NOTE_TO_SELF.search(line) for line in state.transcript.splitlines()):
        reasons.append("note_to_self")
    for kind in sorted(set(items or [])):
        reasons.append(f"claude:{kind}")
    return ("review" if reasons else "auto_file"), reasons


def run_stage(conn) -> dict:
    """Route every current episode Claude has read (episodes with a tap or a note to self are routed even
    before that). Rewrites a route only when it changed."""
    ids = [r[0] for r in conn.execute("SELECT episode_id FROM im.episodes WHERE current")]
    read = {r[0] for r in conn.execute("SELECT DISTINCT episode_id FROM im.extractions WHERE episode_id = ANY(%s)",
                                       (ids,))}
    kinds: dict = {}
    for eid, kind in conn.execute(
            """SELECT i.episode_id, i.kind FROM im.items i
               WHERE i.episode_id = ANY(%s) AND i.confidence >= %s
                 AND NOT EXISTS (SELECT 1 FROM im.item_verdicts v WHERE v.item_id = i.item_id
                                 AND v.verdict = 'discard')""", (ids, ITEM_MIN_CONFIDENCE)):
        kinds.setdefault(eid, []).append(kind)
    counts = {"auto_file": 0, "review": 0, "waiting_for_claude": 0}
    changed = 0
    with conn.transaction():
        for st in render(conn, ids):
            got = kinds.get(st.episode_id, []) if st.episode_id in read else None
            r, reasons = route(st, got)
            if got is None and r == "auto_file":
                counts["waiting_for_claude"] += 1
                continue
            counts[r] += 1
            changed += conn.execute(
                """INSERT INTO im.routes (episode_id, router_version, route, reasons, schema_version, stage_version,
                     input_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (episode_id, router_version) DO UPDATE
                   SET route = excluded.route, reasons = excluded.reasons, input_hash = excluded.input_hash,
                       created_at = now()
                   WHERE (im.routes.route, im.routes.reasons, im.routes.input_hash)
                     IS DISTINCT FROM (excluded.route, excluded.reasons, excluded.input_hash)""",
                (st.episode_id, ROUTER_VERSION, r, reasons, SCHEMA_VERSION, STAGE_VERSION, st.input_hash)).rowcount
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
