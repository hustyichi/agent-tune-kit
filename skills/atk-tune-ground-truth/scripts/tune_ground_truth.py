#!/usr/bin/env python3
"""Apply LLM-produced ground_truth updates based on dataset review feedback.

The script owns deterministic file handling: resolve the exported review CSV,
validate it against the current canonical dataset, and atomically write approved
ground_truth updates back to `.atk/datasets/dataset.csv`.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

DATASETS_DIR = Path(".atk/datasets")
DATASET_FILENAME = "dataset.csv"
REVIEW_FILENAME = "dataset_review.csv"
GROUND_TRUTH_FIELD = "ground_truth"
REVIEW_REQUIRED_FIELDS = ["atk_id", "row_number", "review_feedback"]
UPDATE_REQUIRED_FIELDS = ["atk_id", GROUND_TRUTH_FIELD]


class UserActionRequired(RuntimeError):
    """A user-fixable input or confirmation blocker."""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tune dataset ground_truth from exported dataset review feedback.")
    parser.add_argument("--dataset-path", default=str(DATASETS_DIR / DATASET_FILENAME))
    parser.add_argument("--review-path", default=None)
    parser.add_argument("--updates-path", default=None)
    parser.add_argument("--home-dir", default=None, help="Override home directory for Downloads lookup.")
    parser.add_argument(
        "--dump-context",
        action="store_true",
        help="Print dataset/review context JSON for LLM update generation without writing dataset.csv.",
    )
    return parser.parse_args(argv)


def parse_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, strict=True)
            fieldnames = list(reader.fieldnames or [])
            if not fieldnames or all(not (name or "").strip() for name in fieldnames):
                raise UserActionRequired(f"{path.as_posix()} is empty or missing a header.")
            if any(not (name or "").strip() for name in fieldnames):
                raise UserActionRequired(f"{path.as_posix()} contains blank header names.")
            if len(set(fieldnames)) != len(fieldnames):
                raise UserActionRequired(f"{path.as_posix()} contains duplicate headers.")
            rows: list[dict[str, str]] = []
            for raw_row in reader:
                if None in raw_row:
                    extra_values = raw_row.pop(None)
                    if extra_values:
                        raise UserActionRequired(f"{path.as_posix()} contains extra values beyond the header.")
                rows.append({field: raw_row.get(field, "") or "" for field in fieldnames})
    except csv.Error as exc:
        raise UserActionRequired(f"{path.as_posix()} could not be parsed as CSV: {exc}") from exc
    return fieldnames, rows


def parse_dataset_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    fieldnames, rows = parse_csv(path)
    validate_dataset_rows(fieldnames, rows)
    return fieldnames, rows


def validate_dataset_rows(fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    if "atk_id" not in fieldnames:
        raise UserActionRequired("dataset.csv is missing required atk_id column.")
    seen: set[str] = set()
    invalid: list[str] = []
    for row in rows:
        atk_id = (row.get("atk_id") or "").strip()
        if not atk_id or not atk_id.isdigit() or int(atk_id) <= 0 or atk_id in seen:
            invalid.append(atk_id or "<empty>")
        seen.add(atk_id)
    if invalid:
        sample = ", ".join(invalid[:5])
        raise UserActionRequired(f"dataset.csv has invalid or duplicate atk_id values: {sample}")


def parse_review_csv(path: Path, dataset_rows: list[dict[str, str]]) -> tuple[list[str], list[dict[str, str]]]:
    fieldnames, rows = parse_csv(path)
    missing = [field for field in REVIEW_REQUIRED_FIELDS if field not in fieldnames]
    if missing:
        raise UserActionRequired(f"{path.as_posix()} is missing required review fields: {', '.join(missing)}")
    review_rows = [row for row in rows if (row.get("review_feedback") or "").strip()]
    if not review_rows:
        raise UserActionRequired(f"{path.as_posix()} contains no non-empty review_feedback values.")
    dataset_ids = {row["atk_id"] for row in dataset_rows}
    unknown = sorted({(row.get("atk_id") or "").strip() for row in review_rows if row.get("atk_id") not in dataset_ids})
    if unknown:
        raise UserActionRequired(f"{path.as_posix()} contains review atk_id values absent from dataset.csv: {', '.join(unknown)}")
    duplicates = find_duplicates((row.get("atk_id") or "").strip() for row in review_rows)
    if duplicates:
        raise UserActionRequired(f"{path.as_posix()} contains duplicate review atk_id values: {', '.join(duplicates)}")
    return fieldnames, review_rows


def find_duplicates(values: Any) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


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


def parse_updates_csv(path: Path, dataset_rows: list[dict[str, str]], review_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    fieldnames, rows = parse_csv(path)
    missing = [field for field in UPDATE_REQUIRED_FIELDS if field not in fieldnames]
    if missing:
        raise UserActionRequired(f"{path.as_posix()} is missing required update fields: {', '.join(missing)}")
    updates = [row for row in rows if (row.get(GROUND_TRUTH_FIELD) or "").strip()]
    if not updates:
        raise UserActionRequired(f"{path.as_posix()} contains no non-empty ground_truth updates.")
    dataset_ids = {row["atk_id"] for row in dataset_rows}
    review_ids = {row["atk_id"] for row in review_rows}
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
) -> tuple[list[str], list[dict[str, str]], int]:
    output_fields = list(fieldnames)
    if GROUND_TRUTH_FIELD not in output_fields:
        output_fields.append(GROUND_TRUTH_FIELD)
        for row in dataset_rows:
            row[GROUND_TRUTH_FIELD] = ""

    updates_by_id = {row["atk_id"]: row[GROUND_TRUTH_FIELD] for row in updates}
    updated_count = 0
    for row in dataset_rows:
        atk_id = row["atk_id"]
        if atk_id in updates_by_id:
            row[GROUND_TRUTH_FIELD] = updates_by_id[atk_id]
            updated_count += 1
    return output_fields, dataset_rows, updated_count


def write_dataset_atomic(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_path, path)
    except Exception:
        with suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


def relpath_for_output(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.expanduser().as_posix()


def build_context(dataset_path: Path, review_path: Path, dataset_rows: list[dict[str, str]], review_rows: list[dict[str, str]]) -> dict[str, Any]:
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


def run(argv: list[str]) -> int:
    args = parse_args(argv)
    dataset_path = Path(args.dataset_path)
    if not dataset_path.exists():
        raise UserActionRequired(f"Dataset {dataset_path.as_posix()} is missing; run atk-build-dataset or atk-init first.")
    if not dataset_path.is_file():
        raise UserActionRequired(f"Dataset path {dataset_path.as_posix()} is not a file; fix the dataset.")

    fieldnames, dataset_rows = parse_dataset_csv(dataset_path)
    home_dir = Path(args.home_dir) if args.home_dir else Path.home()
    review_path = resolve_review_path(args.review_path, dataset_path.parent, home_dir, dataset_rows)
    _review_fields, review_rows = parse_review_csv(review_path, dataset_rows)

    if args.dump_context or not args.updates_path:
        print(json.dumps(build_context(dataset_path, review_path, dataset_rows, review_rows), ensure_ascii=False, indent=2))
        if not args.updates_path:
            return 0

    updates = parse_updates_csv(Path(args.updates_path), dataset_rows, review_rows)
    output_fields, output_rows, updated_count = apply_updates(fieldnames, dataset_rows, updates)
    write_dataset_atomic(dataset_path, output_fields, output_rows)
    print(f"dataset={relpath_for_output(dataset_path)}")
    print(f"review={relpath_for_output(review_path)}")
    print(f"review_rows={len(review_rows)}")
    print(f"updated_rows={updated_count}")
    print(f"ground_truth_column={'existing' if GROUND_TRUTH_FIELD in fieldnames else 'appended'}")
    return 0


def main() -> None:
    try:
        raise SystemExit(run(sys.argv[1:]))
    except UserActionRequired as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
