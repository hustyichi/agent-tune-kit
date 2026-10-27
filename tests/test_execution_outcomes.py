import json

import pytest

from tests.test_cli_workflow import read_csv, result_fixture, run_cli, successful, write_adapter
from tests.test_execution_evidence import evidence
from tests.test_execution_tracing import v2_adapter


def grouped_adapter(project):
    path = v2_adapter(
        project,
        "def call_agent(values, *, execution):\n"
        "    return {aid: {'processing_status': 'RETRYABLE_FAILED' if aid == '1' else 'COMPLETED', "
        "'error_code': 'LLM_TIMEOUT' if aid == '1' else ''} for aid in values}\n",
    )
    with path.open("a") as handle:
        handle.write(
            "def build_agent_input(source): return source\n"
            "def plan_executions(selected_rows, all_rows):\n"
            "    return [{'sample_ids': [r['atk_id'] for r in selected_rows], "
            "'source': [r['input'] for r in all_rows], 'context_ids': [r['atk_id'] for r in all_rows]}]\n"
            "def map_agent_output(raw, item):\n"
            "    return {aid: {'output': raw[aid], 'location': '/' + aid, "
            "'output_status': 'error' if raw[aid]['error_code'] else 'ok', "
            "'error_type': raw[aid]['error_code'], "
            "'error_message': 'request timed out' if raw[aid]['error_code'] else ''} "
            "for aid in item['sample_ids']}\n"
        )
    return path


def test_mapped_technical_failure_preserves_siblings_and_business_unknowns(tmp_path):
    grouped_adapter(tmp_path)

    result = successful(tmp_path, "eval")
    root = tmp_path / ".atk/results/v1"
    rows = read_csv(root / "eval_results.csv")

    assert [r["output_status"] for r in rows] == ["error", "ok", "ok"]
    assert rows[0]["error_type"] == "LLM_TIMEOUT"
    assert rows[0]["error_message"] == "request timed out"
    assert json.loads(rows[0]["agent_output"])["processing_status"] == "RETRYABLE_FAILED"
    assert result["technical_failures"] == 1
    assert result["technical_failure_ids"] == ["1"]
    summary = successful(tmp_path, "results", "inspect")
    assert (summary["passed"], summary["failed"], summary["unknown"]) == (0, 0, 3)
    assert summary["technical_failures"] == 1
    assert summary["technical_failure_ids"] == ["1"]
    directory = evidence().locate_execution(root, atk_id="1")
    assert evidence().inspect_execution(directory)["status"] == "technical_failure"
    assert json.loads((root / "run.json").read_text())["status"] == "completed"
    assert not (root / "judgments.csv").exists()
    assert not (root / "failure_cases.csv").exists()
    rerun = run_cli(tmp_path, "eval", "--only-failures")
    assert rerun.returncode == 2
    assert not (tmp_path / ".atk/results/v2").exists()


@pytest.mark.parametrize(
    "metadata",
    [
        {"output_status": "failed"},
        {"output_status": None},
        {"output_status": "error", "error_type": 7},
        {"output_status": "error", "error_message": []},
        {"output_status": "ok", "error_type": "LLM_TIMEOUT"},
    ],
)
def test_invalid_mapped_status_is_not_silently_accepted(tmp_path, metadata):
    path = v2_adapter(tmp_path)
    with path.open("a") as handle:
        handle.write(
            "def map_agent_output(raw, item):\n"
            f"    return {{aid: dict(output=raw, location='', **{metadata!r}) for aid in item['sample_ids']}}\n"
        )

    result = run_cli(tmp_path, "eval")

    assert result.returncode == 2
    assert read_csv(tmp_path / ".atk/results/v1/eval_results.csv") == []
    assert json.loads((tmp_path / ".atk/results/v1/run.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("adapter", [write_adapter, v2_adapter], ids=["v1", "v2"])
def test_ids_filter_before_offset_and_limit_in_canonical_order(tmp_path, adapter):
    adapter(tmp_path)

    check = successful(tmp_path, "eval", "--ids", "3", "1", "--offset", "1", "--limit", "1", "--check")
    assert check["rows"] == 1
    assert not (tmp_path / ".atk/results").exists()
    result = successful(tmp_path, "eval", "--ids", "3", "1", "--offset", "1", "--limit", "1")

    assert result["rows"] == 1
    assert [r["atk_id"] for r in read_csv(tmp_path / ".atk/results/v1/eval_results.csv")] == ["3"]
    assert result["technical_failures"] == 0


def test_ids_preserve_full_group_context_for_unknown_sample(tmp_path):
    grouped_adapter(tmp_path)

    successful(tmp_path, "eval", "--ids", "2")
    root = tmp_path / ".atk/results/v1"

    assert [r["atk_id"] for r in read_csv(root / "eval_results.csv")] == ["2"]
    directory = evidence().locate_execution(root, atk_id="2")
    assert json.loads((directory / "input.json").read_text()) == ["1", "2", "3"]
    assert successful(tmp_path, "results", "inspect")["technical_failures"] == 0


@pytest.mark.parametrize("flags", [("--ids", "999"), ("--ids", "1", "--only-failures")])
def test_invalid_id_selection_cannot_allocate_or_fall_back_to_full_run(tmp_path, flags):
    v2_adapter(tmp_path)

    result = run_cli(tmp_path, "eval", *flags)

    assert result.returncode == 2
    assert not (tmp_path / ".atk/results").exists()


def test_legacy_csv_without_execution_status_does_not_claim_zero_technical_failures(tmp_path):
    result_fixture(tmp_path)

    summary = successful(tmp_path, "results", "inspect")

    assert summary["technical_failures"] is None
    assert summary["technical_failure_ids"] == []


@pytest.mark.parametrize("flags", [(), ("--check",)])
def test_explicit_ids_reject_standalone_runner_before_loading_it(tmp_path, flags):
    runner = tmp_path / ".atk/runner/eval_runner.py"
    runner.parent.mkdir(parents=True)
    runner.write_text("from pathlib import Path\nPath('CALLED').touch()\n")

    result = run_cli(tmp_path, "eval", "--ids", "1", *flags)

    assert result.returncode == 2
    assert not (tmp_path / "CALLED").exists()
    assert not (tmp_path / ".atk/results").exists()
