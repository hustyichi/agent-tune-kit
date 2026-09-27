"""Evidence-bound diagnosis, paired comparison, and final round decisions."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from pathlib import Path

from .checkpoints import changed_paths, head, verify_repo
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
            if new_round.get("previous_round_id") != round_data["id"]:
                raise ATKError("INCOMPLETE_EVIDENCE", "fix Round does not link back to the original Round")
            linked_validation = read_json(
                _round_folder(root, fix["new_round_id"])
                / "validations"
                / safe_id(fix["end_to_end_validation_id"])
                / "validation.json"
            )
            if linked_validation["result"] != "pass":
                raise ATKError("COMPARISON_INVALID", "external fix end-to-end Validation did not pass")
        for ref in issue["evidence_refs"] + issue["mechanism_evidence_refs"]:
            if not isinstance(ref, dict) or not ref.get("batch_id") or not ref.get("evidence_id"):
                raise ATKError("INCOMPLETE_EVIDENCE", "Issue evidence reference is malformed")
            _, _, index = validate_evidence(root, ref["batch_id"])
            if ref["evidence_id"] not in index:
                raise ATKError("INCOMPLETE_EVIDENCE", "Issue evidence reference is missing")
        for check in issue["checks"]:
            if check.get("status") not in {"not_run", "completed", "failed", "inconclusive"}:
                raise ATKError("INCOMPLETE_EVIDENCE", "invalid diagnostic check status")
            if check["status"] == "not_run" and not check.get("reason"):
                raise ATKError("INCOMPLETE_EVIDENCE", "unrun check needs a reason")
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


def compare_and_gate(root: Path, request: dict) -> dict:
    folder = _round_folder(root, request["round_id"])
    round_data = read_json(folder / "round.json")
    plan = read_json(folder / "plan.json")
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
    for batch in (left_batch, right_batch):
        if (
            batch.get("runner_hash") != plan["runner_hash"]
            or batch.get("fixed_context_hash") != plan["fixed_context_hash"]
        ):
            raise ATKError("COMPARISON_INVALID", "runner or fixed component identity differs from frozen plan")
    result = "insufficient" if left_batch.get("status") != "sealed" or right_batch.get("status") != "sealed" else None
    if set(left_slots) != set(right_slots):
        result = "insufficient"
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
    expected_repeats = plan["final_repeats"] if mode == "final" else plan.get("incremental_repeats", 1)
    if any(
        {repeat for case, _, repeat in left_slots if case == case_id} != set(range(1, expected_repeats + 1))
        for case_id in expected_cases
    ):
        result = "insufficient"
    case_results = {}
    rows = []
    for key in sorted(set(left_slots) & set(right_slots)):
        if key[0] not in expected_cases:
            continue
        sides = []
        for side in (left_slots, right_slots):
            attempts = side[key]
            if len(attempts) != 1:  # retries require a frozen aggregation policy, not best-attempt selection
                result = "insufficient"
                sides.append(None)
                continue
            _, row, _ = attempts[0]
            sides.append(row["verdict"] if row["validity"] == "valid" else None)
        rows.append(
            {"case_id": key[0], "fingerprint": key[1], "repeat_index": key[2], "left": sides[0], "right": sides[1]}
        )
        required_loaded = set(plan.get("required_loaded_component_ids", []))
        if required_loaded:
            for slots, batch in ((left_slots, left_batch), (right_slots, right_batch)):
                actual_hashes = {
                    entry["component_id"]: entry.get("actual_sha256") for entry in batch.get("actual_components", [])
                }
                for _, _, record in slots[key]:
                    loaded = {
                        entry.get("component_id")
                        for entry in record.get("loading_evidence", [])
                        if entry.get("state") in {"loaded", "invoked"}
                        and entry.get("fingerprint") == actual_hashes.get(entry.get("component_id"))
                    }
                    if required_loaded - loaded:
                        result = "insufficient"
        if any(verdict not in {"pass", "fail"} for verdict in sides):
            result = "insufficient"
    for case_id in expected_cases:
        paired = [row for row in rows if row["case_id"] == case_id]
        if len(paired) != expected_repeats or any(row["left"] is None or row["right"] is None for row in paired):
            result = "insufficient"
            continue
        case_results[case_id] = {
            "left": sum(row["left"] == "pass" for row in paired) / expected_repeats,
            "right": sum(row["right"] == "pass" for row in paired) / expected_repeats,
        }
    fixed = [case_id for case_id, score in case_results.items() if score["right"] > score["left"]]
    regressed = [case_id for case_id, score in case_results.items() if score["right"] < score["left"]]
    target_cases = set(plan.get("target_case_ids_by_issue", {}).get(request.get("issue_id"), expected_cases))
    if result is None:
        if regressed:
            result = "regression"
        elif not set(fixed) & target_cases or len(fixed) < plan.get("min_fixed_cases", 1):
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
        "evidence_level": "repeated" if expected_repeats > 1 else "single_run",
        "plan_hash": digest(plan),
        "limitations": request.get("limitations", []),
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


def finish_round(repo: Path, root: Path, request: dict) -> dict:
    with locked(root):
        return _finish_round_locked(repo, root, request)


def _finish_round_locked(repo: Path, root: Path, request: dict) -> dict:
    folder = _round_folder(root, request["round_id"])
    value = read_json(folder / "round.json")
    if value["status"] not in {"ready", "optimizing"}:
        raise ATKError("WORKSPACE_CONFLICT", "round cannot be closed from its current state")
    verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
    if value["pending_candidate_id"] or changed_paths(repo) != set(value["baseline_untracked"]):
        raise ATKError("WORKSPACE_CONFLICT", "pending candidate or extra workspace changes")
    action = request["action"]
    if action == "complete":
        validation = read_json(folder / "validations" / safe_id(request["validation_id"]) / "validation.json")
        if validation["mode"] != "final" or validation["result"] != "pass" or validation["final_commit"] != head(repo):
            raise ATKError("COMPARISON_INVALID", "current commit lacks passing final validation")
        value["status"] = "completed"
    elif action == "close_without_adoption" and not value["active_candidate_ids"]:
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
        "reason": request["reason"],
    }
    write_json(folder / "decisions" / f"{decision['id']}.json", decision, immutable=True)
    write_json(folder / "round.json", value)
    return decision
