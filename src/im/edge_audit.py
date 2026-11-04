"""B4 (docs/ROADMAP-v2.md): blind sheets of pairs of clusters for me to type by hand, the labels Jev is scored on.

What the pilot (20 pairs, 2026-11-02) settled: the answers stay supports, contradicts, extends, related and none,
with the matcher's `evolves` counted as `extends`; direction is cheap to give; and contradictions and supports
don't turn up among nearest neighbours, so the sheet goes looking for them. Marks must be mine, made blind: a
model's marks would score Jev against another model.

The pairs come in five sets:
- near: nearest neighbours by embedding among candidates and ideas (SPEC §5: embeddings nominate pairs), spread
  across the range of similarity rather than only the closest;
- evolves: pairs the matcher linked as one evolving into the other;
- apart: neighbours among candidates and ideas first said far apart in time, where a change of mind would show;
- levels: a candidate or idea next to a nearby concrete observation (anyone's), where support would show;
- far: pairs below the median similarity, so `none` is represented.
The sets are shuffled together, each pair's sides in random order, with no similarity or set shown. Each side shows
what was said first, then Claude's summary, marked as Claude's. The sheet quotes people: keep it, and the key, in
saved/ (deploy.md).
"""

import json
import random
import re
from pathlib import Path

import numpy as np

SIZE = 50
NEAR_K = 5
WIDE_K = 10    # how far the apart and levels sets look for neighbours
SHARE = {"evolves": 0.12, "apart": 0.16, "levels": 0.16, "far": 0.08}   # the rest of the sheet is near pairs
SETS = ("near", "evolves", "apart", "levels", "far")
CONCRETE = ("observation",)          # item kinds that are about a case, not a claim
CLAIMS = ("idea", "aphorism")
LABELS = {"supports": "supports", "sup": "supports", "contradicts": "contradicts", "con": "contradicts",
          "extends": "extends", "ext": "extends", "related": "related", "rel": "related", "none": "none",
          "same": "same", "unsure": "unsure", "?": "unsure"}
DIRECTED = ("supports", "extends")

HEADER = """# B4: typing edges (blind)

Each pair is two clusters from the lattice: what was said, then Claude's summary of it. Judge by what was said;
the summary can be wrong. How does A relate to B? After `mark:`, write one of:

- `supports` (`sup`): one is a reason to believe the other: evidence, an example, an argument. Test: if A is true,
  is B more believable, while still saying the same thing?
- `extends` (`ext`): one takes the other further: applies it, generalizes it, builds on it, or is a later version
  of it. Test: does A say something new that B made possible?
- `contradicts` (`con`): they can't both be held as stated, including a change of mind.
- `related` (`rel`): the same subject, but none of the above. Test: knowing one doesn't change how you read the
  other.
- `none`: no real connection.
- `same`: the same idea said twice (a merge the matcher missed).
- `unsure` (`?`).

For `supports` and `extends`, add `A>B` if A supports or extends B, or `B>A` the other way; leave it off if it
runs both ways or you can't say. Use `note:` for anything worth saying, such as a summary that misstates the quotes.
Mark it yourself, without a model's help: these marks are what Jev is scored against.
Then run `im audit-edges score` on this folder. "me" is me, "someone" anyone else.
"""


def _clusters(conn) -> list[tuple]:
    """Every cluster Lattice shows, with its embedding (under the model most were embedded with), whether it's mine
    (a candidate or idea), when it was first said, and the kind most of its captures are."""
    return conn.execute(
        """WITH v AS (SELECT model_version FROM im.idea_embeddings GROUP BY 1 ORDER BY count(*) DESC LIMIT 1),
           said AS (SELECT ev.idea_id, e.started_at AS at, i.kind FROM im.idea_evidence ev
                    JOIN im.items i USING (item_id) JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
                    UNION ALL
                    SELECT idea_id, said_at, NULL FROM im.observations WHERE idea_id IS NOT NULL)
           SELECT p.idea_id::text, e.embedding::text, m.maturity IN ('candidate', 'idea'),
                  (SELECT min(at) FROM said WHERE said.idea_id = p.idea_id),
                  (SELECT mode() WITHIN GROUP (ORDER BY kind) FROM said WHERE said.idea_id = p.idea_id)
           FROM pub.ideas p JOIN im.idea_maturity m USING (idea_id)
           JOIN im.idea_embeddings e ON e.idea_id = p.idea_id AND e.model_version = (SELECT model_version FROM v)
           ORDER BY p.idea_id""").fetchall()


