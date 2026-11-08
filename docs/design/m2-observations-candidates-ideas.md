# M2 design: observations, candidates and ideas

_2026-10-28. Status: done 2026-11-01 (B5 passed; edges cite observations; archive and typed ideas have
observations of their own). Milestone M2 in [ROADMAP-v2.md](../ROADMAP-v2.md); my decisions, worked
out in the M2 design thread. It replaces the provisional/promoted lifecycle in the V2 spec._

## Summary

M2 rebuilds Idea Machine's core around one path: **utterances → observations → candidates → ideas**.

- Everything Claude extracts becomes an observation, tagged me or not me.
- Observations that make the same point cluster together.
- A cluster of mine becomes a candidate, and it gains weight as I come back to it.
- Only I make ideas. The system proposes candidates, and I keep each one as canonical (weighted) or pinned (not
  weighted), or dismiss it.
- Keep/discard verdicts feed nothing.

Lattice keeps working unchanged until M6.

**Done when:**

- every current capture belongs to an observation with a speaker, and every observation to a cluster, while
  `pub.ideas` returns the same ids as before;
- a dedupe audit of 50 merged observations and 50 near misses finds at most 2 false merges (B5);
- every cluster has a weight, and I've seen the first list of proposals (M3 measures whether they're worth my time).

## Terms

Utterances are what Hearsay heard (`im.source_segments`). They're the source, not a stage. Everything after that is a
property of an observation or a cluster:

| Property | Values | Set by |
|---|---|---|
| Maturity | observation → candidate → idea | the system up to candidate; only I make an idea |
| Speaker | me, me by diarization, not me | Hearsay, read at query time |
| Kept as | canonical (weighted) or pinned (not weighted) | me; only ideas have it |
| Weight | a number | computed from the cluster, versioned |

No value label exists anywhere. An observation is neutral, a candidate is something that might be meaningful, and
candidates differ only by weight. Pinning never changes weight, maturity or clustering.

## What changed since the V2 spec

| Change | Date | What it means for M2 |
|---|---|---|
| Attribution is me / not me; Hearsay stopped naming people (hearsay#19) | 2026-10-28 | Speaker comes from `speaker_kind` + `speaker_basis` |
| Keep/discard retired as a signal | 2026-10-28 | Nothing reads verdicts; B2 and B3's keep-recall bar go |
| Ideas are only made by me, as canonical or pinned | 2026-10-28 | The system proposes; a rule never promotes. Stars become pins |
| Synthesis is its own stage, triggered by weight | 2026-10-28 | Not the weekly digest. Its own milestone after M2 |
| B3: Opus stays the extraction model | 2026-10-28 | Sonnet found 82% of keeps at 42% of the cost; Haiku lost 14 of 30 blind slug pairs |
| Hearsay accuracy by ear | 2026-10-28 | Owner by voice 49/49 right; by diarization 147/162; unknown is 6% of speech |

Current counts (prod, 2026-10-28): 527 current captures and 439 V1 ideas. By speaker, the captures are me 386, me by
diarization 26, not me 115.

## Observations

Everything goes in, me and not me alike. An observation is one thing Claude derived from one stretch of speech, and it
outlives re-extraction: when a re-read captures the same speech again, the new item joins the old observation
instead of starting a new one.

- **Table:** `im.observations(observation_id, segment_ids, first_seen)`, plus `items.observation_id`. It's on the
  ledger side, rebuilt only by the dedupe stage.
- **Matching:** the matcher `carry_verdicts` already uses (`feedback.py`). A new item joins an observation when its
  segments descend from the observation's and `quote_score` ≥ 0.4 against every capture already in it. (Until
  2026-11-01 any one capture was enough, and B5 found that chains: a quote Claude stitched from two passages with
  "..." matches each, so it pulled both into one observation.) The threshold was calibrated on the 2026-10-06
  re-transcription: the same captures scored ≥ 0.44, others ≤ 0.3. When Claude captures the same line twice in one
  read, the two fold into one observation.
