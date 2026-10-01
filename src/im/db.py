"""Connections. Settings come from env vars only; no credentials live in the repo."""

import os
from urllib.parse import urlsplit, urlunsplit

import psycopg

PIPELINE_ENV = "IM_DATABASE_URL"   # role with SELECT on hearsay.*, owner of im.*
DEV_ADMIN_ENV = "IM_DEV_ADMIN_URL"  # local dev superuser: fixtures and tests only

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _env(name: str) -> str:
    try:
        return os.environ[name]
    except KeyError:
        raise SystemExit(f"{name} is not set (see .env.example)") from None


def pipeline_dsn() -> str:
    return _env(PIPELINE_ENV)


def dev_admin_dsn() -> str:
    dsn = _env(DEV_ADMIN_ENV)
    assert_local(dsn)
    return dsn


def assert_local(dsn: str) -> None:
    """Refuse to write fixtures or test data anywhere but this machine."""
    host = urlsplit(dsn).hostname or ""
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"refusing dev/test writes to non-local host {host!r}")


def with_dbname(dsn: str, dbname: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path="/" + dbname))


def connect(dsn: str) -> psycopg.Connection:
    # Autocommit, so `with conn.transaction()` blocks are the real transactions.
    return psycopg.connect(dsn, autocommit=True)
