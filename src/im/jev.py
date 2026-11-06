"""Jev, TypeSafe's System One decision model: typed questions about structured state, answered with calibrated
probabilities (docs.typesafe.ai). Not a chat model: the state is named fields, each question is one atomic
judgment (a Noul answers the probability of yes), and the decision is made here, in code.

Its first job here is drafting the probe sheets (tension, analogy, summary faithfulness), so it can be scored
against my marks beside the chat models. Every question's wording is in this module. A judgment is asked as
atomic Nouls and combined in code, as TypeSafe recommends: a tension as four wordings averaged, an analogy as two
conditions that must both hold.

It's reached through OpenRouter by default, which serves TypeSafe's own API (not a chat format) with zero data
retention, on the same OPENROUTER_API_KEY as the other models; with only TYPESAFE_API_KEY set, it goes to TypeSafe
directly, where zero retention is for enterprise accounts. Price: $0.042 per million input tokens; output is free.
"""

import json
import os

from im import llm

ROUTES = {   # (endpoint, key's environment variable, how the model is named there)
    "openrouter": ("https://openrouter.ai/api/v1/systemone", "OPENROUTER_API_KEY",
                   lambda m: "typesafe/" + m.rsplit(".", 1)[0]),
    "typesafe": ("https://api.typesafe.ai/v1/systemone", "TYPESAFE_API_KEY", lambda m: m),
}
MODEL = "jev-1.14.0"               # pinned: an alias (jev-latest) can change answers without a change here
PRICE_IN = 0.042 / 1e6             # $ per input token
YES, NO = 0.7, 0.3                 # TypeSafe's starting thresholds: between them, the state doesn't settle it
# A question read as a ranking gets one cut-off instead: at it or above is yes, below it no. Tension: on four sheets
# (150 pairs, 28 tensions, 10 new; 2026-11-05), 0.45 kept 9 of the 10 new ones and three quarters of all, flagging
# 52; 0.4 kept no more and flagged 73, 0.5 kept 6 new. A proposal I keep or dismiss, so recall comes first.
CUT = {"tension": 0.45}

# What decides. A tension is asked in four wordings of one question and decided by their mean: on the two tension
# sheets (70 pairs, 14 tensions, 2026-11-04), no single question held above 0.80 AUC on both, the four wordings
# correlate 0.7 to 0.9, and their mean held 0.79 to 0.81 on both while ranking tensions new to me as well as known
# ones (pulls_v2 alone favoured known ones, 0.84 against 0.77). The forms asked before (contradiction, change of
# mind, trade-off, the strongest counting: 0.71 to 0.74) stay as probes. A changed set gets a new name here, which names its drafts.
QUESTION_SETS = {"tension": "pulls4"}
QUESTIONS = {
    "tension": {
        "competing_pulls": ("Do `a` and `b` pull a person in directions that would have to be balanced?", None),
        "pulls_v2": ("Would someone who agrees with both `a` and `b` have to give up some of one to honour the "
                     "other?", None),
        "pulls_v3": ("Is there a tension between `a` and `b`?", None),
        "pulls_v4": ("`a` and `b` pull in opposite directions.", None),
    },
    "analogy": {
        "different_subjects": ("Are `a` and `b` about different subjects?",
                               {"true": "They are about different domains, people or things",
                                "false": "They are about the same domain or topic"}),
        "same_structure": ("Do `a` and `b` rest on the same underlying move, pattern or mechanism?",
                           {"true": "The same move applies in both, whatever the subject",
                            "false": "Their reasoning works differently"}),
    },
    "summary": {
        "adds": ("Does `summary` state something that `lines` don't say?",
                 {"true": "It adds an inference, conclusion, motive or outcome that isn't in the lines",
                  "false": "Everything it says is in the lines, perhaps more briefly"}),
        "misstates": ("Does `summary` contradict or misstate what `lines` say?", None),
    },
}


