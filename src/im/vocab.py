"""My vocabulary for Hearsay (migration 028): words Parakeet should know, found in what I write and correct.

Each run refreshes the candidates and, when IM_VOCAB_PATH is set, writes the approved terms (pub.vocab, status
'in') to that file in Hearsay's format, atomically and only when the list changed. Hearsay merges it with its own
list and re-transcribes only when I commit there, so the file can change freely; but over-boosting hurts accuracy,
so suggestions stay few and good: only rare words (wordfreq Zipf below RARE), and repairs need a misheard quote.
"""

import hashlib
import json
import os
import re
from pathlib import Path

from wordfreq import zipf_frequency

from im import themes

RARE = 2.7               # Zipf frequency: about 1 in 2 million words. "moderation" is 3.5, "goetia" 1.5
MIN_LEN = 4
ARCHIVE_MAX = 150        # the rarest archive words Claude picks from
QUOTES = 4
WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’-]*[A-Za-z0-9]")

FIND_SYSTEM = """You find words my speech-to-text misheard. Each idea below was written from what was said; its \
quotes are the transcript. For each idea, list words in the idea (names, terms of art, foreign or rare words) \
that a quote renders as a mishearing: different words that sound alike, e.g. the idea says "goetia" where the \
quote says "go Asia". heard_as must be copied exactly from the quote. Only clear sound-alike mishearings: not \
paraphrases, synonyms, or words the quotes simply don't contain. Most ideas have none."""

PICK_SYSTEM = """From these rare words in my notes, pick the ones a speech-to-text model would likely get wrong \
when I say them: names, foreign words, coinages, jargon and terms of art. Leave out ordinary English that is merely \
uncommon in print, such as long derived words (definitionally, abrasiveness, informality), which speech-to-text \
handles fine. Copy each picked word exactly. Pick few rather than many."""

PICK_SCHEMA = {
    "type": "object", "properties": {"words": {"type": "array", "items": {"type": "string"}}},
    "required": ["words"], "additionalProperties": False,
}

FIND_SCHEMA = {
    "type": "object",
    "properties": {"repairs": {"type": "array", "items": {
        "type": "object",
        "properties": {"idea": {"type": "integer"}, "term": {"type": "string"}, "heard_as": {"type": "string"},
                       "quote": {"type": "integer"}},
        "required": ["idea", "term", "heard_as", "quote"], "additionalProperties": False}}},
    "required": ["repairs"], "additionalProperties": False,
}


def finder_version() -> str:
    return themes.version(FIND_SYSTEM, FIND_SCHEMA, PICK_SYSTEM, PICK_SCHEMA, themes.MODEL, themes.EFFORT, RARE)


def rare_words(text: str) -> list[str]:
    """Rare words in text, in order, once each (any case), as written."""
    seen, out = set(), []
    for w in WORD.findall(text or ""):
        w = re.sub(r"['’]s$", "", w).strip("'’-")
        k = w.lower()
        if len(w) >= MIN_LEN and k not in seen and zipf_frequency(k, "en") < RARE:
            seen.add(k)
            out.append(w)
    return out


def _words(text: str) -> set[str]:
    return {w.lower() for w in WORD.findall(text or "")}


