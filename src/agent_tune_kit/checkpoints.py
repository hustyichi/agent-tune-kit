"""Git checkpoint operations for one local, serial ATK round."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import unicodedata
from collections.abc import Iterable
from contextlib import contextmanager, suppress
from pathlib import Path, PureWindowsPath

from .core import ATKError, atomic_write, digest, locked, new_id, now, read_json, safe_id, write_json


def git(repo: Path, *args: str, ok: bool = True) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)
    if ok and result.returncode:
        raise ATKError("UNSUPPORTED_GIT_STATE", result.stderr.decode(errors="replace").strip())
    return result.stdout


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "--verify", "HEAD").decode().strip()


def branch(repo: Path) -> str:
    name = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", ok=False).decode().strip()
    if not name:
        raise ATKError("UNSUPPORTED_GIT_STATE", "不支持游离 HEAD")
    return name


def changed_paths(repo: Path) -> set[str]:
    tracked = git(repo, "diff", "--name-only", "-z", "--no-ext-diff").decode().strip("\0").split("\0")
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z").decode().strip("\0").split("\0")
    return {path for path in tracked + untracked if path}


def staged_paths(repo: Path) -> set[str]:
    return set(filter(None, git(repo, "diff", "--cached", "--name-only", "-z").decode().split("\0")))


def tracked_paths(repo: Path) -> set[str]:
    return set(filter(None, git(repo, "ls-files", "-z").decode().split("\0")))


def safe_path(repo: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or "\0" in name:
        raise ATKError("SCOPE_VIOLATION", f"不安全的路径：{name}")
    relative = Path(name)
    windows = PureWindowsPath(name)
    if (
        relative.is_absolute()
        or windows.drive
        or "\\" in name
        or name != relative.as_posix()
        or ".." in relative.parts
        or any(part.casefold() in {".git", ".atk"} for part in relative.parts)
    ):
        raise ATKError("SCOPE_VIOLATION", f"不安全的路径：{name}")
    path = repo / relative
    for parent in [path, *path.parents]:
        if parent == repo.parent:
            break
        if parent.is_symlink():
            raise ATKError("SCOPE_VIOLATION", f"符号链接路径：{name}")
    return path


def content(repo: Path, name: str) -> bytes | None:
    path = safe_path(repo, name)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_mode & 0o111:
        raise ATKError("SCOPE_VIOLATION", f"仅支持非可执行的普通文本文件：{name}")
    data = path.read_bytes()
    if b"\0" in data:
        raise ATKError("SCOPE_VIOLATION", f"不支持二进制文件：{name}")
    return data


def _git_file(repo: Path, revision: str, name: str) -> bytes | None:
    found = subprocess.run(["git", "cat-file", "-e", f"{revision}:{name}"], cwd=repo, capture_output=True)
    return git(repo, "show", f"{revision}:{name}") if found.returncode == 0 else None


def _verify_restored_content(repo: Path, revision: str, paths: Iterable[str]) -> None:
    for name in paths:
        if content(repo, name) != _git_file(repo, revision, name):
            raise ATKError("GIT_OPERATION_INTERRUPTED", f"恢复内容与检查点不一致：{name}")


def _write_replay_file(repo: Path, name: str, data: bytes | None, commit: str | None = None) -> None:
    path = safe_path(repo, name)
    if data is None:
        path.unlink(missing_ok=True)
    elif commit:
        git(repo, "--literal-pathspecs", "restore", f"--source={commit}", "--worktree", "--", name)
    else:
        atomic_write(path, data)


def _sealed_replay_content(repo: Path, name: str, commit: str, folder: Path | None, files: dict) -> bytes | None:
    if name not in files:
        return _git_file(repo, commit, name)
    return (folder / "files" / name).read_bytes() if files[name]["exists"] else None


def _check_regular_parent(repo: Path, name: str) -> None:
    listing = git(repo, "ls-tree", "HEAD", "--", name).decode().strip()
    if listing:
        mode = listing.split()[0]
        if mode not in {"100644"}:
            raise ATKError("SCOPE_VIOLATION", f"不支持的父文件模式：{name}（{mode}）")


def verify_repo(repo: Path, *, expected_head: str | None = None, expected_branch: str | None = None) -> None:
    if git(repo, "rev-parse", "--is-inside-work-tree").strip() != b"true":
        raise ATKError("UNSUPPORTED_GIT_STATE", "不是 Git 工作区")
    common = Path(git(repo, "rev-parse", "--git-dir").decode().strip())
    if not common.is_absolute():
        common = repo / common
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-apply", "rebase-merge"):
        if (common / name).exists():
            raise ATKError("UNSUPPORTED_GIT_STATE", f"Git 操作正在进行：{name}")
    if git(repo, "ls-files", "-u", "-z"):
        raise ATKError("UNSUPPORTED_GIT_STATE", "暂存区存在未合并项")
    if expected_head and head(repo) != expected_head:
        raise ATKError("BASELINE_DRIFT", "HEAD 与记录的检查点不一致")
    if expected_branch and branch(repo) != expected_branch:
        raise ATKError("BASELINE_DRIFT", "分支与冻结 Round 不一致")
    if staged_paths(repo):
        raise ATKError("DIRTY_BASELINE", "检查点操作前必须处理暂存修改")
    if any(path == ".atk" or path.startswith(".atk/") for path in tracked_paths(repo)):
        raise ATKError("DIRTY_BASELINE", "开始 Round 前必须从暂存区移除已跟踪的 .atk 产物")


def _round_path(root: Path, round_id: str) -> Path:
    return root / "rounds" / safe_id(round_id)


def _round(root: Path, round_id: str) -> dict:
    return read_json(_round_path(root, round_id) / "round.json")


def _latest_issue(root: Path, round_id: str, issue_id: str) -> dict | None:
    folder = _round_path(root, round_id) / "issues" / safe_id(issue_id)
    revisions = list(folder.glob("revision-*.json"))
    return read_json(max(revisions, key=lambda path: int(path.stem.split("-")[1]))) if revisions else None


def _write_round(root: Path, round_data: dict) -> None:
    round_data["state_version"] += 1
    write_json(_round_path(root, round_data["id"]) / "round.json", round_data)


def create_round(repo: Path, root: Path, request: dict) -> dict:
    with locked(root):
        project_path = root / "project.json"
        analysis_only = request.get(
            "analysis_only", read_json(project_path).get("analysis_only", False) if project_path.exists() else False
        )
        if type(analysis_only) is not bool:
            raise ATKError("INCOMPLETE_EVIDENCE", "analysis_only 必须是布尔值")
        if analysis_only and (request.get("reuse_revision") or request.get("external_fix_identity")):
            raise ATKError("REVISION_MISMATCH", "Revision 复用和外部修复验证需要 B0")
        if not analysis_only:
            verify_repo(repo)
            if git(repo, "diff", "--name-only", "-z"):
                raise ATKError("DIRTY_BASELINE", "记录 B0 时已跟踪工作区必须干净")
        previous_round_id = request.get("previous_round_id")
        if previous_round_id:
            previous = _round(root, previous_round_id)
            if request.get("external_fix_identity") and (
                previous["status"] not in {"closed_without_adoption", "completed"}
                or not any(
                    (issue := _latest_issue(root, previous_round_id, issue_id))
                    and issue["disposition"] == "external_handoff"
                    and issue["resolution"] != "resolved"
                    for issue_id in set(request.get("issue_ids", [])) & set(previous["issues"])
                )
            ):
                raise ATKError("INCOMPLETE_EVIDENCE", "外部修复 Round 需要已关闭来源 Round 和未解决交接 Issue")
        elif request.get("external_fix_identity"):
            raise ATKError("INCOMPLETE_EVIDENCE", "外部修复需要关联此前 Round")
        for old in (root / "rounds").glob("*/round.json"):
            if read_json(old).get("status") in {"ready", "optimizing", "finalizing"}:
                raise ATKError("WORKSPACE_CONFLICT", "已有另一个活跃 Round")
        baseline = None if analysis_only else head(repo)
        baseline_revision_id = None if analysis_only else new_id("revision")
        if request.get("reuse_revision"):
            if (
                not previous_round_id
                or request.get("external_fix_identity")
                or previous["status"] not in {"completed", "completed_with_override", "closed_without_adoption"}
                or previous["current_commit"] != baseline
                or read_json(_round_path(root, previous_round_id) / "plan.json").get("run_config_hash")
                != digest(read_json(root / "project.json"))
            ):
                raise ATKError("REVISION_MISMATCH", "此前 Revision 不能跨此 Round 边界复用")
            baseline_revision_id = previous["current_revision_id"]
        round_id = new_id("round")
        value = {
            "schema_version": 2,
            "id": round_id,
            "created_at": now(),
            "state_version": 1,
            "status": "analysis_only",
            "baseline_commit": baseline,
            "baseline_revision_id": baseline_revision_id,
            "current_commit": baseline,
            "current_revision_id": None,
            "branch": None if analysis_only else branch(repo),
            "issues": request.get("issue_ids", []),
            "candidate_ids": [],
            "active_candidate_ids": [],
            "pending_candidate_id": None,
            "baseline_untracked": [] if analysis_only else sorted(changed_paths(repo)),
            "source_batch_ids": request.get("batch_ids", []),
            "source_assessment_ids": request.get("assessment_ids", []),
            "previous_round_id": request.get("previous_round_id"),
            "reused_baseline_revision": bool(request.get("reuse_revision")),
            "external_fix_identity": request.get("external_fix_identity"),
            "analysis_plan": request.get("analysis_plan", {}),
        }
        path = _round_path(root, round_id) / "round.json"
        value["current_revision_id"] = value["baseline_revision_id"]
        write_json(path, value, immutable=True)
        return value


def freeze_round(repo: Path, root: Path, request: dict) -> dict:
    with locked(root):
        value = _round(root, request["round_id"])
        if value["status"] != "analysis_only":
            raise ATKError("WORKSPACE_CONFLICT", "Round 已冻结")
        for old in (root / "rounds").glob("*/round.json"):
            other = read_json(old)
            if other["id"] != value["id"] and other.get("status") in {"ready", "optimizing", "finalizing"}:
                raise ATKError("WORKSPACE_CONFLICT", "已有另一个活跃 Round")
        if read_json(root / "project.json").get("analysis_only"):
            raise ATKError("NOT_REPLAYABLE", "冻结仅分析 Round 前先配置 runtime")
        plan = dict(request["plan"])
        if value["baseline_commit"] is None:
            verify_repo(repo)
            plan_path = _round_path(root, value["id"]) / "plan.json"
            baseline = (
                read_json(plan_path)["baseline"]
                if plan_path.exists()
                else {
                    "baseline_commit": head(repo),
                    "baseline_revision_id": new_id("revision"),
                    "branch": branch(repo),
                    "baseline_untracked": sorted(changed_paths(repo)),
                }
            )
            value.update(baseline)
            value["current_commit"] = value["baseline_commit"]
            value["current_revision_id"] = value["baseline_revision_id"]
            plan["baseline"] = baseline
        verify_repo(repo, expected_head=value["baseline_commit"], expected_branch=value["branch"])
        if git(repo, "diff", "--name-only", "-z") or changed_paths(repo) != set(value["baseline_untracked"]):
            raise ATKError("DIRTY_BASELINE", "记录 B0 后工作区已改变")
        required = {
            "allowed_paths",
            "protected_paths",
            "issue_ids",
            "evaluation_spec_hash",
            "judger_hash",
            "runner_hash",
            "fixed_context_hash",
            "case_ids",
            "protection_case_ids",
            "repeatability",
            "repeatability_basis",
            "final_repeats",
            "budget",
            "replay_preparation",
            "commit_authorized",
            "rollback_on_failure",
        }
        external_fix_round = bool(value.get("external_fix_identity"))
        if required - plan.keys() or (
            not external_fix_round and (not plan["commit_authorized"] or not plan["allowed_paths"])
        ):
            raise ATKError("INCOMPLETE_EVIDENCE", f"冻结计划不完整：{sorted(required - plan.keys())}")
        if external_fix_round:
            previous_folder = _round_path(root, value["previous_round_id"])
            previous_plan = (
                read_json(previous_folder / "plan.json")
                if (previous_folder / "plan.json").exists()
                else _round(root, value["previous_round_id"]).get("analysis_plan", {})
            )
            affected = {
                case_id
                for issue_id in value["issues"]
                if (issue := _latest_issue(root, value["previous_round_id"], issue_id))
                and issue["disposition"] == "external_handoff"
                and issue["resolution"] != "resolved"
                for case_id in issue["case_ids"]
            }
            if (
                not affected
                or not affected <= set(plan["case_ids"])
                or not set(previous_plan.get("protection_case_ids", [])) <= set(plan["protection_case_ids"])
            ):
                raise ATKError("COMPARISON_INVALID", "外部修复计划必须保留受影响和保护 Case")
        if (
            not plan["case_ids"]
            or len(plan["case_ids"]) != len(set(plan["case_ids"]))
            or not set(plan["protection_case_ids"]) <= set(plan["case_ids"])
        ):
            raise ATKError("COMPARISON_INVALID", "冻结 Case 集为空、重复或不一致")
        if not plan.get("dataset_id"):
            raise ATKError("INCOMPLETE_EVIDENCE", "冻结计划需要 dataset_id")
        from .execution import load_cases

        cases = load_cases(root, plan["dataset_id"], verify_attachments=True)
        if not set(plan["case_ids"]) <= set(cases):
            raise ATKError("COMPARISON_INVALID", "数据集缺少冻结 Case")
        fingerprints = {case_id: cases[case_id]["fingerprint"] for case_id in plan["case_ids"]}
        if plan.get("case_fingerprints", fingerprints) != fingerprints:
            raise ATKError("COMPARISON_INVALID", "冻结 Case 内容与数据集不一致")
        plan["case_fingerprints"] = fingerprints
        if plan["repeatability"] not in {"deterministic", "stochastic", "unknown"}:
            raise ATKError("COMPARISON_INVALID", "重复性状态无效")
        if not isinstance(plan["repeatability_basis"], str) or not plan["repeatability_basis"].strip():
            raise ATKError("COMPARISON_INVALID", "重复性需要已记录的证据依据")
        minimum = 1 if plan["repeatability"] == "deterministic" else 2
        if type(plan["final_repeats"]) is not int or plan["final_repeats"] < minimum:
            raise ATKError("BUDGET_EXHAUSTED", "最终重复计划低于最低要求")
        if (
            plan["repeatability"] != "deterministic"
            and plan["final_repeats"] < 3
            and (not isinstance(plan.get("repeat_plan_basis"), str) or not plan["repeat_plan_basis"].strip())
        ):
            raise ATKError("COMPARISON_INVALID", "较短随机重复计划需要记录原因")
        if type(plan.get("concurrency", 1)) is not int or plan.get("concurrency", 1) < 1:
            raise ATKError("COMPARISON_INVALID", "concurrency 必须是正整数")
        if (
            type(plan.get("timeout_seconds", 120)) not in {int, float}
            or not math.isfinite(plan.get("timeout_seconds", 120))
            or plan.get("timeout_seconds", 120) <= 0
        ):
            raise ATKError("COMPARISON_INVALID", "正式超时必须是有限正数")
        plan.setdefault("timeout_seconds", 120)
        if not isinstance(plan.get("primary_dimension", "task_success"), str) or not plan.get(
            "primary_dimension", "task_success"
        ):
            raise ATKError("COMPARISON_INVALID", "主维度无效")
        plan.setdefault("primary_dimension", "task_success")
        required_dimensions = plan.get("required_dimensions", [])
        if (
            not isinstance(required_dimensions, list)
            or any(not isinstance(item, str) or not item for item in required_dimensions)
            or len(required_dimensions) != len(set(required_dimensions))
        ):
            raise ATKError("COMPARISON_INVALID", "必需维度无效")
        budget = plan["budget"]
        if (
            not isinstance(budget, dict)
            or type(budget.get("executions")) is not int
            or budget["executions"]
            < (1 if external_fix_round else 2) * len(set(plan["case_ids"])) * plan["final_repeats"]
            or any(type(budget.get(key, 0)) is not int or budget.get(key, 0) < 0 for key in ("probes", "candidates"))
        ):
            raise ATKError("BUDGET_EXHAUSTED", "预算必须预留冻结的最终验证")
        if type(plan.get("max_retries_per_slot", 0)) is not int or plan.get("max_retries_per_slot", 0) < 0:
            raise ATKError("COMPARISON_INVALID", "max_retries_per_slot 必须非负")
        if plan.get("objective", "quality") not in {"quality", "efficiency"}:
            raise ATKError("COMPARISON_INVALID", "objective 必须是 quality 或 efficiency")
        if plan.get("objective") == "efficiency" and plan.get("efficiency_metric") not in {
            "cost",
            "duration_seconds",
            "tool_calls",
        }:
            raise ATKError("COMPARISON_INVALID", "效率目标需要受支持指标")
        if not isinstance(plan.get("metric_limits", {}), dict):
            raise ATKError("COMPARISON_INVALID", "metric_limits 必须是对象")
        for key, value_to_check in {
            **{key: plan[key] for key in ("min_primary_delta", "min_efficiency_delta") if key in plan},
            **plan.get("metric_limits", {}),
        }.items():
            if type(value_to_check) not in {int, float} or not math.isfinite(value_to_check) or value_to_check < 0:
                raise ATKError("COMPARISON_INVALID", f"{key} 必须非负")
        if set(plan.get("metric_limits", {})) - {"max_total_cost", "max_mean_duration_seconds", "max_mean_tool_calls"}:
            raise ATKError("COMPARISON_INVALID", "不支持的指标限制")
        preparation = plan["replay_preparation"]
        if not isinstance(preparation, dict) or preparation.get("mode") not in {"stateless", "command"}:
            raise ATKError("NOT_REPLAYABLE", "候选工作前必须冻结重放准备")
        if preparation["mode"] == "stateless" and not preparation.get("reason"):
            raise ATKError("NOT_REPLAYABLE", "无状态重放需要记录缓存核查依据")
        if preparation["mode"] == "command" and (
            not isinstance(preparation.get("argv"), list)
            or not preparation["argv"]
            or any(not isinstance(part, str) or not part for part in preparation["argv"])
            or type(preparation.get("timeout_seconds")) is not int
            or preparation["timeout_seconds"] < 1
        ):
            raise ATKError("NOT_REPLAYABLE", "重放准备需要命令参数及有限超时")
        dependencies = plan.get("blocked_by_issue_ids_by_issue", {})
        workaround_ids = plan.get("workaround_issue_ids", [])
        if (
            not isinstance(dependencies, dict)
            or not isinstance(workaround_ids, list)
            or set(workaround_ids) - set(plan["issue_ids"])
            or any(
                issue_id not in plan["issue_ids"]
                or not isinstance(blockers, list)
                or set(blockers) - set(plan["issue_ids"])
                for issue_id, blockers in dependencies.items()
            )
        ):
            raise ATKError("COMPARISON_INVALID", "Issue 依赖或 workaround 范围超出计划")
        project_hash = digest(read_json(root / "project.json"))
        if plan.get("run_config_hash", project_hash) != project_hash:
            raise ATKError("COMPARISON_INVALID", "冻结 runner 配置与项目不一致")
        plan["run_config_hash"] = project_hash
        plan_path = _round_path(root, value["id"]) / "plan.json"
        if plan_path.exists():
            if read_json(plan_path) != plan:
                raise ATKError("WORKSPACE_CONFLICT", "部分冻结计划与重试不一致")
        else:
            write_json(plan_path, plan, immutable=True)
        value["status"] = "ready"
        _write_round(root, value)
        return value


def _round_workspace_state(repo: Path, root: Path, value: dict) -> dict:
    folder = _round_path(root, value["id"])
    for path in (folder / "operations").glob("*.json"):
        if read_json(path).get("stage") not in {"complete", "aborted"}:
            raise ATKError("GIT_OPERATION_INTERRUPTED", f"继续前先恢复操作：{path.stem}")
    if value["baseline_commit"] is None:
        return {}
    verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
    paths = set()
    if value["pending_candidate_id"]:
        candidate_folder = folder / "candidates" / value["pending_candidate_id"]
        draft = read_json(candidate_folder / "draft.json")
        paths = set(draft["declared_paths"])
        if (candidate_folder / "candidate.json").exists():
            _verify_sealed(repo, candidate_folder, read_json(candidate_folder / "candidate.json"))
    actual = changed_paths(repo)
    baseline = set(value["baseline_untracked"])
    if actual - baseline - paths or baseline - actual:
        raise ATKError("WORKSPACE_CONFLICT", "未解释的工作区修改阻断 Round 状态切换")
    return {name: digest(data) if (data := content(repo, name)) is not None else None for name in sorted(paths)}


def transition_round(repo: Path, root: Path, request: dict) -> dict:
    """Record a lifecycle transition atomically without editing source or Git."""
    with locked(root):
        value = _round(root, request["round_id"])
        action = request.get("action")
        reason = request.get("reason")
        if action not in {"pause", "resume", "start_finalizing"} or not isinstance(reason, str) or not reason.strip():
            raise ATKError("WORKSPACE_CONFLICT", "Round 状态切换需要受支持动作和原因")
        before = value["status"]
        if action == "pause":
            if before not in {"analysis_only", "ready", "optimizing", "finalizing"}:
                raise ATKError("WORKSPACE_CONFLICT", "当前状态不允许暂停 Round")
            snapshot, error = None, None
            try:
                snapshot = _round_workspace_state(repo, root, value)
            except ATKError as exc:
                error = {"code": exc.code, "reason": str(exc)}
            value["pause"] = {"from_status": before, "reason": reason, "workspace": snapshot, "blocker": error}
            value["status"] = "paused"
        elif action == "resume":
            if before != "paused":
                raise ATKError("WORKSPACE_CONFLICT", "只有暂停的 Round 可以恢复")
            for path in (root / "rounds").glob("*/round.json"):
                other = read_json(path)
                if other["id"] != value["id"] and other["status"] in {"ready", "optimizing", "finalizing"}:
                    raise ATKError("WORKSPACE_CONFLICT", "恢复前先暂停或关闭其他活跃 Round")
            plan_path = _round_path(root, value["id"]) / "plan.json"
            if plan_path.exists():
                plan = read_json(plan_path)
                if plan.get("run_config_hash") != digest(read_json(root / "project.json")) or plan[
                    "runner_hash"
                ] != digest(root / "adapters" / "runner.py"):
                    raise ATKError("COMPARISON_INVALID", "Round 暂停期间冻结 runtime 已改变")
            pause = value["pause"]
            snapshot = _round_workspace_state(repo, root, value)
            if pause["workspace"] is not None and snapshot != pause["workspace"]:
                raise ATKError("REVISION_MISMATCH", "Round 暂停期间未决草稿已改变")
            value["status"] = pause["from_status"]
            del value["pause"]
        else:
            if before not in {"ready", "optimizing", "finalizing"} or value["pending_candidate_id"]:
                raise ATKError("WORKSPACE_CONFLICT", "最终验证需要活跃 Round，且不能有未决候选")
            if not value["active_candidate_ids"] and not value.get("external_fix_identity"):
                raise ATKError("WORKSPACE_CONFLICT", "最终验证需要已采用 Revision 或外部修复")
            _round_workspace_state(repo, root, value)
            if before == "finalizing":
                return value
            value["status"] = "finalizing"
        value.setdefault("transitions", []).append(
            {"action": action, "from_status": before, "to_status": value["status"], "reason": reason, "at": now()}
        )
        _write_round(root, value)
        return value


def _allowed(path: str, patterns: list[str]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/") for prefix in patterns)


def prepare_candidate(repo: Path, root: Path, request: dict) -> dict:
    with locked(root):
        value = _round(root, request["round_id"])
        if value["status"] not in {"ready", "optimizing"} or value["pending_candidate_id"]:
            raise ATKError("WORKSPACE_CONFLICT", "Round 尚未准备好接收另一个候选")
        plan = read_json(_round_path(root, value["id"]) / "plan.json")
        if value.get("external_fix_identity"):
            raise ATKError("WORKSPACE_CONFLICT", "外部修复 Round 验证新 B0，不创建 Agent 候选")
        exposure_path = root / "source-exposure.json"
        if exposure_path.exists() and any(
            entry["round_id"] == value["id"] for entry in read_json(exposure_path).get("groups", {}).values()
        ):
            raise ATKError("COMPARISON_INVALID", "holdout 已暴露；编辑前需新建 Round")
        if len(value["candidate_ids"]) >= plan["budget"].get("candidates", len(set(plan["issue_ids"]))):
            raise ATKError("BUDGET_EXHAUSTED", "候选预算已耗尽")
        verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
        if changed_paths(repo) != set(value["baseline_untracked"]):
            raise ATKError("DIRTY_BASELINE", "候选工作前工作区有未解释修改")
        issue_id = request["primary_issue_id"]
        if issue_id not in plan["issue_ids"]:
            raise ATKError("WORKSPACE_CONFLICT", "Issue 超出冻结计划")
        related = request.get("related_issue_ids", [])
        if (
            not isinstance(related, list)
            or any(not isinstance(item, str) for item in related)
            or set(related) - set(plan["issue_ids"])
        ):
            raise ATKError("WORKSPACE_CONFLICT", "关联 Issue 超出冻结计划")
        supersedes = request.get("supersedes")
        if supersedes is not None:
            if supersedes not in value["candidate_ids"]:
                raise ATKError("WORKSPACE_CONFLICT", "被替代候选不属于本轮")
            earlier = read_json(_round_path(root, value["id"]) / "candidates" / safe_id(supersedes) / "candidate.json")
            if earlier["primary_issue_id"] != issue_id:
                raise ATKError("WORKSPACE_CONFLICT", "被替代候选属于另一个 Issue")
        change_kind = request.get("change_kind", "fix")
        if change_kind not in {"fix", "workaround"}:
            raise ATKError("COMPARISON_INVALID", "候选 change_kind 无效")
        blocked = set(request.get("blocked_by_issue_ids", [])) | set(
            plan.get("blocked_by_issue_ids_by_issue", {}).get(issue_id, [])
        )
        issue = _latest_issue(root, value["id"], issue_id)
        if not issue or issue["disposition"] not in {"local_candidate", "external_handoff"}:
            raise ATKError("INCOMPLETE_EVIDENCE", "主 Issue 需要已记录的候选或交接处理方式")
        target_cases = plan.get("target_case_ids_by_issue", {}).get(issue_id, [])
        if (
            not target_cases
            or not set(target_cases) <= set(issue["case_ids"])
            or not set(target_cases) <= set(plan["case_ids"])
        ):
            raise ATKError("COMPARISON_INVALID", "候选目标 Case 必须已冻结并关联其 Issue")
        if any(_latest_issue(root, value["id"], related_id) is None for related_id in related):
            raise ATKError("INCOMPLETE_EVIDENCE", "关联 Issue 没有已记录修订")
        if issue and issue["disposition"] == "external_handoff" and issue["resolution"] != "resolved":
            if change_kind != "workaround":
                raise ATKError("WORKSPACE_CONFLICT", "范围外 Issue 不能作为本地修复")
            blocked.add(issue_id)
        for blocker_id in blocked:
            blocker = _latest_issue(root, value["id"], blocker_id)
            if blocker_id not in plan["issue_ids"] or not blocker or blocker["disposition"] != "external_handoff":
                raise ATKError("INCOMPLETE_EVIDENCE", f"阻塞 Issue 不是已记录交接：{blocker_id}")
        if change_kind == "workaround" and (not blocked or blocked - set(plan.get("workaround_issue_ids", []))):
            raise ATKError("WORKSPACE_CONFLICT", "workaround 需要对阻塞 Issue 的冻结授权")
        paths = request["paths"]
        if not paths or len(paths) != len(set(paths)):
            raise ATKError("SCOPE_VIOLATION", "候选必须声明不重复路径")
        spellings = {}
        for name in tracked_paths(repo) | changed_paths(repo) | set(paths):
            key = unicodedata.normalize("NFC", name).casefold()
            spellings.setdefault(key, set()).add(name)
        if any(len(spellings[unicodedata.normalize("NFC", name).casefold()]) > 1 for name in paths):
            raise ATKError("SCOPE_VIOLATION", "候选路径有大小写或 Unicode 别名")
        for path in paths:
            safe_path(repo, path)
            if (
                path.startswith(".atk/")
                or not _allowed(path, plan["allowed_paths"])
                or _allowed(path, plan["protected_paths"])
            ):
                raise ATKError("SCOPE_VIOLATION", f"候选路径未获授权：{path}")
        candidate_id = new_id("candidate")
        candidate = {
            "schema_version": 2,
            "id": candidate_id,
            "created_at": now(),
            "round_id": value["id"],
            "primary_issue_id": issue_id,
            "primary_issue_revision": issue["revision"],
            "target_case_ids": sorted(target_cases),
            "related_issue_ids": sorted((set(related) | blocked) - {issue_id}),
            "supersedes": supersedes,
            "parent_commit": value["current_commit"],
            "parent_revision_id": value.get("current_revision_id"),
            "blocked_by_issue_ids": sorted(blocked),
            "change_kind": change_kind,
            "declared_paths": paths,
            "content_status": "draft",
        }
        candidates_folder = _round_path(root, value["id"]) / "candidates"
        orphans = [
            folder
            for folder in candidates_folder.glob("*")
            if folder.is_dir() and folder.name not in value["candidate_ids"] and (folder / "draft.json").exists()
        ]
        if orphans:
            if len(orphans) != 1:
                raise ATKError("WORKSPACE_CONFLICT", "存在多个未注册候选草稿，需要检查")
            folder = orphans[0]
            previous = read_json(folder / "draft.json")
            if (
                folder.is_symlink()
                or (folder / "draft.json").is_symlink()
                or previous.get("id") != folder.name
                or not isinstance(previous.get("created_at"), str)
                or not previous["created_at"]
                or set(folder.iterdir()) != {folder / "draft.json"}
                or {key: val for key, val in previous.items() if key not in {"id", "created_at"}}
                != {key: val for key, val in candidate.items() if key not in {"id", "created_at"}}
            ):
                raise ATKError("WORKSPACE_CONFLICT", "未注册候选草稿与重试不一致")
            candidate = previous
            candidate_id = candidate["id"]
        else:
            folder = candidates_folder / candidate_id
            write_json(folder / "draft.json", candidate, immutable=True)
        value["pending_candidate_id"] = candidate_id
        value["candidate_ids"].append(candidate_id)
        value["status"] = "optimizing"
        _write_round(root, value)
        return candidate


def seal_candidate(repo: Path, root: Path, request: dict) -> dict:
    with locked(root):
        value = _round(root, request["round_id"])
        if value["status"] != "optimizing":
            raise ATKError("WORKSPACE_CONFLICT", "Round 不处于优化阶段")
        candidate_id = value["pending_candidate_id"]
        if candidate_id != request["candidate_id"]:
            raise ATKError("WORKSPACE_CONFLICT", "候选不是未决候选")
        folder = _round_path(root, value["id"]) / "candidates" / candidate_id
        draft = read_json(folder / "draft.json")
        verify_repo(repo, expected_head=draft["parent_commit"], expected_branch=value["branch"])
        actual = changed_paths(repo) - set(value["baseline_untracked"])
        if actual != set(draft["declared_paths"]):
            raise ATKError("SCOPE_VIOLATION", f"实际变更路径与声明路径不一致：{sorted(actual)}")
        files = {}
        contents = {}
        for name in sorted(actual):
            _check_regular_parent(repo, name)
            data = content(repo, name)
            files[name] = {"exists": data is not None, "sha256": digest(data) if data is not None else None}
            if data is not None:
                contents[name] = data
        patch = git(repo, "--literal-pathspecs", "diff", "--binary", "--no-ext-diff", "--", *sorted(actual))
        manifest_path = folder / "files.json"
        if manifest_path.exists():
            patch_path = folder / "changes.patch"
            if (
                read_json(manifest_path) != files
                or not patch_path.is_file()
                or patch_path.read_bytes() != patch
                or any(
                    not (folder / "files" / name).is_file() or (folder / "files" / name).read_bytes() != data
                    for name, data in contents.items()
                )
            ):
                raise ATKError("REVISION_MISMATCH", "部分封存与未决候选不一致")
        else:
            for name, data in contents.items():
                atomic_write(folder / "files" / name, data)
            atomic_write(folder / "changes.patch", patch)
            write_json(manifest_path, files, immutable=True)
        revision_path = folder / "revision.json"
        if revision_path.exists():
            revision = read_json(revision_path)
            if (
                revision.get("base_commit") != draft["parent_commit"]
                or revision.get("candidate_id") != candidate_id
                or revision.get("files_hash") != digest(files)
                or revision.get("changed_paths") != sorted(actual)
            ):
                raise ATKError("REVISION_MISMATCH", "记录的 Revision 与封存文件不一致")
        else:
            revision = {
                "schema_version": 2,
                "id": new_id("revision"),
                "created_at": now(),
                "base_commit": draft["parent_commit"],
                "candidate_id": candidate_id,
                "files_hash": digest(files),
                "changed_paths": sorted(actual),
            }
            write_json(revision_path, revision, immutable=True)
        sealed = {
            **draft,
            "content_status": "sealed",
            "revision_id": revision["id"],
            "changed_paths": sorted(actual),
            "patch_ref": f"candidates/{candidate_id}/changes.patch",
            "files_hash": revision["files_hash"],
        }
        candidate_path = folder / "candidate.json"
        if candidate_path.exists():
            if read_json(candidate_path) != sealed:
                raise ATKError("REVISION_MISMATCH", "记录的候选与其 Revision 不一致")
        else:
            write_json(candidate_path, sealed, immutable=True)
        return sealed


def cancel_draft(repo: Path, root: Path, request: dict) -> dict:
    """Release an unsealed Candidate only after its workspace is clean."""
    with locked(root):
        value = _round(root, request["round_id"])
        candidate_id = safe_id(request["candidate_id"])
        folder = _round_path(root, value["id"]) / "candidates" / candidate_id
        decision_path = folder / "decision.json"
        if value["pending_candidate_id"] != candidate_id:
            if decision_path.exists():
                decision = read_json(decision_path)
                if decision.get("action") == "cancel_draft" and decision.get("reason") == request.get("reason"):
                    return decision
            raise ATKError("WORKSPACE_CONFLICT", "候选不是未决草稿")
        if value["status"] != "optimizing" or (folder / "candidate.json").exists():
            raise ATKError("WORKSPACE_CONFLICT", "只能取消未封存草稿")
        reason = request.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ATKError("INCOMPLETE_EVIDENCE", "取消草稿需要原因")
        draft = read_json(folder / "draft.json")
        verify_repo(repo, expected_head=draft["parent_commit"], expected_branch=value["branch"])
        if changed_paths(repo) != set(value["baseline_untracked"]) or staged_paths(repo):
            raise ATKError("WORKSPACE_CONFLICT", "取消前先恢复或检查草稿修改")
        if decision_path.exists():
            decision = read_json(decision_path)
            if (decision.get("action"), decision.get("reason"), decision.get("candidate_id")) != (
                "cancel_draft",
                reason,
                candidate_id,
            ):
                raise ATKError("WORKSPACE_CONFLICT", "已保存草稿取消记录与重试不一致")
        else:
            decision = {
                "schema_version": 2,
                "id": new_id("decision"),
                "created_at": now(),
                "round_id": value["id"],
                "candidate_id": candidate_id,
                "action": "cancel_draft",
                "reason": reason,
                "validation_id": None,
                "before_commit": draft["parent_commit"],
                "after_commit": value["current_commit"],
            }
            write_json(decision_path, decision, immutable=True)
        value["pending_candidate_id"] = None
        _write_round(root, value)
        return decision


def _verify_sealed(repo: Path, folder: Path, candidate: dict) -> dict:
    files = read_json(folder / "files.json")
    if digest(files) != candidate["files_hash"]:
        raise ATKError("REVISION_MISMATCH", "封存文件 manifest 已改变")
    for name, expected in files.items():
        data = content(repo, name)
        if (data is not None) != expected["exists"] or (data is not None and digest(data) != expected["sha256"]):
            raise ATKError("REVISION_MISMATCH", f"候选在封存后被修改：{name}")
        archived = folder / "files" / name
        if expected["exists"] and (not archived.is_file() or digest(archived) != expected["sha256"]):
            raise ATKError("REVISION_MISMATCH", f"封存候选副本被修改：{name}")
    return files


def _verify_candidate_commit(repo: Path, candidate: dict, files: dict, operation: dict) -> None:
    if git(repo, "rev-list", "--parents", "-n", "1", "HEAD").decode().split()[1:] != [candidate["parent_commit"]]:
        raise ATKError("COMMIT_FAILED", "候选提交父节点与封存检查点不一致")
    committed_paths = set(
        filter(None, git(repo, "diff", "--name-only", "-z", "--no-renames", "HEAD^", "HEAD").decode().split("\0"))
    )
    if committed_paths != set(files):
        raise ATKError("COMMIT_FAILED", "候选提交包含未封存路径")
    message = git(repo, "log", "-1", "--format=%B").decode()
    if (
        f"ATK-Candidate: {candidate['id']}" not in message
        or f"ATK-Operation: {operation['id']}" not in message
        or f"ATK-Round: {candidate['round_id']}" not in message
    ):
        raise ATKError("COMMIT_FAILED", "候选提交身份被修改")
    for name, state in files.items():
        committed = _git_file(repo, "HEAD", name)
        if (committed is not None) != state["exists"] or (
            committed is not None and digest(committed) != state["sha256"]
        ):
            raise ATKError("REVISION_MISMATCH", f"已提交候选内容不一致：{name}")


def require_override(request: dict, action: str) -> dict:
    authorization = request.get("override_authorization")
    if (
        not isinstance(authorization, dict)
        or authorization.get("source") != "user"
        or authorization.get("action") != action
        or not authorization.get("reason")
        or not authorization.get("risk")
    ):
        raise ATKError("WORKSPACE_CONFLICT", "例外需要明确的用户动作、原因和风险")
    return authorization


def _restore_parent(repo: Path, candidate: dict, files: dict) -> None:
    restore = []
    for name, state in files.items():
        parent = _git_file(repo, candidate["parent_commit"], name)
        current = content(repo, name)
        if current == parent:
            continue
        if (current is not None) != state["exists"] or (current is not None and digest(current) != state["sha256"]):
            raise ATKError("GIT_OPERATION_INTERRUPTED", f"未知恢复内容需要检查：{name}")
        restore.append((name, parent))
    for name, parent in restore:
        if parent is not None:
            git(
                repo,
                "--literal-pathspecs",
                "restore",
                f"--source={candidate['parent_commit']}",
                "--worktree",
                "--",
                name,
            )
        else:
            safe_path(repo, name).unlink()
    _verify_restored_content(repo, candidate["parent_commit"], files)


def decide_candidate(repo: Path, root: Path, request: dict) -> dict:
    with locked(root):
        value = _round(root, request["round_id"])
        if value["status"] != "optimizing":
            raise ATKError("WORKSPACE_CONFLICT", "Round 不处于优化阶段")
        candidate_id = request["candidate_id"]
        if value["pending_candidate_id"] != candidate_id:
            raise ATKError("WORKSPACE_CONFLICT", "候选不是未决候选")
        folder = _round_path(root, value["id"]) / "candidates" / candidate_id
        candidate = read_json(folder / "candidate.json")
        verify_repo(repo, expected_head=candidate["parent_commit"], expected_branch=value["branch"])
        files = _verify_sealed(repo, folder, candidate)
        if changed_paths(repo) - set(value["baseline_untracked"]) != set(files):
            raise ATKError("WORKSPACE_CONFLICT", "封存后出现未知工作区修改")
        action = request["action"]
        if action not in {"keep", "reject", "defer"}:
            raise ATKError("WORKSPACE_CONFLICT", f"不支持的候选动作：{action}")
        if not isinstance(request.get("reason"), str) or not request["reason"].strip():
            raise ATKError("INCOMPLETE_EVIDENCE", "候选 Decision 需要原因")
        validation_id = request.get("validation_id")
        missing_reason = request.get("validation_missing_reason")
        if not validation_id and (
            action == "keep" or not isinstance(missing_reason, str) or not missing_reason.strip()
        ):
            raise ATKError("INCOMPLETE_EVIDENCE", "缺少候选 Validation 时必须记录原因")
        if validation_id and missing_reason:
            raise ATKError("COMPARISON_INVALID", "候选 Validation 已存在；缺失原因与事实矛盾")
        if not validation_id and any(
            read_json(path).get("candidate_id") == candidate_id
            for path in (_round_path(root, value["id"]) / "validations").glob("*/validation.json")
        ):
            raise ATKError("COMPARISON_INVALID", "必须引用已有候选 Validation")
        validation = (
            read_json(_round_path(root, value["id"]) / "validations" / safe_id(validation_id) / "validation.json")
            if validation_id
            else None
        )
        if validation and (
            validation.get("right_revision_id") != candidate["revision_id"]
            or validation.get("left_commit") != candidate["parent_commit"]
            or validation.get("candidate_id") != candidate_id
            or validation.get("issue_id") != candidate["primary_issue_id"]
            or validation.get("target_case_ids") != candidate["target_case_ids"]
        ):
            raise ATKError("COMPARISON_INVALID", "验证与候选及父版本不匹配")
        override = request.get("override") is True
        if override and action != "keep":
            raise ATKError("WORKSPACE_CONFLICT", "试用例外仅适用于 keep")
        if action == "keep" and validation["result"] != "pass" and not override:
            raise ATKError("COMPARISON_INVALID", "keep 需要通过的验证或明确例外")
        authorization = require_override(request, "trial_commit") if override else None
        if action == "keep":
            blockers = [_latest_issue(root, value["id"], issue_id) for issue_id in candidate["blocked_by_issue_ids"]]
            if any(issue is None for issue in blockers):
                raise ATKError("INCOMPLETE_EVIDENCE", "候选阻塞 Issue 已消失")
            unresolved = [issue for issue in blockers if issue["resolution"] != "resolved"]
            if unresolved and (candidate["change_kind"] != "workaround" or validation["result"] != "pass"):
                raise ATKError("WORKSPACE_CONFLICT", "未解决外部 Issue 阻断候选采用")
        operation = {
            "id": new_id("operation"),
            "action": action,
            "round_id": value["id"],
            "candidate_id": candidate_id,
            "parent_commit": candidate["parent_commit"],
            "files_hash": candidate["files_hash"],
            "validation_id": validation_id,
            "validation_missing_reason": missing_reason if not validation else None,
            "reason": request["reason"],
            "override": override,
            "override_authorization": authorization,
            "validation_result": validation["result"] if validation else None,
            "stage": "prepared",
            "created_at": now(),
        }
        op_path = _round_path(root, value["id"]) / "operations" / f"{operation['id']}.json"
        write_json(op_path, operation, immutable=True)
        if action == "keep":
            git(repo, "--literal-pathspecs", "add", "-A", "--", *candidate["changed_paths"])
            if staged_paths(repo) != set(files):
                raise ATKError("COMMIT_FAILED", "暂存区含意外路径；操作已记录")
            for name, expected in files.items():
                staged = _git_file(repo, "", name)
                if (staged is not None) != expected["exists"] or (
                    staged is not None and digest(staged) != expected["sha256"]
                ):
                    raise ATKError("REVISION_MISMATCH", f"暂存内容与封存候选不一致：{name}")
            try:
                git(
                    repo,
                    "commit",
                    "-m",
                    request.get("message", f"feat: [agent] 采用 ATK 候选 {candidate_id}"),
                    "-m",
                    f"ATK-Round: {value['id']}\nATK-Candidate: {candidate_id}\nATK-Operation: {operation['id']}",
                )
            except ATKError as exc:
                raise ATKError("COMMIT_FAILED", f"提交失败；操作 {operation['id']} 需要检查：{exc}") from exc
            operation["stage"] = "committed"
            operation["commit"] = head(repo)
            write_json(op_path, operation)
            _verify_candidate_commit(repo, candidate, files, operation)
            if changed_paths(repo) != set(value["baseline_untracked"]) or staged_paths(repo):
                raise ATKError("COMMIT_FAILED", "提交后工作区不干净；操作需要检查")
            value["current_commit"] = operation["commit"]
            value["current_revision_id"] = candidate["revision_id"]
            value["active_candidate_ids"].append(candidate_id)
        else:
            _restore_parent(repo, candidate, files)
            if changed_paths(repo) != set(value["baseline_untracked"]):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "恢复未完成；操作需要检查")
            operation["stage"] = "restored"
            write_json(op_path, operation)
        decision = {
            "schema_version": 2,
            "id": new_id("decision"),
            "created_at": now(),
            "round_id": value["id"],
            "candidate_id": candidate_id,
            "action": action,
            "validation_id": validation_id,
            "validation_missing_reason": missing_reason if not validation else None,
            "reason": request["reason"],
            "override": override,
            "override_authorization": authorization,
            "validation_result": validation["result"] if validation else None,
            "operation_id": operation["id"],
            "before_commit": candidate["parent_commit"],
            "after_commit": value["current_commit"],
        }
        write_json(folder / "decision.json", decision, immutable=True)
        value["pending_candidate_id"] = None
        _write_round(root, value)
        operation["stage"] = "complete"
        write_json(op_path, operation)
        return decision


def _recover_restoration_locked(repo: Path, root: Path, folder: Path, operation: dict) -> dict:
    value = _round(root, operation["round_id"])
    candidate_id = safe_id(operation["candidate_id"])
    candidate_folder = folder / "candidates" / candidate_id
    candidate = read_json(candidate_folder / "candidate.json")
    files = read_json(candidate_folder / "files.json")
    if (
        operation.get("round_id") != value["id"]
        or candidate.get("id") != candidate_id
        or operation.get("parent_commit") != candidate["parent_commit"]
        or operation.get("files_hash") != candidate["files_hash"]
        or digest(files) != candidate["files_hash"]
    ):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "恢复操作身份与封存候选不一致")
    decision_path = candidate_folder / "decision.json"
    decision = read_json(decision_path) if decision_path.exists() else None
    expected = {
        "round_id": value["id"],
        "candidate_id": candidate_id,
        "action": operation["action"],
        "validation_id": operation["validation_id"],
        "validation_missing_reason": operation.get("validation_missing_reason"),
        "operation_id": operation["id"],
        "before_commit": candidate["parent_commit"],
        "after_commit": candidate["parent_commit"],
    }
    if decision and any(decision.get(key) != item for key, item in expected.items()):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "已保存 Decision 与恢复结果不一致")
    if value["pending_candidate_id"] != candidate_id:
        if operation.get("stage") == "complete" and decision:
            return {"stage": "complete", "decision": decision}
        if value["pending_candidate_id"] is not None or not decision:
            raise ATKError("GIT_OPERATION_INTERRUPTED", "恢复操作不是未决候选")
    if operation.get("stage") not in {"prepared", "restored"}:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "恢复操作阶段不一致")
    if value["current_commit"] != candidate["parent_commit"]:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "恢复期间 Round 检查点改变")
    verify_repo(repo, expected_head=candidate["parent_commit"], expected_branch=value["branch"])
    if changed_paths(repo) - set(value["baseline_untracked"]) - set(files):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "恢复期间出现未知工作区修改")
    _restore_parent(repo, candidate, files)
    if changed_paths(repo) != set(value["baseline_untracked"]):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "恢复未到达父检查点")
    if operation["stage"] != "restored":
        operation["stage"] = "restored"
        write_json(folder / "operations" / f"{operation['id']}.json", operation)
    if not decision:
        decision = {
            "schema_version": 2,
            "id": new_id("decision"),
            "created_at": now(),
            "reason": operation["reason"],
            "override": False,
            "override_authorization": None,
            "validation_result": operation["validation_result"],
            **expected,
        }
        write_json(decision_path, decision, immutable=True)
    if value["pending_candidate_id"] == candidate_id:
        value["pending_candidate_id"] = None
        _write_round(root, value)
    operation["stage"] = "complete"
    write_json(folder / "operations" / f"{operation['id']}.json", operation)
    return {"stage": "complete", "decision": decision}


def inspect_or_recover_operation(repo: Path, root: Path, request: dict) -> dict:
    """Reconcile a candidate operation with its exact Git and artifact state."""
    with locked(root):
        for path in (root / "rounds").glob("*/round.json"):
            other = read_json(path)
            if other["id"] != request["round_id"] and other["status"] in {"ready", "optimizing", "finalizing"}:
                raise ATKError("WORKSPACE_CONFLICT", "恢复操作前先暂停或关闭其他活跃 Round")
        folder = _round_path(root, request["round_id"])
        operation = read_json(folder / "operations" / f"{safe_id(request['operation_id'])}.json")
        if operation.get("action") == "temporary_replay":
            return _recover_temporary_replay_locked(repo, root, folder, operation)
        if operation.get("action") == "rollback_to":
            return _recover_rollback_locked(repo, folder, operation)
        if operation.get("action") in {"reject", "defer"}:
            return _recover_restoration_locked(repo, root, folder, operation)
        if operation.get("action") != "keep":
            raise ATKError("GIT_OPERATION_INTERRUPTED", "未知候选操作动作")
        candidate_id = operation["candidate_id"]
        candidate_folder = folder / "candidates" / candidate_id
        decision_path = candidate_folder / "decision.json"
        value = _round(root, request["round_id"])
        candidate = read_json(candidate_folder / "candidate.json")
        files = read_json(candidate_folder / "files.json")
        if decision_path.exists() and value["pending_candidate_id"] != candidate_id:
            decision = read_json(decision_path)
            if (
                operation.get("round_id") != value["id"]
                or operation.get("parent_commit") != candidate["parent_commit"]
                or operation.get("files_hash") != candidate["files_hash"]
                or digest(files) != candidate["files_hash"]
                or decision.get("operation_id") != operation["id"]
                or decision.get("candidate_id") != candidate_id
                or decision.get("action") != "keep"
                or decision.get("validation_id") != operation["validation_id"]
                or decision.get("before_commit") != candidate["parent_commit"]
            ):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "已完成操作身份不一致")
            if operation.get("stage") == "complete" and decision.get("after_commit") == operation.get("commit"):
                return {"stage": "complete", "decision": decision}
            if (
                value["pending_candidate_id"] is not None
                or value["current_commit"] != decision.get("after_commit")
                or value["current_revision_id"] != candidate["revision_id"]
                or value["active_candidate_ids"].count(candidate_id) != 1
                or branch(repo) != value["branch"]
                or head(repo) != value["current_commit"]
                or (operation.get("commit") and operation["commit"] != value["current_commit"])
            ):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "保存候选 Decision 后 Round 已改变")
            _verify_candidate_commit(repo, candidate, files, operation)
            if changed_paths(repo) != set(value["baseline_untracked"]) or staged_paths(repo):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "候选提交后工作区不一致")
            operation["commit"] = value["current_commit"]
            operation["stage"] = "complete"
            write_json(folder / "operations" / f"{operation['id']}.json", operation)
            return {"stage": "complete", "decision": decision}
        if value["pending_candidate_id"] != candidate_id:
            raise ATKError("GIT_OPERATION_INTERRUPTED", "操作不是未决候选")
        if (
            operation.get("parent_commit") != candidate["parent_commit"]
            or operation.get("files_hash") != candidate["files_hash"]
            or operation.get("round_id") != value["id"]
        ):
            raise ATKError("GIT_OPERATION_INTERRUPTED", "操作身份与封存候选不一致")
        actual_head = head(repo)
        if actual_head == candidate["parent_commit"] and not decision_path.exists():
            if branch(repo) != value["branch"] or operation["stage"] not in {"prepared", "aborted"}:
                raise ATKError("GIT_OPERATION_INTERRUPTED", "提交前操作状态未知")
            _verify_sealed(repo, candidate_folder, candidate)
            if (changed_paths(repo) | staged_paths(repo)) - set(value["baseline_untracked"]) != set(files):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "提交 attempt 期间候选工作区改变")
            staged = staged_paths(repo)
            if staged - set(files):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "未知暂存路径需要检查")
            for name in staged:
                state = files[name]
                indexed = _git_file(repo, "", name)
                if (indexed is not None) != state["exists"] or (
                    indexed is not None and digest(indexed) != state["sha256"]
                ):
                    raise ATKError("GIT_OPERATION_INTERRUPTED", "暂存内容与封存候选不一致")
            if staged:
                git(repo, "--literal-pathspecs", "restore", "--staged", "--", *sorted(staged))
            if staged_paths(repo):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "候选暂存区未恢复")
            operation["stage"] = "aborted"
            write_json(folder / "operations" / f"{operation['id']}.json", operation)
            return {"stage": "aborted", "candidate_id": candidate_id}
        if branch(repo) != value["branch"]:
            raise ATKError("GIT_OPERATION_INTERRUPTED", "候选提交期间分支改变")
        _verify_candidate_commit(repo, candidate, files, operation)
        if changed_paths(repo) != set(value["baseline_untracked"]) or staged_paths(repo):
            raise ATKError("GIT_OPERATION_INTERRUPTED", "候选提交后工作区不一致")
        decision = (
            read_json(decision_path)
            if decision_path.exists()
            else {
                "schema_version": 2,
                "id": new_id("decision"),
                "created_at": now(),
                "round_id": value["id"],
                "candidate_id": candidate_id,
                "action": "keep",
                "validation_id": operation["validation_id"],
                "reason": operation["reason"],
                "override": operation["override"],
                "override_authorization": operation.get("override_authorization"),
                "validation_result": operation.get("validation_result"),
                "operation_id": operation["id"],
                "before_commit": candidate["parent_commit"],
                "after_commit": actual_head,
            }
        )
        if (
            decision["operation_id"] != operation["id"]
            or decision["after_commit"] != actual_head
            or decision["validation_id"] != operation["validation_id"]
        ):
            raise ATKError("GIT_OPERATION_INTERRUPTED", "已保存 Decision 与候选提交不一致")
        if not decision_path.exists():
            write_json(decision_path, decision, immutable=True)
        value["pending_candidate_id"] = None
        value["current_commit"] = actual_head
        value["current_revision_id"] = candidate["revision_id"]
        value["active_candidate_ids"].append(candidate_id)
        _write_round(root, value)
        operation["commit"] = actual_head
        operation["stage"] = "complete"
        write_json(folder / "operations" / f"{operation['id']}.json", operation)
        return {"stage": "complete", "decision": decision}


def _recover_temporary_replay_locked(repo: Path, root: Path, folder: Path, operation: dict) -> dict:
    value = _round(root, folder.name)
    if operation.get("round_id", value["id"]) != value["id"]:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "重放操作属于其他 Round")
    stage = operation.get("stage")
    if stage in {"complete", "aborted"}:
        return {"stage": stage, "round_id": value["id"]}
    if stage not in {"prepared", "switching", "switched"}:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "重放操作阶段不一致")
    target = operation.get("target_commit")
    known = {value["baseline_commit"]}
    known.update(
        read_json(folder / "candidates" / candidate_id / "decision.json")["after_commit"]
        for candidate_id in value["active_candidate_ids"]
    )
    preparation = read_json(folder / "plan.json").get("replay_preparation")
    candidate_id = value["pending_candidate_id"]
    candidate_folder = folder / "candidates" / candidate_id if candidate_id else None
    candidate = read_json(candidate_folder / "candidate.json") if candidate_folder else None
    files = read_json(candidate_folder / "files.json") if candidate else {}
    if candidate and (
        operation.get("candidate_id") != candidate_id
        or operation.get("files_hash") != candidate["files_hash"]
        or digest(files) != candidate["files_hash"]
        or any(
            not (candidate_folder / "files" / name).is_file()
            or digest(candidate_folder / "files" / name) != expected["sha256"]
            for name, expected in files.items()
            if expected["exists"]
        )
    ):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "封存重放源已改变")
    if (
        operation.get("candidate_id") != candidate_id
        or operation.get("before_commit") != value["current_commit"]
        or target not in known
        or not preparation
        or operation.get("replay_preparation_hash") != digest(preparation)
    ):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "重放操作与当前检查点不一致")
    paths = set(operation.get("paths", []))
    expected_paths = set(
        filter(None, git(repo, "diff", "--name-only", "-z", target, value["current_commit"]).decode().split("\0"))
    )
    if paths != expected_paths | set(files):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "重放路径与当前检查点不一致")
    verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
    if changed_paths(repo) - set(value["baseline_untracked"]) - paths:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "重放期间出现未知工作区修改")
    for name in paths:
        before = _sealed_replay_content(repo, name, value["current_commit"], candidate_folder, files)
        if content(repo, name) not in {_git_file(repo, target, name), before}:
            raise ATKError("GIT_OPERATION_INTERRUPTED", f"重放源含未知内容：{name}")
    for name in sorted(paths):
        _write_replay_file(
            repo,
            name,
            _sealed_replay_content(repo, name, value["current_commit"], candidate_folder, files),
            value["current_commit"] if name not in files else None,
        )
    _prepare_replay(repo, preparation)
    if candidate:
        _verify_sealed(repo, candidate_folder, candidate)
    else:
        _verify_restored_content(repo, value["current_commit"], paths)
    if changed_paths(repo) != set(value["baseline_untracked"]) | set(files) or staged_paths(repo):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "重放恢复未还原开始时检查点")
    operation["stage"] = "aborted"
    write_json(folder / "operations" / f"{operation['id']}.json", operation)
    return {"stage": "aborted", "round_id": value["id"]}


def _verify_rollback_commit(repo: Path, operation: dict) -> None:
    if git(repo, "rev-list", "--parents", "-n", "1", "HEAD").decode().split()[1:] != [operation["before_commit"]]:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "回退提交父节点改变")
    if git(repo, "rev-parse", "HEAD^{tree}") != git(repo, "rev-parse", f"{operation['target_commit']}^{{tree}}"):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "回退提交树与目标检查点不一致")
    paths = set(
        filter(None, git(repo, "diff", "--name-only", "-z", "--no-renames", "HEAD^", "HEAD").decode().split("\0"))
    )
    if paths != set(operation["paths"]):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "回退提交包含意外路径")
    message = git(repo, "log", "-1", "--format=%B").decode()
    if f"ATK-Round: {operation['round_id']}" not in message or f"ATK-Rollback: {operation['id']}" not in message:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "回退提交身份被修改")


def _recover_rollback_locked(repo: Path, folder: Path, operation: dict) -> dict:
    value = read_json(folder / "round.json")
    decision_path = folder / "decisions" / f"{safe_id(operation['decision_id'])}.json"
    if decision_path.exists() and value["current_commit"] != operation["before_commit"]:
        decision = read_json(decision_path)
        if (
            operation.get("stage") != "complete"
            or decision.get("operation_id") != operation["id"]
            or decision.get("after_commit") != operation.get("after_commit")
        ):
            raise ATKError("GIT_OPERATION_INTERRUPTED", "已完成回退身份不一致")
        return {"stage": "complete", "decision": decision}
    if value["current_commit"] != operation["before_commit"] or value["pending_candidate_id"]:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "回退期间 Round 改变")
    if value["active_candidate_ids"][operation["target_index"] :] != operation["withdrawn_candidate_ids"]:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "已采用链与回退记录不一致")
    if branch(repo) != value["branch"]:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "回退期间分支改变")
    paths = operation["paths"]
    if head(repo) == operation["before_commit"] and not decision_path.exists():
        if operation["stage"] not in {"prepared", "aborted"}:
            raise ATKError("GIT_OPERATION_INTERRUPTED", "回退提交在记录后消失")
        if staged_paths(repo) - set(paths):
            raise ATKError("GIT_OPERATION_INTERRUPTED", "未知暂存路径需要检查")
        if (changed_paths(repo) | staged_paths(repo)) - set(value["baseline_untracked"]) - set(paths):
            raise ATKError("GIT_OPERATION_INTERRUPTED", "回退期间出现未知工作区修改")
        for name in paths:
            known = {
                _git_file(repo, operation["before_commit"], name),
                _git_file(repo, operation["target_commit"], name),
            }
            if content(repo, name) not in known or (
                name in staged_paths(repo) and _git_file(repo, "", name) not in known
            ):
                raise ATKError("GIT_OPERATION_INTERRUPTED", f"未知回退内容需要检查：{name}")
        if paths:
            git(
                repo,
                "--literal-pathspecs",
                "restore",
                f"--source={operation['before_commit']}",
                "--worktree",
                "--",
                *paths,
            )
            staged = staged_paths(repo)
            if staged:
                git(repo, "--literal-pathspecs", "restore", "--staged", "--", *sorted(staged))
        _verify_restored_content(repo, operation["before_commit"], paths)
        if changed_paths(repo) != set(value["baseline_untracked"]) or staged_paths(repo):
            raise ATKError("GIT_OPERATION_INTERRUPTED", "中止回退未还原开始时检查点")
        operation["stage"] = "aborted"
        write_json(folder / "operations" / f"{operation['id']}.json", operation)
        return {"stage": "aborted", "round_id": value["id"]}
    _verify_rollback_commit(repo, operation)
    _verify_restored_content(repo, operation["target_commit"], paths)
    if changed_paths(repo) != set(value["baseline_untracked"]) or staged_paths(repo):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "回退提交后工作区不一致")
    decision = (
        read_json(decision_path)
        if decision_path.exists()
        else {
            "schema_version": 2,
            "id": operation["decision_id"],
            "created_at": now(),
            "round_id": value["id"],
            "action": "rollback_to",
            "operation_id": operation["id"],
            "before_commit": operation["before_commit"],
            "after_commit": head(repo),
            "rollback_to": operation["target_commit"],
            "withdrawn_candidate_ids": operation["withdrawn_candidate_ids"],
            "reason": operation["reason"],
        }
    )
    if decision["after_commit"] != head(repo) or decision["operation_id"] != operation["id"]:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "已保存回退 Decision 与提交不一致")
    if not decision_path.exists():
        write_json(decision_path, decision, immutable=True)
    value["active_candidate_ids"] = value["active_candidate_ids"][: operation["target_index"]]
    value["current_commit"] = head(repo)
    value["current_revision_id"] = operation["target_revision_id"]
    if value["status"] == "paused":
        value["pause"]["from_status"] = "optimizing"
    else:
        value["status"] = "optimizing"
    _write_round(folder.parent.parent, value)
    operation["stage"] = "complete"
    operation["after_commit"] = value["current_commit"]
    write_json(folder / "operations" / f"{operation['id']}.json", operation)
    return {"stage": "complete", "decision": decision}


def rollback_to(repo: Path, root: Path, request: dict) -> dict:
    with locked(root):
        value = _round(root, request["round_id"])
        if value["status"] not in {"ready", "optimizing", "finalizing"}:
            raise ATKError("WORKSPACE_CONFLICT", "当前状态不允许回退 Round")
        if value["pending_candidate_id"]:
            raise ATKError("WORKSPACE_CONFLICT", "回退前先处理未决候选")
        verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
        if changed_paths(repo) != set(value["baseline_untracked"]):
            raise ATKError("WORKSPACE_CONFLICT", "未解释修改阻断回退")
        folder = _round_path(root, value["id"])
        checkpoints = [(value["baseline_commit"], value["baseline_revision_id"], 0)]
        for index, candidate_id in enumerate(value["active_candidate_ids"], 1):
            candidate_folder = folder / "candidates" / candidate_id
            checkpoints.append(
                (
                    read_json(candidate_folder / "decision.json")["after_commit"],
                    read_json(candidate_folder / "candidate.json")["revision_id"],
                    index,
                )
            )
        target_commit = request["target_commit"]
        match = next(((revision, index) for commit, revision, index in checkpoints if commit == target_commit), None)
        if match is None or target_commit == value["current_commit"]:
            raise ATKError("WORKSPACE_CONFLICT", "回退目标不是更早的活跃检查点")
        changed = set(filter(None, git(repo, "diff", "--name-only", "-z", target_commit, "HEAD").decode().split("\0")))
        plan = read_json(folder / "plan.json")
        if any(
            not _allowed(name, plan["allowed_paths"]) or _allowed(name, plan["protected_paths"]) for name in changed
        ):
            raise ATKError("SCOPE_VIOLATION", "回退差异包含候选范围外路径")
        operation = {
            "id": new_id("operation"),
            "decision_id": new_id("decision"),
            "action": "rollback_to",
            "round_id": value["id"],
            "stage": "prepared",
            "created_at": now(),
            "before_commit": value["current_commit"],
            "target_commit": target_commit,
            "target_revision_id": match[0],
            "target_index": match[1],
            "withdrawn_candidate_ids": value["active_candidate_ids"][match[1] :],
            "paths": sorted(changed),
            "reason": request["reason"],
        }
        op_path = folder / "operations" / f"{operation['id']}.json"
        write_json(op_path, operation, immutable=True)
        if changed:
            git(
                repo,
                "--literal-pathspecs",
                "restore",
                f"--source={target_commit}",
                "--worktree",
                "--",
                *sorted(changed),
            )
            _verify_restored_content(repo, target_commit, changed)
            git(repo, "--literal-pathspecs", "add", "-A", "--", *sorted(changed))
        if staged_paths(repo) != changed:
            raise ATKError("GIT_OPERATION_INTERRUPTED", "回退暂存区与预期路径不一致")
        try:
            git(
                repo,
                "commit",
                *(["--allow-empty"] if not changed else []),
                "-m",
                f"revert: [agent] 回退 ATK 操作 {operation['id']}",
                "-m",
                f"ATK-Round: {value['id']}\nATK-Rollback: {operation['id']}",
            )
        except ATKError as exc:
            raise ATKError("COMMIT_FAILED", f"回退操作 {operation['id']} 需要检查：{exc}") from exc
        operation["stage"] = "committed"
        write_json(op_path, operation)
        _verify_rollback_commit(repo, operation)
        return _recover_rollback_locked(repo, folder, operation)["decision"]


@contextmanager
def temporary_revision(repo: Path, root: Path, round_id: str, target_commit: str):
    """Replay a known checkpoint in the same directory without moving HEAD or index."""
    with locked(root):
        value = _round(root, round_id)
        candidate_id = value["pending_candidate_id"]
        candidate_folder = _round_path(root, round_id) / "candidates" / candidate_id if candidate_id else None
        if candidate_folder and not (candidate_folder / "candidate.json").is_file():
            raise ATKError("WORKSPACE_CONFLICT", "临时重放需要封存候选")
        candidate = read_json(candidate_folder / "candidate.json") if candidate_folder else None
        files = _verify_sealed(repo, candidate_folder, candidate) if candidate else {}
        verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
        if changed_paths(repo) != set(value["baseline_untracked"]) | set(files):
            raise ATKError("WORKSPACE_CONFLICT", "未解释修改阻断临时重放")
        known = {value["baseline_commit"]}
        for active_id in value["active_candidate_ids"]:
            decision = read_json(_round_path(root, round_id) / "candidates" / active_id / "decision.json")
            known.add(decision["after_commit"])
        if target_commit not in known:
            raise ATKError("REVISION_MISMATCH", "临时重放目标不是已记录检查点")
        paths = set(
            filter(None, git(repo, "diff", "--name-only", "-z", target_commit, "HEAD").decode().split("\0"))
        ) | set(files)
        for name in paths:
            current = _sealed_replay_content(repo, name, value["current_commit"], candidate_folder, files)
            if content(repo, name) != current:
                raise ATKError("WORKSPACE_CONFLICT", f"重放路径与封存源不一致：{name}")
        preparation = read_json(_round_path(root, round_id) / "plan.json").get("replay_preparation")
        if not preparation:
            raise ATKError("NOT_REPLAYABLE", "Round 未冻结重放准备")
        operation = {
            "id": new_id("operation"),
            "action": "temporary_replay",
            "round_id": round_id,
            "stage": "prepared",
            "created_at": now(),
            "before_commit": value["current_commit"],
            "target_commit": target_commit,
            "paths": sorted(paths),
            "candidate_id": candidate_id,
            "files_hash": candidate["files_hash"] if candidate else None,
            "replay_preparation_hash": digest(preparation),
        }
        op_path = _round_path(root, round_id) / "operations" / f"{operation['id']}.json"
        write_json(op_path, operation, immutable=True)
        ready = False
        try:
            operation["stage"] = "switching"
            write_json(op_path, operation)
            for name in sorted(paths):
                _write_replay_file(repo, name, _git_file(repo, target_commit, name), target_commit)
            if any(content(repo, name) != _git_file(repo, target_commit, name) for name in paths):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "重放切换后目标源码不一致")
            _prepare_replay(repo, preparation)
            target_changes = {
                name
                for name in paths
                if _git_file(repo, target_commit, name) != _git_file(repo, value["current_commit"], name)
            }
            if (
                head(repo) != value["current_commit"]
                or staged_paths(repo)
                or changed_paths(repo) != target_changes | set(value["baseline_untracked"])
                or any(content(repo, name) != _git_file(repo, target_commit, name) for name in paths)
            ):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "重放准备改变了源码或 Git 状态")
            operation["stage"] = "switched"
            write_json(op_path, operation)
            ready = True
            yield
        finally:
            if head(repo) != value["current_commit"] or staged_paths(repo):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "临时重放期间 HEAD 或暂存区改变")
            if any(
                content(repo, name)
                not in {
                    _git_file(repo, target_commit, name),
                    _sealed_replay_content(repo, name, value["current_commit"], candidate_folder, files),
                }
                for name in paths
            ):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "重放源含未知内容，需要检查")
            for name in sorted(paths):
                _write_replay_file(
                    repo,
                    name,
                    _sealed_replay_content(repo, name, value["current_commit"], candidate_folder, files),
                    value["current_commit"] if name not in files else None,
                )
            _prepare_replay(repo, preparation)
            if candidate:
                _verify_sealed(repo, candidate_folder, candidate)
            else:
                _verify_restored_content(repo, value["current_commit"], paths)
            if changed_paths(repo) != set(value["baseline_untracked"]) | set(files):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "临时重放未还原开始时检查点")
            operation["stage"] = "complete" if ready else "aborted"
            write_json(op_path, operation)


def _prepare_replay(repo: Path, preparation: dict) -> None:
    if preparation["mode"] == "stateless":
        return
    try:
        with subprocess.Popen(
            preparation["argv"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
        ) as process:
            try:
                process.communicate(timeout=preparation["timeout_seconds"])
            except subprocess.TimeoutExpired as exc:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise ATKError("NOT_REPLAYABLE", f"重放准备超时：{exc}") from exc
    except OSError as exc:
        raise ATKError("NOT_REPLAYABLE", f"重放准备失败：{exc}") from exc
    if process.returncode:
        raise ATKError("NOT_REPLAYABLE", f"重放准备退出码为 {process.returncode}")


@contextmanager
def execution_revision(repo: Path, root: Path, request: dict):
    """Bind an evaluation to actual source content, switching only known commits."""
    round_id = request.get("round_id")
    if not round_id:
        if not request.get("revision_commit"):
            raise ATKError("REVISION_MISMATCH", "未冻结运行需要明确的 revision_commit")
        verify_repo(repo, expected_head=request["revision_commit"])
        if git(repo, "diff", "--name-only", "-z"):
            raise ATKError("DIRTY_BASELINE", "未冻结本地运行需要干净的已跟踪工作区")
        initial_paths = changed_paths(repo)
        yield
        if head(repo) != request["revision_commit"] or staged_paths(repo) or changed_paths(repo) != initial_paths:
            raise ATKError("WORKSPACE_CONFLICT", "runner 改变了未冻结源码或 Git 状态")
        return
    value = _round(root, round_id)
    if value["status"] not in {"analysis_only", "ready", "optimizing", "finalizing"}:
        raise ATKError("WORKSPACE_CONFLICT", "Round 已暂停或关闭")
    revision_id = request["revision_id"]
    if value["baseline_commit"] is None:
        if request["purpose"] != "diagnostic_probe" or revision_id is not None:
            raise ATKError("REVISION_MISMATCH", "未冻结仅分析模式只允许无源码 Revision 的探针")
        yield
        return
    if value["pending_candidate_id"]:
        folder = _round_path(root, round_id) / "candidates" / value["pending_candidate_id"]
        candidate = read_json(folder / "candidate.json")
        if candidate["revision_id"] != revision_id:
            checkpoints = {value["baseline_revision_id"]: value["baseline_commit"]}
            for active_id in value["active_candidate_ids"]:
                active = _round_path(root, round_id) / "candidates" / active_id
                checkpoints[read_json(active / "candidate.json")["revision_id"]] = read_json(active / "decision.json")[
                    "after_commit"
                ]
            target = checkpoints.get(revision_id)
            if not target:
                raise ATKError("REVISION_MISMATCH", "运行请求指定了未知 Revision")
            with temporary_revision(repo, root, round_id, target):
                yield
            return
        verify_repo(repo, expected_head=candidate["parent_commit"], expected_branch=value["branch"])
        _verify_sealed(repo, folder, candidate)
        if changed_paths(repo) - set(value["baseline_untracked"]) != set(candidate["changed_paths"]):
            raise ATKError("WORKSPACE_CONFLICT", "候选工作区有额外修改")
        yield
        _verify_sealed(repo, folder, candidate)
        if (
            head(repo) != candidate["parent_commit"]
            or staged_paths(repo)
            or changed_paths(repo) - set(value["baseline_untracked"]) != set(candidate["changed_paths"])
        ):
            raise ATKError("WORKSPACE_CONFLICT", "runner 改变了候选 Git 状态")
        return
    checkpoints = {value["baseline_revision_id"]: value["baseline_commit"]}
    for candidate_id in value["active_candidate_ids"]:
        folder = _round_path(root, round_id) / "candidates" / candidate_id
        checkpoints[read_json(folder / "candidate.json")["revision_id"]] = read_json(folder / "decision.json")[
            "after_commit"
        ]
    target = checkpoints.get(revision_id)
    if not target:
        raise ATKError("REVISION_MISMATCH", "运行请求指定了未知 Revision")
    if revision_id == value["current_revision_id"]:
        verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
        if changed_paths(repo) != set(value["baseline_untracked"]):
            raise ATKError("WORKSPACE_CONFLICT", "当前检查点工作区不干净")
        yield
        if (
            head(repo) != value["current_commit"]
            or staged_paths(repo)
            or changed_paths(repo) != set(value["baseline_untracked"])
        ):
            raise ATKError("WORKSPACE_CONFLICT", "runner 改变了当前检查点源码")
    else:
        with temporary_revision(repo, root, round_id, target):
            yield
