# Idea Machine V2: spec

_Status: plan, 2026-10-27; lifecycle revised 2026-10-28 (M2). What V2 is meant to do: the scope, the contracts, the data model, proposals and edge
typing. The build order and the bar each step has to clear are in [ROADMAP-v2.md](ROADMAP-v2.md). V1 is tagged `v1`
(`v1.1` adds the extraction reuse fix); its spec and roadmap stay in [ROADMAP.md](ROADMAP.md), frozen._

## 1. Scope

V2 turns the lattice into a graph with a lifecycle: **utterances → observations → candidates → ideas**
([M2 design](design/m2-observations-candidates-ideas.md)). Everything extracted is an observation; observations of
the same point cluster; a cluster of mine is a candidate with a computed weight; and only I make ideas, from the
candidates the system proposes. Edges between ideas say that they're related, and optional kinds say how
(supports, tension, elaborates, sympathy, and more as they're added); each cites its evidence. **Lenses** show the
graph from any seed: an idea, a subject, a sentence, a question
([decision 0010](decisions/0010-connections-kinds-lenses.md)).
Presentation and the MCP server move into this repo ([decision 0005](decisions/0005-presentation-inside-idea-machine.md)).
Lattice left the deploy on 2026-11-11 (docs/deploy.md); the neutral view (`im web`) is the presentation until
Emanation, and no MCP server runs until M5 builds one here.

### Who said it, and what I keep

Who said it decides what an observation can become (2026-10-28, mine). Attribution is me / not me: Hearsay stopped
naming anyone else, and a turn whose speaker is `unknown` counts as not me. How sure "me" is comes from the
segment's `speaker_basis`. Hearsay checked it by ear on 2026-10-28: an owner turn by `voice` was right 49 of 49
times, and one by `diarization` 147 of 162 times (the misses are short interjections). So an owner turn by `voice`,
`named` or `channel` is me; one by `diarization` counts only alongside a me observation.

- **Observations** are neutral and never judged: something I said, something someone else said. Extraction
  captures any thought, not only what seems worth keeping (prompt `v3`): questions, jokes, passing thoughts and
  opinions, but not logistics, small talk or media. Whether a thought matters is left to recurrence and to me.
- **Candidates** are clusters with a me observation. Clusters of only not-me observations never become candidates;
  they link to mine and add weight.
- **Ideas** are what I keep, as **canonical** (weighted: it attracts and pulls linked clusters) or **pinned** (kept
  and visible, no weight). Pinning never changes weight, maturity or clustering. The system proposes; it never makes
  an idea.
- **Lenses:** the way into the graph. A lens is a query (of a pluggable kind, such as questions Jev answers per
  cluster, or all/none), metadata filters and my pins, and returns which clusters belong; it changes nothing. Pinned or archived,
  never thrown away. Themes become lenses. A **board** groups lenses, placed by hand; a Trellis view draws one
  ([0013](decisions/0013-lenses-and-boards.md)). Viewing the graph from one cluster isn't a lens.

It's a refactor of V1, not a rewrite: the ledger (`im.source_*`, `im.items`, `im.egress_log`), versioned inference
and event-sourced human input are what V2 needs, and they stay.

### What V2 fixes from V1

| | Cause | Fixed in |
|---|---|---|
| Naming a speaker re-derives episodes and re-extracts them | Segment identity hashes attribution (`importer.py:76-84`) | v1.1 (no re-spend), M1 (no re-derivation) |
| Extraction keyed on episode, not payload | `extract._pending` | v1.1 |
| Duplicate items with conflicting verdicts; recurrence that counts re-capture | Items hang off episodes; no observation identity | M2 |
| Domain logic in Lattice, map positions in the pipeline | The `pub`-views-only seam | M5–M6 (0005) |
| `pub.review_items` picks an idea with `LIMIT 1` and no `ORDER BY` | `010_review.sql`, `011_corrections.sql` | M2 |

### Non-goals

V1's non-goals ([ROADMAP §1](ROADMAP.md#non-goals)) still hold, except "a UI inside Idea Machine". Added for V2:

- **A second datastore.** No Neo4j, SQLite or Redis. The graph is tables in the existing Postgres; graph algorithms
  run in Python per run and their results are stored. [Decision 0006](decisions/0006-graph-in-postgres.md) says what
  that shapes in the data and the code, and when to look again.
- **The system deciding what's an idea.** Models and queries produce weight and proposals; I decide.
- **Typing every pair.** Embeddings nominate pairs; nothing runs over n².
- **On-demand synthesis.** Synthesis runs as a stage, triggered by connected neighborhoods
  ([0010](decisions/0010-connections-kinds-lenses.md)), not by request.
- **Reprocessing from audio.** V1 data is migrated with a dedupe pass. Its 818 verdicts, 743 feedback events and
  135+ comparisons are the only labels the benchmarks have.

## 2. Contracts

**Hearsay → Idea Machine:** unchanged. The contract is Hearsay's
[docs/stream.md](https://github.com/junglestyle/hearsay/blob/main/docs/stream.md); the import is
[ROADMAP §3.3](ROADMAP.md#33-how-idea-machine-imports-it). V2 starts
storing the index's `open` flag per conversation, which the format already carries.

**Idea Machine → web app and MCP:**

- `pub` stays the read model. The web process connects as a read-mostly role: read `pub`, insert into
  `pub.feedback_events`. It never connects as `im_pipeline`.
- Lattice was removed on 2026-11-11, and with it what only it read from `pub` (migration 058). New views are
  additions.
- **MCP tools are a contract**, not served from 2026-11-11 until M5: the claude.ai connector stopped with Lattice.
  When M5 serves them here (adding the `pub` views they need), the 19 tools Lattice served after the rating tools
  were retired (lattice#10, 2026-10-29; `src/lattice/mcp.py`, `TOOLS`) keep their names, argument schemas and
  result shapes byte-compatible, along with speaker pseudonymization in every answer and the public listener's
  OAuth behavior. New tools are additions: `open_lens` (M5). The seven retired tools (`review_digest`, `look_again`,
  `decide_item`, `keep_rest`, `compare_pair`, `record_comparison`, `rate_digest_point`) asked me for verdicts and
  ratings that nothing reads.
- All human input stays feedback events: stars, links, notes, theme edits, and V2's keep (canonical or pinned) /
  unkeep / dismiss / merge / reject. Old verdict and comparison events stay in the ledger; nothing reads them.
- Where events disagree (a star and an unstar, a link and an unlink, two corrections, two marks), my latest is the
  one that arrived last, the highest `event_id`, in every view. `at` is when an event was made: it bounds windows,
  such as the digest's week, and doesn't order them.

## 3. Data model

Proposed shapes. Each is settled in its milestone's design doc, and every new table is covered by forgetting
(`pipeline._purge_tombstoned`) from its first migration.

| Concept | Shape | Milestone |
|---|---|---|
| Segment identity | `uuid5(conversation, start, end, text)`; speaker fields become annotations. Old ids link forward through `im.source_supersessions` (method `rekey`) | M1 |
| Observation | `im.observations` + `items.observation_id`: one thing derived from one stretch of speech. Items group by segment descent plus `quote_score >= 0.4`. Speaker read at query time | M2 |
| Recurrence | Distinct conversations, or one conversation at least N (30) minutes apart. Never distinct items | M2 |
| Cluster | Today's `im.ideas` row, ids kept. Maturity `observation`, `candidate` or `idea`; an idea is kept as `canonical` or `pinned`; `merged`, `rejected` stay | M2 |
| Weight | `im.weights`: cluster, formula version, weight, the signals it read, when | M2 |
| Proposals | `im.proposals`: cluster, weight, formula version, when, outcome (canonical, pinned, dismissed) | M2 |
| Edges | One connection per pair (`cited_observation_ids`, `confidence`, `method` with the model version, so a rerun can be diffed); kinds (`supports`, `tension`, `elaborates`, `sympathy`, more later) are optional rows on a connection, with their own direction and method; lineage (`evolves`, `synthesizes`) is kept apart from both ([0010](decisions/0010-connections-kinds-lenses.md); built in migration 045, [design](design/connections-storage.md)) | M2, M4 |
| Lenses | A lens: query (or all/none), filters, pins anchored to speech, pinned or archived, its answers cached by cluster text and lens version; boards of placed lenses; themes migrate into lenses ([0013](decisions/0013-lenses-and-boards.md)) | M5 |

## 4. Proposals

Nothing becomes an idea without me. Each run computes every cluster's weight with a versioned formula over stored
signals: independent me recurrences above all, a capped amount for members by diarization or not me, links to
canonical ideas. Weight orders nothing ([decision 0007](decisions/0007-neutral-proposals.md)): the most recently said
candidates come to me as proposals (up to 10 a week), and I keep each as canonical or pinned, or dismiss it. Syntheses
arrive the same way, triggered by a neighborhood of connections
([0010](decisions/0010-connections-kinds-lenses.md)). `im tune` previews any change to the knobs at no cost.

## 5. Edge typing

A model decides edges from structured input (the pair as each side was said and summarized), a choice out. Terra
(`openai/gpt-6.1-terra`, low effort) decides, having passed B4 (2026-11-07); Jev, the cheap, fast decision model, failed
it on edges that only belong together, and may come back as a cheaper decider or a filter in front of one.

- Embeddings nominate pairs: top-k neighbors plus shared evidence.
- The model decides whether there's an edge: connected or `none`. Connected means the two belong in the same tight
  neighborhood, not that they share a subject, which is a lens's job ([0010](decisions/0010-connections-kinds-lenses.md)).
  `none` is a real answer. It also suggests kinds, shown on the edge and never deciding whether it exists
  ([0009](decisions/0009-edges-connection-graded-kind-suggested.md)).
- Tension (two ideas pulling against each other, contradiction included) and analogy (the same structure in
  different subjects) are passes of their own over pairs, each with its own model.
- Every edge records `method` and model version, so it can be rerun and diffed.
- A connection weighs 1 in lenses and cluster weight; a kind adds a versioned fraction, 0 until measured. Deciding
  edges makes no ideas.
- B4's 200 marked pairs are what any decider is scored against (`im audit-edges score`) before it replaces Terra.
