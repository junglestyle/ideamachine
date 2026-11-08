# Vocabulary

_As of 2026-11-07. What each word in Idea Machine means today, where it's stored, and what it isn't. The definitions
are the ones the decisions, the schema and the prompts use. History is left to them. Where a word means two things,
or two words mean one, the last section lists where and proposes one meaning._

How the citations read:

- Migrations live in `src/im/sql/im/`. `045:28` means line 28 of `045_connections.sql`.
- Python is cited as `module.NAME` in `src/im/`, because its line numbers move.
- Docs are cited as file:line or by section, with decisions linked: [0007], [0008], [0009], [0010].
- `[UNGROUNDED]` marks a definition that no doc or file settles.

Storage has three layers ([0006]). The **ledger** is append-only: what was received, extracted and decided
(`im.source_*`, `im.items`, `im.egress_log`, `pub.feedback_events`). The **graph** is what Idea Machine thinks that
means, rebuildable from the ledger (`im.ideas`, connections, weights). **`pub`** is the read model: the web app,
Lattice and MCP read it, and their only write is to insert feedback events (SPEC §2).

## Speech

What Hearsay heard, as Idea Machine reads it. Hearsay's
[stream.md](https://github.com/junglestyle/hearsay/blob/main/docs/stream.md) is the contract (ROADMAP §3.1).

- **Stream.** Hearsay's published output: `index.json`, one JSONL file per conversation, and `forgotten.json`
  (ROADMAP.md:105). Idea Machine reads it read-only and keeps its own copies (ROADMAP §3.3).
- **Conversation.** One recorded conversation: one index entry and one file. `im.source_conversations` (002:6-12)
  holds its `revision` (the file's hash) and `transcript_revision`. Episodes call it `session_id` (001:26,
  `pipeline.SEG_SQL`). _Not_ an episode.
- **Utterance.** One record in a conversation file (`importer.Utterance`). Its `utterance_id` is numbered per import,
  and re-transcription renumbers it (ROADMAP.md:55).
- **Segment.** Idea Machine's append-only copy of one version of an utterance, in `im.source_segments` (002:14-35).
  Its id is `uuid5(conversation, start, end, text)` (`importer.IDENTITY`; [M1 design]). Speaker fields, arousal and
  delivery are annotations, updated in place and outside its identity. _Not_ an episode. "Segment" is also the
  verb for cutting episodes (`segment.py`).
- **Turn.** A prose word for an utterance or segment (SPEC.md:22, 013:8, 019:1). It has no table.
- **Current.** A current segment is in `im.source_members` and not tombstoned (`im.current_segments`, 002:65-68).
  Everything downstream reads only current segments. A current episode is one not yet retired (`episodes.current`,
  001:40). A current capture is one on a current episode.
- **Supersession.** A link from an old segment to the new one that replaced it, in `im.source_supersessions`
  (002:47-54). Its `method` is `utterance_id` (same utterance, new content), `time_overlap` (re-transcription) or
  `rekey` (M1's new segment key; `importer.py`, [M1 design]). It's for traceability and for carrying labels and
  verdicts forward. It doesn't decide what's current.
- **Forgetting, tombstone.** An entry in `forgotten.json` deletes every segment whose time it overlaps, and
  everything derived from them (ROADMAP §3.4, `pipeline._purge_tombstoned`). `im.source_tombstones` keeps one
  textless row per forgotten segment (002:56-62).
- **Speaker kind.** Hearsay's `speaker_kind`: `owner`, `person`, `anonymous`, `stranger` or `unknown` (002:25,
  checked in `importer.py`). _Not_ a capture kind.
- **Speaker basis.** How Hearsay attributed the speaker (`speaker_basis`). Idea Machine reads `voice`, `named` and
  `channel` as solid, and anything else, `diarization` in practice, as not (030:23-24). The full list of values is
  Hearsay's to define: [UNGROUNDED] here, see "speaker basis" below.
- **Me, me by diarization, not me.** Who said a segment, as Idea Machine reads it (`im.item_speaker`, 030:17-30;
  SPEC §1 "Who said it"):
  - `me`: an owner turn by `voice`, `named` or `channel`;
  - `me_diar`: an owner turn on any other basis, which counts only alongside a `me` observation;
  - `not_me`: anything else, including `unknown` and a capture citing no segment.

  `cites_me` says a quote spanning speakers includes solid me speech. Hearsay names no one else (SPEC.md:21-22).
- **Speaker label.** The display name on a segment: `me`, a name, `anon A` or `unknown` (002:27). A capture's
  `said_by` is the current label of one cited segment (029).
- **Tap.** A press on the pendant, stored as a timestamp in `source_conversations.taps` (005:1-2). An episode's taps
  are those within 30 s of it (`triage.py`). A tap is "a weight, not a rule": it tells the model to look closely
  ([0004]:20-21, `extract.SYSTEM_V3`). _Not_ tapping a star in the sky ([M6 notes]:72).
- **Places.** The places I named for a conversation (013:3-5). `im show` prints them; nothing sends them.
- **Arousal (affect).** How animated I sounded, about 0 to 1, from the audio: tone of voice, not sentiment
  (013:7-9). It comes from the stream's `affect.arousal`. There's no `affect` column, and nothing reads it.
- **Valence.** Whether what I said was positive or negative. Not recorded: Hearsay's stream has no valence, so it
  would start as a change to Hearsay's contract. Open (2026-11-07): if both reach Idea Machine, an observation would
  carry them from its segments, as facts to show, filter or light a lens by, not a rating ([0007]).
- **Delivery.** Words per second, pauses and stutters, measured from word timings, not judged (019:1-6). Nothing
  reads it yet (ROADMAP.md:49).
- **Open.** Hearsay's index flag for a conversation that's still growing. SPEC.md:69-70 says V2 stores it; no column
  does yet.

## Episodes and what's sent

- **Episode.** A stretch of one conversation's current segments, cut by heuristics: a silence gap, and a rule that
  splits off my monologues (`segment.py`, `config.SegmentConfig`). Episodes are stored in `im.episodes` and
  `im.episode_segments` (001:20-49) and are content-addressed. A changed episode is retired, not deleted. It's the
  unit a model reads: one extraction per episode, prompt and policy. Its `kind` is `monologue`, `conversation` or
  `others_only` (001:30). _Not_ a conversation.
- **Triage, label, route, project.** V1's first pass over episodes:
  - **triage**: a local model's answers per episode (`is_self_thinking`, `kind`, `project`, `keep_score`), in
    `im.triage` (004);
  - **label**: my answers to the same questions, in `im.labels` (003);
  - **route**: `auto_file`, `review` or `escalate` (006:5), where "a route is a label" and nothing is dropped;
  - **project**: an entry in the hand-kept registry `im.projects` (003:4-10).

  Router v2 sends to review whatever has a tap, a "note to self" or a capture (`router.py`). The local triage
  models are off by default (ROADMAP.md:100).
- **Payload.** Exactly what was sent, system prompt excluded (`egress_log.payload`, 007:13; `egress.Payload`).
- **Transcript line.** One numbered line of a payload, `[n] S1: text`, where line n is the episode's nth segment
  (`egress.py`). Captures cite lines, and lines map back to segments ([0004]:18).
- **Egress log.** Every request that leaves the box: payload, policy, segments, model, provider (`served_by`) and
  cost (`im.egress_log`, 007:3-23; [0008]).
- **Privacy policy B.** Everyone's words go out verbatim. Speakers are pseudonymized per request, names said aren't
  scrubbed, and every request is logged ([0004]:24-33, `egress.POLICY_VERSION`).
- **Privacy policy C.** Policy B, plus every name said is pseudonymized ([0008]; `names.policy_c`). The names come
  from Hearsay's speakers, from config and from GLiNER. Policy C is for trials and audits until the checklist in
  0008 is done. There is no "policy A" in the docs or code: the rule B replaced was "other people's words never
  leave the box" ([0004]:24-25).
- **Pseudonym.** Either of two things:
  - a speaker's alias in a payload: `me`, `unknown`, `S1`, `S2`... (`egress.pseudonyms`);
  - under policy C, a name said, written as `P-` plus five characters of an HMAC under `IM_NAME_KEY` (`names.py`,
    [0008]:28-36).

  Both are mapped back locally, so stored text keeps real names.
- **Segment names.** The names GLiNER found in each segment, cached per finder (`im.segment_names`, 040).

## Capture

- **Extraction.** The stage that sends an episode to a model with a prompt and stores what comes back as captures.
  `im.extractions` records each read, including reads that found nothing (007:48). _Not_ matching.
- **Capture (item).** One thing a model captured from an episode, stored as a row of `im.items` (007:28). The docs
  say "capture" and the schema says "item"; they're the same thing. A re-read captures the same speech again as a
  new item in the same observation. _Not_ an observation, and _not_ an idea.
- **Prompt, prompt version.** The extraction prompts are `extract.PROMPTS` `v1` to `v4`. `prompt_version` is a hash of
  the system prompt, schema, model and effort (`extract.prompt_version`). `im.items` can hold `v1` to `v3`
  (`extract.LIVE`). `v4` is for trials only.
- **Quote.** The words a capture took from its cited lines. Under `v4` it's exactly as transcribed. Before `v4`, a
  quote could fix transcription errors (`extract.SYSTEM_V3`, `SYSTEM_V4`).
- **Reading.** `v4` only: the quote as the speaker meant it, with transcription fixed, fillers dropped and referents
  spelled out (`extract.SYSTEM_V4`). `im.items` doesn't store it yet.
- **Gist.** The thought stated plainly in one line, with the context it needs to stand alone (`im.items.gist`). Under
  `v4` it carries no label ("Joke:"), no note on transcription and no attribution. _Not_ a cluster's statement or
  title.
- **Said by.** Who said it here: the current speaker label of the capture's `said_by_segment_id`, read at query time
  through `im.item_said_by` (029; [M1 design]). The model answers with a pseudonym, which is mapped back.
- **Attributed to.** `v4` only: whose thought the speaker is passing on ("a nurse"), or empty. `said_by` stays
  whoever said it (`extract.SYSTEM_V4`). `im.items` doesn't store it yet.
- **Capture kind.** What sort of thought a capture is (`im.items.kind`, no CHECK, 007:31):
  - `extract.KINDS` (`v1`-`v3`): `idea`, `aphorism`, `joke`, `observation`, `project`, `task`, `decision`. `v3` files
    a question as `observation` unless it's an idea put as a question.
  - `extract.KINDS_V4`: `idea`, `aphorism`, `observation`, `question`, `technique`, `project`, `task`, `decision`.
    `joke` leaves, because a joke is a register, not a sort of thought.

  _Not_ a connection kind, a triage kind or a maturity.
- **Themes (on a capture).** Free-text strings the model returns with each capture (`extract.SCHEMA`), stored in
  `im.items.themes` and shown only in `pub.review_items` (010:19). No prompt says what they are: [UNGROUNDED].
  _Not_ `im.themes`.
- **Confidence (on a capture).** The model's number from 0 to 1. Under `v1` and `v2` it was the chance I'd keep the
  capture. Under `v3` and `v4` it's how sure the model is that the quote (or reading) and gist are what was said and
  meant (`extract.SYSTEM`, `SYSTEM_V2`, `SYSTEM_V3`, `SYSTEM_V4`). _Not_ a connection's confidence.
- **Quote in lines.** The share of a quote's words found, in order, in its cited lines. Under 0.6, the quote isn't
  from its lines (044:1-4, `quotes.FLAG`).
- **Quote score.** The share of the shorter quote's words found, in order, in the other (`feedback.quote_score`). At
  0.4 or more, a capture joins an observation or carries a verdict.
- **Register.** The tone something is said in, such as a joke or irony, as against what it says. A judging pass after
  extraction is meant to give it (`extract.SYSTEM_V4`'s comment; ROADMAP-v2 §5 q10-11). Not built.
- **Note to self.** When I say it, what follows is always captured (`extract.SYSTEM_V3`), and the router sends it to
  review (`router.NOTE_TO_SELF`).

## Observations and clusters

- **Observation.** One thing derived from one stretch of speech, which outlives re-extraction: when a re-read
  captures the same speech, the new capture joins the old observation ([M2 design] §Observations; 030:1-4). Stored in
  `im.observations`. A capture joins when its segments descend from the observation's and its `quote_score` is at
  least 0.4 against every capture already in it (034, B5). Its `source` is `speech`, `archive` or `typed` (036:9).
  An `archive` or `typed` observation belongs to its idea directly and holds its own quote, time, speaker and
  conversation (036:1-7). Its speaker is `me`, `me_diar` or `not_me` (`im.observation_speaker`). Observations are
  neutral and never judged (SPEC.md:27). _Not_ the maturity `observation`, and _not_ the capture kind `observation`.
- **Cluster.** The observations that make the same point. A cluster is an `im.ideas` row, with V1's ids kept; its
  members are the observations of its evidence (030:3-4; SPEC §3). The matcher decides what clusters together; no
  threshold does ([M2 design] §Weight). Every column naming a cluster is `idea_id`. _Not_ a theme, and _not_ a
  Hearsay voice cluster.
- **Member.** An observation in a cluster. Someone else making my point joins my cluster as a not-me member, which
  adds weight and never maturity ([M2 design]:88-89).
- **Matcher.** The stage that decides, for each new capture, against its nearest clusters: `new`, `same_as` one, or
  `evolves` one (`lattice.match_stage`; `item_matches.decision`, 008:30, which adds `skipped`). `judged_by` records
  the matcher's version (`match-1:…`), or `carried:observation` when a capture joins its observation's cluster
  without asking (`lattice.CARRIED`). Opus decides today; Jev is meant to at M4 (ROADMAP-v2 M4).
- **Evidence.** A capture's membership in a cluster: `im.idea_evidence(idea_id, item_id, relation, judged_by)`, with
  `relation` either `origin` (the capture the cluster was made from) or `same_as` (008:16-24). `pub.evidence` shows
  one row per observation (037:1). _Not_ an edge's citations, and _not_ recurrence ([M2 design]:83-84).
- **Recurrence.** An independent repeat: distinct conversations, or observations in one conversation at least
  `recurrence_minutes` (30) apart, never distinct items (SPEC §3; `weight.recurrences`).
- **Maturity.** How far a cluster has come (`im.idea_maturity`, 031:34-44): `idea` if I kept it, `candidate` if it
  has a me observation on a current capture, and `observation` otherwise. The system sets it up to candidate; only
  I make an idea ([M2 design] §Terms).
- **Candidate.** A cluster with a me observation that I haven't kept. Candidates differ only by weight
  ([M2 design]:39-40), and the system proposes them. Clusters with only not-me members never become candidates
  (SPEC.md:30-31).
- **Weight.** A number per cluster, computed each run by a versioned formula over stored signals. The signals are my
  recurrences, a capped count of other members, connections to canonical ideas, and whether the cluster is canonical
  itself (`im.weights`, 031:48-55; `weight.weigh`). It's shown as a fact and orders nothing ([0007]). Pins add
  nothing, a connection counts 1 and lineage counts nothing ([0010]). _Not_ `pub.connections.weight`.
- **Proposal.** A candidate offered to me to keep (canonical or pinned) or dismiss, in `im.proposals` (031:57-67).
  Its `outcome` is `canonical`, `pinned`, `dismissed`, or NULL while open. Proposals come most recently said first,
  up to `proposals_per_week` ([0007], `weight._eligible`). A dismissed candidate returns only once its weight has
  grown by `repropose_margin`. Syntheses and suggested connections are meant to arrive as proposals too
  ([0010]:75-84); neither is stored yet.

## Ideas and my decisions

- **Idea.** A cluster I kept: maturity `idea`, kept as canonical or pinned. Only I make one; the system proposes
  (SPEC.md:32-34, [0007]). _Not_ any `im.ideas` row, and _not_ the capture kind `idea`.
- **Kept as.** How I keep an idea (`im.idea_kept`, 031:17-30):
  - **canonical**: weighted, and it pulls the clusters connected to it;
  - **pinned**: kept and visible, with no weight.

  Pinning never changes weight, maturity or clustering. If I haven't kept or unkept a cluster, the archive's
  clusters and the ones I added start canonical, and starred ones start pinned.
- **Origin (of a cluster).** Where an `im.ideas` row came from: `archive`, `captured` (made by the matcher from a
  capture) or `mine` (I added it with `idea_add`) (008:10, 023:1-9).
- **Archive.** My first, ChatGPT "Idea Machine" conversation. It seeded 36 ideas (`archive_ref` `chatgpt#n`), their
  cross-references and five pinned themes (ROADMAP.md:142). Its messages are observations with source `archive`
  (036).
- **Title.** A cluster's short name, at most 8 words, written by the matcher when it makes the cluster
  (`lattice.SYSTEM`; `im.ideas.title`). My latest correction wins (`im.idea_text`, 011:15-27).
- **Statement.** A cluster's point in one line, keeping the capture's distinctive wording. The matcher writes it,
  falling back to the gist (`lattice.match_stage`; `im.ideas.statement`), and my latest correction wins. A synthesis
  may rewrite it ([M2 design]:270). _Not_ a gist, and _not_ a summary.
- **My note.** My latest `idea_note` on a cluster (`my_note`, 011:23-24). _Not_ `im.ideas.notes`, which holds what the
  archive developed around an idea (008:12). _Not_ a verdict's note, and _not_ a sheet's `note:`.
- **Correction.** An `idea_correct` event that replaces a cluster's title or statement. The originals stay as
  `claude_title` and `claude_statement` (011).
- **Feedback event.** Any input I give, as a row of `pub.feedback_events` (008:88-98). The `kind` must be in the CHECK
  at 031:7-11, and `source` says where it came from (`lattice`, `cli`, `mcp`, `emanation`). Where events disagree,
  my latest is the one with the highest `event_id`; `at` bounds windows and orders nothing (SPEC.md:84-86, 043). The
  kinds:
  - on clusters: `idea_keep` (payload `as`: `canonical` or `pinned`), `idea_unkeep`, `idea_dismiss`, `idea_add`,
    `star`, `unstar`, `link`, `unlink`, `idea_note`, `idea_correct`, `idea_sensitive`;
  - on themes: `pin_theme`, `unpin_theme`, `rename_theme`, `reject_theme`, `merge_theme`, `split_theme`,
    `file_idea`, `unfile_idea`, `theme_sensitive`;
  - on the vocabulary: `vocab_add`, `vocab_remove`;
  - legacy, kept in the ledger and read by nothing that orders or gates ([0007]): `keep` and `discard` (V1, written
    by nothing now), `item_keep`, `item_discard`, `item_star` (verdicts), `idea_compare` (comparisons), and
    `digest_star`, `digest_dismiss`.
- **Keep, unkeep, dismiss.** `idea_keep` makes a cluster an idea or switches how it's kept, and `idea_unkeep` undoes
  it. `idea_dismiss` turns a proposal down; the cluster stays a candidate (031:4-5).
- **Star.** A `star` event on a cluster. A star is a pin ([M2 design]:277, 031:17-18). _Not_ a star on the sky,
  _not_ a capture verdict `star`, and _not_ `digest_star`.
- **Link, unlink.** My act of connecting two clusters, or taking the connection back. Per pair, my latest decides. A
  link makes the pair a connection, shown as `mine`. An unlink hides every stored connection on the pair whose
  method had written before it ([connections storage] §My links).
- **Verdict (legacy).** My `keep`, `discard` or `star` on a capture, in `im.item_verdicts` (007:69, 010:3-5), with
  `how`: `one`, `audit` or `bulk` (020). Verdicts carry to re-captures (014, `feedback.carry_verdicts`). Nothing
  orders or gates by them ([0007]). `pub.ideas` still hides a cluster whose every capture I discarded (043:27-28).
  _Not_ `idea_keep`.
- **Comparison, rubric, score (legacy).** V1's ranking: `idea_compare` events (018), and a rubric of generality,
  novelty, compression and outside value combined into `pub.idea_scores.score` (017, 025). They're thrown out as
  labels ([0007]).
- **Merge, reject (of an idea).** SPEC.md:98 and the [M2 design]:138 say they "stay". No state, column or event
  exists for them on a cluster, and ROADMAP-v2.md:18 says ideas can't be merged yet: [UNGROUNDED]. Themes can be
  merged and rejected (024).

## Graph

- **Edge.** Any stored relation between two clusters: a connection or a lineage link (`checks.UNCITED`,
  `citations.py`).
- **Connection.** Two distinct points belong in the same tight neighbourhood, beyond sharing a subject. A connection
  is one per pair and symmetric ([0010] §Decision). It's a row of `im.connections`, stored with `a < b`, `method`,
  `confidence`, `cited_observation_ids` and `decided_at` (045:13-22). Until a model decides the pair (Terra, M4),
  stand-ins hold the place: embedding neighbours (method `embedding:…;k=3;min=0.7`, `lattice.relate`) and the
  archive's cross-references (`chatgpt-archive`). `im.shown_connections` adds my links and removes what I unlinked
  (045:94-105). In lenses and weight, a connection counts 1. _Not_ lineage, _not_ a shared subject (a lens gathers
  those), and _not_ an analogy.
- **Related.** A connection with no kind: "one with no kind is simply related" ([0010]:42-43). `pub.connections` and
  `pub.card_links` label every stored connection `related` (045:101).
- **Mine (on a connection).** A connection I made with a `link` event. It shows as `kind` `mine`, method `feedback`
  (045:97).
- **Connection kind.** An optional note on a connection saying how the two relate. A connection may carry several
  or none, each with its own `method`. Kinds are stored in `im.connection_kinds` (045:25-35). They're suggestions:
  they never decide whether a connection exists, and they aren't graded ([0009]). Each adds `lens.KIND_WEIGHT`,
  which is 0 for now. The table stays empty until a kind pass writes it. The kinds:
  - `supports`: one is a reason to believe the other (`edge_audit.HEADER`);
  - `elaborates`: one fills in the other, as a case, a description, an application, a generalization, or something
    built on it (`edge_audit.HEADER`);
  - `tension`: they pull against each other, as a contradiction, a change of mind, a trade-off or competing pulls
    (`edge_audit.HEADER`);
  - `sympathy`: the two resonate, or pull the same way ([0010]:39);
  - later, `antipathy` and `disproves` ([0010]:39). No doc defines either beyond its name: [UNGROUNDED].

  `supports` and `elaborates` carry a `direction`: `a>b`, `b>a` or `both`.
- **Lineage.** Directed descent between clusters, kept apart from connections: `im.lineage(from_id, to_id, kind)`
  (045:37-46). Lenses follow it both ways; weight doesn't count it. Its kinds:
  - `evolves`: from a new cluster to the one it develops into a distinct idea of its own (the matcher, `lattice.SYSTEM`);
  - `synthesizes`: from a synthesis to each point it was made from ([0010]:75-79). Nothing writes it yet.
- **Method.** What wrote an edge or a kind: a model and question version, embedding parameters, `chatgpt-archive`
  or `feedback` (SPEC §3; 045). `im.edge_methods` records when each method first wrote, so an unlink hides only what
  it had seen (045:49-53). _Not_ a supersession's method, and _not_ `judged_by`.
- **Citation.** The observations an edge rests on: `cited_observation_ids`, the origin observations of its two ends
  (`citations.py`; [M2 design] §Edges). `im check` fails on an edge that cites nothing where it could.
- **Neighbours.** The nearest clusters by embedding cosine (`pub.idea_neighbours`, 026; `pub.card_neighbours`, 033).
  Neighbours nominate pairs and seed a lens's search. They never decide a connection ([0010]:96).
- **Neighbourhood.** A set of clusters held together by connections. A neighbourhood of connected observations is
  what triggers a synthesis ([0010]:33-36). _Not_ a set of embedding neighbours.
- **Synthesis.** A stage, not built yet, triggered by a neighbourhood of connections. It writes either a new
  statement for one cluster (consolidation) or one idea for several connected clusters, citing observations and
  linked to them by `synthesizes`. It arrives as a proposal (ROADMAP-v2 Synthesis row; [0010]:75-80;
  [M2 design]:268-275). _Not_ the weekly digest.
- **Bridge.** An idea that touches two or more lenses (in Lattice v1, themes), by belonging to both or by connections
  across ([0010]:82-84). _Not_ a proposed connection, and _not_ the M2 design's "bridging" synthesis.
- **Proposed connection.** A connection the system suggests and I haven't made. It's a proposal, drawn as a faint
  dashed link, and becomes a connection only if I keep it ([0010]:83-84, [M6 notes]:22-24). Not built.
- **Analogy.** The same structure in different subjects. A kept analogy is a pinned lens seeded with its named
  structure ("easy against perfect"), whose results are its other instances. It isn't a connection and writes none
  ([0010] §Decision, amended 2026-11-07). The analogy pass asks Jev's `different_subjects` and `same_structure`
  questions, proposes a pair, and names what the pair shares. Until lenses are built, B4 marks an analogous pair
  `none` with a note starting `analogy:`.
- **Tension pass.** A pass of its own that asks only whether a pair is in tension. Jev decides it by the mean of the
  `pulls4` wordings, yes at 0.45 or more ([0009] amendment; `jev.QUESTION_SETS`, `jev.CUT`). It proposes pairs for
  me to keep (ROADMAP-v2 §5 q9).

## Views and lenses

- **Neutral view, card.** `im web`: every cluster that passed the gates, with what's known about it and no score
  ([0007]; 033:1-3; `web.py`). A card is one cluster as the neutral view shows it: a row of `pub.cards` (033:32),
  with title, statement, maturity, `kept_as`, my note, observations by speaker, conversations, first and last said,
  `links` (its count of connections) and `proposed`.
- **Emanation.** The fluid family of views inside Idea Machine, for association and promotion. It need not stay put
  between runs ([0010]:66-69, ROADMAP-v2 §4). The sky is the first.
- **Sky.** `/sky`. Each star is a cluster at its pipeline position ([M6 notes] §First sky, `web.sky`):
  - brightness: recurrence (distinct conversations);
  - sharpness: how recently it was said;
  - size: maturity and how it's kept (observation, candidate, pinned, canonical);
  - colour: its direction from the map's centre.

  The home sky is the lens with no seed ([0005] amendment).
- **Position.** A cluster's 2D map coordinates, projected from embeddings and kept stable from run to run (015, 038,
  `lattice.place`).
- **Lens.** The top-level way into the graph. A lens has a seed and a definition, returns how relevant every cluster
  is, and writes no connections ([0010]:45-49). Today, `lens.relevance` runs personalized PageRank from a seed
  cluster over connections (each 1 plus `KIND_WEIGHT` per kind) and lineage (both ways), and adds the seed's 20
  nearest clusters by meaning (`lens.py`). Only a cluster can be a seed today. Seeds that are a subject, a sentence,
  a question, a correspondence or a structure are decided but not built, and so is lens storage (SPEC §3 Lenses
  row).
- **Seed.** What a lens starts from. _Not_ the archive seed (`im seed`), and _not_ a random seed.
- **Pinned lens, archived lens.** A lens I keep at hand, or one I've left. An archived lens is kept with its
  definition, its cost and the graph version it read, never thrown away ([0010]:47-49). Not built.
- **Subject lens.** A lens seeded with a subject. Themes migrate into subject lenses ([0010]:47, SPEC.md:36). Not
  built.
- **Subject.** What something is about. A shared subject is what a lens gathers; it isn't a connection ([0010]
  §Context; `edge_audit.HEADER` `none`).
