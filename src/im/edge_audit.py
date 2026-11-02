"""B4 pilot (docs/ROADMAP-v2.md): a small blind sheet of pairs of clusters for me to type by hand, before the 200
pairs B4 needs.

The pilot is for settling the labels, not for scoring a model: whether the five answers (supports, contradicts,
extends, related, none) are the right ones and can be told apart, whether direction matters, and how rare
contradictions are among the pairs Jev will actually see. Its marks are kept, and count towards the 200.

The pairs come from the clusters Jev would type, candidates and ideas, in three sets:
- near: each cluster's nearest neighbours by embedding (SPEC §5: embeddings nominate pairs), spread across the
  range of similarity rather than only the closest;
- evolves: pairs the matcher already linked as one evolving into the other;
- far: pairs below the median similarity, so `none` is represented.
The sets are shuffled together, each pair's sides in random order, with no similarity or set shown. The sheet
quotes people: keep it, and the key, in saved/ (deploy.md).
"""

import json
import random
import re
from pathlib import Path

import numpy as np

SIZE = 20
NEAR_K = 5
SHARE = {"evolves": 3, "far": 3}   # the rest of the sheet is near pairs
LABELS = {"supports": "supports", "sup": "supports", "contradicts": "contradicts", "con": "contradicts",
          "extends": "extends", "ext": "extends", "related": "related", "rel": "related", "none": "none",
          "same": "same", "unsure": "unsure", "?": "unsure"}
DIRECTED = ("supports", "extends")

HEADER = """# B4 pilot: typing edges (blind)

Each pair is two clusters from the lattice. How does A relate to B? After `mark:`, write one of:

- `supports` (`sup`): one gives a reason, evidence or an example for the other.
- `contradicts` (`con`): they can't both be held as stated.
- `extends` (`ext`): one takes the other further: applies it, generalizes it, or builds on it.
- `related` (`rel`): the same subject, but none of the above.
- `none`: no real connection.
- `same`: the same idea said twice (a merge the matcher missed).
- `unsure` (`?`).

For `supports` and `extends`, add `A>B` if A supports or extends B, or `B>A` the other way; leave it off if it
runs both ways or you can't say. Use `note:` for anything the labels didn't fit: that's what the pilot is for.
Then run `im audit-edges score` on this folder. Quotes are what was said; "me" is me, "someone" anyone else.
"""


def _pool(conn) -> tuple[list, np.ndarray]:
    """Candidates and ideas Lattice shows, with their embeddings under the model most of them were embedded with."""
    rows = conn.execute(
        """WITH v AS (SELECT model_version FROM im.idea_embeddings GROUP BY 1 ORDER BY count(*) DESC LIMIT 1)
           SELECT p.idea_id, e.embedding::text FROM pub.ideas p
           JOIN im.idea_maturity m USING (idea_id)
           JOIN im.idea_embeddings e ON e.idea_id = p.idea_id AND e.model_version = (SELECT model_version FROM v)
           WHERE m.maturity IN ('candidate', 'idea') ORDER BY p.idea_id""").fetchall()
    if not rows:
        return [], np.zeros((0, 1))
    M = np.vstack([np.array(json.loads(r[1])) for r in rows])
    return [str(r[0]) for r in rows], M / np.linalg.norm(M, axis=1, keepdims=True)


def candidates(conn) -> dict[str, list[dict]]:
    """Every pair each set could draw from, as {"ideas": [a, b], "similarity": s}, a < b."""
    ids, M = _pool(conn)
    if len(ids) < 2:
        return {"near": [], "evolves": [], "far": []}
    S = M @ M.T
    np.fill_diagonal(S, -np.inf)
    pair = lambda i, j: {"ideas": sorted([ids[i], ids[j]]), "similarity": round(float(S[i, j]), 3)}  # noqa: E731
    near = {}
    for i in range(len(ids)):
        for j in np.argsort(-S[i])[:NEAR_K]:
            near[tuple(sorted((i, int(j))))] = None
    index = {x: n for n, x in enumerate(ids)}
    evolves = [pair(index[str(a)], index[str(b)]) for a, b in conn.execute(
        "SELECT a, b FROM im.idea_links WHERE kind = 'evolves' ORDER BY a, b") if str(a) in index and str(b) in index]
    upper = S[np.triu_indices(len(ids), 1)]
    median = float(np.median(upper))
    far = [pair(i, j) for i in range(len(ids)) for j in range(i + 1, len(ids))
           if S[i, j] < median and (i, j) not in near]
    return {"near": sorted((pair(i, j) for i, j in near), key=lambda p: (-p["similarity"], p["ideas"])),
            "evolves": evolves, "far": far}


def _spread(pairs: list[dict], n: int) -> list[dict]:
    """n pairs evenly across the list (sorted by similarity), so the sample covers the whole range."""
    if n >= len(pairs):
        return list(pairs)
    return [pairs[round(k * (len(pairs) - 1) / max(n - 1, 1))] for k in range(n)]


