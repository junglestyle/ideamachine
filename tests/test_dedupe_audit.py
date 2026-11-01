"""B5: the blind sheet of merged captures and near misses, and scoring my marks on it."""

import json
import re

import pytest

from im import dedupe_audit, extract, observations

from helpers import run
from test_carry import DATABASE, capture, retranscribe
from test_extract import FakeClaude

OTHER = "Coffee beans should be ground right before brewing, never the night before."


def with_a_near_miss(text):
    """The database capture, and a second capture of the same line with different words."""
    items = capture(DATABASE)(text)
    return items + [dict(it, quote=OTHER, gist="coffee") for it in items]


@pytest.fixture
def audited(pipe, cfg, stream):
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(capture(DATABASE)))
    observations.assign_stage(pipe)
    retranscribe(pipe, cfg, stream, FakeClaude(with_a_near_miss))   # joins the old observation, plus one apart
    assert observations.assign_stage(pipe) == {"new": 1, "joined": 1, "dropped": 0}
    return pipe


def fill(folder, mark_of):
    """Mark each pair on the sheet from what the key says it is."""
    key = json.loads((folder / "key.json").read_text())
    sheet = (folder / "sheet.md").read_text()
    for n, p in key["pairs"].items():
        sheet = re.sub(rf"(## {n}\n(?:.*\n)*?)mark: ", rf"\1mark: {mark_of(p)}", sheet, count=1)
    (folder / "sheet.md").write_text(sheet)


def test_the_sheet_is_blind_and_has_both_sets(audited, tmp_path):
    out = dedupe_audit.write(audited, tmp_path, size=50)
    assert out["merged"] == 1 and out["near"] == 2   # the coffee capture is apart from both database ones
    sheet = (tmp_path / "sheet.md").read_text()
    assert OTHER in sheet and "managed database" in sheet
    assert not re.search(r"merged|near|0\.\d", sheet)   # nothing says which set a pair is from
    assert all(p["score"] < dedupe_audit.CARRY_MIN_SCORE
               for p in json.loads((tmp_path / "key.json").read_text())["pairs"].values() if p["set"] == "near")
    with pytest.raises(SystemExit):   # never over my marks
        dedupe_audit.write(audited, tmp_path)


def test_the_same_seed_makes_the_same_sheet(audited, tmp_path):
    assert dedupe_audit.build(audited, seed=1) == dedupe_audit.build(audited, seed=1)


def test_scoring_counts_false_merges_and_splits(audited, tmp_path):
    dedupe_audit.write(audited, tmp_path, size=1)
    assert dedupe_audit.score(tmp_path)["passes"] is None   # nothing marked yet
    fill(tmp_path, lambda p: "same")
    got = dedupe_audit.score(tmp_path)
    assert (got["false_merges"], got["false_splits"], got["missing"], got["passes"]) == (0, 1, [], True)

    (tmp_path / "sheet.md").write_text(re.sub(r"mark: .*", "mark: ", (tmp_path / "sheet.md").read_text()))
    fill(tmp_path, lambda p: "different" if p["set"] == "merged" else "d")
    got = dedupe_audit.score(tmp_path)
    assert (got["false_merges"], got["false_splits"], got["passes"]) == (1, 0, True)   # 1 in 1 is under the bar of 2


def test_marks_reads_what_i_typed():
    sheet = "## 1\n\nmark: Same\n\n## 2\nmark: `d`\n\n## 3\nmark: ?\n\n## 4\nmark: maybe\n## 5\nmark:\n"
    assert dedupe_audit.marks(sheet) == {"1": "same", "2": "different", "3": "unsure"}
