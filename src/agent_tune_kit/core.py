"""Small, auditable contracts shared by ATK's deterministic operations."""

from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 2
ASSESSMENT_COLUMNS = (
    "record_id",
    "dimension",
    "validity",
    "validity_reason",
    "verdict",
    "score",
    "reason",
    "evidence_refs",
    "judger_kind",
)
ERROR_CODES = {
    "UNSUPPORTED_EXPORT_FORMAT",
    "AMBIGUOUS_MAPPING",
    "INCOMPLETE_EVIDENCE",
    "NOT_REPLAYABLE",
    "JUDGER_INVALID",
    "LOADING_UNVERIFIED",
    "BASELINE_DRIFT",
    "SCOPE_VIOLATION",
    "REVISION_MISMATCH",
    "COMPARISON_INVALID",
    "BUDGET_EXHAUSTED",
    "WORKSPACE_CONFLICT",
    "GIT_OPERATION_INTERRUPTED",
    "COMMIT_FAILED",
    "DIRTY_BASELINE",
    "UNSUPPORTED_GIT_STATE",
}


class ATKError(Exception):
    def __init__(self, code: str, message: str):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown ATK error code: {code}")
        self.code = code
        super().__init__(message)


def new_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4()}"


def safe_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ATKError("SCOPE_VIOLATION", f"unsafe artifact ID: {value}")
    return value


