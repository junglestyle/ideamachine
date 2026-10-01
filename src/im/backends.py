"""Triage backends: Laya, and the fallback classifier it has to beat.

Heavy imports (torch, laya, sentence-transformers, scikit-learn) happen
inside the classes, so the rest of `im` runs without the `models` extra.
"""

import hashlib
import json
from typing import Protocol

import numpy as np

from im.triage import EpisodeState, Prediction, normalize

# --- Laya -------------------------------------------------------------------


class LayaBackend:
    """Laya, run locally on CPU. Weights are pinned to the revision the laya package pins."""

    name = "laya"

    def __init__(self, checkpoint: str = "english", max_len: int = 8192, threads: int | None = None):
        import laya

        repo, self.subfolder = laya.DEFAULT_MODELS[checkpoint]
        self.repo = repo
        self.revision = laya.PINNED_REVISIONS[repo]
        self.max_len, self.threads = max_len, threads
        self.model = f"laya/{checkpoint}"
        self.model_version = f"laya-{laya.__version__}@{self.revision[:12]};max_len={max_len}"
        self._agent = None

    def _load(self):
        if self._agent is None:
            import laya
            import torch

            if self.threads:
                torch.set_num_threads(self.threads)
            self._agent = laya.load(self.repo, subfolder=self.subfolder, device="cpu", revision=self.revision)
        return self._agent

    def predict(self, states: list[EpisodeState], qs: dict) -> list[Prediction]:
        agent = self._load()
        out = []
        for st in states:
            r = agent.predict(st.as_state(), qs, max_len=self.max_len)
            answers = {}
            for q, spec in qs.items():
                raw = r["answers"][q]
                probs = raw.get("probabilities")
                if spec["type"] == "score" and probs:
                    # Laya keys score levels by their text or 0-based index; store them as 1-5.
                    levels = spec["criteria"]
                    probs = {str(levels.index(k) + 1 if k in levels else int(k) + 1): v for k, v in probs.items()}
                answers[q] = normalize(spec["type"], raw[spec["type"]], probs)
            usage = r.get("usage", {})
            out.append(Prediction(answers, usage.get("state_tokens"), bool(usage.get("truncated"))))
        return out


# --- Embeddings -------------------------------------------------------------


class Embedder(Protocol):
    model: str
    model_version: str

    def embed(self, texts: list[str]) -> np.ndarray: ...  # unit-length rows


class SentenceEmbedder:
    """A small local sentence-embedding model. Long transcripts are embedded in word
    windows and mean-pooled, since the model reads 512 tokens at most."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5", window_words: int = 250):
        self.model, self.window_words = model, window_words
        self.model_version = f"{model};window={window_words};mean"
        self._st = None

    def embed(self, texts: list[str]) -> np.ndarray:
        if self._st is None:
            from sentence_transformers import SentenceTransformer

            self._st = SentenceTransformer(self.model, device="cpu")
        rows = []
        for text in texts:
            words = text.split()
            chunks = [" ".join(words[i:i + self.window_words]) for i in range(0, max(len(words), 1), self.window_words)]
            v = self._st.encode(chunks, normalize_embeddings=True).mean(axis=0)
            rows.append(v / np.linalg.norm(v))
        return np.vstack(rows)


def _vec(v) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def embeddings(conn, embedder: Embedder, states: list[EpisodeState]) -> np.ndarray:
    """Embeddings for these episodes, computing and storing any that are missing."""
    ids = [s.episode_id for s in states]
    have = {r[0]: np.array(json.loads(r[1])) for r in conn.execute(
        """SELECT episode_id, embedding::text FROM im.episode_embeddings
           WHERE episode_id = ANY(%s) AND model = %s AND model_version = %s""",
        (ids, embedder.model, embedder.model_version))}
    missing = [s for s in states if s.episode_id not in have]
    if missing:
        vecs = embedder.embed([s.transcript for s in missing])
        with conn.transaction():
            for s, v in zip(missing, vecs, strict=True):
                conn.execute(
                    """INSERT INTO im.episode_embeddings (episode_id, model, model_version, embedding,
                         schema_version, stage_version, input_hash)
                       VALUES (%s, %s, %s, %s::vector, 1, 'embed/1', %s) ON CONFLICT DO NOTHING""",
                    (s.episode_id, embedder.model, embedder.model_version, _vec(v), s.input_hash))
                have[s.episode_id] = v
    return np.vstack([have[i] for i in ids])


# --- Fallback classifier ----------------------------------------------------

QUESTIONS = ("is_self_thinking", "kind", "project", "keep_score")
QTYPES = {"is_self_thinking": "noul", "kind": "choice", "project": "choice", "keep_score": "score"}


def label_value(question: str, answers: dict) -> str:
    """A label's answer as a class name."""
    v = answers[question]
    if question == "project":
        return v or "none"
    if question == "is_self_thinking":
        return "true" if v else "false"
    return str(v)


