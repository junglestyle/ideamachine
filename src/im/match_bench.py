"""B1: could Jev make the matcher's decisions? Opus's logged decisions, replayed through Jev (ROADMAP-v2 B1, M4).

The matcher (lattice.match_stage) shows Opus each new capture and the ideas nearest it, and Opus answers new, the same
as one, or develops one. Each request is logged with exactly what was sent. This reads them back and asks Jev about
the capture against each candidate: is it the same idea, and does it develop it (jev.QUESTIONS["match"], two wordings
each). Scoring decides as the matcher would, at a grid of cut-offs: the same as the likeliest candidate if that's
likely enough, else developing the likeliest, else new. Agreement with Opus is B1's first measure. Its bar also
needs the `same_as` decisions checked by me, since agreeing with Opus only shows a switch is safe where Opus was
right.

A decision is replayed only if its request is still logged in full (forgetting blanks a payload). Which candidate
Opus chose is found by text: the idea it joined (or developed) as its text reads now, or as Claude first wrote it.
If neither reads like any candidate shown, as after a later correction, the decision counts for its kind only.

Requests go out under policy C, names said pseudonymized, each logged with the segments of the capture and of every
candidate it could be placed with. Jev's answers are kept in the folder (jev-answers.json), so a rerun picks up where
one stopped and scoring sends nothing.
"""

import json
import re
from pathlib import Path

from im import egress, probe_audit

ITEM = re.compile(r"^Item \((?P<kind>[^,]+), said by (?P<who>[^)]+)\): “(?P<quote>.*)” — (?P<gist>.*)$", re.S)
LINE = re.compile(r"^(\d+)\. (.*)$")
NOTE = " (my note: "
SAME, DEVELOPS = ("same", "same_v2"), ("develops", "develops_v2")
CUTS = (0.4, 0.5, 0.6, 0.7, 0.8)
PRECISION = 0.95   # B1's bar for `same_as`; the best cut-offs are the most agreement at or above it


def parse(payload: str) -> tuple[dict, list[dict]] | None:
    """The capture and the candidates as the matcher showed them (lattice._text), or None if it can't be read."""
    head, sep, rest = payload.partition("\n\nCandidates:\n")
    m = ITEM.match(head)
    if not sep or not m:
        return None
    candidates: list[dict] = []
    for line in rest.splitlines():
        n = LINE.match(line)
        if n and int(n.group(1)) == len(candidates) + 1:
            candidates.append({"text": n.group(2)})
        elif candidates:   # a statement that ran onto another line
            candidates[-1]["text"] += "\n" + line
    for c in candidates:
        if c["text"].endswith(")") and NOTE in c["text"]:
            c["text"], note = c["text"].rsplit(NOTE, 1)
            c["note"] = note[:-1]
    item = {"kind": m["kind"], "said_by": m["who"], "quote": m["quote"], "summary": m["gist"]}
    return item, candidates


def decisions(conn) -> list[dict]:
    """Every logged decision of the matcher that can be replayed, with which candidate Opus chose (None for new;
    "?" when it can't be told) and the ideas each candidate could be."""
    texts: dict[str, set] = {}
    for idea, title, statement, c_title, c_statement in conn.execute(
            "SELECT idea_id::text, title, statement, claude_title, claude_statement FROM im.idea_text"):
        for t in {f"{title}: {statement}", f"{c_title}: {c_statement}"}:
            texts.setdefault(t, set()).add(idea)
    out = []
    for egress_id, decision, idea, from_idea, payload, segs in conn.execute(
            """SELECT g.egress_id, m.decision, m.idea_id::text,
                      (SELECT l.to_id::text FROM im.lineage l WHERE l.from_id = m.idea_id AND l.kind = 'evolves' LIMIT 1),
                      g.payload, g.segment_ids::text[]
               FROM im.item_matches m JOIN im.egress_log g USING (egress_id)
               WHERE m.judged_by LIKE 'match-%%' AND m.decision IN ('new', 'same_as', 'evolves')
                 AND g.payload IS NOT NULL ORDER BY g.egress_id"""):
        shown = parse(payload)
        if shown is None or not shown[1]:
            continue
        item, candidates = shown
        for c in candidates:
            c["ideas"] = sorted(texts.get(c["text"], ()))
        target = {"same_as": idea, "evolves": from_idea}.get(decision)
        chosen = None if decision == "new" else next(
            (n for n, c in enumerate(candidates, 1) if target in c["ideas"]), "?")
        out.append({"egress_id": egress_id, "decision": decision, "chosen": chosen, "item": item,
                    "candidates": candidates, "segments": segs})
    return out


def run(conn, client, folder: Path, scrubber=None, limit: int | None = None, monthly_cap_usd: float = 20.0) -> dict:
    """Ask Jev about each decision's capture against each of its candidates, "egress_id n" -> {question: p}."""
    folder.mkdir(parents=True, exist_ok=True)
    found = decisions(conn)[:limit] if limit else decisions(conn)
    (folder / "key.json").write_text(json.dumps({"benchmark": "B1", "decisions": found}, indent=1))
    jobs = {}
    for d in found:
        for n, c in enumerate(d["candidates"], 1):
            state = {"a": d["item"], "b": {"idea": c["text"], **({"my_note": c["note"]} if "note" in c else {})}}
            jobs[f"{d['egress_id']} {n}"] = (
                lambda s=state: probe_audit._scrubbed(s, scrubber)[0] if scrubber else s,
                lambda d=d, c=c: sorted(set(d["segments"]) | {str(s) for s in egress.segments_of(conn, c["ideas"])}))
    got = probe_audit.ask_all(conn, client, "match", jobs, folder / "jev-answers.json",
                              scrubber.policy if scrubber else egress.POLICY_VERSION, monthly_cap_usd, probes=False)
    return {"decisions": len(found), "asked": len(jobs), "answered": sum(k in got for k in jobs)}


