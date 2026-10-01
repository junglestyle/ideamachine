"""Every test gets a fresh `im_test` database in the local dev container.

Uses IM_DEV_ADMIN_URL (must be localhost) and IM_DATABASE_URL's role and
password, with the database name swapped to im_test.
"""

import os
from pathlib import Path

import psycopg
import pytest

from im import db, fixtures, migrate
from im.config import Config

TEST_DB = "im_test"
BOOTSTRAP = Path(__file__).parent.parent / "dev" / "init" / "bootstrap-db.sql.in"


def _dsns():
    if db.DEV_ADMIN_ENV not in os.environ or db.PIPELINE_ENV not in os.environ:
        pytest.skip("set IM_DEV_ADMIN_URL and IM_DATABASE_URL (see .env.example)")
    admin = db.dev_admin_dsn()  # asserts localhost
    pipe = os.environ[db.PIPELINE_ENV]
    db.assert_local(pipe)
    return db.with_dbname(admin, TEST_DB), db.with_dbname(pipe, TEST_DB), admin


@pytest.fixture
def dbs():
    admin_test, pipe_test, admin_dev = _dsns()
    with psycopg.connect(admin_dev, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {TEST_DB}")
    with db.connect(admin_test) as admin:
        admin.execute(BOOTSTRAP.read_text())
        migrate.apply(admin, migrate.HEARSAY_STANDIN)
        with db.connect(pipe_test) as pipe:
            migrate.apply(pipe, migrate.IM)
            yield admin, pipe


@pytest.fixture
def admin(dbs):
    return dbs[0]


@pytest.fixture
def pipe(dbs):
    return dbs[1]


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def loaded(admin):
    fixtures.load(admin)
    return admin
