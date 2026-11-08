# Installing and running Idea Machine

How to set up a dev environment, run the pipeline and its tests, and how `im run` works step by step.
Commands run from the repository root. Production runs on the NAS: see [deploy.md](deploy.md). Design and
plans are in [ROADMAP.md](ROADMAP.md).

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
uv run im trial v3 --model openai/gpt-6.1-sol   # B3: a model on the trial sample, names pseudonymized, vs Opus
uv run im trial v3 --model claude-opus-5-5 --replicate 2   # Opus against itself: the ceiling for recall
uv run im audit-misses sheet saved/misses-terra v3 --model openai/gpt-6.1-terra   # its misses among Opus's solid captures, blind beside ones it found; score my marks
uv run im hard-set saved/hard-1 --per-case 6   # a deliberate hard set of episodes (irony, fragments, relayed thoughts, short thoughts, ones I only acknowledge, ones answering someone), each as extraction would send it under policy C; sends nothing
uv run im audit-edges sheet saved/b4-4 --size 75 --seed 7 --exclude saved/b4-1 saved/b4-2 saved/b4-3   # B4: a fresh sheet with no pair I've marked before
uv run im audit-edges draft saved/b4-3 --model jev-1.14.0   # B4: Jev answers a sheet; score shows connection at every cut
uv run im audit-edges review saved/b4-1 saved/b4-2   # B4: pairs where a chat model's draft disagrees with my mark, with its case; a pair I left unmarked takes the models' mark where they agree (--all lists those too, to spot-check); answer in review.md, run again to apply
uv run im bench-match run saved/b1-1 --limit 20   # B1: Opus's logged match decisions, replayed through Jev; `score` compares
uv run im names warm               # policy C: find names in the trial sample once, before trials in parallel
uv run im audit-tension draft saved/tension-2 --model jev-1.14.0   # Jev drafts a probe sheet (via OpenRouter); --probes asks more
                                  # (OpenRouter models need OPENROUTER_API_KEY; policy C needs IM_NAME_KEY)
