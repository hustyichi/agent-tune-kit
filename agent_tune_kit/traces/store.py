"""Session identity, current field references and legacy capture compatibility."""

import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.evidence import digest_file, json_bytes, now, safe_path, write_manifest
from agent_tune_kit.utils.files import write_text_atomic

from . import archive

SOURCE_FIELDS = ("type", "instance", "project", "environment")
STATES = {"running", "completed", "partial", "failed", "blocked", "interrupted"}
DEFAULT_PROJECT_TEXT = "# Trace project\n\nProject and execution entry points: unknown.\n"


@contextmanager
def session_write_lock(session_dir):
    """OS-owned locks are released on process exit, including interrupted writes."""
    directory = Path(session_dir)
    lock_path = safe_path(directory, ".write.lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    except (BlockingIOError, PermissionError):
        if locked:
            raise
        raise UserActionRequired("Another Trace write is in progress; reread the record before retrying.") from None
    finally:
        os.close(fd)


def identifier(value, kind="record"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise UserActionRequired(f"Invalid {kind} ID.")
    return value


def normalize_source(source):
    if not isinstance(source, dict) or not source.get("type"):
        raise UserActionRequired("A source namespace with type is required.")
    normalized = {key: source.get(key, "") for key in SOURCE_FIELDS}
    if any(not isinstance(value, str) for value in normalized.values()):
        raise UserActionRequired("Source namespace values must be strings.")
    if not normalized["instance"] or not normalized["project"]:
        raise UserActionRequired("Source instance and project are required to disambiguate session identity.")
    for value in normalized.values():
        if "\x00" in value:
            raise UserActionRequired("Invalid source namespace.")
    parsed = urlsplit(normalized["instance"])
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise UserActionRequired("Source instance must not contain credentials, query parameters or fragments.")
    _check_config(normalized)
    return normalized


def session_key(session_id, source):
    if not isinstance(session_id, str) or not session_id.strip():
        raise UserActionRequired("An explicit Session ID is required; Trace or Execution IDs are not substitutes.")
    return hashlib.sha256(json_bytes({"session_id": session_id, "source": normalize_source(source)})).hexdigest()[:32]


def read_json(path):
    path = Path(path)
    try:
        if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise UserActionRequired(f"Missing or damaged Trace record: {path.name}") from None


def trace_root(project_root):
    project = Path(project_root).resolve()
    return safe_path(project, ".atk/traces")


def ensure_workspace(project_root):
    project = Path(project_root).resolve()
    root = trace_root(project)
    ignore = safe_path(project, ".gitignore")
    text = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    # An explicit final rule also protects archives after a previous negated rule.
    rule = "/.atk/traces/"
    changed = not text.rstrip().endswith(rule)
    if changed:
        write_text_atomic(ignore, text + ("\n" if text and not text.endswith("\n") else "") + rule + "\n")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root, changed


def _check_config(value, key=""):
    if isinstance(value, dict):
        for name, child in value.items():
            lower = name.lower()
            if re.search(
                r"(^|_)(password|secret|token|authorization|api_key|public_key|private_key)($|_)", lower
            ) and not lower.endswith(("_env", "_env_var", "_ref")):
                raise UserActionRequired("Trace configuration stores credential references, never credential values.")
            _check_config(child, name)
    elif isinstance(value, list):
        for child in value:
            _check_config(child, key)
    elif isinstance(value, str):
        if key.endswith(("_env", "_env_var")) and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise UserActionRequired("Credential environment references must be environment variable names.")
        if value.startswith(("http://", "https://")):
            parsed = urlsplit(value)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise UserActionRequired("Trace configuration URLs cannot contain credentials or query parameters.")
        if re.search(r"(?:sk|pk)-lf-[A-Za-z0-9_-]+|-----BEGIN .*PRIVATE KEY-----", value):
            raise UserActionRequired("Trace configuration contains a credential value.")


def _merge_config(existing, supplied):
    for key, value in supplied.items():
        if isinstance(existing.get(key), dict) and isinstance(value, dict):
            _merge_config(existing[key], value)
        else:
            existing[key] = value


def _credential_references(value):
    references = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.endswith(("_env", "_env_var")) and isinstance(child, str):
                references.append({"environment_variable": child, "available": bool(os.environ.get(child))})
            else:
                references.extend(_credential_references(child))
    elif isinstance(value, list):
        for child in value:
            references.extend(_credential_references(child))
    return references


def init_workspace(project_root=".", config=None, project_text=None):
    if config is not None:
        if not isinstance(config, dict):
            raise UserActionRequired("Trace config must be a JSON object.")
        _check_config(config)
    if project_text is not None:
        if not isinstance(project_text, str):
            raise UserActionRequired("Trace project notes must be text.")
        _check_config(project_text)
    root = trace_root(project_root)
    config_path = safe_path(root, "config.json")
    existing = read_json(config_path) if config_path.exists() else {}
    if config is not None:
        _merge_config(existing, config)
    _check_config(existing)
    project_path = safe_path(root, "project.md")
    previous_text = project_path.read_text(encoding="utf-8") if project_path.exists() else None
    if project_text is not None and previous_text not in {None, DEFAULT_PROJECT_TEXT, project_text}:
        raise UserActionRequired(
            "project.md already contains project notes; preserve and edit its confirmed differences directly."
        )
    # Preflight all existing notes before any config or ignore-rule mutation.
    root, ignored = ensure_workspace(project_root)
    write_manifest(config_path, existing)
    if previous_text is None or previous_text == DEFAULT_PROJECT_TEXT and project_text is not None:
        write_text_atomic(project_path, project_text or DEFAULT_PROJECT_TEXT)
    return {
        "trace_root": str(root),
        "config_path": str(config_path),
        "project_path": str(project_path),
        "gitignore_updated": ignored,
        "credential_references": _credential_references(existing),
        "connection_verified": False,
    }


def load_session(session_dir):
    directory = Path(session_dir)
    value = read_json(safe_path(directory, "session.json"))
    if value.get("schema_version") == 2:
        manifest = value.get("archive")
        if (
            directory.name != archive.component(value.get("session_id"))
            and not (directory.name == "projects.json" and value.get("session_id") == "projects.json")
            or value.get("source") != normalize_source(value.get("source"))
            or not isinstance(manifest, dict)
            or not isinstance(manifest.get("files"), list)
            or manifest.get("status") not in STATES
            or not isinstance(manifest.get("scope"), dict)
            or not isinstance(manifest.get("completeness"), dict)
            or not isinstance(value.get("detections"), list)
            or not isinstance(value.get("revision"), int)
        ):
            raise UserActionRequired("Invalid current session archive.")
        for entry in manifest["files"]:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("path"), str)
                or not entry["path"].startswith("raw/")
                or type(entry.get("bytes")) is not int
                or not isinstance(entry.get("sha256"), str)
                or not re.fullmatch("[a-f0-9]{64}", entry["sha256"])
            ):
                raise UserActionRequired("Invalid current Trace file entry.")
        if safe_path(directory, ".pending").exists():
            raise UserActionRequired("Interrupted archive update. Retry the import to finish it.")
        return value
    if value.get("schema_version") != 1 or value.get("session_key") != session_key(
        value.get("session_id"), value.get("source")
    ):
        raise UserActionRequired("Invalid Trace session identity.")
    if directory.name != value["session_key"]:
        raise UserActionRequired("Trace session directory does not match its identity.")
    if (
        not isinstance(value.get("captures"), list)
        or not isinstance(value.get("detections"), list)
        or not isinstance(value.get("revision"), int)
        or isinstance(value.get("revision"), bool)
        or value["revision"] < 0
        or not isinstance(value.get("created_at"), str)
        or value["source"] != normalize_source(value["source"])
    ):
        raise UserActionRequired("Invalid Trace session records.")
    captures = [identifier(item, "capture") for item in value["captures"]]
    if len(set(captures)) != len(captures):
        raise UserActionRequired("Duplicate capture references in Trace session.")
    for item in value["detections"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or item.get("status") not in STATES
            or not isinstance(item.get("revision"), int)
            or not isinstance(item.get("started_at"), str)
            or not isinstance(item.get("inputs"), list)
            or not isinstance(item.get("captures"), list)
        ):
            raise UserActionRequired("Invalid Trace detection record.")
        identifier(item["id"], "detection")
    return value


