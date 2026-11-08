# Vocabulary

_As of 2026-11-07. What each term in Idea Machine is, where it's stored, and what it isn't. Definitions say what a
thing is. Where it matters, they keep three things apart: the thing, how it's stored, and how it came about. Status
(what's built, what's planned) goes in a trailing **Status** note, and history is left to the decisions. Words that
mean two things, or two words that mean one, are listed at the end, each with the one meaning to use._

How the citations read:

- Migrations live in `src/im/sql/im/`. `045:28` means line 28 of `045_connections.sql`.
- Python is cited as `module.NAME` in `src/im/`, because its line numbers move.
- Docs are cited as file:line or by section, with decisions linked: [0007], [0008], [0009], [0010].
- **Not yet specified** marks a term the docs and code use but don't define. `[UNGROUNDED]` marks a claim that no doc
  or file settles.

`kind` on its own means nothing here. A kind is always qualified: capture kind, connection kind, lineage kind,
feedback event kind, speaker kind, triage kind, episode shape, digest point kind, stutter kind. The columns keep the
name `kind`.

Storage has three layers ([0006]). The **ledger** is append-only: what was received, extracted and decided
(`im.source_*`, `im.items`, `im.egress_log`, `pub.feedback_events`). The **graph** is the data Idea Machine derives
from the ledger and can rebuild from it (`im.ideas`, connections, lineage, weights). **`pub`** is the read model:
the web app, Lattice and the MCP connector read it, and their only write is to insert feedback events (SPEC §2).

## Speech

What Hearsay heard, as Idea Machine reads it. Hearsay's
[stream.md](https://github.com/junglestyle/hearsay/blob/main/docs/stream.md) is the contract (ROADMAP §3.1).

- **Stream.** Hearsay's published record of what it heard: `index.json`, one JSONL file per conversation, and
  `forgotten.json` (ROADMAP.md:105). Idea Machine reads it read-only and keeps its own copies (ROADMAP §3.3).
- **Conversation.** One recording session as Hearsay publishes it: one index entry and one file
  (`im.source_conversations`, 002:6-12). Episodes call it `session_id` (001:26, `pipeline.SEG_SQL`). _Not_ an episode.
  It has two revision markers:
  - `revision` is a hash of the conversation's file, NULL while it's untranscribed (002:9). The importer checks the
    file against it (`importer.py`).
  - `transcript_revision` marks which transcription the file's text comes from. While it stays the same,
    `utterance_id`s are comparable across imports. When it changes, old and new segments are matched by time overlap
    (ROADMAP.md:51-52, `importer.py`). What makes Hearsay change it is Hearsay's contract.
- **Utterance.** One speech record in a conversation file: speaker, times, text and confidences
  (`importer.Utterance`). Its `utterance_id` is numbered per import, and a re-transcription renumbers it
  (ROADMAP.md:55).
- **Source segment.** Idea Machine's stored copy of one version of an utterance (`im.source_segments`, 002:14-35). Its
  identity is `uuid5(conversation, start, end, text)` (`importer.IDENTITY`, [M1 design]). Speaker fields, arousal
  and delivery are annotations on it, updated in place and outside its identity. _Not_ an episode.
- **Turn.** Not a term: prose for an utterance or a segment (SPEC.md:22, 013:8, 019:1).
- **Current source segment.** A segment in the latest import of its conversation that hasn't been forgotten
  (`im.current_segments`, 002:65-68). Everything downstream reads only these.
- **Supersession.** A recorded correspondence between an old segment and the new segment that replaced it
  (`im.source_supersessions`, 002:47-54). Its `method` is the supersession rule that established the correspondence:
  - `utterance_id`: same utterance, same transcription, new content;
  - `time_overlap`: a new transcription, matched by time;
  - `rekey`: the same start, end and text under an older segment key ([M1 design]).

  It carries labels and verdicts forward. It doesn't decide which segment is current.
- **Forgetting.** The operation that deletes a segment, and everything derived from it, when an entry in
  `forgotten.json` overlaps it in time (ROADMAP §3.4, `pipeline._purge_tombstoned`).
- **Tombstone.** The textless record that a segment was forgotten (`im.source_tombstones`, 002:56-62).
- **Speaker kind.** Hearsay's class of a speaker: `owner`, `person`, `anonymous`, `stranger` or `unknown` (002:25,
  checked in `importer.py`).
- **Speaker basis.** Hearsay's statement of how it attributed a speaker (`speaker_basis`). The set of values is
  Hearsay's contract. Idea Machine's acceptance rule, below, is its own.
- **Owner-attribution class.** How strong the evidence is that I said a segment (`im.item_speaker`, 030:17-30; SPEC
  §1 "Who said it"). These are classes of evidence, not identities:
  - `me`: verified owner attribution, an owner turn by `voice`, `named` or `channel`;
  - `me_diar`: weak owner attribution, an owner turn on any other basis (`diarization` in practice). It counts only
    alongside a `me` observation;
  - `not_me`: not established as me. That covers other people, `unknown` speech, and a capture citing no segment.

  `cites_me` says that a quote spanning speakers includes `me` speech. An observation that isn't speech records only
  `me` or `not_me` (036:13). Hearsay names no one but me (SPEC.md:21-22).
- **Speaker label.** The display name on a segment: `me`, a name, `anon A` or `unknown` (002:27).
- **Tap.** A press on the pendant, recorded as a time (`source_conversations.taps`, 005:1-2). An episode's taps are
  those within 30 s of it (`triage.py`). To extraction a tap is a prompt cue: "A tap usually means I wanted something
  kept: look closely at what I said around that time" (`extract.SYSTEM_V3`). _Not_ tapping a star on the sky.
- **Places.** The places I named for a conversation, with their times (013:3-5).
- **Affect, arousal.** Affect is the stream's container for measures of how I sounded. Arousal is the measure in it:
  how animated I sounded, about 0 to 1, from the audio alone, meaning tone of voice and not sentiment (013:7-9).
  Idea Machine stores arousal per segment; there's no `affect` column. **Status:** nothing reads it.
- **Valence.** Whether what I said was positive or negative. Not recorded: Hearsay's stream has no valence, so
  recording it would start as a change to Hearsay's contract. Open (2026-11-07): if valence and arousal both reach
  Idea Machine, an observation would carry them from its segments, as facts to show, filter or light a lens by, not a
  rating ([0007]).
- **Delivery.** How I spoke: words per second, pauses and stutters, measured from word timings, not judged (019:1-6).
  A stutter kind is `repeat`, `restart` or `filler`. **Status:** nothing reads it (ROADMAP.md:49).
- **Open flag.** Hearsay's index flag for a conversation that's still growing. **Status:** SPEC.md:69-70 says V2
  stores it; no column does yet.

## Episodes and what's sent

- **Episode.** A contiguous stretch of one conversation's current source segments, grouped by segmentation rules: a
  silence gap, and a rule that splits off my monologues (`segment.py`, `config.SegmentConfig`). It's stored in
  `im.episodes` and `im.episode_segments` (001:20-49) and is the unit a model reads. Its episode shape (the `kind`
  column) is `monologue`, `conversation` or `others_only` (001:30). _Not_ a conversation.
- **Active episode.** An episode whose segments are still the current grouping. Its column is
  `episodes.current = true` (001:40). A changed grouping retires the old episode instead of deleting it.
- **Triage, label, route, project.** V1's first pass over episodes:
  - **triage**: a local model's answers about an episode (`is_self_thinking`, triage kind, project, `keep_score`),
    stored in `im.triage` (004). The triage kinds are `idea`, `task`, `decision`, `chatter` and `noise`
    (`triage.KINDS`);
  - **label**: my answer to the same questions (`im.labels`, 003);
  - **route**: `auto_file`, `review` or `escalate` (006:5). "A route is a label": nothing is dropped;
  - **project**: an entry in the hand-kept registry `im.projects` (003:4-10).

  **Status:** the local triage models are off by default (ROADMAP.md:100). Router v2 sends an episode to review
  when it has a tap, a note-to-self cue or a capture (`router.py`).