def fit(X: np.ndarray, labels: list[dict], C: float = 1.0) -> dict:
    """One logistic regression per question. Returns plain coefficients."""
    from sklearn.linear_model import LogisticRegression

    params = {}
    for q in QUESTIONS:
        y = [label_value(q, a) for a in labels]
        classes = sorted(set(y))
        if len(classes) == 1:
            params[q] = {"classes": classes, "coef": None, "intercept": None}
            continue
        clf = LogisticRegression(C=C, max_iter=2000).fit(X, y)
        coef, intercept = clf.coef_, clf.intercept_
        if len(clf.classes_) == 2:  # sklearn keeps one row for binary; make it a two-class softmax
            coef, intercept = np.vstack([-coef[0] / 2, coef[0] / 2]), np.array([-intercept[0] / 2, intercept[0] / 2])
        params[q] = {"classes": [str(c) for c in clf.classes_], "coef": coef.round(8).tolist(),
                     "intercept": intercept.round(8).tolist()}
    return params


def predict_proba(params: dict, X: np.ndarray) -> dict[str, list[dict]]:
    """question -> one {class: probability} per row of X."""
    out = {}
    for q, p in params.items():
        if p["coef"] is None:
            out[q] = [{p["classes"][0]: 1.0} for _ in range(len(X))]
            continue
        z = X @ np.array(p["coef"]).T + np.array(p["intercept"])
        z = np.exp(z - z.max(axis=1, keepdims=True))
        probs = z / z.sum(axis=1, keepdims=True)
        out[q] = [dict(zip(p["classes"], row.tolist(), strict=True)) for row in probs]
    return out


def to_answers(probs: dict[str, dict]) -> dict:
    answers = {}
    for q, pr in probs.items():
        if QTYPES[q] == "noul":
            answers[q] = normalize("noul", pr.get("true", 0.0), None)
        elif QTYPES[q] == "score":
            expected = sum(int(k) * v for k, v in pr.items())
            answers[q] = normalize("score", expected - 1, pr)
        else:
            answers[q] = normalize("choice", max(pr, key=pr.get), pr)
    return answers


def exact_labels(conn) -> list[tuple[int, object, dict]]:
    """(label_id, current episode_id, answers) for every label that resolves exactly."""
    from im.label import resolve_all

    res = resolve_all(conn)
    rows = conn.execute("SELECT label_id, answers FROM im.labels ORDER BY label_id").fetchall()
    return [(lid, next(iter(res[lid][1])), answers) for lid, answers in rows if res[lid][0] == "exact"]


def train(conn, embedder: Embedder, C: float = 1.0) -> dict:
    from im.triage import render

    data = exact_labels(conn)
    if len(data) < 5:
        raise ValueError(f"need at least 5 exact labels to train, have {len(data)}")
    X = embeddings(conn, embedder, render(conn, [eid for _, eid, _ in data]))
    params = fit(X, [a for _, _, a in data], C)
    version = hashlib.sha256(json.dumps(
        [embedder.model_version, C, [(lid, a) for lid, _, a in data]], sort_keys=True).encode()).hexdigest()[:16]
    conn.execute(
        """INSERT INTO im.classifiers (model_version, embedding_model, label_ids, params)
           VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
        (version, embedder.model_version, [lid for lid, _, _ in data], json.dumps(params | {"C": C})))
    return {"model_version": version, "labels": len(data),
            "classes": {q: params[q]["classes"] for q in QUESTIONS}}


class FallbackBackend:
    """The latest trained classifier, over episode embeddings."""

    name = "fallback"

    def __init__(self, conn, embedder: Embedder):
        row = conn.execute(
            """SELECT model_version, params FROM im.classifiers WHERE embedding_model = %s
               ORDER BY trained_at DESC LIMIT 1""", (embedder.model_version,)).fetchone()
        if row is None:
            raise LookupError("no fallback classifier trained yet (im train)")
        self.conn, self.embedder = conn, embedder
        self.model_version, params = row
        self.params = {q: params[q] for q in QUESTIONS}
        self.model = f"logreg/{embedder.model}"

    def predict(self, states: list[EpisodeState], qs: dict) -> list[Prediction]:
        X = embeddings(self.conn, self.embedder, states)
        probs = predict_proba(self.params, X)
        return [Prediction(to_answers({q: probs[q][i] for q in QUESTIONS})) for i in range(len(states))]
