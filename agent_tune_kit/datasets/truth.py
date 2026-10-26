"""Content-bound review declarations; integrity never implies domain correctness."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from agent_tune_kit.errors import UserActionRequired

TRUTH_FIELD = "atk_truth"
REVIEW_STATES = {"pending", "confirmed", "disputed"}
STANDARD_SECTIONS = {"Agent Behavior Standard", "Ground Truth Standard"}
INPUT_CANDIDATES = ("input", "query", "question", "prompt", "task", "user_input", "instruction")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def standard_snapshot(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if path.name == "context.md":
        lines, active = [], False
        for line in text.splitlines():
            if line.startswith("## "):
                active = line[3:].strip() in STANDARD_SECTIONS
            if active:
                lines.append(line.rstrip())
        text = "\n".join(lines).strip()
    return {"text": text, "sha256": digest(text)}


def validate_inputs(row, field, inputs):
    if field not in row or field in {TRUTH_FIELD, "atk_id"}:
        raise UserActionRequired(f"Unknown or reserved answer field: {field}")
    if (
        not isinstance(inputs, list)
        or not inputs
        or any(not isinstance(k, str) for k in inputs)
        or len(set(inputs)) != len(inputs)
        or any(k not in row or k in {field, TRUTH_FIELD, "atk_id"} for k in inputs)
    ):
        raise UserActionRequired(
            "Review requires explicit, unique business input fields, excluding answer and ATK metadata."
        )


def subject_digest(row, field, inputs, standard, *, source="", evidence=""):
    validate_inputs(row, field, inputs)
    return digest(
        {
            "atk_id": row.get("atk_id"),
            "field": field,
            "answer": row[field],
            "inputs": {k: row[k] for k in inputs},
            "standard": standard["sha256"],
            "source": source,
            "evidence": evidence,
        }
    )


def truth_records(row):
    raw = row.get(TRUTH_FIELD, "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if data.get("schema") != 1 or not isinstance(data.get("fields"), dict):
            raise ValueError("Unknown truth schema")
        for field, record in data["fields"].items():
            validate_inputs(row, field, record.get("input_fields"))
            standard = record["standard"]
            if (
                record["status"] not in REVIEW_STATES
                or not isinstance(standard["text"], str)
                or standard["sha256"] != digest(standard["text"])
                or not isinstance(record["subject_sha256"], str)
                or len(record["subject_sha256"]) != 64
            ):
                raise ValueError("Invalid review binding")
            if record["status"] == "confirmed" and any(
                not isinstance(record.get(k), str) or not record[k].strip()
                for k in ("source", "evidence", "reviewer", "reviewed_at")
            ):
                raise ValueError("Confirmed reviews require source, evidence, reviewer and time")
        return data["fields"]
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise UserActionRequired(f"Invalid atk_truth for {row.get('atk_id')}: {exc}") from exc


def truth_status(row, field, *, standard=None):
    record = truth_records(row).get(field)
    if record is None:
        return {"field": field, "status": "unreviewed", "reason": "No review recorded"}
    current = standard if standard is not None else record["standard"]
    stale = (
        subject_digest(
            row,
            field,
            record["input_fields"],
            current,
            source=record.get("source", ""),
            evidence=record.get("evidence", ""),
        )
        != record["subject_sha256"]
    )
    return {
        "field": field,
        "status": "stale" if stale else record["status"],
        "reason": "Input, answer or relevant standard changed" if stale else "Recorded review declaration",
        "record": record,
    }


def review_context(path, rows, field, input_fields=None):
    path = Path(path)
    standard = standard_snapshot(path.parent.parent / "context.md")
    inputs = input_fields or next(([k] for k in INPUT_CANDIDATES if rows and k in rows[0]), [])
    truth = []
    for row in rows:
        state = truth_status(row, field, standard=standard)
        state["atk_id"] = row.get("atk_id", "")
        state["input_fields"] = input_fields or state.get("record", {}).get("input_fields", inputs)
        truth.append(state)
    return {
        "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "dataset_scope": digest(str(path.resolve())),
        "standard": standard,
        "truth": truth,
        "truth_counts": {
            s: sum(r["status"] == s for r in truth) for s in ("unreviewed", "pending", "confirmed", "disputed", "stale")
        },
    }


def validate_review_binding(review, context):
    for key in ("dataset_sha256", "dataset_scope"):
        if review.get(key) != context[key]:
            raise UserActionRequired("Review is stale or belongs to another dataset; inspect the current data again.")
    if review.get("standard_sha256") != context["standard"]["sha256"]:
        raise UserActionRequired("Review standard changed; inspect the current standard again.")


def apply_review(row, review, standard, *, answer_changed=False):
    field = review.get("field", "")
    try:
        inputs = json.loads(review.get("input_fields", "[]"))
    except (ValueError, TypeError) as exc:
        raise UserActionRequired("input_fields must be a JSON array.") from exc
    validate_inputs(row, field, inputs)
    status = review.get("review_status")
    if status not in REVIEW_STATES:
        raise UserActionRequired("review_status must be pending, confirmed or disputed.")
    if status == "confirmed" and (
        not row[field].strip() or any(not review.get(k, "").strip() for k in ("source", "evidence", "reviewer"))
    ):
        raise UserActionRequired("Confirmation requires a non-empty answer, source, evidence and reviewer.")
    records = truth_records(row)
    record = {
        "status": "pending" if answer_changed else status,
        "input_fields": inputs,
        "source": review.get("source", ""),
        "evidence": review.get("evidence", ""),
        "reviewer": review.get("reviewer", ""),
        "standard": standard,
        "subject_sha256": subject_digest(
            row, field, inputs, standard, source=review.get("source", ""), evidence=review.get("evidence", "")
        ),
    }
    old = records.get(field, {})
    # Reapplying the same declaration does not manufacture a new review event.
    record["reviewed_at"] = old.get("reviewed_at") if all(old.get(k) == v for k, v in record.items()) else None
    record["reviewed_at"] = record["reviewed_at"] or datetime.now(UTC).isoformat()
    records[field] = record
    row[TRUTH_FIELD] = json.dumps({"schema": 1, "fields": records}, ensure_ascii=False, sort_keys=True)
