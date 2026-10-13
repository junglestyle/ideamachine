"""Demo mode: tag the ideas and themes I'd hesitate to show in a job interview, so Lattice can hide them.

Claude reads each shown idea (title, statement, the archive's notes and my note, my corrections winning, and up to
three of its quotes, since swearing is usually in what was said) and each theme
(name and description), in batches, and tags only what it would hide, by category. An idea or theme is tagged again
when that text changes. My own marks (idea_sensitive, theme_sensitive) win in pub.sensitivity, either way.
Tagging ideas rather than captures covers everything Lattice shows, and leaves the extraction prompt (and its
version) alone.
"""

import hashlib
import json

from im import themes

BATCH = 50
QUOTES = 3
CATEGORIES = ("sex", "gender_politics", "political_extremism", "violence", "racism", "profanity")

SYSTEM = """I'm about to show my idea lattice (ideas from my conversations and notes, grouped into themes) in a job \
interview. Tag each idea and theme I'd hesitate to show there, with one or more of these categories:
- sex: sexual content, sexual behaviour, dating framed sexually.
- gender_politics: gender and sexual politics: claims about men and women, sexual dimorphism, gender roles and \
power, feminism and its critics.
- political_extremism: fascism, communism and other extreme or revolutionary politics, sympathetic or not.
- violence: violence, weapons, harming people.
- racism: racial or ethnic stereotypes, slurs, or claims about races or ethnic groups, even as a joke.
- profanity: swearing or crude language in the idea's wording.
Tag only what you'd hesitate to put in front of an interviewer, not everything that touches a subject: a neutral \
point about dance technique isn't sex, a passing mention of a war isn't violence, and a point about how cultures \
differ isn't racism. When in doubt between tagging and not, tag. Give an empty list for anything fine to show."""

SCHEMA = {
    "type": "object",
    "properties": {kind: {"type": "array", "items": {
        "type": "object",
        "properties": {"n": {"type": "integer"},
                       "categories": {"type": "array", "items": {"type": "string", "enum": list(CATEGORIES)}}},
        "required": ["n", "categories"], "additionalProperties": False}} for kind in ("ideas", "themes")},
    "required": ["ideas", "themes"], "additionalProperties": False,
}


def tagger_version() -> str:
    return themes.version(SYSTEM, SCHEMA, themes.MODEL, themes.EFFORT)


def _hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def _todo(conn, tv: str) -> tuple[list, list]:
    ideas = conn.execute(
        """SELECT d.idea_id, d.title, d.statement, coalesce(d.notes, ''), coalesce(d.my_note, ''),
                  ARRAY(SELECT left(ev.quote, 200) FROM pub.evidence ev WHERE ev.idea_id = d.idea_id
                          AND ev.verdict IS DISTINCT FROM 'discard' ORDER BY ev.said_at, ev.item_id LIMIT QUOTES),
                  s.input_hash, s.tagger_version
           FROM pub.ideas d LEFT JOIN im.idea_sensitivity s USING (idea_id)
           ORDER BY d.created_at, d.idea_id""".replace("QUOTES", str(QUOTES)))
    ideas = [(i, f"{t}: {st}" + (f" ({n})" if n else "") + (f" [my note: {m}]" if m else "")
              + "".join(f' [said: "{x}"]' for x in q), _hash(t, st, n, m, q))
             for i, t, st, n, m, q, h, v in ideas if v != tv or h != _hash(t, st, n, m, q)]
    th = conn.execute(
        """SELECT t.theme_id, t.name, coalesce(t.description, ''), s.input_hash, s.tagger_version
           FROM im.themes t LEFT JOIN im.theme_sensitivity s USING (theme_id) ORDER BY t.created_at, t.theme_id""")
    th = [(i, f"{n}" + (f": {d}" if d else ""), _hash(n, d)) for i, n, d, h, v in th if v != tv or h != _hash(n, d)]
    return ideas, th


def tag_stage(conn, client, monthly_cap_usd: float = 20.0) -> dict:
    from im.extract import spent_this_month

    tv = tagger_version()
    ideas, th = _todo(conn, tv)
    stats = {"tagger_version": tv, "pending": len(ideas) + len(th), "tagged": 0, "sensitive": 0, "stopped": None}
    work = [("idea", r) for r in ideas] + [("theme", r) for r in th]
    for start in range(0, len(work), BATCH):
        if spent_this_month(conn) >= monthly_cap_usd:
            stats["stopped"] = f"monthly cap of ${monthly_cap_usd:.2f} reached"
            break
        batch = work[start:start + BATCH]
        bi = [r for k, r in batch if k == "idea"]
        bt = [r for k, r in batch if k == "theme"]
        text = ("Ideas:\n" + ("\n".join(f"{n}. {line}" for n, (_, line, _) in enumerate(bi, 1)) or "(none)")
                + "\n\nThemes:\n" + ("\n".join(f"{n}. {line}" for n, (_, line, _) in enumerate(bt, 1)) or "(none)"))
        answer, r = themes._call(client, SYSTEM, SCHEMA, text)
        themes._log(conn, text, f"sensitivity:{tv}", [i for i, _, _ in bi], r)
        if answer is None:
            stats["stopped"] = "refused"
            break
        with conn.transaction():
            for kind, rows, table, key in [("ideas", bi, "idea_sensitivity", "idea_id"),
                                           ("themes", bt, "theme_sensitivity", "theme_id")]:
                got = {a["n"]: [c for c in dict.fromkeys(a["categories"]) if c in CATEGORIES] for a in answer[kind]}
                for n, (id_, _, h) in enumerate(rows, 1):
                    if n not in got:   # not answered: asked again next run
                        continue
                    conn.execute(
                        f"""INSERT INTO im.{table} ({key}, categories, input_hash, tagger_version) VALUES (%s, %s, %s, %s)
                            ON CONFLICT ({key}) DO UPDATE SET categories = excluded.categories,
                              input_hash = excluded.input_hash, tagger_version = excluded.tagger_version,
                              tagged_at = now()""", (id_, got[n], h, tv))
                    stats["tagged"] += 1
                    stats["sensitive"] += bool(got[n])
    return stats
