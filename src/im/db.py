"""Connections. Settings come from env vars only; no credentials live in the repo."""

import os
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

PIPELINE_ENV = "IM_DATABASE_URL"   # role im_pipeline: owns im.*, nothing else
DEV_ADMIN_ENV = "IM_DEV_ADMIN_URL"  # local dev superuser: tests only
STREAM_ENV = "IM_STREAM_DIR"        # Hearsay's utterance stream, read-only

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _env(name: str) -> str:
    try:
        return os.environ[name]
    except KeyError:
        raise SystemExit(f"{name} is not set (see .env.example)") from None


def pipeline_dsn() -> str:
    return _env(PIPELINE_ENV)


def stream_dir() -> Path:
    return Path(_env(STREAM_ENV))


def assert_local(dsn: str) -> None:
    """Refuse to write fixtures or test data anywhere but this machine.

    Checks every host libpq would try, in either DSN form (URL or `key=value`, with `?host=` too), falling back to
    PGHOST/PGHOSTADDR as libpq does: each must be localhost, a loopback address or a local socket."""
    params = conninfo_to_dict(dsn)
    if params.get("service") or os.environ.get("PGSERVICE"):
        raise SystemExit("refusing dev/test writes through a connection service: its host can't be checked")
    for key, env in (("host", "PGHOST"), ("hostaddr", "PGHOSTADDR")):
        for host in (params.get(key) or os.environ.get(env) or "").split(","):
            if host and host not in LOCAL_HOSTS and not host.startswith(("/", "@")):  # sockets: a path, or abstract
                raise SystemExit(f"refusing dev/test writes to non-local host {host!r}")


def with_dbname(dsn: str, dbname: str) -> str:
    return make_conninfo(dsn, dbname=dbname)


def connect(dsn: str) -> psycopg.Connection:
    # Autocommit, so `with conn.transaction()` blocks are the real transactions.
    return psycopg.connect(dsn, autocommit=True)
