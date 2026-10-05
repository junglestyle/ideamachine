# 0003: Router v1: cautious, my signals first

_2026-10-05. Status: in use._

## Context

- After the question rewrite, I labeled 95 episodes. 93 are chatter, and keep is 1 or 2 everywhere except one
  (a probable mis-key). Recordings so far hold almost nothing worth keeping, so no backend can be shown to beat
  "always chatter".
- Against those labels, the LLM (`gpt-oss:20b`) flags 37 of 95 episodes (idea/task/decision, or keep ≥ 3).
  Thresholds tuned from this data would mean nothing.

## Decision

Rules, versioned as `router_version`, stored in `im.routes`. A route is a label; nothing is dropped.

- `review` if I tapped the pendant during the episode (±30 s, as Hearsay defines a tap), or *I* said
  "note to self". These hold whatever any model says.
- `review` if the LLM says idea, task or decision, or keep ≥ 3. "Noise" doesn't flag.
- `auto_file` otherwise. `escalate` (to Claude) waits for Phase 4.

`im review` works through the queue, my own notes first and then newest. It asks the label questions without
pre-filling the model's answers, and saves them as labels with `source = 'review'`. `im eval` reports, per
reason, how many reviewed episodes I judged positive. On `im label` episodes, which the router didn't pick, it
also reports recall and how much of what the router sends me I'd call noise.

## Consequences

- At the current volume, about 4 in 10 episodes come to review. That's high, but every review is a label, and
  the queue is how positives get collected at all.
- The LLM shares eeyore's GPU with Hearsay's transcription (hourly at :00, up to ~12 GB). gpt-oss needs ~13 GB,
  so the two can't overlap. `im run` now notes the failure and still routes taps and notes to self; the next
  run triages what was missed. A timer for `im run` belongs at about :45, after Hearsay's :00 transcription and
  :30 reprocess.
- Revisit when reviews hold ~20 positives: tighten the LLM flags (or drop a reason) using the per-reason
  precision.
