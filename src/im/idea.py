"""One card, read and corrected from the CLI: `im idea show ID` and `im idea edit ID`.

A correction is human input and lives only in pub.feedback_events (011): an `idea_correct` event carrying only the
fields I changed, and an `idea_note`, source `cli`. im.idea_text reads my latest of each field; an empty value reverts
to Claude's original, which im.ideas keeps. Every stage that reads a card's text sees a correction as changed input on
its next run, not as a version change, so the re-read gate doesn't hold it.
"""

import json
import re

SOURCE = "cli"
# The lengths Lattice allowed, as idea_add's payload still does (023).
LIMITS = {"title": 120, "statement": 1000, "note": 1000}


def find(conn, ref: str):
    """The cluster a full id or unique id prefix names. Raises ValueError for no match, an ambiguous prefix, or a
    cluster merged away (naming the cluster it's part of now)."""
    prefix = ref.strip().lower()
    rows = conn.execute("SELECT idea_id FROM im.ideas WHERE idea_id::text LIKE %s ORDER BY idea_id LIMIT 6",
                        (prefix + "%",)).fetchall() if re.fullmatch(r"[0-9a-f-]+", prefix) else []
    if not rows:
        raise ValueError(f"no cluster matches {ref!r}")
    if len(rows) > 1:
        raise ValueError(f"{ref!r} is ambiguous: " + ", ".join(str(r[0]) for r in rows[:5]))
    (idea,) = rows[0]
    into = conn.execute("SELECT into_id FROM im.merged_into WHERE idea_id = %s", (idea,)).fetchone()
    if into:
        raise ValueError(f"{idea} was merged into {into[0]}: use that one")
    return idea


def _text(conn, idea) -> tuple:
    return conn.execute("""SELECT title, statement, my_note, claude_title, claude_statement, corrected_at
                           FROM im.idea_text WHERE idea_id = %s""", (idea,)).fetchone()


def _card(title: str, statement: str, note: str | None) -> list[str]:
    return [f"  {title}", f"  {statement}"] + ([f"  my note: {note}"] if note else [])


def show(conn, ref: str) -> str:
    """The card's text as it counts now, whether I corrected it, its captures' quotes, and what was merged into it."""
    idea = find(conn, ref)
    title, statement, note, claude_title, claude_statement, corrected_at = _text(conn, idea)
    out = [str(idea), *_card(title, statement, note)]
    changed = [f for f, mine, orig in (("title", title, claude_title), ("statement", statement, claude_statement))
               if mine != orig]
    if changed:
        out.append(f"  corrected ({' and '.join(changed)}), last {corrected_at.astimezone():%Y-%m-%d %H:%M}; "
                   "Claude's:")
        out += [f"    {orig}" for f, orig in (("title", claude_title), ("statement", claude_statement)) if f in changed]
    else:
        out.append("  not corrected")
    quotes = conn.execute("""SELECT speaker, quote, said_at FROM pub.card_evidence WHERE idea_id = %s
                             ORDER BY said_at NULLS LAST, item_id""", (idea,)).fetchall()
    out.append(f"\ncaptures ({len(quotes)}):")
    out += [f"  {'' if at is None else f'{at.astimezone():%Y-%m-%d} '}{who or '?'}: \u201c{quote}\u201d"
            for who, quote, at in quotes]
    merged = conn.execute("""SELECT other_id, merged_at, title, statement, my_note FROM im.idea_merges
                             WHERE keep_id = %s ORDER BY merge_id""", (idea,)).fetchall()
    for other, at, m_title, m_statement, m_note in merged:
        out += [f"\nmerged in {at.astimezone():%Y-%m-%d}: {other}", *_card(m_title, m_statement, m_note)]
    return "\n".join(out)


def edit(conn, ref: str, title: str | None = None, statement: str | None = None, note: str | None = None) -> str:
    """Correct a card: one idea_correct event with the fields given, and an idea_note if a note is given. An empty
    value reverts to Claude's original (no note, for the note). Raises ValueError, writing nothing, for an unknown or
    merged-away cluster, no field, or a field too long."""
    given = {k: v.strip() for k, v in (("title", title), ("statement", statement), ("note", note)) if v is not None}
    if not given:
        raise ValueError("nothing to change: give --title, --statement or --note")
    for k, v in given.items():
        if len(v) > LIMITS[k]:
            raise ValueError(f"the {k} is {len(v)} characters; at most {LIMITS[k]}")
    with conn.transaction():
        idea = find(conn, ref)
        before = _text(conn, idea)
        correct = {k: v for k, v in given.items() if k != "note"}
        events = ([("idea_correct", correct)] if correct else []) + (
            [("idea_note", {"note": given["note"]})] if "note" in given else [])
        for kind, payload in events:
            conn.execute("""INSERT INTO pub.feedback_events (kind, idea_id, payload, source)
                            VALUES (%s, %s, %s, %s)""", (kind, idea, json.dumps(payload), SOURCE))
        after = _text(conn, idea)
    return "\n".join([str(idea), "before:", *_card(*before[:3]), "after:", *_card(*after[:3])])
