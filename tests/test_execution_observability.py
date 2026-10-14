from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

from agent_tune_kit import evidence
from agent_tune_kit.core import ATKError, digest, validate_evidence
from agent_tune_kit.execution import initialize_project, run_evaluation, source_groups_for_evidence, store_dataset
from tests.test_vnext_flow import git


def setup_project(tmp_path: Path, script: str, capture: dict | None = None) -> tuple[Path, dict]:
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "agent.py").write_text(script)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    git(repo, "add", "agent.py")
    git(repo, "commit", "-qm", "initial")
    initialize_project(
        repo,
        {
            "python": sys.executable,
            "command": [sys.executable, "agent.py", "{input_file}", "{output_dir}"],
            "components": [],
            "allowed_paths": [],
            "protected_paths": ["agent.py"],
            "external_effects": [],
            "runtime_notes": "offline",
            "observability": capture or {},
        },
    )
    source = tmp_path / "cases.jsonl"
    source.write_text(
        "\n".join(json.dumps({"id": x, "input": x, "usage": "optimization", "source_group_id": x}) for x in ["a", "b"])
    )
    dataset = store_dataset(
        repo / ".atk", {"source": str(source), "mapping": {k: k for k in ["id", "input", "usage", "source_group_id"]}}
    )
    return repo, {
        "dataset_id": dataset["id"],
        "case_ids": ["a", "b"],
        "revision_id": None,
        "revision_commit": git(repo, "rev-parse", "HEAD"),
        "purpose": "evaluation",
        "repeats": 1,
        "timeout_seconds": 3,
        "concurrency": 2,
    }


def event_script(message: str = "done", extra: dict | None = None) -> str:
    return (
        "import json,sys\nfrom pathlib import Path\n"
        "case=json.loads(Path(sys.argv[1]).read_text())\n"
        f"print(json.dumps({{'atk_log': {{'message': {message!r}, 'level': 'INFO', 'extra': {extra or {}}}}}}), file=sys.stderr, flush=True)\n"
        "print(json.dumps({'case':case}))\n"
    )


def test_parallel_attempt_events_and_native_evidence_are_directly_queryable(tmp_path: Path) -> None:
    repo, request = setup_project(
        tmp_path,
        event_script(extra={"phase": "work"})
        + "(Path(sys.argv[2])/'audit.jsonl').write_text(json.dumps({'decision':'keep','secret':'hidden'})+'\\n')\n",
        {
            "format": "atk_json",
            "required_messages": ["done"],
            "required_stages": ["work"],
            "artifacts": [{"glob": "audit.jsonl", "role": "model_audit", "required": True}],
        },
    )
    batch = run_evaluation(repo / ".atk", request)
    _, records, index = validate_evidence(repo / ".atk", batch["id"])
    assert len(records) == 2
    assert all(r.get("capture", {}).get("status") == "complete" for r in records.values())
    assert len([x for x in index.values() if x.get("kind") == "native_artifact"]) == 2
    assert callable(getattr(evidence, "view_execution", None))
    for record in records.values():
        view = evidence.view_execution(
            repo / ".atk", {"batch_id": batch["id"], "execution_id": record["execution"]["id"]}
        )
        assert all(x["execution_id"] == record["execution"]["id"] for x in view["events"])
        assert [x["message"] for x in view["events"] if x["source"] == "python"] == ["done"]
        ref = next(
            x
            for x in index.values()
            if x.get("kind") == "native_artifact" and x["execution_id"] == record["execution"]["id"]
        )
        detail = evidence.read_native_evidence(
            repo / ".atk", {"batch_id": batch["id"], "evidence_id": ref["evidence_id"]}
        )
        assert detail["rows"] == [{"decision": "keep", "secret": "[REDACTED]"}]
        assert source_groups_for_evidence(
            repo / ".atk", [{"batch_id": batch["id"], "evidence_id": ref["evidence_id"]}]
        ) == [record["case_id"]]
        assert view == evidence.view_execution(
            repo / ".atk", {"batch_id": batch["id"], "execution_id": record["execution"]["id"]}
        )
        Path(ref["path"]).write_text("changed")
        with pytest.raises(ATKError, match="指纹"):
            validate_evidence(repo / ".atk", batch["id"])
        break


