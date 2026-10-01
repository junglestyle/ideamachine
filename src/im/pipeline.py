"""`im run`: ingest from hearsay.current_segments, then (re)segment affected sessions.

Reads hearsay.* only. Everything is one REPEATABLE READ transaction, so a run
sees one consistent snapshot and the cursor advances only if the run commits.
"""

import json

import psycopg
from psycopg.rows import class_row

from im.config import Config
from im.segment import Seg, segment

SCHEMA_VERSION = 1
CURSOR = "hearsay.segments"
SEG_COLS = "segment_id, session_id, source, started_at, ended_at, speaker_label, is_self, speaker_conf"


def _start_run(conn, command: str) -> int:
    return conn.execute("INSERT INTO im.runs (command) VALUES (%s) RETURNING run_id", (command,)).fetchone()[0]


def _finish_run(conn, run_id: int, stats: dict) -> None:
    conn.execute("UPDATE im.runs SET finished_at = now(), stats = %s WHERE run_id = %s",
                 (json.dumps(stats), run_id))


def _ingest(conn, cfg: Config) -> tuple[set[str], dict]:
    """Sessions with new (or recently committed) current segments, via the seq cursor
    plus a trailing re-read window (ROADMAP §3.1)."""
    row = conn.execute("SELECT last_seq FROM im.ingest_state WHERE name = %s", (CURSOR,)).fetchone()
    cursor = row[0] if row else 0
    sessions = {r[0] for r in conn.execute(
        """SELECT DISTINCT session_id FROM hearsay.current_segments
           WHERE seq > %s OR created_at > now() - make_interval(secs => %s)""",
        (cursor, cfg.ingest_window_s))}
    top = conn.execute("SELECT coalesce(max(seq), 0) FROM hearsay.segments").fetchone()[0]
    if top > cursor:
        conn.execute(
            """INSERT INTO im.ingest_state (name, last_seq) VALUES (%s, %s)
               ON CONFLICT (name) DO UPDATE SET last_seq = excluded.last_seq, updated_at = now()""",
            (CURSOR, top))
    return sessions, {"cursor_from": cursor, "cursor_to": max(top, cursor)}


def _purge_tombstoned(conn) -> set[str]:
    """Delete every episode, current or retired, that contains a tombstoned segment.
    Tombstones come from Hearsay's forgotten list (ROADMAP §3.4)."""
    rows = conn.execute(
        """DELETE FROM im.episodes e
           WHERE EXISTS (SELECT 1 FROM im.episode_segments es
                         JOIN hearsay.tombstones t USING (segment_id)
                         WHERE es.episode_id = e.episode_id)
           RETURNING session_id""").fetchall()
    return {r[0] for r in rows}


def _sessions_with_stale_episodes(conn, stage_version: str) -> set[str]:
    """Current episodes holding a segment that is no longer current (superseded),
    or built by a different heuristic version."""
    rows = conn.execute(
        """SELECT DISTINCT e.session_id FROM im.episodes e
           WHERE e.current AND (
             e.stage_version <> %s OR EXISTS (
               SELECT 1 FROM im.episode_segments es
               WHERE es.episode_id = e.episode_id
                 AND NOT EXISTS (SELECT 1 FROM hearsay.current_segments c
                                 WHERE c.segment_id = es.segment_id)))""",
        (stage_version,)).fetchall()
    return {r[0] for r in rows}


def _resegment(conn, cfg: Config, sessions: set[str]) -> dict:
    scfg = cfg.segment
    by_session: dict[str, list[Seg]] = {s: [] for s in sessions}
    with conn.cursor(row_factory=class_row(Seg)) as cur:
        cur.execute(f"SELECT {SEG_COLS} FROM hearsay.current_segments WHERE session_id = ANY(%s)",
                    (list(sessions),))
        for s in cur:
            by_session[s.session_id].append(s)

    created = retired = 0
    for session_id in sorted(by_session):
        desired = {e.episode_id: e for e in segment(by_session[session_id], scfg)}
        existing = {r[0] for r in conn.execute(
            "SELECT episode_id FROM im.episodes WHERE session_id = %s AND current", (session_id,))}
        gone = existing - desired.keys()
        if gone:
            conn.execute("UPDATE im.episodes SET current = false, retired_at = now() WHERE episode_id = ANY(%s)",
                         (list(gone),))
            retired += len(gone)
        for eid in desired.keys() - existing:
            e = desired[eid]
            segs = e.segments
            conn.execute(
                """INSERT INTO im.episodes (episode_id, session_id, source, started_at, ended_at, kind,
                     n_segments, n_speakers, self_segments, schema_version, stage_version, input_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (episode_id) DO UPDATE SET current = true, retired_at = NULL""",
                (eid, session_id, segs[0].source, min(s.started_at for s in segs),
                 max(s.ended_at for s in segs), e.kind, len(segs),
                 len({s.speaker_label for s in segs}), e.self_segments,
                 SCHEMA_VERSION, scfg.stage_version, e.input_hash))
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO im.episode_segments (episode_id, segment_id, ord) VALUES (%s, %s, %s)
                       ON CONFLICT DO NOTHING""",
                    [(eid, s.segment_id, i) for i, s in enumerate(segs)])
            created += 1
    return {"sessions": len(by_session), "episodes_created": created, "episodes_retired": retired}


def run(conn: psycopg.Connection, cfg: Config) -> dict:
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('im.run'))")
        run_id = _start_run(conn, "run")
        sessions, stats = _ingest(conn, cfg)
        purged = _purge_tombstoned(conn)
        sessions |= purged
        sessions |= _sessions_with_stale_episodes(conn, cfg.segment.stage_version)
        stats |= {"stage_version": cfg.segment.stage_version, "sessions_with_purges": len(purged)}
        stats |= _resegment(conn, cfg, sessions)
        _finish_run(conn, run_id, stats)
    return stats


STAGES = ("segment",)


def reset(conn: psycopg.Connection, stage: str) -> dict:
    """Drop a stage's derived rows. Rewinding the cursor makes the next run re-derive everything."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; known: {', '.join(STAGES)}")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('im.run'))")
        run_id = _start_run(conn, f"reset --stage {stage}")
        n = conn.execute("DELETE FROM im.episodes").rowcount
        conn.execute("DELETE FROM im.ingest_state WHERE name = %s", (CURSOR,))
        stats = {"episodes_deleted": n}
        _finish_run(conn, run_id, stats)
    return stats
