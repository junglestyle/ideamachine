"""Trying a candidate extraction model or prompt against a reference, before switching to it (B3, decision 0008).

Every model reads the same sample of current episodes (ordered by a hash of their id, so a sample of 100 is the
first 100 of a sample of 200), through the same egress path as extraction: logged, counted toward the monthly cap,
and under policy B or C. What it captures is kept in im.prompt_trials, never in im.items, so it reaches nothing.

The report scores a candidate against a reference's read of the same payloads (Opus, normally): the share of the
reference's captures it also found, matched the way verdicts are carried forward (overlapping segments,
`quote_score`), and what it found that the reference didn't. My keeps aren't the target: capture is the soup, and
the lattice's relationships decide what matters. Which of two models captures better where they differ is what
the blind pairs are for.
"""

import json

from im import egress, extract, llm
from im.feedback import CARRY_MIN_SCORE, quote_score

SAMPLE = 150
EXAMPLES = 15


def sample(conn, n: int = SAMPLE) -> list:
    """The first n current episodes in a fixed pseudo-random order: the same ones for every model."""
    return [eid for (eid,) in conn.execute(
        "SELECT episode_id FROM im.episodes WHERE current ORDER BY md5(episode_id::text) LIMIT %s", (n,))]


def run(conn, client, system: str, model: str = "claude-opus-5-5", effort: str = "medium",
        monthly_cap_usd: float = 20.0, limit: int | None = None, n: int = SAMPLE, scrubber=None) -> dict:
    """Send the sampled episodes this model hasn't read yet under this prompt and policy (or whose payload changed)."""
    pv = extract.prompt_version(model, effort, system)
    policy = scrubber.policy if scrubber else egress.POLICY_VERSION
    done = {r[0]: r[1] for r in conn.execute(
        """SELECT episode_id, payload_sha256 FROM im.prompt_trials
           WHERE prompt_version = %s AND model = %s AND privacy_policy_version = %s""", (pv, model, policy))}
    todo = [p for p in (egress.build(conn, eid, scrubber) for eid in sample(conn, n))
            if done.get(p.episode_id) != p.sha256]
    stats = {"model": model, "prompt_version": pv, "policy": policy, "pending": len(todo), "read": 0, "failed": 0,
             "cost_usd": 0.0, "stopped": None}
    for p in todo[:limit] if limit else todo:
        if extract.spent_this_month(conn) >= monthly_cap_usd:
            stats["stopped"] = f"monthly cap of ${monthly_cap_usd:.2f} reached"
            break
        try:
            r = extract.call(client, model, effort, system, p.text)
            raw = [] if r.refused else json.loads(r.text)["items"]
            items = extract._items(p, raw)
        except llm.Unavailable as e:
            extract._log(conn, p, model, pv, error=str(e)[:500])
            stats["stopped"] = f"API unavailable: {e}"[:200]
            break
        except (RuntimeError, ValueError, KeyError, TypeError) as e:   # an answer that doesn't keep to the schema
            extract._log(conn, p, model, pv, error=f"{type(e).__name__}: {e}"[:500])
            stats["failed"] += 1
            continue
        egress_id = extract._log(conn, p, model, pv, request_id=r.request_id, stop_reason=r.stop_reason,
                                 served_by=r.served_by, input_tokens=r.input_tokens, output_tokens=r.output_tokens,
                                 cost_usd=r.cost_usd)
        keep = ("kind", "said_by", "quote", "gist", "confidence")
        rows = [{k: it[k] for k in keep} | {"source_segment_ids": [str(s) for s in it["source_segment_ids"]]}
                for it in items]
        conn.execute(
            """INSERT INTO im.prompt_trials (episode_id, prompt_version, model, privacy_policy_version, payload_sha256,
                 egress_id, items) VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (episode_id, prompt_version, model, privacy_policy_version) DO UPDATE
               SET payload_sha256 = excluded.payload_sha256, egress_id = excluded.egress_id, items = excluded.items,
                   created_at = now()""",
            (p.episode_id, pv, model, policy, p.sha256, egress_id, json.dumps(rows)))
        stats["read"] += 1
        stats["cost_usd"] += r.cost_usd or 0.0
    stats["cost_usd"] = round(stats["cost_usd"], 4)
    return stats


def _reads(conn, pv: str, model: str, policy: str) -> dict:
    """episode_id -> (payload sha, items, cost) for one model's trial."""
    return {eid: (sha, items, float(cost) if cost is not None else None) for eid, sha, items, cost in conn.execute(
        """SELECT t.episode_id, t.payload_sha256, t.items, g.cost_usd FROM im.prompt_trials t
           JOIN im.egress_log g USING (egress_id)
           WHERE t.prompt_version = %s AND t.model = %s AND t.privacy_policy_version = %s""", (pv, model, policy))}


def _matched(ref: list[dict], cand: list[dict]) -> dict:
    """Reference capture index -> candidate capture index, best quote first, each used once."""
    pairs = sorted(((quote_score(a["quote"], b["quote"]), i, j)
                    for i, a in enumerate(ref) for j, b in enumerate(cand)
                    if set(a["source_segment_ids"]) & set(b["source_segment_ids"])), reverse=True)
    out, used = {}, set()
    for score, i, j in pairs:
        if score >= CARRY_MIN_SCORE and i not in out and j not in used:
            out[i] = j
            used.add(j)
    return out


def report(conn, candidate: tuple[str, str], reference: tuple[str, str], policy: str) -> dict:
    """How a candidate's captures compare with a reference's, on the episodes both read with the same payload.
    `candidate` and `reference` are (prompt_version, model)."""
    cand, ref = _reads(conn, *candidate, policy), _reads(conn, *reference, policy)
    both = [eid for eid in cand if eid in ref and cand[eid][0] == ref[eid][0]]
    n = {"reference_captures": 0, "found": 0, "candidate_captures": 0, "extra": 0,
         "episodes_reference_captured_in": 0, "of_those_candidate_captured_nothing": 0}
    by_kind: dict = {}
    missed, extra = [], []
    for eid in both:
        r_items, c_items = ref[eid][1], cand[eid][1]
        m = _matched(r_items, c_items)
        n["reference_captures"] += len(r_items)
        n["found"] += len(m)
        n["candidate_captures"] += len(c_items)
        n["extra"] += len(c_items) - len(m)
        if r_items:
            n["episodes_reference_captured_in"] += 1
            n["of_those_candidate_captured_nothing"] += not c_items
        for i, it in enumerate(r_items):
            k = by_kind.setdefault(it["kind"], [0, 0])
            k[0] += 1
            k[1] += i in m
            if i not in m:
                missed.append(f"{it['kind']}: {it['quote']}")
        used = set(m.values())
        extra += [f"{it['kind']}: {it['quote']}" for j, it in enumerate(c_items) if j not in used]
    rate = lambda a, b: round(a / b, 3) if b else None  # noqa: E731
    cost = lambda reads: sum(reads[e][2] or 0.0 for e in both)  # noqa: E731
    return {"candidate": candidate[1], "reference": reference[1], "policy": policy, "episodes": len(both),
            "candidate_only_read": len(cand) - len(both)} | n | {
        "recall": rate(n["found"], n["reference_captures"]),
        "recall_by_kind": {k: rate(f, t) for k, (t, f) in sorted(by_kind.items())},
        "cost_usd": round(cost(cand), 4), "reference_cost_usd": round(cost(ref), 4),
        "cost_vs_reference": rate(cost(cand), cost(ref)),
        "missed": missed[:EXAMPLES], "extra_examples": extra[:EXAMPLES]}

