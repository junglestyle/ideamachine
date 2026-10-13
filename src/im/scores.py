"""Surfacing: rank ideas by signals that don't rest on my keep/discard tags (ROADMAP Phase 2, slice 3).

My tags are noisy: judging the same capture twice, I agreed with myself 64% of the time. So the ranking leans on a
fixed rubric Claude applies to each idea, whether I've developed the idea, and my stars, and pub.idea_scores
combines them, weighted by my pairwise comparisons (migration 025). The rubric asks what an idea is worth to
someone who doesn't know me, not whether I'd keep it.
"""

import hashlib
import json

from im import themes

BATCH = 25
CRITERIA = ("generality", "novelty", "compression", "outside_value")

RUBRIC_SYSTEM = """You score ideas from my idea lattice, captured from my conversations and notes. Score each \
idea from 1 to 5 on each criterion, on its own merits:
- generality: 1 applies only to the situation it came from; 3 applies to a family of similar situations; 5 is a \
principle that holds across domains.
- novelty: 1 is common sense or something most thoughtful people already believe; 3 is a fresh angle on a \
familiar point; 5 is genuinely surprising or counterintuitive and still holds up.
- compression: 1 is diffuse or needs a paragraph to get across; 3 is clear; 5 is compact and memorable, the kind \
of line people repeat.
- outside_value: 1 matters only to me or the people in the conversation; 3 would interest a friend; 5 would \
interest a thoughtful stranger who doesn't know me, worth sharing or writing up.
Jokes and bits count: score them as jokes (generality is how widely the bit works, novelty is how unexpected it \
is). Don't reward or penalize an idea for being provocative, controversial or wrong; score how good it is as an \
idea. Use the whole scale: most ideas should land at 2 or 3, and 5 should be rare. For each idea, add one short \
sentence on what drives its score."""

RUBRIC_SCHEMA = {
    "type": "object",
    "properties": {"scores": {"type": "array", "items": {
        "type": "object",
        "properties": {"idea": {"type": "integer"}, **{c: {"type": "integer"} for c in CRITERIA},
                       "why": {"type": "string"}},
        "required": ["idea", *CRITERIA, "why"], "additionalProperties": False}}},
    "required": ["scores"], "additionalProperties": False,
}


def rubric_version() -> str:
    return themes.version(RUBRIC_SYSTEM, RUBRIC_SCHEMA, themes.MODEL, themes.EFFORT)


def _hash(title: str, statement: str) -> str:
    return hashlib.sha256(json.dumps([title, statement]).encode()).hexdigest()[:16]


def _todo(conn, rv: str) -> list[tuple]:
    """Shown ideas whose current text (my corrections win) hasn't been scored with this rubric."""
    rows = conn.execute(
        """SELECT x.idea_id, x.title, x.statement, s.input_hash, s.rubric_version
           FROM pub.ideas d JOIN im.idea_text x USING (idea_id) LEFT JOIN im.idea_scores s USING (idea_id)
           ORDER BY d.created_at, x.idea_id""").fetchall()
    return [(i, t, st) for i, t, st, h, v in rows if v != rv or h != _hash(t, st)]


def score_stage(conn, client, monthly_cap_usd: float = 20.0) -> dict:
    from im.extract import spent_this_month

    rv = rubric_version()
    todo = _todo(conn, rv)
    stats = {"rubric_version": rv, "pending": len(todo), "scored": 0, "stopped": None}
    for start in range(0, len(todo), BATCH):
        if spent_this_month(conn) >= monthly_cap_usd:
            stats["stopped"] = f"monthly cap of ${monthly_cap_usd:.2f} reached"
            break
        batch = todo[start:start + BATCH]
        text = "Ideas:\n" + "\n".join(f"{n}. {t}: {st}" for n, (_, t, st) in enumerate(batch, 1))
        answer, r = themes._call(client, RUBRIC_SYSTEM, RUBRIC_SCHEMA, text)
        themes._log(conn, text, rv, [i for i, _, _ in batch], r)
        if answer is None:
            stats["stopped"] = "refused"
            break
        with conn.transaction():
            for s in answer["scores"]:
                if not 1 <= s["idea"] <= len(batch) or not all(1 <= s[c] <= 5 for c in CRITERIA):
                    continue
                idea_id, title, statement = batch[s["idea"] - 1]
                conn.execute(
                    """INSERT INTO im.idea_scores (idea_id, generality, novelty, compression, outside_value, why,
                         rubric_version, input_hash)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (idea_id) DO UPDATE SET generality = excluded.generality,
                         novelty = excluded.novelty, compression = excluded.compression,
                         outside_value = excluded.outside_value, why = excluded.why,
                         rubric_version = excluded.rubric_version, input_hash = excluded.input_hash, scored_at = now()""",
                    (idea_id, *(s[c] for c in CRITERIA), s["why"][:500], rv, _hash(title, statement)))
                stats["scored"] += 1
    return stats


def top(conn, n: int = 20) -> list[tuple]:
    return conn.execute(
        """SELECT s.score, s.rubric, s.generality, s.novelty, s.compression, s.outside_value, s.recurrence,
                  d.starred, d.title, s.why
           FROM pub.idea_scores s JOIN pub.ideas d USING (idea_id)
           WHERE s.score IS NOT NULL ORDER BY s.score DESC, d.title LIMIT %s""", (n,)).fetchall()