- **Payload.** The exact text of one request, system prompt excluded (`egress_log.payload`, 007:13;
  `egress.Payload`).
- **Payload line.** One numbered line of a payload, `[n] S1: text`. Line n is the episode's nth segment
  (`egress.py`). _Not_ a triage line, which is the local label and text, unnumbered (`triage.render`).
- **Egress log.** The record of every request that leaves the box: payload, policy, segments, model, provider
  (`served_by`) and cost (`im.egress_log`, 007:3-23; [0008]).
- **Speaker-alias policy (policy B).** A privacy policy: everyone's words go out verbatim, each speaker is replaced
  by a speaker alias, names said go out as said, and every request is logged ([0004]:24-33). Its identifier is
  `B1;speakers-pseudonymized;spoken-names-not-scrubbed;no-registry` (`egress.POLICY_VERSION`).
- **Speaker-and-name pseudonymization policy (policy C).** Policy B, plus every name mention replaced by a name
  pseudonym ([0008]). Its identifier names the name finder (`names.POLICY`), so changing the finder re-sends.
  **Status:** used for trials and audits until 0008's checklist is done. There is no policy A: the rule B replaced
  was "other people's words never leave the box" ([0004]:24-25).
- **Pseudonym.** A stand-in for an identity in a request, mapped back locally, so stored text keeps real names. There
  are two:
  - **speaker alias**: what stands for a speaker in one request: `me` for the owner, then `S1`, `S2`... in order of
    first appearance. `unknown` marks an unknown speaker; it isn't an alias (`egress.pseudonyms`);
  - **name pseudonym**: `P-` plus five characters of an HMAC under `IM_NAME_KEY`. The same name gets the same
    pseudonym in every request (`names.py`, [0008]:28-36).
- **Name mention.** A span of a segment's text that names a person, found by GLiNER, or matched from Hearsay's
  speakers and from config ([0008]:31-33).
- **Name finder.** The model and threshold that find name mentions, written `model@threshold` (040:6). The finder
  cache `im.segment_names` holds each segment's name mentions per finder (040).

## Capture