- **Speaker** is derived when it's read, from the segment the observation is said by, so a Hearsay relabel moves it
  without re-extraction.
  - It's me when that segment is the owner's by `voice`, `named` or `channel`.
  - It's me by diarization when the segment is the owner's by `diarization`.
  - Anything else is not me: anonymous, stranger, unknown, or no cited segment.

  A view, `im.observation_speaker`, also returns `cites_me` when a quote that spans speakers includes solid me
  speech.
- **Extraction is unchanged** in M2: observations are whatever it captures today. Widening it to neutral units
  (questions, jokes, passing thoughts) comes after M2.

## Clusters and candidates

Observations that make the same point form a cluster. Repeating myself grows a cluster: that's recurrence, not
evidence. A cluster is today's `im.ideas` row, with its ids kept, and its observations are its members.

- **Recurrence** counts distinct conversations, or observations in one conversation at least N minutes apart. N is
  30 by default, set by the operator. Two repeats within N are both members but count as one recurrence.
- **Someone else making my point:** their observation joins my cluster as a not-me member. It adds weight, never
  maturity.
- **A cluster becomes a candidate** with no model call, in either of two cases:
  - it has a me observation that Claude captured as meaningful (today every capture is, since extraction only
    captures idea-like things);
  - it recurs with at least one me observation.

  Observations by diarization count only alongside a me observation. The candidate's statement is the gist of its
  first observation, as V1 titles ideas today.
- **Clusters with only not-me members stay observations.** They link to my clusters and add to their weight, but
  never become candidates on their own.

## Weight

Weight is how much a cluster contributes to links and synthesis. A versioned formula computes it every run over
stored signals, and keeps it in `im.weights(cluster_id, formula_version, weight, signals jsonb, at)`. That table is on
the graph side, so a new formula writes next to the old one and the two can be compared.

The starting formula, `weight-1`, is tuned at M3:

- independent me recurrences, the main term;
- a smaller amount for each member by diarization or not me, capped so a crowd can't outweigh me;
- links to canonical ideas;
- a fixed boost when the cluster is itself a canonical idea.

Pins add nothing. V1 verdicts add nothing.

**Debug control:** `im tune knob=value ...` previews a change against the current settings. The knobs are the
`[weight]` settings in `im.toml`: N (`recurrence_minutes`), the weight terms, proposals per week, and the re-propose
margin.

- **What it prints:** the weights that move most, and the next proposals before and after.
- **Cost:** it writes nothing and costs $0.
- **Applying:** it prints the `[weight]` block to paste into `im.toml`. A changed formula is a new version, written
  beside the old one on the next run.
- **Clustering isn't a knob.** What clusters together is the matcher's same-point decision (Claude, until Jev in M4),
  not a threshold. The one threshold is how close two captures' quotes must be to fold into one observation
  (`quote_score` ≥ 0.4), and changing that re-assigns observations, so it stays fixed until B5 says otherwise.

## Ideas and proposals

Only I make an idea. I keep something as one of two kinds:

- **Canonical:** weighted, and pulls the clusters linked to it.
- **Pinned:** kept and visible, with no weight.

I can switch between them or un-keep. Keeping works on a candidate, or straight on any observation: pinning a
friend's funny line makes it a pinned idea. Keeping is my endorsement, so it works on not-me material too.

- **Events:** `idea_keep {as: canonical | pinned}`, `idea_unkeep` and `idea_dismiss` feedback events on a cluster, in the ledger like every other
  human input. Merge and reject stay as they are.
- **Starting state:**
  - The archive seed and the ideas I added by hand (`add_idea`) are canonical.
  - V1 stars become pinned.
  - "Never repeat yourself: consult the lattice" was starred and said by diarization, so it becomes pinned.
- **Proposals (emanation, gated by me):**
  - Each run ranks candidates by weight, and the top new ones come to me as proposals: up to 10 a week, highest
    weight first. I keep each as canonical, keep it as pinned, or dismiss it.
  - A dismissed candidate isn't proposed again unless its weight has grown by the re-propose margin.
  - Syntheses arrive the same way once that stage exists.
  - Proposals and their outcomes are kept in `im.proposals(proposal_id, cluster_id, weight, formula_version, made_at,
    outcome, decided_at)`.
