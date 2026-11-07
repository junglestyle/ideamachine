# 0010: Connections, their kinds, and lenses over both views

_2026-11-06. Status: accepted, not built yet. Amends [0005](0005-presentation-inside-idea-machine.md)'s one
coordinate space, [0006](0006-graph-in-postgres.md)'s edge key, [0009](0009-edges-connection-graded-kind-suggested.md)'s
list of kinds, and what triggers synthesis ([0007](0007-neutral-proposals.md)); my decision. Amended 2026-11-07:
an analogy is a lens._

## Context

- **B4 with Jev (2026-11-06, 129 pairs):** no cut-off of the three connection wordings, alone, in pairs or averaged,
  reached both bars (best: agreement 0.891, precision 0.878). The sheet defines `related` as "the same subject, but
  none of the above", and Jev's first wording counts a shared subject as a connection; my notes on `none` marks say
  why it isn't one ("topical only", "both about AI coding, that's all"). A shared subject is what a lens gathers; an
  edge has to say more.
- **`related` meant two things.** In the graph it's an embedding neighbour (cosine ≥ 0.70, top 3,
  `lattice.relate`); on the B4 sheet it's a label beside `supports` and `tension`. Neither is "these two distinct
  points belong in the same tight neighbourhood", which is what edges are for: they shape clusters and what's
  proposed.
- **Kinds are stored as separate edges.** `im.idea_links` is keyed `(a, b, kind)`, and the lens
  (`lens.relevance`), the card's edge count (`pub.cards.links`) and weight (`weight._signals`) count rows. A second
  kind on a pair would read as a second connection: it pulls a lens harder without anything new being known.
- **Reviewing the views** (the sky, `im web`; Lattice v2 in [ROADMAP-v2 §4](../ROADMAP-v2.md#4-views-and-filters-planned-2026-11-04)):
  Lattice's bridges are ideas that touch two or more themes (by membership, or a link across), which Emanation's
  notes mixed up with proposed connections; and a hand-made view and a computed one can't share one map.

## Decision

**A connection is an edge, pure and simple.** Two distinct points are related: they belong in the same tight
neighbourhood, beyond sharing a subject. One per pair, symmetric, with its confidence, cited observations and
`method` (model and question version). Until a model decides them (M4: Terra, which passed B4), embedding neighbours
stand in, named by their `method`.

**Connections promote.** What V1 asked me to do by hand (keep/discard, comparisons, the rubric, all thrown out by
0007) is what connections do instead: a neighbourhood of connected observations is what triggers a synthesis, the
other trigger 0007 left synthesis waiting for. To watch: broad ideas collect connections to everything, so a
neighbourhood held together only by one of them isn't a neighbourhood.

**Kinds are optional annotations on a connection**, from a vocabulary that grows: today `supports`, `elaborates`,
`tension` and `sympathy` (the two resonate, or pull the same way). Later ones (`antipathy`, `disproves`) are added
when they're worth a pass. A kind brings its question, its direction (`supports` and `elaborates` point from one to
the other) and how it's checked; adding one changes no other kind, no connection, and nothing already extracted. A
connection may carry several kinds, or none. `related` is no longer a kind: it's what a connection is, and one with
no kind is simply related. There's no untyped kind.

**A lens is the top-level way in.** Its seed can be anything: an idea, a subject, a sentence, a question, a
correspondence. It returns how relevant every cluster is, and writes no connections: being relevant to a lens isn't
a relationship between two ideas. Themes become subject lenses rather than a concept of their own. I pin a lens to
keep it at hand; one I leave is archived, never thrown away, with its definition and what was paid to compute it, and
the graph version it read. Reopening reuses that; refreshing it is a separate act.

**An analogy is a lens, not a connection.** Analogous ideas are about different subjects, so they're far apart by
definition; a connection between them would pull distant clusters together in Emanation's layout and join
neighbourhoods that aren't one. What an analogy gives is its shared structure, named ("easy against perfect", "bad
work blamed on its maker's state"), and a structure has as many instances as there are, not two. So the analogy pass
(0009's, Jev's different-subjects and same-structure questions) proposes a pair and names what it shares; one I keep
becomes a pinned lens seeded with that structure, whose results are its other instances, and which Lattice v2 can
place like any lens. It writes no connections. Until lenses are built, analogous pairs on B4 are marked `none`, with
a note starting `analogy:` that names the structure: those notes are the first seeds.

**Strength is counted per connection, not per row.** A lens and weight read each connection as 1. Each kind may add
a fractional weight (its specificity), set per kind and versioned like the weight formula; it's 0 until something
shows a kind should pull harder. Adding a kind never adds a connection.

**Two families of views, both drawn through lenses:**

- **Emanation** is fluid: association and promotion. Its sky need not stay put between runs; a stable global map is no
  longer a requirement. The home sky lights a lens's results where they are. Opened on its own, a lens lays its results
  out by their connections (a force layout, started from where its last run left them, so it doesn't jump), and draws
  each neighbourhood as a cloud with its synthesis proposal over it, to keep or dismiss.
- **Lattice v2** is the stable one: a spatial arrangement of pinned lenses that I lay out. The Tree of Life is the
  first: a correspondence table, condensed (once, paid) into definitions I can read and edit, each correspondence a
  lens (1/Kether), the paths too. Placing a lens on the Tree is organization: it creates no connections between the
  ideas it shows.

**Promotion can synthesize.** A neighbourhood of connected observations can be proposed as an idea that a synthesis
writes, which may name several kinds at once (a sympathy and a tension, say). The synthesis points to each point it
was made from (`synthesizes`), as lineage, like `evolves`, not a connection: a lens follows it both ways, weight
doesn't count it (a synthesis would otherwise gain weight from the neighbourhood it was made from), and it adds no
connection between its sources, which are connected already. Whether a synthesis beats proposing the neighbourhood's
best existing point is an A/B test on the same neighbourhoods, judged blind. Either way only I keep an idea.

**A bridge is an idea, not a proposed connection.** As in Lattice v1, a bridge idea touches two or more lenses (by
belonging to both, or by connections across). A connection the system suggests and I haven't made is a proposal, drawn
as one, and becomes a connection only when I keep it.

## Consequences

- B4 asks whether two points belong in the same tight neighbourhood, not whether they share a subject: the sheet's
  `related` and Jev's wordings change to that, and my `related` marks are re-read under it before Jev is tuned
  against them. Kinds stay ungraded.
- Storage: connections keyed by pair, kinds in rows of their own that reference a connection, and lineage
  (`evolves`, `synthesizes`) apart from both; `pub.card_links`, `pub.cards.links`, `lens.relevance` and
  `weight._signals` read connections. A migration, with every reader moved in the same change.
- Lenses get a table of their own (seed, definition, pinned or archived, results by graph version), replacing
  `im.lens_cache` in [SPEC §3](../SPEC.md#3-data-model); themes migrate into it as subject lenses.
- Embeddings stay what nominates pairs and seeds a lens's search, never what decides a connection.
- Synthesis is triggered by connected neighbourhoods, not weight; the M2 design's "bridging" synthesis is this,
  and isn't a bridge idea.
- The analogy pass feeds lenses: a kept analogy is a lens seeded with its named structure, not a kind on a
  connection.
