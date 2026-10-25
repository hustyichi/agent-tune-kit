"""Project-local execution using the shared ATK engine."""

import asyncio
import csv
import inspect
import json
import os
import runpy
import sys
import traceback
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from agent_tune_kit.datasets.validation import parse_dataset_csv
from agent_tune_kit.errors import AgentExecutionError, UserActionRequired
from agent_tune_kit.results.versions import allocate_next_results_version, list_version_dirs
from agent_tune_kit.workspace import DATASET_PATH, RUNNER_PATH

from . import logs
from .logs import capture_python_app_logging, capture_python_row_logging, capture_row_python_logs

RESULTS_FILENAME = "eval_results.csv"
FAILURE_FILENAME = "failure_cases.csv"
AGENT_OUTPUT_LOG_PATH_FIELD = "log_path"
ATK_ID_FIELD = "atk_id"


def load_failure_atk_ids(version_dir: Path) -> set[str]:
    """Load failed sample IDs from a previous failure_cases.csv."""
    failure_path = version_dir / FAILURE_FILENAME
    with failure_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise UserActionRequired(f"{failure_path} has no header row.")
        if ATK_ID_FIELD not in reader.fieldnames:
            raise UserActionRequired(f"{failure_path} is missing required {ATK_ID_FIELD} column.")
        failure_ids: set[str] = set()
        for row_number, row in enumerate(reader, start=1):
            atk_id = row.get(ATK_ID_FIELD, "").strip()
            if not atk_id:
                raise UserActionRequired(f"{failure_path} row {row_number} has an empty {ATK_ID_FIELD} value.")
            failure_ids.add(atk_id)
    if not failure_ids:
        raise UserActionRequired(f"{failure_path} contains no failure rows to rerun.")
    return failure_ids


def select_dataset_rows(
    rows: list[dict[str, str]],
    *,
    offset: int,
    limit: int | None,
) -> list[tuple[int, dict[str, str]]]:
    indexed_rows = list(enumerate(rows, start=1))
    selected = indexed_rows[offset:]
    if limit is not None:
        selected = selected[:limit]
    return selected


def select_failure_dataset_rows(
    rows: list[dict[str, str]],
    failure_atk_ids: set[str],
    *,
    offset: int,
    limit: int | None,
) -> list[tuple[int, dict[str, str]]]:
    indexed_rows = list(enumerate(rows, start=1))
    matched = [(source_index, row) for source_index, row in indexed_rows if row[ATK_ID_FIELD] in failure_atk_ids]
    matched_ids = {row[ATK_ID_FIELD] for _, row in matched}
    missing_ids = sorted(
        failure_atk_ids - matched_ids,
        key=lambda value: (0, int(value)) if value.isdigit() else (1, value),
    )
    if missing_ids:
        raise UserActionRequired(
            "Failure atk_id values are missing from the canonical dataset: " + ", ".join(missing_ids)
        )
    selected = matched[offset:]
    if limit is not None:
        selected = selected[:limit]
    return selected


def normalize_agent_output(raw_output: Any) -> str:
    """Serialize Agent output for the required agent_output column."""
    if isinstance(raw_output, str):
        return raw_output
    return json.dumps(raw_output, ensure_ascii=False, sort_keys=True)


def build_result_row(
    index: int,
    row: dict[str, str],
    *,
    version_dir: Path,
    row_logging_enabled: bool,
) -> dict[str, str]:
    output_row = dict(row)
    output_row[AGENT_OUTPUT_LOG_PATH_FIELD] = ""
    with capture_row_python_logs(version_dir, index, enabled=row_logging_enabled) as row_log_path:
        if row_log_path:
            output_row[AGENT_OUTPUT_LOG_PATH_FIELD] = row_log_path
        try:
            agent_input = build_agent_input(row)
            raw_output = call_agent(agent_input)
            if inspect.isawaitable(raw_output):
                raw_output = asyncio.run(raw_output)
            output_row["agent_output"] = normalize_agent_output(raw_output)
            output_row["output_status"] = "ok"
        except UserActionRequired:
            # Configuration/TODO/confirmation failures must stop the run; otherwise
            # an invalid runner could create a misleading successful eval_results.csv.
            raise
        except AgentExecutionError as exc:
            # Known per-row Agent failures are still useful evaluation evidence.
            output_row["agent_output"] = json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "row_number": index,
                    "traceback": traceback.format_exc(limit=8),
                },
                ensure_ascii=False,
            )
            output_row["output_status"] = "error"
            output_row["error_type"] = type(exc).__name__
            output_row["error_message"] = str(exc)
    return output_row