- **Thought.** What extraction captures: something someone expresses that could be said again, in a different
  conversation weeks later, and be the same thought. "The pills are a band-aid" is one; "toss it on seven" isn't
  (`extract.SYSTEM_V3`'s test).
- **Extraction.** The process of reading an episode with a model, under a prompt revision and a privacy policy, and
  storing the captures it returns. `im.extractions` records each read, including reads that found nothing (007:48).
  The **extractor** is the role that does it. _Not_ matching.
- **Capture (item).** One extraction result that represents a thought expressed in an episode. It holds its capture
  source citation, its quote, gist, said by, capture kind, capture tags and capture confidence, and its extraction
  metadata: model, extraction fingerprint and policy (`im.items`, 007:28). "Capture" and "item" are the same thing.
  _Not_ an observation: rereading the same speech makes a new capture of the same observation.
- **Capture on an active episode.** A capture whose episode is active. One active episode can hold captures from
  several models and prompt revisions (`im.items` is keyed by episode, model, fingerprint and policy). So "current"
  doesn't mean the latest read.
- **Capture source citation.** The payload lines a capture cites, stored as the segments they map to
  (`items.source_segment_ids`; [0004]:18). _Not_ an edge observation citation.
- **Prompt revision.** One of the extraction prompts' texts, `v1` to `v4` (`extract.PROMPTS`). `im.items` can hold
  captures from `v1` to `v3` (`extract.LIVE`). **Status:** `v4` is for trials only.
- **Extraction fingerprint.** A hash of the system prompt, schema, model and effort (`extract.prompt_version`). It's
  stored as `prompt_version` (007:40), and changing any part of it re-reads every episode. _Not_ the prompt revision.
- **Quote.** The capture's excerpt of its cited lines. Under `v4` it's exactly what the transcript says, character
  for character. Under `v1` to `v3` it could fix obvious transcription errors (`extract.SYSTEM_V3`, `SYSTEM_V4`).
- **Reading.** `v4` only: the quote repaired and resolved, with transcription fixed, fillers dropped and what "it" or
  "he" refers to spelled out (`extract.SYSTEM_V4`). **Status:** not stored yet.
- **Gist.** The thought restated in one line, with the context it needs to stand alone for someone who wasn't
  there (`im.items.gist`). Under `v4` it carries no label ("Joke:"), no transcription note and no attribution. _Not_
  a statement: a gist is this one occurrence's, and a statement is the cluster's.
- **Said by.** Who said the capture here: the current speaker label of the capture's `said_by_segment_id`, read at
  query time through `im.item_said_by` (029, [M1 design]).
- **Attributed to.** `v4` only: whose thought the speaker is passing on ("a nurse"), or empty. Said by stays whoever
  said it here (`extract.SYSTEM_V4`). **Status:** not stored yet.
- **Capture kind.** What sort of thought a capture expresses (`im.items.kind`, no CHECK, 007:31).
  - `extract.KINDS` (`v1`-`v3`): `idea`, `aphorism`, `joke`, `observation`, `project`, `task`, `decision`. Under `v3`
    a question is filed as `observation` unless it's an idea put as a question.
  - `extract.KINDS_V4`: `idea`, `aphorism`, `observation`, `question`, `technique`, `project`, `task`, `decision`.
    `question` is something someone is wondering about, unless it's an idea put as a question. `technique` is a
    principle or technique someone is teaching. A joke is filed by what it says (`extract.SYSTEM_V4`).

  The boundaries between the other capture kinds are **not yet specified**.
- **Capture tags.** The free-text strings a model returns with each capture, in the `themes` field (`extract.SCHEMA`;
  `im.items.themes`, 007:35). What they should be is **not yet specified**: no prompt says. _Not_ themes.
- **Capture confidence.** The model's number, from 0 to 1. What it measures depends on the prompt revision:
  - `v1` and `v2`: keep probability, the chance I'd keep the capture;
  - `v3` and `v4`: fidelity confidence, how sure the model is that the quote (or reading) and gist are what was said
    and meant (`extract.SYSTEM`, `SYSTEM_V2`, `SYSTEM_V3`, `SYSTEM_V4`).
- **Quote-source coverage.** The share of a quote's words found, in order, in its cited lines. Under 0.6, the quote
  isn't from its lines (`quote_in_lines`, 044:1-4; `quotes.FLAG`).
- **Quote-overlap score.** The share of the shorter of two quotes' words found, in order, in the other
  (`feedback.quote_score`).
- **Register.** How something is said, such as a joke or irony, as against what it says. **Status:** meant for a
  judging pass after extraction, not built (`extract.SYSTEM_V4`'s comment; ROADMAP-v2 §5 q10-11).
- **Note-to-self cue.** An explicit spoken instruction, "note to self", to keep the thought that follows. Extraction
  captures what follows it (`extract.SYSTEM_V3`), and the router sends the episode to review
  (`router.NOTE_TO_SELF`).

## Observations and clusters

- **Observation.** One source occurrence of a thought: one time it was said, written to the ChatGPT archive, or typed.
  It's a row of `im.observations`, and its `source` is `speech`, `archive` or `typed` (036:9). A speech observation
  is represented by the captures grouped into it: a capture joins when its segments descend from the observation's
  and its quote-overlap score is at least 0.4 against every capture already in it (034, [M2 design]
  §Observations). Archive and typed observations hold their own quote, time, owner-attribution class and source
  context (036:1-23). Observations are neutral and never judged (SPEC.md:27).
- **Source context.** What a non-speech observation is grouped by for recurrence: the ChatGPT conversation, or the
  `idea_add` event (`observations.conversation`, 036:7). _Not_ a Hearsay conversation.
- **Same point, distinct points.** Two occurrences make the same point when one is "the same idea" as the other:
  "restated, repeated, or a near-paraphrase", in the matcher's definition of `same_as` (`lattice.SYSTEM`). Points
  that aren't the same are distinct points.
- **Cluster.** The observations that make the same point, stored as an `im.ideas` row. Its id column is `idea_id`
  everywhere (030:3-4; SPEC §3). The matcher decides what clusters together; no threshold does ([M2 design]
  §Weight).
- **Cluster members.** A cluster's observations: those of its member captures, plus any non-speech observations that
  belong to it (`im.idea_observations`, 036:36-37). Someone else making my point joins my cluster as a not-me
  member ([M2 design]:88-89).
- **Capture membership.** The record that a capture belongs to a cluster: `im.idea_evidence(idea_id, item_id,
  relation, judged_by)` (008:16-24). Its membership provenance (`relation`) is either `origin`, the founding capture
  the cluster was made from, or `same_as`.
- **Cluster evidence.** The material a cluster shows as support: one capture for each member observation
  (`pub.evidence`, 037:1-5; `n_evidence`).
- **Matcher.** The role, and the process, that decides each new capture against its nearest clusters: `new`, `same_as`
  one, or `evolves` one (`lattice.match_stage`; `item_matches.decision`, 008:30, which adds `skipped`).
- **Match provenance.** What placed a capture in its cluster (`judged_by`, 008:21). It's either the matcher's version
  (`match-1:…`) or `carried:observation` (`lattice.CARRIED`). The second records that the capture followed its
  observation into the cluster, without a matcher judgment.
- **Recurrence.** An independent occurrence of a cluster's point by me. I count once for each distinct conversation
  (or source context) my `me` observations are in, and again within one conversation whenever an observation comes at
  least `recurrence_minutes` (30) after the last one counted. The first occurrence counts, so a cluster I said once
  has one recurrence. Owner-attribution classes `me_diar` and `not_me` don't recur; they're counted apart
  (`weight.recurrences`, `weight._signals`; SPEC §3).
- **Maturity.** How far a cluster has come, derived each time it's read, not stored (`im.idea_maturity`, 031:34-44):
  - `idea`: I kept it;
  - `candidate`: not kept, with a `me` observation on an active episode;
  - `observation`: neither of those. In prose, "a cluster without owner evidence".
- **Candidate.** An unkept cluster with at least one `me` observation on an active episode (031:32-44). Candidates
  differ only by weight ([M2 design]:39-40). A cluster with only `not_me` members never becomes one (SPEC.md:30-31).
- **Cluster weight.** A number per cluster, computed each run by a versioned formula over stored signals: my
  recurrences, a capped count of other members, connections to canonical clusters, and whether the cluster is
  canonical itself (`im.weights`, 031:48-55; `weight.weigh`). It's shown as a fact and orders nothing ([0007]).
  Lineage adds nothing ([0010]).
- **Proposal.** A pending request for my decision: keep or dismiss a cluster, or accept a suggested change to the
  graph or the lenses ([0010]:75-84).
- **Cluster proposal.** A proposal to keep a candidate, as canonical or pinned, or dismiss it (`im.proposals`,
  031:57-67). Its `outcome` is `canonical`, `pinned`, `dismissed`, or NULL while open. Cluster proposals come in
  order of when they were last said, latest first, up to `proposals_per_week` ([0007], `weight._eligible`). A
  dismissed candidate is proposed again only once its weight has grown by `repropose_margin`.

## Ideas and my decisions

- **Idea.** A kept cluster: one I kept as canonical or as pinned (maturity `idea`). Only I make one (SPEC.md:32-34,
  [0007]). _Not_ any `im.ideas` row, and _not_ the capture kind or triage kind `idea`.
- **Kept status.** How I keep a cluster (`kept_as`, `im.idea_kept`, 031:17-30): canonical, pinned or unkept. Canonical
  and pinned are two ways of keeping it, told apart by whether being kept adds to weight:
  - **canonical**: being kept adds weight to the cluster (the `canonical` signal), and to every cluster connected to
    it (`canonical_links`);
  - **pinned**: being kept adds no weight to it or anything else. A pinned cluster still has its own computed weight.

  Keeping a cluster either way makes it an idea. Switching between canonical and pinned changes neither maturity nor
  clustering, and unkeeping returns it to candidate or to a cluster without owner evidence (031:34-44; [M2
  design]:135). The archive's clusters and the ones I added start canonical, and starred ones start pinned.
- **Cluster provenance.** Where an `im.ideas` row came from (`origin`, 008:10, 023:1-9): `archive` (seeded from the
  ChatGPT archive), `captured` (made by the matcher from a capture) or `mine` (typed by me through `idea_add`).
- **ChatGPT archive.** The ideas and messages of my first "Idea Machine", a ChatGPT conversation: 36 seeded clusters
  (`archive_ref` `chatgpt#n`), their cross-references, and five themes (ROADMAP.md:142). Its messages are
  observations with source `archive` (036).
- **Title.** A cluster's short name, at most 8 words (`im.ideas.title`). The generated title is the matcher's
  (`lattice.SYSTEM`), and it's kept as `claude_title`. My latest correction overrides it (`im.idea_text`,
  011:15-27).
- **Statement.** A cluster's shared point, in one line, in the founding capture's distinctive wording
  (`im.ideas.statement`). The generated statement is written by the matcher, falling back to the gist
  (`lattice.match_stage`), and it's kept as `claude_statement`. My latest correction overrides it.
- **My note.** My latest `idea_note` on a cluster (`my_note`, 011:23-24).
- **Archive elaboration.** What the ChatGPT archive developed around one of its ideas (`im.ideas.notes`, 008:12).
- **Correction.** An `idea_correct` event that replaces a cluster's title or statement (011).
- **Feedback event.** A recorded action or judgment of mine (`pub.feedback_events`, 008:88-98). `source` says where I
  made it: `lattice`, `cli`, `mcp` or `emanation`. Where events disagree, my latest is the one with the highest
  `event_id`; `at` bounds time windows and orders nothing (SPEC.md:84-86, 043). The feedback event kinds, from the
  CHECK at 031:7-11:
  - on clusters: `idea_keep` (payload `as`: `canonical` or `pinned`), `idea_unkeep`, `idea_dismiss`, `idea_add`,
    `star`, `unstar`, `link`, `unlink`, `idea_note`, `idea_correct`, `idea_sensitive`;
  - on themes: `pin_theme`, `unpin_theme`, `rename_theme`, `reject_theme`, `merge_theme`, `split_theme`,
    `file_idea`, `unfile_idea`, `theme_sensitive`;
  - on vocabulary suggestions: `vocab_add`, `vocab_remove`;
  - legacy, still in the ledger but read by nothing that orders or gates ([0007]): `keep` and `discard` (nothing
    writes these), `item_keep`, `item_discard`, `item_star`, `idea_compare`, `digest_star`, `digest_dismiss`.
- **Keep, unkeep, dismiss.** To keep a cluster is to make it an idea, or to switch how it's kept (`idea_keep`), and to
  unkeep is to undo that (`idea_unkeep`). To dismiss is to turn a cluster proposal down: the cluster stays a
  candidate (`idea_dismiss`, 031:4-5).
- **Pin.** To keep a cluster as pinned. `star` is the legacy event name for the same action: "a star is a pin"
  ([M2 design]:277, 031:17-18).
- **Link, unlink.** My action of connecting two clusters, or taking that back. For each pair, my latest decides. A
  link makes the pair a connection, with display label `mine`. An unlink hides every stored connection on the pair
  whose method had already written ([connections storage] §My links).
- **Capture verdict (legacy).** My `keep`, `discard` or `star` on a capture (`im.item_verdicts`, 007:69, 010:3-5).
  `how` says how I made it: `one` (read on its own), `audit` (a spot check) or `bulk` (kept with the rest unread)
  (020). Verdicts carry to recaptures (014, `feedback.carry_verdicts`). Nothing orders or gates by them ([0007]),
  but `pub.ideas` hides a cluster whose every capture I discarded (043:27-28).
- **Comparison, rubric, score (legacy).** V1's ranking: `idea_compare` events (018), and a rubric of generality,
  novelty, compression and outside value, combined into `pub.idea_scores.score` (017, 025). [0007] threw them out as
  labels.
- **Merging and rejecting clusters.** **Not yet specified.** SPEC.md:98 and the [M2 design]:138 say merge and reject
  "stay", but no state, column or event exists for either on a cluster, and ROADMAP-v2.md:18 says ideas can't be
  merged yet. Themes can be merged and rejected (024).

## Graph

- **Graph.** The data Idea Machine derives: clusters as nodes, connections and lineage links as edges ([0006]).
- **Edge.** A connection or a lineage link (`checks.UNCITED`, `citations.py`).
- **Connection.** The relation that holds when two distinct points belong in the same tight neighborhood, beyond
  sharing a subject. It holds whichever of the two came first ([0010] §Decision). A connection is symmetric, one per
  pair, and stored as a row of `im.connections` with `a < b`, `method`, `confidence`, `cited_observation_ids` and
  `decided_at` (045:13-22). `im.shown_connections` adds my links and hides what I unlinked (045:94-105). In lenses
  and cluster weight, a connection counts 1. A pair may also have a lineage link, kept in its own table. _Not_ a
  shared subject, which a lens gathers.
- **Untyped connection.** A connection with no connection kind. The B4 sheet labels it `related`
  (`edge_audit.HEADER`).
- **Display label.** What `pub.connections` and `pub.card_links` put in their `kind` column (045:96-127):
  - `related`: any stored connection, whatever connection kinds it has;
  - `mine`: a connection I linked;
  - `evolves` and `synthesizes`: lineage links, in `pub.connections` only.

  _Not_ a connection kind.
- **Stand-in connection.** A connection written by something other than a connection judge. Two kinds of stand-in
  hold a pair until a judge decides it: embedding neighbors (method `embedding:…;k=3;min=0.7`, `lattice.relate`) and
  the ChatGPT archive's cross-references (`chatgpt-archive`) ([connections storage] §Tables). **Status:** a judge's
  `none` isn't stored yet.
- **Connection kind.** An optional annotation on a connection that says how the two relate. A connection may carry
  several, each with its own `method` (`im.connection_kinds`, 045:25-35). A connection kind is a suggestion: it never
  decides whether the connection exists, and it isn't graded ([0009]). Each adds `lens.KIND_WEIGHT` to its
  connection, 0 for now. The connection kinds:
  - `supports`: one is a reason to believe the other. Test: if A is true, is B more believable?
  - `elaborates`: one fills in the other, as a case, a description, an application, a generalization, or something
    built on it, without asserting that either came from the other. Test: does one fill in or carry forward the
    other?
  - `tension`: they pull against each other: a contradiction, a change of mind, a trade-off, or competing pulls.
    Test: does holding one make the other harder to hold as stated?
  - `sympathy`: the two resonate, or pull the same way ([0010]:39);
  - later, `antipathy` and `disproves` ([0010]:39). How they differ from `tension` and from contradiction is **not
    yet specified**.

  The tests are the B4 sheet's (`edge_audit.HEADER`). `supports` and `elaborates` have a direction: `a>b` means `a`
  supports or elaborates `b`, `b>a` the reverse, and `both` both (045:29; `edge_audit.HEADER`).
  **Status:** no connection-kind judge writes them yet.
- **Lineage.** The relation that asserts one cluster was derived from another, kept apart from connections
  (`im.lineage(from_id, to_id, kind)`, 045:37-46). Lenses follow it both ways; cluster weight doesn't count it. The
  lineage kinds:
  - `evolves`: a new cluster derived from an earlier cluster, developing it into a distinct idea of its own. It
    points from the new cluster to the earlier one (`lattice.SYSTEM`, [connections storage] §Tables);
  - `synthesizes`: a synthesized cluster derived from each of its source clusters ([0010]:75-79).
- **Edge-generation method.** What wrote a connection, connection kind or lineage link (the `method` column): a model
  and question version, embedding parameters, `chatgpt-archive`, the matcher's version, or `feedback` (SPEC §3; 045).
  `im.edge_methods` records when each method first wrote, so that an unlink hides only what had been written before
  it (045:49-53).
- **Edge observation citation.** The observations an edge rests on: the founding observations of its two ends
  (`cited_observation_ids`; `citations.py`; [M2 design] §Edges). `im check` fails on an edge that cites nothing where
  it could.
- **Embedding neighbors.** A cluster's nearest clusters by embedding similarity (cosine) (`pub.idea_neighbours`,
  026; `pub.card_neighbours`, 033). They nominate pairs and seed a lens's search, and never decide a connection
  ([0010]:96).
