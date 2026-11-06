"""What a cheaper extraction model misses: worth having or not? A blind sheet from the extraction trials (B3, 0008).

On the v3 trials (2026-11-06, 147 episodes), 90% of Opus's captures were made again by its own second read, and
64-81% of each cheaper model's misses were among those: captures Opus makes every time, not the marginal ones. That
says the misses are stable, not that they matter. This sheet asks me: the reference's solid captures (made by both
its reads) that the candidate missed, beside as many it found, without saying which is which. If I'd want the missed
ones as often as the found ones, a cheap model alone loses something; if not, its misses are the soup.

The sheet reads only im.prompt_trials and the segments their captures cite: it sends nothing. Marking and scoring
are the probe sheets' (im.probe_audit): `score` gives each set's rate of captures worth having.
"""

import json
import random
from pathlib import Path

from im import summary_audit, trial

HEADER = """# Captures (blind)

Each item is one capture from an extraction trial: the transcript lines it cites, the quote it took, and its
summary.

Is it worth having: a thought I'd want in the archive, to find again or to see recur? Judge the thought, not how
well it was captured.

After `mark:`, write `yes`, `no` or `unsure` (`?`); a note is optional. Mark it yourself, without a model's help.
Then run `im audit-misses score` on this folder. "me" is me, "someone" anyone else.
"""


def captures(conn, candidate: tuple[str, str], reference: tuple[str, str], replicate: tuple[str, str],
             policy: str) -> tuple[list[dict], list[dict]]:
    """The reference's solid captures (its replicate made them too), as (missed by the candidate, found by it), on
    the episodes all three read with the same payload. Each is (prompt_version, model)."""
    ref, rep, cand = (trial._reads(conn, *r, policy) for r in (reference, replicate, candidate))
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
            it = items[i]
            (found if i in got else missed).append({"episode": str(eid), "item": i, "kind": it["kind"],
                                                    "quote": it["quote"], "gist": it["gist"],
                                                    "segments": it["source_segment_ids"]})
    return missed, found


def build(conn, candidate, reference, replicate, policy: str, seed: int = 13,
          size: int | None = None) -> tuple[str, dict]:
    """Every solid capture the candidate missed (or `size` of them), and as many it found."""
    rng = random.Random(seed)
    missed, found = captures(conn, candidate, reference, replicate, policy)
    if not missed:
        raise SystemExit(f"{candidate[1]} missed none of {reference[1]}'s solid captures on the episodes all three read")
    n = min(size or len(missed), len(missed), len(found))
    picked = [c | {"set": "missed"} for c in rng.sample(missed, n)] + [c | {"set": "found"} for c in rng.sample(found, n)]
    rng.shuffle(picked)
    lines = [HEADER]
    for num, c in enumerate(picked, 1):
        lines += [f"## {num}", "", "What was said:", *summary_audit._lines(conn, c["segments"]), "",
                  f"Quote: \"{c['quote']}\"", f"Summary: {c['gist']}", "", "mark: ", "note: ", ""]
    key = {"benchmark": "misses", "seed": seed, "candidate": candidate[1], "reference": reference[1],
           "prompt_versions": {"candidate": candidate[0], "reference": reference[0], "replicate": replicate[0]},
           "policy": policy, "sizes": {"missed": n, "found": n},
           "available": {"missed": len(missed), "found": len(found)},
           "pairs": {str(num): c for num, c in enumerate(picked, 1)}}
    return "\n".join(lines), key


def write(conn, out: Path, candidate, reference, replicate, policy: str, seed: int = 13,
          size: int | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    sheet, key = out / "sheet.md", out / "key.json"
    if sheet.exists() or key.exists():
        raise SystemExit(f"{out} already has a sheet; score it, or pick another folder")
    text, k = build(conn, candidate, reference, replicate, policy, seed, size)
    sheet.write_text(text)
    key.write_text(json.dumps(k, indent=2))
    return {"sheet": str(sheet), "items": len(k["pairs"]), "by_set": k["sizes"], "available": k["available"]}
