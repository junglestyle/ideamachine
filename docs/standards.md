# Coding standards

Idea Machine follows the [org standards](org-standards.md) and the org's standards for
[Python](org-standards-python.md), [SQL](org-standards-sql.md), [containers](org-standards-containers.md) and
[shell scripts](org-standards-shell.md), unchanged copies of
[junglestyle's](https://github.com/junglestyle/.github/tree/main/code-standards). `.org-standards` lists the topics it
copies beyond the general one. This file adds Idea Machine's own rules and examples. Where it differs from an org rule,
it says so, and it wins.

## Invariants

What the pipeline keeps true, whatever the stage:

- **Raw is immutable.** Hearsay's stream is only read, never written. Idea Machine's own copy of it (`im.source_*`)
  is append-only: a correction is a superseding row, never an update. Forgetting is the one exception, and it
  deletes for real ([ROADMAP §3.4](ROADMAP.md#34-forgetting)).
- **Derived is re-derivable.** Every derived row carries `schema_version`, `stage_version` (code or heuristic),
  `input_hash`, and `model`, `model_version` and `prompt_version` where they apply. Every stage supports
  `im reset --stage X`.
- **Human labels aren't derived.** They live in their own table, anchored to segment IDs, not episode IDs, so they
  survive re-derivation. When a segment is superseded, its labels follow the supersession links to the current
  segments, so a correction never orphans a label.
- **Traceability.** Every episode, label, span, link and synthesized object stores the `segment_id`s it came from.
- **Cheap models route, never drop.** Triage writes labels and a route. No stage deletes or hides segments: "noise"
  is a label, not a filter.
- **Eval before trust.** An automated tier drives nothing until it beats its exit threshold on the hand-labeled set.
- **Egress is a logged, versioned policy.** What goes to Claude is decided by `im.egress.POLICY_VERSION` (policy B:
  everyone's words, speakers pseudonymized; [decision 0004](decisions/0004-claude-extraction.md)), and every request
  is logged with the segment IDs it carried and what it cost. Every paid request goes one way, `im.egress.send`: it
  checks the monthly cap, sends, and logs before reading the answer, so a reply that can't be read is still logged
  and counted, and fails as one item. A model with no known price isn't sent to.

## Dependencies

- Lean on Postgres before writing your own: `im run`'s import, purge and segmentation share one transaction,
  embeddings live in pgvector, and roles draw the boundaries (`im_pipeline` owns `im.*`; `lattice_app` reads `pub` and
  only appends feedback).
- Outside work on extraction is read for ideas, not copied: the AI Co-Ethnographer (`aicoe`) is AGPL-3, and the
  knowledge-ingestion-pipeline has no license (all rights reserved). psy-qm-skills (MIT) and the hypotheses/claims
  benchmark (Apache-2.0) can be reused with their notices, but nothing here needs them. Write our own.

## Structure

- One way in for each kind of feedback: `im ideas --review` and Lattice's verdict events land in the same
  `im.item_verdicts`, and `im themes` inserts the same `pub.feedback_events` Lattice does.
- Idea Machine reads Hearsay only through its file stream, and Lattice reads Idea Machine only through the views in
  schema `pub`.

## Testing

- The behaviors worth a test: a correction re-derives only the episodes it touches, a second run changes nothing,
  forgetting leaves no text or derived row (and reports what was already sent), labels survive corrections and
  resets, the stream is only read, unknown formats and speakers fail closed, `lattice_app` can only read `pub` and
  append feedback, and the monthly cap stops extraction, scoring and the digest.
- Tests run on synthetic streams from `im.fixtures` and never read the real one. Each gets a fresh `im_test`
  database; the code refuses a dev admin URL that isn't localhost or a local socket, in any DSN form. Each runs
  without the developer's `IM_*`, `ANTHROPIC_*`, `LATTICE_*`, `CORRAL_*`, OpenRouter and Typesafe variables
  (`tests/conftest.py`), so a shell that exports the deployment's settings can't send a test to Claude or overwrite
  Hearsay's vocabulary.

## Running it

- `make check` is the whole suite or nothing: it fails unless the database env vars from `.env.example` are set and
  the `models` extra is installed (`uv sync --extra models`), as in CI. Plain `uv run pytest` is for quick runs:
  without the env vars the database tests skip, and without the extra the logreg tests do. Setup and configuration
  live in [install.md](install.md), deployment in [deploy.md](deploy.md) and `install/nas.sh`.

## Docs

- Overrides the org rule's ADRs in `docs/stack.md`: each decision is its own file in [decisions/](decisions/), with what
  was measured.

## Known deviations

Where Idea Machine doesn't follow the org standards yet. These are debt to pay down, not overrides: each one should go.

- Dependencies: Postgres 16 is one of [ROADMAP §2](ROADMAP.md#2-principles-applied)'s stack assumptions, "not
  questioned", and no decision says what it gives. [Decision 0006](decisions/0006-graph-in-postgres.md) only weighs a
  graph store beside it.
- Versions and pins: the deployment runs whatever the NAS's clones have checked out. [deploy.md](deploy.md) updates
  with `git pull` in both, and `install/nas.sh` builds Idea Machine and Lattice from them; no commit is pinned.
- Versions and pins: `install/compose.yaml` runs `pgvector/pgvector:pg16` and `cloudflare/cloudflared:2026.9.3` by
  tag (lines 15 and 81), not by digest.
- Versions and pins: `pyproject.toml` has no `[tool.uv] required-version`, so CI's `setup-uv`
  (`.github/workflows/test.yml`) installs the newest uv.
- Versions and pins: the annotated tags `v1` and `v1.1` aren't `vX.Y.Z` or semver, so they aren't releases, though
  the README, [SPEC.md](SPEC.md) and [ROADMAP-v2.md](ROADMAP-v2.md) present them as V1's versions.
- Versions and pins: `pyproject.toml` says `0.0.0`, and at the commits `v1` and `v1.1` tag it said `0.1.0`, so
  neither tag matches the manifest's version.
- Versions and pins: CI doesn't fail a `vX.Y.Z` tag that doesn't match `pyproject.toml`'s version. No job checks it.
- Privacy: 14 files, among them `install/compose.yaml`, `dev/pull-stream.sh` and `src/im/backends.py`, name the
  workstation's hostname, and [deploy.md](deploy.md) (line 12) names the NAS's tailnet host, `truenas`.
- Privacy: the workstation's layout is committed: `%h/data/code/ideamachine` in
  `dev/systemd/ideamachine-run.service` (lines 10-11) and `~/data/code/lattice` in [ROADMAP.md](ROADMAP.md) (line 149).
- Privacy: the NAS layout (`/mnt/fast/ideamachine/...`) is committed, and nothing here overrides the org rule for it, as
  hearsay's and lattice's standards do.
- Privacy: `IM_VOCAB_PATH` isn't in `.env.example`. `install/compose.yaml` sets it, and `im.vocab` reads it.
- Privacy: the owner's time zone, `America/New_York`, is the default for reading a ChatGPT export's times
  (`im seed-observations --tz`, and `tz` in `archive_observations.parse` and `plan`).
- Secrets: `install/nas.sh` prints Lattice's login token (`LATTICE_TOKEN`) at the end of every run (line 325).
- Docs: V2's roadmap is `docs/ROADMAP-v2.md`, not `docs/ROADMAP.md`, because code comments cite V1's
  [ROADMAP.md](ROADMAP.md) by section. V1's spec stays in its §1–3; V2's is [SPEC.md](SPEC.md).
- Docs: [SPEC.md](SPEC.md) opens with its scope, not the problem, its requirements have no IDs, and it has no
  Unknowns or Open questions section.
- Docs: [ROADMAP.md](ROADMAP.md) and [ROADMAP-v2.md](ROADMAP-v2.md) are arranged by phases and milestones, not
  numbered slices with `Status:`, `Delivers:` and `Done when:`, and ROADMAP.md tracks progress with checkboxes and
  dates.
- Docs: [ROADMAP-v2.md](ROADMAP-v2.md)'s milestones restate what they build instead of naming the
  [SPEC.md](SPEC.md) requirements they deliver, and M1's and M4's goals are internals (stable segment identity, Jev
  behind `judged_by`), not what I'd notice.
- Deployment repos: [deploy.md](deploy.md), [ROADMAP.md](ROADMAP.md) and `install/` carry the deployment's values:
  `/mnt/fast/...` paths, the `fast/ideamachine`, `fast/repos` and `storage/fast-replica` datasets, Lattice's
  `tailscale serve` port, 8444 (`install/lib.sh` line 25), and the NAS's time zone (`install/compose.yaml` line 47).
  `install/lib.sh` and `install/nas.sh` hard-code them. These facts belong in a future `ideamachine-deployment` repo.
- Prose: [SPEC.md](SPEC.md), [ROADMAP-v2.md](ROADMAP-v2.md), [ROADMAP.md](ROADMAP.md) and the design docs carry dates
  and status trails (`Status: plan, 2026-10-27`, answers struck through with the day they were settled), and
  [deploy.md](deploy.md) and [install.md](install.md) keep history ("Moving from `storage` to `fast` (2026-10-15)",
  "eeyore, before the move").
- Containers: `python:3.13-slim` doesn't name the Debian release.
- Containers: the `Dockerfile` installs uv with `pip` (line 6), not `COPY --from=ghcr.io/astral-sh/uv:<version>`.
- Containers: `compose.yaml` (the dev database) has no top-level `name:`.
- Containers: `compose.yaml`'s `db` has no `restart: unless-stopped`; `dev/systemd/ideamachine-db.service` restarts it.
- Python tooling: `make check` runs pytest only. Ruff and mypy aren't configured or run.
- Python layout: `src/im/__init__.py` isn't empty: it holds a docstring.
- Python layout: `tests/helpers.py` is named `helpers`.
- Python typing: 165 of the package's 280 functions lack an annotation on a parameter or the return, e.g. every `conn`.
- Python data modeling: `im.fixtures.Conversation` is a mutable `@dataclass`.
- Python data modeling: no closed set is a `StrEnum` tested against its column's `CHECK`. Among those Python compares,
  branches on or parses from outside: item kinds (`extract.KINDS`), digest point kinds (`digest.KINDS`), match
  decisions (`lattice.py` lines 35 and 258), routes (`router.py` lines 30 and 52), verdicts (`feedback.ITEM_KINDS`,
  `cli.py` line 369), speaker kinds (`importer.py` line 86) and sensitivity categories (`sensitivity.CATEGORIES`).
- Python testing: `test_the_real_archive_parses` (`tests/test_lattice.py` line 114) reads the real archive from
  `~/.local/share/ideamachine/seeds`, not a committed synthetic fixture.
- Python testing: 14 of the 21 test files are named for a module (`test_extract.py`, `test_lattice.py`,
  `test_triage.py`...), not a behavior.
- Python dependencies: `laya==0.3.23` is pinned exactly without a comment naming the break.
- Shell: `dev/init/10-roles.sh`, `dev/pull-stream.sh` and `dev/systemd/install.sh` start with `#!/bin/bash`.
- Shell: `install/lib.sh` doesn't start with `# shellcheck shell=bash`.
- Shell: `dev/init/10-roles.sh` sets up the database but fails on a re-run (`CREATE ROLE`, line 5), and
  `install/move-to-nas.sh`'s header doesn't say whether it's safe to re-run.
- Shell: `make check` doesn't run shellcheck, which reports 19 findings.
- SQL: `pub.review_items` picks an item's idea with `LIMIT 1` and no `ORDER BY` (`010_review.sql`,
  `011_corrections.sql`, and its current definition in `029_said_by_segment.sql`), and an item can belong to several
  ideas.
- SQL: `pipeline.run` sets `REPEATABLE READ` on the connection, so the later stages of `im run` inherit it.
- SQL: some table names aren't plural nouns: `im.ingest_state` (001), `im.triage` (004), `im.egress_log` (007),
  `im.feedback_applied` (009), `im.idea_sensitivity` and `im.theme_sensitivity` (027).
- SQL: these closed sets have no `CHECK`: `im.episodes.kind` (001, line 30), `im.source_segments.speaker_kind` and
  `speaker_basis` (002, lines 24 and 27), `im.source_supersessions.method` (002, line 52; its comment omits `rekey`,
  which `importer.py` line 212 writes), `im.triage.backend` (004, line 8; its comment says `laya | fallback`, but the
  backends are `laya`, `llm` and `logreg`) and `im.routes.triage_backend` (006, line 7), `im.items.kind` (007, line
  31), `im.idea_themes.origin` (008, line 78), `pub.feedback_events.source` (008, line 97), and the `categories` of
  `im.idea_sensitivity` and `im.theme_sensitivity` (027, lines 9 and 17).
