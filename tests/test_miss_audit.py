"""What a cheaper extraction model misses, marked blind beside what it found."""

import json

import pytest

from im import extract, miss_audit, probe_audit, trial

from helpers import run, table
from test_carry import DATABASE
from test_extract import FakeClaude
from test_probe_audit import fill
from test_trial import both


def test_a_misses_sheet_shows_the_references_solid_captures_the_candidate_missed_beside_ones_it_found(
        pipe, cfg, stream, tmp_path):
    run(pipe, cfg, stream)
    v5 = extract.SYSTEM_V5_3
    trial.run(pipe, FakeClaude(both(DATABASE, "coffee", "lockfile")), v5)
    trial.run(pipe, FakeClaude(both(DATABASE, "coffee")), v5, replicate=2)   # the lockfile only one read caught
    trial.run(pipe, FakeClaude(both(DATABASE)), v5, model="claude-sonnet-5-5")
    opus, sonnet = "claude-opus-5-5", "claude-sonnet-5-5"
    candidate = (trial.version(sonnet, "medium", v5), sonnet)
    reference = (trial.version(opus, "medium", v5), opus)
    replicate = (trial.version(opus, "medium", v5, 2), opus)
    sent = table(pipe, "SELECT count(*) FROM im.egress_log")

    out = miss_audit.write(pipe, tmp_path, candidate, reference, replicate)
    assert out["by_set"] == {"missed": 1, "found": 1}
    assert table(pipe, "SELECT count(*) FROM im.egress_log") == sent   # the sheet sends nothing
    sheet = (tmp_path / "sheet.md").read_text()
    assert "coffee" in sheet and DATABASE in sheet
    assert "lockfile" not in sheet   # a capture the reference makes only sometimes isn't on it, missed or not
    key = json.loads((tmp_path / "key.json").read_text())
    assert {p["set"]: p["gist"] for p in key["pairs"].values()} == {"missed": "coffee", "found": DATABASE}

    fill(tmp_path, lambda p: "no" if p["set"] == "missed" else "yes")
    r = probe_audit.score(tmp_path)
    assert r["by_set"] == {"missed": {"no": 1, "unsure": 0, "hit_rate": 0.0},
                           "found": {"yes": 1, "no": 0, "unsure": 0, "hit_rate": 1.0}}


def test_a_misses_sheet_needs_the_references_second_read(pipe, cfg, stream, tmp_path):
    run(pipe, cfg, stream)
    v5 = extract.SYSTEM_V5_3
    trial.run(pipe, FakeClaude(both(DATABASE, "coffee")), v5)
    trial.run(pipe, FakeClaude(both(DATABASE)), v5, model="claude-sonnet-5-5")
    opus, sonnet = "claude-opus-5-5", "claude-sonnet-5-5"
    with pytest.raises(SystemExit, match="replicate"):
        miss_audit.write(pipe, tmp_path, (trial.version(sonnet, "medium", v5), sonnet),
                         (trial.version(opus, "medium", v5), opus), (trial.version(opus, "medium", v5, 2), opus))
    assert not (tmp_path / "sheet.md").exists()


def test_a_both_ways_sheet_puts_what_only_one_model_caught_beside_what_both_did(pipe, cfg, stream, tmp_path):
    run(pipe, cfg, stream)
    v5 = extract.SYSTEM_V5_3
    trial.run(pipe, FakeClaude(both(DATABASE, "coffee")), v5)                         # no second read needed
    trial.run(pipe, FakeClaude(both(DATABASE, "lockfile")), v5, model="claude-sonnet-5-5")
    opus, sonnet = "claude-opus-5-5", "claude-sonnet-5-5"
    out = miss_audit.write(pipe, tmp_path, (trial.version(sonnet, "medium", v5), sonnet),
                           (trial.version(opus, "medium", v5), opus), None)
    assert out["by_set"] == {"candidate_only": 1, "reference_only": 1, "both": 1}
    key = json.loads((tmp_path / "key.json").read_text())
    assert {p["set"]: p["gist"] for p in key["pairs"].values()} == \
        {"candidate_only": "lockfile", "reference_only": "coffee", "both": DATABASE}
    assert key["benchmark"] == "both-ways" and "replicate" not in key["prompt_versions"]


def test_a_live_sheet_marks_a_trial_against_the_live_read_and_scores_with_denominators(pipe, cfg, stream, tmp_path):
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(both(DATABASE, "coffee")))                      # the live read, v5.3
    trial.run(pipe, FakeClaude(both(DATABASE, "lockfile")), extract.SYSTEM_V5_4)       # the trial, v5.4
    opus = "claude-opus-5-5"
    sent = table(pipe, "SELECT count(*) FROM im.egress_log")
    out = miss_audit.write(pipe, tmp_path, (trial.version(opus, "medium", extract.SYSTEM_V5_4), opus),
                           (extract.prompt_version(opus, "medium"), opus), None, live=True)
    assert table(pipe, "SELECT count(*) FROM im.egress_log") == sent   # the sheet sends nothing
    assert out["by_set"] == {"removed": 1, "added": 1, "kept": 1} and out["episodes"]["payload_changed"] == 0
    sheet = (tmp_path / "sheet.md").read_text()
    assert "removed" not in sheet and "Capture eligibility, rubric 1." in sheet   # unlabeled, under the rubric
    key = json.loads((tmp_path / "key.json").read_text())
    assert {p["set"]: p["gist"] for p in key["pairs"].values()} == \
        {"removed": "coffee", "added": "lockfile", "kept": DATABASE}

    fill(tmp_path, lambda p: {"removed": "i", "added": "e", "kept": "eligible"}[p["set"]])
    r = miss_audit.score_live(tmp_path)
    assert r["removed"]["n"] == 1 and r["removed"]["ineligible"]["k"] == r["removed"]["ineligible"]["n"] == 1
    assert r["valid_baseline"]["N"] == 1 and (r["valid_lost"]["k"], r["valid_lost"]["n"]) == (0, 1)
    assert r["added"]["a"] == 1 and (r["added"]["ineligible"]["k"], r["added"]["ineligible"]["n"]) == (0, 1)
