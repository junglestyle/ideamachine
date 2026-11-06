"""Analogy and tension sheets: pairs proposed several ways, asked one question blind, and the hit rate per way."""

import hashlib
import itertools
import json
import re
import time
from types import SimpleNamespace

import pytest

from im import egress, jev, llm, probe_audit

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


def fake_jev(fail_after: int | None = None, takes: float = 0.0):
    """Jev answering each question with a probability that differs by pair and question, the same every time; and
    every body it was sent. With `fail_after`, the requests after that many are rate-limited; each answer takes
    `takes` seconds."""
    sent = []

    def post(body):
        sent.append(body)
        time.sleep(takes)
        if fail_after is not None and len(sent) > fail_after:
            raise llm.Unavailable("Jev via openrouter, HTTP 429")
        state = json.dumps(body["state"], sort_keys=True)
        return {"model": "jev-1.14.0", "answers": {
            q: {"type": "noul", "noul": int(hashlib.sha256((state + q).encode()).hexdigest()[:4], 16) / 65535}
            for q in body["questions"]}, "usage": {"input_tokens": 1000}}
    return jev.Jev(post=post), sent


def fill(folder, mark_of):
    key = json.loads((folder / "key.json").read_text())
    sheet = (folder / "sheet.md").read_text()
    for n, p in key["pairs"].items():
        sheet = re.sub(rf"(## {n}\n(?:.*\n)*?)mark: \nnote: ", rf"\1mark: {mark_of(p)}\nnote: ", sheet, count=1)
    (folder / "sheet.md").write_text(sheet)


def test_an_analogy_sheet_draws_unrelated_pairs_blind_and_scores_each_way(lattice_with_clusters, tmp_path):  # noqa: F811
    client, (jev_client, sent) = FakeProposer(), fake_jev()
    out = probe_audit.write(lattice_with_clusters, client, HashEmbedder(), "analogy", tmp_path, jev=jev_client)
    key = json.loads((tmp_path / "key.json").read_text())
    pairs = list(key["pairs"].values())
    assert out["pairs"] == len(pairs) > 0 and len({tuple(p["ideas"]) for p in pairs}) == len(pairs)
    far = {tuple(p["ideas"]) for p in probe_audit.unrelated(probe_audit.edge_audit._clusters(lattice_with_clusters))}
    assert all(tuple(p["ideas"]) in far for p in pairs)   # every set draws unrelated pairs only
    body = (tmp_path / "sheet.md").read_text()
    assert "secret reason" not in body and "facets" not in body and "Jev" not in body and "Is there an analogy" in body
    assert (tmp_path / "facets.json").exists()
    assert {c["model"] for c in client.calls} == {probe_audit.FACET_MODEL}
    logged = lattice_with_clusters.execute(
        "SELECT count(*) FROM im.egress_log WHERE episode_id IS NULL AND model = %s", (probe_audit.FACET_MODEL,)
    ).fetchone()[0]
    assert logged == len(client.calls)

    # Jev was asked every analogy question about each pool pair once, every request logged, and each question
    # but the ones that only describe a pair proposes its best.
    asked = set(jev.QUESTIONS["analogy"]) | set(jev.PROBES["analogy"])
    assert key["jev_pool"] == len(sent) > 0 and all(set(b["questions"]) == asked for b in sent)
    assert lattice_with_clusters.execute("SELECT count(*) FROM im.egress_log WHERE model = 'jev-1.14.0'"
                                         ).fetchone()[0] == len(sent)
    jev_pairs = [p for p in pairs if p["set"] == "jev"]
    assert jev_pairs and all(p["why"].startswith("Jev ") for p in jev_pairs)
    ways = {w for p in pairs for w in p["proposed_by"]}
    assert any(w.startswith("jev:") for w in ways) and not ways & {f"jev:{a}" for a in probe_audit.AXES}

    fill(tmp_path, lambda p: "yes" if p["set"] != "random" else "no")
    r = probe_audit.score(tmp_path)
    assert r["missing"] == [] and r["marked"] == len(pairs)
    assert all(c["hit_rate"] == (0.0 if s == "random" else 1.0) for s, c in r["by_set"].items()
               if c.get("yes", 0) + c["no"])
    assert all("also_proposed_by" in y for y in r["yes"])
    proposing = {w.removeprefix("jev:") for p in pairs for w in p["proposed_by"] if w.startswith("jev:")}
    assert set(r["by_question"]) == proposing
    for q, c in r["by_question"].items():
        theirs = [p for p in pairs if f"jev:{q}" in p["proposed_by"]]
        assert (c["pairs"], c["held"]) == (len(theirs), sum(p["set"] != "random" for p in theirs))


