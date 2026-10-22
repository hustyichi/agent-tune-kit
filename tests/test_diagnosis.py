from __future__ import annotations

import hashlib
import subprocess
import sys

import pytest

from tests.test_generate_failure_browser import ROOT, load_generator_module, write_csv


def history_fixture(tmp_path, verdicts=None):
    results = tmp_path / "results"
    old, current = results / "v1", results / "v2"
    fields = ["atk_id", "agent_output"]
    rows = [{"atk_id": str(i), "agent_output": "answer"} for i in range(1, 5)]
    write_csv(old / "eval_results.csv", fields, rows)
    write_csv(old / "failure_cases.csv", fields, rows)
    (old / "tuning_plan.md").write_text("## 目标异常清单\n- atk_id: 1, atk_id: 2\n")
    write_csv(current / "eval_results.csv", fields, rows[:3])
    write_csv(current / "failure_cases.csv", fields, [rows[2]])
    if verdicts is not None:
        digest = hashlib.sha256((current / "eval_results.csv").read_bytes()).hexdigest()
        write_csv(
            current / "judgments.csv",
            ["atk_id", "verdict", "reason", "results_sha256"],
            [
                {"atk_id": aid, "verdict": verdict, "reason": "checked", "results_sha256": digest}
                for aid, verdict in verdicts
            ],
        )
    module = load_generator_module()
    return module, results, current, fields, rows


def get_history(fixture):
    module, results, current, fields, rows = fixture
    return module.build_history(results, current, fields, [rows[2]], module.detect_roles(fields))


def test_missing_judgments_never_imply_pass_or_resolution(tmp_path):
    history = get_history(history_fixture(tmp_path))
    states = {row["atkId"]: row["statuses"]["v2"] for row in history["perCase"]}
    assert states == {"1": "unknown", "2": "unknown", "3": "failed", "4": "not_tested"}
    assert history["previousTargets"]["targets"][0]["resolution"] == "indeterminate"
    assert history["buckets"]["newRegressions"] == []


@pytest.mark.parametrize(
    "verdicts",
    [
        [("1", "passed"), ("2", "unknown")],
        [("1", "passed")],
        [("1", "passed"), ("2", "invalid")],
        [("1", "passed"), ("2", "passed"), ("2", "unknown")],
    ],
)
def test_partial_or_invalid_judgments_do_not_resolve_targets(tmp_path, verdicts):
    history = get_history(history_fixture(tmp_path, verdicts))
    states = {row["atkId"]: row["statuses"]["v2"] for row in history["perCase"]}
    assert states["2"] == "unknown"
    assert history["previousTargets"]["targets"][0]["resolution"] == "indeterminate"


def test_only_explicit_current_passes_resolve_targets(tmp_path):
    fixture = history_fixture(tmp_path, [("1", "passed"), ("2", "passed"), ("3", "failed")])
    history = get_history(fixture)
    assert history["previousTargets"]["targets"][0]["resolution"] == "resolved"
    assert history["versions"][-1]["passedCount"] == 2
    assert history["versions"][-1]["unknownCount"] == 0
    current = fixture[2]
    with (current / "eval_results.csv").open("a") as handle:
        handle.write("5,new output\n")
    history = get_history(fixture)
    assert history["previousTargets"]["targets"][0]["resolution"] == "indeterminate"
    assert history["versions"][-1]["passedCount"] == 0


def test_rule_template_preserves_unknown_and_failure_only_rerun_input(tmp_path):
    template = (ROOT / "templates/.atk/runner/failure_rule.py.md").read_text()
    code = template.split("```python\n", 1)[1].split("```", 1)[0]
    # Replace only project-specific judgment; exercise the template's real CSV path.
    start, end = code.index("def judge("), code.index("\ndef write_", code.index("def judge("))
    code = code[:start] + 'def judge(row):\n    return row["expected_verdict"], "test criterion"\n\n' + code[end:]
    script = tmp_path / "rule.py"
    script.write_text(code)
    current = tmp_path / ".atk/results/v1"
    fields = ["atk_id", "agent_output", "expected_verdict"]
    write_csv(
        current / "eval_results.csv",
        fields,
        [
            {"atk_id": str(i), "agent_output": "original", "expected_verdict": verdict}
            for i, verdict in enumerate(["passed", "failed", "unknown"], 1)
        ],
    )
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    import csv

    with (current / "failure_cases.csv").open() as handle:
        failures = list(csv.DictReader(handle))
    assert [row["atk_id"] for row in failures] == ["2"]
    assert failures[0]["agent_output"] == "original"
    with (current / "judgments.csv").open() as handle:
        judgments = list(csv.DictReader(handle))
    assert [row["verdict"] for row in judgments] == ["passed", "failed", "unknown"]
    assert all(
        row["results_sha256"] == hashlib.sha256((current / "eval_results.csv").read_bytes()).hexdigest()
        for row in judgments
    )


