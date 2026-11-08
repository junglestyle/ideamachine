"""Themes (ROADMAP Phase 2, slice 4): my categories, proposed by the system and steered by me.

- The filer puts each unthemed idea into up to two current themes, or none. It looks at an idea again only when
  the set of themes changes, or when its batch was refused or failed (it's filed on the next run).
- The proposer, once enough ideas fit no theme, asks Claude to group them into new themes. These arrive unpinned
  (`origin = 'clustered'`) and stay proposals until I pin, rename or reject them.
- My theme feedback (pin, unpin, rename, reject, merge, split, filing or unfiling one idea) arrives as events in
  pub.feedback_events, from Lattice or the CLI, and applies the moment it's inserted (a trigger running
  im.apply_theme_event, migration 024). Rejected names, and proposals I merged away, are never proposed again. An
  idea I unfiled is never filed into that theme again, and re-filing never removes a placement I made.
- A theme without a description (one I split off) gets one from Claude on the run, since the filer reads them.
"""

import hashlib
import json
import string

from im import egress, llm
from im.config import ModelConfig

BATCH = 25
PROPOSE_MIN = 6           # unthemed ideas, not yet seen by a proposal pass, before proposing
MIN_GROUP = 3             # a proposed theme needs at least this many ideas
MAX_THEMES_PER_IDEA = 2

FILE_SYSTEM = """You file ideas from my idea lattice into my themes. For each idea, give the letters of the \
themes it clearly belongs to, at most two, or an empty list when none fits well. Prefer an empty list over a \
weak fit. Judge by what the idea is about, not by surface words. Don't evaluate the ideas."""

FILE_SCHEMA = {
    "type": "object",
    "properties": {"assignments": {"type": "array", "items": {
        "type": "object",
        "properties": {"idea": {"type": "integer"}, "themes": {"type": "array", "items": {"type": "string"}}},
        "required": ["idea", "themes"], "additionalProperties": False}}},
    "required": ["assignments"], "additionalProperties": False,
}

PROPOSE_SYSTEM = f"""You propose new themes for my idea lattice. You get my existing themes, names I've rejected, and \
ideas that fit none of the existing themes. Group ideas that genuinely share a subject or concern into new themes, \
each with at least {MIN_GROUP} ideas, a short name in the style of the existing ones, and a one-line description. \
Leave ideas that don't group naturally out of every theme; don't force them. Don't propose anything close to an \
existing theme or a rejected name. Don't evaluate the ideas."""

DESCRIBE_SYSTEM = """You describe themes in my idea lattice. For each theme you get its name and some of its \
ideas. Write a one-line description of what the theme covers, in the style of a short catalogue entry, so a \
filer can tell which new ideas belong in it. Don't evaluate the ideas."""

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {"themes": {"type": "array", "items": {
        "type": "object", "properties": {"theme": {"type": "integer"}, "description": {"type": "string"}},
        "required": ["theme", "description"], "additionalProperties": False}}},
    "required": ["themes"], "additionalProperties": False,
}

PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {"themes": {"type": "array", "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                       "ideas": {"type": "array", "items": {"type": "integer"}}},
        "required": ["name", "description", "ideas"], "additionalProperties": False}}},
    "required": ["themes"], "additionalProperties": False,
}


def version(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:12]


def current_themes(conn) -> list[tuple]:
    """(theme_id, name, description), pinned first, then by name."""
    return conn.execute("""SELECT theme_id, name, description FROM im.themes
                           ORDER BY pinned DESC, name""").fetchall()


def themes_version(conn, chat: ModelConfig = ModelConfig()) -> str:
    return version(FILE_SYSTEM, FILE_SCHEMA, chat.model, chat.effort,
                   sorted((str(t), n, d) for t, n, d in current_themes(conn)))


def _letters(n: int) -> list[str]:
    return [string.ascii_uppercase[i] if i < 26 else f"Z{i - 25}" for i in range(n)]


