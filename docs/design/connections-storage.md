# Connections storage: one per pair, kinds on it, lineage apart

_2026-11-07. Status: built (migration 045). Storage for [0010](../decisions/0010-connections-kinds-lenses.md),
settled here as [SPEC §3](../SPEC.md#3-data-model) asks; the model that decides connections (Terra, M4) and the kind
passes are not part of it._

## Today

`im.idea_links` is keyed `(a, b, kind)` with kinds `related` (embedding neighbours: top 3 at cosine ≥ 0.70, rebuilt
every run), `evolves` (the matcher) and `archive` (the ChatGPT archive's `#n` cross-references). My links are not in
it: they are `link`/`unlink` events, added back as `mine` rows by `pub.connections` and `pub.card_links`. Every
reader counts rows, so one pair with `related`, `evolves` and `mine` counts three times, and nothing enforces
`a < b`, so a pair can also appear in both orders.

## Tables

- **`im.connections`**: one row per pair. `a < b`, both cascading from `im.ideas`; `method`, `confidence`,
  `cited_observation_ids`, `decided_at`. One method per pair. Embedding neighbours and the archive's
  cross-references stand in until a model decides the pair, and a model decision replaces them; the embedding
  rebuild deletes and writes only rows whose method is an embedding method, so it never overwrites the archive's or
  a model's. A model's `none`, which keeps a stand-in from returning, is M4's to store.
- **`im.connection_kinds`**: `(a, b, kind)` referencing a connection, cascading with it; `kind` is `supports`,
  `elaborates`, `tension` or `sympathy` (the vocabulary grows by migration); `direction` (`a>b`, `b>a`, `both`) for
  `supports` and `elaborates`; `method`, `decided_at`. Empty until a kind pass writes it.
- **`im.lineage`**: `(from_id, to_id, kind)` with `kind` `evolves` or `synthesizes`, directed: `evolves` from the
  new idea to the one it evolves, as the matcher writes it today; `method`, `cited_observation_ids`, `created_at`.
  Citations stay, so the uncited check covers lineage too.
- **`im.edge_methods`**: every method that has written a connection or a kind, with `seen_event_id`, the highest
  `pub.feedback_events.event_id` when it first wrote. Filled by a trigger, never by hand.

## My links and unlinks

My links stay events, as all my input does (SPEC §2), and the views read them at once. Per pair, my latest
`link`/`unlink` (highest `event_id`) decides:

- a **link** makes the pair a connection, `mine`, whatever else says;
- an **unlink** hides every stored connection on the pair whose method had already written when I unlinked
  (`seen_event_id` below the unlink's `event_id`). A method first used after it (a new question version, a new
  model, a new kind's pass) can connect the pair again, and I can unlink again. Embedding neighbours keep one
  method name across runs, so a rebuild doesn't bring a pair back.

## Archive cross-references

Connections, with method `chatgpt-archive`, symmetric, so mutual references become one. ChatGPT wrote them, under
the first Idea Machine's contract, not this one: they stand in, like embedding neighbours, until a model decides the
pair under 0010.

## Views and readers

| Reader | Today | After |
|---|---|---|
| `pub.connections` (Lattice) | a row per kind, plus `mine` | Same columns, so Lattice works unchanged: one row per shown connection (`kind` `related`, or `mine` for mine), plus `evolves` rows from lineage, which Lattice draws |
| `pub.card_links` | a row per kind, plus `mine` | One row per shown connection, `kind` `related` or `mine`, with `kinds` (text[]) |
| `pub.card_lineage` (new) | | Lineage rows between cards |
| `pub.cards.links` | rows | shown connections, lineage excluded |
| `lens.relevance` | 1 per row, both ways | 1 per connection plus each kind's fraction (0 for now); lineage followed both ways |
| `weight._signals` | rows to canonical ideas, all kinds | connections to canonical ideas, mine included, lineage excluded |
| `web.card`, `web.sky` | a line per kind; draws `evolves` | a line per connection with its kinds; lineage listed apart; the sky draws both |
| `digest`, `lattice.status` | read `pub.connections` | unchanged: they read it as Lattice does, a line per connection plus evolutions |
| `pub.idea_scores.developed` | `mine` rows | unchanged: `mine` stays a `kind` |
| `edge_audit.candidates`, `match_bench` | `evolves` from `idea_links` | from `im.lineage` |
| `citations`, `checks.UNCITED` | keyed `(a, b, kind)` | connections by pair; lineage by its key |

## Migration

One migration moves the rows and drops `im.idea_links`, with every reader moved in the same change:
`related` and `archive` rows become connections (pair ordered, both orders and both sources of a pair merged into
one, citations merged; an archive reference wins the pair over an embedding neighbour, since a rebuild would delete
the neighbour); `evolves` rows become lineage. Counts change where pairs were counted twice: that's the fix. The
methods already stored count as known before any unlink I made, so a pair I'd unlinked, which an embedding or the
archive still connected, stays hidden. Forgetting is unchanged: every new table cascades from `im.ideas`.
