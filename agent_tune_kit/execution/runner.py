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
from contextlib import ExitStack, redirect_stderr, redirect_stdout, suppress
from pathlib import Path
from typing import Any

from agent_tune_kit.datasets.validation import find_duplicates, parse_dataset_csv
from agent_tune_kit.errors import AgentExecutionError, UserActionRequired
from agent_tune_kit.results.summary import execution_summary
from agent_tune_kit.results.versions import allocate_next_results_version, resolve_evaluation_version, write_occupation
from agent_tune_kit.utils.csv_io import parse_csv
from agent_tune_kit.workspace import DATASET_PATH, RUNNER_PATH

from . import logs
from .logs import capture_python_app_logging, capture_python_row_logging, capture_row_python_logs

RESULTS_FILENAME = "eval_results.csv"
FAILURE_FILENAME = "failure_cases.csv"
AGENT_OUTPUT_LOG_PATH_FIELD = "log_path"
ATK_ID_FIELD = "atk_id"
OUTPUT_FIELDS = ("agent_output", "output_status", "error_type", "error_message", AGENT_OUTPUT_LOG_PATH_FIELD)


def business_row(row):
    return {key: value for key, value in row.items() if key != "atk_truth"}


def load_failure_atk_ids(version_dir: Path) -> set[str]:
    """Load failed sample IDs from a previous failure_cases.csv."""
    from agent_tune_kit.results.summary import effective_verdicts
    from agent_tune_kit.results.versions import run_kind

    if run_kind(version_dir) != "legacy":
        ids = {aid for aid, verdict in effective_verdicts(version_dir).items() if verdict == "failed"}
        if not ids:
            raise UserActionRequired("No valid failed judgments; inspect evidence before rerunning.")
        return ids
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
    ids: list[str] | None = None,
) -> list[tuple[int, dict[str, str]]]:
    indexed_rows = list(enumerate(rows, start=1))
    if ids is not None:
        requested = set(ids)
        missing = requested - {row[ATK_ID_FIELD] for row in rows}
        if not requested or missing:
            raise UserActionRequired(
                "Requested atk_id values are empty or missing from the dataset: " + ", ".join(sorted(missing))
            )
        indexed_rows = [(index, row) for index, row in indexed_rows if row[ATK_ID_FIELD] in requested]
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
            agent_input = build_agent_input(business_row(row))
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
    auxiliary_fields = sorted(
        {
            key
            for row in rows
            for key in row
            if key.startswith("agent_output_") and key not in source_fieldnames and key not in OUTPUT_FIELDS
        }
    )
    return list(source_fieldnames) + list(OUTPUT_FIELDS) + auxiliary_fields


def open_results_writer(
    version_dir: Path,
    source_fieldnames: list[str],
    rows: list[dict[str, str]],
) -> tuple[Path, Any, csv.DictWriter[str]]:
    output_path = version_dir / RESULTS_FILENAME
    fieldnames = get_output_fieldnames(source_fieldnames, rows)
    duplicates = find_duplicates(fieldnames)
    if duplicates:
        raise UserActionRequired(
            f"Cannot write {output_path}: duplicate output columns: {', '.join(duplicates)}. "
            "Confirm a rename strategy before evaluation."
        )
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
    conflicts = sorted(set(fields) & set(OUTPUT_FIELDS))
    if conflicts:
        raise UserActionRequired(
            f"{path} contains reserved ATK output columns: {', '.join(conflicts)}. "
            "Confirm a rename strategy and update adapter/rule field references before evaluation. "
            "No automatic renaming was applied."
        )
    return fields, rows


async def run_async_rows(adapter, version, fields, selected, concurrency, row_logging, progress):
    semaphore = asyncio.Semaphore(concurrency)

    async def execute(index, row):
        async with semaphore:
            output = dict(row, log_path="")
            with capture_row_python_logs(version, index, enabled=row_logging) as relative:
                output["log_path"] = relative
                try:
                    value = adapter["build_agent_input"](business_row(row))
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


