"""Project-local ATK runner. Configure command argv in .atk/project.json.

The command receives only Case input. For custom Agent APIs, replace the command
invocation inside run_one while keeping the request/response contract unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def redact_output(value: str) -> str:
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+", "Bearer [REDACTED]", value)
    return re.sub(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", value)


def run_one(attempt: dict, case: dict, config: dict, output: Path, timeout: int) -> dict:
    task_dir = output / "attempts" / attempt["execution_id"]
    task_dir.mkdir(parents=True, exist_ok=False)
    input_file = task_dir / "input.json"
    input_file.write_text(json.dumps(case["input"], ensure_ascii=False), encoding="utf-8")
    variables = {"input": str(case["input"]), "input_file": str(input_file), "output_dir": str(task_dir)}
    command = [part.format_map(variables) for part in config["command"]]
    started = datetime.now(UTC).isoformat()
    started_clock = time.monotonic()
    try:
        result = subprocess.run(
            command, cwd=config["workspace_path"], capture_output=True, text=True, timeout=timeout, check=False
        )
        status = "completed" if result.returncode == 0 else "agent_error"
        response = redact_output(result.stdout)
        stderr = redact_output(result.stderr)
        returncode = result.returncode
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        response = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = (exc.stderr or b"").decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        response, stderr = redact_output(response), redact_output(stderr)
        returncode = None
    except OSError as exc:
        status, response, stderr, returncode = "infrastructure_error", "", redact_output(str(exc)), None
    duration = time.monotonic() - started_clock
    (task_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    loading_path = task_dir / "loading.json"
    loading = json.loads(loading_path.read_text(encoding="utf-8")) if loading_path.exists() else []
    metrics_path = task_dir / "metrics.json"
    try:
        agent_metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    except (OSError, ValueError):
        agent_metrics = {}
    metrics = {"duration_seconds": duration}
    if isinstance(agent_metrics, dict):
        metrics.update({key: agent_metrics[key] for key in ("cost", "tool_calls") if key in agent_metrics})
    return {
        "id": attempt["record_id"],
        "case_id": case["id"],
        "input_present": True,
        "input": case["input"],
        "output_present": bool(response),
        "output": response,
        "loading_evidence": loading,
        "metrics": metrics,
        "source_locator": str(task_dir),
        "execution": {
            "id": attempt["execution_id"],
            "batch_id": config["batch_id"],
            "record_id": attempt["record_id"],
            "case_id": case["id"],
            "case_fingerprint": attempt["case_fingerprint"],
            "repeat_index": attempt["repeat_index"],
            "retry_of": attempt["retry_of"],
            "purpose": config["purpose"],
            "revision_id": config["revision_id"],
            "started_at": started,
            "ended_at": datetime.now(UTC).isoformat(),
            "status": status,
            "returncode": returncode,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    config = json.loads(Path(request["run_config_ref"]).read_text(encoding="utf-8"))
    config.update({key: request[key] for key in ("batch_id", "purpose", "revision_id")})
    cases = {
        case["id"]: case
        for case in (json.loads(line) for line in Path(request["cases_path"]).read_text(encoding="utf-8").splitlines())
    }
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    records = output / "records.jsonl"
    completed = []
    batch = {
        "batch_id": request["batch_id"],
        "planned_record_ids": [a["record_id"] for a in request["attempts"]],
        "completed_record_ids": completed,
        "running_record_id": None,
        "actual_components": [],
    }
    for component in config.get("components", []):
        observed = {
            "component_id": component["component_id"],
            "role": component["role"],
            "change_role": component["change_role"],
            "source_path": component.get("source_path"),
            "actual_sha256": None,
            "identity_status": "unknown",
        }
        if component.get("source_path"):
            path = Path(config["workspace_path"]) / component["source_path"]
            if path.is_file():
                observed["actual_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                observed["identity_status"] = "available"
        batch["actual_components"].append(observed)
    write_json(output / "batch.json", batch)
    with records.open("a", encoding="utf-8") as handle:
        for attempt in request["attempts"]:
            batch["running_record_id"] = attempt["record_id"]
            write_json(output / "batch.json", batch)
            record = run_one(attempt, cases[attempt["case_id"]], config, output, request["timeout_seconds"])
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            completed.append(attempt["record_id"])
            batch["running_record_id"] = None
            write_json(output / "batch.json", batch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
