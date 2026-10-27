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
| M2 | One observation per capture; status, promotions, edge evidence | B5 passes; `pub.ideas` returns the same ids; Lattice works unchanged | at M2 |
| M3 | Promotion rule as a run stage | Operator reviews 50 promotions via comparison; agreement ≥ 75% | at M3 |
| M4 | Jev for match decisions and edge typing, behind versioned `judged_by` | B1 and B4 pass; no rise in duplicate ideas | at M4 |
| M5 | Lenses; MCP server moves here | Connector works unchanged against the new endpoint; 5 canonical lenses judged useful | at M5 |
| M6 | Emanation UI; cut over; archive Lattice | Parity checklist from Lattice's tools and views; a week of use without falling back | at M6 |
| M7 | Cheaper extraction and titles | B3 (Sonnet extraction) and B6 (local titles via Corral) pass; cost per retained idea in `pub.spend` | at M7 |

**Hold:** Hearsay's audio retention stays off until M2 validates. Raw audio is the rollback for the re-link.

## 2. Benchmarks

Each runs on data that already exists and records model and prompt versions.

| # | Decision | Pass bar |
|---|---|---|
| B1 | Match: `new` vs `same_as`/`evolves`, against 1,131 logged Opus decisions after dedupe | ≥ 90% agreement; `same_as` precision ≥ 0.95 on the operator-validated subset (the real bar: agreement with Opus only shows a cost swap is safe) |
| B2 | Keep/discard prior, for review order only | Discard AUC ≥ 0.75 |
| B3 | Extraction model swap (`im trial`, 51 judged episodes) | Keep recall ≥ 90% of Opus; cost ≤ 35% |
| B4 | Edge typing, 200 labelled pairs | Macro-F1 ≥ 0.70; `contradicts` precision ≥ 0.80 |
| B5 | Dedupe migration, operator audit of 50 groups and 50 near misses | ≤ 2 false merges in 50 |
| B6 | Local titles | Local preferred or tied ≥ 60% in 50 blind pairs |

## 3. Cost

Spend is about $25 a month: extraction 58%, matching 25%, all on Opus 5.5. That's low, so V2 doesn't trade
correctness or simplicity for a few dollars. It matters at 5–10× capture, where the lever is a cheaper extraction
model (B3).

## 4. Open questions

1. ~~Does re-transcription of growing conversations dominate repeat extraction?~~ No (2026-10-27): of $14.89 spent
   on extraction, re-transcription was 12% and none of it was speech first sent while its conversation was open,
   so M1 doesn't hold extraction. Repeats of an identical payload were 18% (v1.1 skips them) and naming that
   changed the pseudonymized payload 35%.
2. What N makes two observations in one conversation independent?
3. Which statuses can the operator set directly, and does a manual promotion pin the idea against the rule?
