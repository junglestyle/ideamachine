"""Candidate extraction prompts and models: the live prompt's version never changes, and a trial reads a fixed sample
without touching the lattice, scored against a reference model."""

import json
from dataclasses import replace
from types import SimpleNamespace

from im import cli, config, egress, extract, jev, trial

from helpers import run, system_of, table
from test_carry import DATABASE, capture
from test_extract import FakeClaude
from test_jev import fake
from test_lattice import items_for, seeded, seeded_lattice  # noqa: F401 (fixtures)


class OffSchema(FakeClaude):
    """Answers in a shape the schema doesn't allow, at $4 a request (a million tokens in, on Opus 5.5)."""

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"captures": []}')], stop_reason="end_turn",
                               model="claude-opus-5-5", _request_id="r",
                               usage=SimpleNamespace(input_tokens=1_000_000, output_tokens=0))


def test_an_answer_that_doesnt_keep_to_the_schema_is_paid_for_and_counts_toward_the_cap(pipe, cfg, stream):
    run(pipe, cfg, stream)
    claude = OffSchema()
    stats = trial.run(pipe, claude, extract.SYSTEM_V5_3, monthly_cap_usd=5)
    assert len(claude.calls) == stats["failed"] == 2 and stats["stopped"].startswith("monthly cap")
    assert egress.spent_this_month(pipe) == 8.0


def test_the_live_prompt_keeps_its_version():
    # Every extraction since the rebuild is filed under this; a different one would re-read every episode.
    assert extract.prompt_version("claude-opus-5-5", "medium") == "2fee34df8c9f5502" == \
        extract.prompt_version("claude-opus-5-5", "medium", extract.PROMPTS["v5.3"], extract.SCHEMA_V5)


def test_a_leftover_interests_key_is_ignored_with_a_warning(tmp_path, monkeypatch, capsys):
    path = tmp_path / "im.toml"
    path.write_text('[extract]\nprompt = "v5.3"\ninterests = "model railways"\n')
    monkeypatch.setenv("IM_CONFIG", str(path))
    assert config.load().extract == config.ExtractConfig()
    assert f"[extract] interests: retired with prompts v1 to v4 (decision 0012); delete it from {path}" in \
        capsys.readouterr().err


def both(*needles):
    return lambda text: [i for n in needles for i in capture(n)(text)]


def test_a_trial_scores_a_candidate_against_a_reference_on_the_same_sample(pipe, cfg, stream):
    run(pipe, cfg, stream)
    items_before = table(pipe, "SELECT count(*) FROM im.items")
    episodes = table(pipe, "SELECT count(*) FROM im.episodes WHERE current")[0][0]
    assert trial.sample(pipe, 2) == trial.sample(pipe, 5)[:2]   # a smaller sample is a prefix of a larger one

    v5 = extract.SYSTEM_V5_3
    opus = FakeClaude(both(DATABASE, "coffee"))
    assert trial.run(pipe, opus, v5)["read"] == episodes and all(system_of(c) == v5 for c in opus.calls)
    assert "fallbacks" in opus.calls[0]
    sonnet = FakeClaude(both(DATABASE, "lockfile"))   # finds one of Opus's two, and one of its own
    assert trial.run(pipe, sonnet, v5, model="claude-sonnet-5-5")["read"] == episodes
    assert {c["model"] for c in sonnet.calls} == {"claude-sonnet-5-5"} and not any("fallbacks" in c for c in sonnet.calls)
    assert table(pipe, "SELECT count(*) FROM im.items") == items_before   # nothing reaches the lattice

    pv = lambda model: extract.prompt_version(model, "medium", v5)  # noqa: E731
    r = trial.report(pipe, (pv("claude-sonnet-5-5"), "claude-sonnet-5-5"), (pv("claude-opus-5-5"), "claude-opus-5-5"))
    assert (r["episodes"], r["reference_captures"], r["found"], r["candidate_captures"], r["extra"]) == \
        (episodes, 2, 1, 2, 1)
    assert r["recall"] == 0.5
    assert r["missed"] == ["Who's got the coffee order?"]
    assert r["extra_examples"] == ["We should pin the cache key to the lockfile hash."]
    # The lockfile line is in the coffee's episode: the candidate missed the coffee there, but not the episode.
    assert r["episodes_reference_captured_in"] == 2 and r["of_those_candidate_captured_nothing"] == 0
    assert r["cost_vs_reference"] == 1.0   # the fake answers as Opus, at Opus's price, for both

    assert trial.run(pipe, sonnet, v5, model="claude-sonnet-5-5")["read"] == 0   # read once per payload
    again = FakeClaude(both(DATABASE))   # a replicate reads everything again, filed apart, scored against the first
    assert trial.run(pipe, again, v5, replicate=2)["read"] == episodes
    r2 = trial.report(pipe, (trial.version("claude-opus-5-5", "medium", v5, 2), "claude-opus-5-5"),
                      (pv("claude-opus-5-5"), "claude-opus-5-5"))
    assert r2["recall"] == 0.5 and trial.version("claude-opus-5-5", "medium", v5, 2) == pv("claude-opus-5-5") + "~2"
    assert trial.run(pipe, opus, extract.SYSTEM_V5_2, monthly_cap_usd=0)["stopped"]


