"""The weekly digest: Claude synthesizes what the lattice gained in a week, citing the ideas it rests on."""

import json
from datetime import date
from types import SimpleNamespace

import psycopg
import pytest

from im import digest, scores

from helpers import run, table
from test_review import as_lattice
from test_scores import FakeScorer

WEEK = date(2026, 8, 31)   # the fixture stream is said on Tuesday 2026-09-01


class FakeDigester:
    """Writes a convergence of ideas 1 and 2, a point citing only an idea it wasn't shown, and a project on 1."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        out = {"summary": "A week about sensors and meetings.", "points": [
            {"kind": "convergence", "title": "Two halves of one thing", "body": "They meet.", "ideas": [1, 2, 2]},
            {"kind": "sleeper", "title": "Made up", "body": "Cites nothing it saw.", "ideas": [99]},
            {"kind": "project", "title": "Build it", "body": "Weekend project.", "ideas": [1]}]}
        content = [SimpleNamespace(type="text", text=json.dumps(out))]
        return SimpleNamespace(content=content, stop_reason="end_turn", model="claude-opus-5-5", _request_id="r",
                               usage=SimpleNamespace(input_tokens=2000, output_tokens=400))


def test_last_week_is_the_previous_monday_to_monday():
    assert digest.last_week(date(2026, 10, 13)) == date(2026, 10, 5)   # a Tuesday
    assert digest.last_week(date(2026, 10, 12)) == date(2026, 10, 5)   # a Monday: last week just ended
    assert digest.last_week(date(2026, 10, 11)) == date(2026, 9, 28)   # a Sunday: this week isn't over


def test_a_week_is_digested_once_from_ideas_not_speech(seeded_lattice, admin):
    pipe = seeded_lattice
    scores.score_stage(pipe, FakeScorer())
    claude = FakeDigester()
    stats = digest.digest_stage(pipe, claude, week=WEEK)
    assert stats["written"] and stats["points"] == 2   # the point citing nothing it was shown is dropped
    text = claude.calls[0]["messages"][0]["content"]
    assert text.startswith("Week of 2026-08-31 to 2026-09-06.")
    assert "Said this week (3 ideas):" in text and "Top of the ranking, not said this week:" in text
    assert "said 1x this week" in text and "score " in text
    assert "Alice" not in text and "Bob" not in text   # ideas only: no speakers, no quotes

    assert digest.digest_stage(pipe, claude, week=WEEK)["written"] is False and len(claude.calls) == 1
    (summary, points), = as_lattice(admin, "SELECT summary, points FROM pub.digests")
    assert summary == "A week about sensors and meetings."
    assert [(p["point"], p["kind"], len(p["idea_ids"]), p["mine"]) for p in points] == \
        [(1, "convergence", 2, None), (2, "project", 1, None)]
    assert table(pipe, "SELECT count(*) FROM im.egress_log WHERE prompt_version = %s", digest.prompt_version()) == [(1,)]
    assert "Build it" in digest.show(pipe)

    assert digest.digest_stage(pipe, claude, week=WEEK, again=True)["written"] and len(claude.calls) == 2
    assert table(pipe, "SELECT count(*) FROM im.digests") == [(1,)]


def test_a_quiet_week_costs_nothing(seeded_lattice):
    claude = FakeDigester()
    stats = digest.digest_stage(seeded_lattice, claude, week=date(2026, 9, 7))
    assert stats["written"] and stats["points"] == 0 and not claude.calls
    assert table(seeded_lattice, "SELECT summary FROM pub.digests") == [("Nothing new was said this week.",)]


def test_the_cap_stops_the_digest(seeded_lattice):
    stats = digest.digest_stage(seeded_lattice, FakeDigester(), monthly_cap_usd=0, week=WEEK)
    assert stats["stopped"].startswith("monthly cap") and not stats["written"]


def test_i_can_star_or_dismiss_a_point(seeded_lattice, admin):
    pipe = seeded_lattice
    digest.digest_stage(pipe, FakeDigester(), week=WEEK)
    (did,) = table(pipe, "SELECT digest_id FROM im.digests")[0]
    event = "INSERT INTO pub.feedback_events (kind, payload) VALUES (%s, %s)"
    as_lattice(admin, event, "digest_dismiss", json.dumps({"digest_id": did, "point": 1}))
    as_lattice(admin, event, "digest_star", json.dumps({"digest_id": did, "point": 2}))
    as_lattice(admin, event, "digest_star", json.dumps({"digest_id": did, "point": 1, "note": "on reflection"}))
    (points,), = as_lattice(admin, "SELECT points FROM pub.digests")
    assert [p["mine"] for p in points] == ["star", "star"]
    for bad in [{}, {"digest_id": did}, {"digest_id": str(did), "point": 1}, {"digest_id": did, "point": 1,
                                                                             "note": "x" * 501}]:
        with pytest.raises(psycopg.errors.CheckViolation):
            as_lattice(admin, event, "digest_star", json.dumps(bad))


def test_forgetting_takes_the_digest(seeded_lattice, cfg, stream):
    pipe = seeded_lattice
    digest.digest_stage(pipe, FakeDigester(), week=WEEK)
    stream.forget(3600, 3612)   # the LoRa idea's line, one of the ideas the digest was shown
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert table(pipe, "SELECT count(*) FROM im.digests") == [(0,)]
    assert table(pipe, "SELECT count(*) FROM im.digest_points") == [(0,)]
