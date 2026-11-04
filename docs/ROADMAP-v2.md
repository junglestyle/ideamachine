# Idea Machine V2: roadmap

_Status: plan, 2026-10-27. The order V2 gets built in, and the bar each milestone has to clear. What V2 is meant to
do is in [SPEC.md](SPEC.md). Each risky milestone gets its own design doc before it's built._

## 1. Milestones

Each one leaves the system working and Lattice usable until M6.

| M | Goal | Done when | Design |
|---|---|---|---|
| M0 | Freeze V1 | `v1` tagged on all three repos; Lattice feature-frozen; [decision 0005](decisions/0005-presentation-inside-idea-machine.md) | done |
| v1.1 | Stop re-spending on unchanged payloads | A naming change sends nothing to Claude and re-matches nothing ([#10](https://github.com/junglestyle/ideamachine/pull/10)) | done |
| M1 | Stable segment identity | Naming produces no new segments or episodes, and no requests unless the pseudonymized payload changed; extractions per current episode ≤ 1.05; every verdict reachable, every item cites current segments, `pub.ideas` unchanged | [design](design/m1-segment-identity.md) |
| M2 | Observations, clusters, candidates, weight; ideas I keep (canonical or pinned); proposals; edge citations | B5 passes; every cluster weighted; `pub.ideas` returns the same ids; Lattice works unchanged | done 2026-11-01 ([design](design/m2-observations-candidates-ideas.md)) |
| Synthesis | A stage triggered by weight: consolidates a cluster's statement or bridges several into a candidate, citing observations; arrives as a proposal | Versioned and capped; I keep a fair share of what it proposes | after M2 |
| M3 | ~~Tune weight and proposals~~ No rating: proposals neutral, newest first | Closed without tuning: no signal predicted what I keep | [0007](decisions/0007-neutral-proposals.md) |
| M4 | Jev for match decisions and edge typing, behind versioned `judged_by` | B1 and B4 pass; no rise in duplicate ideas | at M4 |
| M5 | Lenses; MCP server moves here | Connector works unchanged against the new endpoint; 5 canonical lenses judged useful | at M5 |
| M6 | Emanation UI; cut over; archive Lattice | Parity checklist from Lattice's tools and views; a week of use without falling back | at M6 |
| M7 | Local titles | B6 (local titles via Corral) passes | at M7 |

~~**Hold:** Hearsay's audio retention stays off until M2 validates. Raw audio is the rollback for the re-link.~~ Lifted 2026-11-01: M2 validated, and nothing in Idea Machine needs the audio.

## 2. Benchmarks

Each runs on data that already exists and records model and prompt versions.

| # | Decision | Pass bar |
|---|---|---|
| B1 | Match: `new` vs `same_as`/`evolves`, against 1,131 logged Opus decisions after dedupe | ≥ 90% agreement; `same_as` precision ≥ 0.95 on the operator-validated subset (the real bar: agreement with Opus only shows a cost swap is safe) |
| B2 | ~~Keep/discard prior~~ retired 2026-10-28: no keep/discard | — |
| B3 | Extraction model swap (`im trial`). Done 2026-10-28: Opus stays (Sonnet 82% recall at 42% cost; Haiku lost the blind slug check). Rerun 2026-11 on `v3` under policy C across providers, recall against Opus's captures on a fixed sample rather than my verdicts ([0008](decisions/0008-other-providers-and-names.md)) | Recall ≥ Sonnet's, then candidate's slugs preferred or tied ≥ 60% of 30 blind pairs |
| B4 | Edge typing, 200 labelled pairs (`im audit-edges`), marked by me, blind. The pilot (2026-11-02, 20 pairs, model-marked so not counted) kept all five answers, folded `evolves` into `extends`, and found no `contradicts` or `supports` among nearest neighbours, so sheets also draw ideas said far apart and claims beside concrete observations. The first sheet (2026-11-04, 50 pairs, ten models drafting) found no contradictions but two tensions, and every model reading my `extends` as `related`: labels 2 rename `extends` to `elaborates` (a later version stays `evolves`) and replace `contradicts` with `tension`, which includes it. Terra and Opus agreed with me most (80%); the next 150 pairs decide After it, decision [0009](decisions/0009-edges-connection-graded-kind-suggested.md): the top models agree with me 94–96% on whether there's an edge and far less on which kind, so the edge is graded and the kind only suggested; tension and analogy get passes of their own | Connection: agreement ≥ 0.90, and ≥ 0.90 of a model's edges ones I'd make (precision). Tension and analogy reported, bars once there are enough of them |
| B5 | Dedupe migration, operator audit of 50 groups and 50 near misses (`im audit-dedupe`). First sheet 2026-11-01, stopped at 26 merged: 2 false merges, both chained through a quote stitched with "..."; an item now joins only if it matches every capture. **Passed 2026-11-01** on a fresh sheet: 1 false merge in 50; 5 false splits in 50 near misses, so 0.4 stays | ≤ 2 false merges in 50 |
| B6 | Local titles | Local preferred or tied ≥ 60% in 50 blind pairs |

## 3. Cost

Spend is about $25 a month: extraction 58%, matching 25%, all on Opus 5.5. That's low, so V2 doesn't trade
correctness or simplicity for a few dollars. It matters at 5–10× capture, where the lever is a cheaper extraction
model (B3; Opus stayed in 2026-10).

## 4. Open questions

1. ~~Does re-transcription of growing conversations dominate repeat extraction?~~ No (2026-10-27): of $14.89 spent
   on extraction, re-transcription was 12% and none of it was speech first sent while its conversation was open,
   so M1 doesn't hold extraction. Repeats of an identical payload were 18% (v1.1 skips them) and naming that
   changed the pseudonymized payload 35%.
2. ~~What N makes two observations in one conversation independent?~~ 30 minutes, an operator setting (2026-10-28).
3. ~~Which statuses can the operator set directly?~~ Only I make ideas, kept as canonical or pinned; a star is a pin (2026-10-28).
4. ~~My point and someone else's: one cluster or two?~~ One: theirs joins mine as a not-me member (2026-10-28).
5. ~~Digest points or a synthesis stage?~~ A stage of its own, triggered by weight, after M2 (2026-10-28).
6. ~~Widening extraction to neutral observations (questions, jokes, passing thoughts): yes, after M2; when?~~ Now
   (2026-11-02): prompt `v3`. A re-read's captures of speech already captured join their observation's cluster
   without asking the matcher, so it costs about one extraction pass. Trial it, then switch `extract.prompt`.
7. ~~Does Emanation keep a stable global layout?~~ Yes (2026-11-02): one sky with stable positions, and a lens
   returns relevance, not a layout; the home sky is the lens with no seed
   ([0005, amended](decisions/0005-presentation-inside-idea-machine.md#amendment-one-sky-lenses-light-it-2026-11-02)).
