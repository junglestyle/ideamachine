"""Ranking ideas for surfacing: rubric scores from Claude, how developed an idea is, my stars."""

import json
from types import SimpleNamespace

import psycopg
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
    formula = table(pipe, """SELECT d.origin, s.developed, s.score::float, (0.65 * s.rubric + 0.25 * s.developed::int)::float
                             FROM pub.idea_scores s JOIN pub.ideas d USING (idea_id)""")
    assert all(dev == (origin == "archive") and score == pytest.approx(f) for origin, dev, score, f in formula)
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


def test_comparisons_are_checked_and_pairs_move_on(seeded_lattice, admin):
    pipe = seeded_lattice
    scores.score_stage(pipe, FakeScorer())

    def lattice_does(sql, *args):
        with admin.transaction():
            admin.execute("SET LOCAL ROLE lattice_app")
            return admin.execute(sql, args or None)

    pairs = lattice_does("SELECT idea_id, other_idea_id, reason FROM pub.compare_pairs").fetchall()
    shown = table(pipe, "SELECT count(*) FROM pub.ideas")[0][0]
    assert 0 < len(pairs) <= 20 and {r for *_, r in pairs} == {"never_compared"}   # nothing compared yet
    alike = dict((i, (dev, crit)) for i, dev, *crit in table(
        pipe, "SELECT idea_id, developed, generality, novelty, compression, outside_value FROM pub.idea_scores"))
    for a, b, _ in pairs:   # both developed or neither, and something to learn: their criteria differ
        assert alike[a][0] == alike[b][0] and alike[a][1] != alike[b][1]
    assert shown > len(pairs)
    a, b, _ = pairs[0]
    compare = "INSERT INTO pub.feedback_events (kind, idea_id, other_idea_id, payload) VALUES ('idea_compare', %s, %s, %s)"
    lattice_does(compare, b, a, json.dumps({"winner": "second", "note": "sharper"}))   # shown the other way round
    after = lattice_does("SELECT idea_id, other_idea_id FROM pub.compare_pairs").fetchall()
    assert (a, b) not in after and (b, a) not in after
    for bad in [(a, a, {"winner": "first"}), (a, b, {"winner": "both"}), (a, b, {}), (a, None, {"winner": "tie"}),
                (a, b, {"winner": "tie", "note": "x" * 501})]:
        with pytest.raises(psycopg.errors.CheckViolation):
            lattice_does(compare, bad[0], bad[1], json.dumps(bad[2]))


def test_working_on_a_capture_develops_it(seeded_lattice):
    pipe = seeded_lattice
    scores.score_stage(pipe, FakeScorer())
    ideas = [i for (i,) in table(pipe, "SELECT idea_id FROM pub.ideas WHERE origin = 'captured' ORDER BY title")]
    (archived,) = table(pipe, "SELECT idea_id FROM pub.ideas WHERE origin = 'archive' LIMIT 1")[0]
    developed = "SELECT developed FROM pub.idea_scores WHERE idea_id = %s"
    assert all(table(pipe, developed, i) == [(False,)] for i in ideas)
    pipe.execute("INSERT INTO pub.feedback_events (kind, idea_id, payload) VALUES ('idea_note', %s, %s)",
                 (ideas[0], json.dumps({"note": "came up at dinner"})))
    pipe.execute("INSERT INTO pub.feedback_events (kind, idea_id, other_idea_id) VALUES ('link', %s, %s)",
                 (ideas[1], archived))
    assert table(pipe, developed, ideas[0]) == [(True,)] and table(pipe, developed, ideas[1]) == [(True,)]
