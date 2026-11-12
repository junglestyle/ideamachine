# Capture eligibility: measuring it before changing anything

_Plan, 2026-11-12. The measurement behind ROADMAP-v2's Triage row: how many of Opus's live captures shouldn't be
cards. It measures only: routing, prompts, extraction (v5.3 stays as it is) and any quarantine are unchanged.
`im audit-eligibility` (`src/im/eligibility_audit.py`)._

## What's measured

A capture is **eligible** if it deserves to be a card: one of my thoughts worth keeping. It's **ineligible** if it
shouldn't be one: logistics, small talk, narration without a point, transcription noise, someone else's point I only
go along with, a reaction with no content. It's **uncertain** when the capture and the lines shown can't settle it.
The rubric below decides each, and is frozen.

The number wanted is the ineligible rate among the live speech captures, with a confidence interval and N, and
beside it how far two cheaper models' labels can stand in for mine.

## Protocol

1. **Frozen rubric.** Written once (below), stored as `eligibility_audit.RUBRIC` and here, word for word. Its
   version and SHA-256 are recorded in each sheet's `key.json`, each draft and the review's key, and printed by
   every step. Changing it is a new `RUBRIC_VERSION`; a folder made under one rubric refuses to be drafted, reviewed
   or scored under another. It was drafted from [vocabulary.md](../vocabulary.md)'s thought, [decision
   0011](../decisions/0011-observations-are-mine.md), extraction v5.3's capture rules
   (`src/im/prompts/extract_v5_3.txt`) and #161's criteria for the gate's `not_mine` and `mocked` (the stand-behind
   test for deadpan). It doesn't contradict v5.3: everything v5.3 tells the extractor to capture is eligible here,
   and everything it leaves out as not a thought of mine is ineligible. (Its rule against capturing a thought twice
   in one transcript is about duplicates, not eligibility: tie-break 6.)
2. **Representative sample.** `sheet` draws 100 captures uniformly at random (seed 10) from the live speech
   captures: those on current episodes, from the live read (`extract.prompt`, model and effort) under the policy
   requests go out under now. Never stratified by any model's score. The ChatGPT archive's captures (49) have no
   lines to show, so they're left out and counted (`archive_skipped`). A capture whose episode's transcript changed
   since it was read is passed over and counted. The key gives the spread of the sample and of all live captures by
   episode kind (monologue, conversation) and by the episode's route and route reasons (router v3). Each item shows
   up to 8 lines before its first cited line and 3 after its last (as the extractor was sent them), the cited
   lines, the quote and the gist. The sheet sends nothing.
3. **Independent model drafts.** `draft --model M` asks M about every item: the rubric as the system prompt, one
   item as the message, nothing else, so no draft sees another or my marks. Each gives a label and a one-line
   reason. Every request goes through the egress log under the monthly cap; a rerun asks only what's missing, and an
   item whose episode was forgotten or changed since the sheet isn't sent. Two models of different families, and no
   Anthropic model (Opus wrote the captures; the code refuses one):
   - **Terra** (`openai/gpt-6.1-terra`, low effort): it passed B4 and is the connection judge.
   - **Gemini 4 Carbon** (`google/gemini-4-carbon`, low effort): the strongest model in the v5.3 smoke test
     (im-evals `reports/2026-11-10-smoke-v5.3-models.md`, `models.md`) that's neither OpenAI's nor Anthropic's:
     recall 0.914 of Opus's captures with the fewest extras (4 in 20 episodes), every quote exact, and the only model
     to resolve a back-reference. A reader that agrees with Opus about what a thought is, from another family than
     Terra's. On OpenRouter at $2/$10 per million tokens.
4. **Blind disagreement marking.** `review` writes `review.md`: every item where the drafts disagree or either says
   uncertain, and 20 of the items they agree on, drawn by the sheet's seed, shuffled together. Each item is shown as
   on the sheet, followed by `mark:` and `note:`; nothing says why it's there, and no model's label or reason is
   shown. I mark `eligible` (`e`), `ineligible` (`i`) or `uncertain` (`u`, `?`). Which items were disputed and which
   audited is kept in `review-key.json`. Once `review.md` exists it's never rewritten: `review` again reads my marks
   back and lists what's unmarked or unreadable.
5. **Scoring that keeps model agreement apart from human validation.** `score` prints three sections, N in each:
   - (a) model–model agreement over every item both drafted: raw agreement, Cohen's kappa and the confusion table.
     It never reads my marks.
   - (b) human validation, on the items I marked: each model's accuracy against my marks (the marked items are
     mostly ones the models split on, so this is accuracy where it's hard, not over the sample), and how often the
     agreed label held on the 20 audited agreed items.
   - (c) the ineligible rate over the 100: my mark where I made one, the agreed label elsewhere; a disputed item I
     haven't marked is unresolved. The rate is ineligible over eligible plus ineligible, with its Wilson 95%
     interval; uncertain items are counted beside it. The interval treats the agreed labels as right; (b)'s audit
     says how far that holds.

