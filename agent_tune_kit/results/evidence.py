"""Private, independently readable execution evidence. Project callbacks are trusted code."""

import hashlib
import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.utils.files import write_text_atomic


def now():
    return datetime.now(UTC).isoformat()


def json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError, UnicodeError):
        raise UserActionRequired("Evidence serialization failed; provide a safe retention serializer.") from None


def digest_file(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def safe_path(directory, relative):
    root = Path(directory)
    part = PurePosixPath(relative)
    if not relative or part.is_absolute() or ".." in part.parts or "\\" in relative or root.is_symlink():
        raise UserActionRequired("Unsafe evidence path.")
    current = root
    for component in part.parts:
        current /= component
        if current.is_symlink():
            raise UserActionRequired("Unsafe evidence path: symbolic links are not supported.")
    if not current.resolve().is_relative_to(root.resolve()):
        raise UserActionRequired("Unsafe evidence path.")
    return current


def sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_manifest(path, value):
    path = Path(path)
    if path.is_symlink():
        raise UserActionRequired("Unsafe evidence manifest path.")
    write_text_atomic(path, json_bytes(value).decode())
    sync_directory(path.parent)


def read_manifest(directory, name="execution.json"):
    try:
        path = safe_path(directory, name)
        if path.stat().st_size > 16 * 1024 * 1024:
            raise UserActionRequired("Evidence manifest exceeds the supported size.")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise UserActionRequired("Unsupported evidence schema.")
        if not isinstance(value.get("files"), list) or not isinstance(value.get("sealed"), bool):
            raise UserActionRequired("Invalid evidence manifest.")
        return value
    except (OSError, ValueError, UnicodeError):
        raise UserActionRequired("Evidence manifest missing or damaged; no legacy fallback.") from None


def manifest_digest(manifest):
    value = {key: value for key, value in manifest.items() if key not in {"evidence_id", "run_evidence_id"}}
    value["files"] = sorted(value["files"], key=lambda entry: entry["path"])
    return hashlib.sha256(json_bytes(value)).hexdigest()


def verify_manifest(directory, manifest):
    try:
        paths, ids = set(), set()
        for entry in manifest["files"]:
            relative, identity = entry["path"], entry["artifact_id"]
            if relative in paths or identity in ids:
                raise ValueError
            paths.add(relative)
            ids.add(identity)
            path = safe_path(directory, relative)
            if not path.is_file() or path.stat().st_size != entry["bytes"] or digest_file(path) != entry["sha256"]:
                raise ValueError
        key = "run_evidence_id" if "run_kind" in manifest else "evidence_id"
        if manifest["sealed"] and manifest.get(key) != manifest_digest(manifest):
            raise ValueError
    except (OSError, KeyError, TypeError, ValueError):
        raise UserActionRequired("Evidence integrity check failed.") from None


class ExecutionEvidence:
    def __init__(self, directory, *, run_id, sample_ids, policy, origin="managed", execution_id=None, budget=None):
        self.directory = Path(directory)
        if self.directory.exists() or self.directory.is_symlink():
            raise UserActionRequired("Execution directory already exists; refusing overwrite.")
        self.directory.mkdir(mode=0o700, parents=True)
        self.policy = policy
        self.budget = budget
        self.lock = threading.RLock()
        self.seq = 0
        self.used_bytes = 0
        self.manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "execution_id": execution_id or uuid.uuid4().hex,
            "origin": origin,
            "sample_ids": sample_ids,
            "started_at": now() if origin == "managed" else None,
            "observed_at": now(),
            "status": "running" if origin == "managed" else "unknown",
            "sealed": False,
            "files": [],
            "coverage": {"status": "partial", "sources": [], "gaps": []},
        }
        self.flush()

    def flush(self):
        write_manifest(self.directory / "execution.json", self.manifest)

    def retain(self, role, value):
        try:
            callback = self.policy.get("retain")
            if not callable(callback):
                raise ValueError
            return callback(role, value)
        except Exception:
            raise UserActionRequired("Evidence retention rejected content; original bytes were not saved.") from None

    def _open(self):
        if self.manifest["sealed"]:
            raise UserActionRequired("Execution evidence is sealed.")

    def _budget(self, count):
        if self.budget is not None:
            self.budget.charge(count)
            return
        maximum = self.policy.get("max_bytes")
        if maximum is not None and self.used_bytes + count > maximum:
            raise UserActionRequired("Evidence storage budget exhausted.")

    def save(self, role, value, *, path=None, media_type="application/json", source=None):
        with self.lock:
            self._open()
            value = self.retain(role, value)
            if isinstance(value, bytes):
                data = value
            elif media_type == "application/json":
                data = json_bytes(value)
            else:
                data = value.encode() if isinstance(value, str) else value
            if not isinstance(data, bytes):
                raise UserActionRequired("Evidence serializer must return bytes for binary content.")
            self._budget(len(data))
            identity = uuid.uuid4().hex
            relative = path or f"artifacts/{identity}"
            target = safe_path(self.directory, relative)
            if relative in {"execution.json", "events.jsonl"} or target.exists():
                raise UserActionRequired("Evidence path already exists or is reserved.")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                sync_directory(target.parent)
            except OSError:
                raise UserActionRequired("Evidence write failed; partial material was preserved.") from None
            self.used_bytes += len(data)
            entry = {
                "artifact_id": identity,
                "path": relative,
                "role": role,
                "media_type": media_type,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "retention_policy": self.policy.get("name", "project"),
            }
            if source is not None:
                entry["source"] = self.retain("source", source)
            self.manifest["files"].append(entry)
            self.flush()
            return entry

    def save_chunks(self, role, chunks, *, media_type="application/octet-stream", source=None):
        """The explicit streaming retention callback must handle cross-chunk sensitive content."""
        with self.lock:
            self._open()
            callback = self.policy.get("retain_chunks")
            if not callable(callback):
                raise UserActionRequired("Streaming retention must be explicitly declared.")
            identity = uuid.uuid4().hex
            relative = f"artifacts/{identity}"
            target = safe_path(self.directory, relative)
            target.parent.mkdir(mode=0o700, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            source_info = self.retain("source", source) if source is not None else None
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as handle:
                    for chunk in callback(role, chunks):
                        if not isinstance(chunk, bytes):
                            raise UserActionRequired("Streaming retention must yield bytes.")
                        self._budget(len(chunk))
                        handle.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                        self.used_bytes += len(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                sync_directory(target.parent)
            except UserActionRequired:
                raise
            except Exception:
                raise UserActionRequired(
                    "Streaming retention/write failed; safe partial bytes are unregistered."
                ) from None
            entry = {
                "artifact_id": identity,
                "path": relative,
                "role": role,
                "media_type": media_type,
                "bytes": size,
                "sha256": digest.hexdigest(),
                "retention_policy": self.policy.get("name", "project"),
            }
            if source_info is not None:
                entry["source"] = source_info
            self.manifest["files"].append(entry)
            self.flush()
            return entry

    def copy_file(self, role, path, *, media_type="application/octet-stream", source=None):
        path = Path(path)
        before = digest_file(path)
        if callable(self.policy.get("retain_chunks")):
            with path.open("rb") as handle:
                entry = self.save_chunks(
                    role, iter(lambda: handle.read(65536), b""), media_type=media_type, source=source
                )
        else:
            entry = self.save(role, path.read_bytes(), media_type=media_type, source=source)
        if digest_file(path) != before:
            raise UserActionRequired("Evidence source changed while copying.")
        return entry

    def register_direct_artifacts(self):
        if not self.policy.get("allow_direct_artifacts"):
            return
        known = {entry["path"] for entry in self.manifest["files"]}
        for candidate in safe_path(self.directory, "artifacts").rglob("*"):
            relative = candidate.relative_to(self.directory).as_posix()
            path = safe_path(self.directory, relative)
            if path.is_dir() or relative in known:
                continue
            before = path.stat()
            if not path.is_file() or before.st_nlink != 1:
                raise UserActionRequired("Direct artifacts must be independent regular files.")
            digest = digest_file(path)
            if before != path.stat():
                raise UserActionRequired("Direct artifact is not stable.")
            self._budget(before.st_size)
            self.used_bytes += before.st_size
            path.chmod(0o600)
            self.manifest["files"].append(
                {
                    "artifact_id": uuid.uuid4().hex,
                    "path": relative,
                    "role": "direct-artifact",
                    "media_type": "application/octet-stream",
                    "bytes": before.st_size,
                    "sha256": digest,
                    "retention_policy": "project-direct-write",
                }
            )

    def event(self, kind, attributes, *, span_id=None, parent_span_id=None, occurred_at=None):
        with self.lock:
            self._open()
            body = self.retain("event", {"kind": kind, "attributes": attributes})
            event = {
                "kind": body["kind"],
                "attributes": body["attributes"],
                "schema_version": 1,
                "run_id": self.manifest["run_id"],
                "execution_id": self.manifest["execution_id"],
                "event_id": uuid.uuid4().hex,
                "seq": self.seq + 1,
                "occurred_at": occurred_at,
                "observed_at": now(),
                "span_id": span_id,
                "parent_span_id": parent_span_id,
            }
            data = json_bytes(event) + b"\n"
            self._budget(len(data))
            target = safe_path(self.directory, "events.jsonl")
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "ab") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self.seq += 1
            self.used_bytes += len(data)
            return event

    def seal(self, status):
        with self.lock:
            self._open()
            verify_manifest(self.directory, self.manifest)
            events = read_events(self.directory, limit=0)
            if events["incomplete_tail"]:
                raise UserActionRequired("Cannot seal an incomplete event stream.")
            event_path = self.directory / "events.jsonl"
            if event_path.exists():
                self.manifest["files"].append(
                    {
                        "artifact_id": "events",
                        "path": "events.jsonl",
                        "role": "events",
                        "media_type": "application/x-ndjson",
                        "bytes": event_path.stat().st_size,
                        "sha256": digest_file(event_path),
                    }
                )
            self.manifest.update(status=status, sealed=True, collected_until=now())
            self.manifest["evidence_id"] = manifest_digest(self.manifest)
            self.flush()
            return self.manifest["evidence_id"]


def inspect_execution(directory):
    manifest = read_manifest(directory)
    verify_manifest(directory, manifest)
    read_events(directory, limit=0, manifest=manifest)
    return manifest


def read_events(directory, after_seq=0, limit=100, *, manifest=None):
    if after_seq < 0 or not 0 <= limit <= 1000:
        raise UserActionRequired("Invalid event pagination; limit must be between 0 and 1000.")
    manifest = manifest or read_manifest(directory)
    if manifest["sealed"]:
        verify_manifest(directory, manifest)
    path = safe_path(directory, "events.jsonl")
    result, seq, seen, incomplete = [], 0, set(), False
    if path.exists():
        with path.open("rb") as handle:
            for line in handle:
                if not line.endswith(b"\n"):
                    incomplete = True
                    if manifest["sealed"]:
                        raise UserActionRequired("Invalid sealed event tail.")
                    break
                try:
                    event = json.loads(line)
                    if (
                        event["seq"] != seq + 1
                        or event["event_id"] in seen
                        or event["execution_id"] != manifest["execution_id"]
                        or event["run_id"] != manifest["run_id"]
                        or event["schema_version"] != 1
                    ):
                        raise ValueError
                    seen.add(event["event_id"])
                    seq = event["seq"]
                    if seq > after_seq and len(result) < limit:
                        result.append(event)
                except (ValueError, KeyError, TypeError, UnicodeError):
                    raise UserActionRequired("Invalid evidence event stream.") from None
    return {
        "events": result,
        "incomplete_tail": incomplete,
        "total": seq,
        "next_seq": result[-1]["seq"] if result else after_seq,
        "run_id": manifest["run_id"],
        "execution_id": manifest["execution_id"],
    }


def read_artifact(directory, artifact_id, offset=0, limit=8000):
    if offset < 0 or not 0 <= limit <= 100000:
        raise UserActionRequired("Invalid artifact pagination.")
    manifest = inspect_execution(directory)
    entry = next((entry for entry in manifest["files"] if entry["artifact_id"] == artifact_id), None)
    if entry is None:
        raise UserActionRequired("Unknown artifact ID for this execution.")
    path = safe_path(directory, entry["path"])
    result = dict(entry, local_path=str(path.absolute()))
    if entry["media_type"].startswith("text/") or entry["media_type"] in {"application/json", "application/x-ndjson"}:
        total, parts = 0, []
        with path.open(encoding="utf-8") as handle:
            while chunk := handle.read(65536):
                start, end = max(0, offset - total), min(len(chunk), offset + limit - total)
                if end > start:
                    parts.append(chunk[start:end])
                total += len(chunk)
        result.update(
            text="".join(parts),
            offset=offset,
            total_length=total,
            unit="characters",
            next_offset=min(total, offset + limit),
            truncated=offset + limit < total,
        )
    if digest_file(path) != entry["sha256"]:
        raise UserActionRequired("Evidence integrity changed while reading.")
    return result


class RunEvidence:
    def __init__(self, directory, *, policy, run_kind="evaluation", origin="managed"):
        self.directory = Path(directory)
        if self.directory.is_symlink() or (self.directory.exists() and any(self.directory.iterdir())):
            raise UserActionRequired("Run directory is nonempty; refusing overwrite.")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.policy = policy
        self.lock = threading.RLock()
        self.failed = threading.Event()
        self.used_bytes = 0
        self.seq = 0
        self.manifest = {
            "schema_version": 1,
            "run_id": uuid.uuid4().hex,
            "run_kind": run_kind,
            "origin": origin,
            "observed_at": now(),
            "sealed": False,
            "status": "running",
            "files": [],
            "executions": {},
            "sample_executions": {},
            "planned": [],
            "gaps": [],
        }
        self.flush()

    def flush(self):
        write_manifest(self.directory / "run.json", self.manifest)

    def execution(self, sample_ids):
        with self.lock:
            if self.manifest["sealed"] or any(aid in self.manifest["sample_executions"] for aid in sample_ids):
                raise UserActionRequired("Duplicate sample mapping or sealed Run.")
            identity = uuid.uuid4().hex
            relative = f"executions/{identity}"
            item = ExecutionEvidence(
                self.directory / relative,
                run_id=self.manifest["run_id"],
                sample_ids=sample_ids,
                policy=self.policy,
                origin=self.manifest["origin"],
                execution_id=identity,
                budget=self,
            )
            self.manifest["executions"][identity] = relative
            self.manifest["sample_executions"].update(dict.fromkeys(sample_ids, identity))
            self.flush()
            return item

    def charge(self, count):
        # shortcut: counts retained content, use a filesystem quota when metadata also needs a hard disk limit.
        with self.lock:
            maximum = self.policy.get("max_bytes")
            if maximum is not None and self.used_bytes + count > maximum:
                self.failed.set()
                raise UserActionRequired("Run evidence storage budget exhausted.")
            self.used_bytes += count

    def event(self, kind, attributes):
        with self.lock:
            if self.manifest["sealed"]:
                raise UserActionRequired("Run is sealed.")
            try:
                body = self.policy["retain"]("event", {"kind": kind, "attributes": attributes})
                data = (
                    json_bytes(
                        {
                            **body,
                            "schema_version": 1,
                            "run_id": self.manifest["run_id"],
                            "execution_id": None,
                            "event_id": uuid.uuid4().hex,
                            "seq": self.seq + 1,
                            "observed_at": now(),
                        }
                    )
                    + b"\n"
                )
                self.charge(len(data))
                path = safe_path(self.directory, "run-events.jsonl")
                fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "ab") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                self.seq += 1
            except Exception:
                self.failed.set()
                raise UserActionRequired("Run evidence persistence failed.") from None

    def seal(self, status):
        with self.lock:
            if self.manifest["sealed"]:
                raise UserActionRequired("Run is sealed.")
            for relative in self.manifest["executions"].values():
                item = inspect_execution(safe_path(self.directory, relative))
                if not item["sealed"]:
                    raise UserActionRequired("Run contains unsealed execution evidence.")
                path = safe_path(self.directory, relative + "/execution.json")
                self.manifest["files"].append(
                    {
                        "artifact_id": item["execution_id"],
                        "path": relative + "/execution.json",
                        "role": "execution",
                        "bytes": path.stat().st_size,
                        "sha256": digest_file(path),
                    }
                )
            for name in ["eval_results.csv", "run-events.jsonl"]:
                path = safe_path(self.directory, name)
                if path.is_file():
                    self.manifest["files"].append(
                        {
                            "artifact_id": name,
                            "path": name,
                            "role": name,
                            "bytes": path.stat().st_size,
                            "sha256": digest_file(path),
                        }
                    )
            self.manifest.update(status=status, sealed=True, collected_until=now())
            self.manifest["run_evidence_id"] = manifest_digest(self.manifest)
            self.flush()