- **Where I see them:** two new Lattice tools, `proposals` and `keep_idea`, until Emanation replaces Lattice at M6.
  The old review queue was retired on 2026-10-29 (lattice#10): Lattice's first tab lists proposals instead.

## Edges

M2 gives edges the columns they cite with. Typing the edges waits for Jev in M4. Built 2026-11-01 (migration 035,
`im/citations.py`), with these decisions:

- `im.idea_links` gains `cited_observation_ids uuid[]` and `confidence real` (`method` was already there).
  `confidence` is the cosine for `related` edges, and empty where nothing measured one.
- **An edge cites the origin observations of its two ends:** what each idea's statement was made from. For
  `related` edges that's the text that was embedded; for `evolves`, the capture the matcher judged and the idea it
  evolved.
- **Cite what exists.** An idea with no observation (the archive's, or one I added) adds nothing to the edges it's
  on, so an edge between two of them cites nothing and its `method` says where it came from. That keeps the
  archive's cross-references, and the `related` edge that puts an idea I add next to its near-duplicate. Archive
  and added ideas get observations of their own next (the ChatGPT transcript has my words and their times), and
  then their edges cite them with no change here.
- **Not-me clusters link like any other** (`related`, cosine ≥ 0.70, top 3). No forced link below the threshold:
  it would mostly be noise, and `weight-1` doesn't count those links anyway.
- **Citations are derived.** They're recomputed whenever observations or edges change, so a rebuild of
  observations (like migration 034) costs nothing. Forgetting drops the ids it deletes; an edge made from forgotten
  speech already goes with the idea it came from. `im check` fails on an edge citing an observation that's gone, or
  citing nothing when one of its ends has an observation.

## Observations that aren't speech

_2026-11-01, migration 036._ The archive's ideas and the ones I add were said too: into the ChatGPT Idea Machine,
or typed. They get observations of their own, so they recur, weigh and cite like captures do.

- `im.observations` gains a `source`: `speech`, `archive` or `typed`. One that isn't speech has no segments and no
  capture: it belongs to its idea directly, and holds its own quote, time, speaker and conversation. Forgetting
  works on segments, so it never reaches them.
- **Archive:** `im seed-observations <export>` reads the ChatGPT conversation's export (ChatGPT Exporter's
  Markdown). Each message goes to the idea ChatGPT's answer numbered it under ("**Idea #14, refined:**"), which the
  archive keeps as `chatgpt#14`; the archive's own quotes are ChatGPT's cleaned-up copies, and it has no times. A
  message with no numbered answer isn't an idea. Without `--write` it only shows the plan, lowest match with the
  archive's text first, so a wrong number stands out. `--not-me 18`: #18 was my brother's point.
- **Typed:** each idea I add gets one observation of what I typed, at the time, from its first `idea_add` event.
- **Recurrence** counts them like speech, by conversation and `recurrence_minutes`: two messages to ChatGPT minutes
  apart are one recurrence, weeks apart two. The neutral view's cards show them as quotes.

On the NAS, after the export is in `saved/`:

```sh
im seed-observations /saved/chatgpt-idea-machine.md --not-me 18           # look it over
im seed-observations /saved/chatgpt-idea-machine.md --not-me 18 --write   # then add them
```

## Migration, validation and rollback

Everything M2 adds is new tables, columns and views, so the existing `pub` views and Lattice don't change. The
deploy has the same shape as M1's:

1. Take a `pg_dump` to `~/review/im-pre-m2.dump`, and run `m2_check.sql` (a read-only snapshot).
2. Pull and run `install/nas.sh`. The migration adds the tables and columns, and the first run assigns observations,
   maturity and weights. Nothing goes to Claude.
3. Run `m2_check.sql` again and diff it against the first run.

| Check | Expect |
|---|---|
| Current items without an observation | 0 |
| `pub.ideas` ids | the same set as before |
| Clusters by maturity | about 326 candidates (me); about 113 left as observations (92 not me, 21 by diarization only); canonical and pinned ideas = seed + added + starred |
| `egress_log` rows added during the run | 0 |
| First proposals | a list I look over; no bar until M3 |

Then comes **B5**: a blind sheet of 50 merged observation groups and 50 near misses, like the B3 slug sheet. It
passes with at most 2 false merges.

- **Merged:** 50 observations with two or more captures, sampled, each shown as its least alike pair. Merges that
  quote the very same words are sampled only when there aren't 50 others.
- **Near misses:** the 50 closest pairs of observations that stayed apart: captures citing the same speech by
  segment descent, scoring under 0.4, each pair of observations once.
- No two pairs on the sheet quote the same two things: re-reads repeat a capture's words, and each is asked once.
- Both sets are shuffled together, sides in random order, with no score. I mark each pair `same`, `different` or
  `unsure`. A merged pair marked different (or unsure) is a false merge; a near miss marked same is a false split,
  which says the threshold could come down.

On the NAS, as root (the sheet quotes people, so it stays in `saved/`):

```sh
install -d -o root -g 568 -m 770 /mnt/fast/ideamachine/saved/b5   # the run container is apps (568)
docker compose -f /mnt/fast/repos/ideamachine/install/compose.yaml run --rm \
  -v /mnt/fast/ideamachine/saved:/saved run audit-dedupe sheet /saved/b5
# mark /mnt/fast/ideamachine/saved/b5/sheet.md, then:
docker compose -f /mnt/fast/repos/ideamachine/install/compose.yaml run --rm \
  -v /mnt/fast/ideamachine/saved:/saved run audit-dedupe score /saved/b5
```

It reads only, and sends nothing to Claude.

**First sheet, 2026-11-01:** stopped after 53 pairs, 26 of them merged. 2 false merges, both scoring under 0.4:
each observation held two points, joined through a third capture that quoted both with "..." between them. True
merges scored 0.429 and up, the one false split 0.294, so 0.4 stays; the join rule changed to every capture
(migration 034 rebuilds observations). The near misses also repeated, one per pair of captures of the same two
observations; the sheet now has each pair once. B5 reruns on a fresh sheet.

**Second sheet, 2026-11-01: B5 passes.** After the rebuild (528 merged observations, 293 of them verbatim; 80 near
misses), all 100 pairs marked:

| | Pairs | Wrong |
|---|---|---|
| Merged | 50 | 1 false merge, scoring 0.444 |
| Near misses | 50 | 5 false splits, scoring 0.2–0.351; 1 unsure |

True merges scored 0.433 and up. The threshold stays at 0.4: the false splits spread down to 0.2, so no threshold
recovers them without letting merges like the 0.444 one through. A false split is also the cheaper error: both
observations stay in their cluster, and two from the same moment are one recurrence, so weight doesn't move; a
false merge hides a point.

**Rollback:** each new table and column drops cleanly, because nothing old reads them, and the dump is the backstop.
The ledger (items, verdicts, feedback events) is never rewritten, only pointed at.

## Decisions (2026-10-28)

- **Someone else makes my point:** their observation joins my cluster as a not-me member, adding weight but never
  maturity.
- **Synthesis:** its own stage, triggered by weight, after M2. When a cluster, or the clusters around a canonical
  idea, gain enough weight since their last synthesis, Opus writes one of two things, citing observations:
  - consolidation: a new statement for one cluster;
  - bridging: a candidate that ties several clusters together.

  Each synthesis is versioned and capped, and it arrives as a proposal. _Amended by
  [0010](../decisions/0010-connections-kinds-lenses.md) (2026-11-06): triggered by a neighborhood of connections,
  not weight; a synthesis links to what it was made from (`synthesizes`)._
- **N:** 30 minutes by default, an operator setting, adjustable with `im tune`.
- **Stars:** a star is a pin, with no weight.
- **Not-me clusters** never become candidates on their own, and a canonical idea doesn't pull them up. I can still
  keep one myself.
- **Widening extraction** to neutral observations: yes, but after M2. It needs a new prompt and a full Opus re-read.
- **Proposals:** up to 10 a week, highest weight first, adjustable with `im tune`.