def resolve_session(project_root, session_id, source=None):
    if not isinstance(session_id, str) or not session_id.strip():
        raise UserActionRequired("An explicit Session ID is required.")
    root = trace_root(project_root)
    current = safe_path(root, archive.component(session_id))
    if (current / "session.json").is_file():
        value = load_session(current)
        if value["session_id"] != session_id or source is not None and value["source"] != normalize_source(source):
            raise UserActionRequired("Trace session identity conflict.")
        return current
    if source is not None:
        path = safe_path(root, session_key(session_id, source))
        value = load_session(path)
        if value["session_id"] != session_id or value["source"] != normalize_source(source):
            raise UserActionRequired("Trace session identity conflict.")
        return path
    found = []
    if root.exists():
        for child in root.iterdir():
            if child.is_dir() and not child.is_symlink() and (child / "session.json").exists():
                value = load_session(child)
                if value["session_id"] == session_id:
                    found.append(child)
    if len(found) != 1:
        raise UserActionRequired("Session not found or source is ambiguous; provide an explicit source namespace.")
    return found[0]


def prepare_capture(project_root, session_id, source, *, scope=None, capture_id=None):
    source = normalize_source(source)
    key = session_key(session_id, source)
    capture_id = identifier(capture_id or f"CAP-{uuid.uuid4().hex[:16]}", "capture")
    root, ignored = ensure_workspace(project_root)
    directory = safe_path(root, key)
    directory.mkdir(mode=0o700, exist_ok=True)
    with session_write_lock(directory):
        session_path = safe_path(directory, "session.json")
        if session_path.exists():
            session = load_session(directory)
        else:
            session = {
                "schema_version": 1,
                "session_id": session_id,
                "session_key": key,
                "source": source,
                "created_at": now(),
                "revision": 0,
                "captures": [],
                "detections": [],
            }
        capture = safe_path(directory, f"captures/{capture_id}")
        if capture.exists():
            raise UserActionRequired("Capture already exists; resume the running capture or create a new one.")
        capture.mkdir(mode=0o700, parents=True)
        manifest = {
            "schema_version": 1,
            "capture_id": capture_id,
            "session_id": session_id,
            "source": source,
            "status": "running",
            "sealed": False,
            "scope": scope or {},
            "completeness": {"status": "unknown"},
            "files": [],
            "errors": [],
            "started_at": now(),
        }
        write_manifest(capture / "manifest.json", manifest)
        session["captures"].append(capture_id)
        session["revision"] += 1
        write_manifest(session_path, session)
        return _capture_result(directory, capture, manifest, ignored)


