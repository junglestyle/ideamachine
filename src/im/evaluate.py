"""`im eval`: per-question accuracy and a reliability table for each backend, on my labels.

The fallback is scored by k-fold cross-validation, so every prediction is on
a label it wasn't trained on. Laya isn't trained on labels, so its stored
triage rows for the labeled episodes are scored directly.
"""

import numpy as np

from im.backends import QTYPES, QUESTIONS, embeddings, exact_labels, fit, label_value, predict_proba, to_answers
from im.triage import prompt_version, questions, render

BUCKETS = [0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0001]


def _hit(question: str, answer: dict, label: dict) -> bool:
    truth = label_value(question, label)
    if QTYPES[question] == "noul":
        return ("true" if answer["value"] >= 0.5 else "false") == truth
    if QTYPES[question] == "score":
        return str(int(round(answer["value"]))) == truth
    return str(answer["value"]) == truth


def _confidence(question: str, answer: dict) -> float:
    if QTYPES[question] == "score":
        return max(answer["probabilities"].values()) if answer["probabilities"] else 0.0
    return answer["confidence"] or 0.0


def score(pairs: list[tuple[dict, dict]]) -> dict:
    """pairs of (predicted answers, label answers) -> per-question metrics."""
    out = {}
    for q in QUESTIONS:
        rows = [(_hit(q, a[q], lab), _confidence(q, a[q])) for a, lab in pairs]
        table = []
        for lo, hi in zip(BUCKETS, BUCKETS[1:]):
            b = [hit for hit, c in rows if lo <= c < hi]
            if b:
                table.append({"confidence": f"{lo:.1f}-{min(hi, 1):.1f}", "n": len(b), "accuracy": sum(b) / len(b)})
        m = {"n": len(rows), "accuracy": sum(h for h, _ in rows) / len(rows) if rows else None, "reliability": table}
        if q == "keep_score" and pairs:
            m["mae"] = float(np.mean([abs(a[q]["value"] - lab[q]) for a, lab in pairs]))
        out[q] = m
    return out


def cross_validate(X: np.ndarray, labels: list[dict], k: int = 5, seed: int = 0, C: float = 1.0) -> list[dict]:
    order = np.random.default_rng(seed).permutation(len(labels))
    folds = np.array_split(order, min(k, len(labels)))
    preds: list = [None] * len(labels)
    for held in folds:
        train = np.setdiff1d(order, held)
        params = fit(X[train], [labels[i] for i in train], C)
        probs = predict_proba(params, X[held])
        for j, i in enumerate(held):
            preds[i] = to_answers({q: probs[q][j] for q in QUESTIONS})
    return preds


def evaluate(conn, embedder=None, k: int = 5) -> dict:
    data = exact_labels(conn)
    if not data:
        raise ValueError("no exact labels yet (im label)")
    labels = [a for _, _, a in data]
    results = {}
    if embedder is not None and len(data) >= 2:
        X = embeddings(conn, embedder, render(conn, [eid for _, eid, _ in data]))
        results[f"fallback (logreg/{embedder.model}, {min(k, len(data))}-fold CV)"] = score(
            list(zip(cross_validate(X, labels, k), labels, strict=True)))
    pv = prompt_version(questions(conn))
    stored: dict = {}
    for backend, model, mv, eid, answers in conn.execute(
            """SELECT backend, model, model_version, episode_id, answers FROM im.triage
               WHERE backend <> 'fallback' AND prompt_version = %s AND episode_id = ANY(%s)""",
            (pv, [eid for _, eid, _ in data])):
        stored.setdefault(f"{backend} ({model}, {mv})", {})[eid] = answers
    for name, by_episode in stored.items():
        pairs = [(by_episode[eid], a) for _, eid, a in data if eid in by_episode]
        results[f"{name}, {len(pairs)}/{len(data)} labeled episodes triaged"] = score(pairs)
    return {"labels": len(data), "results": results}


def report(ev: dict) -> str:
    lines = [f"{ev['labels']} exact labels"]
    for name, metrics in ev["results"].items():
        lines += ["", name]
        for q, m in metrics.items():
            acc = "n/a" if m["accuracy"] is None else f"{m['accuracy']:.0%}"
            extra = f", MAE {m['mae']:.2f}" if "mae" in m else ""
            lines.append(f"  {q:<17} accuracy {acc} (n={m['n']}){extra}")
            for b in m["reliability"]:
                lines.append(f"      confidence {b['confidence']}: n={b['n']:<3} accuracy {b['accuracy']:.0%}")
    if not ev["results"]:
        lines.append("nothing to evaluate: train the fallback (im train) or triage the labeled episodes with Laya")
    return "\n".join(lines)
