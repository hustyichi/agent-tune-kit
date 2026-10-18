"""Small, auditable contracts shared by ATK's deterministic operations."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import tempfile
import uuid
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
            raise ValueError(f"未知 ATK 错误码：{code}")
        self.code = code
        super().__init__(message)


def new_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4()}"


def safe_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ATKError("SCOPE_VIOLATION", f"不安全的产物 ID：{value}")
    return value


def now() -> str:
    return datetime.now(UTC).isoformat()


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: bytes | Path | object) -> str:
    if isinstance(value, Path):
        fingerprint = hashlib.sha256()
        with value.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                fingerprint.update(chunk)
        return fingerprint.hexdigest()
    elif not isinstance(value, bytes):
        value = canonical(value)
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write(path: Path, data: bytes, *, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        raise ATKError("WORKSPACE_CONFLICT", f"不可变产物已存在：{path}")
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
            raise ATKError("WORKSPACE_CONFLICT", f"不可变产物已存在：{path}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: dict, *, immutable: bool = False) -> None:
    atomic_write(path, canonical(value) + b"\n", immutable=immutable)


def store_object(root: Path, family: str, value: dict) -> Path:
    if value.get("schema_version") != SCHEMA_VERSION or not value.get("id") or not value.get("created_at"):
        raise ATKError("INCOMPLETE_EVIDENCE", f"无效的 {family} 身份")
    path = root / family / safe_id(value["id"]) / "manifest.json"
    write_json(path, value, immutable=True)
    return path


def validate_evidence(root: Path, batch_id: str) -> tuple[dict, dict[str, dict], dict[str, dict]]:
    folder = root / "evidence" / safe_id(batch_id)
    manifest = read_json(folder / "manifest.json")
    if manifest.get("id") != batch_id or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ATKError("INCOMPLETE_EVIDENCE", "证据 manifest 身份不匹配")
    records = {}
    for line in (folder / "records.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        record_id = record.get("id")
        if not record_id or record_id in records:
            raise ATKError("INCOMPLETE_EVIDENCE", "记录 ID 重复或缺失")
        records[record_id] = record
    evidence = {}
    for entry in manifest.get("evidence_index", []):
        evidence_id = entry.get("evidence_id")
        if not evidence_id or evidence_id in evidence or not entry.get("fingerprint") or not entry.get("locator"):
            raise ATKError("INCOMPLETE_EVIDENCE", "证据索引无效")
        evidence[evidence_id] = entry
        if entry.get("kind") in {"events", "native_artifact"}:
            path = Path(entry.get("path", ""))
            if (
                not path.resolve().is_relative_to((root / "evidence").resolve())
                or not path.is_file()
                or digest(path) != entry["fingerprint"]
            ):
                raise ATKError("INCOMPLETE_EVIDENCE", "原生采集证据路径或指纹改变")
    if manifest.get("records_sha256") != digest(folder / "records.jsonl"):
        raise ATKError("INCOMPLETE_EVIDENCE", "证据记录在封存后被修改")
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
        raise ATKError("JUDGER_INVALID", "evaluation_spec 需要有版本的评测边界、维度规则和分母规则")
    if not judger.get("version") or judger.get("readiness") not in {"calibrated", "uncalibrated"}:
        raise ATKError("JUDGER_INVALID", "必须提供 judger 版本和校准状态")
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
            raise ATKError("JUDGER_INVALID", "已校准 judger 需要判定一致的正反示例")
    dimensions = set(spec["dimensions"])
    rows = request["rows"]
    keys = set()
    for row in rows:
        if set(row) != set(ASSESSMENT_COLUMNS) or row["record_id"] not in records or row["dimension"] not in dimensions:
            raise ATKError("JUDGER_INVALID", "判定行包含未知字段、记录或维度")
        key = (row["record_id"], row["dimension"])
        if key in keys:
            raise ATKError("JUDGER_INVALID", f"重复的判定键：{key}")
        keys.add(key)
        if row["validity"] not in {"valid", "invalid", "unknown"} or row["verdict"] not in {
            "pass",
            "fail",
            "unknown",
            "not_applicable",
        }:
            raise ATKError("JUDGER_INVALID", "判定或有效性状态无效")
        if row["validity"] != "valid" and row["verdict"] != "unknown":
            raise ATKError("JUDGER_INVALID", "无效证据不能有确定判定")
        refs = row["evidence_refs"]
        if (
            not isinstance(refs, list)
            or not refs
            or any(
                not isinstance(ref, dict) or ref.get("batch_id") != batch_id or ref.get("evidence_id") not in evidence
                for ref in refs
            )
        ):
            raise ATKError("INCOMPLETE_EVIDENCE", "判定包含无效证据引用")
        record = records[row["record_id"]]
        if not any(
            ref["evidence_id"] == row["record_id"]
            or (
                record.get("source_trace_id")
                and evidence[ref["evidence_id"]].get("source_trace_id") == record["source_trace_id"]
            )
            or (
                record.get("execution")
                and evidence[ref["evidence_id"]].get("execution_id") == record["execution"].get("id")
            )
            for ref in refs
        ):
            raise ATKError("INCOMPLETE_EVIDENCE", "判定必须引用自身记录的证据")
    if keys != {(record_id, dimension) for record_id in records for dimension in dimensions}:
        raise ATKError("INCOMPLETE_EVIDENCE", "判定未覆盖全部记录和维度")
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
        raise ATKError("JUDGER_INVALID", "判定 CSV 指纹不匹配")
    with (folder / "assessment.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return manifest, rows


def render_assessment_html(root: Path, assessment_id: str) -> Path:
    """Regenerate an escaped local view from the authoritative Assessment CSV."""
    manifest, rows = read_assessment(root, assessment_id)
    labels = ("记录 ID", "评测维度", "证据有效性", "有效性说明", "判定", "分数", "判定理由", "证据引用", "判定方式")
    values = {
        "validity": {"valid": "有效", "invalid": "无效", "unknown": "未知"},
        "verdict": {"pass": "通过", "fail": "失败", "unknown": "未知", "not_applicable": "不适用"},
        "judger_kind": {"semantic": "语义判定", "deterministic": "确定性检查"},
    }
    headings = "".join(f"<th>{html.escape(label)}</th>" for label in labels)
    body = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(values.get(name, {}).get(row[name], str(row[name])))}</td>"
            for name in ASSESSMENT_COLUMNS
        )
        + "</tr>"
        for row in rows
    )
    title = html.escape(str(manifest["evaluation_spec"]["boundary"]))
    page = (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'\">"
        f"<title>{title}</title><h1>{title}</h1><table><thead><tr>{headings}</tr></thead>"
        f"<tbody>{body}</tbody></table></html>"
    )
    path = root / "assessments" / safe_id(assessment_id) / "report.html"
    atomic_write(path, page.encode("utf-8"))
    return path