def ask(conn, client, chat: ModelConfig, system: str, schema: dict, text: str, prompt_version: str, idea_ids: list,
        monthly_cap_usd: float) -> egress.Answer:
    """A request about lattice ideas, on the stage's model (`chat`), logged with the segments behind the ideas it
    shows. Raises llm.Stop at the cap or when Claude can't answer now; a request that failed comes back failed."""
    return egress.ask(conn, client, chat.model, chat.effort, system, schema, text, prompt_version=prompt_version,
                      segment_ids=egress.segments_of(conn, idea_ids), monthly_cap_usd=monthly_cap_usd)


def _unthemed(conn, tv: str) -> list[tuple]:
    """Visible ideas with no theme that the filer hasn't checked against this set of themes."""
    return conn.execute(
        """SELECT d.idea_id, d.title, d.statement FROM pub.ideas d
           WHERE NOT EXISTS (SELECT 1 FROM im.idea_themes it WHERE it.idea_id = d.idea_id)
             AND NOT EXISTS (SELECT 1 FROM im.theme_checks c WHERE c.idea_id = d.idea_id AND c.themes_version = %s)
           ORDER BY d.created_at""", (tv,)).fetchall()


def refile_corrected(conn) -> int:
    """Ideas I corrected after they were filed lose the filer's themes (not the archive's) and get filed again:
    the first filing may have rested on Claude's misreading."""
    ids = [r[0] for r in conn.execute(
        """SELECT x.idea_id FROM im.idea_text x JOIN im.theme_checks c USING (idea_id)
           WHERE x.corrected_at > c.checked_at""")]
    if ids:
        with conn.transaction():
            conn.execute("""DELETE FROM im.idea_themes WHERE idea_id = ANY(%s) AND origin IN ('assigned', 'proposed')""",
                         (ids,))
            conn.execute("DELETE FROM im.theme_checks WHERE idea_id = ANY(%s)", (ids,))
    return len(ids)


def file_stage(conn, client, monthly_cap_usd: float = 20.0, chat: ModelConfig = ModelConfig()) -> dict:
    """`chat`, here and in the other stages: the model and effort ([themes] in im.toml), part of every version."""
    refiled = refile_corrected(conn)

    tv = themes_version(conn, chat)
    themes = current_themes(conn)
    todo = _unthemed(conn, tv)
    stats = {"themes_version": tv, "pending": len(todo), "refiled_after_correction": refiled, "assigned": 0, "none": 0,
             "refused": 0, "failed": 0, "stopped": None}
    if not themes or not todo:
        return stats
    letters = _letters(len(themes))
    by_letter = dict(zip(letters, (t[0] for t in themes), strict=True))
    header = "Themes:\n" + "\n".join(f"{L}. {n}" + (f": {d}" if d else "") for L, (_, n, d) in zip(letters, themes))
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        text = header + "\n\nIdeas:\n" + "\n".join(f"{n}. {t}: {s}" for n, (_, t, s) in enumerate(batch, 1))
        try:
            a = ask(conn, client, chat, FILE_SYSTEM, FILE_SCHEMA, text, f"themes-file:{tv}", [b[0] for b in batch],
                    monthly_cap_usd)
        except llm.Stop as e:
            stats["stopped"] = str(e)[:200]
            break
        if a.refused or a.failed:   # no answer isn't "fits no theme": filed on the next run
            stats["refused" if a.refused else "failed"] += 1
            continue
        got = {x["idea"]: x["themes"] for x in a.data["assignments"]}
        with conn.transaction():
            for n, (idea_id, _, _) in enumerate(batch, 1):
                blocked = {b for (b,) in conn.execute(
                    "SELECT theme_id FROM im.idea_theme_blocks WHERE idea_id = %s", (idea_id,))}
                picks = [by_letter[L] for L in dict.fromkeys(got.get(n, []))
                         if L in by_letter and by_letter[L] not in blocked][:MAX_THEMES_PER_IDEA]
                for theme_id in picks:
                    conn.execute("""INSERT INTO im.idea_themes (idea_id, theme_id, origin) VALUES (%s, %s, 'assigned')
                                    ON CONFLICT DO NOTHING""", (idea_id, theme_id))
                result = "assigned" if picks else "none"
                conn.execute(
                    """INSERT INTO im.theme_checks (idea_id, themes_version, result) VALUES (%s, %s, %s)
                       ON CONFLICT (idea_id) DO UPDATE SET themes_version = excluded.themes_version,
                         result = excluded.result, checked_at = now()""", (idea_id, tv, result))
                stats[result] += 1
    return stats


