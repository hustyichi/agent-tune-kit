"""Evidence-bound diagnosis, paired comparison, and final round decisions."""

from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from pathlib import Path

from .checkpoints import changed_paths, git, head, require_override, verify_repo
from .core import (
    ATKError,
    atomic_write,
    digest,
    locked,
    new_id,
    now,
    read_assessment,
    read_json,
    safe_id,
    validate_evidence,
    write_json,
)


def _round_folder(root: Path, round_id: str) -> Path:
    return root / "rounds" / safe_id(round_id)


def store_diagnosis(root: Path, request: dict) -> dict:
    with locked(root):
        return _store_diagnosis_locked(root, request)


def _store_diagnosis_locked(root: Path, request: dict) -> dict:
    folder = _round_folder(root, request["round_id"])
    round_data = read_json(folder / "round.json")
    issues = request["issues"]
    if not issues:
        raise ATKError("INCOMPLETE_EVIDENCE", "diagnosis requires at least one Issue")
    saved = []
    for issue in issues:
        required = {
            "id",
            "symptom",
            "hypothesis",
            "competing_explanations",
            "checks",
            "evidence_refs",
            "mechanism_evidence_refs",
            "intervention_validation_refs",
            "root_cause_status",
            "intervention_layer",
            "responsible_component",
            "case_ids",
            "priority",
            "disposition",
            "resolution",
            "next_action",
        }
        if required - issue.keys():
            raise ATKError("INCOMPLETE_EVIDENCE", f"Issue missing fields: {sorted(required - issue.keys())}")
        if issue["root_cause_status"] not in {"hypothesis", "supported", "intervention_supported", "inconclusive"}:
            raise ATKError("INCOMPLETE_EVIDENCE", "invalid root-cause status")
        if issue["disposition"] not in {
            "local_candidate",
            "external_handoff",
            "judge_revision",
            "needs_evidence",
            "no_action",
        }:
            raise ATKError("INCOMPLETE_EVIDENCE", "invalid Issue disposition")
        if issue["root_cause_status"] == "supported" and (
            not issue["mechanism_evidence_refs"]
            or not any(check.get("status") == "completed" for check in issue["checks"])
            or not issue.get("competing_explanations_addressed")
        ):
            raise ATKError("INCOMPLETE_EVIDENCE", "supported mechanism needs direct evidence and executed checks")
        if issue["root_cause_status"] == "intervention_supported":
            if not issue["intervention_validation_refs"]:
                raise ATKError("INCOMPLETE_EVIDENCE", "intervention requires a Validation reference")
            for validation_id in issue["intervention_validation_refs"]:
                validation = read_json(folder / "validations" / safe_id(validation_id) / "validation.json")
                if validation["result"] != "pass":
                    raise ATKError("COMPARISON_INVALID", "intervention Validation did not pass")
        if issue["root_cause_status"] in {"hypothesis", "inconclusive"} and not issue["next_action"]:
            raise ATKError("INCOMPLETE_EVIDENCE", "unresolved diagnosis needs a next check")
        if issue["disposition"] == "external_handoff":
            handoff = issue.get("handoff", {})
            if not handoff.get("trigger_input") or not handoff.get("expected") or not handoff.get("actual"):
                raise ATKError("INCOMPLETE_EVIDENCE", "external handoff needs trigger and expected/actual behavior")
        if issue["resolution"] == "resolved":
            fix = issue.get("external_fix", {})
            if (
                not fix.get("new_round_id")
                or not fix.get("component_identity")
                or not fix.get("direct_evidence_refs")
                or not fix.get("end_to_end_validation_id")
            ):
                raise ATKError(
                    "INCOMPLETE_EVIDENCE",
                    "resolution needs a linked new Round, fix identity, direct check, and end-to-end Validation",
                )
            new_round = read_json(_round_folder(root, fix["new_round_id"]) / "round.json")
            if (
                new_round.get("previous_round_id") != round_data["id"]
                or issue["id"] not in new_round["issues"]
                or new_round.get("external_fix_identity") != fix["component_identity"]
            ):
                raise ATKError("INCOMPLETE_EVIDENCE", "fix Round does not link Issue and component identity")
            linked_validation = read_json(
                _round_folder(root, fix["new_round_id"])
                / "validations"
                / safe_id(fix["end_to_end_validation_id"])
                / "validation.json"
            )
            if (
                new_round["status"] != "completed"
                or linked_validation["mode"] != "external_fix"
                or linked_validation["result"] != "pass"
                or linked_validation["component_identity"] != fix["component_identity"]
                or linked_validation["direct_evidence_refs"] != fix["direct_evidence_refs"]
                or not set(issue["case_ids"]) <= set(linked_validation["case_ids"])
            ):
                raise ATKError("COMPARISON_INVALID", "external fix end-to-end Validation did not cover affected Cases")
        for ref in issue["evidence_refs"] + issue["mechanism_evidence_refs"]:
            if not isinstance(ref, dict) or not ref.get("batch_id") or not ref.get("evidence_id"):
                raise ATKError("INCOMPLETE_EVIDENCE", "Issue evidence reference is malformed")
            _, _, index = validate_evidence(root, ref["batch_id"])
            if ref["evidence_id"] not in index:
                raise ATKError("INCOMPLETE_EVIDENCE", "Issue evidence reference is missing")
        for check in issue["checks"]:
            if not isinstance(check, dict) or check.get("status") not in {
                "not_run",
                "completed",
                "failed",
                "inconclusive",
            }:
                raise ATKError("INCOMPLETE_EVIDENCE", "invalid diagnostic check status")
            if check["status"] == "not_run" and not check.get("reason"):
                raise ATKError("INCOMPLETE_EVIDENCE", "unrun check needs a reason")
            if check["status"] == "completed" and (
                not check.get("expected") or not check.get("actual") or not check.get("evidence_refs")
            ):
                raise ATKError("INCOMPLETE_EVIDENCE", "completed check needs expected, actual, and evidence refs")
            if not isinstance(check.get("evidence_refs", []), list):
                raise ATKError("INCOMPLETE_EVIDENCE", "diagnostic check evidence refs must be a list")
            for ref in check.get("evidence_refs", []):
                if (
                    not isinstance(ref, dict)
                    or not ref.get("batch_id")
                    or ref.get("evidence_id") not in validate_evidence(root, ref["batch_id"])[2]
                ):
                    raise ATKError("INCOMPLETE_EVIDENCE", "diagnostic check evidence reference is missing")
        issue_folder = folder / "issues" / safe_id(issue["id"])
        revision = 1 + len(list(issue_folder.glob("revision-*.json")))
        value = {
            **issue,
            "schema_version": 2,
            "round_id": round_data["id"],
            "revision": revision,
            "created_at": now(),
            "previous_revision": revision - 1 if revision > 1 else None,
        }
        path = issue_folder / f"revision-{revision}.json"
        write_json(path, value, immutable=True)
        saved.append(value)
    return {"issues": saved}


