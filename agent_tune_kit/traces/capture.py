"""Retain declared local formats without executing project code or allocating eval versions."""

import csv
import hashlib
import io
import json
import re
from pathlib import Path

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.evidence import inspect_execution, json_bytes, read_manifest, safe_path, verify_manifest

from . import store

_SECRET_KEYS = {
    "password",
    "public_key",
    "publickey",
    "private_key",
    "privatekey",
    "client_secret",
    "clientsecret",
    "accesstoken",
    "refreshtoken",
    "apitoken",
    "authorization",
    "cookie",
    "cookies",
    "secret",
    "secret_key",
    "secretkey",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
}


def load_config(project_root):
    path = safe_path(Path(project_root), ".atk/traces/config.json")
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError, UnicodeError):
        raise UserActionRequired("Trace configuration is missing or damaged.") from None


def retention_policy(project_root, retention=None):
    policy = retention if retention is not None else load_config(project_root).get("retention", {})
    if not isinstance(policy, dict) or policy.get("allow_body") is not True:
        raise UserActionRequired(
            "Trace body retention is not enabled; set retention.allow_body after checking the source."
        )
    if not isinstance(policy.get("redact_keys", []), list) or any(
        not isinstance(key, str) for key in policy.get("redact_keys", [])
    ):
        raise UserActionRequired("retention.redact_keys must contain field names.")
    maximum = policy.get("max_string_chars")
    if maximum is not None and (not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1):
        raise UserActionRequired("retention.max_string_chars must be a positive integer.")
    return policy


def retain(value, policy, *, secret_values=()):
    """Apply exclusion before serialization; references bind the resulting bytes."""
    keys = _SECRET_KEYS | {key.casefold() for key in policy.get("redact_keys", [])}
    maximum = policy.get("max_string_chars")
    changes = set()

    def clean(item, field=None):
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if str(key).casefold() in keys:
                    result[key] = "[REDACTED]"
                    changes.add("secret-field exclusion")
                else:
                    result[key] = clean(child, str(key))
            return result
        if isinstance(item, list):
            return [clean(child) for child in item]
        if isinstance(item, str):
            text = item
            try:
                nested = json.loads(item)
            except (ValueError, UnicodeError):
                nested = None
            if isinstance(nested, (dict, list)):
                nested_retained = clean(nested)
                if nested_retained != nested:
                    text = json.dumps(nested_retained, ensure_ascii=False, separators=(",", ":"))
                    changes.add("serialized JSON retention")
            for secret in secret_values:
                if secret and secret in text:
                    text = text.replace(secret, "[REDACTED]")
            text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
            text = re.sub(r"\b(?:sk|pk)-lf-[A-Za-z0-9_-]+", "[REDACTED]", text)
            text = re.sub(
                r"-----BEGIN ((?:[A-Z]+ )*PRIVATE KEY)-----.*?-----END \1-----",
                "[REDACTED PRIVATE KEY]",
                text,
                flags=re.DOTALL,
            )
            if text != item:
                changes.add("secret-value exclusion")
            identity_fields = {
                "id",
                "traceId",
                "sessionId",
                "session_id",
                "parentObservationId",
                "execution_id",
                "run_id",
                "event_id",
            }
            if maximum is not None and field not in identity_fields and len(text) > maximum:
                changes.add("string clipping")
                return text[:maximum] + " [TRUNCATED BY RETENTION]"
            return text
        return item

    return clean(value), sorted(changes)


def read_local(path):
    path = Path(path).absolute()
    if any(parent.is_symlink() for parent in [path, *path.parents]):
        raise UserActionRequired("Local trace sources must not contain symbolic links.")
    if not path.is_file():
        raise UserActionRequired("Local trace source is not a regular file.")
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in identity) or len(data) != before.st_size:
        raise UserActionRequired("Local trace source changed while reading.")
    return data


def _json(data):
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise UserActionRequired("Unsupported or damaged trace JSON; source bytes were not archived.") from None


def _association(association, session_id, path, data):
    if not isinstance(association, dict) or association.get("session_id") != session_id or not association.get("basis"):
        return False
    for item in association.get("files", []):
        if (
            isinstance(item, dict)
            and item.get("path") in {str(path), path.name}
            and item.get("sha256") == hashlib.sha256(data).hexdigest()
        ):
            return True
    return False


def _check_source_fields(record, session_id, source):
    for key in ("sessionId", "session_id"):
        if key in record and record[key] is not None and record[key] != session_id:
            raise UserActionRequired("Trace material contains conflicting Session ID ownership.")
    if source:
        for key in ("projectId", "project_id"):
            if record.get(key) is not None and source.get("project") and record[key] != source["project"]:
                raise UserActionRequired("Trace material project differs from the configured source.")
        if (
            record.get("environment") is not None
            and source.get("environment")
            and record["environment"] != source["environment"]
        ):
            raise UserActionRequired("Trace material environment differs from the configured source.")


