"""Jev, TypeSafe's System One decision model: typed questions about structured state, answered with calibrated
probabilities (docs.typesafe.ai). Not a chat model: the state is named fields, each question is one atomic
judgment (a Noul answers the probability of yes), and the decision is made here, in code.

Its first job here is drafting the probe sheets (tension, analogy, summary faithfulness), so it can be scored
against my marks beside the chat models. Every question's wording is in this module. A judgment with several
forms (a tension can be a contradiction, a change of mind, a trade-off or competing pulls) is asked as one Noul
per form and combined in code, as TypeSafe recommends.

Price: $0.042 per million input tokens; output is free. The key is TYPESAFE_API_KEY (config/pipeline.env).
"""

import json
import os
import urllib.error
import urllib.request

from im import llm

URL = "https://api.typesafe.ai/v1/systemone"
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


def combine(question: str, p: dict[str, float]) -> float:
    """One probability from the atomic ones: any form of tension, or a summary that adds or misstates (the most
    likely of them); an analogy needs different subjects and the same structure (the less likely)."""
    return min(p.values()) if question == "analogy" else max(p.values())


def answer(p: float) -> str:
    return "yes" if p >= YES else "no" if p <= NO else "unsure"


class Jev:
    def __init__(self, key: str | None = None, post=None, model: str = MODEL):
        self.key, self.model = key or os.environ.get("TYPESAFE_API_KEY"), model
        if not self.key and post is None:
            raise SystemExit("TYPESAFE_API_KEY isn't set: Jev needs it (config/pipeline.env)")
        self.post = post or self._post

    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 429 or e.code >= 500:
                raise llm.Unavailable(f"TypeSafe HTTP {e.code}: {detail}") from None
            raise RuntimeError(f"TypeSafe HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise llm.Unavailable(f"TypeSafe: {e}") from None

    def body(self, question: str, state: dict) -> dict:
        qs = {}
        for name, (instructions, criteria) in QUESTIONS[question].items():
            qs[name] = {"type": "noul", "instructions": instructions, **({"criteria": criteria} if criteria else {})}
        return {"model": self.model, "state": state, "questions": qs}

    def ask(self, question: str, state: dict) -> tuple[dict, dict]:
        """The atomic probabilities and the combined one, and what the request cost: ({name: p, "p": p}, usage)."""
        body = self.body(question, state)
        r = self.post(body)
        p = {name: float(a["noul"]) for name, a in r["answers"].items()}
        usage = r.get("usage") or {}
        tokens_in = usage.get("input_tokens") or 0
        return p | {"p": combine(question, p)}, {
            "served_by": r.get("model") or self.model, "input_tokens": tokens_in,
            "output_tokens": usage.get("output_tokens") or 0, "cost_usd": round(tokens_in * PRICE_IN, 8),
            "payload": json.dumps(body)}