def test_a_thought_quoted_from_the_next_line_counts_as_found_once_jev_says_its_the_same_point(pipe, cfg, stream):
    run(pipe, cfg, stream)
    v5 = extract.SYSTEM_V5_3
    trial.run(pipe, FakeClaude(both(DATABASE, "coffee")), v5)
    trial.run(pipe, FakeClaude(both(DATABASE, "lockfile")), v5, model="claude-sonnet-5-5")   # the next line
    pv = lambda model: extract.prompt_version(model, "medium", v5)  # noqa: E731
    cand, ref = (pv("claude-sonnet-5-5"), "claude-sonnet-5-5"), (pv("claude-opus-5-5"), "claude-opus-5-5")
    by_quote = trial.report(pipe, cand, ref)

    def counted(nouls):
        judge, sent = fake(nouls)
        stats = trial.same_point(pipe, judge, cand, ref, 20.0)
        return stats, sent, trial.report(pipe, cand, ref, jev.kept(pipe, jev.question_set(judge, "match")))

    stats, sent, unsure = counted({"same": 0.7, "same_v2": 0.6})   # below the cut
    assert stats["pairs"] == stats["asked"] == len(sent) == 1 and unsure["found_by_same_point"] == 0
    assert "coffee" in json.dumps(sent[0]["state"]) and "lockfile" in json.dumps(sent[0]["state"])

    pipe.execute("DELETE FROM im.jev_answers")
    stats, sent, r = counted({"same": 0.9, "same_v2": 0.8})
    assert r["found_by_same_point"] == 1 and r["found"] == by_quote["found"] + 1
    assert r["extra"] == by_quote["extra"] - 1
    assert trial.same_point(pipe, fake({})[0], cand, ref, 20.0)["asked"] == 0   # kept, not asked again


def test_a_trial_sends_names_said_as_said_and_logs_each_request(pipe, cfg, stream):
    turns = stream.conversations["c-conv"].turns
    turns[0] = replace(turns[0], text="Did you see the build broke again, Bob?")
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    v5 = extract.SYSTEM_V5_3

    claude = FakeClaude(both("build broke"))
    trial.run(pipe, claude, v5)
    (sent,) = [c["messages"][0]["content"] for c in claude.calls if "build broke" in c["messages"][0]["content"]]
    assert "broke again, Bob?" in sent   # the name said, as said
    assert "S1:" in sent and "Alice" not in sent   # speakers are pseudonymized

    (items,) = [i for (i,) in table(pipe, "SELECT items FROM im.prompt_trials WHERE privacy_policy_version = %s",
                                    egress.POLICY_VERSION) if i]
    assert items[0]["quote"] == "Did you see the build broke again, Bob?"
    # One row per request: exactly what was sent, and the episode's segments, line by line.
    logged = table(pipe, """SELECT g.payload, g.segment_ids, (SELECT array_agg(segment_id ORDER BY ord)
                                                              FROM im.episode_segments WHERE episode_id = g.episode_id)
                            FROM im.egress_log g WHERE privacy_policy_version = %s ORDER BY egress_id""",
                   egress.POLICY_VERSION)
    assert [p for p, _, _ in logged] == [c["messages"][0]["content"] for c in claude.calls]
    assert all(sent_ids == lines for _, sent_ids, lines in logged)
    assert trial.run(pipe, claude, v5)["read"] == 0   # read once per payload


