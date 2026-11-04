# 0008: Other providers' models, and names said pseudonymized

_2026-11-04. Status: accepted for trials; the live switch waits on the checklist below. Amends
[0004](0004-claude-extraction.md)'s privacy policy and B3's method in [ROADMAP-v2.md](../ROADMAP-v2.md); my decision._

## Context

- Extraction is on Opus 5.5. Sonnet found 82% of what Opus found at 42% of the cost (B3, 2026-10-28), and Opus
  stayed. Spend is about $25 a month, but it will grow severalfold once I wear the pendant all day. Testing now, at
  today's volume, is cheap; finding out later is expensive.
- Other providers have models priced between Sonnet and Haiku: GPT-6.1 Sol and Gemini 4 Argon at Sonnet's price,
  GPT-6.1 Terra at half that, and GLM-5.3 (and its flash version) cheaper still. If one finds what Sonnet finds,
  that's significant.
- Policy B pseudonymizes speakers but sends names *said* as they were said. Another provider seeing my
  conversations is fine by me; sending them every name I and the people around me say isn't necessary.
- B3 scored a candidate against my keep/discard verdicts. I'm not good at that judgment, and M2 stopped reading
  verdicts: capture is the soup, and the lattice's relationships decide what matters
  ([0007](0007-neutral-proposals.md)).

## Decision

**Other providers, through OpenRouter.** A model named with a provider prefix (`openai/gpt-6.1-sol`,
`z-ai/glm-5.3`) goes to OpenRouter (`im.llm`); `claude-*` goes to Anthropic as before. OpenRouter is asked for
strict JSON-schema output, only from providers that support it and that don't keep or train on requests
(`provider.data_collection: deny`). It reports each request's cost, which goes to the egress log like any other.
One key (`OPENROUTER_API_KEY`) covers every model, so trying another is a flag.

**Policy C: names said are pseudonymized** (`im.names`). Each word of a name becomes `P-` and five characters of
an HMAC-SHA256 under a key that never leaves the box (`IM_NAME_KEY`): the same name gets the same pseudonym in
every request, so a model can still tell people apart and link "Julie" to "Julie Chen", but can't recover the
name. The names come from the people Hearsay knows as speakers, from config (`[egress] my_names`, `names`), and
from GLiNER (`urchade/gliner_medium-v2.1`, pinned, run locally on the CPU) for everyone else. Only capitalized
occurrences are replaced, and a name that's also a common word ("Will", "May") is left alone where a sentence
starts. The payload says that names are pseudonyms, and which one is mine. What comes back is mapped through the
same table, locally, so stored quotes keep the real names. The model and threshold are part of the policy
version: changing either re-sends.

**Trials compare models on a fixed sample, against a reference.** `im trial` reads the same sample of current
episodes for every model (ordered by a hash of the episode id), under policy C by default, and reports a
candidate's **recall** of the reference's captures (Opus, matched by overlapping segments and `quote_score`, as
verdicts are carried), recall by kind, its extra captures, and its cost against the reference's. Verdicts aren't
read. Blind pairs (B3's preference check) then decide between the models that come close, since recall against
Opus can't say whether a capture Opus missed is better or worse.

The order: Opus, Sonnet and Haiku on `v3` under policy C while the Anthropic credits last (to 2026-11-25); then
Sol, GLM-5.3, GLM-5.3-flash and Terra; then local Qwen or Granite on eeyore, scheduled around Hearsay; then blind
pairs for whichever reach Sonnet's 82%.

## Before the live switch

Policy C is for trials until each of these is done. A switch re-reads every episode once, so it goes out together
with any prompt switch, as one re-read.

1. Every stage that sends what was said scrubs it: matching (`lattice.py`), themes, vocabulary, and the analogy
   spike. Today they send stored quotes, which keep real names.
2. ~~GLiNER's results are cached by segment in the database: the live run rebuilds every pending payload each
   hour.~~ Done 2026-11-04 (`im.segment_names`, `im names warm`), after ten trials in parallel each ran GLiNER over
   the same sample on the NAS's CPU and sent nothing for 40 minutes. Batched, with long lines chunked, and two
   torch threads per process. The dev box's GPU fills it for backlogs as a corral job (`install/gpu.sh`,
   deploy.md), through `ssh nas` as a role that can only read segments and write names.
3. A look at what GLiNER misses and over-matches on a few hundred real segments.

## Consequences

- Third parties' words now go to whichever provider a trial names, under OpenRouter's no-retention routing. That's
  my call; the egress log records the model, provider (`served_by`) and policy of every request.
- Jev (TypeSafe, `im.jev`, 2026-11-04) takes typed yes/no questions about structured state, not chat, under the
  same policy C. It goes through OpenRouter (`typesafe/jev-1.14`), which serves TypeSafe's own API with zero data
  retention, on the OpenRouter key; TypeSafe directly (TYPESAFE_API_KEY) is the fallback, where zero retention is
  for enterprise accounts only.
- `IM_NAME_KEY` must never change: a new key changes every pseudonym, so every payload, so everything is re-read.
  It lives in `config/pipeline.env`, backed up with the rest of config.
- Pseudonyms are linkable across requests by design. A provider could learn that `P-K3M7Q` comes up often, never
  who that is.
- The pipeline image now bakes GLiNER in next to the embedding model, so runs stay offline.
