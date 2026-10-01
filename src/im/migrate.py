"""A tiny runner for numbered plain-SQL migrations (NNN_name.sql), one transaction each."""

import hashlib
import re
from importlib import resources

import psycopg

IM = ("im", "im.schema_migrations")
HEARSAY_STANDIN = ("hearsay_standin", "public.hearsay_standin_migrations")

_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def _files(track: str) -> list[tuple[int, str, str]]:
    out = []
    for entry in resources.files("im.sql").joinpath(track).iterdir():
        m = _NAME.match(entry.name)
        if m:
            out.append((int(m.group(1)), entry.name, entry.read_text()))
    return sorted(out)


def apply(conn: psycopg.Connection, track: tuple[str, str]) -> list[str]:
    """Apply pending migrations of a track. Returns the names applied."""
    name, table = track
    with conn.transaction():
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
            version integer PRIMARY KEY, name text NOT NULL, sha256 text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now())""")
    applied = []
    for version, fname, sql in _files(name):
        digest = hashlib.sha256(sql.encode()).hexdigest()
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (table,))
            row = conn.execute(f"SELECT sha256 FROM {table} WHERE version = %s", (version,)).fetchone()
            if row:
                if row[0] != digest:
                    raise SystemExit(f"{fname} changed after it was applied; add a new migration instead")
                continue
            conn.execute(sql)
            conn.execute(f"INSERT INTO {table} (version, name, sha256) VALUES (%s, %s, %s)",
                         (version, fname, digest))
            applied.append(fname)
    return applied
