import csv
import io
import re
from pathlib import Path
from typing import Any

from agent_tune_kit.results.assessment import has_assessments
from agent_tune_kit.results.judgments import read_judgments as _read_judgments
from agent_tune_kit.results.versions import list_version_dirs

from .fields import detect_atk_id_field, normalize_name
from .fields import detect_roles as _detect_roles

FAILURE_FILENAME = "failure_cases.csv"

EVAL_FILENAME = "eval_results.csv"

TUNING_PLAN_FILENAME = "tuning_plan.md"

REPORT_FILENAME = "report.md"

REPORT_MAX_BYTES = 256 * 1024

REPORT_MAX_EXCERPTS = 5

REPORT_EXCERPT_MAX_CHARS = 800

HISTORY_FAILURE_MAX_BYTES = 64 * 1024 * 1024

HISTORY_EVAL_MAX_BYTES = 64 * 1024 * 1024

HISTORY_TUNING_PLAN_MAX_BYTES = 256 * 1024

HISTORY_PERSISTENT_MIN_FAILS = 2

REASON_VALUE_MAX_CHARS = 80

REASON_TOP_N = 12

REPORT_KEYWORDS = (
    "summary",
    "failure",
    "root",
    "cause",
    "tuning",
    "priority",
    "recommend",
    "摘要",
    "异常",
    "失败",
    "原因",
    "根因",
    "优先",
    "调优",
)


def detect_roles(fieldnames):
    return _detect_roles(fieldnames, ROLE_CANDIDATES)


def read_judgments(version_dir):
    return _read_judgments(version_dir, max_bytes=HISTORY_FAILURE_MAX_BYTES)


def _safe_read_text(path: Path, max_bytes: int) -> tuple[str, bool]:
    """Read up to max_bytes from path; return (text, truncated)."""
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    return raw.decode("utf-8", errors="replace"), truncated


def _safe_read_csv_text(path: Path, max_bytes: int) -> tuple[str, bool]:
    """Read bounded CSV text and drop a truncated trailing partial record."""
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
        if raw and not raw.endswith((b"\n", b"\r")):
            last_newline = raw.rfind(b"\n")
            raw = raw[: last_newline + 1] if last_newline >= 0 else b""
    return raw.decode("utf-8", errors="replace"), truncated


