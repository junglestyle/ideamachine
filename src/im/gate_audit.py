"""Setting the capture gate's cuts from marked captures (decision 0012): models draft the marks, I review where they
disagree.

The sheet holds captures from one trial read that Jev has answered about (im.gate), half of them ones Jev gives 0.5 or
more on some deciding check and half the rest, so both sides of every cut are on it. Each gets two marks:

- gist: `ok`; `past` when it goes past what its lines say (adds, misstates, overstates or completes them); or
  `unclear` when it points at something the transcript names without naming it. Either is repaired;
- capture: `keep`, or `drop` when the point isn't mine (someone else's I only go along with) or I voice it only to
  mock it.

Two chat models from different families draft both marks (`draft`); `review` lists only the marks they disagree on
that I haven't made myself, on review.md; a mark they agree on stands. `score` then says how well each of Jev's checks
ranks the marks (AUC) and suggests each decision's cut: for a repair, the highest cut that still catches 90% of the
gists marked past or unclear (a repair costs a request); for a drop, the lowest at which 90% of what it drops is
marked drop (a drop loses a capture). A sheet made before a check was asked scores on the checks its answers have.

The sheet sends nothing; a draft sends each capture, names said pseudonymized as the trial's payload had them.
"""

import json
import random
import re
from pathlib import Path

from im import egress, gate, jev, llm, names, themes, trial

SIZE = 80
FLAGGED_AT = 0.5
TARGET = 0.9      # the repair cut's recall, the drop cut's precision
CUTS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
LABELS = {"gist": ("ok", "past", "unclear"), "capture": ("keep", "drop")}
REPAIRED = ("past", "unclear")
UNSURE = ("?", "unsure")
HEADER = """# Captures for the gate (blind)

Each item is one capture from an extraction trial: the transcript around it, the lines it cites, the quote it took and
its summary. "me" is me; S1, S2... are other people.

- `gist:` write `ok`, or `past` if the summary goes past what the cited lines say: it adds an inference, conclusion,
  motive or outcome, misstates them, says it more strongly, broadly or surely, or finishes a thought they leave
  unfinished. Compressing is fine. Write `unclear` if it doesn't go past them but points at something the transcript
  names without naming it ("that kind of overview", "my approach", "it").
- `capture:` write `keep`, or `drop` if the point is someone else's that I only agree with, answer or repeat back, or
  one I voice only to mock or reject it. Passing on a view as worth telling is mine.

`?` for unsure; a note is optional. You don't have to mark this sheet: the models draft it, and `im audit-gate
review` lists only where they disagree. A mark made here wins over theirs.
"""
DRAFT_SYSTEM = """One capture from my conversations, made by a model reading the transcript: the lines around it, \
the lines it cites, the quote it took, and the summary (gist) it wrote. "me" is me; S1, S2 and so on are other \
people; codes like P-ABCDE stand for names.

Two questions.

gist: does the gist go past what the cited lines say? Answer past if it adds an inference, a conclusion, a motive or \
an outcome that isn't there; misstates or contradicts them; says it more strongly, more broadly or more surely (a \
dropped hedge, one case turned into a rule); or finishes a thought the lines leave unfinished. Compressing what was \
said is fine, and so is framing an answer with the question it answers. If it doesn't go past them, answer unclear \
if it points at something the transcript names without naming it ("that kind of overview", "my approach", "it"), so \
that someone who didn't hear the conversation couldn't tell what it means; otherwise ok.

capture: is this my thought, meant? Answer drop if the point is someone else's that I only agree with, answer or \
repeat back, or one I voice only to mock, parody or reject it. Passing on someone's view as worth telling counts as \
mine. Otherwise keep.

For each, say why in a few words. Don't evaluate the ideas."""
DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"gist": {"type": "string", "enum": list(LABELS["gist"])}, "gist_why": {"type": "string"},
                   "capture": {"type": "string", "enum": ["keep", "drop"]}, "capture_why": {"type": "string"}},
    "required": ["gist", "gist_why", "capture", "capture_why"], "additionalProperties": False,
}


def _shown(s: dict) -> str:
    return (f"Around it:\n{s['context']}\n\nCited lines:\n{s['lines']}\n\nQuote: \"{s['quote']}\"\n"
            f"Summary: {s['gist']}")


def _capture(c: dict) -> dict:
    return {"quote": c["quote"], "gist": c["gist"], "source_segment_ids": c["segments"]}


