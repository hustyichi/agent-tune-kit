"""Dataset and local runner orchestration; the target runner owns Agent invocation."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
from importlib import resources
from pathlib import Path

from .checkpoints import execution_revision, git, tracked_paths, verify_repo
from .core import ATKError, canonical, digest, new_id, now, safe_id, write_json


def initialize_project(repo: Path, request: dict) -> dict:
    repo = repo.expanduser().resolve()
    root = repo / ".atk"
    if root.exists():
        if (root / "project.json").exists():
            raise ATKError("WORKSPACE_CONFLICT", "ATK v2 project already exists; inspect before changing configuration")
        raise ATKError(
            "WORKSPACE_CONFLICT", "existing .atk data has no v2 project.json; preserve it and choose a clean project"
        )
    verify_repo(repo)
    if any(path == ".atk" or path.startswith(".atk/") for path in tracked_paths(repo)):
        raise ATKError("DIRTY_BASELINE", "tracked .atk files must be handled before initialization")
    required = {"python", "command", "components", "allowed_paths", "protected_paths", "runtime_notes"}
    if required - request.keys() or not isinstance(request["command"], list) or not request["command"]:
        raise ATKError("INCOMPLETE_EVIDENCE", f"project configuration missing: {sorted(required - request.keys())}")
    for component in request["components"]:
        if not {"component_id", "role", "change_role"} <= component.keys():
            raise ATKError("INCOMPLETE_EVIDENCE", "component identity is incomplete")
    git_dir = Path(git(repo, "rev-parse", "--git-dir").decode().strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    previous = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if ".atk/" not in previous.splitlines():
        exclude.write_text(previous.rstrip("\n") + "\n.atk/\n", encoding="utf-8")
    root.mkdir()
    project = {
        "schema_version": 2,
        "created_at": now(),
        "workspace_path": str(repo),
        "python": request["python"],
        "command": request["command"],
        "components": request["components"],
        "allowed_paths": request["allowed_paths"],
        "protected_paths": request["protected_paths"],
        "redact_keys": request.get("redact_keys", []),
        "loading_verification": request.get("loading_verification", {}),
        "external_effects": request.get("external_effects", []),
    }
    write_json(root / "project.json", project, immutable=True)
    (root / "runtime.md").write_text(request["runtime_notes"], encoding="utf-8")
    template = resources.files("agent_tune_kit").joinpath("plugin_payload/agent-tune-kit/templates/runner.py")
    if template.is_file():
        with resources.as_file(template) as source:
            runner_source = Path(source)
            (root / "adapters").mkdir()
            shutil.copyfile(runner_source, root / "adapters" / "runner.py")
    else:
        runner_source = Path(__file__).resolve().parents[2] / "templates" / "runner.py"
        if not runner_source.exists():
            raise ATKError("WORKSPACE_CONFLICT", "bundled runner template is missing")
        (root / "adapters").mkdir()
        shutil.copyfile(runner_source, root / "adapters" / "runner.py")
    return project


def store_dataset(root: Path, request: dict) -> dict:
    source = Path(request["source"]).expanduser().resolve()
    if source.suffix == ".csv":
        with source.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
    elif source.suffix == ".jsonl":
        rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        raise ATKError("UNSUPPORTED_EXPORT_FORMAT", "dataset must be CSV or JSONL")
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ATKError("INCOMPLETE_EVIDENCE", "dataset has no cases")
    mapping = request["mapping"]
    if not mapping.get("input"):
        raise ATKError("AMBIGUOUS_MAPPING", "dataset input mapping is required")
    cases = []
    ids = set()
    for index, row in enumerate(rows, 1):
        case_id = str(row.get(mapping.get("id", "")) or f"case-{index}")
        if case_id in ids:
            raise ATKError("AMBIGUOUS_MAPPING", f"duplicate Case ID: {case_id}")
        ids.add(case_id)
        if mapping["input"] not in row:
            raise ATKError("AMBIGUOUS_MAPPING", f"missing input for {case_id}")
        source_group = row.get(mapping.get("source_group_id", ""))
        usage = row.get(mapping.get("usage", "")) or "optimization"
        if usage not in {"optimization", "protection", "holdout"}:
            raise ATKError("AMBIGUOUS_MAPPING", f"invalid usage for {case_id}")
        case = {
            "id": case_id,
            "input": row[mapping["input"]],
            "expected_present": bool(mapping.get("expected") and mapping["expected"] in row),
            "expected": row.get(mapping.get("expected", "")),
            "source_group_id": source_group,
            "usage": usage,
            "initial_conditions": row.get(mapping.get("initial_conditions", "")),
            "source": {"path": str(source), "row": index},
        }
        case["fingerprint"] = digest({key: value for key, value in case.items() if key != "source"})
        cases.append(case)
    groups = {}
    for case in cases:
        group = case["source_group_id"]
        if (
            group
            and group in groups
            and {groups[group], case["usage"]} & {"holdout"}
            and groups[group] != case["usage"]
        ):
            raise ATKError("COMPARISON_INVALID", "holdout shares a source group with optimization/protection")
        if group:
            groups[group] = case["usage"]
    dataset_id = new_id("dataset")
    folder = root / "datasets" / safe_id(dataset_id)
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / "cases.jsonl"
    path.write_bytes(b"\n".join(canonical(case) for case in cases) + b"\n")
    manifest = {
        "schema_version": 2,
        "id": dataset_id,
        "created_at": now(),
        "source": str(source),
        "source_sha256": digest(source),
        "cases_sha256": digest(path),
        "case_count": len(cases),
        "case_ids": sorted(ids),
        "mapping": mapping,
    }
    write_json(folder / "manifest.json", manifest, immutable=True)
    return manifest


def load_cases(root: Path, dataset_id: str) -> dict[str, dict]:
    folder = root / "datasets" / dataset_id
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    path = folder / "cases.jsonl"
    if digest(path) != manifest["cases_sha256"]:
        raise ATKError("INCOMPLETE_EVIDENCE", "dataset changed after sealing")
    return {case["id"]: case for case in (json.loads(line) for line in path.read_text().splitlines())}


def run_evaluation(root: Path, request: dict) -> dict:
    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    repo = Path(project["workspace_path"])
    cases = load_cases(root, request["dataset_id"])
    selected = request["case_ids"]
    repeats = request.get("repeats", 1)
    if not selected or repeats < 1 or any(case_id not in cases for case_id in selected):
        raise ATKError("INCOMPLETE_EVIDENCE", "run requires known Cases and positive repeat count")
    if request["purpose"] not in {"evaluation", "diagnostic_probe"}:
        raise ATKError("INCOMPLETE_EVIDENCE", "invalid run purpose")
    if request["purpose"] == "diagnostic_probe" and not request.get("probe_authorized"):
        raise ATKError("WORKSPACE_CONFLICT", "probe requires recorded command and budget authorization")
    batch_id = new_id("batch")
    folder = root / "evidence" / batch_id
    folder.mkdir(parents=True, exist_ok=False)
    attempts = []
    for case_id in selected:
        for repeat_index in range(1, repeats + 1):
            attempts.append(
                {
                    "execution_id": new_id("execution"),
                    "record_id": new_id("record"),
                    "case_id": case_id,
                    "case_fingerprint": cases[case_id]["fingerprint"],
                    "repeat_index": repeat_index,
                    "retry_of": None,
                }
            )
    runner_request = {
        "batch_id": batch_id,
        "purpose": request["purpose"],
        "revision_id": request["revision_id"],
        "revision_commit": request.get("revision_commit"),
        "workspace_path": str(repo),
        "cases_path": str((root / "datasets" / safe_id(request["dataset_id"]) / "cases.jsonl").resolve()),
        "attempts": attempts,
        "timeout_seconds": request.get("timeout_seconds", 120),
        "concurrency": request.get("concurrency", 1),
        "run_config_ref": str(root / "project.json"),
        "output_dir": str(folder.resolve()),
    }
    request_path = folder / "request.json"
    write_json(request_path, runner_request, immutable=True)
    runner = root / "adapters" / "runner.py"
    if not runner.exists():
        raise ATKError("NOT_REPLAYABLE", f"project runner is missing: {runner}")
    command = [
        project["python"],
        str(runner),
        "--request",
        str(request_path.resolve()),
        "--output",
        str(folder.resolve()),
    ]
    timed_out = False
    with execution_revision(repo, root, request):
        try:
            result = subprocess.run(
                command, cwd=repo, capture_output=True, timeout=request.get("batch_timeout_seconds", 3600), check=False
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            result = subprocess.CompletedProcess(command, 124, b"", b"")
    batch_path = folder / "batch.json"
    records_path = folder / "records.jsonl"
    if timed_out and not batch_path.exists():
        write_json(batch_path, {"batch_id": batch_id, "completed_record_ids": [], "actual_components": []})
    if timed_out and not records_path.exists():
        records_path.write_bytes(b"")
    if not batch_path.exists() or not records_path.exists():
        raise ATKError(
            "INCOMPLETE_EVIDENCE", f"runner returned {result.returncode} without required batch artifacts: {folder}"
        )
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    expected = {item["record_id"]: item for item in attempts}
    observed = set()
    for record in records:
        record_id = record.get("id")
        if record_id not in expected or record_id in observed:
            raise ATKError("INCOMPLETE_EVIDENCE", "runner returned unknown or duplicate record ID")
        observed.add(record_id)
        execution = record.get("execution") or {}
        if (
            execution.get("id") != expected[record_id]["execution_id"]
            or execution.get("case_id") != expected[record_id]["case_id"]
        ):
            raise ATKError("INCOMPLETE_EVIDENCE", "runner changed an attempt identity")
    if set(batch.get("completed_record_ids", [])) != observed:
        raise ATKError("INCOMPLETE_EVIDENCE", "batch completion list disagrees with records")
    evidence_index = [
        {
            "evidence_id": record["id"],
            "fingerprint": digest(record),
            "locator": f"{records_path}#/{index}",
            "origin": request["purpose"] == "diagnostic_probe" and "diagnostic_probe" or "original_execution",
            "source_component": "target_runner",
            "execution_id": record["execution"]["id"],
        }
        for index, record in enumerate(records)
    ]
    fixed_components = [
        component for component in batch.get("actual_components", []) if component.get("change_role") == "fixed"
    ]
    manifest = {
        "schema_version": 2,
        "id": batch_id,
        "created_at": now(),
        "purpose": request["purpose"],
        "source_type": "local_runner",
        "dataset_id": request["dataset_id"],
        "revision_id": request["revision_id"],
        "revision_commit": request.get("revision_commit"),
        "records_ref": "records.jsonl",
        "records_sha256": digest(records_path),
        "record_count": len(records),
        "planned_count": len(attempts),
        "status": "interrupted"
        if timed_out
        else "sealed"
        if len(records) == len(attempts) and result.returncode == 0
        else "partial",
        "runner_exit_code": result.returncode,
        "run_config_hash": digest(project),
        "runner_hash": digest(runner),
        "fixed_context_hash": digest(fixed_components),
        "actual_components": batch.get("actual_components", []),
        "evidence_index": evidence_index,
        "missing_record_ids": sorted(set(expected) - observed),
    }
    write_json(folder / "manifest.json", manifest, immutable=True)
    return manifest
