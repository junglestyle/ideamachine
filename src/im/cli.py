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
            try:
                out.append(_llm(cfg))
            except OSError as e:
                notes.append(f"llm unavailable, will retry next run: {e}")
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
    """Every stage, recorded on one im.runs row, including a failure: Lattice's status line reads it (pub.runs)."""
    cfg = config.load()
    with db.connect(db.pipeline_dsn()) as conn:
        try:
            stats = _run_stages(conn, cfg)
        except Exception as e:
            partial = dict(getattr(e, "stats", {}))
            _record_run(conn, partial.pop("run_id", None), partial | {"error": f"{type(e).__name__}: {e}"[:500]})
            raise
        _record_run(conn, stats.pop("run_id"), stats)
        _print(stats)


def _record_run(conn, run_id, stats: dict) -> None:
    import json

    if run_id is None:   # the first stage failed, and its transaction (with the row) rolled back
        conn.execute("INSERT INTO im.runs (command, finished_at, stats) VALUES ('run', now(), %s)", (json.dumps(stats),))
    else:
        conn.execute("UPDATE im.runs SET finished_at = now(), stats = %s WHERE run_id = %s", (json.dumps(stats), run_id))


def _run_stages(conn, cfg) -> dict:
    stats = pipeline.run(conn, cfg, db.stream_dir()) | {"monthly_cap_usd": cfg.extract.monthly_cap_usd}
    try:
        from im import feedback

        # Before extraction and matching, so captures I discarded in Lattice stay out of the lattice.
        stats["feedback"] = feedback.apply_item_feedback(conn)
        if cfg.triage.backends:
            run_backends, notes = _backends(conn, cfg, cfg.triage.backends)
            stats["triage"] = []
            for b in run_backends:
                try:
                    stats["triage"].append(triage.run_stage(conn, b, cfg.triage.max_episodes_per_run))
                except OSError as e:  # e.g. Ollama down, or the GPU busy with Hearsay's transcription
                    notes.append(f"{b.name} unavailable, will retry next run: {e}")
            stats["triage_notes"] = notes
        if cfg.extract.enabled:
            stats["extract"] = _extract(conn, cfg)
        # After extraction (the new items exist) and before matching and routing (a carried discard counts).
        stats["verdicts_carried"] = feedback.carry_verdicts(conn)
        if cfg.lattice.enabled:
            stats["lattice"] = _lattice(conn, cfg)
            stats["themes"] = _themes(conn, cfg)
        from im import router

        stats["route"] = router.run_stage(conn)
    except Exception as e:   # keep what's known so far, including run_id, for the record
        e.stats = stats
        raise
    return stats


def _extract(conn, cfg) -> dict:
    """Run extraction, or say why it couldn't run (no credentials, API down); the run goes on either way."""
    import anthropic

    from im import extract

    x = cfg.extract
    try:
        client = anthropic.Anthropic()
        return extract.run_stage(conn, client, x.model, x.effort, x.monthly_cap_usd, x.max_episodes_per_run)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        return {"stopped": f"Claude credentials rejected: {e}"}
    except anthropic.AnthropicError as e:  # e.g. no credentials configured at all
        return {"stopped": f"Claude unavailable: {e}"}


def _lattice(conn, cfg) -> dict:
    """Match new captures into the lattice, or say why it couldn't run; the run goes on either way."""
    import anthropic

    from im import lattice
    from im.backends import SentenceEmbedder

    try:
        return lattice.match_stage(conn, anthropic.Anthropic(), SentenceEmbedder(cfg.lattice.embedding_model),
                                   cfg.extract.monthly_cap_usd, cfg.lattice.max_items_per_run)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        return {"stopped": f"Claude credentials rejected: {e}"}
    except anthropic.AnthropicError as e:
        return {"stopped": f"Claude unavailable: {e}"}


