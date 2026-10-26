from pathlib import Path

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.utils.csv_io import parse_csv, write_csv_atomic
from agent_tune_kit.workspace import DATASET_PATH

from .truth import TRUTH_FIELD
from .validation import validate_dataset_rows


def prepare_dataset(source, dataset_path=DATASET_PATH, *, append=False, overwrite=False):
    source, target = Path(source), Path(dataset_path)
    fields, rows = parse_csv(source)
    old_fields, old_rows = parse_csv(target) if target.exists() else ([], [])
    if append and not target.exists():
        raise UserActionRequired("Append requires an existing canonical dataset.")
    if append:
        validate_dataset_rows(old_fields, old_rows)
        if set(fields) - {"atk_id", TRUTH_FIELD} != set(old_fields) - {"atk_id", TRUTH_FIELD}:
            raise UserActionRequired("Append fields differ; confirm the field mapping before appending.")
        if TRUTH_FIELD in fields and TRUTH_FIELD not in old_fields:
            old_fields = old_fields + [TRUTH_FIELD]
    if "atk_id" not in fields:
        fields = fields + ["atk_id"]
        start = max((int(row["atk_id"]) for row in old_rows), default=0) + 1 if append else 1
        for index, row in enumerate(rows, start):
            row["atk_id"] = str(index)
    validate_dataset_rows(fields, rows)
    if append:
        fields, rows = old_fields, old_rows + rows
        validate_dataset_rows(fields, rows)
    if old_fields == fields and old_rows == rows:
        return {"dataset": str(target), "rows": len(rows), "reused": True}
    if target.exists() and not (append or overwrite):
        raise UserActionRequired(
            f"Refusing to overwrite different canonical content: {target}; confirm and use --overwrite."
        )
    write_csv_atomic(target, fields, rows)
    return {"dataset": str(target), "rows": len(rows), "reused": False}
