"""Issue-scoped records; semantic judgments remain the host Agent's responsibility."""

import copy
import uuid
from functools import wraps
from pathlib import Path

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.evidence import json_bytes, now, safe_path, write_manifest
from agent_tune_kit.utils.files import write_text_atomic

from .segments import resolve_segments, segments_for_storage
from .store import (
    STATES,
    evidence_ref,
    identifier,
    load_session,
    read_capture,
    read_json,
    resolve_ref,
    session_write_lock,
    validate_ref,
)

ISSUE_STATES = {"open", "investigating", "diagnosed", "changing", "verifying", "resolved", "excluded", "blocked"}


def _locked_write(function):
    @wraps(function)
    def write(session_dir, *args, **kwargs):
        with session_write_lock(session_dir):
            return function(session_dir, *args, **kwargs)

    return write


def _id(value, prefix):
    return identifier(value or f"{prefix}-{uuid.uuid4().hex[:16]}")


def _issue_path(session_dir, issue_id):
    return safe_path(Path(session_dir), f"issues/{identifier(issue_id, 'issue')}/issue.json")


def _reference_shape(ref):
    return (
        isinstance(ref, dict)
        and isinstance(ref.get("path"), str)
        and isinstance(ref.get("sha256"), str)
        and isinstance(ref.get("bytes"), int)
    )


def load_issue(session_dir, issue_id):
    load_session(session_dir)
    issue = read_json(_issue_path(session_dir, issue_id))
    if (
        issue.get("schema_version") != 1
        or issue.get("id") != issue_id
        or any(not isinstance(issue.get(key), list) for key in ("evidence", "diagnoses", "changes"))
        or not isinstance(issue.get("revision"), int)
        or isinstance(issue.get("revision"), bool)
        or issue["revision"] < 1
        or any(not isinstance(issue.get(key), str) for key in ("title", "description", "created_at"))
        or any(not isinstance(issue.get(key), list) for key in ("notes", "history"))
        or issue.get("status") not in ISSUE_STATES
    ):
        raise UserActionRequired("Invalid issue identity or record structure.")
    segments = issue.get("segments", [])
    if "segments" in issue and not isinstance(segments, list):
        raise UserActionRequired("Invalid issue segment structure.")
    for segment in segments:
        if (
            not isinstance(segment, dict)
            or not isinstance(segment.get("id"), str)
            or not segment["id"].strip()
            or not isinstance(segment.get("summary"), str)
            or not segment["summary"].strip()
            or not isinstance(segment.get("scope"), dict)
            or not isinstance(segment.get("evidence"), list)
            or not segment["evidence"]
        ):
            raise UserActionRequired("Invalid issue segment structure.")
        for ref in segment["evidence"]:
            if not _reference_shape(ref):
                raise UserActionRequired("Invalid issue evidence reference structure.")
        scope = segment["scope"]
        if "node" in scope:
            if set(scope) != {"node"} or not _reference_shape(scope["node"]):
                raise UserActionRequired("Invalid issue segment scope.")
        elif set(scope) == {"from", "to"}:
            if not _reference_shape(scope["from"]) or not _reference_shape(scope["to"]):
                raise UserActionRequired("Invalid issue segment scope.")
        else:
            raise UserActionRequired("Invalid issue segment scope.")
    for ref in issue["evidence"] + issue["diagnoses"]:
        if not _reference_shape(ref):
            raise UserActionRequired("Invalid issue evidence reference structure.")
    for link in issue["changes"]:
        if not isinstance(link, dict) or any(
            not isinstance(link.get(key), str) for key in ("path", "owner_issue", "change_id")
        ):
            raise UserActionRequired("Invalid issue change link structure.")
    return issue


def _revision(existing, data):
    expected = data.get("revision", data.get("expected_revision"))
    if expected != existing["revision"]:
        raise UserActionRequired(f"Stale or missing revision; current revision is {existing['revision']}.")


def _refs(session_dir, references):
    if not isinstance(references, list):
        raise UserActionRequired("Evidence must be a list of retained file references.")
    for ref in references:
        validate_ref(session_dir, ref)
    return references


def _record_result(session_dir, path, record):
    return {**record, "path": str(path), "ref": evidence_ref(session_dir, path.relative_to(session_dir).as_posix())}


