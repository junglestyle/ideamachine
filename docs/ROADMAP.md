# Idea Machine Roadmap

_Status: draft, 2026-10-01. Idea Machine reads Hearsay's utterance stream (§3). Hearsay has delivered the stream changes Idea Machine asked for; the forget command itself comes with Hearsay slice 12. Phase 1 steps 1–4 (stream import, segmentation, projects registry, labeling, and triage with Laya and the fallback) are built. What's left of Phase 1 needs real labels._

## 1. Scope

Idea Machine reads the speaker-attributed utterances that Hearsay publishes in its utterance stream (or any capture source producing the same format), and keeps its own append-only copy of every version it has seen. It turns them into something I can use: it groups segments into episodes, triages every episode cheaply on my homelab, relates episodes to each other over time, and sends only the episodes worth it to Claude. Claude turns those into structured ideas, tasks, decisions and project mentions, each traceable to source segments. Every derived row records which code, model and prompt produced it, so any layer can be dropped and rebuilt from raw.

### Non-goals

- **Audio of any kind.** No fetching, decoding or processing. Hearsay never hands audio to consumers. `im show` prints the conversation ID and times so I can find the audio in Hearsay myself.
- **Speaker attribution.** Diarization, voiceprints and identity belong to Hearsay. Idea Machine consumes the stream's `speaker` fields as given.
- **Agent automation.** The proposals table and approved agent work are out of early scope (see Phase 5).
- **Event infrastructure.** No Kafka, NATS or queues. One consumer polls one directory.
- **A memory framework as the core.** No Mem0, Cognee or similar. Plain tables plus pgvector.
- **A UI inside Idea Machine.** Presentation is a separate app, Lattice (§3.5), reading a published contract.
- **Multi-user or hosted deployment.** One user, one TrueNAS box.

## 2. Principles applied

