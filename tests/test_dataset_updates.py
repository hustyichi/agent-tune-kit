from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "atk-dataset" / "scripts" / "update_dataset.py"


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run_script(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=project,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=20,
    )


def load_module():
    spec = importlib.util.spec_from_file_location("tune_ground_truth", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TuneGroundTruthTests(unittest.TestCase):
    def test_prefers_dataset_review_file_and_writes_ground_truth_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            dataset = project / ".atk" / "datasets" / "dataset.csv"
            review = project / ".atk" / "datasets" / "dataset_review.csv"
            write_csv(
                dataset,
                ["atk_id", "input", "ground_truth", "notes"],
                [
                    {"atk_id": "1", "input": "Refund after 40 days", "ground_truth": "Reject", "notes": "keep"},
                    {"atk_id": "2", "input": "Refund after 5 days", "ground_truth": "Approve", "notes": "keep"},
                ],
            )
            write_csv(
                review,
                ["atk_id", "row_number", "review_feedback"],
                [{"atk_id": "1", "row_number": "2", "review_feedback": "Should approve if VIP customer"}],
            )
            updates = project / "updates.csv"
            write_csv(
                updates,
                ["atk_id", "ground_truth"],
                [{"atk_id": "1", "ground_truth": "Approve only when the customer is VIP; otherwise reject."}],
            )

            result = run_script(project, "--updates-path", str(updates))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("review=.atk/datasets/dataset_review.csv", result.stdout)
            self.assertIn("updated_rows=1", result.stdout)
            rows = read_csv(dataset)
            self.assertEqual(rows[0]["ground_truth"], "Approve only when the customer is VIP; otherwise reject.")
            self.assertEqual(rows[0]["notes"], "keep")
            self.assertEqual(rows[1]["ground_truth"], "Approve")

    def test_resolves_latest_valid_downloads_review_when_dataset_review_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            downloads = Path(tmp) / "Downloads"
            dataset = project / ".atk" / "datasets" / "dataset.csv"
            write_csv(dataset, ["atk_id", "input"], [{"atk_id": "1", "input": "hello"}])
            write_csv(
                downloads / "dataset_review.csv",
                ["atk_id", "row_number", "review_feedback"],
                [{"atk_id": "999", "row_number": "2", "review_feedback": "old unrelated"}],
            )
            valid = downloads / "dataset_review (1).csv"
            write_csv(
                valid,
                ["atk_id", "row_number", "review_feedback"],
                [{"atk_id": "1", "row_number": "2", "review_feedback": "Use a warmer greeting"}],
            )

            module = load_module()
            _fieldnames, rows = module.parse_dataset_csv(dataset)
            resolved = module.resolve_review_path(None, dataset.parent, Path(tmp), rows)

            self.assertEqual(resolved, valid)

    def test_appends_ground_truth_column_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            dataset = project / ".atk" / "datasets" / "dataset.csv"
            write_csv(dataset, ["atk_id", "input"], [{"atk_id": "1", "input": "hello"}])
            write_csv(
                project / ".atk" / "datasets" / "dataset_review.csv",
                ["atk_id", "row_number", "review_feedback"],
                [{"atk_id": "1", "row_number": "2", "review_feedback": "Expected answer should be bonjour"}],
            )
            updates = project / "updates.csv"
            write_csv(updates, ["atk_id", "ground_truth"], [{"atk_id": "1", "ground_truth": "bonjour"}])

            result = run_script(project, "--updates-path", str(updates))

            self.assertEqual(result.returncode, 0, result.stderr)
            with dataset.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames, ["atk_id", "input", "ground_truth"])
                rows = list(reader)
            self.assertEqual(rows[0]["ground_truth"], "bonjour")

    def test_rejects_updates_for_unknown_atk_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_csv(
                project / ".atk" / "datasets" / "dataset.csv", ["atk_id", "input"], [{"atk_id": "1", "input": "hi"}]
            )
            write_csv(
                project / ".atk" / "datasets" / "dataset_review.csv",
                ["atk_id", "row_number", "review_feedback"],
                [{"atk_id": "1", "row_number": "2", "review_feedback": "make it formal"}],
            )
            updates = project / "updates.csv"
            write_csv(updates, ["atk_id", "ground_truth"], [{"atk_id": "2", "ground_truth": "hello"}])

            result = run_script(project, "--updates-path", str(updates))

            self.assertEqual(result.returncode, 2)
            self.assertIn("unknown atk_id values: 2", result.stderr)


