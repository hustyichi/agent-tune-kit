from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys

import pytest


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run_cli(project, *args):
    return subprocess.run(
        [sys.executable, "-m", "agent_tune_kit.cli", *args],
        cwd=project,
        text=True,
        capture_output=True,
        timeout=30,
    )


def successful(project, *args):
    result = run_cli(project, *args, "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_prepare_reuse_append_and_protect_existing_dataset(tmp_path):
    source = tmp_path / "source.csv"
    write_csv(source, ["input", "expected"], [{"input": "a", "expected": "A"}])
    successful(tmp_path, "dataset", "prepare", "--source", str(source))
    target = tmp_path / ".atk/datasets/dataset.csv"
    original = target.read_bytes()
    successful(tmp_path, "dataset", "prepare", "--source", str(source))
    assert target.read_bytes() == original
    write_csv(source, ["input", "expected"], [{"input": "b", "expected": "B"}])
    assert run_cli(tmp_path, "dataset", "prepare", "--source", str(source)).returncode == 2
    assert target.read_bytes() == original
    successful(tmp_path, "dataset", "prepare", "--source", str(source), "--append")
    assert [row["atk_id"] for row in read_csv(target)] == ["1", "2"]
    assert not (tmp_path / ".atk/results").exists()


def test_update_and_visualization_keep_schema_and_json_output(tmp_path):
    dataset = tmp_path / ".atk/datasets/dataset.csv"
    write_csv(
        dataset,
        ["atk_id", "input", "expected", "custom"],
        [{"atk_id": "7", "input": "a", "expected": "old", "custom": "keep"}],
    )
    updates = tmp_path / "updates.csv"
    write_csv(updates, ["atk_id", "expected"], [{"atk_id": "7", "expected": "new"}])
    output = successful(
        tmp_path, "dataset", "update", "--direct", "--field", "expected", "--updates-path", str(updates)
    )
    assert output["updated_rows"] == 1
    assert read_csv(dataset) == [{"atk_id": "7", "input": "a", "expected": "new", "custom": "keep"}]
    successful(tmp_path, "visualize", "dataset", "--no-open")
    html = dataset.with_suffix(".html").read_text()
    assert "dataset-data" in html and "new" in html
    assert run_cli(tmp_path, "visualize", "dataset", "--no-open").returncode == 2


def test_visualization_keeps_reviewable_invalid_ids_and_extra_values(tmp_path):
    dataset = tmp_path / ".atk/datasets/dataset.csv"
    dataset.parent.mkdir(parents=True)
    dataset.write_text("atk_id,input,expected,ground_truth\n1,a,A,GA,extra\n1,b,B,GB\n")
    before = dataset.read_bytes()
    result = successful(tmp_path, "visualize", "dataset", "--no-open")
    assert result["warnings"] and result["issue_rows"] == 2
    assert dataset.read_bytes() == before
    assert "__extra_values" in dataset.with_suffix(".html").read_text()


def result_fixture(project, version="v1"):
    current = project / ".atk/results" / version
    write_csv(
        current / "eval_results.csv",
        ["atk_id", "input", "expected", "agent_output"],
        [{"atk_id": str(i), "input": str(i), "expected": "ok", "agent_output": "out"} for i in range(1, 4)],
    )
    return current


def test_judgments_preserve_unknown_and_reject_stale_input(tmp_path):
    current = result_fixture(tmp_path)
    result_path = current / "eval_results.csv"
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    updates = tmp_path / "judgments.csv"
    write_csv(
        updates,
        ["atk_id", "verdict", "reason", "results_sha256"],
        [
            {"atk_id": str(i), "verdict": verdict, "reason": "criterion", "results_sha256": digest}
            for i, verdict in enumerate(["passed", "failed", "unknown"], 1)
        ],
    )
    successful(
        tmp_path, "results", "apply-judgments", "--judgments-path", str(updates), "--source-results", str(result_path)
    )
    assert [row["atk_id"] for row in read_csv(current / "failure_cases.csv")] == ["2"]
    summary = successful(tmp_path, "results", "inspect")
    assert summary["results_sha256"] == digest
    assert (summary["passed"], summary["failed"], summary["unknown"]) == (1, 1, 1)
    before = (current / "judgments.csv").read_bytes()
    result_path.write_bytes(result_path.read_bytes() + b"\n")
    assert run_cli(tmp_path, "results", "apply-judgments", "--judgments-path", str(updates)).returncode == 2
    assert (current / "judgments.csv").read_bytes() == before
    assert successful(tmp_path, "results", "inspect")["passed"] == 0
    successful(tmp_path, "visualize", "failures", "--no-open")


def test_current_version_never_falls_back(tmp_path):
    result_fixture(tmp_path)
    (tmp_path / ".atk/results/v2").mkdir()
    assert run_cli(tmp_path, "visualize", "failures", "--no-open").returncode == 2
    summary = successful(tmp_path, "results", "inspect")
    assert summary["version"] == "v2" and summary["executed"] is None


def write_adapter(project, *, async_agent=False):
    path = project / ".atk/runner/eval_runner.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "ATK_MANAGED_RUNNER = True\nPYTHON_LOGGING_CAPTURE_ENABLED = True\n"
        "ROW_LOGGER_NAMES = ('agent',)\n"
        "def build_agent_input(row): return row['input']\n"
        + ("async " if async_agent else "")
        + "def call_agent(value):\n"
        "    import logging\n"
        "    logging.getLogger('agent').warning('row %s', value)\n"
        "    return {'answer': value}\n"
    )
    write_csv(
        project / ".atk/datasets/dataset.csv",
        ["input", "expected", "atk_id"],
        [{"input": str(i), "expected": str(i), "atk_id": str(i)} for i in range(1, 4)],
    )
    return path