def inspect_run(directory):
    manifest = read_manifest(directory, "run.json")
    verify_manifest(directory, manifest)
    executions = {}
    for identity, relative in manifest.get("executions", {}).items():
        item = inspect_execution(safe_path(directory, relative))
        if item["run_id"] != manifest["run_id"] or item["execution_id"] != identity:
            raise UserActionRequired("Execution identity disagrees with Run index.")
        for aid in item["sample_ids"]:
            if manifest["sample_executions"].get(aid) != identity:
                raise UserActionRequired("Sample mapping disagrees with Run index.")
        executions[identity] = item
    return dict(manifest, execution_details=executions)


def locate_execution(directory, *, atk_id=None, execution_id=None):
    manifest = read_manifest(directory, "run.json")
    if atk_id is not None:
        execution_id = manifest["sample_executions"].get(str(atk_id))
    relative = manifest["executions"].get(execution_id)
    if relative is None:
        raise UserActionRequired("No execution matches the requested identity.")
    target = safe_path(directory, relative)
    item = inspect_execution(target)
    if item["run_id"] != manifest["run_id"] or item["execution_id"] != execution_id:
        raise UserActionRequired("Execution identity disagrees with Run index.")
    if atk_id is not None and str(atk_id) not in item["sample_ids"]:
        raise UserActionRequired("Sample mapping disagrees with execution.")
    return target


