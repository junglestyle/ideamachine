"""Candidate extraction prompts and models: v1 never changes, and a trial reads a fixed sample without touching
the lattice, scored against a reference model."""

from dataclasses import replace

from im import egress, extract, names, trial

from helpers import run, table
from test_carry import DATABASE, capture
from test_extract import FakeClaude
from test_lattice import items_for, seeded, seeded_lattice  # noqa: F401 (fixtures)


def test_the_first_prompt_keeps_its_version():
    # Every extraction so far is filed under this; a different one would re-read every episode.
    assert extract.prompt_version("claude-opus-5-5", "medium") == "a63128f23cb17bed"
    assert extract.system_prompt("v1", "anything at all") == extract.SYSTEM


def test_interests_fill_v2s_slot_and_are_part_of_its_version():
    plain, dance = extract.system_prompt("v2"), extract.system_prompt("v2", "model railways")
    assert "{interests}" not in plain and "My interests" not in plain
    assert "My interests, as context for what's worth a closer look (not a filter): model railways" in dance
    versions = {extract.prompt_version("claude-opus-5-5", "medium", s) for s in (extract.SYSTEM, plain, dance)}
    assert len(versions) == 3


def test_v3_widens_to_neutral_observations_with_the_same_slot():
    v3 = extract.system_prompt("v3", "model railways")
    assert "{interests}" not in v3 and "model railways" in v3
    assert "questions someone is actually wondering about" in v3 and "in a different conversation, weeks later" in v3
    assert extract.prompt_version("claude-opus-5-5", "medium", v3) != \
        extract.prompt_version("claude-opus-5-5", "medium", extract.system_prompt("v2", "model railways"))


def both(*needles):
    return lambda text: [i for n in needles for i in capture(n)(text)]


def test_a_trial_scores_a_candidate_against_a_reference_on_the_same_sample(pipe, cfg, stream):
    run(pipe, cfg, stream)
    items_before = table(pipe, "SELECT count(*) FROM im.items")
    episodes = table(pipe, "SELECT count(*) FROM im.episodes WHERE current")[0][0]
    assert trial.sample(pipe, 2) == trial.sample(pipe, 5)[:2]   # a smaller sample is a prefix of a larger one

    v3 = extract.system_prompt("v3")
    opus = FakeClaude(both(DATABASE, "coffee"))
    assert trial.run(pipe, opus, v3)["read"] == episodes and all(c["system"] == v3 for c in opus.calls)
    assert "fallbacks" in opus.calls[0]
    sonnet = FakeClaude(both(DATABASE, "lockfile"))   # finds one of Opus's two, and one of its own
    assert trial.run(pipe, sonnet, v3, model="claude-sonnet-5-5")["read"] == episodes
    assert {c["model"] for c in sonnet.calls} == {"claude-sonnet-5-5"} and not any("fallbacks" in c for c in sonnet.calls)
    assert table(pipe, "SELECT count(*) FROM im.items") == items_before   # nothing reaches the lattice

    pv = lambda model: extract.prompt_version(model, "medium", v3)  # noqa: E731
    r = trial.report(pipe, (pv("claude-sonnet-5-5"), "claude-sonnet-5-5"), (pv("claude-opus-5-5"), "claude-opus-5-5"),
                     egress.POLICY_VERSION)
    assert (r["episodes"], r["reference_captures"], r["found"], r["candidate_captures"], r["extra"]) == \
        (episodes, 2, 1, 2, 1)
    assert r["recall"] == 0.5 and r["recall_by_kind"] == {"idea": 0.5}
    assert r["missed"] == ["idea: Who's got the coffee order?"]
    assert r["extra_examples"] == ["idea: We should pin the cache key to the lockfile hash."]
    # The lockfile line is in the coffee's episode: the candidate missed the coffee there, but not the episode.
    assert r["episodes_reference_captured_in"] == 2 and r["of_those_candidate_captured_nothing"] == 0
    assert r["cost_vs_reference"] == 1.0   # the fake answers as Opus, at Opus's price, for both

    assert trial.run(pipe, sonnet, v3, model="claude-sonnet-5-5")["read"] == 0   # read once per payload
    again = FakeClaude(both(DATABASE))   # a replicate reads everything again, filed apart, scored against the first
    assert trial.run(pipe, again, v3, replicate=2)["read"] == episodes
    r2 = trial.report(pipe, (trial.version("claude-opus-5-5", "medium", v3, 2), "claude-opus-5-5"),
                      (pv("claude-opus-5-5"), "claude-opus-5-5"), egress.POLICY_VERSION)
    assert r2["recall"] == 0.5 and trial.version("claude-opus-5-5", "medium", v3, 2) == pv("claude-opus-5-5") + "~2"
    assert trial.run(pipe, opus, extract.system_prompt("v3", "x"), monthly_cap_usd=0)["stopped"]