@pytest.mark.parametrize(
    "capture,script,missing",
    [
        ({"format": "atk_json", "required_messages": ["done"]}, "print('answer')\n", "done"),
        ({"format": "atk_json", "required_stages": ["work"]}, event_script(), "work"),
        (
            {"format": "atk_json", "artifacts": [{"glob": "audit.jsonl", "role": "audit", "required": True}]},
            event_script(),
            "audit",
        ),
    ],
)
def test_missing_declared_observations_do_not_claim_complete(
    tmp_path: Path, capture: dict, script: str, missing: str
) -> None:
    repo, request = setup_project(tmp_path, script, capture)
    batch = run_evaluation(repo / ".atk", request)
    _, records, _ = validate_evidence(repo / ".atk", batch["id"])
    assert all(r.get("capture", {}).get("status") == "partial" for r in records.values())
    assert missing in json.dumps([r["capture"] for r in records.values()])
    assert all(r["execution"]["status"] == "completed" for r in records.values())


def test_events_are_persisted_before_agent_finishes_and_survive_timeout(tmp_path: Path) -> None:
    repo, request = setup_project(tmp_path, event_script() + "import time\ntime.sleep(10)\n", {"format": "atk_json"})
    runner_request = {
        **request,
        "batch_id": "batch-live",
        "workspace_path": str(repo),
        "run_config_ref": str(repo / ".atk/project.json"),
        "cases_path": str(repo / ".atk/datasets" / request["dataset_id"] / "cases.jsonl"),
        "attempts": [
            {
                "execution_id": "execution-live",
                "record_id": "record-live",
                "case_id": "a",
                "case_fingerprint": "f",
                "repeat_index": 1,
                "retry_of": None,
            }
        ],
    }
    path = tmp_path / "request.json"
    path.write_text(json.dumps(runner_request))
    folder = tmp_path / "output"
    process = subprocess.Popen(
        [sys.executable, str(repo / ".atk/adapters/runner.py"), "--request", str(path), "--output", str(folder)]
    )
    try:
        events = folder / "attempts/execution-live/events.jsonl"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and (not events.exists() or '"done"' not in events.read_text()):
            time.sleep(0.02)
        assert events.exists(), "日志必须在执行结束前落盘"
        assert '"done"' in events.read_text()
        assert process.poll() is None
        assert process.wait(timeout=5) == 0
        rows = [json.loads(x) for x in events.read_text().splitlines()]
        assert rows[-1]["message"] == "attempt.finished"
        assert rows[-1]["extra"]["status"] == "timeout"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_oversize_and_malformed_logs_are_visible_and_secrets_never_persist(tmp_path: Path) -> None:
    repo, request = setup_project(
        tmp_path,
        "import sys,time\nsys.stderr.write('api_');sys.stderr.flush();time.sleep(.01)\n"
        "sys.stderr.write('key=DO_NOT_STORE\\n');sys.stderr.flush()\n"
        "print('ATK_LOG {bad',file=sys.stderr)\nprint('x'*70000,file=sys.stderr)\nprint('answer')\n",
        {"format": "atk_json"},
    )
    batch = run_evaluation(repo / ".atk", request)
    _, records, _ = validate_evidence(repo / ".atk", batch["id"])
    assert all(r.get("capture", {}).get("status") == "partial" for r in records.values())
    text = "".join(f.read_text() for f in (repo / ".atk/evidence" / batch["id"]).rglob("*") if f.is_file())
    assert "DO_NOT_STORE" not in text
    assert "line_too_large" in text and "malformed_event" in text


def test_json_stderr_fallback_redacts_fields_without_corrupting_structure(tmp_path: Path) -> None:
    repo, request = setup_project(
        tmp_path,
        "import json,sys\nprint(json.dumps({'secret':'DO_NOT_STORE','detail':42}),file=sys.stderr)\nprint('answer')\n",
        {"format": "text"},
    )
    batch = run_evaluation(repo / ".atk", request)
    files = list((repo / ".atk/evidence" / batch["id"]).glob("attempts/*/events.jsonl"))
    assert all("DO_NOT_STORE" not in path.read_text() for path in files)


