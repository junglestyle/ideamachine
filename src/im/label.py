"""`im label`: my answers to the triage questions, per episode, in the terminal.

Labels are anchored to segment IDs (ROADMAP §2). A label resolves to current
episodes by following im.source_supersessions forward from its segments. It's
*exact* when it lands on exactly one current episode with the same segments.
Only exact labels count as labeling that episode; the others are kept, and
the episode comes up again. When an episode has several exact labels (I
re-labeled it), the latest is its label and the older ones are history.
"""

import json
from collections import Counter

from im import projects
from im.show import show

QUESTIONS_VERSION = "triage-q1"
BOUNDARIES = {"o": "ok", "s": "split", "m": "merge"}
KINDS = {"i": "idea", "t": "task", "d": "decision", "c": "chatter", "n": "noise"}
YES_NO = {"y": True, "n": False}
TARGET = 50
QUESTION_FIELDS = ("boundaries", "is_self_thinking", "kind", "project", "keep_score")


def validate(answers: dict, project_slugs: set[str]) -> None:
    expected = {"boundaries", "is_self_thinking", "kind", "project", "keep_score"}
    if set(answers) != expected:
        raise ValueError(f"answers must be exactly {sorted(expected)}")
    if answers["boundaries"] not in BOUNDARIES.values():
        raise ValueError(f"boundaries: {answers['boundaries']!r}")
    if not isinstance(answers["is_self_thinking"], bool):
        raise ValueError("is_self_thinking must be a bool")
    if answers["kind"] not in KINDS.values():
        raise ValueError(f"kind: {answers['kind']!r}")
    if answers["project"] is not None and answers["project"] not in project_slugs:
        raise ValueError(f"project: {answers['project']!r} isn't in the registry")
    if answers["keep_score"] not in range(1, 6):
        raise ValueError(f"keep_score: {answers['keep_score']!r}")


def save(conn, episode_id, answers: dict, note: str | None, source: str = "label") -> int:
    validate(answers, {p[0] for p in projects.active(conn)})
    with conn.transaction():
        row = conn.execute(
            """SELECT array_agg(es.segment_id ORDER BY es.ord), e.stage_version
               FROM im.episodes e JOIN im.episode_segments es USING (episode_id)
               WHERE e.episode_id = %s AND e.current GROUP BY e.stage_version""", (episode_id,)).fetchone()
        if row is None:
            raise ValueError(f"{episode_id} isn't a current episode")
        return conn.execute(
            """INSERT INTO im.labels (segment_ids, questions_version, answers, note, episode_id,
                 episode_stage_version, source) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING label_id""",
            (row[0], QUESTIONS_VERSION, json.dumps(answers), note or None, episode_id, row[1], source)).fetchone()[0]


def resolve_all(conn) -> dict[int, tuple[str, set]]:
    """label_id -> (exact | partial | unresolved, current episode IDs it lands on)."""
    resolved: dict[int, set] = {r[0]: set() for r in conn.execute("SELECT label_id FROM im.labels")}
    for label_id, seg in conn.execute(
            """WITH RECURSIVE fwd(label_id, seg) AS (
                 SELECT label_id, unnest(segment_ids) FROM im.labels
                 UNION
                 SELECT f.label_id, x.new_segment_id
                 FROM fwd f JOIN im.source_supersessions x ON x.old_segment_id = f.seg)
               SELECT f.label_id, f.seg FROM fwd f JOIN im.current_segments c ON c.segment_id = f.seg"""):
        resolved[label_id].add(seg)
    members: dict = {}
    episode_of: dict = {}
    for eid, seg in conn.execute(
            """SELECT es.episode_id, es.segment_id FROM im.episode_segments es
               JOIN im.episodes e USING (episode_id) WHERE e.current"""):
        members.setdefault(eid, set()).add(seg)
        episode_of[seg] = eid
    out = {}
    for label_id, segs in resolved.items():
        eps = {episode_of[s] for s in segs if s in episode_of}
        if not eps:
            out[label_id] = ("unresolved", eps)
        elif len(eps) == 1 and members[next(iter(eps))] == segs:
            out[label_id] = ("exact", eps)
        else:
            out[label_id] = ("partial", eps)
    return out


def current_labels(conn) -> dict:
    """episode_id -> (label_id, answers) of its latest exact label."""
    res = resolve_all(conn)
    out = {}
    for lid, answers in conn.execute("SELECT label_id, answers FROM im.labels ORDER BY label_id"):
        status, eps = res[lid]
        if status == "exact":
            out[next(iter(eps))] = (lid, answers)  # later labels overwrite earlier ones
    return out


def labeled_episodes(conn) -> set:
    return set(current_labels(conn))


def next_episode(conn, skip=frozenset()):
    """An unlabeled current episode, from the episode kind with the fewest labels so far,
    so chatter and noise get labeled as well as ideas."""
    done = labeled_episodes(conn)
    rows = conn.execute("SELECT episode_id, kind FROM im.episodes WHERE current "
                        "ORDER BY md5(episode_id::text)").fetchall()
    labeled_by_kind = Counter(kind for eid, kind in rows if eid in done)
    todo = [(labeled_by_kind[kind], i, eid) for i, (eid, kind) in enumerate(rows)
            if eid not in done and eid not in skip]
    return min(todo)[2] if todo else None


