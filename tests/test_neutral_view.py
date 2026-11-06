"""The neutral view (decision 0007): every cluster past the gates, newest first, no score, no verdict filter."""

import http.client
import threading
from http.server import ThreadingHTTPServer

import pytest

from im import web

from helpers import table
from test_lattice import seeded  # noqa: F401
from test_weight import idea, matched  # noqa: F401

TOKEN = "a-long-enough-test-token"


def as_app(admin, fn, *args):
    with admin.transaction():
        admin.execute("SET LOCAL ROLE lattice_app")
        return fn(admin, *args)


def test_a_capture_i_discarded_still_shows(matched, admin):  # noqa: F811
    new = idea(matched, "Something new")
    matched.execute("""INSERT INTO im.item_verdicts (item_id, segment_ids, quote, verdict)
                       SELECT i.item_id, i.source_segment_ids, i.quote, 'discard'
                       FROM im.items i JOIN im.idea_evidence ev USING (item_id) WHERE ev.idea_id = %s""", (new,))
    assert table(matched, "SELECT 1 FROM pub.ideas WHERE idea_id = %s", new) == []     # Lattice hides it
    assert as_app(admin, lambda c: c.execute("SELECT maturity, by_me FROM pub.cards WHERE idea_id = %s",
                                             (new,)).fetchall()) == [("candidate", 1)]


def test_the_feed_is_newest_first_and_names_no_one(matched, admin):  # noqa: F811
    page = as_app(admin, web.feed, "candidates")
    # Broken builds was said earlier and weighs more (a canonical link): time orders, not weight.
    assert page.index("Something new") < page.index("Broken builds as signal")
    assert "Signal may not be true" in as_app(admin, web.feed, "ideas")             # the archive's, by when made
    shown = as_app(admin, web.card, str(idea(matched, "Something new")))
    assert "me ·" in shown and "anon A" not in shown and "Near by meaning" in shown
    assert as_app(admin, web.card, "00000000-0000-0000-0000-000000000000") is None


def test_keeping_from_a_card_is_my_feedback(matched, admin):  # noqa: F811
    new = idea(matched, "Something new")
    as_app(admin, web.act, str(new), "pinned")
    assert table(matched, "SELECT kept_as FROM im.idea_kept WHERE idea_id = %s", new) == [("pinned",)]
    as_app(admin, web.act, str(new), "unkeep")
    as_app(admin, web.act, str(new), "dismiss")
    assert table(matched, """SELECT kind, source FROM pub.feedback_events WHERE idea_id = %s
                             ORDER BY event_id""", new) == [
        ("idea_keep", "emanation"), ("idea_unkeep", "emanation"), ("idea_dismiss", "emanation")]
    assert table(matched, "SELECT kept_as FROM im.idea_kept WHERE idea_id = %s", new) == [(None,)]


def test_only_a_cookie_signed_with_the_token_logs_in(monkeypatch):
    monkeypatch.setenv("LATTICE_TOKEN", "a-long-enough-test-token")
    assert web.logged_in(f"other=1; {web.COOKIE}={web._session()}")
    assert not web.logged_in(f"{web.COOKIE}=forged") and not web.logged_in(None)
    monkeypatch.setenv("LATTICE_TOKEN", "short")
    with pytest.raises(SystemExit):
        web.logged_in(f"{web.COOKIE}=x")


@pytest.fixture
def served(matched, lattice_dsn, monkeypatch):  # noqa: F811
    """The view, served on a loopback port as `im web` serves it: its port."""
    monkeypatch.setenv("LATTICE_TOKEN", TOKEN)
    monkeypatch.setattr(web.Handler, "dsn", lattice_dsn)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    thread.join()
    server.server_close()


def request(port, method, path, form=None, cookie=None):
    """(status, headers, body) of one request to the view."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=form, headers={"Content-Type": "application/x-www-form-urlencoded"}
              | ({"Cookie": cookie} if cookie else {}))
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read().decode()


def test_without_the_token_the_view_shows_nothing_and_takes_nothing(served, matched):  # noqa: F811
    new = str(idea(matched, "Something new"))
    events = lambda: table(matched, "SELECT count(*) FROM pub.feedback_events")  # noqa: E731
    before = events()
    for path in ("/", f"/idea/{new}", "/sky"):
        status, headers, body = request(served, "GET", path)
        assert (status, headers.get("Location")) == (303, "/login") and "Something new" not in body
    assert request(served, "POST", f"/idea/{new}", "action=dismiss")[0] == 403
    status, headers, _ = request(served, "POST", "/login", "token=another-long-enough-token")
    assert status == 403 and "Set-Cookie" not in headers
    assert events() == before

    status, headers, _ = request(served, "POST", "/login", f"token={TOKEN}")   # with it, both
    cookie = headers["Set-Cookie"].split(";")[0]
    assert status == 303 and "Something new" in request(served, "GET", "/", cookie=cookie)[2]
    assert request(served, "POST", f"/idea/{new}", "action=dismiss", cookie)[0] == 303
    assert events() == [(before[0][0] + 1,)]



def test_the_app_icons_ship_and_need_no_login():
    import json
    from importlib.resources import files

    static = files("im").joinpath("static")
    manifest = json.loads(static.joinpath("manifest.webmanifest").read_text())
    assert {i["src"] for i in manifest["icons"]} <= set(web.STATIC)
    for path in web.STATIC:
        assert static.joinpath(path.lstrip("/")).read_bytes()
    assert "/manifest.webmanifest" in web._page("t", "") and "/apple-touch-icon.png" in web._page("t", "")


def sky_data(page):
    """The data a sky page draws from."""
    import json
    import re
    return json.loads(re.search(r'<script type="application/json" id="d">(.*?)</script>', page, re.S)[1]
                      .replace("<\\/", "</"))


def test_the_sky_places_every_card_even_one_lattice_hides(matched, admin):  # noqa: F811
    from im import lattice
    from test_triage import HashEmbedder

    new = idea(matched, "Something new")
    matched.execute("""INSERT INTO im.item_verdicts (item_id, segment_ids, quote, verdict)
                       SELECT i.item_id, i.source_segment_ids, i.quote, 'discard'
                       FROM im.items i JOIN im.idea_evidence ev USING (item_id) WHERE ev.idea_id = %s""", (new,))
    lattice.place(matched, HashEmbedder().model_version)
    data = sky_data(as_app(admin, web.sky))
    cards = as_app(admin, lambda c: {r[0] for r in c.execute("SELECT idea_id::text FROM pub.cards")})
    assert {s[0] for s in data["stars"]} == cards and str(new) in cards   # discarded, so not in Lattice, but here
    assert data["rel"] is None and data["seed"] is None   # the home sky: no lens, the encodings light it


def test_a_lens_lights_its_seed_and_what_it_reaches(matched, admin):  # noqa: F811
    build = str(idea(matched, "Broken builds as signal"))
    (signal,) = table(matched, "SELECT idea_id::text FROM im.ideas WHERE statement LIKE '%%always correct%%'")[0]
    data = sky_data(as_app(admin, web.sky, build))
    assert data["seed"] == build and data["rel"][build] == 1.0
    assert data["rel"][signal] > 0   # it evolves from the signal idea
    assert all(0 < v <= 1 for v in data["rel"].values())
    assert {s[0]: (s[1], s[2]) for s in data["stars"]} == \
        {s[0]: (s[1], s[2]) for s in sky_data(as_app(admin, web.sky))["stars"]}   # a lens moves nothing
    assert as_app(admin, web.sky, "00000000-0000-0000-0000-000000000000") is None