def import_evidence(source, adapter, results_dir=".atk/results"):
    import tempfile

    with tempfile.TemporaryFile(mode="w+b") as retained_events:
        return _import_evidence(source, adapter, results_dir, retained_events)


def _import_evidence(source, adapter, results_dir, retained_events):
    import ast
    import runpy

    from agent_tune_kit.utils.csv_io import write_csv_atomic

    from .versions import allocate_next_results_version, list_version_dirs, write_occupation

    source, adapter, results_dir = Path(source), Path(adapter), Path(results_dir)
    tree = ast.parse(adapter.read_text(encoding="utf-8"))
    markers = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ATK_IMPORT_VERSION" for target in node.targets)
        and isinstance(node.value, ast.Constant)
    ]
    if markers != [1]:
        raise UserActionRequired("Import adapter must declare ATK_IMPORT_VERSION = 1.")
    if not source.is_dir() or source.is_symlink():
        raise UserActionRequired("Import source must be an explicit local directory.")
    try:
        namespace = runpy.run_path(str(adapter), run_name="atk_import_adapter")
        policy = namespace["EVIDENCE"]
        if not callable(policy.get("retain")):
            raise ValueError
        payload = namespace["load_evidence"](source)
        source_identity = {key: payload[key] for key in ["source_system", "source_run_id"]}
        if any(not isinstance(value, str) or not value for value in source_identity.values()):
            raise ValueError
        items = payload["executions"]
        source_ids = [item["source_id"] for item in items]
        if not source_ids or len(source_ids) != len(set(source_ids)):
            raise ValueError
        event_ranges = []
        for item in items:
            start, count, digest = retained_events.tell(), 0, hashlib.sha256()
            for event in item.get("events", []):
                data = json_bytes(policy["retain"]("import_event", event)) + b"\n"
                retained_events.write(data)
                digest.update(data)
                count += 1
            event_ranges.append((start, count))
            item["events"] = {"sha256": digest.hexdigest(), "count": count}
        hashes = {}
        for item in items:
            for entry in item.get("files", []) + payload.get("context_files", []):
                path = safe_path(source, entry["path"])
                if not path.is_file():
                    raise ValueError
                hashes[entry["path"]] = digest_file(path)
        fingerprint = hashlib.sha256(
            json_bytes({"payload": payload, "files": hashes, "adapter": digest_file(adapter)})
        ).hexdigest()
        source_identity = policy["retain"]("source", source_identity)
    except Exception:
        raise UserActionRequired("Import adapter or source validation failed; source was not modified.") from None
    with write_occupation(results_dir):
        for _, previous in list_version_dirs(results_dir):
            if (previous / "run.json").exists():
                existing = read_manifest(previous, "run.json")
                if existing.get("source") == source_identity:
                    if existing.get("source_fingerprint") != fingerprint:
                        raise UserActionRequired("Source material changed; supplementary imports are not supported.")
                    if not existing["sealed"]:
                        raise UserActionRequired("Prior import is incomplete; automatic resume is not supported.")
                    inspect_run(previous)
                    return {"version": previous.name, "run_kind": existing["run_kind"], "reused": True}
        kind = "evaluation" if "results" in payload else "evidence-only"
        run = RunEvidence(allocate_next_results_version(results_dir), policy=policy, run_kind=kind, origin="imported")
        run.manifest.update(source=source_identity, source_fingerprint=fingerprint)
        run.flush()
        try:
            for item, (event_offset, event_count) in zip(items, event_ranges, strict=True):
                execution = run.execution(item.get("sample_ids", []))
                execution.manifest["source"] = execution.retain(
                    "source", {**source_identity, "execution": item["source_id"]}
                )
                execution.manifest["coverage"] = execution.retain(
                    "coverage",
                    item.get("coverage", {"status": "partial", "sources": [], "gaps": ["Source coverage unknown"]}),
                )
                for role in ["input", "output"]:
                    if role in item:
                        execution.save(role, item[role], path=role + ".json")
                file_entries = {}
                for entry in item.get("files", []) + payload.get("context_files", []):
                    path = safe_path(source, entry["path"])
                    before = digest_file(path)
                    saved = execution.copy_file(
                        entry["role"],
                        path,
                        media_type=entry.get("media_type", "application/octet-stream"),
                        source={"path": entry["path"], "phase": entry.get("phase", "unknown")},
                    )
                    if before != hashes[entry["path"]] or digest_file(path) != before:
                        raise UserActionRequired("Import source changed while copying.")
                    file_entries[entry["path"]] = saved
                retained_events.seek(event_offset)
                for _ in range(event_count):
                    event = json.loads(retained_events.readline())
                    source_ref = dict(event.get("source", {}))
                    if source_ref.get("file") in file_entries:
                        saved = file_entries[source_ref["file"]]
                        source_ref.update(artifact_id=saved["artifact_id"], retained_sha256=saved["sha256"])
                    execution.event(
                        event["kind"],
                        event.get("attributes", {}),
                        occurred_at=event.get("occurred_at"),
                        span_id=event.get("span_id"),
                        parent_span_id=event.get("parent_span_id"),
                    )
                    execution.event(
                        "source.mapping",
                        {"event_seq": execution.seq, "source": source_ref, "converter_sha256": digest_file(adapter)},
                    )
                from agent_tune_kit.execution.tracing import write_log_view

                write_log_view(execution)
                execution.seal(item.get("status", "unknown"))
            if kind == "evaluation":
                rows = payload["results"]
                ids = [row["atk_id"] for row in rows]
                if len(set(ids)) != len(ids) or set(ids) != set(run.manifest["sample_executions"]):
                    raise UserActionRequired("Imported result mapping must cover the declared samples exactly once.")
                if any("agent_output" not in row for row in rows) or policy["retain"]("result_rows", rows) != rows:
                    raise UserActionRequired("Imported scoring fields are missing or changed by retention.")
                fields = list(dict.fromkeys(key for row in rows for key in row))
                write_csv_atomic(run.directory / "eval_results.csv", fields, rows)
            for relative, digest in hashes.items():
                if digest_file(safe_path(source, relative)) != digest:
                    raise UserActionRequired("Import source drifted before sealing.")
            run.seal("completed")
        except BaseException:
            run.manifest.update(status="failed")
            run.manifest["gaps"].append("import_not_completed")
            run.flush()
            raise UserActionRequired("Import failed; partial evidence preserved, source not modified.") from None
        return {
            "version": run.directory.name,
            "run_id": run.manifest["run_id"],
            "run_kind": kind,
            "executions": run.manifest["executions"],
            "reused": False,
        }


