"""Names said in a conversation, pseudonymized before they leave the box (policy C, decision 0008).

Each word of a name becomes a stable pseudonym: `P-` and five letters or digits from a keyed hash (HMAC-SHA256 with
IM_NAME_KEY, which never leaves the box). The same name gets the same pseudonym in every request, so a model can
still tell people apart, and "Julie" and "Julie Chen" share their first part; without the key, a pseudonym can't
be turned back into a name. What comes back is mapped through the same table, locally.

Where names come from:
- the people Hearsay knows as speakers (their labels);
- my own names, and any others, from config (`[egress] my_names`, `names`);
- a local entity model (GLiNER, pinned in the policy version), for anyone else.

Names are replaced where they're capitalized, as speech-to-text writes them. A name that's also a common word
("Will", "May") is left alone at the start of a line or sentence, where it's most likely the word.
"""

import base64
import hashlib
import hmac
import os
import re
from functools import lru_cache

from wordfreq import zipf_frequency

KEY_ENV = "IM_NAME_KEY"
NER_MODEL = "urchade/gliner_medium-v2.1"
NER_THRESHOLD = 0.5
COMMON = 4.5             # Zipf: "will" and "may" are above, "julie" and "mark" below
TOKEN = re.compile(r"\bP-[A-Z2-7]{5}\b")
NOT_NAMES = {"i", "me", "my", "you", "he", "she", "they", "we", "him", "her", "them", "us", "mom", "dad", "god"}
POLICY = "C1;speakers-pseudonymized;spoken-names-hmac;ner={model}@{threshold};no-registry"


def key_from_env() -> bytes:
    key = os.environ.get(KEY_ENV)
    if not key:
        raise SystemExit(f"{KEY_ENV} isn't set: policy C needs it to pseudonymize names (install/nas.sh makes one)")
    return key.encode()


def token(word: str, key: bytes) -> str:
    digest = hmac.new(key, word.casefold().encode(), hashlib.sha256).digest()
    return "P-" + base64.b32encode(digest).decode()[:5]


def words(name: str) -> list[str]:
    """The parts of a name worth pseudonymizing: capitalized words, at least two letters, not pronouns."""
    return [w for w in re.findall(r"[^\W\d_][\w'’-]*", name)
            if len(w) >= 2 and w[0].isupper() and w.casefold() not in NOT_NAMES]


class Scrubber:
    def __init__(self, key: bytes, known=(), mine=(), finder=None, policy: str | None = None):
        self.key, self.finder = key, finder
        self.known = {w for n in known for w in words(n)}
        self.mine = [w for n in mine for w in words(n)]
        self.known |= set(self.mine)
        self.policy = policy or POLICY.format(model=NER_MODEL if finder else "none",
                                              threshold=NER_THRESHOLD if finder else 0)

    def scrub(self, texts: list[str]) -> tuple[list[str], dict, list[str]]:
        """The texts with names replaced; pseudonym -> the name as first said; and my pseudonyms, if I'm named."""
        found = set(self.known)
        if self.finder:
            found |= {w for n in self.finder(texts) for w in words(n)}
        if not found:
            return texts, {}, []
        alternation = "|".join(re.escape(w) for w in sorted(found, key=len, reverse=True))
        pattern = re.compile(rf"(?<![\w'’-])({alternation})(?![\w-])")
        back: dict = {}

        def swap(m: re.Match) -> str:
            word = m.group(1)
            before = m.string[:m.start()].rstrip()
            if zipf_frequency(word.lower(), "en") >= COMMON and (not before or before[-1] in ".!?"):
                return word
            t = token(word, self.key)
            back.setdefault(t, word)
            return t

        out = [pattern.sub(swap, t) for t in texts]
        mine = [t for t in dict.fromkeys(token(w, self.key) for w in self.mine) if t in back]
        return out, back, mine


def for_trials(conn, egress_config, finder=None) -> Scrubber:
    """Policy C's scrubber: the key from the environment, every name Hearsay and config know, and GLiNER."""
    from im import egress
    return Scrubber(key_from_env(), [*egress.known_names(conn), *egress_config.names], egress_config.my_names,
                    finder or Gliner())


def unscrub(text: str, back: dict) -> str:
    return TOKEN.sub(lambda m: back.get(m.group(0), m.group(0)), text) if back else text


class Gliner:
    """People's names in each text, by GLiNER on the CPU. Loaded on first use; results cached by text."""

    def __init__(self, model: str = NER_MODEL, threshold: float = NER_THRESHOLD):
        self.model_name, self.threshold, self._model = model, threshold, None

    def __call__(self, texts: list[str]) -> set[str]:
        return {n for t in texts for n in self._names(t)}

    @lru_cache(maxsize=200_000)  # noqa: B019 (one finder per process)
    def _names(self, text: str) -> tuple[str, ...]:
        if self._model is None:
            from gliner import GLiNER
            self._model = GLiNER.from_pretrained(self.model_name)
        return tuple(e["text"] for e in self._model.predict_entities(text, ["person"], threshold=self.threshold))