def _run_v1(*, check=False, limit=None, offset=0, concurrency=1, only_failures=False, no_progress=False, ids=None):
    if offset < 0 or (limit is not None and limit < 0) or concurrency < 1:
        raise UserActionRequired("limit/offset must be nonnegative and concurrency must be at least 1.")
    adapter = load_adapter()
    fields, rows = load_dataset()
    if only_failures:
        failure_ids = load_failure_atk_ids(resolve_evaluation_version())
        selected = select_failure_dataset_rows(rows, failure_ids, offset=offset, limit=limit)
    else:
        selected = select_dataset_rows(rows, offset=offset, limit=limit, ids=ids)
    if check:
        if selected:
            value = adapter["build_agent_input"](business_row(selected[0][1]))
            if inspect.isawaitable(value):
                asyncio.run(value)
        return {"checked": True, "rows": len(selected)}
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
    return {
        "version": version.name,
        "rows": len(selected),
        "output": str(output),
        "row_logs": row_logging,
        **execution_summary(*parse_csv(output)),
    }


async def invoke(function, *args, **kwargs):
    if inspect.iscoroutinefunction(function):
        return await function(*args, **kwargs)
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        value = await asyncio.shield(task)
    except asyncio.CancelledError:
        # A live synchronous call may still emit evidence; keep its observer alive until it returns.
        await task
        raise
    return await value if inspect.isawaitable(value) else value


def plan_items(adapter, selected, all_rows):
    rows = [business_row(row) for _, row in selected]
    planner = adapter.get("plan_executions")
    items = (
        planner(rows, [business_row(row) for row in all_rows])
        if planner
        else [{"sample_ids": [row[ATK_ID_FIELD]], "source": row, "context_ids": [row[ATK_ID_FIELD]]} for row in rows]
    )
    if not isinstance(items, list):
        raise UserActionRequired("plan_executions must return a list.")
    seen, valid = [], {row[ATK_ID_FIELD] for row in all_rows}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("sample_ids"), list) or "source" not in item:
            raise UserActionRequired("Each execution needs sample_ids and source.")
        ids = item["sample_ids"]
        context = item.get("context_ids", ids)
        if not ids or any(not isinstance(aid, str) or aid not in valid for aid in ids + context):
            raise UserActionRequired("Execution sample/context identity is invalid.")
        if len(ids) > 1 and not callable(adapter.get("map_agent_output")):
            raise UserActionRequired("Shared executions require map_agent_output.")
        seen.extend(ids)
    if len(seen) != len(set(seen)) or set(seen) != {row[ATK_ID_FIELD] for row in rows}:
        raise UserActionRequired("Execution plan must map every selected sample exactly once.")
    return items


