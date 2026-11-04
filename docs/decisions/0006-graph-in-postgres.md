# 0006: The graph stays in Postgres, and what that shapes

_2026-10-27. Status: accepted, not built yet (V2 milestones M2, M4, M5). Settles [SPEC.md](../SPEC.md)'s non-goal "a
second datastore"; my decision._

## Context

V2 makes the lattice a graph: ideas with a lifecycle, typed edges that cite evidence, and lenses that show it from one
idea outward. A graph database (Neo4j) is the obvious home for that. The architecture review sized the workload
instead: 564 ideas and 365 edges now, about 6,000 and 10,000 at ten times the capture. At that size Postgres
answers every query V2 needs in milliseconds. A second store would also split the one thing V2 can't lose, provenance
from every idea and edge back to segment ids, across two transactions and two forgetting cascades.

Picking the store isn't neutral, though. It decides the shape of the data and where the graph code lives, so this
records both, along with what Postgres costs us and when to look again.

An earlier design conversation proposed two stores. SQLite would hold an append-only evidence ledger: what Idea
Machine received, what it extracted, by which model and prompt, plus the ideas I authored. Neo4j would hold the
interpretive graph built from it. The separation is right, and this decision keeps it. What it rejects is the second
database. The boundary is drawn in the schema instead.

## Decision

The graph is tables in the existing Postgres. Graph algorithms run in Python, and their results are stored.

**Ledger and graph are separate layers in one database:**

| Layer | Tables | Rule |
|---|---|---|
| Ledger: what was received, extracted and decided | `im.source_*`, `im.items`, `im.egress_log`, `pub.feedback_events` | Append-only, with stable ids, model, prompt version and source references. Only Hearsay's forgetting deletes from it |
| Graph: what Idea Machine thinks it means | `im.ideas` (status), `im.idea_links`, themes, promotions, lens cache | Written only by graph stages. Rebuildable from the ledger at any time, with a different rule or algorithm, without touching the ledger |

- An observation (a stretch of speech) and an idea (an interpretation of it) stay distinct. Items cite segments, and
  ideas cite items as evidence.
- Ideas I author go through the ledger first. That already holds: `add_idea` records an `idea_add` feedback event,
  and a trigger creates the idea (`023_idea_add.sql`). An authored idea enters the graph as `canonical` and doesn't
  wait for promotion.
- A rebuild with a different algorithm writes a new graph version next to the old one, so the two can be compared
  before switching.

Why one database and not two: forgetting has to reach the graph in the same transaction that removes the speech. With
two stores, forgotten speech would stay in the graph until a sync worker caught up. Provenance from an edge back to
its segments would become an application-level join across stores. And SQLite would give up pgvector, the roles and
the `pub` contract that Lattice and MCP read. The independence the two-store design wanted, rebuilding the graph
without touching the records, comes from the write rule above.

**Shape of the data:**

- Nodes are rows of `im.ideas`, one table, with `status` for the lifecycle. A provisional idea is a status, not
  another table.
- Edges are rows of `im.idea_links`. Its key is `(a, b, kind)` (`008_lattice.sql:39-48`), so there's at most one edge
  of each kind between two ideas. More support for an existing edge adds to its `evidence_item_ids` and raises its
  `confidence`; it doesn't add a parallel edge.
- An edge's properties are fixed columns (`kind`, `confidence`, `evidence_item_ids`, `method`). A new property is a
  migration. A free-form property bag is out.
- Direction is per kind. `supports`, `elaborates` and `evolves` point from `a` to `b`. `related` and `tension` are
  symmetric and stored once, with `a < b`. (On 2026-11-04, after B4's first sheet, `elaborates` replaced `extends`
  and `tension` replaced `contradicts`.)
- Graph-wide results (community, PageRank) are columns written per run and keyed by a graph version, the way map
  positions are today. Between runs they are stale.

**Shape of the software:**

- Traversal is Python. A stage or request loads the edge list into memory (NetworkX; 10,000 edges is a few
  megabytes) and works there. SQL holds only fixed-depth reads in `pub` views, such as an idea's direct neighbours.
- A lens is computed in Python (personalized PageRank from the seed) and cached in `im.lens_cache` by seed and graph
  version (M5). Emanation shows lens projections, not live traversal.
- There's no ad-hoc graph query language. A question like "how is X connected to Y" becomes a function, then an MCP
  tool if it earns one.
- Every graph write shares a transaction with the provenance it cites, and forgetting
  (`pipeline._purge_tombstoned`) covers every graph table from its first migration.

## Consequences

- Given up: Cypher-style pattern queries, graph-native exploration tools, and cheap variable-depth paths at millions
  of edges. None is needed at ten thousand.
- Kept: one store, one backup, one role model, the `pub` contract, and provenance in a single transaction.
- **Look again** when any of these happens: edges pass 100,000; an uncached lens takes more than a second; or a third
  hand-written path query is needed. The edge table is already an edge list, so the way out is mechanical. Export it
  to a graph store as a read replica, with Postgres staying the source of truth.