def _enrich_refs(directory, references):
    return [
        {**ref, **evidence_ref(directory, ref["path"], resolve_ref(directory, ref)[1])}
        if ref["path"].startswith("raw/") and "pointer" in ref
        else ref
        for ref in references
    ]


def prepare_issue(session_dir, data, *, dry_run=False):
    """Validate issue fields and segments; optionally skip writing."""
    directory = Path(session_dir)
    load_session(directory)
    data = copy.deepcopy(data)
    if not isinstance(data, dict):
        raise UserActionRequired("Issue data must be an object.")
    issue_id = data.get("id", data.get("issue_id"))
    if issue_id is None:
        number = 1
        while _issue_path(directory, f"ISSUE-{number:03}").exists():
            number += 1
        issue_id = f"ISSUE-{number:03}"
    path = _issue_path(directory, issue_id)
    previous = load_issue(directory, issue_id) if path.exists() else None
    if previous:
        _revision(previous, data)
    for key in ("diagnoses", "changes", "history", "schema_version", "created_at"):
        if key in data and (not previous or data[key] != previous.get(key)):
            raise UserActionRequired(f"Issue {key} is managed by its dedicated record operation.")
    issue = (
        copy.deepcopy(previous)
        if previous
        else {
            "schema_version": 1,
            "id": issue_id,
            "evidence": [],
            "diagnoses": [],
            "changes": [],
            "notes": [],
            "history": [],
            "created_at": now(),
            "status": "open",
            "segments": [],
        }
    )
    for field in ("title", "description", "evidence", "status", "notes", "selection_reason", "segments"):
        if field in data:
            issue[field] = data[field]
    if not previous:
        issue.setdefault("segments", [])
    if (
        not isinstance(issue.get("title"), str)
        or not issue["title"].strip()
        or not isinstance(issue.get("description"), str)
        or not issue["description"].strip()
    ):
        raise UserActionRequired("Issue title and description are required.")
    if not isinstance(issue.get("evidence", []), list):
        raise UserActionRequired("Issue evidence must be a list of retained file references.")
    _refs(directory, issue.get("evidence", []))
    if "evidence" in data:
        issue["evidence"] = _enrich_refs(directory, issue["evidence"])
    resolved_segments = []
    if "segments" in issue:
        if not isinstance(issue["segments"], list):
            raise UserActionRequired("Issue segments must be a list.")
        source_segments = issue["segments"]
        # Validate current segments whenever the field is present, including empty replacements.
        resolved_segments = resolve_segments(directory, source_segments)
        stored = segments_for_storage(resolved_segments)
        for segment in stored:
            segment["evidence"] = _enrich_refs(directory, segment["evidence"])
            scope = segment["scope"]
            if "node" in scope:
                scope["node"] = _enrich_refs(directory, [scope["node"]])[0]
            else:
                scope["from"] = _enrich_refs(directory, [scope["from"]])[0]
                scope["to"] = _enrich_refs(directory, [scope["to"]])[0]
        issue["segments"] = stored
        resolved_segments = resolve_segments(directory, stored)
    if not issue.get("evidence") and not issue.get("segments"):
        raise UserActionRequired("An issue needs retained evidence references or segments.")
    if issue["status"] not in ISSUE_STATES or not isinstance(issue["notes"], list):
        raise UserActionRequired("Invalid issue status or notes.")
    if previous and not dry_run:
        issue["history"].append(
            {
                key: previous.get(key)
                for key in (
                    "revision",
                    "updated_at",
                    "title",
                    "description",
                    "evidence",
                    "segments",
                    "status",
                    "notes",
                )
            }
        )
    issue["revision"] = previous["revision"] + 1 if previous else 1
    issue["updated_at"] = now()
    return path, issue, resolved_segments, previous


def _segment_coverage(resolved_segments):
    return [
        {
            "id": item["id"],
            "summary": item["summary"],
            "kind": item["kind"],
            "trace_id": item["coverage"]["trace_id"],
            "node_count": item["coverage"]["node_count"],
            "duration_ms": item["coverage"]["duration_ms"],
            "start_time": item["coverage"]["start_time"],
            "end_time": item["coverage"]["end_time"],
            "node_indexes": item["coverage"]["node_indexes"],
        }
        for item in resolved_segments
    ]


