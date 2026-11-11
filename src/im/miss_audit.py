"""What a cheaper extraction model misses, and what it adds: worth having or not? A blind sheet from the extraction
trials (B3, 0008, 0012).

On the v3 trials (2026-11-06, 147 episodes), 90% of Opus's captures were made again by its own second read, and
64-81% of each cheaper model's misses were among those: captures Opus makes every time, not the marginal ones. That
says the misses are stable, not that they matter. The misses sheet asks me: the reference's solid captures (made by both
its reads) that the candidate missed, beside as many it found, without saying which is which. If I'd want the missed
ones as often as the found ones, a cheap model alone loses something; if not, its misses are the soup.

On v5 (2026-11-09, 150 episodes) Sol and Terra made a third more captures than Opus. The both-ways sheet (no
replicate) asks the same of three sets side by side: captures only the candidate made, only the reference made, and
both made. It says whose bar is right: if I'd want the candidate's extras as often as the reference's own, the
reference is too strict, not the candidate too loose.

The sheet reads only im.prompt_trials and the segments their captures cite: it sends nothing. Marking and scoring
are the probe sheets' (im.probe_audit): `score` gives each set's rate of captures worth having.
"""

import json
import random
from pathlib import Path

from im import egress, summary_audit, trial

HEADER = """# Captures (blind)

Each item is one capture from an extraction trial: the transcript lines it cites, the quote it took, and its
summary.

Is it worth having: a thought I'd want in the archive, to find again or to see recur? Judge the thought, not how
well it was captured.

After `mark:`, write `yes`, `no` or `unsure` (`?`); a note is optional. Mark it yourself, without a model's help.
Then run `im audit-misses score` on this folder. "me" is me, "someone" anyone else.
"""
BOTH_WAYS_SIZE = 20   # per set, when no --size: 60 captures to mark


def _capture(eid, i: int, it: dict) -> dict:
    return {"episode": str(eid), "item": i, "kind": it.get("kind"), "quote": it["quote"], "gist": it["gist"],
            "segments": it["source_segment_ids"]}


def captures(conn, candidate: tuple[str, str], reference: tuple[str, str],
             replicate: tuple[str, str]) -> tuple[list[dict], list[dict]]:
    """The reference's solid captures (its replicate made them too), as (missed by the candidate, found by it), on
    the episodes all three read with the same payload. Each is (prompt_version, model)."""
    ref, rep, cand = (trial._reads(conn, *r) for r in (reference, replicate, candidate))
    if not rep:
        raise SystemExit("the reference has no replicate to tell its solid captures from the ones it makes only "
                         "sometimes: im trial <prompt> --model <reference> --replicate 2")
    missed, found = [], []
    for eid in sorted(ref, key=str):
        if not (eid in rep and eid in cand and ref[eid][0] == rep[eid][0] == cand[eid][0]):
            continue
        items = ref[eid][1]
        got = trial._matched(items, cand[eid][1])
        for i in sorted(trial._matched(items, rep[eid][1])):
            (found if i in got else missed).append(_capture(eid, i, items[i]))
    return missed, found


def both_ways(conn, candidate: tuple[str, str], reference: tuple[str, str]) -> dict[str, list[dict]]:
    """Every capture on the episodes both read with the same payload, as made only by the candidate, only by the
    reference, or by both (the reference's, standing for the pair)."""
    ref, cand = (trial._reads(conn, *r) for r in (reference, candidate))
    sets = {"candidate_only": [], "reference_only": [], "both": []}
    for eid in sorted(ref, key=str):
        if not (eid in cand and ref[eid][0] == cand[eid][0]):
            continue
        r_items, c_items = ref[eid][1], cand[eid][1]
        got = trial._matched(r_items, c_items)
        for i, it in enumerate(r_items):
            sets["both" if i in got else "reference_only"].append(_capture(eid, i, it))
        used = set(got.values())
        sets["candidate_only"] += [_capture(eid, j, it) for j, it in enumerate(c_items) if j not in used]
    return sets


def build(conn, candidate, reference, replicate, seed: int = 13, size: int | None = None) -> tuple[str, dict]:
    """With a replicate: every solid capture the candidate missed (or `size` of them), and as many it found. Without:
    `size` (default BOTH_WAYS_SIZE) from each of both_ways' sets."""
    rng = random.Random(seed)
    if replicate:
        missed, found = captures(conn, candidate, reference, replicate)
        if not missed:
            raise SystemExit(f"{candidate[1]} missed none of {reference[1]}'s solid captures on the episodes all three "
                             "read")
        sets, n = {"missed": missed, "found": found}, min(size or len(missed), len(missed), len(found))
    else:
        sets = both_ways(conn, candidate, reference)
        n = min(size or BOTH_WAYS_SIZE, *map(len, sets.values()))
        if not n:
            raise SystemExit(f"{candidate[1]} and {reference[1]} leave a set empty on the episodes both read: "
                             + ", ".join(f"{s} {len(c)}" for s, c in sets.items()))
    picked = [c | {"set": s} for s, cs in sets.items() for c in rng.sample(cs, n)]
    rng.shuffle(picked)
    lines = [HEADER]
    for num, c in enumerate(picked, 1):
        lines += [f"## {num}", "", "What was said:", *summary_audit._lines(conn, c["segments"]), "",
                  f"Quote: \"{c['quote']}\"", f"Summary: {c['gist']}", "", "mark: ", "note: ", ""]
    key = {"benchmark": "misses" if replicate else "both-ways", "seed": seed, "candidate": candidate[1],
           "reference": reference[1],
           "prompt_versions": {"candidate": candidate[0], "reference": reference[0]}
           | ({"replicate": replicate[0]} if replicate else {}),
           "policy": egress.POLICY_VERSION, "sizes": {s: n for s in sets},
           "available": {s: len(cs) for s, cs in sets.items()},
           "pairs": {str(num): c for num, c in enumerate(picked, 1)}}
    return "\n".join(lines), key


def write(conn, out: Path, candidate, reference, replicate, seed: int = 13, size: int | None = None) -> dict:
    """The sheet and its key in `out`; `replicate` None for the both-ways sheet."""
    out.mkdir(parents=True, exist_ok=True)
    sheet, key = out / "sheet.md", out / "key.json"
    if sheet.exists() or key.exists():
        raise SystemExit(f"{out} already has a sheet; score it, or pick another folder")
    text, k = build(conn, candidate, reference, replicate, seed, size)
    sheet.write_text(text)
    key.write_text(json.dumps(k, indent=2))
    return {"sheet": str(sheet), "items": len(k["pairs"]), "by_set": k["sizes"], "available": k["available"]}
