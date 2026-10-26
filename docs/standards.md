# Coding standards

Idea Machine follows the [org standards](org-standards.md) and the org's [Python standards](org-standards-python.md),
unchanged copies of [junglestyle's](https://github.com/junglestyle/.github/tree/main/code-standards). `.org-standards`
lists the topics it copies beyond the general one. This file adds Idea Machine's own rules and examples. Where it
differs from an org rule, it says so, and it wins.

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

## Dependencies

- Lean on Postgres before writing your own: `im run`'s import, purge and segmentation share one transaction,
  embeddings live in pgvector, and roles draw the boundaries (`im_pipeline` owns `im.*`; `lattice_app` reads `pub` and
  only appends feedback).

## Structure

- One way in for each kind of feedback: `im ideas --review` and Lattice's verdict events land in the same
  `im.item_verdicts`, and `im themes` inserts the same `pub.feedback_events` Lattice does.
- Idea Machine reads Hearsay only through its file stream, and Lattice reads Idea Machine only through the views in
  schema `pub`.

## Testing

- The behaviors worth a test: a correction re-derives only the episodes it touches, a second run changes nothing,
  forgetting leaves no text or derived row (and reports what was already sent), labels survive corrections and
  resets, the stream is only read, unknown formats and speakers fail closed, `lattice_app` can only read `pub` and
  append feedback, and the monthly cap stops extraction, scoring and the digest.
- Tests run on synthetic streams from `im.fixtures` and never read the real one. Each gets a fresh `im_test`
  database; the code refuses a dev admin URL that isn't localhost.

## Running it

- The tests need the database env vars from `.env.example`; without them the database tests skip. Setup and
  configuration live in [install.md](install.md), deployment in [deploy.md](deploy.md) and `install/nas.sh`.

## Docs

- Overrides the org rule's ADRs in `docs/stack.md`: each decision is its own file in [decisions/](decisions/), with what
  was measured.

## Known deviations

Where Idea Machine doesn't follow the org standards yet. These are debt to pay down, not overrides: each one should go.

- `make check` runs pytest only. Ruff and mypy aren't configured or run.
- The `Dockerfile` installs uv with `pip`.
- Dependencies: `laya==0.3.23` is pinned exactly without a comment naming the break.
- Data modeling: `im.fixtures.Conversation` is a mutable `@dataclass`.
- Dependencies: Postgres 16 is one of [ROADMAP §2](ROADMAP.md#2-principles-applied)'s stack assumptions, "not
  questioned", and no decision says what it gives.
- Privacy: `IM_VOCAB_PATH` isn't in `.env.example`. `install/compose.yaml` sets it, and `im.vocab` reads it.
