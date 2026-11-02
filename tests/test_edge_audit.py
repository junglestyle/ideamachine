"""B4: the blind sheet of pairs of clusters, and tallying my marks on it."""

import json
import re

import pytest

from im import edge_audit, observations

from test_lattice import lat_setup


@pytest.fixture
def lattice_with_clusters(seeded, cfg, stream):
    lat_setup(seeded, cfg, stream)   # three archive ideas, plus captured clusters, one evolving an archive idea
    observations.assign_stage(seeded)
    return seeded


def fill(folder, mark_of):
    """Mark each pair on the sheet from what the key says it is."""
    key = json.loads((folder / "key.json").read_text())
    sheet = (folder / "sheet.md").read_text()
    for n, p in key["pairs"].items():
        mark, note = mark_of(p)
        sheet = re.sub(rf"(## {n}\n(?:.*\n)*?)mark: \nnote: ", rf"\1mark: {mark}\nnote: {note}", sheet, count=1)
    (folder / "sheet.md").write_text(sheet)


def test_the_sheet_is_blind_and_draws_every_set(lattice_with_clusters, tmp_path):
    out = edge_audit.write(lattice_with_clusters, tmp_path)
    assert out["by_set"]["evolves"] == 1 and out["by_set"]["near"] >= 1
    key = json.loads((tmp_path / "key.json").read_text())
    assert len({tuple(p["ideas"]) for p in key["pairs"].values()}) == out["pairs"]   # no pair twice
    body = (tmp_path / "sheet.md").read_text().split("\n## 1\n", 1)[1]
    assert "Signal may not be true" in body and "> \"" in body   # statements, and quotes of what was said
    assert body.index("> \"") < body.index("Claude's summary")   # what was said comes first
    assert not re.search(r"near|evolves|far|similar|0\.\d", body)   # nothing says why a pair is there
    with pytest.raises(SystemExit):   # never over my marks
        edge_audit.write(lattice_with_clusters, tmp_path)


def test_a_sheet_has_as_many_pairs_as_asked_for(lattice_with_clusters):
    # An evolves pair is usually a near pair too: drawing it twice mustn't leave the sheet short.
    pool = edge_audit.candidates(lattice_with_clusters)
    every = {tuple(p["ideas"]) for s in pool.values() for p in s}
    for size in range(1, len(every) + 1):
        assert len(edge_audit.build(lattice_with_clusters, size=size)[1]["pairs"]) == size


def test_the_same_seed_makes_the_same_sheet(lattice_with_clusters):
    assert edge_audit.build(lattice_with_clusters, seed=1) == edge_audit.build(lattice_with_clusters, seed=1)


def test_scoring_tallies_labels_direction_and_notes(lattice_with_clusters, tmp_path):
    edge_audit.write(lattice_with_clusters, tmp_path)
    assert edge_audit.score(tmp_path)["marked"] == 0
    fill(tmp_path, lambda p: ("ext b > a", "a later version, not an extension") if p["set"] == "evolves"
         else ("none", "") if p["set"] == "far" else ("`con`", ""))
    r = edge_audit.score(tmp_path)
    assert r["missing"] == [] and r["by_set"]["evolves"] == {"extends": 1}
    assert r["direction_given"] == "1 of 1" and [m["direction"] for m in r["marks"] if m["set"] == "evolves"] == ["B>A"]
    near = sum(r["by_set"]["near"].values())
    assert near and r["contradicts_among_near"] == f"{near} of {near}"
    assert [n["note"] for n in r["notes"]] == ["a later version, not an extension"]


def test_marks_read_shorthand_and_ignore_blanks():
    sheet = "## 1\n\nmark: sup A>B\nnote: \n\n## 2\n\nmark: \nnote: \n\n## 3\n\nmark: rel B>A\nnote: odd one\n"
    assert edge_audit.marks(sheet) == {"1": {"label": "supports", "direction": "A>B"},
                                       "3": {"label": "related", "direction": None, "note": "odd one"}}
