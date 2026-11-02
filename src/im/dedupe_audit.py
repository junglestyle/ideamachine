"""B5 (docs/ROADMAP-v2.md): does folding captures into one observation (im.observations) merge things that aren't
the same capture?

`sheet` writes a blind sheet of pairs of captures: from 50 merged observations, each one's least alike pair, and
the 50 near misses, captures of the same speech (by segment descent, as the observation stage looks) that scored
just under the fold threshold and stayed apart. The two sets are shuffled together, each pair's sides in random
order, with no score shown. I mark each pair `same` or `different`. `score` reads the marks against the key: a
merged pair I mark different is a false merge (the bar is at most 2 in 50), a near miss I mark same is a false
split, which says the threshold could come down. An `unsure` merged pair counts against the bar.

Merges whose captures quote the very same words are trivially right, so they're sampled only when there aren't 50
others. A near miss is one per pair of observations, and no two pairs on the sheet quote the same two things. The sheet quotes people: keep it, and the key, where only root and the apps read (`saved/`, deploy.md).
"""

import itertools
import json
import random
import re
from pathlib import Path

from im.feedback import CARRY_MIN_SCORE, CARRY_METHOD, _words, quote_score

SIZE = 50
BAR = 2   # false merges allowed in SIZE
MARKS = {"same": "same", "s": "same", "different": "different", "diff": "different", "d": "different",
         "unsure": "unsure", "?": "unsure"}

_ANCESTRY = """WITH RECURSIVE a(seg) AS (
                 SELECT unnest(%s::uuid[])
                 UNION SELECT x.old_segment_id FROM a JOIN im.source_supersessions x ON x.new_segment_id = a.seg)"""


def _captures(conn) -> dict:
    """Every capture with an observation: item_id -> (observation_id, quote, gist, said on, segment ids)."""
    return {str(r[0]): r[1:] for r in conn.execute(
        """SELECT i.item_id, i.observation_id, i.quote, i.gist, e.started_at::date, i.source_segment_ids
           FROM im.items i JOIN im.episodes e USING (episode_id) WHERE i.observation_id IS NOT NULL
           ORDER BY i.item_id""")}


def merged(caps: dict) -> list[dict]:
    """Each observation with two or more captures, as its least alike pair."""
    by_obs: dict = {}
    for item_id, (obs, quote, *_) in caps.items():
        by_obs.setdefault(obs, []).append((item_id, quote))
    out = []
    for obs, members in sorted(by_obs.items(), key=lambda kv: str(kv[0])):
        if len(members) < 2:
            continue
        score, a, b = min((quote_score(qa, qb), a, b)
                          for (a, qa), (b, qb) in itertools.combinations(members, 2))
        verbatim = len({tuple(_words(q)) for _, q in members}) == 1
        out.append({"set": "merged", "observation_id": str(obs), "items": [a, b], "score": round(score, 3),
                    "captures": len(members), "verbatim": verbatim})
    return out


def near_misses(conn, caps: dict) -> list[dict]:
    """Pairs of observations, one's capture citing speech the other's descends from, under the threshold: each as
    its closest pair of captures, the closest first. An observation has a capture per re-read, so one pair of
    observations would otherwise come up once for each pair of their captures."""
    seen, best = set(), {}
    for item_id, (obs, quote, _, _, segs) in caps.items():
        for other, other_obs, other_quote in conn.execute(
                _ANCESTRY + """
                   SELECT i.item_id, i.observation_id, i.quote FROM im.items i
                   WHERE i.observation_id IS NOT NULL AND i.observation_id <> %s
                     AND i.source_segment_ids && ARRAY(SELECT seg FROM a)""", (segs, obs)):
            pair = tuple(sorted((item_id, str(other))))
            if pair in seen:
                continue
            seen.add(pair)
            score = quote_score(quote, other_quote)
            observations = tuple(sorted((str(obs), str(other_obs))))
            if score < CARRY_MIN_SCORE and (-score, pair) < best.get(observations, (1.0, ())):
                best[observations] = (-score, pair)
    out = [{"set": "near", "observations": list(o), "items": list(pair), "score": round(-neg, 3)}
           for o, (neg, pair) in best.items()]
    return sorted(out, key=lambda p: (-p["score"], p["items"]))


def distinct(pairs: list[dict], caps: dict) -> list[dict]:
    """The first pair for each pair of quotes, whichever side each is on: the sheet asks about each only once."""
    seen, out = set(), []
    for p in pairs:
        words = frozenset(tuple(_words(caps[i][1])) for i in p["items"])
        if words not in seen:
            seen.add(words)
            out.append(p)
    return out