# Probes: questions asked beside the ones that decide, kept in the draft but never in `p`, so new axes and new
# wordings are scored against my marks without changing what's being compared. PROBE_SET names the draft.
# Tension's probes2 (2026-11-04) also asked whether the sides differ in structure, only in emphasis, or are an
# exception, and whether both are mine: all near chance on both sheets, so dropped.
PROBE_SET = {"tension": "probes4", "analogy": "probes2"}
BOTH = {"true": "Someone could hold both without strain", "false": "Holding one means giving up some of the other"}
PROBES = {
    "tension": {
        # The forms that decided before.
        "contradiction": ("Can `a` and `b` not both be true as said?",
                          {"true": "Believing one as said means rejecting the other",
                           "false": "Both can be held as said at the same time"}),
        "change_of_mind": ("Does one of `a` and `b` reverse a view the other holds?", None),
        "trade_off": ("Do `a` and `b` favour opposite sides of a trade-off?",
                      {"true": "Each gives up what the other values",
                       "false": "They value the same things, or unrelated things"}),
        # Kinds of pull: inner conflict and opposite stance led on one sheet and not the other.
        "opposite_stance": ("Do `a` and `b` take opposite stances toward the same rule, norm or value?", None),
        "cant_act_on_both": ("Would doing what one of `a` and `b` recommends make it harder to do what the other "
                             "recommends?", None),
        "undercuts": ("Does one of `a` and `b` undercut the reasoning or premise of the other?", None),
        "inner_conflict": ("Do `a` and `b` describe conflicting wants or traits in the same person?", None),
        # What may tell new from known: my new tensions ran across subjects, on all four sheets (10 new, 18
        # known). Competing values held on two sheets and not on the other two, so probes4 dropped it.
        "same_subject": ("Are `a` and `b` about the same subject?",
                         {"true": "They discuss the same situation, activity or kind of thing",
                          "false": "They discuss different situations or things"}),
        # A sign of no tension: lower on my tensions on all four sheets (0.20 to 0.34).
        "both_true": ("Could `a` and `b` both be completely true at once, with no qualification?", BOTH),
        "both_true_v2": ("Someone could fully agree with both `a` and `b`.", None),
    },
    # Analogy's first probes (2026-11-05), many wordings to pare down as tension's were. The one sheet so far found
    # three analogies, each a specific shared move: a template (X : better X :: then : now), a cause (bad work
    # blamed on its maker's state), a trade-off (easy against perfect); what sounded alike only by genre (two jokes,
    # two category questions) wasn't one. probes2 (after analogy-2, 2026-11-06: 3 analogies in 25 Jev pairs) drops
    # the wordings that ranked them worst, AUC 0.65 to 0.68 where most held 0.90 or more: whether `a` explains `b`,
    # a shared template, the payoff of looking through one at the other, and whether what they share is specific.
    "analogy": {
        # Ways of asking for the same structure.
        "same_structure_v2": ("`a` and `b` rest on the same underlying pattern, in different settings.", None),
        "same_move": ("Do `a` and `b` make the same move, each in its own setting?", None),
        "same_mechanism": ("Does the same cause-and-effect mechanism drive what `a` and `b` describe?", None),
        "same_relation": ("Does `a` relate two things the way `b` relates two others, as in X is to Y as Z is "
                          "to W?", None),
        "roles_map": ("Could the parts of `a` be matched one to one with parts of `b` that play the same roles?",
                      None),
        "general_rule": ("Are `a` and `b` both instances of one general rule or principle?", None),
        "analogy_v2": ("Is there an analogy between `a` and `b`?", None),
        "same_lesson": ("Would the lesson of `a` apply to the situation in `b`?", None),
        # Kinds of shared structure, from the three found.
        "same_trade_off": ("Do `a` and `b` turn on the same trade-off, such as easy against thorough, or cheap "
                           "against reliable?", None),
        "same_reasoning": ("Do `a` and `b` reach their point by the same line of reasoning?", None),
        "same_problem": ("Do `a` and `b` describe the same underlying problem in different places?", None),
        # Strictness: what they share must be more than their genre.
        "only_genre": ("Is what `a` and `b` have in common only their genre, such as both being jokes, "
                       "complaints or opinions?",
                       {"true": "They share a kind of statement and nothing more specific",
                        "false": "They share a specific move, mechanism or pattern"}),
        # Distance and novelty.
        "different_domains": ("Are `a` and `b` from different areas of life, such as work and leisure, or "
                              "technology and relationships?", None),
        "surprising": ("Would someone who knows both `a` and `b` be surprised to see them linked?", None),
    },
}
# Combinations of deciding and probe answers, computed here and scored like any question.
COMBOS = {
    "tension": {
        "pulls_not_both_true": lambda q: _mean(q, QUESTIONS["tension"])
        * (1 - (q["both_true"] + q["both_true_v2"]) / 2),
        "pulls_across_subjects": lambda q: _mean(q, QUESTIONS["tension"]) * (1 - q["same_subject"]),
        "old_p": lambda q: max(q["contradiction"], q["change_of_mind"], q["trade_off"], q["competing_pulls"]),
    },
    "analogy": {
        "structure_mean": lambda q: _mean(q, ("same_structure", "same_structure_v2", "same_move", "same_mechanism",
                                             "same_relation", "roles_map", "general_rule", "analogy_v2")),
        "structure_not_genre": lambda q: q["same_structure"] * (1 - q["only_genre"]),
    },
}


