"""Weight and proposals (M2): only I make ideas; the system weighs clusters and proposes the heaviest candidates."""

from datetime import datetime, timedelta

import psycopg
import pytest

from im import extract, lattice, observations, weight
from im.config import WeightConfig

from helpers import run, table
from test_extract import FakeClaude
from test_lattice import FakeMatcher, items_for, seeded  # noqa: F401
from test_triage import HashEmbedder


def test_repeats_close_together_count_once():
    t = datetime(2026, 10, 28, 9)
    mine = [("c1", t), ("c1", t + timedelta(minutes=10)), ("c1", t + timedelta(minutes=40)), ("c2", t)]
    assert weight.recurrences(mine, 30) == 3
    assert weight.recurrences(mine, 5) == 4
    assert weight.recurrences(mine, 60) == 2


@pytest.fixture
def matched(seeded, cfg, stream):  # noqa: F811
    run(seeded, cfg, stream)
    extract.run_stage(seeded, FakeClaude(items_for))
    lattice.match_stage(seeded, FakeMatcher(), HashEmbedder())
    observations.assign_stage(seeded)
    return seeded


def idea(conn, title):
    return table(conn, "SELECT idea_id FROM im.idea_text WHERE title = %s", title)[0][0]


def event(conn, kind, idea_id, payload="{}"):
    conn.execute("INSERT INTO pub.feedback_events (kind, idea_id, payload, source) VALUES (%s, %s, %s, 'test')",
                 (kind, idea_id, payload))


def proposals(conn):
    return table(conn, """SELECT x.title, p.outcome FROM im.proposals p JOIN im.idea_text x USING (idea_id)
                          ORDER BY p.proposal_id""")


def test_candidates_are_proposed_and_only_i_make_them_ideas(matched):
    w = WeightConfig(proposals_per_week=1)
    out = weight.stage(matched, w)
    assert out["proposed"] == 1 and out["weights_changed"] == len(table(matched, "SELECT idea_id FROM im.ideas"))
    assert weight.stage(matched, w)["weights_changed"] == 0                       # nothing new, nothing written
    (first,) = proposals(matched)
    assert first[1] is None and first[0] in ("Something new", "Broken builds as signal")
    assert table(matched, "SELECT title FROM pub.proposals") == [(first[0],)]
    m = dict(table(matched, "SELECT idea_id, maturity FROM im.idea_maturity"))
    assert m[idea(matched, first[0])] == "candidate"                                # proposing changes nothing

    event(matched, "idea_keep", idea(matched, first[0]), '{"as": "canonical"}')
    weight.stage(matched, WeightConfig(proposals_per_week=2))
    assert proposals(matched)[0] == (first[0], "canonical")
    assert len(proposals(matched)) == 2                                             # the week's second slot
    assert dict(table(matched, "SELECT idea_id, kept_as FROM im.idea_kept"))[idea(matched, first[0])] == "canonical"

    event(matched, "idea_unkeep", idea(matched, first[0]))
    m = dict(table(matched, "SELECT idea_id, maturity FROM im.idea_maturity"))
    assert m[idea(matched, first[0])] == "candidate"


def test_a_dismissed_candidate_comes_back_only_heavier(matched):
    weight.stage(matched, WeightConfig(proposals_per_week=5))
    for title, _ in proposals(matched):
        event(matched, "idea_dismiss", idea(matched, title))
    out = weight.stage(matched, WeightConfig(proposals_per_week=5))
    assert out["dismissed"] == 2 and out["proposed"] == 0
    assert weight.stage(matched, WeightConfig(proposals_per_week=5, repropose_margin=0))["proposed"] == 2


def test_keep_needs_canonical_or_pinned(matched):
    with pytest.raises(psycopg.errors.CheckViolation):
        event(matched, "idea_keep", idea(matched, "Something new"), '{"as": "important"}')


def test_tune_previews_without_writing(matched):
    weight.stage(matched, WeightConfig())
    before = table(matched, "SELECT count(*) FROM im.weights")
    out = weight.tune(matched, WeightConfig(), weight.parse_overrides(["canonical=5", "others-cap=1"]))
    assert out["formula_version"]["now"] != out["formula_version"]["trial"]
    assert out["weights_changed"] > 0 and "canonical = 5.0" in out["im.toml"] and "others_cap = 1" in out["im.toml"]
    assert table(matched, "SELECT count(*) FROM im.weights") == before
    with pytest.raises(ValueError):
        weight.parse_overrides(["clustering=0.5"])
