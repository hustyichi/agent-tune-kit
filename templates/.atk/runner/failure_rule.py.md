# Template: .atk/runner/failure_rule.py

Use this optional template inside `atk-diagnose` for reusable checks. Prepare or reuse the rule and execute it in the same Skill. Return `unknown` when a check does not establish the overall outcome; a format check alone cannot establish business success.

```python
#!/usr/bin/env python3
"""Rule-based failure-case filter for Agent Tune Kit.

Prepared on demand by atk-diagnose; also records judgments.csv.
Reads current .atk/results/vN/eval_results.csv and overwrites current
.atk/results/vN/failure_cases.csv.
"""

from __future__ import annotations

import csv
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Dict, List

RESULTS_DIR = Path(".atk/results")
RESULTS_FILENAME = "eval_results.csv"
FAILURE_FILENAME = "failure_cases.csv"


class UserActionRequired(RuntimeError):
    """Raised when the user must repair inputs or confirm an unsafe inference."""


def list_version_dirs(results_dir=RESULTS_DIR):
    # Return [(number, path)] for directories named vN where N is a positive integer.
    # Missing results_dir means no versions exist yet; runner first run will create v1.
    if not results_dir.exists():
        return []
    return sorted(
        (int(path.name[1:]), path)
        for path in results_dir.iterdir()
        if path.is_dir() and path.name.startswith("v") and path.name[1:].isdigit() and int(path.name[1:]) > 0
    )


def resolve_current_version(results_dir=RESULTS_DIR):
    # Used by every non-runner Skill. Do not filter by required files.
    versions = list_version_dirs(results_dir)
    if not versions:
        raise UserActionRequired("No vN results directory exists; run eval_runner.py first or confirm repair.")
    return versions[-1][1]


def require_current_file(current_dir, filename):
    # Missing inputs are blockers; never fall back to an older version.
    path = current_dir / filename
    if not path.exists():
        raise UserActionRequired(f"Current version {current_dir.name} is missing {filename}; fix or rerun the prior step.")
    return path


def load_results(path: Path) -> tuple[List[str], List[Dict[str, str]]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise UserActionRequired(f"eval_results.csv has no header row: {path}")
        fieldnames = list(reader.fieldnames)
        if "agent_output" not in fieldnames:
            raise UserActionRequired("eval_results.csv is missing required agent_output column.")
        return fieldnames, list(reader)


def judge(row: Dict[str, str]) -> tuple[str, str]:
    """Return (passed/failed/unknown, reason) against the confirmed task criteria.

    TODO_AGENT_TUNING: implement confirmed checks. Uncovered semantic requirements,
    missing evidence and rule errors must not be converted to passed.
    """
    raise UserActionRequired("TODO_AGENT_TUNING: implement confirmed failure rule before running.")


def write_failures(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    current_dir = resolve_current_version()
    results_path = require_current_file(current_dir, RESULTS_FILENAME)
    fieldnames, rows = load_results(results_path)
    if "atk_id" not in fieldnames:
        raise UserActionRequired("eval_results.csv is missing atk_id.")
    ids = [row["atk_id"].strip() for row in rows]
    if any(not aid for aid in ids) or len(set(ids)) != len(ids):
        raise UserActionRequired("atk_id must be non-empty and unique.")
    digest = hashlib.sha256(results_path.read_bytes()).hexdigest()
    judgments, failure_rows = [], []
    for row, aid in zip(rows, ids, strict=True):
        verdict, reason = judge(row)
        if verdict not in {"passed", "failed", "unknown"} or not isinstance(reason, str) or not reason.strip():
            raise UserActionRequired(f"Invalid judgment for atk_id {aid}.")
        judgments.append({"atk_id": aid, "verdict": verdict, "reason": reason, "results_sha256": digest})
        if verdict == "failed":
            failure_rows.append(row)
    failure_path = current_dir / FAILURE_FILENAME
    # Invalidate old passes before replacing failures; interruption stays conservative.
    judgment_path = current_dir / "judgments.csv"
    judgment_fields = ["atk_id", "verdict", "reason", "results_sha256"]
    write_failures(judgment_path, judgment_fields, [])
    write_failures(failure_path, fieldnames, failure_rows)
    write_failures(judgment_path, judgment_fields, judgments)
    print(f"Read {results_path}")
    print(f"Overwrote {failure_path} with {len(failure_rows)} failure rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