def store_knowledge(root: Path, request: dict) -> dict:
    with locked(root):
        return _store_knowledge_locked(root, request)


def _store_knowledge_locked(root: Path, request: dict) -> dict:
    value = request["knowledge"]
    required = {
        "status",
        "applicability",
        "component_hashes",
        "contract_hashes",
        "judger_hash",
        "evidence_refs",
        "contrary_refs",
        "candidate_ids",
        "validation_ids",
        "body",
    }
    if required - value.keys() or value["status"] not in {
        "provisional",
        "validated_in_scope",
        "contradicted",
        "retired",
    }:
        raise ATKError("INCOMPLETE_EVIDENCE", "Knowledge metadata is incomplete")
    for ref in value["evidence_refs"] + value["contrary_refs"]:
        if ref.get("evidence_id") not in validate_evidence(root, ref["batch_id"])[2]:
            raise ATKError("INCOMPLETE_EVIDENCE", "Knowledge evidence reference is missing")
    knowledge_id = request.get("knowledge_id") or new_id("knowledge")
    folder = root / "knowledge" / safe_id(knowledge_id)
    revision = 1 + len(list(folder.glob("revision-*.json")))
    metadata = {key: item for key, item in value.items() if key != "body"}
    metadata.update(
        {
            "schema_version": 2,
            "id": knowledge_id,
            "revision": revision,
            "created_at": now(),
            "body_sha256": digest(value["body"].encode()),
            "previous_revision": revision - 1 if revision > 1 else None,
        }
    )
    atomic_write(folder / f"revision-{revision}.md", value["body"].encode(), immutable=True)
    write_json(folder / f"revision-{revision}.json", metadata, immutable=True)
    return metadata


def knowledge_applicability(root: Path, request: dict) -> dict:
    folder = root / "knowledge" / safe_id(request["knowledge_id"])
    revisions = sorted(folder.glob("revision-*.json"), key=lambda path: int(path.stem.split("-")[1]))
    if not revisions:
        raise ATKError("INCOMPLETE_EVIDENCE", "Knowledge entry is missing")
    value = read_json(revisions[-1])
    current = request["current_identity"]
    status = (
        "validated_in_scope"
        if value["status"] == "validated_in_scope"
        and all(value[key] == current.get(key) for key in ("component_hashes", "contract_hashes", "judger_hash"))
        else "needs_revalidation"
    )
    return {"knowledge_id": value["id"], "revision": value["revision"], "applicability": status}