async def execute_v2_item(adapter, run, item, rows_by_id, snapshots):
    import hashlib

    from agent_tune_kit.results.evidence import digest_file, json_bytes, safe_path

    from .tracing import Observer, write_log_view

    if run.failed.is_set():
        raise UserActionRequired("Evidence failure stopped new execution.")
    evidence = run.execution(item["sample_ids"])
    observer = Observer(evidence, run)
    status, stage, results = "running", "context", []
    with observer.activate():
        try:
            evidence.save(
                "context",
                {
                    "selected": [rows_by_id[aid] for aid in item["sample_ids"]],
                    "context": [rows_by_id[aid] for aid in item.get("context_ids", item["sample_ids"])],
                },
                path="context/samples.json",
            )
            for index, (source, content) in enumerate(snapshots):
                evidence.save(
                    "snapshot",
                    content,
                    path=f"context/snapshot-{index}",
                    media_type="application/octet-stream",
                    source={"path": source, "phase": "before"},
                )
            import platform

            from agent_tune_kit import __version__

            evidence.save(
                "environment",
                {
                    "python": sys.version,
                    "platform": platform.platform(),
                    "atk": __version__,
                    "engine_sha256": {path.name: digest_file(path) for path in Path(__file__).parent.glob("*.py")},
                },
                path="context/environment.json",
            )
            stage = "input"
            observer.event("input.started")
            value = await invoke(adapter["build_agent_input"], item["source"])
            evidence.save("input", value, path="input.json")
            if run.failed.is_set():
                raise UserActionRequired("Required evidence failed before Agent invocation.")
            stage = "call"
            with observer.span("agent"):
                raw = await invoke(adapter["call_agent"], value, execution=observer)
            stage = "output"
            evidence.save("output", raw, path="output.json")
            stage = "mapping"
            mapper = adapter.get("map_agent_output")
            mapping = (
                await invoke(mapper, raw, item) if mapper else {item["sample_ids"][0]: {"output": raw, "location": ""}}
            )
            if not isinstance(mapping, dict) or set(mapping) != set(item["sample_ids"]):
                raise UserActionRequired("Output mapping must cover selected samples exactly once.")
            locations = {}
            for aid, mapped in mapping.items():
                if (
                    not isinstance(mapped, dict)
                    or "output" not in mapped
                    or not isinstance(mapped.get("location"), str)
                ):
                    raise UserActionRequired("Each mapped output needs output and location.")
                output = mapped["output"]
                output_status = mapped.get("output_status", "ok")
                errors = {key: mapped.get(key, "") for key in ("error_type", "error_message")}
                if (
                    output_status not in ("ok", "error")
                    or any(not isinstance(value, str) for value in errors.values())
                    or (output_status == "ok" and any(errors.values()))
                ):
                    raise UserActionRequired(
                        "Mapped status must be ok/error with string error details only for errors."
                    )
                row = dict(
                    rows_by_id[aid],
                    agent_output=output if isinstance(output, str) else json_bytes(output).decode(),
                    output_status=output_status,
                    **errors,
                    log_path=f"executions/{evidence.directory.name}/app.log",
                )
                retained = evidence.retain("result_row", row)
                if retained != row:
                    raise UserActionRequired("Retention changes scoring fields; results cannot be scored safely.")
                results.append(row)
                locations[aid] = mapped["location"]
            evidence.manifest["output_locations"] = evidence.retain("mapping", locations)
            status = "technical_failure" if any(row["output_status"] == "error" for row in results) else "completed"
        except AgentExecutionError as exc:
            status = "technical_failure"
            observer.event("execution.failed", {"stage": stage, "error_type": type(exc).__name__, "message": str(exc)})
            for aid in item["sample_ids"]:
                row = dict(
                    rows_by_id[aid],
                    agent_output=json_bytes({"error": type(exc).__name__}).decode(),
                    output_status="error",
                    error_type=type(exc).__name__,
                    log_path=f"executions/{evidence.directory.name}/app.log",
                )
                if evidence.retain("result_row", row) != row:
                    raise UserActionRequired("Retention changes scoring fields.") from None
                results.append(row)
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "technical_failure"
            run.failed.set()
            observer.event("execution.failed", {"stage": stage, "error_type": type(exc).__name__, "message": str(exc)})
            raise
        finally:
            evidence.manifest["status"] = status
            for source, content in snapshots:
                try:
                    changed = digest_file(safe_path(Path.cwd(), source)) != hashlib.sha256(content).hexdigest()
                except (OSError, UserActionRequired):
                    changed = True
                if changed:
                    observer.gaps.append({"reason": "snapshot_drift", "source": source})
            sources = evidence.retain("coverage", adapter["EVIDENCE"].get("sources", []))
            complete = (
                bool(sources)
                and not observer.failed
                and not observer.gaps
                and all(source.get("source") in observer.closed_sources for source in sources)
            )
            evidence.manifest["coverage"] = {
                "status": "complete_declared" if complete else "partial",
                "sources": sources,
                "closure_events": observer.closed_sources,
                "limits": [
                    "Closure is adapter-declared; hidden SDK attempts and undeclared background work are outside scope."
                ],
                "gaps": ([] if complete else ["declared_source_closure_unverified"])
                + observer.gaps
                + (["capture_failed"] if observer.failed else []),
            }
            try:
                evidence.register_direct_artifacts()
                write_log_view(evidence)
                evidence.seal(status)
            except Exception:
                run.failed.set()
    if observer.failed or run.failed.is_set():
        raise UserActionRequired("Execution evidence collection failed; partial scene preserved.")
    return results


