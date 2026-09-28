"""Dataset and local runner orchestration; the target runner owns Agent invocation."""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import signal
import subprocess
from contextlib import suppress
from importlib import resources
from pathlib import Path

from .checkpoints import execution_revision, git, tracked_paths, verify_repo
from .core import (
    ATKError,
    atomic_write,
    canonical,
    digest,
    locked,
    new_id,
    now,
    read_json,
    safe_id,
    validate_evidence,
    write_json,
)


def _version_commands(components: list[dict]) -> dict[str, tuple[list[str], int, str | None]]:
    if not isinstance(components, list):
        raise ATKError("INCOMPLETE_EVIDENCE", "components must be a list")
    commands = {}
    ids = set()
    for component in components:
        if not isinstance(component, dict) or not {"component_id", "role", "change_role"} <= component.keys():
            raise ATKError("INCOMPLETE_EVIDENCE", "component identity is incomplete")
        component_id = component["component_id"]
        if not isinstance(component_id, str) or not component_id:
            raise ATKError("INCOMPLETE_EVIDENCE", "component ID is invalid")
        if component_id in ids:
            raise ATKError("INCOMPLETE_EVIDENCE", f"duplicate component ID: {component_id}")
        ids.add(component_id)
        command = component.get("version_command")
        if command is None:
            continue
        timeout = component.get("version_timeout_seconds", 5)
        expected = component.get("expected_version")
        if (
            component["change_role"] != "fixed"
            or not isinstance(command, list)
            or not command
            or any(not isinstance(part, str) or not part for part in command)
            or type(timeout) is not int
            or not 1 <= timeout <= 60
            or (
                expected is not None
                and (not isinstance(expected, str) or not re.fullmatch(r"[A-Za-z0-9._+:/@-]{1,128}", expected))
            )
        ):
            raise ATKError("INCOMPLETE_EVIDENCE", f"invalid version command for component: {component_id}")
        commands[component_id] = (command, timeout, expected)
    return commands


def _probe_versions(
    repo: Path, commands: dict[str, tuple[list[str], int, str | None]]
) -> dict[str, tuple[str | None, str | None]]:
    versions = {}
    for component_id, (command, timeout, _) in commands.items():
        try:
            with subprocess.Popen(
                command, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
            ) as process:
                try:
                    stdout, _ = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    versions[component_id] = (None, "timeout")
                    continue
            value = stdout.decode(errors="replace").strip()
            versions[component_id] = (
                (value, None)
                if process.returncode == 0 and re.fullmatch(r"[A-Za-z0-9._+:/@-]{1,128}", value)
                else (None, "nonzero_exit" if process.returncode else "invalid_output")
            )
        except OSError:
            versions[component_id] = (None, "launch_error")
    return versions


