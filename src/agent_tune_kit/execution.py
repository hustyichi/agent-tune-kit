"""Dataset and local runner orchestration; the target runner owns Agent invocation."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
from importlib import resources
from pathlib import Path

from .checkpoints import execution_revision, git, tracked_paths, verify_repo
from .core import ATKError, canonical, digest, locked, new_id, now, read_json, safe_id, validate_evidence, write_json


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
    folder = root / "datasets" / safe_id(dataset_id)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    path = folder / "cases.jsonl"
    if digest(path) != manifest["cases_sha256"]:
        raise ATKError("INCOMPLETE_EVIDENCE", "dataset changed after sealing")
    return {case["id"]: case for case in (json.loads(line) for line in path.read_text().splitlines())}


def _reserve_run(
    root: Path, request: dict, cases: dict, attempts: list[dict], batch_id: str, project: dict
) -> dict | None:
    round_id = request.get("round_id")
    if not round_id:
        if request["purpose"] == "diagnostic_probe" or any(cases[a["case_id"]]["usage"] == "holdout" for a in attempts):
            raise ATKError("WORKSPACE_CONFLICT", "probes and holdout runs require a recorded Round")
        return
    with locked(root):
        folder = root / "rounds" / safe_id(round_id)
        round_data = read_json(folder / "round.json")
        if round_data["status"] not in {"analysis_only", "ready", "optimizing", "finalizing"}:
            raise ATKError("WORKSPACE_CONFLICT", "Round is already closed")
        plan = (
            read_json(folder / "plan.json") if (folder / "plan.json").exists() else round_data.get("analysis_plan", {})
        )
        if plan.get("run_config_hash") and plan["run_config_hash"] != digest(project):
            raise ATKError("COMPARISON_INVALID", "project runner configuration changed after Round freeze")
        budget = plan.get("budget", {})
        purpose = request["purpose"]
        probe_config = None
        if purpose == "diagnostic_probe":
            permission_id = request.get("probe_authorization_id")
            permissions = plan.get("probe_permissions", [])
            permission = (
                next((p for p in permissions if isinstance(p, dict) and p.get("id") == permission_id), None)
                if isinstance(permissions, list)
                else None
            )
            probe_command = permission.get("command", project["command"]) if permission else None
            workdir_ref = permission.get("working_directory", ".") if permission else None
            workdir = (
                (Path(project["workspace_path"]) / workdir_ref).resolve() if isinstance(workdir_ref, str) else None
            )
            timeout = permission.get("timeout_seconds", 120) if permission else None
            max_calls = permission.get("max_calls", budget.get("probes", 0)) if permission else None
            script_ref = permission.get("script_path") if permission else None
            script_path = (root / "probes" / script_ref).resolve() if isinstance(script_ref, str) else None
            script_hash = permission.get("script_sha256") if permission else None
            if (
                not permission
                or not isinstance(probe_command, list)
                or not probe_command
                or any(not isinstance(part, str) for part in probe_command)
                or permission.get("command_hash") != digest(probe_command)
                or (permission.get("kind") == "direct_component" and probe_command == project["command"])
                or permission.get("runner_hash") != digest(root / "adapters" / "runner.py")
                or not set(a["case_id"] for a in attempts) <= set(permission.get("case_ids", []))
                or not permission.get("isolation_ref")
                or not workdir
                or not workdir.is_dir()
                or not workdir.is_relative_to(Path(project["workspace_path"]).resolve())
                or type(timeout) is not int
                or timeout < 1
                or type(max_calls) is not int
                or max_calls < 1
                or (
                    script_ref is not None
                    and (
                        not script_path
                        or not script_path.is_relative_to((root / "probes").resolve())
                        or not script_path.is_file()
                        or digest(script_path) != script_hash
                        or not any((workdir / part).resolve() == script_path for part in probe_command)
                    )
                )
            ):
                raise ATKError("WORKSPACE_CONFLICT", "probe command, cases, or isolation are not authorized")
            probe_config = {
                "command": probe_command,
                "command_hash": permission["command_hash"],
                "working_directory": str(workdir),
                "timeout_seconds": timeout,
                "isolation_ref": permission["isolation_ref"],
                "script_path": str(script_path) if script_path else None,
                "script_sha256": script_hash,
            }
        elif round_data["status"] == "analysis_only" or not set(a["case_id"] for a in attempts) <= set(
            plan["case_ids"]
        ):
            raise ATKError("COMPARISON_INVALID", "formal execution is outside the frozen Round")
        phase = request.get("phase", "incremental")
        if purpose == "evaluation" and phase not in {"incremental", "final", "external_fix"}:
            raise ATKError("COMPARISON_INVALID", "evaluation phase is not recognized")
        if purpose == "evaluation" and round_data.get("external_fix_identity") and phase != "external_fix":
            raise ATKError("COMPARISON_INVALID", "external fix Round needs its dedicated verification phase")
        if (
            purpose == "evaluation"
            and phase == "external_fix"
            and (
                not round_data.get("external_fix_identity")
                or round_data["pending_candidate_id"]
                or round_data["active_candidate_ids"]
                or request["revision_id"] != round_data["baseline_revision_id"]
            )
        ):
            raise ATKError("COMPARISON_INVALID", "external fix verification must run on the new B0")
        if (
            purpose == "evaluation"
            and phase == "final"
            and (round_data["pending_candidate_id"] or not round_data["active_candidate_ids"])
        ):
            raise ATKError("COMPARISON_INVALID", "final runs require a fixed accepted Revision")
        if purpose == "evaluation" and phase in {"final", "external_fix"} and not request.get("retry_batch_id"):
            selected = request.get("case_ids", [])
            if (
                len(selected) != len(set(selected))
                or set(selected) != set(plan["case_ids"])
                or request.get("repeats", 1) != plan["final_repeats"]
                or request["revision_id"] not in {round_data["baseline_revision_id"], round_data["current_revision_id"]}
            ):
                raise ATKError("COMPARISON_INVALID", "final run differs from the frozen Case and repeat plan")
        holdout = [cases[a["case_id"]] for a in attempts if cases[a["case_id"]]["usage"] == "holdout"]
        if holdout:
            milestone = request.get("holdout_milestone_id")
            if (
                purpose != "evaluation"
                or round_data["pending_candidate_id"]
                or not milestone
                or milestone != plan.get("holdout_milestone_id")
            ):
                raise ATKError(
                    "COMPARISON_INVALID", "holdout requires its frozen final milestone and no pending candidate"
                )
            groups = {case["source_group_id"] for case in holdout}
            if not all(groups):
                raise ATKError("COMPARISON_INVALID", "holdout source group is unknown")
            for dataset in (root / "datasets").glob("*/manifest.json"):
                for case in load_cases(root, dataset.parent.name).values():
                    if case["source_group_id"] in groups and case["usage"] != "holdout":
                        raise ATKError(
                            "COMPARISON_INVALID", "holdout source group was used for optimization or protection"
                        )
            exposure_path = root / "source-exposure.json"
            exposure = read_json(exposure_path) if exposure_path.exists() else {"groups": {}}
            for group in groups:
                previous = exposure["groups"].get(group)
                if previous and (previous["round_id"], previous["milestone_id"]) != (round_id, milestone):
                    raise ATKError("COMPARISON_INVALID", "holdout source group was exposed in another milestone")
        usage_path = folder / "budget-usage.json"
        usage = read_json(usage_path) if usage_path.exists() else {"executions": 0, "probes": 0, "reservations": []}
        if request.get("retry_batch_id") and any(
            reservation.get("retry_batch_id") == request["retry_batch_id"] for reservation in usage["reservations"]
        ):
            raise ATKError("COMPARISON_INVALID", "retry batch was already continued; use its successor")
        if (
            purpose == "evaluation"
            and phase in {"final", "external_fix"}
            and not request.get("retry_batch_id")
            and any(
                reservation.get("phase") == phase and reservation.get("revision_id") == request["revision_id"]
                for reservation in usage["reservations"]
            )
        ):
            raise ATKError("COMPARISON_INVALID", "final side was already run; reassess its evidence instead")
        counter = "probes" if purpose == "diagnostic_probe" else "executions"
        limit = budget.get(counter, 0)
        if type(limit) is not int or limit < 0 or usage[counter] + len(attempts) > limit:
            raise ATKError("BUDGET_EXHAUSTED", f"{counter} budget cannot cover {len(attempts)} attempts")
        if (
            probe_config
            and sum(
                reservation["attempts"]
                for reservation in usage["reservations"]
                if reservation.get("probe_authorization_id") == permission_id
            )
            + len(attempts)
            > max_calls
        ):
            raise ATKError("BUDGET_EXHAUSTED", "probe permission max_calls exceeded")
        if purpose == "evaluation" and phase not in {"final", "external_fix"}:
            final_needed = (
                (1 if round_data.get("external_fix_identity") else 2)
                * len(set(plan["case_ids"]))
                * plan["final_repeats"]
            )
            final_spent = sum(
                reservation["attempts"]
                for reservation in usage["reservations"]
                if reservation.get("phase") in {"final", "external_fix"}
            )
            if limit - usage["executions"] - len(attempts) < max(0, final_needed - final_spent):
                raise ATKError("BUDGET_EXHAUSTED", "run would consume the reserved final validation budget")
        usage[counter] += len(attempts)
        usage["reservations"].append(
            {
                "batch_id": batch_id,
                "purpose": purpose,
                "phase": phase if purpose == "evaluation" else None,
                "revision_id": request["revision_id"],
                "retry_batch_id": request.get("retry_batch_id"),
                "probe_authorization_id": permission_id if purpose == "diagnostic_probe" else None,
                "attempts": len(attempts),
                "at": now(),
            }
        )
        write_json(usage_path, usage)
        if holdout:
            for group in groups:
                entry = exposure["groups"].setdefault(
                    group, {"round_id": round_id, "milestone_id": milestone, "batch_ids": []}
                )
                entry["batch_ids"].append(batch_id)
            write_json(exposure_path, exposure)
        return probe_config


def run_evaluation(root: Path, request: dict) -> dict:
    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    repo = Path(project["workspace_path"])
    retry_batch_id = request.get("retry_batch_id")
    prior_manifest, prior_records = None, []
    if retry_batch_id:
        prior_manifest, records, _ = validate_evidence(root, retry_batch_id)
        if prior_manifest.get("source_type") != "local_runner" or prior_manifest.get("status") != "sealed":
            raise ATKError("COMPARISON_INVALID", "only a sealed local batch can be retried")
        prior_records = list(records.values())
        for key in ("dataset_id", "revision_id", "round_id", "purpose", "phase"):
            expected_value = request.get(key, "incremental") if key == "phase" else request.get(key)
            if expected_value != prior_manifest.get(key):
                raise ATKError("REVISION_MISMATCH", f"retry changes frozen {key}")
    cases = load_cases(root, request["dataset_id"])
    selected = request.get("case_ids", [])
    repeats = request.get("repeats", 1)
    if not retry_batch_id and (
        not selected
        or len(selected) != len(set(selected))
        or type(repeats) is not int
        or repeats < 1
        or any(case_id not in cases for case_id in selected)
    ):
        raise ATKError("INCOMPLETE_EVIDENCE", "run requires known Cases and positive repeat count")
    if request["purpose"] not in {"evaluation", "diagnostic_probe"}:
        raise ATKError("INCOMPLETE_EVIDENCE", "invalid run purpose")
    if retry_batch_id and request["purpose"] != "evaluation":
        raise ATKError("COMPARISON_INVALID", "diagnostic probes cannot be retried as formal evidence")
    batch_id = new_id("batch")
    attempts = []
    if retry_batch_id:
        targets = request.get("retry_execution_ids", [])
        executions = {record["execution"]["id"]: record for record in prior_records}
        plan = read_json(root / "rounds" / safe_id(request["round_id"]) / "plan.json")
        max_retries = plan.get("max_retries_per_slot", 0)
        if not targets or len(targets) != len(set(targets)) or type(max_retries) is not int or max_retries < 1:
            raise ATKError("COMPARISON_INVALID", "retry needs distinct targets and a frozen retry allowance")
        for target_id in targets:
            target = executions.get(target_id)
            if not target or target["execution"]["status"] not in {"timeout", "infrastructure_error"}:
                raise ATKError("COMPARISON_INVALID", "only an infrastructure failure can be retried")
            previous = target["execution"]
            if (
                previous["case_id"] not in cases
                or previous["case_fingerprint"] != cases[previous["case_id"]]["fingerprint"]
            ):
                raise ATKError("REVISION_MISMATCH", "retry Case content differs from the original attempt")
            slot = (previous["case_id"], previous["repeat_index"])
            chain = [
                r["execution"]
                for r in prior_records
                if (r["execution"]["case_id"], r["execution"]["repeat_index"]) == slot
            ]
            if chain[-1]["id"] != target_id or len(chain) - 1 >= max_retries:
                raise ATKError("COMPARISON_INVALID", "retry target is stale or its allowance is exhausted")
            attempts.append(
                {
                    "execution_id": new_id("execution"),
                    "record_id": new_id("record"),
                    "case_id": previous["case_id"],
                    "case_fingerprint": previous["case_fingerprint"],
                    "repeat_index": previous["repeat_index"],
                    "retry_of": target_id,
                }
            )
    else:
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
    if retry_batch_id and prior_manifest.get("run_config_hash") != digest(project):
        raise ATKError("COMPARISON_INVALID", "runner configuration changed before retry")
    runner = root / "adapters" / "runner.py"
    if not runner.exists():
        raise ATKError("NOT_REPLAYABLE", f"project runner is missing: {runner}")
    if retry_batch_id and prior_manifest.get("runner_hash") != digest(runner):
        raise ATKError("COMPARISON_INVALID", "runner changed before retry")
    probe_config = _reserve_run(root, request, cases, attempts, batch_id, project)
    folder = root / "evidence" / batch_id
    folder.mkdir(parents=True, exist_ok=False)
    config_path = folder / "run-config.json"
    write_json(config_path, project, immutable=True)
    runner_request = {
        "batch_id": batch_id,
        "purpose": request["purpose"],
        "phase": request.get("phase", "incremental") if request["purpose"] == "evaluation" else None,
        "revision_id": request["revision_id"],
        "revision_commit": request.get("revision_commit"),
        "workspace_path": str(repo),
        "cases_path": str((root / "datasets" / safe_id(request["dataset_id"]) / "cases.jsonl").resolve()),
        "attempts": attempts,
        "timeout_seconds": probe_config["timeout_seconds"] if probe_config else request.get("timeout_seconds", 120),
        "concurrency": request.get("concurrency", 1),
        "run_config_ref": str(config_path.resolve()),
        "output_dir": str(folder.resolve()),
    }
    if probe_config:
        runner_request["probe_config"] = probe_config
    request_path = folder / "request.json"
    write_json(request_path, runner_request, immutable=True)
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
    if retry_batch_id and batch.get("actual_components") != prior_manifest.get("actual_components"):
        raise ATKError("REVISION_MISMATCH", "component identity changed during retry")
    if prior_records:
        records = prior_records + records
        records_path.write_bytes(b"\n".join(canonical(record) for record in records) + b"\n")
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
        "phase": request.get("phase", "incremental") if request["purpose"] == "evaluation" else None,
        "source_type": "local_runner",
        "dataset_id": request["dataset_id"],
        "round_id": request.get("round_id"),
        "probe_authorization_id": request.get("probe_authorization_id")
        if request["purpose"] == "diagnostic_probe"
        else None,
        "probe_config": probe_config,
        "supersedes_batch_id": retry_batch_id,
        "revision_id": request["revision_id"],
        "revision_commit": request.get("revision_commit"),
        "records_ref": "records.jsonl",
        "records_sha256": digest(records_path),
        "record_count": len(records),
        "planned_count": prior_manifest["planned_count"] + len(attempts) if prior_manifest else len(attempts),
        "status": "interrupted"
        if timed_out
        else "sealed"
        if len(observed) == len(attempts) and result.returncode == 0
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
