"""Trying a candidate extraction model or prompt against a reference, before switching to it (B3, decision 0008).

Every model reads the same sample of current episodes (ordered by a hash of their id, so a sample of 100 is the
first 100 of a sample of 200), through the same egress path as extraction: logged, counted toward the monthly cap,
and under policy B or C. What it captures is kept in im.prompt_trials, never in im.items, so it reaches nothing.

The report scores a candidate against a reference's read of the same payloads (Opus, normally): the share of the
reference's captures it also found, matched the way verdicts are carried forward (overlapping segments,
`quote_score`), and what it found that the reference didn't. My keeps aren't the target: capture is the soup, and
the lattice's relationships decide what matters. Which of two models captures better where they differ is what
the blind pairs are for.

Recall is also split by how far each reference capture's idea reaches in the live lattice: the captures on the same
lines in live extraction, and the number of conversations their cluster has been said in. Missing a capture whose
idea recurs costs something; missing a one-off mostly saves matching and noise. Captures live extraction didn't
place in a cluster are counted apart.

A replicate (`replicate=2`) is a second, independent read by the same model and prompt, filed apart. The reference
against its own replicate is the ceiling: models don't capture exactly the same things twice, so a candidate's
recall means something only next to it.
"""

import json
import re

from im import egress, extract, llm
from im.feedback import CARRY_MIN_SCORE, quote_score

SAMPLE = 150
EXAMPLES = 15
KEEP = ("kind", "said_by", "attributed_to", "quote", "reading", "gist", "confidence")   # what a trial keeps


def sample(conn, n: int = SAMPLE) -> list:
    """The first n current episodes in a fixed pseudo-random order: the same ones for every model."""
    return [eid for (eid,) in conn.execute(
        "SELECT episode_id FROM im.episodes WHERE current ORDER BY md5(episode_id::text) LIMIT %s", (n,))]


def run(conn, client, system: str, model: str = "claude-opus-5-5", effort: str = "medium",
        monthly_cap_usd: float = 20.0, limit: int | None = None, n: int = SAMPLE, scrubber=None,
        replicate: int = 1, schema: dict = extract.SCHEMA) -> dict:
    """Send the sampled episodes this model hasn't read yet under this prompt and policy (or whose payload changed).
    `schema` is the prompt's (extract.schema_for)."""
    pv = version(model, effort, system, replicate, schema)
    policy = scrubber.policy if scrubber else egress.POLICY_VERSION
    done = {r[0]: r[1] for r in conn.execute(
        """SELECT episode_id, payload_sha256 FROM im.prompt_trials
           WHERE prompt_version = %s AND model = %s AND privacy_policy_version = %s""", (pv, model, policy))}
    todo = [p for p in (egress.build(conn, eid, scrubber) for eid in sample(conn, n))
            if done.get(p.episode_id) != p.sha256]
    stats = {"model": model, "prompt_version": pv, "policy": policy, "pending": len(todo), "read": 0, "failed": 0,
             "cost_usd": 0.0, "stopped": None}
    for p in todo[:limit] if limit else todo:
        try:
            a = egress.ask(conn, client, model, effort, system, schema, p.text, segment_ids=p.segment_ids,
                           prompt_version=pv, monthly_cap_usd=monthly_cap_usd, policy=p.policy,
                           episode_id=p.episode_id, read=lambda answer: extract._items(p, answer["items"]))
        except llm.Stop as e:
            stats["stopped"] = str(e)[:200]
            break
        stats["cost_usd"] += a.cost_usd
        if a.failed:   # an answer that doesn't keep to the schema: logged and paid for, read again next time
            stats["failed"] += 1
            continue
        egress_id, items = a.egress_id, a.data or []
        rows = [{k: it[k] for k in KEEP if k in it} | {"source_segment_ids": [str(s) for s in it["source_segment_ids"]]}
                for it in items]
        conn.execute(
            """INSERT INTO im.prompt_trials (episode_id, prompt_version, model, privacy_policy_version, payload_sha256,
                 egress_id, items) VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (episode_id, prompt_version, model, privacy_policy_version) DO UPDATE
               SET payload_sha256 = excluded.payload_sha256, egress_id = excluded.egress_id, items = excluded.items,
                   created_at = now()""",
            (p.episode_id, pv, model, policy, p.sha256, egress_id, json.dumps(rows)))
        stats["read"] += 1
    stats["cost_usd"] = round(stats["cost_usd"], 4)
    return stats


def version(model: str, effort: str, system: str, replicate: int = 1, schema: dict = extract.SCHEMA) -> str:
    """The prompt version a trial is filed under; a replicate's is marked `~n`."""
    pv = extract.prompt_version(model, effort, system, schema)
    return pv if replicate == 1 else f"{pv}~{replicate}"


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


