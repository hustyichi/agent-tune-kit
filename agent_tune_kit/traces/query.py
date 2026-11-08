"""Read native saved formats with byte-bound references, never refetching or judging."""

import json
from datetime import datetime
from math import inf
from pathlib import Path

from agent_tune_kit.errors import UserActionRequired

from . import store


def _milliseconds(value):
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
            if moment.utcoffset() is not None:
                return round(moment.timestamp() * 1_000_000) / 1000
        except (ValueError, OSError, OverflowError):
            pass
    return None


def _page(offset, limit):
    if not isinstance(offset, int) or not isinstance(limit, int) or offset < 0 or limit < 1 or limit > 1000:
        raise UserActionRequired("Use offset >= 0 and a limit between 1 and 1000.")


def _pointer(document, pointer):
    if pointer in {None, ""}:
        return document
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise UserActionRequired("Evidence pointer must be a JSON Pointer.")
    value = document
    try:
        for segment in pointer[1:].split("/"):
            key = segment.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        raise UserActionRequired("Evidence JSON Pointer is unavailable in the retained file.") from None


def _node(row, reference, *, trace_id=None, kind=None):
    node = {
        "id": row.get("id", row.get("event_id", row.get("execution_id"))),
        "observation_id": row.get("id") if kind != "TRACE" else None,
        "trace_id": row.get("traceId", row.get("trace_id", trace_id)),
        "parent_id": row.get("parentObservationId", row.get("parent_span_id")),
        "type": kind or row.get("type", row.get("kind", "observation")),
        "name": row.get("name", row.get("kind", "")),
        "level": row.get("level") if isinstance(row.get("level"), str) else None,
        "start_time": row.get("startTime", row.get("timestamp", row.get("occurred_at"))),
        "end_time": row.get("endTime"),
        "input": row.get("input"),
        "output": row.get("output"),
        "reference": reference,
        "ref": reference,
    }
    # A dispatch link identifies a real child task. An AGENT span alone does not.
    metadata = row.get("metadata")
    branch = row.get("parent_tool_use_id") or row.get("subagent_id")
    if not branch and isinstance(metadata, dict) and metadata.get("agentRole") == "subagent":
        branch = metadata.get("agentId")
    if not branch and str(node["name"]).startswith("sdk.") and isinstance(node["input"], dict):
        branch = node["input"].get("parent_tool_use_id")
    if isinstance(branch, str) and branch:
        node["branch"] = branch
    return node


def nodes(session_dir, capture_id=None):
    session_dir = Path(session_dir)
    session = store.load_session(session_dir)
    current = session["schema_version"] == 2
    if current and capture_id is not None:
        raise UserActionRequired("Current archives use the Session ID directly.")
    manifest = session["archive"] if current else store.read_capture(session_dir, capture_id)
    result = []
    for entry in manifest["files"]:
        relative = entry["path"] if current else f"captures/{manifest['capture_id']}/{entry['path']}"
        path = store.validate_ref(session_dir, {"path": relative, "sha256": entry["sha256"], "bytes": entry["bytes"]})
        reference = {"path": relative, "sha256": entry["sha256"], "bytes": entry["bytes"]}
        suffix = path.suffix.casefold()
        if suffix == ".json":
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError):
                raise UserActionRequired("Saved trace JSON is missing or damaged.") from None
            if isinstance(document, dict) and isinstance(document.get("trace"), dict):
                trace = document["trace"]
                result.append(_node(trace, {**reference, "pointer": "/trace"}, trace_id=trace["id"], kind="TRACE"))
                for index, row in enumerate(trace.get("observations", [])):
                    result.append(
                        _node(
                            row,
                            {**reference, "pointer": f"/trace/observations/{index}"},
                            trace_id=trace["id"],
                        )
                    )
            elif isinstance(document, dict) and isinstance(document.get("data"), list):
                for index, row in enumerate(document["data"]):
                    result.append(_node(row, {**reference, "pointer": f"/data/{index}"}))
            elif path.name in {"input.json", "output.json"}:
                node = {
                    "id": relative,
                    "name": path.name,
                    "type": "atk-artifact",
                    "input": document if path.name == "input.json" else None,
                    "output": document if path.name == "output.json" else None,
                }
                result.append(_node(node, dict(reference), kind="atk-artifact"))
        elif suffix == ".jsonl":
            for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    raise UserActionRequired("Saved event JSONL is damaged.") from None
                if not isinstance(row, dict):
                    result.append(
                        {
                            "id": f"{relative}:{index}",
                            "type": "unavailable-event",
                            "name": "Invalid event object",
                            "trace_id": None,
                            "parent_id": None,
                            "input": None,
                            "output": "Event line is not an object.",
                            "reference": {**reference, "line": index},
                            "ref": {**reference, "line": index},
                        }
                    )
                    continue
                reference = dict(reference)
                reference["line"] = index
                node = _node(row, reference, kind="atk-event")
                node["output"] = row.get("attributes", row)
                result.append(node)
        elif suffix in {".csv", ".log", ".txt"}:
            reference = dict(reference)
            result.append(
                {
                    "id": relative,
                    "trace_id": None,
                    "parent_id": None,
                    "type": "local-evidence",
                    "name": path.name,
                    "input": None,
                    "output": path.read_text(encoding="utf-8"),
                    "reference": reference,
                    "ref": reference,
                    "start_time": None,
                    "end_time": None,
                }
            )
    return result


