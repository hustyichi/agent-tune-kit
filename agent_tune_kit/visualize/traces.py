"""Project-wide dashboard over every archived session, written to ``.atk/traces/index.html``.

The page only summarizes: identity, when the session itself ran, actual progress and category per issue, whether the latest
detection finished, plus a link into each session's own preview. It reads saved records through ``records.status``
(which already verifies evidence digests); retained trace files are read only for their event times, and no trace body
is embedded. A damaged session or issue is reported on its own entry and cannot hide the healthy ones.
"""

from __future__ import annotations

from datetime import datetime
from importlib import resources
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.evidence import safe_path
from agent_tune_kit.traces import query, records
from agent_tune_kit.traces.categories import category_map, load_catalog
from agent_tune_kit.traces.progress import PROGRESS, issue_progress, parse_instant, summarize
from agent_tune_kit.traces.projects import linked_projects, load_projects, project_index, project_map
from agent_tune_kit.traces.store import read_json, trace_root
from agent_tune_kit.utils.files import write_text_atomic

from .render import neutralize_script_close, open_in_browser, safe_json_for_html

ASSETS = resources.files("agent_tune_kit.visualize").joinpath("assets", "traces")
SHARED_STYLES = resources.files("agent_tune_kit.visualize").joinpath("assets", "shared", "base.css")
INDEX_NAME = "index.html"
PREVIEW_NAME = "preview.html"
SAFE_SOURCE_FIELDS = ("type", "instance", "project", "environment")
# Preview files are written just after the last record, so allow for coarse filesystem timestamps.
STALE_AFTER_SECONDS = 2
TITLE_LIMIT = 300
TEXT_LIMIT = 1200
LIST_LIMIT = 20
ITEM_LIMIT = 600


def _text(value, limit=TEXT_LIMIT):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _latest(stamps):
    known = [stamp for stamp in stamps if stamp is not None]
    return max(known) if known else None


def _problem(warning):
    where = next((warning[key] for key in ("issue_id", "change_id", "capture_id", "path") if warning.get(key)), "")
    return _text(f"{where}: {warning.get('error', '')}" if where else warning.get("error", ""), ITEM_LIMIT)


def _session_span(session_dir):
    """Return the earliest and latest event time recorded in the retained trace, in epoch milliseconds.

    This is when the session itself ran, not when ATK imported or processed it. Times are read with the same rules as
    the session page; a session whose events carry no usable time, or whose trace cannot be read, has no span.
    """
    try:
        items = query.nodes(session_dir)
    except (OSError, ValueError, RecursionError, UserActionRequired):
        return None, None
    stamps = [
        stamp
        for item in items
        for stamp in (query.timestamp_ms(item.get("start_time")), query.timestamp_ms(item.get("end_time")))
        if stamp is not None
    ]
    return (int(min(stamps)), int(max(stamps))) if stamps else (None, None)


def _preview_mtime(session_dir):
    path = session_dir / PREVIEW_NAME
    try:
        return path.stat().st_mtime if path.is_file() and not path.is_symlink() else None
    except OSError:
        return None


def _verifications(session_dir, owner, change_id):
    """Read every verification of a change like the session page does; an unreadable one is kept as None.

    ``records.status`` drops verifications that fail validation (for example after a re-import changes a cited file),
    which would let an older outcome stand in for a newer one and contradict the session page.
    """
    root = safe_path(session_dir, f"issues/{owner}/changes/{change_id}/verifications")
    if not root.is_dir() or root.is_symlink():
        return []
    found = []
    for path in sorted(root.glob("*/result.json")):
        try:
            found.append(read_json(path))
        except UserActionRequired:
            found.append(None)
    return found


def _issue_entries(session_dir, status, projects):
    """Return one summary per issue and the instants of every record that backs it."""
    changes = {(change["owner_issue"], change["id"]): change for change in status["changes"]}
    entries, stamps = [], []
    for issue in status["issues"]:
        pairs, own = [], [parse_instant(issue.get("updated_at"))]
        for link in issue["changes"]:
            change = changes.get((link["owner_issue"], link["change_id"]))
            if change is None:
                continue
            verifications = _verifications(session_dir, change["owner_issue"], change["id"])
            pairs.append((change, verifications))
            own.append(parse_instant(change.get("updated_at")))
            own.extend(parse_instant(item.get("updated_at")) for item in verifications if item)
        stamps.extend(own)
        category = issue.get("category")
        entries.append(
            {
                "id": issue["id"],
                "title": _text(issue["title"], TITLE_LIMIT),
                "linked_projects": linked_projects(session_dir.parent, issue, projects),
                "category": category if isinstance(category, str) and category else None,
                "progress": issue_progress(issue, pairs)["key"],
                "segments": len(issue.get("segments") or []),
            }
        )
    return entries, stamps