def _side(conn, idea_id: str) -> list[str]:
    """A cluster on the sheet: its title and statement, and up to two quotes, mine first, with when they were said."""
    title, statement, note = conn.execute(
        "SELECT title, statement, my_note FROM im.idea_text WHERE idea_id = %s", (idea_id,)).fetchone()
    quotes = conn.execute(
        """SELECT o.quote, o.said_on, o.me FROM (
             SELECT DISTINCT ON (i.observation_id) i.quote, e.started_at::date AS said_on, s.speaker = 'me' AS me
             FROM im.idea_evidence ev JOIN im.items i USING (item_id)
             JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
             JOIN im.observation_speaker s USING (observation_id)
             WHERE ev.idea_id = %s ORDER BY i.observation_id, i.created_at) o
           ORDER BY o.me DESC, o.said_on, o.quote LIMIT 2""", (idea_id,)).fetchall()
    lines = [f"**{title}**: {statement}"] + ([f"   (my note: {note})"] if note else [])
    lines += [f"   > \"{q}\" ({'me' if me else 'someone'}, {on})" for q, on, me in quotes]
    return lines


def build(conn, seed: int = 4, size: int = SIZE) -> tuple[str, dict]:
    """The sheet's text and its key."""
    rng = random.Random(seed)
    pool = candidates(conn)
    taken, picked = set(), []

    def take(pairs: list[dict], n: int, kind: str) -> None:
        for p in pairs:
            if n <= 0:
                return
            if tuple(p["ideas"]) not in taken:
                taken.add(tuple(p["ideas"]))
                picked.append(p | {"set": kind})
                n -= 1

    take(rng.sample(pool["evolves"], len(pool["evolves"])), SHARE["evolves"], "evolves")
    take(rng.sample(pool["far"], min(len(pool["far"]), 10 * size)), SHARE["far"], "far")
    near = [p for p in pool["near"] if tuple(p["ideas"]) not in taken]   # an evolves pair is often a near one too
    take(_spread(near, size - len(picked)), size - len(picked), "near")
    rng.shuffle(picked)
    for p in picked:
        p["sides"] = rng.sample(p["ideas"], 2)
    lines = [HEADER]
    for n, p in enumerate(picked, 1):
        lines += [f"## {n}", ""]
        for side, idea in zip("AB", p["sides"]):
            first, *rest = _side(conn, idea)
            lines += [f"{side}. {first}", *rest, ""]
        lines += ["mark: ", "note: ", ""]
    key = {"benchmark": "B4-pilot", "seed": seed, "size": size, "near_k": NEAR_K,
           "available": {k: len(v) for k, v in pool.items()},
           "pairs": {str(n): p for n, p in enumerate(picked, 1)}}
    return "\n".join(lines), key


def write(conn, out: Path, seed: int = 4, size: int = SIZE) -> dict:
    """Write sheet.md and key.json into `out`. It won't write over a sheet: marks would be lost."""
    out.mkdir(parents=True, exist_ok=True)
    sheet, key = out / "sheet.md", out / "key.json"
    if sheet.exists() or key.exists():
        raise SystemExit(f"{out} already has a sheet; score it, or pick another folder")
    text, k = build(conn, seed, size)
    sheet.write_text(text)
    key.write_text(json.dumps(k, indent=2))
    sets = [p["set"] for p in k["pairs"].values()]
    return {"sheet": str(sheet), "key": str(key), "pairs": len(sets),
            "by_set": {s: sets.count(s) for s in ("near", "evolves", "far")}, "available": k["available"]}


def marks(text: str) -> dict:
    """Pair number -> {"label", "direction", "note"} from a filled-in sheet. A blank or unreadable mark is missing."""
    out, n = {}, None
    for line in text.splitlines():
        line = line.strip()
        if m := re.match(r"##\s+(\d+)\s*$", line):
            n = m.group(1)
        elif n and (m := re.match(r"mark:\s*`?([a-z?]+)`?\s*([AB]\s*>\s*[AB])?", line, re.I)):
            label = LABELS.get(m.group(1).lower())
            if label:
                arrow = re.sub(r"\s", "", m.group(2) or "").upper()
                out.setdefault(n, {})["label"] = label
                out[n]["direction"] = arrow if label in DIRECTED and arrow in ("A>B", "B>A") else None
        elif n and (m := re.match(r"note:\s*(.+)$", line, re.I)):
            out.setdefault(n, {})["note"] = m.group(1).strip()
    return {k: v for k, v in out.items() if "label" in v or "note" in v}


def score(folder: Path) -> dict:
    """What the pilot says about the labels: how often each came up, per set, and where they didn't fit."""
    key = json.loads((folder / "key.json").read_text())
    got = marks((folder / "sheet.md").read_text())
    counts = {s: {} for s in ("near", "evolves", "far")}
    missing, notes, directed, labels = [], [], {"given": 0, "of": 0}, []
    for num, p in sorted(key["pairs"].items(), key=lambda kv: int(kv[0])):
        mark = got.get(num, {})
        label = mark.get("label")
        if label is None:
            missing.append(int(num))
        else:
            counts[p["set"]][label] = counts[p["set"]].get(label, 0) + 1
            labels.append({"pair": int(num), "set": p["set"], "similarity": p["similarity"], "label": label,
                           "direction": mark.get("direction"), "a": p["sides"][0], "b": p["sides"][1]})
            if label in DIRECTED:
                directed["of"] += 1
                directed["given"] += mark.get("direction") is not None
        if mark.get("note"):
            notes.append({"pair": int(num), "label": label, "note": mark["note"]})
    total = {}
    for c in counts.values():
        for label, k in c.items():
            total[label] = total.get(label, 0) + k
    near = sum(counts["near"].values())
    return {"benchmark": "B4-pilot", "marked": len(labels), "missing": missing, "labels": total, "by_set": counts,
            "contradicts_among_near": f"{counts['near'].get('contradicts', 0)} of {near}",
            "direction_given": f"{directed['given']} of {directed['of']}", "notes": notes, "marks": labels}
