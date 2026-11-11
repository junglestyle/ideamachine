"""im run records every stage on its run row (im.runs), and a failure too."""

import json

import pytest

from im import cli

from helpers import table


@pytest.fixture
def run_cli(pipe, stream, monkeypatch, capsys):
    monkeypatch.setattr(cli.db, "pipeline_dsn", lambda: pipe.info.dsn + " password=" + pipe.info.password)
    monkeypatch.setattr(cli.db, "stream_dir", lambda: stream.dir)

    def go():
        cli.main(["run"])
        return json.loads(capsys.readouterr().out)
    return go


def test_a_run_records_every_stage_and_its_warnings(pipe, run_cli):
    out = run_cli()
    (stats, finished) = table(pipe, "SELECT stats, finished_at IS NOT NULL FROM im.runs WHERE command = 'run'")[0]
    assert finished and "extract" in stats and "route" in stats and stats["monthly_cap_usd"] == 20.0
    assert "run_id" not in out
    assert "error" not in stats and stats["extract"]["stopped"].startswith("Claude unavailable")


def test_a_failed_stage_is_recorded_with_what_ran_before_it(pipe, run_cli, monkeypatch):
    def boom(conn):
        raise RuntimeError("disk full")
    monkeypatch.setattr("im.router.run_stage", boom)
    with pytest.raises(RuntimeError):
        run_cli()
    (stats,) = table(pipe, "SELECT stats FROM im.runs WHERE command = 'run'")[0]
    assert stats["error"] == "RuntimeError: disk full" and "extract" in stats and "conversations_changed" in stats


def test_a_run_that_fails_at_once_still_leaves_a_row(pipe, run_cli, monkeypatch):
    def gone(*a, **k):
        raise FileNotFoundError("no index.json in /data/stream")
    monkeypatch.setattr("im.pipeline.import_stream", gone)
    with pytest.raises(FileNotFoundError):
        run_cli()
    assert table(pipe, "SELECT stats->>'error', finished_at IS NOT NULL FROM im.runs WHERE command = 'run'") == \
        [("FileNotFoundError: no index.json in /data/stream", True)]
