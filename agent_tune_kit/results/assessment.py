"""Validate declared judgment grounds without claiming to verify their semantics."""

import csv
import hashlib
import json
from pathlib import Path

from agent_tune_kit.datasets.truth import TRUTH_FIELD, digest, truth_records, truth_status
from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.utils.csv_io import parse_csv
from agent_tune_kit.utils.files import write_text_atomic


def parse_assessment(raw):
    if not raw:
        return None
    try:
        data = json.loads(raw)
        standard, judge = data["standard"], data["judge"]
        if (
            data.get("schema") != 1
            or data["basis"] not in {"truth", "independent"}
            or not isinstance(standard["text"], str)
            or standard["sha256"] != digest(standard["text"])
            or judge["kind"] not in {"rule", "semantic"}
            or not judge["identity"].strip()
        ):
            raise ValueError("Invalid standard or judge")
        if judge["kind"] == "rule":
            if not judge["source"].strip() or judge["sha256"] != digest(judge["source"]):
                raise ValueError("Rule source snapshot does not match hash")
        elif not judge["instructions"].strip():
            raise ValueError("Semantic judge instructions are required")
        for key in ("input_fields", "truth_fields"):
            values = data[key]
            if (
                not isinstance(values, list)
                or any(not isinstance(v, str) or not v for v in values)
                or len(set(values)) != len(values)
                or any(v in {TRUTH_FIELD, "atk_id", "agent_output"} for v in values)
            ):
                raise ValueError(f"Invalid {key}")
        if not data["input_fields"] or set(data["input_fields"]) & set(data["truth_fields"]):
            raise ValueError("Input fields must be explicit and exclude reference fields")
        if data["basis"] == "truth" and not data["truth_fields"]:
            raise ValueError("Truth-dependent judgments require reference fields")
        if data["basis"] == "independent" and not standard["text"].strip():
            raise ValueError("Independent judgments require an explicit standard")
        return data
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise UserActionRequired(f"Invalid assessment: {exc}") from exc


def validate_assessment(source_row, judgment):
    try:
        data = parse_assessment(judgment.get("assessment"))
        if data is None:
            return not source_row.get(TRUTH_FIELD) or judgment.get("verdict") == "unknown"
        if any(k not in source_row for k in data["input_fields"] + data["truth_fields"]):
            return False
        reference = data.get("reference_snapshot", source_row)
        if (
            not isinstance(reference, dict)
            or reference.get("atk_id") != source_row.get("atk_id")
            or any(reference.get(k) != source_row.get(k) for k in data["input_fields"])
        ):
            return False
        if judgment.get("verdict") == "unknown":
            return True
        if data["basis"] == "truth" and any(
            truth_status(reference, field, standard=data["standard"])["status"] != "confirmed"
            for field in data["truth_fields"]
        ):
            return False
        calibration = data.get("calibration")
        if calibration:
            path = Path(calibration["path"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != calibration["sha256"]:
                return False
        return True
    except (UserActionRequired, OSError, KeyError, TypeError):
        return False


def truth_counts(rows):
    counts = dict.fromkeys(("confirmed", "unreviewed", "pending", "disputed", "stale", "invalid"), 0)
    for row in rows:
        try:
            statuses = {truth_status(row, field)["status"] for field in truth_records(row)}
            state = next(
                (s for s in ("stale", "disputed", "pending") if s in statuses),
                "confirmed" if statuses else "unreviewed",
            )
        except UserActionRequired:
            state = "invalid"
        counts[state] += 1
    return counts


def archive_judgments(path):
    path = Path(path)
    if not path.is_file():
        return
    fields, rows = parse_csv(path)
    if not rows:
        return
    data = path.read_bytes()
    target = path.parent / "judgment-history" / (hashlib.sha256(data).hexdigest() + ".csv")
    if target.exists():
        if target.read_bytes() != data:
            raise UserActionRequired("Judgment history was changed; refusing to overwrite it.")
    else:
        write_text_atomic(target, data.decode("utf-8"))


def has_assessments(version_dir):
    for name in ("judgments.csv", "failure_cases.csv", "eval_results.csv"):
        path = Path(version_dir) / name
        if path.is_file():
            with path.open(encoding="utf-8-sig", newline="") as handle:
                fields = next(csv.reader(handle), [])
            if "assessment" in fields or TRUTH_FIELD in fields:
                return True
    return False


def compare_assessments(current, previous, ids):
    def load(directory):
        if directory is None:
            return {}, {}
        result, judgments = Path(directory) / "eval_results.csv", Path(directory) / "judgments.csv"
        return (
            {r["atk_id"]: r for r in parse_csv(result)[1]} if result.is_file() else {},
            {r["atk_id"]: r for r in parse_csv(judgments)[1]} if judgments.is_file() else {},
        )

    now, verdicts = load(current)
    old, old_verdicts = load(previous)
    differences = {}
    for aid in ids:
        changes = []
        try:
            a = parse_assessment(verdicts.get(aid, {}).get("assessment"))
            b = parse_assessment(old_verdicts.get(aid, {}).get("assessment"))
            if a is None or b is None or aid not in now or aid not in old:
                changes.append("missing_assessment_or_sample")
            else:
                for key in ("standard", "judge", "input_fields", "truth_fields", "basis", "calibration"):
                    if a.get(key) != b.get(key):
                        changes.append(key)
                for key in sorted(set(a["input_fields"] + b["input_fields"] + a["truth_fields"] + b["truth_fields"])):
                    if a.get("reference_snapshot", now[aid]).get(key) != b.get("reference_snapshot", old[aid]).get(key):
                        changes.append("field:" + key)
                if not validate_assessment(now[aid], verdicts[aid]) or not validate_assessment(
                    old[aid], old_verdicts[aid]
                ):
                    changes.append("invalid_assessment")
        except UserActionRequired:
            changes.append("invalid_assessment")
        if changes:
            differences[aid] = changes
    return differences