def write(conn, out: Path, client, read: tuple[str, str], policy: str, seed: int = 13, size: int = SIZE) -> dict:
    """The sheet and its key: `size` captures of `read` (prompt_version, model) Jev has answered about (`client`'s
    question set), half flagged at FLAGGED_AT on a deciding check, half not."""
    out.mkdir(parents=True, exist_ok=True)
    sheet, key = out / "sheet.md", out / "key.json"
    if sheet.exists() or key.exists():
        raise SystemExit(f"{out} already has a sheet; score it, or pick another folder")
    kept = jev.kept(conn, jev.question_set(client, "gate"))
    caps = {"flagged": [], "not_flagged": []}
    for eid, (sha, items, _) in sorted(trial._reads(conn, *read, policy).items(), key=lambda kv: str(kv[0])):
        for it in items:
            if (a := kept.get(gate.key(sha, it))) is None:
                continue
            flagged = max(a[q] for q in gate.GIST + gate.DROP) >= FLAGGED_AT
            caps["flagged" if flagged else "not_flagged"].append(
                {"episode": str(eid), "payload_sha256": sha, "quote": it["quote"], "gist": it["gist"],
                 "segments": it["source_segment_ids"], "answers": a})
    if not any(caps.values()):
        raise SystemExit(f"Jev hasn't answered about {read[1]}'s captures yet: im gate <prompt> --model {read[1]}")
    rng = random.Random(seed)
    picked = [c | {"set": s} for s, cs in caps.items() for c in rng.sample(cs, min(size // 2, len(cs)))]
    rng.shuffle(picked)
    lines = [HEADER]
    for num, c in enumerate(picked, 1):
        local = egress.build(conn, c["episode"])   # as I'd read it: names as said
        lines += [f"## {num}", "", _shown(gate.state(local, _capture(c))), "", "gist: ", "capture: ", "note: ", ""]
    k = {"benchmark": "gate", "seed": seed, "model": read[1], "prompt_version": read[0], "policy": policy,
         "question_set": jev.question_set(client, "gate"), "available": {s: len(cs) for s, cs in caps.items()},
         "items": {str(num): c for num, c in enumerate(picked, 1)}}
    sheet.write_text("\n".join(lines))
    key.write_text(json.dumps(k, indent=2))
    sets = [c["set"] for c in picked]
    return {"sheet": str(sheet), "items": len(picked), "by_set": {s: sets.count(s) for s in caps},
            "available": k["available"]}


def draft(conn, folder: Path, client, model: str, effort: str, scrubber, monthly_cap_usd: float) -> dict:
    """A chat model (`client`, llm.client_for) marks every item, blind to mine and the others', into
    draft-<model>.json as it goes. An item whose episode's transcript changed since the trial is skipped."""
    k = json.loads((folder / "key.json").read_text())
    path = folder / f"draft-{model.replace('/', '_')}.json"
    got = json.loads(path.read_text()) if path.exists() else {}
    pv = themes.version(DRAFT_SYSTEM, DRAFT_SCHEMA, model, effort)
    stats = {"items": len(k["items"]), "drafted": 0, "failed": 0, "payload_changed": 0, "cost_usd": 0.0,
             "stopped": None}
    for num, c in sorted(k["items"].items(), key=lambda kv: int(kv[0])):
        if num in got:
            continue
        p = egress.build(conn, c["episode"], scrubber)
        if p.sha256 != c["payload_sha256"]:
            stats["payload_changed"] += 1
            continue
        try:
            a = egress.ask(conn, client, model, effort, DRAFT_SYSTEM, DRAFT_SCHEMA, _shown(gate.state(p, _capture(c))),
                           segment_ids=c["segments"], prompt_version=pv, monthly_cap_usd=monthly_cap_usd,
                           policy=p.policy, episode_id=c["episode"])
        except llm.Stop as e:
            stats["stopped"] = str(e)[:200]
            break
        stats["cost_usd"] += a.cost_usd
        if a.failed or a.data is None:
            stats["failed"] += 1
            continue
        got[num] = {f: names.unscrub(v, p.names) for f, v in a.data.items()}
        stats["drafted"] += 1
        path.write_text(json.dumps(got, indent=2))
    stats["cost_usd"] = round(stats["cost_usd"], 6)
    return {"draft": str(path)} | stats


def marks(text: str) -> dict[str, dict]:
    """Item number -> {"gist", "capture", "note"}: the marks made, `?` as unsure; an unreadable one kept as
    {"unreadable": ...} so a typo doesn't pass for a blank."""
    out = {}
    for num, body in re.findall(r"^##[ \t]+(\d+)[ \t]*$\n(.*?)(?=^##[ \t]+\d+[ \t]*$|\Z)", text, re.M | re.S):
        got = {}
        for field, allowed in LABELS.items():
            m = re.search(rf"^{field}:[ \t]*`?([a-z?]*)`?", body, re.M | re.I)
            word = m.group(1).lower() if m else ""
            if word in allowed:
                got[field] = word
            elif word in UNSURE:
                got[field] = "unsure"
            elif word:
                got.setdefault("unreadable", {})[field] = word
        if (note := re.search(r"^note:[ \t]*(.+)$", body, re.M)) and note.group(1).strip():
            got["note"] = note.group(1).strip()
        if got:
            out[num] = got
    return out


def _drafts(folder: Path) -> dict[str, dict]:
    return {d.stem.removeprefix("draft-"): json.loads(d.read_text()) for d in sorted(folder.glob("draft-*.json"))}


def labels(folder: Path) -> dict[str, dict]:
    """Item number -> {field: (label, from)}: mine on the sheet, then mine on review.md, then the drafts' where two
    or more drafted it and all agree. Unsure counts as no label."""
    mine = marks((folder / "sheet.md").read_text())
    reviewed = marks((folder / "review.md").read_text()) if (folder / "review.md").exists() else {}
    drafts = _drafts(folder)
    out = {}
    for num in json.loads((folder / "key.json").read_text())["items"]:
        got = {}
        for field in LABELS:
            said = {d[num][field] for d in drafts.values() if num in d}
            n = sum(num in d for d in drafts.values())
            for source, label in (("me", mine.get(num, {}).get(field)), ("me", reviewed.get(num, {}).get(field)),
                                  ("drafts", next(iter(said)) if n >= 2 and len(said) == 1 else None)):
                if label in LABELS[field]:
                    got[field] = (label, source)
                    break
                if label == "unsure":
                    break
        out[num] = got
    return out


def review(folder: Path) -> dict:
    """review.md: every mark the drafts disagree on that I haven't made on the sheet, with each model's case. What
    I mark there is kept by the next review."""
    k = json.loads((folder / "key.json").read_text())["items"]
    sheet, drafts = (folder / "sheet.md").read_text(), _drafts(folder)
    if len(drafts) < 2:
        raise SystemExit("review needs two models' drafts: im audit-gate draft <folder> --model <model>, twice")
    mine = marks(sheet)
    path = folder / "review.md"
    before = marks(path.read_text()) if path.exists() else {}
    shown = dict(re.findall(r"^##[ \t]+(\d+)[ \t]*$\n(.*?)(?=^gist:)", sheet, re.M | re.S))
    lines = ["# Gate review: where the models disagree", "",
             "Mark only the fields listed under each item (`ok`/`past`/`unclear`, `keep`/`drop`, or `?`). Then run "
             "`im audit-gate score`; `review` again keeps these marks.", ""]
    n = 0
    for num in sorted(k, key=int):
        disputed = [f for f in LABELS if f not in mine.get(num, {})
                    and len({d[num][f] for d in drafts.values() if num in d}) > 1]
        if not disputed:
            continue
        n += 1
        lines += [f"## {num}", "", shown.get(num, "").strip(), ""]
        for f in disputed:
            lines += [f"{model} says {f} {d[num][f]}: {d[num][f + '_why']}" for model, d in drafts.items() if num in d]
        lines += [""] + [f"{f}: {before.get(num, {}).get(f, '')}" for f in disputed] + ["note: ", ""]
    path.write_text("\n".join(lines))
    return {"review": str(path), "to_review": n, "models": sorted(drafts)}


def _auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    return round(sum(1.0 if p > q else 0.5 if p == q else 0.0 for p in pos for q in neg) / (len(pos) * len(neg)), 3)


def _at_cuts(scored: list[tuple[float, bool]]) -> dict:
    out = {}
    for cut in CUTS:
        hit = [y for p, y in scored if p >= cut]
        pos = sum(y for _, y in scored)
        out[str(cut)] = {"flagged": round(len(hit) / len(scored), 3),
                         "precision": round(sum(hit) / len(hit), 3) if hit else None,
                         "recall": round(sum(hit) / pos, 3) if pos else None}
    return out


def score(folder: Path) -> dict:
    """Per deciding check, how well Jev's probability ranks the labels (AUC); per decision (its checks' highest
    probability), precision and recall at each cut, and the suggested cut."""
    k = json.loads((folder / "key.json").read_text())["items"]
    got = labels(folder)
    sources = [s for g in got.values() for _, s in g.values()]
    out = {"items": len(k), "labels": {"from_me": sources.count("me"), "from_drafts": sources.count("drafts"),
                                        "missing": 2 * len(k) - len(sources)}}
    for decision, field, positive, checks in (("repair", "gist", REPAIRED, gate.GIST),
                                              ("drop", "capture", ("drop",), gate.DROP)):
        rows = [(k[n]["answers"], g[field][0] in positive) for n, g in got.items() if field in g]
        asked = [q for q in checks if all(q in a for a, _ in rows)]   # an older sheet's answers lack later checks
        scored = [(max(a[q] for q in asked), y) for a, y in rows]
        cuts = _at_cuts(scored) if scored else {}
        if decision == "repair":
            ok = [float(c) for c, r in cuts.items() if r["recall"] is not None and r["recall"] >= TARGET]
            suggested = max(ok) if ok else None
        else:
            ok = [float(c) for c, r in cuts.items() if r["precision"] is not None and r["precision"] >= TARGET]
            suggested = min(ok) if ok else None
        out[decision] = {"labeled": len(rows), "marked_" + "_or_".join(positive): sum(y for _, y in rows),
                         "auc_by_check": {q: _auc([a[q] for a, y in rows if y], [a[q] for a, y in rows if not y])
                                          for q in asked},
                         "auc": _auc([p for p, y in scored if y], [p for p, y in scored if not y]),
                         "at_cuts": cuts, "suggested_cut": suggested}
    return out
