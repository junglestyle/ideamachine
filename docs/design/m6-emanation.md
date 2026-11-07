# M6 design: Emanation (notes)

_2026-11-02. Status: notes, not a design yet. Milestone M6 in [ROADMAP-v2.md](../ROADMAP-v2.md). Collected from a
conversation about visualizing the lattice (night sky, river, strata, and others); what I agreed with, and where it
meets decisions already made. Nothing here is decided until the design is written._

## Direction

Emanation is one temporal, semantic graph explorer with several visual encodings, not several visualizations. The
data has six dimensions (semantic, topological, temporal, lifecycle, activity, provenance); each encoding picks a few
to show rather than all six at once.

For M6:

1. **A sky as the home view.** Stars are clusters, placed by semantic neighbourhood. ~~The layout updates
   incrementally and stays put.~~ Not required since [0010](../decisions/0010-connections-kinds-lenses.md): Emanation
   is the fluid view, and Lattice v2 (pinned lenses laid out by hand) is the stable one.
2. **Lenses as zoom, not separate pages.** Subjects → clusters → candidates → observations → the original
   utterances. On the home sky a lens lights its results where they are; opened on its own, it lays them out by their
   connections (a force layout started from its last run's positions) and draws each neighbourhood as a cloud with
   its synthesis proposal over it ([0010](../decisions/0010-connections-kinds-lenses.md)).
3. **Proposed connections as faint dashed links.** The "negative space": connections the evidence suggests that I
   haven't made. These are hypotheses, never edges, until I keep them. Not to be confused with a bridge idea, which
   is an existing idea that touches two or more lenses (as Lattice v1's bridges touch two themes).

**2D, not 3D** (2026-11-04). Depth hides neighbours and makes you orbit to find them; motion (replay, new evidence
arriving, the camera flying to a lens) earns more than a third axis of meaning. A faster renderer (WebGL) doesn't
need 3D positions, and changing the pipeline's stable positions to 3D later would reset where everything is.

## Encodings

| Channel | Shows | Why it's safe |
|---|---|---|
| Sharpness | How recently it was said | A fact |
| Brightness | Recurrence (distinct conversations), decaying with time | A fact |
| Size | Lifecycle, and canonical vs pinned | My decision |
| Colour | Semantic neighbourhood | Descriptive |
| Links | Connections; their kinds and cited observations on inspection | Evidence-backed |

**No significance channel.** [Decision 0007](../decisions/0007-neutral-proposals.md) closed M3 because no signal
predicted what I keep, so weight orders nothing. Encoding weight as brightness or size would put that ranking back
in visually.

## The negative-space map is synthesis, drawn

It doesn't need a new subsystem. The spec already has the parts: embeddings nominate pairs, the edge model answers
`none` as a real answer ([SPEC §5](../SPEC.md#5-edge-typing)), and the synthesis stage writes an idea from a neighbourhood
of connected clusters, which arrives as a proposal (whether that beats proposing its best existing point is an A/B
test, [0010](../decisions/0010-connections-kinds-lenses.md)). The dashed links are:

- connections proposed and not yet decided, and
- possibly pairs that are close by embedding where Jev answered `none` or fell below the confidence floor.

Clicking one shows the cited utterances on both sides. Keeping a proposed connection makes it a connection; keeping
a synthesis makes it an idea, linked to what it was made from.

## Conflicts with decisions already made

- ~~[Decision 0005](../decisions/0005-presentation-inside-idea-machine.md) drops global positions for lens
  layouts.~~ Resolved by amending 0005 (2026-11-02): positions stay in the pipeline, a lens returns relevance, and the
  home sky is the lens with no seed.
  Amended again by [0010](../decisions/0010-connections-kinds-lenses.md) (2026-11-06): the one map need not stay put.

## First sky (`im web`, `/sky`)

- Every card at its pipeline position (`pub.card_positions`; the pipeline places every card, not only Lattice's).
- Brightness: distinct conversations. Sharpness: days since last said (older is softer and wider). Size: lifecycle
  (observation, candidate, pinned, canonical). Colour: the direction from the map's centre, so neighbours share a
  hue. Edges: faint, every kind.
- `/sky?seed=<id>` is a lens: personalized PageRank from the seed over edges, plus the seed's nearest by meaning
  (`lens.relevance`). Unlit clusters dim; the camera frames the lit ones.
- Tap a star for its title and facts, then open its card or make it the lens. Drag to pan, pinch or scroll to zoom.

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