@_locked_write
def save_issue(session_dir, data, *, dry_run=False):
    path, issue, resolved_segments, previous = prepare_issue(session_dir, data, dry_run=dry_run)
    directory = Path(session_dir)
    coverage = _segment_coverage(resolved_segments)
    if dry_run:
        return {
            **issue,
            "path": str(path),
            "ref": None,
            "segment_coverage": coverage,
            "dry_run": True,
            "would_revise": previous["revision"] + 1 if previous else 1,
        }
    write_manifest(path, issue)
    return {**_record_result(directory, path, issue), "segment_coverage": coverage, "dry_run": False}


@_locked_write
def save_detection(session_dir, data):
    directory = Path(session_dir)
    session = load_session(directory)
    if not isinstance(data, dict):
        raise UserActionRequired("Detection data must be an object.")
    record = copy.deepcopy(data)
    record["id"] = _id(record.get("id"), "DET")
    previous = next((item for item in session["detections"] if item["id"] == record["id"]), None)
    if previous:
        _revision(previous, record)
        if previous["status"] != "running":
            raise UserActionRequired("Finished detection coverage is immutable; start a new detection.")
        record = {**previous, **record}
    if record.get("status") not in STATES:
        raise UserActionRequired("Detection requires an explicit execution status.")
    if not isinstance(record.get("scope"), (dict, str)) or not record["scope"]:
        raise UserActionRequired("Detection must describe its actual coverage scope.")
    if session["schema_version"] == 2:
        input_refs = session["archive"]["files"]
        if not input_refs:
            raise UserActionRequired("Detection requires retained trace material.")
        _refs(directory, input_refs)
    else:
        captures = record.get("captures")
        if not isinstance(captures, list) or not captures:
            raise UserActionRequired("Detection must bind at least one captured input.")
        input_refs = []
        for capture_id in captures:
            if not read_capture(directory, capture_id)["sealed"]:
                raise UserActionRequired("Detection needs a stable, sealed capture; finish or interrupt capture first.")
            input_refs.append(evidence_ref(directory, f"captures/{capture_id}/manifest.json"))
    if previous and previous["inputs"] != input_refs:
        raise UserActionRequired("Detection input changed; create a new detection record.")
    record.update(
        inputs=input_refs,
        revision=previous["revision"] + 1 if previous else 1,
        started_at=previous["started_at"] if previous else now(),
        updated_at=now(),
    )
    record.pop("expected_revision", None)
    if previous:
        session["detections"][session["detections"].index(previous)] = record
    else:
        session["detections"].append(record)
    session["revision"] += 1
    write_manifest(safe_path(directory, "session.json"), session)
    return record


@_locked_write
def save_diagnosis(session_dir, issue_id, text, diagnosis_id=None):
    directory = Path(session_dir)
    issue = load_issue(directory, issue_id)
    if not isinstance(text, str) or not text.strip():
        raise UserActionRequired("Diagnosis must contain an explanation and a plan.")
    diagnosis_id = _id(diagnosis_id, "DIA")
    path = safe_path(directory, f"issues/{issue_id}/diagnoses/{diagnosis_id}.md")
    if path.exists():
        raise UserActionRequired("Diagnosis already exists; preserve it and save a new revision.")
    snapshot = {
        "issue_id": issue_id,
        "issue_revision": issue["revision"],
        "title": issue["title"],
        "description": issue["description"],
        "evidence": issue["evidence"],
        "segments": issue.get("segments", []),
        "created_at": now(),
    }
    _refs(directory, issue["evidence"])
    for segment in snapshot["segments"]:
        _refs(directory, segment["evidence"])
        scope = segment["scope"]
        _refs(directory, [scope["node"]] if "node" in scope else [scope["from"], scope["to"]])
    # Keep adopted problem descriptions recoverable after the live issue changes.
    content = "<!-- atk-issue-snapshot " + json_bytes(snapshot).decode().replace("-->", "--\\u003e") + " -->\n\n" + text
    write_text_atomic(path, content)
    ref = evidence_ref(directory, path.relative_to(directory).as_posix())
    issue["diagnoses"].append(ref)
    issue["revision"] += 1
    issue["updated_at"] = now()
    write_manifest(_issue_path(directory, issue_id), issue)
    return {
        "id": diagnosis_id,
        "issue_id": issue_id,
        "path": str(path),
        "ref": ref,
        "issue_revision": issue["revision"],
    }