def _assessment_slots(root: Path, assessment_id: str, dimension: str) -> tuple[dict, dict, dict]:
    manifest, rows = read_assessment(root, assessment_id)
    batch, records, _ = validate_evidence(root, manifest["batch_id"])
    slots = defaultdict(list)
    for row in rows:
        if row["dimension"] != dimension:
            continue
        record = records[row["record_id"]]
        execution = record.get("execution")
        if not execution or batch.get("purpose") != "evaluation":
            raise ATKError("COMPARISON_INVALID", "comparison requires formal local Executions")
        key = (execution["case_id"], execution["case_fingerprint"], execution["repeat_index"])
        slots[key].append((execution, row, record))
    return manifest, batch, slots


def _selected_attempt(attempts: list, max_retries: int):
    by_id = {execution["id"]: (execution, row, record) for execution, row, record in attempts}
    roots = [item for item in attempts if not item[0].get("retry_of")]
    if len(by_id) != len(attempts) or len(roots) != 1 or len(attempts) - 1 > max_retries:
        return None
    current = roots[0]
    visited = {current[0]["id"]}
    while len(visited) < len(attempts):
        children = [item for item in attempts if item[0].get("retry_of") == current[0]["id"]]
        if len(children) != 1 or current[0].get("status") not in {"timeout", "infrastructure_error"}:
            return None
        current = children[0]
        if current[0]["id"] in visited:
            return None
        visited.add(current[0]["id"])
    return current


def _metric_total(slots: dict, cases: set[str], metric: str) -> float | None:
    values = []
    for key, attempts in slots.items():
        if key[0] not in cases:
            continue
        for _, _, record in attempts:
            value = record.get("metrics", {}).get(metric)
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                return None
            values.append(float(value))
    return sum(values) if values else None


def _fixed_identity_known(batch: dict) -> bool:
    for component in batch.get("actual_components", []):
        if component.get("change_role") != "fixed":
            continue
        if component.get("identity_status") not in {"available", "verified"}:
            return False
        if component.get("actual_sha256"):
            continue
        basis = component.get("identity_basis")
        if (
            component.get("identity_status") != "verified"
            or not component.get("actual_version")
            or component.get("actual_version") != component.get("post_run_actual_version")
            or not isinstance(basis, dict)
            or basis.get("method") != "version_command"
            or not basis.get("command_hash")
        ):
            return False
    return True


def _loaded_component_ids(root: Path, batch: dict, record: dict) -> set[str]:
    config_path = root / "evidence" / safe_id(batch["id"]) / "run-config.json"
    if not config_path.is_file():
        return set()
    config = read_json(config_path)
    if (
        digest(config) != batch.get("run_config_hash")
        or Path(config["workspace_path"]).resolve() != root.parent.resolve()
    ):
        return set()
    declared = {entry["component_id"]: entry for entry in config.get("components", [])}
    observed = {entry["component_id"]: entry for entry in batch.get("actual_components", [])}
    loaded = set()
    for event in record.get("loading_evidence", []):
        component_id = event.get("component_id")
        component = observed.get(component_id)
        source = declared.get(component_id, {}).get("source_path")
        observed_path = event.get("resolved_path")
        if (
            not source
            or not component
            or component.get("source_path") != source
            or not component.get("actual_sha256")
            or not isinstance(observed_path, str)
            or not Path(observed_path).is_absolute()
            or event.get("state") not in {"loaded", "invoked"}
            or event.get("fingerprint") != component.get("actual_sha256")
        ):
            continue
        try:
            source_path = (root.parent / source).resolve()
            loaded_path = Path(observed_path).resolve()
            staged_from = event.get("staged_from_path")
            copied_from_source = (
                isinstance(staged_from, str)
                and Path(staged_from).is_absolute()
                and Path(staged_from).resolve() == source_path
                and loaded_path.is_file()
                and digest(loaded_path) == component["actual_sha256"]
            )
            if loaded_path == source_path or copied_from_source:
                loaded.add(component_id)
        except OSError:
            continue
    return loaded


def compare_and_gate(root: Path, request: dict) -> dict:
    with locked(root):
        return _compare_and_gate_locked(root, request)


