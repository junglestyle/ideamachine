# Idea Machine

Turns Hearsay's speaker-attributed transcript segments into episodes and, later, triage and ideas.
See [docs/ROADMAP.md](docs/ROADMAP.md).

Built so far: Phase 1 step 1 (ingest and heuristic episode segmentation).

## Dev setup (eeyore)

```sh
cp .env.example .env              # pick passwords; .env is gitignored
podman compose up -d              # Postgres 16 + pgvector on 127.0.0.1:55432 (docker compose works too)
set -a; . ./.env; set +a
uv sync
uv run im migrate                 # im schema (pipeline role)
uv run im load-fixtures           # synthetic data into the local hearsay stand-in
uv run im run                     # ingest + segment; idempotent
uv run im check                   # invariant queries
uv run im show <episode-prefix>
uv run im reset --stage segment   # drop episodes; the next run rebuilds them
uv run pytest                     # each test gets a fresh im_test database
```

Two connection settings, both env vars:

- `IM_DATABASE_URL`: the pipeline. Role `im_pipeline` has `SELECT` only on `hearsay.*` and owns `im.*`, in dev too.
- `IM_DEV_ADMIN_URL`: the local dev superuser, used only by `im load-fixtures` and tests. Code refuses it unless it
  points at localhost, so fixtures and tests can't reach the NAS.

`IM_CONFIG` can name a TOML file that overrides the segmentation defaults in `src/im/config.py`. Every value there
goes into the episodes' `stage_version`, so changing one re-derives episodes on the next run.

## How `im run` works

One REPEATABLE READ transaction:

1. **Ingest.** Find sessions with current segments past the `seq` cursor, or created in the trailing window
   (default 10 min, for rows that commit out of order).
2. **Purge.** Delete every episode, current or retired, that contains a tombstoned segment.
3. **Stale.** Find sessions whose current episodes hold a segment that's no longer current, or that were built
   with a different `stage_version`.
4. **Segment.** Re-segment those sessions from `hearsay.current_segments`. Episodes are content-addressed
   (`uuid5(stage_version, input_hash)`), so unchanged episodes aren't touched, ones that no longer come out are
   retired (`current = false`), and new ones are inserted.

For now the source is a stand-in `hearsay` schema. Phase 1 step 2 replaces it with an importer for Hearsay's
utterance stream (ROADMAP §3).