def initialize_project(repo: Path, request: dict) -> dict:
    repo = repo.expanduser().resolve()
    root = repo / ".atk"
    analysis_only = request.get("analysis_only", False)
    if type(analysis_only) is not bool:
        raise ATKError("INCOMPLETE_EVIDENCE", "analysis_only must be boolean")
    upgrade = False
    if (root / "project.json").exists() and request.get("configure_runtime") is True:
        existing = read_json(root / "project.json")
        upgrade = existing.get("schema_version") == 2 and existing.get("analysis_only") is True
    if root.exists() and not upgrade:
        if (root / "project.json").exists():
            raise ATKError("WORKSPACE_CONFLICT", "ATK v2 project already exists; inspect before changing configuration")
        raise ATKError(
            "WORKSPACE_CONFLICT", "existing .atk data has no v2 project.json; preserve it and choose a clean project"
        )
    runtime_requested = any(
        key in request for key in ("python", "command", "components", "external_effects", "configure_runtime")
    )
    if analysis_only and not runtime_requested:
        project = {
            "schema_version": 2,
            "created_at": now(),
            "workspace_path": str(repo),
            "analysis_only": True,
            "redact_keys": request.get("redact_keys", []),
        }
        write_json(root / "project.json", project, immutable=True)
        return project
    if upgrade and any(
        read_json(path).get("status") in {"ready", "optimizing", "finalizing", "paused"}
        for path in (root / "rounds").glob("*/round.json")
    ):
        raise ATKError("WORKSPACE_CONFLICT", "runtime setup requires inactive analysis Rounds")
    if not analysis_only:
        verify_repo(repo)
        if any(path == ".atk" or path.startswith(".atk/") for path in tracked_paths(repo)):
            raise ATKError("DIRTY_BASELINE", "tracked .atk files must be handled before initialization")
    required = {"python", "command", "components", "runtime_notes", "external_effects"}
    if not analysis_only:
        required |= {"allowed_paths", "protected_paths"}
    if required - request.keys() or not isinstance(request["command"], list) or not request["command"]:
        raise ATKError("INCOMPLETE_EVIDENCE", f"project configuration missing: {sorted(required - request.keys())}")
    infrastructure_codes = request.get("infrastructure_exit_codes", [])
    if (
        not isinstance(infrastructure_codes, list)
        or any(type(code) is not int or not 1 <= code <= 255 for code in infrastructure_codes)
        or len(infrastructure_codes) != len(set(infrastructure_codes))
    ):
        raise ATKError("INCOMPLETE_EVIDENCE", "infrastructure exit codes must be distinct integers from 1 to 255")
    external_effects = request["external_effects"]
    if not isinstance(external_effects, list) or any(
        not isinstance(effect, dict) or not isinstance(effect.get("name"), str) or not effect["name"].strip()
        for effect in external_effects
    ):
        raise ATKError("INCOMPLETE_EVIDENCE", "external effects must be named objects")
    metric_sources = request.get("metric_sources", {})
    if (
        not isinstance(metric_sources, dict)
        or set(metric_sources) - {"cost", "tool_calls"}
        or any(
            not isinstance(source, dict)
            or not isinstance(source.get("source"), str)
            or not source["source"].strip()
            or source["source"] in {"agent_sidecar", "runner_clock"}
            or not isinstance(source.get("evidence_ref"), str)
            or not source["evidence_ref"].strip()
            for source in metric_sources.values()
        )
    ):
        raise ATKError("INCOMPLETE_EVIDENCE", "cost and tool-call metrics need a named independent collector")
    _version_commands(request["components"])
    if not analysis_only:
        git_dir = Path(git(repo, "rev-parse", "--git-dir").decode().strip())
        if not git_dir.is_absolute():
            git_dir = repo / git_dir
        exclude = git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        previous = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if ".atk/" not in previous.splitlines():
            exclude.write_text(previous.rstrip("\n") + "\n.atk/\n", encoding="utf-8")
    root.mkdir(exist_ok=upgrade)
    project = {
        "schema_version": 2,
        "created_at": now(),
        "workspace_path": str(repo),
        "analysis_only": analysis_only,
        "python": request["python"],
        "command": request["command"],
        "infrastructure_exit_codes": infrastructure_codes,
        "components": request["components"],
        "allowed_paths": request.get("allowed_paths", []),
        "protected_paths": request.get("protected_paths", []),
        "redact_keys": request.get("redact_keys", existing.get("redact_keys", []) if upgrade else []),
        "loading_verification": request.get("loading_verification", {}),
        "external_effects": external_effects,
        "metric_sources": metric_sources,
    }
    (root / "runtime.md").write_text(request["runtime_notes"], encoding="utf-8")
    template = resources.files("agent_tune_kit").joinpath("plugin_payload/agent-tune-kit/templates/runner.py")
    if template.is_file():
        with resources.as_file(template) as source:
            runner_source = Path(source)
            (root / "adapters").mkdir(exist_ok=True)
            shutil.copyfile(runner_source, root / "adapters" / "runner.py")
    else:
        runner_source = Path(__file__).resolve().parents[2] / "templates" / "runner.py"
        if not runner_source.exists():
            raise ATKError("WORKSPACE_CONFLICT", "bundled runner template is missing")
        (root / "adapters").mkdir(exist_ok=True)
        shutil.copyfile(runner_source, root / "adapters" / "runner.py")
    write_json(root / "project.json", project, immutable=not upgrade)
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
        if source_group is not None and not isinstance(source_group, str):
            raise ATKError("AMBIGUOUS_MAPPING", f"invalid source group for {case_id}")
        usage = row.get(mapping.get("usage", "")) or "optimization"
        if usage not in {"optimization", "protection", "holdout"}:
            raise ATKError("AMBIGUOUS_MAPPING", f"invalid usage for {case_id}")
        attachments = row.get(mapping.get("attachments", ""), []) if mapping.get("attachments") else []
        if isinstance(attachments, str):
            try:
                attachments = json.loads(attachments) if attachments.strip() else []
            except ValueError as exc:
                raise ATKError("AMBIGUOUS_MAPPING", f"invalid attachments for {case_id}") from exc
        if not isinstance(attachments, list) or any(not isinstance(item, str) or not item for item in attachments):
            raise ATKError("AMBIGUOUS_MAPPING", f"attachments must be a list of file paths for {case_id}")
        attachment_snapshots = []
        for item in attachments:
            path = Path(item).expanduser()
            path = (source.parent / path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_file():
                raise ATKError("NOT_REPLAYABLE", f"attachment is missing for {case_id}: {path}")
            attachment_snapshots.append({"path": str(path), "sha256": digest(path)})
        case = {
            "id": case_id,
            "input": row[mapping["input"]],
            "expected_present": bool(mapping.get("expected") and mapping["expected"] in row),
            "expected": row.get(mapping.get("expected", "")),
            "source_group_id": source_group,
            "usage": usage,
            "initial_conditions": row.get(mapping.get("initial_conditions", "")),
            "attachments": attachment_snapshots,
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


def load_cases(root: Path, dataset_id: str, *, verify_attachments: bool = False) -> dict[str, dict]:
    folder = root / "datasets" / safe_id(dataset_id)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    path = folder / "cases.jsonl"
    if digest(path) != manifest["cases_sha256"]:
        raise ATKError("INCOMPLETE_EVIDENCE", "dataset changed after sealing")
    cases = {case["id"]: case for case in (json.loads(line) for line in path.read_text().splitlines())}
    if verify_attachments:
        for case in cases.values():
            for attachment in case.get("attachments", []):
                attachment_path = Path(attachment["path"])
                if not attachment_path.is_file() or digest(attachment_path) != attachment["sha256"]:
                    raise ATKError("NOT_REPLAYABLE", f"Case attachment changed or disappeared: {case['id']}")
    return cases


def source_groups_for_evidence(root: Path, refs: list[dict]) -> list[str]:
    groups = set()
    for ref in refs:
        if not isinstance(ref, dict) or not ref.get("batch_id") or not ref.get("evidence_id"):
            raise ATKError("INCOMPLETE_EVIDENCE", "Knowledge evidence reference is malformed")
        manifest, records, index = validate_evidence(root, ref["batch_id"])
        if ref["evidence_id"] not in index:
            raise ATKError("INCOMPLETE_EVIDENCE", "Knowledge evidence reference is missing")
        if manifest.get("source_type") == "source_contract":
            continue
        if manifest.get("source_type") != "local_runner":
            group = ref.get("source_group_id")
            if not isinstance(group, str) or not group.strip():
                raise ATKError("INCOMPLETE_EVIDENCE", "imported Knowledge evidence needs a source group")
            groups.add(group)
            continue
        if ref["evidence_id"] not in records:
            raise ATKError("INCOMPLETE_EVIDENCE", "Knowledge local evidence must reference an Execution")
        case_id = records[ref["evidence_id"]].get("execution", {}).get("case_id")
        case = load_cases(root, manifest["dataset_id"]).get(case_id)
        if not case:
            raise ATKError("INCOMPLETE_EVIDENCE", "Knowledge Execution has no matching Case")
        if ref.get("source_group_id") not in {None, case.get("source_group_id")}:
            raise ATKError("INCOMPLETE_EVIDENCE", "Knowledge source group differs from its Case")
        if case.get("source_group_id"):
            groups.add(case["source_group_id"])
    return sorted(groups)


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
        if request["purpose"] == "evaluation" and (
            not plan.get("case_fingerprints")
            or any(
                plan["case_fingerprints"].get(attempt["case_id"]) != attempt["case_fingerprint"] for attempt in attempts
            )
        ):
            raise ATKError("COMPARISON_INVALID", "formal run Cases differ from the frozen dataset")
        if plan.get("run_config_hash") and plan["run_config_hash"] != digest(project):
            raise ATKError("COMPARISON_INVALID", "project runner configuration changed after Round freeze")
        if request["purpose"] == "evaluation" and request.get("concurrency", 1) != plan.get("concurrency", 1):
            raise ATKError("COMPARISON_INVALID", "execution concurrency differs from the frozen plan")
        if request["purpose"] == "evaluation" and request.get(
            "timeout_seconds", plan.get("timeout_seconds", 120)
        ) != plan.get("timeout_seconds", 120):
            raise ATKError("COMPARISON_INVALID", "execution timeout differs from the frozen plan")
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
        if round_data["status"] == "finalizing" and purpose == "evaluation" and phase == "incremental":
            raise ATKError("WORKSPACE_CONFLICT", "finalizing Round cannot start incremental runs")
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
        if (
            purpose == "evaluation"
            and phase in {"final", "external_fix"}
            and not (request.get("retry_batch_id") or request.get("continue_batch_id"))
        ):
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
            for knowledge in (root / "knowledge").glob("*/revision-*.json"):
                entry = read_json(knowledge)
                used_groups = entry.get("optimization_source_group_ids")
                if used_groups is None:
                    used_groups = source_groups_for_evidence(
                        root, entry.get("evidence_refs", []) + entry.get("contrary_refs", [])
                    )
                if groups.intersection(used_groups):
                    raise ATKError("COMPARISON_INVALID", "holdout source group was used for optimization knowledge")
            exposure_path = root / "source-exposure.json"
            exposure = read_json(exposure_path) if exposure_path.exists() else {"groups": {}}
            for group in groups:
                previous = exposure["groups"].get(group)
                if previous and (previous["round_id"], previous["milestone_id"]) != (round_id, milestone):
                    raise ATKError("COMPARISON_INVALID", "holdout source group was exposed in another milestone")
        usage_path = folder / "budget-usage.json"
        usage = read_json(usage_path) if usage_path.exists() else {"executions": 0, "probes": 0, "reservations": []}
        predecessor_id = request.get("retry_batch_id") or request.get("continue_batch_id")
        if predecessor_id and any(
            reservation.get("retry_batch_id") == predecessor_id
            or reservation.get("continue_batch_id") == predecessor_id
            for reservation in usage["reservations"]
        ):
            raise ATKError("COMPARISON_INVALID", "batch was already continued; use its successor")
        if (
            purpose == "evaluation"
            and phase in {"final", "external_fix"}
            and not predecessor_id
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
        if purpose == "evaluation" and phase in {"final", "external_fix"} and round_data["status"] != "finalizing":
            round_data.setdefault("transitions", []).append(
                {
                    "action": "start_finalizing",
                    "from_status": round_data["status"],
                    "to_status": "finalizing",
                    "reason": "frozen final execution",
                    "at": now(),
                }
            )
            round_data["status"] = "finalizing"
            round_data["state_version"] += 1
            write_json(folder / "round.json", round_data)
        usage[counter] += len(attempts)
        usage["reservations"].append(
            {
                "batch_id": batch_id,
                "purpose": purpose,
                "phase": phase if purpose == "evaluation" else None,
                "revision_id": request["revision_id"],
                "retry_batch_id": request.get("retry_batch_id"),
                "continue_batch_id": request.get("continue_batch_id"),
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
    with locked(root):
        return _run_evaluation_locked(root, request)


def _run_evaluation_locked(root: Path, request: dict) -> dict:
    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    if project.get("analysis_only") and request.get("purpose") != "diagnostic_probe":
        raise ATKError("NOT_REPLAYABLE", "analysis-only project needs explicit runtime setup before execution")
    if project.get("analysis_only") and not all(key in project for key in ("python", "command", "components")):
        raise ATKError("NOT_REPLAYABLE", "analysis-only probe needs explicit runtime setup")
    concurrency = request.get("concurrency", 1)
    if type(concurrency) is not int or concurrency < 1:
        raise ATKError("INCOMPLETE_EVIDENCE", "concurrency must be a positive integer")
    if type(request.get("batch_timeout_seconds", 3600)) is not int or request.get("batch_timeout_seconds", 3600) < 1:
        raise ATKError("INCOMPLETE_EVIDENCE", "batch timeout must be a positive integer")
    for effect in project.get("external_effects", []):
        if not isinstance(effect, dict) or not isinstance(effect.get("name"), str):
            raise ATKError("NOT_REPLAYABLE", "external effect declaration is invalid")
        protection = effect.get("protection")
        if (
            not isinstance(protection, dict)
            or protection.get("kind") not in {"test_environment", "stub", "approved_safeguard"}
            or not isinstance(protection.get("evidence_ref"), str)
            or not protection["evidence_ref"].strip()
        ):
            raise ATKError("NOT_REPLAYABLE", f"external effect lacks recorded protection: {effect['name']}")
    repo = Path(project["workspace_path"])
    plan_path = root / "rounds" / safe_id(request["round_id"]) / "plan.json" if request.get("round_id") else None
    formal_plan = (
        read_json(plan_path) if plan_path and plan_path.exists() and request["purpose"] == "evaluation" else {}
    )
    formal_timeout = request.get("timeout_seconds", formal_plan.get("timeout_seconds", 120))
    if type(formal_timeout) not in {int, float} or not math.isfinite(formal_timeout) or formal_timeout <= 0:
        raise ATKError("INCOMPLETE_EVIDENCE", "execution timeout must be positive and finite")
    version_commands = _version_commands(project["components"])
    retry_batch_id = request.get("retry_batch_id")
    continue_batch_id = request.get("continue_batch_id")
    if retry_batch_id and continue_batch_id:
        raise ATKError("COMPARISON_INVALID", "retry and continuation are separate operations")
    prior_manifest, prior_records = None, []
    predecessor_id = retry_batch_id or continue_batch_id
    if predecessor_id:
        prior_manifest, records, _ = validate_evidence(root, predecessor_id)
        if (
            prior_manifest.get("source_type") != "local_runner"
            or (retry_batch_id and prior_manifest.get("status") != "sealed")
            or (continue_batch_id and prior_manifest.get("status") not in {"partial", "interrupted"})
        ):
            raise ATKError("COMPARISON_INVALID", "batch state does not permit the requested retry or continuation")
        prior_records = (
            list(records.values())
            if retry_batch_id
            else [
                record for record in records.values() if record["id"] in prior_manifest.get("completed_record_ids", [])
            ]
        )
        for key in ("dataset_id", "revision_id", "round_id", "purpose", "phase"):
            expected_value = request.get(key, "incremental") if key == "phase" else request.get(key)
            if expected_value != prior_manifest.get(key):
                raise ATKError("REVISION_MISMATCH", f"continuation changes frozen {key}")
    cases = load_cases(root, request["dataset_id"], verify_attachments=True)
    selected = request.get("case_ids", [])
    repeats = request.get("repeats", 1)
    if not predecessor_id and (
        not selected
        or len(selected) != len(set(selected))
        or type(repeats) is not int
        or repeats < 1
        or any(case_id not in cases for case_id in selected)
    ):
        raise ATKError("INCOMPLETE_EVIDENCE", "run requires known Cases and positive repeat count")
    if request["purpose"] not in {"evaluation", "diagnostic_probe"}:
        raise ATKError("INCOMPLETE_EVIDENCE", "invalid run purpose")
    if predecessor_id and request["purpose"] != "evaluation":
        raise ATKError("COMPARISON_INVALID", "diagnostic probes cannot be continued as formal evidence")
    batch_id = new_id("batch")
    attempts = []
    if continue_batch_id:
        previous_path = root / "evidence" / safe_id(continue_batch_id) / "request.json"
        if not previous_path.is_file() or prior_manifest.get("request_sha256") != digest(previous_path):
            raise ATKError("INCOMPLETE_EVIDENCE", "predecessor attempt plan changed")
        previous_request = read_json(previous_path)
        pending = set(prior_manifest.get("not_started_record_ids", [])) | set(
            prior_manifest.get("unknown_record_ids", [])
        )
        unknown = set(prior_manifest.get("unknown_record_ids", []))
        authorization = request.get("unknown_execution_authorization")
        if unknown and (
            not isinstance(authorization, dict)
            or authorization.get("source") != "user"
            or authorization.get("batch_id") != continue_batch_id
            or not isinstance(authorization.get("record_ids"), list)
            or any(not isinstance(item, str) for item in authorization["record_ids"])
            or set(authorization["record_ids"]) != unknown
            or not authorization.get("reason")
            or not authorization.get("risk")
        ):
            raise ATKError("COMPARISON_INVALID", "unknown executions need inspection and explicit rerun authorization")
        if not pending or pending != {item["record_id"] for item in previous_request["attempts"]} - set(
            prior_manifest.get("completed_record_ids", [])
        ):
            raise ATKError("COMPARISON_INVALID", "batch has no safely identifiable pending slots")
        for previous in previous_request["attempts"]:
            if previous["record_id"] not in pending:
                continue
            if (
                previous["case_id"] not in cases
                or previous["case_fingerprint"] != cases[previous["case_id"]]["fingerprint"]
            ):
                raise ATKError("REVISION_MISMATCH", "continuation Case changed")
            attempts.append(
                {
                    "execution_id": new_id("execution"),
                    "record_id": new_id("record"),
                    "case_id": previous["case_id"],
                    "case_fingerprint": previous["case_fingerprint"],
                    "repeat_index": previous["repeat_index"],
                    "retry_of": None,
                }
            )
    elif retry_batch_id:
        targets = request.get("retry_execution_ids", [])
        executions = {record["execution"]["id"]: record for record in prior_records}
        plan = read_json(root / "rounds" / safe_id(request["round_id"]) / "plan.json")
        max_retries = plan.get("max_retries_per_slot", 0)
        if not targets or len(targets) != len(set(targets)) or type(max_retries) is not int or max_retries < 1:
            raise ATKError("COMPARISON_INVALID", "retry needs distinct targets and a frozen retry allowance")
        for target_id in targets:
            target = executions.get(target_id)
            if not target or target["execution"]["status"] not in {"timeout", "infra_error"}:
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
    if predecessor_id and prior_manifest.get("run_config_hash") != digest(project):
        raise ATKError("COMPARISON_INVALID", "runner configuration changed before continuation")
    if predecessor_id and prior_manifest.get("timeout_seconds") != formal_timeout:
        raise ATKError("COMPARISON_INVALID", "execution timeout changed before continuation")
    runner = root / "adapters" / "runner.py"
    if not runner.exists():
        raise ATKError("NOT_REPLAYABLE", f"project runner is missing: {runner}")
    if predecessor_id and prior_manifest.get("runner_hash") != digest(runner):
        raise ATKError("COMPARISON_INVALID", "runner changed before continuation")
    with execution_revision(repo, root, request):
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
            "timeout_seconds": probe_config["timeout_seconds"] if probe_config else formal_timeout,
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
        runner_start_error = None
        component_drift = []
        batch_path = folder / "batch.json"
        runner_batch = None
        versions_before = _probe_versions(repo, version_commands)
        try:
            with subprocess.Popen(
                command, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
            ) as process:
                try:
                    stdout, stderr = process.communicate(timeout=request.get("batch_timeout_seconds", 3600))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGTERM)
                    try:
                        stdout, stderr = process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                        stdout, stderr = process.communicate()
                    finally:
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except OSError as exc:
            runner_start_error = str(exc)
            result = subprocess.CompletedProcess(command, 127, b"", b"")
        versions_after = _probe_versions(repo, version_commands)
        if batch_path.exists():
            try:
                parsed = json.loads(batch_path.read_text(encoding="utf-8"))
                runner_batch = parsed if isinstance(parsed, dict) else None
            except (OSError, UnicodeDecodeError, ValueError):
                pass
        if runner_batch is not None:
            reported = runner_batch.get("actual_components", [])
            for component in reported if isinstance(reported, list) else []:
                if not isinstance(component, dict):
                    continue
                source = component.get("source_path")
                before = component.get("actual_sha256")
                if not isinstance(source, str) or not source or not isinstance(before, str) or not before:
                    continue
                path = repo / source
                try:
                    after = digest(path) if path.is_file() else None
                except OSError:
                    after = None
                if after != before:
                    component_drift.append(
                        {
                            "component_id": component.get("component_id"),
                            "source_path": source,
                            "before_sha256": before,
                            "after_sha256": after,
                        }
                    )
    records_path = folder / "records.jsonl"
    missing_artifacts = [
        name for name, path in (("batch.json", batch_path), ("records.jsonl", records_path)) if not path.exists()
    ]
    invalid_artifacts = ["batch.json"] if batch_path.exists() and runner_batch is None else []
    batch = runner_batch
    if batch is None:
        batch = {
            "batch_id": batch_id,
            "planned_record_ids": [attempt["record_id"] for attempt in attempts],
            "completed_record_ids": [],
            "running_record_id": None,
            "not_started_record_ids": [attempt["record_id"] for attempt in attempts] if runner_start_error else [],
            "actual_components": [],
        }
        if not batch_path.exists():
            write_json(batch_path, batch)
    if not records_path.exists():
        records_path.write_bytes(b"")
    actual_components = batch.get("actual_components", [])
    if not isinstance(actual_components, list) or any(not isinstance(item, dict) for item in actual_components):
        actual_components = []
        component_drift.append({"reason": "runner returned an invalid component inventory"})
    declared_components = {component["component_id"]: component for component in project["components"]}
    observed_ids = [component.get("component_id") for component in actual_components]
    valid_ids = all(isinstance(component_id, str) and component_id for component_id in observed_ids)
    observed_components = (
        {component_id: component for component_id, component in zip(observed_ids, actual_components, strict=True)}
        if valid_ids
        else {}
    )
    if (
        not valid_ids
        or len(observed_ids) != len(set(observed_ids))
        or not set(declared_components) <= set(observed_components)
        or any(
            observed_components[component_id].get(key) != declared.get(key)
            for component_id, declared in declared_components.items()
            if component_id in observed_components
            for key in ("role", "change_role", "source_path")
        )
    ):
        component_drift.append({"reason": "runner component inventory differs from project configuration"})
    for component_id, (version_command, _, expected_version) in version_commands.items():
        observed_component = observed_components.get(component_id)
        if not observed_component:
            component_drift.append({"component_id": component_id, "reason": "runner omitted component identity"})
            continue
        (before, before_issue), (after, after_issue) = versions_before[component_id], versions_after[component_id]
        if before_issue:
            issue = f"before_{before_issue}"
        elif after_issue:
            issue = f"after_{after_issue}"
        elif before != after:
            issue = "version_changed_during_batch"
        elif expected_version is not None and before != expected_version:
            issue = "expected_version_mismatch"
        else:
            issue = None
        if issue is None:
            status = "verified"
        elif before_issue or after_issue:
            status = "unknown"
        elif before != after:
            status = "drifted"
        else:
            status = "mismatch"
        observed_component.update(
            {
                "expected_version": expected_version,
                "actual_version": before,
                "post_run_actual_version": after,
                "identity_basis": {"method": "version_command", "command_hash": digest(version_command)},
                "identity_status": status,
            }
        )
        if issue:
            observed_component["identity_issue"] = issue
        if before != after:
            component_drift.append({"component_id": component_id, "before_version": before, "after_version": after})
    raw_records = records_path.read_bytes()
    records = []
    malformed_records = False
    for line in raw_records.splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except (UnicodeDecodeError, ValueError):
                malformed_records = True
                break
    if malformed_records:
        atomic_write(folder / "records.incomplete.jsonl", raw_records, immutable=True)
        atomic_write(records_path, b"\n".join(canonical(record) for record in records) + (b"\n" if records else b""))
    expected = {item["record_id"]: item for item in attempts}
    if batch.get("batch_id") != batch_id or batch.get("planned_record_ids") != list(expected):
        raise ATKError("INCOMPLETE_EVIDENCE", "runner changed planned attempt identities")
    observed = set()
    invalid_execution_status_ids = []
    for record in records:
        if not isinstance(record, dict):
            raise ATKError("INCOMPLETE_EVIDENCE", "runner returned an invalid attempt record")
        record_id = record.get("id")
        if not isinstance(record_id, str) or record_id not in expected or record_id in observed:
            raise ATKError("INCOMPLETE_EVIDENCE", "runner returned unknown or duplicate record ID")
        observed.add(record_id)
        execution = record.get("execution") or {}
        attempt = expected[record_id]
        identity = {
            "id": attempt["execution_id"],
            "batch_id": batch_id,
            "record_id": record_id,
            "case_id": attempt["case_id"],
            "case_fingerprint": attempt["case_fingerprint"],
            "repeat_index": attempt["repeat_index"],
            "retry_of": attempt["retry_of"],
            "purpose": request["purpose"],
            "revision_id": request["revision_id"],
        }
        if (
            not isinstance(execution, dict)
            or record.get("case_id") != attempt["case_id"]
            or any(execution.get(key) != value for key, value in identity.items())
        ):
            raise ATKError("INCOMPLETE_EVIDENCE", "runner changed an attempt identity")
        if execution.get("status") not in {
            "completed",
            "agent_error",
            "infra_error",
            "timeout",
            "cancelled",
            "unknown",
        }:
            invalid_execution_status_ids.append(record_id)
    for field in ("completed_record_ids", "not_started_record_ids", "running_record_ids"):
        ids = batch.get(field, [])
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids) or len(ids) != len(set(ids)):
            raise ATKError("INCOMPLETE_EVIDENCE", f"runner returned invalid {field}")
    declared_completed = set(batch.get("completed_record_ids", []))
    declared_not_started = set(batch.get("not_started_record_ids", []))
    running_record_id = batch.get("running_record_id")
    if declared_completed - set(expected) or (
        running_record_id is not None and (not isinstance(running_record_id, str) or running_record_id not in expected)
    ):
        raise ATKError("INCOMPLETE_EVIDENCE", "runner returned unknown attempt status")
    running = set(batch.get("running_record_ids", []))
    if running_record_id:
        running.add(running_record_id)
    if running - set(expected):
        raise ATKError("INCOMPLETE_EVIDENCE", "runner returned unknown running attempt")
    confirmed = (declared_completed & observed) - running
    not_started = declared_not_started - observed
    if not_started - set(expected):
        raise ATKError("INCOMPLETE_EVIDENCE", "runner returned unknown not-started attempt")
    not_started -= declared_completed | running
    unknown = set(expected) - confirmed - not_started
    status_conflict = bool(
        declared_not_started & (declared_completed | observed) or running & (declared_completed | declared_not_started)
    )
    if predecessor_id and actual_components != prior_manifest.get("actual_components"):
        raise ATKError("REVISION_MISMATCH", "component identity changed during continuation")
    if prior_records:
        records = prior_records + records
        atomic_write(records_path, b"\n".join(canonical(record) for record in records) + b"\n")
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
    fixed_components = [component for component in actual_components if component.get("change_role") == "fixed"]
    manifest = {
        "schema_version": 2,
        "id": batch_id,
        "created_at": now(),
        "purpose": request["purpose"],
        "phase": request.get("phase", "incremental") if request["purpose"] == "evaluation" else None,
        "source_type": "local_runner",
        "concurrency": concurrency,
        "dataset_id": request["dataset_id"],
        "round_id": request.get("round_id"),
        "probe_authorization_id": request.get("probe_authorization_id")
        if request["purpose"] == "diagnostic_probe"
        else None,
        "probe_config": probe_config,
        "supersedes_batch_id": predecessor_id,
        "continuation_kind": "resume" if continue_batch_id else "retry" if retry_batch_id else None,
        "unknown_execution_authorization": request.get("unknown_execution_authorization")
        if continue_batch_id
        else None,
        "revision_id": request["revision_id"],
        "revision_commit": request.get("revision_commit"),
        "records_ref": "records.jsonl",
        "records_sha256": digest(records_path),
        "record_count": len(records),
        "planned_count": prior_manifest["planned_count"] + len(attempts)
        if retry_batch_id
        else prior_manifest["planned_count"]
        if continue_batch_id
        else len(attempts),
        "status": "interrupted"
        if timed_out
        else "sealed"
        if len(confirmed) == len(attempts)
        and result.returncode == 0
        and not component_drift
        and not missing_artifacts
        and not invalid_artifacts
        and not malformed_records
        and not status_conflict
        and not invalid_execution_status_ids
        else "partial",
        "runner_exit_code": result.returncode,
        "runner_start_error": runner_start_error,
        "missing_runner_artifacts": missing_artifacts,
        "invalid_runner_artifacts": invalid_artifacts,
        "incomplete_records_sha256": digest(raw_records) if malformed_records else None,
        "runner_status_conflict": status_conflict,
        "invalid_execution_status_ids": invalid_execution_status_ids,
        "run_config_hash": digest(project),
        "request_sha256": digest(request_path),
        "timeout_seconds": runner_request["timeout_seconds"],
        "runner_hash": digest(runner),
        "fixed_context_hash": digest(fixed_components),
        "actual_components": actual_components,
        "post_run_component_drift": component_drift,
        "evidence_index": evidence_index,
        "missing_record_ids": sorted(set(expected) - observed),
        "completed_record_ids": sorted(confirmed | {record["id"] for record in prior_records}),
        "not_started_record_ids": sorted(not_started),
        "unknown_record_ids": sorted(unknown),
    }
    write_json(folder / "manifest.json", manifest, immutable=True)
    return manifest