def _replace(conn, source: str, rows: list[tuple]) -> None:
    conn.execute("DELETE FROM im.vocab_candidates WHERE source = %s", (source,))
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO im.vocab_candidates (term_key, term, source, idea_id, example) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING""", [(t.lower(), t, source, i, ex) for t, i, ex in rows])


def corrections(conn) -> list[tuple]:
    """Rare words my corrections brought into an idea that Claude's version didn't have."""
    out = []
    for idea_id, title, statement, c_title, c_statement in conn.execute(
            """SELECT idea_id, title, statement, claude_title, claude_statement FROM pub.ideas
               WHERE corrected ORDER BY idea_id"""):
        before = _words(f"{c_title} {c_statement}")
        out += [(w, idea_id, f"{title}: {statement}"[:200]) for w in rare_words(f"{title} {statement}")
                if w.lower() not in before]
    return out


def archive(conn) -> list[tuple]:
    """The rarest words in my archive's ideas (title, statement, notes), each with the idea it's from: what Claude
    picks from."""
    found = {}
    for idea_id, title, statement, notes in conn.execute(
            """SELECT idea_id, title, statement, coalesce(notes, '') FROM pub.ideas WHERE origin = 'archive'
               ORDER BY archive_ref, idea_id"""):
        for w in rare_words(f"{title} {statement} {notes}"):
            found.setdefault(w.lower(), (w, idea_id, title[:200]))
    rarest = sorted(found.values(), key=lambda r: (zipf_frequency(r[0].lower(), "en"), r[0].lower()))
    return rarest[:ARCHIVE_MAX]


def _repair_todo(conn, fv: str) -> list[tuple]:
    """Captured ideas with a rare word that none of their quotes contains, not yet checked with this text."""
    rows = conn.execute(
        """SELECT d.idea_id, d.title, d.statement,
                  ARRAY(SELECT ev.item_id FROM pub.evidence ev WHERE ev.idea_id = d.idea_id
                          AND ev.verdict IS DISTINCT FROM 'discard' ORDER BY ev.said_at, ev.item_id LIMIT %s),
                  ARRAY(SELECT ev.quote FROM pub.evidence ev WHERE ev.idea_id = d.idea_id
                          AND ev.verdict IS DISTINCT FROM 'discard' ORDER BY ev.said_at, ev.item_id LIMIT %s),
                  c.input_hash, c.finder_version
           FROM pub.ideas d LEFT JOIN im.vocab_checks c USING (idea_id)
           WHERE d.origin = 'captured' ORDER BY d.created_at, d.idea_id""", (QUOTES, QUOTES)).fetchall()
    out = []
    for idea_id, title, statement, items, quotes, h, v in rows:
        digest = hashlib.sha256(json.dumps([title, statement, quotes]).encode()).hexdigest()[:16]
        if (h, v) == (digest, fv) or not quotes:
            continue
        said = _words(" ".join(quotes))
        if any(w.lower() not in said for w in rare_words(f"{title} {statement}")):
            out.append((idea_id, title, statement, items, quotes, digest))
        else:   # nothing to look for: record the check so it isn't redone
            out.append((idea_id, None, None, None, None, digest))
    return out


def repair_stage(conn, client, monthly_cap_usd: float = 20.0) -> dict:
    """Ask Claude which rare words in an idea its quotes misheard, for ideas where that's possible."""
    from im.extract import spent_this_month

    fv = finder_version()
    todo = _repair_todo(conn, fv)
    ask = [r for r in todo if r[1] is not None]
    stats = {"checked": len(todo), "asked": len(ask), "repairs": 0, "stopped": None}
    record = lambda rows: conn.cursor().executemany(  # noqa: E731
        """INSERT INTO im.vocab_checks (idea_id, input_hash, finder_version) VALUES (%s, %s, %s)
           ON CONFLICT (idea_id) DO UPDATE SET input_hash = excluded.input_hash,
             finder_version = excluded.finder_version, checked_at = now()""", [(r[0], r[5], fv) for r in rows])
    with conn.transaction():
        record([r for r in todo if r[1] is None])
    for start in range(0, len(ask), 25):
        if spent_this_month(conn) >= monthly_cap_usd:
            stats["stopped"] = f"monthly cap of ${monthly_cap_usd:.2f} reached"
            break
        batch = ask[start:start + 25]
        text = "\n\n".join(f"Idea {n}. {t}: {s}\n" + "\n".join(f"  Quote {k}: {q}" for k, q in enumerate(qs, 1))
                           for n, (_, t, s, _, qs, _) in enumerate(batch, 1))
        answer, r = themes._call(client, FIND_SYSTEM, FIND_SCHEMA, text)
        themes._log(conn, text, f"vocab-repair:{fv}", [b[0] for b in batch], r)
        if answer is None:
            stats["stopped"] = "refused"
            break
        with conn.transaction():
            for a in answer["repairs"]:
                if not 1 <= a["idea"] <= len(batch):
                    continue
                idea_id, title, statement, items, quotes, _ = batch[a["idea"] - 1]
                term, heard = a["term"].strip(), a["heard_as"].strip()
                k = a["quote"] - 1
                if not (0 <= k < len(quotes)) or not heard or heard.lower() not in quotes[k].lower() \
                        or term.lower() not in _words(f"{title} {statement}") or term.lower() in _words(quotes[k]) \
                        or term not in rare_words(term):
                    continue   # heard_as must be in the quote, and the term rare, in the idea, and not in the quote
                at = quotes[k].lower().index(heard.lower())
                example = quotes[k][max(0, at - 60):at + len(heard) + 60]
                conn.execute(
                    """INSERT INTO im.vocab_candidates (term_key, term, source, heard_as, idea_id, item_id, example)
                       VALUES (%s, %s, 'repair', %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    (term.lower(), term, quotes[k][at:at + len(heard)], idea_id, items[k], example))
                stats["repairs"] += 1
            record(batch)
    return stats


def pick_archive(conn, client, monthly_cap_usd: float = 20.0) -> dict:
    """Have Claude pick, from the archive's rare words, the ones speech-to-text would get wrong; once per list."""
    from im.extract import spent_this_month

    fv = finder_version()
    rows = archive(conn)
    words = sorted({w for w, _, _ in rows}, key=str.lower)
    h = hashlib.sha256(json.dumps([words, fv]).encode()).hexdigest()[:16]
    got = conn.execute("SELECT picked FROM im.vocab_picks WHERE words_hash = %s", (h,)).fetchone()
    stats = {"rare": len(words), "asked": got is None, "stopped": None}
    if got is None:
        if not words:
            got = ([],)
        elif spent_this_month(conn) >= monthly_cap_usd:
            stats["stopped"] = f"monthly cap of ${monthly_cap_usd:.2f} reached"
            return stats
        else:
            text = "Words:\n" + "\n".join(words)
            answer, r = themes._call(client, PICK_SYSTEM, PICK_SCHEMA, text)
            themes._log(conn, text, f"vocab-pick:{fv}", sorted({i for _, i, _ in rows}), r)
            if answer is None:
                stats["stopped"] = "refused"
                return stats
            got = ([w for w in answer["words"] if w in words],)
            conn.execute("INSERT INTO im.vocab_picks (words_hash, picked, finder_version) VALUES (%s, %s, %s)",
                         (h, got[0], fv))
    picked = {w.lower() for w in got[0]}
    with conn.transaction():
        _replace(conn, "archive", [r for r in rows if r[0].lower() in picked])
    stats["picked"] = len(picked)
    return stats


def refresh(conn, client=None, monthly_cap_usd: float = 20.0, path: str | None = None) -> dict:
    """Recompute corrections, and with a client the archive picks and repairs; then write the file."""
    with conn.transaction():
        c = corrections(conn)
        _replace(conn, "correction", c)
    stats = {"corrections": len(c)}
    if client is not None:
        stats["archive"] = pick_archive(conn, client, monthly_cap_usd)
        stats["repair"] = repair_stage(conn, client, monthly_cap_usd)
    path = path or os.environ.get("IM_VOCAB_PATH")
    if path:
        stats["written"] = write(conn, Path(path))
    stats["in"] = conn.execute("SELECT count(*) FROM pub.vocab WHERE status = 'in'").fetchone()[0]
    return stats


def approved(conn) -> dict:
    """Hearsay's format: {"format_version": 1, "terms": [{"term", "source", "heard_as"?}]}, sorted by term."""
    terms = [{"term": t, "source": s, **({"heard_as": h} if s == "repair" and h else {})}
             for t, s, h in conn.execute("""SELECT term, source, heard_as FROM pub.vocab WHERE status = 'in'
                                            ORDER BY lower(term), term""")]
    return {"format_version": 1, "terms": terms}


def write(conn, path: Path) -> bool:
    """Write the approved list atomically (a temp file in the same folder, then rename), only if it changed."""
    text = json.dumps(approved(conn), ensure_ascii=False, indent=1) + "\n"
    if path.exists() and path.read_text() == text:
        return False
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o644)   # Hearsay reads it as another user
    os.replace(tmp, path)
    return True
