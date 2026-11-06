"""Observations (M2): one per stretch of speech across re-reads, who said it, and how far each cluster has come."""

from im import extract, lattice, observations

from helpers import assert_invariants, run, segment_of, table
from test_carry import DATABASE, capture, current_items, retranscribe
from test_extract import FakeClaude
from test_lattice import FakeMatcher, items_for, seeded  # noqa: F401
from test_triage import HashEmbedder

LORA = "Idea: the garden sensors could report soil moisture over LoRa."


def observation_of(pipe, item_id):
    return table(pipe, "SELECT observation_id FROM im.items WHERE item_id = %s", item_id)[0][0]


def maturity(pipe):
    return dict(table(pipe, """SELECT x.title, m.maturity || coalesce(':' || m.kept_as, '')
                               FROM im.idea_maturity m JOIN im.idea_text x USING (idea_id)"""))


def test_a_re_read_joins_the_observation_it_captured_before(pipe, cfg, stream):
    run(pipe, cfg, stream)
    claude = FakeClaude(capture(DATABASE))
    extract.run_stage(pipe, claude)
    assert observations.assign_stage(pipe) == {"new": 1, "joined": 0, "dropped": 0, "typed": 0}
    ((old, _),) = current_items(pipe)

    retranscribe(pipe, cfg, stream, claude)   # a new segment and episode, linked to the old one
    ((new, _),) = current_items(pipe)
    assert new != old
    assert observations.assign_stage(pipe) == {"new": 0, "joined": 1, "dropped": 0, "typed": 0}
    assert observation_of(pipe, new) == observation_of(pipe, old)
    assert observations.assign_stage(pipe) == {"new": 0, "joined": 0, "dropped": 0, "typed": 0}   # each item once
    assert_invariants(pipe)


def test_the_same_line_captured_twice_in_one_read_is_one_observation(pipe, cfg, stream):
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(lambda text: capture(DATABASE)(text) * 2 + capture("coffee")(text)))
    assert observations.assign_stage(pipe) == {"new": 2, "joined": 1, "dropped": 0, "typed": 0}
    by_quote = {}
    for item_id, quote in current_items(pipe):
        by_quote.setdefault(quote, set()).add(observation_of(pipe, item_id))
    assert sorted(len(v) for v in by_quote.values()) == [1, 1]   # both database captures share one


def test_speaker_follows_the_segment_label(pipe, cfg, stream):
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(lambda text: [i for i in items_for(text) if "LoRa" in i["quote"]]))
    observations.assign_stage(pipe)
    speaker = lambda: table(pipe, "SELECT speaker, cites_me FROM im.observation_speaker")  # noqa: E731
    assert speaker() == [("me", True)]
    seg = segment_of(pipe, LORA)
    pipe.execute("UPDATE im.source_segments SET speaker_basis = 'diarization' WHERE segment_id = %s", (seg,))
    assert speaker() == [("me_diar", False)]
    pipe.execute("UPDATE im.source_segments SET speaker_kind = 'unknown', speaker_basis = 'none' WHERE segment_id = %s",
                 (seg,))
    assert speaker() == [("not_me", False)]


def test_maturity_comes_from_who_said_it_and_what_i_kept(seeded, cfg, stream):  # noqa: F811
    run(seeded, cfg, stream)
    extract.run_stage(seeded, FakeClaude(items_for))
    lattice.match_stage(seeded, FakeMatcher(), HashEmbedder())
    observations.assign_stage(seeded)
    got = maturity(seeded)
    assert got["Something new"] == "candidate"                   # the LoRa line, mine by voice
    assert got["Broken builds as signal"] == "candidate"
    assert got["The coffee order is the real meeting."] == "idea:canonical"   # the archive seed
    assert {v for k, v in got.items() if k not in ("Something new", "Broken builds as signal")} == {"idea:canonical"}

    seeded.execute("UPDATE im.source_segments SET speaker_kind = 'anonymous', speaker_basis = 'cluster' "
                   "WHERE segment_id = %s", (segment_of(seeded, LORA),))
    assert maturity(seeded)["Something new"] == "observation"    # nobody's words of mine left in it
    (lora,) = table(seeded, "SELECT idea_id FROM im.idea_text WHERE title = 'Something new'")[0]
    seeded.execute("INSERT INTO pub.feedback_events (kind, idea_id, source) VALUES ('star', %s, 'test')", (lora,))
    assert maturity(seeded)["Something new"] == "idea:pinned"    # a star is a pin, on anything


def test_forgetting_takes_the_observation(seeded, cfg, stream):  # noqa: F811
    run(seeded, cfg, stream)
    extract.run_stage(seeded, FakeClaude(items_for))
    observations.assign_stage(seeded)
    before = table(seeded, "SELECT count(*) FROM im.observations")[0][0]
    stream.forget(3600, 3612)   # the LoRa line
    stream.write(stream.dir)
    run(seeded, cfg, stream)
    assert table(seeded, "SELECT count(*) FROM im.observations")[0][0] == before - 1
    assert observations.assign_stage(seeded) == {"new": 0, "joined": 0, "dropped": 0, "typed": 0}
    assert_invariants(seeded)


HOSTING = "The hosting line doubled and nobody noticed for a month."
MOVE = "I think we should move off the managed database before renewal."
STITCHED = HOSTING[:-1] + "... " + MOVE   # two passages Claude joined with "...": it matches each


def test_a_stitched_quote_doesnt_chain_two_points_into_one_observation(pipe, cfg, stream):
    run(pipe, cfg, stream)
    def respond(text):   # three captures of the database line; nothing on other episodes
        return [dict(line, quote=q) for line in capture(DATABASE)(text) for q in (HOSTING, STITCHED, MOVE)]
    extract.run_stage(pipe, FakeClaude(respond))
    # Assigned in this order, the stitched quote joins the first point's observation before the second point
    # comes; it matches the stitched quote, not the first point.
    for n, quote in enumerate((HOSTING, STITCHED, MOVE)):
        pipe.execute("UPDATE im.items SET created_at = created_at + %s * interval '1 second' WHERE quote = %s",
                     (n, quote))
    assert observations.assign_stage(pipe) == {"new": 2, "joined": 1, "dropped": 0, "typed": 0}
    obs = dict(table(pipe, "SELECT quote, observation_id FROM im.items"))
    assert obs[HOSTING] != obs[MOVE]   # B5's false merges: two points, one observation
    assert obs[STITCHED] == obs[HOSTING]