def _themes(conn, cfg) -> dict:
    """Apply my theme feedback, file unthemed ideas, propose themes for the leftovers."""
    import anthropic

    from im import themes

    out = {"feedback": themes.apply_feedback(conn)}
    try:
        client = anthropic.Anthropic()
        out["file"] = themes.file_stage(conn, client, cfg.extract.monthly_cap_usd)
        out["propose"] = themes.propose_stage(conn, client, cfg.extract.monthly_cap_usd)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        out["stopped"] = f"Claude credentials rejected: {e}"
    except anthropic.AnthropicError as e:
        out["stopped"] = f"Claude unavailable: {e}"
    return out


def cmd_themes(args) -> None:
    from im import themes

    with db.connect(db.pipeline_dsn()) as conn:
        if args.action != "list":
            try:
                theme_id = themes.find(conn, args.theme)
            except ValueError as e:
                raise SystemExit(str(e)) from None
            kind = {"pin": "pin_theme", "unpin": "unpin_theme", "rename": "rename_theme", "reject": "reject_theme"}
            if args.action == "rename" and not args.name:
                raise SystemExit("rename needs the new name")
            themes.record(conn, kind[args.action], theme_id, {"name": args.name} if args.action == "rename" else None)
            print(themes.apply_feedback(conn))
        print(themes.listing(conn))


def cmd_pending(args) -> None:
    """What the next run would send to Claude, without sending anything. After moving the database to another
    machine, `already_extracted_would_resend` must be 0: anything else means the payloads changed (e.g. the time
    zone), and the run would re-send episodes and orphan my verdicts."""
    from im import extract, lattice, themes

    cfg = config.load()
    with db.connect(db.pipeline_dsn()) as conn:
        pv = extract.prompt_version(cfg.extract.model, cfg.extract.effort)
        todo = extract._pending(conn, cfg.extract.model, pv)
        done = {r[0] for r in conn.execute(
            "SELECT episode_id FROM im.extractions WHERE model = %s AND prompt_version = %s",
            (cfg.extract.model, pv))}
        out = {"episodes_to_extract": len(todo),
               "already_extracted_would_resend": sum(p.episode_id in done for p in todo),
               "items_to_match": len(lattice._pending_items(conn)),
               "ideas_to_file": len(themes._unthemed(conn, themes.themes_version(conn)))}
    _print(out)
    if args.expect_none_resent and out["already_extracted_would_resend"]:
        raise SystemExit(1)


def cmd_seed(args) -> None:
    from im import lattice

    with db.connect(db.pipeline_dsn()) as conn:
        _print(lattice.seed_archive(conn, Path(args.path), args.name))


def cmd_lattice(args) -> None:
    from im import lattice

    with db.connect(db.pipeline_dsn()) as conn:
        _print(lattice.status(conn))


def cmd_ideas(args) -> None:
    from im import extract

    with db.connect(db.pipeline_dsn()) as conn:
        if args.review:
            n = _review_items(conn, extract)
            print(f"\ndecided {n}; {len(extract.review_items(conn))} still undecided")
            return
        if args.discards:
            rows = extract.discards(conn)
            for kind, who, quote, gist, conf, at, note, model, pv in rows:
                print(f"{at.astimezone():%Y-%m-%d %H:%M}  {kind:<11} {who:<16} {conf:.2f}  {model} prompt {pv}")
                print(f"    \u201c{quote}\u201d")
                print(f"    {gist}")
                print(f"    why discarded: {note or '(no note)'}\n")
            print(f"{len(rows)} discarded")
            return
        rows = conn.execute(
            """SELECT i.kind, i.said_by, i.quote, i.gist, i.confidence, e.started_at,
                      (SELECT v.verdict FROM im.item_verdicts v WHERE v.item_id = i.item_id
                       ORDER BY v.decided_at DESC LIMIT 1)
               FROM im.items i JOIN im.episodes e USING (episode_id)
               WHERE e.current ORDER BY e.started_at DESC, i.confidence DESC""").fetchall()
        shown = 0
        for kind, who, quote, gist, conf, at, verdict in rows:
            if verdict == "discard" and not args.all:
                continue
            mark = {"keep": "kept", "star": "\u2605", "discard": "discarded", None: "new"}[verdict]
            print(f"{at.astimezone():%Y-%m-%d %H:%M}  {kind:<11} {who:<16} {conf:.2f}  [{mark}]")
            print(f"    \u201c{quote}\u201d")
            print(f"    {gist}\n")
            shown += 1
        print(f"{shown} shown ({len(rows)} captured on current episodes)")


