# Template: .atk/runner/eval_runner.py

Generate this project adapter after inspecting Agent invocation, input fields, runtime and logs.
Preserves all original dataset columns through the shared CLI engine. The engine reads
`.atk/datasets/dataset.csv`, requires `atk_id`, appends `agent_output` and `log_path`, and
writes `.atk/results/vN/eval_results.csv`. Current max version rules are implemented once
in ATK. Direct execution remains supported with `--limit`, `--offset`, `--concurrency`,
`--only-failures` and `--no-progress`. Replace all TODO_AGENT_TUNING blocks.

```python
#!/usr/bin/env python3
"""Project-specific Agent adapter; fixed execution behavior belongs to ATK."""
import logging
import shutil
import subprocess
import sys
from pathlib import Path

ATK_MANAGED_RUNNER = True
PYTHON_LOGGING_CAPTURE_ENABLED = False
CONCURRENT_ROW_LOGGING_ENABLED = True
ROW_LOGGER_NAMES = ("",)
ROW_LOG_LEVEL = logging.INFO
ROW_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
REPO_ROOT = Path(__file__).resolve().parents[2]

if __name__ == "__main__":
    atk = shutil.which("atk")
    if not atk:
        print("Install ATK first: uv tool install agent-tune-kit; atk install", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(subprocess.call([atk, "eval", "--python", sys.executable, *sys.argv[1:]]))
else:
    from agent_tune_kit.errors import AgentExecutionError, UserActionRequired

# TODO_AGENT_TUNING_IMPORT_PATHS: retain project-specific imports and runtime setup.
for root in (REPO_ROOT / "src", REPO_ROOT):
    if root.is_dir():
        sys.path.insert(0, str(root))


def build_agent_input(row):
    # TODO_AGENT_TUNING: exclude answer fields and ATK metadata from Agent inputs.
    raise UserActionRequired("TODO_AGENT_TUNING: implement build_agent_input(row).")


def call_agent(agent_input):
    # TODO_AGENT_TUNING: implement the project's sync or async Agent invocation.
    # Raise AgentExecutionError only for known row failures; propagate setup errors.
    raise UserActionRequired("TODO_AGENT_TUNING: implement call_agent(agent_input).")
```
