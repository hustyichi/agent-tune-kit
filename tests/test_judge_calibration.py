import json

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.utils.csv_io import parse_csv, write_csv_atomic


def samples(tmp_path):
    path = tmp_path / "gold.csv"
    rows = [
        dict(
            atk_id=str(i),
            input="x",
            agent_output=actual,
            expected_verdict=expected,
            expected_reason="Manually checked",
            source="policy R1",
            reviewer="owner",
            criteria="R1",
        )
        for i, actual, expected in [(1, "good", "passed"), (2, "bad", "failed"), (3, "", "unknown")]
    ]
    write_csv_atomic(path, list(rows[0]), rows)
    return path


def test_rule_calibration_never_sees_gold_or_writes_evaluation_results(tmp_path, monkeypatch):
    from agent_tune_kit.execution.calibration import calibrate

    monkeypatch.chdir(tmp_path)
    gold = samples(tmp_path)
    rule = tmp_path / "rule.py"
    rule.write_text(
        "def judge(row):\n"
        '    assert "expected_verdict" not in row and "reviewer" not in row\n'
        '    return ("passed" if row["agent_output"] else "unknown"), "checked"\n'
    )
    output = tmp_path / "calibration.csv"
    summary = calibrate(gold, output, rule_path=rule)
    assert summary["false_passes"] == 1
    assert summary["matched"] == 2
    assert summary["disagreement_ids"] == ["2"]
    assert not (tmp_path / ".atk/results").exists()
    fields, rows = parse_csv(output)
    assert "expected_reason" in fields and json.loads(rows[0]["judge_snapshot"])["source"] == rule.read_text()
    with pytest.raises(UserActionRequired):
        calibrate(gold, gold, rule_path=rule)
    with pytest.raises(UserActionRequired):
        calibrate(gold, output, rule_path=rule)


def test_semantic_calibration_requires_full_unique_coverage(tmp_path):
    from agent_tune_kit.execution.calibration import calibrate

    gold = samples(tmp_path)
    predictions = tmp_path / "predictions.csv"
    write_csv_atomic(
        predictions,
        ["atk_id", "verdict", "reason"],
        [
            {"atk_id": "1", "verdict": "passed", "reason": "checked"},
        ],
    )
    with pytest.raises(UserActionRequired):
        calibrate(gold, tmp_path / "out.csv", judgments_path=predictions, judge_id="semantic", instructions="R1")
    assert not (tmp_path / "out.csv").exists()


def test_calibration_does_not_overwrite_a_file_created_during_judging(tmp_path):
    from agent_tune_kit.execution.calibration import calibrate

    gold = samples(tmp_path)
    output = tmp_path / "result.csv"
    rule = tmp_path / "rule.py"
    rule.write_text(
        f'from pathlib import Path\ndef judge(row):\n    Path({str(output)!r}).write_text("other writer")\n    return "unknown", "missing"\n'
    )
    with pytest.raises(UserActionRequired):
        calibrate(gold, output, rule_path=rule)
    assert output.read_text() == "other writer"