def _review_items(conn, extract, read=input, write=print) -> int:
    from im.label import _choose

    n = 0
    for item_id, kind, who, quote, gist, themes, conf, _, at, _ in extract.review_items(conn):
        write("\n" + "─" * 72)
        write(f"{at.astimezone():%A %Y-%m-%d %H:%M}  {kind} said by {who}  (confidence {conf:.2f})")
        write(f"  \u201c{quote}\u201d")
        write(f"  {gist}")
        if themes:
            write(f"  themes: {', '.join(themes)}")
        got = _choose(read, write, "[k]eep · [*] star · [d]iscard · [s]kip · [q]uit > ",
                      {"k": "keep", "*": "star", "d": "discard", "s": "skip", "q": "quit"})
        if got == "quit":
            break
        if got == "skip":
            continue
        # Only a discard asks why: that's what tuning the extraction prompt needs (im ideas --discards).
        note = (read("why? (optional; helps tune the prompt) > ").strip() or None) if got == "discard" else None
        extract.decide(conn, item_id, got, note)
        n += 1
    return n


def cmd_forgotten(args) -> None:
    from im import extract

    with db.connect(db.pipeline_dsn()) as conn:
        rows = extract.forgotten_sent(conn)
    if not rows:
        print("no forgotten segment was ever sent to Claude")
    for seg, forgotten_at, first_sent, n in rows:
        print(f"{seg}  forgotten {forgotten_at:%Y-%m-%d}, sent {n}x, first {first_sent:%Y-%m-%d %H:%M}")


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


def cmd_review(args) -> None:
    from im import router

    with db.connect(db.pipeline_dsn()) as conn:
        if args.status:
            print(f"{len(router.review_queue(conn))} episodes waiting for review")
            return
        try:
            n = label.routed_session(conn)
        except (KeyboardInterrupt, EOFError):
            n = None
        print(f"\n{'' if n is None else f'reviewed {n}; '}{len(router.review_queue(conn))} still waiting")


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
    pe = sub.add_parser("pending", help="what the next run would send to Claude, without sending anything")
    pe.add_argument("--expect-none-resent", action="store_true",
                    help="exit 1 if any already-extracted episode would be sent again (use after a move)")
    pe.set_defaults(func=cmd_pending)
    sd = sub.add_parser("seed", help="import an idea archive into the lattice (its clusters become pinned themes)")
    sd.add_argument("path", help="the archive, e.g. ~/.local/share/ideamachine/seeds/chatgpt-archive.md")
    sd.add_argument("--name", default="chatgpt", help="prefix for its references, e.g. chatgpt#17")
    sd.set_defaults(func=cmd_seed)
    sub.add_parser("lattice", help="counts: ideas, evidence, connections, unmatched items").set_defaults(
        func=cmd_lattice)
    th = sub.add_parser("themes", help="list themes; pin, unpin, rename or reject one")
    th.add_argument("action", nargs="?", default="list", choices=["list", "pin", "unpin", "rename", "reject"])
    th.add_argument("theme", nargs="?", help="theme name (or its start), or id prefix")
    th.add_argument("name", nargs="?", help="the new name, for rename")
    th.set_defaults(func=cmd_themes)
    ia = sub.add_parser("ideas", help="what Claude captured; --review to keep or discard each item")
    ia.add_argument("--review", action="store_true", help="keep or discard each undecided item, most confident first")
    ia.add_argument("--all", action="store_true", help="also list discarded items")
    ia.add_argument("--discards", action="store_true",
                    help="discarded items with why: material for revising the extraction prompt")
    ia.set_defaults(func=cmd_ideas)
    fg = sub.add_parser("forgotten", help="forgotten segments that had already been sent to Claude")
    fg.add_argument("--sent", action="store_true", required=True)
    fg.set_defaults(func=cmd_forgotten)
    rv = sub.add_parser("review", help="work through the episodes the router sent me")
    rv.add_argument("--status", action="store_true", help="show how many are waiting and exit")
    rv.set_defaults(func=cmd_review)
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