- **Theme.** A Lattice v1 category of clusters: `im.themes(name, description, origin, pinned)`, with membership in
  `im.idea_themes` (008:65-80). `origin` is `archive`, `clustered` (proposed by Claude) or `mine`. Theme events edit
  themes (024). Themes are to become subject lenses. _Not_ a capture's `themes` field.
- **Lattice (v1).** The separate app and repo that reads `pub`: the map, review and the first MCP server
  ([0005]). It's feature-frozen at `v1` and archived at M6.
- **Lattice v2.** The stable family of views: pinned lenses laid out by hand. The first are a grid of lenses and the
  Tree of Life (ROADMAP-v2 §4, [0010]:70-73). Not built. _Not_ the Lattice repo.
- **Tree of Life.** The Qabalistic correspondence table, condensed into definitions. Each correspondence is a lens
  (1/Kether), placed on the sephirot and the paths between them (ROADMAP-v2.md:57-58, [0010]:70-73).
- **Digest.** A weekly write-up of the week's ideas, by Claude: a summary and 3 to 7 points, each citing ideas
  (`im.digests`, `im.digest_points`, 022; `digest.py`). A point's kind is `recurring`, `convergence`, `sleeper`,
  `project` or `outside_value`. _Not_ a synthesis.
- **MCP connector.** The 19 tools Lattice serves, a contract kept byte-compatible. `open_lens` is added at M5
  (SPEC §2, [0005]).