def _summary(node):
    result = {key: value for key, value in node.items() if key not in {"input", "output"}}
    for field in ("input", "output"):
        value = node.get(field)
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        result[field] = text[:240]
        result[field + "_truncated"] = len(text) > 240
    return result


def overview(session_dir, capture_id=None, offset=0, limit=100, trace_id=None):
    _page(offset, limit)
    session = store.load_session(session_dir)
    manifest = session.get("archive") or store.read_capture(session_dir, capture_id)
    items = nodes(session_dir, capture_id)
    if trace_id is not None:
        if not isinstance(trace_id, (str, int)) or (isinstance(trace_id, str) and not trace_id):
            raise UserActionRequired("Provide a nonempty Trace ID.")
        items = [item for item in items if item.get("trace_id") == trace_id]
    ordered = sorted(
        items,
        key=lambda item: (
            _milliseconds(item.get("start_time")) is None,
            _milliseconds(item.get("start_time")) if _milliseconds(item.get("start_time")) is not None else inf,
            str(item.get("trace_id") or ""),
            str(item.get("id") or ""),
        ),
    )
    session_start = next((_milliseconds(item.get("start_time")) for item in ordered if _milliseconds(item.get("start_time")) is not None), None)

    def row(item):
        summary = _summary(item)
        at = _milliseconds(item.get("start_time"))
        summary["node_id"] = item.get("id")
        summary["parent_id"] = item.get("parent_id")
        summary["session_offset_ms"] = None if session_start is None or at is None else at - session_start
        return summary

    return {
        "session_id": session["session_id"],
        **({"capture_id": manifest["capture_id"]} if "capture_id" in manifest else {}),
        "scope": manifest.get("scope"),
        "completeness": manifest.get("completeness"),
        "status": manifest.get("status"),
        "trace_id": trace_id,
        "total": len(ordered),
        "offset": offset,
        "limit": limit,
        "rows": [row(item) for item in ordered[offset : offset + limit]],
        "unread_nodes": max(0, len(ordered) - min(len(ordered), offset + limit)),
    }


def search(session_dir, text, capture_id=None, offset=0, limit=50):
    _page(offset, limit)
    if not isinstance(text, str) or not text:
        raise UserActionRequired("Provide a nonempty literal search string.")
    matches = [
        node
        for node in nodes(session_dir, capture_id)
        if text.casefold() in json.dumps(node, ensure_ascii=False).casefold()
    ]
    return {
        "query": text,
        "total": len(matches),
        "offset": offset,
        "limit": limit,
        "rows": [_summary(node) for node in matches[offset : offset + limit]],
        "case_sensitive": False,
    }


def show(session_dir, reference, offset=0, limit=8000):
    if not isinstance(offset, int) or not isinstance(limit, int) or offset < 0 or limit < 1 or limit > 100000:
        raise UserActionRequired("Use offset >= 0 and a character limit between 1 and 100000.")
    if not isinstance(reference, dict):
        raise UserActionRequired("Provide a saved evidence reference with path and SHA-256.")
    path, pointer = store.resolve_ref(session_dir, reference)
    try:
        text = path.read_text(encoding="utf-8")
        if "line" in reference:
            line = reference["line"]
            if type(line) is not int or line < 1 or "pointer" in reference:
                raise ValueError
            text = text.splitlines()[line - 1]
        elif pointer is not None:
            value = _pointer(json.loads(text), pointer)
            text = json.dumps(value, ensure_ascii=False, indent=2)
    except (OSError, UnicodeError, ValueError, IndexError):
        raise UserActionRequired("Evidence location is unavailable in the saved material.") from None
    return {
        "reference": reference,
        **({"resolved_pointer": pointer} if pointer != reference.get("pointer") else {}),
        "text": text[offset : offset + limit],
        "offset": offset,
        "limit": limit,
        "characters": len(text),
        "truncated": offset > 0 or len(text) > offset + limit,
        "next_offset": offset + limit if len(text) > offset + limit else None,
    }
