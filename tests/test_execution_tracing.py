import importlib.util
import json
import logging

from tests.test_cli_workflow import read_csv, run_cli, successful, write_adapter
from tests.test_execution_evidence import evidence


def v2_adapter(project, body=None):
    path = write_adapter(project)
    path.write_text(
        "ATK_MANAGED_RUNNER = True\nATK_ADAPTER_VERSION = 2\n"
        "EVIDENCE = {'retain': lambda role, value: value, 'logging': ['fixture'], "
        "'sources': [{'source': 'fixture', 'scope': 'public calls', 'closed': True}]}\n"
        "def build_agent_input(row): return row['input']\n"
        + (
            body
            or "def call_agent(value, *, execution):\n"
            "    import logging\n"
            "    with execution.span('model', {'request': value}):\n"
            "        logging.getLogger('fixture').warning('observed %s', value)\n"
            "        execution.artifact('response', {'text': value})\n"
            "        return {'answer': value}\n"
        )
    )
    return path


def test_v2_cli_produces_independent_execution_evidence(tmp_path):
    v2_adapter(tmp_path)
    result = successful(tmp_path, "eval", "--concurrency", "2")
    root = tmp_path / ".atk/results" / result["version"]
    assert (root / "run.json").exists(), "V2 did not create a Run"
    run = evidence().inspect_run(root)
    assert len(run["executions"]) == 3
    rows = read_csv(root / "eval_results.csv")
    for row in rows:
        directory = evidence().locate_execution(root, atk_id=row["atk_id"])
        assert json.loads((directory / "input.json").read_text()) == row["input"]
        events = evidence().read_events(directory)["events"]
        assert any(event["kind"] == "log" for event in events)
        assert (root / row["log_path"]).is_file()
        assert all(event["execution_id"] == directory.name for event in events)


def test_v2_handles_async_builder_and_sync_returning_awaitable(tmp_path):
    path = v2_adapter(
        tmp_path, "def call_agent(value, *, execution):\n    async def reply(): return value\n    return reply()\n"
    )
    path.write_text(path.read_text().replace("def build_agent_input", "async def build_agent_input"))
    successful(tmp_path, "eval")
    rows = read_csv(tmp_path / ".atk/results/v1/eval_results.csv")
    assert [row["agent_output"] for row in rows] == ["1", "2", "3"]


def test_v2_grouping_scores_only_selected_samples(tmp_path):
    path = v2_adapter(tmp_path, "def call_agent(value, *, execution): return {'both': value}\n")
    with path.open("a") as handle:
        handle.write(
            "def plan_executions(selected_rows, all_rows):\n"
            "    return [{'sample_ids': [r['atk_id'] for r in selected_rows], 'source': all_rows[0], "
            "'context_ids': [r['atk_id'] for r in all_rows]}]\n"
            "def map_agent_output(raw, item):\n"
            "    return {aid: {'output': raw, 'location': '/both'} for aid in item['sample_ids']}\n"
        )
    successful(tmp_path, "eval", "--limit", "1")
    root = tmp_path / ".atk/results/v1"
    assert (root / "run.json").exists(), "Grouped execution was not recorded"
    assert len(evidence().inspect_run(root)["executions"]) == 1
    assert len(read_csv(root / "eval_results.csv")) == 1


def test_unknown_adapter_version_rejected_before_module_execution(tmp_path):
    path = v2_adapter(tmp_path)
    path.write_text(
        "raise RuntimeError('MODULE_EXECUTED')\n" + path.read_text().replace("VERSION = 2", "VERSION = 999")
    )
    result = run_cli(tmp_path, "eval")
    assert result.returncode == 2
    assert "MODULE_EXECUTED" not in result.stderr
    assert not (tmp_path / ".atk/results").exists()


def test_required_input_retention_failure_prevents_agent_and_preserves_scene(tmp_path):
    path = v2_adapter(
        tmp_path,
        "def call_agent(value, *, execution):\n"
        "    from pathlib import Path\n    Path('CALLED').touch()\n    return value\n",
    )
    with path.open("a") as handle:
        handle.write(
            "def retain(role, value):\n"
            "    if role == 'input': raise ValueError('SECRET')\n"
            "    return value\nEVIDENCE['retain'] = retain\n"
        )
    result = run_cli(tmp_path, "eval")
    assert result.returncode != 0
    assert not (tmp_path / "CALLED").exists()
    assert "SECRET" not in result.stderr
    assert list((tmp_path / ".atk/results/v1/executions").glob("*/execution.json"))


