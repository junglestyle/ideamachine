from im import fixtures
from im.checks import run_checks


def table(conn, sql):
    return conn.execute(sql).fetchall()


def episodes(conn):
    return table(conn, "SELECT * FROM im.episodes ORDER BY episode_id")


def derived_state(conn):
    """Everything `im run` derives (the runs log excluded)."""
    return {
        "episodes": episodes(conn),
        "episode_segments": table(conn, "SELECT * FROM im.episode_segments ORDER BY episode_id, segment_id"),
        "ingest_state": table(conn, "SELECT * FROM im.ingest_state ORDER BY name"),
    }


def current_episodes(conn):
    """Current episodes by content, without bookkeeping timestamps."""
    return table(conn, """
        SELECT e.episode_id, session_id, source, started_at, ended_at, kind, n_segments, n_speakers,
               self_segments, schema_version, stage_version, input_hash,
               array_agg(es.segment_id ORDER BY es.ord)
        FROM im.episodes e JOIN im.episode_segments es USING (episode_id)
        WHERE current GROUP BY e.episode_id ORDER BY e.episode_id""")


def episodes_containing(conn, names, current_only=True):
    rows = conn.execute(
        f"""SELECT DISTINCT e.episode_id FROM im.episodes e JOIN im.episode_segments es USING (episode_id)
            WHERE es.segment_id = ANY(%s) {"AND e.current" if current_only else ""}""",
        ([fixtures.sid(n) for n in names],)).fetchall()
    return {r[0] for r in rows}


def assert_invariants(conn):
    assert run_checks(conn) == {"orphans": [], "stale": [], "tombstoned": []}
