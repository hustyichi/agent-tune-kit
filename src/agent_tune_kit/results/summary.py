import hashlib
from pathlib import Path

from agent_tune_kit.utils.csv_io import parse_csv
from agent_tune_kit.workspace import RESULTS_DIR

from .judgments import read_judgments
from .versions import resolve_current_version, resolve_previous_version


def effective_verdicts(current):
    verdicts = read_judgments(current)
    path = current / "failure_cases.csv"
    if path.is_file():
        _, failures = parse_csv(path)
        for row in failures:
            if row.get("atk_id"):
                verdicts[row["atk_id"]] = "failed"
    return verdicts


def inspect_results(results_dir=RESULTS_DIR):
    current = resolve_current_version(Path(results_dir))
    previous = resolve_previous_version(current, Path(results_dir))
    names = ["eval_results.csv", "judgments.csv", "failure_cases.csv", "report.md", "tuning_plan.md", "app.log"]
    result = {
        "version": current.name,
        "current_dir": str(current),
        "previous_version": previous.name if previous else None,
        "files": {name: (current / name).is_file() for name in names},
        "executed": None,
        "judged": None,
        "passed": None,
        "failed": None,
        "unknown": None,
    }
    if result["files"]["eval_results.csv"]:
        result["results_sha256"] = hashlib.sha256((current / "eval_results.csv").read_bytes()).hexdigest()
        _, rows = parse_csv(current / "eval_results.csv")
        verdicts = effective_verdicts(current)
        counts = {verdict: 0 for verdict in ["passed", "failed", "unknown"]}
        for row in rows:
            counts[verdicts.get(row.get("atk_id"), "unknown")] += 1
        result.update(counts, executed=len(rows), judged=counts["passed"] + counts["failed"])
    return result


def compare_targets(ids, results_dir=RESULTS_DIR):
    current = resolve_current_version(Path(results_dir))
    previous = resolve_previous_version(current, Path(results_dir))
    verdicts = effective_verdicts(current)
    executed = set()
    if (current / "eval_results.csv").is_file():
        _, rows = parse_csv(current / "eval_results.csv")
        executed = {row.get("atk_id") for row in rows}
    states = [verdicts.get(aid, "unknown") if aid in executed else "unknown" for aid in ids]
    status = "无法判断"
    if previous and states and "unknown" not in states:
        status = "已解决" if set(states) == {"passed"} else "未解决" if set(states) == {"failed"} else "部分解决"
    return {
        "version": current.name,
        "previous_version": previous.name if previous else None,
        "status": status,
        "targets": dict(zip(ids, states, strict=True)),
        "comparability_requires_review": True,
    }