def _compare_and_gate_locked(root: Path, request: dict) -> dict:
    folder = _round_folder(root, request["round_id"])
    round_data = read_json(folder / "round.json")
    plan = read_json(folder / "plan.json")
    if round_data.get("external_fix_identity"):
        raise ATKError("COMPARISON_INVALID", "external fix Round needs one-sided new B0 validation")
    mode = request["mode"]
    if mode not in {"incremental", "final"}:
        raise ATKError("COMPARISON_INVALID", "mode must be incremental or final")
    dimension = request.get("dimension", "task_success")
    left_manifest, left_batch, left_slots = _assessment_slots(root, request["left_assessment_id"], dimension)
    right_manifest, right_batch, right_slots = _assessment_slots(root, request["right_assessment_id"], dimension)
    for manifest in (left_manifest, right_manifest):
        if manifest["judger_readiness"] != "calibrated" or digest(manifest["judger"]) != plan["judger_hash"]:
            raise ATKError("JUDGER_INVALID", "comparison requires the frozen calibrated judger")
        if manifest["evaluation_spec_hash"] != plan["evaluation_spec_hash"]:
            raise ATKError("COMPARISON_INVALID", "evaluation_spec differs from frozen plan")
    if left_batch.get("run_config_hash") != right_batch.get("run_config_hash"):
        raise ATKError("COMPARISON_INVALID", "runner configuration drifted between sides")
    if plan.get("run_config_hash") and left_batch.get("run_config_hash") != plan["run_config_hash"]:
        raise ATKError("COMPARISON_INVALID", "runner configuration differs from the frozen Round")
    for batch in (left_batch, right_batch):
        if (
            batch.get("runner_hash") != plan["runner_hash"]
            or batch.get("fixed_context_hash") != plan["fixed_context_hash"]
        ):
            raise ATKError("COMPARISON_INVALID", "runner or fixed component identity differs from frozen plan")
    limitations = request.get("limitations", [])
    if not isinstance(limitations, list):
        raise ATKError("COMPARISON_INVALID", "limitations must be a list")
    limitations = list(limitations)
    result = "insufficient" if left_batch.get("status") != "sealed" or right_batch.get("status") != "sealed" else None
    if result:
        limitations.append("one or both execution batches are not sealed")
    if not _fixed_identity_known(left_batch) or not _fixed_identity_known(right_batch):
        result = "insufficient"
        limitations.append("fixed component identity is unknown")
    if left_batch.get("phase", "incremental") != mode or right_batch.get("phase", "incremental") != mode:
        result = "insufficient"
        limitations.append("execution phase differs from the requested comparison")
    if set(left_slots) != set(right_slots):
        result = "insufficient"
        limitations.append("paired Case slots differ between baseline and candidate")
    expected_cases = set(
        plan["case_ids"]
        if mode == "final"
        else plan["protection_case_ids"] + plan.get("target_case_ids_by_issue", {}).get(request.get("issue_id"), [])
    )
    if mode == "incremental":
        for accepted_id in round_data["active_candidate_ids"]:
            decision = read_json(folder / "candidates" / safe_id(accepted_id) / "decision.json")
            prior = read_json(folder / "validations" / safe_id(decision["validation_id"]) / "validation.json")
            expected_cases.update(prior["fixed_case_ids"])
    if not expected_cases or expected_cases - {slot[0] for slot in left_slots}:
        result = "insufficient"
        limitations.append("frozen target or protection Cases are missing from the baseline")
    expected_repeats = plan["final_repeats"] if mode == "final" else plan.get("incremental_repeats", 1)
    if any(
        {repeat for case, _, repeat in left_slots if case == case_id} != set(range(1, expected_repeats + 1))
        for case_id in expected_cases
    ):
        result = "insufficient"
        limitations.append("baseline repeat count differs from the frozen plan")
    max_retries = plan.get("max_retries_per_slot", 0)
    case_results = {}
    case_distributions = {}
    rows = []
    for key in sorted(set(left_slots) & set(right_slots)):
        if key[0] not in expected_cases:
            continue
        sides = []
        for side_name, side in (("baseline", left_slots), ("candidate", right_slots)):
            selected = _selected_attempt(side[key], max_retries)
            if selected is None:
                result = "insufficient"
                limitations.append(f"{side_name} {key[0]} repeat {key[2]} has no usable execution")
                sides.append(None)
                continue
            execution, row, _ = selected
            sides.append(
                row["verdict"]
                if row["validity"] == "valid"
                and row["verdict"] in {"pass", "fail"}
                and execution.get("status") not in {"timeout", "infrastructure_error"}
                else None
            )
        rows.append(
            {"case_id": key[0], "fingerprint": key[1], "repeat_index": key[2], "left": sides[0], "right": sides[1]}
        )
        required_loaded = set(plan.get("required_loaded_component_ids", []))
        if required_loaded:
            for side_name, slots, batch in (
                ("baseline", left_slots, left_batch),
                ("candidate", right_slots, right_batch),
            ):
                selected = _selected_attempt(slots[key], max_retries)
                for _, _, record in [selected] if selected else []:
                    missing_loaded = required_loaded - _loaded_component_ids(root, batch, record)
                    if missing_loaded:
                        result = "insufficient"
                        limitations.append(
                            f"{side_name} {key[0]} repeat {key[2]} lacks verified loading: "
                            + ", ".join(sorted(missing_loaded))
                        )
        if any(verdict not in {"pass", "fail"} for verdict in sides):
            result = "insufficient"
            limitations.append(f"{key[0]} repeat {key[2]} has an unknown or invalid verdict")
    for case_id in expected_cases:
        paired = [row for row in rows if row["case_id"] == case_id]
        repeats = [[row for row in paired if row["repeat_index"] == index] for index in range(1, expected_repeats + 1)]
        case_distributions[case_id] = {}
        for side in ("left", "right"):
            verdicts = [matches[0][side] if len(matches) == 1 else None for matches in repeats]
            case_distributions[case_id][side] = {verdict: verdicts.count(verdict) for verdict in ("pass", "fail")}
            case_distributions[case_id][side]["unknown"] = verdicts.count(None)
        if len(paired) != expected_repeats or any(
            len(matches) != 1 or matches[0]["left"] is None or matches[0]["right"] is None for matches in repeats
        ):
            result = "insufficient"
            limitations.append(f"{case_id} lacks complete paired repeats")
            continue
        case_results[case_id] = {
            "left": sum(row["left"] == "pass" for row in paired) / expected_repeats,
            "right": sum(row["right"] == "pass" for row in paired) / expected_repeats,
        }
    fixed = [case_id for case_id, score in case_results.items() if score["right"] > score["left"]]
    regressed = [case_id for case_id, score in case_results.items() if score["right"] < score["left"]]
    outcome_counts = {
        "fixed": len(fixed),
        "regressed": len(regressed),
        "persistent_failure": sum(score["left"] == score["right"] == 0 for score in case_results.values()),
        "stable_success": sum(score["left"] == score["right"] == 1 for score in case_results.values()),
        "unchanged_mixed": sum(0 < score["left"] == score["right"] < 1 for score in case_results.values()),
        "unknown": len(expected_cases) - len(case_results),
    }
    target_cases = set(plan.get("target_case_ids_by_issue", {}).get(request.get("issue_id"), expected_cases))
    primary_delta = (
        sum(score["right"] - score["left"] for score in case_results.values()) / len(expected_cases)
        if len(case_results) == len(expected_cases)
        else None
    )
    metric_names = {"cost", "duration_seconds", "tool_calls"}
    required_metrics = set()
    limits = plan.get("metric_limits", {})
    if "max_total_cost" in limits:
        required_metrics.add("cost")
    if "max_mean_duration_seconds" in limits:
        required_metrics.add("duration_seconds")
    if "max_mean_tool_calls" in limits:
        required_metrics.add("tool_calls")
    if plan.get("objective") == "efficiency":
        required_metrics.add(plan["efficiency_metric"])
    metrics = {
        metric: {
            "left_total": _metric_total(left_slots, expected_cases, metric),
            "right_total": _metric_total(right_slots, expected_cases, metric),
        }
        for metric in sorted(metric_names)
    }
    if any(value is None for name in required_metrics for value in metrics[name].values()):
        result = "insufficient"
        limitations.append("required cost, duration, or tool-call metrics are missing")
    slot_count = len(expected_cases) * expected_repeats
    exceeds_limit = (
        (
            ("max_total_cost" in limits and metrics["cost"]["right_total"] > limits["max_total_cost"])
            or (
                "max_mean_duration_seconds" in limits
                and metrics["duration_seconds"]["right_total"] / slot_count > limits["max_mean_duration_seconds"]
            )
            or (
                "max_mean_tool_calls" in limits
                and metrics["tool_calls"]["right_total"] / slot_count > limits["max_mean_tool_calls"]
            )
        )
        if result is None
        else False
    )
    if result is None:
        if regressed or exceeds_limit:
            result = "regression"
        elif plan.get("objective", "quality") == "efficiency":
            metric = metrics[plan["efficiency_metric"]]
            result = (
                "pass"
                if metric["left_total"] - metric["right_total"] > plan.get("min_efficiency_delta", 0)
                else "no_effect"
            )
        elif (
            not set(fixed) & target_cases
            or len(fixed) < plan.get("min_fixed_cases", 1)
            or primary_delta <= plan.get("min_primary_delta", 0)
        ):
            result = "no_effect"
        else:
            result = "pass"
    validation = {
        "schema_version": 2,
        "id": new_id("validation"),
        "created_at": now(),
        "round_id": round_data["id"],
        "mode": mode,
        "left_assessment_id": request["left_assessment_id"],
        "right_assessment_id": request["right_assessment_id"],
        "left_revision_id": left_batch.get("revision_id"),
        "right_revision_id": right_batch.get("revision_id"),
        "left_commit": request["left_commit"],
        "result": result,
        "case_ids": sorted(expected_cases),
        "fixed_case_ids": sorted(fixed),
        "regressed_case_ids": sorted(regressed),
        "case_scores": case_results,
        "case_distributions": case_distributions,
        "outcome_counts": outcome_counts,
        "coverage": {
            "complete_cases": len(case_results),
            "planned_cases": len(expected_cases),
            "planned_repeats_per_case": expected_repeats,
        },
        "primary_delta": primary_delta,
        "metrics": metrics,
        "evidence_level": "repeated" if expected_repeats > 1 else "single_run",
        "plan_hash": digest(plan),
        "limitations": limitations,
    }
    if mode == "incremental":
        candidate = read_json(folder / "candidates" / safe_id(request["candidate_id"]) / "candidate.json")
        if (
            validation["left_commit"] != candidate["parent_commit"]
            or validation["left_revision_id"] != candidate["parent_revision_id"]
            or validation["right_revision_id"] != candidate["revision_id"]
        ):
            raise ATKError("REVISION_MISMATCH", "assessment Revision does not match candidate")
        validation["candidate_id"] = candidate["id"]
    else:
        if (
            validation["left_commit"] != round_data["baseline_commit"]
            or validation["left_revision_id"] != round_data["baseline_revision_id"]
            or validation["right_revision_id"] != round_data.get("current_revision_id")
        ):
            raise ATKError("REVISION_MISMATCH", "final validation must compare B0 with current Revision")
        validation["final_commit"] = round_data["current_commit"]
    out = folder / "validations" / validation["id"]
    csv_output = io.StringIO(newline="")
    writer = csv.DictWriter(csv_output, fieldnames=("case_id", "fingerprint", "repeat_index", "left", "right"))
    writer.writeheader()
    writer.writerows(rows)
    comparison = out / "comparison.csv"
    atomic_write(comparison, csv_output.getvalue().encode(), immutable=True)
    validation["comparison_sha256"] = digest(comparison)
    write_json(out / "validation.json", validation, immutable=True)
    return validation


