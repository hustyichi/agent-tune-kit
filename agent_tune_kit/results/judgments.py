"""Validated judgment records and interruption-safe failure projections."""

import csv
import hashlib
import io
from pathlib import Path

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.utils.csv_io import parse_csv, write_csv_atomic
from agent_tune_kit.utils.files import read_text_bounded
from agent_tune_kit.workspace import RESULTS_DIR

from .assessment import archive_judgments, validate_assessment
from .versions import require_current_file, resolve_current_version

JUDGMENT_FIELDS = ["atk_id", "verdict", "reason", "results_sha256"]
VERDICTS = {"passed", "failed", "unknown"}


def read_judgments(version_dir, *, max_bytes=64 * 1024 * 1024):
    """Old, ambiguous and stale records provide no evidence of a pass."""
    version_dir = Path(version_dir)
    try:
        result_path = version_dir / "eval_results.csv"
        if result_path.stat().st_size > max_bytes:
            return {}
        with result_path.open("rb") as handle:
            result_bytes = handle.read(max_bytes + 1)
        if len(result_bytes) > max_bytes:
            return {}
        digest = hashlib.sha256(result_bytes).hexdigest()
        source = csv.DictReader(io.StringIO(result_bytes.decode("utf-8-sig")), strict=True)
        source_fields = source.fieldnames or []
        source_rows = list(source)
        ids = [(row.get("atk_id") or "").strip() for row in source_rows]
        by_id = dict(zip(ids, source_rows, strict=True))
        if (
            "atk_id" not in source_fields
            or len(source_fields) != len(set(source_fields))
            or any(not aid for aid in ids)
            or len(set(ids)) != len(ids)
        ):
            return {}
        text, truncated = read_text_bounded(version_dir / "judgments.csv", max_bytes)
        if truncated:
            return {}
        reader = csv.DictReader(io.StringIO(text), strict=True)
        fields = reader.fieldnames or []
        if not set(JUDGMENT_FIELDS) <= set(fields) or len(set(fields)) != len(fields):
            return {}
        verdicts, duplicates = {}, set()
        for row in reader:
            aid = (row.get("atk_id") or "").strip()
            if not aid:
                continue
            if aid in verdicts:
                duplicates.add(aid)
            verdicts[aid] = (
                row.get("verdict")
                if (
                    row.get("verdict") in VERDICTS
                    and row.get("results_sha256") == digest
                    and (row.get("reason") or "").strip()
                    and None not in row
                    and validate_assessment(by_id.get(aid, {}), row)
                )
                else "unknown"
            )
        for aid in duplicates:
            verdicts[aid] = "unknown"
        return verdicts
    except (OSError, UnicodeError, csv.Error):
        return {}


def apply_judgments(judgments_path, *, source_results=None, results_dir=RESULTS_DIR):
    current = resolve_current_version(Path(results_dir))
    result_path = require_current_file(current, "eval_results.csv")
    if source_results is not None and Path(source_results).resolve() != result_path.resolve():
        raise UserActionRequired("Source results no longer belong to the current version; recheck before judging.")
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    fields, rows = parse_csv(result_path)
    ids = [row.get("atk_id", "") for row in rows]
    if (
        "atk_id" not in fields
        or "agent_output" not in fields
        or any(not aid for aid in ids)
        or len(set(ids)) != len(ids)
    ):
        raise UserActionRequired("Results require unique non-empty atk_id and agent_output columns.")
    judgment_fields, judgments = parse_csv(Path(judgments_path))
    if not set(JUDGMENT_FIELDS) <= set(judgment_fields):
        raise UserActionRequired("Judgments require atk_id,verdict,reason,results_sha256.")
    judged_ids = [row["atk_id"] for row in judgments]
    if len(set(judged_ids)) != len(judged_ids) or set(judged_ids) != set(ids):
        raise UserActionRequired("Judgments must cover every executed ID exactly once, including explicit unknowns.")
    if any(
        row["verdict"] not in VERDICTS or not row["reason"].strip() or row["results_sha256"] != digest
        for row in judgments
    ):
        raise UserActionRequired("Invalid or stale judgment; preserve the source hash and recheck the criteria.")
    verdicts = {row["atk_id"]: row["verdict"] for row in judgments}
    by_id = {row["atk_id"]: row for row in rows}
    if any(not validate_assessment(by_id[row["atk_id"]], row) for row in judgments):
        raise UserActionRequired("Unsupported assessment: confirm the applicable truth/standard or record unknown.")
    explanations = {row["atk_id"]: row for row in judgments}
    extra_fields = [field for field in judgment_fields if field not in JUDGMENT_FIELDS and field not in fields]
    previous = {}
    if (current / "failure_cases.csv").is_file():
        old_fields, old_rows = parse_csv(current / "failure_cases.csv")
        extra_fields += [field for field in old_fields if field not in fields and field not in extra_fields]
        previous = {row.get("atk_id"): row for row in old_rows}
    failures = [
        dict(
            row,
            **{
                field: explanations[row["atk_id"]].get(field, previous.get(row["atk_id"], {}).get(field, ""))
                for field in extra_fields
            },
        )
        for row in rows
        if verdicts[row["atk_id"]] == "failed"
    ]
    if (
        resolve_current_version(Path(results_dir)) != current
        or hashlib.sha256(result_path.read_bytes()).hexdigest() != digest
    ):
        raise UserActionRequired("Results changed during diagnosis; nothing was written.")
    # Invalidate old passes before replacing failures; interruption stays conservative.
    stored_fields = list(JUDGMENT_FIELDS)
    if "assessment" in judgment_fields:
        stored_fields.append("assessment")
    archive_judgments(current / "judgments.csv")
    write_csv_atomic(current / "judgments.csv", stored_fields, [])
    write_csv_atomic(current / "failure_cases.csv", fields + extra_fields, failures)
    write_csv_atomic(current / "judgments.csv", stored_fields, judgments)
    return {"version": current.name, "failed": len(failures), "judged": len(judgments), "results_sha256": digest}
