"""Jev, TypeSafe's System One decision model: typed questions about structured state, answered with calibrated
probabilities (docs.typesafe.ai). Not a chat model: the state is named fields, each question is one atomic
judgment (a Noul answers the probability of yes), and the decision is made here, in code.

Its first job here is drafting the probe sheets (tension, analogy, summary faithfulness), so it can be scored
against my marks beside the chat models. Every question's wording is in this module. A judgment with several
forms (a tension can be a contradiction, a change of mind, a trade-off or competing pulls) is asked as one Noul
per form and combined in code, as TypeSafe recommends.

It's reached through OpenRouter by default, which serves TypeSafe's own API (not a chat format) with zero data
retention, on the same OPENROUTER_API_KEY as the other models; with only TYPESAFE_API_KEY set, it goes to TypeSafe
directly, where zero retention is for enterprise accounts. Price: $0.042 per million input tokens; output is free.
"""

import json
import os
import urllib.error
import urllib.request

from im import llm

ROUTES = {   # (endpoint, key's environment variable, how the model is named there)
    "openrouter": ("https://openrouter.ai/api/v1/systemone", "OPENROUTER_API_KEY",
                   lambda m: "typesafe/" + m.rsplit(".", 1)[0]),
    "typesafe": ("https://api.typesafe.ai/v1/systemone", "TYPESAFE_API_KEY", lambda m: m),
}
MODEL = "jev-1.14.0"               # pinned: an alias (jev-latest) can change answers without a change here
PRICE_IN = 0.042 / 1e6             # $ per input token
YES, NO = 0.7, 0.3                 # TypeSafe's starting thresholds: between them, the state doesn't settle it

QUESTIONS = {
    "tension": {
        "contradiction": ("Can `a` and `b` not both be true as said?",
                          {"true": "Believing one as said means rejecting the other",
                           "false": "Both can be held as said at the same time"}),
        "change_of_mind": ("Does one of `a` and `b` reverse a view the other holds?", None),
        "trade_off": ("Do `a` and `b` favour opposite sides of a trade-off?",
                      {"true": "Each gives up what the other values",
                       "false": "They value the same things, or unrelated things"}),
        "competing_pulls": ("Do `a` and `b` pull a person in directions that would have to be balanced?", None),
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


# Probes: questions asked beside the ones that decide, kept in the draft but never in `p`, so a new wording or a
# new decomposition is scored against my marks without changing what's being compared. PROBE_SET names the draft.
PROBE_SET = "probes1"
PROBES = {
    "tension": {   # competing pulls alone fires on anything with two angles; what it may be missing (2026-11-04)
        "same_subject": ("Are `a` and `b` about the same subject?",
                         {"true": "They discuss the same situation, activity or kind of thing",
                          "false": "They discuss different situations or things"}),
        "different_structure": ("Do `a` and `b` rest on different underlying moves or mechanisms?",
                                {"true": "The reasoning in one works differently from the reasoning in the other",
                                 "false": "They make the same kind of move"}),
    },
}
# Combinations of decisive and probe answers, computed here and scored like any question.
COMBOS = {
    "tension": {
        "pulls_and_same_subject": lambda q: min(q["competing_pulls"], q["same_subject"]),
        "pulls_same_subject_different_structure": lambda q: min(q["competing_pulls"], q["same_subject"],
                                                                q["different_structure"]),
        "pulls_x_same_subject_x_different_structure": lambda q: q["competing_pulls"] * q["same_subject"]
        * q["different_structure"],
    },
}


def combine(question: str, p: dict[str, float]) -> float:
    """One probability from the atomic ones: any form of tension, or a summary that adds or misstates (the most
    likely of them); an analogy needs different subjects and the same structure (the less likely)."""
    return min(p.values()) if question == "analogy" else max(p.values())


def answer(p: float) -> str:
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
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 429 or e.code >= 500:
                raise llm.Unavailable(f"Jev via {self.route}, HTTP {e.code}: {detail}") from None
            raise RuntimeError(f"Jev via {self.route}, HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise llm.Unavailable(f"Jev via {self.route}: {e}") from None

    def body(self, question: str, state: dict, probes: bool = False) -> dict:
        qs = {}
        asked = QUESTIONS[question] | (PROBES.get(question, {}) if probes else {})
        for name, (instructions, criteria) in asked.items():
            qs[name] = {"type": "noul", "instructions": instructions, **({"criteria": criteria} if criteria else {})}
        return {"model": self.sent_as, "state": state, "questions": qs}

    def ask(self, question: str, state: dict, probes: bool = False) -> tuple[dict, dict]:
        """The atomic probabilities, the combined one, and with probes their answers and combinations; and what
        the request cost: ({name: p, "p": p, ...}, usage)."""
        body = self.body(question, state, probes)
        r = self.post(body)
        got = {name: float(a["noul"]) for name, a in r["answers"].items()}
        deciding = {k: v for k, v in got.items() if k in QUESTIONS[question]}
        combos = {k: f(got) for k, f in COMBOS.get(question, {}).items()} if probes else {}
        usage = r.get("usage") or {}
        tokens_in = usage.get("input_tokens") or 0
        return got | combos | {"p": combine(question, deciding)}, {
            "served_by": r.get("model") or self.model, "input_tokens": tokens_in,
            "output_tokens": usage.get("output_tokens") or 0, "cost_usd": round(tokens_in * PRICE_IN, 8),
            "payload": json.dumps(body)}
