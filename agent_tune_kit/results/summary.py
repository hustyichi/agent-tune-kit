import hashlib
from pathlib import Path

from agent_tune_kit.utils.csv_io import parse_csv
from agent_tune_kit.workspace import RESULTS_DIR

from .assessment import compare_assessments, has_assessments, truth_counts
from .judgments import read_judgments
from .versions import resolve_current_version, resolve_evaluation_version, resolve_previous_version, run_kind


def effective_verdicts(current):
    verdicts = read_judgments(current)
    path = current / "failure_cases.csv"
    if path.is_file() and run_kind(current) == "legacy" and not has_assessments(current):
        _, failures = parse_csv(path)
        for row in failures:
            if row.get("atk_id"):
                verdicts[row["atk_id"]] = "failed"
    return verdicts


def _inspect_version(current, results_dir):
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
        result["truth_counts"] = truth_counts(rows)
    return result


def inspect_results(results_dir=RESULTS_DIR, *, version=None):
    from agent_tune_kit.errors import UserActionRequired

    from .evidence import inspect_run

    latest = resolve_current_version(Path(results_dir))
    result = _inspect_version(latest, results_dir)
    result["latest_run"] = dict(result)
    if (latest / "run.json").exists() or (latest / "executions").exists():
        try:
            result["latest_run"]["evidence"] = inspect_run(latest)
        except UserActionRequired as exc:
            result["latest_run"]["evidence_error"] = str(exc)
    selected = latest
    if version:
        from .versions import list_version_dirs

        selected = {path.name: path for _, path in list_version_dirs(Path(results_dir))}.get(version)
        if selected is None:
            raise UserActionRequired("Selected result version does not exist.")
        run_kind(selected)
        result.update(_inspect_version(selected, results_dir))
    try:
        target = resolve_evaluation_version(results_dir, version if run_kind(selected) != "evidence-only" else None)
        result["evaluation_target"] = _inspect_version(target, results_dir)
    except UserActionRequired as exc:
        result["evaluation_target"] = None
        result["selection_error"] = str(exc)
    return result


def compare_targets(ids, results_dir=RESULTS_DIR, *, version=None):
    current = resolve_evaluation_version(Path(results_dir), version)
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
    differences = compare_assessments(current, previous, ids)
    assessed = has_assessments(current)
    return {
        "version": current.name,
        "previous_version": previous.name if previous else None,
        "status": "无法判断" if assessed and differences else status,
        "current_status": status,
        "comparison_differences": differences,
        "targets": dict(zip(ids, states, strict=True)),
        "comparability_requires_review": True,
    }