if __name__ == "__main__":
    unittest.main()


def test_direct_feedback_updates_existing_expected_without_adding_ground_truth(tmp_path):
    dataset = tmp_path / ".atk/datasets/dataset.csv"
    fields = ["notes", "atk_id", "input", "expected"]
    rows = [
        {"notes": "保留", "atk_id": "7", "input": "原始输入\n第二行", "expected": "旧答案"},
        {"notes": "unchanged", "atk_id": "23", "input": "other", "expected": "trusted"},
    ]
    write_csv(dataset, fields, rows)
    updates = tmp_path / "updates.csv"
    write_csv(updates, ["atk_id", "expected"], [{"atk_id": "7", "expected": "新答案"}])
    # An unrelated exported review must not affect explicitly requested direct updates.
    write_csv(dataset.parent / "dataset_review.csv", ["wrong"], [{"wrong": "unrelated"}])
    result = run_script(tmp_path, "--direct", "--field", "expected", "--updates-path", str(updates))
    assert result.returncode == 0, result.stderr
    with dataset.open() as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == fields
        result_rows = list(reader)
    assert result_rows == [{**rows[0], "expected": "新答案"}, rows[1]]
    assert not (tmp_path / ".atk/results").exists()


def test_direct_noop_leaves_existing_csv_bytes_unchanged(tmp_path):
    dataset = tmp_path / ".atk/datasets/dataset.csv"
    dataset.parent.mkdir(parents=True)
    original = b'\xef\xbb\xbf"atk_id","input","expected"\r\n"8","hello","yes"\r\n'
    dataset.write_bytes(original)
    updates = tmp_path / "updates.csv"
    write_csv(updates, ["atk_id", "expected"], [{"atk_id": "8", "expected": "yes"}])
    result = run_script(tmp_path, "--direct", "--field", "expected", "--updates-path", str(updates))
    assert result.returncode == 0, result.stderr
    assert dataset.read_bytes() == original
    assert "updated_rows=0" in result.stdout


def test_review_feedback_can_update_acceptance_criteria(tmp_path):
    dataset = tmp_path / ".atk/datasets/dataset.csv"
    write_csv(
        dataset,
        ["atk_id", "input", "acceptance_criteria"],
        [{"atk_id": "4", "input": "hello", "acceptance_criteria": "old"}],
    )
    review = dataset.parent / "dataset_review.csv"
    write_csv(
        review,
        ["atk_id", "row_number", "review_feedback"],
        [{"atk_id": "4", "row_number": "1", "review_feedback": "Use the confirmed greeting rule"}],
    )
    updates = tmp_path / "updates.csv"
    write_csv(
        updates,
        ["atk_id", "acceptance_criteria"],
        [{"atk_id": "4", "acceptance_criteria": "Reply in the user language"}],
    )
    result = run_script(
        tmp_path, "--review-path", str(review), "--field", "acceptance_criteria", "--updates-path", str(updates)
    )
    assert result.returncode == 0, result.stderr
    assert read_csv(dataset) == [{"atk_id": "4", "input": "hello", "acceptance_criteria": "Reply in the user language"}]


def test_dataset_identity_cannot_be_updated_as_an_answer(tmp_path):
    dataset = tmp_path / ".atk/datasets/dataset.csv"
    write_csv(dataset, ["atk_id", "input"], [{"atk_id": "7", "input": "hello"}])
    original = dataset.read_bytes()
    updates = tmp_path / "updates.csv"
    write_csv(updates, ["atk_id"], [{"atk_id": "7"}])
    result = run_script(tmp_path, "--direct", "--field", "atk_id", "--updates-path", str(updates))
    assert result.returncode == 2
    assert "cannot update atk_id" in result.stderr
    assert dataset.read_bytes() == original


