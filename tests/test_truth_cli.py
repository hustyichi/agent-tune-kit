import json
import os
import subprocess
import sys
from pathlib import Path

from tests.test_truth_contract import dataset, review

ROOT = Path(__file__).parents[1]


def cli(tmp_path, *args):
    result = subprocess.run(
        [sys.executable, "-m", "agent_tune_kit.cli", *args],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_confirmation_and_calibration_through_public_cli(tmp_path):
    from tests.test_judge_calibration import samples

    path = dataset(tmp_path)
    feedback = review(path, tmp_path)
    applied = cli(
        tmp_path,
        "dataset",
        "update",
        "--dataset-path",
        str(path),
        "--review-path",
        str(feedback),
        "--field",
        "expected",
        "--json",
    )
    assert applied["reviewed_rows"] == 1
    inspected = cli(
        tmp_path, "dataset", "inspect", "--direct", "--dataset-path", str(path), "--field", "expected", "--json"
    )
    assert inspected["truth_counts"]["confirmed"] == 1
    gold = samples(tmp_path)
    rule = tmp_path / "rule.py"
    rule.write_text('def judge(row):\n    return "unknown", "Need more evidence"\n')
    calibrated = cli(
        tmp_path,
        "results",
        "calibrate",
        "--samples-path",
        str(gold),
        "--output-path",
        str(tmp_path / "calibration.csv"),
        "--rule-path",
        str(rule),
        "--python",
        sys.executable,
        "--json",
    )
    assert calibrated["samples"] == 3
    assert calibrated["unknown"] == 3
    assert not (tmp_path / ".atk/results").exists()
