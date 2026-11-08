# 0009: An edge is graded on whether it exists; its kind is a suggestion

_2026-11-04. Status: accepted. Changes B4's bar in [ROADMAP-v2.md](../ROADMAP-v2.md) and Jev in
[SPEC §5](../SPEC.md#5-edge-typing); my decision._

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
  exists, and not graded. _Amended by [0010](0010-connections-kinds-lenses.md) (2026-11-06): `related` is what an
  edge is, not a kind; kinds are optional, several may apply, and the vocabulary grows._
- **Tension and analogy are passes of their own**, each asking one question of pairs, with the model that answers
  it best. Their bars come when there are enough marked examples.
- `im audit-edges score` reports connection first, then tension and analogy, then the kinds as diagnostics, and
  lists a mark that isn't a label apart from pairs left blank.

## Consequences

- Terra is the edge model on this evidence (94–96% on connection, no invented edges, under $2 a month at today's
  volume). The next 150 pairs confirm or overturn that.
- Marking stays the same; the sheet still asks for the kind, which keeps the suggestions measurable.

## Amendment: agreement for structure, novelty for discovery (2026-11-04)

The point of Idea Machine includes surfacing what my own reading of the lattice wouldn't: I already know how my
ideas connect, so a system that agrees with me perfectly on everything adds nothing. Two kinds of relation are
scored two ways:

| Relation | It's for | Scored by |
|---|---|---|
| Links by content (B4) | The graph that lenses, the sky and weight read | Agreement with me |
| Themes I've named | How I group my own ideas | Agreement with me |
| Structural analogies, tensions, throughlines I haven't named | Showing me something | Novelty: `new`, `known` or `no` |

Discovery sheets (`im audit-analogies`, `im audit-tension`) ask whether a pair is worth seeing: `new` (it holds,
and I hadn't connected it), `known` (it holds, I had it), `no`. A proposer or filter is chosen for how much `new`
it brings, at a noise I'll tolerate, not for agreeing with me: a filter that drops what was new to me filters out
the point.

What the tension sheets showed (70 pairs, 2026-11-04): random neighbors 0 in 10, neighbors said far apart and
claims beside observations 1 in 10 each, Sonnet reading every idea 11 in 39. Checking Sonnet's proposals, the cheap
models did best (GLM-5.3-flash precision 0.55, recall 0.67; DeepSeek V4.1 Flash 0.57, 0.44) and the large ones
barely beat keeping everything (0.30), finding a pull almost everywhere. That was agreement; whether a filter is
worth having is decided on novelty, once those marks are `new` or `known`. Discoveries arrive as proposals I keep
or dismiss, like candidates ([0007](0007-neutral-proposals.md)); none becomes an edge without me.

Jev on both tension sheets, with each `yes` re-marked `new` or `known` (70 pairs, 14 tensions, 6 new; 2026-11-04):
no single question ranked my tensions above 0.80 AUC on both sheets, and the forms it decided by (contradiction,
change of mind, trade-off, competing pulls, the strongest counting) managed 0.71 and 0.74. Four wordings of
competing pulls correlate 0.7 to 0.9; their mean held 0.79 and 0.81, and ranked new tensions as well as known ones
(0.80, 0.79), where the best single wording favored known ones (0.77, 0.84). So Jev decides a tension by that mean
(`jev.QUESTION_SETS`, `pulls4`), read as a ranking: its scores run lower, and a cut-off waits for more marks. Every
chat-model checker dropped at least one new tension; Jev at 0.5 dropped none. What may tell new from known, on 14
pairs: new ones ran across subjects, over values, and between sides that could both be true; kept as probes.

Two more sheets of model-proposed pairs (2026-11-05) make four: 150 pairs, 28 tensions, 10 new. The mean ranked my
tensions above my no's at 0.78 (0.70 to 0.82 by sheet), above the old answer on every sheet. "Jev at 0.5 dropped
none" was two pairs on the line: new tensions score 0.34 to 0.64, most between 0.45 and 0.6, and over all four 0.5
kept 6 of the 10. So a tension is yes at 0.45 (`jev.CUT`): 52 flagged, precision 0.40, recall 0.75, 9 of 10 new;
0.4 kept no more and flagged 73. The cut was chosen on the first three sheets; the fourth's one new tension scored
0.64, so it agreed without testing the line. The mean ranks known tensions above new ones, and on the fourth sheet
scored two tensions between levels of a thing (a social risk against a technical one) under 0.4. Different
subjects marked a new tension on all four sheets and stays a probe; competing values held on two, so `probes4`
dropped it.
