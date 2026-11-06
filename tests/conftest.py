"""Every test gets a fresh `im_test` database in the local dev container, its own stream directory, and none of the
developer's settings.

Uses IM_DEV_ADMIN_URL (must be localhost) and IM_DATABASE_URL's role and
password, with the database name swapped to im_test. Both are read once, here; each test then runs without every
variable the code reads to find real files and services (Hearsay's vocabulary, the config, the stream, the
databases, Claude and the other model APIs). A test that needs one sets it.
"""

import os
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from im import db, migrate
from im.config import Config
from im.fixtures import FixtureStream

TEST_DB = "im_test"
BOOTSTRAP = Path(__file__).parent.parent / "dev" / "init" / "bootstrap-db.sql.in"
DSNS = {name: os.environ.get(name) for name in (db.DEV_ADMIN_ENV, db.PIPELINE_ENV)}
# The ambient settings no test may see. IM_TEST_MODELS is the suite's own switch.
AMBIENT = ("IM_", "ANTHROPIC_", "LATTICE_", "CORRAL_", "OPENROUTER_", "TYPESAFE_")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in list(os.environ):
        if name.startswith(AMBIENT) and name != "IM_TEST_MODELS":
            monkeypatch.delenv(name)
    monkeypatch.setenv("ANTHROPIC_PROFILE", "im-test-no-such-profile")   # nor a logged-in `ant` profile: no Claude


def _dsns():
    if not all(DSNS.values()):
        pytest.skip("set IM_DEV_ADMIN_URL and IM_DATABASE_URL (see .env.example)")
    admin, pipe = DSNS[db.DEV_ADMIN_ENV], DSNS[db.PIPELINE_ENV]
    db.assert_local(admin)
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
        with db.connect(pipe_test) as conn:
            migrate.apply(conn, migrate.IM)
            yield conn, admin


@pytest.fixture
def pipe(dbs):
    return dbs[0]


@pytest.fixture
def admin(dbs):
    """A superuser connection to the test database (e.g. to SET ROLE lattice_app)."""
    return dbs[1]


@pytest.fixture
def lattice_dsn(dbs):
    """A DSN for the test database that connects as lattice_app, as Lattice and the neutral view do."""
    return make_conninfo(_dsns()[0], options="-c role=lattice_app")


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def stream(tmp_path):
    """The base fixture stream, written. Mutate it, then call .write(stream.dir) again."""
    s = FixtureStream()
    s.dir = tmp_path / "stream"
    s.write(s.dir)
    return s


# The lattice fixtures live in test_lattice; make them available to every test module.
from test_lattice import seeded, seeded_lattice  # noqa: E402,F401  # pyflakes: fixtures, used by name
