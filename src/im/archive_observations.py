"""What I said into the ChatGPT Idea Machine, as observations of the archive ideas it became (migration 036).

The archive (`im seed`) is ChatGPT's summary: its quotes are close to mine but cleaned up, and it has no times.
The conversation's export has every message I sent, in my words, with when I sent it. ChatGPT answered each idea
under a heading with its number ("**Idea #14, refined:**", "Updated **Idea #17**"), and the archive keeps those
numbers (archive_ref chatgpt#14), so each message goes to its idea by that number, not by guessing from the words.
A message with no numbered answer (an instruction to ChatGPT, say) isn't an idea and is left out.

Nothing is written until `write`: `plan` shows every message, the idea it goes to, and how much of its wording the
archive's text shares (`quote_score`), so a wrong number shows up as a low score.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from im import citations
from im.feedback import quote_score

_TURN = re.compile(r"^## (Prompt|Response):\n(.+?)\n", re.M)
_WHEN = re.compile(r"(\d{1,2}/\d{1,2}/\d{4}, \d{1,2}:\d{2}:\d{2} [AP]M)")
_LINK = re.compile(r"\*\*Link:\*\* \[https://chatgpt\.com/c/([0-9a-f-]+)\]")
# ChatGPT's heading for the idea a message was: "**Idea #3:**", "**Idea #14, refined:**", "correction to **Idea
# #14**", "Updated **Idea #17**". A passing mention ("connects to **Idea #2**") isn't one.
_HEADING = re.compile(r"\*\*Idea #(\d+)(?:, refined)?:\*\*|(?:correction to|Updated) \*\*Idea #(\d+)\*\*")


def parse(text: str, tz: str = "America/New_York") -> tuple[str, list[dict]]:
    """The conversation's id, and my messages: {"at", "text", "ideas": the numbers ChatGPT's answer gave them}."""
    link = _LINK.search(text)
    if not link:
        raise ValueError("not a ChatGPT export: no **Link:** to a chatgpt.com conversation")
    zone = ZoneInfo(tz)
    turns, marks = [], list(_TURN.finditer(text))
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
        body = text[m.end():end].split("\n---\nPowered by", 1)[0].strip()
        when = _WHEN.search(m.group(2))
        if not when:
            raise ValueError(f"no time on {m.group(1)} {k + 1}: {m.group(2)!r}")
        at = datetime.strptime(when.group(1), "%m/%d/%Y, %I:%M:%S %p").replace(tzinfo=zone)
        turns.append((m.group(1), at, body))
    messages = []
    for k, (role, at, body) in enumerate(turns):
        if role != "Prompt":
            continue
        answer = turns[k + 1][2] if k + 1 < len(turns) and turns[k + 1][0] == "Response" else ""
        ideas = sorted({int(a or b) for a, b in _HEADING.findall(answer)})
        messages.append({"at": at, "text": body, "ideas": ideas})
    return link.group(1), messages


def plan(conn, text: str, name: str = "chatgpt", not_me: set[int] = frozenset(), tz: str = "America/New_York"):
    """What `write` would add, for me to look over first."""
    conversation, messages = parse(text, tz)
    ideas = {int(ref.split("#")[1]): (idea_id, statement + "\n" + (notes or "")) for idea_id, ref, statement, notes
             in conn.execute("""SELECT idea_id, archive_ref, statement, notes FROM im.ideas
                                WHERE origin = 'archive' AND archive_ref LIKE %s""", (f"{name}#%",))}
    rows, skipped, unknown = [], [], []
    for msg in messages:
        if not msg["ideas"]:
            skipped.append(f"{msg['at']:%Y-%m-%d %H:%M}: {msg['text'][:70]}")
        for n in msg["ideas"]:
            if n not in ideas:
                unknown.append(n)
                continue
            rows.append({"idea": n, "idea_id": ideas[n][0], "said_at": msg["at"], "quote": msg["text"],
                         "said_by": "not_me" if n in not_me else "me",
                         "matches_archive": round(quote_score(msg["text"], ideas[n][1]), 2),
                         "source_ref": f"{name}:{conversation}:{msg['at'].isoformat()}:{n}",
                         "conversation": f"{name}:{conversation}"})
    found = {r["idea"] for r in rows}
    return {"conversation": conversation, "messages": len(messages), "observations": rows,
            "not_ideas": skipped, "numbers_not_in_archive": sorted(set(unknown)),
            "archive_ideas_without_a_message": sorted(set(ideas) - found)}


def write(conn, p: dict) -> dict:
    with conn.transaction():
        added = sum(conn.execute(
            """INSERT INTO im.observations (segment_ids, first_seen, source, idea_id, quote, said_at, said_by,
                                            conversation, source_ref)
               VALUES ('{}', %s, 'archive', %s, %s, %s, %s, %s, %s) ON CONFLICT (source_ref) DO NOTHING""",
            (r["said_at"], r["idea_id"], r["quote"], r["said_at"], r["said_by"], r["conversation"],
             r["source_ref"])).rowcount for r in p["observations"])
        cited = citations.cite(conn)
    return {"added": added, "already_there": len(p["observations"]) - added, "cited_links": cited}


def report(p: dict) -> dict:
    """The plan as I read it: one line per observation, lowest match first."""
    lines = [f"#{r['idea']:<3} {r['said_at']:%Y-%m-%d %H:%M}  match {r['matches_archive']:.2f}  "
             f"{'NOT ME  ' if r['said_by'] == 'not_me' else ''}{r['quote'][:80]}"
             for r in sorted(p["observations"], key=lambda r: (r["matches_archive"], r["idea"]))]
    return {k: v for k, v in p.items() if k != "observations"} | {"observations": lines}
