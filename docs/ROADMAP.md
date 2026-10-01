# Idea Machine Roadmap

_Status: draft, 2026-10-01. Idea Machine reads Hearsay's utterance stream (§3); four small asks of Hearsay are pending. Phase 1 step 1 (segmentation) is built against a stand-in; step 2 swaps the stand-in for the stream importer._

## 1. Scope

Idea Machine reads the speaker-attributed utterances that Hearsay publishes in its utterance stream (or any capture source producing the same format), and keeps its own append-only copy of every version it has seen. It turns them into something I can use: it groups segments into episodes, triages every episode cheaply on my homelab, relates episodes to each other over time, and sends only the episodes worth it to Claude. Claude turns those into structured ideas, tasks, decisions and project mentions, each traceable to source segments. Every derived row records which code, model and prompt produced it, so any layer can be dropped and rebuilt from raw.

### Non-goals

- **Audio of any kind.** No fetching, decoding or processing. Hearsay never hands audio to consumers. `im show` prints the conversation ID and times so I can find the audio in Hearsay myself.
- **Speaker attribution.** Diarization, voiceprints and identity belong to Hearsay. Idea Machine consumes the stream's `speaker` fields as given.
- **Agent automation.** The proposals table and approved agent work are out of early scope (see Phase 5).
- **Event infrastructure.** No Kafka, NATS or queues. One consumer polls one directory.
- **A memory framework as the core.** No Mem0, Cognee or similar. Plain tables plus pgvector.
- **A UI** before the CLI has proven what's worth looking at.
- **Multi-user or hosted deployment.** One user, one TrueNAS box.

## 2. Principles applied

| Principle | How it shows up |
|---|---|
| Raw is immutable | Idea Machine reads Hearsay's stream read-only and never writes to it. Its own copy of the stream (`im.source_*`) is append-only: corrections become superseding rows (§3), never updates. Forgetting is the one exception, and it deletes for real (§3). |
| Derived is re-derivable | Every derived row carries `schema_version`, `stage_version` (code/heuristic), and `model` + `model_version` + `prompt_version` where relevant, plus `input_hash`. Each stage supports `im reset --stage X`. |
| Cheap models route, never drop | Triage writes labels and a route. No stage deletes or hides segments. "Noise" is a label, not a filter. |
| Traceability | Every episode, label, span, link and synthesized object stores the `segment_id`s it came from. |
| Eval before trust | Each automated tier has to beat its exit threshold on the hand-labeled set before its output drives anything. |
| Human labels are not derived | Labels live in their own table, anchored to segment IDs rather than episode IDs, so they survive episode re-derivation. When a segment is superseded, its labels follow the supersession links to the current segments, so a correction never orphans a label. |

**Stack assumptions** (not questioned, change if wrong): Python; Idea Machine's own Postgres 16 + pgvector (Hearsay has no Postgres) with schema `im`; and a single container on TrueNAS, run by a timer, with Hearsay's stream directory mounted read-only. Everything is CPU-first. A GPU is a speedup, not a requirement.

## 3. Hearsay → Idea Machine data contract

The source is Hearsay's **utterance stream** (Hearsay slice 8, `hearsay/stream.py`), read-only. Hearsay rewrites it after every hourly reprocess into `/mnt/storage/hearsay/stream/`. Idea Machine needs no Hearsay database and no access to Hearsay's internals.

### 3.1 What Hearsay publishes today

- `index.json`: one entry per conversation, with `conversation_id`, `start`, `end`, `open`, `transcribed`, `taps`, `utterances`, `revision` and `file`.
- `conversations/<conversation_id>.jsonl`: one utterance per line, with `conversation_id`, `utterance_id` (`<conversation_id>:<idx>`), `start` and `end` (UTC), `speaker {kind: owner|person|anonymous|stranger|unknown, name, label}`, `text`, `text_confidence` and `speaker_confidence {basis, owner_similarity}`.
- Behaviour already in the code:
  - Files are written to a temp file and renamed, with the index written last.
  - A file is rewritten only when its content changes.
  - `revision` is the first 16 hex characters of the sha256 of the file's bytes.
  - Turns the operator filed as `_noise` or `_media` are left out by Hearsay. That's Hearsay's call about who is speaking, not a content filter, so it doesn't conflict with "noise is a label".
