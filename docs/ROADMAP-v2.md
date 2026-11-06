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
| B3 | Extraction model swap (`im trial`). Done 2026-10-28: Opus stays (Sonnet 82% recall at 42% cost; Haiku lost the blind slug check). Rerun 2026-11 on `v3` under policy C across providers, recall against Opus's captures on a fixed sample rather than my verdicts ([0008](decisions/0008-other-providers-and-names.md)). On 147 episodes (2026-11-04, read 2026-11-06), Opus's second read made 90% of its captures again; of those solid captures Sol found 85%, Terra 85% at 21% of Opus's cost, Sonnet 77%, GLM-5.3-flash 74%, GLM-5.3 69%, Haiku 66%. 64–81% of each model's misses are solid captures, and at most 2 per model are of an idea said in more than one conversation. Opus stays for now; Terra is the candidate for later. Whether its misses are worth having: `im audit-misses`. Rerun on `v4` | Recall ≥ Sonnet's, then candidate's slugs preferred or tied ≥ 60% of 30 blind pairs |
| B4 | Edge typing, 200 labelled pairs (`im audit-edges`), marked by me, blind. The pilot (2026-11-02, 20 pairs, model-marked so not counted) kept all five answers, folded `evolves` into `extends`, and found no `contradicts` or `supports` among nearest neighbours, so sheets also draw ideas said far apart and claims beside concrete observations. The first sheet (2026-11-04, 50 pairs, ten models drafting) found no contradictions but two tensions, and every model reading my `extends` as `related`: labels 2 rename `extends` to `elaborates` (a later version stays `evolves`) and replace `contradicts` with `tension`, which includes it. Terra and Opus agreed with me most (80%); the next 150 pairs decide After it, decision [0009](decisions/0009-edges-connection-graded-kind-suggested.md): the top models agree with me 94–96% on whether there's an edge and far less on which kind, so the edge is graded and the kind only suggested; tension and analogy get passes of their own | Connection: agreement ≥ 0.90, and ≥ 0.90 of a model's edges ones I'd make (precision). Tension and analogy reported, bars once there are enough of them |
| B5 | Dedupe migration, operator audit of 50 groups and 50 near misses (`im audit-dedupe`). First sheet 2026-11-01, stopped at 26 merged: 2 false merges, both chained through a quote stitched with "..."; an item now joins only if it matches every capture. **Passed 2026-11-01** on a fresh sheet: 1 false merge in 50; 5 false splits in 50 near misses, so 0.4 stays | ≤ 2 false merges in 50 |
| B6 | Local titles | Local preferred or tied ≥ 60% in 50 blind pairs |

## 3. Cost

Spend is about $25 a month: extraction 58%, matching 25%, all on Opus 5.5. That's low, so V2 doesn't trade
correctness or simplicity for a few dollars. It matters at 5–10× capture, where the lever is a cheaper extraction
model (B3; Opus stayed in 2026-10).

## 4. Views and filters (planned 2026-11-04)

Two families of views, both inside Idea Machine ([0005](decisions/0005-presentation-inside-idea-machine.md)), and a
filter over both.

