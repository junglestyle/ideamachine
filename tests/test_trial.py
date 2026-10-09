"""Candidate extraction prompts: v1 never changes, and a trial reads judged episodes without touching my queue."""

from im import extract, trial

from helpers import run, table
from test_carry import DATABASE, capture
from test_extract import FakeClaude


def test_the_first_prompt_keeps_its_version():
    # Every extraction so far is filed under this; a different one would re-read every episode.
    assert extract.prompt_version("claude-opus-5-5", "medium") == "a63128f23cb17bed"
    assert extract.system_prompt("v1", "anything at all") == extract.SYSTEM


def test_interests_fill_v2s_slot_and_are_part_of_its_version():
    plain, dance = extract.system_prompt("v2"), extract.system_prompt("v2", "partner dancing")
    assert "{interests}" not in plain and "My interests" not in plain
    assert "My interests, as context for what's worth a closer look (not a filter): partner dancing" in dance
    versions = {extract.prompt_version("claude-opus-5-5", "medium", s) for s in (extract.SYSTEM, plain, dance)}
    assert len(versions) == 3


def both(*needles):
    return lambda text: [i for n in needles for i in capture(n)(text)]


def test_a_trial_compares_the_candidate_with_my_verdicts(pipe, cfg, stream):
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(both(DATABASE, "coffee")))
    verdicts = {"keep": DATABASE, "discard": "coffee"}
    for verdict, needle in verdicts.items():
        (item,) = [r[0] for r in table(pipe, "SELECT item_id FROM im.items WHERE quote LIKE %s", f"%{needle}%")]
        extract.decide(pipe, item, verdict)
    items_before = table(pipe, "SELECT count(*) FROM im.items")

    v2 = extract.system_prompt("v2")
    candidate = FakeClaude(both(DATABASE, "lockfile"))   # keeps the keep, skips the discard, finds something new
    stats = trial.run(pipe, candidate, v2)
    assert stats["read"] == 2 and all(c["system"] == v2 for c in candidate.calls)
    pv = extract.prompt_version("claude-opus-5-5", "medium", v2)
    assert table(pipe, "SELECT count(*) FROM im.egress_log WHERE prompt_version = %s", pv) == [(2,)]
    assert table(pipe, "SELECT count(*) FROM im.items") == items_before   # nothing reaches my queue

    r = trial.report(pipe, pv)
    assert (r["kept"], r["kept_found"], r["discarded"], r["discarded_found"], r["unjudged"]) == (1, 1, 1, 0, 1)
    assert (r["keep_rate_before"], r["keep_rate_on_judged"], r["keep_rate_if_unjudged_are_discards"]) == (0.5, 1.0, 0.5)
    assert r["skipped_discards"] == ["Who's got the coffee order?"]
    assert r["unjudged_captures"] == ["idea: We should pin the cache key to the lockfile hash."]

    assert trial.run(pipe, candidate, v2)["read"] == 0   # read once per payload
    assert trial.run(pipe, candidate, extract.system_prompt("v2", "x"), monthly_cap_usd=0)["stopped"]