- The unit of change is the conversation. Naming a speaker rewrites the files of every conversation that speaker appears in, and a growing conversation is re-transcribed.

### 3.2 Asks of Hearsay (PENDING)

1. **Format version.** Add `format_version` to `index.json`. Idea Machine refuses to import a version it doesn't know.
2. **Transcript revision.** Today `utterance_id` is renumbered when a conversation is re-transcribed, so the same ID can name different speech. Add a per-conversation `transcript_revision` that changes when the transcript changes and stays the same when only speaker fields change. Within one `transcript_revision`, an `utterance_id` must always name the same stretch of speech.
3. **Forgotten list.** Add an append-only `forgotten.json` that never shrinks. Each entry has `forgotten_at`, `start`, `end`, and the `conversation_id` and `utterance_ids` as they were. Anything that leaves the stream without appearing here is a restructure, not a deletion. Whether Hearsay also deletes raw is Hearsay's decision.
4. **Stated guarantees.** Keep atomic writes (index last) and "revision = hash of the file" as documented guarantees.

### 3.3 How Idea Machine imports it

These tables are owned by Idea Machine and live in its own database:

- `im.source_conversations(conversation_id, revision, transcript_revision, imported_at)`: what was last imported.
- `im.source_segments`: an append-only copy of every utterance version Idea Machine has seen.
  - `segment_id = uuid5(conversation_id, transcript_revision, utterance_id, content_hash)`. An unchanged utterance keeps its ID; any change gives a new one.
  - Field mapping: `session_id` is `conversation_id`. `is_self` is true for `owner`, NULL for `unknown`, and false otherwise. Hearsay already thresholds `owner` for precision, and NULL fails closed to not-me. `speaker_label` is name, else label, else kind. `speaker_conf` is `owner_similarity` and `asr_confidence` is `text_confidence`.
- `im.source_supersessions(old_segment_id, new_segment_id)`, written by the importer:
  - Same `utterance_id` and same `transcript_revision` but different content (e.g. a speaker was named): link old to new.
  - The transcript changed, or a conversation was split, merged or disappeared: link each old segment to the new segments that overlap it in time. Splits and merges fall out of this.
  - An old segment with no overlap gets no link. It stops being current but is not deleted.
- `im.source_tombstones`: one row per segment matched by a forgotten entry.
- `im.current_segments`: segments that are neither superseded nor tombstoned. Everything downstream reads only this.

**Import loop** (one transaction per run): read `index.json`. For each conversation whose `revision` changed:
1. Read its file and check the file's hash matches the revision. If it doesn't, Hearsay was mid-write, so skip the conversation until the next run.
2. Compare with the previous version and write new segments and supersession links.

Conversations that left the index are handled as restructures unless the forgotten list covers them. After the import, segmentation runs exactly as built in Phase 1 step 1, finding stale episodes by anti-joining against `im.current_segments`.

### 3.4 Forgetting

A forgotten segment is deleted from everything Idea Machine holds:
- its text in `im.source_segments`
- episodes
- triage
- **labels**: the one place where human input is deleted
- embeddings, links and spans
- synthesized objects

Forgetting can't recall anything already sent to Claude. To at least report it, `egress_log` (Phase 4) records the `segment_id`s in every payload, and `im forgotten --sent` lists forgotten segments that went out, and when.

## 4. Phases

### Phase 1 — Ingest, segment, triage, end to end

**Goal:** new Hearsay segments turn into episodes with triage labels and a route, automatically, and I can measure whether the triage can be trusted.

**Progress**
- Step 1 (done, 2026-10-01): the project skeleton, migrations, heuristic segmentation, tombstone purge and the invariant tests. They run against a stand-in `hearsay` Postgres schema shaped like the tables in §3.3.
- Step 2: the stream importer (§3.3) fills `im.source_*`, the stand-in schema goes away, and the fixtures become stream directories. This depends on Hearsay asks 1–3; until they land, it uses fixtures that already include the fields being asked for.

