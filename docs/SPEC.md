# Idea Machine V2: spec

_Status: plan, 2026-10-27. What V2 is meant to do: the scope, the contracts, the data model, promotion and edge
typing. The build order and the bar each step has to clear are in [ROADMAP-v2.md](ROADMAP-v2.md). V1 is tagged `v1`
(`v1.1` adds the extraction reuse fix); its spec and roadmap stay in [ROADMAP.md](ROADMAP.md), frozen._

## 1. Scope

V2 turns the lattice into a graph with a lifecycle. Captured ideas start **provisional** and are **promoted** by a
deterministic rule over stored signals. Edges between ideas say how they relate (supports, contradicts, extends) and
cite the evidence for it. **Lenses** show the graph from one idea or theme outward. Presentation and the MCP server
move into this repo ([decision 0005](decisions/0005-presentation-inside-idea-machine.md)), and Lattice is archived.

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
  run in Python per run and their results are stored.
- **A model deciding promotion.** Models produce signals; a query decides.
- **Typing every pair.** Embeddings nominate pairs; nothing runs over n².
- **On-demand synthesis.** Weekly digest points are the syntheses.
- **Reprocessing from audio.** V1 data is migrated with a dedupe pass. Its 818 verdicts, 743 feedback events and
  135+ comparisons are the only labels the benchmarks have.

## 2. Contracts

**Hearsay → Idea Machine:** unchanged ([ROADMAP §3](ROADMAP.md#3-hearsay--idea-machine-data-contract)). V2 starts
storing the index's `open` flag per conversation, which the format already carries.

**Idea Machine → web app and MCP:**

- `pub` stays the read model. The web process connects as a read-mostly role: read `pub`, insert into
  `pub.feedback_events`. It never connects as `im_pipeline`.
- Until cutover (M6), every existing `pub` view keeps its shape, so Lattice keeps working. New views are additions.
- **MCP tools are a contract.** The 24 tools at Lattice `v1` (`src/lattice/mcp.py`, `TOOLS`) keep their names,
  argument schemas and result shapes byte-compatible, along with speaker pseudonymization in every answer and the
  public listener's OAuth behaviour. New tools are additions: `open_lens`, `promote_idea`.
- All human input stays feedback events: verdicts, stars, links, comparisons, theme edits, and V2's
  promote / demote / merge / reject.

## 3. Data model

Proposed shapes. Each is settled in its milestone's design doc, and every new table is covered by forgetting
(`pipeline._purge_tombstoned`) from its first migration.

| Concept | Shape | Milestone |
|---|---|---|
| Segment identity | `uuid5(conversation, start, end, text)`; speaker fields become annotations. Old ids link forward through `im.source_supersessions` (method `rekey`) | M1 |
| Observation | `items.observation_id`: one capture of one stretch of speech. Items group by segment descent plus `quote_score >= 0.4`. Verdicts fold onto it (latest wins) | M2 |
| Independent support | Distinct conversations, or one conversation at least N minutes apart. Never distinct items | M2 |
| Idea status | `ideas.status`: `canonical` (archive and mine), `promoted`, `provisional`, `merged`, `rejected` | M2 |
| Promotion history | `im.promotions`: idea, from and to status, rule version, the signals it read, when | M2 |
| Edges | `im.idea_links` gains `supports`, `contradicts`, `extends` beside `evolves`, `related`, `archive`, plus `evidence_item_ids`, `confidence`, and `method` with the model version, so a rerun can be diffed | M2, M4 |
| Lens cache | `im.lens_cache(seed, graph_version, result)` | M5 |

## 4. Promotion

A run stage evaluates a rule over stored signals and writes `status` plus a `promotions` row when it changes. The
starting rule (from the architecture review, **not yet validated**): at least two independent observations or a link
to a canonical idea; rubric generality or outside value of 3 or more; not discarded, not merged. M3 tunes the
thresholds against operator review, and the rule is versioned like any stage.

## 5. Edge typing (Jev)

A cheap, fast decision model ("Jev") types and weights edges: structured input (the pair, fixed choices, weighting
instructions), a choice out.

- Embeddings nominate pairs: top-k neighbours plus shared evidence.
- Jev answers `supports`, `contradicts`, `extends`, `related` or `none`. `none` is a real answer, and a confidence
  floor keeps weak `related` edges out of the graph.
- Every edge records `method` and model version, so it can be rerun and diffed.
- Jev's weights feed lenses and the promotion signals. Jev does not decide promotion.
- The bottleneck is labels: about 200 operator-labelled pairs (B4) before its weights are trusted.