def _session_entry(session_dir, projects):
    entry = {
        "dir": session_dir.name,
        "href": f"./{quote(session_dir.name, safe='')}/{PREVIEW_NAME}",
        "session_id": session_dir.name,
        "error": None,
        "source": {},
        "issues": [],
        "problems": [],
        "problem_count": 0,
        "preview": {"exists": False, "stale": False},
    }
    try:
        status = records.status(session_dir)
    except (OSError, ValueError, RecursionError, UserActionRequired) as exc:
        # One damaged session must never hide the others. OSError text can carry local paths; only ATK's own messages
        # are safe to embed.
        entry["error"] = (
            _text(str(exc)) if isinstance(exc, UserActionRequired) else "Session records could not be read."
        )
        return entry
    session = status["session"]
    detections = [item for item in session.get("detections", []) if isinstance(item, dict)]
    issues, issue_stamps = _issue_entries(session_dir, status, projects)
    archived = parse_instant(session.get("created_at"))
    updated = _latest([archived, *issue_stamps, *(parse_instant(item.get("updated_at")) for item in detections)])
    mtime = _preview_mtime(session_dir)
    registry = session_dir.parent / "projects.json"
    if registry.is_file():
        updated = max(updated or 0, registry.stat().st_mtime)
    # The drawer is a preview: it only needs to say whether the issue list can be trusted. Scale, capture coverage,
    # focus and gap details stay on the session page, which shows every detection in full.
    last = detections[-1] if detections else None
    started, ended = _session_span(session_dir)
    entry.update(
        session_id=session["session_id"],
        source={key: _text(session.get("source", {}).get(key), ITEM_LIMIT) for key in SAFE_SOURCE_FIELDS},
        # Processing times (import, detection, diagnosis) only decide preview staleness; the page shows when the
        # session itself ran.
        started_ms=started,
        ended_ms=ended,
        detection={"status": _text(last.get("status"), 40)} if last else None,
        issues=issues,
        problems=[_problem(item) for item in status["warnings"][:LIST_LIMIT]],
        problem_count=len(status["warnings"]),
        preview={
            "exists": mtime is not None,
            "stale": mtime is None or (updated is not None and mtime + STALE_AFTER_SECONDS < updated),
        },
    )
    return entry


def build_dashboard_data(project_root):
    """Build the disposable dashboard view without writing or changing any saved record."""
    root = trace_root(project_root)
    if not root.is_dir():
        raise UserActionRequired("No Trace archive found; capture or import a session first.")
    warnings = []
    try:
        load_projects(root)
    except UserActionRequired:
        warnings.append("工程目录不可读取；关联项目暂显示记录中的 ID。")
    projects = project_map(root)
    sessions = [
        _session_entry(child, projects)
        for child in sorted(root.iterdir())
        if child.is_dir() and not child.is_symlink() and (child / "session.json").is_file()
    ]
    if not sessions:
        raise UserActionRequired("No archived sessions to summarize.")
    # Most recently run session first; sessions whose run time is unknown go last.
    sessions.sort(key=lambda item: (item.get("started_ms") is None, -(item.get("started_ms") or 0), item["dir"]))
    try:
        load_catalog(root)
    except UserActionRequired:
        warnings.append("The category vocabulary is damaged; category names fall back to their IDs.")
    return {
        "project": Path(project_root).resolve().name,
        "sessions": sessions,
        "project_index": project_index(
            root,
            ((session["dir"], issue["linked_projects"]) for session in sessions for issue in session["issues"]),
            projects,
        ),
        "categories": {
            key: {name: entry[name] for name in ("name", "definition", "deprecated")}
            for key, entry in category_map(root).items()
        },
        "progress_legend": [{"key": key, "label": label} for key, label in PROGRESS],
        "warnings": warnings,
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


def summarize_dashboard(data):
    issues = [issue for session in data["sessions"] for issue in session["issues"]]
    return {
        "sessions": len(data["sessions"]),
        "unreadable_sessions": sum(1 for session in data["sessions"] if session["error"]),
        "issues": len(issues),
        "categories": len({issue["category"] for issue in issues if issue["category"]}),
        "uncategorized": sum(1 for issue in issues if not issue["category"]),
        "progress": summarize(issue["progress"] for issue in issues),
        "projects": [{key: entry[key] for key in ("id", "name", "issues")} for entry in data.get("project_index", [])],
        "stale_previews": sum(
            1 for session in data["sessions"] if not session["error"] and session["preview"]["stale"]
        ),
    }


def generate_traces(project_root=".", *, overwrite=False, open_browser=True):
    data = build_dashboard_data(project_root)
    output = trace_root(project_root) / INDEX_NAME
    if output.is_symlink():
        raise UserActionRequired("Refusing to replace a symlink with the trace dashboard.")
    if output.exists() and not overwrite:
        raise UserActionRequired(f"Trace dashboard already exists: {output}; use --overwrite to regenerate it.")
    template = ASSETS.joinpath("page.html").read_text(encoding="utf-8")
    styles = (
        SHARED_STYLES.read_text(encoding="utf-8") + "\n" + ASSETS.joinpath("styles.css").read_text(encoding="utf-8")
    )
    page = template.replace("__STYLES__", styles)
    page = page.replace("__APP__", neutralize_script_close(ASSETS.joinpath("app.js").read_text(encoding="utf-8")))
    # Data goes in last so record text can never be mistaken for a template marker.
    page = page.replace("__DATA__", safe_json_for_html(data))
    write_text_atomic(output, page)
    opened = False
    if open_browser:
        try:
            opened, reason = open_in_browser(output)
        except OSError as exc:
            opened, reason = False, str(exc)
        if not opened:
            data["warnings"].append(f"Browser could not open the saved dashboard: {reason}")
    return {"output_path": str(output), **summarize_dashboard(data), "opened": opened, "warnings": data["warnings"]}
