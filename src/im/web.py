"""The neutral view (docs/decisions/0007-neutral-proposals.md; the start of Emanation, decision 0005).

Every cluster that passed the gates, newest first, each showing what's known about it: who said it (me, me by
diarization, not me; never a name), in how many conversations, when, its maturity, how I kept it, and its edges.
No score orders or filters anything. From a card I keep a cluster (canonical or pinned), un-keep it or dismiss it,
as feedback events, the same kinds Lattice's keep_idea wrote.

Two more pages draw the same cards (docs/design/m6-emanation.md): Trails (`/trails`), the sky's map with each
conversation as the path it took from thought to thought, and Crossroads (`/crossroads/<id>`), one card with what was
said around it and what's like it; and Echoes (`/echoes`), every connection between thoughts said in different
conversations. They only read.

Demo mode (a toggle, or `im web --demo` for nothing else) reads the `demo` views instead of `pub`'s, so nothing
sensitive is there to show: the sky keeps a sensitive cluster's star, blank, with its edges, and the lists (the
feed, a card's quotes and connections) leave it out. Nothing can be kept or dismissed in demo mode.

It connects as its own role (IM_WEB_DATABASE_URL, role lattice_app: read pub, append feedback), never as
im_pipeline, and logs in with its token (IM_WEB_TOKEN), exchanged once per device for a signed cookie. Loopback only:
tailscale serve publishes it to the tailnet (install/nas.sh).
"""

import hashlib
import hmac
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

import psycopg

DATABASE_ENV = "IM_WEB_DATABASE_URL"
TOKEN_ENV = "IM_WEB_TOKEN"
COOKIE = "emanation_session"
DEMO_COOKIE = "emanation_demo"
PAGE = 50
SHOW = {  # which maturities the feed shows, and the tab's name
    "candidates": (("candidate", "idea"), "candidates and ideas"),
    "ideas": (("idea",), "ideas"),
    "all": (("observation", "candidate", "idea"), "everything"),
}
ACTIONS = {"canonical": ("idea_keep", '{"as": "canonical"}'), "pinned": ("idea_keep", '{"as": "pinned"}'),
           "unkeep": ("idea_unkeep", "{}"), "dismiss": ("idea_dismiss", "{}")}
SPEAKERS = {"me": "me", "me_diar": "me (diarization)", "not_me": "not me"}
# The app's icons and manifest (dev/make_icons.py draws them). Served without a login: a browser fetches the manifest
# without cookies, and the mark is no secret.
STATIC = {"/manifest.webmanifest": "application/manifest+json", "/icon.svg": "image/svg+xml",
          "/icon-192.png": "image/png", "/icon-512.png": "image/png", "/apple-touch-icon.png": "image/png"}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _token() -> str:
    t = os.environ.get(TOKEN_ENV, "")
    if len(t) < 16:
        raise SystemExit(f"{TOKEN_ENV} is not set, or shorter than 16 characters")
    return t


def _session() -> str:
    return hmac.new(_token().encode(), b"emanation-session-v1", hashlib.sha256).hexdigest()