def _change_path(session_dir, issue_id, change_id):
    return safe_path(Path(session_dir), f"issues/{identifier(issue_id)}/changes/{identifier(change_id)}/change.json")


def load_change(session_dir, issue_id, change_id):
    load_issue(session_dir, issue_id)
    change = read_json(_change_path(session_dir, issue_id, change_id))
    if (
        change.get("id") != change_id
        or change.get("owner_issue") != issue_id
        or change.get("schema_version") != 1
        or not isinstance(change.get("diagnoses"), dict)
        or not isinstance(change.get("target_issues"), list)
        or not isinstance(change.get("revision"), int)
        or isinstance(change.get("revision"), bool)
        or change["revision"] < 1
        or change.get("status") not in STATES
        or not isinstance(change.get("before"), dict)
        or not isinstance(change.get("created_at"), str)
        or not isinstance(change.get("verifications"), list)
    ):
        raise UserActionRequired("Invalid change identity or record structure.")
    targets = [identifier(item, "issue") for item in change["target_issues"]]
    if issue_id not in targets or len(set(targets)) != len(targets) or set(change["diagnoses"]) != set(targets):
        raise UserActionRequired("Invalid change targets or adopted diagnoses.")
    if change["status"] == "completed" and (
        not isinstance(change.get("candidate"), dict) or not change["candidate"] or not change.get("diff")
    ):
        raise UserActionRequired("Completed change has no fixed candidate or actual diff.")
    for ref in change["diagnoses"].values():
        validate_ref(session_dir, ref)
    return change


def _sync_change_links(session_dir, owner, change):
    relative = f"issues/{owner}/changes/{change['id']}/change.json"
    # Mutable links locate records; adopted evidence references always bind bytes.
    link = {"path": relative, "owner_issue": owner, "change_id": change["id"]}
    for issue_id in change["target_issues"]:
        issue = load_issue(session_dir, issue_id)
        if not any(item["path"] == relative for item in issue["changes"]):
            issue["changes"].append(link)
            issue["revision"] += 1
            issue["updated_at"] = now()
            write_manifest(_issue_path(session_dir, issue_id), issue)


@_locked_write
def save_change(session_dir, issue_id, data):
    directory = Path(session_dir)
    load_issue(directory, issue_id)
    if not isinstance(data, dict):
        raise UserActionRequired("Change data must be an object.")
    data = copy.deepcopy(data)
    change_id = _id(data.get("id", data.get("change_id")), "CHG")
    path = _change_path(directory, issue_id, change_id)
    previous = load_change(directory, issue_id, change_id) if path.exists() else None
    if previous:
        _revision(previous, data)
        if previous["status"] != "running":
            raise UserActionRequired("Finished candidate is immutable; create a new change.")
        for field in ("diagnoses", "target_issues", "before", "owner_issue"):
            if field in data and data[field] != previous.get(field):
                raise UserActionRequired("Adopted diagnosis and pre-change state cannot be rewritten.")
    change = {**previous, **data} if previous else data
    targets = change.setdefault("target_issues", [issue_id])
    if not isinstance(targets, list) or issue_id not in targets or len(set(targets)) != len(targets):
        raise UserActionRequired("Change targets must include its owning issue without duplicates.")
    issues = {target: load_issue(directory, target) for target in targets}
    diagnoses = change.get("diagnoses")
    if diagnoses is None:
        if any(not issue["diagnoses"] for issue in issues.values()):
            raise UserActionRequired("Every target issue needs an adopted diagnosis.")
        diagnoses = {target: issue["diagnoses"][-1] for target, issue in issues.items()}
    if not isinstance(diagnoses, dict) or set(diagnoses) != set(targets):
        raise UserActionRequired("Adopted diagnoses must bind every target issue.")
    for target, ref in diagnoses.items():
        validate_ref(directory, ref)
        if ref not in issues[target]["diagnoses"]:
            raise UserActionRequired("Adopted diagnosis does not belong to its target issue.")
    if change.get("status") not in STATES or not isinstance(change.get("before"), dict) or not change["before"]:
        raise UserActionRequired("Change requires execution status and the actual pre-change state.")
    if change["status"] == "completed" and (
        not isinstance(change.get("candidate"), dict) or not change["candidate"] or not change.get("diff")
    ):
        raise UserActionRequired("A completed change needs actual candidate identity and a recorded diff.")
    if not previous and change["status"] != "running":
        raise UserActionRequired("Save a running change before applying the implementation.")
    for field in ("verifications", "schema_version", "owner_issue", "created_at"):
        if field in data and (not previous or data[field] != previous.get(field)):
            raise UserActionRequired(f"Change {field} is managed by its dedicated operation.")
    change.update(
        schema_version=1,
        id=change_id,
        owner_issue=issue_id,
        diagnoses=diagnoses,
        revision=previous["revision"] + 1 if previous else 1,
        created_at=previous["created_at"] if previous else now(),
        updated_at=now(),
    )
    change.setdefault("verifications", [])
    change.pop("expected_revision", None)
    write_manifest(path, change)
    _sync_change_links(directory, issue_id, change)
    return _record_result(directory, path, change)