def test_logging_helper_captures_thread_and_async_events(tmp_path: Path) -> None:
    script = (
        "import sys,os,logging,asyncio\nfrom concurrent.futures import ThreadPoolExecutor\n"
        "sys.path.insert(0,os.path.dirname(os.environ['ATK_RUNNER_PATH']))\n"
        "from logging_json import install_logging\ninstall_logging(['application'],level='INFO')\n"
        "log=logging.getLogger('application');log.setLevel(logging.INFO)\n"
        "with ThreadPoolExecutor() as pool: pool.submit(log.info,'thread').result()\n"
        "async def work(): log.info('async')\nasyncio.run(work())\nprint('answer')\n"
    )
    repo, request = setup_project(tmp_path, script, {"format": "atk_json", "required_messages": ["thread", "async"]})
    batch = run_evaluation(repo / ".atk", request)
    _, records, _ = validate_evidence(repo / ".atk", batch["id"])
    assert all(r["execution"]["status"] == "completed" for r in records.values())
    assert all(r.get("capture", {}).get("status") == "complete" for r in records.values())


@pytest.mark.parametrize(
    "capture",
    [
        {"format": "bad"},
        {"artifacts": [{"glob": "../*.json", "role": "audit"}]},
        {"format": "text", "stage_field": []},
        {"format": "text", "artifacts": [{"glob": "x", "role": "audit", "required": "false"}]},
    ],
)
def test_invalid_capture_configuration_is_rejected(tmp_path: Path, capture: dict) -> None:
    with pytest.raises(ATKError, match="采集"):
        setup_project(tmp_path, "print('answer')", capture)


def test_loguru_native_fields_and_pagination_preserve_observed_context(tmp_path: Path) -> None:
    payload = {
        "record": {
            "message": "stage.started",
            "level": {"name": "INFO"},
            "extra": {"phase": "work", "run_id": "native-1", "api_key": "DO_NOT_STORE"},
            "time": {"repr": "source-time"},
            "name": "application",
            "file": {"name": "agent.py", "path": "/source/agent.py"},
            "line": 42,
            "thread": {"id": 7, "name": "worker"},
            "process": {"id": 8, "name": "main"},
        }
    }
    repo, request = setup_project(
        tmp_path,
        f"import sys\nprint({json.dumps(payload)!r},file=sys.stderr)\nprint('answer')\n",
        {"format": "loguru_json", "required_stages": ["work"]},
    )
    batch = run_evaluation(repo / ".atk", request)
    assert callable(getattr(evidence, "view_execution", None))
    view = evidence.view_execution(
        repo / ".atk", {"batch_id": batch["id"], "case_id": "a", "stage": "work", "limit": 1}
    )
    assert view["total"] == 1 and view["next_offset"] is None
    assert view["events"][0]["extra"] == {"phase": "work", "run_id": "native-1", "api_key": "[REDACTED]"}
    assert view["events"][0].get("line") == 42
    assert view["events"][0].get("thread") == {"id": 7, "name": "worker"}
    event_file = next(ref for ref in view["files"] if ref["role"] == "events")
    Path(event_file["path"]).write_text("changed")
    with pytest.raises(ATKError, match="指纹"):
        evidence.view_execution(repo / ".atk", {"batch_id": batch["id"]})


def test_capture_helper_drift_is_rejected_before_execution(tmp_path: Path) -> None:
    repo, request = setup_project(tmp_path, event_script(), {"format": "atk_json"})
    (repo / ".atk/adapters/logging_json.py").write_text("# changed")
    with pytest.raises(ATKError, match="采集"):
        run_evaluation(repo / ".atk", request)


