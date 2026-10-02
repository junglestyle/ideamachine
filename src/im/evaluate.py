"""`im eval`: per-question accuracy and a reliability table for each backend, on my labels.

The fallback is scored by k-fold cross-validation, so every prediction is on
a label it wasn't trained on. Laya isn't trained on labels, so its stored
triage rows for the labeled episodes are scored directly.
"""

from collections import Counter

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


TEMPERATURES = np.exp(np.linspace(np.log(0.2), np.log(20), 121))


def temper(probs: dict, t: float) -> dict:
    """Temperature-scale a distribution: p ** (1/t), renormalized. t > 1 softens, t < 1 sharpens."""
    keys = list(probs)
    z = np.log(np.clip([probs[k] for k in keys], 1e-9, 1)) / t
    z = np.exp(z - z.max())
    return dict(zip(keys, (z / z.sum()).tolist(), strict=True))


def _distribution(q: str, answer: dict) -> dict:
    return answer["probabilities"] or {}


def fit_temperature(q: str, pairs: list[tuple[dict, dict]]) -> float:
    """The temperature minimizing log-loss of the labeled answers (grid search; 1.0 if there's nothing to fit)."""
    usable = [(_distribution(q, a[q]), label_value(q, lab)) for a, lab in pairs if _distribution(q, a[q])]
    if len(usable) < 5:
        return 1.0
    def nll(t):
        return -np.mean([np.log(max(temper(p, t).get(truth, 0.0), 1e-9)) for p, truth in usable])
    return float(min(TEMPERATURES, key=nll))


def apply_temperature(q: str, answer: dict, t: float) -> dict:
    probs = temper(_distribution(q, answer), t) if _distribution(q, answer) else {}
    if not probs:
        return answer
    if QTYPES[q] == "noul":
        p = probs["true"]
        return {"value": p, "probabilities": probs, "confidence": max(p, 1 - p)}
    if QTYPES[q] == "score":
        return {"value": sum(int(k) * v for k, v in probs.items()), "probabilities": probs,
                "confidence": max(probs.values())}
    return {"value": answer["value"], "probabilities": probs, "confidence": probs.get(str(answer["value"]))}


def calibrate_cv(pairs: list[tuple[dict, dict]], k: int = 5, seed: int = 0) -> tuple[list, dict]:
    """Held-out temperature scaling: each answer is scaled by a temperature fitted on the other folds.
    Returns (calibrated pairs, temperatures fitted on all pairs)."""
    order = np.random.default_rng(seed).permutation(len(pairs))
    out: list = [None] * len(pairs)
    for held in np.array_split(order, min(k, len(pairs))):
        train = [pairs[i] for i in np.setdiff1d(order, held)]
        temps = {q: fit_temperature(q, train) for q in QUESTIONS}
        for i in held:
            a, lab = pairs[i]
            out[i] = ({q: apply_temperature(q, a[q], temps[q]) for q in QUESTIONS}, lab)
    return out, {q: round(fit_temperature(q, pairs), 3) for q in QUESTIONS}


def majority(labels: list[dict]) -> dict:
    """Accuracy of always answering the most common label: the floor any backend has to clear."""
    out = {}
    for q in QUESTIONS:
        counts = Counter(label_value(q, a) for a in labels)
        answer, n = counts.most_common(1)[0]
        out[q] = {"answer": answer, "accuracy": n / len(labels)}
    return out


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
    temperatures = {}
    for name, by_episode in stored.items():
        pairs = [(by_episode[eid], a) for _, eid, a in data if eid in by_episode]
        results[f"{name}, {len(pairs)}/{len(data)} labeled episodes triaged"] = score(pairs)
        if len(pairs) >= 10:
            calibrated, temperatures[name] = calibrate_cv(pairs, k)
            results[f"{name}, temperature-scaled ({min(k, len(pairs))}-fold CV)"] = score(calibrated)
    return {"labels": len(data), "majority": majority(labels), "results": results, "temperatures": temperatures}


def report(ev: dict) -> str:
    lines = [f"{ev['labels']} exact labels", "", "baseline: always the most common answer"]
    for q, m in ev["majority"].items():
        lines.append(f"  {q:<17} accuracy {m['accuracy']:.0%} (always {m['answer']!r})")
    for name, metrics in ev["results"].items():
        lines += ["", name]
        for q, m in metrics.items():
            acc = "n/a" if m["accuracy"] is None else f"{m['accuracy']:.0%}"
            extra = f", MAE {m['mae']:.2f}" if "mae" in m else ""
            lift = "" if m["accuracy"] is None else f", {100 * (m['accuracy'] - ev['majority'][q]['accuracy']):+.0f} pts vs baseline"
            lines.append(f"  {q:<17} accuracy {acc} (n={m['n']}){extra}{lift}")
            for b in m["reliability"]:
                lines.append(f"      confidence {b['confidence']}: n={b['n']:<3} accuracy {b['accuracy']:.0%}")
    for name, temps in ev.get("temperatures", {}).items():
        lines += ["", f"temperatures fitted on all labels, {name}: " + ", ".join(f"{q} {t}" for q, t in temps.items())]
    if not ev["results"]:
        lines.append("nothing to evaluate: train the fallback (im train) or triage the labeled episodes with Laya")
    return "\n".join(lines)