def propose_stage(conn, client, monthly_cap_usd: float = 20.0, chat: ModelConfig = ModelConfig()) -> dict:
    """Group ideas that fit no theme into proposed themes, once enough new ones have piled up."""
    loose = conn.execute(
        """SELECT d.idea_id, d.title, d.statement, c.proposal_seen FROM pub.ideas d
           JOIN im.theme_checks c ON c.idea_id = d.idea_id AND c.result = 'none'
           WHERE NOT EXISTS (SELECT 1 FROM im.idea_themes it WHERE it.idea_id = d.idea_id)
           ORDER BY d.created_at""").fetchall()
    fresh = sum(not seen for *_, seen in loose)
    stats = {"unthemed": len(loose), "unseen": fresh, "proposed": 0, "failed": 0, "stopped": None}
    if fresh < PROPOSE_MIN:
        return stats
    rejected = [n for (n,) in conn.execute("SELECT name FROM im.theme_rejections ORDER BY name")]
    text = ("Existing themes:\n" + "\n".join(f"- {n}" for _, n, _ in current_themes(conn))
            + "\n\nRejected names:\n" + ("\n".join(f"- {n}" for n in rejected) or "(none)")
            + "\n\nIdeas that fit no theme:\n" + "\n".join(f"{n}. {t}: {s}" for n, (_, t, s, _) in enumerate(loose, 1)))
    pv = version(PROPOSE_SYSTEM, PROPOSE_SCHEMA, chat.model, chat.effort)
    try:
        a = ask(conn, client, chat, PROPOSE_SYSTEM, PROPOSE_SCHEMA, text, f"themes-propose:{pv}",
                [x[0] for x in loose], monthly_cap_usd)
    except llm.Stop as e:
        stats["stopped"] = str(e)[:200]
        return stats
    if a.failed:   # the ideas stay unseen, for the next run
        stats["failed"] = 1
        return stats
    answer = a.data
    taken = {n.lower() for _, n, _ in current_themes(conn)} | {n.lower() for n in rejected}
    with conn.transaction():
        for t in (answer or {"themes": []})["themes"]:
            members = [loose[n - 1][0] for n in dict.fromkeys(t["ideas"]) if 1 <= n <= len(loose)]
            name = t["name"].strip()
            if len(members) < MIN_GROUP or not name or name.lower() in taken:
                continue
            taken.add(name.lower())
            theme = conn.execute("""INSERT INTO im.themes (name, description, origin, pinned)
                                    VALUES (%s, %s, 'clustered', false) RETURNING theme_id""",
                                 (name, t["description"].strip() or None)).fetchone()[0]
            for idea in members:
                conn.execute("""INSERT INTO im.idea_themes (idea_id, theme_id, origin) VALUES (%s, %s, 'proposed')
                                ON CONFLICT DO NOTHING""", (idea, theme))
            stats["proposed"] += 1
        conn.execute("UPDATE im.theme_checks SET proposal_seen = true WHERE idea_id = ANY(%s)", ([x[0] for x in loose],))
    return stats


# --- My feedback ------------------------------------------------------------------------------------------

THEME_KINDS = ("pin_theme", "unpin_theme", "rename_theme", "reject_theme", "merge_theme", "split_theme", "file_idea",
               "unfile_idea")


def apply_feedback(conn) -> dict:
    """Apply theme events not applied yet, in order. New events apply on insert (the trigger); this catches any
    inserted before it existed. Both go through im.apply_theme_event."""
    done: dict = {}
    rows = conn.execute(
        """SELECT f.event_id FROM pub.feedback_events f
           WHERE f.kind = ANY(%s) AND NOT EXISTS (SELECT 1 FROM im.feedback_applied a WHERE a.event_id = f.event_id)
           ORDER BY f.event_id""", (list(THEME_KINDS),)).fetchall()
    for (event_id,) in rows:
        with conn.transaction():
            (result,) = conn.execute(
                """INSERT INTO im.feedback_applied (event_id, result)
                   SELECT f.event_id, im.apply_theme_event(f) FROM pub.feedback_events f WHERE f.event_id = %s
                   RETURNING result""", (event_id,)).fetchone()
            done[result] = done.get(result, 0) + 1
    return done


