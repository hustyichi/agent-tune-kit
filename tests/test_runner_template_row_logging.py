from __future__ import annotations

import csv
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "templates/.atk/runner/eval_runner.py.md"


def render_runner(*, concurrent_enabled: bool = True, inject_context_free_log: bool = False) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    match = re.search(r"```python\n(.*?)\n```", template, flags=re.DOTALL)
    assert match is not None, "runner template must contain a Python code fence"
    code = match.group(1)
    code = code.replace(
        'DATASET_PATH = Path("TODO_AGENT_TUNING_DATASET_PATH")',
        'DATASET_PATH = Path("dataset.csv")',
    )
    code = code.replace("PYTHON_LOGGING_CAPTURE_ENABLED = False", "PYTHON_LOGGING_CAPTURE_ENABLED = True")
    code = code.replace(
        "CONCURRENT_ROW_LOGGING_ENABLED = True",
        f"CONCURRENT_ROW_LOGGING_ENABLED = {concurrent_enabled!r}",
    )
    code = code.replace(
        '    raise UserActionRequired("TODO_AGENT_TUNING: implement build_agent_input(row).")',
        "    return row",
    )
    code = code.replace(
        '    raise UserActionRequired("TODO_AGENT_TUNING: implement call_agent(agent_input).")',
        textwrap.indent(
            textwrap.dedent(
                """
                import time
                import threading

                token = agent_input["token"]
                mode = agent_input.get("mode", "ok")
                logger = logging.getLogger("agent_smoke")
                logger.info("start %s", token)
                if mode == "slow":
                    time.sleep(0.05)
                if mode == "background":
                    thread = threading.Thread(
                        target=lambda: logging.getLogger("agent_smoke").info("background %s", token)
                    )
                    thread.start()
                    thread.join()
                if mode == "known_error":
                    logger.info("known-error %s", token)
                    raise AgentExecutionError(f"known {token}")
                if mode == "unknown_error":
                    logger.info("unknown-error %s", token)
                    raise RuntimeError(f"unknown {token}")
                logger.info("end %s", token)
                return f"ok {token}"
                """
            ).strip(),
            "    ",
        ),
    )
    if inject_context_free_log:
        code = code.replace(
            "        with capture_python_row_logging(enabled=row_logging_enabled):\n            for completed",
            (
                "        with capture_python_row_logging(enabled=row_logging_enabled):\n"
                "            logging.getLogger(\"agent_smoke\").info(\"CONTEXT_FREE_DURING_RUN\")\n"
                "            for completed"
            ),
        )
    return code


def run_rendered_runner(
    rows: list[dict[str, str]],
    *,
    concurrency: int,
    concurrent_enabled: bool = True,
    inject_context_free_log: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="atk-runner-row-logs-"))
    runner_path = temp_dir / ".atk/runner/eval_runner.py"
    runner_path.parent.mkdir(parents=True)
    runner_path.write_text(
        render_runner(concurrent_enabled=concurrent_enabled, inject_context_free_log=inject_context_free_log),
        encoding="utf-8",
    )
    dataset_path = temp_dir / "dataset.csv"
    with dataset_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["token", "mode"])
        writer.writeheader()
        writer.writerows(rows)
    completed = subprocess.run(
        [sys.executable, str(runner_path), "--concurrency", str(concurrency), "--no-progress"],
        cwd=temp_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    return completed, temp_dir


def read_results(temp_dir: Path) -> list[dict[str, str]]:
    results_path = temp_dir / ".atk/results/v1/eval_results.csv"
    with results_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class RunnerTemplateRowLoggingTests(unittest.TestCase):
    def test_concurrent_row_logs_do_not_cross_contaminate_or_capture_context_free_records(self) -> None:
        completed, temp_dir = run_rendered_runner(
            [
                {"token": "ROW_A_UNIQUE", "mode": "slow"},
                {"token": "ROW_B_UNIQUE", "mode": "ok"},
                {"token": "ROW_C_UNIQUE", "mode": "background"},
            ],
            concurrency=3,
            inject_context_free_log=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        results = read_results(temp_dir)
        self.assertEqual({row["agent_output_log_path"] for row in results}, {
            "logs/row_000001.log",
            "logs/row_000002.log",
            "logs/row_000003.log",
        })

        logs = {
            row["token"]: (temp_dir / ".atk/results/v1" / row["agent_output_log_path"]).read_text(encoding="utf-8")
            for row in results
        }
        self.assertIn("ROW_A_UNIQUE", logs["ROW_A_UNIQUE"])
        self.assertNotIn("ROW_B_UNIQUE", logs["ROW_A_UNIQUE"])
        self.assertIn("ROW_B_UNIQUE", logs["ROW_B_UNIQUE"])
        self.assertNotIn("ROW_A_UNIQUE", logs["ROW_B_UNIQUE"])
        self.assertNotIn("CONTEXT_FREE_DURING_RUN", "\n".join(logs.values()))
        self.assertNotIn("background ROW_C_UNIQUE", logs["ROW_C_UNIQUE"])

    def test_known_agent_error_keeps_row_log_path_and_error_status(self) -> None:
        completed, temp_dir = run_rendered_runner(
            [
                {"token": "ERR_ROW_UNIQUE", "mode": "known_error"},
                {"token": "AFTER_ERROR_UNIQUE", "mode": "ok"},
            ],
            concurrency=2,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        rows = {row["token"]: row for row in read_results(temp_dir)}
        row = rows["ERR_ROW_UNIQUE"]
        self.assertEqual(row["agent_output_status"], "error")
        self.assertEqual(row["agent_output_log_path"], "logs/row_000001.log")
        log_text = (temp_dir / ".atk/results/v1/logs/row_000001.log").read_text(encoding="utf-8")
        self.assertIn("known-error ERR_ROW_UNIQUE", log_text)
        after_error_log = (temp_dir / ".atk/results/v1/logs/row_000002.log").read_text(encoding="utf-8")
        self.assertIn("AFTER_ERROR_UNIQUE", after_error_log)
        self.assertNotIn("ERR_ROW_UNIQUE", after_error_log)

    def test_disabled_concurrent_row_logging_downgrades_without_row_files(self) -> None:
        completed, temp_dir = run_rendered_runner(
            [{"token": "NO_ROW_LOG", "mode": "ok"}],
            concurrency=2,
            concurrent_enabled=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        [row] = read_results(temp_dir)
        self.assertEqual(row["agent_output_log_path"], "")
        self.assertIn("Concurrent row-level Python logging capture is disabled", completed.stderr)
        self.assertFalse((temp_dir / ".atk/results/v1/logs").exists())

    def test_serial_row_logging_still_writes_row_logs(self) -> None:
        completed, temp_dir = run_rendered_runner(
            [{"token": "SERIAL_ROW_UNIQUE", "mode": "ok"}],
            concurrency=1,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        [row] = read_results(temp_dir)
        self.assertEqual(row["agent_output_log_path"], "logs/row_000001.log")
        log_text = (temp_dir / ".atk/results/v1/logs/row_000001.log").read_text(encoding="utf-8")
        self.assertIn("SERIAL_ROW_UNIQUE", log_text)


if __name__ == "__main__":
    unittest.main()
