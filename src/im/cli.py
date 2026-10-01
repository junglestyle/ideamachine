"""The `im` command."""

import argparse
import json

from im import config, db, fixtures, migrate, pipeline
from im.checks import run_checks
from im.show import show


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_migrate(args) -> None:
    with db.connect(db.pipeline_dsn()) as conn:
        _print({"applied": migrate.apply(conn, migrate.IM)})


def cmd_load_fixtures(args) -> None:
    with db.connect(db.dev_admin_dsn()) as conn:
        _print(fixtures.load(conn))


def cmd_run(args) -> None:
    with db.connect(db.pipeline_dsn()) as conn:
        _print(pipeline.run(conn, config.load()))


def cmd_reset(args) -> None:
    with db.connect(db.pipeline_dsn()) as conn:
        _print(pipeline.reset(conn, args.stage))


def cmd_show(args) -> None:
    with db.connect(db.pipeline_dsn()) as conn:
        print(show(conn, args.episode, config.load().segment))


def cmd_check(args) -> None:
    with db.connect(db.pipeline_dsn()) as conn:
        failures = {k: v for k, v in run_checks(conn).items() if v}
    _print({k: len(v) for k, v in failures.items()} or "all invariants hold")
    if failures:
        raise SystemExit(1)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="im", description="Idea Machine")
    sub = p.add_subparsers(required=True, metavar="command")
    sub.add_parser("migrate", help="apply im schema migrations").set_defaults(func=cmd_migrate)
    sub.add_parser("load-fixtures", help="write synthetic segments into the local hearsay stand-in (dev only)"
                   ).set_defaults(func=cmd_load_fixtures)
    sub.add_parser("run", help="ingest and segment; idempotent").set_defaults(func=cmd_run)
    r = sub.add_parser("reset", help="drop a stage's derived rows so the next run rebuilds them")
    r.add_argument("--stage", required=True, choices=pipeline.STAGES)
    r.set_defaults(func=cmd_reset)
    s = sub.add_parser("show", help="print an episode")
    s.add_argument("episode", help="episode id or unique prefix")
    s.set_defaults(func=cmd_show)
    sub.add_parser("check", help="run invariant queries; exit 1 on failure").set_defaults(func=cmd_check)
    args = p.parse_args(argv)
    args.func(args)
