"""The `im` command."""

import argparse
import json
from pathlib import Path

from im import config, db, fixtures, label, migrate, pipeline, projects, triage
from im.checks import run_checks
from im.show import show


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_migrate(args) -> None:
    with db.connect(db.pipeline_dsn()) as conn:
        _print({"applied": migrate.apply(conn, migrate.IM)})


def cmd_load_fixtures(args) -> None:
    _print(fixtures.write(Path(args.dir) if args.dir else db.stream_dir(), args.scenario))


def _backends(conn, cfg, names):
    """The configured triage backends that can run. logreg waits until it's trained."""
    from im import backends

    out, notes = [], []
    for name in names:
        if name == "laya":
            out.append(backends.LayaBackend(cfg.triage.laya_checkpoint, cfg.triage.laya_max_len, cfg.triage.threads))
        elif name == "llm":
            out.append(_llm(cfg))
        elif name == "logreg":
            try:
                out.append(backends.LogregBackend(conn, _embedder(cfg)))
            except LookupError as e:
                notes.append(str(e))
        else:
            raise SystemExit(f"unknown triage backend {name!r}")
    return out, notes


def _llm(cfg):
    from im.backends import LLMBackend

    t = cfg.triage
    return LLMBackend(t.llm_model, t.llm_url, t.llm_think, t.llm_num_ctx)


def _embedder(cfg):
    from im.backends import SentenceEmbedder

    return SentenceEmbedder(cfg.triage.embedding_model)


def cmd_run(args) -> None:
    cfg = config.load()
    with db.connect(db.pipeline_dsn()) as conn:
        stats = pipeline.run(conn, cfg, db.stream_dir())
        if cfg.triage.backends:
            run_backends, notes = _backends(conn, cfg, cfg.triage.backends)
            stats["triage"] = [triage.run_stage(conn, b, cfg.triage.max_episodes_per_run) for b in run_backends]
            stats["triage_notes"] = notes
        _print(stats)


def cmd_train(args) -> None:
    from im import backends

    cfg = config.load()
    with db.connect(db.pipeline_dsn()) as conn:
        try:
            _print(backends.train(conn, _embedder(cfg), args.C))
        except ValueError as e:
            raise SystemExit(str(e)) from None


def cmd_eval(args) -> None:
    from im import evaluate

    cfg = config.load()
    with db.connect(db.pipeline_dsn()) as conn:
        from im.backends import LayaBackend, exact_labels

        only = [eid for _, eid, _ in exact_labels(conn)]
        if args.llm:  # make sure every labeled episode has the backend's answers to score
            triage.run_stage(conn, _llm(cfg), only=only)
        if args.laya:
            b = LayaBackend(cfg.triage.laya_checkpoint, cfg.triage.laya_max_len, cfg.triage.threads)
            triage.run_stage(conn, b, only=only)
        try:
            print(evaluate.report(evaluate.evaluate(conn, _embedder(cfg), args.k)))
        except ValueError as e:
            raise SystemExit(str(e)) from None


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
            review = tuple(args.review.split("=", 1)) if args.review else None
            if review and (len(review) != 2 or review[0] not in label.QUESTION_FIELDS):
                raise SystemExit(f"--review takes question=answer, e.g. kind=idea; questions: {', '.join(label.QUESTION_FIELDS)}")
            n = label.session(conn, review=review)
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
    sub.add_parser("run", help="import, segment and triage; idempotent").set_defaults(func=cmd_run)
    t = sub.add_parser("train", help="train the logreg baseline on my exact labels")
    t.add_argument("-C", type=float, default=1.0, help="inverse regularization strength")
    t.set_defaults(func=cmd_train)
    ev = sub.add_parser("eval", help="per-question accuracy and reliability for each backend, on my labels")
    ev.add_argument("-k", type=int, default=5, help="folds for logreg's and calibration's cross-validation")
    ev.add_argument("--llm", action="store_true", help="first triage any labeled episode the LLM hasn't answered")
    ev.add_argument("--laya", action="store_true", help="first triage any labeled episode Laya hasn't answered")
    ev.set_defaults(func=cmd_eval)
    r = sub.add_parser("reset", help="drop a stage's derived rows so the next run rebuilds them")
    r.add_argument("--stage", required=True, choices=pipeline.STAGES)
    r.set_defaults(func=cmd_reset)
    s = sub.add_parser("show", help="print an episode")
    s.add_argument("episode", help="episode id or unique prefix")
    s.set_defaults(func=cmd_show)
    lb = sub.add_parser("label", help="label episodes with my answers to the triage questions")
    lb.add_argument("--status", action="store_true", help="show labeling progress and exit")
    lb.add_argument("--review", metavar="QUESTION=ANSWER",
                    help="re-label episodes whose latest label has this answer, e.g. kind=idea or project=none")
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
