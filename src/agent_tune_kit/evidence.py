"""Import batch results and Langfuse exports without inventing missing evidence."""

from __future__ import annotations

import csv
import gzip
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .core import ATKError, canonical, digest, new_id, now, validate_evidence, write_json

MAX_SOURCE_BYTES = 100 * 1024 * 1024
SECRET_KEY = re.compile(
    r"(?:password|secret|authorization|api[_-]?key|private[_-]?key|cookie|"
    r"token(?:[_-]?value)?$)",
    re.I,
)
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+")
INLINE_SECRET = re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+")


def _page(request: dict) -> tuple[int, int]:
    offset, limit = request.get("offset", 0), request.get("limit", 100)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ATKError("INCOMPLETE_EVIDENCE", "分页必须是非负 offset 和 1 至 1000 的 limit")
    return offset, limit


def view_execution(root: Path, request: dict) -> dict:
    """Read sealed events at stable locations; never materialize another batch."""
    offset, limit = _page(request)
    batch, records, index = validate_evidence(root, request["batch_id"])
    selected = [
        record
        for record in records.values()
        if (not request.get("execution_id") or record.get("execution", {}).get("id") == request["execution_id"])
        and (not request.get("case_id") or record.get("case_id") == request["case_id"])
    ]
    known_ids = {record.get("execution", {}).get("id") for record in selected}
    for ref in index.values():
        if (
            ref.get("execution_status") == "unknown"
            and ref["execution_id"] not in known_ids
            and (not request.get("execution_id") or ref["execution_id"] == request["execution_id"])
            and (not request.get("case_id") or ref.get("case_id") == request["case_id"])
        ):
            selected.append(
                {
                    "case_id": ref["case_id"],
                    "execution": {"id": ref["execution_id"], "status": "unknown"},
                    "capture": {"status": "partial", "capability": "execution", "issues": ["unconfirmed_attempt"]},
                }
            )
            known_ids.add(ref["execution_id"])
    if not selected:
        raise ATKError("INCOMPLETE_EVIDENCE", "没有对应执行记录")
    ids = {record.get("execution", {}).get("id") for record in selected}
    events = []
    files = [
        ref
        for ref in index.values()
        if ref.get("execution_id") in ids and ref.get("kind") in {"events", "native_artifact"}
    ]
    count = 0
    for ref in files:
        if ref["kind"] != "events":
            continue
        with Path(ref["path"]).open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    if ref.get("execution_status") == "unknown":
                        break
                    raise ATKError("INCOMPLETE_EVIDENCE", "封存事件格式无效") from None
                stage_field = next(
                    (
                        r.get("capture", {}).get("stage_field", "phase")
                        for r in selected
                        if r.get("execution", {}).get("id") == ref["execution_id"]
                    ),
                    "phase",
                )
                if request.get("stage") and row.get("extra", {}).get(stage_field) != request["stage"]:
                    continue
                if request.get("message") and row.get("message") != request["message"]:
                    continue
                if offset <= count < offset + limit:
                    events.append({**row, "evidence_id": ref["evidence_id"]})
                count += 1
    return {
        "batch_id": batch["id"],
        "executions": [
            {
                "execution": row.get("execution"),
                "case_id": row.get("case_id"),
                "capture": row.get("capture", {"status": "unknown", "capability": "legacy"}),
            }
            for row in selected
        ],
        "events": events,
        "files": files,
        "offset": offset,
        "total": count,
        "next_offset": offset + limit if offset + limit < count else None,
    }