async def run_v2_items(adapter, run, items, fields, rows, concurrency, snapshots):
    pending, iterator = set(), iter(items)
    by_id = {row[ATK_ID_FIELD]: row for row in rows}
    output, handle, writer = open_results_writer(run.directory, fields, rows)
    os.chmod(output, 0o600)
    try:
        while True:
            while len(pending) < concurrency and not run.failed.is_set():
                item = next(iterator, None)
                if item is None:
                    break
                pending.add(asyncio.create_task(execute_v2_item(adapter, run, item, by_id, snapshots)))
            if not pending:
                break
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            errors = []
            for task in done:
                try:
                    results = task.result()
                except BaseException as exc:
                    errors.append(exc)
                    run.failed.set()
                else:
                    for row in results:
                        writer.writerow(row)
                    handle.flush()
                    os.fsync(handle.fileno())
            if errors:
                raise errors[0]
            if run.failed.is_set():
                raise UserActionRequired("Capture failed; no further executions scheduled.")
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        handle.close()
    return output


def _run_v2(*, check=False, limit=None, offset=0, concurrency=1, only_failures=False, no_progress=False, ids=None):
    from agent_tune_kit.results.evidence import RunEvidence, safe_path

    from .tracing import capture_logs

    fields, rows = load_dataset()
    selected = (
        select_failure_dataset_rows(
            rows, load_failure_atk_ids(resolve_evaluation_version()), offset=offset, limit=limit
        )
        if only_failures
        else select_dataset_rows(rows, offset=offset, limit=limit, ids=ids)
    )
    if check:
        adapter = load_adapter()
        if not callable(adapter.get("EVIDENCE", {}).get("retain")):
            raise UserActionRequired("V2 requires EVIDENCE.retain(role, value).")
        items = plan_items(adapter, selected, rows)
        for item in items:
            asyncio.run(invoke(adapter["build_agent_input"], item["source"]))
        return {"checked": True, "rows": len(selected), "executions": len(items), "adapter_version": 2}
    version = allocate_next_results_version()
    run = RunEvidence(version, policy={})
    try:
        adapter = load_adapter()
        policy = adapter.get("EVIDENCE", {})
        if not isinstance(policy, dict) or not callable(policy.get("retain")):
            raise UserActionRequired("V2 requires EVIDENCE.retain(role, value).")
        run.policy = policy
        items = plan_items(adapter, selected, rows)
        run.manifest["planned"] = [item["sample_ids"] for item in items]
        run.flush()
        snapshots = []
        defaults = [
            name for name in [".atk/context.md", "uv.lock", "poetry.lock", "requirements.txt"] if Path(name).is_file()
        ]
        for relative in dict.fromkeys([str(RUNNER_PATH), *defaults, *policy.get("snapshots", [])]):
            path = safe_path(Path.cwd(), relative)
            before = path.stat()
            content = path.read_bytes()
            if before != path.stat():
                raise UserActionRequired("Snapshot source changed while reading.")
            snapshots.append((relative, content))
        with capture_logs(run, policy):
            output = asyncio.run(run_v2_items(adapter, run, items, fields, rows, concurrency, snapshots))
        if run.failed.is_set():
            raise UserActionRequired("Evidence collection failed.")
        run.seal("completed")
        return {
            "version": version.name,
            "run_id": run.manifest["run_id"],
            "rows": len(selected),
            "executions": len(items),
            "output": str(output),
            "adapter_version": 2,
            **execution_summary(*parse_csv(output)),
        }
    except BaseException as exc:
        run.manifest.update(
            status="interrupted" if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else "failed"
        )
        run.manifest["gaps"].append("run_not_completed")
        with suppress(OSError):
            run.flush()
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise UserActionRequired("V2 evaluation failed; preserved scene contains available details and gaps.") from None


def run_evaluation(
    *, check=False, limit=None, offset=0, concurrency=1, only_failures=False, no_progress=False, ids=None
):
    from .runtime import adapter_version

    if offset < 0 or (limit is not None and limit < 0) or concurrency < 1:
        raise UserActionRequired("limit/offset must be nonnegative and concurrency must be at least 1.")
    if only_failures and ids is not None:
        raise UserActionRequired("Select either explicit IDs or only-failures.")
    version = adapter_version()
    operation = _run_v2 if version == 2 else _run_v1
    options = dict(
        check=check,
        limit=limit,
        offset=offset,
        concurrency=concurrency,
        only_failures=only_failures,
        no_progress=no_progress,
        ids=ids,
    )
    if check:
        return operation(**options)
    with write_occupation():
        return operation(**options)