def test_observer_preserves_original_exception_and_logger_configuration(tmp_path):
    assert importlib.util.find_spec("agent_tune_kit.execution.tracing"), "Tracing observer missing"
    from agent_tune_kit.execution.tracing import Observer, capture_logs

    run = evidence().RunEvidence(tmp_path / "v1", policy={"retain": lambda role, value: value})
    item = run.execution(["1"])
    observer = Observer(item, run)
    logger = logging.getLogger("fixture-test")
    before = (logger.level, list(logger.handlers), logger.propagate)
    try:
        with capture_logs(run, {"logging": ["fixture-test"]}), observer.activate(), observer.span("tool"):
            logger.warning("hello")
            raise LookupError("business")
    except LookupError as exc:
        assert str(exc) == "business"
    else:
        raise AssertionError("Business exception was swallowed")
    assert (logger.level, logger.handlers, logger.propagate) == before
    assert any(event["kind"] == "log" for event in evidence().read_events(item.directory)["events"])


def test_atk_truth_metadata_is_not_sent_to_builders(tmp_path):
    from tests.test_cli_workflow import write_csv

    path = v2_adapter(tmp_path)
    path.write_text(path.read_text().replace("return row['input']", "return sorted(row)"))
    write_csv(
        tmp_path / ".atk/datasets/dataset.csv",
        ["atk_id", "input", "expected", "atk_truth"],
        [{"atk_id": "1", "input": "one", "expected": "answer", "atk_truth": '{"schema":1,"fields":{}}'}],
    )
    successful(tmp_path, "eval")
    root = tmp_path / ".atk/results/v1"
    item = evidence().locate_execution(root, atk_id="1")
    assert "atk_truth" not in json.loads((item / "input.json").read_text())
    assert read_csv(root / "eval_results.csv")[0]["atk_truth"] == '{"schema":1,"fields":{}}'


def test_collection_error_stops_bounded_scheduler(tmp_path):
    path = v2_adapter(
        tmp_path,
        "def call_agent(value, *, execution):\n"
        "    from pathlib import Path\n"
        "    with Path('calls').open('a') as f: f.write(value + '\\n')\n"
        "    execution.artifact('denied', value)\n    return value\n",
    )
    with path.open("a") as handle:
        handle.write(
            "def retain(role, value):\n"
            "    if role == 'denied': raise ValueError('hidden')\n"
            "    return value\nEVIDENCE['retain'] = retain\n"
        )
    result = run_cli(tmp_path, "eval", "--concurrency", "1")
    assert result.returncode != 0
    assert (tmp_path / "calls").read_text().splitlines() == ["1"]


def test_loguru_and_internal_thread_context_preserve_business_sink(tmp_path):
    path = v2_adapter(
        tmp_path,
        "def call_agent(value, *, execution):\n"
        "    from loguru import logger\n"
        "    from contextvars import copy_context\n"
        "    from threading import Thread\n"
        "    context = copy_context()\n"
        "    thread = Thread(target=lambda: context.run(logger.info, 'token {}', value))\n"
        "    thread.start(); thread.join()\n    return value\n",
    )
    with path.open("a") as handle:
        handle.write("EVIDENCE['loguru'] = True\n")
    successful(tmp_path, "eval", "--concurrency", "2")
    root = tmp_path / ".atk/results/v1"
    for aid in ["1", "2", "3"]:
        item = evidence().locate_execution(root, atk_id=aid)
        messages = [
            event["attributes"]["message"] for event in evidence().read_events(item)["events"] if event["kind"] == "log"
        ]
        assert messages == [f"token {aid}"]


def test_packaged_v2_template_requires_explicit_retention(tmp_path):
    import re
    from pathlib import Path

    template = (Path(__file__).parents[1] / "templates/.atk/runner/eval_runner.py.md").read_text()
    code = re.search(r"```python\n(.*?)\n```", template, re.S).group(1)
    assert "ATK_ADAPTER_VERSION = 2" in code
    path = v2_adapter(tmp_path)
    path.write_text(code)
    result = run_cli(tmp_path, "eval", "--check")
    assert result.returncode != 0
    assert not (tmp_path / ".atk/results").exists()


