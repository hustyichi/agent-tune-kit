# Template: .atk/runner/failure_rule.py

Prepare this optional rule inside atk-diagnose after inspecting confirmed criteria.
The CLI owns current-version selection, results validation, judgments.csv and failure_cases.csv.
Return unknown when applicable requirements are not covered; format success alone is not task success.

```python
#!/usr/bin/env python3
"""Project-specific rule; execute with atk results run-rule."""
import shutil
import subprocess
import sys
from pathlib import Path

ATK_MANAGED_RULE = True

if __name__ == "__main__":
    atk = shutil.which("atk")
    if not atk:
        print("Install ATK first: uv tool install agent-tune-kit; atk install", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(subprocess.call([atk, "results", "run-rule", "--rule-path", str(Path(__file__).resolve()), "--python", sys.executable]))
else:
    from agent_tune_kit.errors import UserActionRequired


def judge(row):
    """Return (passed/failed/unknown, reason) using confirmed checks."""
    # TODO_AGENT_TUNING: implement confirmed checks; do not invent a heuristic.
    raise UserActionRequired("TODO_AGENT_TUNING: implement confirmed failure rule before running.")
```