- **Graph neighborhood.** A set of clusters joined by connections. "A neighborhood of connected observations"
  ([0010]:33-36) means the observations of those clusters. What makes a set of clusters a neighborhood (how many,
  how densely connected) is **not yet specified**.
- **Synthesis pass.** The process that, given a graph neighborhood, writes a synthesis result and sends it to me as
  a proposal (ROADMAP-v2 Synthesis row; [0010]:75-80; [M2 design]:268-275). **Status:** not built.
- **Synthesis result.** A synthesis pass's output, citing observations. It's either:
  - **consolidation**: a new statement for one cluster ([M2 design]:270; ROADMAP-v2.md:16);
  - **multi-cluster synthesis**: one statement for several connected clusters.
- **Synthesized cluster.** The cluster that holds a kept multi-cluster synthesis, linked to each source cluster by
  `synthesizes`.
- **Bridge idea.** An idea that belongs to two or more lenses (themes, in the Lattice app), or is connected across
  them ([0010]:82-84). _Not_ a connection proposal, and _not_ a multi-cluster synthesis.
- **Connection proposal.** A proposal to make a pair of clusters a connection. It's drawn as a faint dashed link, and
  becomes a connection only if I accept it ([0010]:83-84, [M6 notes]:22-24). **Status:** not built.
- **Structure, structure description.** A structure is what an idea does or claims with its subject matter removed. A
  structure description is a model's line stating it: four per idea in `probe_audit`, where they're called facets,
  and one per idea in `analogy_spike`.
- **Analogy.** A structural correspondence across different subjects: two ideas about different subjects that share
  one named structure, such as "easy against perfect" ([0010] §Decision, amended 2026-11-07). An unkept analogy is
  still an analogy: a proposed pair with its named structure. It's never a connection.
- **Analogy lens.** The kept form of an analogy: a pinned lens seeded with the named structure, whose results are the
  structure's other instances ([0010]:51-58).
- **Analogy pass.** The process that proposes analogous pairs and names their structure. It asks Jev's
  `different_subjects` and `same_structure` questions (`jev.QUESTIONS`).
- **Tension pass.** The process that asks only whether a pair is in tension and proposes the pairs that are. Jev
  decides it by the mean of the `pulls4` wordings, yes at 0.45 or more ([0009] amendment; `jev.QUESTION_SETS`,
  `jev.CUT`).

## Views and lenses

- **Card eligibility.** A cluster is shown in the neutral view if its provenance is `archive` or `mine`, or if it has
  a capture on an active episode (033:58). That's what "every cluster that passed the gates" means (033:1-3).
- **Neutral view, card.** The neutral view is a list of every eligible cluster, with what's known about each and no
  score ([0007]; 033:1-3; `web.py`). A card is one cluster as that view shows it: a row of `pub.cards` (033:32), with
  title, statement, maturity, kept status, my note, observations by owner-attribution class, conversations, first
  and last said, its count of connections (`links`) and whether it's proposed.
