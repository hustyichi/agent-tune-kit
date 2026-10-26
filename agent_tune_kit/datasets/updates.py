from pathlib import Path
from typing import Any

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.utils.csv_io import parse_csv
from agent_tune_kit.utils.csv_io import write_csv_atomic as write_dataset_atomic
from agent_tune_kit.workspace import DATASET_PATH

from .truth import TRUTH_FIELD, apply_review, review_context, validate_review_binding
from .validation import find_duplicates, parse_dataset_csv

REVIEW_FILENAME = "dataset_review.csv"
GROUND_TRUTH_FIELD = "ground_truth"
REVIEW_REQUIRED_FIELDS = ["atk_id", "row_number", "review_feedback"]


def parse_review_csv(path: Path, dataset_rows: list[dict[str, str]]) -> tuple[list[str], list[dict[str, str]]]:
    fieldnames, rows = parse_csv(path)
    missing = [field for field in REVIEW_REQUIRED_FIELDS if field not in fieldnames]
    if missing:
        raise UserActionRequired(f"{path.as_posix()} is missing required review fields: {', '.join(missing)}")
    review_rows = [row for row in rows if (row.get("review_feedback") or "").strip() or row.get("review_status")]
    if not review_rows:
        raise UserActionRequired(f"{path.as_posix()} contains no non-empty review_feedback values.")
    dataset_ids = {row["atk_id"] for row in dataset_rows}
    unknown = sorted({(row.get("atk_id") or "").strip() for row in review_rows if row.get("atk_id") not in dataset_ids})
    if unknown:
        raise UserActionRequired(
            f"{path.as_posix()} contains review atk_id values absent from dataset.csv: {', '.join(unknown)}"
        )
    duplicates = find_duplicates((row.get("atk_id", ""), row.get("field", "")) for row in review_rows)
    if duplicates:
        raise UserActionRequired(f"{path.as_posix()} contains duplicate review targets: {duplicates}")
    return fieldnames, review_rows


