"""Import batch results and Langfuse exports without inventing missing evidence."""

from __future__ import annotations

import csv
import gzip
import json
import re
from collections import defaultdict
from pathlib import Path

from .core import ATKError, canonical, digest, new_id, now, write_json

MAX_SOURCE_BYTES = 100 * 1024 * 1024
SECRET_KEY = re.compile(r"(?:password|secret|token|authorization|api[_-]?key|private[_-]?key|cookie)", re.I)
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+")


def redact(value: object, extra_keys: set[str] | None = None) -> object:
    extra_keys = extra_keys or set()
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SECRET_KEY.search(key) or key in extra_keys else redact(item, extra_keys)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, extra_keys) for item in value]
    if isinstance(value, str):
        return BEARER.sub("Bearer [REDACTED]", value)
    return value


def _source_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ATKError("UNSUPPORTED_EXPORT_FORMAT", f"source does not exist: {path}")
    files = sorted(file for file in path.iterdir() if file.is_file() and not file.name.startswith("_"))
    if not files:
        raise ATKError("UNSUPPORTED_EXPORT_FORMAT", "directory contains no export files")
    return files


def _read_source(path: Path) -> tuple[object, str]:
    suffixes = path.suffixes
    compressed = bool(suffixes and suffixes[-1] == ".gz")
    extension = suffixes[-2] if compressed and len(suffixes) > 1 else path.suffix
    if extension not in {".json", ".jsonl", ".csv"}:
        raise ATKError("UNSUPPORTED_EXPORT_FORMAT", f"unsupported export format: {path.name}")
    with gzip.open(path, "rb") if compressed else path.open("rb") as handle:
        data = handle.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ATKError("UNSUPPORTED_EXPORT_FORMAT", f"export exceeds {MAX_SOURCE_BYTES} bytes: {path.name}")
    try:
        content = data.decode("utf-8-sig")
        if extension == ".json":
            result = json.loads(content)
        elif extension == ".jsonl":
            result = [json.loads(line) for line in content.splitlines() if line.strip()]
        else:
            result = list(csv.DictReader(content.splitlines()))
    except (UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        raise ATKError("UNSUPPORTED_EXPORT_FORMAT", f"cannot parse {path.name}: {exc}") from exc
    return result, digest(data)


def _rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload["data"] if isinstance(payload.get("data"), list) else [payload]
    else:
        raise ATKError("AMBIGUOUS_MAPPING", "export must be an object or array of objects")
    if any(not isinstance(row, dict) for row in rows):
        raise ATKError("AMBIGUOUS_MAPPING", "export rows must be objects")
    return rows


def _alias(row: dict, *names: str):
    for name in names:
        if name in row:
            return row[name]
    return None


def _normalize_csv_json_columns(rows: list[dict], columns: list[str]) -> list[dict]:
    for row in rows:
        for name in columns:
            if name in row and row[name]:
                try:
                    row[name] = json.loads(row[name])
                except json.JSONDecodeError as exc:
                    raise ATKError("AMBIGUOUS_MAPPING", f"declared JSON column {name} is invalid") from exc
    return rows


def _trace_record(trace: dict, *, namespace: str, locator: str, extra_keys: set[str]) -> tuple[dict, list[dict]]:
    trace_id = _alias(trace, "id", "traceId", "trace_id")
    if not trace_id:
        raise ATKError("AMBIGUOUS_MAPPING", f"Trace ID missing at {locator}")
    observations = trace.get("observations", [])
    if observations is None:
        observations = []
    if not isinstance(observations, list):
        raise ATKError("AMBIGUOUS_MAPPING", "observations must be an array")
    observed_ids = set()
    events = []
    index = []
    for offset, observation in enumerate(observations):
        if not isinstance(observation, dict) or not observation.get("id"):
            raise ATKError("AMBIGUOUS_MAPPING", "Observation requires a distinct ID")
        event_id = observation["id"]
        if event_id in observed_ids:
            raise ATKError("AMBIGUOUS_MAPPING", f"duplicate Observation ID: {event_id}")
        observed_ids.add(event_id)
        event = redact(observation, extra_keys)
        events.append(event)
        index.append(
            {
                "evidence_id": f"observation:{event_id}",
                "fingerprint": digest(event),
                "locator": f"{locator}/observations/{offset}",
                "origin": "original_execution",
                "source_component": observation.get("name") or observation.get("type") or "unknown",
                "source_trace_id": trace_id,
                "source_observation_id": event_id,
            }
        )
    missing_parents = [
        parent
        for event in events
        if (parent := event.get("parentObservationId") or event.get("parent_observation_id"))
        and parent not in observed_ids
    ]
    missing = []
    if not observations:
        missing.append("observations")
    if missing_parents:
        missing.append("parent_observations")
    if "input" not in trace:
        missing.append("trace_input")
    if "output" not in trace:
        missing.append("trace_output")
    record = {
        "id": new_id("record"),
        "source_namespace": namespace,
        "source_trace_id": trace_id,
        "source_session_id": _alias(trace, "sessionId", "session_id"),
        "execution": None,
        "input_present": "input" in trace,
        "input": redact(trace.get("input"), extra_keys),
        "output_present": "output" in trace,
        "output": redact(trace.get("output"), extra_keys),
        "events": events,
        "external_scores": redact(trace.get("scores", []), extra_keys),
        "metrics": redact({"latency": trace.get("latency"), "totalCost": trace.get("totalCost")}, extra_keys),
        "analysis": "limited" if missing else "sufficient",
        "missing": missing,
        "replayability": "needs_setup",
        "source_locator": locator,
    }
    index.insert(
        0,
        {
            "evidence_id": f"trace:{trace_id}",
            "fingerprint": digest(redact(trace, extra_keys)),
            "locator": locator,
            "origin": "original_execution",
            "source_component": "trace",
            "source_trace_id": trace_id,
        },
    )
    return record, index


def import_evidence(root: Path, request: dict) -> dict:
    source = Path(request["source"]).expanduser().resolve()
    source_kind = request["source_kind"]
    namespace = request.get("source_namespace")
    profile = request.get("adapter_profile")
    mapping_version = request.get("mapping_version")
    if not namespace or not mapping_version:
        raise ATKError("AMBIGUOUS_MAPPING", "source_namespace and mapping_version are required")
    if source_kind == "langfuse" and profile not in {"langfuse_trace_bundle", "langfuse_observation_rows"}:
        raise ATKError("AMBIGUOUS_MAPPING", "explicit Langfuse adapter_profile is required")
    if source_kind not in {"langfuse", "batch_results"}:
        raise ATKError("AMBIGUOUS_MAPPING", "unsupported source_kind")
    extra_keys = set(request.get("redact_keys", []))
    files = _source_files(source)
    source_refs = []
    rows_by_file = []
    for file in files:
        payload, fingerprint = _read_source(file)
        rows = _rows(payload)
        rows = _normalize_csv_json_columns(rows, request.get("json_columns", []))
        source_refs.append({"path": str(file), "sha256": fingerprint})
        rows_by_file.append((file, rows))
    source_fingerprint = digest(source_refs)
    previous_revision = None
    for old in (root / "evidence").glob("*/manifest.json"):
        previous = json.loads(old.read_text(encoding="utf-8"))
        if (
            previous.get("source_fingerprint") == source_fingerprint
            and previous.get("source_namespace") == namespace
            and previous.get("mapping_version") == mapping_version
        ):
            return previous
        if (
            previous.get("source_namespace") == namespace
            and [item.get("path") for item in previous.get("source_refs", [])] == [item["path"] for item in source_refs]
            and (previous_revision is None or previous["created_at"] > previous_revision["created_at"])
        ):
            previous_revision = previous
    records = []
    evidence_index = []
    if source_kind == "langfuse" and profile == "langfuse_trace_bundle":
        for file, rows in rows_by_file:
            for offset, row in enumerate(rows):
                trace = row.get("trace", row)
                if not isinstance(trace, dict) or "observations" not in trace and "id" not in trace:
                    raise ATKError("AMBIGUOUS_MAPPING", f"not a Trace bundle: {file.name}")
                locator = f"{file}#/trace" if "trace" in row else f"{file}#/{offset}"
                record, index = _trace_record(trace, namespace=namespace, locator=locator, extra_keys=extra_keys)
                records.append(record)
                evidence_index.extend(index)
    elif source_kind == "langfuse":
        grouped = defaultdict(list)
        for file, rows in rows_by_file:
            for offset, row in enumerate(rows):
                trace_id = _alias(row, "traceId", "trace_id")
                event_id = row.get("id")
                if not trace_id or not event_id:
                    raise ATKError("AMBIGUOUS_MAPPING", "Observation rows require traceId and id")
                grouped[str(trace_id)].append((row, f"{file}#/{offset}"))
        for trace_id, group in grouped.items():
            trace = {"id": trace_id, "observations": [row for row, _ in group]}
            record, index = _trace_record(trace, namespace=namespace, locator=group[0][1], extra_keys=extra_keys)
            record["analysis"] = "limited"
            record["missing"].extend(["trace_input", "trace_output", "trace_summary"])
            records.append(record)
            evidence_index.extend(index)
    else:
        mapping = request.get("mapping", {})
        if not mapping.get("input") or not mapping.get("output"):
            raise ATKError("AMBIGUOUS_MAPPING", "batch_results requires input and output field mapping")
        for file, rows in rows_by_file:
            for offset, row in enumerate(rows):
                locator = f"{file}#/{offset}"
                redacted = redact(row, extra_keys)
                record = {
                    "id": new_id("record"),
                    "source_namespace": namespace,
                    "case_id": row.get(mapping.get("case_id")) if mapping.get("case_id") else None,
                    "execution": None,
                    "input_present": mapping["input"] in row,
                    "input": redacted.get(mapping["input"]),
                    "output_present": mapping["output"] in row,
                    "output": redacted.get(mapping["output"]),
                    "source_locator": locator,
                    "analysis": "limited",
                    "missing": ["execution_identity", "component_manifest"],
                    "replayability": "needs_setup",
                }
                records.append(record)
                evidence_index.append(
                    {
                        "evidence_id": record["id"],
                        "fingerprint": digest(redacted),
                        "locator": locator,
                        "origin": "original_execution",
                        "source_component": "batch_result",
                    }
                )
    if not records:
        raise ATKError("INCOMPLETE_EVIDENCE", "source has no task records")
    ids = [record.get("source_trace_id") for record in records if record.get("source_trace_id")]
    if len(ids) != len(set(ids)):
        raise ATKError("AMBIGUOUS_MAPPING", "duplicate Trace IDs in one import")
    batch_id = new_id("batch")
    folder = root / "evidence" / batch_id
    folder.mkdir(parents=True, exist_ok=False)
    records_path = folder / "records.jsonl"
    records_path.write_bytes(b"\n".join(canonical(record) for record in records) + b"\n")
    manifest = {
        "schema_version": 2,
        "id": batch_id,
        "created_at": now(),
        "purpose": "evaluation",
        "source_type": source_kind,
        "source_namespace": namespace,
        "adapter_profile": profile,
        "mapping_version": mapping_version,
        "source_refs": source_refs,
        "source_fingerprint": source_fingerprint,
        "supersedes_batch_id": previous_revision["id"] if previous_revision else None,
        "records_ref": "records.jsonl",
        "records_sha256": digest(records_path),
        "record_count": len(records),
        "planned_count": None,
        "filtered_scope": request.get("filtered_scope"),
        "completeness": "unknown" if source_kind == "langfuse" else "limited",
        "redaction": {"default_secret_keys": True, "extra_keys": sorted(extra_keys)},
        "evidence_index": evidence_index,
        "status": "sealed",
    }
    write_json(folder / "manifest.json", manifest, immutable=True)
    return manifest