uv run im seed ~/.local/share/ideamachine/seeds/chatgpt-archive.md   # seed the idea lattice (once)
uv run im lattice                 # lattice counts: ideas, evidence, connections
uv run im themes                  # themes; im themes pin|unpin|reject <name>, im themes rename <name> <new name>
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
make check                        # the whole suite; fails without the IM_* env vars or the models extra
uv run pytest                     # a quick run: without them the database or logreg tests skip instead
IM_TEST_MODELS=1 uv run pytest    # also load the real Laya model
```

Settings are env vars:

- `IM_DATABASE_URL`: the pipeline's database. Role `im_pipeline` owns `im.*` and nothing else.
- `IM_STREAM_DIR`: Hearsay's utterance stream. Idea Machine only reads it. `im load-fixtures` writes only to
  directories it created itself.
- `ANTHROPIC_API_KEY` (or an `ant auth login` profile): for idea extraction. Without it `im run` skips extraction
  and says so. What's sent, and how, is in [decisions/0004-claude-extraction.md](decisions/0004-claude-extraction.md).
- `LATTICE_DATABASE_URL` and `LATTICE_TOKEN`: for `im web`, the neutral view (every cluster past the gates, newest
  first, no score; [decision 0007](decisions/0007-neutral-proposals.md)). It reads `pub` and appends feedback as
  Lattice's role, logs in with Lattice's token, and listens on 127.0.0.1:8792.
- `IM_DEV_ADMIN_URL`: the local dev superuser, used only by tests to create and drop `im_test`. Code refuses it
  unless every host it (or `PGHOST`) names is localhost, a loopback address or a socket directory. Each test gets a
  fresh `im_test` database and stream directory, and none of the other settings here.

`IM_CONFIG` can name a TOML file that overrides the defaults in `src/im/config.py`. An unknown key in a section
stops the run. Every segmentation value goes into the episodes' `stage_version`, so changing one re-derives episodes
on the next run. Triage rows record the model, its weights revision and a hash of the questions, so a model or
question change re-triages.

Every stage that asks Claude takes its model and effort from the file:

| Section | Stage | `model` | `effort` |
|---|---|---|---|
| `[extract]` | extraction (also `prompt`, `interests`, `monthly_cap_usd`, `max_episodes_per_run`) | `claude-opus-5-5` | `medium` |
| `[match]` | the lattice's matcher | `claude-opus-5-5` | `low` |
| `[themes]` | describing, filing and proposing themes | `claude-opus-5-5` | `low` |
| `[scores]` | the rubric | `claude-opus-5-5` | `low` |
| `[sensitivity]` | the tags demo mode hides by | `claude-opus-5-5` | `low` |
| `[vocab]` | vocabulary repairs and archive picks | `claude-opus-5-5` | `low` |
| `[digest]` | the weekly digest | `claude-opus-5-5` | `low` |

Both go into the stage's version. Changing extraction's re-reads every episode, so try it first with `im trial`.
Changing the scores', sensitivity's or vocab's asks again about every idea, and the themes' about every idea that
fits no theme. The matcher and the digest use a new one from then on; what they've already done stays.

Set `CUDA_VISIBLE_DEVICES=""` on a box with a GPU to measure what the NAS's CPU will do.

## Real data on eeyore

Until Idea Machine runs on the NAS, `dev/pull-stream.sh` copies Hearsay's stream from the NAS (it only reads
there) into `~/.local/share/ideamachine/stream`. Point `IM_STREAM_DIR` there and `IM_DATABASE_URL` at a
database of its own (`im`), separate from the fixture one (`im_dev`). Then `dev/pull-stream.sh && uv run im run`.

## On the NAS

Production runs on TrueNAS: see [deploy.md](deploy.md). eeyore is for development.

## Running on its own (eeyore, before the move)

`dev/systemd/install.sh` links and enables two user units:

- `ideamachine-db.service` keeps the Postgres container running. Its port forward on 127.0.0.1:55432 then lives in
  that service and not in whichever shell started the container, and it comes back after a reboot (lingering is
  on for this user).
- `ideamachine-run.timer` runs `dev/pull-stream.sh && im run` hourly at :45, after Hearsay's transcription (:00)
  and reprocess (:30). Logs: `journalctl --user -u ideamachine-run`.

Then the only manual step is `im ideas --review`.

## How `im run` works

One REPEATABLE READ transaction:

1. **Import** ([ROADMAP §3.3](ROADMAP.md)). Re-read conversations whose `revision` changed in `index.json`, checking each file
   against its revision (a mismatch waits for the next run). Store new utterance versions in `im.source_segments`,
   keyed by content (conversation, start, end and text; any other field is an annotation), so unchanged speech
   keeps its ID when Hearsay renumbers or adds a field. Link replaced versions: by
   `utterance_id` when the transcript is the same, by time overlap otherwise. Then purge everything that overlaps
   a `forgotten.json` span.
2. **Purge.** Delete everything made from a forgotten segment: every episode (current or retired) that contains
   one; every label, idea, digest, verdict and observation made from one; Lattice's events on captures of it, even
   once the capture is gone; and the classifiers trained on those labels.
3. **Segment.** Re-segment conversations whose membership changed, or whose episodes are stale or missing.
   Episodes are content-addressed (`uuid5(stage_version, input_hash)`), so unchanged episodes aren't touched,
   ones that no longer come out are retired (`current = false`), and new ones are inserted.

Then, outside that transaction:

- **Triage.** Each configured backend (none by default; see [decisions/0004](decisions/0004-claude-extraction.md)) answers the triage questions for
  current episodes whose rendered input it hasn't answered yet, newest first, committing in small batches. If
  Ollama is down or the GPU is busy, the run notes it and goes on.
- **Quotes** (`src/im/quotes.py`). Each new capture's quote is lined up with the lines it cites and the line
  either side, and the capture then cites every line its words are on: a model's citation can stop a line short or
  point a line off. Citations only grow, and the capture's observation, its idea, my verdicts on it, Lattice's
  events on it and the matcher's request that sent it grow with it, so forgetting any line it quotes reaches all of
  them. A line label copied into the quote ("[23] me: ") is dropped. `im.items.quote_in_lines` keeps the share of
  the quote's words found; under 0.6 the quote isn't from its lines. The first run after migration 044 does this
  for every existing capture.
- **Lattice.** Each new capture is matched against the nearest existing ideas (local embeddings), and Claude
  decides new / same as / evolves. Related-idea edges are rebuilt. Lattice, the separate presentation app, reads
  the result through schema `pub` ([ROADMAP §3.5](ROADMAP.md)).
- **Digest.** Claude writes the last completed week's digest (Monday to Monday) once every episode of that week
  has been read and its captures matched; until then the run leaves it, with `"waiting": true` in its stats, and a
  later run writes it.
- **Observations and weight** ([M2 design](design/m2-observations-candidates-ideas.md)). Each new capture joins
  the observation it re-captures, or starts one. Each cluster is weighed (`[weight]` in `im.toml`), and up to 10
  candidates a week, the most recently said first, are proposed to me to keep as canonical or pinned. `im tune knob=value ...` previews a change to
  those settings, writing nothing, and prints the `[weight]` block to paste.
- **Route.** The router sends to review whatever I tapped or called a note to self, and any episode where Claude
  captured an item above the confidence bar; the rest is auto-filed (`src/im/router.py`; v1 used the local LLM's flags instead, see
  [decisions/0003-router-v1.md](decisions/0003-router-v1.md)).

Every stage that asks a model (extraction, matching, themes, scores, sensitivity, vocab, the digest) stops, saying
why in its stats, when the month's spend reaches `extract.monthly_cap_usd`, when the API can't answer now, or when
no request would get through (the credentials or the account's credit rejected, a model id that doesn't exist); the
stages after it still run. A request whose reply fails (an error response, no answer, one cut off or not in the
schema) is logged in `im.egress_log` with what it cost, counted in the stage's
`failed`, and asked again on the next run; the stage goes on to its next item. A refused filing, scoring, tagging or
vocab-repair batch is skipped the same way and asked again, rather than recorded or stopping the batches after it; a
refused extraction is recorded as read, with nothing captured, and a refused match makes a new idea.
