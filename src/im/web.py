"""The neutral view (docs/decisions/0007-neutral-proposals.md; the start of Emanation, decision 0005).

Every cluster that passed the gates, newest first, each showing what's known about it: who said it (me, me by
diarization, not me; never a name), in how many conversations, when, its maturity, how I kept it, and its edges.
No score orders or filters anything. From a card I keep a cluster (canonical or pinned), un-keep it or dismiss it,
as the same feedback events Lattice's keep_idea writes.

It connects as Lattice's role (LATTICE_DATABASE_URL: read pub, append feedback), never as im_pipeline, and logs in
with Lattice's token (LATTICE_TOKEN), exchanged once per device for a signed cookie. Loopback only: tailscale serve
publishes it to the tailnet (install/nas.sh).
"""

import hashlib
import hmac
import html
import json
import os
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

import psycopg

DATABASE_ENV = "LATTICE_DATABASE_URL"
TOKEN_ENV = "LATTICE_TOKEN"
COOKIE = "emanation_session"
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


def logged_in(cookie_header: str | None) -> bool:
    for part in (cookie_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE and hmac.compare_digest(value.encode(), _session().encode()):
            return True
    return False


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


def feed(conn, show: str = "candidates", page: int = 0) -> str:
    maturities = SHOW.get(show, SHOW["candidates"])[0]
    rows = _rows(conn, """SELECT * FROM pub.cards WHERE maturity = ANY(%s)
                          ORDER BY last_seen DESC, idea_id LIMIT %s OFFSET %s""",
                 (list(maturities), PAGE + 1, page * PAGE))
    tabs = " ".join(f'<a href="/?show={k}"{" aria-current=page" if k == show else ""}>{name}</a>'
                    for k, (_, name) in SHOW.items()) + ' <a href="/sky">sky</a>'
    cards = "".join(f'<li><a href="/idea/{r["idea_id"]}">{_e(r["title"])}</a><p>{_e(r["statement"])}</p>'
                    f'<small>{_e(_facts(r))}</small></li>' for r in rows[:PAGE])
    more = f'<a href="/?show={_e(show)}&page={page + 1}">older</a>' if len(rows) > PAGE else ""
    return _page("Idea Machine", f"<nav>{tabs}</nav><ol>{cards or '<li>Nothing here.</li>'}</ol>{more}")


def card(conn, idea_id: str) -> str | None:
    rows = _rows(conn, "SELECT * FROM pub.cards WHERE idea_id = %s", (idea_id,))
    if not rows:
        return None
    r = rows[0]
    quotes = _rows(conn, """SELECT speaker, quote, said_at FROM pub.card_evidence WHERE idea_id = %s
                            ORDER BY said_at, item_id""", (idea_id,))
    links = _rows(conn, """SELECT l.kind, l.kinds, c.idea_id, c.title FROM pub.card_links l
                           JOIN pub.cards c ON c.idea_id = CASE WHEN l.a = %s THEN l.b ELSE l.a END
                           WHERE %s IN (l.a, l.b) ORDER BY l.kind, c.title""", (idea_id, idea_id))
    lineage = _rows(conn, """SELECT CASE WHEN l.from_id = %s THEN l.kind WHEN l.kind = 'evolves' THEN 'evolved by'
                                         ELSE 'synthesized in' END AS kind,
                                    c.idea_id, c.title FROM pub.card_lineage l
                             JOIN pub.cards c ON c.idea_id = CASE WHEN l.from_id = %s THEN l.to_id ELSE l.from_id END
                             WHERE %s IN (l.from_id, l.to_id) ORDER BY 1, c.title""", (idea_id, idea_id, idea_id))
    near = _rows(conn, """SELECT c.idea_id, c.title, n.similarity FROM pub.card_neighbours(%s) n
                          JOIN pub.cards c USING (idea_id) ORDER BY n.similarity DESC""", (idea_id,))
    buttons = "".join(f'<button name="action" value="{a}">{a}</button>' for a in ACTIONS)
    body = [f"<h1>{_e(r['title'])}</h1><p>{_e(r['statement'])}</p><small>{_e(_facts(r))}</small>",
            f'<form method="post" action="/idea/{idea_id}">{buttons}</form>',
            f'<p><a href="/sky?seed={idea_id}">Light the sky from here</a></p>']
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


def sky(conn, seed: str | None = None) -> str | None:
    """Every card at its place on the one map (decision 0005, amended). With a seed, a lens lights what's relevant to
    it; without one, the encodings light everything. None if the seed isn't on the map."""
    from im import lens

    rows = _rows(conn, """SELECT c.*, p.x, p.y FROM pub.cards c JOIN pub.card_positions p USING (idea_id)
                          ORDER BY c.idea_id""")
    index = {str(r["idea_id"]): n for n, r in enumerate(rows)}
    if seed is not None and seed not in index:
        return None
    now = datetime.now(timezone.utc)
    stars = [[str(r["idea_id"]), round(float(r["x"]), 4), round(float(r["y"]), 4),
              SIZES[r["maturity"]] + (r["kept_as"] == "canonical"), r["conversations"],
              max((now - r["last_seen"]).days, 0) if r["last_seen"] else 365, r["title"], _facts(r)] for r in rows]
    links = sorted({tuple(sorted((index[a], index[b]))) for a, b in conn.execute(
        """SELECT a::text, b::text FROM pub.card_links UNION ALL SELECT from_id::text, to_id::text FROM pub.card_lineage""")
        if a in index and b in index and a != b})
    rel = {k: v for k, v in lens.relevance(conn, seed).items() if k in index} if seed else None
    data = json.dumps({"stars": stars, "links": links, "rel": rel, "seed": seed}, separators=(",", ":"))
    title = rows[index[seed]]["title"] if seed else "Sky"
    return _SKY.replace("{title}", _e(title)).replace("{data}", data.replace("</", "<\\/"))


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


class Handler(BaseHTTPRequestHandler):
    dsn = ""

    def _send(self, code: int, body: str = "", headers: dict | None = None) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            if url.path == "/":
                page = int(q["page"]) if q.get("page", "").isdigit() else 0
                return self._send(200, feed(conn, q.get("show", "candidates"), page))
            if url.path == "/sky":
                seed = q.get("seed")
                body = sky(conn, seed) if seed is None or _UUID.match(seed) else None
                return self._send(200, body) if body else self._send(404, _page("Not found", "Not found."))
            m = re.fullmatch(r"/idea/([0-9a-f-]{36})", url.path)
            body = card(conn, m[1]) if m and _UUID.match(m[1]) else None
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
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            act(conn, m[1], form["action"])
        self._send(303, headers={"Location": f"/idea/{m[1]}"})


def serve(host: str, port: int) -> None:
    try:
        Handler.dsn = os.environ[DATABASE_ENV]
    except KeyError:
        raise SystemExit(f"{DATABASE_ENV} is not set (role lattice_app; see .env.example)") from None
    _token()
    print(f"serving on http://{host}:{port}/", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
