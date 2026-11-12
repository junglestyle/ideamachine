"""The capture gate (decision 0012): Jev's checks on each live capture, a cleanup pass over the rebuilt archive.

Code checks (im.capture_checks) are recorded on every capture as it's read. Jev then asks of each capture, from the
transcript around it (jev.QUESTIONS["gate"]):

- does the gist add to, misstate, overstate or complete what its lines say, or point at something the transcript
  names without naming it ("that kind of overview")? A gist that does is repaired;
- is the point someone else's that I only go along with, or one I voice only to mock? The capture is dropped;
- does the gist make two points? Flagged only.

Each check decides at its own cut. Until the cuts are set from marked captures (recall first for repairs, which cost
a request; precision first for drops, which lose a capture), each is Jev's own confident yes (jev.YES): the summary
says how many captures each check would catch, not yet which of them are right.

This reads the live captures (im.items under the live prompt, on current episodes; the archive's have no lines and
are left out) and sends only to Jev. Answers are kept (im.jev_answers), so a capture is asked about once. Nothing is
repaired or dropped yet: the summary measures how often each check fires.
"""

import hashlib
import json

from im import egress, jev, llm
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


def captures(conn, read: tuple[str, str]) -> tuple[dict, int]:
    """The live captures under `read` (prompt_version, model) and the policy requests go out under now: episode_id ->
    (the payload sha the episode's current read was of, their captures) on current episodes; and how many captures of
    the archive there are under it, which have no transcript lines to check a gist against and are left out. The sha is
    the read's (im.extractions), as egress.Payload.sha256 counts it: without the mishearing hints, which the egress
    log's hash of what was sent includes."""
    pv, model = read
    out: dict = {}
    for eid, sha, item_id, quote, gist, segs in conn.execute(
            """SELECT i.episode_id, x.input_hash, i.item_id, i.quote, i.gist, i.source_segment_ids
               FROM im.items i JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
               JOIN im.extractions x ON x.episode_id = i.episode_id AND x.model = i.model
                 AND x.prompt_version = i.prompt_version AND x.privacy_policy_version = i.privacy_policy_version
               WHERE i.prompt_version = %s AND i.model = %s AND i.privacy_policy_version = %s
               ORDER BY i.episode_id, i.item_id""", (pv, model, egress.POLICY_VERSION)):
        out.setdefault(eid, (sha, []))[1].append({"item_id": str(item_id), "quote": quote, "gist": gist,
                                                  "source_segment_ids": [str(s) for s in segs]})
    archive = conn.execute("""SELECT count(*) FROM im.items WHERE episode_id IS NULL AND prompt_version = %s
                              AND model = %s AND privacy_policy_version = %s""",
                           (pv, model, egress.POLICY_VERSION)).fetchone()[0]
    return out, archive


def run(conn, client, read: tuple[str, str], monthly_cap_usd: float, limit: int | None = None) -> dict:
    """Ask Jev about each live capture (captures) it hasn't answered about under its current questions, at most
    `limit` of them (0 sends nothing and says how many are due). An episode whose payload changed since it was read
    is skipped: its captures are of another transcript until it's read again."""
    kept = jev.kept(conn, jev.question_set(client, "gate"))
    caps, archive = captures(conn, read)
    stats = {"captures": 0, "archive_skipped": archive, "already_answered": 0, "due": 0, "asked": 0, "failed": 0,
             "payload_changed": 0, "cost_usd": 0.0, "stopped": None}
    for eid, (sha, items) in sorted(caps.items(), key=lambda kv: str(kv[0])):
        stats["captures"] += len(items)
        todo = [it for it in items if key(sha, it) not in kept]
        stats["already_answered"] += len(items) - len(todo)
        if not todo:
            continue
        p = egress.build(conn, eid)
        if p.sha256 != sha:
            stats["payload_changed"] += len(todo)
            continue
        stats["due"] += len(todo)
        if stats["stopped"] or (limit is not None and stats["asked"] >= limit):
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
    stats["due"] -= stats["asked"]
    stats["cost_usd"] = round(stats["cost_usd"], 6)
    return stats


def summary(conn, client, read: tuple[str, str], cuts: dict[str, float] = CUTS) -> dict:
    """Over the live captures Jev has answered about: how many each check catches at its cut and at 0.5, what the
    gate would do with them (keep, repair, drop: counted, nothing is applied), and each check's likeliest examples."""
    kept = jev.kept(conn, jev.question_set(client, "gate"))
    caps, _ = captures(conn, read)
    answered = [(it, kept[k]) for sha, items in caps.values() for it in items if (k := key(sha, it)) in kept]
    if not answered:
        return {"captures": 0}
    decisions = [verdict(a, cuts)["decision"] for _, a in answered]
    by_check = {}
    for q in CHECKS:
        top = sorted(answered, key=lambda x: -x[1][q])[:EXAMPLES]
        by_check[q] = {"at_cut": sum(a[q] >= cuts[q] for _, a in answered),
                       "at_0.5": sum(a[q] >= 0.5 for _, a in answered),
                       "likeliest": [{"p": round(a[q], 3), "item_id": it["item_id"], "quote": it["quote"],
                                      "gist": it["gist"]} for it, a in top]}
    return {"captures": len(answered), "cuts": cuts,
            "decisions": {d: decisions.count(d) for d in ("keep", "repair", "drop")}, "by_check": by_check}
