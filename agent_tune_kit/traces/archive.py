"""Current session storage. A native Trace ID owns one replaceable raw file."""

import hashlib
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.evidence import digest_file, json_bytes, now, safe_path, write_manifest

from . import store


def component(value):
    if not isinstance(value, str) or not value.strip():
        raise UserActionRequired("An explicit nonempty native ID is required.")
    name = quote(value, safe="")
    # Names of the files that live beside session directories at the archive root.
    reserved = {"project.md", "config.json", "categories.json", "projects.json", "index.html", "_export_summary"}
    if name.startswith(".") or name.casefold() in reserved:
        name = "%" + format(ord(name[0]), "02X") + name[1:]
    if len(name) > 200:
        name = name[:150] + "~" + hashlib.sha256(value.encode()).hexdigest()
    return name


def normalize_files(files):
    """Split observation pages by native Trace ID, independent of pagination or filenames."""
    output, groups = {}, {}

    def put(name, body):
        if name in output and output[name] != body:
            raise UserActionRequired("Conflicting duplicate material for the same Trace ID or file.")
        output[name] = body

    for name, body in files.items():
        if name.startswith("materials/") or name == "association.json":
            put(name, body)
            continue
        doc = json.loads(body) if name.lower().endswith(".json") else None
        if isinstance(doc, dict) and isinstance(doc.get("trace"), dict) and doc["trace"].get("id"):
            put(component(doc["trace"]["id"]) + ".json", body)
        elif (
            isinstance(doc, dict)
            and isinstance(doc.get("data"), list)
            and all(isinstance(row, dict) and row.get("traceId") and row.get("id") for row in doc["data"])
        ):
            for row in doc["data"]:
                group = groups.setdefault(row["traceId"], {})
                if row["id"] in group and group[row["id"]] != row:
                    raise UserActionRequired("Conflicting duplicate observation.")
                group[row["id"]] = row
        elif isinstance(doc, dict) and "trace_count" in doc and "traces" in doc:
            put("_export_summary.json", body)
        else:
            put(name, body)
    for trace_id, rows in groups.items():
        name = component(trace_id) + ".json"
        if name in output:
            exported = {row["id"]: row for row in json.loads(output[name])["trace"].get("observations", [])}
            if any(exported.get(key) != row for key, row in rows.items()):
                raise UserActionRequired("Overlapping observation pages and native export differ for this Trace.")
        else:
            put(name, json_bytes({"data": [rows[key] for key in sorted(rows)]}))
    return output


def recover(directory):
    """Finish an interrupted publish. Caller owns the session lock."""
    pending = safe_path(directory, ".pending")
    if not pending.exists():
        return
    session = store.read_json(safe_path(pending, "session.json"))
    for entry in session["archive"]["files"]:
        relative = entry["path"]
        if not relative.startswith("raw/"):
            raise UserActionRequired("Unsafe pending archive path.")
        staged = safe_path(pending, relative)
        target = safe_path(directory, relative)
        if staged.exists():
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(staged, target)
        if not target.is_file() or digest_file(target) != entry["sha256"]:
            raise UserActionRequired("Interrupted archive update has damaged material.")
    write_manifest(safe_path(directory, "session.json"), session)
    shutil.rmtree(pending)


def save(project_root, session_id, source, files, *, scope, completeness, status="completed", errors=None):
    source = store.normalize_source(source)
    materials = normalize_files(files)
    root, ignored = store.ensure_workspace(project_root)
    directory = safe_path(root, component(session_id))
    if not directory.exists() and (root / store.session_key(session_id, source)).is_dir():
        raise UserActionRequired("This session has a legacy archive. Migrate it before importing again.")
    directory.mkdir(mode=0o700, exist_ok=True)
    preview = {}
    with store.session_write_lock(directory):
        recover(directory)
        path = safe_path(directory, "session.json")
        if path.exists():
            session = store.load_session(directory)
            if session["source"] != source or session["session_id"] != session_id or session["schema_version"] != 2:
                raise UserActionRequired("Archive directory belongs to a different session or source.")
        else:
            session = {
                "schema_version": 2,
                "session_id": session_id,
                "source": source,
                "created_at": now(),
                "revision": 0,
                "detections": [],
            }
        entries = {e["path"]: e for e in session.get("archive", {}).get("files", [])}
        with TemporaryDirectory(prefix=".import-", dir=directory) as temp:
            stage = Path(temp)
            for name, body in materials.items():
                relative = "raw/" + name
                target = safe_path(directory, relative)
                if status != "completed" and relative in entries:
                    continue  # Incomplete pagination cannot replace a previously retained Trace.
                digest = hashlib.sha256(body).hexdigest()
                if not target.exists() or digest_file(target) != digest:
                    staged = safe_path(stage, relative)
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    staged.write_bytes(body)
                entries[relative] = {"path": relative, "sha256": digest, "bytes": len(body)}
            if len({key.casefold() for key in entries}) != len(entries):
                raise UserActionRequired("Trace IDs differ only in case and would share a local file.")
            manifest = {
                "scope": scope,
                "completeness": completeness,
                "status": status,
                "errors": errors or [],
                "files": [entries[key] for key in sorted(entries)],
            }
            if session.get("archive") != manifest or (stage / "raw").exists():
                # Validate unchanged files before publishing: a corrupt unrelated file
                # must not strand an otherwise retryable import behind a pending transaction.
                for entry in manifest["files"]:
                    relative = entry["path"]
                    if not safe_path(stage, relative).exists():
                        store.validate_ref(directory, entry)
                session.update(archive=manifest, revision=session["revision"] + 1)
                write_manifest(stage / "session.json", session)
                stage.rename(safe_path(directory, ".pending"))
                recover(directory)
                if safe_path(directory, "preview.html").is_file():
                    from agent_tune_kit.visualize.trace import generate_trace

                    try:
                        preview = generate_trace(
                            project_root, session_id=session_id, overwrite=True, open_browser=False
                        )
                    except (OSError, ValueError, UserActionRequired) as exc:
                        preview = {"warnings": [f"Trace saved. Regenerate the preview: {exc}"]}
    return {
        **({"preview": preview} if preview else {}),
        "session_id": session_id,
        "session_dir": str(directory),
        "manifest": manifest,
        "gitignore_updated": ignored,
    }