def test_dataset_update_browse_evaluate_diagnose_and_failure_rerun(tmp_path):
    from tests.test_generate_failure_browser import extract_failure_payload
    from tests.test_runner_template_row_logging import render_runner

    dataset = tmp_path / ".atk/datasets/dataset.csv"
    fields = ["atk_id", "token", "expected", "notes"]
    write_csv(
        dataset,
        fields,
        [
            {"atk_id": "7", "token": "a", "expected": "old", "notes": "keep"},
            {"atk_id": "23", "token": "b", "expected": "different", "notes": "keep"},
            {"atk_id": "41", "token": "c", "expected": "", "notes": "no answer yet"},
        ],
    )
    updates = tmp_path / "updates.csv"
    write_csv(updates, ["atk_id", "expected"], [{"atk_id": "7", "expected": "ok a"}])
    result = run_script(tmp_path, "--direct", "--field", "expected", "--updates-path", str(updates))
    assert result.returncode == 0, result.stderr
    preserved = dataset.read_bytes()

    def execute(script, *args):
        result = subprocess.run([sys.executable, str(script), *args], cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    execute(ROOT / "skills/atk-visualize-dataset/scripts/generate_dataset_browser.py", "--no-open")
    assert (dataset.parent / "dataset.html").is_file()
    runner = tmp_path / ".atk/runner/eval_runner.py"
    runner.parent.mkdir(parents=True)
    # The test Agent uses only the business token, never expected answers.
    code = render_runner().replace("    return row\n", '    return {"token": row["token"]}\n')
    runner.write_text(code)
    execute(runner, "--no-progress")
    current = tmp_path / ".atk/results/v1"
    rows = read_csv(current / "eval_results.csv")
    assert [row["atk_id"] for row in rows] == ["7", "23", "41"]
    assert [row["expected"] for row in rows] == ["ok a", "different", ""]
    assert [row["agent_output"] for row in rows] == ["ok a", "ok b", "ok c"]

    rule = runner.parent / "failure_rule.py"
    code = (ROOT / "templates/.atk/runner/failure_rule.py.md").read_text().split("```python\n", 1)[1].split("```", 1)[0]
    code = code.replace(
        '    raise UserActionRequired("TODO_AGENT_TUNING: implement confirmed failure rule before running.")',
        '    if not row["expected"]:\n        return "unknown", "missing expected answer"\n'
        '    return ("passed" if row["expected"] == row["agent_output"] else "failed"), "exact match"',
    )
    rule.write_text(code)
    execute(rule)
    assert [row["verdict"] for row in read_csv(current / "judgments.csv")] == ["passed", "failed", "unknown"]
    assert [row["atk_id"] for row in read_csv(current / "failure_cases.csv")] == ["23"]
    execute(ROOT / "skills/atk-visualize-failures/scripts/generate_failure_browser.py", "--no-open")
    payload = extract_failure_payload((current / "failure_cases.html").read_text())
    assert [row["values"]["is_abnormal"] for row in payload["rows"]] == ["false", "true", "unknown"]
    first_results = (current / "eval_results.csv").read_bytes()
    execute(runner, "--no-progress", "--only-failures")
    assert [row["atk_id"] for row in read_csv(tmp_path / ".atk/results/v2/eval_results.csv")] == ["23"]
    assert dataset.read_bytes() == preserved
    assert (current / "eval_results.csv").read_bytes() == first_results


def test_direct_updates_reject_duplicate_or_unknown_ids_without_partial_write(tmp_path):
    dataset = tmp_path / ".atk/datasets/dataset.csv"
    write_csv(dataset, ["atk_id", "input", "expected"], [{"atk_id": "7", "input": "hello", "expected": "original"}])
    original = dataset.read_bytes()
    updates = tmp_path / "updates.csv"
    for bad_id in ["7", "999"]:
        write_csv(
            updates,
            ["atk_id", "expected"],
            [{"atk_id": "7", "expected": "valid correction"}, {"atk_id": bad_id, "expected": "invalid correction"}],
        )
        result = run_script(tmp_path, "--direct", "--field", "expected", "--updates-path", str(updates))
        assert result.returncode == 2
        assert dataset.read_bytes() == original