def get_output_fieldnames(
    source_fieldnames: list[str],
    rows: list[dict[str, str]],
) -> list[str]:
    fixed_output_fields = [
        "output_status",
        "error_type",
        "error_message",
        AGENT_OUTPUT_LOG_PATH_FIELD,
    ]
    auxiliary_fields = sorted(
        {
            key
            for row in rows
            for key in row
            if key.startswith("agent_output_") and key not in source_fieldnames and key not in fixed_output_fields
        }
    )
    return list(source_fieldnames) + ["agent_output"] + fixed_output_fields + auxiliary_fields


def open_results_writer(
    version_dir: Path,
    source_fieldnames: list[str],
    rows: list[dict[str, str]],
) -> tuple[Path, Any, csv.DictWriter[str]]:
    output_path = version_dir / RESULTS_FILENAME
    fieldnames = get_output_fieldnames(source_fieldnames, rows)
    handle = output_path.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    handle.flush()
    os.fsync(handle.fileno())
    return output_path, handle, writer


def run_rows(
    version_dir: Path,
    source_fieldnames: list[str],
    selected_rows: list[tuple[int, dict[str, str]]],
    *,
    total_rows: int,
    show_progress: bool,
    concurrency: int,
    row_logging_enabled: bool,
) -> Path:
    output_path, handle, writer = open_results_writer(
        version_dir,
        source_fieldnames,
        [row for _, row in selected_rows],
    )
    try:
        with capture_python_row_logging(enabled=row_logging_enabled):
            for completed, (source_index, result_row) in enumerate(
                execute_rows(
                    selected_rows,
                    version_dir=version_dir,
                    concurrency=concurrency,
                    row_logging_enabled=row_logging_enabled,
                ),
                start=1,
            ):
                writer.writerow(result_row)
                handle.flush()
                os.fsync(handle.fileno())
                if show_progress:
                    print(
                        f"Processed row {source_index}/{total_rows} ({completed}/{len(selected_rows)} selected)",
                        flush=True,
                        file=sys.__stderr__,
                    )
    finally:
        handle.close()
    return output_path


def execute_rows(
    selected_rows: list[tuple[int, dict[str, str]]],
    *,
    version_dir: Path,
    concurrency: int,
    row_logging_enabled: bool,
) -> Iterator[tuple[int, dict[str, str]]]:
    """Execute rows and return results as they complete.

    With concurrency=1 this preserves dataset order. With concurrency>1, rows are
    yielded in completion order so eval_results.csv remains useful during long runs.
    """
    if concurrency == 1:
        for source_index, row in selected_rows:
            yield (
                source_index,
                build_result_row(
                    source_index,
                    row,
                    version_dir=version_dir,
                    row_logging_enabled=row_logging_enabled,
                ),
            )
        return

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(
                build_result_row,
                source_index,
                row,
                version_dir=version_dir,
                row_logging_enabled=row_logging_enabled,
            ): source_index
            for source_index, row in selected_rows
        }
        for future in as_completed(futures):
            source_index = futures[future]
            yield source_index, future.result()


def load_adapter(path=RUNNER_PATH):
    path = Path(path)
    if not path.is_file():
        raise UserActionRequired(f"Runner {path} is missing; run atk-init first.")
    for root in [Path.cwd() / "src", Path.cwd()]:
        if root.is_dir():
            sys.path.insert(0, str(root))
    adapter = runpy.run_path(str(path), run_name="atk_project_adapter")
    if not callable(adapter.get("build_agent_input")) or not callable(adapter.get("call_agent")):
        raise UserActionRequired("Runner requires build_agent_input(row) and call_agent(input).")
    return adapter


def load_dataset(path=DATASET_PATH):
    fields, rows = parse_dataset_csv(Path(path))
    if {"agent_output", "log_path"} & set(fields):
        raise UserActionRequired("Dataset already contains reserved output columns; confirm a rename strategy.")
    return fields, rows