def candidates(conn) -> dict[str, list[dict]]:
    """Every pair each set could draw from, as {"ideas": [a, b], "similarity": s}, a < b."""
    rows = _clusters(conn)
    mine = [n for n, r in enumerate(rows) if r[2]]
    if len(mine) < 2:
        return {s: [] for s in SETS}
    ids = [r[0] for r in rows]
    M = np.vstack([np.array(json.loads(r[1])) for r in rows])
    M = M / np.linalg.norm(M, axis=1, keepdims=True)
    S = M @ M.T
    np.fill_diagonal(S, -np.inf)
    pair = lambda i, j: {"ideas": sorted([ids[i], ids[j]]), "similarity": round(float(S[i, j]), 3)}  # noqa: E731
    by_sim = lambda ps: sorted(ps, key=lambda p: (-p["similarity"], p["ideas"]))  # noqa: E731

    def neighbours(k: int, among: list[int]) -> set[tuple[int, int]]:
        out = set()
        for i in mine:
            for j in [among[x] for x in np.argsort(-S[i, among])[:k]]:   # S[i, i] is -inf: never among them
                if j != i:
                    out.add(tuple(sorted((i, j))))
        return out

    near = neighbours(NEAR_K, mine)
    index = {x: n for n, x in enumerate(ids)}
    evolves = [pair(index[a], index[b]) for a, b in conn.execute(
        "SELECT a::text, b::text FROM im.idea_links WHERE kind = 'evolves' ORDER BY a, b")
        if a in index and b in index and rows[index[a]][2] and rows[index[b]][2]]
    wide = neighbours(WIDE_K, mine)
    gap = {(i, j): abs((rows[i][3] - rows[j][3]).days) for i, j in wide if rows[i][3] and rows[j][3]}
    longest = float(np.quantile(list(gap.values()), 0.75)) if gap else 0
    apart = [pair(i, j) for (i, j), d in gap.items() if d >= max(longest, 1)]
    levels = [pair(i, j) for i, j in neighbours(WIDE_K, list(range(len(ids))))
              if {rows[i][4] in CONCRETE, rows[j][4] in CONCRETE} == {True, False}
              and (rows[i][4] in CLAIMS or rows[j][4] in CLAIMS)]
    sub = S[np.ix_(mine, mine)][np.triu_indices(len(mine), 1)]
    median = float(np.median(sub))
    far = [pair(mine[a], mine[b]) for a in range(len(mine)) for b in range(a + 1, len(mine))
           if S[mine[a], mine[b]] < median and tuple(sorted((mine[a], mine[b]))) not in near]
    return {"near": by_sim(pair(i, j) for i, j in near), "evolves": evolves, "apart": by_sim(apart),
            "levels": by_sim(levels), "far": far}


def _spread(pairs: list[dict], n: int) -> list[dict]:
    """n pairs evenly across the list (sorted by similarity), so the sample covers the whole range."""
    if n >= len(pairs):
        return list(pairs)
    return [pairs[round(k * (len(pairs) - 1) / max(n - 1, 1))] for k in range(n)]


