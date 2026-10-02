"""The triage stage, the logreg baseline and `im eval`, with fake models.
Laya itself is exercised only when IM_TEST_MODELS=1 (see test_laya_backend)."""

import hashlib
import os

import numpy as np
import pytest

from im import backends, evaluate, label, pipeline, projects, triage
from im.triage import Prediction, normalize

from helpers import episodes_with_text, run, table
from test_pipeline import BACKUP, SECRET

sklearn = pytest.importorskip("sklearn")


class HashEmbedder:
    """Bag of words hashed into 64 dimensions: deterministic, and similar texts land close."""
    model, model_version = "hash-bow", "hash-bow/64"

    def embed(self, texts):
        rows = []
        for t in texts:
            v = np.zeros(64)
            for w in t.lower().split():
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
            rows.append(v / (np.linalg.norm(v) or 1))
        return np.vstack(rows)


class KeywordBackend:
    """Answers from keywords, so tests can predict what it says."""
    name, model, model_version = "laya", "fake/keywords", "kw-1"

    def __init__(self):
        self.calls = 0

    def predict(self, states, qs):
        self.calls += len(states)
        out = []
        for st in states:
            text = st.transcript.lower()
            kind = "idea" if "idea" in text else "decision" if "decided" in text else "chatter"
            out.append(Prediction({
                "is_self_thinking": normalize("noul", 0.8 if "me:" in text else 0.1, None),
                "kind": normalize("choice", kind, {k: (0.7 if k == kind else 0.075) for k in triage.KINDS}),
                "project": normalize("choice", "none", {"none": 0.9}),
                "keep_score": normalize("score", 2.0, {"3": 0.6, "2": 0.4}),
            }, state_tokens=len(text.split())))
        return out


def triage_count(pipe, backend="laya"):
    return table(pipe, "SELECT count(*) FROM im.triage WHERE backend = %s", backend)[0][0]


def test_triage_answers_every_current_episode_once(pipe, cfg, stream):
    run(pipe, cfg, stream)
    b = KeywordBackend()
    assert triage.run_stage(pipe, b)["triaged"] == 9
    assert triage.run_stage(pipe, b)["triaged"] == 0 and b.calls == 9
    row = table(pipe, "SELECT answers, model, schema_version, stage_version, input_hash FROM im.triage LIMIT 1")[0]
    assert set(row[0]) == {"is_self_thinking", "kind", "project", "keep_score"} and row[1] == "fake/keywords"


def test_only_new_episodes_are_triaged_after_a_correction(pipe, cfg, stream):
    run(pipe, cfg, stream)
    b = KeywordBackend()
    triage.run_stage(pipe, b)
    stream.name_speaker("c-conv", "anon A", "Carol")
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert triage.run_stage(pipe, b)["triaged"] == 1
    assert triage_count(pipe) == 10  # the retired episode keeps its row


def test_a_registry_change_is_a_prompt_change(pipe, cfg, stream):
    run(pipe, cfg, stream)
    b = KeywordBackend()
    triage.run_stage(pipe, b)
    projects.add(pipe, "garden", "Garden sensors", [])
    assert triage.run_stage(pipe, b)["triaged"] == 9


def test_limit_bounds_a_run(pipe, cfg, stream):
    run(pipe, cfg, stream)
    b = KeywordBackend()
    assert triage.run_stage(pipe, b, limit=4)["triaged"] == 4
    assert triage.run_stage(pipe, b)["triaged"] == 5


def test_resets_and_forgetting(pipe, cfg, stream):
    run(pipe, cfg, stream)
    triage.run_stage(pipe, KeywordBackend())
    (eid,) = episodes_with_text(pipe, BACKUP)
    label.save(pipe, eid, {"boundaries": "ok", "is_self_thinking": True, "kind": "idea", "project": None,
                           "keep_score": 4}, None)
    stream.forget(14406, 14412)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert not table(pipe, """SELECT 1 FROM im.triage t JOIN im.episode_segments es USING (episode_id)
                              JOIN im.source_tombstones x USING (segment_id)""")
    assert pipeline.reset(pipe, "triage")["triage_deleted"] == 8
    assert table(pipe, "SELECT count(*) FROM im.episodes WHERE current")[0][0] == 9
    triage.run_stage(pipe, KeywordBackend())
    assert pipeline.reset(pipe, "segment")["triage_deleted"] == 9
    assert triage_count(pipe) == 0 and table(pipe, "SELECT count(*) FROM im.labels") == [(1,)]