## Rubric 1

SHA-256 `9573dbb1411a65eac1a990032ea57b1aba34ef151864e8c4139a2aedd216e976`. The drafting models get it as their
system prompt, followed by: "You'll be shown one capture: the lines around it, the lines it cites, its quote and its
gist. Label it eligible, ineligible or uncertain under this rubric, and say why in one line, naming what in the rubric
decides it. Don't evaluate, fact-check or moralize the thought."

```text
Capture eligibility, rubric 1.

A capture is a thought of mine that a model took from a transcript of my speech: the lines around it, the lines it
cites, the quote it took and its gist (a one-line restatement). "me" is me; S1, S2 and so on are other people;
"unknown" is a voice nobody was matched to. Go by the speaker labels. The question: does this capture deserve to be a
card, one of my thoughts worth keeping, that I'd recognize when I say it again? Not whether it's true, original,
important or kind, and not how well the gist is written.

eligible: in my lines I express a thought whose meaning reaches beyond the moment it was said in. Any of:
- an idea, theory, hunch, opinion, belief or preference I hold; half-formed, unfinished or obvious is fine;
- a question I'm actually wondering about;
- an observation about people, myself, social dynamics or the world that applies past this occasion, or a pattern
  in myself stated as a pattern;
- a judgment of what a particular person is like, or what they should do differently;
- a formulation, image, story or joke that carries a point of mine; exaggerated, provocative or false points count
  as said;
- something I want to build, try or look into, and what it's for; a method or rule for getting something done;
- someone else's thought I pass on as a point in the conversation (a teacher's rule, something I read), even with
  doubt or distance;
- a task or decision I explicitly mark to remember ("note to self").

ineligible: none of those is in my lines. The capture is only:
- logistics, errands, scheduling, instructions for the task at hand, or the next steps of something under way;
- small talk, a greeting, filler, or a reaction with no content ("nice", "wow", "that's crazy");
- narration of what's happening or happened, venting about one event or what someone did that one time, one
  instance of a pattern I don't state, or what's wrong with the thing in front of me, not stated as a rule;
- an insult or put-down with no point beyond it;
- someone else's point that I only agree with, say yes to or repeat back, adding nothing of my own;
- a view I voice only to mock, parody or reject it, deadpan included: asked straight, I wouldn't stand behind it,
  and the lines show I don't hold it;
- media playing or something read aloud, which I don't answer with a thought of my own;
- garbled transcription, or a fragment where you can't tell what I'm claiming even with the lines around it.

uncertain: only when what's shown can't settle it: the line that would decide is outside what's shown, or the
transcription is garbled enough that both readings stay open. Not for a close call: decide those by the tie-breaks.

Tie-breaks:
1. Judge my lines, read in their context, not the gist. A gist that says more than the lines doesn't make them
   eligible; a gist that states a real thought badly doesn't make it ineligible.
2. Small, personal, odd or unfinished counts. Doubt about whether it's worth keeping isn't a reason for
   ineligible: only the list above is.
3. Agreeing and then adding something of my own (a reason, a qualification, an example, a correction, a different
   conclusion) is eligible.
4. A joke, a tease or a story is eligible when it carries a point I'd stand behind asked straight, ineligible when
   it's only play about the moment.
5. If what the capture took from my lines includes an eligible thought, logistics or small talk around it don't
   make it ineligible.
6. Judge each capture alone: that another capture makes the same point doesn't change its label.
```

## Running it

On the NAS, after `git pull` and `sudo /mnt/fast/repos/ideamachine/install/nas.sh` (deploy.md, Updates):

```sh
sudo im audit-eligibility sheet /saved/eligibility-1                                  # sends nothing
sudo im audit-eligibility draft /saved/eligibility-1 --model openai/gpt-6.1-terra     # about $0.3
sudo im audit-eligibility draft /saved/eligibility-1 --model google/gemini-4-carbon   # about $1-3
sudo im audit-eligibility review /saved/eligibility-1                                 # sends nothing
# mark /saved/eligibility-1/review.md, then:
sudo im audit-eligibility review /saved/eligibility-1                                 # what's marked, what isn't
sudo im audit-eligibility score /saved/eligibility-1                                  # sends nothing
```

A draft that stops (the cap, the provider) picks up where it left off when run again. Expected cost, about $2 and
under $5 in all, within the monthly cap: about $0.003 a request for Terra (the connection judge's measured $0.0012 a
pair, with a longer prompt here), and $0.01-0.03 for Carbon (about 1,600 tokens in at $2 a million, and up to 2,000
out, reasoning included, at $10).
