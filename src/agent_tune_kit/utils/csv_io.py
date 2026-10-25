import csv
from pathlib import Path

from agent_tune_kit.errors import UserActionRequired

from .files import atomic_text_writer


def parse_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
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


def write_csv_atomic(path, fieldnames, rows):
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
