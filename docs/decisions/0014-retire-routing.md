# 0014: Retire V1's routing, labels and triage backends

_2026-11-13. Status: accepted. Retires [0003](0003-router-v1.md)'s router and what's left of
[0001](0001-laya-spike.md) and [0002](0002-first-triage-eval.md); my decision._

## Context

The gating review (im-evals `reports/2026-11-13-gating-review-measurements.md`) replayed router v3
(`rules-3;claude-items;tap;note_to_self`) on the live archive: 455 current episodes, 246 routed to review, 209
auto-filed.

- **Routing is moot.** 241 of the 246 reviews (98%) were there only because Opus captured something. The other 5 were
  taps, and all 7 taps in the archive were tests or accidents. "Note to self" had been said once, still inside
  Hearsay's two-week delay.
- **The review list has no reader.** Only `im review` read routes (and the eligibility audit, to report a spread), and
  its answers were `im label` labels, V1's ground truth for scoring the local triage backends. I never used either.
  Every capture becomes a card whether its episode was routed or not; my actual gate is the card feed (keep, pin,
  dismiss).
- **The triage backends were already off.** None drove anything since [0004](0004-claude-extraction.md) (`[triage]
  backends = []`), and the rebuild carried no labels over ([0012](0012-rebuild-with-v5.md)).

What routing was for, a list of what I asked to see again, is the attention queue (#187, migration 066): a tap or a
note to self queues its lines, found in code, and removing an item only removes it.

## Decision

- **Router v3, `im review`, `im label`, `im eval`, `im train`, `im project` and the triage backends (Laya, the local
  LLM, logreg) go**, with `im reset --stage triage` and `--stage route` and the `[triage]` config section (ignored,
  with a warning, until deleted).
- **Their tables go** (migration 067): `im.routes`, `im.triage`, `im.episode_embeddings`, `im.classifiers`,
  `im.labels` and `im.projects`. Labels and projects are human input, so the migration stops instead if either holds
  a row.
- **What they shared moved first** (#193): the "note to self" regex to `attention.py`, the part of day to `egress.py`,
  the sentence embedder to `lattice.py`.

## Consequences

- `im run` no longer routes, and its stats have no `route` or `triage`.
- The `models` extra is only the lattice's embeddings (sentence-transformers on CPU-only torch); Laya's pin goes. No
  test needs the extra, so CI and `make check` don't install or require it.
- The eligibility audit's key no longer gives the spread by route. Sheets already drawn keep theirs.
- `im_v1`, the archived database, still holds V1's labels, routes and triage answers.
