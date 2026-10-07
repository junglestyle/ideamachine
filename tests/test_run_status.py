"""im run records every stage on its run row, and a failure too; Lattice reads that through pub.runs."""

import json

import pytest

from im import cli

from helpers import table


@pytest.fixture
def run_cli(pipe, stream, monkeypatch, capsys):
    monkeypatch.setattr(cli.db, "pipeline_dsn", lambda: pipe.info.dsn + " password=" + pipe.info.password)
    monkeypatch.setattr(cli.db, "stream_dir", lambda: stream.dir)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_PROFILE", "im-test-no-such-profile")   # no Claude: extraction stops, and says so

    def go():
        cli.main(["run"])
        return json.loads(capsys.readouterr().out)
    return go


def test_a_run_records_every_stage_and_its_warnings(pipe, run_cli):
    out = run_cli()
    (stats, finished) = table(pipe, "SELECT stats, finished_at IS NOT NULL FROM im.runs WHERE command = 'run'")[0]
    assert finished and "extract" in stats and "route" in stats and stats["monthly_cap_usd"] == 20.0
    assert "run_id" not in out
    (error, warnings, cap) = table(pipe, "SELECT error, warnings, monthly_cap_usd FROM pub.runs")[0]
    assert error is None and cap == 20 and any(w.startswith("Claude unavailable") for w in warnings)


def test_a_failed_stage_is_recorded_with_what_ran_before_it(pipe, run_cli, monkeypatch):
    def boom(conn):
        raise RuntimeError("disk full")
    monkeypatch.setattr("im.router.run_stage", boom)
    with pytest.raises(RuntimeError):
        run_cli()
    (stats,) = table(pipe, "SELECT stats FROM im.runs WHERE command = 'run'")[0]
    assert stats["error"] == "RuntimeError: disk full" and "extract" in stats and "conversations_changed" in stats
    assert table(pipe, "SELECT error FROM pub.runs") == [("RuntimeError: disk full",)]


def test_a_run_that_fails_at_once_still_leaves_a_row(pipe, run_cli, monkeypatch):
    def gone(*a, **k):
        raise FileNotFoundError("no index.json in /data/stream")
    monkeypatch.setattr("im.pipeline.import_stream", gone)
    with pytest.raises(FileNotFoundError):
        run_cli()
    assert table(pipe, "SELECT error, finished_at IS NOT NULL FROM pub.runs") == \
        [("FileNotFoundError: no index.json in /data/stream", True)]


def test_lattice_can_read_status_and_spend(pipe, run_cli, admin):
    run_cli()
    with admin.transaction():
        admin.execute("SET LOCAL ROLE lattice_app")
        assert len(admin.execute("SELECT * FROM pub.runs").fetchall()) == 1
        assert admin.execute("SELECT month_to_date FROM pub.spend").fetchone()[0] == 0
