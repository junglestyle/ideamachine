"""The capture gate (decision 0012): Jev's checks on each capture, so a cheaper extractor can do the bulk of the work.

Code checks (im.capture_checks) are recorded on every capture as it's read. Jev then asks of each capture, from the
transcript around it (jev.QUESTIONS["gate"]):

- does the gist add to, misstate, overstate or complete what its lines say, or point at something the transcript
  names without naming it ("that kind of overview")? A gist that does is repaired;
- is the point someone else's that I only go along with, or one I voice only to mock? The capture is dropped;
- does the gist make two points? Flagged only.

Each check decides at its own cut. Until the cuts are set from marked captures (recall first for repairs, which cost
a request; precision first for drops, which lose a capture), each is Jev's own confident yes (jev.YES): the summary
says how many captures each check would catch, not yet which of them are right.

This reads extraction trials (im.prompt_trials) and sends only to Jev. Answers are kept (im.jev_answers), so a
capture is asked about once.
"""

import hashlib
import json

from im import egress, jev, llm, trial
from im.capture_checks import lines_of

GIST = ("adds", "misstates", "overstates", "completes", "unresolved")
DROP = ("not_mine", "mocked")
FLAG = ("two_points",)
CHECKS = GIST + DROP + FLAG
CUTS = {q: jev.YES for q in CHECKS}
BEFORE, AFTER = 8, 3   # context: lines shown before the first cited line and after the last
EXAMPLES = 3


def key(payload_sha256: str, item: dict) -> str:
    """What a capture's answer is kept under: the capture, and the transcript it was read from."""
    return hashlib.sha256(json.dumps([payload_sha256, item["quote"], item["gist"],
                                      sorted(item["source_segment_ids"])]).encode()).hexdigest()


def state(p: egress.Payload, item: dict) -> dict:
    """What Jev is shown about one capture: the lines around it and the ones it cites, as sent, its quote and gist."""
    lines = lines_of(p.text)
    at = {str(s): n for n, s in enumerate(p.segment_ids, 1)}
    cited = sorted(at[str(s)] for s in item["source_segment_ids"] if str(s) in at)
    show = lambda ns: "\n".join(f"[{n}] {lines[n][0]}: {lines[n][1]}" for n in ns if n in lines)  # noqa: E731
    return {"context": show(range(cited[0] - BEFORE, cited[-1] + AFTER + 1)), "lines": show(cited),
            "quote": item["quote"], "gist": item["gist"]}


def verdict(answers: dict[str, float], cuts: dict[str, float] = CUTS) -> dict:
    """keep, repair (a gist check failed) or drop (a capture check failed), and every check at or over its cut."""
    failing = [q for q in CHECKS if answers[q] >= cuts[q]]
    decision = "drop" if set(failing) & set(DROP) else "repair" if set(failing) & set(GIST) else "keep"
    return {"decision": decision, "failing": failing}


def run(conn, client, read: tuple[str, str], monthly_cap_usd: float, limit: int | None = None) -> dict:
    """Ask Jev about each capture of one trial read (prompt_version, model) it hasn't answered about yet, at most
    `limit` of them. An episode whose payload changed since the trial read it is skipped: its captures are of
    another transcript."""
    kept = jev.kept(conn, jev.question_set(client, "gate"))
    stats = {"captures": 0, "already_answered": 0, "asked": 0, "failed": 0, "payload_changed": 0, "cost_usd": 0.0,
             "stopped": None}
    for eid, (sha, items, _) in sorted(trial._reads(conn, *read).items(), key=lambda kv: str(kv[0])):
        stats["captures"] += len(items)
        todo = [it for it in items if key(sha, it) not in kept]
        stats["already_answered"] += len(items) - len(todo)
        if not todo or stats["stopped"] or (limit is not None and stats["asked"] >= limit):
            continue
        p = egress.build(conn, eid)
        if p.sha256 != sha:
            stats["payload_changed"] += len(todo)
            continue
        for it in todo[:None if limit is None else limit - stats["asked"]]:
            try:
                got, cost, _ = jev.ask(conn, client, "gate", key(sha, it), lambda it=it: state(p, it),
                                       segment_ids=it["source_segment_ids"],
                                       monthly_cap_usd=monthly_cap_usd)
            except llm.Stop as e:
                stats["stopped"] = str(e)[:200]
                break
            stats["asked"] += 1
            stats["failed"] += got is None
            stats["cost_usd"] += cost
    stats["cost_usd"] = round(stats["cost_usd"], 6)
    return stats


def summary(conn, client, read: tuple[str, str], cuts: dict[str, float] = CUTS) -> dict:
    """Over one trial read's captures Jev has answered about: how many each check catches at its cut and at 0.5,
    what the gate would do with them, and each check's likeliest examples."""
    kept = jev.kept(conn, jev.question_set(client, "gate"))
    answered = [(it, kept[k]) for sha, items, _ in trial._reads(conn, *read).values() for it in items
                if (k := key(sha, it)) in kept]
    if not answered:
        return {"captures": 0}
    n = len(answered)
    decisions = [verdict(a, cuts)["decision"] for _, a in answered]
    by_check = {}
    for q in CHECKS:
        top = sorted(answered, key=lambda x: -x[1][q])[:EXAMPLES]
        by_check[q] = {"at_cut": round(sum(a[q] >= cuts[q] for _, a in answered) / n, 3),
                       "at_0.5": round(sum(a[q] >= 0.5 for _, a in answered) / n, 3),
                       "likeliest": [{"p": round(a[q], 3), "quote": it["quote"], "gist": it["gist"]}
                                     for it, a in top]}
    return {"captures": n, "cuts": cuts, "decisions": {d: decisions.count(d) for d in ("keep", "repair", "drop")},
            "by_check": by_check}