**Deliverables**
- `im` schema and migrations. Tables: `source_conversations`, `source_segments`, `source_supersessions`, `source_tombstones`, `episodes`, `episode_segments`, `triage`, `labels`, `projects`, `runs`.
- **Ingest (step 2):** the stream importer in §3.3, run on Hearsay's stream directory mounted read-only.
- **Fixture loader:** writes synthetic stream directories (`index.json`, conversation JSONL and `forgotten.json`) in Hearsay's format, including revisions that name a speaker, re-transcribe a conversation, split or merge a conversation, and forget something. Development and tests never depend on live capture. For manual runs on eeyore, the real stream can be copied from the NAS, but tests never read it.
- **Episode segmentation (heuristic):** split within a `session_id` on a silence gap > *G* seconds, plus a speaker-change rule (e.g. self-monologue vs. multi-party). *G* and the rules are config and are recorded as `stage_version`. Episodes store `input_hash` over their current segment IDs, so an episode is recomputed only when its inputs change.
- **Projects registry:** a hand-maintained `projects` table (name, aliases, one-line description). It feeds the "which project" question.
- **Labeling:** `im label` presents unlabeled episodes in the terminal (transcript, speakers, times, and the conversation ID and offsets for finding the audio in Hearsay) and records my answers to the triage questions. Labels are stored against the episode's segment IDs. Target: **50 labeled episodes**, deliberately covering chatter and noise, not just ideas.
- **Laya spike, then triage:**
  - First, confirm Laya installs, runs on the TrueNAS CPU at an acceptable per-episode latency, and accepts our typed questions. Timebox: 2 days. If it fails, skip to the fallback and continue.
  - The triage questions per episode are `is_self_thinking` (bool), `kind` (idea/task/decision/chatter/noise), `project` (registry or none), and `keep_score` (1–5). Each answer is stored with its confidence.
  - **Fallback classifier:** local sentence embeddings with one logistic regression per question, trained on my labels. Building it is not optional, because it doubles as the baseline Laya has to beat. The embeddings are reused in Phase 2.
- **Routing:** `auto_file` / `review` / `escalate`, chosen by per-question confidence thresholds set from the eval set. A route is a label, and nothing is dropped.
- **CLI:** `im run` (all stages, idempotent), `im reset --stage S`, `im show <episode>`, `im eval`, `im review` (works through the `review` queue and adds labels as I go).

**Exit criteria (verifiable)**
- [ ] Utterances that appear in the stream get episodes and triage within 15 min of the stream update, with no manual step.
- [ ] Invariant query passes: every current segment belongs to exactly one current episode, and the number of segments not in an episode is 0.
- [ ] A revision change in the fixture stream (a speaker named, a re-transcription, a split or merge) re-derives exactly the affected episode(s). Their labels still resolve, by following supersession links.
- [ ] A forgotten entry leaves no text, episode, triage row or label for its segments after the next `im run`.
- [ ] `im reset --stage triage && im run` completes, produces the same triage rows (same versions → same outputs for deterministic stages), and leaves `labels` untouched.
- [ ] Segmentation: in a sample of 50 episodes, I judge ≥ 80% to have acceptable boundaries.
- [ ] `im eval` prints per-question accuracy and a reliability table (confidence bucket → observed accuracy) for both Laya and the fallback, on held-out labels.
- [ ] A router is chosen, with thresholds such that `auto_file` precision on `kind` is ≥ 90% on the eval set. The decision is written down in `docs/decisions/`.

**Deferred:** entity extraction, related-episode search, Claude, any UI, audio playback, active-learning tooling beyond `im review`.

### Phase 2 — Connections

_Moved ahead of extraction: relating episodes is the core value, it's cheap, and it doesn't need extracted entities._

**Goal:** for any episode, show related episodes from any point in time.