def _mean(p: dict[str, float], names) -> float:
    return sum(p[n] for n in names) / len(names)


def combine(question: str, p: dict[str, float]) -> float:
    """One probability from the atomic ones: a tension is the mean of its wordings; an analogy needs different
    subjects and the same structure (the less likely); a summary fails if it adds or misstates (the more likely)."""
    if question == "tension":
        return _mean(p, p)
    return min(p.values()) if question == "analogy" else max(p.values())


def answer(question: str, p: float) -> str:
    if question in CUT:
        return "yes" if p >= CUT[question] else "no"
    return "yes" if p >= YES else "no" if p <= NO else "unsure"


class Jev:
    """`model` is TypeSafe's versioned ID (jev-1.14.0); `route` is openrouter (the default, when its key is set) or
    typesafe."""

    def __init__(self, key: str | None = None, post=None, model: str = MODEL, route: str | None = None):
        self.route = route or ("openrouter" if os.environ.get("OPENROUTER_API_KEY") or post else "typesafe")
        self.url, env, named = ROUTES[self.route]
        self.model, self.sent_as = model, named(model)
        self.key = key or os.environ.get(env)
        if not self.key and post is None:
            raise SystemExit("Jev needs OPENROUTER_API_KEY (zero retention) or TYPESAFE_API_KEY (config/pipeline.env)")
        self.post = post or self._post

    def _post(self, body: dict) -> dict:
        return llm.post_json(self.url, self.key, body, f"Jev via {self.route}", 120)

    def body(self, question: str, state: dict, probes: bool = False) -> dict:
        qs = {}
        asked = QUESTIONS[question] | (PROBES.get(question, {}) if probes else {})
        for name, (instructions, criteria) in asked.items():
            qs[name] = {"type": "noul", "instructions": instructions, **({"criteria": criteria} if criteria else {})}
        return {"model": self.sent_as, "state": state, "questions": qs}

    def complete(self, body: dict) -> llm.Reply:
        """Send a body (egress.send logs it): Jev's answer as it came, as JSON, and what it cost (output is free)."""
        r = self.post(body)
        usage = r.get("usage") or {}
        tokens_in = usage.get("input_tokens") or 0
        return llm.Reply(json.dumps(r), "end_turn", r.get("model") or self.model, r.get("id"), tokens_in,
                         usage.get("output_tokens") or 0, round(tokens_in * PRICE_IN, 8))


def read(question: str, r: dict, probes: bool = False) -> dict[str, float]:
    """From Jev's answer, the atomic probabilities, the combined one, and with probes their combinations:
    {name: p, ..., "p": p}. An answer missing a deciding question raises KeyError."""
    got = {name: float(a["noul"]) for name, a in r["answers"].items()}
    deciding = {k: got[k] for k in QUESTIONS[question]}
    combos = {k: f(got) for k, f in COMBOS.get(question, {}).items()} if probes else {}
    return got | combos | {"p": combine(question, deciding)}
