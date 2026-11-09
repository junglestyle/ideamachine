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

- **A query:** whether a cluster fits the lens's meaning (see "A query is pluggable" below). Or `all` (no meaning
  test: the filters decide) or `none` (only the pins).
- **Metadata filters:** conditions on stored facts, not meaning: sensitivity tags, maturity, how I kept it, recency,
  recurrence, arousal, who said it. They narrow what the query admits.
- **Pins:** clusters I include or exclude by hand. A pin beats the query and the filters: an included pin is in, an
  excluded one out, whatever they say. Demo mode still hides a sensitive pin.

A cluster is in a lens if it's pinned in, or it passes the query and the filters and isn't pinned out.

**Viewing from a cluster isn't a lens.** Lighting the sky from one cluster (`lens.relevance`) is a view from that
cluster's perspective. It isn't stored and stays as it is. A lens I pin lights the sky the same way every time,
which is what gives Emanation stable pins.

**A query is pluggable.** A theme may need one or two questions; Kether, ten or twenty. So a lens holds a query of
some kind, and the lens only needs its answer. Every kind offers the same four things:

- `version`: a hash of the query's definition. Changing it asks again; the same version reuses what's been answered.
- `candidates`: which clusters are worth asking. Every cluster by default; a kind may narrow it.
- `judge`: for each cluster asked, in or out, a score, and a short reason, so "why is this here" still has one answer.
- `describe`: what a view shows of it: the definition, or a summary of a long query.

A query is stored as data, not code: its kind and that kind's parameters, each kind a class in the code. The first
kinds are `all`, `none` and `jev`: a set of questions Jev answers in one request per cluster, a rule combining them
("any two at 0.7", "the mean of these four"), and the query's own cut. Other kinds (a chat model with a rubric, an
embedding threshold) are added as code when needed; nothing stored runs as code. A query with a question or two can
be made and edited in a view; a long one is built offline (a model drafts it from a correspondence table, I edit it,
and a short marked sheet sets its cut, as for the gate) and imported from a file.

**How much is asked is a choice at run time.** While I'm working on a lens, a run can ask only the clusters nearest
its definition by embedding, up to a maximum (on by default). A run without the maximum asks the rest: answers are
kept by the query's version and each cluster's text, so nothing already answered is asked again. A lens says how
much of the map it has judged ("judged 300 of 3,100"), so a cluster not yet asked isn't mistaken for a no.

**Follow and depth are choices of the view, not of the lens.** Membership stays the lens's alone; how much
around it a view shows is set when I look:

- **Follow:** off shows only the lens's members. On spreads from all of them over connections, fading with
  distance: the view from a cluster, generalized to a set. It follows every kind of connection, and lineage.
- **Depth:** a view can show only what's within some number of steps of its focus, whether the focus is a lens's
  members or one cluster ("two steps from here").
- **What a view must keep apart:** members from what's reached by following, and the kinds of connection, as a view
  can draw them. How is the presentation's choice.
- **Demo mode beats every other choice.** A sensitive cluster reached by following, within the depth, or pinned is
  still blanked or left out, as demo mode decides for that view.

**Themes are lenses.** A theme's name and description become a lens's query, and my filing corrections (file,
unfile) become its pins. Theme is no longer a concept of its own (as 0010 planned).

**Lenses are flat.** A lens never refers to another lens: its members depend only on each cluster's own text, facts
and my pins. So each lens is worked out on its own, cached, and redone when one cluster changes; "why is this here"
has one answer (the query, a filter, or a pin); there are no cycles; and a lens costs its own query per cluster,
however lenses are combined.

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
- Storage of each query as its kind and parameters, with its version; a `jev` query's questions, combining rule
  and cut. `im lens import FILE` for queries built offline.
- A lens's first run over an archive of a few thousand clusters costs cents with Jev; its cost is time (a request
  per cluster), which the maximum asked trims while I work on it. Later runs ask only new or changed clusters.
- A model drafts definitions and questions from a correspondence table or a theme, for me to edit. A query's cut is
  set from marks where it matters (a long query like Kether), and may stay at a default for a one-question theme.
- `lens.relevance` grows from one cluster to a set, for follow; views gain a depth limit.
- Not decided: the default maximum asked, how a board is drawn, and how each view draws members, followed clusters
  and connection kinds.