REACHES = ("recurring", "one_off", "unplaced")


def _live_reach(conn, episode_ids: list) -> dict:
    """episode_id -> [(segment ids, quote, conversations)] for the live captures there that are in a cluster:
    the most conversations any cluster they're evidence for has been said in."""
    out: dict = {}
    for eid, segs, quote, conversations in conn.execute(
            # Conversations from each cluster's current captures' own lines, and its observations that aren't speech:
            # a capture counts whether or not the observation stage has placed it yet.
            """WITH reach AS (
                 SELECT idea_id, count(DISTINCT conversation) AS conversations FROM (
                   SELECT ev.idea_id, s.conversation_id::text AS conversation
                   FROM im.idea_evidence ev JOIN im.items i USING (item_id)
                   JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
                   JOIN im.source_segments s ON s.segment_id = ANY(i.source_segment_ids)
                   UNION ALL
                   SELECT o.idea_id, o.conversation::text FROM im.observations o WHERE o.source <> 'speech') x
                 GROUP BY idea_id)
               SELECT i.episode_id, i.source_segment_ids, i.quote, max(r.conversations)
               FROM im.items i JOIN im.idea_evidence ev USING (item_id) JOIN reach r USING (idea_id)
               WHERE i.episode_id = ANY(%s)
               GROUP BY i.item_id, i.episode_id, i.source_segment_ids, i.quote""", (episode_ids,)):
        out.setdefault(eid, []).append(({str(x) for x in segs}, quote, conversations))
    return out


def by_reach(conn, found: list) -> dict:
    """Recall of the reference's captures split by reach: `recurring` (its idea said in two or more conversations),
    `one_off` (in one), `unplaced` (no live capture on its lines in a cluster). `found` is [(episode, capture,
    found by the candidate)]."""
    live = _live_reach(conn, list({eid for eid, _, _ in found}))
    n = {r: [0, 0] for r in REACHES}
    for eid, it, hit in found:
        best = max(((quote_score(it["quote"], q), c) for segs, q, c in live.get(eid, [])
                    if segs & set(it["source_segment_ids"])), default=(0.0, None))
        reach = "unplaced" if best[0] < CARRY_MIN_SCORE else "recurring" if best[1] >= 2 else "one_off"
        n[reach][0] += 1
        n[reach][1] += hit
    return {r: {"captures": t, "recall": round(f / t, 3) if t else None} for r, (t, f) in n.items()}


def report(conn, candidate: tuple[str, str], reference: tuple[str, str], policy: str) -> dict:
    """How a candidate's captures compare with a reference's, on the episodes both read with the same payload.
    `candidate` and `reference` are (prompt_version, model)."""
    cand, ref = _reads(conn, *candidate, policy), _reads(conn, *reference, policy)
    both = [eid for eid in cand if eid in ref and cand[eid][0] == ref[eid][0]]
    n = {"reference_captures": 0, "found": 0, "candidate_captures": 0, "extra": 0,
         "episodes_reference_captured_in": 0, "of_those_candidate_captured_nothing": 0}
    by_kind: dict = {}
    missed, extra, found_by_reach = [], [], []
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
            found_by_reach.append((eid, it, i in m))
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
        "recall_by_reach": by_reach(conn, found_by_reach),
        "cost_usd": round(cost(cand), 4), "reference_cost_usd": round(cost(ref), 4),
        "cost_vs_reference": rate(cost(cand), cost(ref)),
        "quotes_as_said": {"candidate": as_said(conn, [cand[e][1] for e in both]),
                           "reference": as_said(conn, [ref[e][1] for e in both])},
        "missed": missed[:EXAMPLES], "extra_examples": extra[:EXAMPLES]}


def _words(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


def as_said(conn, reads: list[list[dict]]) -> float | None:
    """The share of captures whose quote field copies the transcript lines they cite word for word (case and
    punctuation aside): v4 asks for the quote exactly as said, with the tidied version in `reading`, where earlier
    prompts let the quote itself be tidied (or stitched with "...")."""
    items = [it for r in reads for it in r]
    if not items:
        return None
    text = dict(conn.execute("SELECT segment_id::text, text FROM im.source_segments WHERE segment_id = ANY(%s::uuid[])",
                             ([s for it in items for s in it["source_segment_ids"]],)).fetchall())
    hits = sum(f" {_words(it['quote'])} " in f" {_words(' '.join(text.get(s, '') for s in it['source_segment_ids']))} "
               for it in items)
    return round(hits / len(items), 3)