- **Demo mode, sensitivity.** Claude tags ideas and themes by category: `sex`, `gender_politics`,
  `political_extremism`, `violence`, `racism`, `profanity`. My marks win (027; `sensitivity.CATEGORIES`). Removing
  sensitive elements per view builds on it (ROADMAP-v2 §4).
- **Vocabulary (for Hearsay).** Rare words Hearsay's transcription should know. They're collected from my
  corrections, the archive and misheard quotes, approved by me (`vocab_add`, `vocab_remove`) and written to
  `vocab.json` (028; ROADMAP.md:165; deploy.md:130). _Not_ this document.

## Models and measurement

- **Opus.** `claude-opus-5-5`, effort `medium`: the model for extraction and matching ([0004]; `config.ExtractConfig`).
  A refusal falls back to `claude-opus-4-8`.
- **Sonnet, Haiku.** `claude-sonnet-5-5` and `claude-haiku-5-5`: Anthropic models compared in B3 (`llm.PRICES`).
- **Terra.** `openai/gpt-6.1-terra` at low effort, through OpenRouter. It decides connections, having passed B4
  (SPEC §5).
- **Sol, GLM.** `openai/gpt-6.1-sol`, `z-ai/glm-5.3` and its flash version: other providers' models in B3 ([0008]).
- **Jev.** TypeSafe's decision model (`jev.MODEL`, `jev-1.14.0`). It answers typed questions about structured state
  with probabilities; it isn't a chat model (`jev.py`). Its terms:
  - **Noul**: one atomic yes/no question, answered with the probability of yes; Nouls are combined in code.
  - **choice**, **score**: question types not used yet (ROADMAP-v2 §5 q11).
  - **question set**: the wordings that decide (`jev.QUESTION_SETS`: `pulls4` for tension, `tight1` for an edge).
  - **probe**: a question asked only to measure (`jev.PROBE_SET`).
  - **cut**: the probability at or above which a ranking reads as yes (`jev.CUT`).
