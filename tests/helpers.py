from im import pipeline
from im.checks import run_checks


def run(pipe, cfg, stream):
    return pipeline.run(pipe, cfg, stream.dir)


def table(conn, sql, *args):
    return conn.execute(sql, args or None).fetchall()


def episodes(conn):
    return table(conn, "SELECT * FROM im.episodes ORDER BY episode_id")


def derived_state(conn):
    """Everything `im run` writes, except the runs log."""
    return {name: table(conn, f"SELECT * FROM im.{name} ORDER BY 1, 2")
            for name in ["source_conversations", "source_segments", "source_members",
                         "source_supersessions", "source_tombstones", "episodes", "episode_segments"]}


def current_episodes(conn):
    """Current episodes by content, without bookkeeping timestamps."""
    return table(conn, """
        SELECT e.episode_id, session_id, source, started_at, ended_at, kind, n_segments, n_speakers,
               self_segments, schema_version, stage_version, input_hash,
               array_agg(es.segment_id ORDER BY es.ord)
        FROM im.episodes e JOIN im.episode_segments es USING (episode_id)
        WHERE current GROUP BY e.episode_id ORDER BY e.episode_id""")


def segment_of(conn, text):
    rows = table(conn, "SELECT segment_id FROM im.source_segments WHERE text = %s", text)
    assert len(rows) == 1, (text, rows)
    return rows[0][0]


def episodes_with_text(conn, text, current_only=True):
    rows = table(conn, f"""
        SELECT DISTINCT e.episode_id FROM im.episodes e
        JOIN im.episode_segments es USING (episode_id) JOIN im.source_segments s USING (segment_id)
        WHERE s.text = %s {"AND e.current" if current_only else ""}""", text)
    return {r[0] for r in rows}


def kinds(conn):
    out = {}
    for session, kind in conn.execute(
            "SELECT session_id, kind FROM im.episodes WHERE current ORDER BY started_at"):
        out.setdefault(session, []).append(kind)
    return out


def changed_episodes(before, after):
    """(retired, created, untouched-but-different) between two `episodes()` snapshots."""
    b, a = {r[0]: r for r in before}, {r[0]: r for r in after}
    retired = {e for e in b if b[e] != a.get(e)}
    created = set(a) - set(b)
    return retired, created


def assert_invariants(conn):
    assert run_checks(conn) == {"orphans": [], "stale": [], "tombstoned": []}