def resolve_review_path(
    explicit_review_path: str | None,
    dataset_dir: Path,
    home_dir: Path,
    dataset_rows: list[dict[str, str]],
) -> Path:
    if explicit_review_path:
        review_path = Path(explicit_review_path).expanduser()
        if not review_path.exists():
            raise UserActionRequired(f"Review file {review_path.as_posix()} does not exist.")
        parse_review_csv(review_path, dataset_rows)
        return review_path

    project_review = dataset_dir / REVIEW_FILENAME
    if project_review.exists():
        parse_review_csv(project_review, dataset_rows)
        return project_review

    downloads = home_dir.expanduser() / "Downloads"
    candidates = sorted(downloads.glob("dataset_review*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    valid_candidates: list[Path] = []
    for candidate in candidates:
        try:
            parse_review_csv(candidate, dataset_rows)
        except UserActionRequired:
            continue
        valid_candidates.append(candidate)
    if not valid_candidates:
        raise UserActionRequired(
            "No valid dataset_review.csv was found in .atk/datasets/ or the Downloads directory; "
            "pass --review-path explicitly."
        )
    return valid_candidates[0]


def parse_updates_csv(
    path: Path,
    dataset_rows: list[dict[str, str]],
    review_rows: list[dict[str, str]] | None,
    field: str = GROUND_TRUTH_FIELD,
) -> list[dict[str, str]]:
    fieldnames, rows = parse_csv(path)
    missing = [name for name in ("atk_id", field) if name not in fieldnames]
    if missing:
        raise UserActionRequired(f"{path.as_posix()} is missing required update fields: {', '.join(missing)}")
    updates = [row for row in rows if (row.get(field) or "").strip()]
    if not updates:
        raise UserActionRequired(f"{path.as_posix()} contains no non-empty {field} updates.")
    dataset_ids = {row["atk_id"] for row in dataset_rows}
    review_ids = (
        {row["atk_id"] for row in review_rows if not row.get("field") or row["field"] == field}
        if review_rows is not None
        else dataset_ids
    )
    unknown = sorted({row["atk_id"] for row in updates if row["atk_id"] not in dataset_ids})
    if unknown:
        raise UserActionRequired(f"{path.as_posix()} contains unknown atk_id values: {', '.join(unknown)}")
    unreviewed = sorted({row["atk_id"] for row in updates if row["atk_id"] not in review_ids})
    if unreviewed:
        raise UserActionRequired(f"{path.as_posix()} contains updates without review feedback: {', '.join(unreviewed)}")
    duplicates = find_duplicates(row["atk_id"] for row in updates)
    if duplicates:
        raise UserActionRequired(f"{path.as_posix()} contains duplicate update atk_id values: {', '.join(duplicates)}")
    return updates


def apply_updates(
    fieldnames: list[str],
    dataset_rows: list[dict[str, str]],
    updates: list[dict[str, str]],
    field: str = GROUND_TRUTH_FIELD,
) -> tuple[list[str], list[dict[str, str]], int]:
    output_fields = list(fieldnames)
    if field not in output_fields:
        output_fields.append(field)
        for row in dataset_rows:
            row[field] = ""

    updates_by_id = {row["atk_id"]: row[field] for row in updates}
    updated_count = 0
    for row in dataset_rows:
        atk_id = row["atk_id"]
        if atk_id in updates_by_id and row[field] != updates_by_id[atk_id]:
            row[field] = updates_by_id[atk_id]
            updated_count += 1
    return output_fields, dataset_rows, updated_count


def relpath_for_output(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.expanduser().as_posix()


def build_context(
    dataset_path: Path, review_path: Path, dataset_rows: list[dict[str, str]], review_rows: list[dict[str, str]]
) -> dict[str, Any]:
    rows_by_id = {row["atk_id"]: row for row in dataset_rows}
    return {
        "dataset_path": relpath_for_output(dataset_path),
        "review_path": relpath_for_output(review_path),
        "review_count": len(review_rows),
        "rows": [
            {
                "atk_id": review_row["atk_id"],
                "row_number": review_row.get("row_number", ""),
                "review_feedback": review_row.get("review_feedback", ""),
                "dataset_row": rows_by_id.get(review_row["atk_id"], {}),
            }
            for review_row in review_rows
        ],
    }


def inspect_dataset(
    dataset_path=DATASET_PATH, *, direct=False, review_path=None, home_dir=None, field="ground_truth", input_fields=None
):
    dataset_path = Path(dataset_path)
    if not field.strip() or field in {"atk_id", TRUTH_FIELD}:
        raise UserActionRequired("Answer field must be non-empty; cannot update atk_id.")
    if not dataset_path.is_file():
        raise UserActionRequired(f"Dataset {dataset_path} is missing; run atk-dataset or atk-init first.")
    _, rows = parse_dataset_csv(dataset_path)
    trust = review_context(dataset_path, rows, field, input_fields)
    if direct:
        return {"dataset_path": str(dataset_path), "field": field, "rows": rows, **trust}
    review = resolve_review_path(
        str(review_path) if review_path else None,
        dataset_path.parent,
        Path(home_dir) if home_dir else Path.home(),
        rows,
    )
    _, feedback = parse_review_csv(review, rows)
    for row in feedback:
        if row.get("review_status") or row.get("dataset_sha256"):
            validate_review_binding(row, trust)
    return {**build_context(dataset_path, review, rows, feedback), **trust}


def update_dataset(
    updates_path=None,
    dataset_path=DATASET_PATH,
    *,
    direct=False,
    review_path=None,
    home_dir=None,
    field="ground_truth",
    input_fields=None,
):
    context = inspect_dataset(
        dataset_path, direct=direct, review_path=review_path, home_dir=home_dir, field=field, input_fields=input_fields
    )
    fields, rows = parse_dataset_csv(Path(dataset_path))
    before = [dict(row) for row in rows]
    feedback = None
    if not direct:
        _, feedback = parse_review_csv(Path(context["review_path"]), rows)
    updates = parse_updates_csv(Path(updates_path), rows, feedback, field) if updates_path else []
    if not updates and not any(r.get("review_status") for r in feedback or []):
        raise UserActionRequired("Provide answer updates or explicit content-bound review decisions.")
    output_fields, output_rows, count = apply_updates(fields, rows, updates, field)
    original = {row["atk_id"]: row for row in before}
    by_id = {row["atk_id"]: row for row in output_rows}
    for review in feedback or []:
        if review.get("review_status"):
            row = by_id[review["atk_id"]]
            apply_review(
                row,
                review,
                context["standard"],
                answer_changed=original[row["atk_id"]].get(review.get("field")) != row.get(review.get("field")),
            )
            if TRUTH_FIELD not in output_fields:
                output_fields.append(TRUTH_FIELD)
    reviewed = sum(a.get(TRUTH_FIELD, "") != b.get(TRUTH_FIELD, "") for a, b in zip(before, output_rows, strict=True))
    current = review_context(Path(dataset_path), before, field)
    if any(current[k] != context[k] for k in ("dataset_sha256", "standard")):
        raise UserActionRequired("Dataset or standard changed during review; nothing was written.")
    if count or reviewed:
        write_dataset_atomic(Path(dataset_path), output_fields, output_rows)
    result = {
        "dataset": str(dataset_path),
        "updated_rows": count,
        "reviewed_rows": reviewed,
        f"{field}_column": "existing" if field in fields else "appended",
    }
    if not direct:
        result.update(review=context["review_path"], review_rows=len(feedback))
    return result
