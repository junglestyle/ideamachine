# Idea Machine Roadmap

_Status: draft, 2026-10-01. The Hearsay contract (§3) needs agreement before Phase 1 ingestion is built._

## 1. Scope

Idea Machine reads the immutable, speaker-attributed transcript segments that Hearsay (or any capture source producing the same format) writes to Postgres. It turns them into something I can use: it groups segments into episodes, triages every episode cheaply on my homelab, relates episodes to each other over time, and sends only the episodes worth it to Claude. Claude turns those into structured ideas, tasks, decisions and project mentions, each traceable to source segments. Every derived row records which code, model and prompt produced it, so any layer can be dropped and rebuilt from raw.

### Non-goals

- **Audio of any kind.** No fetching, decoding or processing. Audio references are passed through untouched.
- **Speaker attribution.** Diarization, voiceprints and identity belong to Hearsay. Idea Machine consumes `speaker_id` / `is_self` as given.
- **Agent automation.** The proposals table and approved agent work are out of early scope (see Phase 5).
- **Event infrastructure.** No Kafka, NATS or queues. One consumer polls one table.
- **A memory framework as the core.** No Mem0, Cognee or similar. Plain tables plus pgvector.
- **A UI** before the CLI has proven what's worth looking at.
- **Multi-user or hosted deployment.** One user, one TrueNAS box.

## 2. Principles applied

| Principle | How it shows up |
|---|---|
| Raw is immutable | Idea Machine connects with a read-only role on `hearsay.*`. Corrections arrive as superseding rows (§3) and never as updates. |
| Derived is re-derivable | Every derived row carries `schema_version`, `stage_version` (code/heuristic), and `model` + `model_version` + `prompt_version` where relevant, plus `input_hash`. Each stage supports `im reset --stage X`. |
| Cheap models route, never drop | Triage writes labels and a route. No stage deletes or hides segments. "Noise" is a label, not a filter. |
| Traceability | Every episode, label, span, link and synthesized object stores the `segment_id`s it came from. |
| Eval before trust | Each automated tier has to beat its exit threshold on the hand-labeled set before its output drives anything. |
| Human labels are not derived | Labels live in their own table, anchored to segment IDs rather than episode IDs, so they survive episode re-derivation. |

**Stack assumptions** (not questioned, change if wrong): Python, Postgres 16 + pgvector in the same instance Hearsay uses (separate `im` schema), and a single container on TrueNAS run by a timer. Everything is CPU-first. A GPU is a speedup, not a requirement.

## 3. Hearsay → Idea Machine data contract (proposed — NEEDS AGREEMENT)

Shared Postgres, schema `hearsay`. Idea Machine gets `SELECT` only.

```sql
-- Append-only. Corrections are new rows that supersede old ones; nothing is UPDATEd or DELETEd.
CREATE TABLE hearsay.segments (
  segment_id      uuid PRIMARY KEY,
  seq             bigint GENERATED ALWAYS AS IDENTITY UNIQUE, -- poll cursor
  supersedes      uuid NULL REFERENCES hearsay.segments(segment_id),
  source          text NOT NULL,          -- capture source/device, e.g. 'omi'; keeps IM source-agnostic
  session_id      text NOT NULL,          -- one continuous capture session
  started_at      timestamptz NOT NULL,
  ended_at        timestamptz NOT NULL,
  text            text NOT NULL,
  asr_confidence  real NULL,              -- 0..1
  language        text NULL,              -- BCP-47
  speaker_label   text NOT NULL,          -- diarization cluster, stable within session_id
  speaker_id      uuid NULL,              -- resolved person, NULL if unknown
  is_self         boolean NULL,           -- true = me; NULL = unknown
  speaker_conf    real NULL,              -- confidence in speaker_id/is_self, 0..1
  audio_ref       jsonb NULL,             -- opaque: {"audio_id": "...", "start_ms": 0, "end_ms": 0}
  producer        text NOT NULL,          -- e.g. 'hearsay/asr=whisper-x.y/diar=...'
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- What Idea Machine treats as truth: rows nobody has superseded.
CREATE VIEW hearsay.current_segments AS
SELECT s.* FROM hearsay.segments s
WHERE NOT EXISTS (SELECT 1 FROM hearsay.segments n WHERE n.supersedes = s.segment_id);
```

Points to agree on:

