"""The `im` command."""

import argparse
import json
from pathlib import Path

from im import config, db, fixtures, label, migrate, pipeline, projects
from im.checks import run_checks
from im.show import show


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_migrate(args) -> None:
    with db.connect(db.pipeline_dsn()) as conn:
        _print({"applied": migrate.apply(conn, migrate.IM)})


def cmd_load_fixtures(args) -> None:
    _print(fixtures.write(Path(args.dir) if args.dir else db.stream_dir(), args.scenario))


def cmd_run(args) -> None:
    with db.connect(db.pipeline_dsn()) as conn:
        _print(pipeline.run(conn, config.load(), db.stream_dir()))


def cmd_reset(args) -> None:
    with db.connect(db.pipeline_dsn()) as conn:
        _print(pipeline.reset(conn, args.stage))


def cmd_show(args) -> None:
    with db.connect(db.pipeline_dsn()) as conn:
        print(show(conn, args.episode))


def cmd_label(args) -> None:
    with db.connect(db.pipeline_dsn()) as conn:
        if args.status:
            print(label.status(conn))
            return
        try:
            n = label.session(conn)
        except (KeyboardInterrupt, EOFError):
            n = None  # each label is saved as it's confirmed, so nothing is lost
        print("\n" + label.status(conn) if n is None else f"saved {n}\n" + label.status(conn))


def cmd_project(args) -> None:
    with db.connect(db.pipeline_dsn()) as conn:
        try:
            if args.action == "add":
                print(projects.add(conn, args.slug, args.description, args.alias or []))
            elif args.action == "retire":
                projects.retire(conn, args.slug)
            print(projects.listing(conn))
        except ValueError as e:
            raise SystemExit(str(e)) from None


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
    f = sub.add_parser("load-fixtures", help="write a synthetic Hearsay stream (dev only)")
    f.add_argument("--dir", help="where to write it (default: $IM_STREAM_DIR)")
    f.add_argument("--scenario", choices=fixtures.SCENARIOS, default="base",
                   help="base, or base plus every correction and a forget")
    f.set_defaults(func=cmd_load_fixtures)
    sub.add_parser("run", help="ingest and segment; idempotent").set_defaults(func=cmd_run)
    r = sub.add_parser("reset", help="drop a stage's derived rows so the next run rebuilds them")
    r.add_argument("--stage", required=True, choices=pipeline.STAGES)
    r.set_defaults(func=cmd_reset)
    s = sub.add_parser("show", help="print an episode")
    s.add_argument("episode", help="episode id or unique prefix")
    s.set_defaults(func=cmd_show)
    lb = sub.add_parser("label", help="label episodes with my answers to the triage questions")
    lb.add_argument("--status", action="store_true", help="show labeling progress and exit")
    lb.set_defaults(func=cmd_label)
    pr = sub.add_parser("project", help="the projects registry")
    pa = pr.add_subparsers(dest="action", required=True, metavar="action")
    a = pa.add_parser("add", help="add a project, or update its description and add aliases")
    a.add_argument("slug")
    a.add_argument("--description", "-d")
    a.add_argument("--alias", "-a", action="append")
    r2 = pa.add_parser("retire", help="hide a project from labeling (labels keep it)")
    r2.add_argument("slug")
    pa.add_parser("list")
    pr.set_defaults(func=cmd_project)
    sub.add_parser("check", help="run invariant queries; exit 1 on failure").set_defaults(func=cmd_check)
    args = p.parse_args(argv)
    args.func(args)