- **Emanation.** The family of graph views whose layouts may change between runs ([0010]:66-69, ROADMAP-v2 §4).
- **Sky.** The Emanation view that draws every cluster as a star at its stored map position ([M6 notes] §First sky,
  `web.sky`):
  - brightness: recurrence, counted as distinct conversations;
  - sharpness: how recently it was said;
  - size: maturity and kept status (cluster without owner evidence, candidate, pinned, canonical);
  - color: its direction from the map's center.

  The home sky is the seedless lens.
- **Star (on the sky).** The glyph that draws one cluster. _Not_ the `star` event.
- **Stored map position.** A cluster's 2D coordinates, projected from embeddings by the pipeline and kept stable from
  run to run (015, 038, `lattice.place`). _Not_ a view layout: where a view draws a cluster. The sky draws at stored
  positions; a lens opened on its own may lay its results out afresh ([0010]:66-69).
- **Lens.** A defined relevance query over clusters, given a lens seed, or explicitly seedless ([0010]:45-49). It
  returns relevance for every cluster and writes no connections. Today `lens.relevance` computes it as personalized
  PageRank from the seed over connections (each 1 plus `KIND_WEIGHT` per connection kind) and lineage (both ways),
  plus the seed's 20 embedding neighbors, weighted by similarity (`lens.py`). What a lens definition holds beyond
  its seed is **not yet specified**.
- **Relevance.** A lens's number for each cluster, from 0 to 1: how closely the graph and meaning tie that cluster to
  the seed. The seed is 1, the rest are scaled so the most relevant other cluster is 1 too, and clusters the lens
  doesn't reach are 0 (`lens.relevance`). A seedless lens gives every cluster the same relevance. _Not_ weight,
  confidence or value.
- **Lens seed.** The input that sets a lens's focus: a cluster, or a subject, sentence, question, correspondence or
  structure ([0010]:45). **Status:** only a cluster can be a seed today.
- **Pinned lens, archived lens.** A pinned lens is one I keep at hand. An archived lens is one I've left: never thrown
  away, but kept with its definition, its cost (what was paid to compute it) and the graph version it read
  ([0010]:47-49). What a graph version is, is **not yet specified**. **Status:** lens storage isn't built.
- **Subject.** What something is about. A shared subject is what a lens gathers, and is no connection ([0010]
  §Context; `edge_audit.HEADER`, `none`).
- **Subject lens.** A lens seeded with a subject ([0010]:47, SPEC.md:36). **Status:** not built. Themes are to
  migrate into subject lenses.
- **Theme.** A stored category of clusters: `im.themes(name, description, origin, pinned)`, with membership in
  `im.idea_themes` (008:65-80). Its provenance is `archive`, `clustered` (proposed by Claude) or `mine`. Theme events
  edit it (024).
- **Lattice.** The legacy app, in its own repo, that reads `pub`: the map, review and the first MCP server ([0005]).
  **Status:** feature-frozen at `v1`, and archived at M6.
- **Trellis.** The stable view: pinned lenses laid out by hand, called Lattice v2 in [0010] ([0010]:70-73,
  ROADMAP-v2 §4). The first are a grid of lenses and the Tree of Life. **Status:** not built.
- **Tree of Life, correspondence.** The Tree of Life is the Qabalistic correspondence table, condensed into
  definitions and laid out on the sephirot and the paths between them. A correspondence is one entry of that table,
  such as 1/Kether, and each becomes a lens (ROADMAP-v2.md:57-58, [0010]:70-73).
- **Digest.** A weekly write-up of the ideas said that week, written by a model (`im.digests`, `im.digest_points`, 022;
  `digest.py`). It has a digest summary and 3 to 7 points, each citing ideas. Digest point kinds
  (`digest.SYSTEM`):
  - `recurring`: an idea I keep coming back to;
  - `convergence`: separate ideas that point at the same thing;
  - `sleeper`: an idea that matters more than it seemed when I said it;
  - `project`: something forming that I could build, write or do;
  - `outside_value`: an idea worth something to a thoughtful stranger.
- **MCP connector.** The tool interface through which MCP clients, such as claude.ai, query Idea Machine and submit
  feedback events. Its tools' names, arguments and results are a contract (SPEC §2, [0005]).
- **Sensitivity annotation, demo mode.** A sensitivity annotation is the categories a cluster or theme touches:
  `sex`, `gender_politics`, `political_extremism`, `violence`, `racism`, `profanity`. A model proposes them, and my
  marks win (027; `sensitivity.CATEGORIES`). Demo mode is a presentation mode that leaves out what's annotated.
- **Vocabulary suggestion.** A rare word that Hearsay's transcription should know. Suggestions come from my
  corrections, the archive and misheard quotes, and I approve them (`im.vocab_candidates`, 028:1-13). Approved ones
  are written to `vocab.json` (ROADMAP.md:165, deploy.md:130).

## Models and measurement

Each role decides one thing. The model behind it can change, so models are listed apart from roles.

| Role | Decides | Model now |
|---|---|---|
| Extractor | the captures in an episode | `claude-opus-5-5`, effort `medium` (`config.ExtractConfig`) |
| Matcher | `new`, `same_as` or `evolves` for each capture | `[match]`, default `claude-opus-5-5` (`config.ModelConfig`); Jev planned (ROADMAP-v2 M4) |
| Connection judge | whether a pair is connected | Terra, chosen on B4 (SPEC §5). **Status:** not writing yet; stand-ins hold |
| Connection-kind judge | the connection kinds on a connection | none yet |
| Tension judge | whether a pair is in tension, as a proposal | Jev, `pulls4` |
| Name finder | name mentions, for policy C | GLiNER (`urchade/gliner_medium-v2.1`) |
| Embedder | embeddings for neighbors, positions, lens search | `BAAI/bge-small-en-v1.5`, local |
| Theme filer, scorer, sensitivity tagger, vocabulary checker, digest writer | as named | `[themes]`, `[scores]`, `[sensitivity]`, `[vocab]`, `[digest]`; default Opus (`config.ModelConfig`) |

- **Opus, Sonnet, Haiku.** Anthropic's `claude-opus-5-5`, `claude-sonnet-5-5` and `claude-haiku-5-5` (`llm.PRICES`). A
  refusal falls back to `claude-opus-4-8` ([0004]).
- **Terra, Sol, GLM.** Other providers' models, reached through OpenRouter: `openai/gpt-6.1-terra`,
  `openai/gpt-6.1-sol`, `z-ai/glm-5.3` and its flash version ([0008]).
- **Jev.** TypeSafe's decision model (`jev.MODEL`). It answers typed questions about structured state with
  probabilities, and isn't a chat model (`jev.py`). Its terms:
  - **Noul**: one atomic yes/no question, answered with the probability of yes. Nouls are combined in code;
  - **choice**, **score**: Jev's other question types. Not used (ROADMAP-v2 §5 q11);
  - **question set**: the wordings that decide (`jev.QUESTION_SETS`);
  - **probe**: a question asked only to measure (`jev.PROBE_SET`);
  - **cut**: the probability at or above which a ranking counts as yes (`jev.CUT`).
- **Laya, logreg, local LLM.** V1's triage backends (`backends.py`, [0001], [0002]). **Status:** off by default.
- **Corral.** The job queue that runs the pipeline on the NAS, and GPU jobs on the dev box (deploy.md:4-6,
  109-120).