1. **Cursor safety.** Identity values can commit out of order under concurrent writers, which lets a naive `seq > last` poll skip rows. Two fixes: Hearsay keeps a single writer, or Idea Machine re-reads a trailing window (e.g. the last 10 min of `seq`) and upserts idempotently. The plan assumes the trailing window, since it costs nothing.
2. **Supersession semantics.** A superseding row replaces the whole segment (text, speaker, times). A split or merge is several rows superseding one, or one row superseding several. The second case needs `supersedes uuid[]` or a link table. **Decide which.**
3. **`is_self` fail-closed.** Idea Machine treats `NULL` or low `speaker_conf` as not-me. Hearsay needs to publish the threshold it considers reliable, or Idea Machine picks one from the eval set.
4. **Deletion.** "Immutable" will collide with "delete what I said about X" or a request from someone else. Proposal: a `hearsay.tombstones(segment_id, reason, created_at)` table that Idea Machine honors by purging derived rows. **Decide whether raw text is physically deleted.**
5. **Speaker directory.** Idea Machine needs `speaker_id → display name` locally for CLI output. Proposal: read-only `hearsay.speakers(speaker_id, display_name)`. These names never leave the box (see Phase 4).

## 4. Phases

### Phase 1 — Ingest, segment, triage, end to end

**Goal:** new Hearsay segments turn into episodes with triage labels and a route, automatically, and I can measure whether the triage can be trusted.

**Deliverables**
- `im` schema and migrations. Tables: `ingest_state`, `episodes`, `episode_segments`, `triage`, `labels`, `projects`, `runs`.
- **Ingest:** a poller that reads `hearsay.current_segments` with the trailing-window cursor. When a segment is superseded, it marks every episode containing it stale.
- **Fixture loader:** a script that writes synthetic or exported transcripts into a local `hearsay` schema that matches §3, so development and tests don't depend on live capture.
- **Episode segmentation (heuristic):** split within a `session_id` on a silence gap > *G* seconds, plus a speaker-change rule (e.g. self-monologue vs. multi-party). *G* and the rules are config and are recorded as `stage_version`. Episodes store `input_hash` over their current segment IDs, so an episode is recomputed only when its inputs change.
- **Projects registry:** a hand-maintained `projects` table (name, aliases, one-line description). It feeds the "which project" question.
- **Labeling:** `im label` presents unlabeled episodes in the terminal (transcript, speakers, times, `audio_ref` printed for manual playback) and records my answers to the triage questions. Labels are stored against the episode's segment IDs. Target: **50 labeled episodes**, deliberately covering chatter and noise, not just ideas.
- **Laya spike, then triage:**
  - First, confirm Laya installs, runs on the TrueNAS CPU at an acceptable per-episode latency, and accepts our typed questions. Timebox: 2 days. If it fails, skip to the fallback and continue.
  - The triage questions per episode are `is_self_thinking` (bool), `kind` (idea/task/decision/chatter/noise), `project` (registry or none), and `keep_score` (1–5). Each answer is stored with its confidence.
  - **Fallback classifier:** local sentence embeddings with one logistic regression per question, trained on my labels. Building it is not optional, because it doubles as the baseline Laya has to beat. The embeddings are reused in Phase 2.
- **Routing:** `auto_file` / `review` / `escalate`, chosen by per-question confidence thresholds set from the eval set. A route is a label, and nothing is dropped.
- **CLI:** `im run` (all stages, idempotent), `im reset --stage S`, `im show <episode>`, `im eval`, `im review` (works through the `review` queue and adds labels as I go).

**Exit criteria (verifiable)**
- [ ] Segments written to `hearsay.segments` get episodes and triage within 15 min, with no manual step.
- [ ] Invariant query passes: every current segment belongs to exactly one current episode, and the number of segments not in an episode is 0.
- [ ] Inserting a superseding segment in the fixture DB re-derives exactly the affected episode(s). Their labels still resolve.
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
  - `egress_log`: per call, the payload (or its hash, configurable), policy version, model, token counts and cost.
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

1. Contract items §3.2 (split/merge supersession) and §3.4 (deletion/tombstones) must be decided with Hearsay before Phase 1 ingest is coded.
2. Daily capture volume (hours/day, % multi-party) drives Laya throughput, GLiNER2 runtime and Claude cost. It needs measuring in the first week of Phase 1.
3. Should episodes ever span sessions (e.g. a monologue interrupted by a reconnect)? The default is no.
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
| **Contract drift with Hearsay** | Ingest breaks silently | A contract test in the Idea Machine repo checks the `hearsay` view's columns and types on startup and refuses to run on mismatch. |
| **Supersession churn:** Hearsay re-attributes speakers in bulk after enrolling a voiceprint | Mass re-derivation, and the Claude tier re-spends | Re-derivation is incremental by `input_hash`. Synthesis re-runs only when the *egress payload* hash changes, not on any upstream change. |
| **Privacy leak through gists or the scrub** | Other people's words or identities reach the API | Fail-closed attribution, the leak test in CI, payload logging for audit, and a policy module that can be tightened plus re-derived. |
| **Heuristic segmentation is wrong for long multi-party conversations** | Triage and synthesis see muddled episodes | Measured in the Phase 1 exit criteria. If it fails, add the simplest fix (topic-shift via embedding distance between windows) before adding any model. |
