"""Names said in a conversation, pseudonymized before they leave the box (policy C, decision 0008).

Each word of a name becomes a stable pseudonym: `P-` and five letters or digits from a keyed hash (HMAC-SHA256 with
IM_NAME_KEY, which never leaves the box). The same name gets the same pseudonym in every request, so a model can
still tell people apart, and "Julie" and "Julie Chen" share their first part; without the key, a pseudonym can't
be turned back into a name. What comes back is mapped through the same table, locally.

Where names come from:
- the people Hearsay knows as speakers (their labels);
- my own names, and any others, from config (`[egress] my_names`, `names`);
- a local entity model (GLiNER, pinned in the policy version), for anyone else. What it finds in each segment is
  kept (im.segment_names), so a segment is looked at once, not once per process per run; `warm` fills it ahead
  of a batch of trials. GLiNER reads lines in batches, and long ones in chunks it can see whole.

Names are replaced where they're capitalized, as speech-to-text writes them. A name that's also a common word
("Will", "May") is left alone at the start of a line or sentence, where it's most likely the word.
"""

import base64
import hashlib
import hmac
import os
import re
from wordfreq import zipf_frequency

KEY_ENV = "IM_NAME_KEY"
NER_MODEL = "urchade/gliner_medium-v2.1"
NER_REVISION = "40ec419335d09393f298636f471328b722c6da9e"   # the Dockerfile bakes the same one
NER_THRESHOLD = 0.5
FINDER = f"{NER_MODEL}@{NER_THRESHOLD}"
CHUNK_WORDS = 200        # GLiNER sees 384 tokens: a longer line is read in pieces of about this many words
BATCH = 32
THREADS_ENV = "IM_NER_THREADS"   # torch threads per process on the CPU (default 2), so parallel runs share cores
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

    def find(self, conn, segments: list[tuple]) -> set[str]:
        """The names the finder sees in these (segment_id, text) pairs, through the cache when it's GLiNER's."""
        if not self.finder:
            return set()
        if not hasattr(self.finder, "per_text"):
            return set(self.finder([t for _, t in segments]))
        return cached(conn, self.finder, segments)

    def scrub(self, texts: list[str], found: set | None = None) -> tuple[list[str], dict, list[str]]:
        """The texts with names replaced; pseudonym -> the name as first said; and my pseudonyms, if I'm named.
        `found` is what the finder saw in them already (`find`); otherwise the finder reads them now."""
        if found is None:
            found = set(self.finder(texts)) if self.finder else set()
        found = set(self.known) | {w for n in found for w in words(n)}
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


def cached(conn, finder, segments: list[tuple]) -> set[str]:
    """Names in these (segment_id, text) pairs: kept ones whose text hasn't changed, the rest found now and kept."""
    sha = {sid: hashlib.sha256(text.encode()).hexdigest() for sid, text in segments}
    have = {sid: found for sid, h, found in conn.execute(
        "SELECT segment_id, text_sha256, names FROM im.segment_names WHERE finder = %s AND segment_id = ANY(%s)",
        (finder.id, list(sha))) if h == sha[sid]}
    todo = [(sid, text) for sid, text in dict(segments).items() if sid not in have]
    if todo:
        found = finder.per_text([text for _, text in todo])
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO im.segment_names (segment_id, finder, text_sha256, names) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (segment_id, finder) DO UPDATE SET text_sha256 = excluded.text_sha256,
                     names = excluded.names, found_at = now()""",
                [(sid, finder.id, sha[sid], f) for (sid, _), f in zip(todo, found)])
        have |= {sid: f for (sid, _), f in zip(todo, found)}
    return {n for found in have.values() for n in found}


def warm(conn, finder, episode_ids: list, batch: int = 512) -> dict:
    """Find the names in these episodes' segments ahead of time, in large batches."""
    import time

    start = time.monotonic()
    rows = conn.execute(
        """SELECT DISTINCT s.segment_id, s.text FROM im.episode_segments es
           JOIN im.source_segments s USING (segment_id) WHERE es.episode_id = ANY(%s)""", (episode_ids,)).fetchall()
    before = conn.execute("SELECT count(*) FROM im.segment_names WHERE finder = %s", (finder.id,)).fetchone()[0]
    for i in range(0, len(rows), batch):
        cached(conn, finder, rows[i:i + batch])
    after = conn.execute("SELECT count(*) FROM im.segment_names WHERE finder = %s", (finder.id,)).fetchone()[0]
    return {"episodes": len(episode_ids), "segments": len(rows), "newly_read": after - before,
            "seconds": round(time.monotonic() - start, 1)}