def test_interrupted_batch_retains_queryable_unconfirmed_attempt_trace(tmp_path: Path) -> None:
    repo, request = setup_project(tmp_path, event_script() + "import time\ntime.sleep(10)\n", {"format": "atk_json"})
    batch = run_evaluation(repo / ".atk", {**request, "case_ids": ["a"], "batch_timeout_seconds": 1})
    assert batch["status"] == "interrupted"
    assert callable(getattr(evidence, "view_execution", None))
    view = evidence.view_execution(repo / ".atk", {"batch_id": batch["id"], "case_id": "a"})
    assert any(row["message"] == "done" for row in view["events"])
    assert view["executions"][0]["execution"]["status"] == "unknown"


def test_inherited_output_pipe_does_not_hang_after_parent_exits(tmp_path: Path) -> None:
    repo, request = setup_project(
        tmp_path,
        event_script() + "import subprocess\nsubprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'])\n",
        {"format": "atk_json"},
    )
    batch = run_evaluation(
        repo / ".atk", {**request, "case_ids": ["a"], "timeout_seconds": 0.3, "batch_timeout_seconds": 2}
    )
    _, records, _ = validate_evidence(repo / ".atk", batch["id"])
    assert batch["status"] == "sealed"
    assert "descendant_stream_open" in next(iter(records.values()))["capture"]["issues"]


def test_capture_file_loss_records_runner_failure_instead_of_silent_success(tmp_path: Path) -> None:
    repo, request = setup_project(
        tmp_path,
        "import sys\nfrom pathlib import Path\n(Path(sys.argv[2])/'events.jsonl').unlink()\nprint('answer')\n",
        {"format": "text"},
    )
    batch = run_evaluation(repo / ".atk", {**request, "case_ids": ["a"]})
    assert batch["status"] == "partial"
    assert "FileNotFoundError" in (batch.get("runner_error") or "")


@pytest.mark.parametrize("reader", ["core", "runner"])
def test_large_artifact_fingerprints_use_bounded_memory(tmp_path: Path, reader: str) -> None:
    path = tmp_path / "large.bin"
    with path.open("wb") as handle:
        for _ in range(16):
            handle.write(b"x" * 1024 * 1024)
    expected = hashlib.sha256(b"x" * 16 * 1024 * 1024).hexdigest()
    if reader == "runner":
        spec = importlib.util.spec_from_file_location(
            "memory_runner", Path(__file__).parents[1] / "templates/runner.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        capture = module.EventCapture(
            tmp_path,
            {"execution_id": "memory", "case_id": "a"},
            {"observability": {"format": "text", "artifacts": [{"glob": "large.bin", "role": "binary"}]}},
        )
    tracemalloc.start()
    try:
        actual = digest(path) if reader == "core" else capture.finish("completed")["files"][1]["sha256"]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert actual == expected
    assert peak < 2 * 1024 * 1024, "指纹校验不能一次加载完整日志/审计文件"


def test_invalid_output_encoding_is_not_replaced_with_a_fabricated_answer(tmp_path: Path) -> None:
    repo, request = setup_project(tmp_path, "import sys\nsys.stdout.buffer.write(b'bad\\xff')\n", {"format": "text"})
    batch = run_evaluation(repo / ".atk", {**request, "case_ids": ["a"]})
    _, records, _ = validate_evidence(repo / ".atk", batch["id"])
    record = next(iter(records.values()))
    assert not record["output_present"]
    assert "output_decode_error" in record["capture"]["issues"]


@pytest.mark.parametrize("bad", [{"extra": {}}, {"message": "bad", "extra": [1]}, {"message": [1]}])
def test_malformed_native_schema_does_not_silently_stop_log_collection(tmp_path: Path, bad: dict) -> None:
    repo, request = setup_project(
        tmp_path,
        "import sys\n" + f"print({json.dumps({'atk_log': bad})!r},file=sys.stderr)\n" + event_script(),
        {"format": "atk_json"},
    )
    batch = run_evaluation(repo / ".atk", {**request, "case_ids": ["a"]})
    _, records, _ = validate_evidence(repo / ".atk", batch["id"])
    record = next(iter(records.values()))
    assert "malformed_event" in record["capture"]["issues"]
    view = evidence.view_execution(repo / ".atk", {"batch_id": batch["id"]})
    assert any(event["message"] == "done" for event in view["events"])