- **GLiNER.** A local entity model that finds names said, for policy C ([0008]).
- **Embedding model.** `BAAI/bge-small-en-v1.5`, local (`config.py`). It nominates pairs, places clusters and finds
  neighbours.
- **Laya, logreg, local LLM.** V1's triage backends (`backends.py`, [0001], [0002]). They're off by default.
- **Corral.** The job queue that runs the pipeline on the NAS and GPU jobs on the dev box (deploy.md:4-6, 109-120).
- **Trial.** `im trial`: several models read a fixed sample of episodes under policy C. A trial reports each model's
  recall of a reference's captures (Opus's), recall by kind, extra captures and cost ([0008]:38-43). What a trial
  captures stays out of the live tables (`im.prompt_trials`, 016, 039).
- **Benchmarks** (ROADMAP-v2 §2):
  - **B1**, matching: `new` against `same_as`/`evolves`, scored against Opus's logged decisions;
  - **B2**, keep/discard prior: retired;
  - **B3**, extraction model swap: recall of Opus's captures;
  - **B4**, edges: 200 pairs I marked blind; graded on whether there's a connection; Terra passed;
  - **B5**, dedupe: false merges of observations; passed;
  - **B6**, local titles: blind pairs against Claude's.
- **Sheet, mark.** A blind Markdown file of pairs or captures, which I mark by hand. My marks are what models are
  scored against (`edge_audit`, `dedupe_audit`, `probe_audit`, `summary_audit`).