def _capture_result(directory, capture, manifest, ignored=False):
    return {
        "session_dir": str(directory),
        "session_key": directory.name,
        "capture_id": manifest["capture_id"],
        "capture_dir": str(capture),
        "manifest_path": str(capture / "manifest.json"),
        "manifest": manifest,
        "gitignore_updated": ignored,
    }


def finish_capture(session_dir, capture_id, files, *, scope=None, completeness=None, status="completed", errors=None):
    directory = Path(session_dir)
    with session_write_lock(directory):
        load_session(directory)
        capture = safe_path(directory, f"captures/{identifier(capture_id, 'capture')}")
        manifest = read_capture(directory, capture_id)
        if manifest["sealed"]:
            raise UserActionRequired("Capture is sealed; original evidence cannot be overwritten.")
        if status not in STATES - {"running"}:
            raise UserActionRequired("Capture finish status must describe the actual terminal outcome.")
        if not isinstance(files, dict):
            raise UserActionRequired("Capture files must map safe relative names to retained bytes.")
        prepared = []
        for relative, data in files.items():
            path = safe_path(capture, "raw/" + relative)
            if relative.startswith("/") or relative in {"", "."}:
                raise UserActionRequired("Unsafe capture file path.")
            data = data.encode("utf-8") if isinstance(data, str) else data
            if not isinstance(data, bytes):
                raise UserActionRequired("Capture content must be retained bytes or text.")
            prepared.append(
                (
                    path,
                    data,
                    {"path": "raw/" + relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
                )
            )
        entries = {entry["path"]: entry for entry in manifest["files"]}
        for path, data, entry in prepared:
            if path.exists():
                if path.is_symlink() or path.read_bytes() != data:
                    raise UserActionRequired("Existing partial capture bytes differ; refusing overwrite.")
            else:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            entries[entry["path"]] = entry
            manifest["files"] = list(entries.values())
            write_manifest(capture / "manifest.json", manifest)
        manifest.update(
            status=status,
            sealed=True,
            completed_at=now(),
            completeness=completeness or {"status": "partial", "gaps": ["Completeness was not established."]},
            errors=errors or [],
        )
        if scope is not None:
            manifest["scope"] = scope
        manifest["manifest_sha256"] = hashlib.sha256(json_bytes(manifest)).hexdigest()
        write_manifest(capture / "manifest.json", manifest)
        return _capture_result(directory, capture, manifest)


def create_capture(
    project_root,
    session_id,
    source,
    files,
    scope=None,
    completeness=None,
    *,
    status="completed",
    capture_id=None,
    errors=None,
):
    prepared = prepare_capture(project_root, session_id, source, scope=scope, capture_id=capture_id)
    result = finish_capture(
        prepared["session_dir"],
        prepared["capture_id"],
        files,
        scope=scope,
        completeness=completeness,
        status=status,
        errors=errors,
    )
    result["gitignore_updated"] = prepared["gitignore_updated"]
    return result


def read_capture(session_dir, capture_id=None, verify=True):
    directory = Path(session_dir)
    session = load_session(directory)
    if capture_id is None:
        if not session["captures"]:
            raise UserActionRequired("Session has no capture evidence.")
        capture_id = session["captures"][-1]
    identifier(capture_id, "capture")
    if capture_id not in session["captures"]:
        raise UserActionRequired("Capture does not belong to this session.")
    capture = safe_path(directory, f"captures/{capture_id}")
    manifest = read_json(safe_path(capture, "manifest.json"))
    if (
        manifest.get("schema_version") != 1
        or manifest.get("capture_id") != capture_id
        or manifest.get("session_id") != session["session_id"]
        or manifest.get("source") != session["source"]
    ):
        raise UserActionRequired("Capture identity conflicts with its session.")
    if (
        not isinstance(manifest.get("files"), list)
        or not isinstance(manifest.get("sealed"), bool)
        or manifest.get("status") not in STATES
        or not isinstance(manifest.get("scope"), dict)
        or not isinstance(manifest.get("completeness"), dict)
        or not isinstance(manifest.get("errors"), list)
    ):
        raise UserActionRequired("Invalid capture manifest.")
    for entry in manifest["files"]:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not isinstance(entry.get("bytes"), int)
            or isinstance(entry.get("bytes"), bool)
            or entry["bytes"] < 0
            or not isinstance(entry.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
        ):
            raise UserActionRequired("Invalid capture file entry.")
    if verify:
        paths = set()
        try:
            for entry in manifest["files"]:
                relative = entry["path"]
                if not relative.startswith("raw/") or relative in paths:
                    raise ValueError
                paths.add(relative)
                path = safe_path(capture, relative)
                if path.stat().st_size != entry["bytes"] or digest_file(path) != entry["sha256"]:
                    raise ValueError
            if manifest["sealed"]:
                payload = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
                if hashlib.sha256(json_bytes(payload)).hexdigest() != manifest.get("manifest_sha256"):
                    raise ValueError
        except (OSError, KeyError, TypeError, ValueError):
            raise UserActionRequired("Capture integrity check failed.") from None
    return manifest


def read_capture_file(session_dir, capture_id, path):
    manifest = read_capture(session_dir, capture_id)
    relative = path if path.startswith("raw/") else "raw/" + path
    if relative not in {entry["path"] for entry in manifest["files"]}:
        raise UserActionRequired("File is not listed in this capture manifest.")
    return safe_path(Path(session_dir), f"captures/{capture_id}/{relative}").read_bytes()


def evidence_ref(session_dir, relative_path, pointer=None):
    path = safe_path(Path(session_dir), relative_path)
    if not path.is_file():
        raise UserActionRequired("Evidence reference does not name a retained file.")
    ref = {"path": relative_path, "sha256": digest_file(path), "bytes": path.stat().st_size}
    if pointer is not None:
        value = _resolve_pointer(path, pointer)
        if relative_path.startswith("raw/"):
            ref["field_sha256"] = hashlib.sha256(json_bytes(value)).hexdigest()
            observation = re.match(r"(/(?:data|trace/observations)/[0-9]+)(?:/|$)", pointer)
            if observation:
                ref["node_id"] = _resolve_pointer(path, observation[1] + "/id")
        ref["pointer"] = pointer
    return ref


def _resolve_pointer(path, pointer):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise UserActionRequired("Evidence pointer must be an RFC 6901 JSON Pointer.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if pointer:
            for part in pointer[1:].split("/"):
                if re.search(r"~(?![01])", part):
                    raise ValueError
                part = part.replace("~1", "/").replace("~0", "~")
                if isinstance(value, list):
                    if not re.fullmatch(r"0|[1-9][0-9]*", part):
                        raise ValueError
                    value = value[int(part)]
                else:
                    value = value[part]
    except (OSError, ValueError, KeyError, IndexError, TypeError, UnicodeError):
        raise UserActionRequired("Evidence pointer cannot be resolved in the retained file.") from None
    return value


def resolve_ref(session_dir, ref):
    try:
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            raise ValueError
        path = safe_path(Path(session_dir), ref["path"])
        digest = digest_file(path)
        matches = path.stat().st_size == ref["bytes"] and digest == ref["sha256"]
        pointer = ref.get("pointer")
        if matches:
            if pointer is not None:
                _resolve_pointer(path, pointer)
            return path, pointer
        if not (ref.get("field_sha256") and isinstance(pointer, str) and ref["path"].startswith("raw/")):
            raise ValueError
        # Only follow a replaced file if it was published by a successful import.
        entries = load_session(session_dir)["archive"]["files"]
        if not any(entry["path"] == ref["path"] and entry["sha256"] == digest for entry in entries):
            raise ValueError
        if "node_id" in ref:
            match = re.fullmatch(r"(/(?:data|trace/observations))/[0-9]+(/.*)?", pointer)
            if not match:
                raise ValueError
            rows = _resolve_pointer(path, match[1])
            indexes = [i for i, row in enumerate(rows) if isinstance(row, dict) and row.get("id") == ref["node_id"]]
            if len(indexes) != 1:
                raise UserActionRequired("证据节点已更新，需重新检测。")
            pointer = f"{match[1]}/{indexes[0]}{match[2] or ''}"
        try:
            value = _resolve_pointer(path, pointer)
        except UserActionRequired:
            raise UserActionRequired("证据字段已更新，需重新检测。") from None
        if hashlib.sha256(json_bytes(value)).hexdigest() != ref["field_sha256"]:
            raise UserActionRequired("证据字段已更新，需重新检测。")
        return path, pointer
    except (OSError, KeyError, TypeError, ValueError):
        raise UserActionRequired("Evidence reference is missing or its retained bytes changed.") from None


def validate_ref(session_dir, ref):
    path, _ = resolve_ref(session_dir, ref)
    return path