def build(conn, seed: int = 5, size: int = SIZE) -> tuple[str, dict]:
    """The sheet's text and its key."""
    caps = _captures(conn)
    rng = random.Random(seed)
    groups = merged(caps)
    worded = distinct([g for g in groups if not g["verbatim"]], caps)
    verbatim = distinct([g for g in groups if g["verbatim"]], caps)
    picked = rng.sample(worded, min(size, len(worded)))
    picked += rng.sample(verbatim, min(size - len(picked), len(verbatim)))
    near = distinct(near_misses(conn, caps), caps)
    pairs = picked + near[:size]
    rng.shuffle(pairs)
    for p in pairs:
        rng.shuffle(p["items"])
    lines = ["# B5: dedupe audit (blind)", "",
             "Each pair is two captures of the same stretch of speech. Are they the same capture: the same point,",
             "from the same words? Mark each `same` or `different` (or `unsure`) after `mark:`, then run",
             "`im audit-dedupe score` on this folder. Gists are Claude's, quotes are what was said.", ""]
    for n, p in enumerate(pairs, 1):
        lines += [f"## {n}", ""]
        for side, item_id in zip("AB", p["items"]):
            _, quote, gist, on, _ = caps[item_id]
            lines += [f"{side} ({on}): \"{quote}\"", f"   {gist}", ""]
        lines += ["mark: ", ""]
    key = {"benchmark": "B5", "method": CARRY_METHOD, "seed": seed, "size": size,
           "available": {"merged": len(groups), "merged_verbatim": len(verbatim), "near_misses": len(near)},
           "pairs": {str(n): p for n, p in enumerate(pairs, 1)}}
    return "\n".join(lines), key


def write(conn, out: Path, seed: int = 5, size: int = SIZE) -> dict:
    """Write sheet.md and key.json into `out`. It won't write over a sheet: marks would be lost."""
    out.mkdir(parents=True, exist_ok=True)
    sheet, key = out / "sheet.md", out / "key.json"
    if sheet.exists() or key.exists():
        raise SystemExit(f"{out} already has a sheet; score it, or pick another folder")
    text, k = build(conn, seed, size)
    sheet.write_text(text)
    key.write_text(json.dumps(k, indent=2))
    sets = [p["set"] for p in k["pairs"].values()]
    return {"sheet": str(sheet), "key": str(key), "merged": sets.count("merged"), "near": sets.count("near"),
            "available": k["available"]}


def marks(text: str) -> dict:
    """Pair number -> my mark, from a filled-in sheet. A blank or unreadable mark is missing."""
    out, n = {}, None
    for line in text.splitlines():
        if m := re.match(r"##\s+(\d+)\s*$", line):
            n = m.group(1)
        elif n and (m := re.match(r"mark:\s*(.*)$", line.strip(), re.I)):
            got = MARKS.get(m.group(1).strip().strip("`").lower())
            if got:
                out[n] = got
    return out


def score(folder: Path) -> dict:
    key = json.loads((folder / "key.json").read_text())
    got = marks((folder / "sheet.md").read_text())
    n = {"merged": 0, "near": 0}
    false_merges, false_splits, unsure, missing = [], [], [], []
    true_merges = []
    for num, p in sorted(key["pairs"].items(), key=lambda kv: int(kv[0])):
        n[p["set"]] += 1
        mark = got.get(num)
        row = {"pair": int(num), "score": p["score"], "items": p["items"]}
        if mark is None:
            missing.append(int(num))
        elif mark == "unsure":
            unsure.append(row | {"set": p["set"]})
        elif p["set"] == "merged" and mark == "different":
            false_merges.append(row)
        elif p["set"] == "near" and mark == "same":
            false_splits.append(row)
        elif p["set"] == "merged":
            true_merges.append(p["score"])
    done, counted = not missing, len(false_merges) + sum(r["set"] == "merged" for r in unsure)
    return {"benchmark": "B5", "method": key["method"], "merged": n["merged"], "near_misses": n["near"],
            "false_merges": len(false_merges), "false_splits": len(false_splits), "unsure": len(unsure),
            "missing": missing, "bar": f"<= {BAR} false merges in {n['merged']}",
            "passes": counted <= BAR if done and n["merged"] >= key["size"] else None,
            "lowest_true_merge_score": min(true_merges, default=None),
            "highest_false_split_score": max((r["score"] for r in false_splits), default=None),
            "false_merge_pairs": false_merges, "false_split_pairs": false_splits, "unsure_pairs": unsure}
