# 0011: Observations are mine; everyone else's speech is context

_2026-11-08. Status: accepted, not built yet (extraction v5). Amends SPEC §1 ("who said it") and §3, the
[M2 design](../design/m2-observations-candidates-ideas.md)'s not-me members, and the weight formula's `others`
signal; my decision._

## Context

- Since v3 the extraction prompt captures "anyone's thought: mine, a friend's, a stranger's". Someone else making my
  point joins my cluster as a not-me member and adds weight (`others`, capped); a cluster of only not-me members is
  an observation that never becomes a candidate (SPEC §3).
- What I'm building is a map of my ideas; other people's thoughts matter as what my thoughts answer, build on or
  pass on.
- Reviewing v4 before the re-extraction (2026-11-08), deduplication within an episode had to choose whose
  expression of a thought to keep: with other people captured, it could drop mine.

## Decision

**Only my speech is captured.** An observation is a thought I expressed: in speech, the lines Hearsay labels me (on
any basis; `me_diar` still counts only beside a `me` observation), in the archive, or typed. Everyone else's
speech is **context**: the extractor reads it to understand what I mean and to resolve what I refer to, and may cite
the lines without which my thought can't be understood, but never captures their thought on its own.

- **Agreement isn't authorship.** If I agree with someone ("yeah, exactly"), it's my thought only when I state it
  myself.
- **Relaying is mine.** A thought I pass on (a teacher's rule, something I read) is captured as mine, with
  `attributed_to` naming its source.
- **Reminders are mine.** A task or decision is captured only when I mark it to remember; someone else's reminder
  follows the ordinary rules.
- **Once per episode.** The same thought said again within one episode is captured once, from its clearest
  complete statement.

**What's already there stays in the ledger, unread.** Captures and observations of other people's speech aren't
deleted. They stop counting and showing: weight drops its `others` signal, clusters with no me observation leave
the views, and a not-me capture never joins my cluster. Reversible, since nothing is lost.

## Consequences

- Extraction v5: only my lines; no `said_by` in the schema (the code sets it from the first cited line labeled me,
  and drops a capture citing none); no capture tags.
- Weight formula: a new version without `others`.
- Views (`pub.cards` and the views behind Lattice): clusters need a me observation.
- Observation grouping: a capture joins only an observation of mine.
- Benchmarks drawn from both speakers' clusters (B4's sheets, the tension and analogy sheets) keep their marks; new
  sheets draw only from mine.