def test_a_trial_keeps_the_quote_as_said_apart_from_its_reading_and_attribution(pipe, cfg, stream):
    turns = stream.conversations["c-conv"].turns
    turns[0] = replace(turns[0], text="Did you see the build broke again, Bob?")
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    v5 = extract.SYSTEM_V5_2

    def respond(text):   # the line as said, and the same line tidied: only the first is word for word
        out = []
        for line in text.splitlines():
            if line.startswith("[") and "build broke" in line:
                n, said = int(line[1:].split("]")[0]), line.split(": ", 1)[1]
                common = {"lines": [n], "reading": "Bob, the build broke again.", "mishearings": [],
                          "gist": "The build keeps breaking."}
                out += [common | {"quote": said, "attributed_to": ""},
                        common | {"quote": "Did you see that the build broke", "attributed_to": "Bob's lead"}]
        return out

    claude = FakeClaude(respond)
    trial.run(pipe, claude, v5)
    assert all(c["output_config"]["format"]["schema"] == extract.SCHEMA_V5 for c in claude.calls)
    (items,) = [i for (i,) in table(pipe, "SELECT items FROM im.prompt_trials") if i]
    assert [(it["reading"], it["attributed_to"]) for it in items] == \
        [("Bob, the build broke again.", ""), ("Bob, the build broke again.", "Bob's lead")]
    opus = (trial.version("claude-opus-5-5", "medium", v5), "claude-opus-5-5")
    assert trial.report(pipe, opus, opus)["verbatim_quotes"] == {"candidate": 0.5, "reference": 0.5}

    # v5.2 is for trials only, so the live run doesn't read under it.
    sent = table(pipe, "SELECT count(*) FROM im.egress_log")
    stats = cli._extract(pipe, replace(cfg, extract=replace(cfg.extract, prompt="v5.2")))
    assert stats["stopped"] == "extract.prompt v5.2 isn't live; `im run` reads with v5.3"
    assert table(pipe, "SELECT count(*) FROM im.egress_log") == sent


def test_v5_records_which_capture_checks_each_capture_fails(pipe, cfg, stream):
    turns = stream.conversations["c-conv"].turns
    turns[0] = replace(turns[0], text="Did you see the build broke again, Bob?")
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    v5, schema = extract.SYSTEM_V5, extract.SCHEMA_V5

    def respond(text):
        rows = {int(r[1:].split("]")[0]): r.split(": ", 1) for r in text.splitlines() if r.startswith("[")}
        mine = next(n for n, (who, said) in rows.items() if who.endswith("me") and "build broke" in said)
        theirs = next(n for n, (who, _) in rows.items() if not who.endswith("me"))
        said, other = rows[mine][1], rows[theirs][1]
        base = {"lines": [mine], "attributed_to": "", "reading": "Bob, the build broke again.", "mishearings": [],
                "gist": "The build broke again."}
        return [base | {"quote": said, "attributed_to": "Bob",
                        "mishearings": [{"heard": "build", "meant": "build"}]},       # passes
                base | {"quote": said.lower()},                                           # tidied
                base | {"quote": other, "lines": [mine, theirs]},                       # someone else's words
                base | {"quote": said, "mishearings": [{"heard": "cash", "meant": "cache"}]}]

    claude = FakeClaude(respond)
    trial.run(pipe, claude, v5, schema=schema)
    assert all(c["output_config"]["format"]["schema"] == extract.SCHEMA_V5 for c in claude.calls)
    (items,) = [i for (i,) in table(pipe, "SELECT items FROM im.prompt_trials") if i]
    assert [it["checks"] for it in items] == [[], ["quote_inexact"], ["quote_not_mine"],
                                              ["mishearing_not_in_quote", "mishearing_not_in_reading"]]
    assert items[0]["attributed_to"] == "Bob" and items[0]["said_by"] == "me" and "confidence" not in items[0]
    opus = (trial.version("claude-opus-5-5", "medium", v5, schema=schema), "claude-opus-5-5")
    assert trial.report(pipe, opus, opus)["checks"]["candidate"]["pass"] == 0.25


def test_recall_is_split_by_how_far_each_captures_idea_reaches(seeded_lattice):
    conn = seeded_lattice
    v5 = extract.SYSTEM_V5_3
    trial.run(conn, FakeClaude(items_for), v5)                          # the reference: what live captured
    trial.run(conn, FakeClaude(), v5, model="claude-haiku-5-5")          # a candidate that captures nothing
    opus, haiku = ((extract.prompt_version(m, "medium", v5), m) for m in ("claude-opus-5-5", "claude-haiku-5-5"))
    itself = trial.report(conn, opus, opus)
    reach = itself["recall_by_reach"]
    assert sum(r["captures"] for r in reach.values()) == itself["reference_captures"] == 3
    assert reach["unplaced"]["captures"] == 0   # live matched every one into a cluster
    assert all(r["recall"] in (1.0, None) for r in reach.values())
    assert all(r["recall"] in (0.0, None) for r in trial.report(conn, haiku, opus)
               ["recall_by_reach"].values())
    # The LoRa idea is new, said once: a one-off.
    lora = [it for (items,) in table(conn, "SELECT items FROM im.prompt_trials") for it in items if "LoRa" in it["quote"]]
    assert trial.by_reach(conn, [(e, lora[0], True) for (e,) in table(
        conn, "SELECT episode_id FROM im.prompt_trials WHERE items::text LIKE '%%LoRa%%' LIMIT 1")])["one_off"] == \
        {"captures": 1, "recall": 1.0}
