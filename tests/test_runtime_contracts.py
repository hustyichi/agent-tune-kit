import hashlib
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.judgments import apply_judgments, read_judgments
from tests.test_cli_workflow import read_csv, result_fixture, run_cli, successful, write_adapter, write_csv


def judgments_for(current, verdicts):
    digest = hashlib.sha256((current / "eval_results.csv").read_bytes()).hexdigest()
    path = current.parent.parent.parent / "incoming.csv"
    write_csv(
        path,
        ["atk_id", "verdict", "reason", "results_sha256"],
        [
            dict(atk_id=str(i), verdict=verdict, reason="criterion", results_sha256=digest)
            for i, verdict in enumerate(verdicts, 1)
        ],
    )
    return path


def test_rejudging_preserves_existing_failure_explanation_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    current = result_fixture(tmp_path)
    fields = ["atk_id", "input", "expected", "agent_output", "failure_reason"]
    write_csv(
        current / "failure_cases.csv",
        fields,
        [dict(atk_id="2", input="2", expected="ok", agent_output="out", failure_reason="user analysis")],
    )
    apply_judgments(judgments_for(current, ["passed", "failed", "unknown"]))
    assert read_csv(current / "failure_cases.csv")[0]["failure_reason"] == "user analysis"


def test_duplicate_stale_and_conflicting_verdicts_never_establish_pass(tmp_path):
    current = result_fixture(tmp_path)
    path = judgments_for(current, ["passed", "passed", "unknown"])
    rows = read_csv(path)
    rows.append(dict(rows[0], verdict="passed"))
    write_csv(current / "judgments.csv", list(rows[0]), rows)
    assert read_judgments(current)["1"] == "unknown"


def test_duplicate_executed_identity_cannot_prove_a_pass(tmp_path):
    current = result_fixture(tmp_path)
    rows = read_csv(current / "eval_results.csv")
    rows[1]["atk_id"] = "1"
    write_csv(current / "eval_results.csv", list(rows[0]), rows)
    path = judgments_for(current, ["passed", "passed", "passed"])
    (current / "judgments.csv").write_bytes(path.read_bytes())
    assert read_judgments(current).get("1", "unknown") == "unknown"


def test_invalid_judgment_does_not_partially_replace_existing_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    current = result_fixture(tmp_path)
    path = judgments_for(current, ["passed", "failed", "unknown"])
    apply_judgments(path)
    before = {
        name: (current / name).read_bytes() for name in ["eval_results.csv", "judgments.csv", "failure_cases.csv"]
    }
    rows = read_csv(path)
    write_csv(path, list(rows[0]), rows[:-1])
    with pytest.raises(UserActionRequired):
        apply_judgments(path)
    assert before == {name: (current / name).read_bytes() for name in before}


def test_interrupted_projection_invalidates_old_passes(tmp_path, monkeypatch):
    from agent_tune_kit.results import judgments

    monkeypatch.chdir(tmp_path)
    current = result_fixture(tmp_path)
    path = judgments_for(current, ["passed", "failed", "unknown"])
    apply_judgments(path)
    original = judgments.write_csv_atomic

    def interrupt(path, fields, rows):
        if Path(path).name == "failure_cases.csv":
            raise OSError("interrupted")
        original(path, fields, rows)

    monkeypatch.setattr(judgments, "write_csv_atomic", interrupt)
    with pytest.raises(OSError):
        apply_judgments(path)
    assert read_judgments(current) == {}


def test_legacy_full_engine_owns_version_allocation_and_reports_results(tmp_path):
    write_adapter(tmp_path)
    fixture = Path(__file__).parent / "fixtures/legacy_eval_runner.py.txt"
    code = fixture.read_text().replace(
        'raise UserActionRequired("TODO_AGENT_TUNING: implement build_agent_input(row).")', 'return row["input"]'
    )
    code = code.replace(
        'raise UserActionRequired("TODO_AGENT_TUNING: implement call_agent(agent_input).")', "return agent_input"
    )
    (tmp_path / ".atk/runner/eval_runner.py").write_text(code)
    result = successful(tmp_path, "eval", "--limit", "2")
    assert result["legacy"] and result["version"] == "v1"
    assert len(read_csv(tmp_path / ".atk/results/v1/eval_results.csv")) == 2
    assert [p.name for p in (tmp_path / ".atk/results").iterdir()] == ["v1"]


def test_configuration_failure_preserves_inspectable_partial_results(tmp_path):
    adapter = write_adapter(tmp_path)
    adapter.write_text(
        adapter.read_text().replace(
            "return {'answer': value}",
            "\n    if value == '2': raise RuntimeError('configuration failure')\n    return {'answer': value}",
        )
    )
    result = run_cli(tmp_path, "eval", "--json")
    assert result.returncode != 0 and "configuration failure" in result.stderr
    rows = read_csv(tmp_path / ".atk/results/v1/eval_results.csv")
    assert [row["atk_id"] for row in rows] == ["1"]


def test_check_awaits_async_input_mapping_without_executing_agent(tmp_path):
    adapter = write_adapter(tmp_path, async_agent=True)
    adapter.write_text(
        adapter.read_text().replace(
            "def build_agent_input(row): return row['input']",
            "async def build_agent_input(row): raise RuntimeError('invalid async input')",
        )
    )
    result = run_cli(tmp_path, "eval", "--check", "--json")
    assert result.returncode != 0 and "invalid async input" in result.stderr
    assert not (tmp_path / ".atk/results").exists()


def test_discovered_venv_keeps_project_only_dependencies(tmp_path):
    adapter = write_adapter(tmp_path)
    adapter.write_text(
        adapter.read_text().replace(
            "return {'answer': value}", "import project_only\n    return project_only.answer(value)"
        )
    )
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(tmp_path / ".venv")], check=True)
    python = tmp_path / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    purelib = subprocess.check_output(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True
    ).strip()
    (Path(purelib) / "project_only.py").write_text("def answer(value): return {'project': value}\n")
    result = successful(tmp_path, "eval")
    assert result["version"] == "v1"


def test_legacy_filter_runs_once_and_successful_system_exit_is_preserved(tmp_path):
    result_fixture(tmp_path)
    rule = tmp_path / ".atk/runner/failure_rule.py"
    rule.parent.mkdir(parents=True)
    rule.write_text(
        "from pathlib import Path\np=Path('rule_runs')\np.write_text(p.read_text()+'x' if p.exists() else 'x')\nraise SystemExit(0)\n"
    )
    result = successful(tmp_path, "results", "run-rule")
    assert result["legacy"] and (tmp_path / "rule_runs").read_text() == "x"


def test_interrupt_keeps_incremental_results(tmp_path):
    adapter = write_adapter(tmp_path)
    adapter.write_text(
        adapter.read_text().replace("import logging", "import logging, time\n    if value != '1': time.sleep(2)")
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "agent_tune_kit.cli", "eval", "--json"],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    output = tmp_path / ".atk/results/v1/eval_results.csv"
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if output.exists() and len(read_csv(output)) >= 1:
                break
            time.sleep(0.02)
        assert output.exists() and read_csv(output)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, (stdout, stderr)
        assert read_csv(output)[0]["atk_id"] == "1"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
