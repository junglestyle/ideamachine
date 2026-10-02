# 0002: First triage eval: nothing beats the base rates

_2026-10-01. Status: superseded in part. Chosen: option 1 (a local LLM), plus context in the state. See the follow-up at the end._

## Data

52 exact labels on real episodes (from 83 at the time), labeled by me with `im label`:

| Question | Labels |
|---|---|
| kind | chatter 29, idea 21, decision 1, task 1, noise 0 |
| me thinking | yes 12, no 40 |
| keep | 1: 21, 2: 26, 3: 3, 4: 1, 5: 1 |
| project | none 17, 8 projects with 1–10 each |
| boundaries | ok 44, should split 5, should merge 3. 85%: meets the segmentation exit criterion |

## Results (`im eval`; the fallback is 5-fold cross-validated)

| Question | Always the most common answer | Fallback | Laya english | Laya multilingual | Laya typed-decisions |
|---|---|---|---|---|---|
| me thinking | 77% | 77% | 38% | 48% | 33% |
| kind | 56% | 54% | 40% | 33% | 33% |
| project | 33% | 31% | 37% | 27% | 40% |
| keep (exact 1–5) | 50% | 50% | 19% | 31% | 2% |

With 52 labels, a difference of under ~13 points is noise.

- **The fallback has learned only the base rates.** 52 labels are too few for a classifier over embeddings.
- **Laya zero-shot is below the base rates.** Temperature scaling (fitted on held-out folds) doesn't change which
  answer wins, so it can't help. The confusion matrices show why:
  - "Me thinking" probabilities sit at 0.3–0.7 for both classes: no signal.
  - Kind confuses chatter and idea in both directions, and over-calls "decision".
  - Keep runs about one point higher than my scale.
  - Project mostly answers "none". It misses a place-based project and a person-based one, which are about *where I am* and *who I'm
    with*, things the transcript text rarely says.
- **Ops:** Laya at `max_len=8192` was OOM-killed (26.5 GB) on an 8,400-token episode. The cap is now 4096
  (~5 GB).

## What this means for the roadmap

The Phase 1 exit criterion "auto_file precision on kind ≥ 90%" is out of reach with these backends. Following the
roadmap's own rule ("Eval before trust"), no automated triage output drives anything yet.

## Options

1. **A local instruct LLM as a triage backend**: e.g. an 8–14B model via Ollama on eeyore's RTX 5070 Ti (16 GB),
   which is already installed there. Hearsay already uses eeyore's GPU on an hourly timer, so there's precedent.
   Phase 4 needs a local 7–14B model for gists anyway. It fits behind the existing backend interface, and the
   same 52 labels evaluate it.
2. **Put context in the state, and code what code can know**: speaker names present and time of day go into the
   triage state. A people → project mapping in the registry (a friend speaks → that friend's project) is a rule, not a model
   judgment. Location (the bar I go to) isn't in the transcript at all.
3. **Simplify what triage decides**: what drives action is "surface this or not". In my labels that's keep ≥ 3,
   which is 5 of 52. A binary keep (ROADMAP open question 4) plus `kind` only among keepers is easier to judge and
   to evaluate. 5 positives is too few either way, though: more labels need to target likely keepers.
4. **More labels** for the fallback. Probably hundreds per question; slow, but they're needed for any eval of
   the rare classes.

These combine. The ROADMAP stack assumption "CPU-first on TrueNAS" would change for triage if option 1 is
taken.

## Follow-up (same day): local LLM

`gpt-oss:20b` through Ollama on eeyore's GPU (`think=low`), same questions, same 52 labels:

| Question | Baseline | LLM |
|---|---|---|
| me thinking | 77% | 73% |
| kind | 56% | 54% |
| project | 33% | **54%** |
| keep | 50% | 46% |

- About 1 s per episode. Token log-probabilities are near-certain after the model's reasoning, so they aren't
  usable as confidence.
- Most of the remaining misses were label-definition mismatches, not model errors:
  - Projects I meant as *who's there or where* (a person, a place), while the question asked what it was *about*.
  - The registry didn't know the name the bar goes by.
  - "Idea" labels I gave to chatter because there were so few ideas.
- Changes made from that:
  - The project question now covers place as well as topic.
  - The person-based project is replaced by the topic it was really about, and the bar's project gets the name it goes by as an alias.
  - "Idea" counts anyone's idea.
  - Keep counts "note to self" and pendant taps.
  - Episodes carry time, speakers and taps in their context.
- Because those changes were made after seeing these 52 labels, the next number that means anything is the score
  on 20–30 labels collected afterwards.
