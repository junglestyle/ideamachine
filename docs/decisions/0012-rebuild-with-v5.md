# 0012: Rebuild from the stream with extraction v5, in parallel, then cut over

_2026-11-08. Status: accepted, not built yet; amended 2026-11-10 (the extractor and the gate, below). Amends [0010](0010-connections-kinds-lenses.md) (who writes `evolves`)
and [0011](0011-observations-are-mine.md) (relayed and held thoughts); settles the prompt review before the
re-extraction; my decision. The prompt texts were reviewed by two reviewers and three adversarial teardowns, merged,
and read by me; they go into the code as extraction v5 and matcher v2._

## Context

- The archive has to be re-read on Claude before 2026-11-25. v5 changes what a capture is (no capture kind or
  confidence; a reading, attribution and mishearings), 0011 drops everyone else's speech, and 0010 changed the graph's
  storage. Carrying the old data across would carry v1–v3 captures, other people's observations, statements that
  quote and label, and V1's routes and verdicts.
- Nothing in the current database is important to me, my input included.
- A re-read capture of speech already captured joins its old observation's cluster without the matcher, so an
  in-place re-read wouldn't repair the statements it was meant to.

## Decision

**Rebuild, don't migrate.** A second database is built from Hearsay's stream with extraction v5 and matcher v2, beside
the live one, and replaces it when its checks pass. Nothing is carried over but the code and config; the old database
is archived, not deleted. Cluster ids change once, at cutover.

**Extraction v5** captures only my speech (0011), each capture with an exact quote, its cited lines, a reading, the
attribution of a thought I relay, the mishearings the reading fixed, and a gist that says only what the cited lines
say. It has no capture kind and no confidence (nothing read them), and no interests slot (it favored my usual
subjects). Insults about a particular person stay out; judgments about people count; names may appear where they're
part of the thought, and attribution uses a role when the transcript gives one, otherwise the name as said.

**Matcher v2 decides membership only:** same point as a candidate cluster, or new, one yes/no per candidate, the
decision made in code. It writes the statement and title once, and nothing rewrites them but me. Relayed thoughts and
thoughts I hold never share a cluster (code offers each only its own side); connections link them across.

**"Builds on" is a connection, not lineage.** The matcher no longer writes `evolves`: a point that refines, extends or
applies another is a connection with connection kind `elaborates` and a direction, for the connection judge (Terra).
`evolves` stays defined for what's already stored; `synthesizes` is unchanged.

**Consolidation is shelved.** Rewriting a statement from its members could only narrow and hedge it, and rewriting on
each arrival would move the matcher's retrieval anchor. Instead a Jev check flags members whose gist doesn't make
their cluster's point, as possible false merges for review.

**Mishearings replace the vocabulary sent to Hearsay**, which hurt its transcription. A mishearing is a (heard, meant)
pair: provisional when the extractor reports a fix, confirmed or rejected by me. The extractor sees the confirmed and
provisional pairs whose heard words occur in the episode; a rejected pair carries no weight, and stays rejected if the
fix recurs. The quote always stays as heard.

**A cheaper extractor through a Jev gate.** Captures are checked in code (exact quote within my cited lines, schema,
mishearings, pseudonyms) and by Jev (does the gist add, misstate, overstate or complete; is it mine; is it a mocked
view), with each check's cut set from labeled captures. A gist that fails is repaired, a capture that isn't mine is
dropped, and an episode that fails the code checks is re-read on the stronger model. The archive rebuild runs on Claude;
a cheaper extractor replaces it only if, gated, it holds up on the same checks.

**Sensitivity is eight descriptive tags,** asked of Jev one yes/no each: profanity, sex, gender politics, politics,
drugs, offensive, violence, and private person (anything about a specific private individual). They describe; a view
decides what to hide.

**Arousal is carried onto observations** from their segments, as a fact to show and filter by, not a signal that
decides anything.

## Consequences

- Before the rebuild: v5 as a trial prompt with its code checks; the Jev gate; goldens from the hard set (the two
  reviewers' drafts compared, where they disagree reviewed by me); the trial on the goldens, the held-out hard set and
  the 150-episode sample, on Claude and then the cheaper candidate; then matcher v2, the false-merge check, mishearing
  review and the sensitivity tags.
- Storage: `im.items` gains `reading`, `attributed_to` and `mishearings`, and `kind` and `confidence` become nullable;
  a mishearings table; observations carry arousal. The grouping overlap's floor rises to 8 words.
- Retired: the vocab stage's finding and picking prompts and the vocabulary written back to Hearsay (Hearsay stops
  reading it); the rubric; the legacy confidence route; `extract.interests`.
- Live stages that send what was said pseudonymize names said (0008's policy C) before the rebuild reads anything.
- The monthly spending cap rises for the trial and the rebuild; the rebuild uses prompt caching and the Batch API.
- B4's and the tension sheets' marks stay as they are; new sheets draw from the rebuilt graph.

## Amendment (2026-11-10): Opus on v5.3; the gate after the rebuild

_My decision, on the trials of 2026-11-10 (the private evaluations repo has the reports)._

**The rebuild extracts with Opus (medium effort) on extraction v5.3.** v5.3 is v5 with the goldens' decisions
written in: a joke's, story's or example's point stated at the scope it was told; judgments about a person counting,
apart from venting about one event; explaining the fault in front of me, or the next steps of reworking something,
not counting; a personal reaction kept personal; back-references spelled out. On the 150-episode sample, Opus read
every episode, every quote was word for word, and v5.3 made 6% more captures than v5, mostly the kinds it was meant
to add. No cheaper model held up: fourteen were tried on v5.3. Those that came close to Opus (Gemini 4 Carbon, Qwen
3.8 Max Prime) cost 2.3–2.5× as much per episode, since Opus's prompt caching makes it cheaper than its list price
suggests (about $0.024 an episode). The cheap ones (Sol, Terra and others) capture comment, status and teasing, and
split single thoughts, which the gate can't repair.

**The gate runs after the rebuild, not in it.** It was there so a cheaper extractor could do the bulk of the work;
with Opus extracting, its checks are a cleanup pass over the rebuilt captures, run once B7 has set its cuts. Its
answers are kept per capture, so it can run at any time. The code checks still run on every capture as it's read.