| Principle | How it shows up |
|---|---|
| Egress is a logged, versioned policy | What leaves the box is decided by `im.egress.POLICY_VERSION` (currently policy B: everyone's words, speakers pseudonymized; decision 0004) and every request is logged with the segment IDs it carried. |
| Raw is immutable | Idea Machine reads Hearsay's stream read-only and never writes to it. Its own copy of the stream (`im.source_*`) is append-only: corrections become superseding rows (§3), never updates. Forgetting is the one exception, and it deletes for real (§3). |
| Derived is re-derivable | Every derived row carries `schema_version`, `stage_version` (code/heuristic), and `model` + `model_version` + `prompt_version` where relevant, plus `input_hash`. Each stage supports `im reset --stage X`. |
| Cheap models route, never drop | Triage writes labels and a route. No stage deletes or hides segments. "Noise" is a label, not a filter. |
| Traceability | Every episode, label, span, link and synthesized object stores the `segment_id`s it came from. |
| Eval before trust | Each automated tier has to beat its exit threshold on the hand-labeled set before its output drives anything. |
| Human labels are not derived | Labels live in their own table, anchored to segment IDs rather than episode IDs, so they survive episode re-derivation. When a segment is superseded, its labels follow the supersession links to the current segments, so a correction never orphans a label. |

**Stack assumptions** (not questioned, change if wrong): Python; Idea Machine's own Postgres 16 + pgvector (Hearsay has no Postgres) with schema `im`; and a single container on TrueNAS, run by a timer, with Hearsay's stream directory mounted read-only. Triage needs a GPU after all (decision 0002): it runs against Ollama on eeyore, as Hearsay's transcription does. Everything else is CPU.

## 3. Hearsay → Idea Machine data contract

The source is Hearsay's **utterance stream** (Hearsay slice 8, `hearsay/stream.py`), read-only. Hearsay rewrites it after every hourly reprocess into `/mnt/storage/hearsay/stream/`. Idea Machine needs no Hearsay database and no access to Hearsay's internals.

### 3.1 What Hearsay publishes

The authoritative description is the output contract in Hearsay's AGENTS.md. In summary:

- `index.json`: `{"format_version": 1, "conversations": [...]}`. Each entry has `conversation_id`, `start`, `end`, `open`, `transcribed`, `taps`, `utterances`, `revision`, `transcript_revision` and `file`.
- `conversations/<conversation_id>.jsonl`: one utterance per line, with `conversation_id`, `utterance_id` (`<conversation_id>:<idx>`), `start` and `end` (UTC), `speaker {kind: owner|person|anonymous|stranger|unknown, name, label}`, `text`, `text_confidence` and `speaker_confidence {basis, owner_similarity}`.
- `forgotten.json`: an append-only list, never rewritten or shrunk, of `{forgotten_at, start, end, conversation_id, utterance_ids, reason}`, with IDs as they were. It's the only deletion signal: anything else that leaves the stream was restructured, not deleted.
- Guarantees:
  - Files are written atomically, conversation files first and `index.json` last, and only when their content changes.
  - `revision` is the first 16 hex characters of the sha256 of the conversation file's bytes.
  - `transcript_revision` hashes the conversation's turns as transcribed: times, index, text, diarized speaker and confidence, including `_noise`/`_media` turns. Who a turn is attributed to never goes into the hash, so naming, merging or relabeling a speaker leaves it unchanged. Within one `transcript_revision`, a given `utterance_id` always names the same stretch of speech; across revisions, IDs are renumbered. It's null for conversations that haven't been transcribed.
- Turns the operator filed as `_noise` or `_media` are left out of the stream, and can come back if refiled. That's Hearsay's call about who is speaking, not a content filter, so it doesn't conflict with "noise is a label".
- The unit of change is the conversation. Naming a speaker rewrites the files of every conversation that speaker appears in. An open conversation is re-transcribed as it grows, which gives it a new `transcript_revision` each time.

### 3.2 Status of the Hearsay asks (delivered 2026-10-01)

1. `format_version`: done.
2. `transcript_revision`: done. Hearsay hashes the turns, not the WAV, which follows exactly what an ID points at.
3. `forgotten.json`: the format is done; the list stays empty until Hearsay slice 12 adds the operator's forget command. Forgetting will be operator input, kept by absolute time and applied on every reprocess, so a re-run from raw never brings forgotten speech back. For now Hearsay keeps the raw payloads and audio of a forgotten span; whether to delete them is decided with retention in slice 12.
4. Atomic writes and the revision definition: documented as guarantees in Hearsay's AGENTS.md.

### 3.3 How Idea Machine imports it

These tables are owned by Idea Machine and live in its own database:

- `im.source_conversations(conversation_id, revision, transcript_revision, imported_at)`: what was last imported.
- `im.source_members(conversation_id, segment_id, utterance_id)`: which segments the latest import of each conversation contains, and the `utterance_id` each has in that import.
- `im.source_segments`: an append-only copy of every utterance version Idea Machine has seen.
  - `segment_id` is a uuid5 over the `conversation_id` and a hash of the utterance record without its `utterance_id`. An utterance whose content is unchanged keeps its segment, even when a re-transcription renumbers it; any change to its times, speaker, text or confidences gives a new one.
  - Field mapping: `session_id` is `conversation_id`. `is_self` is true for `owner`, NULL for `unknown`, and false otherwise. Hearsay already thresholds `owner` for precision, and NULL fails closed to not-me. `speaker_label` is name, else label, else kind. `speaker_conf` is `owner_similarity` and `asr_confidence` is `text_confidence`.
- `im.source_supersessions(old_segment_id, new_segment_id)`, written by the importer:
  - Same `utterance_id` and same `transcript_revision` but different content (e.g. a speaker was named): link old to new.
  - The transcript changed, or a conversation was split, merged or disappeared: link each old segment to the new segments that overlap it in time. Splits and merges fall out of this.
  - An old segment with no overlap gets no link. It leaves `source_members` but stays in `source_segments`.
  - The links are for traceability and for carrying labels forward; they don't decide what is current.
- `im.source_tombstones`: one row per segment matched by a forgotten entry. A segment matches if its time overlaps the entry's `start`..`end`, whichever conversation or version it's in. `utterance_ids` aren't used for matching: they're renumbered across transcript revisions, so an old ID can name different speech in another version.
- `im.current_segments`: segments in `source_members` that aren't tombstoned. Everything downstream reads only this. A turn that's filed as `_noise` and later refiled comes back with the same `segment_id`, so its labels come back with it.

**Import loop** (one transaction per run): read `index.json`. For each conversation whose `revision` changed:
1. Read its file and check the file's hash matches the revision. If it doesn't, Hearsay was mid-write, so skip the conversation until the next run.
2. Compare with the previous version and write new segments and supersession links.

Conversations that left the index lose their members, and are handled as restructures unless the forgotten list covers them. Open conversations are imported like the rest (the 15-minute target needs them), so their episodes are re-derived each hour while they grow. That's cheap for the heuristic stages; Claude only re-runs when the egress payload hash changes (§6). After the import, segmentation runs exactly as built in Phase 1 step 1, finding stale episodes by anti-joining against `im.current_segments`.

### 3.4 Forgetting

A forgotten segment is deleted from everything Idea Machine holds:
- its text in `im.source_segments`
- episodes
- triage
- **labels**: the one place where human input is deleted
- embeddings, links and spans
- synthesized objects

Until Hearsay slice 12 the list stays empty, so Idea Machine builds and tests the purge against fixtures. Forgetting can't recall anything already sent to Claude. To at least report it, `egress_log` (Phase 4) records the `segment_id`s in every payload, and `im forgotten --sent` lists forgotten segments that went out, and when.

### 3.5 Idea Machine → Lattice (the `pub` schema)

**Lattice** is a separate app (its own repo) for the presentation layer: the map, digests, and voice and MCP access. Idea Machine owns the truth; Lattice is a read-mostly view of it. It's the same pattern as Hearsay → Idea Machine.

- **Reads.** Idea Machine publishes views in schema `pub` (e.g. `pub.ideas`, `pub.evidence`, `pub.connections`, later `pub.themes`). Lattice connects as role `lattice_app`, which can read those views and nothing in `im.*`.
- **Writes.** Lattice's only write is appending rows to `pub.feedback_events`: star, keep, discard, link or unlink two ideas, pin or rename a theme, each timestamped. Idea Machine reads the events and folds them into its weighting, themes and prompts.
- **"Append-only" applies to Lattice, not to the table.** `lattice_app` can only insert. Idea Machine owns the table and deletes events when forgetting requires it: when a segment is purged, the events on ideas that existed only because of it go too. Otherwise forgotten speech would leak through the star history.
- **No formal versioning yet.** One person, one consumer. The views and the role are the boundary; versioning comes with the first breaking change.
- The schema is named `pub` so that "Lattice" always means the app.
- If other sources of ideas appear (reading notes, a journal), `pub` and its event table can lift out into a service of their own, with Idea Machine as one producer.

## 4. Phases

### Phase 1 — Ingest, segment, triage, end to end

**Goal:** new Hearsay segments turn into episodes with triage labels and a route, automatically, and I can measure whether the triage can be trusted.

**Progress**
- Step 1 (done, 2026-10-01): the project skeleton, migrations, heuristic segmentation, tombstone purge and the invariant tests. They run against a stand-in `hearsay` Postgres schema shaped like the tables in §3.3.
- Step 2 (done, 2026-10-01): the stream importer (§3.3) fills `im.source_*`, and the stand-in schema and the `seq` cursor are gone. Fixtures are stream directories in Hearsay's `format_version` 1, with every correction Hearsay makes. Checked once against a copy of the real data rendered by Hearsay's new `stream.py`: 17 conversations, 3,378 utterances and 76 episodes, with the invariants holding.
- Step 3 (done, 2026-10-01): the projects registry (`im project add/list/retire`) and `im label`. Labels follow supersession links. A label counts for an episode only when it resolves *exactly* (one current episode, same segments); otherwise it's kept and the episode comes up again. `im label` also records a **boundaries** judgment (ok / should split / should merge) for the segmentation exit criterion, and `im label --status` tracks progress toward 50. Next: the Laya spike and the fallback classifier.
- Step 4 (done, 2026-10-01): the Laya spike passed (`docs/decisions/0001-laya-spike.md`). Triage runs in `im run` with Laya and, once `im train` has run, the fallback classifier. `im eval` reports per-question accuracy and reliability for both, with the fallback cross-validated. `im reset --stage triage` is in place. Episode embeddings are stored in pgvector, ready for Phase 2.
- Step 5 (2026-10-01, in progress): the first eval (`docs/decisions/0002-first-triage-eval.md`) found that neither Laya nor the logreg baseline beats always giving the most common answer. Triage now defaults to a local LLM through Ollama on eeyore's GPU (`gpt-oss:20b`, about 1 s per episode), which is the first backend to beat the baseline (project +21 points). Laya stays available for comparison. Episodes now carry context (local time, length, speakers present, pendant taps), and triage redoes an episode whenever that input changes. Labels can be revised (`im label --review kind=idea`), and an episode's latest label wins. Next: re-label with the revised definitions, then judge the LLM on 20–30 *fresh* labels, since the question wording was tuned on the first 52.
- Step 6 (2026-10-05): router v1 and `im review` (`docs/decisions/0003-router-v1.md`). After re-labeling, 93 of 95 episodes are chatter with nothing to keep, so no model can be validated yet. Routing is cautious: taps and "note to self" always come to me, and LLM flags add to the review queue. Reviews are labels, which is how positives get collected. Phase 1 exit criteria still open: triage within 15 min without a manual step (needs a timer at :45 and, eventually, the NAS deployment), and a router chosen *from* an eval. That needs positives first.
- Step 7 (2026-10-06): idea extraction with Claude, pulled forward from Phase 4 (`docs/decisions/0004-claude-extraction.md`). Every episode goes to Claude under privacy policy B (everyone's words, speakers pseudonymized, every request logged). Captured items go to `im ideas --review` (keep or discard), and router v2 sends to review whatever Claude captured, tapped episodes, and notes to self. The local LLM and Laya are off by default.

**Deliverables**
- `im` schema and migrations. Tables: `source_conversations`, `source_segments`, `source_supersessions`, `source_tombstones`, `episodes`, `episode_segments`, `triage`, `labels`, `projects`, `runs`.
- **Ingest (step 2):** the stream importer in §3.3, run on Hearsay's stream directory mounted read-only.
- **Fixture loader:** writes synthetic stream directories (`index.json`, conversation JSONL and `forgotten.json`) in Hearsay's format, including revisions that name a speaker, re-transcribe a conversation, split or merge a conversation, and forget something. Development and tests never depend on live capture. For manual runs on eeyore, the real stream can be copied from the NAS, but tests never read it.
- **Episode segmentation (heuristic):** split within a `session_id` on a silence gap > *G* seconds, plus a speaker-change rule (e.g. self-monologue vs. multi-party). *G* and the rules are config and are recorded as `stage_version`. Episodes store `input_hash` over their current segment IDs, so an episode is recomputed only when its inputs change.
- **Projects registry:** a hand-maintained `projects` table (name, aliases, one-line description). It feeds the "which project" question.
- **Labeling:** `im label` presents unlabeled episodes in the terminal (transcript, speakers, times, and the conversation ID and offsets for finding the audio in Hearsay) and records my answers to the triage questions, plus whether the episode's boundaries are right. Labels are stored against the episode's segment IDs. Target: **50 labeled episodes**, deliberately covering chatter and noise, not just ideas.
- **Laya spike, then triage:**
  - First, confirm Laya installs, runs on the TrueNAS CPU at an acceptable per-episode latency, and accepts our typed questions. Timebox: 2 days. If it fails, skip to the fallback and continue.
  - The triage questions per episode are `is_self_thinking` (bool), `kind` (idea/task/decision/chatter/noise), `project` (registry or none), and `keep_score` (1–5). Each answer is stored with its probabilities and confidence.
  - Laya ships over-confident (per its model card, and seen in the spike). Its temperatures are fitted on a training split of the labels (`laya.fit_temperatures`) before any threshold is set from its confidences.
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

### Phase 2 — The idea lattice

_Replaces the earlier "connections between episodes" plan. Extraction (decision 0004) showed the value is in ideas, not episodes: 70 of 120 captured items were worth keeping from recordings I'd labeled 93% chatter._

**Goal:** Idea Machine drinks from the fire hose, keeps a durable lattice of ideas that grows over time, and feeds it back to me. I navigate it graphically or in conversation, and my flags steer what it captures and how it categorizes.

**Model:**
- **Ideas** are canonical and durable, and they carry my feedback.
- **Items** (Claude's captures) are evidence attached to ideas: quote, who said it, when, and the segments it came from.
- **Connections** link ideas: same as, evolves, related, and links I make.
- **Themes** are categories proposed by clustering and steered by me.

**Slices, in order** (each usable on its own):
1. **Lattice core (Idea Machine).** _Done 2026-10-06: 36 archive ideas seeded (5 pinned themes, 11 cross-references), 120 captures matched into 70 captured ideas (5 evolutions) for $0.44, and 26 related edges at cosine ≥ 0.70._
   - Seed it from my ChatGPT archive (36 ideas, 5 clusters).
   - Embed items and ideas locally into pgvector.
   - For each new item, Claude judges `new` / `same_as:<idea>` / `evolves:<idea>` against its nearest ideas. This was Phase 4's "object identity"; it only links, never merges destructively.
   - Related-idea edges come from the embeddings.
   - Publish `pub` with the `lattice_app` role (§3.5).
   - *Done when:* the same idea said in two conversations is one idea with two pieces of evidence, and the archive's ideas sit in the same lattice as the captures.
2. **Thin Lattice view (new repo `lattice`).** _Done 2026-10-06 (`~/data/code/lattice`, `lattice serve`). What it showed: captured ideas have no themes yet (all gray), most ideas are isolated, and the archive's clusters and evolutions read correctly._ An ugly, read-only page of nodes and edges straight from `pub`, on eeyore and tailnet-only. It proves the contract and shows whether the lattice is worth investing in before any weighting work.
3. **Feedback that updates the weighting.**
   - A ★ *interesting* verdict above keep and discard, in `im ideas --review` and as a Lattice feedback event.
   - A small personal model (item embedding, kind, speaker → keep) orders review.
   - A rotating set of my kept and discarded examples goes into the extraction prompt.
   - The prompt revision from my discard reasons (a capture must make sense without the conversation around it; my interests as context).
   - *Done when:* a held-out eval shows the ranking puts my keeps first, and the keep rate of new captures rises above the first prompt's 58%.
4. **Themes.** _Done 2026-10-06, ahead of slice 3 because the thin view showed captured ideas had no structure. The archive's 5 clusters are the anchors. The filer put 48 ideas into them, Claude proposed 6 themes for the leftovers, and 13 stayed unthemed; $0.09. Pin, unpin, rename and reject go through `pub.feedback_events` (`im themes …` now, Lattice later), and rejected names aren't proposed again._ Periodic clustering of ideas, named by Claude. I can pin, rename, merge or split themes from Lattice (feedback events). Pinned themes become stable categories that new ideas are filed into.
5. **The map (Lattice).** A 2D semantic map colored by theme and sized by weight, with edges. Clicking an idea shows its evidence. I can star, keep, discard and link in place, and "new since my last visit" is highlighted.
6. **Periodic synthesis.** A weekly Claude pass over new ideas and the lattice: recurring themes, convergences, ideas that matter more than they seemed, emerging projects. Idea Machine writes the digest, Lattice shows it, and it may also go to my phone.
7. **Talk to it.** An MCP server in Lattice over `pub` (search, open a theme, what's new, link, star), so a Claude app can walk the lattice with me, by voice too. Flags made there are feedback events.

**Deferred:** temporal fact tracking (Phase 5), sources other than Hearsay.

### Phase 3 — Entity and span extraction

_Still wanted, now for a second reason: under privacy policy B (decision 0004), names spoken in conversations go to Claude unscrubbed. Entity extraction is what would let a stricter policy scrub them._

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

_Mostly overtaken (2026-10-06):_
- _Extraction with Claude is built under privacy policy B (decision 0004), which replaces the local-gist boundary below._
- _Object identity moved to Phase 2 slice 1._
- _The batch job became the hourly `im run` (Batches API optional)._

_What's left from here: the leak test and local gists, if a stricter policy is ever wanted, and `im cost`. The text below is the original plan._

**Goal:** flagged episodes plus their neighbors become structured objects, without other people's words leaving the box.

**Gate (lifted by decision 0004 for extraction):** nothing is sent to Claude until the operator can actually forget something. That needs Hearsay slice 12's forget command (or an earlier, smaller version of it), with Idea Machine's purge verified end to end on real data. Sending is the one step forgetting can't undo.

**Deliverables**
- **Object tables:** `ideas`, `tasks`, `decisions`, `project_mentions`. Each row has `source_segment_ids`, timestamps, `model`, `prompt_version`, `privacy_policy_version` and `batch_id`.
- **Provenance:** an idea counts whoever had it, and each object records who said it. That comes from the speakers of its source segments (me, a named person, or an anonymous voice), so it follows Hearsay's later re-attributions.
- **Notes to self:** a pendant tap, or saying "note to self", marks speech I meant to keep. Episodes carry taps in their context now. Phase 4 should treat a tapped span as always worth synthesizing.
- **Egress policy module** (`im.egress`, one swappable module, versioned):
  - My speech goes out verbatim. Anything else counts as not-me (fail closed). **Decide** whether `owner` turns whose basis is `diarization` (inherited from a diarized speaker rather than matched by voice) count as mine here. In the first real data they were 526 of 999 owner utterances.
  - Other speakers' turns are summarized locally by a 7–14B model via Ollama into gists that keep the conversational function (proposed, objected, agreed) and drop the wording.
  - The projects registry stays local. Its descriptions can hold other people's details (e.g. a friend's health), so they never go out verbatim: payloads name projects by slug only, or pass the descriptions through the same scrub as other speakers' gists.
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
- A review UI: superseded by Lattice (§3.5, Phase 2).

## 5. Open questions

1. The Hearsay asks in §3.2 are delivered. What's still open is when the operator can actually forget (Hearsay slice 12). Phase 4 is gated on it.
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
