"""Router v1 and `im review`."""

import pytest

from im import evaluate, label, router, triage
from im.triage import EpisodeState, normalize

from helpers import episodes_with_text, run, table
from test_label import ANSWERS, script
from test_pipeline import BACKUP, SECRET
from test_triage import KeywordBackend, fixtures_iso

pytest.importorskip("numpy")


class LowKeep(KeywordBackend):
    """The keyword backend, but nothing is worth keeping: only its `kind` can flag an episode."""

    def predict(self, states, qs):
        preds = super().predict(states, qs)
        for p in preds:
            p.answers["keep_score"] = normalize("score", 0.0, {"1": 1.0})
        return preds


def answers(kind="chatter", keep=1.0):
    return {"kind": normalize("choice", kind, {kind: 1.0}), "keep_score": normalize("score", keep - 1, {})}


@pytest.mark.parametrize("state, ans, expect", [
    (EpisodeState(1, "me: nothing much"), answers(), ("auto_file", [])),
    (EpisodeState(1, "me: nothing much", taps=("t",)), answers(), ("review", ["tap"])),
    (EpisodeState(1, "me: Note to self, buy batteries"), None, ("review", ["note_to_self"])),
    (EpisodeState(1, "Alice: note to self, she said"), answers(), ("auto_file", [])),
    (EpisodeState(1, "me: hm"), answers("idea"), ("review", ["llm:idea"])),
    (EpisodeState(1, "me: hm"), answers("noise", 2.6), ("review", ["llm:keep>=3"])),
    (EpisodeState(1, "me: hm"), answers("noise", 2.4), ("auto_file", [])),
])
def test_rules(state, ans, expect):
    assert router.route(state, ans) == expect


def test_routes_are_written_once_and_taps_route_before_triage(pipe, cfg, stream):
    stream.conversations["c-forget"].taps = [fixtures_iso(14408)]
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    first = router.run_stage(pipe, backend="laya")  # the fake backend calls itself laya
    assert first["review"] == 1 and first["waiting_for_triage"] == 8
    triage.run_stage(pipe, LowKeep())
    stats = router.run_stage(pipe, backend="laya")
    assert stats["waiting_for_triage"] == 0 and stats["changed"] == 9
    assert router.run_stage(pipe, backend="laya")["changed"] == 0
    (tapped,) = episodes_with_text(pipe, SECRET)
    assert table(pipe, "SELECT route, reasons FROM im.routes WHERE episode_id = %s", tapped) == [("review", ["tap"])]
    # The keyword backend says idea for the garden monologue, so it's queued too; taps come first.
    queue = router.review_queue(pipe)
    assert queue[0][0] == tapped and len(queue) == stats["review"]


def test_im_review_labels_what_the_router_sent(pipe, cfg, stream):
    run(pipe, cfg, stream)
    triage.run_stage(pipe, LowKeep())
    router.run_stage(pipe, backend="laya")
    n = len(router.review_queue(pipe))
    assert n >= 1
    read, write, out = script("", "o", "y", "i", "0", "4", "", "y", "q")
    assert label.routed_session(pipe, read, write) == 1
    assert len(router.review_queue(pipe)) == n - 1
    assert table(pipe, "SELECT source FROM im.labels") == [("review",)]
    assert any(line.startswith("here because the LLM says") for line in out)


def test_router_report(pipe, cfg, stream):
    run(pipe, cfg, stream)
    triage.run_stage(pipe, LowKeep())
    router.run_stage(pipe, backend="laya")
    (idea_ep,) = episodes_with_text(pipe, "Idea: the garden sensors could report soil moisture over LoRa.")
    label.save(pipe, idea_ep, ANSWERS | {"kind": "idea", "keep_score": 4}, None, "review")
    (other,) = episodes_with_text(pipe, BACKUP)
    label.save(pipe, other, ANSWERS | {"kind": "task", "keep_score": 3}, None)  # a positive the router missed
    rr = evaluate.router_report(pipe)
    assert rr["by_reason"]["llm:idea"] == [1, 1]
    assert rr["sampled"] == {"positives": 1, "positives_routed": 0, "routed": 0, "routed_positive": 0, "n": 1}


def test_im_run_survives_an_unavailable_llm(pipe, cfg, stream, monkeypatch, capsys):
    import json

    from im import backends, cli

    run(pipe, cfg, stream)

    def down(*a, **k):
        raise OSError("ollama /api/chat: HTTP 500: cudaMalloc failed: out of memory")
    monkeypatch.setattr(backends.LLMBackend, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(backends.LLMBackend, "predict", down)
    for attr, value in {"name": "llm", "model": "ollama/x", "model_version": "x"}.items():
        monkeypatch.setattr(backends.LLMBackend, attr, value, raising=False)
    monkeypatch.setattr(cli.db, "pipeline_dsn", lambda: pipe.info.dsn + " password=" + pipe.info.password)
    monkeypatch.setattr(cli.db, "stream_dir", lambda: stream.dir)
    cli.main(["run"])
    out = json.loads(capsys.readouterr().out)
    assert out["triage"] == [] and "out of memory" in out["triage_notes"][0]
    assert out["route"]["waiting_for_triage"] == 9
