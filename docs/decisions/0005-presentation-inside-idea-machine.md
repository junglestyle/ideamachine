# 0005: Presentation moves inside Idea Machine

_2026-10-27. Status: accepted, not built yet (V2 milestones M5–M6). Reverses ROADMAP §1's non-goal "a UI inside
Idea Machine" and changes §3.5; my decision._

## Context

V1 split the system along the `pub` schema: Idea Machine owns the truth and publishes views, and Lattice
(a separate repo) reads them as `lattice_app` and only appends feedback. The privacy property was worth having: the UI
can never read `im.*`.

What the seam cost, measured in the V2 architecture review:

- **Domain logic ended up in the consumer, twice.** Lattice computes which ideas bridge themes
  (`lattice/src/lattice/data.py:110-127`), which bulk-kept captures to look at again (`data.py:130-158`), which
  captures are spot checks (`data.py:261-269`) and the fallback comparison pairs (`data.py:161-191`). Two of those
  are hand-copied again in `index.html` (`:1456`, `:951`), and Lattice's own standards list four hand-synchronized
  duplicates.
- **Presentation ended up in the pipeline.** Idea Machine computes global 2D map positions (`src/im/lattice.py:310-380`,
  migration 015) because the views-only seam gave Lattice no way to do it.
- **V2's lenses don't fit a view.** A lens is a per-query projection (personalized PageRank over evidentiary edges
  plus embedding similarity). It needs the graph code, edges and embeddings that live in the pipeline, and its
  relevance is relative to the seed, so it's neither a global snapshot nor a fixed position.

## Decision

- Idea Machine gets a web package, `src/im/web/`: one HTTP app serving `/api/*` (lens projections, proposals,
  feedback), `/mcp`, and **Emanation**, the static page that replaces Lattice's UI.
- **The MCP server moves in with its tool names, argument schemas and result shapes byte-compatible**
  (`lattice/src/lattice/mcp.py`, `TOOLS`), and keeps speaker pseudonymization in every answer and the public
  listener's OAuth behaviour. It's a live claude.ai connector, so renaming a tool is a breaking change. New tools are
  additions only (`open_lens`). _Amended 2026-10-29: the contract is the 19 tools after lattice#10, which retired
  the seven review and rating tools (M2 reads no verdicts) and added `proposals` and `keep_idea`._
- **Domain logic moves into pipeline stages or `pub` views**: bridges.
  The page renders; it doesn't decide.
- **The role boundary stays.** The web process connects as a read-mostly role (read `pub`, insert into
  `pub.feedback_events`), never as `im_pipeline`. All human input stays feedback events.
- From Lattice, Emanation ports the offline feedback queue and sync, the review card and decide flow, the comparison
  flow, and the force-map renderer (fed a lens's `{nodes, links}` instead of a global snapshot). The rest is reachable
  through MCP first and gets ported only if it's missed.
- **Lattice is feature-frozen from tag `v1`** (2026-10-27). Fixes only, until cutover. After a week of using
  Emanation without falling back, it's archived with a README saying what it proved and where its ideas went.

## Consequences

- One repo holds the pipeline and its presentation, so each domain rule has one implementation, in Python, that
  the CLI, API and MCP share (the org standard's "one code path").
- Map positions leave the pipeline once the lens view replaces the global map.
- The migration order keeps Lattice working throughout: `pub` view shapes stay the same until M6, and Lattice's MCP
  server stays up next to the new one until the connector has been verified against the new endpoint.
- Rollback until archive: restart the Lattice container against `pub`.
