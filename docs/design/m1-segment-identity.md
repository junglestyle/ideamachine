# M1 design: segment identity and re-link

_2026-10-27. Status: approved, building. Milestone M1 in [ROADMAP-v2.md](../ROADMAP-v2.md); my decision. The
queries behind its numbers are in [m1/](m1/)._

## Summary

The re-link is less risky than the V2 architecture review assumed. The importer already keeps every version of a segment and links old to new. Labels and verdicts already follow those links forward. So the M1 re-key is an **append and link, not an in-place rewrite**: new rows, new `rekey` supersession links, and old rows untouched.

Recommendation:

1. Re-key segment identity to (conversation, start, end, text), with speaker as a mutable annotation. Do it as an append plus link migration.
2. Don't hold extraction for open conversations, and don't build fuzzy re-linking. The churn query (below) found no re-transcription of speech sent while its conversation was open. Fuzzy matching is the wrong tool anyway, because quotes must stay verbatim.
3. Turn on prompt caching for the extraction prompt; it can engage at this size (below).

Everything below separates **observed** (in code, cited) from **proposed**.

## How identity and churn work today (observed)

| What | How | Where |
| --- | --- | --- |
| Segment id | `uuid5(conversation, sha256(record minus utterance_id, affect, delivery))`. It includes speaker, speaker confidence and text confidence. | `importer.py:76-84` |
| Naming a speaker | Same `transcript_revision` and same `utterance_id` but different content gives a new segment, linked old to new by `utterance_id` | `importer.py:186-195` |
| Re-transcription | A new revision; every new segment overlapping an unmatched old one in time is linked by `time_overlap` (many to many) | `importer.py:294-296` |
| Old versions | Kept in `im.source_segments`; `im.source_members` holds the current set. Only forgetting deletes. | `importer.py:197-199`, `pipeline.py:28-60` |
| Episode id | `uuid5(stage, sorted segment ids)`, so any segment change re-derives it. The old episode is retired, not deleted. | `segment.py:105-107`, `001_init.sql:22` |
| Items | Hang off episodes (`ON DELETE CASCADE`). They stay while the episode is retired but drop out of `pub` | `007_extraction.sql:29`, `021_evidence_how.sql:12` |
| Verdicts | Anchored to item, segment ids and quote; carried to the current item through supersession links plus `quote_score` | `feedback.py:74-86`, `014_verdict_carries.sql` |
| Labels | Resolve forward through supersession links | `label.py:56-64` |
| `reset --stage segment` | Deletes every episode, which cascades items, matches and evidence | `pipeline.py:145-147` |