def _cookie(cookie_header: str | None, name: str) -> str | None:
    for part in (cookie_header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


def logged_in(cookie_header: str | None) -> bool:
    value = _cookie(cookie_header, COOKIE)
    return value is not None and hmac.compare_digest(value.encode(), _session().encode())


def _views(demo: bool) -> str:
    """The prefix of the views with words a page reads: pub.demo_ in demo mode, else pub."""
    return "pub.demo_" if demo else "pub."


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _when(ts) -> str:
    return ts.strftime("%Y-%m-%d") if ts else "—"


def _facts(r: dict) -> str:
    kept = f"kept as {r['kept_as']}" if r["kept_as"] else r["maturity"]
    said = [f"{n} {label}" for n, label in ((r["by_me"], "me"), (r["by_me_diar"], "me (diarization)"),
                                            (r["by_others"], "not me")) if n]
    parts = [kept + (" · proposed" if r["proposed"] else ""), ", ".join(said) or r["origin"],
             f"{r['conversations']} conversation{'s' * (r['conversations'] != 1)}" if r["conversations"] else "",
             f"{_when(r['first_said'])} → {_when(r['last_seen'])}" if r["first_said"] else _when(r["last_seen"]),
             f"{r['links']} edge{'s' * (r['links'] != 1)}" if r["links"] else ""]
    return " · ".join(p for p in parts if p)


def _rows(conn, sql: str, args=()) -> list[dict]:
    cur = conn.execute(sql, args)
    names = [c.name for c in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def feed(conn, show: str = "candidates", page: int = 0, demo: bool = False) -> str:
    maturities = SHOW.get(show, SHOW["candidates"])[0]
    rows = _rows(conn, f"""SELECT * FROM {_views(demo)}cards WHERE maturity = ANY(%s) {"AND NOT redacted" if demo else ""}
                           ORDER BY last_seen DESC, idea_id LIMIT %s OFFSET %s""",
                 (list(maturities), PAGE + 1, page * PAGE))
    tabs = " ".join(f'<a href="/?show={k}"{" aria-current=page" if k == show else ""}>{name}</a>'
                    for k, (_, name) in SHOW.items()) + ' <a href="/sky">sky</a> <a href="/trails">trails</a>' \
        + ' <a href="/echoes">echoes</a> <a href="/crossroads">crossroads</a>' \
        + f' <a href="/demo?on={0 if demo else 1}">{"leave demo mode" if demo else "demo mode"}</a>'
    cards = "".join(f'<li><a href="/idea/{r["idea_id"]}">{_e(r["title"])}</a><p>{_e(r["statement"])}</p>'
                    f'<small>{_e(_facts(r))}</small></li>' for r in rows[:PAGE])
    more = f'<a href="/?show={_e(show)}&page={page + 1}">older</a>' if len(rows) > PAGE else ""
    return _page("Idea Machine", f"<nav>{tabs}</nav><ol>{cards or '<li>Nothing here.</li>'}</ol>{more}")


def card(conn, idea_id: str, demo: bool = False) -> str | None:
    v = _views(demo)
    shown = "AND NOT c.redacted" if demo else ""
    rows = _rows(conn, f"SELECT * FROM {v}cards WHERE idea_id = %s", (idea_id,))
    if not rows:
        return None
    r = rows[0]
    if demo and r["redacted"]:
        return _page("Hidden", f"<h1>Hidden in demo mode</h1><small>{_e(_facts(r))}</small>")
    quotes = _rows(conn, f"""SELECT speaker, quote, said_at FROM {v}card_evidence WHERE idea_id = %s
                             ORDER BY said_at, item_id""", (idea_id,))
    links = _rows(conn, f"""SELECT l.kind, l.kinds, c.idea_id, c.title FROM pub.card_links l
                            JOIN {v}cards c ON c.idea_id = CASE WHEN l.a = %s THEN l.b ELSE l.a END
                            WHERE %s IN (l.a, l.b) {shown} ORDER BY l.kind, c.title""", (idea_id, idea_id))
    lineage = _rows(conn, f"""SELECT CASE WHEN l.from_id = %s THEN l.kind WHEN l.kind = 'evolves' THEN 'evolved by'
                                          ELSE 'synthesized in' END AS kind,
                                     c.idea_id, c.title FROM pub.card_lineage l
                              JOIN {v}cards c ON c.idea_id = CASE WHEN l.from_id = %s THEN l.to_id ELSE l.from_id END
                              WHERE %s IN (l.from_id, l.to_id) {shown} ORDER BY 1, c.title""",
                    (idea_id, idea_id, idea_id))
    near = _rows(conn, f"""SELECT c.idea_id, c.title, n.similarity FROM pub.card_neighbours(%s) n
                           JOIN {v}cards c USING (idea_id) WHERE true {shown} ORDER BY n.similarity DESC""",
                 (idea_id,))
    buttons = "".join(f'<button name="action" value="{a}">{a}</button>' for a in ACTIONS)
    body = [f"<h1>{_e(r['title'])}</h1><p>{_e(r['statement'])}</p><small>{_e(_facts(r))}</small>",
            "" if demo else f'<form method="post" action="/idea/{idea_id}">{buttons}</form>',
            f'<p><a href="/sky?seed={idea_id}">Light the sky from here</a> · '
            f'<a href="/trails?at={idea_id}">Its trail</a> · <a href="/crossroads/{idea_id}">Its crossroads</a></p>']
    if r["my_note"]:
        body.append(f"<h2>My note</h2><p>{_e(r['my_note'])}</p>")
    body.append("<h2>Said</h2><ol>" + "".join(
        f"<li><blockquote>{_e(q['quote'])}</blockquote><small>{_e(SPEAKERS.get(q['speaker'], q['speaker']))} · "
        f"{_when(q['said_at'])}</small></li>" for q in quotes) + "</ol>" if quotes else "")
    body.append("<h2>Connections</h2><ul>" + "".join(
        f'<li>{_e(", ".join([x["kind"], *x["kinds"]]))}: <a href="/idea/{x["idea_id"]}">{_e(x["title"])}</a></li>'
        for x in links) + "</ul>" if links else "")
    body.append("<h2>Lineage</h2><ul>" + "".join(
        f'<li>{_e(x["kind"])}: <a href="/idea/{x["idea_id"]}">{_e(x["title"])}</a></li>' for x in lineage)
        + "</ul>" if lineage else "")
    body.append("<h2>Near by meaning</h2><ul>" + "".join(
        f'<li><a href="/idea/{x["idea_id"]}">{_e(x["title"])}</a> <small>{x["similarity"]:.2f}</small></li>'
        for x in near) + "</ul>" if near else "")
    return _page(r["title"], "".join(body))


SIZES = {"observation": 0, "candidate": 1, "idea": 2}   # a canonical idea is one bigger than a pinned one


def sky(conn, seed: str | None = None, demo: bool = False) -> str | None:
    """Every card at its place on the one map (decision 0005, amended). With a seed, a lens lights what's relevant to
    it; without one, the encodings light everything. None if the seed isn't on the map. In demo mode a sensitive
    card keeps its star, blank. The page loads the map once: lighting it from another card fetches only that view's
    relevance (sky_lens)."""
    v = _views(demo)
    rows = _rows(conn, f"""SELECT c.*, p.x, p.y FROM {v}cards c JOIN pub.card_positions p USING (idea_id)
                           ORDER BY c.idea_id""")
    index = {str(r["idea_id"]): n for n, r in enumerate(rows)}
    if seed is not None and seed not in index:
        return None
    now = datetime.now(timezone.utc)
    stars = [[str(r["idea_id"]), round(float(r["x"]), 4), round(float(r["y"]), 4),
              SIZES[r["maturity"]] + (r["kept_as"] == "canonical"), r["conversations"],
              max((now - r["last_seen"]).days, 0) if r["last_seen"] else 365, r["title"] or "", _facts(r)]
             for r in rows]
    links = sorted({tuple(sorted((index[a], index[b]))) for a, b in conn.execute(
        """SELECT a::text, b::text FROM pub.card_links UNION ALL SELECT from_id::text, to_id::text FROM pub.card_lineage""")
        if a in index and b in index and a != b})
    rel = _relevance(conn, seed, index) if seed else None
    data = json.dumps({"stars": stars, "links": links, "rel": rel, "seed": seed}, separators=(",", ":"))
    title = (rows[index[seed]]["title"] or "Sky") if seed else "Sky"
    return _SKY.replace("{title}", _e(title)).replace("{data}", data.replace("</", "<\\/"))


def _relevance(conn, seed: str, on_map) -> dict[str, float]:
    from im import lens

    return {k: v for k, v in lens.relevance(conn, seed).items() if k in on_map}


def sky_lens(conn, seed: str, demo: bool = False) -> dict | None:
    """What the sky needs to light itself from `seed` without loading the map again: its relevance to every card on
    the map, and the page's title. None if the seed isn't on the map."""
    v = _views(demo)
    rows = _rows(conn, f"""SELECT c.idea_id::text AS id, c.title FROM {v}cards c
                           JOIN pub.card_positions p USING (idea_id)""")
    on_map = {r["id"]: r["title"] for r in rows}
    if seed not in on_map:
        return None
    return {"seed": seed, "rel": _relevance(conn, seed, on_map), "title": on_map[seed] or "Sky"}


WHO = {"me": 0, "me_diar": 1, "not_me": 2}


def trails(conn, at: str | None = None, demo: bool = False) -> str | None:
    """The sky's map, drawn through time (docs/design/m6-emanation.md, Trails): every card where the sky puts it, and
    every moment (pub.card_moments: a card, said in a conversation at a time), so the page can draw each conversation
    as the trail it took across the map. Conversations are sent as numbers, never their ids. `at` opens the page on a
    card's conversation; None if that card isn't on the map. In demo mode a sensitive card keeps its star, blank, and
    has no moments, so a trail passes it by."""
    v = _views(demo)
    rows = _rows(conn, f"""SELECT c.*, p.x, p.y FROM {v}cards c JOIN pub.card_positions p USING (idea_id)
                           ORDER BY c.idea_id""")
    index = {str(r["idea_id"]): n for n, r in enumerate(rows)}
    if at is not None and at not in index:
        return None
    stars = [[str(r["idea_id"]), round(float(r["x"]), 4), round(float(r["y"]), 4),
              SIZES[r["maturity"]] + (r["kept_as"] == "canonical"), r["title"] or "", _facts(r)] for r in rows]
    conversations: dict[str, int] = {}
    moments = [[index[i], conversations.setdefault(c, len(conversations)), int(t.timestamp()), WHO.get(who, 2)]
               for i, c, t, who in conn.execute(
                   f"""SELECT idea_id::text, conversation_id, said_at, speaker FROM {v}card_moments
                       WHERE conversation_id IS NOT NULL AND said_at IS NOT NULL
                       ORDER BY said_at, conversation_id, item_id, observation_id""") if i in index]
    data = json.dumps({"stars": stars, "moments": moments, "at": at}, separators=(",", ":"))
    return _TRAILS.replace("{data}", data.replace("</", "<\\/"))


def _apart(a, b) -> str:
    """How far apart two times are, in words: "4 minutes", "3 days", "5 months"."""
    s = abs((a - b).total_seconds())
    for size, name in ((86400 * 365, "year"), (86400 * 30.4, "month"), (86400, "day"), (3600, "hour"), (60, "minute")):
        if s >= size:
            n = round(s / size)
            return f"{n} {name}{'s' * (n != 1)}"
    return "moments"


def _door(way: str, r: dict, why: str, quotes: dict) -> str:
    quote = quotes.get(r["idea_id"])
    return (f'<a class="door" data-way="{way}" href="/crossroads/{r["idea_id"]}"><small>{_e(why)}</small>'
            f'<b>{_e(r["title"])}</b>' + (f"<q>{_e(quote)}</q>" if quote else "") + "</a>")


ECHOES = 4   # how many doors each way in meaning


def crossroads(conn, idea_id: str, demo: bool = False) -> str | None:
    """One card with the two kinds of neighbor it has (docs/design/m6-emanation.md, Crossroads). Across: what was said
    just before and just after it in its own conversations, usually about something else. Up and down: what's like
    it, said at another time (its connections, lineage and nearest by meaning, less the doors across), earlier above
    and later below. Every door leads to that card's own crossroads. Nothing orders a door but time, and likeness within one direction.
    None if there's no such card; in demo mode a sensitive one says only that it's hidden, and no door leads to one."""
    v = _views(demo)
    shown = "AND NOT c.redacted" if demo else ""
    rows = _rows(conn, f"SELECT * FROM {v}cards WHERE idea_id = %s", (idea_id,))
    if not rows:
        return None
    r = rows[0]
    if demo and r["redacted"]:
        return _page("Hidden", f"<h1>Hidden in demo mode</h1><small>{_e(_facts(r))}</small>")
    # Across: every moment of the conversations this card was said in, in the order said.
    said = _rows(conn, f"""SELECT m.idea_id, m.conversation_id, m.said_at, m.speaker, m.quote, c.title
                           FROM {v}card_moments m JOIN {v}cards c USING (idea_id)
                           WHERE m.conversation_id IN (SELECT conversation_id FROM {v}card_moments WHERE idea_id = %s)
                             AND m.said_at IS NOT NULL
                           ORDER BY m.conversation_id, m.said_at, m.item_id, m.observation_id""", (idea_id,))
    mine = [m for m in said if str(m["idea_id"]) == idea_id]
    before, after, seen = [], [], {idea_id}
    for n, m in enumerate(said):
        if str(m["idea_id"]) != idea_id:
            continue
        for found, step in ((before, -1), (after, 1)):
            k = n + step
            while 0 <= k < len(said) and said[k]["conversation_id"] == m["conversation_id"] \
                    and str(said[k]["idea_id"]) == idea_id:
                k += step
            if 0 <= k < len(said) and said[k]["conversation_id"] == m["conversation_id"] \
                    and (step, said[k]["idea_id"]) not in seen:
                seen.add((step, said[k]["idea_id"]))
                found.append((said[k], f"{_apart(said[k]['said_at'], m['said_at'])} {'earlier' if step < 0 else 'later'}"
                                       ", same conversation"))
    # Up and down: connections and lineage first, then the nearest by meaning, each once.
    like = _rows(conn, f"""SELECT c.idea_id, c.title, c.first_said, 'connected' AS why, 2::real AS similarity
                           FROM pub.card_links l
                           JOIN {v}cards c ON c.idea_id = CASE WHEN l.a = %s THEN l.b ELSE l.a END
                           WHERE %s IN (l.a, l.b) {shown}
                           UNION ALL
                           SELECT c.idea_id, c.title, c.first_said, 'lineage', 2 FROM pub.card_lineage l
                           JOIN {v}cards c ON c.idea_id = CASE WHEN l.from_id = %s THEN l.to_id ELSE l.from_id END
                           WHERE %s IN (l.from_id, l.to_id) {shown}
                           UNION ALL
                           SELECT c.idea_id, c.title, c.first_said, 'near by meaning', n.similarity
                           FROM pub.card_neighbours(%s, %s) n JOIN {v}cards c USING (idea_id) WHERE true {shown}
                           ORDER BY similarity DESC, idea_id""", (idea_id,) * 5 + (4 * ECHOES,))
    earlier, later, met = [], [], {r["idea_id"]} | {x["idea_id"] for x, _ in before + after}
    for x in like:
        if x["idea_id"] in met:
            continue
        met.add(x["idea_id"])
        if r["first_said"] and x["first_said"]:
            past = x["first_said"] < r["first_said"]
            why = f"{x['why']} · {_apart(x['first_said'], r['first_said'])} {'earlier' if past else 'later'}"
        else:
            past, why = False, x["why"]
        side = earlier if past else later
        if len(side) < ECHOES:
            side.append((x, why))
    doors = [x["idea_id"] for x, _ in before + after + earlier + later]
    quotes = dict(conn.execute(f"""SELECT DISTINCT ON (idea_id) idea_id, quote FROM {v}card_moments
                                   WHERE idea_id = ANY(%s) ORDER BY idea_id, said_at, item_id""", (doors,)).fetchall())

    def arm(way: str, name: str, found: list) -> str:
        inner = "".join(_door(way, x, why, quotes) for x, why in found) or "<i>nothing</i>"
        return f'<section class="{way}"><h2>{name}</h2>{inner}</section>'

    here = "".join(f"<blockquote><p>{_e(m['quote'])}</p><small>{_e(SPEAKERS.get(m['speaker'], m['speaker']))} · "
                   f"{_when(m['said_at'])}</small></blockquote>" for m in mine[:3])
    body = (arm("up", "Like it, earlier", earlier) + arm("left", "Just before", before)
            + f'<main><h1>{_e(r["title"])}</h1>{here}<p>{_e(r["statement"])}</p><small>{_e(_facts(r))}</small>'
              f'<nav><a href="/idea/{idea_id}">Card</a><a href="/trails?at={idea_id}">Its trail</a>'
              f'<a href="/sky?seed={idea_id}">Light the sky</a><a href="/crossroads">Somewhere else</a></nav></main>'
            + arm("right", "Just after", after) + arm("down", "Like it, later", later))
    return _CROSSROADS.replace("{title}", _e(r["title"] or "Crossroads")).replace("{body}", body)


GAPS = {"any": (0, "any gap"), "week": (7, "a week or more"), "month": (30, "a month or more")}
ECHO_KINDS = {"all": "every kind", "elaborates": "elaborates", "supports": "supports", "tension": "tension",
              "analogy": "analogous"}


def _arcs(found: list, first, last) -> str:
    """Every echo as an arc over the archive's time, from when the earlier card was first said to the later one."""
    width, base, top = 1000.0, 250.0, 240.0
    span = max((last - first).total_seconds(), 1.0)
    at = lambda t: round(8 + (width - 16) * (t - first).total_seconds() / span, 1)   # noqa: E731
    out = []
    for n, e in enumerate(found):
        x0, x1 = at(e["earlier"]["first_said"]), at(e["later"]["first_said"])
        r = max((x1 - x0) / 2, 1.5)
        out.append(f'<a href="#e{n}"><path class="{" ".join(e["kinds"]) or "plain"}" d="M{x0},{base}A{r:.1f},'
                   f'{min(r, top):.1f} 0 0 1 {x1},{base}"><title>{_e(e["earlier"]["title"])} … '
                   f'{_e(e["later"]["title"])} ({_e(e["gap"])})</title></path></a>')
    ticks, t = [], first.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while t <= last:
        if t >= first:
            ticks.append(f'<line x1="{at(t)}" x2="{at(t)}" y1="{base}" y2="{base + 6}"/>'
                         f'<text x="{at(t) + 4}" y="{base + 16}">{t.strftime("%b %Y")}</text>')
        t = (t.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (f'<svg viewBox="0 0 {width:.0f} {base + 22:.0f}" role="img" aria-label="Each echo as an arc from when '
            f'the earlier thought was said to when the later one was"><line class="axis" x1="0" x2="{width:.0f}" '
            f'y1="{base}" y2="{base}"/>{"".join(ticks)}{"".join(out)}</svg>')


def echoes(conn, kind: str = "all", gap: str = "any", demo: bool = False) -> str:
    """What I came back to (docs/design/m6-emanation.md, Echoes): every connection between two cards said in
    different conversations, the earlier beside the later, with how far apart they were said, the connection's kinds
    and the judge's reason. Almost every capture founds its own cluster, so this is where recurrence shows. Newest
    first, by when the later one was said: nothing else orders it (decision 0007). `kind` and `gap` only narrow it.
    In demo mode a pair with a sensitive card is left out."""
    v = _views(demo)
    kind, gap = (kind if kind in ECHO_KINDS else "all"), (gap if gap in GAPS else "any")
    cards = {r["idea_id"]: r for r in _rows(conn, f"SELECT * FROM {v}cards WHERE first_said IS NOT NULL")
             if not (demo and r["redacted"])}
    said: dict = {}   # card -> its conversations, and its first quote
    for i, c, q in conn.execute(f"""SELECT idea_id, conversation_id, quote FROM {v}card_moments
                                    ORDER BY said_at, item_id, observation_id"""):
        convs, _ = said.setdefault(i, (set(), q))
        convs.add(c)
    found = []
    for a, b, kinds, direction, why, analogy in conn.execute(
            """SELECT l.a, l.b, l.kinds, j.direction, j.why, j.analogy FROM pub.card_links l
               LEFT JOIN pub.card_judgments j ON j.a = least(l.a, l.b) AND j.b = greatest(l.a, l.b)
               ORDER BY l.a, l.b"""):
        if a not in cards or b not in cards or said.get(a, (set(),))[0] & said.get(b, (set(),))[0]:
            continue
        earlier, later = sorted((cards[a], cards[b]), key=lambda r: (r["first_said"], r["idea_id"]))
        days = (later["first_said"] - earlier["first_said"]).days
        if days < GAPS[gap][0] or not (kind == "all" or kind in kinds or (kind == "analogy" and analogy)):
            continue
        how = []
        for k in kinds:   # a>b reads "the lower id supports (or elaborates) the higher"
            subject = min(a, b) if direction == "a>b" else max(a, b) if direction == "b>a" else None
            how.append(k if subject is None or k == "tension" else
                       f"the later {k} the earlier" if subject == later["idea_id"] else f"the earlier {k} the later")
        found.append({"earlier": earlier, "later": later, "kinds": kinds, "why": why, "analogy": analogy,
                      "gap": _apart(later["first_said"], earlier["first_said"]), "how": ", ".join(how) or "connected"})
    found.sort(key=lambda e: (e["later"]["first_said"], e["later"]["idea_id"], e["earlier"]["idea_id"]), reverse=True)

    def side(r: dict) -> str:
        quote = said.get(r["idea_id"], (None, None))[1]
        return (f'<a class="side" href="/crossroads/{r["idea_id"]}"><small>{_when(r["first_said"])}</small>'
                f'<b>{_e(r["title"])}</b>' + (f"<q>{_e(quote)}</q>" if quote else "") + "</a>")

    def chips(name: str, options: dict, current: str, other: str) -> str:
        return "".join(f'<a href="/echoes?{name}={k}&{other}"{" aria-current=true" if k == current else ""}>'
                       f'{_e(label if isinstance(label, str) else label[1])}</a>' for k, label in options.items())

    rows = "".join(
        f'<li id="e{n}" class="{" ".join(e["kinds"]) or "plain"}">{side(e["earlier"])}'
        f'<div class="gap"><b>{_e(e["gap"])}</b><small>{_e(e["how"])}</small></div>{side(e["later"])}'
        + (f'<p>{_e(e["why"])}' + (f' <i>Same structure: {_e(e["analogy"])}</i>' if e["analogy"] else "") + "</p>"
           if e["why"] or e["analogy"] else "") + "</li>" for n, e in enumerate(found))
    times = [r["first_said"] for r in cards.values()]
    body = (f'<h1>Echoes</h1><p class="lede">{len(found)} connection{"s" * (len(found) != 1)} between thoughts said '
            f'in different conversations: what I came back to. Newest first.</p>'
            f'<nav>{chips("kind", ECHO_KINDS, kind, f"gap={gap}")}<span></span>'
            f'{chips("gap", GAPS, gap, f"kind={kind}")}</nav>'
            + (_arcs(found, min(times), max(times)) if found else "")
            + (f"<ol>{rows}</ol>" if found else "<p>Nothing here.</p>"))
    return _ECHOES.replace("{body}", body)


def anywhere(conn, demo: bool = False) -> str | None:
    """A card picked at random, for a crossroads to start from: nothing chooses it (decision 0007)."""
    row = conn.execute(f"""SELECT idea_id::text FROM {_views(demo)}cards c
                           WHERE true {"AND NOT c.redacted" if demo else ""} ORDER BY random() LIMIT 1""").fetchone()
    return row[0] if row else None


def act(conn, idea_id: str, action: str) -> None:
    kind, payload = ACTIONS[action]
    conn.execute("INSERT INTO pub.feedback_events (kind, idea_id, payload, source) VALUES (%s, %s, %s, 'emanation')",
                 (kind, idea_id, payload))


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{_e(title)}</title>
<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" href="/icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png"><meta name="apple-mobile-web-app-title" content="Idea Machine">
<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#161618" media="(prefers-color-scheme: dark)"><style>
:root {{ --bg: #fff; --fg: #1d1d1f; --muted: #6e6e73; --line: #e5e5ea; --accent: #0a58ca; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #161618; --fg: #ececf0; --muted: #9a9aa2; --line: #2c2c30;
  --accent: #7aa7ff; }} }}
body {{ background: var(--bg); color: var(--fg); font: 16px/1.5 system-ui, sans-serif; max-width: 46rem;
  margin: 0 auto; padding: 1rem; }}
a {{ color: var(--accent); }} small {{ color: var(--muted); }} nav a {{ margin-right: 1rem; }}
nav a[aria-current] {{ font-weight: 600; text-decoration: none; color: var(--fg); }}
ol, ul {{ padding-left: 1.2rem; }} li {{ margin: 0 0 1rem; }} li p {{ margin: .2rem 0; }}
blockquote {{ margin: 0; padding-left: .8rem; border-left: 3px solid var(--line); }}
button {{ margin: 0 .4rem .4rem 0; font: inherit; padding: .3rem .8rem; }}
header {{ display: flex; align-items: center; gap: .5rem; margin-bottom: .5rem; }}
header a {{ color: var(--fg); text-decoration: none; font-weight: 600; }}
</style><body><header><a href="/"><img src="/icon.svg" alt="" width="28" height="28" style="vertical-align: middle">
Idea Machine</a></header>{body}</body></html>"""


_LOGIN = _page("Log in", '<form method="post" action="/login"><input type="password" name="token" autofocus> '
                         '<button>Log in</button></form>')


_SKY = (files("im") / "sky.html").read_text()
_TRAILS = (files("im") / "trails.html").read_text()
_CROSSROADS = (files("im") / "crossroads.html").read_text()
_ECHOES = (files("im") / "echoes.html").read_text()


class Handler(BaseHTTPRequestHandler):
    dsn = ""
    demo_only = False   # im web --demo: demo mode, and no way out of it

    def _demo(self) -> bool:
        return self.demo_only or _cookie(self.headers.get("Cookie"), DEMO_COOKIE) == "1"

    def _send(self, code: int, body: str = "", headers: dict | None = None,
              content_type: str = "text/html; charset=utf-8") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _form(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return {k: v[0] for k, v in parse_qs(self.rfile.read(min(n, 4096)).decode()).items()}

    def _send_static(self, path: str) -> None:
        data = files("im").joinpath("static", path.lstrip("/")).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", STATIC[path])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path in STATIC:
            return self._send_static(url.path)
        if url.path == "/login":
            return self._send(200, _LOGIN)
        if not logged_in(self.headers.get("Cookie")):
            return self._send(303, headers={"Location": "/login"})
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/demo":   # the toggle: a cookie for this browser
            on = "1" if self.demo_only or q.get("on") == "1" else "0"
            return self._send(303, headers={"Location": "/", "Set-Cookie": f"{DEMO_COOKIE}={on}; Path=/; "
                                            "Max-Age=31536000; HttpOnly; SameSite=Strict"})
        demo = self._demo()
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            if url.path == "/":
                page = int(q["page"]) if q.get("page", "").isdigit() else 0
                return self._send(200, feed(conn, q.get("show", "candidates"), page, demo))
            if url.path == "/sky":
                seed = q.get("seed")
                body = sky(conn, seed, demo) if seed is None or _UUID.match(seed) else None
                return self._send(200, body) if body else self._send(404, _page("Not found", "Not found."))
            if url.path == "/sky/lens":   # the page lighting itself from another card, without reloading the map
                seed = q.get("seed", "")
                got = sky_lens(conn, seed, demo) if _UUID.match(seed) else None
                return self._send(200, json.dumps(got, separators=(",", ":")), content_type="application/json") \
                    if got else self._send(404, "{}", content_type="application/json")
            if url.path == "/trails":
                at = q.get("at")
                body = trails(conn, at, demo) if at is None or _UUID.match(at) else None
                return self._send(200, body) if body else self._send(404, _page("Not found", "Not found."))
            if url.path == "/echoes":
                return self._send(200, echoes(conn, q.get("kind", "all"), q.get("gap", "any"), demo))
            if url.path == "/crossroads":   # anywhere: a card at random
                to = anywhere(conn, demo)
                return self._send(303, headers={"Location": f"/crossroads/{to}" if to else "/"})
            m = re.fullmatch(r"/(idea|crossroads)/([0-9a-f-]{36})", url.path)
            body = (card if m[1] == "idea" else crossroads)(conn, m[2], demo) if m and _UUID.match(m[2]) else None
        self._send(200, body) if body else self._send(404, _page("Not found", "Not found."))

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        form = self._form()
        if url.path == "/login":
            if hmac.compare_digest(form.get("token", "").encode(), _token().encode()):
                secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
                return self._send(303, headers={"Location": "/", "Set-Cookie": f"{COOKIE}={_session()}; Path=/; "
                                                f"Max-Age=31536000; HttpOnly; SameSite=Strict{secure}"})
            return self._send(403, _LOGIN)
        if not logged_in(self.headers.get("Cookie")):
            return self._send(403, _LOGIN)
        m = re.fullmatch(r"/idea/([0-9a-f-]{36})", url.path)
        if not (m and _UUID.match(m[1]) and form.get("action") in ACTIONS):
            return self._send(400, _page("Bad request", "Bad request."))
        if self._demo():
            return self._send(403, _page("Demo mode", "Nothing can be kept or dismissed in demo mode."))
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            act(conn, m[1], form["action"])
        self._send(303, headers={"Location": f"/idea/{m[1]}"})


def serve(host: str, port: int, demo: bool = False) -> None:
    """`demo`: demo mode for every page, with no way out of it."""
    try:
        Handler.dsn = os.environ[DATABASE_ENV]
    except KeyError:
        raise SystemExit(f"{DATABASE_ENV} is not set (role lattice_app; see .env.example)") from None
    _token()
    Handler.demo_only = demo
    print(f"serving on http://{host}:{port}/" + (" in demo mode" if demo else ""), flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
