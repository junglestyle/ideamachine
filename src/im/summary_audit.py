"""Do summaries say more than what was said? A blind sheet of captures from the extraction trials (roadmap §5.9).

On the first tension sheet, summaries sharpened readings the quotes only allowed ("comfort beats the stigma" for
"they're pretty comfy"). Every stage that reads summaries inherits what they add. This sheet samples captures
evenly across the models in the extraction trials (policy C, every prompt), and shows the lines each capture cites,
its quote and its summary, without saying which model wrote it. The question: does the summary claim anything those
lines don't say? `yes` means it adds.

Marking, model drafts and scoring are the probe sheets' (im.probe_audit): `score` gives each model's rate of
summaries that add (a set per model), and drafts say which cheap model can be trusted to check them, the
faithfulness pass a cheaper summarizer would need.
"""

import json
import random
from pathlib import Path

SIZE = 50
HEADER = """# Summaries (blind)

Each item is one capture from an extraction trial: the transcript lines it cites, the quote it took, and the
summary written for it. Judge the summary against the lines.

Does the summary claim anything those lines don't say: an inference, a conclusion, a motive or an outcome that
isn't there? Wording that compresses what was said is fine; adding to it isn't.

After `mark:`, write `yes` (it adds), `no` (faithful) or `unsure` (`?`). If yes, say in `note:` what it adds. Mark it
yourself, without a model's help. Then run `im audit-summaries score` on this folder. "me" is me, "someone" anyone
else.
"""


def _captures(conn) -> list[dict]:
    """Every capture with a summary in the extraction trials under policy C, one read per model (not replicates)."""
    out = []
    for model, eid, items in conn.execute(
            """SELECT t.model, t.episode_id::text, t.items FROM im.prompt_trials t
               JOIN im.episodes e ON e.episode_id = t.episode_id AND e.current
               WHERE t.privacy_policy_version LIKE 'C%%' AND t.prompt_version NOT LIKE '%%~%%'
               ORDER BY t.model, t.episode_id"""):
        for n, it in enumerate(items):
            if (it.get("gist") or "").strip() and it.get("source_segment_ids"):
                out.append({"model": model, "episode": eid, "item": n, "kind": it["kind"], "quote": it["quote"],
                            "gist": it["gist"], "segments": it["source_segment_ids"]})
    return out


def _lines(conn, segments: list[str]) -> list[str]:
    return [f"> [{'me' if kind == 'owner' else 'someone'}] {text}" for text, kind in conn.execute(
        """SELECT s.text, s.speaker_kind FROM unnest(%s::uuid[]) WITH ORDINALITY u(id, n)
           JOIN im.source_segments s ON s.segment_id = u.id ORDER BY u.n""", (segments,))]


def build(conn, seed: int = 11, size: int = SIZE) -> tuple[str, dict]:
    rng = random.Random(seed)
    by_model: dict[str, list[dict]] = {}
    for c in _captures(conn):
        by_model.setdefault(c["model"], []).append(c)
    if not by_model:
        raise SystemExit("no extraction trials under policy C with summaries yet (im trial v3 --model ...)")
    per = max(1, size // len(by_model))
    picked = [c | {"set": model} for model, cs in sorted(by_model.items()) for c in rng.sample(cs, min(per, len(cs)))]
    rng.shuffle(picked)
    lines = [HEADER]
    for n, c in enumerate(picked, 1):
        lines += [f"## {n}", "", "What was said:", *_lines(conn, c["segments"]), "",
                  f"Quote: \"{c['quote']}\"", f"Summary: {c['gist']}", "", "mark: ", "note: ", ""]
    key = {"benchmark": "summary", "seed": seed, "sizes": {m: sum(p["set"] == m for p in picked) for m in by_model},
           "available": {m: len(cs) for m, cs in by_model.items()},
           "pairs": {str(n): c for n, c in enumerate(picked, 1)}}
    return "\n".join(lines), key


def write(conn, out: Path, seed: int = 11, size: int = SIZE) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    sheet, key = out / "sheet.md", out / "key.json"
    if sheet.exists() or key.exists():
        raise SystemExit(f"{out} already has a sheet; score it, or pick another folder")
    text, k = build(conn, seed, size)
    sheet.write_text(text)
    key.write_text(json.dumps(k, indent=2))
    return {"sheet": str(sheet), "items": len(k["pairs"]), "by_model": k["sizes"], "available": k["available"]}

