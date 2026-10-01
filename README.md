# Idea Machine

Turns Hearsay's speaker-attributed transcript segments into episodes and, later, triage and ideas.
See [docs/ROADMAP.md](docs/ROADMAP.md).

Built so far: Phase 1 steps 1–2 (import Hearsay's utterance stream, heuristic episode segmentation).

## Dev setup (eeyore)

```sh
cp .env.example .env              # pick passwords; .env is gitignored
podman compose up -d              # Postgres 16 + pgvector on 127.0.0.1:55432 (docker compose works too)
set -a; . ./.env; set +a
uv sync
uv run im migrate                 # im schema
uv run im load-fixtures           # a synthetic Hearsay stream in $IM_STREAM_DIR (dev/stream)
uv run im run                     # import + segment; idempotent
uv run im load-fixtures --scenario edited   # the same stream after every kind of correction and a forget
uv run im run
uv run im check                   # invariant queries
uv run im show <episode-prefix>
uv run im reset --stage segment   # drop episodes; the next run rebuilds them
uv run pytest                     # each test gets a fresh im_test database and stream directory
```

Settings are env vars:

- `IM_DATABASE_URL`: the pipeline's database. Role `im_pipeline` owns `im.*` and nothing else.
- `IM_STREAM_DIR`: Hearsay's utterance stream. Idea Machine only reads it. `im load-fixtures` writes only to
  directories it created itself.
- `IM_DEV_ADMIN_URL`: the local dev superuser, used only by tests to create and drop `im_test`. Code refuses it
  unless it points at localhost.

`IM_CONFIG` can name a TOML file that overrides the segmentation defaults in `src/im/config.py`. Every value there
goes into the episodes' `stage_version`, so changing one re-derives episodes on the next run.

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
