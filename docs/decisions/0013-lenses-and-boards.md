# 0013: A lens is a query, filters and pins; boards group lenses

_2026-11-09. Status: accepted, not built yet (M5). Amends [0010](0010-connections-kinds-lenses.md) (what a lens is,
and its seed) and SPEC §2-3 (lenses); my decision._

## Context

- [0010](0010-connections-kinds-lenses.md) made the lens the top-level way in, "seeded by anything", and planned a
  table for lenses that themes migrate into. What a lens stores, and how its members are found, wasn't settled.
- Today a lens is only computed on the fly from one cluster (`lens.relevance`: personalized PageRank from the seed
  over connections, plus its nearest by meaning), which is how the sky lights "from here".
- The rebuild (0012) is a fresh database: every cluster and observation id changes once. Anything stored against
  those ids breaks at the cutover.
- Trellis needs lenses I can lay out by hand, the first being the Tree of Life: a correspondence table, each
  correspondence condensed into a definition.

## Decision

**A lens is a stored question about clusters, in three parts:**

- **A query:** a definition in words, and Jev's yes or no on whether a cluster fits it. Or `all` (no meaning test:
  the filters decide) or `none` (only the pins).
- **Metadata filters:** conditions on stored facts, not meaning: sensitivity tags, maturity, how I kept it, recency,
  recurrence, arousal, who said it. They narrow what the query admits.
- **Pins:** clusters I include or exclude by hand. A pin beats the query and the filters: an included pin is in, an
  excluded one out, whatever they say. Demo mode still hides a sensitive pin.

A cluster is in a lens if it's pinned in, or it passes the query and the filters and isn't pinned out.

**Viewing from a cluster isn't a lens.** Lighting the sky from one cluster (`lens.relevance`) is a view from that
cluster's perspective. It isn't stored and stays as it is. A lens I pin lights the sky the same way every time,
which is what gives Emanation stable pins.

**Themes are lenses.** A theme's name and description become a lens's query, and my filing corrections (file,
unfile) become its pins. Theme is no longer a concept of its own (as 0010 planned).

**Lenses are flat.** A lens never refers to another lens: its members depend only on each cluster's own text, facts
and my pins. So each lens is worked out on its own, cached, and redone when one cluster changes; "why is this here"
has one answer (the query, a filter, or a pin); there are no cycles; and the cost is one Jev question per cluster
per lens, however lenses are combined.

**A board groups lenses.** A board is a named set of lenses, with positions where I've placed them, and one level
only: a board holds lenses, never boards. A Trellis view draws one board; the Tree of Life is a board of 32 lenses
(the ten sephirot and the 22 paths). Combining lenses happens on a board when it's viewed: the union of its lenses,
or two picked and intersected. A filter can be set on a board to apply to all its lenses. A combination worth keeping
becomes a lens with a more specific query.

**A lens survives the rebuild.** Its query and filters are text and rules. A pin is anchored to the speech, not to
ids that change: the segments of the capture I pinned (Hearsay's segment ids, stable since M1), or for an archive or
typed observation its source reference. A pin resolves to whichever cluster holds that speech now, before the rebuild
and after it.

## Consequences

- Storage at M5: lenses (query or `all`/`none`, filters, pinned or archived), their pins (anchored to speech, in or
  out), boards and their placed lenses, and each lens's answers cached by the cluster's text and the lens's version.
  It replaces SPEC §3's planned `im.lens_cache`; themes migrate into it.
- Jev answers a lens's query per cluster, asked only of the clusters nearest the definition by embedding, so a lens
  over thousands of clusters costs hundreds of questions, not thousands. How many to ask, and the cut, are set from
  a first few lenses checked by hand.
- A model drafts definitions from a correspondence table or a theme, for me to edit.
- Not decided: the cut, how many nearest clusters are asked, and how a board is drawn.