def validate_external_fix(root: Path, request: dict) -> dict:
    with locked(root):
        return _validate_external_fix_locked(root, request)


def _validate_external_fix_locked(root: Path, request: dict) -> dict:
    folder = _round_folder(root, request["round_id"])
    round_data = read_json(folder / "round.json")
    plan = read_json(folder / "plan.json")
    if not round_data.get("external_fix_identity") or round_data["status"] not in {"ready", "optimizing"}:
        raise ATKError("WORKSPACE_CONFLICT", "external fix validation needs an open linked Round")
    refs = request.get("direct_evidence_refs", [])
    if not isinstance(refs, list) or not refs or not request.get("direct_assessment_id"):
        raise ATKError("INCOMPLETE_EVIDENCE", "external fix needs separate direct-check evidence")
    direct_manifest, direct_rows = read_assessment(root, request["direct_assessment_id"])
    if direct_manifest["judger_readiness"] != "calibrated":
        raise ATKError("JUDGER_INVALID", "direct component check needs a calibrated Assessment")
    direct_dimension = request.get("direct_dimension", "task_success")
    direct_batches = []
    for ref in refs:
        if not isinstance(ref, dict) or not ref.get("batch_id") or not ref.get("evidence_id"):
            raise ATKError("INCOMPLETE_EVIDENCE", "direct-check evidence reference is malformed")
        direct_batch, _, index = validate_evidence(root, ref["batch_id"])
        permission = next(
            (
                item
                for item in plan.get("probe_permissions", [])
                if item.get("id") == direct_batch.get("probe_authorization_id")
            ),
            None,
        )
        if (
            direct_batch.get("purpose") != "diagnostic_probe"
            or direct_batch.get("round_id") != round_data["id"]
            or direct_batch.get("revision_id") != round_data["baseline_revision_id"]
            or direct_batch.get("status") != "sealed"
            or not permission
            or permission.get("kind") != "direct_component"
            or permission.get("component_identity") != round_data["external_fix_identity"]
            or permission.get("evaluation_spec_hash") != direct_manifest["evaluation_spec_hash"]
            or direct_batch.get("runner_hash") != plan["runner_hash"]
            or direct_manifest["batch_id"] != direct_batch["id"]
            or ref["evidence_id"] not in index
            or not any(
                row["record_id"] == ref["evidence_id"]
                and row["dimension"] == direct_dimension
                and row["validity"] == "valid"
                and row["verdict"] == "pass"
                for row in direct_rows
            )
        ):
            raise ATKError(
                "INCOMPLETE_EVIDENCE", "direct check needs a passing authorized component probe on the new B0"
            )
        direct_batches.append(direct_batch)
    dimension = request.get("dimension", "task_success")
    manifest, batch, slots = _assessment_slots(root, request["assessment_id"], dimension)
    if manifest["judger_readiness"] != "calibrated" or digest(manifest["judger"]) != plan["judger_hash"]:
        raise ATKError("JUDGER_INVALID", "external fix needs the frozen calibrated judger")
    if manifest["evaluation_spec_hash"] != plan["evaluation_spec_hash"]:
        raise ATKError("COMPARISON_INVALID", "evaluation_spec differs from frozen plan")
    if (
        batch.get("status") != "sealed"
        or batch.get("phase") != "external_fix"
        or batch.get("round_id") != round_data["id"]
        or batch.get("revision_id") != round_data["baseline_revision_id"]
        or batch.get("runner_hash") != plan["runner_hash"]
        or (plan.get("run_config_hash") and batch.get("run_config_hash") != plan["run_config_hash"])
        or batch.get("fixed_context_hash") != plan["fixed_context_hash"]
        or any(direct.get("run_config_hash") != batch.get("run_config_hash") for direct in direct_batches)
    ):
        raise ATKError("COMPARISON_INVALID", "external fix run differs from the frozen new B0")
    expected = {(case_id, repeat) for case_id in plan["case_ids"] for repeat in range(1, plan["final_repeats"] + 1)}
    if {(case_id, repeat) for case_id, _, repeat in slots} != expected or len(slots) != len(expected):
        raise ATKError("COMPARISON_INVALID", "external fix run lacks frozen Cases or repeats")
    required_loaded = set(plan.get("required_loaded_component_ids", []))
    result = "pass"
    if not _fixed_identity_known(batch):
        result = "insufficient"
    case_scores: dict[str, float] = defaultdict(float)
    for (case_id, _, _), attempts in slots.items():
        selected = _selected_attempt(attempts, plan.get("max_retries_per_slot", 0))
        if selected is None:
            result = "insufficient"
            continue
        execution, row, record = selected
        if (
            row["validity"] != "valid"
            or execution.get("status") in {"timeout", "infrastructure_error"}
            or row["verdict"] not in {"pass", "fail"}
            or required_loaded - _loaded_component_ids(root, batch, record)
        ):
            result = "insufficient"
        elif row["verdict"] == "fail" and result == "pass":
            result = "no_effect"
        elif row["verdict"] == "pass":
            case_scores[case_id] += 1 / plan["final_repeats"]
    validation = {
        "schema_version": 2,
        "id": new_id("validation"),
        "created_at": now(),
        "round_id": round_data["id"],
        "mode": "external_fix",
        "assessment_id": request["assessment_id"],
        "revision_id": batch["revision_id"],
        "final_commit": round_data["current_commit"],
        "component_identity": round_data["external_fix_identity"],
        "direct_evidence_refs": refs,
        "direct_assessment_id": request["direct_assessment_id"],
        "result": result,
        "case_ids": sorted(plan["case_ids"]),
        "case_scores": dict(case_scores),
        "plan_hash": digest(plan),
    }
    write_json(folder / "validations" / validation["id"] / "validation.json", validation, immutable=True)
    return validation