@_locked_write
def save_verification(session_dir, issue_id, change_id, data):
    directory = Path(session_dir)
    change = load_change(directory, issue_id, change_id)
    if change["status"] != "completed":
        raise UserActionRequired("Verification requires a completed, fixed candidate.")
    if not isinstance(data, dict):
        raise UserActionRequired("Verification data must be an object.")
    data = copy.deepcopy(data)
    verification_id = _id(data.get("id", data.get("verification_id")), "VER")
    path = safe_path(directory, f"issues/{issue_id}/changes/{change_id}/verifications/{verification_id}/result.json")
    previous = load_verification(directory, issue_id, change_id, verification_id) if path.exists() else None
    if previous:
        _revision(previous, data)
        if previous["status"] != "running":
            raise UserActionRequired("Verification results are immutable; save another execution or assessment.")
    result = {**previous, **data} if previous else data
    if not previous and result.get("status") != "running":
        raise UserActionRequired("Save a running verification before executing it.")
    if result.get("status") not in STATES or not isinstance(result.get("execution"), dict):
        raise UserActionRequired("Verification requires explicit status and execution details.")
    if result.get("candidate", change["candidate"]) != change["candidate"]:
        raise UserActionRequired("Verification cannot substitute a different candidate.")
    for ref in change["diagnoses"].values():
        validate_ref(directory, ref)
    _refs(directory, result.get("evidence", []))
    loading = result.get("loading_evidence", [])
    if not isinstance(loading, list):
        raise UserActionRequired("Loading evidence must be a list of references or concrete loading observations.")
    for ref in loading:
        if isinstance(ref, dict):
            validate_ref(directory, ref)
        elif not isinstance(ref, str) or not ref.strip():
            raise UserActionRequired("Invalid candidate loading evidence.")
    conclusions = result.get("conclusions", {})
    if not isinstance(conclusions, dict) or set(conclusions) - set(change["target_issues"]):
        raise UserActionRequired("Verification conclusions must refer to the change's target issues.")
    if result["status"] != "running" and set(conclusions) != set(change["target_issues"]):
        raise UserActionRequired("Finished verification needs a separate conclusion for every target issue.")
    for conclusion in conclusions.values():
        if (
            not isinstance(conclusion, dict)
            or conclusion.get("outcome") not in {"pass", "fail", "inconclusive"}
            or not conclusion.get("reason")
        ):
            raise UserActionRequired("Each effect conclusion requires an outcome and reason.")
        if conclusion["outcome"] != "inconclusive" and (result.get("candidate_loaded") is not True or not loading):
            raise UserActionRequired("Unverified candidate loading requires an inconclusive effect judgment.")
    if result["status"] == "completed" and not result["execution"]:
        raise UserActionRequired("Completed verification needs actual execution details.")
    result.update(
        change=evidence_ref(directory, _change_path(directory, issue_id, change_id).relative_to(directory).as_posix()),
        schema_version=1,
        id=verification_id,
        owner_issue=issue_id,
        change_id=change_id,
        candidate=change["candidate"],
        revision=previous["revision"] + 1 if previous else 1,
        created_at=previous["created_at"] if previous else now(),
        updated_at=now(),
    )
    result.pop("expected_revision", None)
    write_manifest(path, result)
    return _record_result(directory, path, result)


