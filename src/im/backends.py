"""Triage backends:

- `llm`: a local instruct model through Ollama (on eeyore's GPU). The default.
- `laya`: Laya on CPU. Failed the first eval (docs/decisions/0002); kept for comparison.
- `logreg`: logistic regression over sentence embeddings, trained on my labels: the baseline.

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
        # All checkpoints share one repo revision, so the checkpoint has to be part of the version.
        self.model_version = f"laya-{laya.__version__}@{self.revision[:12]}/{checkpoint};max_len={max_len}"
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


# --- Local LLM via Ollama ----------------------------------------------------

LLM_SYSTEM = """You triage transcripts of speech I recorded on an always-on pendant, so I can find what's worth keeping.
"me" is me. Other speakers are named, or labeled like "anon A", or "unknown".
The context line gives the local date, time of day, length and the speakers present.
Answer every question about the whole transcript, following each question's definitions exactly.

Questions:
{questions}"""


def _describe(qs: dict) -> str:
    out = []
    for q, spec in qs.items():
        out.append(f"- {q}: {spec['instructions']}")
        if spec["type"] == "noul":
            out += [f"    true: {spec['criteria']['true']}", f"    false: {spec['criteria']['false']}"]
        elif spec["type"] == "score":
            out += [f"    {i}: {level}" for i, level in enumerate(spec["criteria"], 1)]
        else:
            out += [f"    {k}: {v}" for k, v in spec["criteria"].items()]
    return "\n".join(out)


def _schema(qs: dict) -> dict:
    props = {}
    for q, spec in qs.items():
        if spec["type"] == "noul":
            props[q] = {"type": "boolean"}
        elif spec["type"] == "score":
            props[q] = {"type": "integer", "enum": list(range(1, len(spec["criteria"]) + 1))}
        else:
            props[q] = {"type": "string", "enum": list(spec["criteria"])}
    return {"type": "object", "properties": props, "required": list(qs)}


def _options(spec: dict) -> list[str]:
    if spec["type"] == "noul":
        return ["true", "false"]
    if spec["type"] == "score":
        return [str(i) for i in range(1, len(spec["criteria"]) + 1)]
    return list(spec["criteria"])


def answer_distributions(logprobs: list[dict], content: str, qs: dict) -> dict[str, dict]:
    """Per question, the model's probabilities over its options, read at the token where the answer's
    value starts. Options are matched by that first token, mass on tokens that match no option is
    dropped, and the rest is renormalized. When several options share a first token they can't be told
    apart there, so the chosen option takes that token's mass."""
    # Find where the JSON content starts in the token stream (after any reasoning tokens).
    texts = [t["token"] for t in logprobs]
    squeeze = lambda x: "".join(x.split())  # noqa: E731 -- compare without whitespace
    target = squeeze(content)[:20]
    start = next((i for i in range(len(texts)) if texts[i].lstrip().startswith("{")
                  and squeeze("".join(texts[i:])).startswith(target)), None)
    if start is None:
        return {}
    parsed = json.loads(content)
    out = {}
    built = ""
    for i in range(start, len(texts)):
        built += texts[i]
        for q, spec in qs.items():
            if q in out:
                continue
            key = f'"{q}":'
            tail = built.replace(" ", "")
            if tail.endswith(key) or tail.endswith(key + '"'):
                # The value starts at the first token after the key that isn't just spaces or a quote.
                nxt = next((t for t in logprobs[i + 1:] if t["token"].strip().strip('"').strip()), None)
                if nxt is None:
                    continue
                opts = _options(spec)
                chosen = str(parsed[q]).lower() if spec["type"] == "noul" else str(parsed[q])
                mass: dict = {}
                for alt in nxt.get("top_logprobs", []):
                    tok = alt["token"].strip().lstrip('"').strip()
                    if not tok:
                        continue
                    hits = [o for o in opts if o.startswith(tok) or tok.startswith(o)]
                    if not hits:
                        continue
                    who = chosen if chosen in hits else hits[0]
                    mass[who] = mass.get(who, 0.0) + float(np.exp(alt["logprob"]))
                total = sum(mass.values())
                out[q] = {o: mass.get(o, 0.0) / total for o in opts} if total > 0 else {chosen: 1.0}
    return out


