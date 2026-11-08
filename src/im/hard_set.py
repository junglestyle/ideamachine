"""A deliberate hard set of episodes for checking an extraction prompt (2026-11-08): real episodes chosen because an
earlier read shows the speech is hard, beside the random trial sample (`trial.sample`), which mostly holds easy
ones. Each case is a rule over the captures already made; an episode is picked once, by the first case that picks
it, in a fixed shuffle so the same seed picks the same set.

What's written is each episode exactly as extraction would send it (`egress.build`), under policy C by default:
speakers and names said are pseudonymized, so the set can go to another model for review. The folder quotes people:
keep it in saved/.
"""

import hashlib
import json
from pathlib import Path

from im import egress

_ON = "FROM im.items i JOIN im.episodes e ON e.episode_id = i.episode_id AND e.current"
_SOURCES = "nurse|teacher|instructor|friend|doctor|coach|therapist|book|article|podcast|video|dad|mom|mother|father|boss"
_YES = "yeah|yes|yep|exactly|totally|right|true|definitely|absolutely|agreed|for sure|uh huh|mm hmm"

# Each case: what makes it hard, and the captures that show it.
CASES = {
    "irony": ("jokes, bits, irony: a capture filed as a joke, or a gist labeled as one",
              rf"""SELECT i.episode_id, i.item_id {_ON}
                   WHERE i.kind = 'joke' OR i.gist ~* '^\W*(joke|bit|sarcasm|irony|ironic)\W'"""),
    "fragmented": ("fragmented speech: a quote not found in its lines, or stitched from pieces",
                   rf"""SELECT i.episode_id, i.item_id {_ON}
                        WHERE i.quote_in_lines < 0.6 OR i.quote ~ '\.\.\.|…| / '"""),
    "relayed": ("a quotation or someone else's thought passed on, often without a named source",
                rf"""SELECT i.episode_id, i.item_id {_ON}
                     WHERE i.gist ~* '\m(a|the|my|his|her|their) ({_SOURCES})\M'
                        OR i.gist ~* '\m(someone|somebody|they|people|he|she) (said|says|told|tells)\M'
                        OR i.gist ~* '\maccording to\M'"""),
    "incomplete": ("an incomplete or very short thought: a quote under eight words",
                   rf"""SELECT i.episode_id, i.item_id {_ON}
                        WHERE cardinality(regexp_split_to_array(btrim(i.quote), '\s+')) < 8"""),
    "acknowledged": ("someone else's idea I only acknowledge: their capture, then a short yes from me within a minute",
                     rf"""SELECT i.episode_id, i.item_id {_ON}
                          JOIN im.item_speaker w ON w.item_id = i.item_id AND w.speaker = 'not_me'
                          JOIN im.source_segments s ON s.segment_id = i.said_by_segment_id
                          WHERE EXISTS (
                            SELECT 1 FROM im.source_segments r
                            WHERE r.conversation_id = s.conversation_id AND r.speaker_kind = 'owner'
                              AND r.started_at >= s.ended_at AND r.started_at < s.ended_at + interval '60 seconds'
                              AND cardinality(regexp_split_to_array(btrim(r.text), '\s+')) <= 4
                              AND r.text ~* '\m({_YES})\M')"""),
    "answering": ("my thought that only makes sense against someone else's line: my capture citing their speech",
                  rf"""SELECT i.episode_id, i.item_id {_ON}
                       JOIN im.item_speaker w ON w.item_id = i.item_id AND w.speaker = 'me'
                       WHERE EXISTS (SELECT 1 FROM im.source_segments c
                                     WHERE c.segment_id = ANY(i.source_segment_ids) AND c.speaker_kind <> 'owner')"""),
}
PER_CASE = 6


def _shuffled(rows: list[tuple], seed: int) -> list[tuple]:
    """Episodes in a fixed order for this seed, one capture each (the first by the same order)."""
    key = lambda r: hashlib.sha256(f"{seed}:{r[0]}:{r[1]}".encode()).hexdigest()  # noqa: E731
    out: dict = {}
    for eid, item in sorted(rows, key=key):
        out.setdefault(eid, item)
    return sorted(out.items(), key=lambda r: hashlib.sha256(f"{seed}:{r[0]}".encode()).hexdigest())


def pick(conn, per_case: int = PER_CASE, seed: int = 1) -> tuple[list[dict], dict]:
    """Up to `per_case` episodes for each case, none twice ({"case", "episode_id", "item_id"}), and how many
    episodes each case's rule found before any were taken by an earlier case."""
    taken, out, available = set(), [], {}
    for case, (_, sql) in CASES.items():
        found = _shuffled(conn.execute(sql).fetchall(), seed)
        available[case] = len(found)
        chosen = [(eid, item) for eid, item in found if eid not in taken][:per_case]
        taken |= {eid for eid, _ in chosen}
        out += [{"case": case, "episode_id": str(eid), "item_id": str(item)} for eid, item in chosen]
    return out, available


def write(conn, folder: Path, per_case: int = PER_CASE, seed: int = 1, scrubber=None, controls: int = 0) -> dict:
    """Each picked episode as extraction would send it, one file per episode named by its case, and index.json
    saying which case and which earlier capture picked it. `controls` adds that many episodes from the trials' random
    sample (`trial.sample`: the episodes every trial reads), leaving out any already picked. Won't write over a set."""
    from im import trial

    if (folder / "index.json").exists():
        raise SystemExit(f"{folder} already has a hard set; pick another folder")
    folder.mkdir(parents=True, exist_ok=True)
    picked, available = pick(conn, per_case, seed)
    taken = {p["episode_id"] for p in picked}
    picked += [{"case": "control", "episode_id": str(eid), "item_id": None}
               for eid in trial.sample(conn, len(taken) + controls) if str(eid) not in taken][:controls]
    index, n = [], {}
    for p in picked:
        n[p["case"]] = n.get(p["case"], 0) + 1
        name = f"{p['case']}-{n[p['case']]:02d}.txt"
        payload = egress.build(conn, p["episode_id"], scrubber)
        (folder / name).write_text(payload.text + "\n")
        why = CASES[p["case"]][0] if p["case"] in CASES else "a control: from the trials' random sample"
        index.append({"file": name, "case": p["case"], "why": why, "episode_id": p["episode_id"],
                      "picked_by_item": p["item_id"], "policy": payload.policy})
    (folder / "index.json").write_text(json.dumps({"seed": seed, "per_case": per_case, "controls": controls,
                                                   "episodes": index}, indent=2))
    return {"folder": str(folder), "episodes": len(index), "by_case": n, "available": available,
            "policy": index[0]["policy"] if index else None}