def read_native_evidence(root: Path, request: dict) -> dict:
    offset, limit = _page(request)
    _, _, index = validate_evidence(root, request["batch_id"])
    ref = index.get(request["evidence_id"])
    if not ref or ref.get("kind") not in {"events", "native_artifact"}:
        raise ATKError("INCOMPLETE_EVIDENCE", "不是可读取的原生证据")
    path = Path(ref["path"])
    if ".jsonl" in path.suffixes:
        opener = gzip.open if path.suffix == ".gz" else open
        rows, total = [], 0
        with opener(path, "rt", encoding="utf-8") as handle:
            while line := handle.readline(1024 * 1024 + 1):
                if len(line) > 1024 * 1024:
                    raise ATKError("INCOMPLETE_EVIDENCE", "原生记录超过读取限制")
                if offset <= total < offset + limit:
                    if len(line.encode()) > MAX_SOURCE_BYTES:
                        raise ATKError("INCOMPLETE_EVIDENCE", "原生记录超过读取限制")
                    rows.append(redact(json.loads(line)))
                total += 1
        return {
            "evidence_ref": ref,
            "rows": rows,
            "total": total,
            "next_offset": offset + limit if offset + limit < total else None,
        }
    if path.suffix == ".json":
        value, _ = _read_source(path)
        return {"evidence_ref": ref, "value": redact(value)}
    return {"evidence_ref": ref, "readable": False, "reason": "原格式附件，请使用对应文件读取工具"}


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
        return INLINE_SECRET.sub(r"\1=[REDACTED]", BEARER.sub("Bearer [REDACTED]", value))
    return value


def record_source_contract(repo: Path, root: Path, request: dict) -> dict:
    """Seal a small, redacted source excerpt as diagnostic evidence."""
    if not isinstance(request.get("source_path"), str) or not request["source_path"]:
        raise ATKError("INCOMPLETE_EVIDENCE", "必须提供契约源码路径")
    path = (repo / request["source_path"]).resolve()
    if (
        not path.is_relative_to(repo.resolve())
        or ".atk" in path.relative_to(repo.resolve()).parts
        or not path.is_file()
        or path.stat().st_size > 1024 * 1024
    ):
        raise ATKError("SCOPE_VIOLATION", "契约源码必须是目标项目内的小文件")
    start, end = request.get("start_line"), request.get("end_line")
    if (
        type(start) is not int
        or type(end) is not int
        or start < 1
        or end < start
        or end - start > 99
        or not request.get("component_id")
        or not request.get("source_revision")
    ):
        raise ATKError("INCOMPLETE_EVIDENCE", "契约需要组件、源码版本及不超过 100 行的摘录")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeError as exc:
        raise ATKError("UNSUPPORTED_EXPORT_FORMAT", "契约源码不是 UTF-8 文本") from exc
    if end > len(lines):
        raise ATKError("INCOMPLETE_EVIDENCE", "契约行范围超出源码")
    excerpt = "\n".join(
        "[REDACTED SECRET LINE]" if SECRET_KEY.search(line) else redact(line) for line in lines[start - 1 : end]
    )
    batch_id, record_id = new_id("batch"), new_id("record")
    folder = root / "evidence" / batch_id
    folder.mkdir(parents=True, exist_ok=False)
    record = {
        "id": record_id,
        "source_locator": f"{path}#L{start}-L{end}",
        "source_sha256": digest(path),
        "source_revision": request["source_revision"],
        "component_id": request["component_id"],
        "artifact_identity": request.get("artifact_identity"),
        "excerpt": excerpt,
    }
    records_path = folder / "records.jsonl"
    records_path.write_bytes(canonical(record) + b"\n")
    manifest = {
        "schema_version": 2,
        "id": batch_id,
        "created_at": now(),
        "source_type": "source_contract",
        "purpose": "diagnostic_reference",
        "round_id": request.get("round_id"),
        "records_ref": "records.jsonl",
        "records_sha256": digest(records_path),
        "record_count": 1,
        "evidence_index": [
            {
                "evidence_id": record_id,
                "fingerprint": digest(record),
                "locator": record["source_locator"],
                "origin": "source_contract",
                "source_component": request["component_id"],
            }
        ],
    }
    write_json(folder / "manifest.json", manifest, immutable=True)
    return manifest


def _source_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ATKError("UNSUPPORTED_EXPORT_FORMAT", f"来源不存在：{path}")
    files = sorted(file for file in path.iterdir() if file.is_file() and not file.name.startswith("_"))
    if not files:
        raise ATKError("UNSUPPORTED_EXPORT_FORMAT", "目录中没有导出文件")
    return files