def test_under_policy_c_names_said_go_out_pseudonymized_and_come_back(pipe, cfg, stream):
    turns = stream.conversations["c-conv"].turns
    turns[0] = replace(turns[0], text="Did you see the build broke again, Bob?")
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    v3 = extract.system_prompt("v3")
    scrubber = names.Scrubber(b"k", egress.known_names(pipe), mine=["Nathan"])
    assert egress.known_names(pipe) == ["Alice", "Bob"]

    claude = FakeClaude(both("build broke"))
    trial.run(pipe, claude, v3, scrubber=scrubber)
    (sent,) = [c["messages"][0]["content"] for c in claude.calls if "build broke" in c["messages"][0]["content"]]
    bob = names.token("Bob", b"k")
    assert "Bob" not in sent and f"broke again, {bob}?" in sent and "Names said are pseudonyms (P-...)." in sent
    assert "S1:" in sent and "Alice" not in sent   # speakers are still pseudonymized as before

    (items,) = [i for (i,) in table(pipe, "SELECT items FROM im.prompt_trials WHERE privacy_policy_version = %s",
                                    scrubber.policy) if i]
    assert items[0]["quote"] == "Did you see the build broke again, Bob?"
    logged = table(pipe, "SELECT payload FROM im.egress_log WHERE privacy_policy_version = %s", scrubber.policy)
    assert logged and not any("Bob" in p for (p,) in logged)

    # The same model and prompt under policy B is another trial, read again.
    assert trial.run(pipe, claude, v3)["read"] == table(pipe, "SELECT count(*) FROM im.episodes WHERE current")[0][0]


def test_recall_is_split_by_how_far_each_captures_idea_reaches(seeded_lattice):
    conn = seeded_lattice
    v3 = extract.system_prompt("v3")
    trial.run(conn, FakeClaude(items_for), v3)                          # the reference: what live captured
    trial.run(conn, FakeClaude(), v3, model="claude-haiku-5-5")          # a candidate that captures nothing
    opus, haiku = ((extract.prompt_version(m, "medium", v3), m) for m in ("claude-opus-5-5", "claude-haiku-5-5"))
    itself = trial.report(conn, opus, opus, egress.POLICY_VERSION)
    reach = itself["recall_by_reach"]
    assert sum(r["captures"] for r in reach.values()) == itself["reference_captures"] == 3
    assert reach["unplaced"]["captures"] == 0   # live matched every one into a cluster
    assert all(r["recall"] in (1.0, None) for r in reach.values())
    assert all(r["recall"] in (0.0, None) for r in trial.report(conn, haiku, opus, egress.POLICY_VERSION)
               ["recall_by_reach"].values())
    # The LoRa idea is new, said once: a one-off.
    lora = [it for (items,) in table(conn, "SELECT items FROM im.prompt_trials") for it in items if "LoRa" in it["quote"]]
    assert trial.by_reach(conn, [(e, lora[0], True) for (e,) in table(
        conn, "SELECT episode_id FROM im.prompt_trials WHERE items::text LIKE '%%LoRa%%' LIMIT 1")])["one_off"] == \
        {"captures": 1, "recall": 1.0}