def test_browser_marks_unjudged_rows_unknown_even_without_history(tmp_path):
    from tests.test_generate_failure_browser import extract_failure_payload, run_generator

    fixture = history_fixture(tmp_path / ".atk", [("1", "passed"), ("2", "unknown"), ("3", "failed")])
    result = run_generator(tmp_path, "--no-history")
    assert result.returncode == 0, result.stderr
    payload = extract_failure_payload((fixture[2] / "failure_cases.html").read_text())
    states = {r["values"]["atk_id"]: r["values"]["is_abnormal"] for r in payload["rows"]}
    assert states == {"1": "false", "2": "unknown", "3": "true"}


def test_browser_normal_filter_excludes_unknown(tmp_path):
    import json
    import shutil

    runtime = shutil.which("bun") or shutil.which("node")
    if not runtime:
        pytest.skip("JavaScript runtime unavailable")
    js = (ROOT / "skills/atk-visualize-failures/assets/app.js").read_text()
    payload = {
        "fieldnames": ["is_abnormal"],
        "rows": [{"values": {"is_abnormal": value}} for value in ["false", "unknown", "true"]],
    }
    harness = (
        "global.window={localStorage:{getItem(){return null},setItem(){}}};\n"
        f"const payload={json.dumps(payload)};\n"
        "global.document={getElementById(id){return id==='failure-data'?{textContent:JSON.stringify(payload)}:null}};\n"
        + js.replace(
            "  init();\n})();", '  state.statusFilter="normal"; console.log(JSON.stringify(filteredRows()));\n})();'
        )
    )
    result = subprocess.run([runtime, "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert [r["values"]["is_abnormal"] for r in json.loads(result.stdout)] == ["false"]


def test_browser_unknown_count_and_detail_never_claim_pass():
    import json
    import shutil

    runtime = shutil.which("bun") or shutil.which("node")
    if not runtime:
        pytest.skip("JavaScript runtime unavailable")
    js = (ROOT / "skills/atk-visualize-failures/assets/app.js").read_text()
    payload = {
        "fieldnames": ["is_abnormal"],
        "rows": [{"values": {"is_abnormal": value}} for value in ["false", "unknown", "true"]],
    }
    harness = (
        "global.window={localStorage:{getItem(){return null},setItem(){}}};\n"
        f"const payload={json.dumps(payload)};\n"
        "global.document={getElementById(id){return id==='failure-data'?{textContent:JSON.stringify(payload)}:null},"
        "createElement(){return {children:[],appendChild(c){this.children.push(c)},setAttribute(){},addEventListener(){}}},"
        "createTextNode(s){return {textContent:s}}};\n"
        + js.replace(
            "  init();\n})();",
            '  console.log(JSON.stringify({counts:getStatusCounts(), banner:buildStatusBanner(payload.rows[1],false,"")}));\n})();',
        )
    )
    result = subprocess.run([runtime, "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert "PASS" not in json.dumps(output["banner"])
    assert output["counts"] == {"all": 3, "normal": 1, "abnormal": 1, "unknown": 1}


def test_truncated_judgments_cannot_prove_pass(tmp_path):
    from unittest import mock

    fixture = history_fixture(tmp_path, [("1", "passed"), ("2", "passed")])
    with mock.patch.object(fixture[0], "HISTORY_FAILURE_MAX_BYTES", 20):
        assert fixture[0].read_judgments(fixture[2]) == {}