- **Labels (B4).** The answers on the edge sheet. Labels 3 are `supports`, `elaborates`, `tension`, `related`,
  `none`, `same` (the same idea twice, a merge the matcher missed) and `unsure` (`edge_audit.LABELS`, `HEADER`).
- **Novelty marks.** Discovery sheets (analogy, tension) mark a pair `new` (it holds, and I hadn't connected it),
  `known` (it holds, and I had it) or `no` ([0009] amendment, `probe_audit.MARKS`).
- **Facets, structure lines.** A model's lines describing an idea's structure with its subject matter removed: four
  per idea in `probe_audit`, one in `analogy_spike`. They're used to propose analogies.
- **`im tune`.** Previews a change to the weight knobs, at no cost ([M2 design] §Weight).

## Overloaded or drifting terms

Each entry lists the meanings found, with evidence, then proposes one meaning. The proposals are proposals: nothing
here renames code.

### kind

- A capture's sort of thought: `im.items.kind` (007:31), `extract.KINDS`, `extract.KINDS_V4`.
- An episode's triage answer: `idea`, `task`, `decision`, `chatter` or `noise` (`triage.KINDS`, `label.KINDS`).
- An episode's shape: `monologue`, `conversation` or `others_only` (001:30).
- A speaker's: `speaker_kind` (002:25). A stutter's: `repeat`, `restart` or `filler` (019:2).
- A feedback event's type (008:91, 031:7-11).
- A connection kind (045:28) and a lineage kind (045:40). The dropped `im.idea_links.kind` mixed `evolves`, `related`
  and `archive` (008:42).
- The `kind` column of `pub.connections` and `pub.card_links`, which carries `related`, `mine`, `evolves` and
  `synthesizes` (045:97-116). None of those is a connection kind: `mine` is provenance, and the last two are lineage.
  `checks.UNCITED` adds a pseudo-kind, `connection`.
- A digest point's type (022:21), and `pub.sensitivity.kind`, which is `idea` or `theme` (027).

**Proposed:** bare "kind" means a connection kind, 0010's growing vocabulary. Every other use is qualified: capture
kind, lineage kind, event kind, and so on. Read the `kind` column of `pub.connections` and `pub.card_links` as "shown
as".

### observation

- A row of `im.observations` (030:1-4).
- The maturity `observation`: a cluster with no me observation (030:41-50).
- The capture kind `observation` (`extract.KINDS`). `edge_audit.CONCRETE` reads it as "about a case, not a claim",
  and `v3` files questions under it.
- Any capture: the `v3` and `v4` prompts say "You extract observations" (`extract.SYSTEM_V3`).
- "A stretch of speech", in [0006]:33.

**Proposed:** an observation is an `im.observations` row. Call the maturity "a cluster with no me observation" in
prose. If capture kinds come to matter downstream (ROADMAP-v2 §5 q11), rename the capture kind.

### idea, cluster, candidate

- Every cluster is an `im.ideas` row, and every cluster column is `idea_id` (031:49, 031:60). `pub.ideas` lists
  clusters of any maturity (043:27-28). `lens.py` and the B4 sheet say "cluster" for the same rows (`lens.relevance`,
  `edge_audit` docstring).
- An idea is also the maturity `idea` (031:36), the capture kind `idea` (`extract.KINDS`) and the triage kind `idea`.
  The `v1` prompt says "You extract ideas" (`extract.SYSTEM`).
- "Candidate" is the maturity (030:49). It's also the nearest clusters the matcher is shown ("Candidates:",
  `lattice.SYSTEM`; ROADMAP-v2.md:118-119), a model under trial ("a candidate's recall", [0008]:40) and
  `im.vocab_candidates` (028).
