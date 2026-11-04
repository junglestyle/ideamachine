"""Analogy and tension sheets: pairs proposed several ways, asked one question blind, and the hit rate per way."""

import json
import re
from types import SimpleNamespace

from im import probe_audit

from test_edge_audit import lattice_with_clusters  # noqa: F401 (fixture)
from test_lattice import seeded  # noqa: F401 (fixture)
from test_triage import HashEmbedder

SHARED = "an agent keeps the option that cannot fail badly"


class FakeProposer:
    """Facets: every idea gets four lines, and ideas 1 and 2 of each batch share one. Picks: pairs (1, 2), (1, 3)."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        props = kw["output_config"]["format"]["schema"]["properties"]
        n = kw["messages"][0]["content"].count("\n")
        if "answer" in props:   # a draft: yes on every pair
            answer = {"answer": "yes", "why": "they pull"}
        elif "ideas" in props:
            answer = {"ideas": [{"idea": i, "structures": [SHARED if i <= 2 else f"pattern {i} alone",
                                                           f"move {i} one", f"move {i} two", f"move {i} three"]}
                                for i in range(1, n + 1)]}
        else:
            answer = {"pairs": [{"a": 1, "b": 2, "why": "secret reason"}, {"a": 1, "b": 3, "why": "another"}]}
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(answer))], stop_reason="end_turn",
                               model=kw["model"], _request_id="r", usage=SimpleNamespace(input_tokens=900,
                                                                                         output_tokens=90))


def fill(folder, mark_of):
    key = json.loads((folder / "key.json").read_text())
    sheet = (folder / "sheet.md").read_text()
    for n, p in key["pairs"].items():
        sheet = re.sub(rf"(## {n}\n(?:.*\n)*?)mark: \nnote: ", rf"\1mark: {mark_of(p)}\nnote: ", sheet, count=1)
    (folder / "sheet.md").write_text(sheet)


def test_an_analogy_sheet_draws_unrelated_pairs_blind_and_scores_each_way(lattice_with_clusters, tmp_path):  # noqa: F811
    client = FakeProposer()
    out = probe_audit.write(lattice_with_clusters, client, HashEmbedder(), "analogy", tmp_path)
    key = json.loads((tmp_path / "key.json").read_text())
    pairs = list(key["pairs"].values())
    assert out["pairs"] == len(pairs) > 0 and len({tuple(p["ideas"]) for p in pairs}) == len(pairs)
    far = {tuple(p["ideas"]) for p in probe_audit.unrelated(probe_audit.edge_audit._clusters(lattice_with_clusters))}
    assert all(tuple(p["ideas"]) in far for p in pairs)   # every set draws unrelated pairs only
    body = (tmp_path / "sheet.md").read_text()
    assert "secret reason" not in body and "facets" not in body and "Is there an analogy" in body
    assert (tmp_path / "facets.json").exists()
    assert {c["model"] for c in client.calls} == {probe_audit.FACET_MODEL, probe_audit.PICK_MODEL}
    logged = lattice_with_clusters.execute(
        "SELECT count(*) FROM im.egress_log WHERE episode_id IS NULL AND model = ANY(%s)",
        ([probe_audit.FACET_MODEL, probe_audit.PICK_MODEL],)).fetchone()[0]
    assert logged == len(client.calls)

    fill(tmp_path, lambda p: "yes" if p["set"] != "random" else "no")
    r = probe_audit.score(tmp_path)
    assert r["missing"] == [] and r["marked"] == len(pairs)
    assert all(c["hit_rate"] == (0.0 if s == "random" else 1.0) for s, c in r["by_set"].items() if c["yes"] + c["no"])
    assert all("also_proposed_by" in y for y in r["yes"])


def test_a_tension_sheet_scores_yes_no_and_says_when_a_mark_isnt_one(lattice_with_clusters, tmp_path):  # noqa: F811
    out = probe_audit.write(lattice_with_clusters, FakeProposer(), None, "tension", tmp_path)
    assert out["pairs"] > 0 and "Do A and B pull against each other" in (tmp_path / "sheet.md").read_text()
    fill(tmp_path, lambda p: "maybe")
    r = probe_audit.score(tmp_path)
    assert r["marked"] == 0 and [u["mark"] for u in r["unreadable"]] == ["maybe"] * out["pairs"]


def test_marks_are_one_word():
    got = probe_audit.marks("## 1\nmark: y\nnote: same move\n## 2\nmark: ?\n## 3\nmark: \n")
    assert got == {"1": {"mark": "yes", "note": "same move"}, "2": {"mark": "unsure"}}


def test_a_model_answers_the_sheet_blind_and_is_scored_against_me(lattice_with_clusters, tmp_path):  # noqa: F811
    out = probe_audit.write(lattice_with_clusters, FakeProposer(), None, "tension", tmp_path)
    fill(tmp_path, lambda p: "yes" if p["set"] == "model" else "no")
    model = FakeProposer()
    d = probe_audit.draft(lattice_with_clusters, model, tmp_path, "claude-opus-5-5")
    assert d["pairs"] == out["pairs"] == len(model.calls)
    sent = "\n".join(c["messages"][0]["content"] for c in model.calls)
    assert "mark:" not in sent and "Do they pull against each other" in model.calls[0]["system"]
    assert probe_audit.draft(lattice_with_clusters, model, tmp_path, "claude-opus-5-5")["pairs"] == out["pairs"]
    assert len(model.calls) == out["pairs"]   # a rerun asks nothing it has already answered
    r = probe_audit.score(tmp_path)["drafts"]["claude-opus-5-5-low"]
    mine_yes = sum(1 for p in json.loads((tmp_path / "key.json").read_text())["pairs"].values() if p["set"] == "model")
    assert r["recall"] == (1.0 if mine_yes else None) and r["pairs"] == out["pairs"]
    assert r["precision"] == (round(mine_yes / out["pairs"], 3) if out["pairs"] else None)


def test_agreement_counts_only_pairs_both_answered():
    mine = {"1": "yes", "2": "no", "3": "unsure", "4": "yes"}
    theirs = {"1": {"answer": "yes"}, "2": {"answer": "yes"}, "3": {"answer": "no"}, "4": {"answer": "unsure"}}
    r = probe_audit.agreement(mine, theirs)
    assert (r["pairs"], r["agree"], r["precision"], r["recall"]) == (2, 0.5, 0.5, 1.0)
    assert (r["found"], r["invented"], r["missed"]) == ([1], [2], [])