def describe_stage(conn, client, monthly_cap_usd: float = 20.0, chat: ModelConfig = ModelConfig()) -> dict:
    """Give themes without a description (ones I split off) one, from their name and up to 12 of their ideas."""
    todo = conn.execute("SELECT theme_id, name FROM im.themes WHERE description IS NULL ORDER BY created_at").fetchall()
    stats = {"pending": len(todo), "described": 0, "failed": 0, "stopped": None}
    if not todo:
        return stats
    blocks, ideas = [], []
    for n, (theme_id, name) in enumerate(todo, 1):
        rows = conn.execute("""SELECT d.idea_id, d.title FROM pub.ideas d JOIN im.idea_themes it USING (idea_id)
                               WHERE it.theme_id = %s ORDER BY d.n_evidence DESC, d.title LIMIT 12""",
                            (theme_id,)).fetchall()
        ideas += [r[0] for r in rows]
        blocks.append(f"{n}. {name}\n" + ("\n".join(f"   - {t}" for _, t in rows) or "   (no ideas yet)"))
    text = "Themes:\n" + "\n".join(blocks)
    try:
        a = ask(conn, client, chat, DESCRIBE_SYSTEM, DESCRIBE_SCHEMA, text,
                f"themes-describe:{version(DESCRIBE_SYSTEM, DESCRIBE_SCHEMA, chat.model, chat.effort)}", ideas,
                monthly_cap_usd)
    except llm.Stop as e:
        stats["stopped"] = str(e)[:200]
        return stats
    stats["failed"] = int(a.failed)   # undescribed, so asked again next run
    answer = a.data
    with conn.transaction():
        for t in (answer or {"themes": []})["themes"]:
            desc = t["description"].strip()[:300]
            if 1 <= t["theme"] <= len(todo) and desc:
                stats["described"] += conn.execute(
                    "UPDATE im.themes SET description = %s WHERE theme_id = %s AND description IS NULL",
                    (desc, todo[t["theme"] - 1][0])).rowcount
    return stats


def find(conn, ref: str):
    """A theme by name (case-insensitive prefix) or id prefix. Raises ValueError unless exactly one matches."""
    rows = conn.execute("""SELECT theme_id, name FROM im.themes
                           WHERE lower(name) LIKE lower(%s) OR theme_id::text LIKE lower(%s)""",
                        (ref + "%", ref + "%")).fetchall()
    exact = [r for r in rows if r[1].lower() == ref.lower()]
    if len(exact) == 1 or len(rows) == 1:
        return (exact or rows)[0][0]
    raise ValueError(f"{ref!r} matches {len(rows)} themes" + (f": {', '.join(r[1] for r in rows[:5])}" if rows else ""))


def record(conn, kind: str, theme_id, payload: dict | None = None) -> None:
    conn.execute("INSERT INTO pub.feedback_events (kind, theme_id, payload, source) VALUES (%s, %s, %s, 'cli')",
                 (kind, theme_id, json.dumps(payload or {})))


def listing(conn) -> str:
    rows = conn.execute("""SELECT name, origin, pinned, n_ideas, description FROM pub.themes
                           ORDER BY pinned DESC, n_ideas DESC, name""").fetchall()
    out = []
    for name, origin, pinned, n, desc in rows:
        state = "pinned" if pinned else ("proposed" if origin == "clustered" else "unpinned")
        out.append(f"{n:>4}  {state:<9} {name}" + (f"\n            {desc}" if desc and origin == "clustered" else ""))
    unthemed = conn.execute("""SELECT count(*) FROM pub.ideas d WHERE NOT EXISTS (
                                 SELECT 1 FROM im.idea_themes it WHERE it.idea_id = d.idea_id)""").fetchone()[0]
    return "\n".join(out + [f"{unthemed:>4}  ideas with no theme"])