async def run_async_rows(adapter, version, fields, selected, concurrency, row_logging, progress):
    semaphore = asyncio.Semaphore(concurrency)

    async def execute(index, row):
        async with semaphore:
            output = dict(row, log_path="")
            with capture_row_python_logs(version, index, enabled=row_logging) as relative:
                output["log_path"] = relative
                try:
                    value = adapter["build_agent_input"](row)
                    if inspect.isawaitable(value):
                        value = await value
                    raw = await adapter["call_agent"](value)
                    output.update(agent_output=normalize_agent_output(raw), output_status="ok")
                except AgentExecutionError as exc:
                    output.update(
                        agent_output=json.dumps(
                            {
                                "error": type(exc).__name__,
                                "message": str(exc),
                                "row_number": index,
                                "traceback": traceback.format_exc(limit=8),
                            },
                            ensure_ascii=False,
                        ),
                        output_status="error",
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
            return index, output

    tasks = [asyncio.create_task(execute(index, row)) for index, row in selected]
    output, handle, writer = open_results_writer(version, fields, [row for _, row in selected])
    try:
        with capture_python_row_logging(enabled=row_logging):
            for completed, task in enumerate(asyncio.as_completed(tasks), 1):
                index, row = await task
                writer.writerow(row)
                handle.flush()
                os.fsync(handle.fileno())
                if progress:
                    print(
                        f"Processed row {index} ({completed}/{len(selected)} selected)", file=sys.__stderr__, flush=True
                    )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        handle.close()
    return output


def run_evaluation(*, check=False, limit=None, offset=0, concurrency=1, only_failures=False, no_progress=False):
    if offset < 0 or (limit is not None and limit < 0) or concurrency < 1:
        raise UserActionRequired("limit/offset must be nonnegative and concurrency must be at least 1.")
    adapter = load_adapter()
    fields, rows = load_dataset()
    if check:
        if rows:
            value = adapter["build_agent_input"](rows[0])
            if inspect.isawaitable(value):
                asyncio.run(value)
        return {"checked": True, "rows": len(rows)}
    if only_failures:
        candidates = [path for _, path in list_version_dirs() if (path / FAILURE_FILENAME).is_file()]
        if not candidates:
            raise UserActionRequired("No prior failure_cases.csv; run atk-diagnose first.")
        ids = load_failure_atk_ids(candidates[-1])
        selected = select_failure_dataset_rows(rows, ids, offset=offset, limit=limit)
    else:
        selected = select_dataset_rows(rows, offset=offset, limit=limit)
    version = allocate_next_results_version()
    enabled = adapter.get("PYTHON_LOGGING_CAPTURE_ENABLED", False)
    concurrent_logs = adapter.get("CONCURRENT_ROW_LOGGING_ENABLED", True)
    row_logging = enabled and (concurrency == 1 or concurrent_logs)
    if enabled and not row_logging:
        print(
            "Concurrent row-level Python logging capture is disabled; no per-row log files will be created for --concurrency > 1.",
            file=sys.__stderr__,
        )
    for name in ["ROW_LOGGER_NAMES", "ROW_LOG_LEVEL", "ROW_LOG_FORMAT"]:
        if name in adapter:
            setattr(logs, name, adapter[name])
    global build_agent_input, call_agent
    build_agent_input, call_agent = adapter["build_agent_input"], adapter["call_agent"]
    with ExitStack() as stack:
        if enabled:
            stack.enter_context(capture_python_app_logging(version, enabled=True))
        else:
            fallback = stack.enter_context((version / "app.log").open("w", encoding="utf-8"))
            stack.enter_context(redirect_stdout(fallback))
            stack.enter_context(redirect_stderr(fallback))
        if inspect.iscoroutinefunction(call_agent):
            output = asyncio.run(
                run_async_rows(adapter, version, fields, selected, concurrency, row_logging, not no_progress)
            )
        else:
            output = run_rows(
                version,
                fields,
                selected,
                total_rows=len(rows),
                show_progress=not no_progress,
                concurrency=concurrency,
                row_logging_enabled=row_logging,
            )
    return {"version": version.name, "rows": len(selected), "output": str(output), "row_logs": row_logging}
