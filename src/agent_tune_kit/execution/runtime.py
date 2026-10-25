"""Launch trusted installed ATK code with the target project's interpreter."""

import ast
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.workspace import RUNNER_PATH


class ExecutionFailed(RuntimeError):
    def __init__(self, returncode):
        super().__init__(f"Project execution failed (exit {returncode}); partial results were preserved.")
        self.returncode = returncode


def python_command(python=None):
    if python:
        return [str(python)]
    if Path("uv.lock").is_file() and shutil.which("uv"):
        return ["uv", "run", "python"]
    venv = Path(".venv") / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if venv.is_file():
        # Resolving a venv symlink would switch execution to the base interpreter.
        return [str(venv.absolute())]
    if Path("poetry.lock").is_file() and shutil.which("poetry"):
        return ["poetry", "run", "python"]
    return [shutil.which("python3") or sys.executable]


def managed_runner(path=RUNNER_PATH):
    if not Path(path).is_file():
        raise UserActionRequired(f"Runner {path} is missing; run atk-init first.")
    for node in ast.parse(Path(path).read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ATK_MANAGED_RUNNER" for target in node.targets
        ):
            return isinstance(node.value, ast.Constant) and node.value.value is True
    return False


def launch(operation, *, python=None, **options):
    if operation == "eval" and not managed_runner() and not options.get("check"):
        flags = []
        for key, value in options.items():
            if value is True:
                flags.append("--" + key.replace("_", "-"))
            elif value is not False and value is not None:
                flags.extend(["--" + key.replace("_", "-"), str(value)])
        command = python_command(python) + [str(RUNNER_PATH), *flags]
        result = subprocess.run(command, stdout=sys.stderr, check=False)
        if result.returncode:
            raise ExecutionFailed(result.returncode)
        from agent_tune_kit.results.versions import list_version_dirs

        versions = list_version_dirs()
        current = versions[-1][1] if versions else None
        return {
            "legacy": True,
            "version": current.name if current else None,
            "output": str(current / "eval_results.csv")
            if current and (current / "eval_results.csv").is_file()
            else None,
        }
    root = str(Path(__file__).resolve().parents[2])
    bootstrap = "import sys;sys.path.insert(0,sys.argv[1]);from agent_tune_kit.execution.runtime import worker;worker()"
    process = subprocess.Popen(
        python_command(python) + ["-c", bootstrap, root, operation, json.dumps(options)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, _ = process.communicate()
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait()
        raise
    if process.returncode:
        raise ExecutionFailed(process.returncode)
    return json.loads(stdout)


def worker():
    from contextlib import redirect_stdout

    try:
        with redirect_stdout(sys.stderr):
            options = json.loads(sys.argv[3])
            if sys.argv[2] == "eval":
                from .runner import run_evaluation

                result = run_evaluation(**options)
            else:
                from .rules import run_rule

                result = run_rule(**options)
        print(json.dumps(result, ensure_ascii=False))
    except UserActionRequired as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