def _choose(read, write, prompt: str, options: dict):
    while True:
        got = read(prompt).strip().lower()
        if got in options:
            return options[got]
        write(f"  one of: {', '.join(options)}")


def _project(conn, read, write) -> str | None:
    active = projects.active(conn)
    for i, (slug, aliases, description) in enumerate(active, 1):
        write(f"  {i:>2}  {slug:<18} {description}")
    write("   0  none            · +slug to add a project")
    while True:
        got = read("project > ").strip()
        if got in ("0", ""):
            return None
        if got.isdigit() and 1 <= int(got) <= len(active):
            return active[int(got) - 1][0]
        if got.startswith("+") and len(got) > 1:
            slug = got[1:].lower()
            try:
                projects.add(conn, slug, read(f"description of {slug} > ").strip(), [])
            except Exception as e:  # bad slug or empty description: ask again
                write(f"  couldn't add {slug}: {e}")
                continue
            return slug
        write("  a number, 0, or +slug")


def ask(conn, read, write) -> dict:
    while True:
        answers = {
            "boundaries": _choose(read, write, "boundaries [o]k / should [s]plit / should [m]erge > ", BOUNDARIES),
            "is_self_thinking": _choose(read, write, "me thinking (y/n) > ", YES_NO),
            "kind": _choose(read, write, "kind [i]dea [t]ask [d]ecision [c]hatter [n]oise > ", KINDS),
            "project": _project(conn, read, write),
            "keep_score": _choose(read, write, "keep 1-5 > ", {str(i): i for i in range(1, 6)}),
        }
        write("  " + json.dumps(answers))
        if _choose(read, write, "save? [y]es / [r]edo > ", {"y": True, "": True, "r": False}):
            return answers


def status(conn) -> str:
    res = resolve_all(conn)
    latest = current_labels(conn)
    by_status = Counter(st for st, _ in res.values())
    kinds = Counter(r[0] for r in conn.execute(
        "SELECT e.kind FROM im.episodes e WHERE e.current AND e.episode_id = ANY(%s)", (list(latest),)))
    answers = [a for _, a in latest.values()]
    lines = [f"labeled episodes: {len(latest)} / {TARGET}",
             "labels: " + ", ".join(f"{n} {st}" for st, n in sorted(by_status.items())) if res else "labels: none",
             "by episode kind: " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items()))]
    if answers:
        ok = sum(a["boundaries"] == "ok" for a in answers)
        lines.append(f"boundaries ok: {ok}/{len(answers)} ({100 * ok / len(answers):.0f}%, exit needs ≥ 80% of 50)")
        lines.append("by answer kind: " + ", ".join(f"{k} {n}" for k, n in sorted(Counter(a['kind'] for a in answers).items())))
    return "\n".join(lines)


def review_queue(conn, field: str, value: str) -> list:
    """Labeled episodes whose latest label has answers[field] == value (`none` matches no project)."""
    want = None if (field == "project" and value == "none") else value
    out = []
    for eid, (_, answers) in current_labels(conn).items():
        got = answers.get(field)
        if str(got).lower() == str(want).lower() if want is not None else got is None:
            out.append(eid)
    return out


def _loop(conn, read, write, items, source: str, empty: str) -> int:
    """Show each (episode_id, header lines) and record my answers. Returns how many were saved."""
    saved = 0
    for eid, header in items:
        write("\n" + "─" * 72)
        for line in header:
            write(line)
        write(show(conn, str(eid)))
        cmd = _choose(read, write, "\n[enter] label · [s]kip · [q]uit > ", {"": "label", "s": "skip", "q": "quit"})
        if cmd == "quit":
            return saved
        if cmd == "skip":
            continue
        save(conn, eid, ask(conn, read, write), None, source)
        saved += 1
    write(empty)
    return saved


def session(conn, read=input, write=print, review: tuple[str, str] | None = None) -> int:
    """Label episodes until I quit or none are left. Returns how many were saved.
    With `review=(field, value)`, re-label the episodes whose latest label has that answer instead."""
    if review:
        queue = review_queue(conn, *review)
        latest = current_labels(conn)
        items = ((eid, [f"reviewing {review[0]}={review[1]}: {len(queue) - i - 1} more after this",
                        f"previous label: {json.dumps(latest[eid][1])}"]) for i, eid in enumerate(queue))
        return _loop(conn, read, write, items, "label", "nothing left to review")

    def fresh():
        seen: set = set()
        while (eid := next_episode(conn, seen)) is not None:
            seen.add(eid)  # labeled or skipped, either way not again this session
            yield eid, [f"{len(labeled_episodes(conn))} / {TARGET} labeled"]
    return _loop(conn, read, write, fresh(), "label", "nothing left to label")


WHY = {"tap": "you tapped the pendant", "note_to_self": "you said \"note to self\""}


def routed_session(conn, read=input, write=print) -> int:
    """`im review`: work through what the router sent me. Each answer is a label (source `review`)."""
    from im.router import review_queue as routed

    queue = routed(conn)
    items = ((eid, [f"review: {len(queue) - i - 1} more after this",
                    "here because " + "; ".join(WHY.get(r, f"the LLM says {r.removeprefix('llm:')}") for r in reasons)])
             for i, (eid, reasons) in enumerate(queue))
    return _loop(conn, read, write, items, "review", "review queue is empty")
