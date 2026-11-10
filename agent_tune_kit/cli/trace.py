"""Explicit session commands; analysis and effect judgments belong to the host."""

import json
from pathlib import Path

from agent_tune_kit.traces import categories, records, store
from agent_tune_kit.traces.capture import fetch_session, import_session
from agent_tune_kit.traces.query import overview, search, show


def read_json(path):
    if path is None:
        return None
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def add_session_options(parser):
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--source", help="JSON file with type, instance, project and environment")
    parser.add_argument("--json", action="store_true")


def register(subparsers):
    commands = subparsers.add_parser(
        "trace", help="capture and investigate one session, independently of batch evaluation"
    )
    actions = commands.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init", help="save project context and non-secret source configuration")
    init.add_argument("--project-root", default=".")
    init.add_argument("--config", help="non-secret configuration JSON file")
    init.add_argument("--project-file", help="project context Markdown file")
    init.add_argument("--json", action="store_true")
    category = actions.add_parser("category", help="list or update the project issue category vocabulary")
    category.add_argument("--project-root", default=".")
    mode = category.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="list categories with per-session issue counts")
    mode.add_argument("--data", help="category JSON; revising the vocabulary requires its current revision")
    category.add_argument("--json", action="store_true")
    for action in [
        "migrate",
        "fetch",
        "import",
        "overview",
        "search",
        "show",
        "status",
        "ref",
        "issue",
        "detect",
        "diagnose",
        "change",
        "verify",
    ]:
        parser = actions.add_parser(action)
        add_session_options(parser)
        if action == "fetch":
            parser.add_argument("--config")
            parser.add_argument("--from-time")
            parser.add_argument("--to-time")
            parser.add_argument("--retention")
        elif action == "import":
            parser.add_argument(
                "--path", action="append", required=True, help="supported source file; repeat for multiple files"
            )
            parser.add_argument("--association", help="JSON provenance linking native ATK evidence to this session")
            parser.add_argument("--retention", help="explicit body retention and redaction policy JSON")
        elif action in {"overview", "search", "show"}:
            parser.add_argument("--offset", type=int, default=0)
            parser.add_argument("--limit", type=int, default=8000 if action == "show" else 50)
            if action != "show":
                parser.add_argument("--capture-id")
            if action == "overview":
                parser.add_argument("--trace-id", help="limit overview rows to one native Trace ID")
            if action == "search":
                parser.add_argument("--text", required=True)
            elif action == "show":
                parser.add_argument(
                    "--ref", required=True, help="JSON evidence reference returned by overview/search/ref"
                )
        elif action == "ref":
            parser.add_argument("--path", required=True, help="path relative to the archived session")
            parser.add_argument("--pointer", help="JSON Pointer inside the retained evidence")
        elif action in {"issue", "detect", "change", "verify"}:
            parser.add_argument("--data", required=True, help="record JSON; updates require the current revision")
            if action == "issue":
                parser.add_argument(
                    "--dry-run",
                    action="store_true",
                    help="validate issue segments and report coverage without writing",
                )
            if action in {"change", "verify"}:
                parser.add_argument("--issue-id", required=True)
            if action == "verify":
                parser.add_argument("--change-id", required=True)
        elif action == "diagnose":
            parser.add_argument("--issue-id", required=True)
            parser.add_argument("--file", required=True, help="root cause and repair/verification plan Markdown")
            parser.add_argument("--diagnosis-id")


def execute(args):
    if args.action == "init":
        text = Path(args.project_file).read_text(encoding="utf-8") if args.project_file else None
        return store.init_workspace(args.project_root, config=read_json(args.config), project_text=text)
    if args.action == "category":
        if args.list:
            return categories.list_categories(args.project_root)
        return categories.save_category(args.project_root, read_json(args.data))
    source = read_json(args.source)
    if args.action == "migrate":
        from agent_tune_kit.traces.migrate import migrate

        return migrate(args.project_root, args.session_id, source)
    if args.action == "fetch":
        config = read_json(args.config)
        if source is not None:
            if config is None:
                config = read_json(Path(args.project_root) / ".atk/traces/config.json")
            config = {**config, "source": source}
        return fetch_session(
            args.project_root,
            args.session_id,
            config=config,
            from_time=args.from_time,
            to_time=args.to_time,
            retention=read_json(args.retention),
        )
    if args.action == "import":
        if source is None:
            config = read_json(Path(args.project_root) / ".atk/traces/config.json")
            source = config.get("source")
        return import_session(
            args.project_root,
            args.session_id,
            source,
            args.path,
            association=read_json(args.association),
            retention=read_json(args.retention),
        )
    session_dir = store.resolve_session(args.project_root, args.session_id, source)
    if args.action in {"overview", "search", "show"}:
        options = {"offset": args.offset, "limit": args.limit}
        if args.action == "show":
            return show(session_dir, read_json(args.ref), **options)
        options["capture_id"] = args.capture_id
        if args.action == "search":
            return search(session_dir, args.text, **options)
        if getattr(args, "trace_id", None):
            options["trace_id"] = args.trace_id
        return overview(session_dir, **options)
    if args.action == "status":
        return records.status(session_dir)
    if args.action == "ref":
        return store.evidence_ref(session_dir, args.path, args.pointer)
    if args.action == "diagnose":
        return records.save_diagnosis(
            session_dir,
            args.issue_id,
            Path(args.file).read_text(encoding="utf-8"),
            diagnosis_id=args.diagnosis_id,
        )
    data = read_json(args.data)
    if args.action == "issue":
        return records.save_issue(session_dir, data, dry_run=bool(getattr(args, "dry_run", False)))
    if args.action == "detect":
        return records.save_detection(session_dir, data)
    if args.action == "change":
        return records.save_change(session_dir, args.issue_id, data)
    return records.save_verification(session_dir, args.issue_id, args.change_id, data)
