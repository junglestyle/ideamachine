# 0009: An edge is graded on whether it exists; its kind is a suggestion

_2026-11-04. Status: accepted. Changes B4's bar in [ROADMAP-v2.md](../ROADMAP-v2.md) and Jev in
[SPEC §5](../SPEC.md#5-edge-typing-jev); my decision._

## Context

B4's first sheet: 50 pairs, marked by me, drafted blind by ten models, twice for the four contenders (labels 2,
after `extends` became `elaborates` and `contradicts` became `tension`).

- **Whether two ideas are connected, the best models nearly match me.** Counting any label but `none` as an edge:
  Opus 48 of 50 (all 29 of my `none`), Terra 47 of 50, never inventing one.
- **Which kind, nobody does.** I gave `elaborates` 5 times and `tension` twice. Opus got 1 and 0, Terra 1 and 0.
  GLM-5.3 did best (3 and 1) while linking 15 of my 29 `none`. Where models said `elaborates`, I mostly said
  `related`, and I moved one pair across that line myself. Macro-F1 over the kinds topped out at 0.49.
- **The kinds are rare.** Even at 200 pairs there would be about 20 `elaborates` and 8 `tension`: too few for an
  F1 to mean much, so a bar of macro-F1 ≥ 0.70 over them is neither reachable nor informative.
- **Tension and analogy are different questions.** The models that find them (GLM-5.3 one of my two tensions;
  Sonnet all seven of my analogies) are the ones that over-link as edge typers.

## Decision

- **The graph uses whether there's an edge.** Jev answers connected or `none`; lenses, the sky and weight read only
  that. B4's bar: agreement ≥ 0.90 on connection, and ≥ 0.90 of a model's edges ones I'd make.
- **The kind is a suggestion** (`supports`, `elaborates`, `related`), shown on the edge, never deciding whether it
  exists, and not graded.
- **Tension and analogy are passes of their own**, each asking one question of pairs, with the model that answers
  it best. Their bars come when there are enough marked examples.
- `im audit-edges score` reports connection first, then tension and analogy, then the kinds as diagnostics, and
  lists a mark that isn't a label apart from pairs left blank.

## Consequences

- Terra is the edge model on this evidence (94–96% on connection, no invented edges, under $2 a month at today's
  volume). The next 150 pairs confirm or overturn that.
- Marking stays the same; the sheet still asks for the kind, which keeps the suggestions measurable.