def test_declared_direct_artifact_is_registered_and_verified(tmp_path):
    path = v2_adapter(
        tmp_path,
        "def call_agent(value, *, execution):\n"
        "    (execution.artifact_directory / 'business.txt').write_text(value)\n    return value\n",
    )
    with path.open("a") as handle:
        handle.write("EVIDENCE['allow_direct_artifacts'] = True\n")
    successful(tmp_path, "eval", "--limit", "1")
    directory = evidence().locate_execution(tmp_path / ".atk/results/v1", atk_id="1")
    item = evidence().inspect_execution(directory)
    assert any(entry["path"] == "artifacts/business.txt" for entry in item["files"])


def test_optional_artifact_rejection_is_visible_without_failing_business(tmp_path):
    path = v2_adapter(
        tmp_path, "def call_agent(value, *, execution):\n    execution.artifact('optional', value)\n    return value\n"
    )
    with path.open("a") as handle:
        handle.write(
            "def retain(role, value):\n"
            "    if role == 'optional': raise ValueError('secret')\n"
            "    return value\nEVIDENCE.update(retain=retain, optional_roles=['optional'])\n"
        )
    successful(tmp_path, "eval", "--limit", "1")
    directory = evidence().locate_execution(tmp_path / ".atk/results/v1", atk_id="1")
    assert "optional" in json.dumps(evidence().inspect_execution(directory)["coverage"])


def test_original_error_is_retained_when_collection_also_fails(tmp_path):
    from agent_tune_kit.execution.tracing import Observer

    run = evidence().RunEvidence(tmp_path / "v1", policy={"retain": lambda role, value: value})
    item = run.execution([])
    observer = Observer(item, run)

    def fail(role, value):
        raise ValueError("private")

    item.policy = {"retain": fail}
    try:
        with observer.span("call"):
            raise LookupError("original")
    except LookupError as exc:
        assert str(exc) == "original"
    else:
        raise AssertionError("Original exception changed")
    assert run.failed.is_set()


def test_stream_observation_does_not_advance_unconsumed_fragments(tmp_path):
    from agent_tune_kit.execution.tracing import Observer

    run = evidence().RunEvidence(tmp_path / "v1", policy={"retain": lambda role, value: value})
    item = run.execution([])
    observer = Observer(item, run)
    consumed = []

    def stream():
        for value in range(3):
            consumed.append(value)
            yield value

    source = stream()
    with observer.activate(), observer.span("stream"):
        observer.event("chunk", {"value": next(source)})
    assert consumed == [0]


def test_new_evidence_log_link_rejects_symlink(tmp_path):
    from agent_tune_kit.visualize.failures import safe_log_href

    outside = tmp_path / "outside.log"
    outside.write_text("private")
    run = evidence().RunEvidence(tmp_path / "v1", policy={"retain": lambda role, value: value})
    (run.directory / "link.log").symlink_to(outside)
    assert safe_log_href("link.log", run.directory) == ""


def test_declared_scope_requires_explicit_source_closure(tmp_path):
    path = v2_adapter(
        tmp_path,
        "def call_agent(value, *, execution):\n"
        "    execution.event('source.closed', {'source': 'fixture'})\n    return value\n",
    )
    successful(tmp_path, "eval", "--limit", "1")
    directory = evidence().locate_execution(tmp_path / ".atk/results/v1", atk_id="1")
    assert evidence().inspect_execution(directory)["coverage"]["status"] == "complete_declared"
    path.write_text(path.read_text().replace("execution.event('source.closed', {'source': 'fixture'})", "pass"))
    successful(tmp_path, "eval", "--limit", "1")
    directory = evidence().locate_execution(tmp_path / ".atk/results/v2", atk_id="1")
    assert evidence().inspect_execution(directory)["coverage"]["status"] == "partial"


def test_effective_standard_and_engine_snapshot_are_saved(tmp_path):
    v2_adapter(tmp_path)
    (tmp_path / ".atk/context.md").write_text("# Ground Truth Standard\nCriterion A\n")
    successful(tmp_path, "eval", "--limit", "1")
    directory = evidence().locate_execution(tmp_path / ".atk/results/v1", atk_id="1")
    item = evidence().inspect_execution(directory)
    assert any(entry.get("source", {}).get("path") == ".atk/context.md" for entry in item["files"])
    environment = json.loads((directory / "context/environment.json").read_text())
    assert environment["engine_sha256"]