- "Cluster" is also the archive's five clusters, which became themes (ROADMAP.md:143), a theme's origin `clustered`
  (008:69), and Hearsay's voice clusters (`speaker_basis` `cluster`, 002:28; "a cluster tagged `_me`",
  [M1 design]:41).

**Proposed:** a cluster is any `im.ideas` row, and `idea_id` reads as a cluster id. An idea is the maturity `idea`
only. A candidate is the maturity only: the matcher's are "nearest clusters", and a trial's is "the model under
trial".

### summary, statement, gist, title

- No capture or cluster has a `summary` column. The only one is the digest's summary of the week (022:11).
- On the B4 and probe sheets, "Claude's summary" is a cluster's `title. statement` (`edge_audit.side_data`,
  `probe_audit`). SPEC.md:115 says the pair "as each side was said and summarized".
- In `summary_audit` (its docstring and `HEADER`), `match_bench` and `miss_audit`, "summary" is a capture's gist.
  Jev's `summary` questions compare it with the cited lines (`jev.QUESTIONS`).
- ROADMAP-v2 §5 q9 uses "summaries" for both. Its evidence comes from a tension sheet (title and statement), and its
  plan is per capture (the gist).
- `archive_observations.py` calls the archive's text "ChatGPT's summary".
- The [M2 design]:95-96 says a candidate's statement is the gist of its first observation. In fact the matcher writes
  the statement and uses the gist only as a fallback (`lattice.match_stage`).

**Proposed:** a capture has a gist, a cluster has a title and a statement, and "summary" means the digest's alone.
Sheets would say "Claude's title and statement".

### link, connection, edge

- A connection is an edge "pure and simple" ([0010]:28). Edges are connections plus lineage (`checks.UNCITED`,
  `citations.py`).
- `pub.cards.links` counts connections only (033:33-35). The neutral view prints that count as "N edges"
  (`web._facts`) under a "Connections" heading (`web.card`). The sky's `links` include lineage (`web.sky`).
- "Link" is my act (`link`, `unlink`, 008:91), shown as kind `mine`. It's also an archive cross-reference
  (`lattice.py`), a "lineage link" (`lens.py` docstring), weight's "links to canonical ideas" (SPEC.md:107), and V1's
  "Connections link ideas: same as, evolves, related, and links I make" (ROADMAP.md:138).
- [0006]:49-56 still describes edges as rows of `im.idea_links` with `evidence_item_ids`, a column that never existed;
  0010 amends it.

**Proposed:** a connection is an `im.connections` row or a link of mine, and lineage is an `im.lineage` row. An edge
is either, and only when both are meant. A link is only my `link` act.

### related

- An embedding neighbour (top 3, cosine ≥ 0.70): the old `idea_links` kind (008:37-42), `lattice.RELATED_K`, and the
  "related edges" of 026:3.