def _side(conn, idea_id: str) -> list[str]:
    """A cluster on the sheet: up to two things said, mine first, with when; then Claude's summary, and my note."""
    title, statement, note = conn.execute(
        "SELECT title, statement, my_note FROM im.idea_text WHERE idea_id = %s", (idea_id,)).fetchone()
    quotes = conn.execute(
        """SELECT quote, said_on, me FROM (
             SELECT DISTINCT ON (i.observation_id) i.quote, e.started_at::date AS said_on, s.speaker = 'me' AS me
             FROM im.idea_evidence ev JOIN im.items i USING (item_id)
             JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current
             JOIN im.observation_speaker s USING (observation_id)
             WHERE ev.idea_id = %(id)s ORDER BY i.observation_id, i.created_at) o
           UNION ALL
           SELECT quote, said_at::date, said_by = 'me' FROM im.observations WHERE idea_id = %(id)s
           ORDER BY me DESC, said_on, quote LIMIT 2""", {"id": idea_id}).fetchall()
    lines = [f"> \"{q}\" ({'me' if me else 'someone'}, {on})" for q, on, me in quotes]
    lines += [f"   Claude's summary: {title}. {statement}"] + ([f"   My note: {note}"] if note else [])
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

    for kind in ("evolves", "apart", "levels"):
        take(rng.sample(pool[kind], len(pool[kind])), round(SHARE[kind] * size), kind)
    take(rng.sample(pool["far"], min(len(pool["far"]), 10 * size)), round(SHARE["far"] * size), "far")
    near = [p for p in pool["near"] if tuple(p["ideas"]) not in taken]   # an evolves pair is often a near one too
    take(_spread(near, size - len(picked)), size - len(picked), "near")
    for kind in SETS:   # a small lattice: whatever is left, so the sheet still has `size` pairs if it can
        take(pool[kind], size - len(picked), kind)
    rng.shuffle(picked)
    for p in picked:
        p["sides"] = rng.sample(p["ideas"], 2)
    lines = [HEADER]
    for n, p in enumerate(picked, 1):
        lines += [f"## {n}", ""]
        for side, idea in zip("AB", p["sides"]):
            first, *rest = _side(conn, idea) or ["(nothing said on record)"]
            lines += [f"{side}. {first.strip()}", *[f"   {x}" if x.startswith(">") else x for x in rest], ""]
        lines += ["mark: ", "note: ", ""]
    key = {"benchmark": "B4", "seed": seed, "size": size, "near_k": NEAR_K, "wide_k": WIDE_K,
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
            "by_set": {s: sets.count(s) for s in SETS}, "available": k["available"]}


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
    """My marks, tallied: how often each label came up, per set, how often I gave a direction, and my notes."""
    key = json.loads((folder / "key.json").read_text())
    got = marks((folder / "sheet.md").read_text())
    counts = {s: {} for s in SETS}
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
    mine = {num: {"label": m.get("label"), "note": m.get("note")} for num, m in got.items()}
    drafts = {d.stem.removeprefix("draft-"): agreement(mine, json.loads(d.read_text()))
              for d in sorted(folder.glob("draft-*.json"))}
    return {"benchmark": key["benchmark"], "marked": len(labels), "missing": missing, "labels": total,
            "by_set": {s: c for s, c in counts.items() if c},
            "direction_given": f"{directed['given']} of {directed['of']}", "notes": notes,
            **({"drafts": drafts} if drafts else {}), "marks": labels}


# --- A model's marks on the same sheet (B4's agreement test) ---------------------------------------------------------

DRAFT_SYSTEM = """You type the relationship between two clusters from my idea lattice, captured from my \
conversations. Each side shows what was said, then a summary of it; judge by what was said, since the summary can \
be wrong. "me" is me, "someone" anyone else. Choose one label:
- supports: one is a reason to believe the other: evidence, an example, an argument. Test: if A is true, is B more \
believable, while still saying the same thing?
- extends: one takes the other further: applies it, generalizes it, builds on it, or is a later version of it. \
Test: does A say something new that B made possible?
- contradicts: they can't both be held as stated, including a change of mind.
- related: the same subject, but none of the above. Test: knowing one doesn't change how you read the other.
- none: no real connection. Sharing a broad area (relationships, emotions, work) isn't a connection.
- same: the same idea said twice.
- unsure.
For supports and extends, give the direction: "A>B" if A supports or extends B, "B>A" the other way, or "both" if \
it runs both ways or you can't say; otherwise "none".
Separately, say whether the two are analogous: about different subjects, but with the same underlying structure \
(the same move, pattern or mechanism in two domains). If they are, name the shared structure in a few words; \
otherwise leave it empty. Don't evaluate the ideas."""

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string", "enum": ["supports", "extends", "contradicts", "related", "none",
                                                          "same", "unsure"]},
                   "direction": {"type": "string", "enum": ["A>B", "B>A", "both", "none"]},
                   "analogy": {"type": "string"}},
    "required": ["label", "direction", "analogy"], "additionalProperties": False,
}
CORE = ("none", "related", "extends")   # what B4's revised bar would score (macro-F1)


def _pairs_as_shown(text: str) -> dict[str, str]:
    """Pair number -> what the sheet showed for it, without my mark or note: what the model sees is what I saw."""
    out, n, lines = {}, None, []
    for line in text.split("\n## ")[1:]:
        head, _, body = line.partition("\n")
        n = head.strip()
        lines = [x for x in body.splitlines() if not re.match(r"\s*(mark|note):", x, re.I)]
        out[n] = "\n".join(lines).strip()
    return out


