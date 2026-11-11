"""Mishearings (decision 0012): words the transcription got wrong, and what was meant, as hints for later reads.

Extraction v5 reports each transcription fix its reading makes; those pairs are provisional (im.mishearings). I
confirm or reject them, or add my own, confirmed (`im mishearings`). A read's context line lists the confirmed and
provisional pairs whose heard words occur in its transcript, and v5 applies a confirmed one wherever it fits and
treats a provisional one as a likely fix. They're hints for what's read next: the line isn't part of what decides
whether an episode was already read (egress.Payload.sha256), so a new pair re-reads nothing.
"""

import re

STATUSES = ("confirmed", "provisional", "rejected")


def pairs(conn) -> list[tuple[str, str, str, int]]:
    """Every pair, (heard, meant, status, captures that proposed it): undecided first, the most proposed first."""
    return conn.execute(
        """SELECT heard, meant, status, seen FROM im.mishearings
           ORDER BY status <> 'provisional', seen DESC, lower(heard), lower(meant)""").fetchall()


def decide(conn, heard: str, meant: str, status: str) -> None:
    """Confirm or reject a pair; a pair nobody proposed is added as I decide it."""
    if status not in ("confirmed", "rejected"):
        raise ValueError(f"a decision is confirmed or rejected, not {status}")
    heard, meant = heard.strip(), meant.strip()
    if not heard or not meant:
        raise ValueError("a mishearing needs what was heard and what was meant")
    conn.execute(
        """INSERT INTO im.mishearing_decisions (heard, meant, status) VALUES (%s, %s, %s)
           ON CONFLICT (lower(heard), lower(meant)) DO UPDATE SET status = excluded.status, decided_at = now()""",
        (heard, meant, status))


def _occurs(heard: str, texts: list[str]) -> bool:
    pattern = re.compile(rf"(?<!\w){re.escape(heard)}(?!\w)", re.I)
    return any(pattern.search(t) for t in texts)


def hints(conn, texts: list[str]) -> list[tuple[str, str, str]]:
    """The confirmed and provisional pairs whose heard words occur in these texts (case-insensitively, as whole
    words): (heard, meant, status), confirmed first."""
    return [(h, m, s) for h, m, s, _ in conn.execute(
        """SELECT heard, meant, status, seen FROM im.mishearings WHERE status <> 'rejected'
           ORDER BY status <> 'confirmed', seen DESC, lower(heard), lower(meant)""") if _occurs(h, texts)]


def line(found: list[tuple[str, str, str]]) -> str:
    """The context line's sentence for these pairs, or "" for none."""
    if not found:
        return ""
    return "Known mishearings: " + "; ".join(f'"{h}" for "{m}" ({s})' for h, m, s in found) + "."