def _read_source(path: Path) -> tuple[object, str]:
    suffixes = path.suffixes
    compressed = bool(suffixes and suffixes[-1] == ".gz")
    extension = suffixes[-2] if compressed and len(suffixes) > 1 else path.suffix
    if extension not in {".json", ".jsonl", ".csv"}:
        raise ATKError("UNSUPPORTED_EXPORT_FORMAT", f"不支持的导出格式：{path.name}")
    with gzip.open(path, "rb") if compressed else path.open("rb") as handle:
        data = handle.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ATKError("UNSUPPORTED_EXPORT_FORMAT", f"导出文件超过 {MAX_SOURCE_BYTES} 字节：{path.name}")
    try:
        content = data.decode("utf-8-sig")
        if extension == ".json":
            result = json.loads(content)
        elif extension == ".jsonl":
            result = [json.loads(line) for line in content.splitlines() if line.strip()]
        else:
            result = list(csv.DictReader(content.splitlines()))
    except (UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        raise ATKError("UNSUPPORTED_EXPORT_FORMAT", f"无法解析 {path.name}：{exc}") from exc
    return result, digest(data)


def _rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload["data"] if isinstance(payload.get("data"), list) else [payload]
    else:
        raise ATKError("AMBIGUOUS_MAPPING", "导出数据必须是对象或对象数组")
    if any(not isinstance(row, dict) for row in rows):
        raise ATKError("AMBIGUOUS_MAPPING", "导出行必须是对象")
    return rows


def _alias(row: dict, *names: str):
    for name in names:
        if name in row:
            return row[name]
    return None


def _mapped_row(row: dict, fields: dict[str, str]) -> dict:
    if not isinstance(row, dict):
        raise ATKError("AMBIGUOUS_MAPPING", "映射的导出行必须是对象")
    mapped = dict(row)
    for canonical_name, source_name in fields.items():
        if source_name not in row:
            raise ATKError("AMBIGUOUS_MAPPING", f"声明的字段 {source_name} 缺失")
        if canonical_name in row and row[canonical_name] != row[source_name]:
            raise ATKError("AMBIGUOUS_MAPPING", f"字段 {canonical_name} 的映射冲突")
        mapped[canonical_name] = row[source_name]
    return mapped


def _normalize_csv_json_columns(rows: list[dict], columns: list[str]) -> list[dict]:
    for row in rows:
        for name in columns:
            if name in row and row[name]:
                try:
                    row[name] = json.loads(row[name])
                except json.JSONDecodeError as exc:
                    raise ATKError("AMBIGUOUS_MAPPING", f"声明的 JSON 列 {name} 无效") from exc
    return rows


def _parsed_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _trace_record(
    trace: dict,
    *,
    namespace: str,
    locator: str,
    extra_keys: set[str],
    observation_locators: dict[str, str] | None = None,
    score_locators: dict[str, str] | None = None,
    root_observation_name: str | None = None,
    trace_source: dict | None = None,
) -> tuple[dict, list[dict]]:
    trace_id = _alias(trace, "id", "traceId", "trace_id")
    if not trace_id:
        raise ATKError("AMBIGUOUS_MAPPING", f"{locator} 缺少 Trace ID")
    observations = trace.get("observations", [])
    if observations is None:
        observations = []
    if not isinstance(observations, list):
        raise ATKError("AMBIGUOUS_MAPPING", "observations 必须是数组")
    observed_ids = set()
    events = []
    index = []
    for offset, observation in enumerate(observations):
        if not isinstance(observation, dict) or not observation.get("id"):
            raise ATKError("AMBIGUOUS_MAPPING", "Observation 需要唯一 ID")
        event_id = observation["id"]
        if _alias(observation, "traceId", "trace_id") not in (None, trace_id):
            raise ATKError("AMBIGUOUS_MAPPING", f"Observation {event_id} 属于另一个 Trace")
        if event_id in observed_ids:
            raise ATKError("AMBIGUOUS_MAPPING", f"重复的 Observation ID：{event_id}")
        observed_ids.add(event_id)
        event = redact(observation, extra_keys)
        raw_start = _alias(event, "startTime", "start_time")
        raw_end = _alias(event, "endTime", "end_time")
        event.update(
            event_id=event_id,
            parent_event_id=_alias(event, "parentObservationId", "parent_observation_id") or None,
            event_type=event.get("type"),
            event_name=event.get("name"),
            raw_started_at=raw_start,
            started_at=_parsed_time(raw_start),
            raw_ended_at=raw_end,
            ended_at=_parsed_time(raw_end),
            input_present="input" in observation,
            output_present="output" in observation,
            source_status={"level": event.get("level"), "message": _alias(event, "statusMessage", "status_message")},
            runtime_metadata={
                key: event[key]
                for key in (
                    "model",
                    "modelParameters",
                    "model_parameters",
                    "usage",
                    "usageDetails",
                    "usage_details",
                    "costDetails",
                    "cost_details",
                    "version",
                    "metadata",
                    "environment",
                )
                if key in event
            },
        )
        events.append(event)
        index.append(
            {
                "evidence_id": f"observation:{event_id}",
                "fingerprint": digest(event),
                "locator": (observation_locators or {}).get(event_id, f"{locator}/observations/{offset}"),
                "origin": "original_execution",
                "source_component": observation.get("name") or observation.get("type") or "unknown",
                "source_trace_id": trace_id,
                "source_observation_id": event_id,
            }
        )
    missing_parents = [
        parent for event in events if (parent := event["parent_event_id"]) and parent not in observed_ids
    ]
    missing = []
    if not observations:
        missing.append("observations")
    if missing_parents:
        missing.append("parent_observations")
    selected_roots = [
        event
        for event in events
        if root_observation_name and event["event_name"] == root_observation_name and event["parent_event_id"] is None
    ]
    selected_root = selected_roots[0] if len(selected_roots) == 1 else None
    input_source = trace if "input" in trace else selected_root or {}
    output_source = trace if "output" in trace else selected_root or {}
    if "input" not in input_source:
        missing.append("trace_input")
    if "output" not in output_source:
        missing.append("trace_output")
    scores = trace.get("scores", [])
    if scores is None:
        scores = []
    if not isinstance(scores, list):
        raise ATKError("AMBIGUOUS_MAPPING", "scores 必须是数组")
    for offset, score in enumerate(scores):
        if not isinstance(score, dict):
            raise ATKError("AMBIGUOUS_MAPPING", "Score 必须是对象")
        score_id = score.get("id") or f"{trace_id}:{offset}"
        index.append(
            {
                "evidence_id": f"score:{score_id}",
                "fingerprint": digest(redact(score, extra_keys)),
                "locator": (score_locators or {}).get(score_id, f"{locator}/scores/{offset}"),
                "origin": "original_execution",
                "source_component": "score",
                "source_trace_id": trace_id,
            }
        )
    trace_start = _alias(trace, "timestamp", "startTime", "start_time")
    trace_end = _alias(trace, "endTime", "end_time")
    record = {
        "id": new_id("record"),
        "source_namespace": namespace,
        "source_trace_id": trace_id,
        "source_session_id": _alias(trace, "sessionId", "session_id"),
        "raw_started_at": trace_start,
        "started_at": _parsed_time(trace_start),
        "raw_ended_at": trace_end,
        "ended_at": _parsed_time(trace_end),
        "source_status": redact(
            {"level": trace.get("level"), "message": _alias(trace, "statusMessage", "status_message")},
            extra_keys,
        ),
        "execution": None,
        "input_present": "input" in input_source,
        "input": redact(input_source.get("input"), extra_keys),
        "input_source": "trace"
        if "input" in trace
        else f"observation:{selected_root['event_id']}"
        if selected_root
        else None,
        "output_present": "output" in output_source,
        "output": redact(output_source.get("output"), extra_keys),
        "output_source": "trace"
        if "output" in trace
        else f"observation:{selected_root['event_id']}"
        if selected_root
        else None,
        "events": events,
        "external_scores": redact(scores, extra_keys),
        "metrics": redact({"latency": trace.get("latency"), "totalCost": trace.get("totalCost")}, extra_keys),
        "runtime_metadata": redact(
            {
                key: trace[key]
                for key in ("model", "usage", "usageDetails", "metadata", "version", "release", "environment")
                if key in trace
            },
            extra_keys,
        ),
        "analysis": "limited" if missing else "sufficient",
        "missing": missing,
        "replayability": "needs_setup",
        "source_locator": locator,
    }
    index.insert(
        0,
        {
            "evidence_id": f"trace:{trace_id}",
            "fingerprint": digest(redact(trace if trace_source is None else trace_source, extra_keys)),
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
        raise ATKError("AMBIGUOUS_MAPPING", "必须提供 source_namespace 和 mapping_version")
    if source_kind == "langfuse" and profile not in {"langfuse_trace_bundle", "langfuse_observation_rows"}:
        raise ATKError("AMBIGUOUS_MAPPING", "必须明确提供 Langfuse adapter_profile")
    if source_kind not in {"langfuse", "batch_results"}:
        raise ATKError("AMBIGUOUS_MAPPING", "不支持的 source_kind")
    mapping = request.get("mapping", {})
    if not isinstance(mapping, dict) or (
        "root_observation_name" in mapping and not isinstance(mapping["root_observation_name"], str)
    ):
        raise ATKError("AMBIGUOUS_MAPPING", "mapping 必须包含字段名或根 Observation 名称")
    for role in ("trace", "observation", "score"):
        fields = mapping.get(role, {})
        if not isinstance(fields, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in fields.items()
        ):
            raise ATKError("AMBIGUOUS_MAPPING", f"{role} 字段映射必须指定来源列名")
    extra_keys = set(request.get("redact_keys", []))
    files = _source_files(source)
    file_roles = request.get("file_roles", {})
    if (
        not isinstance(file_roles, dict)
        or file_roles
        and (
            source_kind != "langfuse"
            or profile != "langfuse_trace_bundle"
            or set(file_roles) != {file.name for file in files}
            or set(file_roles.values()) - {"trace", "observation", "score"}
            or "trace" not in file_roles.values()
        )
    ):
        raise ATKError("AMBIGUOUS_MAPPING", "file_roles 必须分类每个 Trace bundle 文件")
    source_refs = []
    rows_by_file = []
    for file in files:
        payload, fingerprint = _read_source(file)
        rows = _rows(payload)
        rows = _normalize_csv_json_columns(rows, request.get("json_columns", []))
        source_refs.append({"path": str(file), "sha256": fingerprint})
        rows_by_file.append((file, rows))
    source_fingerprint = digest(source_refs)
    import_config = {
        "source_kind": source_kind,
        "adapter_profile": profile,
        "mapping_version": mapping_version,
        "mapping": mapping,
        "json_columns": sorted(set(request.get("json_columns", []))),
        "file_roles": file_roles,
        "redact_keys": sorted(extra_keys),
        "filtered_scope": request.get("filtered_scope"),
    }
    import_config_fingerprint = digest(import_config)
    previous_revision = None
    for old in (root / "evidence").glob("*/manifest.json"):
        previous = json.loads(old.read_text(encoding="utf-8"))
        if (
            previous.get("source_fingerprint") == source_fingerprint
            and previous.get("source_namespace") == namespace
            and previous.get("import_config_fingerprint") == import_config_fingerprint
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
        observations_by_trace = defaultdict(list)
        scores_by_trace = defaultdict(list)
        traces = []
        for file, rows in rows_by_file:
            for offset, row in enumerate(rows):
                role = file_roles.get(file.name, "trace")
                if role in {"observation", "score"}:
                    row = _mapped_row(row, mapping.get(role, {}))
                    trace_id = _alias(row, "traceId", "trace_id")
                    if not trace_id or not row.get("id"):
                        raise ATKError("AMBIGUOUS_MAPPING", f"{role} 需要 traceId 和 id")
                    target = observations_by_trace if role == "observation" else scores_by_trace
                    target[str(trace_id)].append((row, f"{file}#/{offset}"))
                    continue
                trace = row.get("trace", row)
                if not isinstance(trace, dict):
                    raise ATKError("AMBIGUOUS_MAPPING", f"不是 Trace bundle：{file.name}")
                mapped_trace = _mapped_row(trace, mapping.get("trace", {}))
                if "observations" not in mapped_trace and "id" not in mapped_trace:
                    raise ATKError("AMBIGUOUS_MAPPING", f"不是 Trace bundle：{file.name}")
                locator = f"{file}#/trace" if "trace" in row else f"{file}#/{offset}"
                traces.append((mapped_trace, trace, locator))
        trace_ids = {_alias(trace, "id", "traceId", "trace_id") for trace, _, _ in traces}
        for trace_id in observations_by_trace.keys() | scores_by_trace.keys():
            if trace_id not in trace_ids:
                raise ATKError("AMBIGUOUS_MAPPING", f"分离的行引用了未知 Trace：{trace_id}")
        for trace, original_trace, locator in traces:
            trace_id = str(_alias(trace, "id", "traceId", "trace_id"))
            joined = dict(trace)
            observation_locators = {}
            score_locators = {}
            for key, grouped, locators in (
                ("observations", observations_by_trace, observation_locators),
                ("scores", scores_by_trace, score_locators),
            ):
                role = "observation" if key == "observations" else "score"
                values = [_mapped_row(item, mapping.get(role, {})) for item in trace.get(key) or []]
                if not isinstance(trace.get(key, []), (list, type(None))):
                    raise ATKError("AMBIGUOUS_MAPPING", f"{key} 必须是数组")
                known = {item.get("id"): item for item in values if isinstance(item, dict)}
                for item, item_locator in grouped[trace_id]:
                    item_id = item["id"]
                    if item_id in known:
                        if known[item_id] != item:
                            raise ATKError("AMBIGUOUS_MAPPING", f"{key} ID 冲突：{item_id}")
                        continue
                    values.append(item)
                    known[item_id] = item
                    locators[item_id] = item_locator
                joined[key] = values
            record, index = _trace_record(
                joined,
                namespace=namespace,
                locator=locator,
                extra_keys=extra_keys,
                observation_locators=observation_locators,
                score_locators=score_locators,
                root_observation_name=mapping.get("root_observation_name"),
                trace_source=original_trace,
            )
            records.append(record)
            evidence_index.extend(index)
    elif source_kind == "langfuse":
        grouped = defaultdict(list)
        for file, rows in rows_by_file:
            for offset, row in enumerate(rows):
                row = _mapped_row(row, mapping.get("observation", {}))
                trace_id = _alias(row, "traceId", "trace_id")
                event_id = row.get("id")
                if not trace_id or not event_id:
                    raise ATKError("AMBIGUOUS_MAPPING", "Observation 行需要 traceId 和 id")
                grouped[str(trace_id)].append((row, f"{file}#/{offset}"))
        for trace_id, group in grouped.items():
            trace = {"id": trace_id, "observations": [row for row, _ in group]}
            record, index = _trace_record(
                trace,
                namespace=namespace,
                locator=group[0][1],
                extra_keys=extra_keys,
                observation_locators={row["id"]: locator for row, locator in group},
                root_observation_name=mapping.get("root_observation_name"),
            )
            record["analysis"] = "limited"
            record["missing"].append("trace_summary")
            records.append(record)
            evidence_index.extend(index)
    else:
        if not mapping.get("input") or not mapping.get("output"):
            raise ATKError("AMBIGUOUS_MAPPING", "batch_results 需要 input 和 output 字段映射")
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
        raise ATKError("INCOMPLETE_EVIDENCE", "来源中没有任务记录")
    ids = [record.get("source_trace_id") for record in records if record.get("source_trace_id")]
    if len(ids) != len(set(ids)):
        raise ATKError("AMBIGUOUS_MAPPING", "同一次导入中的 Trace ID 重复")
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
        "import_config_fingerprint": import_config_fingerprint,
        "mapping": import_config["mapping"],
        "json_columns": import_config["json_columns"],
        "file_roles": file_roles,
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
