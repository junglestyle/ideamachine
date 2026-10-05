"""`im eval`: per-question accuracy and a reliability table for each backend, on my labels.

logreg is scored by k-fold cross-validation, so every prediction is on a
label it wasn't trained on. The LLM and Laya aren't trained on labels, so
their stored triage rows for the labeled episodes are scored directly.
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


def positive(answers: dict) -> bool:
    """What the router exists to find: anything but chatter or noise, or something worth keeping."""
    from im.router import FLAG_KEEP, FLAG_KINDS

    return answers["kind"] in FLAG_KINDS or answers["keep_score"] >= FLAG_KEEP


def router_report(conn) -> dict:
    """How the current router's routes compare with my labels.

    - By reason, the share of reviewed episodes I judged positive: the flag's precision.
    - For episodes labeled with `im label` (not picked by the router), how many of my positives the router
      sends to review (recall) and how much of what it sends is noise to me.
    """
    from im.label import current_labels
    from im.router import ROUTER_VERSION

    latest = current_labels(conn)
    source = {r[0]: r[1] for r in conn.execute("SELECT label_id, source FROM im.labels")}
    routes = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT episode_id, route, reasons FROM im.routes WHERE router_version = %s", (ROUTER_VERSION,))}
    by_reason: dict = {}
    sampled = {"positives": 0, "positives_routed": 0, "routed": 0, "routed_positive": 0, "n": 0}
    for eid, (lid, answers) in latest.items():
        if eid not in routes:
            continue
        r, reasons = routes[eid]
        pos = positive(answers)
        if r == "review":
            for reason in reasons:
                b = by_reason.setdefault(reason, [0, 0])
                b[0] += 1
                b[1] += pos
        if source[lid] == "label":
            sampled["n"] += 1
            sampled["positives"] += pos
            sampled["positives_routed"] += pos and r == "review"
            sampled["routed"] += r == "review"
            sampled["routed_positive"] += pos and r == "review"
    return {"router_version": ROUTER_VERSION, "by_reason": by_reason, "sampled": sampled}


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
        results[f"logreg baseline ({embedder.model}, {min(k, len(data))}-fold CV)"] = score(
            list(zip(cross_validate(X, labels, k), labels, strict=True)))
    pv = prompt_version(questions(conn))
    stored: dict = {}
    for backend, model, mv, eid, answers in conn.execute(
            """SELECT backend, model, model_version, episode_id, answers FROM im.triage
               WHERE backend <> 'logreg' AND prompt_version = %s AND episode_id = ANY(%s)""",
            (pv, [eid for _, eid, _ in data])):
        stored.setdefault(f"{backend} ({model}, {mv})", {})[eid] = answers
    temperatures = {}
    for name, by_episode in stored.items():
        pairs = [(by_episode[eid], a) for _, eid, a in data if eid in by_episode]
        results[f"{name}, {len(pairs)}/{len(data)} labeled episodes triaged"] = score(pairs)
        if len(pairs) >= 10:
            calibrated, temperatures[name] = calibrate_cv(pairs, k)
            results[f"{name}, temperature-scaled ({min(k, len(pairs))}-fold CV)"] = score(calibrated)
    return {"labels": len(data), "majority": majority(labels), "results": results, "temperatures": temperatures,
            "router": router_report(conn)}


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
    if "router" in ev:
        rr = ev["router"]
        lines += ["", f"router {rr['router_version']}"]
        for reason, (n, pos) in sorted(rr["by_reason"].items()):
            lines.append(f"  flagged for {reason:<14} {n:>3} labeled, {pos} of them positive to me ({pos / n:.0%})")
        s = rr["sampled"]
        if s["n"]:
            lines.append(f"  on {s['n']} episodes labeled with im label: {s['positives']} positive to me, "
                         f"{s['positives_routed']} of those sent to review; "
                         f"{s['routed']} sent to review, {s['routed_positive']} of those positive")
    if not ev["results"]:
        lines.append("nothing to evaluate: im eval --llm, or train the logreg baseline (im train)")
    return "\n".join(lines)
