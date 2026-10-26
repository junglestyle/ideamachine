# Coding standards

Idea Machine follows the [org standards](org-standards.md), an unchanged copy of
[junglestyle's](https://github.com/junglestyle/.github/blob/main/code-standards/README.md). This file adds
Idea Machine's own rules and examples. Where it differs from an org rule, it says so, and it wins.

## Invariants

What the pipeline keeps true, whatever the stage:

- **Raw is immutable.** Hearsay's stream is only read, never written. Idea Machine's own copy of it (`im.source_*`)
  is append-only: a correction is a superseding row, never an update. Forgetting is the one exception, and it
  deletes for real ([ROADMAP §3.4](ROADMAP.md#34-forgetting)).
- **Derived is re-derivable.** Every derived row carries `schema_version`, `stage_version` (code or heuristic),
  `input_hash`, and `model`, `model_version` and `prompt_version` where they apply. Every stage supports
  `im reset --stage X`.
- **Human labels aren't derived.** They live in their own table, anchored to segment IDs, not episode IDs, so they
  survive re-derivation. When a segment is superseded, its labels follow the supersession links to the current
  segments, so a correction never orphans a label.
- **Traceability.** Every episode, label, span, link and synthesized object stores the `segment_id`s it came from.
- **Cheap models route, never drop.** Triage writes labels and a route. No stage deletes or hides segments: "noise"
  is a label, not a filter.
- **Eval before trust.** An automated tier drives nothing until it beats its exit threshold on the hand-labeled set.
- **Egress is a logged, versioned policy.** What goes to Claude is decided by `im.egress.POLICY_VERSION` (policy B:
  everyone's words, speakers pseudonymized; [decision 0004](decisions/0004-claude-extraction.md)), and every request
  is logged with the segment IDs it carried.

## Jungle Style

- Lean on Postgres before writing your own: `im run`'s import, purge and segmentation share one transaction,
  embeddings live in pgvector, and roles draw the boundaries (`im_pipeline` owns `im.*`; `lattice_app` reads `pub` and
  only appends feedback).

## Zen

- One way in for each kind of feedback: `im ideas --review` and Lattice's verdict events land in the same
  `im.item_verdicts`, and `im themes` inserts the same `pub.feedback_events` Lattice does.

## Pragmatic Programmer

- Orthogonality: Idea Machine reads Hearsay only through its file stream, and Lattice reads Idea Machine only through
  the views in schema `pub`.

## Testing

- The behaviors worth a test: a correction re-derives only the episodes it touches, a second run changes nothing,
  forgetting leaves no text or derived row (and reports what was already sent), labels survive corrections and
  resets, the stream is only read, unknown formats and speakers fail closed, `lattice_app` can only read `pub` and
  append feedback, and the monthly cap stops extraction, scoring and the digest.
- Tests run on synthetic streams from `im.fixtures` and never read the real one. Each gets a fresh `im_test`
  database; the code refuses a dev admin URL that isn't localhost.

## Running it

- `make check` runs everything CI runs except the org standards diff. The tests need the database env vars from `.env.example`; without them the
  database tests skip. Setup and configuration live in [install.md](install.md), deployment in
  [deploy.md](deploy.md) and `install/nas.sh`.
- `make standards` diffs [org-standards.md](org-standards.md) against the org's current file, so a hand edit or a
  stale copy fails. It needs the network, so it isn't part of `make check`; CI runs it as its own job.
  `make sync-standards` refreshes the copy.