def every_pair(conn):
    ids = sorted(r[0] for r in probe_audit.edge_audit._clusters(conn) if r[2])
    return [{"ideas": [a, b]} for a, b in itertools.combinations(ids, 2)]


def test_jevs_pool_picks_up_where_a_stopped_run_left_off(lattice_with_clusters, tmp_path):  # noqa: F811
    pairs = every_pair(lattice_with_clusters)
    assert len(pairs) > 5
    stopped, sent = fake_jev(fail_after=5)
    with pytest.raises(llm.Unavailable):
        probe_audit.jev_pool(lattice_with_clusters, stopped, None, pairs, tmp_path)
    kept = json.loads((tmp_path / "jev-pool.json").read_text())
    answered, logged = lattice_with_clusters.execute(
        "SELECT count(*) FILTER (WHERE error IS NULL), count(*) FROM im.egress_log WHERE model = 'jev-1.14.0'"
    ).fetchone()
    assert 0 < len(kept) <= 5 and answered == len(kept)   # what was answered is kept and logged
    assert logged == len(sent)   # and so is every request that went, answered or not
    again, resent = fake_jev()
    got = probe_audit.jev_pool(lattice_with_clusters, again, None, pairs, tmp_path)
    assert len(got) == len(pairs) and len(resent) == len(pairs) - len(kept)


def test_a_pool_stopped_by_anything_sends_nothing_more_and_keeps_only_what_it_logged(
        lattice_with_clusters, tmp_path, monkeypatch):  # noqa: F811
    pairs = every_pair(lattice_with_clusters)
    real, calls = egress._log, []

    def log_then_interrupt(*args, **kw):
        calls.append(1)
        if len(calls) >= 3:
            raise KeyboardInterrupt   # Ctrl-C, or the database going away, mid-pool
        return real(*args, **kw)
    monkeypatch.setattr(egress, "_log", log_then_interrupt)
    monkeypatch.setattr(probe_audit, "JEV_WORKERS", 1)
    client, sent = fake_jev(takes=0.05)   # slower than logging, so the rest are still queued when it stops
    with pytest.raises(KeyboardInterrupt):
        probe_audit.jev_pool(lattice_with_clusters, client, None, pairs, tmp_path)
    kept = json.loads((tmp_path / "jev-pool.json").read_text())
    logged = lattice_with_clusters.execute("SELECT count(*) FROM im.egress_log WHERE model = 'jev-1.14.0'").fetchone()[0]
    assert len(sent) <= 4 < len(pairs)   # the queue was dropped: at most the one in flight went too
    assert len(kept) == logged == 2