def test_binary_questions_match_sklearn():
    from sklearn.linear_model import LogisticRegression

    rng = np.random.default_rng(1)
    X = rng.normal(size=(40, 6))
    labels = [{"is_self_thinking": bool(x[0] > 0), "kind": ["idea", "chatter", "noise"][i % 3],
               "project": None, "keep_score": 1 + i % 5} for i, x in enumerate(X)]
    params = backends.fit(X, labels)
    ours = [p["true"] for p in backends.predict_proba(params, X)["is_self_thinking"]]
    ref = LogisticRegression(max_iter=2000).fit(X, [str(bool(x[0] > 0)).lower() for x in X]).predict_proba(X)[:, 1]
    assert np.allclose(ours, ref, atol=1e-6)
    assert params["project"]["coef"] is None  # one class seen: constant
    assert backends.predict_proba(params, X[:2])["project"] == [{"none": 1.0}, {"none": 1.0}]


def test_cross_validation_learns_a_separable_question():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(60, 8))
    labels = [{"is_self_thinking": bool(x[0] > 0), "kind": "idea" if x[1] > 0 else "chatter",
               "project": None, "keep_score": 3} for x in X]
    m = evaluate.score(list(zip(evaluate.cross_validate(X, labels), labels)))
    assert m["is_self_thinking"]["accuracy"] > 0.85 and m["kind"]["accuracy"] > 0.85
    assert sum(b["n"] for b in m["kind"]["reliability"]) == 60


def _label_everything(pipe):
    for eid, kind in table(pipe, "SELECT episode_id, kind FROM im.episodes WHERE current"):
        label.save(pipe, eid, {"boundaries": "ok", "is_self_thinking": kind == "monologue",
                               "kind": "idea" if kind == "monologue" else "chatter",
                               "project": None, "keep_score": 4 if kind == "monologue" else 2}, None)


def test_train_then_triage_with_logreg(pipe, cfg, stream):
    run(pipe, cfg, stream)
    with pytest.raises(ValueError):
        backends.train(pipe, HashEmbedder())
    _label_everything(pipe)
    first = backends.train(pipe, HashEmbedder())
    assert backends.train(pipe, HashEmbedder())["model_version"] == first["model_version"]
    fb = backends.LogregBackend(pipe, HashEmbedder())
    assert triage.run_stage(pipe, fb)["triaged"] == 9
    for (answers,) in table(pipe, "SELECT answers FROM im.triage WHERE backend = 'logreg'"):
        assert answers["kind"]["value"] in {"idea", "chatter"}
        assert abs(sum(answers["kind"]["probabilities"].values()) - 1) < 1e-3
        assert 1 <= answers["keep_score"]["value"] <= 5
    assert table(pipe, "SELECT count(*) FROM im.episode_embeddings") == [(9,)]


def test_eval_reports_every_backend(pipe, cfg, stream):
    run(pipe, cfg, stream)
    _label_everything(pipe)
    triage.run_stage(pipe, KeywordBackend())
    ev = evaluate.evaluate(pipe, HashEmbedder(), k=3)
    assert ev["labels"] == 9 and len(ev["results"]) == 2
    kw = next(v for k, v in ev["results"].items() if k.startswith("laya"))
    # Wrong on three: two monologues never say "idea", and "Decided then." reads as a decision.
    assert kw["kind"]["accuracy"] == pytest.approx(6 / 9)
    text = evaluate.report(ev)
    assert "logreg" in text and "confidence 0.7-0.8" in text


@pytest.mark.models
@pytest.mark.skipif(os.environ.get("IM_TEST_MODELS") != "1", reason="set IM_TEST_MODELS=1 to load Laya")
def test_laya_backend(pipe, cfg, stream):
    run(pipe, cfg, stream)
    b = backends.LayaBackend("multilingual", max_len=2048, threads=4)
    stats = triage.run_stage(pipe, b, limit=2)
    assert stats["triaged"] == 2
    for (answers, tokens) in table(pipe, "SELECT answers, state_tokens FROM im.triage"):
        assert answers["kind"]["value"] in triage.KINDS and tokens > 0
        assert set(answers["keep_score"]["probabilities"]) <= {"1", "2", "3", "4", "5"}
        assert 1 <= answers["keep_score"]["value"] <= 5


