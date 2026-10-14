"""Project-local ATK runner. Configure command argv in .atk/project.json.

The command receives only Case input. For custom Agent APIs, replace the command
invocation inside run_one while keeping the request/response contract unchanged.
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

_processes = set()
_process_lock = threading.Lock()
_stopping = threading.Event()


def file_sha256(path: Path) -> str:
    fingerprint = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            fingerprint.update(chunk)
    return fingerprint.hexdigest()


def redact_fields(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if re.search(r"(?i)^(authorization|cookie|password|secret|api[_-]?key|token|private[_-]?key)$", key)
            else redact_fields(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_fields(item) for item in value]
    return redact_output(value) if isinstance(value, str) else value


class EventCapture:
    """One writer per attempt; native records keep their observed fields."""

    def __init__(self, directory: Path, attempt: dict, config: dict):
        self.attempt = attempt
        self.config = config.get("observability", {})
        self.directory = directory
        self.native = self.config.get("format") == "native"
        self.path = directory / "logs/stderr.log" if self.native else directory / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sequence = 0
        self.messages = set()
        self.stages = set()
        self.issues = []
        self.lock = threading.Lock()
        self.handle = self.path.open("w", encoding="utf-8")
        os.chmod(self.path, 0o600)

    def emit(self, message: str, source: str = "runner", extra: dict | None = None, **fields) -> None:
        if self.native:
            return
        with self.lock:
            self.sequence += 1
            extra = extra or {}
            event = {
                "schema_version": 1,
                "id": f"{self.attempt['execution_id']}:{self.sequence}",
                "execution_id": self.attempt["execution_id"],
                "case_id": self.attempt["case_id"],
                "sequence": self.sequence,
                "received_at": datetime.now(UTC).isoformat(),
                "source": source,
                "message": message,
                "extra": extra,
                **fields,
            }
            event = redact_fields(event)
            self.handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.handle.flush()
            os.fsync(self.handle.fileno())
            if source != "runner":
                if event["message"] in self.config.get("required_messages", []):
                    self.messages.add(event["message"])
                stage = event["extra"].get(self.config.get("stage_field", "phase"))
                if isinstance(stage, str) and stage in self.config.get("required_stages", []):
                    self.stages.add(stage)

    def line(self, value: str) -> None:
        if self.native:
            from logging_capture import redact_record, redact_text

            if value.startswith("ATK_CAPTURE_ERROR ") and "native_outlet_error" not in self.issues:
                self.issues.append("native_outlet_error")
            try:
                raw = json.loads(value)
            except ValueError:
                rendered = redact_text(value)
            else:
                clean = redact_record(raw)
                rendered = value if clean == raw else json.dumps(clean, ensure_ascii=False)
            self.handle.write(rendered.rstrip("\r\n") + "\n")
            self.handle.flush()
            os.fsync(self.handle.fileno())
            return
        value = value.rstrip("\r\n")
        try:
            raw = json.loads(value.removeprefix("ATK_LOG "))
        except ValueError:
            if value.startswith("ATK_LOG "):
                self.issues.append("malformed_event")
            self.emit(value, "stderr")
            return
        fmt = self.config.get("format")
        key = "record" if fmt == "loguru_json" else "atk_log" if fmt == "atk_json" else None
        if key and isinstance(raw, dict) and key in raw:
            record = raw[key]
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("message"), str)
                or not isinstance(record.get("extra", {}), dict)
                or (
                    fmt == "loguru_json"
                    and (not isinstance(record.get("level", {}), dict) or not isinstance(record.get("time", {}), dict))
                )
            ):
                self.issues.append("malformed_event")
                self.emit("capture.malformed_event")
                return
        if fmt == "loguru_json" and isinstance(raw, dict) and isinstance(raw.get("record"), dict):
            record = raw["record"]
            self.emit(
                record["message"],
                "loguru",
                record.get("extra", {}),
                level=record.get("level", {}).get("name"),
                source_time=record.get("time", {}).get("repr"),
                logger=record.get("name"),
                location=record.get("function"),
                file=record.get("file"),
                line=record.get("line"),
                thread=record.get("thread"),
                process=record.get("process"),
            )
        elif fmt == "atk_json" and isinstance(raw, dict) and isinstance(raw.get("atk_log"), dict):
            record = raw["atk_log"]
            self.emit(
                record["message"],
                "python",
                record.get("extra", {}),
                level=record.get("level"),
                source_time=record.get("time"),
                logger=record.get("logger"),
                file=record.get("file"),
                line=record.get("line"),
                thread=record.get("thread"),
                process=record.get("process"),
            )
        else:
            self.emit(json.dumps(redact_fields(raw), ensure_ascii=False), "stderr")

    def consume(self, stream) -> None:
        pending = bytearray()
        oversized = False
        try:
            while chunk := os.read(stream.fileno(), 4096):
                for piece in chunk.splitlines(keepends=True):
                    if not oversized:
                        pending.extend(piece)
                        if len(pending) > 65536:
                            pending.clear()
                            oversized = True
                            self.issues.append("line_too_large")
                    if piece.endswith(b"\n"):
                        if not oversized:
                            self.line(pending.decode("utf-8", errors="strict"))
                        else:
                            self.emit("capture.line_too_large")
                        pending.clear()
                        oversized = False
            if pending:
                self.line(pending.decode("utf-8", errors="strict"))
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.issues.append(type(error).__name__)
            # Keep draining so a capture error cannot deadlock the Agent's stderr pipe.
            with suppress(OSError):
                while os.read(stream.fileno(), 4096):
                    pass

    def finish(self, status: str) -> dict:
        self.emit("attempt.finished", extra={"status": status})
        self.handle.close()
        files = [
            {
                "path": str(self.path),
                "sha256": file_sha256(self.path),
                "role": "stderr" if self.native else "events",
                **({"format": "text"} if self.native else {}),
            }
        ]
        if self.native:
            from logging_capture import log_records

            seen = {self.path.resolve()}
            for rule in self.config.get("logs", []):
                accepted = []
                for path in sorted(self.directory.glob(rule["glob"])):
                    if not path.is_file() or not path.resolve().is_relative_to(self.directory.resolve()):
                        self.issues.append("log_outside_attempt")
                        continue
                    if path.resolve() in seen:
                        self.issues.append("duplicate_log_source")
                        continue
                    seen.add(path.resolve())
                    accepted.append(path)
                    files.append(
                        {
                            "path": str(path.resolve()),
                            "sha256": file_sha256(path),
                            "role": "service_log",
                            "format": rule["format"],
                        }
                    )
                    if rule.get("required") and not path.stat().st_size:
                        self.issues.append("empty_required_log")
                    try:
                        for _, _, record in log_records(path, rule["format"]):
                            self.sequence += 1
                            if (
                                record is None
                                and rule["format"] != "text"
                                and "malformed_native_log" not in self.issues
                            ):
                                self.issues.append("malformed_native_log")
                            if record:
                                message = record["message"]
                                stage = record.get("extra", {}).get(self.config.get("stage_field", "phase"))
                                if message in self.config.get("required_messages", []):
                                    self.messages.add(message)
                                if isinstance(stage, str) and stage in self.config.get("required_stages", []):
                                    self.stages.add(stage)
                    except (OSError, ValueError, UnicodeError) as error:
                        self.issues.append(str(error))
                if rule.get("required") and not accepted:
                    self.issues.append("missing_log:" + rule["glob"])
        missing = sorted(set(self.config.get("required_messages", [])) - self.messages)
        missing += sorted(set(self.config.get("required_stages", [])) - self.stages)
        for item in self.config.get("artifacts", []):
            paths = sorted(self.directory.glob(item["glob"]))
            accepted = []
            for path in paths:
                if not path.is_file() or not path.resolve().is_relative_to(self.directory.resolve()):
                    self.issues.append("artifact_outside_attempt")
                    continue
                accepted.append(path)
                files.append(
                    {
                        "path": str(path.resolve()),
                        "sha256": file_sha256(path),
                        "role": item["role"],
                    }
                )
            if item.get("required") and not accepted:
                missing.append(item["role"])
        complete = not missing and not self.issues and status != "timeout"
        return {
            "schema_version": 1,
            "status": "complete" if complete else "partial",
            "capability": "workflow" if self.config.get("required_stages") and complete else "execution",
            "stage_field": self.config.get("stage_field", "phase"),
            "format": self.config.get("format"),
            "event_count": self.sequence,
            "observed_messages": sorted(self.messages),
            "observed_stages": sorted(self.stages),
            "missing": missing,
            "issues": self.issues,
            "files": files,
        }


def stop_processes() -> None:
    _stopping.set()
    with _process_lock:
        for process in _processes:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)


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


def attachments_unchanged(attachments: list[dict]) -> bool:
    return all(file_sha256(Path(item["path"])) == item["sha256"] for item in attachments)


def run_one(attempt: dict, case: dict, config: dict, output: Path, timeout: int) -> dict:
    task_dir = output / "attempts" / attempt["execution_id"]
    task_dir.mkdir(parents=True, exist_ok=False)
    capture = (
        EventCapture(task_dir, {**attempt, "case_id": case["id"]}, config) if config.get("observability") else None
    )
    if capture:
        capture.emit("attempt.started")
    input_file = task_dir / "input.json"
    input_file.write_text(json.dumps(case["input"], ensure_ascii=False), encoding="utf-8")
    attachments = case.get("attachments", [])
    attachments_file = task_dir / "attachments.json"
    attachments_file.write_text(json.dumps(attachments, ensure_ascii=False), encoding="utf-8")
    variables = {
        "input": str(case["input"]),
        "input_file": str(input_file),
        "attachments_file": str(attachments_file),
        "output_dir": str(task_dir),
    }
    command = [part.format_map(variables) for part in config["command"]]
    started = datetime.now(UTC).isoformat()
    started_clock = time.monotonic()
    try:
        if not attachments_unchanged(attachments):
            raise OSError("Case 附件在数据集导入后被修改")
        if config.get("script_path") and file_sha256(Path(config["script_path"])) != config["script_sha256"]:
            raise OSError("探针脚本在授权后被修改")
        with _process_lock:
            if _stopping.is_set():
                raise OSError("runner 正在停止")
            process = subprocess.Popen(
                command,
                cwd=config.get("working_directory", config["workspace_path"]),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=capture is None,
                env={
                    **os.environ,
                    "ATK_EXECUTION_ID": attempt["execution_id"],
                    "ATK_CASE_ID": case["id"],
                    "ATK_RUNNER_PATH": str(Path(__file__).resolve()),
                },
                start_new_session=True,
            )
            _processes.add(process)
        try:
            try:
                if capture:
                    responses = []
                    output_size = [0]

                    def read_output():
                        decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
                        try:
                            while chunk := os.read(process.stdout.fileno(), 4096):
                                output_size[0] += len(chunk)
                                if output_size[0] <= 8 * 1024 * 1024:
                                    responses.append(decoder.decode(chunk))
                            responses.append(decoder.decode(b"", final=True))
                        except UnicodeDecodeError:
                            capture.issues.append("output_decode_error")
                            responses.clear()
                            while os.read(process.stdout.fileno(), 4096):
                                pass

                    stdout_thread = threading.Thread(target=read_output)
                    stderr_thread = threading.Thread(target=capture.consume, args=(process.stderr,))
                    stdout_thread.start()
                    stderr_thread.start()
                    try:
                        process.wait(timeout=timeout)
                    finally:
                        if process.poll() is None:
                            with suppress(ProcessLookupError):
                                os.killpg(process.pid, signal.SIGKILL)
                        stdout_thread.join(timeout)
                        stderr_thread.join(timeout if not stdout_thread.is_alive() else 0)
                        if stdout_thread.is_alive() or stderr_thread.is_alive():
                            capture.issues.append("descendant_stream_open")
                            with suppress(ProcessLookupError):
                                os.killpg(process.pid, signal.SIGKILL)
                            stdout_thread.join()
                            stderr_thread.join()
                    response, stderr = "".join(responses), ""
                    if output_size[0] > 8 * 1024 * 1024:
                        capture.issues.append("output_too_large")
                        response = ""
                else:
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
                if process.poll() is None:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                if capture:
                    response, stderr = "".join(responses), ""
                else:
                    response, stderr = process.communicate()
                status, returncode = "timeout", None
        finally:
            process.wait()
            with _process_lock:
                _processes.discard(process)
        response, stderr = redact_output(response), redact_output(stderr)
        if not attachments_unchanged(attachments):
            status = "infra_error"
            stderr += "\nCase 附件在执行期间被修改"
    except OSError as exc:
        status, response, stderr, returncode = "infra_error", "", redact_output(str(exc)), None
    duration = time.monotonic() - started_clock
    if capture and stderr:
        capture.line(stderr)
    if not capture:
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
    record = {
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
    if capture:
        record["capture"] = capture.finish(status)
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    concurrency = request.get("concurrency", 1)
    if type(concurrency) is not int or concurrency < 1:
        raise ValueError("concurrency 必须是正整数")
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: _stopping.set())
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
        "running_record_ids": [],
        "not_started_record_ids": [a["record_id"] for a in request["attempts"]],
        "actual_components": [],
    }

    def save_batch() -> None:
        batch["not_started_record_ids"] = [
            record_id
            for record_id in batch["planned_record_ids"]
            if record_id not in completed and record_id not in batch["running_record_ids"]
        ]
        batch["running_record_id"] = batch["running_record_ids"][0] if len(batch["running_record_ids"]) == 1 else None
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
                observed["actual_sha256"] = file_sha256(path)
                observed["identity_status"] = "available"
        batch["actual_components"].append(observed)
    save_batch()
    attempts = iter(request["attempts"])
    pending = {}
    pool = ThreadPoolExecutor(max_workers=min(concurrency, max(1, len(request["attempts"]))))
    try:
        with records.open("a", encoding="utf-8") as handle:
            while True:
                while len(pending) < concurrency and not _stopping.is_set():
                    attempt = next(attempts, None)
                    if attempt is None:
                        break
                    batch["running_record_ids"].append(attempt["record_id"])
                    save_batch()
                    future = pool.submit(
                        run_one, attempt, cases[attempt["case_id"]], config, output, request["timeout_seconds"]
                    )
                    pending[future] = attempt["record_id"]
                if _stopping.is_set() or not pending:
                    break
                done, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                for future in done:
                    record = future.result()
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                    record_id = pending.pop(future)
                    completed.append(record_id)
                    batch["running_record_ids"].remove(record_id)
                    save_batch()
        return 1 if _stopping.is_set() else 0
    finally:
        stop_processes()
        pool.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    raise SystemExit(main())