def test_v2_interrupt_preserves_completed_item_and_releases_occupation(tmp_path):
    import signal
    import subprocess
    import sys
    import time

    v2_adapter(
        tmp_path,
        "async def call_agent(value, *, execution):\n"
        "    import asyncio\n"
        "    if value != '1': await asyncio.sleep(20)\n    return value\n",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "agent_tune_kit.cli", "eval", "--json"],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    root = tmp_path / ".atk/results/v1"
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (root / "eval_results.csv").exists() and read_csv(root / "eval_results.csv"):
                break
            time.sleep(0.02)
        assert read_csv(root / "eval_results.csv")
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=10)
        assert process.returncode == 130
        assert not (root.parent / ".writer.json").exists()
        item = evidence().locate_execution(root, atk_id="1")
        assert evidence().inspect_execution(item)["status"] == "completed"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_five_case_calls_map_to_ten_scored_samples(tmp_path):
    from tests.test_cli_workflow import write_csv

    path = v2_adapter(
        tmp_path,
        "def call_agent(value, *, execution):\n"
        "    execution.artifact('audit', {'case_input': value})\n    return value\n",
    )
    with path.open("a") as handle:
        handle.write(
            "def plan_executions(selected_rows, all_rows):\n"
            "    return [{'sample_ids':[r['atk_id'] for r in selected_rows if r['case']==case],\n"
            "             'context_ids':[r['atk_id'] for r in all_rows if r['case']==case],\n"
            "             'source':{'input':case}} for case in dict.fromkeys(r['case'] for r in selected_rows)]\n"
            "def map_agent_output(raw,item): return {aid:{'output':raw,'location':''} for aid in item['sample_ids']}\n"
        )
    write_csv(
        tmp_path / ".atk/datasets/dataset.csv",
        ["atk_id", "input", "case", "expected"],
        [{"atk_id": str(i), "input": str(i), "case": str((i - 1) // 2), "expected": "ok"} for i in range(1, 11)],
    )
    successful(tmp_path, "eval", "--concurrency", "3")
    root = tmp_path / ".atk/results/v1"
    assert len(evidence().inspect_run(root)["executions"]) == 5
    assert len(read_csv(root / "eval_results.csv")) == 10


def test_snapshot_drift_is_visible_without_replacing_original_bytes(tmp_path):
    path = v2_adapter(
        tmp_path,
        "def call_agent(value, *, execution):\n"
        "    from pathlib import Path\n    Path('prompt.txt').write_text('after')\n    return value\n",
    )
    (tmp_path / "prompt.txt").write_text("before")
    with path.open("a") as handle:
        handle.write("EVIDENCE['snapshots'] = ['prompt.txt']\n")
    successful(tmp_path, "eval", "--limit", "1")
    directory = evidence().locate_execution(tmp_path / ".atk/results/v1", atk_id="1")
    item = evidence().inspect_execution(directory)
    assert "snapshot_drift" in json.dumps(item["coverage"])
    artifact = next(entry for entry in item["files"] if entry.get("source", {}).get("path") == "prompt.txt")
    assert (directory / artifact["path"]).read_text() == "before"


def test_nested_spans_and_structured_logging_preserve_association(tmp_path):
    from agent_tune_kit.execution.tracing import Observer, capture_logs

    run = evidence().RunEvidence(tmp_path / "v1", policy={"retain": lambda role, value: value})
    item = run.execution([])
    observer = Observer(item, run)
    with (
        capture_logs(run, {"logging": ["structured"]}),
        observer.activate(),
        observer.span("model"),
        observer.span("attempt"),
    ):
        logging.getLogger("structured").warning("retry", extra={"attempt": 2})
    events = evidence().read_events(item.directory)["events"]
    starts = [event for event in events if event["kind"] == "span.started"]
    assert starts[1]["parent_span_id"] == starts[0]["span_id"]
    logged = next(event for event in events if event["kind"] == "log")
    assert logged["span_id"] == starts[1]["span_id"]
    assert logged["attributes"]["extra"]["attempt"] == 2