def finish_round(repo: Path, root: Path, request: dict) -> dict:
    with locked(root):
        return _finish_round_locked(repo, root, request)


def _finish_round_locked(repo: Path, root: Path, request: dict) -> dict:
    folder = _round_folder(root, request["round_id"])
    value = read_json(folder / "round.json")
    final_actions = {"complete", "complete_with_override", "complete_external_fix", "close_without_adoption"}
    saved = [
        (path, decision)
        for path in (folder / "decisions").glob("*.json")
        if (decision := read_json(path)).get("action") in final_actions
    ]
    if len(saved) > 1:
        raise ATKError("WORKSPACE_CONFLICT", "multiple final decisions need inspection")
    previous = saved[0][1] if saved else None
    if previous and (
        previous.get("id") != saved[0][0].stem
        or previous.get("round_id") != value["id"]
        or previous.get("action") != request.get("action")
        or previous.get("reason") != request.get("reason")
        or previous.get("validation_id") != request.get("validation_id")
        or previous.get("validation_missing_reason") != request.get("validation_missing_reason")
        or previous.get("override_authorization")
        != (request.get("override_authorization") if request.get("action") == "complete_with_override" else None)
        or previous.get("after_commit") != value["current_commit"]
    ):
        raise ATKError("WORKSPACE_CONFLICT", "saved final decision differs from the retry")
    if value["status"] not in {"ready", "optimizing"}:
        if (
            previous
            and value["status"]
            == {
                "complete": "completed",
                "complete_with_override": "completed_with_override",
                "complete_external_fix": "completed",
                "close_without_adoption": "closed_without_adoption",
            }[previous["action"]]
        ):
            verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
            return previous
        raise ATKError("WORKSPACE_CONFLICT", "round cannot be closed from its current state")
    verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
    if value["pending_candidate_id"] or changed_paths(repo) != set(value["baseline_untracked"]):
        raise ATKError("WORKSPACE_CONFLICT", "pending candidate or extra workspace changes")
    action = request["action"]
    if action == "complete_external_fix":
        validation = read_json(folder / "validations" / safe_id(request["validation_id"]) / "validation.json")
        if (
            not value.get("external_fix_identity")
            or value["active_candidate_ids"]
            or validation["mode"] != "external_fix"
            or validation["result"] != "pass"
            or validation["round_id"] != value["id"]
            or validation["revision_id"] != value["current_revision_id"]
            or validation["component_identity"] != value["external_fix_identity"]
            or validation["final_commit"] != head(repo)
            or validation["plan_hash"] != digest(read_json(folder / "plan.json"))
        ):
            raise ATKError("COMPARISON_INVALID", "external fix lacks passing direct and end-to-end checks")
        value["status"] = "completed"
    elif action in {"complete", "complete_with_override"}:
        if value.get("external_fix_identity"):
            raise ATKError("COMPARISON_INVALID", "external fix Round needs its dedicated completion gate")
        validation_id = request.get("validation_id")
        validation = (
            read_json(folder / "validations" / safe_id(validation_id) / "validation.json") if validation_id else None
        )
        if validation and (
            validation["mode"] != "final"
            or validation["round_id"] != value["id"]
            or validation["left_revision_id"] != value["baseline_revision_id"]
            or validation["right_revision_id"] != value["current_revision_id"]
            or validation["final_commit"] != head(repo)
            or validation["plan_hash"] != digest(read_json(folder / "plan.json"))
        ):
            raise ATKError("COMPARISON_INVALID", "final Validation does not match current Round and Revision")
        if action == "complete":
            if not validation or validation["result"] != "pass":
                raise ATKError("COMPARISON_INVALID", "current commit lacks passing final validation")
            value["status"] = "completed"
        else:
            if not value["active_candidate_ids"] or (validation and validation["result"] == "pass"):
                raise ATKError("COMPARISON_INVALID", "override is only for an unverified adopted Revision")
            if not validation and not request.get("validation_missing_reason"):
                raise ATKError("INCOMPLETE_EVIDENCE", "missing final Validation needs a recorded reason")
            require_override(request, "retain_without_final_pass")
            value["status"] = "completed_with_override"
    elif action == "close_without_adoption" and not value["active_candidate_ids"]:
        if value["current_revision_id"] != value["baseline_revision_id"] or git(
            repo, "rev-parse", "HEAD^{tree}"
        ) != git(repo, "rev-parse", f"{value['baseline_commit']}^{{tree}}"):
            raise ATKError("WORKSPACE_CONFLICT", "empty Round does not match B0 content")
        value["status"] = "closed_without_adoption"
    else:
        raise ATKError("WORKSPACE_CONFLICT", "unsupported final action")
    value["state_version"] += 1
    decision = {
        "schema_version": 2,
        "id": new_id("decision"),
        "created_at": now(),
        "round_id": value["id"],
        "action": action,
        "after_commit": value["current_commit"],
        "validation_id": request.get("validation_id"),
        "validation_result": validation["result"]
        if action in {"complete", "complete_with_override", "complete_external_fix"} and validation
        else None,
        "validation_missing_reason": request.get("validation_missing_reason"),
        "override": action == "complete_with_override",
        "override_authorization": request.get("override_authorization") if action == "complete_with_override" else None,
        "reason": request["reason"],
    }
    if previous:
        if {key: val for key, val in previous.items() if key not in {"id", "created_at"}} != {
            key: val for key, val in decision.items() if key not in {"id", "created_at"}
        }:
            raise ATKError("WORKSPACE_CONFLICT", "saved final decision differs from the retry")
        decision = previous
    else:
        write_json(folder / "decisions" / f"{decision['id']}.json", decision, immutable=True)
    write_json(folder / "round.json", value)
    return decision
