"""Read a saved session and render its evidence and issue records offline."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from importlib import resources
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from zoneinfo import ZoneInfo

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.traces.categories import category_map
from agent_tune_kit.traces.progress import PROGRESS, instant, issue_progress
from agent_tune_kit.traces.projects import project_map, root_projects
from agent_tune_kit.traces.segments import resolve_segments
from agent_tune_kit.utils.files import read_text_bounded, write_text_atomic

from .render import neutralize_script_close, open_in_browser, safe_json_for_html
from .timeline import build_timeline, legacy_point_fragments, recorded_segments

ASSETS = resources.files("agent_tune_kit.visualize").joinpath("assets", "trace")
SHARED_STYLES = resources.files("agent_tune_kit.visualize").joinpath("assets", "shared", "base.css")
PROJECT_MENU = resources.files("agent_tune_kit.visualize").joinpath("assets", "shared", "project-menu.js")
RECORD_LIMIT = 1024 * 1024
# shortcut: collect plain inline destinations, use a shared parser if reference-style links are needed.
INLINE_LINK = re.compile(r"\]\((?:<([^<>\n]+)>|([^\s)]+))\)")


def _reference_key(ref):
    return json.dumps(
        [ref.get("path"), ref.get("sha256"), ref.get("pointer", ""), ref.get("line"), ref.get("bytes")],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _references(value):
    if isinstance(value, dict):
        if isinstance(value.get("path"), str) and isinstance(value.get("sha256"), str):
            yield value
        else:
            for child in value.values():
                yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


def _locate_evidence(captures, references, issues):
    """Bind validated field previews to nodes in their original snapshot."""
    files = {}
    nodes = {}
    for capture in captures:
        capture_id = capture["id"]
        for entry in capture["manifest"].get("files", []):
            relative = entry["path"] if capture.get("current") else f"captures/{capture_id}/{entry['path']}"
            files[relative] = capture_id
        for index, node in enumerate(capture["nodes"]):
            ref = node["reference"]
            nodes[(ref["path"], ref.get("line"), ref.get("pointer", ""))] = index, node

    issues_by_reference = {}
    for issue in issues:
        record = issue["record"]
        for key in {_reference_key(ref) for ref in record["evidence"]}:
            issues_by_reference.setdefault(key, []).append(record["id"])

    for key, saved in references.items():
        ref = saved["reference"]
        capture_id = files.get(ref["path"])
        if not capture_id:
            continue
        saved["capture_id"] = capture_id
        if "preview" not in saved:
            continue
        pointer = saved["preview"].get("resolved_pointer", ref.get("pointer", ""))
        ancestor = pointer
        while True:
            match = nodes.get((ref["path"], ref.get("line"), ancestor))
            if match is not None:
                index, node = match
                saved["location"] = {"capture_id": capture_id, "node_index": index, "field": pointer[len(ancestor) :]}
                for issue_id in issues_by_reference.get(key, []):
                    ids = node.setdefault("issue_ids", [])
                    if issue_id not in ids:
                        ids.append(issue_id)
                break
            if not ancestor:
                break
            # RFC 6901 escapes literal slash in keys as ~1, so trim only at a segment boundary.
            ancestor = ancestor.rpartition("/")[0]


def _record_time(item):
    record = item.get("record") if isinstance(item, dict) else None
    return instant(record if isinstance(record, dict) else {})


def build_trace_data(project_root, *, session_id, source=None):
    """Build a disposable view without writing or changing any saved conclusion."""
    from agent_tune_kit.traces import query, records, store

    session_dir = store.resolve_session(project_root, session_id, source)
    session = store.load_session(session_dir)
    warnings = []
    summaries = {"session.json": hashlib.sha256((session_dir / "session.json").read_bytes()).hexdigest()}
    references = {}

    def read_record(path):
        relative = path.relative_to(session_dir).as_posix()
        if not path.resolve().is_relative_to(session_dir.resolve()):
            raise ValueError(f"Unsafe record path: {relative}")
        text, truncated = read_text_bounded(path, RECORD_LIMIT)
        if truncated:
            raise ValueError(f"Record exceeds preview limit: {relative}")
        summaries[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            return json.loads(text)
        except RecursionError:
            raise ValueError(f"Record nesting is too deep: {relative}") from None

    def read_diagnosis(ref):
        path = store.validate_ref(session_dir, ref)
        text, truncated = read_text_bounded(path, RECORD_LIMIT)
        if truncated:
            raise ValueError(f"Record exceeds preview limit: {ref['path']}")
        summaries[ref["path"]] = ref["sha256"]
        snapshot = None
        first, _, body = text.partition("\n")
        if first.startswith("<!-- atk-issue-snapshot {") and first.rstrip("\r").endswith("} -->"):
            snapshot = json.loads(first[len("<!-- atk-issue-snapshot ") : -len(" -->")])
            text = body.lstrip("\r\n")
        return {
            "reference": ref,
            "text": text,
            **({"snapshot": snapshot} if snapshot and "project_links" in snapshot else {}),
        }

    captures = []
    if "archive" in session:
        current = {"id": session_id, "current": True, "manifest": session["archive"], "nodes": []}
        try:
            current["nodes"] = query.nodes(session_dir)
        except (OSError, ValueError, UserActionRequired) as exc:
            current["error"] = str(exc)
            warnings.append(str(exc))
        captures.append(current)
    for capture_id in session.get("captures", []):
        try:
            manifest = store.read_capture(session_dir, capture_id, verify=True)
            relative = f"captures/{capture_id}/manifest.json"
            summaries[relative] = store.evidence_ref(session_dir, relative)["sha256"]
            captures.append({"id": capture_id, "manifest": manifest, "nodes": query.nodes(session_dir, capture_id)})
        except (OSError, ValueError, UserActionRequired) as exc:
            captures.append({"id": capture_id, "manifest": {}, "nodes": [], "error": str(exc)})
            warnings.append(f"Capture {capture_id}: {exc}")

    issues = []
    for path in sorted((session_dir / "issues").glob("*/issue.json")):
        try:
            issue = read_record(path)
            records.load_issue(session_dir, path.parent.name)
            item = {
                "root_projects": root_projects(session_dir.parent, issue),
                "record": issue,
                "diagnoses": [],
                "changes": [],
            }
            for ref in issue.get("diagnoses", []):
                try:
                    item["diagnoses"].append(read_diagnosis(ref))
                except (OSError, ValueError, UserActionRequired) as exc:
                    item["diagnoses"].append({"reference": ref, "error": str(exc)})
            for ref in issue.get("changes", []):
                try:
                    change = records.load_change(session_dir, ref["owner_issue"], ref["change_id"])
                    relative = f"issues/{ref['owner_issue']}/changes/{ref['change_id']}/change.json"
                    if ref["path"] != relative:
                        raise ValueError("Shared change locator conflicts with its owning issue.")
                    change_ref = store.evidence_ref(session_dir, relative)
                    changed = {"reference": change_ref, "record": change, "verifications": []}
                    summaries[relative] = change_ref["sha256"]
                    change_path = store.validate_ref(session_dir, change_ref)
                    for verification_path in sorted((change_path.parent / "verifications").glob("*/result.json")):
                        try:
                            verification = read_record(verification_path)
                            verification_ref = store.evidence_ref(
                                session_dir, verification_path.relative_to(session_dir).as_posix()
                            )
                            changed["verifications"].append({"reference": verification_ref, "record": verification})
                        except (OSError, ValueError, UserActionRequired) as exc:
                            changed["verifications"].append(
                                {
                                    "reference": {"path": verification_path.relative_to(session_dir).as_posix()},
                                    "error": str(exc),
                                }
                            )
                    changed["verifications"].sort(key=_record_time)
                    item["changes"].append(changed)
                except (OSError, ValueError, UserActionRequired, KeyError, TypeError) as exc:
                    item["changes"].append({"reference": ref, "error": str(exc)})
            item["changes"].sort(key=_record_time)
            item["progress"] = issue_progress(
                issue,
                [
                    # An unreadable verification stays in the list as None so it cannot hide behind an older result.
                    (change["record"], [entry.get("record") for entry in change["verifications"]])
                    for change in item["changes"]
                    if "record" in change
                ],
            )
            issues.append(item)
        except (OSError, ValueError, UserActionRequired) as exc:
            warnings.append(f"Issue {path.parent.name}: {exc}")

    def all_issue_refs(item):
        record = item["record"]
        refs = list(record.get("evidence", []))
        for segment in record.get("segments", []):
            refs.extend(segment.get("evidence", []))
            scope = segment.get("scope", {})
            if "node" in scope:
                refs.append(scope["node"])
            else:
                refs.extend([scope.get("from"), scope.get("to")])
        return [ref for ref in refs if isinstance(ref, dict)]

    for ref in _references([session["detections"], [all_issue_refs(item) for item in issues]]):
        key = _reference_key(ref)
        if key in references:
            continue
        try:
            references[key] = {"reference": ref, "preview": query.show(session_dir, ref, limit=8000)}
        except (OSError, ValueError, UserActionRequired) as exc:
            references[key] = {"reference": ref, "error": str(exc)}
            warnings.append(f"Reference {ref['path']}: {exc}")

    # Attach issue ids to nodes from both top-level and segment evidence.
    for item in issues:
        record = item["record"]
        record_evidence = list(record.get("evidence", []))
        for segment in record.get("segments", []):
            record_evidence.extend(segment.get("evidence", []))
        item["_evidence_for_locate"] = record_evidence
    located_issues = [{"record": {**item["record"], "evidence": item["_evidence_for_locate"]}} for item in issues]
    _locate_evidence(captures, references, located_issues)
    for item in issues:
        item.pop("_evidence_for_locate", None)
    captures_by_id = {capture["id"]: capture for capture in captures}
    for capture in captures:
        build_timeline(capture)
    for item in issues:
        record = item["record"]
        display_refs = list(record.get("evidence", []))
        for segment in record.get("segments", []):
            display_refs.extend(segment.get("evidence", []))
        evidence = [references[_reference_key(ref)] for ref in display_refs if _reference_key(ref) in references]
        # Fragment refs index into this combined list (top-level + segment evidence).
        item["evidence"] = evidence
        segments = record.get("segments") or []
        if segments:
            try:
                resolved = resolve_segments(session_dir, segments)
                item["fragments"] = recorded_segments(captures_by_id, record, resolved, evidence, _reference_key)
            except (OSError, ValueError, UserActionRequired) as exc:
                warnings.append(f"Issue {record['id']} segments: {exc}")
                item["fragments"] = legacy_point_fragments(captures_by_id, evidence)
                for fragment in item["fragments"]:
                    fragment["unscoped"] = fragment.get("kind") in {"point", "untimed", "trace"}
        else:
            item["fragments"] = legacy_point_fragments(captures_by_id, evidence)
            for fragment in item["fragments"]:
                if fragment.get("kind") in {"point", "untimed", "trace"}:
                    fragment["unscoped"] = True

    project = ""
    project_path = session_dir.parent / "project.md"
    if project_path.exists():
        if project_path.resolve().is_relative_to(session_dir.parent.resolve()):
            project, truncated = read_text_bounded(project_path, 32 * 1024)
            if truncated:
                warnings.append("Project context preview is truncated at 32 KiB; read project.md for the full text.")
        else:
            warnings.append("Project context is unavailable: unsafe project.md path.")

    # Current configuration is deliberately omitted: it can contain credential references.
    safe_source = {key: session.get("source", {}).get(key) for key in ("type", "instance", "project", "environment")}
    return session_dir, {
        "session": session,
        "source": safe_source,
        "project": project,
        "captures": captures,
        "issues": issues,
        "references": references,
        "record_digests": summaries,
        "warnings": warnings,
        "projects": project_map(session_dir.parent),
        "categories": {
            key: {name: entry[name] for name in ("name", "definition", "deprecated")}
            for key, entry in category_map(session_dir.parent).items()
        },
        "progress_legend": [{"key": key, "label": label} for key, label in PROGRESS],
        # generate_trace() enables the dashboard link only for the default preview beside an existing index.html.
        "dashboard": {"available": False, "session": session_dir.name},
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


def _source_links(issues, project_root, output):
    root = Path(project_root).resolve()
    links = {}
    destinations = []
    for item in issues:
        for diagnosis in item["diagnoses"]:
            # The renderer uses markdown-it to decide which destinations actually become links.
            destinations.extend(match[1] or match[2] for match in INLINE_LINK.finditer(diagnosis.get("text", "")))
        for change in item["changes"]:
            summary = change.get("record", {}).get("change_summary", [])
            if isinstance(summary, list):
                for entry in summary:
                    files = entry.get("files", []) if isinstance(entry, dict) else []
                    if isinstance(files, list):
                        destinations.extend(path for path in files if isinstance(path, str))
    for destination in destinations:
        try:
            url = urlsplit(destination)
            if url.scheme or url.netloc or url.query or not url.path:
                continue
            path = unquote(url.path)
            line = url.fragment
            if path.startswith("/") and not line:
                legacy = re.fullmatch(r"(.+):([1-9][0-9]*)", path)
                if legacy:
                    path, line = legacy[1], f"L{legacy[2]}"
            if line and not re.fullmatch(r"L[1-9][0-9]*", line):
                continue
            candidate = root / path
            original = candidate.relative_to(root)
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root)
            if any(part.startswith(".") for part in (*original.parts, *relative.parts)):
                continue
            if not resolved.is_file():
                continue
        except (OSError, RuntimeError, ValueError):
            continue
        try:
            target = quote(Path(os.path.relpath(resolved, output.parent.resolve())).as_posix(), safe="/")
        except ValueError:
            target = resolved.as_uri()
        links[destination] = target + (f"#{line}" if line else "")
    return links


def generate_trace(project_root=".", *, session_id, source=None, output_path=None, overwrite=False, open_browser=True):
    session_dir, data = build_trace_data(project_root, session_id=session_id, source=source)
    output = Path(output_path).expanduser().absolute() if output_path else session_dir / "preview.html"
    if output.suffix.lower() != ".html":
        raise UserActionRequired("Trace preview output must have an .html extension.")
    if output.is_symlink():
        raise UserActionRequired("Refusing to replace a symlink with a trace preview.")
    if (
        output.resolve().is_relative_to(session_dir.parent.resolve())
        and output.resolve() != session_dir / "preview.html"
    ):
        raise UserActionRequired(
            "Inside .atk/traces, write only this session's preview.html to protect saved evidence."
        )
    if output.exists() and not overwrite:
        raise UserActionRequired(f"Trace preview already exists: {output}; use --overwrite to regenerate it.")
    data["source_links"] = _source_links(data["issues"], project_root, output)
    data["dashboard"]["available"] = (
        output.resolve() == (session_dir / "preview.html").resolve() and (session_dir.parent / "index.html").is_file()
    )
    template = ASSETS.joinpath("page.html").read_text(encoding="utf-8")
    styles = (
        SHARED_STYLES.read_text(encoding="utf-8") + "\n" + ASSETS.joinpath("styles.css").read_text(encoding="utf-8")
    )
    page = template.replace("__STYLES__", styles)
    page = page.replace("__LANES__", neutralize_script_close(ASSETS.joinpath("lanes.js").read_text(encoding="utf-8")))
    vendor = ASSETS.joinpath("vendor")
    notice = vendor.joinpath("markdown-it.LICENSE").read_text(encoding="utf-8")
    markdown = vendor.joinpath("markdown-it.min.js").read_text(encoding="utf-8")
    page = page.replace("__MARKDOWN__", neutralize_script_close(f"/*\n{notice}\n*/\n{markdown}"))
    scripts = PROJECT_MENU.read_text(encoding="utf-8") + "\n" + ASSETS.joinpath("app.js").read_text(encoding="utf-8")
    page = page.replace("__APP__", neutralize_script_close(scripts))
    page = page.replace("__DATA__", safe_json_for_html(data))
    write_text_atomic(output, page)
    opened = False
    if open_browser:
        try:
            opened, reason = open_in_browser(output)
        except OSError as exc:
            opened, reason = False, str(exc)
        if not opened:
            data["warnings"].append(f"Browser could not open the saved preview: {reason}")
    return {
        "output_path": str(output),
        "session_id": session_id,
        "traces": len(
            {node["trace_id"] for view in data["captures"] for node in view["nodes"] if node.get("trace_id")}
        ),
        "issues": len(data["issues"]),
        "opened": opened,
        "warnings": data["warnings"],
    }