def _merge_rows(seen, rows):
    for key, row in rows.items():
        if key in seen and seen[key] != row:
            raise UserActionRequired("Conflicting duplicate observation across selected materials.")
        seen[key] = row


def validate_document(document, session_id, *, associated=False, source=None):
    """Recognize native Langfuse shapes and check every row before creating an archive."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise UserActionRequired("An explicit Session ID is required; a Trace ID is not a session identity.")
    if not isinstance(document, dict):
        raise UserActionRequired("Unsupported trace JSON format.")
    if isinstance(document.get("trace"), dict):
        trace = document["trace"]
        if (
            not isinstance(trace.get("id"), str)
            or not trace["id"]
            or not isinstance(trace.get("observations", []), list)
        ):
            raise UserActionRequired("Invalid Langfuse trace export.")
        rows = trace.get("observations", [])
        _check_source_fields(trace, session_id, source)
        if isinstance(document.get("export"), dict):
            _check_source_fields(document["export"], session_id, source)
        identity = trace.get("sessionId", trace.get("session_id"))
        if identity != session_id and not (identity is None and associated):
            raise UserActionRequired("Trace export Session ID is absent or conflicts with the selected session.")
        seen = {}
        for row in rows:
            if not isinstance(row, dict):
                raise UserActionRequired("Invalid observation in trace export.")
            if (
                (not isinstance(row.get("id"), str) or not row["id"])
                or row.get("sessionId", session_id) != session_id
                or row.get("traceId", trace["id"]) != trace["id"]
            ):
                raise UserActionRequired("Trace export contains conflicting session or trace ownership.")
            _check_source_fields(row, session_id, source)
            _merge_rows(seen, {(trace["id"], row["id"]): row})
        return {"format": "langfuse-trace-export", "trace_ids": [trace["id"]], "observations": len(seen), "_rows": seen}
    if isinstance(document.get("data"), list):
        trace_ids, seen = set(), {}
        for row in document["data"]:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not row["id"]
                or not isinstance(row.get("traceId"), str)
                or not row["traceId"]
            ):
                raise UserActionRequired("Invalid Langfuse observation row.")
            _check_source_fields(row, session_id, source)
            identity = row.get("sessionId", row.get("session_id"))
            if identity != session_id and not (identity is None and associated):
                raise UserActionRequired("Observation Session ID is absent or conflicts with the selected session.")
            if source and source.get("project") and row.get("projectId") not in {None, source["project"]}:
                raise UserActionRequired("Observation project differs from the configured source.")
            if source and source.get("environment") and row.get("environment") not in {None, source["environment"]}:
                raise UserActionRequired("Observation environment differs from the configured source.")
            key = (row["traceId"], row["id"])
            if key in seen and seen[key] != row:
                raise UserActionRequired("Conflicting duplicate observation in the same snapshot.")
            seen[key] = row
            trace_ids.add(row["traceId"])
        return {
            "format": "langfuse-observations-v2",
            "trace_ids": sorted(trace_ids),
            "observations": len(seen),
            "_rows": seen,
        }
    raise UserActionRequired("Unsupported trace JSON format; expected a trace export or observation page.")


def _execution_materials(path, session_id, policy, association):
    path = Path(path)
    manifest = inspect_execution(path)
    raw_manifest = read_local(safe_path(path, "execution.json"))
    materials, native_ids, transformations = {}, set(), set()
    for entry in manifest["files"]:
        if entry["path"] not in {"input.json", "output.json", "events.jsonl", "app.log"}:
            continue
        data = read_local(safe_path(path, entry["path"]))
        if entry["path"].endswith(".jsonl"):
            rows = [_json(line) for line in data.splitlines() if line.strip()]
            if any(not isinstance(row, dict) for row in rows):
                raise UserActionRequired("Native event JSONL requires an object on every nonempty line.")
            for row in rows:
                attributes = row.get("attributes", {})
                if isinstance(attributes, dict):
                    identity = attributes.get("session_id", attributes.get("sessionId"))
                    if identity:
                        native_ids.add(identity)
            retained, changes = retain(rows, policy)
            materials[entry["path"]] = b"".join(json_bytes(row) + b"\n" for row in retained)
        else:
            value = _json(data) if entry["path"].endswith(".json") else data.decode("utf-8")
            retained, changes = retain(value, policy)
            materials[entry["path"]] = json_bytes(retained) if entry["path"].endswith(".json") else retained.encode()
        transformations.update(changes)
    linked = _association(association, session_id, path / "execution.json", raw_manifest)
    if native_ids and native_ids != {session_id} or not native_ids and not linked:
        raise UserActionRequired(
            "ATK execution requires native Session ID evidence or a hash-bound association manifest."
        )
    run_path = path.parent.parent
    run_verified = False
    if (run_path / "run.json").exists():
        run = read_manifest(run_path, "run.json")
        verify_manifest(run_path, run)
        relative = run.get("executions", {}).get(manifest["execution_id"])
        if (
            run.get("run_id") != manifest["run_id"]
            or not relative
            or safe_path(run_path, relative).resolve() != path.resolve()
        ):
            raise UserActionRequired("ATK run and execution ownership conflict.")
        run_verified = True
    provenance = {
        "format": "atk-execution-copy",
        "session_id": session_id,
        "run_id": manifest["run_id"],
        "execution_id": manifest["execution_id"],
        "original_evidence_id": manifest.get("evidence_id"),
        "original_manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
        "original_manifest_verified": True,
        "run_ownership_verified": run_verified,
        "original_files": [{key: entry[key] for key in ("path", "sha256", "bytes")} for entry in manifest["files"]],
        "retained_files": list(materials),
        "transformed_copy": True,
        "association_basis": association.get("basis") if linked else "native event Session ID",
    }
    materials["provenance.json"] = json_bytes(provenance)
    return materials, {"format": "atk-execution-copy", "trace_ids": [], "observations": 0}, sorted(transformations)


def _capture_materials(path, session_id, source, policy):
    manifest_bytes = read_local(safe_path(path, "manifest.json"))
    manifest = _json(manifest_bytes)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or not manifest.get("sealed")
        or manifest.get("session_id") != session_id
        or manifest.get("source") != store.normalize_source(source)
    ):
        raise UserActionRequired(
            "Capture package identity differs from the selected Session ID or source, or is not sealed."
        )
    expected = manifest.get("manifest_sha256")
    payload = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if hashlib.sha256(json_bytes(payload)).hexdigest() != expected:
        raise UserActionRequired("Capture package manifest integrity check failed.")
    materials, traces, count, changes, seen, observations = {}, set(), 0, set(), set(), {}
    for entry in manifest.get("files", []):
        relative = entry.get("path", "")
        if not relative.startswith("raw/") or relative in seen:
            raise UserActionRequired("Capture package contains an unsafe or duplicate file path.")
        seen.add(relative)
        data = read_local(safe_path(path, relative))
        if len(data) != entry.get("bytes") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            raise UserActionRequired("Capture package file integrity check failed.")
        document = _json(data)
        details = validate_document(document, session_id, source=source)
        _merge_rows(observations, details.get("_rows", {}))
        retained, transformations = retain(document, policy)
        materials[relative[4:]] = json_bytes(retained)
        changes.update(transformations)
        traces.update(details["trace_ids"])
        count += details["observations"]
    if not materials:
        raise UserActionRequired("Capture package contains no supported observation pages.")
    return (
        materials,
        {
            "format": "langfuse-capture-package",
            "trace_ids": sorted(traces),
            "observations": len(observations),
            "_rows": observations,
            "original_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        },
        sorted(changes),
    )


def _is_source_summary(document):
    return (
        isinstance(document, dict)
        and "session_id" in document
        and isinstance(document.get("traces"), list)
        and "trace_count" in document
    )


def _validate_source_summary(document, session_id, source, selected):
    _check_source_fields(document, session_id, source)
    if document.get("session_id") != session_id:
        raise UserActionRequired("Export summary must identify the selected Session ID.")
    matched, output_names = set(), set()
    for entry in document["traces"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("output_path"), str) or not entry.get("trace_id"):
            raise UserActionRequired("Export summary has an invalid trace entry.")
        name = Path(entry["output_path"]).name
        if name in output_names:
            raise UserActionRequired("Export summary has ambiguous duplicate output names.")
        output_names.add(name)
        for path, data, native in selected:
            if path.name != name:
                continue
            if len(data) != entry.get("bytes") or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                raise UserActionRequired(
                    "Export summary file size or SHA-256 does not match the selected source bytes."
                )
            if (
                not isinstance(native, dict)
                or not isinstance(native.get("trace"), dict)
                or native["trace"].get("id") != entry["trace_id"]
            ):
                raise UserActionRequired("Export summary Trace ID conflicts with the selected native export.")
            matched.add(str(path))
    if not matched:
        raise UserActionRequired("Export summary does not bind any selected native trace file.")
    for path, _, native in selected:
        if isinstance(native, dict) and isinstance(native.get("trace"), dict) and str(path) not in matched:
            raise UserActionRequired("A selected native export is not covered by the supplied export summary.")
    return sorted(matched)


def import_session(project_root, session_id, source, paths, *, retention=None, association=None):
    policy = retention_policy(project_root, retention)
    if not session_id or not isinstance(session_id, str) or not session_id.strip():
        raise UserActionRequired("An explicit Session ID is required.")
    if isinstance(paths, (str, Path)):
        paths = [paths]
    if not paths:
        raise UserActionRequired("Select at least one supported local trace source.")
    materials, inputs, traces, transformations, count, seen = {}, [], set(), set(), 0, {}
    selected = []
    for value in paths:
        path = Path(value).absolute()
        if path.suffix.casefold() == ".json" and not path.is_dir():
            data = read_local(path)
            selected.append((path, data, _json(data)))
    summaries = {str(path): document for path, _, document in selected if _is_source_summary(document)}
    summary_bindings = {
        path: _validate_source_summary(document, session_id, source, selected) for path, document in summaries.items()
    }
    for index, value in enumerate(paths, 1):
        path = Path(value).absolute()
        if any(parent.is_symlink() for parent in [path, *path.parents]):
            raise UserActionRequired("Local trace sources must not contain symbolic links.")
        prefix = f"sources/{index:04d}/"
        if path.is_dir():
            if (path / "manifest.json").exists():
                files, details, changes = _capture_materials(path, session_id, source, policy)
            else:
                files, details, changes = _execution_materials(path, session_id, policy, association)
            materials.update({prefix + key: data for key, data in files.items()})
            origin = {"path": str(path), "format": details["format"]}
            if details.get("original_manifest_sha256"):
                origin["original_manifest_sha256"] = details["original_manifest_sha256"]
                origin["original_manifest_verified"] = True
        else:
            data = read_local(path)
            linked = _association(association, session_id, path, data)
            if path.suffix.casefold() in {".csv", ".log", ".txt", ".jsonl"}:
                if not linked:
                    raise UserActionRequired(
                        "Local row or log evidence requires a hash-bound Session ID association manifest."
                    )
                if path.suffix.casefold() == ".jsonl":
                    rows = [_json(line) for line in data.splitlines() if line.strip()]
                    if any(not isinstance(row, dict) for row in rows):
                        raise UserActionRequired("Local event JSONL requires an object on every nonempty line.")
                    retained, changes = retain(rows, policy)
                    body = b"".join(json_bytes(row) + b"\n" for row in retained)
                elif path.suffix.casefold() == ".csv":
                    reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
                    if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                        raise UserActionRequired("Local evidence CSV requires unique column names.")
                    rows = list(reader)
                    if any(None in row or any(value is None for value in row.values()) for row in rows):
                        raise UserActionRequired("Local evidence CSV has inconsistent row widths.")
                    retained, changes = retain(rows, policy)
                    output = io.StringIO(newline="")
                    writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
                    writer.writeheader()
                    writer.writerows(retained)
                    body = output.getvalue().encode()
                else:
                    retained, changes = retain(data.decode("utf-8"), policy)
                    body = retained.encode()
                details = {"format": "associated-local-evidence", "trace_ids": [], "observations": 0}
            else:
                document = _json(data)
                if str(path) in summaries:
                    details = {"format": "langfuse-export-summary", "trace_ids": [], "observations": 0}
                else:
                    details = validate_document(document, session_id, associated=linked, source=source)
                retained, changes = retain(document, policy)
                body = json_bytes(retained)
            materials[prefix + path.name] = body
            origin = {
                "path": str(path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "format": details["format"],
            }
            if str(path) in summary_bindings:
                origin["verified_selected_files"] = summary_bindings[str(path)]
        _merge_rows(seen, details.get("_rows", {}))
        transformations.update(changes)
        traces.update(details["trace_ids"])
        count += details["observations"]
        inputs.append(origin)
    if association is not None:
        retained, changes = retain(association, policy)
        materials["association.json"] = json_bytes(retained)
        transformations.update(changes)
    if seen:
        count = len(seen)
    completeness = {
        "status": "partial",
        "trace_count": len(traces),
        "observation_count": count,
        "trace_ids": sorted(traces),
        "gaps": ["Local selection does not prove complete session coverage."],
        "transformations": sorted(transformations),
    }
    result = store.create_capture(
        project_root,
        session_id,
        source,
        materials,
        scope={"kind": "local-import", "inputs": inputs},
        completeness=completeness,
    )
    result.update(trace_count=len(traces), observation_count=count)
    return result


def fetch_session(project_root, session_id, config=None, *, from_time=None, to_time=None, retention=None):
    from .langfuse import fetch_session as fetch

    return fetch(project_root, session_id, config, from_time=from_time, to_time=to_time, retention=retention)
