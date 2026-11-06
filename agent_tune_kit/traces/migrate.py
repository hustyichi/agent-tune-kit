"""Migrate a legacy single-snapshot trace export without editing the original."""

import json
import re
import shutil
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.evidence import json_bytes, safe_path, write_manifest

from . import archive, store


def migrate(project_root, session_id, source=None):
    old = store.resolve_session(project_root, session_id, source)
    with store.session_write_lock(old):
        session = store.load_session(old)
        if session["schema_version"] == 2:
            return {"session_dir": str(old), "already_current": True}
        if len(session["captures"]) != 1:
            raise UserActionRequired("Automatic migration requires one snapshot; the legacy archive remains readable.")
        cid = session["captures"][0]
        manifest = store.read_capture(old, cid)
        if not manifest["sealed"]:
            raise UserActionRequired("Finish the legacy capture before migrating.")
        destination = safe_path(old.parent, archive.component(session_id))
        if destination.exists():
            raise UserActionRequired("The destination already exists.")
        with TemporaryDirectory(dir=old.parent, prefix=".migrate-") as temp:
            paths, files = {}, {}
            for entry in manifest["files"]:
                relative = f"captures/{cid}/{entry['path']}"
                data = safe_path(old, relative).read_bytes()
                normalized = archive.normalize_files({entry["path"]: data})
                if len(normalized) != 1:
                    raise UserActionRequired("Automatic migration requires native single-Trace exports.")
                name, body = next(iter(normalized.items()))
                if body != data or "/" in name or name in files:
                    raise UserActionRequired("The archive requires explicit mapping of legacy evidence paths.")
                files[name] = data
                paths[relative] = "raw/" + name
            result = archive.save(
                temp,
                session_id,
                session["source"],
                files,
                scope=manifest["scope"],
                completeness=manifest["completeness"],
                status=manifest["status"],
                errors=manifest["errors"],
            )
            new = Path(result["session_dir"])
            current = store.load_session(new)
            issues = safe_path(old, "issues")
            if issues.exists():
                if any(p.is_symlink() for p in issues.rglob("*")):
                    raise UserActionRequired("Unsafe symlink in issue records.")
                shutil.copytree(issues, new / "issues")
            completed, active = set(), set()

            @cache
            def raw_ref(relative, sha, size, pointer):
                store.validate_ref(old, {"path": relative, "sha256": sha, "bytes": size})
                return store.evidence_ref(new, paths[relative], pointer)

            def rewrite(value):
                if isinstance(value, list):
                    return [rewrite(child) for child in value]
                if isinstance(value, str):
                    for before, after in paths.items():
                        value = value.replace(before, after)
                    return value
                if not isinstance(value, dict):
                    return value
                relative = value.get("path")
                if isinstance(relative, str) and "sha256" in value and "bytes" in value:
                    if relative in paths:
                        return {**value, **raw_ref(relative, value["sha256"], value["bytes"], value.get("pointer"))}
                    if relative.startswith("issues/"):
                        store.validate_ref(old, value)
                        convert_record(relative)
                        return {**value, **store.evidence_ref(new, relative, value.get("pointer"))}
                return {key: rewrite(child) for key, child in value.items() if not (key == "capture" and child == cid)}

            def convert_record(relative):
                if relative in completed:
                    return
                if relative in active:
                    raise UserActionRequired("Cannot migrate cyclic record references.")
                active.add(relative)
                path = safe_path(new, relative)
                if (
                    path.name in {"issue.json", "change.json", "result.json"}
                    and "evidence" not in path.relative_to(new).parts
                ):
                    write_manifest(path, rewrite(store.read_json(path)))
                elif path.suffix == ".md" and path.parent.name == "diagnoses":
                    original = path.read_text(encoding="utf-8")
                    text = rewrite(original)
                    match = re.match(r"<!-- atk-issue-snapshot (.*?) -->", original)
                    if match:
                        header = json_bytes(rewrite(json.loads(match[1]))).decode().replace("-->", "--\\u003e")
                        text = "<!-- atk-issue-snapshot " + header + " -->" + text.split(" -->", 1)[1]
                    path.write_text(text, encoding="utf-8")
                active.remove(relative)
                completed.add(relative)

            for path in (new / "issues").rglob("*"):
                if path.is_file():
                    convert_record(path.relative_to(new).as_posix())
            for detection in session["detections"]:
                detection.pop("captures")
                for ref in detection["inputs"]:
                    store.validate_ref(old, ref)
                    if ref["path"] != f"captures/{cid}/manifest.json":
                        raise UserActionRequired("Unexpected detection input; original archive preserved.")
                detection["inputs"] = current["archive"]["files"]
            current.update(
                created_at=session["created_at"],
                revision=session["revision"] + 1,
                detections=rewrite(session["detections"]),
            )
            write_manifest(new / "session.json", current)
            new.rename(destination)
    return {"session_dir": str(destination), "legacy_dir": str(old)}