def _mean(a: dict, names) -> float:
    return sum(a[k] for k in names) / len(names)


def decide(answers: dict[int, dict], cut_same: float, cut_develops: float) -> tuple[str, int | None]:
    """The matcher's decision from Jev's answers about each candidate n: the likeliest same if it's at the cut, else
    the likeliest development if it is, else new. Ties go to the nearer candidate, as the matcher lists them."""
    for kind, names, cut in (("same_as", SAME, cut_same), ("evolves", DEVELOPS, cut_develops)):
        p = {n: _mean(a, names) for n, a in answers.items()}
        best = max(p, key=lambda n: (p[n], -n), default=None)
        if best is not None and p[best] >= cut:
            return kind, best
    return "new", None


def score(folder: Path) -> dict:
    """The best pair of cut-offs in full; agreement and same_as precision at every pair, as two small tables (a row
    per cut-off on "same", a column per cut-off on "develops"); and how well each wording picks out the candidate
    Opus joined (AUC). Over the decisions Jev answered in full."""
    key = json.loads((folder / "key.json").read_text())
    got = json.loads((folder / "jev-answers.json").read_text())
    done = []
    for d in key["decisions"]:
        answers = {n: got.get(f"{d['egress_id']} {n}") for n in range(1, len(d["candidates"]) + 1)}
        if all(answers.values()):
            done.append((d, answers))
    grid = {(cs, cd): _agreement([(d, decide(a, cs, cd)) for d, a in done]) for cs in CUTS for cd in CUTS}
    best = max(grid, key=lambda k: ((grid[k]["same_as_precision"] or 0) >= PRECISION, grid[k]["agree"] or 0))
    table = lambda field: {"develops": "  ".join(f"{cd:4}" for cd in CUTS)} | {  # noqa: E731
        f"same {cs}": "  ".join("   -" if grid[cs, cd][field] is None else f"{grid[cs, cd][field]:.2f}" for cd in CUTS)
        for cs in CUTS}
    return {"benchmark": "B1", "decisions": len(key["decisions"]), "answered": len(done),
            "by_opus": {k: sum(d["decision"] == k for d, _ in done) for k in ("new", "same_as", "evolves")},
            "chosen_unknown": sum(d["chosen"] == "?" for d, _ in done),
            "best": {"same/develops": f"{best[0]}/{best[1]}", **grid[best]},
            "agree": table("agree"), "same_as_precision": table("same_as_precision"), "auc": _auc(done)}


def _agreement(pairs: list[tuple[dict, tuple]]) -> dict:
    """How often Jev's decision is Opus's: the kind, and the kind with the same candidate where that's known."""
    rate = lambda a, b: round(a / b, 3) if b else None  # noqa: E731
    kind = sum(d["decision"] == j[0] for d, j in pairs)
    known = [(d, j) for d, j in pairs if d["chosen"] != "?"]
    exact = sum(d["decision"] == j[0] and d["chosen"] == j[1] for d, j in known)
    jev_same = [(d, j) for d, j in known if j[0] == "same_as"]
    opus_same = [(d, j) for d, j in known if d["decision"] == "same_as"]
    hit = sum(d["decision"] == "same_as" and d["chosen"] == j[1] for d, j in jev_same)
    confusion: dict = {}
    for d, j in pairs:
        confusion.setdefault(d["decision"], {}).setdefault(j[0], 0)
        confusion[d["decision"]][j[0]] += 1
    return {"agree_kind": rate(kind, len(pairs)), "agree": rate(exact, len(known)),
            "same_as_precision": rate(hit, len(jev_same)), "same_as_recall": rate(hit, len(opus_same)),
            "jev_same_as": len(jev_same), "confusion": confusion}


def _auc(done: list[tuple[dict, dict]]) -> dict:
    """Per wording, the chance that the candidate Opus joined (or developed) scores above one it passed over, ties
    half: whether the wording picks out the right idea. Over decisions where Opus chose a known candidate."""
    out = {}
    for kind, names in (("same_as", SAME), ("evolves", DEVELOPS)):
        rows = [(d, a) for d, a in done if d["decision"] == kind and isinstance(d["chosen"], int)]
        for name in (*names, "mean"):
            p = (lambda a: _mean(a, names)) if name == "mean" else (lambda a, name=name: a[name])
            wins = total = 0
            for d, a in rows:
                yes = p(a[d["chosen"]])
                for n, other in a.items():
                    if n != d["chosen"]:
                        wins += (yes > p(other)) + 0.5 * (yes == p(other))
                        total += 1
            out[f"{kind}:{name}"] = round(wins / total, 3) if total else None
    return out
