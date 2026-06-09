from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "atk-tune-ground-truth" / "scripts" / "tune_ground_truth.py"


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
            write_csv(project / ".atk" / "datasets" / "dataset.csv", ["atk_id", "input"], [{"atk_id": "1", "input": "hi"}])
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
