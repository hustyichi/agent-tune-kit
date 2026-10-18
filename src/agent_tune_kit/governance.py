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
    new_id,
    now,
    read_assessment,
    read_json,
    safe_id,
    validate_evidence,
    write_json,
)
from .execution import source_groups_for_evidence


def _round_folder(root: Path, round_id: str) -> Path:
    return root / "rounds" / safe_id(round_id)


def store_diagnosis(root: Path, request: dict) -> dict:
    folder = _round_folder(root, request["round_id"])
    round_data = read_json(folder / "round.json")
    if round_data["status"] == "paused":
        raise ATKError("WORKSPACE_CONFLICT", "更新诊断前请先恢复 Round")
    issues = request["issues"]
    if not issues:
        raise ATKError("INCOMPLETE_EVIDENCE", "诊断至少需要一个 Issue")
    prepared = []
    seen_ids = set()
    for issue in issues:
        if not isinstance(issue, dict):
            raise ATKError("INCOMPLETE_EVIDENCE", "Issue 必须是对象")
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
            raise ATKError("INCOMPLETE_EVIDENCE", f"Issue 缺少字段：{sorted(required - issue.keys())}")
        safe_id(issue["id"])
        if issue["id"] in seen_ids:
            raise ATKError("INCOMPLETE_EVIDENCE", "同一次诊断中的 Issue ID 重复")
        seen_ids.add(issue["id"])
        if issue["root_cause_status"] not in {"hypothesis", "supported", "intervention_supported", "inconclusive"}:
            raise ATKError("INCOMPLETE_EVIDENCE", "根因状态无效")
        if issue["disposition"] not in {
            "local_candidate",
            "external_handoff",
            "judge_revision",
            "needs_evidence",
            "no_action",
        }:
            raise ATKError("INCOMPLETE_EVIDENCE", "Issue 处理方式无效")
        if issue["root_cause_status"] == "supported" and (
            not issue["mechanism_evidence_refs"]
            or not any(check.get("status") == "completed" for check in issue["checks"])
            or not issue.get("competing_explanations_addressed")
        ):
            raise ATKError("INCOMPLETE_EVIDENCE", "已支持的机制需要直接证据和已执行检查")
        if issue["root_cause_status"] == "intervention_supported":
            if not issue["intervention_validation_refs"]:
                raise ATKError("INCOMPLETE_EVIDENCE", "干预需要 Validation 引用")
            for validation_id in issue["intervention_validation_refs"]:
                validation = read_json(folder / "validations" / safe_id(validation_id) / "validation.json")
                if validation["result"] != "pass":
                    raise ATKError("COMPARISON_INVALID", "干预 Validation 未通过")
        if issue["root_cause_status"] in {"hypothesis", "inconclusive"} and not issue["next_action"]:
            raise ATKError("INCOMPLETE_EVIDENCE", "未解决诊断需要下一步检查")
        if issue["disposition"] == "external_handoff":
            handoff = issue.get("handoff", {})
            if not handoff.get("trigger_input") or not handoff.get("expected") or not handoff.get("actual"):
                raise ATKError("INCOMPLETE_EVIDENCE", "外部交接需要触发输入及预期／实际行为")
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
                    "解决问题需要关联新 Round、修复身份、直接检查和端到端 Validation",
                )
            new_round = read_json(_round_folder(root, fix["new_round_id"]) / "round.json")
            if (
                new_round.get("previous_round_id") != round_data["id"]
                or issue["id"] not in new_round["issues"]
                or new_round.get("external_fix_identity") != fix["component_identity"]
            ):
                raise ATKError("INCOMPLETE_EVIDENCE", "修复 Round 未关联 Issue 和组件身份")
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
                raise ATKError("COMPARISON_INVALID", "外部修复端到端 Validation 未覆盖受影响 Case")
        for ref in issue["evidence_refs"] + issue["mechanism_evidence_refs"]:
            if not isinstance(ref, dict) or not ref.get("batch_id") or not ref.get("evidence_id"):
                raise ATKError("INCOMPLETE_EVIDENCE", "Issue 证据引用格式错误")
            _, _, index = validate_evidence(root, ref["batch_id"])
            if ref["evidence_id"] not in index:
                raise ATKError("INCOMPLETE_EVIDENCE", "Issue 证据引用缺失")
        for check in issue["checks"]:
            if not isinstance(check, dict) or check.get("status") not in {
                "not_run",
                "completed",
                "failed",
                "inconclusive",
            }:
                raise ATKError("INCOMPLETE_EVIDENCE", "诊断检查状态无效")
            if check["status"] == "not_run" and not check.get("reason"):
                raise ATKError("INCOMPLETE_EVIDENCE", "未运行检查需要记录原因")
            if check["status"] == "completed" and (
                not check.get("expected") or not check.get("actual") or not check.get("evidence_refs")
            ):
                raise ATKError("INCOMPLETE_EVIDENCE", "已完成检查需要预期、实际和证据引用")
            if not isinstance(check.get("evidence_refs", []), list):
                raise ATKError("INCOMPLETE_EVIDENCE", "诊断检查证据引用必须是列表")
            for ref in check.get("evidence_refs", []):
                if (
                    not isinstance(ref, dict)
                    or not ref.get("batch_id")
                    or ref.get("evidence_id") not in validate_evidence(root, ref["batch_id"])[2]
                ):
                    raise ATKError("INCOMPLETE_EVIDENCE", "诊断检查证据引用缺失")
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
        prepared.append((path, value))
    for path, value in prepared:
        write_json(path, value, immutable=True)
    return {"issues": [value for _, value in prepared]}


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
        raise ATKError("INCOMPLETE_EVIDENCE", "知识元数据不完整")
    if (
        not value["applicability"]
        or any(
            not isinstance(value[key], dict)
            or not value[key]
            or any(not isinstance(item, str) or not item for item in value[key].values())
            for key in ("component_hashes", "contract_hashes")
        )
        or not isinstance(value["judger_hash"], str)
        or not value["judger_hash"]
        or not isinstance(value["body"], str)
        or any(
            not isinstance(value[key], list)
            for key in ("evidence_refs", "contrary_refs", "candidate_ids", "validation_ids")
        )
        or any(not isinstance(item, str) for key in ("candidate_ids", "validation_ids") for item in value[key])
    ):
        raise ATKError("INCOMPLETE_EVIDENCE", "必须提供知识适用性和身份指纹")
    if value["status"] == "validated_in_scope" and not (value["evidence_refs"] or value["validation_ids"]):
        raise ATKError("INCOMPLETE_EVIDENCE", "已验证知识需要支持证据")
    if value["status"] == "contradicted" and not value["contrary_refs"]:
        raise ATKError("INCOMPLETE_EVIDENCE", "被反驳知识需要反向证据")
    for key, folder_name, filename, label in (
        ("candidate_ids", "candidates", "candidate.json", "Candidate"),
        ("validation_ids", "validations", "validation.json", "Validation"),
    ):
        if len(value[key]) != len(set(value[key])):
            raise ATKError("INCOMPLETE_EVIDENCE", f"重复的知识 {label} 引用")
        for artifact_id in value[key]:
            matches = list((root / "rounds").glob(f"*/{folder_name}/{safe_id(artifact_id)}/{filename}"))
            if len(matches) != 1:
                raise ATKError("INCOMPLETE_EVIDENCE", f"知识 {label} 引用缺失")
            linked = read_json(matches[0])
            if linked.get("id") != artifact_id or linked.get("schema_version") != 2:
                raise ATKError("INCOMPLETE_EVIDENCE", f"知识 {label} 引用身份无效")
    used_groups = source_groups_for_evidence(root, value["evidence_refs"] + value["contrary_refs"])
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
            "applicability_hash": digest(value["applicability"]),
            "optimization_source_group_ids": used_groups,
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
        raise ATKError("INCOMPLETE_EVIDENCE", "知识条目缺失")
    value = read_json(revisions[-1])
    current = request["current_identity"]
    status = (
        "validated_in_scope"
        if value["status"] == "validated_in_scope"
        and all(value[key] == current.get(key) for key in ("component_hashes", "contract_hashes", "judger_hash"))
        and current.get("applicability") is not None
        and value.get("applicability_hash", digest(value["applicability"])) == digest(current["applicability"])
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
            raise ATKError("COMPARISON_INVALID", "比较需要正式本地 Execution")
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
        if len(children) != 1 or current[0].get("status") not in {"timeout", "infra_error"}:
            return None
        current = children[0]
        if current[0]["id"] in visited:
            return None
        visited.add(current[0]["id"])
    return current


def _required_dimensions(plan: dict, spec: dict, requested: str | None = None) -> list[str]:
    primary = plan["primary_dimension"]
    if requested is not None and requested != primary:
        raise ATKError("COMPARISON_INVALID", "比较维度与冻结主维度不一致")
    dimensions = spec["dimensions"]
    required = plan.get("required_dimensions") or dimensions
    if primary not in dimensions or not set(required) <= set(dimensions):
        raise ATKError("COMPARISON_INVALID", "冻结维度与 Assessment 规格不一致")
    return sorted(set(required) | {primary})


def _dimension_verdicts(
    root: Path, assessment_id: str, dimension: str, cases: set[str], repeats: int, max_retries: int
) -> dict[str, list[str]] | None:
    _, _, slots = _assessment_slots(root, assessment_id, dimension)
    outcome = {case_id: [] for case_id in cases}
    for case_id in cases:
        for repeat in range(1, repeats + 1):
            matches = [attempts for (name, _, index), attempts in slots.items() if name == case_id and index == repeat]
            if len(matches) != 1 or (selected := _selected_attempt(matches[0], max_retries)) is None:
                return None
            execution, row, _ = selected
            if (
                row["validity"] != "valid"
                or row["verdict"] not in {"pass", "fail"}
                or execution["status"] not in {"completed", "agent_error"}
            ):
                return None
            outcome[case_id].append(row["verdict"])
    return outcome


def _metric_total(slots: dict, cases: set[str], metric: str, source: str | None) -> float | None:
    if source is None:
        return None
    values = []
    for key, attempts in slots.items():
        if key[0] not in cases:
            continue
        for _, _, record in attempts:
            metrics = record.get("metrics")
            sources = record.get("metric_sources")
            if not isinstance(metrics, dict) or not isinstance(sources, dict):
                return None
            value = metrics.get(metric)
            if (
                sources.get(metric) != source
                or type(value) not in {int, float}
                or not math.isfinite(value)
                or value < 0
            ):
                return None
            values.append(float(value))
    return sum(values) if values else None


def _trusted_metric_source(root: Path, batch: dict, metric: str) -> str | None:
    config_path = root / "evidence" / safe_id(batch["id"]) / "run-config.json"
    if not config_path.is_file():
        return None
    config = read_json(config_path)
    if digest(config) != batch.get("run_config_hash"):
        return None
    if metric == "duration_seconds":
        return "runner_clock"
    sources = config.get("metric_sources")
    source = sources.get(metric, {}) if isinstance(sources, dict) else {}
    return (
        source.get("source")
        if isinstance(source, dict)
        and isinstance(source.get("source"), str)
        and source["source"].strip()
        and source["source"] not in {"agent_sidecar", "runner_clock"}
        and isinstance(source.get("evidence_ref"), str)
        and source["evidence_ref"].strip()
        else None
    )


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
    folder = _round_folder(root, request["round_id"])
    round_data = read_json(folder / "round.json")
    if round_data["status"] not in {"ready", "optimizing", "finalizing"}:
        raise ATKError("WORKSPACE_CONFLICT", "Round 已暂停、未冻结或已关闭")
    plan = read_json(folder / "plan.json")
    if not plan.get("case_fingerprints"):
        raise ATKError("COMPARISON_INVALID", "Round 没有冻结的 Case 内容身份")
    if round_data.get("external_fix_identity"):
        raise ATKError("COMPARISON_INVALID", "外部修复 Round 需要仅验证新 B0")
    mode = request["mode"]
    if round_data["status"] == "finalizing" and mode != "final":
        raise ATKError("WORKSPACE_CONFLICT", "正在收尾的 Round 不能接受增量验证")
    if mode not in {"incremental", "final"}:
        raise ATKError("COMPARISON_INVALID", "mode 必须是 incremental 或 final")
    dimension = plan["primary_dimension"]
    left_manifest, left_batch, left_slots = _assessment_slots(root, request["left_assessment_id"], dimension)
    right_manifest, right_batch, right_slots = _assessment_slots(root, request["right_assessment_id"], dimension)
    required_dimensions = _required_dimensions(plan, left_manifest["evaluation_spec"], request.get("dimension"))
    for manifest in (left_manifest, right_manifest):
        if manifest["judger_readiness"] != "calibrated" or digest(manifest["judger"]) != plan["judger_hash"]:
            raise ATKError("JUDGER_INVALID", "比较需要冻结且已校准的 judger")
        if manifest["evaluation_spec_hash"] != plan["evaluation_spec_hash"]:
            raise ATKError("COMPARISON_INVALID", "evaluation_spec 与冻结计划不一致")
    if left_batch.get("run_config_hash") != right_batch.get("run_config_hash"):
        raise ATKError("COMPARISON_INVALID", "两侧 runner 配置发生变化")
    if plan.get("run_config_hash") and left_batch.get("run_config_hash") != plan["run_config_hash"]:
        raise ATKError("COMPARISON_INVALID", "runner 配置与冻结 Round 不一致")
    for batch in (left_batch, right_batch):
        if (
            batch.get("concurrency", 1) != plan.get("concurrency", 1)
            or batch.get("timeout_seconds") != plan["timeout_seconds"]
        ):
            raise ATKError("COMPARISON_INVALID", "执行 concurrency 或超时与冻结计划不一致")
        if (
            batch.get("runner_hash") != plan["runner_hash"]
            or batch.get("fixed_context_hash") != plan["fixed_context_hash"]
        ):
            raise ATKError("COMPARISON_INVALID", "runner 或固定组件身份与冻结计划不一致")
    limitations = request.get("limitations", [])
    if not isinstance(limitations, list):
        raise ATKError("COMPARISON_INVALID", "limitations 必须是列表")
    limitations = list(limitations)
    result = "insufficient" if left_batch.get("status") != "sealed" or right_batch.get("status") != "sealed" else None
    if result:
        limitations.append("一个或两个执行批次尚未封存")
    if not _fixed_identity_known(left_batch) or not _fixed_identity_known(right_batch):
        result = "insufficient"
        limitations.append("固定组件身份尚未确认")
    if left_batch.get("phase", "incremental") != mode or right_batch.get("phase", "incremental") != mode:
        result = "insufficient"
        limitations.append("执行阶段与请求的比较不一致")
    if set(left_slots) != set(right_slots):
        result = "insufficient"
        limitations.append("基线与候选的配对 Case 槽位不一致")
    if any(
        plan["case_fingerprints"].get(case_id) != fingerprint
        for slots in (left_slots, right_slots)
        for case_id, fingerprint, _ in slots
    ):
        result = "insufficient"
        limitations.append("执行 Case 内容与冻结数据集不一致")
    candidate = None
    issue_id = None
    if mode == "incremental":
        candidate = read_json(folder / "candidates" / safe_id(request["candidate_id"]) / "candidate.json")
        issue_id = candidate["primary_issue_id"]
        if request.get("issue_id", issue_id) != issue_id:
            raise ATKError("COMPARISON_INVALID", "验证 Issue 与封存 Candidate 不一致")
        if candidate.get("target_case_ids") != sorted(plan.get("target_case_ids_by_issue", {}).get(issue_id, [])):
            raise ATKError("COMPARISON_INVALID", "Candidate 目标 Case 与冻结计划不一致")
    expected_cases = set(
        plan["case_ids"] if mode == "final" else plan["protection_case_ids"] + candidate["target_case_ids"]
    )
    if mode == "incremental":
        for accepted_id in round_data["active_candidate_ids"]:
            decision = read_json(folder / "candidates" / safe_id(accepted_id) / "decision.json")
            prior = read_json(folder / "validations" / safe_id(decision["validation_id"]) / "validation.json")
            expected_cases.update(prior["fixed_case_ids"])
    if not expected_cases or expected_cases - {slot[0] for slot in left_slots}:
        result = "insufficient"
        limitations.append("基线缺少冻结目标或保护 Case")
    expected_repeats = plan["final_repeats"] if mode == "final" else plan.get("incremental_repeats", 1)
    if any(
        {repeat for case, _, repeat in left_slots if case == case_id} != set(range(1, expected_repeats + 1))
        for case_id in expected_cases
    ):
        result = "insufficient"
        limitations.append("基线重复次数与冻结计划不一致")
    secondary_regression = False
    for required_dimension in required_dimensions:
        if required_dimension == dimension:
            continue
        left_verdicts = _dimension_verdicts(
            root,
            request["left_assessment_id"],
            required_dimension,
            expected_cases,
            expected_repeats,
            plan.get("max_retries_per_slot", 0),
        )
        right_verdicts = _dimension_verdicts(
            root,
            request["right_assessment_id"],
            required_dimension,
            expected_cases,
            expected_repeats,
            plan.get("max_retries_per_slot", 0),
        )
        if left_verdicts is None or right_verdicts is None:
            result = "insufficient"
            limitations.append(f"必需维度 {required_dimension} 存在未知或无效判定")
        elif any(
            right_verdicts[case_id].count("pass") < left_verdicts[case_id].count("pass") for case_id in expected_cases
        ):
            secondary_regression = True
            limitations.append(f"必需维度 {required_dimension} 发生退化")
    max_retries = plan.get("max_retries_per_slot", 0)
    case_results = {}
    case_distributions = {}
    rows = []
    for key in sorted(set(left_slots) & set(right_slots)):
        if key[0] not in expected_cases:
            continue
        sides = []
        for side_name, side in (("基线", left_slots), ("候选", right_slots)):
            selected = _selected_attempt(side[key], max_retries)
            if selected is None:
                result = "insufficient"
                limitations.append(f"{side_name} {key[0]} 第 {key[2]} 次重复没有可用执行")
                sides.append(None)
                continue
            execution, row, _ = selected
            sides.append(
                row["verdict"]
                if row["validity"] == "valid"
                and row["verdict"] in {"pass", "fail"}
                and execution.get("status") in {"completed", "agent_error"}
                else None
            )
        rows.append(
            {"case_id": key[0], "fingerprint": key[1], "repeat_index": key[2], "left": sides[0], "right": sides[1]}
        )
        required_loaded = set(plan.get("required_loaded_component_ids", []))
        if required_loaded:
            for side_name, slots, batch in (
                ("基线", left_slots, left_batch),
                ("候选", right_slots, right_batch),
            ):
                selected = _selected_attempt(slots[key], max_retries)
                for _, _, record in [selected] if selected else []:
                    missing_loaded = required_loaded - _loaded_component_ids(root, batch, record)
                    if missing_loaded:
                        result = "insufficient"
                        limitations.append(
                            f"{side_name} {key[0]} 第 {key[2]} 次重复缺少已核验加载证据："
                            + ", ".join(sorted(missing_loaded))
                        )
        if any(verdict not in {"pass", "fail"} for verdict in sides):
            result = "insufficient"
            limitations.append(f"{key[0]} 第 {key[2]} 次重复存在未知或无效判定")
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
            limitations.append(f"{case_id} 缺少完整配对重复")
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
    target_cases = set(candidate["target_case_ids"] if candidate else expected_cases)
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
            "left_total": _metric_total(
                left_slots, expected_cases, metric, _trusted_metric_source(root, left_batch, metric)
            ),
            "right_total": _metric_total(
                right_slots, expected_cases, metric, _trusted_metric_source(root, right_batch, metric)
            ),
        }
        for metric in sorted(metric_names)
    }
    if any(value is None for name in required_metrics for value in metrics[name].values()):
        result = "insufficient"
        limitations.append("必需的成本、耗时或工具调用指标缺少可信来源或有效值")
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
        if regressed or secondary_regression or exceeds_limit:
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
        "primary_dimension": dimension,
        "required_dimensions": required_dimensions,
        "metrics": metrics,
        "evidence_level": "repeated" if expected_repeats > 1 else "single_run",
        "plan_hash": digest(plan),
        "limitations": limitations,
    }
    if mode == "incremental":
        validation["issue_id"] = issue_id
        validation["target_case_ids"] = candidate["target_case_ids"]
        if (
            validation["left_commit"] != candidate["parent_commit"]
            or validation["left_revision_id"] != candidate["parent_revision_id"]
            or validation["right_revision_id"] != candidate["revision_id"]
        ):
            raise ATKError("REVISION_MISMATCH", "Assessment Revision 与候选不匹配")
        validation["candidate_id"] = candidate["id"]
    else:
        if (
            validation["left_commit"] != round_data["baseline_commit"]
            or validation["left_revision_id"] != round_data["baseline_revision_id"]
            or validation["right_revision_id"] != round_data.get("current_revision_id")
        ):
            raise ATKError("REVISION_MISMATCH", "最终验证必须比较 B0 与 current Revision")
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
    folder = _round_folder(root, request["round_id"])
    round_data = read_json(folder / "round.json")
    plan = read_json(folder / "plan.json")
    if not plan.get("case_fingerprints"):
        raise ATKError("COMPARISON_INVALID", "Round 没有冻结的 Case 内容身份")
    if not round_data.get("external_fix_identity") or round_data["status"] not in {"ready", "optimizing", "finalizing"}:
        raise ATKError("WORKSPACE_CONFLICT", "外部修复验证需要尚未关闭的关联 Round")
    refs = request.get("direct_evidence_refs", [])
    if not isinstance(refs, list) or not refs or not request.get("direct_assessment_id"):
        raise ATKError("INCOMPLETE_EVIDENCE", "外部修复需要独立的直接检查证据")
    direct_manifest, direct_rows = read_assessment(root, request["direct_assessment_id"])
    if direct_manifest["judger_readiness"] != "calibrated":
        raise ATKError("JUDGER_INVALID", "直接组件检查需要已校准 Assessment")
    if "direct_dimension" in request:
        raise ATKError("COMPARISON_INVALID", "直接检查维度由探针许可冻结")
    direct_batches = []
    direct_slots = set()
    direct_permission = None
    for ref in refs:
        if not isinstance(ref, dict) or not ref.get("batch_id") or not ref.get("evidence_id"):
            raise ATKError("INCOMPLETE_EVIDENCE", "直接检查证据引用格式错误")
        direct_batch, direct_records, index = validate_evidence(root, ref["batch_id"])
        permission = next(
            (
                item
                for item in plan.get("probe_permissions", [])
                if item.get("id") == direct_batch.get("probe_authorization_id")
            ),
            None,
        )
        required_direct_dimensions = (
            permission.get("required_dimensions", direct_manifest["evaluation_spec"]["dimensions"])
            if permission
            else []
        )
        record = direct_records.get(ref["evidence_id"], {})
        execution = record.get("execution", {})
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
            or execution.get("status") != "completed"
            or not isinstance(required_direct_dimensions, list)
            or not required_direct_dimensions
            or any(not isinstance(item, str) for item in required_direct_dimensions)
            or not set(required_direct_dimensions) <= set(direct_manifest["evaluation_spec"]["dimensions"])
            or not isinstance(permission.get("case_ids"), list)
            or not permission["case_ids"]
            or any(not isinstance(item, str) for item in permission["case_ids"])
            or len(permission["case_ids"]) != len(set(permission["case_ids"]))
            or type(permission.get("required_repeats", 1)) is not int
            or permission.get("required_repeats", 1) < 1
            or direct_permission is not None
            and permission["id"] != direct_permission["id"]
            or any(
                not any(
                    row["record_id"] == ref["evidence_id"]
                    and row["dimension"] == dimension
                    and row["validity"] == "valid"
                    and row["verdict"] == "pass"
                    for row in direct_rows
                )
                for dimension in required_direct_dimensions
            )
        ):
            raise ATKError("INCOMPLETE_EVIDENCE", "直接检查需要在新 B0 上通过获准组件探针")
        direct_permission = permission
        slot = (execution["case_id"], execution["repeat_index"])
        if slot in direct_slots or plan["case_fingerprints"].get(slot[0]) != execution["case_fingerprint"]:
            raise ATKError("INCOMPLETE_EVIDENCE", "直接检查缺少冻结 Case 覆盖")
        direct_slots.add(slot)
        direct_batches.append(direct_batch)
    expected_direct_slots = {
        (case_id, repeat)
        for case_id in direct_permission["case_ids"]
        for repeat in range(1, direct_permission.get("required_repeats", 1) + 1)
    }
    if direct_slots != expected_direct_slots:
        raise ATKError("INCOMPLETE_EVIDENCE", "直接检查缺少冻结 Case 覆盖")
    dimension = plan["primary_dimension"]
    manifest, batch, slots = _assessment_slots(root, request["assessment_id"], dimension)
    required_dimensions = _required_dimensions(plan, manifest["evaluation_spec"], request.get("dimension"))
    if manifest["judger_readiness"] != "calibrated" or digest(manifest["judger"]) != plan["judger_hash"]:
        raise ATKError("JUDGER_INVALID", "外部修复需要冻结且已校准的 judger")
    if manifest["evaluation_spec_hash"] != plan["evaluation_spec_hash"]:
        raise ATKError("COMPARISON_INVALID", "evaluation_spec 与冻结计划不一致")
    if (
        batch.get("status") != "sealed"
        or batch.get("phase") != "external_fix"
        or batch.get("concurrency", 1) != plan.get("concurrency", 1)
        or batch.get("timeout_seconds") != plan["timeout_seconds"]
        or batch.get("round_id") != round_data["id"]
        or batch.get("revision_id") != round_data["baseline_revision_id"]
        or batch.get("runner_hash") != plan["runner_hash"]
        or (plan.get("run_config_hash") and batch.get("run_config_hash") != plan["run_config_hash"])
        or batch.get("fixed_context_hash") != plan["fixed_context_hash"]
        or any(direct.get("run_config_hash") != batch.get("run_config_hash") for direct in direct_batches)
    ):
        raise ATKError("COMPARISON_INVALID", "外部修复运行与冻结的新 B0 不一致")
    expected = {(case_id, repeat) for case_id in plan["case_ids"] for repeat in range(1, plan["final_repeats"] + 1)}
    if (
        {(case_id, repeat) for case_id, _, repeat in slots} != expected
        or len(slots) != len(expected)
        or any(plan["case_fingerprints"].get(case_id) != fingerprint for case_id, fingerprint, _ in slots)
    ):
        raise ATKError("COMPARISON_INVALID", "外部修复运行缺少冻结 Case 或重复次数")
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
            or execution.get("status") not in {"completed", "agent_error"}
            or row["verdict"] not in {"pass", "fail"}
            or required_loaded - _loaded_component_ids(root, batch, record)
        ):
            result = "insufficient"
        elif row["verdict"] == "fail" and result == "pass":
            result = "no_effect"
        elif row["verdict"] == "pass":
            case_scores[case_id] += 1 / plan["final_repeats"]
    for required_dimension in required_dimensions:
        if required_dimension == dimension:
            continue
        verdicts = _dimension_verdicts(
            root,
            request["assessment_id"],
            required_dimension,
            set(plan["case_ids"]),
            plan["final_repeats"],
            plan.get("max_retries_per_slot", 0),
        )
        if verdicts is None:
            result = "insufficient"
        elif any("fail" in values for values in verdicts.values()) and result == "pass":
            result = "no_effect"
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
        "primary_dimension": dimension,
        "required_dimensions": required_dimensions,
        "case_ids": sorted(plan["case_ids"]),
        "case_scores": dict(case_scores),
        "plan_hash": digest(plan),
    }
    write_json(folder / "validations" / validation["id"] / "validation.json", validation, immutable=True)
    return validation