class CutsOff(FakeProposer):
    """FakeProposer, its every answer cut off mid-JSON."""

    def create(self, **kw):
        r = super().create(**kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"pairs": [{"a"')], stop_reason="max_tokens",
                               model=kw["model"], _request_id="r", usage=r.usage)


@pytest.mark.parametrize("question, only", [("tension", "model"), ("analogy", "facets")])
def test_a_proposer_that_fails_writes_no_sheet_and_a_rerun_asks_again(lattice_with_clusters, tmp_path, question,  # noqa: F811
                                                                      only):
    embedder = HashEmbedder() if question == "analogy" else None
    with pytest.raises(SystemExit):
        probe_audit.write(lattice_with_clusters, CutsOff(), embedder, question, tmp_path, only=only)
    assert not (tmp_path / "sheet.md").exists()
    assert probe_audit.write(lattice_with_clusters, FakeProposer(), embedder, question, tmp_path, only=only)["pairs"]


def test_a_pool_answer_that_cant_be_read_stops_the_sheet_and_is_asked_again(lattice_with_clusters, tmp_path):  # noqa: F811
    pairs = every_pair(lattice_with_clusters)
    good, _ = fake_jev()
    count = itertools.count(1)

    def post(body):   # the second answer comes back without any
        n = next(count)   # atomic: the pool asks from several threads
        return {"model": "jev-1.14.0", "usage": {"input_tokens": 1000}} if n == 2 else good.post(body)
    with pytest.raises(SystemExit):
        probe_audit.jev_pool(lattice_with_clusters, jev.Jev(post=post), None, pairs, tmp_path)
    assert len(json.loads((tmp_path / "jev-pool.json").read_text())) == len(pairs) - 1
    again, resent = fake_jev()
    assert len(probe_audit.jev_pool(lattice_with_clusters, again, None, pairs, tmp_path)) == len(pairs)
    assert len(resent) == 1



def test_tied_scores_propose_the_same_pairs_whatever_order_the_answers_came_back_in():
    answers = {f"{a} {b}": {"same_move": 0.5, "same_form": 0.5 if a < "m" else 0.4}
               for a, b in [("a", "z"), ("q", "r"), ("c", "d"), ("x", "y")]}
    backwards = dict(reversed(answers.items()))
    assert probe_audit._jev_proposals(answers) == probe_audit._jev_proposals(backwards)
    swapped = {pair: dict(reversed(qs.items())) for pair, qs in answers.items()}   # Jev's answers in another order
    assert probe_audit._jev_proposals(answers) == probe_audit._jev_proposals(swapped)


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


def test_a_sheet_from_one_set_leaves_out_pairs_already_asked(lattice_with_clusters, tmp_path):  # noqa: F811
    first, second = tmp_path / "one", tmp_path / "two"
    probe_audit.write(lattice_with_clusters, FakeProposer(), None, "tension", first)
    out = probe_audit.write(lattice_with_clusters, FakeProposer(), None, "tension", second, only="model", size=5,
                            exclude_folders=[first])
    asked = probe_audit.earlier([first])
    pairs = json.loads((second / "key.json").read_text())["pairs"].values()
    assert out["by_set"] == {"model": len(pairs)} and all(p["set"] == "model" for p in pairs)
    assert not {tuple(p["ideas"]) for p in pairs} & asked


def test_a_quotes_only_draft_never_shows_the_summaries(lattice_with_clusters, tmp_path):  # noqa: F811
    probe_audit.write(lattice_with_clusters, FakeProposer(), None, "tension", tmp_path)
    model = FakeProposer()
    d = probe_audit.draft(lattice_with_clusters, model, tmp_path, "claude-sonnet-5-5", quotes_only=True)
    assert d["draft"].endswith("-low-quotes.json") and model.calls
    assert not any("Claude's summary" in c["messages"][0]["content"] for c in model.calls)
    assert "then a summary" not in model.calls[0]["system"]
    assert any("> \"" in c["messages"][0]["content"] for c in model.calls)   # quotes still go, where there are any


def test_new_and_known_both_hold_and_only_new_counts_as_novel(lattice_with_clusters, tmp_path):  # noqa: F811
    probe_audit.write(lattice_with_clusters, FakeProposer(), None, "tension", tmp_path)
    key = json.loads((tmp_path / "key.json").read_text())["pairs"]
    order = {n: i for i, n in enumerate(sorted(key, key=int))}
    marks = {n: ("new", "known", "no")[order[n] % 3] for n in key}
    fill(tmp_path, lambda p: marks[next(n for n, q in key.items() if q["ideas"] == p["ideas"])])
    r = probe_audit.score(tmp_path)
    total = {k: sum(c.get(k, 0) for c in r["by_set"].values()) for k in ("new", "known", "no")}
    assert total == {k: sum(m == k for m in marks.values()) for k in ("new", "known", "no")}
    assert {y["mark"] for y in r["yes"]} <= {"new", "known"} and len(r["yes"]) == total["new"] + total["known"]
    assert "Judge whether it's worth seeing" in (tmp_path / "sheet.md").read_text()


def test_a_filter_that_drops_what_was_new_to_me_says_so():
    mine = {"1": "new", "2": "known", "3": "no", "4": "new"}
    theirs = {"1": {"answer": "yes"}, "2": {"answer": "yes"}, "3": {"answer": "no"}, "4": {"answer": "no"}}
    r = probe_audit.agreement(mine, theirs)
    assert (r["precision"], r["recall"], r["recall_new"], r["new_dropped"]) == (1.0, round(2 / 3, 3), 0.5, [4])


def test_a_draft_with_probabilities_is_read_at_each_cut_over_every_pair():
    mine = {"1": "yes", "2": "no", "3": "new", "4": "no"}
    theirs = {"1": {"answer": "unsure", "p": 0.55}, "2": {"answer": "unsure", "p": 0.62},
              "3": {"answer": "yes", "p": 0.9}, "4": {"answer": "no", "p": 0.1}}
    r = probe_audit.at_cuts(mine, theirs)
    assert r["pairs"] == 4
    assert r["cuts"]["0.5"] == {"flagged": 3, "precision": round(2 / 3, 3), "recall": 1.0, "recall_new": 1.0}
    assert r["cuts"]["0.7"] == {"flagged": 1, "precision": 1.0, "recall": 0.5, "recall_new": 1.0}


def test_each_question_is_scored_by_how_well_it_ranks_mine_above_the_rest():
    mine = {"1": "yes", "2": "no", "3": "known", "4": "no"}
    theirs = {"1": {"p": 0.9, "a": 0.2}, "2": {"p": 0.4, "a": 0.8}, "3": {"p": 0.7, "a": 0.5}, "4": {"p": 0.7, "a": 0.1}}
    r = probe_audit.auc(mine, theirs)
    assert r["p"] == round((1 + 1 + 1 + 0.5) / 4, 3) and r["a"] == 0.5 and list(r) == ["p", "a"]