def draft(conn, client, folder: Path, model: str = "claude-opus-5-5", effort: str = "low") -> dict:
    """A model marks every pair on the sheet, blind to my marks, into draft-<model>-<effort>.json. Each request goes
    to the egress log, like every other call that sends what was said."""
    from im import themes
    from im.extract import _cost

    key = json.loads((folder / "key.json").read_text())
    shown = _pairs_as_shown((folder / "sheet.md").read_text())
    out_path = folder / f"draft-{model}-{effort}.json"
    got = json.loads(out_path.read_text()) if out_path.exists() else {}
    pv = themes.version(DRAFT_SYSTEM, DRAFT_SCHEMA, model, effort)
    fallback = {"betas": ["server-side-fallback-2026-06-01"], "fallbacks": [{"model": "claude-opus-4-8"}]} \
        if model.startswith("claude-opus") else {}
    cost = 0.0
    for num, p in sorted(key["pairs"].items(), key=lambda kv: int(kv[0])):
        if num in got or num not in shown:
            continue
        r = client.beta.messages.create(
            model=model, max_tokens=4000, system=DRAFT_SYSTEM, messages=[{"role": "user", "content": shown[num]}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": DRAFT_SCHEMA}}, **fallback)
        served = getattr(r, "model", model) or model
        segs = [s for (s,) in conn.execute(
            """SELECT DISTINCT unnest(i.source_segment_ids) FROM im.idea_evidence ev JOIN im.items i USING (item_id)
               WHERE ev.idea_id = ANY(%s::uuid[])""", (p["ideas"],))]
        c = _cost(served, r.usage)
        conn.execute(
            """INSERT INTO im.egress_log (episode_id, segment_ids, model, prompt_version, privacy_policy_version,
                 payload, payload_sha256, request_id, stop_reason, served_by, input_tokens, output_tokens, cost_usd)
               VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (segs, model, pv, _policy(), shown[num], _sha(shown[num]), getattr(r, "_request_id", None),
             r.stop_reason, served, r.usage.input_tokens, r.usage.output_tokens, c))
        cost += c or 0.0
        if r.stop_reason == "refusal":
            got[num] = {"label": "unsure", "direction": "none", "analogy": "", "refused": True}
        else:
            got[num] = json.loads(next(b.text for b in r.content if b.type == "text"))
        out_path.write_text(json.dumps(got, indent=2))   # as it goes: a rerun picks up where this stopped
    return {"draft": str(out_path), "pairs": len(got), "cost_usd": round(cost, 4), "prompt_version": pv}


def _policy() -> str:
    from im import egress
    return egress.POLICY_VERSION


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def agreement(mine: dict, theirs: dict) -> dict:
    """How a model's marks compare with mine, on the pairs I didn't mark unsure.

    mine: pair -> {"label", "note"}; theirs: pair -> {"label", "analogy"}."""
    pairs = [n for n, m in mine.items() if m.get("label") not in (None, "unsure") and n in theirs]
    confusion: dict = {}
    for n in pairs:
        row = confusion.setdefault(mine[n]["label"], {})
        row[theirs[n]["label"]] = row.get(theirs[n]["label"], 0) + 1
    f1 = {}
    for label in CORE:
        tp = sum(mine[n]["label"] == label and theirs[n]["label"] == label for n in pairs)
        fp = sum(mine[n]["label"] != label and theirs[n]["label"] == label for n in pairs)
        fn = sum(mine[n]["label"] == label and theirs[n]["label"] != label for n in pairs)
        f1[label] = round(2 * tp / (2 * tp + fp + fn), 3) if tp + fp + fn else None
    scored = [v for v in f1.values() if v is not None]
    invented = [int(n) for n in pairs if theirs[n]["label"] in ("supports", "contradicts")
                and mine[n]["label"] not in ("supports", "contradicts")]
    my_analogies = {n for n, m in mine.items() if (m.get("note") or "").lower().startswith("analogy")}
    their_analogies = {n for n in theirs if (theirs[n].get("analogy") or "").strip()}
    return {"pairs": len(pairs),
            "agree": round(sum(mine[n]["label"] == theirs[n]["label"] for n in pairs) / len(pairs), 3)
            if pairs else None,
            "f1": f1, "macro_f1_core": round(sum(scored) / len(scored), 3) if scored else None,
            "invented_supports_or_contradicts": sorted(invented),
            "analogies": {"mine": sorted(map(int, my_analogies)), "theirs": sorted(map(int, their_analogies)),
                          "both": sorted(map(int, my_analogies & their_analogies))},
            "confusion_mine_to_theirs": confusion}
