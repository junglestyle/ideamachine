"""Themes (ROADMAP Phase 2, slice 4): my categories, proposed by the system and steered by me.

- The filer puts each unthemed idea into up to two current themes, or none. It looks at an idea again only when
  the set of themes changes.
- The proposer, once enough ideas fit no theme, asks Claude to group them into new themes. These arrive unpinned
  (`origin = 'clustered'`) and stay proposals until I pin, rename or reject them.
- My theme feedback (pin, unpin, rename, reject) arrives as events in pub.feedback_events, from Lattice or the CLI,
  and is applied here. Rejected names are never proposed again.
"""

import hashlib
import json
import string

from im import egress

MODEL, EFFORT = "claude-opus-5-5", "low"
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


def themes_version(conn) -> str:
    return version(FILE_SYSTEM, FILE_SCHEMA, MODEL, EFFORT,
                   sorted((str(t), n, d) for t, n, d in current_themes(conn)))


def _letters(n: int) -> list[str]:
    return [string.ascii_uppercase[i] if i < 26 else f"Z{i - 25}" for i in range(n)]


def _call(client, system: str, schema: dict, text: str):
    r = client.beta.messages.create(
        model=MODEL, max_tokens=16000, system=system, messages=[{"role": "user", "content": text}],
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-06-01"], fallbacks=[{"model": "claude-opus-4-8"}])
    if r.stop_reason == "refusal":
        return None, r
    return json.loads(next(b.text for b in r.content if b.type == "text")), r


def _log(conn, text: str, prompt_version: str, idea_ids: list, r) -> None:
    from im.extract import _cost

    segs = [s for (s,) in conn.execute(
        """SELECT DISTINCT unnest(i.source_segment_ids) FROM im.idea_evidence ev JOIN im.items i USING (item_id)
           WHERE ev.idea_id = ANY(%s)""", (idea_ids,))]
    served = getattr(r, "model", MODEL) or MODEL
    conn.execute(
        """INSERT INTO im.egress_log (episode_id, segment_ids, model, prompt_version, privacy_policy_version, payload,
             payload_sha256, request_id, stop_reason, served_by, input_tokens, output_tokens, cost_usd)
           VALUES (NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (segs, MODEL, prompt_version, egress.POLICY_VERSION, text, hashlib.sha256(text.encode()).hexdigest(),
         getattr(r, "_request_id", None), r.stop_reason, served, r.usage.input_tokens, r.usage.output_tokens,
         _cost(served, r.usage)))


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


def file_stage(conn, client, monthly_cap_usd: float = 20.0) -> dict:
    from im.extract import spent_this_month

    refiled = refile_corrected(conn)

    tv = themes_version(conn)
    themes = current_themes(conn)
    todo = _unthemed(conn, tv)
    stats = {"themes_version": tv, "pending": len(todo), "refiled_after_correction": refiled, "assigned": 0, "none": 0,
             "stopped": None}
    if not themes or not todo:
        return stats
    letters = _letters(len(themes))
    by_letter = dict(zip(letters, (t[0] for t in themes), strict=True))
    header = "Themes:\n" + "\n".join(f"{L}. {n}" + (f": {d}" if d else "") for L, (_, n, d) in zip(letters, themes))
    for start in range(0, len(todo), BATCH):
        if spent_this_month(conn) >= monthly_cap_usd:
            stats["stopped"] = f"monthly cap of ${monthly_cap_usd:.2f} reached"
            break
        batch = todo[start:start + BATCH]
        text = header + "\n\nIdeas:\n" + "\n".join(f"{n}. {t}: {s}" for n, (_, t, s) in enumerate(batch, 1))
        answer, r = _call(client, FILE_SYSTEM, FILE_SCHEMA, text)
        _log(conn, text, f"themes-file:{tv}", [b[0] for b in batch], r)
        got = {a["idea"]: a["themes"] for a in (answer or {"assignments": []})["assignments"]}
        with conn.transaction():
            for n, (idea_id, _, _) in enumerate(batch, 1):
                picks = [by_letter[L] for L in dict.fromkeys(got.get(n, [])) if L in by_letter][:MAX_THEMES_PER_IDEA]
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


def propose_stage(conn, client, monthly_cap_usd: float = 20.0) -> dict:
    """Group ideas that fit no theme into proposed themes, once enough new ones have piled up."""
    from im.extract import spent_this_month

    loose = conn.execute(
        """SELECT d.idea_id, d.title, d.statement, c.proposal_seen FROM pub.ideas d
           JOIN im.theme_checks c ON c.idea_id = d.idea_id AND c.result = 'none'
           WHERE NOT EXISTS (SELECT 1 FROM im.idea_themes it WHERE it.idea_id = d.idea_id)
           ORDER BY d.created_at""").fetchall()
    fresh = sum(not seen for *_, seen in loose)
    stats = {"unthemed": len(loose), "unseen": fresh, "proposed": 0, "stopped": None}
    if fresh < PROPOSE_MIN:
        return stats
    if spent_this_month(conn) >= monthly_cap_usd:
        stats["stopped"] = f"monthly cap of ${monthly_cap_usd:.2f} reached"
        return stats
    rejected = [n for (n,) in conn.execute("SELECT name FROM im.theme_rejections ORDER BY name")]
    text = ("Existing themes:\n" + "\n".join(f"- {n}" for _, n, _ in current_themes(conn))
            + "\n\nRejected names:\n" + ("\n".join(f"- {n}" for n in rejected) or "(none)")
            + "\n\nIdeas that fit no theme:\n" + "\n".join(f"{n}. {t}: {s}" for n, (_, t, s, _) in enumerate(loose, 1)))
    pv = version(PROPOSE_SYSTEM, PROPOSE_SCHEMA, MODEL, EFFORT)
    answer, r = _call(client, PROPOSE_SYSTEM, PROPOSE_SCHEMA, text)
    _log(conn, text, f"themes-propose:{pv}", [x[0] for x in loose], r)
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

THEME_KINDS = ("pin_theme", "unpin_theme", "rename_theme", "reject_theme")


def apply_feedback(conn) -> dict:
    """Apply theme events not applied yet, in order. Other kinds wait for the slices that use them."""
    done: dict = {}
    rows = conn.execute(
        """SELECT f.event_id, f.kind, f.theme_id, f.payload FROM pub.feedback_events f
           WHERE f.kind = ANY(%s) AND NOT EXISTS (SELECT 1 FROM im.feedback_applied a WHERE a.event_id = f.event_id)
           ORDER BY f.event_id""", (list(THEME_KINDS),)).fetchall()
    for event_id, kind, theme_id, payload in rows:
        with conn.transaction():
            row = conn.execute("SELECT name, origin, pinned FROM im.themes WHERE theme_id = %s", (theme_id,)).fetchone()
            if row is None:
                result = "no such theme"
            elif kind == "pin_theme":
                conn.execute("UPDATE im.themes SET pinned = true WHERE theme_id = %s", (theme_id,))
                result = "pinned"
            elif kind == "unpin_theme":
                conn.execute("UPDATE im.themes SET pinned = false WHERE theme_id = %s", (theme_id,))
                result = "unpinned"
            elif kind == "rename_theme":
                name = (payload or {}).get("name", "").strip()
                if name:
                    conn.execute("UPDATE im.themes SET name = %s WHERE theme_id = %s", (name, theme_id))
                result = "renamed" if name else "no name given"
            else:  # reject_theme: only proposals; pinned themes and the archive's are mine
                if row[1] == "clustered" and not row[2]:
                    conn.execute("INSERT INTO im.theme_rejections (name) VALUES (%s) ON CONFLICT DO NOTHING", (row[0],))
                    conn.execute("DELETE FROM im.themes WHERE theme_id = %s", (theme_id,))
                    result = "rejected"
                else:
                    result = "not rejected: only unpinned proposals can be"
            conn.execute("INSERT INTO im.feedback_applied (event_id, result) VALUES (%s, %s)", (event_id, result))
            done[result] = done.get(result, 0) + 1
    return done


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
