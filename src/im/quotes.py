"""Each capture's quote, checked against the transcript lines it cites (ROADMAP-v2 §5, question 10).

A model's citation can stop a line short of its quote, or point a line off: 2 of 20 live captures sampled
(2026-11-06) quoted words from a line they didn't cite, so forgetting that line would have left the capture, and
everything made from it, holding them. Each capture's quote is lined up with its cited lines and the line either
side, in the episode's order, and the capture cites every line its words are on. Citations only grow, so forgetting
reaches more, never less.

The share of the quote's words found there, in order, is kept as `quote_in_lines`. Under FLAG the quote isn't from
those lines (made up, or cited further off than a line), and the capture is one to read again. A tidied quote (v1-v3
dropped fillers and fixed mistranscriptions) still scores high: its words are there, with others between them.

A quote can begin with the transcript's own line label ("[23] me: "), copied by the model; that goes first. A quote
stitched from pieces ("...", " / ") is lined up piece by piece.

Every place forgetting finds a capture's speech by segment gets the added lines too: the capture's observation, the
idea it made, my verdicts on it, Lattice's events on it, and the matcher's request that sent it.
"""

import difflib
import re

LABEL = re.compile(r"^\s*\[\d+\]\s*[^:\]\n]{1,40}:\s*")
STITCH = re.compile(r"\s*(?:\.\.\.|…|\s/\s)\s*")
MIN_RUN = 2    # words in a row that put a quote on a line: one shared word ("the", "so") doesn't
FLAG = 0.6     # under this share of its words found, a quote isn't from its lines


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def place(quote: str, window: list[tuple[str, str]], cited: list[str]) -> tuple[str, list[str], float]:
    """The quote without a leaked line label; the lines it's on, from `window` ([(segment id, text)] in the
    episode's order) and always every one of `cited`, in window order; and the share of its words found in order."""
    quote = LABEL.sub("", quote, count=1)
    words, line = [], []
    for sid, text in window:
        w = _words(text)
        words += w
        line += [sid] * len(w)
    on, found, total = set(), 0, 0
    for piece in STITCH.split(quote):
        q = _words(piece)
        if not q:
            continue
        total += len(q)
        runs = difflib.SequenceMatcher(None, q, words, autojunk=False).get_matching_blocks()
        found += sum(r.size for r in runs)
        on |= {line[r.b + k] for r in runs if r.size >= min(MIN_RUN, len(q)) for k in range(r.size)}
    order = [sid for sid, _ in window]
    segs = [sid for sid in order if sid in on or sid in cited] + [s for s in cited if s not in order]
    return quote, segs, round(found / total, 3) if total else 1.0


def windows(conn, episode_ids) -> dict[str, list[tuple[str, str]]]:
    """Each episode's lines, [(segment id, text)] in order."""
    out: dict = {}
    for eid, sid, text in conn.execute(
            """SELECT es.episode_id::text, es.segment_id::text, s.text FROM im.episode_segments es
               JOIN im.source_segments s USING (segment_id) WHERE es.episode_id = ANY(%s::uuid[])
               ORDER BY es.episode_id, es.ord""", ([str(e) for e in episode_ids],)):
        out.setdefault(eid, []).append((sid, text))
    return out


def near(lines: list[tuple[str, str]], cited: list[str]) -> list[tuple[str, str]]:
    """The cited lines, everything between them, and one line either side."""
    cited_ = set(cited)
    at = [n for n, (sid, _) in enumerate(lines) if sid in cited_]
    return lines[max(min(at) - 1, 0):max(at) + 2] if at else []


def place_stage(conn) -> dict:
    """Every capture not checked yet, placed: its quote unlabelled, its citation grown to the lines its words are
    on (and everything that holds its segments with it), and quote_in_lines kept."""
    rows = conn.execute("""SELECT item_id::text, episode_id::text, quote, source_segment_ids::text[] FROM im.items
                           WHERE quote_in_lines IS NULL ORDER BY created_at, item_id""").fetchall()
    stats = {"checked": len(rows), "unlabelled": 0, "cited_more": 0, "flagged": 0}
    lines = windows(conn, {eid for _, eid, _, _ in rows})
    with conn.transaction():
        for item_id, eid, quote, cited in rows:
            text, segs, share = place(quote, near(lines.get(eid, []), cited), cited)
            added = [s for s in segs if s not in cited]
            stats["unlabelled"] += text != quote
            stats["cited_more"] += bool(added)
            stats["flagged"] += share < FLAG
            conn.execute("""UPDATE im.items SET quote = %s, source_segment_ids = %s::uuid[], quote_in_lines = %s
                            WHERE item_id = %s""", (text, segs, share, item_id))
            if added:
                _cite_everywhere(conn, item_id, added)
    return stats


def _cite_everywhere(conn, item_id: str, added: list[str]) -> None:
    """The added lines, on everything forgetting finds by this capture's segments."""
    grow = "ARRAY(SELECT DISTINCT x FROM unnest({col} || %(added)s::uuid[]) x)"
    args = {"item": item_id, "added": added}
    conn.execute(f"""UPDATE im.observations o SET segment_ids = {grow.format(col='o.segment_ids')}
                     WHERE o.observation_id = (SELECT observation_id FROM im.items WHERE item_id = %(item)s)""", args)
    conn.execute(f"""UPDATE im.ideas d SET segment_ids = {grow.format(col='d.segment_ids')}
                     WHERE d.origin = 'captured' AND d.idea_id IN (
                       SELECT idea_id FROM im.idea_evidence WHERE item_id = %(item)s AND relation = 'origin')""", args)
    conn.execute(f"""UPDATE im.item_verdicts v SET segment_ids = {grow.format(col='v.segment_ids')}
                     WHERE v.item_id = %(item)s""", args)
    conn.execute(f"""UPDATE pub.feedback_events f SET item_segment_ids = {grow.format(col='f.item_segment_ids')}
                     WHERE f.item_id = %(item)s""", args)
    conn.execute(f"""UPDATE im.egress_log g SET segment_ids = {grow.format(col='g.segment_ids')}
                     WHERE g.egress_id IN (SELECT egress_id FROM im.item_matches
                                           WHERE item_id = %(item)s AND egress_id IS NOT NULL)""", args)