- **Trial.** A comparison of an extraction model or prompt against a reference read (normally Opus's) of the same
  sample of episodes, under policy B or C (`im trial`; `trial.py`; [0008]:38-43). Trial captures are kept apart in
  `im.prompt_trials`. Its counts:
  - **recall**: the share of the reference's captures the trial also made. A match is overlapping segments plus the
    quote-overlap score, as verdicts are carried;
  - **extra captures**: what the trial captured that the reference didn't;
  - **replicate**: a second read by the reference model, whose recall is the ceiling.
- **Benchmarks.** Each has an id and a durable name (ROADMAP-v2 §2):
  - **B1, match agreement**: how often a matcher agrees with the logged Opus match decisions, and its `same_as`
    precision on the subset I validated;
  - **B2, keep prior**: retired;
  - **B3, extraction recall**: a trial model's recall of the reference's captures;
  - **B4, connection agreement**: agreement and precision on whether a pair is connected, against 200 pairs I
    marked blind;
  - **B5, observation dedupe**: false merges among 50 merged observations. A false merge is a merged pair of
    captures I mark different (or unsure); a false split is a near-miss pair I mark same ([M2 design]:225-227);
  - **B6, local title preference**: blind pairs of titles, local against Claude's.
- **Evaluation sheet, mark.** An evaluation sheet is a set of examples for me to judge by hand, with the systems that
  made them hidden. A mark is one recorded judgment (`edge_audit`, `dedupe_audit`, `probe_audit`, `summary_audit`).
- **B4 labels.** The marks the connection-agreement sheet allows (`edge_audit.LABELS`, `HEADER`):
  - `supports`, `elaborates`, `tension`: connected, with that connection kind;
  - `related`: an untyped connection;
  - `none`: no connection;
  - `same`: the same idea said twice, a merge the matcher missed;
  - `unsure`.
- **B5 marks.** `same` means two captures of one stretch of speech are the same capture ("the same point, from the
  same words"), and `different` means they aren't (`dedupe_audit`).
- **Novelty marks.** Discovery sheets (analogy, tension) mark a pair `new` (it holds, and I hadn't connected it),
  `known` (it holds, and I had it) or `no` ([0009] amendment, `probe_audit.MARKS`).
- **`im tune`.** Previews a change to the weight knobs, at no cost ([M2 design] §Weight).

## Telling them apart

- **Capture, observation, cluster, idea.** Another read of the same occurrence is a new capture of the same
  observation. Another occurrence of the same point is a new observation in the same cluster. If I keep the cluster,
  it's an idea.
- **Candidate.** An unkept cluster with `me` evidence on an active episode. Without that evidence, it's a cluster
  without owner evidence; once I keep it, it's an idea.
- **Cluster evidence, recurrence.** Cluster evidence counts member observations. Recurrence counts only my
  occurrences that meet the independence rule. Several captures of one occurrence are one observation, and at most
  one recurrence.
- **Quote, reading, gist.** The quote preserves the wording, the reading repairs and resolves it, and the gist
  restates it to stand alone.
- **Gist, statement.** A gist is one occurrence's; a statement is the cluster's shared point.
- **Capture kind, register.** The capture kind is what is expressed; the register is how. A proposal can be said as a
  joke, and a standalone joke may fit no capture kind.
- **Subject, theme, lens.** What something is about is a subject. A stored category with members is a theme. A
  defined relevance query is a lens.
- **Connection, lineage.** If a relation holds without knowing which of the two came from the other, it's a
  connection. If it says one was derived from the other, it's lineage. A pair can have both.
- **`evolves`, `elaborates`.** `evolves` is historical derivation, a lineage link from the new cluster to the earlier
  one. `elaborates` is an explanatory relation, a connection kind that asserts no descent.
- **Canonical, pinned.** Does being kept add weight? Canonical does; pinned doesn't. Pinned isn't zero weight.
- **Connection proposal, connection.** A connection proposal is something I haven't accepted yet. A connection is
  stored, or linked by me.
- **Analogy, analogy lens.** An analogy is a correspondence between ideas. An analogy lens is the query I keep for
  it.

## Overloaded or drifting terms

Each entry lists the meanings found, with evidence, and then the one meaning to use. Column and code names stay as
they are; prose follows the meanings given here.

### kind

- Capture kind: `im.items.kind` (007:31), `extract.KINDS`, `extract.KINDS_V4`.
- Triage kind: `triage.KINDS`, `label.KINDS`.
- Episode shape (001:30), speaker kind (002:25) and stutter kind (019:2).
- Feedback event kind (008:91, 031:7-11).
- Connection kind (045:28) and lineage kind (045:40). The dropped `im.idea_links.kind` mixed `evolves`, `related`
  and `archive` (008:42).
- The display label in `pub.connections` and `pub.card_links` (045:96-127). `checks.UNCITED` adds a pseudo-kind,
  `connection`.
- Digest point kind (022:21), and `pub.sensitivity.kind`, which is `idea` or `theme` (027).

**Use:** always qualify it. Bare "kind" means nothing (owner's decision, 2026-11-07).

### prompt version

`prompt_version` (007:40) is a hash of the system prompt, schema, model and effort (`extract.prompt_version`). The docs
also say "prompt `v3`" for a prompt's text (ROADMAP-v2 §5 q6).

**Use:** a prompt revision is `v1` to `v4`. The extraction fingerprint is the hash, stored as `prompt_version`.

### observation

- An `im.observations` row (030:1-4).
- The maturity `observation`, meaning a cluster with no `me` evidence (030:41-50).
- The capture kind `observation` (`extract.KINDS`). `edge_audit.CONCRETE` reads it as "about a case, not a claim",
  and `v3` files questions under it.
- Any capture: "You extract observations" (`extract.SYSTEM_V3`).
- "A stretch of speech" ([0006]:33).

**Use:** an observation is a source occurrence of a thought, stored as an `im.observations` row. The maturity value
is "a cluster without owner evidence" in prose. The capture kind keeps its name until capture kinds matter
downstream (ROADMAP-v2 §5 q11). If its meaning is "about a case, not a claim", it should be renamed then. Questions
already have their own capture kind under `v4`.

### idea, cluster, candidate

- Every cluster is an `im.ideas` row with an `idea_id` (031:49, 031:60), and `pub.ideas` lists clusters of any
  maturity (043:27-28).
- "Idea" is also the maturity `idea` (031:36), the capture kind and triage kind `idea`, and "You extract ideas" in
  the `v1` prompt (`extract.SYSTEM`).
- "Candidate" is also the nearest clusters shown to the matcher ("Candidates:", `lattice.SYSTEM`), a model under
  trial ([0008]:40) and `im.vocab_candidates` (028).
- "Cluster" is also the archive's five clusters (ROADMAP.md:143), a theme's provenance `clustered` (008:69), and
  Hearsay's voice clusters (`speaker_basis` `cluster`, 002:28; [M1 design]:41).

**Use:** an idea is a kept cluster, a cluster is any `im.ideas` row (`idea_id` is a cluster id), and a candidate is
the maturity. For the others: the matcher's nearest clusters, the trial model, vocabulary suggestions, archive
themes and voice clusters. Whether the capture kind `idea` means "a suggestion" is not established.

### summary, statement, gist, title

- No capture or cluster has a `summary` column. The only one is the digest summary (022:11).
- On the B4 and probe sheets, "Claude's summary" is a cluster's title and statement (`edge_audit.side_data`,
  `probe_audit`). SPEC.md:115 speaks of each side "as said and summarized".
- `summary_audit`, `match_bench`, `miss_audit` and Jev's `summary` questions use "summary" for a capture's gist.
- ROADMAP-v2 §5 q9 means both.
- `archive_observations.py` calls the archive's text "ChatGPT's summary".

**Use:** a capture gist, a cluster title and statement, and a digest summary. Where a model wrote one, say
"generated" ("the generated title and statement"), with the model as provenance.

### link, connection, edge

- A connection is an edge ([0010]:28). Edges are connections plus lineage (`checks.UNCITED`, `citations.py`).
- `pub.cards.links` counts connections only (033:33-35), and the neutral view prints that count as "N edges"
  (`web._facts`) under a "Connections" heading (`web.card`). The sky's `links` include lineage (`web.sky`).
- "Link" is my action (008:91), an archive cross-reference (`lattice.py`), a "lineage link" (`lens.py`), weight's
  "links to canonical ideas" (SPEC.md:107), and V1's connections (ROADMAP.md:138).
- [0006]:49-56 describes edges as `im.idea_links` rows with `evidence_item_ids`, a column that never existed.

**Use:** "link" is my action in prose. A connection is a connection, a lineage link is lineage, and "edge" covers
both. A count says which it counts: "N connections", not "N edges".

### related

- An embedding neighbor: the old `idea_links` kind (008:37-42), `lattice.RELATED_K`, and the "related edges" of
  026:3.
- The display label of every stored connection, including ones with connection kinds (045:101).
- On B4, labels 1-2: "the same subject, but none of the above". Labels 3: connected, with no connection kind
  (`edge_audit`).
- [0010]:42-43: "one with no kind is simply related".

**Use:** the term is **untyped connection**. `related` survives only as the B4 label for it and as the display label
that views give every stored connection (owner's decision, 2026-11-07).

### theme, topic, subject

- A theme is an `im.themes` row (008:65-73). A capture's `themes` field is capture tags (007:35).
- "Subject" is what a lens gathers ([0010] §Context), a subject lens's seed ([0010]:47), and Jev's
  `different_subjects`.
- "Topic" has no definition. In use it means subject, measured by embedding similarity (`analogy_spike`,
  `probe_audit`), and it's also a prefix a gist shouldn't open with (`extract.SYSTEM_V4`).

**Use:** subject (what it's about), theme (a stored category with members) and lens (a relevance query). "Topic"
becomes "subject". The `themes` field is capture tags, whose meaning is still not specified.

### evidence

- Capture membership: `im.idea_evidence` (008:16).
- Cluster evidence: `pub.evidence`, one row per observation since 037 (037:1), and `n_evidence`.
- The ledger as a whole: the "evidence ledger" ([0006]:17).
- What an edge cites: "each cites its evidence" (SPEC.md:13), "evidentiary edges" ([0005]:21), and "shared evidence"
  (SPEC.md:119).
- What recurrence isn't ([M2 design]:83-84).
- The argument sense, in `supports`: "evidence or an argument for it" (`edge_audit.HEADER`).

**Use:** capture membership (the record), cluster members (the observations) and cluster evidence (what's shown as
support). An edge has edge observation citations. The ordinary sense, as in `supports`, is fine.

### weight

- Cluster weight (031:48-55), and its snapshot on a cluster proposal (031:61).
- `pub.connections.weight`, which is a connection's confidence or 1 (045:110). The dropped `idea_links.weight`
  (008:43).
- How much a connection pulls in a lens: 1, plus `KIND_WEIGHT` per connection kind ([0010]:60-62, `lens.py`).
- "A tap is a weight, not a rule" ([0004]:20).

**Use:** cluster weight. A connection has a connection confidence. What a lens adds per connection is lens
traversal weight. A tap is a prompt cue.

### confidence

- Capture confidence, whose meaning changed under one column: keep probability under `v1` and `v2`, fidelity
  confidence under `v3` and `v4` (`extract.SYSTEM`, `SYSTEM_V3`).
- Connection confidence. For embedding stand-ins it holds the cosine ([M2 design]:159), which is a similarity, not a
  confidence.
- A triage answer's confidence (004:12), `speaker_conf` (Hearsay's owner similarity) and `asr_confidence` (002:30-31).

**Use:** say whose and in what: keep probability, capture fidelity confidence, connection confidence,
speaker-attribution confidence, ASR confidence. Cosine is embedding similarity. A capture confidence compares only
within one prompt revision.

### proposal

- A cluster proposal (`im.proposals`, 031:57).
- A theme Claude proposed (provenance `clustered`; "only unpinned proposals" can be rejected, 024:107).
- A connection proposal ([0010]:83, [M6 notes]:22), a synthesis result ([0010]:75) and a discovery ([0009]:60-61).
- V1's Phase 5 proposals: agent work for me to approve (ROADMAP.md:226).
- The order: "highest weight first" in the [M2 design]:144 and 031:69, but by when last said since [0007].

**Use:** a proposal is a pending request for my decision. Only cluster proposals are stored, and only they are
ordered by when last said, latest first.

### keep, pin, star, dismiss

- Keep: `idea_keep` (031:4). Also a capture verdict `keep` (007:69), the legacy events `keep` and `item_keep`,
  `pub.ideas.kept` (a count of verdicts), and the triage question `keep_score`.
- Pin: keeping a cluster as pinned (031). Also `themes.pinned` and `pin_theme` (008:71), and a pinned lens
  ([0010]:47).
- Star: the `star` event, which is a pin (031:17-18). Also the capture verdict `star` (010:3-5), `digest_star`, and
  a cluster drawn on the sky.
- Dismiss: `idea_dismiss`, `digest_dismiss`, and the outcome `dismissed`.

**Use:** to keep a cluster, a capture verdict, to pin a cluster, theme or lens, and to dismiss a proposal. The action
is "pin". "Star" is the sky glyph, and also the legacy event name for a pin.

### merge, reject, same

- SPEC.md:83 and 98 and the [M2 design]:138 list merging and rejecting clusters. Nothing implements either
  (031:7-11), and ROADMAP-v2.md:18 says ideas can't be merged yet.
- To merge or reject a theme (024).
- To group captures into an observation; B5 counts these "merged observations" (`dedupe_audit`).
- The matcher's `same_as`, a same-point match (008:30).
- B4's `same`: the same idea said twice. B5's `same`: the same capture.

**Use:** merge themes, reject a theme, group captures into an observation, and a same-point match. B4's `same` and
B5's `same` are two different marks. Merging and rejecting clusters is not yet specified.

### me, mine

- `me`: verified owner attribution (030:23).
- "A cluster of mine": one with a `me` observation (SPEC.md:11).
- Provenance `mine`: a cluster typed by me (023:1). Display label `mine`: a connection linked by me (045:97). Theme
  provenance `mine`: a theme I created by splitting another (024:127).
- `speaker_label` `me`, and the owner's speaker alias `me` in a payload (`egress.pseudonyms`).

**Use:** as an owner-attribution class, `me` says the evidence shows I said it; it isn't an identity. "Mine" is always
qualified: typed by me, linked by me, a theme created by me.

### archive

- The ChatGPT archive: cluster provenance, `observations.source`, `chatgpt-archive`, the old link kind, and the
  vocabulary source `archive` (028:13).
- An archived lens ([0010]:48), and the Lattice repo, archived at M6.
- My whole collection: "my idea archive" in the `v1` and `v2` prompts (`extract.SYSTEM`).

**Use:** the ChatGPT archive (the seed archive), an archived lens, the archived Lattice repository. The whole
collection is "the graph".

### origin

- Cluster provenance: `im.ideas.origin` (008:10).
- Membership provenance: `idea_evidence.relation` `origin`, the founding capture (008:16-20). Edge observation
  citations cite "origin observations" (`citations.py`).
- Theme provenance (`themes.origin`) and theme-membership provenance (`idea_themes.origin`: `archive`, `assigned`,
  `proposed` or `mine`, with no CHECK, 008:78).

**Use:** cluster provenance, membership provenance, theme provenance. The capture or observation a cluster was made
from is its founding capture or founding observation; "first" is ambiguous.

### synthesis, bridge

- The synthesis pass ([0010]). In V1, "periodic synthesis" was the weekly digest (ROADMAP.md:164), and the
  "Synthesis (Claude)" phase was extraction (ROADMAP.md:188, 248).
- A bridge idea ([0010]:82). The [M2 design]:271 calls a synthesis over several clusters "bridging". Emanation's
  notes once mixed bridges with connection proposals ([0010]:22-24).

**Use:** the synthesis pass (process), a synthesis result (output) and a synthesized cluster. A bridge is always a
bridge idea, and M2's "bridging" is a multi-cluster synthesis.

### neighborhood, neighbors

- A graph neighborhood, and "a neighborhood of connected observations" ([0010]:33-36).
- Embedding neighbors: `pub.idea_neighbours`, `pub.card_neighbours`, `lattice.RELATED_K`. B4 "drew its pairs from
  neighborhoods" in this sense (`probe_audit`, ROADMAP-v2.md:87).
- The "semantic neighborhood" that places and colors a star ([M6 notes]:15, 37).

**Use:** embedding neighbors (by similarity) and a graph neighborhood (a set of clusters, by connections).
Observations "in" a neighborhood belong to its clusters.

### analogy

- SPEC.md:124-125 and [0009]:28 make analogy a pass over pairs, beside tension. The 0010 amendment (2026-11-07)
  makes a kept analogy a lens that writes no connection.
- `lens` is also the name of one analogy wording (ROADMAP-v2.md:96).

**Use:** an analogy is a structural correspondence across subjects, and an analogy lens is its kept form. The
analogy pass only proposes.

### lattice

- The data: "the idea lattice" (008:1, ROADMAP Phase 2), and "the lattice's relationships" ([0008]:17).
- The Lattice app ([0005]), and Lattice v2 ([0010]:70).
- `src/im/lattice.py`: the matcher, the embedding neighbors and the positions.

**Use:** Lattice names only the legacy app. The stable view of pinned lenses is Trellis (owner's decision,
2026-11-07), and the data is the graph. The module's name stays.

### evolves, elaborates, develops, extends

- `evolves`: lineage from a new cluster to the earlier one it develops, refines, extends or applies
  (`lattice.SYSTEM`, 045:40).
- The B4 pilot folded `evolves` into `extends`. Labels 2 renamed `extends` to `elaborates`, saying "a later version
  stays `evolves`". But the sheet's header tells me to mark a later version `elaborates` if it refines the earlier
  one (`edge_audit`).
- Jev's match questions say `develops` for `evolves` (`jev.QUESTIONS`, `match_bench`).

**Use:** `evolves` is a new cluster derived from an earlier one (new → earlier). `elaborates` is explanation without
asserted descent. `develops` is wording inside Jev's questions, not a term. The B4 sheet's later-version rule
conflicts with `evolves`.

### recurrence

- The recurrence count, as defined above (SPEC §3, `weight.recurrences`).
- The sky's brightness: a count of distinct conversations only ([M6 notes]:67).
- `pub.idea_scores.recurrence`: V1's scaled count of captures (017:18-26), which SPEC §3 rules out.

**Use:** recurrence is my independent occurrences, the first included. The sky shows a conversation count, and the
legacy score has a capture count.

### note, label, point

- Note: my note (`idea_note`), the archive elaboration (`im.ideas.notes`), a verdict note, an audit note (a sheet's
  `note:` and `analogy:`), and the note-to-self cue.
- Label: the speaker label, a triage label (`im.labels`), a B4 label, the prefixes gists open with ("Joke:", ROADMAP-v2
  §5 q10), and "No value label exists" ([M2 design]:39).
- Point: what an occurrence says ("the same point", [M2 design]:83), "two distinct points" ([0010]:28), a cluster
  ("the neighborhood's best existing point", [0010]:80), and a digest point (022).

**Use:** my note, archive elaboration, verdict note, audit note, note-to-self cue; labels always qualified. A point
is what an observation says; a graph node is a cluster, not a point; a digest's are digest points.

### method, judged_by

- The edge-generation method (045). The supersession rule (002:51, whose comment omits `rekey`), which records how a
  correspondence was established, not what wrote it. `feedback.CARRY_METHOD`, and the positioning method
  (`lattice.POSITIONS_METHOD`).
- Match provenance (`judged_by`, 008:21). The matcher's version is also the edge-generation method of the `evolves`
  rows it writes (`lattice.match_stage`). `carried:observation` sits in `judged_by` without being a judgment.

**Use:** edge-generation method, supersession rule, positioning method, match provenance.

### conversation, session, segment, current, line

- `session_id` is `conversation_id` (`pipeline.SEG_SQL`, ROADMAP.md:48). A non-speech observation's `conversation`
  is a source context (036:7).
- Utterance, segment and turn name one unit at different stages (`importer.Utterance`; 002:14; 013:8).
- "Current" is said of segments (002:65), episodes (`episodes.current`, 001:40) and captures. A "current capture"
  can be misread as the latest read.
- A payload line is `[n] S1: text` (`egress.py`). A triage line is `label: text` (`triage.render`).

**Use:** conversation, utterance, source segment and source context. "Turn" goes. Current source segment, active
episode, capture on an active episode. Payload line and triage line.

### pseudonym, citation

- Pseudonym covers both a speaker alias and a name pseudonym (`egress.pseudonyms`, `names.py`). `unknown` is in the
  speaker map but isn't an alias.
- Citation covers both a capture's source citation (`source_segment_ids`) and an edge's observation citation
  (`cited_observation_ids`).

**Use:** speaker alias or name pseudonym, and capture source citation or edge observation citation.

### speaker basis

002:28 lists `voice | diarization | named | cluster | none`. The code accepts `channel` as verified (030:23), and
SPEC.md:25 names `voice`, `named` and `channel`. Hearsay's enumeration and Idea Machine's acceptance rule are two
contracts, and neither list here cites Hearsay's: [UNGROUNDED].

**Use:** take the values from Hearsay's contract, fix the 002 comment to match, and state the acceptance rule apart.

## Not yet specified

These terms are in use, but no doc or file defines them:

- How `antipathy` differs from `tension`, and how `disproves` differs from contradiction ([0010]:39).
- What a lens definition holds beyond its seed, and what a graph version is ([0010]:45-49; [0006]:61-62).
- What makes a set of clusters a graph neighborhood that triggers a synthesis ([0010]:33-36).
- The boundaries between capture kinds other than `question` and `technique`. Capture tags.
- Merging and rejecting clusters (SPEC §2-3, [M2 design]:138).
- Storing a connection judge's `none` ([connections storage] §Tables).
- The full set of speaker basis values (Hearsay's contract).

## Stale definitions

- Stored map position vs view layout: `lens.py`'s docstring calls positions "one map for every view", and `sky.html`
  says they "never move". Since [0010], positions are still stored, but views lay clusters out as they need, and
  Emanation need not stay put.
- Derived maturity vs stored status: [0006]:31 and 49-50 describe `im.ideas` with a `status` column, "promotions" and
  provisional ideas. Maturity is derived (031:34-44), and no status column exists.
- Lens storage: [0010]:94-95 says it replaces `im.lens_cache` "in SPEC §3". SPEC §3 no longer names that, and no such
  table exists.
- Segment identity vs annotations: ROADMAP.md:47 says the speaker is part of a segment's identity. It's frozen at V1;
  [M1 design] made the speaker an annotation.
- The episode identity recipe: 001:20 gives `uuid5(stage_version, input_hash)`. The code adds the speakers' aliases
  (`segment.py`, [M1 design]:77).
- Pinning and maturity: the [M2 design]:40 says "Pinning never changes weight, maturity or clustering". Keeping a
  cluster as pinned makes it an idea (031:34-44; [M2 design]:135). The sentence holds only for switching an idea
  between canonical and pinned.
- The statement: the [M2 design]:95-96 says a candidate's statement is the gist of its first observation. The matcher
  writes the statement, and uses the gist only as a fallback (`lattice.match_stage`).
- Analogy: SPEC.md:124-125 and [0009]:28 predate 0010's analogy amendment.

[0001]: decisions/0001-laya-spike.md
[0002]: decisions/0002-first-triage-eval.md
[0004]: decisions/0004-claude-extraction.md
[0005]: decisions/0005-presentation-inside-idea-machine.md
[0006]: decisions/0006-graph-in-postgres.md
[0007]: decisions/0007-neutral-proposals.md
[0008]: decisions/0008-other-providers-and-names.md
[0009]: decisions/0009-edges-connection-graded-kind-suggested.md
[0010]: decisions/0010-connections-kinds-lenses.md
[M1 design]: design/m1-segment-identity.md
[M2 design]: design/m2-observations-candidates-ideas.md
[M6 notes]: design/m6-emanation.md
[connections storage]: design/connections-storage.md
