# 0004: Extract ideas with Claude, everyone's words included

_2026-10-06. Status: in use. Changes ROADMAP §2 and Phase 4's privacy boundary; my decision._

## Context

- My recordings are mostly chatter (decisions 0002, 0003), but the point of Idea Machine includes pulling value
  out of that noise: anyone's ideas, jokes and observations, not only mine.
- The local LLM shares eeyore's GPU with Hearsay's transcription and can't run at the same time.
- Claude is stronger at this judgment and needs no local GPU.

## Decision

`im run` sends every current episode to Claude (`claude-opus-5-5`, effort `medium`, structured JSON output) with an
extraction prompt (`src/im/extract.py`) adapted from my original ChatGPT "Idea Machine":

- Capture faithfully, quote the transcript, don't evaluate or moralize. Anyone's idea counts.
- Cite transcript lines. Lines map back to segment IDs, which is where provenance comes from.
- "Note to self" means capture.
- A pendant **tap is a weight, not a rule.** Taps can be accidental, so a tap means "look closely here and raise
  confidence if something's worth keeping".
- Most transcripts yield nothing.

**Privacy policy B** (`im.egress.POLICY_VERSION`), replacing the roadmap's "other people's words never leave
the box":

- Everyone's words go out verbatim.
- Speakers are pseudonymized per request (`me`, `S1`, `S2`, `unknown`). The mapping stays local and is applied to
  what comes back.
- Names *spoken* in the conversation are not scrubbed. That needs entity extraction (Phase 3).
- Nothing from the projects registry goes out.
- Every request is logged in `im.egress_log`: the payload, its hash, the segment IDs, the model, the policy, the
  tokens and the cost.

**Forgetting** still can't recall anything already sent. Until Hearsay's forget command exists (its slice 12),
nothing can be forgotten at all. When it is, Idea Machine deletes its items, my verdicts, and the local copy of
sent payloads, and `im forgotten --sent` lists forgotten segments that had already gone out, and when. This
lifts the roadmap's Phase 4 gate ("nothing sent until forgetting works") by my choice.

**Cost control:** a monthly cap (`[extract] monthly_cap_usd`, default $20) is checked before every request.
At current volume a full pass costs well under a dollar. A refusal falls back to `claude-opus-4-8` server-side;
a refusal by both is recorded as read with nothing captured.

**Review:** `im ideas --review` keeps or discards each captured item, most confident first. Verdicts are human
input, anchored to the item's segments and quote, and are deleted only by forgetting.

**Router v2** (`rules-2`): review when Claude captured an undiscarded item with confidence ≥ 0.5, or I said
"note to self", or I tapped. Everything else is auto-filed. A discard takes the episode out of the queue unless
something else holds it there. The local LLM's flags are no longer read, and `im run` no longer runs a local
triage backend by default (`[triage] backends = []`). Laya, the local LLM and logreg remain available for
`im eval`.

## Consequences

- Third parties' words go to Anthropic's API. That's my call for my recordings; it's recorded here and in the
  egress log, so it can be audited and changed (a new `POLICY_VERSION` re-sends under the new policy).
- When Hearsay renames a speaker, the episode is re-derived and goes out again, even though the pseudonymized
  payload is usually identical. That's cheap at current volume; reuse by payload hash if it ever matters.
- Phase 4's other egress work (local gists of other speakers, the scrub, the leak test) isn't built. Policy B
  doesn't need it; a stricter policy would.
