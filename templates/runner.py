"""Project-local ATK runner. Configure command argv in .atk/project.json.

The command receives only Case input. For custom Agent APIs, replace the command
invocation inside run_one while keeping the request/response contract unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
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
        if (
            config.get("script_path")
            and hashlib.sha256(Path(config["script_path"]).read_bytes()).hexdigest() != config["script_sha256"]
        ):
            raise OSError("probe script changed after authorization")
        with subprocess.Popen(
            command,
            cwd=config.get("working_directory", config["workspace_path"]),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        ) as process:
            try:
                response, stderr = process.communicate(timeout=timeout)
                status = (
                    "completed"
                    if process.returncode == 0
                    else "infra_error"
                    if process.returncode in config.get("infrastructure_exit_codes", [])
                    else "agent_error"
                )
                returncode = process.returncode
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                response, stderr = process.communicate()
                status, returncode = "timeout", None
        response, stderr = redact_output(response), redact_output(stderr)
    except OSError as exc:
        status, response, stderr, returncode = "infra_error", "", redact_output(str(exc)), None
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
    metric_sources = {"duration_seconds": "runner_clock"}
    if isinstance(agent_metrics, dict):
        for key in ("cost", "tool_calls"):
            if key in agent_metrics:
                metrics[key] = agent_metrics[key]
                metric_sources[key] = "agent_sidecar"
    return {
        "id": attempt["record_id"],
        "case_id": case["id"],
        "input_present": True,
        "input": case["input"],
        "output_present": bool(response),
        "output": response,
        "loading_evidence": loading,
        "metrics": metrics,
        "metric_sources": metric_sources,
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
    if request["purpose"] == "diagnostic_probe":
        config.update(request["probe_config"])
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
        "not_started_record_ids": [a["record_id"] for a in request["attempts"]],
        "actual_components": [],
    }

    def save_batch() -> None:
        batch["not_started_record_ids"] = [
            record_id
            for record_id in batch["planned_record_ids"]
            if record_id not in completed and record_id != batch["running_record_id"]
        ]
        write_json(output / "batch.json", batch)

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
    save_batch()
    with records.open("a", encoding="utf-8") as handle:
        for attempt in request["attempts"]:
            batch["running_record_id"] = attempt["record_id"]
            save_batch()
            record = run_one(attempt, cases[attempt["case_id"]], config, output, request["timeout_seconds"])
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            completed.append(attempt["record_id"])
            batch["running_record_id"] = None
            save_batch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