def pending(conn, finder, limit: int) -> dict:
    """Find names in up to `limit` segments of current episodes not looked at yet, newest first: one chunk of a
    backlog. `more` says whether any are left."""
    rows = conn.execute(
        """SELECT s.segment_id, s.text FROM im.source_segments s
           JOIN (SELECT DISTINCT ON (es.segment_id) es.segment_id, e.started_at FROM im.episode_segments es
                 JOIN im.episodes e ON e.episode_id = es.episode_id AND e.current
                 ORDER BY es.segment_id, e.started_at DESC) c USING (segment_id)
           WHERE NOT EXISTS (SELECT 1 FROM im.segment_names n WHERE n.segment_id = s.segment_id AND n.finder = %s)
           ORDER BY c.started_at DESC, s.segment_id LIMIT %s""", (finder.id, limit + 1)).fetchall()
    cached(conn, finder, rows[:limit])
    return {"items": min(len(rows), limit), "more": len(rows) > limit}


TUNNEL_PORT = 55433   # Postgres on the NAS's loopback (install/compose.yaml), and the tunnel's end here


def job(finder=None) -> dict:
    """One chunk of the names backlog as a corral job (install/gpu.sh): through an ssh tunnel to the NAS when
    IM_SSH_TUNNEL names the host, with the role that can only read segments and write names (IM_DATABASE_URL).
    Takes CORRAL_SUGGESTED_ITEMS segments (2000 before corral knows the pace) and writes CORRAL_RESULT."""
    import json
    import socket
    import subprocess
    import time

    from im import db

    host, tunnel = os.environ.get("IM_SSH_TUNNEL"), None
    try:
        if host:
            tunnel = subprocess.Popen(
                ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                 "-L", f"127.0.0.1:{TUNNEL_PORT}:127.0.0.1:{TUNNEL_PORT}", host])
            for _ in range(100):
                if tunnel.poll() is not None:
                    raise SystemExit(f"ssh tunnel to {host} failed (exit {tunnel.returncode})")
                try:
                    socket.create_connection(("127.0.0.1", TUNNEL_PORT), timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.1)
        with db.connect(db.pipeline_dsn()) as conn:
            result = pending(conn, finder or Gliner(), int(os.environ.get("CORRAL_SUGGESTED_ITEMS") or 2000))
    finally:
        if tunnel:
            tunnel.terminate()
    if os.environ.get("CORRAL_RESULT"):
        with open(os.environ["CORRAL_RESULT"], "w") as f:
            json.dump(result, f)
    return result


def chunks(text: str, size: int = CHUNK_WORDS) -> list[str]:
    """A text in pieces of at most about `size` words, split between sentences where it can."""
    out, cur = [], []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        w = sentence.split()
        while len(w) > size:   # one sentence too long on its own
            if cur:
                out.append(" ".join(cur))
                cur = []
            out.append(" ".join(w[:size]))
            w = w[size:]
        if cur and len(cur) + len(w) > size:
            out.append(" ".join(cur))
            cur = []
        cur += w
    if cur:
        out.append(" ".join(cur))
    return out


def unscrub(text: str, back: dict) -> str:
    return TOKEN.sub(lambda m: back.get(m.group(0), m.group(0)), text) if back else text


class Gliner:
    """People's names in each text, by GLiNER: on the GPU if there is one, else the CPU with a few threads."""

    id = FINDER

    def __init__(self, model: str = NER_MODEL, threshold: float = NER_THRESHOLD, batch: int = BATCH):
        self.model_name, self.threshold, self.batch, self._model = model, threshold, batch, None

    def _load(self):
        if self._model is None:
            import torch
            from gliner import GLiNER
            torch.set_num_threads(int(os.environ.get(THREADS_ENV, "2")))
            self._model = GLiNER.from_pretrained(self.model_name, revision=NER_REVISION)
            if torch.cuda.is_available():
                self._model = self._model.to("cuda")
        return self._model

    def per_text(self, texts: list[str]) -> list[list[str]]:
        pieces = [(i, c) for i, t in enumerate(texts) for c in chunks(t)]
        out: list[list[str]] = [[] for _ in texts]
        if pieces:
            found = self._load().inference([c for _, c in pieces], ["person"], threshold=self.threshold,
                                           batch_size=self.batch)
            for (i, _), entities in zip(pieces, found):
                out[i] += [e["text"] for e in entities]
        return out

    def __call__(self, texts: list[str]) -> set[str]:
        return {n for found in self.per_text(texts) for n in found}
