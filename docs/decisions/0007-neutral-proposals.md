# 0007: No rating; proposals are neutral

_2026-10-30. Status: accepted. Closes milestone M3 in [ROADMAP-v2.md](../ROADMAP-v2.md) without tuning; my decision._

## Context

M3 was meant to tune `weight-1` until I kept at least 40% of 50 proposals. Before tuning, two read-only queries
checked every signal Idea Machine has against my own labels: [m3_calibrate.sql](../design/m3/m3_calibrate.sql) and
[m3_signals.sql](../design/m3/m3_signals.sql), run on prod on 2026-10-30.

What they found:

- **Weight has nothing to rank with.** 382 of 386 candidates have one recurrence, so nearly every candidate weighs 1
  and proposals came out in the recency tie-break anyway.
- **No signal predicts what I keep.** Counting only verdicts I made by reading the capture (`how` `one` or `audit`,
  not bulk keeps), AUC over candidates, 127 kept against 36 discarded or dismissed: weight 0.48, recurrences 0.49,
  others 0.50, extraction confidence 0.45, the rubric (ranking v2) 0.36. The rubric points the wrong way.
- **The rubric agrees with my comparisons** (74% of 39 untied pairs between captures), but ranking v2 was fitted to
  those comparisons ([025](../../src/im/sql/im/025_ranking_v2.sql)), so that is circular.
- **Proposals:** I kept 4 of the first 10. The one I kept as canonical had the second-lowest rubric (0.29); the
  highest (0.75) was dismissed.

And the labels themselves are weak: I agreed with my own keep/discard 64% of the time
([017](../../src/im/sql/im/017_idea_scores.sql)), and Lattice's rating (keep/discard, comparisons, the rubric
ranking) never produced a view I use.

## Decision

- **Lattice's ratings are thrown out as labels.** Verdicts, comparisons and the rubric stay in the ledger, unread by
  anything that orders or gates.
- **Nothing is rated.** Weight is still computed and stored with its signals, as facts to show, but it orders
  nothing. Proposals come most recently said first (`weight._eligible`).
- The gates stay: an observation is deduped, a candidate needs a me observation, only I make ideas, and a dismissed
  candidate comes back only after it grows (`repropose_margin`, one more recurrence at today's terms).
- **What's next is a neutral view** (Emanation, from [decision 0005](0005-presentation-inside-idea-machine.md)): the
  graph ordered by time and connection, every card showing what's known (who said it, where and when, how often,
  maturity, how I kept it, its edges), no score. Whether junk reaches it tells me which gate leaks.

## Consequences

- M3's bar (40% of 50 kept) is dropped. Proposals are measured only by whether the neutral view turns up things
  the gates should have stopped.
- `im tune` still previews weight, but a knob now only changes what a run stores and when a dismissed candidate
  returns.
- Synthesis is triggered by weight (M2 design). It waits until weight means something, or another trigger is chosen.
- **Look again** when clusters recur enough for weight to vary (more than a few dozen with two or more recurrences),
  or when I've labelled through the neutral view rather than Lattice's rating flows.