@pytest.mark.parametrize("async_agent", [False, True])
def test_project_interpreter_execution_check_logging_and_failure_rerun(tmp_path, async_agent):
    write_adapter(tmp_path, async_agent=async_agent)
    # A separate interpreter with no ATK installation proves runtime bootstrapping.
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(tmp_path / "env")], check=True)
    python = tmp_path / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    successful(tmp_path, "eval", "--check", "--python", str(python))
    assert not (tmp_path / ".atk/results").exists()
    output = successful(tmp_path, "eval", "--python", str(python), "--concurrency", "2")
    assert output["version"] == "v1"
    current = tmp_path / ".atk/results/v1"
    rows = read_csv(current / "eval_results.csv")
    assert len(rows) == 3
    for row in rows:
        assert json.loads(row["agent_output"]) == {"answer": row["input"]}
        assert (current / row["log_path"]).read_text().count("row ") == 1
    write_csv(current / "failure_cases.csv", list(rows[0]), [rows[0]])
    output = successful(tmp_path, "eval", "--python", str(python), "--only-failures")
    assert output["version"] == "v2"
    assert [row["atk_id"] for row in read_csv(tmp_path / ".atk/results/v2/eval_results.csv")] == [rows[0]["atk_id"]]


def test_legacy_runner_executes_once_without_preallocating_results(tmp_path):
    runner = tmp_path / ".atk/runner/eval_runner.py"
    runner.parent.mkdir(parents=True)
    runner.write_text(
        "from pathlib import Path\np=Path('executions')\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
    )
    assert run_cli(tmp_path, "eval").returncode == 0
    assert (tmp_path / "executions").read_text() == "x"
    assert not (tmp_path / ".atk/results").exists()


def test_rule_execution_and_comparison_do_not_infer_pass(tmp_path):
    current = result_fixture(tmp_path)
    rule = tmp_path / ".atk/runner/failure_rule.py"
    rule.parent.mkdir(parents=True)
    rule.write_text("def judge(row): return ('failed' if row['atk_id']=='2' else 'unknown', 'criterion')\n")
    successful(tmp_path, "results", "run-rule")
    assert [row["atk_id"] for row in read_csv(current / "failure_cases.csv")] == ["2"]
    assert successful(tmp_path, "results", "inspect")["unknown"] == 2
    result_fixture(tmp_path, "v2")
    compared = successful(tmp_path, "results", "compare", "--ids", "2")
    assert compared["status"] == "无法判断"
