# M6 design: Emanation (notes)

_2026-11-02. Status: notes, not a design yet. Milestone M6 in [ROADMAP-v2.md](../ROADMAP-v2.md). Collected from a
conversation about visualizing the lattice (night sky, river, strata, and others); what I agreed with, and where it
meets decisions already made. Nothing here is decided until the design is written._

## Direction

Emanation is one temporal, semantic graph explorer with several visual encodings, not several visualizations. The
data has six dimensions (semantic, topological, temporal, lifecycle, activity, provenance); each encoding picks a few
to show rather than all six at once.

For M6:

1. **A stable global sky as the home view.** Stars are clusters, placed by semantic neighbourhood. The layout updates
   incrementally: existing stars stay roughly where they were, and new ones land near their neighbours. If the whole
   sky rearranges after a run, I lose my sense of where things are.
2. **Lenses as zoom, not separate pages.** Themes → clusters → candidates → observations → the original utterances,
   all in the same space. A lens focuses the sky from a seed instead of replacing it.
3. **Bridge proposals as faint dashed links.** The "negative space": connections the evidence suggests that I haven't
   made. These are hypotheses, never edges, until I keep them.

## Encodings

| Channel | Shows | Why it's safe |
|---|---|---|
| Sharpness | How recently it was said | A fact |
| Brightness | Recurrence (distinct conversations), decaying with time | A fact |
| Size | Lifecycle, and canonical vs pinned | My decision |
| Colour | Semantic neighbourhood | Descriptive |
| Links | Typed edges; type and cited observations on inspection | Evidence-backed |

**No significance channel.** [Decision 0007](../decisions/0007-neutral-proposals.md) closed M3 because no signal
predicted what I keep, so weight orders nothing. Encoding weight as brightness or size would put that ranking back
in visually.

## The negative-space map is synthesis, drawn

It doesn't need a new subsystem. The spec already has the parts: embeddings nominate pairs, Jev answers `none` as
a real answer ([SPEC §5](../SPEC.md#5-edge-typing-jev)), and the synthesis stage bridges clusters into a candidate
that arrives as a proposal. The dashed links are:

- bridge proposals I haven't decided on yet, and
- possibly pairs that are close by embedding where Jev answered `none` or fell below the confidence floor.

Clicking one shows the cited utterances on both sides. Keeping it goes through the normal proposal flow.

## Conflicts with decisions already made

- **[Decision 0005](../decisions/0005-presentation-inside-idea-machine.md)** says map positions leave the pipeline
  once lenses replace the global map, and the force map is fed a lens's `{nodes, links}`. A stable sky needs a global
  layout that persists between runs. Lens projections stay relative to their seed. Resolve this before the design:
  amend 0005 so a persisted, incrementally updated layout is kept, either in the pipeline or as a `pub` view.

## Later encodings (not M6)

- **Mycelium:** growth and lifecycle. More grounded than it looks, because utterance → observation → candidate → idea and
  `evolves` edges are real lineage. Only links between clusters must not imply causation.
- **Seismograph:** periods when the landscape changes quickly (novel regions, sudden cross-links, new
  contradictions). Call them events, not discoveries.
- **Weather:** attention as moving systems over semantic space; replay a month in thirty seconds.
- **Double exposure:** then vs now. The ledger and the edges' `method`/model version mostly record when something
  was inferred. **Check first:** if embeddings get recomputed, past positions can't be rebuilt honestly, and
  relationships inferred in November mustn't be replayed into July.
- **River, strata, orrery:** attention trends, persistent preoccupations, canonical ideas as gravity (an alternative
  model view, not the default).
