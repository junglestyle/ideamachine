"""`im label`: my answers to the triage questions, per episode, in the terminal.

Labels are anchored to segment IDs (ROADMAP §2). A label resolves to current
episodes by following im.source_supersessions forward from its segments. It's
*exact* when it lands on exactly one current episode with the same segments.
Only exact labels count as labeling that episode; the others are kept, and
the episode comes up again.
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


def save(conn, episode_id, answers: dict, note: str | None) -> int:
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
                 episode_stage_version) VALUES (%s, %s, %s, %s, %s, %s) RETURNING label_id""",
            (row[0], QUESTIONS_VERSION, json.dumps(answers), note or None, episode_id, row[1])).fetchone()[0]


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


def labeled_episodes(conn) -> set:
    return {next(iter(eps)) for status, eps in resolve_all(conn).values() if status == "exact"}


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


def ask(conn, read, write) -> tuple[dict, str | None]:
    while True:
        answers = {
            "boundaries": _choose(read, write, "boundaries [o]k / should [s]plit / should [m]erge > ", BOUNDARIES),
            "is_self_thinking": _choose(read, write, "me thinking (y/n) > ", YES_NO),
            "kind": _choose(read, write, "kind [i]dea [t]ask [d]ecision [c]hatter [n]oise > ", KINDS),
            "project": _project(conn, read, write),
            "keep_score": _choose(read, write, "keep 1-5 > ", {str(i): i for i in range(1, 6)}),
        }
        note = read("note (enter for none) > ").strip() or None
        write("  " + json.dumps(answers) + (f"  note: {note}" if note else ""))
        if _choose(read, write, "save? [y]es / [r]edo > ", {"y": True, "": True, "r": False}):
            return answers, note


def status(conn) -> str:
    res = resolve_all(conn)
    exact = {lid for lid, (st, _) in res.items() if st == "exact"}
    by_status = Counter(st for st, _ in res.values())
    kinds = Counter(r[0] for r in conn.execute(
        "SELECT e.kind FROM im.episodes e WHERE e.current AND e.episode_id = ANY(%s)",
        ([next(iter(res[lid][1])) for lid in exact],)))
    answers = [r[0] for r in conn.execute("SELECT answers FROM im.labels WHERE label_id = ANY(%s)", (list(exact),))]
    lines = [f"labeled episodes: {len(exact)} / {TARGET}",
             "labels: " + ", ".join(f"{n} {st}" for st, n in sorted(by_status.items())) if res else "labels: none",
             "by episode kind: " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items()))]
    if answers:
        ok = sum(a["boundaries"] == "ok" for a in answers)
        lines.append(f"boundaries ok: {ok}/{len(answers)} ({100 * ok / len(answers):.0f}%, exit needs ≥ 80% of 50)")
        lines.append("by answer kind: " + ", ".join(f"{k} {n}" for k, n in sorted(Counter(a['kind'] for a in answers).items())))
    return "\n".join(lines)


def session(conn, read=input, write=print) -> int:
    """Label episodes until I quit or none are left. Returns how many were saved."""
    skipped, saved = set(), 0
    while (eid := next_episode(conn, skipped)) is not None:
        write("\n" + "─" * 72)
        write(f"{len(labeled_episodes(conn))} / {TARGET} labeled")
        write(show(conn, str(eid)))
        cmd = _choose(read, write, "\n[enter] label · [s]kip · [q]uit > ", {"": "label", "s": "skip", "q": "quit"})
        if cmd == "quit":
            return saved
        if cmd == "skip":
            skipped.add(eid)
            continue
        answers, note = ask(conn, read, write)
        save(conn, eid, answers, note)
        saved += 1
    write("nothing left to label")
    return saved
