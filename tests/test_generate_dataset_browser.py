from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "atk-visualize-dataset" / "scripts" / "generate_dataset_browser.py"


def run_generator(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    dataset = project / ".atk" / "datasets" / "dataset.csv"
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--no-open", "--dataset-path", str(dataset), *args],
        cwd=project,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=20,
    )


def load_generator_module():
    spec = importlib.util.spec_from_file_location("generate_dataset_browser", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class GenerateDatasetBrowserTests(unittest.TestCase):
    def test_writes_dataset_html_with_review_ui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_csv(
                project / ".atk" / "datasets" / "dataset.csv",
                ["atk_id", "scenario", "input", "expected", "custom_col"],
                [
                    {
                        "atk_id": "1",
                        "scenario": "main",
                        "input": "What is 2+2?",
                        "expected": "4",
                        "custom_col": "preserved value",
                    },
                    {
                        "atk_id": "2",
                        "scenario": "main",
                        "input": "Capital of France?",
                        "expected": "Paris",
                        "custom_col": "kept",
                    },
                ],
            )

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("rows=2", result.stdout)
            output = project / ".atk" / "datasets" / "dataset.html"
            self.assertTrue(output.exists())
            text = output.read_text(encoding="utf-8")
            for phrase in [
                "Search / filter / pagination",
                "input-vs-ground_truth comparison",
                "ground_truth confirmation",
                "dataset quality lint",
                "client-side review export",
                "schema-adaptive role switching",
                "bundled offline ECharts",
                "custom_col",
                "preserved value",
                '"defaultPageSize":50',
                "dataset-data",
            ]:
                self.assertIn(phrase, text)

    def test_visual_migration_contract_matches_dataset_visualize_shell(self) -> None:
        """Lock the first-pass static shell markers mapped from dataset-visualize."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_csv(
                project / ".atk" / "datasets" / "dataset.csv",
                ["atk_id", "scenario", "input", "ground_truth", "custom_col"],
                [
                    {
                        "atk_id": "1",
                        "scenario": "main",
                        "input": "What is 2+2?",
                        "ground_truth": "4",
                        "custom_col": "preserved value",
                    },
                ],
            )

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            text = (project / ".atk" / "datasets" / "dataset.html").read_text(encoding="utf-8")
            for phrase in [
                "Dataset Visualizer",
                "application-root",
                "export-reviewed-dataset-btn",
                "导出评测结果",
                "数据列表",
                "字段特征分析",
                "dataset-table-controller",
                "statistics-dashboard",
                "stat-card-rows",
                "stat-card-fields",
                "stat-card-gt",
                "inspector-overlay-pane",
            ]:
                self.assertIn(phrase, text)

    def test_static_visualization_has_no_user_frontend_dependency_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_csv(
                project / ".atk" / "datasets" / "dataset.csv",
                ["atk_id", "input", "expected"],
                [{"atk_id": "1", "input": "hi", "expected": "hello"}],
            )

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            text = (project / ".atk" / "datasets" / "dataset.html").read_text(encoding="utf-8")
            forbidden_runtime_dependencies = [
                "fonts.googleapis.com",
                "cdn.jsdelivr.net",
                "unpkg.com",
                "esm.sh",
                "react-dom",
                "tailwindcss",
                "papaparse",
                "vite/client",
            ]
            for phrase in forbidden_runtime_dependencies:
                self.assertNotIn(phrase, text.lower())

    def test_migrated_frontend_preserves_atk_review_utility_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_csv(
                project / ".atk" / "datasets" / "dataset.csv",
                ["atk_id", "scenario", "input", "ground_truth"],
                [
                    {"atk_id": "1", "scenario": "main", "input": "hello", "ground_truth": "hi"},
                    {"atk_id": "2", "scenario": "edge", "input": "", "ground_truth": ""},
                ],
            )

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            datasets_dir = project / ".atk" / "datasets"
            self.assertEqual({path.name for path in datasets_dir.iterdir()}, {"dataset.csv", "dataset.html"})
            text = (datasets_dir / "dataset.html").read_text(encoding="utf-8")
            for phrase in [
                "GROUND TRUTH",
                "dataset quality lint",
                "quality-bar",
                "exportReview",
                "dataset_review.csv",
                "detected_issues",
                "review-note",
                "verdict-btn",
                "localStorage",
                "字段角色映射",
                "No data rows in dataset.csv",
                '"id:" + row.atkId + ":row:" + row.rowNumber',
            ]:
                self.assertIn(phrase, text)
            self.assertNotIn("dataset_summary.json", text)
            self.assertNotIn("visualize_config", text)

    def test_missing_dataset_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".atk" / "datasets").mkdir(parents=True)

            result = run_generator(project)

            self.assertEqual(result.returncode, 2)
            self.assertIn("run atk-build-dataset or atk-init first", result.stderr)
            self.assertFalse((project / ".atk" / "datasets" / "dataset.html").exists())

    def test_overwrite_refusal_then_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_csv(
                project / ".atk" / "datasets" / "dataset.csv",
                ["atk_id", "input", "expected"],
                [{"atk_id": "1", "input": "hi", "expected": "hello"}],
            )
            output = project / ".atk" / "datasets" / "dataset.html"
            output.write_text("USER EDIT", encoding="utf-8")

            refused = run_generator(project)
            self.assertEqual(refused.returncode, 2)
            self.assertIn("Refusing to overwrite existing", refused.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "USER EDIT")

            overwritten = run_generator(project, "--overwrite")
            self.assertEqual(overwritten.returncode, 0, overwritten.stderr)
            self.assertIn("overwrite=overwrote existing HTML", overwritten.stdout)
            self.assertIn("dataset-data", output.read_text(encoding="utf-8"))
            self.assertFalse(list((project / ".atk" / "datasets").glob("*.tmp")))

    def test_empty_dataset_with_headers_generates_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_csv(project / ".atk" / "datasets" / "dataset.csv", ["atk_id", "input", "expected"], [])

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("rows=0", result.stdout)
            text = (project / ".atk" / "datasets" / "dataset.html").read_text(encoding="utf-8")
            self.assertIn("No data rows in dataset.csv", text)

    def test_blank_or_duplicate_headers_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            dataset = project / ".atk" / "datasets" / "dataset.csv"
            dataset.parent.mkdir(parents=True)
            dataset.write_text("atk_id,input,input\n1,a,b\n", encoding="utf-8")

            result = run_generator(project)

            self.assertEqual(result.returncode, 2)
            self.assertIn("duplicate headers", result.stderr)


class DatasetQualityLintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = load_generator_module()

    def _issues(self, fieldnames: list[str], rows: list[dict[str, str]]) -> list[list[str]]:
        roles = self.mod.detect_roles(fieldnames)
        return self.mod.compute_issues(rows, fieldnames, roles)

    def test_empty_input_and_ground_truth(self) -> None:
        rows = [
            {"atk_id": "1", "input": "", "expected": ""},
            {"atk_id": "2", "input": "ok", "expected": "fine"},
        ]
        issues = self._issues(["atk_id", "input", "expected"], rows)
        self.assertIn("empty_input", issues[0])
        self.assertIn("empty_gt", issues[0])
        self.assertEqual(issues[1], [])

    def test_duplicate_and_missing_atk_id(self) -> None:
        rows = [
            {"atk_id": "1", "input": "a", "expected": "x"},
            {"atk_id": "1", "input": "b", "expected": "y"},
            {"atk_id": "", "input": "c", "expected": "z"},
            {"atk_id": "0", "input": "d", "expected": "w"},
        ]
        issues = self._issues(["atk_id", "input", "expected"], rows)
        self.assertIn("dup_id", issues[0])
        self.assertIn("dup_id", issues[1])
        self.assertIn("dup_id", issues[2])  # empty id
        self.assertIn("dup_id", issues[3])  # zero is not positive

    def test_conflict_same_input_different_ground_truth(self) -> None:
        rows = [
            {"atk_id": "1", "input": "Refund policy?", "expected": "30 days"},
            {"atk_id": "2", "input": "refund policy?", "expected": "No refunds"},
        ]
        issues = self._issues(["atk_id", "input", "expected"], rows)
        self.assertIn("conflict", issues[0])
        self.assertIn("conflict", issues[1])

    def test_exact_duplicate_samples(self) -> None:
        rows = [
            {"atk_id": "1", "input": "same q", "expected": "same a"},
            {"atk_id": "2", "input": "same q", "expected": "same a"},
        ]
        issues = self._issues(["atk_id", "input", "expected"], rows)
        self.assertIn("duplicate", issues[0])
        self.assertIn("duplicate", issues[1])
        # identical pair is a duplicate, not a conflict
        self.assertNotIn("conflict", issues[0])

    def test_ground_truth_length_outliers(self) -> None:
        long_value = "x" * 400
        rows = [{"atk_id": str(i + 1), "input": f"q{i}", "expected": "a normal length answer here"} for i in range(6)]
        rows.append({"atk_id": "7", "input": "short one", "expected": "y"})
        rows.append({"atk_id": "8", "input": "long one", "expected": long_value})
        issues = self._issues(["atk_id", "input", "expected"], rows)
        self.assertIn("gt_too_short", issues[6])
        self.assertIn("gt_too_long", issues[7])
        self.assertNotIn("gt_too_short", issues[0])
        self.assertNotIn("gt_too_long", issues[0])

    def test_no_input_role_skips_input_checks(self) -> None:
        rows = [{"atk_id": "1", "reference": "value"}]
        issues = self._issues(["atk_id", "reference"], rows)
        # 'reference' maps to expected role; no input role -> no empty_input/conflict/duplicate
        self.assertNotIn("empty_input", issues[0])
        self.assertNotIn("conflict", issues[0])

    def test_ground_truth_role_wins_over_expected_column(self) -> None:
        roles = self.mod.detect_roles(["atk_id", "input", "expected", "ground_truth"])
        self.assertEqual(roles["expected"]["field"], "ground_truth")

    def test_frontend_skips_null_dom_attributes(self) -> None:
        app_js = (ROOT / "skills" / "atk-visualize-dataset" / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertIn("if (props[k] == null) continue;", app_js)

    def test_frontend_review_keys_include_row_number_for_duplicate_atk_ids(self) -> None:
        app_js = (ROOT / "skills" / "atk-visualize-dataset" / "assets" / "app.js").read_text(encoding="utf-8")
        self.assertIn('"id:" + row.atkId + ":row:" + row.rowNumber', app_js)


if __name__ == "__main__":
    unittest.main()