def query_evidence(
    *,
    results_dir=".atk/results",
    version=None,
    execution_dir=None,
    atk_id=None,
    execution_id=None,
    event_id=None,
    artifact_id=None,
    after_seq=None,
    offset=0,
    limit=None,
):
    import re

    from .versions import resolve_current_version

    if execution_dir and any(value is not None for value in [version, atk_id, execution_id]):
        raise UserActionRequired("Independent directory and Run selectors are mutually exclusive.")
    if atk_id is not None and execution_id is not None or event_id is not None and artifact_id is not None:
        raise UserActionRequired("Conflicting evidence selectors.")
    if execution_dir:
        target = Path(execution_dir)
    else:
        if version is not None and not re.fullmatch(r"v[1-9][0-9]*", version):
            raise UserActionRequired("Invalid version.")
        root = Path(results_dir) / version if version else resolve_current_version(results_dir)
        if atk_id is None and execution_id is None:
            if any(value is not None for value in [event_id, artifact_id, after_seq]):
                raise UserActionRequired("Select an execution before reading its content.")
            return inspect_run(root)
        target = locate_execution(root, atk_id=atk_id, execution_id=execution_id)
    if artifact_id is not None:
        return read_artifact(target, artifact_id, offset=offset, limit=8000 if limit is None else limit)
    if event_id is not None:
        cursor = 0
        while True:
            page = read_events(target, after_seq=cursor, limit=1000)
            for event in page["events"]:
                if event["event_id"] == event_id:
                    return event
            if not page["events"] or page["next_seq"] >= page["total"]:
                raise UserActionRequired("Unknown event ID for this execution.")
            cursor = page["next_seq"]
    if after_seq is not None:
        return read_events(target, after_seq=after_seq, limit=100 if limit is None else limit)
    return inspect_execution(target)


