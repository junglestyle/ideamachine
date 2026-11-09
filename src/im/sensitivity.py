"""Sensitivity tags (decision 0012): eight descriptive tags on each idea and theme, so a view can hide what I'd rather
not show, as Lattice's demo mode does.

Jev answers one yes or no per tag (jev.QUESTIONS["sensitivity"]) about each shown idea (title, statement, the
archive's notes and my note, my corrections winning, and up to three of its quotes, since swearing is usually in what
was said) and each theme (name and description). A tag holds at CUT or above, and Jev's probabilities are kept beside
the tags, so the cut can be set later from my own marks. The tags describe; which of them hide is the view's choice
(pub.sensitivity lists them, and counts anything with a tag as sensitive). My own marks (idea_sensitive,
theme_sensitive) win there, either way. Names said are pseudonymized before anything goes out (policy C, 0008: Jev
isn't Anthropic). An idea or theme is asked about again when its text changes, at most MAX_PER_RUN a run.
"""

import hashlib
import json

from im import egress, jev, llm

TAGS = ("profanity", "sex", "gender_politics", "politics", "drugs", "offensive", "violence", "private_person")
CUT = 0.5          # not yet set from marks; recall first, since a tag only hides something in a demo
QUOTES = 3
MAX_PER_RUN = 500


def tagger_version(client: jev.Jev) -> str:
    return f"{jev.question_set(client, 'sensitivity')}@{CUT}"


def _hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def _todo(conn, tv: str) -> list[tuple]:
    """(kind, id, the item as Jev is shown it, its hash) for each idea and theme not tagged under `tv` for its text."""
    out = []
    for i, t, st, n, m, q, h, v in conn.execute(
            """SELECT d.idea_id, d.title, d.statement, coalesce(d.notes, ''), coalesce(d.my_note, ''),
                      ARRAY(SELECT left(ev.quote, 200) FROM pub.evidence ev WHERE ev.idea_id = d.idea_id
                              AND ev.verdict IS DISTINCT FROM 'discard' ORDER BY ev.said_at, ev.item_id LIMIT %s),
                      s.input_hash, s.tagger_version
               FROM pub.ideas d LEFT JOIN im.idea_sensitivity s USING (idea_id)
               ORDER BY d.created_at, d.idea_id""", (QUOTES,)):
        if v != tv or h != _hash(t, st, n, m, q):
            item = {"title": t, "statement": st} | ({"notes": n} if n else {}) | ({"my_note": m} if m else {}) \
                | ({"said": q} if q else {})
            out.append(("idea", i, item, _hash(t, st, n, m, q)))
    for i, n, d, h, v in conn.execute(
            """SELECT t.theme_id, t.name, coalesce(t.description, ''), s.input_hash, s.tagger_version
               FROM im.themes t LEFT JOIN im.theme_sensitivity s USING (theme_id) ORDER BY t.created_at, t.theme_id"""):
        if v != tv or h != _hash(n, d):
            out.append(("theme", i, {"theme": n} | ({"description": d} if d else {}), _hash(n, d)))
    return out


def _pseudonymized(item: dict, scrubber) -> dict:
    """The item with names said pseudonymized in every text, consistently across them."""
    if scrubber is None:
        return item
    texts = [t for v in item.values() for t in (v if isinstance(v, list) else [v])]
    it = iter(scrubber.scrub(texts)[0])
    return {k: [next(it) for _ in v] if isinstance(v, list) else next(it) for k, v in item.items()}


def tag_stage(conn, client: jev.Jev, scrubber, monthly_cap_usd: float = 20.0, limit: int = MAX_PER_RUN) -> dict:
    """Tag what's new or changed, one request each. One that failed is asked again on a later run."""
    tv = tagger_version(client)
    todo = _todo(conn, tv)
    policy = scrubber.policy if scrubber else egress.POLICY_VERSION
    stats = {"tagger_version": tv, "pending": len(todo), "tagged": 0, "sensitive": 0, "failed": 0, "stopped": None}
    for kind, id_, item, h in todo[:limit]:
        body = client.body("sensitivity", {"item": _pseudonymized(item, scrubber)})
        try:
            a = egress.send(conn, lambda body=body: client.complete(body), model=client.model,
                            payload=json.dumps(body), segment_ids=egress.segments_of(conn, [id_]) if kind == "idea"
                            else [], prompt_version=f"sensitivity:{tv}", policy=policy,
                            monthly_cap_usd=monthly_cap_usd, read=lambda r: jev.read("sensitivity", r))
        except llm.Stop as e:
            stats["stopped"] = str(e)[:200]
            break
        if a.failed:
            stats["failed"] += 1
            continue
        p = {t: round(a.data[t], 3) for t in TAGS}
        tags = [t for t in TAGS if p[t] >= CUT]
        table, key = ("idea_sensitivity", "idea_id") if kind == "idea" else ("theme_sensitivity", "theme_id")
        conn.execute(
            f"""INSERT INTO im.{table} ({key}, categories, probabilities, input_hash, tagger_version)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT ({key}) DO UPDATE SET categories = excluded.categories,
                  probabilities = excluded.probabilities, input_hash = excluded.input_hash,
                  tagger_version = excluded.tagger_version, tagged_at = now()""",
            (id_, tags, json.dumps(p), h, tv))
        stats["tagged"] += 1
        stats["sensitive"] += bool(tags)
    return stats