class LLMBackend:
    """A local instruct model through Ollama, with schema-constrained JSON answers and probabilities
    from the tokens' log-probabilities. Nothing leaves the machine Ollama runs on."""

    name = "llm"

    def __init__(self, model: str = "gpt-oss:20b", url: str = "http://localhost:11434", think: str | None = "low",
                 num_ctx: int = 16384, timeout: float = 600):
        import urllib.request

        self.url, self.think, self.num_ctx, self.timeout = url.rstrip("/"), think, num_ctx, timeout
        self._urlopen = urllib.request.urlopen
        info = self._post("/api/show", {"model": model})
        digest = info.get("digest") or hashlib.sha256(json.dumps(info.get("details", {}), sort_keys=True).encode()).hexdigest()
        self.model = f"ollama/{model}"
        self.ollama_model = model
        self.model_version = f"{model}@{digest[:12]};think={think};ctx={num_ctx}"

    def _post(self, path: str, body: dict) -> dict:
        import urllib.request

        import urllib.error

        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with self._urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:  # Ollama says why in the body (e.g. the GPU is out of memory)
            raise OSError(f"ollama {path}: HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from None

    def predict(self, states: list[EpisodeState], qs: dict) -> list[Prediction]:
        system = LLM_SYSTEM.format(questions=_describe(qs))
        out = []
        for st in states:
            body = {"model": self.ollama_model, "stream": False, "logprobs": True, "top_logprobs": 20,
                    "format": _schema(qs), "options": {"temperature": 0, "seed": 0, "num_ctx": self.num_ctx},
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": f"Context: {st.context}\n\nTranscript:\n{st.transcript}"}]}
            if self.think is not None:
                body["think"] = self.think
            r = self._post("/api/chat", body)
            content = r["message"]["content"]
            parsed = json.loads(content)
            dists = answer_distributions(r.get("logprobs") or [], content, qs)
            answers = {}
            for q, spec in qs.items():
                chosen = str(parsed[q]).lower() if spec["type"] == "noul" else str(parsed[q])
                d = dists.get(q) or {chosen: 1.0}  # no log-probabilities: certain, which eval will expose
                if spec["type"] == "noul":
                    answers[q] = normalize("noul", d.get("true", 0.0), None)
                elif spec["type"] == "score":
                    expected = sum(int(k) * v for k, v in d.items())
                    answers[q] = normalize("score", expected - 1, d)
                else:
                    answers[q] = normalize("choice", parsed[q], d)
            prompt_tokens = r.get("prompt_eval_count")
            out.append(Prediction(answers, prompt_tokens, bool(prompt_tokens and prompt_tokens >= self.num_ctx)))
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
    current = {s.episode_id: s.input_hash for s in states}
    have = {r[0]: np.array(json.loads(r[1])) for r in conn.execute(
        """SELECT episode_id, embedding::text, input_hash FROM im.episode_embeddings
           WHERE episode_id = ANY(%s) AND model = %s AND model_version = %s""",
        (ids, embedder.model, embedder.model_version)) if r[2] == current[r[0]]}
    missing = [s for s in states if s.episode_id not in have]
    if missing:
        vecs = embedder.embed([f"{s.context}\n{s.transcript}" for s in missing])
        with conn.transaction():
            for s, v in zip(missing, vecs, strict=True):
                conn.execute(
                    """INSERT INTO im.episode_embeddings (episode_id, model, model_version, embedding,
                         schema_version, stage_version, input_hash)
                       VALUES (%s, %s, %s, %s::vector, 1, 'embed/1', %s)
                       ON CONFLICT (episode_id, model, model_version) DO UPDATE
                       SET embedding = excluded.embedding, input_hash = excluded.input_hash, created_at = now()""",
                    (s.episode_id, embedder.model, embedder.model_version, _vec(v), s.input_hash))
                have[s.episode_id] = v
    return np.vstack([have[i] for i in ids])


# --- Logistic-regression baseline -----------------------------------------

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
    """(label_id, current episode_id, answers): each labeled episode's latest exact label."""
    from im.label import current_labels

    return sorted((lid, eid, answers) for eid, (lid, answers) in current_labels(conn).items())


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


class LogregBackend:
    """The latest trained classifier over episode embeddings: the baseline every backend must beat."""

    name = "logreg"

    def __init__(self, conn, embedder: Embedder):
        row = conn.execute(
            """SELECT model_version, params FROM im.classifiers WHERE embedding_model = %s
               ORDER BY trained_at DESC LIMIT 1""", (embedder.model_version,)).fetchone()
        if row is None:
            raise LookupError("no logreg classifier trained yet (im train)")
        self.conn, self.embedder = conn, embedder
        self.model_version, params = row
        self.params = {q: params[q] for q in QUESTIONS}
        self.model = f"logreg/{embedder.model}"

    def predict(self, states: list[EpisodeState], qs: dict) -> list[Prediction]:
        X = embeddings(self.conn, self.embedder, states)
        probs = predict_proba(self.params, X)
        return [Prediction(to_answers({q: probs[q][i] for q in QUESTIONS})) for i in range(len(states))]