def test_native_outlets_are_isolated_and_queryable_without_event_copies(tmp_path: Path) -> None:
    script = (
        "import os,sys,json,logging\nfrom pathlib import Path\n"
        "sys.path.insert(0,os.path.dirname(os.environ['ATK_RUNNER_PATH']))\n"
        "from logging_capture import capture_logging\n"
        "case=json.loads(Path(sys.argv[1]).read_text())\n"
        "log=logging.getLogger('application');log.setLevel(logging.INFO)\n"
        "with capture_logging(Path(sys.argv[2])/'logs/service.log', ['application'], "
        "logging.Formatter('%(levelname)s %(message)s')):\n"
        " log.info('case='+case)\n"
        "print('fallback api_key=DO_NOT_STORE',file=sys.stderr)\nprint('answer')\n"
    )
    repo, request = setup_project(
        tmp_path,
        script,
        {
            "format": "native",
            "logs": [{"glob": "logs/service.log", "format": "text", "required": True}],
        },
    )
    batch = run_evaluation(repo / ".atk", request)
    _, records, _ = validate_evidence(repo / ".atk", batch["id"])
    for record in records.values():
        assert record["capture"]["status"] == "complete"
        view = evidence.view_execution(
            repo / ".atk", {"batch_id": batch["id"], "execution_id": record["execution"]["id"]}
        )
        assert view["total"] == 1
        event = view["events"][0]
        assert event["message"] == "INFO case=" + record["case_id"]
        assert event["source_line"] == 1
        ref = next(x for x in view["files"] if x["role"] == "service_log")
        assert event["source_locator"] == ref["path"] + "#L1"
        detail = evidence.read_native_evidence(
            repo / ".atk", {"batch_id": batch["id"], "evidence_id": ref["evidence_id"]}
        )
        assert detail["rows"] == [event["message"]]
        directory = Path(record["source_locator"])
        assert not (directory / "events.jsonl").exists()
        assert (directory / "logs/stderr.log").read_text() == "fallback api_key=[REDACTED]\n"


def test_native_loguru_view_retains_raw_record_and_file_line(tmp_path: Path) -> None:
    payload = {
        "text": "native rendered text",
        "record": {
            "message": "done",
            "extra": {"phase": "work", "run_id": "run-a"},
            "level": {"name": "INFO"},
            "time": {"repr": "source-time"},
            "file": {"name": "source.py"},
            "line": 42,
            "custom_native_field": [1, 2],
        },
    }
    repo, request = setup_project(
        tmp_path,
        "import sys\nfrom pathlib import Path\n"
        "p=Path(sys.argv[2])/'logs';p.mkdir(exist_ok=True)\n"
        f"(p/'service.jsonl').write_text({json.dumps(payload)!r}+'\\n')\nprint('answer')\n",
        {
            "format": "native",
            "required_messages": ["done"],
            "required_stages": ["work"],
            "logs": [{"glob": "logs/service.jsonl", "format": "loguru_json", "required": True}],
        },
    )
    batch = run_evaluation(repo / ".atk", {**request, "case_ids": ["a"]})
    view = evidence.view_execution(repo / ".atk", {"batch_id": batch["id"], "stage": "work"})
    assert view["executions"][0]["capture"]["status"] == "complete"
    assert view["events"][0]["raw"] == payload
    assert view["events"][0]["line"] == 42
    assert view["events"][0]["source_line"] == 1