def test_temperature_scaling_softens_an_overconfident_backend():
    from im.backends import label_value

    rng = np.random.default_rng(3)
    pairs = []
    for i in range(60):
        truth = ["idea", "chatter"][i % 2]
        said = truth if rng.random() < 0.6 else ["idea", "chatter"][(i + 1) % 2]  # right 60% of the time...
        probs = {said: 0.95, ["idea", "chatter"][said == "idea"]: 0.05}           # ...but always 95% sure
        a = {q: normalize("choice", said, probs) for q in ("kind",)}
        a |= {"is_self_thinking": normalize("noul", 0.5, None), "project": normalize("choice", "none", {"none": 1.0}),
              "keep_score": normalize("score", 1.0, {"2": 1.0})}
        pairs.append((a, {"kind": truth, "is_self_thinking": False, "project": None, "keep_score": 2}))
    calibrated, temps = evaluate.calibrate_cv(pairs)
    assert temps["kind"] > 2
    before = np.mean([a["kind"]["confidence"] for a, _ in pairs])
    after = np.mean([a["kind"]["confidence"] for a, _ in calibrated])
    assert before == pytest.approx(0.95) and 0.5 < after < 0.75
    assert [a["kind"]["value"] for a, _ in calibrated] == [a["kind"]["value"] for a, _ in pairs]


def test_each_laya_checkpoint_is_its_own_model_version():
    pytest.importorskip("laya")
    versions = {backends.LayaBackend(c).model_version for c in ("english", "multilingual", "typed-decisions")}
    assert len(versions) == 3
    assert backends.LayaBackend("english", max_len=2048).model_version not in versions


def test_llm_answer_distributions_read_the_value_tokens():
    from im.backends import answer_distributions

    def tok(t, alts):
        return {"token": t, "logprob": 0.0, "top_logprobs": [{"token": a, "logprob": float(np.log(p))} for a, p in alts]}

    qs = {"kind": {"type": "choice", "criteria": {"idea": "", "chatter": "", "noise": ""}},
          "is_self_thinking": {"type": "noul", "criteria": {}},
          "keep_score": {"type": "score", "criteria": ["a", "b", "c", "d", "e"]}}
    content = '{"kind": "idea", "is_self_thinking": true, "keep_score": 4}'
    stream = [tok("<|channel|>", []), tok("analysis", []), tok("<|message|>", []), tok("hmm", []),
              tok("{", []), tok(' "', []), tok("kind", []), tok('":', []), tok(' "', [(' "', 1.0)]),
              tok("idea", [("idea", 0.6), ("chat", 0.3), ("xyz", 0.1)]), tok('",', []),
              tok(' "', []), tok("is_self_thinking", []), tok('":', []),
              tok(" true", [(" true", 0.8), (" false", 0.2)]), tok(",", []),
              tok(' "', []), tok("keep_score", []), tok('":', []), tok(" ", [(" ", 1.0)]),
              tok("4", [("4", 0.5), ("3", 0.25), ("5", 0.25)]), tok("}", [])]
    d = answer_distributions(stream, content, qs)
    assert d["kind"] == pytest.approx({"idea": 2 / 3, "chatter": 1 / 3, "noise": 0.0})
    assert d["is_self_thinking"] == pytest.approx({"true": 0.8, "false": 0.2})
    assert d["keep_score"] == pytest.approx({"1": 0, "2": 0, "3": 0.25, "4": 0.5, "5": 0.25})


def test_a_tap_reaches_the_context_and_retriages_its_episode(pipe, cfg, stream):
    run(pipe, cfg, stream)
    b = KeywordBackend()
    triage.run_stage(pipe, b)
    (eid,) = episodes_with_text(pipe, SECRET)
    stream.conversations["c-forget"].taps = [fixtures_iso(14408)]
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    (st,) = triage.render(pipe, [eid])
    assert "I tapped the pendant at" in st.context
    stats = triage.run_stage(pipe, b)
    assert stats["triaged"] == 1  # only the tapped episode's input changed
    assert triage_count(pipe) == 9


def fixtures_iso(seconds):
    from im.fixtures import iso
    return iso(seconds)
