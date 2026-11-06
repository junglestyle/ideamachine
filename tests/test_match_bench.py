"""B1: the matcher's logged decisions, replayed through Jev and scored against Opus."""

import json

from im import jev, match_bench, names

from helpers import table
from test_lattice import lat_setup, seeded  # noqa: F401 (fixture)


def fake_jev(sent):
    """The same idea when both sides speak of coffee; a development when a broken build meets the signal idea."""
    def post(body):
        sent.append(body)
        a, b = json.dumps(body["state"]["a"]).lower(), json.dumps(body["state"]["b"]).lower()
        same, develops = "coffee" in a and "coffee" in b, "build" in a and "signal" in b
        p = {"same": 0.9 if same else 0.1, "same_v2": 0.8 if same else 0.2,
             "develops": 0.9 if develops else 0.1, "develops_v2": 0.6 if develops else 0.3}
        return {"model": "jev-1.14.0", "usage": {"input_tokens": 500},
                "answers": {q: {"type": "noul", "noul": p[q]} for q in body["questions"]}}
    return jev.Jev(post=post)


def test_jev_replays_the_matchers_decisions_and_is_scored_against_opus(seeded, cfg, stream, tmp_path):
    lat_setup(seeded, cfg, stream)   # Opus (a fake) decided: the coffee capture same as the coffee idea, the build
    sent = []                        # capture develops the signal idea, the LoRa capture is new
    out = match_bench.run(seeded, fake_jev(sent), tmp_path, names.Scrubber(b"k"))
    assert out == {"decisions": 3, "asked": len(sent), "answered": len(sent)}
    assert all(set(b["questions"]) == set(jev.QUESTIONS["match"]) for b in sent)
    # Each request is logged with the capture's segments and those of the idea it was asked about.
    logged = table(seeded, """SELECT g.segment_ids FROM im.egress_log g WHERE g.model = 'jev-1.14.0'""")
    matcher = {s for (segs,) in table(seeded, """SELECT g.segment_ids FROM im.item_matches m
                                                JOIN im.egress_log g USING (egress_id)""") for s in segs}
    assert len(logged) == len(sent) and all(set(segs) & matcher for (segs,) in logged)

    r = match_bench.score(tmp_path)
    assert r["by_opus"] == {"new": 1, "same_as": 1, "evolves": 1} and r["chosen_unknown"] == 0
    assert (r["best"]["agree"], r["best"]["same_as_precision"], r["best"]["same_as_recall"]) == (1.0, 1.0, 1.0)
    assert r["auc"]["same_as:mean"] == 1.0 and r["auc"]["evolves:mean"] == 1.0
    # Jev rates the development 0.75 on "develops": a cut above that makes it a new idea, and Opus disagrees.
    assert r["agree"]["same 0.5"].split()[-1] == "0.67"   # at develops 0.8: the development read as new, 2 of 3

    assert match_bench.run(seeded, fake_jev(sent), tmp_path, names.Scrubber(b"k"))["answered"] == out["answered"]
    assert len(sent) == out["asked"]   # a rerun sends nothing it has an answer to


def test_the_matchers_request_is_read_back_as_it_was_shown():
    payload = ("Item (idea, said by me): “the build broke — again” — Builds break.\n\nCandidates:\n"
               "1. Signals: A broken build is a signal (my note: true)\n2. Coffee: The order is\nthe meeting.")
    item, candidates = match_bench.parse(payload)
    assert item == {"kind": "idea", "said_by": "me", "quote": "the build broke — again", "summary": "Builds break."}
    assert candidates == [{"text": "Signals: A broken build is a signal", "note": "true"},
                          {"text": "Coffee: The order is\nthe meeting."}]
