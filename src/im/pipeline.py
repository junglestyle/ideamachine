"""`im run`: import Hearsay's stream, then (re)segment the affected conversations.

Everything is one REPEATABLE READ transaction, so a run sees one consistent
snapshot of im.* and leaves nothing half-done if it fails.
"""

import json
from pathlib import Path

import psycopg
from psycopg.rows import class_row

from im.config import Config
from im.importer import import_stream
from im.segment import Seg, segment

SCHEMA_VERSION = 1
SEG_SQL = """SELECT segment_id, conversation_id AS session_id, 'hearsay' AS source, started_at, ended_at,
                    speaker_label, is_self, speaker_conf
             FROM im.current_segments WHERE conversation_id = ANY(%s)"""


def _start_run(conn, command: str) -> int:
    return conn.execute("INSERT INTO im.runs (command) VALUES (%s) RETURNING run_id", (command,)).fetchone()[0]


def _finish_run(conn, run_id: int, stats: dict) -> None:
    conn.execute("UPDATE im.runs SET finished_at = now(), stats = %s WHERE run_id = %s",
                 (json.dumps(stats), run_id))


def _purge_tombstoned(conn) -> set[str]:
    """Delete every episode, current or retired, that contains a forgotten segment (ROADMAP §3.4)."""
    rows = conn.execute(
        """DELETE FROM im.episodes e
           WHERE EXISTS (SELECT 1 FROM im.episode_segments es
                         JOIN im.source_tombstones t USING (segment_id)
                         WHERE es.episode_id = e.episode_id)
           RETURNING session_id""").fetchall()
    return {r[0] for r in rows}


def _sessions_needing_work(conn, stage_version: str) -> set[str]:
    """Conversations whose current episodes are out of date (a segment left, or another heuristic
    version built them), or that have current segments in no current episode (e.g. after a reset)."""
    rows = conn.execute(
        """SELECT e.session_id FROM im.episodes e
           WHERE e.current AND (
             e.stage_version <> %s OR EXISTS (
               SELECT 1 FROM im.episode_segments es
               WHERE es.episode_id = e.episode_id
                 AND NOT EXISTS (SELECT 1 FROM im.current_segments c WHERE c.segment_id = es.segment_id)))
           UNION
           SELECT c.conversation_id FROM im.current_segments c
           WHERE NOT EXISTS (SELECT 1 FROM im.episode_segments es JOIN im.episodes e USING (episode_id)
                             WHERE es.segment_id = c.segment_id AND e.current)""",
        (stage_version,)).fetchall()
    return {r[0] for r in rows}


def _resegment(conn, cfg: Config, sessions: set[str]) -> dict:
    scfg = cfg.segment
    by_session: dict[str, list[Seg]] = {s: [] for s in sessions}
    with conn.cursor(row_factory=class_row(Seg)) as cur:
        cur.execute(SEG_SQL, (list(sessions),))
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


def run(conn: psycopg.Connection, cfg: Config, stream_dir: Path) -> dict:
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('im.run'))")
        run_id = _start_run(conn, "run")
        sessions, stats = import_stream(conn, stream_dir)
        purged = _purge_tombstoned(conn)
        sessions |= purged
        sessions |= _sessions_needing_work(conn, cfg.segment.stage_version)
        stats |= {"stage_version": cfg.segment.stage_version, "sessions_with_purges": len(purged)}
        stats |= _resegment(conn, cfg, sessions)
        _finish_run(conn, run_id, stats)
    return stats


STAGES = ("segment",)


def reset(conn: psycopg.Connection, stage: str) -> dict:
    """Drop a stage's derived rows. The next run rebuilds them for every current segment."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; known: {', '.join(STAGES)}")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('im.run'))")
        run_id = _start_run(conn, f"reset --stage {stage}")
        n = conn.execute("DELETE FROM im.episodes").rowcount
        stats = {"episodes_deleted": n}
        _finish_run(conn, run_id, stats)
    return stats