def finish_round(repo: Path, root: Path, request: dict) -> dict:
    folder = _round_folder(root, request["round_id"])
    value = read_json(folder / "round.json")
    final_actions = {"complete", "complete_with_override", "complete_external_fix", "close_without_adoption"}
    saved = [
        (path, decision)
        for path in (folder / "decisions").glob("*.json")
        if (decision := read_json(path)).get("action") in final_actions
    ]
    if len(saved) > 1:
        raise ATKError("WORKSPACE_CONFLICT", "存在多个最终决策，需要检查")
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
        raise ATKError("WORKSPACE_CONFLICT", "已保存最终决策与重试不一致")
    if value["status"] not in {"analysis_only", "ready", "optimizing", "finalizing"}:
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
            if value["baseline_commit"] is not None:
                verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
            return previous
        raise ATKError("WORKSPACE_CONFLICT", "当前状态不允许关闭 Round")
    if value["status"] == "analysis_only" and request.get("action") != "close_without_adoption":
        raise ATKError("WORKSPACE_CONFLICT", "仅分析 Round 只能以无采用关闭")
    if value["baseline_commit"] is not None:
        verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
    if value["pending_candidate_id"] or (
        value["baseline_commit"] is not None and changed_paths(repo) != set(value["baseline_untracked"])
    ):
        raise ATKError("WORKSPACE_CONFLICT", "存在未决候选或额外工作区修改")
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
            raise ATKError("COMPARISON_INVALID", "外部修复缺少通过的直接和端到端检查")
        value["status"] = "completed"
    elif action in {"complete", "complete_with_override"}:
        if value.get("external_fix_identity"):
            raise ATKError("COMPARISON_INVALID", "外部修复 Round 需要专用完成门禁")
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
            raise ATKError("COMPARISON_INVALID", "最终 Validation 与当前 Round 和 Revision 不匹配")
        if action == "complete":
            if not validation or validation["result"] != "pass":
                raise ATKError("COMPARISON_INVALID", "当前提交缺少通过的最终验证")
            value["status"] = "completed"
        else:
            if not value["active_candidate_ids"] or (validation and validation["result"] == "pass"):
                raise ATKError("COMPARISON_INVALID", "例外仅适用于未验证的已采用 Revision")
            if not validation and not request.get("validation_missing_reason"):
                raise ATKError("INCOMPLETE_EVIDENCE", "缺少最终 Validation 时必须记录原因")
            require_override(request, "retain_without_final_pass")
            value["status"] = "completed_with_override"
    elif action == "close_without_adoption" and not value["active_candidate_ids"]:
        if value["baseline_commit"] is not None and (
            value["current_revision_id"] != value["baseline_revision_id"]
            or git(repo, "rev-parse", "HEAD^{tree}") != git(repo, "rev-parse", f"{value['baseline_commit']}^{{tree}}")
        ):
            raise ATKError("WORKSPACE_CONFLICT", "空 Round 内容与 B0 不匹配")
        value["status"] = "closed_without_adoption"
    else:
        raise ATKError("WORKSPACE_CONFLICT", "不支持的最终动作")
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
            raise ATKError("WORKSPACE_CONFLICT", "已保存最终决策与重试不一致")
        decision = previous
    else:
        write_json(folder / "decisions" / f"{decision['id']}.json", decision, immutable=True)
    write_json(folder / "round.json", value)
    return decision
