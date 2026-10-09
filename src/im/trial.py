"""Trying a candidate extraction prompt on episodes I've already judged, before switching to it (ROADMAP Phase 2,
slice 3).

The candidate reads each current episode that has a capture I've decided on, through the same egress path as
extraction (logged, counted toward the monthly cap). What it captures is kept in im.prompt_trials, never in
im.items, so it doesn't reach my review queue. The report matches its captures to my judged ones the way
verdicts are carried forward (overlapping segments, `quote_score`), so I can see which keeps it would still find,
which discards it would now skip, and what it captures that I haven't judged.
"""

import json
import uuid

from im import egress, extract
from im.feedback import CARRY_MIN_SCORE, quote_score

KEPT = ("keep", "star")


def judged(conn) -> dict:
    """Current episodes with captures I've decided on: episode_id -> [(verdict, quote, segment_ids)], latest verdict."""
    out: dict = {}
    for eid, verdict, quote, segs in conn.execute(
            """SELECT i.episode_id, v.verdict, i.quote, i.source_segment_ids
               FROM im.items i JOIN im.episodes e USING (episode_id)
               JOIN LATERAL (SELECT verdict FROM im.item_verdicts WHERE item_id = i.item_id
                             ORDER BY decided_at DESC, verdict_id DESC LIMIT 1) v ON true
               WHERE e.current ORDER BY e.started_at, i.item_id"""):
        out.setdefault(eid, []).append((verdict, quote, set(segs)))
    return out


def run(conn, client, system: str, model: str = "claude-opus-5-5", effort: str = "medium",
        monthly_cap_usd: float = 20.0, limit: int | None = None) -> dict:
    """Send the judged episodes the candidate hasn't read yet (or whose payload changed) to Claude with it."""
    import anthropic

    pv = extract.prompt_version(model, effort, system)
    done = {r[0]: r[1] for r in conn.execute(
        "SELECT episode_id, payload_sha256 FROM im.prompt_trials WHERE prompt_version = %s AND model = %s", (pv, model))}
    todo = [p for p in (egress.build(conn, eid) for eid in judged(conn)) if done.get(p.episode_id) != p.sha256]
    stats = {"prompt_version": pv, "pending": len(todo), "read": 0, "cost_usd": 0.0, "stopped": None}
    for p in todo[:limit] if limit else todo:
        if extract.spent_this_month(conn) >= monthly_cap_usd:
            stats["stopped"] = f"monthly cap of ${monthly_cap_usd:.2f} reached"
            break
        try:
            r = extract.call(client, model, effort, system, p.text)
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as e:
            extract._log(conn, p, model, pv, error=f"{type(e).__name__}: {e}"[:500])
            stats["stopped"] = f"API unavailable: {type(e).__name__}"
            break
        served = getattr(r, "model", model) or model
        cost = extract._cost(served, r.usage)
        egress_id = extract._log(conn, p, model, pv, request_id=getattr(r, "_request_id", None),
                                 stop_reason=r.stop_reason, served_by=served, input_tokens=r.usage.input_tokens,
                                 output_tokens=r.usage.output_tokens, cost_usd=cost)
        items = [] if r.stop_reason == "refusal" else extract._items(
            p, json.loads(next(b.text for b in r.content if b.type == "text"))["items"])
        keep = ("kind", "said_by", "quote", "gist", "confidence")
        rows = [{k: it[k] for k in keep} | {"source_segment_ids": [str(s) for s in it["source_segment_ids"]]}
                for it in items]
        conn.execute(
            """INSERT INTO im.prompt_trials (episode_id, prompt_version, model, payload_sha256, egress_id, items)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (episode_id, prompt_version, model) DO UPDATE SET payload_sha256 = excluded.payload_sha256,
                 egress_id = excluded.egress_id, items = excluded.items, created_at = now()""",
            (p.episode_id, pv, model, p.sha256, egress_id, json.dumps(rows)))
        stats["read"] += 1
        stats["cost_usd"] += cost or 0.0
    stats["cost_usd"] = round(stats["cost_usd"], 4)
    return stats


def report(conn, prompt_version: str, model: str = "claude-opus-5-5") -> dict:
    """How the candidate's captures compare with my verdicts on the episodes it has read."""
    mine = judged(conn)
    trials = conn.execute("SELECT episode_id, items FROM im.prompt_trials WHERE prompt_version = %s AND model = %s",
                          (prompt_version, model)).fetchall()
    n = {"kept": 0, "kept_found": 0, "discarded": 0, "discarded_found": 0, "captured": 0, "unjudged": 0}
    missed, skipped, unjudged = [], [], []
    for eid, items in trials:
        if eid not in mine:
            continue   # the episode was replaced since, or my verdicts moved on
        judged_items, found = mine[eid], set()
        pairs = sorted(((quote_score(q, it["quote"]), j, c)
                        for j, (_, q, segs) in enumerate(judged_items)
                        for c, it in enumerate(items) if segs & {uuid.UUID(s) for s in it["source_segment_ids"]}),
                       reverse=True)
        used_c = set()
        for score, j, c in pairs:
            if score >= CARRY_MIN_SCORE and j not in found and c not in used_c:
                found.add(j)
                used_c.add(c)
        for j, (verdict, quote, _) in enumerate(judged_items):
            kept = verdict in KEPT
            n["kept" if kept else "discarded"] += 1
            if j in found:
                n["kept_found" if kept else "discarded_found"] += 1
            else:
                (missed if kept else skipped).append(quote)
        n["captured"] += len(items)
        new = [it for c, it in enumerate(items) if c not in used_c]
        n["unjudged"] += len(new)
        unjudged += [f"{it['kind']}: {it['quote']}" for it in new]
    found = n["kept_found"] + n["discarded_found"]
    rate = lambda a, b: round(a / b, 3) if b else None  # noqa: E731
    return {"prompt_version": prompt_version, "episodes": len(trials)} | n | {
        "keep_rate_before": rate(n["kept"], n["kept"] + n["discarded"]),
        "keep_rate_on_judged": rate(n["kept_found"], found),
        "keep_rate_if_unjudged_are_discards": rate(n["kept_found"], found + n["unjudged"]),
        "keeps_still_found": rate(n["kept_found"], n["kept"]),
        "missed_keeps": missed, "skipped_discards": skipped, "unjudged_captures": unjudged}
