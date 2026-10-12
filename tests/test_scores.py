"""Ranking ideas for surfacing: rubric scores from Claude, recurrence, my stars."""

import json
from types import SimpleNamespace

import pytest

from im import scores

from helpers import table


class FakeScorer:
    """Scores idea n with 3 on every criterion, except ideas whose line mentions `best`: 5s."""

    def __init__(self, best="coffee"):
        self.calls, self.best = [], best
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        lines = [ln for ln in kw["messages"][0]["content"].splitlines()[1:] if ln]
        out = [{"idea": int(ln.split(".")[0]), **{c: 5 if self.best in ln.lower() else 3 for c in scores.CRITERIA},
                "why": "test"} for ln in lines]
        content = [SimpleNamespace(type="text", text=json.dumps({"scores": out}))]
        return SimpleNamespace(content=content, stop_reason="end_turn", model="claude-opus-5-5", _request_id="r",
                               usage=SimpleNamespace(input_tokens=500, output_tokens=100))


def ranked(conn):
    return table(conn, """SELECT d.title, s.score::float, s.rubric::float, s.recurrence::float FROM pub.idea_scores s
                          JOIN pub.ideas d USING (idea_id) ORDER BY s.score DESC NULLS LAST, d.title""")


def test_every_shown_idea_is_scored_once_and_ranked(seeded_lattice, admin):
    pipe = seeded_lattice
    shown = table(pipe, "SELECT count(*) FROM pub.ideas")[0][0]
    assert all(r[1] is None for r in ranked(pipe))   # not scored yet
    claude = FakeScorer()
    assert scores.score_stage(pipe, claude) == {"rubric_version": scores.rubric_version(), "pending": shown,
                                                "scored": shown, "stopped": None}
    assert scores.score_stage(pipe, claude)["pending"] == 0 and len(claude.calls) == 1
    top = ranked(pipe)
    assert "coffee" in top[0][0].lower() and top[0][2] == 1.0   # all 5s
    middling = [r for r in top if "coffee" not in r[0].lower()]
    assert all(r[2] == 0.5 for r in middling)
    assert all(r[1] == pytest.approx(0.7 * r[2] + 0.2 * r[3]) for r in top)   # nothing starred yet
    recurrence = {n: float(r) for n, r in table(pipe, """SELECT d.n_evidence, s.recurrence FROM pub.idea_scores s
                                                          JOIN pub.ideas d USING (idea_id)""")}
    assert recurrence.get(0, 0.0) == 0.0 and recurrence.get(1, 0.0) == 0.0   # one capture, or an archive idea
    assert recurrence.get(2, 0.5) == pytest.approx(0.5)
    logged = table(pipe, "SELECT cardinality(segment_ids) > 0, prompt_version FROM im.egress_log "
                         "WHERE episode_id IS NULL")
    assert logged == [(True, scores.rubric_version())]
    with admin.transaction():
        admin.execute("SET LOCAL ROLE lattice_app")
        assert admin.execute("SELECT count(*) FROM pub.idea_scores WHERE score IS NOT NULL").fetchone()[0] == shown


def test_a_star_lifts_an_idea_and_a_correction_rescores_it(seeded_lattice):
    pipe = seeded_lattice
    scores.score_stage(pipe, FakeScorer())
    (idea, title, before), = table(pipe, """SELECT d.idea_id, d.title, s.score::float FROM pub.ideas d
                                            JOIN pub.idea_scores s USING (idea_id)
                                            WHERE d.title ILIKE '%%grape%%'""")
    pipe.execute("INSERT INTO pub.feedback_events (kind, idea_id) VALUES ('star', %s)", (idea,))
    assert table(pipe, "SELECT score::float FROM pub.idea_scores WHERE idea_id = %s", idea) == \
        [(pytest.approx(before + 0.1),)]
    pipe.execute("INSERT INTO pub.feedback_events (kind, idea_id, payload) VALUES ('idea_correct', %s, %s)",
                 (idea, json.dumps({"title": "A grape, best of all"})))
    stats = scores.score_stage(pipe, FakeScorer(best="best of all"))
    assert stats["pending"] == 1 and stats["scored"] == 1
    assert table(pipe, "SELECT rubric::float FROM pub.idea_scores WHERE idea_id = %s", idea) == [(1.0,)]


def test_the_cap_stops_scoring(seeded_lattice):
    assert scores.score_stage(seeded_lattice, FakeScorer(), monthly_cap_usd=0)["stopped"].startswith("monthly cap")
    assert table(seeded_lattice, "SELECT count(*) FROM im.idea_scores") == [(0,)]
