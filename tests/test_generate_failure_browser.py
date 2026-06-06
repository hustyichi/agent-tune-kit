from __future__ import annotations

import csv
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "atk-visualize-failures" / "scripts" / "generate_failure_browser.py"


def run_generator(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--no-open", *args],
        cwd=project,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )


def load_generator_module():
    spec = importlib.util.spec_from_file_location("generate_failure_browser", SCRIPT)
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


def extract_failure_payload(html: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="failure-data">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert match, "generated HTML should embed the failure payload"
    return json.loads(match.group(1))


class GenerateFailureBrowserTests(unittest.TestCase):
    def test_writes_current_version_from_target_cwd_with_review_ui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v2"
            (project / ".atk" / "results" / "v1").mkdir(parents=True)
            (current / "logs").mkdir(parents=True)
            (current / "logs" / "row_000001.log").write_text("[INIT] case verification started", encoding="utf-8")
            write_csv(
                current / "failure_cases.csv",
                [
                    "case_id",
                    "input",
                    "expected_output",
                    "agent_output",
                    "failure_reason",
                    "log_path",
                    "custom_col",
                ],
                [
                    {
                        "case_id": "C-1",
                        "input": "What is 2+2?",
                        "expected_output": "4",
                        "agent_output": "5",
                        "failure_reason": "wrong arithmetic",
                        "log_path": "logs/row_000001.log",
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
                "Agent 评测结果可视化",
                "application-root",
                "数据列表",
                "字段特征分析",
                "dataset-table-controller",
                "statistics-dashboard",
                "stat-card-abnormal",
                "inspector-overlay-pane",
                "custom_col",
                "preserved evidence",
                "logs/row_000001.log",
                '"defaultPageSize":50',
                "logContent",
                "case verification started",
            ]:
                self.assertIn(phrase, text)

    def test_visual_migration_contract_matches_dataset_visualize_shell(self) -> None:
        """Lock the first-pass static shell markers mapped from dataset-visualize."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v1"
            write_csv(
                current / "failure_cases.csv",
                ["case_id", "input", "ground_truth", "agent_output", "failure_reason", "is_abnormal", "custom_col"],
                [
                    {
                        "case_id": "C-1",
                        "input": "What is 2+2?",
                        "ground_truth": "4",
                        "agent_output": "5",
                        "failure_reason": "wrong arithmetic",
                        "is_abnormal": "true",
                        "custom_col": "preserved value",
                    },
                ],
            )

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            text = (current / "failure_cases.html").read_text(encoding="utf-8")
            for phrase in [
                "Agent 评测结果可视化",
                "application-root",
                "数据列表",
                "字段特征分析",
                "dataset-table-controller",
                "statistics-dashboard",
                "stat-card-rows",
                "stat-card-fields",
                "stat-card-abnormal",
                "inspector-overlay-pane",
            ]:
                self.assertIn(phrase, text)
            self.assertRegex(text, r"id=[\"']stat-card-abnormal[\"']")
            self.assertIn("合并异常用例", text)
            self.assertIn("失败率", text)

    def test_static_visualization_has_no_user_frontend_dependency_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v1"
            write_csv(
                current / "failure_cases.csv",
                ["case_id", "input", "expected_output", "agent_output"],
                [{"case_id": "C-1", "input": "hi", "expected_output": "hello", "agent_output": "bad"}],
            )

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            text = (current / "failure_cases.html").read_text(encoding="utf-8").lower()
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
                self.assertNotIn(phrase, text)

    def test_migrated_frontend_preserves_json_code_and_review_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v1"
            write_csv(
                current / "failure_cases.csv",
                ["case_id", "input", "expected_output", "agent_output", "failure_reason", "custom_json"],
                [
                    {
                        "case_id": "C-1",
                        "input": '{"messages":[{"role":"user","content":"Write code"}]}',
                        "expected_output": "```python\nprint(4)\n```",
                        "agent_output": "```python\nprint(5)\n```",
                        "failure_reason": "wrong arithmetic",
                        "custom_json": '{"answer":"4","code":"def solve():\\n    return 4"}',
                    },
                ],
            )

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            text = (current / "failure_cases.html").read_text(encoding="utf-8")
            for phrase in [
                "formatJsonToHtml",
                "json-pre",
                "code-pre",
                "sub-code-pane",
                "exportReview",
                "failure_cases_review.csv",
                "localStorage",
                "custom_json",
                "def solve",
            ]:
                self.assertIn(phrase, text)

    def test_old_failure_dashboard_modules_are_not_primary_acceptance_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v1"
            write_csv(current / "failure_cases.csv", ["id", "agent_output"], [{"id": "1", "agent_output": "bad"}])
            (current / "report.md").write_text("# Summary\nFailure root cause summary.\n", encoding="utf-8")

            result = run_generator(project)

            self.assertEqual(result.returncode, 0, result.stderr)
            text = (current / "failure_cases.html").read_text(encoding="utf-8")
            for primary_marker in [
                'data-tab="cross"',
                'id="tab-cross"',
                'id="chart-trend"',
                'id="persistent-body"',
                'id="targets-body"',
                'data-open-drawer="report"',
            ]:
                self.assertNotIn(primary_marker, text)

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

    def test_generator_opens_browser_by_default_and_can_opt_out(self) -> None:
        module = load_generator_module()
        self.assertTrue(module.parse_args([]).open_browser)
        self.assertFalse(module.parse_args(["--no-open"]).open_browser)
        self.assertTrue(module.parse_args(["--no-open", "--open"]).open_browser)

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            current = project / ".atk" / "results" / "v1"
            write_csv(current / "failure_cases.csv", ["id", "agent_output"], [{"id": "1", "agent_output": "bad"}])

            with (
                chdir(project),
                mock.patch.object(module, "open_in_browser", return_value=(True, "file:///fake.html")) as opened,
            ):
                result = module.run([])

            self.assertEqual(result, 0)
            opened.assert_called_once_with(Path(".atk/results/v1/failure_cases.html"))

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
            payload = extract_failure_payload(text)
            first_row_values = payload["rows"][0]["values"]
            self.assertIn("schema-adaptive role switching", text)
            self.assertIn("manual/unmapped", text)
            self.assertIn("opaque_extra", text)
            self.assertIn("keep me", text)
            self.assertEqual(first_row_values["opaque_extra"], "keep me")
            for phrase in [
                "dataset-table-controller",
                "inspector-overlay-pane",
                "globalSearch",
                "question_text",
                "model_reply",
                "why_bad",
            ]:
                self.assertIn(phrase, text)
            self.assertIn("report=skipped (Skipped by --no-report.)", result.stdout)

    def test_table_does_not_append_issue_column_to_failure_rows(self) -> None:
        app_js = (ROOT / "skills" / "atk-visualize-failures" / "assets" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('headerRow.appendChild(el("th", { text: "问题" }));', app_js)
        self.assertNotIn('tr.appendChild(el("td", {}, buildRowBadges(row)));', app_js)

    def test_safe_embedding_and_unsafe_log_paths_are_not_clickable(self) -> None:
        unsafe_paths = [
            "https://example.test/log",
            "//example.test/log",
            "/tmp/log",
            "../secret",
            "%2e%2e/secret",
            "%252e%252e/secret",
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
                    "log_path": path,
                }
                for index, path in enumerate(unsafe_paths, start=1)
            ]
            write_csv(current / "failure_cases.csv", ["id", "expected_output", "agent_output", "log_path"], rows)

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
            self.assertNotIn('href="%2e%2e/secret"', text)
            self.assertNotIn('href="%252e%252e/secret"', text)
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
            self.assertIn("Failure root cause summary.", text)
            self.assertNotIn("SENTINEL_AFTER_LIMIT", text)
            self.assertFalse((current / "report_summary.json").exists())
            self.assertFalse((current / "metadata.json").exists())
            self.assertFalse((project / ".atk" / "visualize_config.json").exists())

    def test_historical_eval_results_read_is_byte_bounded(self) -> None:
        module = load_generator_module()
        with tempfile.TemporaryDirectory() as tmp:
            eval_path = Path(tmp) / "eval_results.csv"
            eval_path.write_text(
                "atk_id,agent_output\nCASE-000,ok\nCASE-PARTIAL-SHOULD-NOT-APPEAR,ok\n", encoding="utf-8"
            )
            with mock.patch.object(module, "HISTORY_EVAL_MAX_BYTES", 45):
                result = module._read_version_tested_ids(eval_path)

        self.assertTrue(result["truncated"])
        self.assertTrue(result["available"])
        self.assertEqual(result["testedCount"], 1)
        self.assertIn("CASE-000", result["atkIds"])
        self.assertNotIn("CASE-PARTIAL-SHOULD-NOT-APPEAR", result["atkIds"])
        self.assertNotIn("CASE-PARTIAL", result["atkIds"])

    def test_historical_failure_rows_read_is_byte_bounded(self) -> None:
        module = load_generator_module()
        with tempfile.TemporaryDirectory() as tmp:
            failure_path = Path(tmp) / "failure_cases.csv"
            failure_path.write_text(
                "atk_id,failure_reason\nCASE-000,complete reason\nCASE-PARTIAL-SHOULD-NOT-APPEAR,partial reason\n",
                encoding="utf-8",
            )
            with mock.patch.object(module, "HISTORY_FAILURE_MAX_BYTES", 58):
                result = module._read_version_failures(failure_path)

        self.assertTrue(result["truncated"])
        self.assertTrue(result["readable"])
        self.assertEqual(result["failedCount"], 1)
        self.assertIn("CASE-000", result["atkIds"])
        self.assertNotIn("CASE-PARTIAL-SHOULD-NOT-APPEAR", result["atkIds"])
        self.assertNotIn("CASE-PARTIAL", result["atkIds"])

    def test_code_highlighter_does_not_rewrite_generated_span_markup(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available for generated JavaScript syntax smoke")
        app_js = (ROOT / "skills" / "atk-visualize-failures" / "assets" / "app.js").read_text(encoding="utf-8")
        payload = json.dumps(
            {
                "roles": {},
                "fieldnames": ["custom_json"],
                "rows": [{"rowNumber": 1, "values": {"custom_json": ""}, "safeLogHrefs": {}}],
                "config": {"defaultPageSize": 50, "pageSizes": [50], "snippetMaxChars": 240},
            }
        )
        harness = (
            "global.window={localStorage:{getItem(){return null},setItem(){}}};\n"
            f"const payload={payload};\n"
            "global.document={getElementById(id){return id==='failure-data'?{textContent:JSON.stringify(payload)}:null}};\n"
            + app_js.replace(
                "  init();\n})();",
                "  console.log(highlightCode('def solve():\\n    return 4'));\n})();",
            )
        )
        check = subprocess.run(
            [node, "-e", harness],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn('<span class="tok-kw">return</span>', check.stdout)
        self.assertIn('<span class="tok-num">4</span>', check.stdout)
        self.assertNotIn("<span <span", check.stdout)
        self.assertNotIn("class</span>=", check.stdout)

    def test_code_highlighter_preserves_quoted_text_inside_comments(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available for generated JavaScript syntax smoke")
        app_js = (ROOT / "skills" / "atk-visualize-failures" / "assets" / "app.js").read_text(encoding="utf-8")
        payload = json.dumps(
            {
                "roles": {},
                "fieldnames": ["custom_code"],
                "rows": [{"rowNumber": 1, "values": {"custom_code": ""}, "safeLogHrefs": {}}],
                "config": {"defaultPageSize": 50, "pageSizes": [50], "snippetMaxChars": 240},
            }
        )
        harness = (
            "global.window={localStorage:{getItem(){return null},setItem(){}}};\n"
            f"const payload={payload};\n"
            "global.document={getElementById(id){return id==='failure-data'?{textContent:JSON.stringify(payload)}:null}};\n"
            + app_js.replace(
                "  init();\n})();",
                '  console.log(highlightCode(\'// "quoted"\\n/* "also quoted" */\'));\n})();',
            )
        )
        check = subprocess.run(
            [node, "-e", harness],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn('<span class="tok-com">// &quot;quoted&quot;</span>', check.stdout)
        self.assertIn('<span class="tok-com">/* &quot;also quoted&quot; */</span>', check.stdout)
        self.assertNotIn("TOK", check.stdout)

    def test_review_export_neutralizes_spreadsheet_formula_cells(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available for generated JavaScript syntax smoke")
        app_js = (ROOT / "skills" / "atk-visualize-failures" / "assets" / "app.js").read_text(encoding="utf-8")
        payload = json.dumps(
            {
                "roles": {},
                "fieldnames": ["agent_output"],
                "rows": [{"rowNumber": 1, "values": {"agent_output": ""}, "safeLogHrefs": {}}],
                "config": {"defaultPageSize": 50, "pageSizes": [50], "snippetMaxChars": 240},
            }
        )
        harness = (
            "global.window={localStorage:{getItem(){return null},setItem(){}}};\n"
            f"const payload={payload};\n"
            "global.document={getElementById(id){return id==='failure-data'?{textContent:JSON.stringify(payload)}:null}};\n"
            + app_js.replace(
                "  init();\n})();",
                "  console.log(csvCell('=HYPERLINK(\"http://bad\")'));\n"
                "  console.log(csvCell('  @cmd'));\n"
                "  console.log(csvCell('safe value'));\n})();",
            )
        )
        check = subprocess.run(
            [node, "-e", harness],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
        lines = check.stdout.splitlines()
        self.assertEqual(lines[0], '"\'=HYPERLINK(""http://bad"")"')
        self.assertEqual(lines[1], "'  @cmd")
        self.assertEqual(lines[2], "safe value")

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