def load_verification(session_dir, issue_id, change_id, verification_id):
    directory = Path(session_dir)
    change = load_change(directory, issue_id, change_id)
    path = safe_path(
        directory, f"issues/{issue_id}/changes/{change_id}/verifications/{identifier(verification_id)}/result.json"
    )
    result = read_json(path)
    if (
        result.get("schema_version") != 1
        or result.get("id") != verification_id
        or result.get("owner_issue") != issue_id
        or result.get("change_id") != change_id
        or result.get("candidate") != change.get("candidate")
        or result.get("status") not in STATES
        or not isinstance(result.get("execution"), dict)
        or not isinstance(result.get("conclusions", {}), dict)
        or not isinstance(result.get("loading_evidence", []), list)
        or not isinstance(result.get("revision"), int)
        or isinstance(result.get("revision"), bool)
        or result["revision"] < 1
        or not isinstance(result.get("created_at"), str)
    ):
        raise UserActionRequired("Invalid verification identity, candidate or record structure.")
    validate_ref(directory, result.get("change"))
    _refs(directory, result.get("evidence", []))
    for ref in result.get("loading_evidence", []):
        if isinstance(ref, dict):
            validate_ref(directory, ref)
    return result


def status(session_dir):
    directory = Path(session_dir)
    session = load_session(directory)
    result = {
        "session": session,
        "session_dir": str(directory),
        **({"archive": session["archive"]} if "archive" in session else {"captures": []}),
        "issues": [],
        "changes": [],
        "warnings": [],
    }
    for entry in session.get("archive", {}).get("files", []):
        try:
            validate_ref(directory, entry)
        except UserActionRequired as exc:
            result["warnings"].append({"path": entry["path"], "error": str(exc)})
    for capture_id in session.get("captures", []):
        try:
            result["captures"].append(read_capture(directory, capture_id))
        except UserActionRequired as exc:
            result["warnings"].append({"capture_id": capture_id, "error": str(exc)})
    issue_root = safe_path(directory, "issues")
    links = {}
    if issue_root.exists():
        for path in sorted(issue_root.iterdir()):
            if not path.is_dir():
                continue
            try:
                issue = load_issue(directory, path.name)
            except UserActionRequired as exc:
                result["warnings"].append({"issue_id": path.name, "error": str(exc)})
                continue
            result["issues"].append(issue)
            for ref in issue["evidence"] + issue["diagnoses"]:
                try:
                    validate_ref(directory, ref)
                except UserActionRequired as exc:
                    result["warnings"].append({"issue_id": path.name, "error": str(exc)})
            for link in issue["changes"]:
                try:
                    owner, change_id = identifier(link["owner_issue"]), identifier(link["change_id"])
                    expected = f"issues/{owner}/changes/{change_id}/change.json"
                    if link.get("path") != expected:
                        raise UserActionRequired("Change link does not match its owner and ID.")
                    links[(owner, change_id)] = link
                except (KeyError, TypeError, UserActionRequired):
                    result["warnings"].append({"issue_id": path.name, "error": "Invalid change link."})
    for owner, change_id in links:
        try:
            change = load_change(directory, owner, change_id)
            change = {**change, "verification_records": []}
            result["changes"].append(change)
            verification_root = safe_path(directory, f"issues/{owner}/changes/{change_id}/verifications")
            if verification_root.exists():
                for path in sorted(verification_root.iterdir()):
                    if not path.is_dir():
                        continue
                    try:
                        change["verification_records"].append(load_verification(directory, owner, change_id, path.name))
                    except UserActionRequired as exc:
                        result["warnings"].append(
                            {"change_id": change_id, "verification_id": path.name, "error": str(exc)}
                        )
        except UserActionRequired as exc:
            result["warnings"].append({"owner_issue": owner, "change_id": change_id, "error": str(exc)})
    return result
