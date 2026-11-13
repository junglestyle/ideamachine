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

The live sheet (`--live`, 2026-11-13, for v5.4's moment-bound-remarks trial) compares a trial with the live read
itself (im.items: the prompt, model and effort `im run` reads with), not a trial read of the live prompt: every
capture only the live read made (removed by the candidate), every one only the candidate made (added), and a sample
of those both made, shuffled and unlabeled, marked eligible, ineligible or uncertain under the eligibility audit's
frozen rubric (im.eligibility_audit). `score` gives counts with their denominators and Wilson intervals.
"""

import json
import random
from pathlib import Path

from im import egress, eligibility_audit, summary_audit, trial

HEADER = """# Captures (blind)

Each item is one capture from an extraction trial: the transcript lines it cites, the quote it took, and its
summary.

Is it worth having: a thought I'd want in the archive, to find again or to see recur? Judge the thought, not how
well it was captured.

After `mark:`, write `yes`, `no` or `unsure` (`?`); a note is optional. Mark it yourself, without a model's help.
Then run `im audit-misses score` on this folder. "me" is me, "someone" anyone else.
"""
LIVE_HEADER = """# Captures (blind): eligibility, rubric {version} ({sha})

Each item is one capture: the transcript lines it cites, the quote it took, and its summary. Some are from one read
of the transcript, some from another, some from both; nothing says which.

After `mark:`, write `eligible` (`e`), `ineligible` (`i`) or `uncertain` (`u`, `?`) under the rubric below; a note
is optional. Mark it yourself, without a model's help. Then run `im audit-misses score` on this folder.

```text
{rubric}
```
"""
BOTH_WAYS_SIZE = 20   # per set, when no --size: 60 captures to mark


def _capture(eid, i: int, it: dict) -> dict:
    return {"episode": str(eid), "item": i, "quote": it["quote"], "gist": it["gist"],
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


def live_reads(conn, read: tuple[str, str]) -> dict:
    """The live read `read` (prompt_version, model) in trial._reads' shape: episode_id -> (the transcript hash it
    read, its captures, None), every current episode it read under the policy requests go out under now, captures or
    not."""
    pv, model = read
    args = (pv, model, egress.POLICY_VERSION)
    out = {eid: (sha, [], None) for eid, sha in conn.execute(
        """SELECT x.episode_id, x.input_hash FROM im.extractions x
           JOIN im.episodes e ON e.episode_id = x.episode_id AND e.current
           WHERE x.prompt_version = %s AND x.model = %s AND x.privacy_policy_version = %s""", args)}
    for eid, quote, gist, segs in conn.execute(
            """SELECT episode_id, quote, gist, source_segment_ids FROM im.items
               WHERE prompt_version = %s AND model = %s AND privacy_policy_version = %s AND episode_id = ANY(%s)
               ORDER BY item_id""", (*args, list(out))):
        out[eid][1].append({"quote": quote, "gist": gist, "source_segment_ids": [str(s) for s in segs]})
    return out


def both_ways(conn, candidate: tuple[str, str], reference: tuple[str, str], live: bool = False) -> dict[str, list]:
    """Every capture on the episodes both read with the same payload, as made only by the candidate, only by the
    reference, or by both (the reference's, standing for the pair). With `live`, the reference is the live read
    (live_reads), not a trial's."""
    ref = live_reads(conn, reference) if live else trial._reads(conn, *reference)
    cand = trial._reads(conn, *candidate)
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


def build_live(conn, candidate, reference, seed: int = 13, size: int | None = None) -> tuple[str, dict]:
    """Every capture only the live read `reference` made (`removed`), every one only the trial `candidate` made
    (`added`), and `size` (default BOTH_WAYS_SIZE) of those both made (`kept`), shuffled together."""
    rng = random.Random(seed)
    sets = both_ways(conn, candidate, reference, live=True)
    sets = {"removed": sets["reference_only"], "added": sets["candidate_only"], "kept": sets["both"]}
    if not sets["removed"] and not sets["added"]:
        raise SystemExit(f"the trial and the live read made the same {len(sets['kept'])} captures on the episodes "
                         "both read")
    sizes = {"removed": len(sets["removed"]), "added": len(sets["added"]),
             "kept": min(size or BOTH_WAYS_SIZE, len(sets["kept"]))}
    picked = [c | {"set": s} for s, cs in sets.items() for c in rng.sample(cs, sizes[s])]
    rng.shuffle(picked)
    cand, ref = trial._reads(conn, *candidate), live_reads(conn, reference)
    r = eligibility_audit.rubric()
    lines = [LIVE_HEADER.format(version=r["version"], sha=r["sha256"][:12], rubric=eligibility_audit.RUBRIC)]
    for num, c in enumerate(picked, 1):
        lines += [f"## {num}", "", "What was said:", *summary_audit._lines(conn, c["segments"]), "",
                  f"Quote: \"{c['quote']}\"", f"Summary: {c['gist']}", "", "mark: ", "note: ", ""]
    key = {"benchmark": "live", "rubric": r, "seed": seed, "candidate": candidate[1], "reference": reference[1],
           "prompt_versions": {"candidate": candidate[0], "reference": reference[0]},
           "policy": egress.POLICY_VERSION, "sizes": sizes, "available": {s: len(cs) for s, cs in sets.items()},
           "episodes": {"compared": sum(e in ref and ref[e][0] == cand[e][0] for e in cand),
                        "not_read_live": sum(e not in ref for e in cand),
                        "payload_changed": sum(e in ref and ref[e][0] != cand[e][0] for e in cand)},
           "pairs": {str(num): c for num, c in enumerate(picked, 1)}}
    return "\n".join(lines), key


def write(conn, out: Path, candidate, reference, replicate, seed: int = 13, size: int | None = None,
          live: bool = False) -> dict:
    """The sheet and its key in `out`; `replicate` None for the both-ways sheet; `live` for the live sheet, with
    `reference` the live read."""
    out.mkdir(parents=True, exist_ok=True)
    sheet, key = out / "sheet.md", out / "key.json"
    if sheet.exists() or key.exists():
        raise SystemExit(f"{out} already has a sheet; score it, or pick another folder")
    text, k = (build_live(conn, candidate, reference, seed, size) if live
               else build(conn, candidate, reference, replicate, seed, size))
    sheet.write_text(text)
    key.write_text(json.dumps(k, indent=2))
    return {"sheet": str(sheet), "items": len(k["pairs"]), "by_set": k["sizes"], "available": k["available"]} \
        | ({"episodes": k["episodes"]} if live else {})


def _rate(k: int, n: int) -> dict:
    return {"k": k, "n": n, "share": round(k / n, 3) if n else None, "ci95": eligibility_audit.wilson(k, n)}


def score_live(folder: Path) -> dict:
    """The live sheet's counts, each with its denominator. `removed` (n) and its ineligible share among those marked
    eligible or ineligible (k, Wilson 95%); the valid baseline (N): the live captures I marked eligible among the
    removed, plus every capture both reads made (the kept sample's marks are reported beside it, as a check on
    counting those valid); valid lost (m of N): the removed I marked eligible; `added` (a) and its ineligible share.
    Uncertain and unmarked are counted apart, never in a share."""
    key = json.loads((folder / "key.json").read_text())
    if key.get("benchmark") != "live":
        raise SystemExit(f"{folder} isn't a live sheet")
    eligibility_audit._same_rubric(key.get("rubric"), f"{folder}'s sheet")
    got = eligibility_audit.marks((folder / "sheet.md").read_text())
    tally = {s: {"eligible": 0, "ineligible": 0, "uncertain": 0, "unmarked": 0} for s in ("removed", "added", "kept")}
    unreadable, notes = {}, {}
    for num, c in key["pairs"].items():
        m = got.get(num, {})
        if "unreadable" in m:
            unreadable[num] = m["unreadable"]
        tally[c["set"]][m.get("label", "unmarked")] += 1
        if m.get("note"):
            notes[num] = f"{c['set']}: {m['note']}"
    rm, ad, kp = tally["removed"], tally["added"], tally["kept"]
    n_valid = rm["eligible"] + key["available"]["kept"]
    return {"benchmark": "live", "rubric": key["rubric"], "candidate": key["prompt_versions"]["candidate"],
            "reference": key["prompt_versions"]["reference"], "episodes": key["episodes"],
            "removed": {"n": key["available"]["removed"], "marks": rm,
                        "ineligible": _rate(rm["ineligible"], rm["eligible"] + rm["ineligible"])},
            "valid_baseline": {"N": n_valid, "removed_eligible": rm["eligible"], "kept": key["available"]["kept"],
                               "kept_sample_marks": kp},
            "valid_lost": _rate(rm["eligible"], n_valid),
            "added": {"a": key["available"]["added"], "marks": ad,
                      "ineligible": _rate(ad["ineligible"], ad["eligible"] + ad["ineligible"])},
            **({"unreadable": unreadable} if unreadable else {}), "notes": notes}