def now() -> str:
    return datetime.now(UTC).isoformat()


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: bytes | Path | object) -> str:
    if isinstance(value, Path):
        value = value.read_bytes()
    elif not isinstance(value, bytes):
        value = canonical(value)
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write(path: Path, data: bytes, *, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        raise ATKError("WORKSPACE_CONFLICT", f"immutable artifact already exists: {path}")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if immutable and path.exists():
            raise ATKError("WORKSPACE_CONFLICT", f"immutable artifact already exists: {path}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: dict, *, immutable: bool = False) -> None:
    atomic_write(path, canonical(value) + b"\n", immutable=immutable)


@contextmanager
def locked(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".operation.lock").open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def store_object(root: Path, family: str, value: dict) -> Path:
    if value.get("schema_version") != SCHEMA_VERSION or not value.get("id") or not value.get("created_at"):
        raise ATKError("INCOMPLETE_EVIDENCE", f"invalid {family} identity")
    path = root / family / safe_id(value["id"]) / "manifest.json"
    write_json(path, value, immutable=True)
    return path


def validate_evidence(root: Path, batch_id: str) -> tuple[dict, dict[str, dict], dict[str, dict]]:
    folder = root / "evidence" / safe_id(batch_id)
    manifest = read_json(folder / "manifest.json")
    if manifest.get("id") != batch_id or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ATKError("INCOMPLETE_EVIDENCE", "evidence manifest identity mismatch")
    records = {}
    for line in (folder / "records.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        record_id = record.get("id")
        if not record_id or record_id in records:
            raise ATKError("INCOMPLETE_EVIDENCE", "duplicate or missing record ID")
        records[record_id] = record
    evidence = {}
    for entry in manifest.get("evidence_index", []):
        evidence_id = entry.get("evidence_id")
        if not evidence_id or evidence_id in evidence or not entry.get("fingerprint") or not entry.get("locator"):
            raise ATKError("INCOMPLETE_EVIDENCE", "invalid evidence index")
        evidence[evidence_id] = entry
    if manifest.get("records_sha256") != digest(folder / "records.jsonl"):
        raise ATKError("INCOMPLETE_EVIDENCE", "evidence records changed after sealing")
    return manifest, records, evidence


def store_assessment(root: Path, request: dict) -> Path:
    batch_id = request["batch_id"]
    _, records, evidence = validate_evidence(root, batch_id)
    spec = request["evaluation_spec"]
    judger = request["judger"]
    required_spec = {"version", "boundary", "dimensions", "dimension_rules", "denominator_rule"}
    if (
        required_spec - spec.keys()
        or not spec.get("dimensions")
        or set(spec["dimensions"]) != set(spec["dimension_rules"])
    ):
        raise ATKError("JUDGER_INVALID", "evaluation_spec needs a versioned boundary, dimension rules, and denominator")
    if not judger.get("version") or judger.get("readiness") not in {"calibrated", "uncalibrated"}:
        raise ATKError("JUDGER_INVALID", "judger version and readiness are required")
    if judger["readiness"] == "calibrated":
        examples = judger.get("calibration_examples", [])
        verdicts = {
            example.get("expected_verdict")
            for example in examples
            if example.get("source_ref") and example.get("actual_verdict") == example.get("expected_verdict")
        }
        if not {"pass", "fail"} <= verdicts or any(
            example.get("expected_verdict") != example.get("actual_verdict") for example in examples
        ):
            raise ATKError("JUDGER_INVALID", "calibrated judger needs matched positive and negative examples")
    dimensions = set(spec["dimensions"])
    rows = request["rows"]
    keys = set()
    for row in rows:
        if set(row) != set(ASSESSMENT_COLUMNS) or row["record_id"] not in records or row["dimension"] not in dimensions:
            raise ATKError("JUDGER_INVALID", "assessment row has unknown fields, record, or dimension")
        key = (row["record_id"], row["dimension"])
        if key in keys:
            raise ATKError("JUDGER_INVALID", f"duplicate assessment key: {key}")
        keys.add(key)
        if row["validity"] not in {"valid", "invalid", "unknown"} or row["verdict"] not in {
            "pass",
            "fail",
            "unknown",
            "not_applicable",
        }:
            raise ATKError("JUDGER_INVALID", "invalid verdict or validity")
        if row["validity"] != "valid" and row["verdict"] != "unknown":
            raise ATKError("JUDGER_INVALID", "invalid evidence cannot have a determinate verdict")
        refs = row["evidence_refs"]
        if (
            not isinstance(refs, list)
            or not refs
            or any(
                not isinstance(ref, dict) or ref.get("batch_id") != batch_id or ref.get("evidence_id") not in evidence
                for ref in refs
            )
        ):
            raise ATKError("INCOMPLETE_EVIDENCE", "assessment has an invalid evidence reference")
    if keys != {(record_id, dimension) for record_id in records for dimension in dimensions}:
        raise ATKError("INCOMPLETE_EVIDENCE", "assessment does not cover every record and dimension")
    assessment_id = new_id("assessment")
    folder = root / "assessments" / safe_id(assessment_id)
    folder.mkdir(parents=True, exist_ok=False)
    import io

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=ASSESSMENT_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow({**row, "evidence_refs": json.dumps(row["evidence_refs"], ensure_ascii=False)})
    csv_path = folder / "assessment.csv"
    atomic_write(csv_path, output.getvalue().encode(), immutable=True)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "id": assessment_id,
        "created_at": now(),
        "batch_id": batch_id,
        "evaluation_spec": spec,
        "evaluation_spec_hash": digest(spec),
        "judger": judger,
        "judger_readiness": judger.get("readiness", "uncalibrated"),
        "csv_sha256": digest(csv_path),
        "counts": {
            verdict: sum(row["verdict"] == verdict for row in rows)
            for verdict in ("pass", "fail", "unknown", "not_applicable")
        },
    }
    write_json(folder / "manifest.json", manifest, immutable=True)
    return folder / "manifest.json"


def read_assessment(root: Path, assessment_id: str) -> tuple[dict, list[dict]]:
    folder = root / "assessments" / safe_id(assessment_id)
    manifest = read_json(folder / "manifest.json")
    if manifest["csv_sha256"] != digest(folder / "assessment.csv"):
        raise ATKError("JUDGER_INVALID", "assessment CSV fingerprint mismatch")
    with (folder / "assessment.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return manifest, rows
