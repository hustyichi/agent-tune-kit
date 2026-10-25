from pathlib import Path
from typing import Any

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.utils.csv_io import parse_csv


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


def parse_dataset_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    fieldnames, rows = parse_csv(path)
    validate_dataset_rows(fieldnames, rows)
    return fieldnames, rows


def find_duplicates(values: Any) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)
