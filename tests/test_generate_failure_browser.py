from __future__ import annotations

import csv
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "atk-visualize-failures" / "scripts" / "generate_failure_browser.py"


def run_generator(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=project,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class GenerateFailureBrowserTests(unittest.TestCase):
    def test_writes_current_version_from_target_cwd_with_review_ui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v2"
            (project / ".atk" / "results" / "v1").mkdir(parents=True)
            (current / "logs").mkdir(parents=True)
            write_csv(
                current / "failure_cases.csv",
                [
                    "case_id",
                    "input",
                    "expected_output",
                    "agent_output",
                    "failure_reason",
                    "agent_output_log_path",
                    "custom_col",
                ],
                [
                    {
                        "case_id": "C-1",
                        "input": "What is 2+2?",
                        "expected_output": "4",
                        "agent_output": "5",
                        "failure_reason": "wrong arithmetic",
                        "agent_output_log_path": "logs/row_000001.log",
                        "custom_col": "preserved evidence",
                    }
                ],
            )
            (current / "report.md").write_text(
                "# Summary\nFailure root cause summary.\n# Tuning priorities\n- Fix arithmetic.\n", encoding="utf-8"
            )

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("version=v2", result.stdout)
            self.assertIn("rows=1", result.stdout)
            output = current / "failure_cases.html"
            self.assertTrue(output.exists())
            self.assertFalse((ROOT / ".atk" / "results" / "v2" / "failure_cases.html").exists())
            text = output.read_text(encoding="utf-8")
            for phrase in [
                "Search / filter / pagination",
                "expected-vs-actual comparison",
                "schema-adaptive role switching",
                "auto-detected",
                "custom_col",
                "preserved evidence",
                "Bounded report.md context",
                "logs/row_000001.log",
                '"defaultPageSize":50',
            ]:
                self.assertIn(phrase, text)

    def test_missing_current_failure_csv_exits_2_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            old = project / ".atk" / "results" / "v1"
            current = project / ".atk" / "results" / "v2"
            write_csv(old / "failure_cases.csv", ["id", "agent_output"], [{"id": "old", "agent_output": "bad"}])
            current.mkdir(parents=True)

            result = run_generator(project)

            self.assertEqual(result.returncode, 2)
            self.assertIn("Current version v2 is missing failure_cases.csv", result.stderr)
            self.assertFalse((current / "failure_cases.html").exists())

    def test_overwrite_refusal_and_overwrite_success_preserve_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v1"
            write_csv(current / "failure_cases.csv", ["id", "agent_output"], [{"id": "1", "agent_output": "bad"}])
            output = current / "failure_cases.html"
            output.write_text("USER EDIT", encoding="utf-8")

            refused = run_generator(project)
            self.assertEqual(refused.returncode, 2)
            self.assertIn("Refusing to overwrite existing", refused.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "USER EDIT")

            overwritten = run_generator(project, "--overwrite")
            self.assertEqual(overwritten.returncode, 0, overwritten.stderr)
            self.assertIn("overwrite=overwrote existing HTML", overwritten.stdout)
            self.assertIn("failure-data", output.read_text(encoding="utf-8"))
            self.assertFalse(list(current.glob("*.tmp")))

    def test_empty_csv_with_headers_generates_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v1"
            write_csv(current / "failure_cases.csv", ["id", "agent_output"], [])

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            text = (current / "failure_cases.html").read_text(encoding="utf-8")
            self.assertIn("rows=0", result.stdout)
            self.assertIn("No failure rows in current failure_cases.csv", text)

    def test_nonstandard_fields_can_be_role_switched_and_all_fields_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v1"
            write_csv(
                current / "failure_cases.csv",
                ["sample", "question_text", "gold", "model_reply", "why_bad", "opaque_extra"],
                [
                    {
                        "sample": "S1",
                        "question_text": "Q",
                        "gold": "A",
                        "model_reply": "B",
                        "why_bad": "Mismatch",
                        "opaque_extra": "keep me",
                    }
                ],
            )

            result = run_generator(project, "--no-report")

            self.assertEqual(result.returncode, 0, result.stderr)
            text = (current / "failure_cases.html").read_text(encoding="utf-8")
            self.assertIn("schema-adaptive role switching", text)
            self.assertIn("manual/unmapped", text)
            self.assertIn("opaque_extra", text)
            self.assertIn("keep me", text)
            self.assertIn("expected-vs-actual", text)
            self.assertIn("report=skipped (Skipped by --no-report.)", result.stdout)

    def test_safe_embedding_and_unsafe_log_paths_are_not_clickable(self) -> None:
        unsafe_paths = [
            "https://example.test/log",
            "//example.test/log",
            "/tmp/log",
            "../secret",
            "%2e%2e/secret",
            "logs\\row_1.log",
            "C:\\temp\\row.log",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v1"
            rows = [
                {
                    "id": str(index),
                    "expected_output": "safe",
                    "agent_output": "</script><script>alert('x')</script> \u2028 \u2029 <b>bold</b>",
                    "agent_output_log_path": path,
                }
                for index, path in enumerate(unsafe_paths, start=1)
            ]
            write_csv(
                current / "failure_cases.csv", ["id", "expected_output", "agent_output", "agent_output_log_path"], rows
            )

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            text = (current / "failure_cases.html").read_text(encoding="utf-8")
            self.assertNotIn("</script><script>alert", text)
            self.assertNotIn("<b>bold</b>", text)
            self.assertIn("\\u003c/script\\u003e", text)
            self.assertIn("\\u2028", text)
            self.assertIn("\\u2029", text)
            self.assertNotIn('href="https://example.test/log"', text)
            self.assertNotIn('href="//example.test/log"', text)
            self.assertNotIn('href="../secret"', text)
            self.assertIn("not clickable because it is outside the safe relative path contract", text)

    def test_report_oversized_is_bounded_and_sidecars_are_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v1"
            write_csv(current / "failure_cases.csv", ["id", "agent_output"], [{"id": "1", "agent_output": "bad"}])
            report_text = "# Summary\n" + ("Failure root cause summary.\n" * 20000) + "SENTINEL_AFTER_LIMIT"
            (current / "report.md").write_text(report_text, encoding="utf-8")

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            text = (current / "failure_cases.html").read_text(encoding="utf-8")
            self.assertIn("Read first 262144 bytes only", text)
            self.assertNotIn("SENTINEL_AFTER_LIMIT", text)
            self.assertFalse((current / "report_summary.json").exists())
            self.assertFalse((current / "metadata.json").exists())
            self.assertFalse((project / ".atk" / "visualize_config.json").exists())

    def test_generated_javascript_is_syntax_valid_when_node_is_available(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available for generated JavaScript syntax smoke")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v1"
            write_csv(
                current / "failure_cases.csv",
                ["id", "input", "expected_output", "agent_output"],
                [{"id": "1", "input": "Q", "expected_output": "A", "agent_output": "B"}],
            )
            result = run_generator(project)
            self.assertEqual(result.returncode, 0, result.stderr)
            html = (current / "failure_cases.html").read_text(encoding="utf-8")
            scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
            self.assertTrue(scripts, "generated HTML should include executable frontend JavaScript")
            js_path = current / "generated.js"
            js_path.write_text("\n".join(scripts), encoding="utf-8")
            check = subprocess.run(
                [node, "--check", str(js_path)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=10,
            )
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_malformed_unreliable_csv_exits_2_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v1"
            current.mkdir(parents=True)
            (current / "failure_cases.csv").write_text('id,agent_output\n"unterminated,bad\n', encoding="utf-8")
            output = current / "failure_cases.html"
            output.write_text("existing", encoding="utf-8")

            result = run_generator(project, "--overwrite")

            self.assertEqual(result.returncode, 2)
            self.assertIn("Could not parse failure_cases.csv reliably", result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
