"""Weekly synthesis (ROADMAP Phase 2, slice 6): once a week Claude reads what the lattice gained and writes a digest.

A week runs Monday 00:00 to Monday 00:00 in local time (the machine's TZ). The run writes the last completed week's
digest once; `im digest --again` rewrites it. Claude sees ideas, not raw speech: each idea's title and statement
(my corrections win), its themes, how often it was said, its rank, and what I did with it that week (stars, links,
comparisons). Speakers aren't named, though an idea's text, and so the digest, may name people: it's written for
me. The digest keeps the segment IDs behind every idea it was shown, so forgetting any of that speech deletes it.
"""

from datetime import date, datetime, time, timedelta

from im import themes

TOP = 15          # the top of the ranking, beside the week's own ideas, for context
KINDS = ("recurring", "convergence", "sleeper", "project", "outside_value")

SYSTEM = """You write my weekly digest from my idea lattice: ideas captured from my conversations and notes, \
matched together over time, grouped into themes and ranked. You get the ideas said this week, the top of the \
ranking for context, the links between them, and what I did this week (stars, links, comparisons). Write 3 to 7 \
points, each one of these kinds:
- recurring: an idea or cluster I keep coming back to, and what that might mean.
- convergence: separate ideas, often from different themes or conversations, that point at the same thing.
- sleeper: an idea that matters more than it seemed when I said it (plain-sounding but general, or a key piece \
of something larger).
- project: something forming that I could build, write or do.
- outside_value: an idea worth something to a thoughtful stranger who doesn't know me: worth writing up or \
sharing, and to whom.
Every point cites the ideas it rests on, by number, and claims only what those ideas support. Prefer the week's \
ideas; bring in older ones where they connect. Skip a kind rather than force it. Write to me, plainly: a short \
title, then two to four sentences. Add a one- or two-sentence summary of the week. If the week was thin, say so \
briefly and write fewer points."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "points": {"type": "array", "items": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": list(KINDS)}, "title": {"type": "string"},
                           "body": {"type": "string"}, "ideas": {"type": "array", "items": {"type": "integer"}}},
            "required": ["kind", "title", "body", "ideas"], "additionalProperties": False}},
    },
    "required": ["summary", "points"], "additionalProperties": False,
}


def prompt_version() -> str:
    return themes.version(SYSTEM, SCHEMA, themes.MODEL, themes.EFFORT)


def last_week(today: date | None = None) -> date:
    """The Monday that starts the last completed week."""
    today = today or datetime.now().astimezone().date()
    return today - timedelta(days=today.weekday() + 7)


def _period(week: date) -> tuple[datetime, datetime]:
    start = datetime.combine(week, time()).astimezone()
    return start, datetime.combine(week + timedelta(days=7), time()).astimezone()


def _said(conn, start: datetime, end: datetime) -> list[tuple]:
    """Ideas shown in Lattice with captures said in the week: id, title, statement, themes, said that week, said in
    all, first said, score, starred. Most said first, then by rank."""
    return conn.execute(
        """SELECT d.idea_id, d.title, d.statement, d.themes, w.n, d.n_evidence, d.first_said, s.score, d.starred
           FROM pub.ideas d
           JOIN (SELECT idea_id, count(*) AS n FROM pub.evidence
                 WHERE said_at >= %s AND said_at < %s AND verdict IS DISTINCT FROM 'discard' GROUP BY idea_id) w
             USING (idea_id)
           LEFT JOIN pub.idea_scores s USING (idea_id)
           ORDER BY w.n DESC, s.score DESC NULLS LAST, d.idea_id""", (start, end)).fetchall()


def _top(conn, exclude: list) -> list[tuple]:
    return conn.execute(
        """SELECT d.idea_id, d.title, d.statement, d.themes, 0, d.n_evidence, d.first_said, s.score, d.starred
           FROM pub.idea_scores s JOIN pub.ideas d USING (idea_id)
           WHERE s.score IS NOT NULL AND NOT d.idea_id = ANY(%s)
           ORDER BY s.score DESC, d.idea_id LIMIT %s""", (exclude, TOP)).fetchall()


def _line(n: int, row: tuple) -> str:
    _, title, statement, theme_names, week_n, total, first, score, starred = row
    facts = [f"themes: {', '.join(theme_names)}" if theme_names else "no theme"]
    if week_n:
        facts.append(f"said {week_n}x this week, {total}x in all")
    elif total:
        facts.append(f"said {total}x in all")
    if first:
        facts.append(f"first said {first.astimezone():%Y-%m-%d}")
    if score is not None:
        facts.append(f"score {float(score):.2f}")
    if starred:
        facts.append("starred")
    return f"{n}. {title}: {statement} [{'; '.join(facts)}]"


def payload(conn, week: date) -> tuple[str, list]:
    """The text Claude reads for the week, and the idea ids in the order it numbers them."""
    start, end = _period(week)
    said = _said(conn, start, end)
    top = _top(conn, [r[0] for r in said])
    ideas = said + top
    num = {r[0]: n for n, r in enumerate(ideas, 1)}
    links = conn.execute(
        """SELECT a, b, kind FROM pub.connections WHERE a = ANY(%s) AND b = ANY(%s) ORDER BY a, b""",
        (list(num), list(num))).fetchall()
    events = conn.execute(
        """SELECT f.kind, f.idea_id, f.other_idea_id, f.payload->>'winner' FROM pub.feedback_events f
           WHERE f.at >= %s AND f.at < %s AND f.kind IN ('star', 'link', 'idea_compare')
           ORDER BY f.event_id""", (start, end)).fetchall()
    activity = []
    for kind, a, b, winner in events:
        if a not in num or (b is not None and b not in num):
            continue
        if kind == "star":
            activity.append(f"starred {num[a]}")
        elif kind == "link":
            activity.append(f"linked {num[a]} and {num[b]}")
        elif winner == "tie":
            activity.append(f"compared {num[a]} and {num[b]}: a tie")
        else:
            better, worse = (a, b) if winner == "first" else (b, a)
            activity.append(f"preferred {num[better]} over {num[worse]}")
    text = [f"Week of {week:%Y-%m-%d} to {week + timedelta(days=6):%Y-%m-%d}.", "",
            f"Said this week ({len(said)} ideas):", *(_line(num[r[0]], r) for r in said), "",
            "Top of the ranking, not said this week:", *(_line(num[r[0]], r) for r in top)]
    if links:
        text += ["", "Links: " + "; ".join(f"{num[a]}-{num[b]} ({k})" for a, b, k in links)]
    if activity:
        text += ["", "What I did this week: " + "; ".join(activity) + "."]
    return "\n".join(text), [r[0] for r in ideas]


def digest_stage(conn, client, monthly_cap_usd: float = 20.0, week: date | None = None, again: bool = False) -> dict:
    """Write the week's digest (by default the last completed week) unless it exists. A week with nothing said
    gets a short digest without asking Claude."""
    from im.extract import spent_this_month

    week = week or last_week()
    stats = {"week": week.isoformat(), "written": False, "points": 0, "stopped": None}
    if conn.execute("SELECT 1 FROM im.digests WHERE week_start = %s", (week,)).fetchone() and not again:
        return stats
    start, end = _period(week)
    text, ids = payload(conn, week)
    pv = prompt_version()
    if not _said(conn, start, end):
        answer, segs = {"summary": "Nothing new was said this week.", "points": []}, []
    else:
        if spent_this_month(conn) >= monthly_cap_usd:
            stats["stopped"] = f"monthly cap of ${monthly_cap_usd:.2f} reached"
            return stats
        answer, r = themes._call(client, SYSTEM, SCHEMA, text)
        themes._log(conn, text, pv, ids, r)
        if answer is None:
            stats["stopped"] = "refused"
            return stats
        segs = [s for (s,) in conn.execute(
            """SELECT DISTINCT unnest(i.source_segment_ids) FROM im.idea_evidence ev JOIN im.items i USING (item_id)
               WHERE ev.idea_id = ANY(%s)""", (ids,))]
    with conn.transaction():
        conn.execute("DELETE FROM im.digests WHERE week_start = %s", (week,))
        (digest_id,) = conn.execute(
            """INSERT INTO im.digests (week_start, period_start, period_end, summary, prompt_version, segment_ids)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING digest_id""",
            (week, start, end, answer["summary"][:2000], pv, segs)).fetchone()
        ord_ = 0
        for p in answer["points"]:
            cited = [ids[n - 1] for n in dict.fromkeys(p["ideas"]) if 1 <= n <= len(ids)]
            if not cited or p["kind"] not in KINDS:   # a point must rest on ideas it was shown
                continue
            ord_ += 1
            conn.execute(
                """INSERT INTO im.digest_points (digest_id, ord, kind, title, body, idea_ids)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (digest_id, ord_, p["kind"], p["title"][:200], p["body"][:2000], cited))
    stats.update(written=True, points=ord_, digest_id=digest_id)
    return stats


def show(conn, week: date | None = None) -> str:
    row = conn.execute(
        """SELECT digest_id, week_start, summary, points FROM pub.digests
           WHERE %s::date IS NULL OR week_start = %s ORDER BY week_start DESC LIMIT 1""", (week, week)).fetchone()
    if not row:
        return "No digest yet."
    digest_id, week_start, summary, points = row
    titles = dict(conn.execute("SELECT idea_id::text, title FROM pub.ideas"))
    out = [f"Week of {week_start:%Y-%m-%d}", "", summary]
    for p in points:
        out += ["", f"{p['point']}. [{p['kind']}] {p['title']}", p["body"],
                *(f"   - {titles.get(i, i)}" for i in p["idea_ids"])]
    return "\n".join(out)