- In `pub.connections` and `pub.card_links`, the `kind` of every stored connection, including archive ones and ones
  that carry kinds (045:101, 045:120-124).
- On B4, labels 1-2: "the same subject, but none of the above" (`edge_audit` docstring).
- On B4, labels 3: connected, with no kind to say how (`edge_audit.HEADER`).
- "A connection with no kind is simply related" ([0010]:42-43).

**Proposed:** related means a connection with no kind. Embedding neighbours are "neighbours" (their method says
`embedding:`), and the views' `related` reads as "a stored connection".

### theme, topic, subject

- A theme is an `im.themes` row (008:65-73). A capture's `themes` field is free tags that no prompt defines (007:35,
  `extract.SCHEMA`).
- "Subject" is what a lens gathers ([0010] §Context), the seed of a subject lens ([0010]:47), and Jev's
  `different_subjects` (`jev.QUESTIONS`).
- "Topic" has no definition. It means a subject measured by embedding similarity ("by topic", `analogy_spike.py`,
  `probe_audit.py`), and a prefix a gist shouldn't open with (`extract.SYSTEM_V4`): [UNGROUNDED] as a term.

**Proposed:** a subject is what something is about, gathered by a lens. A theme is the legacy table until it
migrates into subject lenses. "Topic" becomes "subject". A capture's `themes` are "tags".

### evidence

- A capture's membership in a cluster: `im.idea_evidence` (008:16), `pub.evidence`, one row per observation since 037
  (037:1), and `n_evidence`.
- The ledger as a whole: the "evidence ledger" ([0006]:17).
- What an edge cites: "each cites its evidence" (SPEC.md:13), "evidentiary edges" ([0005]:21), "Evidence-backed"
  ([M6 notes]:38). Also "shared evidence" between nominated pairs (SPEC.md:119).
- What recurrence isn't: "that's recurrence, not evidence" ([M2 design]:83-84).
- In the B4 definition of `supports`: "evidence or an argument for it" (`edge_audit.HEADER`).

**Proposed:** evidence is a cluster's member captures and observations (`im.idea_evidence`). What an edge rests on is
its citations.

### weight

- A cluster's weight (031:48-55), and the snapshot of it on a proposal (031:61).
- `pub.connections.weight`, which is a connection's confidence or 1 (045:110). The dropped `idea_links.weight`
  (008:43).
- "A connection weighs 1", plus each kind's fraction ([0010]:60-62, `lens.KIND_WEIGHT`).
- A tap: "a weight, not a rule" ([0004]:20).

**Proposed:** weight is a cluster's alone. A connection has a confidence, and counts 1.

### confidence

- A capture's, whose meaning changed in one column. Under `v1` and `v2` it was the chance I'd keep the capture; under
  `v3` and `v4` it's how sure the model is of what was said and meant (`extract.SYSTEM`, `SYSTEM_V3`).
- A connection's, which is the cosine for embedding stand-ins ([M2 design]:159).
- A triage answer's (004:12), `speaker_conf`, and `asr_confidence` (002:30-31).

**Proposed:** always say whose. A capture's confidence compares only within one `prompt_version`.

### proposal

- A candidate offered to become an idea (`im.proposals`, 031:57).
- A theme Claude proposed (`origin` `clustered`; "only unpinned proposals" can be rejected, 024:107).
- A suggested connection ([0010]:83, [M6 notes]:22), a synthesis ([0010]:75), and a discovery ([0009]:60-61).
- In V1's Phase 5, agent work for me to approve (ROADMAP.md:226).
- Order: "highest weight first" in the [M2 design]:144 and 031:69, but most recently said first since [0007].

**Proposed:** a proposal is anything offered for me to keep or dismiss, and only cluster proposals are stored. They
come newest first.

### keep, pin, star, dismiss

- **Keep:** `idea_keep`, which makes an idea (031:4). Also a capture verdict `keep` (007:69), the legacy events
  `keep` and `item_keep`, `pub.ideas.kept` (a count of verdicts), and triage's `keep_score`.
- **Pin:** an idea kept as pinned (031). Also `themes.pinned` and `pin_theme` (008:71), and a pinned lens
  ([0010]:47).
- **Star:** a `star` event, which is a pin (031:17-18). Also the capture verdict `star` (010:3-5), `digest_star`, and a
  cluster drawn on the sky ([M6 notes] §First sky).
- **Dismiss:** `idea_dismiss`, a proposal turned down. Also `digest_dismiss` and the outcome `dismissed`.

**Proposed:** keep means `idea_keep`, and V1's keep/discard are "verdicts". Pinned is always qualified (a pinned idea,
theme or lens). On the sky, a star is a drawing.

### merge, reject, same

- SPEC.md:83 lists merge and reject among V2's events, and SPEC.md:98 and the [M2 design]:138 say `merged` and
  `rejected` "stay". No event, column or state exists for either on a cluster (031:7-11). ROADMAP-v2.md:18 says
  "Ideas can't be merged yet", and the matcher "never merges destructively" (`lattice.py` docstring).
- `merge_theme` and `reject_theme` exist (024). B5 counts "merged" observations (`dedupe_audit`). B4's `same` is "a
  merge the matcher missed". The matcher's `same_as` joins a capture to a cluster (008:30). B5 marks pairs `same`
  or `different`.

**Proposed:** merging and rejecting apply to themes, and to observations in B5, until merging clusters is built. The
SPEC and M2 lines are [UNGROUNDED] until then.

### me, mine

- `me`: a segment said by me (030:23).
- "A cluster of mine": one with a me observation (SPEC.md:11).
- `origin` `mine`: a cluster I typed in (023:1).
- A connection of kind `mine`: one I linked (045:97).
- `themes.origin` `mine`: a theme I split off (024:127).
- `speaker_label` `me`, and the speaker pseudonym `me` (`egress.pseudonyms`).

**Proposed:** "me" means said by me. "Mine" is always qualified: typed by me, linked by me.

### archive

- My ChatGPT archive: `origin` `archive`, `observations.source` `archive`, method `chatgpt-archive`, the old link
  kind `archive`, and the vocabulary source `archive` (028:13).
- A lens I've left ([0010]:48), and the Lattice repo at M6.
- My whole collection: "my idea archive" in the `v1` and `v2` prompts (`extract.SYSTEM`).

**Proposed:** the archive is the ChatGPT one. "Archived" is said of lenses and of the Lattice repo only.

### origin