So naming currently churns segment, episode and item, and (before v1.1) an Opus call. [PR #10](https://github.com/junglestyle/ideamachine/pull/10) removes the call and the re-match when the pseudonymized payload is unchanged. The segment, episode and item churn remain. That churn (duplicate items with conflicting verdicts, recurrence that counts re-capture) is what the re-key fixes.

## What the churn query decides

[churn_split.sql](m1/churn_split.sql) sorts every extraction request into one bucket, first match wins. It uses the supersession links above, so it measures cause directly instead of inferring it. Each result maps to an M1 action:

| Bucket dominates | What it means | M1 action (proposed) |
| --- | --- | --- |
| identical | Naming with unchanged pseudonyms | Already fixed by v1.1. Re-key for data hygiene, not cost |
| naming | Naming that changes pseudonyms (e.g. a cluster tagged `_me` becomes the owner) | Re-key. Re-extracting is correct here: the payload really changed |
| retranscribe, mostly **open** | Hearsay re-transcribed a conversation while it was still growing | **Hold extraction** until the conversation has stopped growing (below) |
| retranscribe, mostly closed | Bulk reprocess (e.g. the slice 10 re-diarization re-transcribed everything) | Accept as one-off cost. Don't build for it |
| regrouped | Episode boundaries moved as new speech arrived | Same fix as open re-transcription |

**Result (2026-10-27, observed).** 716 extraction requests, $14.89:

| Bucket | Requests | USD | Share of spend |
| --- | --- | --- | --- |
| identical | 132 | 2.73 | 18% |
| naming | 132 | 5.18 | 35% |
| retranscribe | 100 (0 open) | 1.74 | 12% |
| regrouped | 28 | 0.26 | 2% |
| first | 324 | 4.98 | 33% |

Two thirds of spend was repeats. 90 of the 100 re-transcriptions fell in the week of 2026-10-05, 10 the week after, and none since, and none was open speech. So it reads as a bulk Hearsay reprocess (inferred), the "mostly closed" row: accept it, don't build for it. Identical repeats are what v1.1 already skips. Naming is the biggest bucket left; per the table it's correct to re-read when the pseudonymized payload really changed, and the re-key stops the segment and episode churn around it.

First extractions fell from 195 (week of 10-05) to 9 and 5 in the following two weeks because capture was quiet, not because anything stalled.

**Hold extraction for growing conversations: dropped.** The churn result shows nothing for it to catch. If open re-transcription shows up later, the shape still works: store Hearsay's `open` flag in `im.source_conversations` and have `extract._pending` skip open conversations.

**Why not fuzzy re-linking of extractions** (time overlap plus text similarity, reusing the old items). Items quote the transcript verbatim and cite lines. Re-transcribed text means the quotes may no longer be in it, so reused items would cite speech that doesn't say what they quote. Fuzziness already exists at the right layer: verdicts carry to the re-extracted item by supersession plus `quote_score`. Re-extraction is $0.021 a call, so paying for it beats a correctness hole.

## The re-key and re-link (proposed)

Target: `import/stream-v4`, where segment id = `uuid5(conversation | sha256(conversation, start, end, text))`. Speaker, speaker confidence and text confidence become annotations, updated in place the way affect and delivery already are (`importer.py:211`).

1. **Code, behind tests first.** A fixture test where naming a speaker produces zero new segments, zero new episodes and zero requests. A second where a naming that changes pseudonyms produces exactly one new extraction. Then the importer change. `said_by` stops being stored as a name (today it's the local label, `007_extraction.sql:32`, mapped back at `extract.py:215`) and is derived at read time from the speaker annotation on the item's cited segments, with `items.said_by_segment_id` naming which one. Once naming no longer re-derives anything, a stored name would go stale. A stored request pseudonym won't work either, because S1 only means something inside one request.
2. **Pre-flight.** Sync every Lattice device so no offline feedback is queued. `pg_dump` the database. Run the [validation snapshot](#validation) and save its output.
3. **The migration is the importer's first run.** As built, there's no separate script: the stream-v4 importer re-reads every conversation that still has segments an older importer keyed, whatever its revision. Each old segment links to the new one with the same start, end and text, with method `rekey`; v3 versions that differ only in speaker collapse into one v4 row. `im.source_members` points at the v4 ids. No existing segment row is updated or deleted. It runs inside the normal `im run` transaction, so a failure leaves nothing behind.
4. **That same `im run`.** Every episode re-derives (all segment ids changed) and the old ones retire. Extraction finds byte-identical payloads, so v1.1 reuses every read and carries every match: expect **0 requests, $0**. `carry_verdicts` follows the `rekey` links and carries every verdict by quote (score 1.0, the quotes are identical). Labels resolve forward the same way.
5. **Validate**, then compare against the pre-flight snapshot.
6. Do steps 2–5 on a restored copy of the production dump locally before doing them for real.

**Observed bug the pre-flight works around:** `carry_verdicts` settles a retired item once (`verdict_carries` primary key). A verdict that arrives later on that item, such as a queued offline event, is never carried (`feedback.py:92`). That's true today for any churn. M2's observation ids fix it properly; for M1, drain the queues first.

**Episode identity, added while building.** An episode's id now also covers how its speakers would be pseudonymized (`segment.py`, through `egress.pseudonyms`). Without that, a naming that changes the pseudonyms (merging two voices, relabeling the owner) would leave the episode in place with a changed payload, and extraction would replace its items in place. Deleting an item cascades to its feedback events, which are ledger. With it, such a naming makes a new episode, the old one retires with its items, and verdicts carry as they do for any re-derivation. A naming that keeps the pseudonyms changes nothing but the label.

## Validation

The migration passes only if every check below holds after step 4. Each one is a query in the snapshot script [relink_check.sql](m1/relink_check.sql), run before and after, with the outputs diffed.

| Check | Pass when |
| --- | --- |
| Every verdict still reachable | Each item that had a latest verdict before is current with that verdict, or has a `verdict_carries` row to a current item with the same verdict. Count of unreachable = 0 |
| Every item cites real segments | Every current item's `source_segment_ids` is a subset of `im.current_segments`. Violations = 0 |
| Same idea set | `pub.ideas` returns the same `idea_id` set, and the same `n_evidence`, `kept`, `discarded` and `starred` per idea |
| No spend | No new `egress_log` rows for the extraction or match prompts during the run |
| No duplicate captures | Extractions per current episode ≤ 1.05 (the M1 bar) |
| Labels intact | `label.resolve_all` covers the same set of current segments |
| Nothing deleted | Row counts of `source_segments`, `egress_log`, `item_verdicts` and `pub.feedback_events` are ≥ before |

Any failure means roll back (next section) and fix on the local copy. No partial acceptance.

## Rollback

Restore the pre-flight `pg_dump` (57 MB, seconds) and redeploy the v3 importer. Pause Lattice for the window so no feedback lands between dump and restore. That's simpler than a scripted undo, and for one user it's the right trade.

A scripted undo also exists, because the migration is append-only: delete the `rekey` links and v4 rows, restore `source_members` from the links, then `im run` with the v3 importer. Episodes, items and verdicts re-derive or carry back the same way they came. It's slower to trust than a restore, so it's the fallback.

No raw audio is needed for either. Keep Hearsay's audio retention on hold until M2 validates anyway, as already decided.

## Prompt caching

It can engage: Opus 5.5's minimum cacheable prefix is 512 tokens, and the extraction system prompt is about 750. The refusal fallback, Opus 4.8, needs 1024, so a fallback call won't cache. That's rare and doesn't matter.

Change (proposed): put `cache_control: {"type": "ephemeral"}` on the system block in `llm.complete` (`llm.py`), which extraction calls through `egress.ask`. `llm.cost` already prices cache writes and reads. The default 5-minute TTL fits, because an hourly run sends its episodes back to back. Check it in production by looking for non-zero `cache_read_input_tokens` on the second and later calls of a run. That needs those two usage fields logged in `egress_log`, which they aren't today.

Worth: about $2 a month now, $20 at 10× (the architecture review's cost estimate). It's independent of the re-key, so it can ship any time.

## Open questions

- [x] Churn query output: answered 2026-10-27. No hold in M1.
- [x] `said_by` after a rename: answered 2026-10-27. Of 414 current items ([said_by_split.sql](m1/said_by_split.sql)), 55 (13%) cite more than one speaker, and in all 414 the stored `said_by` is one of the cited speakers. So `said_by_segment_id` is the first cited segment whose speaker matches `said_by`. The migration fills it deterministically, and new items get it at extraction.
- [x] Does anything outside `im.*` hold segment ids? Answered 2026-10-27: no. Lattice's source never mentions `segment_id`, and its offline queues hold item ids, which the pre-flight sync covers.
