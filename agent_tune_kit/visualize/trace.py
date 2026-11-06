"""Read a saved session and render its evidence and issue records offline."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.utils.files import read_text_bounded, write_text_atomic

from .render import neutralize_script_close, open_in_browser, safe_json_for_html
from .timeline import build_timeline, issue_fragments

ASSETS = resources.files("agent_tune_kit.visualize").joinpath("assets", "trace")
RECORD_LIMIT = 1024 * 1024


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
            files[(f"captures/{capture_id}/{entry['path']}", entry["sha256"])] = capture_id
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
        capture_id = files.get((ref["path"], ref["sha256"]))
        if not capture_id:
            continue
        saved["capture_id"] = capture_id
        if "preview" not in saved:
            continue
        pointer = ref.get("pointer", "")
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
        return json.loads(text)

    def read_ref(ref, *, markdown=False):
        path = store.validate_ref(session_dir, ref)
        text, truncated = read_text_bounded(path, RECORD_LIMIT)
        if truncated:
            raise ValueError(f"Record exceeds preview limit: {ref['path']}")
        summaries[ref["path"]] = ref["sha256"]
        return {"reference": ref, "text": text} if markdown else json.loads(text)

    captures = []
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
            item = {"record": issue, "diagnoses": [], "changes": []}
            for ref in issue.get("diagnoses", []):
                try:
                    item["diagnoses"].append(read_ref(ref, markdown=True))
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
                    item["changes"].append(changed)
                except (OSError, ValueError, UserActionRequired, KeyError, TypeError) as exc:
                    item["changes"].append({"reference": ref, "error": str(exc)})
            issues.append(item)
        except (OSError, ValueError, UserActionRequired) as exc:
            warnings.append(f"Issue {path.parent.name}: {exc}")

    for ref in _references([session, issues]):
        key = _reference_key(ref)
        if key in references:
            continue
        try:
            references[key] = {"reference": ref, "preview": query.show(session_dir, ref, limit=8000)}
        except (OSError, ValueError, UserActionRequired) as exc:
            references[key] = {"reference": ref, "error": str(exc)}
            warnings.append(f"Reference {ref['path']}: {exc}")

    _locate_evidence(captures, references, issues)
    captures_by_id = {capture["id"]: capture for capture in captures}
    windows = {capture["id"]: build_timeline(capture) for capture in captures}
    for item in issues:
        evidence = [references[_reference_key(ref)] for ref in item["record"]["evidence"]]
        item["fragments"] = issue_fragments(captures_by_id, windows, evidence)

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
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


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
    template = ASSETS.joinpath("page.html").read_text(encoding="utf-8")
    page = template.replace("__STYLES__", ASSETS.joinpath("styles.css").read_text(encoding="utf-8"))
    page = page.replace("__LANES__", neutralize_script_close(ASSETS.joinpath("lanes.js").read_text(encoding="utf-8")))
    page = page.replace("__APP__", neutralize_script_close(ASSETS.joinpath("app.js").read_text(encoding="utf-8")))
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
        "captures": len(data["captures"]),
        "issues": len(data["issues"]),
        "opened": opened,
        "warnings": data["warnings"],
    }