@pytest.mark.parametrize("interrupted", [False, True])
def test_native_logs_survive_timeout_and_batch_interruption(tmp_path: Path, interrupted: bool) -> None:
    script = (
        "import os,sys,logging,time\nfrom pathlib import Path\n"
        "sys.path.insert(0,os.path.dirname(os.environ['ATK_RUNNER_PATH']))\n"
        "from logging_capture import capture_logging\n"
        "log=logging.getLogger('application');log.setLevel(logging.INFO)\n"
        "with capture_logging(Path(sys.argv[2])/'logs/service.log', ['application'], "
        "logging.Formatter('%(message)s')):\n"
        " log.info('before termination')\n time.sleep(10)\n"
    )
    repo, request = setup_project(
        tmp_path,
        script,
        {
            "format": "native",
            "logs": [{"glob": "logs/service.log", "format": "text", "required": True}],
        },
    )
    batch = run_evaluation(
        repo / ".atk",
        {
            **request,
            "case_ids": ["a"],
            "timeout_seconds": 3 if interrupted else 1,
            "batch_timeout_seconds": 1 if interrupted else 5,
        },
    )
    view = evidence.view_execution(repo / ".atk", {"batch_id": batch["id"]})
    assert [row["message"] for row in view["events"]] == ["before termination"]
    execution = view["executions"][0]
    assert execution["capture"]["status"] == "partial"
    assert execution["execution"]["status"] == ("unknown" if interrupted else "timeout")


@pytest.mark.parametrize(
    "logs", [[], [{"glob": "../service.log", "format": "text"}], [{"glob": "logs/service.log", "format": "unknown"}]]
)
def test_native_log_configuration_rejects_missing_or_unsafe_sources(tmp_path, logs):
    with pytest.raises(ATKError, match="采集"):
        setup_project(tmp_path, "print('answer')", {"format": "native", "logs": logs})


def test_missing_native_outlet_is_a_capture_gap(tmp_path):
    repo, request = setup_project(
        tmp_path,
        "print('answer')",
        {
            "format": "native",
            "logs": [{"glob": "logs/service.log", "format": "text", "required": True}],
        },
    )
    batch = run_evaluation(repo / ".atk", {**request, "case_ids": ["a"]})
    view = evidence.view_execution(repo / ".atk", {"batch_id": batch["id"]})
    assert view["executions"][0]["capture"]["status"] == "partial"
    assert "missing_log:logs/service.log" in view["executions"][0]["capture"]["issues"]


def test_malformed_native_line_keeps_original_and_later_records(tmp_path):
    payload = {"record": {"message": "done", "extra": {}}}
    repo, request = setup_project(
        tmp_path,
        "import sys\nfrom pathlib import Path\n"
        f"(Path(sys.argv[2])/'logs/service.jsonl').write_text('bad native line\\n'+{json.dumps(payload)!r}+'\\n')\n"
        "print('answer')\n",
        {"format": "native", "logs": [{"glob": "logs/service.jsonl", "format": "loguru_json", "required": True}]},
    )
    batch = run_evaluation(repo / ".atk", {**request, "case_ids": ["a"]})
    view = evidence.view_execution(repo / ".atk", {"batch_id": batch["id"]})
    assert view["executions"][0]["capture"]["status"] == "partial"
    assert [r["message"] for r in view["events"]] == ["bad native line", "done"]
    ref = next(r for r in view["files"] if r["role"] == "service_log")
    assert Path(ref["path"]).read_text().startswith("bad native line\n")


def test_caught_outlet_failure_cannot_claim_complete_capture(tmp_path):
    script = (
        "import os,sys,logging\nfrom pathlib import Path\n"
        "sys.path.insert(0,os.path.dirname(os.environ['ATK_RUNNER_PATH']))\n"
        "from logging_capture import capture_logging\n"
        "log=logging.getLogger('application');log.setLevel(logging.INFO)\n"
        "with capture_logging(Path(sys.argv[2])/'logs/service.log', ['application'], logging.Formatter('%(field)s')):\n"
        " log.info('ok',extra={'field':'first'})\n"
        " try: log.info('bad')\n except ValueError: pass\n"
        "print('answer')\n"
    )
    repo, request = setup_project(
        tmp_path,
        script,
        {
            "format": "native",
            "logs": [{"glob": "logs/service.log", "format": "text", "required": True}],
        },
    )
    batch = run_evaluation(repo / ".atk", {**request, "case_ids": ["a"]})
    view = evidence.view_execution(repo / ".atk", {"batch_id": batch["id"]})
    execution = view["executions"][0]
    assert execution["execution"]["status"] == "completed"
    assert execution["capture"]["status"] == "partial"
    assert "native_outlet_error" in execution["capture"]["issues"]