def validate_references(directory, references):
    try:
        refs = json.loads(references) if isinstance(references, str) and references.strip() else references or []
        if not isinstance(refs, list):
            raise ValueError
        for ref in refs:
            root = read_manifest(directory, "run.json")
            if ref["run_id"] != root["run_id"]:
                raise ValueError
            if "execution_id" not in ref:
                verify_manifest(directory, root)
                if not root["sealed"] or ref.get("run_evidence_id") != root.get("run_evidence_id"):
                    raise ValueError
                continue
            target = locate_execution(directory, execution_id=ref["execution_id"])
            item = inspect_execution(target)
            if item["sealed"]:
                if ref.get("evidence_id") != item["evidence_id"]:
                    raise ValueError
            elif not ref.get("partial") or (Path(directory).parent / ".writer.json").exists():
                raise ValueError
            if "artifact_id" in ref:
                entry = next(entry for entry in item["files"] if entry["artifact_id"] == ref["artifact_id"])
                if ref.get("sha256") != entry["sha256"]:
                    raise ValueError
            elif "event_id" in ref:
                event = query_evidence(execution_dir=target, event_id=ref["event_id"])
                if ref.get("sha256") != hashlib.sha256(json_bytes(event)).hexdigest():
                    raise ValueError
            else:
                raise ValueError
        return True
    except (OSError, ValueError, KeyError, TypeError, StopIteration, UserActionRequired):
        return False
