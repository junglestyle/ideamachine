# Idea Machine

Turns Hearsay's speaker-attributed transcript segments into episodes and, later, triage and ideas.
See [docs/ROADMAP.md](docs/ROADMAP.md).

Built so far: importing Hearsay's utterance stream, heuristic episode segmentation, the projects registry, labeling,
triage backends for evaluation (a local LLM, Laya, logreg), idea extraction with Claude, and routing.

## Dev setup (eeyore)

```sh
cp .env.example .env              # pick passwords; .env is gitignored
podman compose up -d              # Postgres 16 + pgvector on 127.0.0.1:55432 (docker compose works too)
set -a; . ./.env; set +a
uv sync --extra models          # CPU-only torch, Laya, sentence-transformers, scikit-learn
uv run im migrate                 # im schema
uv run im load-fixtures           # a synthetic Hearsay stream in $IM_STREAM_DIR (dev/stream)
uv run im run                     # import + segment + extract ideas with Claude + route; idempotent
uv run im ideas                   # what Claude captured; --review to keep or discard each item
uv run im ideas --discards        # what I discarded and why: material for revising the extraction prompt
uv run im load-fixtures --scenario edited   # the same stream after every kind of correction and a forget
uv run im run
uv run im check                   # invariant queries
uv run im show <episode-prefix>
uv run im reset --stage segment   # drop episodes; the next run rebuilds them (labels are kept)
uv run im project add garden -d "Garden sensors" -a lora
uv run im label                   # label episodes in the terminal; --status for progress
uv run im train                   # fit the logreg baseline on my labels
uv run im eval --llm              # accuracy and reliability per question, per backend, plus the router's precision
uv run im review                  # work through what the router sent me (pendant taps and notes to self first)
uv run im reset --stage triage    # drop triage rows; the next run re-triages
uv run pytest                     # each test gets a fresh im_test database and stream directory
IM_TEST_MODELS=1 uv run pytest    # also load the real Laya model
```

Settings are env vars:

- `IM_DATABASE_URL`: the pipeline's database. Role `im_pipeline` owns `im.*` and nothing else.
- `IM_STREAM_DIR`: Hearsay's utterance stream. Idea Machine only reads it. `im load-fixtures` writes only to
  directories it created itself.
- `ANTHROPIC_API_KEY` (or an `ant auth login` profile): for idea extraction. Without it `im run` skips extraction
  and says so. What's sent, and how, is in `docs/decisions/0004-claude-extraction.md`.
- `IM_DEV_ADMIN_URL`: the local dev superuser, used only by tests to create and drop `im_test`. Code refuses it
  unless it points at localhost.

`IM_CONFIG` can name a TOML file that overrides the defaults in `src/im/config.py` (`[segment]` and `[triage]`).
Every segmentation value goes into the episodes' `stage_version`, so changing one re-derives episodes on the
next run. Triage rows record the model, its weights revision and a hash of the questions, so a model or
question change re-triages.

Set `CUDA_VISIBLE_DEVICES=""` on a box with a GPU to measure what the NAS's CPU will do.

## Real data on eeyore

Until Idea Machine runs on the NAS, `dev/pull-stream.sh` copies Hearsay's stream from the NAS (it only reads
there) into `~/.local/share/ideamachine/stream`. Point `IM_STREAM_DIR` there and `IM_DATABASE_URL` at a
database of its own (`im`), separate from the fixture one (`im_dev`). Then `dev/pull-stream.sh && uv run im run`.

## How `im run` works

One REPEATABLE READ transaction:

1. **Import** (ROADMAP §3.3). Re-read conversations whose `revision` changed in `index.json`, checking each file
   against its revision (a mismatch waits for the next run). Store new utterance versions in `im.source_segments`,
   keyed by content, so unchanged speech keeps its ID when Hearsay renumbers. Link replaced versions: by
   `utterance_id` when the transcript is the same, by time overlap otherwise. Then purge everything that overlaps
   a `forgotten.json` span.
2. **Purge.** Delete every episode, current or retired, that contains a forgotten segment.
3. **Segment.** Re-segment conversations whose membership changed, or whose episodes are stale or missing.
   Episodes are content-addressed (`uuid5(stage_version, input_hash)`), so unchanged episodes aren't touched,
   ones that no longer come out are retired (`current = false`), and new ones are inserted.

Then, outside that transaction:

- **Triage.** Each configured backend (by default the local LLM through Ollama) answers the triage questions for
  current episodes whose rendered input it hasn't answered yet, newest first, committing in small batches. If
  Ollama is down or the GPU is busy, the run notes it and goes on.
- **Route.** Router v1 sends to review whatever I tapped or called a note to self, and whatever the LLM flags; the
  rest is auto-filed (`docs/decisions/0003-router-v1.md`).