**Deliverables**
- Store episode embeddings in pgvector, versioned by embedding model. Add an HNSW index.
- `im related <episode>`: the top-k neighbors with similarity, dates and a snippet.
- `links` table holding nearest-neighbor edges above a threshold, with `method` and `version`. The threshold is chosen from labels.
- Add a "related or not" judgment to `im label` for neighbor pairs, to build a small pair-eval set (~50 pairs).

**Exit criteria**
- [ ] On the pair-eval set, precision@5 ≥ 70% for episodes with `kind ∈ {idea, decision}`.
- [ ] `im related` returns in < 1 s at my current corpus size.
- [ ] Swapping the embedding model is a version bump plus `im reset --stage embed`, and old links are replaced without leftovers.

**Deferred:** clustering/topics, graph visualization, entity-based linking.

### Phase 3 — Entity and span extraction

**Goal:** know who, what project and when each episode involves, and which spans carry the actual content.

**Deliverables**
- GLiNER2 on CPU: people, projects, dates/times, orgs, places, and a "content span" type (the sentence(s) that state the idea, task or decision). Results go to the `spans` table with character offsets into specific `segment_id`s.
- Normalize project mentions against the registry (exact match or alias, with fuzzy matching flagged for review).
- Add span correctness to the labeling flow for ~30 episodes.
- Optional: entity overlap as a second link `method` alongside embeddings.

**Exit criteria**
- [ ] Every span resolves to a current segment and a valid offset range (invariant query).
- [ ] On labeled episodes, person/project recall ≥ 80% and content-span hit rate (overlaps my marked span) ≥ 70%.
- [ ] Runtime fits inside the polling interval at my daily volume on the TrueNAS CPU.

**Deferred:** coreference, relation extraction, temporal normalization beyond what GLiNER2 gives.

### Phase 4 — Synthesis (Claude) with the privacy boundary

**Goal:** flagged episodes plus their neighbors become structured objects, without other people's words leaving the box.

**Deliverables**
- **Object tables:** `ideas`, `tasks`, `decisions`, `project_mentions`. Each row has `source_segment_ids`, timestamps, `model`, `prompt_version`, `privacy_policy_version` and `batch_id`.
- **Egress policy module** (`im.egress`, one swappable module, versioned):
  - My speech (`is_self = true` and `speaker_conf` ≥ threshold) goes out verbatim. Anything else counts as not-me (fail closed).
  - Other speakers' turns are summarized locally by a 7–14B model via Ollama into gists that keep the conversational function (proposed, objected, agreed) and drop the wording.
  - Speakers are pseudonymized per request (`me`, `S1`, `S2`…) with a fresh mapping each call, re-linked locally on return. No speaker UUIDs or names go out.
  - Final scrub: GLiNER2 removes names, places, orgs and health terms from the gists. Laya flags sensitive turns, and if it's unsure the turn becomes `[S2: omitted]`.
  - `egress_log`: per call, the payload (or its hash, configurable), the `segment_id`s it was built from, policy version, model, token counts and cost. The segment IDs are what lets `im forgotten --sent` report forgotten segments that already went out (§3.4).
- **Batch job:** daily. It selects episodes routed `escalate` or with high `keep_score`, adds their top-k neighbors (already-synthesized neighbors go in as their object summaries, not as raw text), and submits via the Message Batches API. A monthly budget cap is enforced before submission.
- **Object identity across time:** before creating an idea, retrieve existing ideas by embedding and let Claude return `new` / `same_as:<id>` / `evolves:<id>`. This only records links, never merges destructively.

**Exit criteria**
- [ ] **Leak test:** a fixture with known third-party phrases, names and health terms gets run through the policy. An n-gram check confirms no third-party 5-gram and no fixture name appears in any logged payload. It runs in CI and is required to pass.
- [ ] Every object has ≥ 1 `source_segment_id` that resolves to a current segment.
- [ ] On 20 hand-reviewed synthesized episodes, ≥ 80% of objects are ones I'd keep, and none are missing an idea or decision I'd consider important.
- [ ] Monthly cost is reported by `im cost` and stays under the configured cap. Exceeding it skips the batch, which never partially sends.
- [ ] Changing `privacy_policy_version` and re-running regenerates the payloads and objects, with old rows kept or removed per `im reset` flags.

