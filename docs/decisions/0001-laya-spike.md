# 0001: Laya spike

_2026-10-01. Status: Laya passes the spike; checkpoint and router choice wait for real labels._

## Question

Does Laya install, run on the TrueNAS CPU at an acceptable per-episode latency, and accept our typed triage
questions? (ROADMAP Phase 1, timeboxed at 2 days.)

## What Laya is

A non-autoregressive "System 1" decision model from Convai Innovations
(`convaiinnovations/laya` on Hugging Face, Apache-2.0). It reads a state plus typed questions (`choice`, `score`,
`noul`) and returns typed answers with probabilities in one forward pass. It never generates text. The `laya`
package (PyPI, 0.3.23) pins each checkpoint's weights to a Hugging Face revision. Idea Machine loads that pinned
revision and records it in every triage row's `model_version`.

Three checkpoints:
- `english`: ModernBERT-large, 421M parameters.
- `multilingual`: mmBERT-base, 322M parameters.
- `typed-decisions`: fine-tuned for typed decisions.

We use the official `laya` package. The unaffiliated `laya-mnn` port (0.1.3, a week old) was rejected.

## Findings

**Installs:** yes, with CPU-only torch (`uv sync --extra models`; the venv is 1.2 GB, against 5.4 GB with CUDA
wheels). Each checkpoint loads in 8–13 s.

**Accepts our questions:** yes. All four triage questions go in one call. `usage` reports state tokens and
truncation.

**Latency** on eeyore (Ryzen 5 9600X, CPU only, 6 threads, four questions per call):

| Checkpoint | ~300 tokens | ~1,200 | ~2,400 | ~4,800 |
|---|---|---|---|---|
| english | 2.0 s | 7.9 s | 19.6 s | 51.5 s |
| multilingual | 0.7 s | 3.1 s | 8.4 s | 24.8 s |
| typed-decisions | 2.0 s | 7.9 s | 19.9 s | 51.3 s |

- The NAS (i7-8700) should be roughly 2× slower.
- Real episodes (from one test import of the real stream) average about 9 minutes. That's very roughly 2,000–2,500
  tokens, or about 40 s each on the NAS with `english` and about 17 s with `multilingual`.
- The first real import had 76 episodes for 4.4 hours of speech. At that volume a day's triage is roughly 30–50
  minutes of NAS CPU with `english`, which fits an hourly timer. Open conversations re-derive hourly, which adds
  to that.
- `max_episodes_per_run` bounds a run if that becomes a problem.

**Context:** the model card gives 512 tokens for `english`, but with `max_len=8192` nothing was truncated up to
~4,800 tokens. ModernBERT handles long inputs natively; whether answer quality holds at that length is
something `im eval` measures, not something this spike can.

**Calibration:** the model card says Laya ships over-confident and needs temperature refitting on domain data
(`laya.fit_temperatures`). Our first look agrees: on throwaway labels it was 0.8–0.9 confident on wrong `kind`
answers. Confidence thresholds for routing must not be set from raw Laya probabilities.

## Decision

- Laya passes the spike and is the primary triage backend, behind the `Triage` backend interface
  (`src/im/backends.py`). The checkpoint is config (`[triage] laya_checkpoint`), and `english` is the default.
- The fallback (bge-small-en-v1.5 embeddings plus one logistic regression per question, `im train`) is built
  and is the baseline Laya has to beat on `im eval`.
- Still open until there are about 50 real labels: which checkpoint, whether temperature fitting is enough,
  and the router thresholds (that decision goes in its own record).
