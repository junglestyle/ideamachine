"""Jev: atomic Nouls about structured state, combined in code, drafting the probe sheets."""

import json

import pytest

from im import jev, names, probe_audit

from test_edge_audit import lattice_with_clusters  # noqa: F401 (fixture)
from test_lattice import seeded  # noqa: F401 (fixture)
from test_probe_audit import FakeProposer, fill


def fake(nouls):
    sent = []

    def post(body):
        sent.append(body)
        return {"model": "jev-1.14.0", "answers": {q: {"type": "noul", "noul": nouls.get(q, 0.1)}
                                                    for q in body["questions"]},
                "usage": {"input_tokens": 1000, "output_tokens": 9}}
    return jev.Jev(post=post), sent


def test_a_tension_is_four_atomic_questions_and_the_strongest_counts():
    client, sent = fake({"trade_off": 0.82})
    p, meta = client.ask("tension", {"a": {"said": []}, "b": {"said": []}})
    body = sent[0]
    assert body["model"] == "typesafe/jev-1.14" and set(body["questions"]) == set(jev.QUESTIONS["tension"])
    assert all(q["type"] == "noul" for q in body["questions"].values())
    assert body["questions"]["trade_off"]["criteria"]["true"].startswith("Each gives up")
    assert p["p"] == 0.82 and jev.answer(p["p"]) == "yes"
    assert meta["cost_usd"] == pytest.approx(1000 * 0.042 / 1e6) and json.loads(meta["payload"]) == body


def test_an_analogy_needs_both_and_the_middle_is_unsure():
    client, _ = fake({"different_subjects": 0.9, "same_structure": 0.5})
    p, _ = client.ask("analogy", {"a": {}, "b": {}})
    assert p["p"] == 0.5 and jev.answer(0.5) == "unsure" and jev.answer(0.2) == "no"


def test_jev_drafts_a_sheet_from_structured_state_with_names_pseudonymized(lattice_with_clusters, tmp_path):  # noqa: F811
    probe_audit.write(lattice_with_clusters, FakeProposer(), None, "tension", tmp_path)
    key = json.loads((tmp_path / "key.json").read_text())
    fill(tmp_path, lambda p: "no")
    client, sent = fake({"contradiction": 0.95})
    scrubber = names.Scrubber(b"k", known=["Bob", "Alice"])
    d = probe_audit.draft(lattice_with_clusters, client, tmp_path, "jev-1.14.0", scrubber=scrubber)
    assert d["draft"].endswith("draft-jev-1.14.0.json") and d["pairs"] == len(key["pairs"]) == len(sent)
    state = sent[0]["state"]
    assert set(state) == {"a", "b"} and "summary" in state["a"] and isinstance(state["a"]["said"], list)
    assert "Bob" not in json.dumps([b["state"] for b in sent]) and "Alice" not in json.dumps([b["state"] for b in sent])
    got = json.loads((tmp_path / "draft-jev-1.14.0.json").read_text())
    assert all(a["answer"] == "yes" and a["p"] == 0.95 for a in got.values())
    r = probe_audit.score(tmp_path)["drafts"]["jev-1.14.0"]
    assert r["precision"] == 0.0 and len(r["invented"]) == len(key["pairs"])
    logged = lattice_with_clusters.execute(
        "SELECT count(*), sum(cost_usd) FROM im.egress_log WHERE model = 'jev-1.14.0'").fetchone()
    assert logged[0] == len(sent) and float(logged[1]) == pytest.approx(len(sent) * 1000 * 0.042 / 1e6)
    quotes, qsent = fake({})
    probe_audit.draft(lattice_with_clusters, quotes, tmp_path, "jev-1.14.0", quotes_only=True)
    assert all("summary" not in b["state"]["a"] for b in qsent)


def test_the_route_decides_the_endpoint_key_and_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key")
    via = jev.Jev()
    assert (via.route, via.url, via.key, via.sent_as) == ("openrouter", "https://openrouter.ai/api/v1/systemone",
                                                          "or-key", "typesafe/jev-1.14")
    direct = jev.Jev(route="typesafe")
    assert (direct.url, direct.key, direct.sent_as) == ("https://api.typesafe.ai/v1/systemone", "ts-key",
                                                        "jev-1.14.0")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert jev.Jev().route == "typesafe"