def _read_version_failures(failure_path: Path) -> dict[str, Any]:
    """Return atk_id set + reason distribution + raw failure count for a historical version."""
    out: dict[str, Any] = {
        "failedCount": 0,
        "atkIds": set(),
        "reasonCounts": {},
        "atkIdField": "",
        "reasonField": "",
        "readable": True,
        "truncated": False,
    }
    try:
        text, truncated = _safe_read_csv_text(failure_path, HISTORY_FAILURE_MAX_BYTES)
        out["truncated"] = truncated
        with io.StringIO(text, newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            atk_field = detect_atk_id_field(fieldnames)
            roles = detect_roles(fieldnames)
            reason_field = roles.get("reason", {}).get("field", "")
            out["atkIdField"] = atk_field
            out["reasonField"] = reason_field
            for raw_row in reader:
                out["failedCount"] += 1
                if atk_field:
                    aid = (raw_row.get(atk_field) or "").strip()
                    if aid:
                        out["atkIds"].add(aid)
                if reason_field:
                    value = (raw_row.get(reason_field) or "").strip()
                    if value:
                        key = truncate(value, REASON_VALUE_MAX_CHARS)
                        out["reasonCounts"][key] = out["reasonCounts"].get(key, 0) + 1
    except (OSError, UnicodeDecodeError, csv.Error):
        out["readable"] = False
    return out


def _read_version_tested_ids(eval_path: Path) -> dict[str, Any]:
    """Return tested atk_id set for a version from a bounded eval_results.csv read."""
    out: dict[str, Any] = {"testedCount": 0, "atkIds": set(), "available": False, "truncated": False}
    if not eval_path.is_file():
        return out
    try:
        size = eval_path.stat().st_size
    except OSError:
        return out
    out["truncated"] = size > HISTORY_EVAL_MAX_BYTES
    try:
        text, truncated = _safe_read_csv_text(eval_path, HISTORY_EVAL_MAX_BYTES)
        out["truncated"] = out["truncated"] or truncated
        with io.StringIO(text, newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            atk_field = detect_atk_id_field(fieldnames)
            if not atk_field:
                return out
            out["available"] = True
            for raw_row in reader:
                aid = (raw_row.get(atk_field) or "").strip()
                if aid:
                    out["atkIds"].add(aid)
                out["testedCount"] += 1
    except (OSError, csv.Error):
        return out
    return out


def _parse_tuning_plan_targets(plan_path: Path) -> dict[str, Any]:
    """Extract bullet-level targets and best-effort atk_id tokens from tuning_plan.md."""
    out: dict[str, Any] = {"available": False, "reason": "", "targets": [], "atkIds": set()}
    if not plan_path.is_file():
        out["reason"] = "tuning_plan.md not found in previous version."
        return out
    try:
        text, truncated = _safe_read_text(plan_path, HISTORY_TUNING_PLAN_MAX_BYTES)
    except OSError as exc:
        out["reason"] = f"Could not read tuning_plan.md: {exc}"
        return out
    out["available"] = True
    if truncated:
        out["reason"] = f"Read first {HISTORY_TUNING_PLAN_MAX_BYTES} bytes only."

    in_targets = False
    current_bullet: list[str] = []
    bullets: list[str] = []

    def _flush() -> None:
        if current_bullet:
            joined = " ".join(part.strip() for part in current_bullet if part.strip())
            if joined:
                bullets.append(joined)
        current_bullet.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            _flush()
            heading = stripped.lstrip("#").strip()
            in_targets = "目标异常清单" in heading or "target failures" in heading.lower()
            continue
        if not in_targets:
            continue
        if stripped.startswith(("- ", "* ", "+ ")):
            _flush()
            current_bullet.append(stripped[2:].strip())
        elif stripped and current_bullet:
            current_bullet.append(stripped)
    _flush()

    for bullet in bullets:
        ids: list[str] = []
        seen: set[str] = set()
        for match in _ATK_ID_TOKEN_RE.finditer(bullet):
            token = match.group(1).strip(".,;:'`\"")
            if token and token not in seen and token.lower() not in {"the", "and", "of"}:
                seen.add(token)
                ids.append(token)
        if not ids:
            for match in _BARE_ID_RE.finditer(bullet):
                token = match.group(1)
                if token.isdigit() and token not in seen:
                    seen.add(token)
                    ids.append(token)
        out["targets"].append({"text": truncate(bullet, 600), "atkIds": ids})
        for token in ids:
            out["atkIds"].add(token)
    return out


def _summarize_version(version_dir: Path) -> dict[str, Any]:
    """Read failure + tested atk_id sets for a single historical version."""
    failure_info = (
        _read_version_failures(version_dir / FAILURE_FILENAME) if (version_dir / FAILURE_FILENAME).is_file() else None
    )
    eval_info = _read_version_tested_ids(version_dir / EVAL_FILENAME)
    return {
        "name": version_dir.name,
        "number": int(version_dir.name[1:])
        if version_dir.name.startswith("v") and version_dir.name[1:].isdigit()
        else 0,
        "hasFailureCsv": failure_info is not None,
        "failureReadable": bool(failure_info and failure_info["readable"]),
        "failedCount": failure_info["failedCount"] if failure_info else 0,
        "failedAtkIds": failure_info["atkIds"] if failure_info else set(),
        "reasonCounts": failure_info["reasonCounts"] if failure_info else {},
        "reasonField": failure_info["reasonField"] if failure_info else "",
        "atkIdField": failure_info["atkIdField"] if failure_info else "",
        "evalAvailable": eval_info["available"],
        "testedCount": eval_info["testedCount"],
        "testedAtkIds": eval_info["atkIds"],
        "evalTruncated": eval_info["truncated"],
    }


def build_history(
    results_dir: Path,
    current_dir: Path,
    current_fieldnames: list[str],
    current_rows: list[dict[str, str]],
    current_roles: dict[str, dict[str, str]],
    *,
    skip: bool = False,
) -> dict[str, Any]:
    """Assemble cross-version trend, explicit per-case status, and target resolution."""
    if skip:
        return {
            "status": "skipped",
            "reason": "Skipped by --no-history.",
            "versions": [],
            "perCase": [],
            "previousTargets": {},
        }

    atk_field = detect_atk_id_field(current_fieldnames)
    versions = list_version_dirs(results_dir)
    current_number = (
        int(current_dir.name[1:]) if current_dir.name.startswith("v") and current_dir.name[1:].isdigit() else 0
    )
    current_reason_field = current_roles.get("reason", {}).get("field", "")

    current_failed_ids: set[str] = set()
    current_reason_counts: dict[str, int] = {}

    abnormal_field = ""
    for f in current_fieldnames:
        if normalize_name(f) in ("is_abnormal", "abnormal", "is_failure"):
            abnormal_field = f
            break

    for row in current_rows:
        if abnormal_field:
            val = str(row.get(abnormal_field, "")).strip().lower()
            if val not in ("true", "1", "yes", "y", "异常", "失败", "fail", "failed", "bad"):
                continue
        if atk_field:
            aid = (row.get(atk_field) or "").strip()
            if aid:
                current_failed_ids.add(aid)
        if current_reason_field:
            value = (row.get(current_reason_field) or "").strip()
            if value:
                key = truncate(value, REASON_VALUE_MAX_CHARS)
                current_reason_counts[key] = current_reason_counts.get(key, 0) + 1

    # Build per-version summaries (skip the current version on disk; we use in-memory data for it).
    version_summaries: list[dict[str, Any]] = []
    interesting_ids: set[str] = set(current_failed_ids)
    for _, vpath in versions:
        if vpath.resolve() == current_dir.resolve():
            summary = {
                "name": current_dir.name,
                "number": current_number,
                "isCurrent": True,
                "hasFailureCsv": True,
                "failureReadable": True,
                "failedCount": len(current_failed_ids),
                "failedAtkIds": current_failed_ids,
                "reasonCounts": current_reason_counts,
                "reasonField": current_reason_field,
                "atkIdField": atk_field,
                "evalAvailable": False,
                "testedCount": 0,
                "testedAtkIds": set(),
                "evalTruncated": False,
            }
            eval_info = _read_version_tested_ids(current_dir / EVAL_FILENAME)
            summary["evalAvailable"] = eval_info["available"]
            summary["testedCount"] = eval_info["testedCount"]
            summary["testedAtkIds"] = eval_info["atkIds"]
            summary["evalTruncated"] = eval_info["truncated"]
        else:
            summary = _summarize_version(vpath)
            summary["isCurrent"] = False
        verdicts = read_judgments(vpath)
        tested_ids = summary["testedAtkIds"]
        if has_assessments(vpath):
            summary["failedAtkIds"] = set()
        summary["failedAtkIds"] |= {
            aid for aid, verdict in verdicts.items() if verdict == "failed" and aid in tested_ids
        }
        summary["passedAtkIds"] = {
            aid for aid, verdict in verdicts.items() if verdict == "passed" and aid in tested_ids
        } - summary["failedAtkIds"]
        summary["unknownAtkIds"] = tested_ids - summary["failedAtkIds"] - summary["passedAtkIds"]
        if summary["atkIdField"]:
            summary["failedCount"] = len(summary["failedAtkIds"])
        version_summaries.append(summary)
        interesting_ids.update(summary["unknownAtkIds"])
        interesting_ids.update(summary.get("failedAtkIds", set()))

    # Heuristic subset-run detection: tested set materially shrinks vs previous.
    prev_tested = None
    for summary in version_summaries:
        tested = summary["testedCount"] if summary["evalAvailable"] else None
        is_subset = False
        if tested is not None and prev_tested is not None and prev_tested > 0 and tested < prev_tested * 0.5:
            is_subset = True
        summary["isSubsetRun"] = is_subset
        if tested is not None:
            prev_tested = tested

    # A missing verdict is unknown, never an implicit pass.
    per_case: list[dict[str, Any]] = []
    if atk_field and interesting_ids:
        for aid in sorted(interesting_ids, key=lambda v: (len(v), v)):
            statuses: dict[str, str] = {}
            failed_versions: list[str] = []
            tested_versions: list[str] = []
            first_seen = ""
            for summary in version_summaries:
                vname = summary["name"]
                failed = aid in summary["failedAtkIds"]
                if failed:
                    statuses[vname] = "failed"
                    failed_versions.append(vname)
                    tested_versions.append(vname)
                    if not first_seen:
                        first_seen = vname
                elif summary["evalAvailable"]:
                    if aid in summary["testedAtkIds"]:
                        statuses[vname] = "passed" if aid in summary["passedAtkIds"] else "unknown"
                        tested_versions.append(vname)
                    else:
                        statuses[vname] = "unknown" if summary["evalTruncated"] else "not_tested"
                else:
                    statuses[vname] = "unknown"
            per_case.append(
                {
                    "atkId": aid,
                    "statuses": statuses,
                    "failedCount": len(failed_versions),
                    "testedCount": len(tested_versions),
                    "firstSeenVersion": first_seen,
                }
            )

    # Previous-version targets resolution
    previous = version_summaries[-2] if len(version_summaries) >= 2 else None
    previous_targets: dict[str, Any] = {"available": False}
    if previous is not None:
        prev_dir = results_dir / previous["name"]
        parsed = _parse_tuning_plan_targets(prev_dir / TUNING_PLAN_FILENAME)
        previous_targets = {
            "previousVersion": previous["name"],
            "available": parsed["available"],
            "reason": parsed["reason"],
            "targets": [],
        }
        current_summary = version_summaries[-1]
        for target in parsed["targets"]:
            ids = target["atkIds"]
            if not ids:
                resolution = "indeterminate"
                detail = "no atk_id reference detected"
            else:
                failed_now = [i for i in ids if i in current_summary["failedAtkIds"]]
                passed_now = [i for i in ids if i in current_summary["passedAtkIds"]]
                if len(failed_now) + len(passed_now) < len(ids):
                    resolution = "indeterminate"
                    detail = "some target atk_ids were not executed or lack a valid judgment"
                elif len(passed_now) == len(ids):
                    resolution = "resolved"
                    detail = f"{len(ids)}/{len(ids)} target atk_ids explicitly passed in current"
                elif len(failed_now) == len(ids):
                    resolution = "unresolved"
                    detail = f"all {len(ids)} target atk_ids still failing"
                else:
                    resolution = "partial"
                    detail = f"{len(passed_now)}/{len(ids)} resolved, {len(failed_now)} still failing"
            previous_targets.setdefault("targets", []).append(
                {
                    "text": target["text"],
                    "atkIds": ids,
                    "resolution": resolution,
                    "detail": detail,
                    "stillFailing": [i for i in ids if i in current_failed_ids],
                }
            )

    # Aggregate previous target resolution summary
    summary_counts = {"resolved": 0, "partial": 0, "unresolved": 0, "indeterminate": 0}
    for target in previous_targets.get("targets", []):
        summary_counts[target["resolution"]] = summary_counts.get(target["resolution"], 0) + 1
    previous_targets["summary"] = summary_counts

    # Regression / persistent buckets (relative to current version)
    new_regressions: list[str] = []
    persistent: list[str] = []
    if previous is not None and atk_field:
        for aid in current_failed_ids:
            if aid in previous["passedAtkIds"]:
                new_regressions.append(aid)
    if atk_field:
        for case in per_case:
            if case["failedCount"] >= HISTORY_PERSISTENT_MIN_FAILS and case["atkId"] in current_failed_ids:
                persistent.append(case["atkId"])

    # Reason distribution per version (Top-N union for legend stability)
    all_reasons: dict[str, int] = {}
    for summary in version_summaries:
        for key, count in summary["reasonCounts"].items():
            all_reasons[key] = all_reasons.get(key, 0) + count
    top_reasons = [k for k, _ in sorted(all_reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:REASON_TOP_N]]

    serialized_versions = []
    for summary in version_summaries:
        serialized_versions.append(
            {
                "name": summary["name"],
                "number": summary["number"],
                "isCurrent": summary.get("isCurrent", False),
                "isSubsetRun": summary.get("isSubsetRun", False),
                "hasFailureCsv": summary["hasFailureCsv"],
                "failureReadable": summary["failureReadable"],
                "failedCount": summary["failedCount"],
                "evalAvailable": summary["evalAvailable"],
                "testedCount": summary["testedCount"],
                "passedCount": len(summary["passedAtkIds"]),
                "unknownCount": len(summary["unknownAtkIds"]),
                "evalTruncated": summary["evalTruncated"],
                "reasonField": summary["reasonField"],
                "atkIdField": summary["atkIdField"],
                "reasonCounts": summary["reasonCounts"],
            }
        )

    return {
        "status": "included" if len(version_summaries) >= 1 else "skipped",
        "reason": "" if atk_field else "current failure_cases.csv has no atk_id column; cross-version disabled.",
        "atkIdField": atk_field,
        "currentVersion": current_dir.name,
        "versions": serialized_versions,
        "perCase": per_case,
        "previousTargets": previous_targets,
        "buckets": {
            "newRegressions": new_regressions,
            "persistent": persistent,
        },
        "topReasons": top_reasons,
        "persistentMinFails": HISTORY_PERSISTENT_MIN_FAILS,
    }


def read_report_context(report_path: Path, *, skip: bool = False) -> dict[str, Any]:
    if skip:
        return {"status": "skipped", "reason": "Skipped by --no-report.", "excerpts": []}
    if not report_path.exists():
        return {"status": "skipped", "reason": "report.md not found in current version.", "excerpts": []}
    if not report_path.is_file():
        return {"status": "skipped", "reason": "report.md is not a regular file.", "excerpts": []}

    try:
        with report_path.open("rb") as handle:
            raw = handle.read(REPORT_MAX_BYTES + 1)
        truncated = len(raw) > REPORT_MAX_BYTES
        if truncated:
            raw = raw[:REPORT_MAX_BYTES]
        text = raw.decode("utf-8", errors="replace")
    except OSError as exc:
        return {"status": "skipped", "reason": f"Could not read report.md: {exc}", "excerpts": []}

    excerpts: list[dict[str, str]] = []
    current_heading = "Report context"
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            current_heading = stripped.lstrip("#").strip() or current_heading
            if len(excerpts) < REPORT_MAX_EXCERPTS and contains_report_keyword(current_heading):
                excerpts.append(
                    {"heading": current_heading, "text": truncate(current_heading, REPORT_EXCERPT_MAX_CHARS)}
                )
            continue
        if contains_report_keyword(stripped):
            excerpts.append({"heading": current_heading, "text": truncate(stripped, REPORT_EXCERPT_MAX_CHARS)})
        if len(excerpts) >= REPORT_MAX_EXCERPTS:
            break

    if not excerpts and text.strip():
        excerpts.append(
            {"heading": "Report context", "text": truncate(text.strip().replace("\n", " "), REPORT_EXCERPT_MAX_CHARS)}
        )

    status = "included" if excerpts else "skipped"
    reason = "Included bounded report.md excerpts."
    if truncated:
        reason += f" Read first {REPORT_MAX_BYTES} bytes only."
    if not excerpts:
        reason = "report.md had no extractable bounded context."
    return {"status": status, "reason": reason, "excerpts": excerpts[:REPORT_MAX_EXCERPTS], "truncated": truncated}


def contains_report_keyword(text: str) -> bool:
    lower = text.lower()
    return any(keyword in lower for keyword in REPORT_KEYWORDS)


def truncate(value: str, max_chars: int) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


ROLE_CANDIDATES: dict[str, list[str]] = {
    "id": ["case_id", "failure_id", "id", "source_index", "row_id", "index", "merge_id"],
    "input": ["input", "query", "question", "prompt", "task", "user_input", "instruction", "source", "base"],
    "expected": ["expected", "expected_output", "reference", "ground_truth", "answer", "label", "target"],
    "actual": ["agent_output", "actual", "actual_output", "output", "response", "prediction", "result"],
    "reason": [
        "failure_reason",
        "failure",
        "reason",
        "explanation",
        "root_cause",
        "root-cause",
        "error",
        "analysis",
        "error_message",
        "errot_message",
        "error_type",
        "agent_output_error_message",
    ],
    "log": ["agent_output_log_path", "log_path", "logs", "log", "trace_path"],
}

_ATK_ID_TOKEN_RE = re.compile(
    r"(?:atk[_\s-]?id|case[_\s-]?id|id)\s*[:=#]?\s*[`\"\']?\s*([A-Za-z0-9_\-.]+)",
    re.IGNORECASE,
)

_BARE_ID_RE = re.compile(r"`([A-Za-z0-9_\-.]{1,64})`")