- `im.ideas.origin` (008:10).
- `idea_evidence.relation` `origin`: the capture a cluster was made from (008:16-20). Edge citations cite the
  "origin observations" (`citations.py`).
- `themes.origin`, and `idea_themes.origin` (`archive`, `assigned`, `proposed`, `mine`; no CHECK, 008:78).

**Proposed:** origin is `im.ideas.origin`. The other is "the first capture".

### synthesis, bridge

- Synthesis is the V2 stage ([0010]). In V1, "periodic synthesis" was the weekly digest (ROADMAP.md:164), and the
  "Synthesis (Claude)" phase was extraction (ROADMAP.md:188, 248).
- A bridge is an idea touching two lenses ([0010]:82). The [M2 design]:271 calls a synthesis over several clusters
  "bridging". Emanation's notes once mixed bridges with proposed connections ([0010]:22-24).

**Proposed:** synthesis is the V2 stage only, and a bridge is an idea only. M2's "bridging" is "a synthesis of
several clusters".

### neighbourhood, neighbours

- A tight neighbourhood: what a connection asserts, and a set of connected clusters that triggers a synthesis
  ([0010]).
- Embedding neighbours: `pub.idea_neighbours`, `pub.card_neighbours`, `lattice.RELATED_K`. B4 "drew its pairs from
  neighbourhoods" in this sense (`probe_audit.py` docstring, ROADMAP-v2.md:87).
- The "semantic neighbourhood" that places and colours a star ([M6 notes]:15, 37).

**Proposed:** neighbours are by embedding, and a neighbourhood is by connections.

### analogy

- SPEC.md:124-125 and [0009]:28 make analogy a pass over pairs, standing beside tension. The 0010 amendment
  (2026-11-07) makes a kept analogy a lens, which writes no connection.
- `lens` is also the name of one analogy wording on the second analogy sheet (ROADMAP-v2.md:96).

**Proposed:** an analogy is a named shared structure, kept as a pinned lens, and the pass only proposes pairs. SPEC
§5's line is stale.

### lattice

- The graph of ideas: "the idea lattice" (008:1, ROADMAP Phase 2), and the "relationships" of the lattice
  ([0008]:17).
- The Lattice app and repo ([0005]), and Lattice v2, pinned lenses laid out by hand ([0010]:70).
- `src/im/lattice.py`, which holds the matcher, the embedding neighbours and the positions.
- "A conceptual or thematic lattice (a grid) of lenses" (ROADMAP-v2.md:56).

**Proposed:** Lattice is the V1 app, and Lattice v2 is the pinned-lens view. The data is "the graph".

### evolves, elaborates, develops, extends

- `evolves` is lineage: a new cluster that develops, refines, extends or applies another (`lattice.SYSTEM`, 045:40).
- B4's pilot folded `evolves` into `extends`. Labels 2 renamed `extends` to `elaborates`, saying "a later version
  stays `evolves`" (`edge_audit` docstring). But the B4 sheet tells me a later version is `elaborates` if it refines
  it (`edge_audit.HEADER`).
- Jev's match questions say `develops` for `evolves` (`jev.QUESTIONS`, `match_bench`).

**Proposed:** `evolves` is lineage (a later, distinct version), `elaborates` is a connection kind, and `develops` is
Jev's wording for `evolves`. The sheet's later-version rule conflicts with the matcher's `evolves`.

### recurrence

- `weight.recurrences`: distinct conversations, or 30 minutes apart (SPEC §3).
- `pub.idea_scores.recurrence`: V1's scaled count of captures (017:18-26), which is what SPEC §3 rules out.
- The sky's brightness counts distinct conversations only ([M6 notes]:67).

**Proposed:** recurrence is `weight.recurrences`.

### note, label, point

- **Note:** my note (`idea_note`), `im.ideas.notes` (the archive's), a verdict's note, a sheet's `note:` and
  `analogy:` notes, and "note to self".
- **Label:** `speaker_label`, triage labels (`im.labels`), B4's labels, the prefixes gists open with ("Joke:",
  ROADMAP-v2 §5 q10), and "No value label exists" ([M2 design]:39).
- **Point:** what an observation or cluster makes ("the same point", [M2 design]:83), "two distinct points" in a
  connection ([0010]:28), a cluster ("the neighbourhood's best existing point", [0010]:80), and a digest's points
  (022).

**Proposed:** "my note" is `idea_note` alone. Labels are always qualified. A point is what an observation says, and a
digest's are "digest points".

### method, judged_by

- An edge's or kind's `method` (045). A supersession's `method` (002:51, which omits `rekey`).
  `feedback.CARRY_METHOD` and `lattice.POSITIONS_METHOD`.
- `judged_by` holds the matcher's version (008:21), and the same `match-1:…` string is the `method` of the `evolves`
  rows it writes (`lattice.match_stage`).

**Proposed:** a method is what wrote a stored row, qualified by table. `judged_by` is the matcher's method.

### conversation, session, utterance, segment, turn, line

- `session_id` is `conversation_id` (`pipeline.SEG_SQL`, ROADMAP.md:48). A non-speech observation's `conversation` is
  a ChatGPT conversation or an `idea_add` event (036:7).
- Utterance, segment and turn name one unit at different stages (`importer.Utterance`; 002:14; 013:8).
- A line is a payload's `[n] S1: text` (`egress.py`). In `triage.render` a line is `label: text`, with no number.

**Proposed:** a conversation is Hearsay's, an utterance is the stream's record, and a segment is Idea Machine's
stored version. "Turn" goes. A line is a payload line.

### speaker basis

002:28 lists `voice | diarization | named | cluster | none`, while the code reads `channel` as solid me (030:23) and
SPEC.md:25 names `voice`, `named` and `channel`. Hearsay's stream.md is the authority, and neither list cites it:
[UNGROUNDED].

**Proposed:** take the values from Hearsay's contract and fix the 002 comment to match.

### Stale definitions

- `lens.py`'s docstring says positions are "one map for every view", and `sky.html` says positions "never move".
  Since [0010], Emanation need not stay put.
- [0006]:31 and 49-50 describe `im.ideas` with a `status`, "promotions" and provisional ideas. Maturity is a view, and
  there's no status column.
- [0010]:94-95 says lens storage replaces `im.lens_cache` "in SPEC §3", and SPEC §3 no longer names it.
- ROADMAP.md:47 says a segment's identity includes its speaker. It's frozen at V1; [M1 design] changed it.
- 001:20 gives `episode_id` as `uuid5(stage_version, input_hash)`. The code adds the speakers' pseudonyms
  (`segment.py`, [M1 design]:77).

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