- **Emanation: cluster-based cloud views, for association and promotion.** The sky is the first: one stable map,
  lenses that light it, proposals promoted from it. Others follow the encodings in the
  [M6 notes](design/m6-emanation.md). Navigation is slow today: panning redraws every star with a fresh radial
  gradient each frame (draw each kind of star once and stamp it instead), and a lens reloads the whole page (load
  the map once and fetch only a lens's relevance). Measure the server's time for `/sky` before changing it.
- **Lattice v2: pinned views, for organization.** Where I place ideas by hand, as opposed to where meaning puts
  them. Not the V1 Lattice repo, which is still archived at M6. First two:
  - a conceptual or thematic lattice (a grid) to pin ideas into;
  - the Qabalistic Tree of Life, pinning ideas by correspondence (to the sephirot and the paths between them).
- **Removing sensitive elements**, per view and for the MCP connector. Builds on demo mode
  (`im/sensitivity.py`, migration 027), which already tags ideas and themes by category and lets my marks win:
  - offensive: derogatory to a group or a person (demo mode's `racism`, broadened);
  - vulgarity: curse words (`profanity`), which can be blacked out in place rather than the idea withheld;
  - politics (`political_extremism`, broadened to politics generally);
  - sex and gender (`sex`, `gender_politics`).
  The tagging is a set of yes/no judgments, one per category, which suits Jev: cheap enough to run on every idea
  each time its text changes.

## 5. Open questions

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
8. Can analogies between unrelated ideas be found? The spike (2026-11-04, `im analogy-spike`, 52¢) ranked my seven
   analogy partners by one structure line per idea: median rank 55 of 314, against 3 by topic. But every B4 pair
   was drawn from neighbourhoods, so those analogies were close by topic already, and one line per idea drops the
   facet an analogy rests on. Open: `im audit-analogies` asks the question of unrelated pairs proposed by four
   structure lines per idea, by a model reading every idea, and at random. `im audit-tension` does the same for
   tension, which B4's first sheet found twice. The first analogy sheet (2026-11-05, 26 pairs, marked strictly: a
   specific shared move, not a genre) found 3, all new: 2 of 15 by structure lines, 1 by the model, none of 10 at
   random. The model (Sonnet) proposed only 1 pair between unrelated ideas, so the next sheet has Jev rank 3,000
   unrelated pairs on 20 wordings of the question, each wording proposing its best, to be graded and pared down.
   The second sheet (analogy-2, 2026-11-06, 40 pairs) found 3, all new, all from Jev: 3 of 25 by Jev, none of 10 by
   structure lines, none of 5 at random. Over the 25 Jev pairs, most wordings ranked my 3 above the other 22 (AUC
   0.90-0.955) and so did their mean (0.92); `specific`, `explains`, `same_form` and `lens` didn't (0.65-0.68).
   Agreement predicted a hit: pairs in 10 or more wordings' top 10 held 2 of 5, those in 2 or fewer 1 of 14. Jev's
   `surprising` ran the wrong way (0.19). So the next sheet asks `probes2`, without those four, and draws 30 pairs
   by the mean of the remaining wordings beside 10 at random; facets leave the default (2 of 25 over two sheets, at
   a Claude request per batch of ideas). Tension isn't where ideas are close: a sheet of only near pairs
   (tension-near-1, 2026-11-06) held 1 in 30, against 28 in 150 over the four mixed sheets, whose new tensions ran
   across subjects. Jev's `pulls4` flagged 3 of the 30 at 0.45, the one tension among them.
9. Do summaries say more than the quotes? The first tension sheet (2026-11-04): the two tensions three models agreed
   on and I didn't were made by the summaries ("comfort beats the stigma" for "they're pretty comfy"), and only
   Sonnet beat chance at all (2 of my 5, precision 0.5), so a tension pass proposes for me to keep, not links on
   its own. Every stage that reads summaries (matching, links, the sky, these sheets) inherits what they add.
   Plan: a faithfulness pass after extraction, a cheap decision model like Jev, asking of each capture whether its
   summary claims anything its quotes don't; an unfaithful summary is rejected and rewritten, not kept. Measure
   first: how often it happens on a sample, checked by me, and whether the extraction prompt alone fixes it.
10. Does a capture keep what was said apart from what was meant? Not on `v3` (2026-11-06): 11% of the live
    captures' unstitched quotes and 15% of the pieces of stitched ones aren't in their lines (fillers dropped,
    mistranscriptions fixed, words put back), 183 gists note a transcription fix, and gists and statements open
    with labels ("Bit:", "Joke:"), attribution or topic. Prompt `v4` (trials only) asks for the quote exactly as
    transcribed, a reading, an unlabelled gist and who a relayed thought is from; `im trial` reports each read's
    `quotes_as_said`, the share whose quote copies its cited lines word for word. Register (joke, irony) leaves
    `kind` for a judging pass after extraction.
