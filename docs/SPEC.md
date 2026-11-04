# Idea Machine V2: spec

_Status: plan, 2026-10-27; lifecycle revised 2026-10-28 (M2). What V2 is meant to do: the scope, the contracts, the data model, proposals and edge
typing. The build order and the bar each step has to clear are in [ROADMAP-v2.md](ROADMAP-v2.md). V1 is tagged `v1`
(`v1.1` adds the extraction reuse fix); its spec and roadmap stay in [ROADMAP.md](ROADMAP.md), frozen._

## 1. Scope

V2 turns the lattice into a graph with a lifecycle: **utterances → observations → candidates → ideas**
([M2 design](design/m2-observations-candidates-ideas.md)). Everything extracted is an observation; observations of
the same point cluster; a cluster of mine is a candidate with a computed weight; and only I make ideas, from the
candidates the system proposes. Edges between ideas say how they relate (supports, tension, extends) and
cite the evidence for it. **Lenses** show the graph from one idea or theme outward. Presentation and the MCP server
move into this repo ([decision 0005](decisions/0005-presentation-inside-idea-machine.md)), and Lattice is archived.

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
- **Lenses:** topic overlays on the graph. They show it and change nothing.

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
- **On-demand synthesis.** Synthesis runs as a stage, triggered by weight, not by request.
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
- Until cutover (M6), every existing `pub` view keeps its shape, so Lattice keeps working. New views are additions.
- **MCP tools are a contract.** The 19 tools Lattice serves after the rating tools were retired (lattice#10,
  2026-10-29; `src/lattice/mcp.py`, `TOOLS`) keep their names, argument schemas and result shapes byte-compatible,
  along with speaker pseudonymization in every answer and the public listener's OAuth behaviour. New tools are
  additions: `open_lens` (M5). The seven retired tools (`review_digest`, `look_again`, `decide_item`, `keep_rest`,
  `compare_pair`, `record_comparison`, `rate_digest_point`) asked me for verdicts and ratings that nothing reads.
- All human input stays feedback events: stars, links, notes, theme edits, and V2's keep (canonical or pinned) /
  unkeep / dismiss / merge / reject. Old verdict and comparison events stay in the ledger; nothing reads them.

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
| Edges | `im.idea_links` gains `supports`, `tension`, `extends` beside `evolves`, `related`, `archive`, plus `cited_observation_ids`, `confidence`, and `method` with the model version, so a rerun can be diffed | M2, M4 |
| Lens cache | `im.lens_cache(seed, graph_version, result)` | M5 |

## 4. Proposals

Nothing becomes an idea without me. Each run computes every cluster's weight with a versioned formula over stored
signals: independent me recurrences above all, a capped amount for members by diarization or not me, links to
canonical ideas. Weight orders nothing ([decision 0007](decisions/0007-neutral-proposals.md)): the most recently said
candidates come to me as proposals (up to 10 a week), and I keep each as canonical or pinned, or dismiss it. Syntheses
arrive the same way. `im tune` previews any change to the knobs at no cost.

## 5. Edge typing (Jev)

A cheap, fast decision model ("Jev") types and weights edges: structured input (the pair, fixed choices, weighting
instructions), a choice out.

- Embeddings nominate pairs: top-k neighbours plus shared evidence.
- Jev answers `supports`, `tension`, `extends`, `related` or `none`. `none` is a real answer, and a confidence
  floor keeps weak `related` edges out of the graph. `extends` is elaboration (an instance, a description, an
  application, a later version); `tension` is two ideas pulling against each other, contradiction included (B4's
  first sheet, 2026-11-04).
- Every edge records `method` and model version, so it can be rerun and diffed.
- Jev's weights feed lenses and cluster weight. Jev does not make ideas.
- The bottleneck is labels: about 200 operator-labelled pairs (B4) before its weights are trusted.
