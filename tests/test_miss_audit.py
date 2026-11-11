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