**Deferred:** interactive Q&A over the corpus, real-time synthesis, a proposals/agent workflow.

### Phase 5+ — Later (not planned in detail)

- **Proposals:** `proposals(status: pending/approved/rejected, payload, source_object_ids)` for agent work I approve. Nothing runs without an explicit approval row.
- **Temporal facts:** Graphiti-style validity intervals on project and decision facts ("planned X from t1, switched to Y at t2"). Only adopt it if `evolves:` links from Phase 4 turn out to be insufficient.
- A review UI, if the CLI becomes the bottleneck.

## 5. Open questions

1. The Hearsay asks in §3.2 (format version, transcript revision, forgotten list, stated guarantees) are pending. Step 2's importer is built against fixtures that include those fields, so the importer can be written now.
2. Daily capture volume (hours/day, % multi-party) drives Laya throughput, GLiNER2 runtime and Claude cost. It needs measuring in the first week of Phase 1.
3. Should episodes ever span sessions? Sessions are now Hearsay conversations, which Hearsay already splits on 3 minutes of silence. The default is no.
4. Is 1–5 the right `keep_score` scale, or is binary keep/skip easier to label consistently? Decide during labeling.
5. Do the Phase 4 objects need to be exported anywhere (notes vault, task manager), or is the DB plus CLI the destination?

## 6. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Laya is unproven:** it may not install cleanly, may be slow on CPU, or its confidence may not be calibrated | Routing thresholds are meaningless, so too much gets auto-filed or escalated | Timeboxed spike first. The fallback classifier is a required Phase 1 deliverable, and Laya must beat it on `im eval`. Check the reliability table, not just accuracy. Wrap Laya behind a `Triage` interface so swapping is a config change. |
| **Small eval set:** 50 episodes give wide error bars, especially for calibration and rare classes like `decision` | False confidence in thresholds | Treat thresholds as provisional. `im review` grows labels from the low-confidence queue (cheap active learning). Re-check thresholds at 150 labels. |
| **Claude tier cost** | Runaway spend from neighbor context or re-derivation | Only flagged episodes go out. Neighbors are sent as object summaries once synthesized. Batches API, a hard monthly cap with fail-closed skipping, and `im cost`. A re-run of synthesis is an explicit, priced action (`im reset --stage synth --dry-run` shows the estimate). |
| **LLM output is re-derivable but not reproducible** | A re-run changes objects I'd already acted on | Keep old object versions instead of blowing them away by default, and diff them on re-run. |
| **Schema churn:** derived tables change often early on | Migrations become a tax, and labels get orphaned | Derived tables are disposable: drop and rebuild, rather than migrating data. Only `labels`, `projects` and later `proposals` get careful migrations. Labels anchor to segment IDs, never to derived IDs. |
| **Contract drift with Hearsay** | Ingest breaks silently | The importer refuses unknown `format_version`s and fails a run on any utterance that doesn't parse, rather than skipping it. Fixtures follow Hearsay's `stream.py` format, and are re-checked when Hearsay changes it. |
| **Supersession churn:** naming a speaker rewrites every conversation they're in, and growing conversations are re-transcribed hourly | Mass re-derivation, and the Claude tier re-spends | Re-derivation is incremental by `input_hash`. Synthesis re-runs only when the *egress payload* hash changes, not on any upstream change. |
| **Privacy leak through gists or the scrub** | Other people's words or identities reach the API | Fail-closed attribution, the leak test in CI, payload logging for audit, and a policy module that can be tightened plus re-derived. |
| **Heuristic segmentation is wrong for long multi-party conversations** | Triage and synthesis see muddled episodes | Measured in the Phase 1 exit criteria. If it fails, add the simplest fix (topic-shift via embedding distance between windows) before adding any model. |
