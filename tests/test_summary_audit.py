"""Summaries checked against what was said: a blind sheet across the extraction trials' models."""

import json

from im import extract, names, probe_audit, summary_audit, trial

from helpers import run
from test_carry import DATABASE, capture
from test_extract import FakeClaude
from test_probe_audit import FakeProposer, fill


def test_a_summary_sheet_samples_every_models_captures_blind(pipe, cfg, stream, tmp_path):
    run(pipe, cfg, stream)
    v3 = extract.system_prompt("v3")
    scrubber = names.Scrubber(b"k")
    for model in ("claude-opus-5-5", "claude-sonnet-5-5"):
        assert trial.run(pipe, FakeClaude(capture(DATABASE)), v3, model=model, scrubber=scrubber)["read"]
    out = summary_audit.write(pipe, tmp_path, size=4)
    assert out["by_model"] == {"claude-opus-5-5": 1, "claude-sonnet-5-5": 1}   # as many from each as both can give
    sheet = (tmp_path / "sheet.md").read_text()
    assert "sonnet" not in sheet and "opus" not in sheet                       # which model wrote it stays hidden
    assert "What was said:" in sheet and "managed database" in sheet and "Summary: managed database" in sheet

    fill(tmp_path, lambda p: "yes" if p["set"] == "claude-sonnet-5-5" else "no")
    model = FakeProposer()   # a checker that says every summary adds something
    d = probe_audit.draft(pipe, model, tmp_path, "claude-haiku-5-5")
    assert d["pairs"] == 2 and model.calls[0]["system"] == probe_audit.DRAFT_SYSTEM["summary"]
    r = probe_audit.score(tmp_path)
    assert r["by_set"]["claude-sonnet-5-5"]["hit_rate"] == 1.0 and r["by_set"]["claude-opus-5-5"]["hit_rate"] == 0.0
    assert r["drafts"]["claude-haiku-5-5-low-B1"]["precision"] == 0.5
    segs = json.loads((tmp_path / "key.json").read_text())["pairs"]["1"]["segments"]
    logged = pipe.execute("SELECT segment_ids FROM im.egress_log WHERE model = 'claude-haiku-5-5'").fetchall()
    assert any(set(map(str, s)) == set(segs) for (s,) in logged)   # each check is logged with the lines it showed
