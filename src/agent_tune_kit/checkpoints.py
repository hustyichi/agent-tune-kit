"""Git checkpoint operations for one local, serial ATK round."""

from __future__ import annotations

import math
import subprocess
from contextlib import contextmanager
from pathlib import Path

from .core import ATKError, digest, locked, new_id, now, read_json, safe_id, write_json


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
        raise ATKError("UNSUPPORTED_GIT_STATE", "detached HEAD is unsupported")
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
    relative = Path(name)
    if not name or relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
        raise ATKError("SCOPE_VIOLATION", f"unsafe path: {name}")
    path = repo / relative
    for parent in [path, *path.parents]:
        if parent == repo.parent:
            break
        if parent.is_symlink():
            raise ATKError("SCOPE_VIOLATION", f"symlink path: {name}")
    return path


def content(repo: Path, name: str) -> bytes | None:
    path = safe_path(repo, name)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_mode & 0o111:
        raise ATKError("SCOPE_VIOLATION", f"only regular non-executable text files are supported: {name}")
    data = path.read_bytes()
    if b"\0" in data:
        raise ATKError("SCOPE_VIOLATION", f"binary file is unsupported: {name}")
    return data


def _git_file(repo: Path, revision: str, name: str) -> bytes | None:
    found = subprocess.run(["git", "cat-file", "-e", f"{revision}:{name}"], cwd=repo, capture_output=True)
    return git(repo, "show", f"{revision}:{name}") if found.returncode == 0 else None


def _check_regular_parent(repo: Path, name: str) -> None:
    listing = git(repo, "ls-tree", "HEAD", "--", name).decode().strip()
    if listing:
        mode = listing.split()[0]
        if mode not in {"100644"}:
            raise ATKError("SCOPE_VIOLATION", f"unsupported parent file mode: {name} ({mode})")


def verify_repo(repo: Path, *, expected_head: str | None = None, expected_branch: str | None = None) -> None:
    if git(repo, "rev-parse", "--is-inside-work-tree").strip() != b"true":
        raise ATKError("UNSUPPORTED_GIT_STATE", "not a Git worktree")
    common = Path(git(repo, "rev-parse", "--git-dir").decode().strip())
    if not common.is_absolute():
        common = repo / common
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-apply", "rebase-merge"):
        if (common / name).exists():
            raise ATKError("UNSUPPORTED_GIT_STATE", f"Git operation in progress: {name}")
    if git(repo, "ls-files", "-u", "-z"):
        raise ATKError("UNSUPPORTED_GIT_STATE", "unmerged index entries")
    if expected_head and head(repo) != expected_head:
        raise ATKError("BASELINE_DRIFT", "HEAD differs from recorded checkpoint")
    if expected_branch and branch(repo) != expected_branch:
        raise ATKError("BASELINE_DRIFT", "branch differs from frozen round")
    if staged_paths(repo):
        raise ATKError("DIRTY_BASELINE", "staged changes must be resolved before checkpoint operation")
    if any(path == ".atk" or path.startswith(".atk/") for path in tracked_paths(repo)):
        raise ATKError("DIRTY_BASELINE", "tracked .atk artifacts must be removed from the index before a round")


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
        verify_repo(repo)
        if git(repo, "diff", "--name-only", "-z"):
            raise ATKError("DIRTY_BASELINE", "tracked working tree must be clean for B0")
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
                raise ATKError(
                    "INCOMPLETE_EVIDENCE", "external fix Round needs a closed source Round and open handoff Issue"
                )
        elif request.get("external_fix_identity"):
            raise ATKError("INCOMPLETE_EVIDENCE", "external fix needs a linked previous Round")
        for old in (root / "rounds").glob("*/round.json"):
            if read_json(old).get("status") in {"ready", "optimizing", "finalizing"}:
                raise ATKError("WORKSPACE_CONFLICT", "another active round exists")
        baseline = head(repo)
        baseline_revision_id = new_id("revision")
        if request.get("reuse_revision"):
            if (
                not previous_round_id
                or request.get("external_fix_identity")
                or previous["status"] not in {"completed", "completed_with_override", "closed_without_adoption"}
                or previous["current_commit"] != baseline
                or read_json(_round_path(root, previous_round_id) / "plan.json").get("run_config_hash")
                != digest(read_json(root / "project.json"))
            ):
                raise ATKError("REVISION_MISMATCH", "prior Revision cannot be reused across this Round boundary")
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
            "branch": branch(repo),
            "issues": request.get("issue_ids", []),
            "candidate_ids": [],
            "active_candidate_ids": [],
            "pending_candidate_id": None,
            "baseline_untracked": sorted(changed_paths(repo)),
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
            raise ATKError("WORKSPACE_CONFLICT", "round is already frozen")
        verify_repo(repo, expected_head=value["baseline_commit"], expected_branch=value["branch"])
        if git(repo, "diff", "--name-only", "-z") or changed_paths(repo) != set(value["baseline_untracked"]):
            raise ATKError("DIRTY_BASELINE", "workspace changed since B0 was recorded")
        plan = dict(request["plan"])
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
            raise ATKError("INCOMPLETE_EVIDENCE", f"incomplete frozen plan: {sorted(required - plan.keys())}")
        if external_fix_round:
            previous_folder = _round_path(root, value["previous_round_id"])
            previous_plan = read_json(previous_folder / "plan.json")
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
                or not set(previous_plan["protection_case_ids"]) <= set(plan["protection_case_ids"])
            ):
                raise ATKError("COMPARISON_INVALID", "external fix plan must retain affected and protection Cases")
        if (
            not plan["case_ids"]
            or len(plan["case_ids"]) != len(set(plan["case_ids"]))
            or not set(plan["protection_case_ids"]) <= set(plan["case_ids"])
        ):
            raise ATKError("COMPARISON_INVALID", "frozen Case set is empty, duplicated, or inconsistent")
        if plan["repeatability"] not in {"deterministic", "stochastic", "unknown"}:
            raise ATKError("COMPARISON_INVALID", "repeatability is invalid")
        minimum = 1 if plan["repeatability"] == "deterministic" else 2
        if type(plan["final_repeats"]) is not int or plan["final_repeats"] < minimum:
            raise ATKError("BUDGET_EXHAUSTED", "final repeat plan is below required minimum")
        budget = plan["budget"]
        if (
            not isinstance(budget, dict)
            or type(budget.get("executions")) is not int
            or budget["executions"]
            < (1 if external_fix_round else 2) * len(set(plan["case_ids"])) * plan["final_repeats"]
            or any(type(budget.get(key, 0)) is not int or budget.get(key, 0) < 0 for key in ("probes", "candidates"))
        ):
            raise ATKError("BUDGET_EXHAUSTED", "budget must reserve the frozen final validation")
        if type(plan.get("max_retries_per_slot", 0)) is not int or plan.get("max_retries_per_slot", 0) < 0:
            raise ATKError("COMPARISON_INVALID", "max_retries_per_slot must be non-negative")
        if plan.get("objective", "quality") not in {"quality", "efficiency"}:
            raise ATKError("COMPARISON_INVALID", "objective must be quality or efficiency")
        if plan.get("objective") == "efficiency" and plan.get("efficiency_metric") not in {
            "cost",
            "duration_seconds",
            "tool_calls",
        }:
            raise ATKError("COMPARISON_INVALID", "efficiency objective needs a supported metric")
        if not isinstance(plan.get("metric_limits", {}), dict):
            raise ATKError("COMPARISON_INVALID", "metric_limits must be an object")
        for key, value_to_check in {
            **{key: plan[key] for key in ("min_primary_delta", "min_efficiency_delta") if key in plan},
            **plan.get("metric_limits", {}),
        }.items():
            if type(value_to_check) not in {int, float} or not math.isfinite(value_to_check) or value_to_check < 0:
                raise ATKError("COMPARISON_INVALID", f"{key} must be non-negative")
        if set(plan.get("metric_limits", {})) - {"max_total_cost", "max_mean_duration_seconds", "max_mean_tool_calls"}:
            raise ATKError("COMPARISON_INVALID", "unsupported metric limit")
        preparation = plan["replay_preparation"]
        if not isinstance(preparation, dict) or preparation.get("mode") not in {"stateless", "command"}:
            raise ATKError("NOT_REPLAYABLE", "replay preparation must be frozen before candidate work")
        if preparation["mode"] == "stateless" and not preparation.get("reason"):
            raise ATKError("NOT_REPLAYABLE", "stateless replay needs a recorded cache assessment")
        if preparation["mode"] == "command" and (
            not isinstance(preparation.get("argv"), list)
            or not preparation["argv"]
            or any(not isinstance(part, str) or not part for part in preparation["argv"])
            or type(preparation.get("timeout_seconds")) is not int
            or preparation["timeout_seconds"] < 1
        ):
            raise ATKError("NOT_REPLAYABLE", "replay preparation needs command arguments and a finite timeout")
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
            raise ATKError("COMPARISON_INVALID", "Issue dependencies or workaround scope are outside the plan")
        project_hash = digest(read_json(root / "project.json"))
        if plan.get("run_config_hash", project_hash) != project_hash:
            raise ATKError("COMPARISON_INVALID", "frozen runner configuration differs from the project")
        plan["run_config_hash"] = project_hash
        write_json(_round_path(root, value["id"]) / "plan.json", plan, immutable=True)
        value["status"] = "ready"
        _write_round(root, value)
        return value


def _allowed(path: str, patterns: list[str]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/") for prefix in patterns)


def prepare_candidate(repo: Path, root: Path, request: dict) -> dict:
    with locked(root):
        value = _round(root, request["round_id"])
        plan = read_json(_round_path(root, value["id"]) / "plan.json")
        if value["status"] not in {"ready", "optimizing"} or value["pending_candidate_id"]:
            raise ATKError("WORKSPACE_CONFLICT", "round is not ready for another candidate")
        if value.get("external_fix_identity"):
            raise ATKError("WORKSPACE_CONFLICT", "external fix Round verifies its new B0 without an Agent candidate")
        exposure_path = root / "source-exposure.json"
        if exposure_path.exists() and any(
            entry["round_id"] == value["id"] for entry in read_json(exposure_path).get("groups", {}).values()
        ):
            raise ATKError("COMPARISON_INVALID", "holdout was exposed; start a new Round before editing")
        if len(value["candidate_ids"]) >= plan["budget"].get("candidates", len(set(plan["issue_ids"]))):
            raise ATKError("BUDGET_EXHAUSTED", "candidate budget is exhausted")
        verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
        if changed_paths(repo) != set(value["baseline_untracked"]):
            raise ATKError("DIRTY_BASELINE", "workspace has unaccounted changes before candidate")
        issue_id = request["primary_issue_id"]
        if issue_id not in plan["issue_ids"]:
            raise ATKError("WORKSPACE_CONFLICT", "issue is outside frozen plan")
        change_kind = request.get("change_kind", "fix")
        if change_kind not in {"fix", "workaround"}:
            raise ATKError("COMPARISON_INVALID", "candidate change_kind is invalid")
        blocked = set(request.get("blocked_by_issue_ids", [])) | set(
            plan.get("blocked_by_issue_ids_by_issue", {}).get(issue_id, [])
        )
        issue = _latest_issue(root, value["id"], issue_id)
        if issue and issue["disposition"] == "external_handoff" and issue["resolution"] != "resolved":
            if change_kind != "workaround":
                raise ATKError("WORKSPACE_CONFLICT", "out-of-scope Issue cannot be a local fix")
            blocked.add(issue_id)
        for blocker_id in blocked:
            blocker = _latest_issue(root, value["id"], blocker_id)
            if blocker_id not in plan["issue_ids"] or not blocker or blocker["disposition"] != "external_handoff":
                raise ATKError("INCOMPLETE_EVIDENCE", f"blocking Issue is not a recorded handoff: {blocker_id}")
        if change_kind == "workaround" and (not blocked or blocked - set(plan.get("workaround_issue_ids", []))):
            raise ATKError("WORKSPACE_CONFLICT", "workaround needs a frozen authorization for its blocking Issues")
        paths = request["paths"]
        if not paths or len(paths) != len(set(paths)):
            raise ATKError("SCOPE_VIOLATION", "candidate must declare distinct paths")
        for path in paths:
            safe_path(repo, path)
            if (
                path.startswith(".atk/")
                or not _allowed(path, plan["allowed_paths"])
                or _allowed(path, plan["protected_paths"])
            ):
                raise ATKError("SCOPE_VIOLATION", f"candidate path is not authorized: {path}")
        candidate_id = new_id("candidate")
        candidate = {
            "schema_version": 2,
            "id": candidate_id,
            "created_at": now(),
            "round_id": value["id"],
            "primary_issue_id": issue_id,
            "related_issue_ids": sorted((set(request.get("related_issue_ids", [])) | blocked) - {issue_id}),
            "parent_commit": value["current_commit"],
            "parent_revision_id": value.get("current_revision_id"),
            "blocked_by_issue_ids": sorted(blocked),
            "change_kind": change_kind,
            "declared_paths": paths,
            "content_status": "draft",
        }
        folder = _round_path(root, value["id"]) / "candidates" / candidate_id
        write_json(folder / "draft.json", candidate, immutable=True)
        value["pending_candidate_id"] = candidate_id
        value["candidate_ids"].append(candidate_id)
        value["status"] = "optimizing"
        _write_round(root, value)
        return candidate


def seal_candidate(repo: Path, root: Path, request: dict) -> dict:
    with locked(root):
        value = _round(root, request["round_id"])
        candidate_id = value["pending_candidate_id"]
        if candidate_id != request["candidate_id"]:
            raise ATKError("WORKSPACE_CONFLICT", "candidate is not pending")
        folder = _round_path(root, value["id"]) / "candidates" / candidate_id
        draft = read_json(folder / "draft.json")
        if (folder / "candidate.json").exists():
            raise ATKError("WORKSPACE_CONFLICT", "candidate already sealed")
        verify_repo(repo, expected_head=draft["parent_commit"], expected_branch=value["branch"])
        actual = changed_paths(repo) - set(value["baseline_untracked"])
        if actual != set(draft["declared_paths"]):
            raise ATKError("SCOPE_VIOLATION", f"actual changed paths differ from declared paths: {sorted(actual)}")
        files = {}
        for name in sorted(actual):
            _check_regular_parent(repo, name)
            data = content(repo, name)
            files[name] = {"exists": data is not None, "sha256": digest(data) if data is not None else None}
            if data is not None:
                target = folder / "files" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        patch = git(repo, "--literal-pathspecs", "diff", "--binary", "--no-ext-diff", "--", *sorted(actual))
        (folder / "changes.patch").write_bytes(patch)
        write_json(folder / "files.json", files, immutable=True)
        revision = {
            "schema_version": 2,
            "id": new_id("revision"),
            "created_at": now(),
            "base_commit": draft["parent_commit"],
            "candidate_id": candidate_id,
            "files_hash": digest(files),
            "changed_paths": sorted(actual),
        }
        write_json(folder / "revision.json", revision, immutable=True)
        sealed = {
            **draft,
            "content_status": "sealed",
            "revision_id": revision["id"],
            "changed_paths": sorted(actual),
            "files_hash": revision["files_hash"],
        }
        write_json(folder / "candidate.json", sealed, immutable=True)
        return sealed


def _verify_sealed(repo: Path, folder: Path, candidate: dict) -> dict:
    files = read_json(folder / "files.json")
    if digest(files) != candidate["files_hash"]:
        raise ATKError("REVISION_MISMATCH", "sealed file manifest changed")
    for name, expected in files.items():
        data = content(repo, name)
        if (data is not None) != expected["exists"] or (data is not None and digest(data) != expected["sha256"]):
            raise ATKError("REVISION_MISMATCH", f"candidate changed after sealing: {name}")
    return files


def _verify_candidate_commit(repo: Path, candidate: dict, files: dict, operation: dict) -> None:
    if git(repo, "rev-list", "--parents", "-n", "1", "HEAD").decode().split()[1:] != [candidate["parent_commit"]]:
        raise ATKError("COMMIT_FAILED", "candidate commit parent differs from its sealed checkpoint")
    committed_paths = set(
        filter(None, git(repo, "diff", "--name-only", "-z", "--no-renames", "HEAD^", "HEAD").decode().split("\0"))
    )
    if committed_paths != set(files):
        raise ATKError("COMMIT_FAILED", "candidate commit contains unsealed paths")
    message = git(repo, "log", "-1", "--format=%B").decode()
    if (
        f"ATK-Candidate: {candidate['id']}" not in message
        or f"ATK-Operation: {operation['id']}" not in message
        or f"ATK-Round: {candidate['round_id']}" not in message
    ):
        raise ATKError("COMMIT_FAILED", "candidate commit identity was changed")
    for name, state in files.items():
        committed = _git_file(repo, "HEAD", name)
        if (committed is not None) != state["exists"] or (
            committed is not None and digest(committed) != state["sha256"]
        ):
            raise ATKError("REVISION_MISMATCH", f"committed candidate content differs: {name}")


def require_override(request: dict, action: str) -> dict:
    authorization = request.get("override_authorization")
    if (
        not isinstance(authorization, dict)
        or authorization.get("source") != "user"
        or authorization.get("action") != action
        or not authorization.get("reason")
        or not authorization.get("risk")
    ):
        raise ATKError("WORKSPACE_CONFLICT", "override needs explicit user action, reason, and risk")
    return authorization


def _restore_parent(repo: Path, folder: Path, candidate: dict, files: dict) -> None:
    parent_paths = tracked_paths(repo)
    for name, state in files.items():
        path = safe_path(repo, name)
        if name in parent_paths:
            git(
                repo,
                "--literal-pathspecs",
                "restore",
                f"--source={candidate['parent_commit']}",
                "--worktree",
                "--",
                name,
            )
        elif state["exists"] and path.exists() and digest(path.read_bytes()) == state["sha256"]:
            path.unlink()
        else:
            raise ATKError("WORKSPACE_CONFLICT", f"new candidate file cannot be safely removed: {name}")


def decide_candidate(repo: Path, root: Path, request: dict) -> dict:
    with locked(root):
        value = _round(root, request["round_id"])
        candidate_id = request["candidate_id"]
        if value["pending_candidate_id"] != candidate_id:
            raise ATKError("WORKSPACE_CONFLICT", "candidate is not pending")
        folder = _round_path(root, value["id"]) / "candidates" / candidate_id
        candidate = read_json(folder / "candidate.json")
        verify_repo(repo, expected_head=candidate["parent_commit"], expected_branch=value["branch"])
        files = _verify_sealed(repo, folder, candidate)
        if changed_paths(repo) - set(value["baseline_untracked"]) != set(files):
            raise ATKError("WORKSPACE_CONFLICT", "unknown workspace changes appeared after sealing")
        action = request["action"]
        validation = read_json(
            _round_path(root, value["id"]) / "validations" / safe_id(request["validation_id"]) / "validation.json"
        )
        if (
            validation.get("right_revision_id") != candidate["revision_id"]
            or validation.get("left_commit") != candidate["parent_commit"]
        ):
            raise ATKError("COMPARISON_INVALID", "validation does not match candidate and parent")
        override = request.get("override") is True
        if override and action != "keep":
            raise ATKError("WORKSPACE_CONFLICT", "trial override applies only to keep")
        if action == "keep" and validation["result"] != "pass" and not override:
            raise ATKError("COMPARISON_INVALID", "keep requires a passing validation or explicit override")
        authorization = require_override(request, "trial_commit") if override else None
        if action == "keep":
            blockers = [_latest_issue(root, value["id"], issue_id) for issue_id in candidate["blocked_by_issue_ids"]]
            if any(issue is None for issue in blockers):
                raise ATKError("INCOMPLETE_EVIDENCE", "candidate blocking Issue disappeared")
            unresolved = [issue for issue in blockers if issue["resolution"] != "resolved"]
            if unresolved and (candidate["change_kind"] != "workaround" or validation["result"] != "pass"):
                raise ATKError("WORKSPACE_CONFLICT", "unresolved external Issue blocks candidate adoption")
        if action not in {"keep", "reject", "defer"}:
            raise ATKError("WORKSPACE_CONFLICT", f"unsupported candidate action: {action}")
        operation = {
            "id": new_id("operation"),
            "action": action,
            "round_id": value["id"],
            "candidate_id": candidate_id,
            "parent_commit": candidate["parent_commit"],
            "files_hash": candidate["files_hash"],
            "validation_id": request["validation_id"],
            "reason": request["reason"],
            "override": override,
            "override_authorization": authorization,
            "validation_result": validation["result"],
            "stage": "prepared",
            "created_at": now(),
        }
        op_path = _round_path(root, value["id"]) / "operations" / f"{operation['id']}.json"
        write_json(op_path, operation, immutable=True)
        if action == "keep":
            git(repo, "--literal-pathspecs", "add", "-A", "--", *candidate["changed_paths"])
            if staged_paths(repo) != set(files):
                raise ATKError("COMMIT_FAILED", "index contains unexpected paths; operation is recorded")
            for name, expected in files.items():
                staged = _git_file(repo, "", name)
                if (staged is not None) != expected["exists"] or (
                    staged is not None and digest(staged) != expected["sha256"]
                ):
                    raise ATKError("REVISION_MISMATCH", f"index content differs from sealed candidate: {name}")
            try:
                git(
                    repo,
                    "commit",
                    "-m",
                    request.get("message", f"feat: [agent] ATK {candidate_id}"),
                    "-m",
                    f"ATK-Round: {value['id']}\nATK-Candidate: {candidate_id}\nATK-Operation: {operation['id']}",
                )
            except ATKError as exc:
                raise ATKError(
                    "COMMIT_FAILED", f"commit failed; operation {operation['id']} needs inspection: {exc}"
                ) from exc
            operation["stage"] = "committed"
            operation["commit"] = head(repo)
            write_json(op_path, operation)
            _verify_candidate_commit(repo, candidate, files, operation)
            if changed_paths(repo) != set(value["baseline_untracked"]) or staged_paths(repo):
                raise ATKError("COMMIT_FAILED", "workspace not clean after commit; operation requires inspection")
            value["current_commit"] = operation["commit"]
            value["current_revision_id"] = candidate["revision_id"]
            value["active_candidate_ids"].append(candidate_id)
        else:
            _restore_parent(repo, folder, candidate, files)
            if changed_paths(repo) != set(value["baseline_untracked"]):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "restoration incomplete; operation requires inspection")
            operation["stage"] = "restored"
            write_json(op_path, operation)
        decision = {
            "schema_version": 2,
            "id": new_id("decision"),
            "created_at": now(),
            "round_id": value["id"],
            "candidate_id": candidate_id,
            "action": action,
            "validation_id": request["validation_id"],
            "reason": request["reason"],
            "override": override,
            "override_authorization": authorization,
            "validation_result": validation["result"],
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


def inspect_or_recover_operation(repo: Path, root: Path, request: dict) -> dict:
    """Reconcile a candidate operation with its exact Git and artifact state."""
    with locked(root):
        folder = _round_path(root, request["round_id"])
        operation = read_json(folder / "operations" / f"{safe_id(request['operation_id'])}.json")
        if operation.get("action") == "rollback_to":
            return _recover_rollback_locked(repo, folder, operation)
        if operation.get("action") != "keep":
            raise ATKError("GIT_OPERATION_INTERRUPTED", "restore operation needs manual content inspection")
        candidate_id = operation["candidate_id"]
        candidate_folder = folder / "candidates" / candidate_id
        decision_path = candidate_folder / "decision.json"
        value = _round(root, request["round_id"])
        if decision_path.exists() and value["pending_candidate_id"] != candidate_id:
            decision = read_json(decision_path)
            if (
                operation.get("stage") != "complete"
                or decision.get("operation_id") != operation["id"]
                or decision.get("after_commit") != operation.get("commit")
            ):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "completed operation identity is inconsistent")
            return {"stage": "complete", "decision": decision}
        if value["pending_candidate_id"] != candidate_id:
            raise ATKError("GIT_OPERATION_INTERRUPTED", "operation is not the pending candidate")
        candidate = read_json(candidate_folder / "candidate.json")
        files = read_json(candidate_folder / "files.json")
        if (
            operation.get("parent_commit") != candidate["parent_commit"]
            or operation.get("files_hash") != candidate["files_hash"]
            or operation.get("round_id") != value["id"]
        ):
            raise ATKError("GIT_OPERATION_INTERRUPTED", "operation identity differs from sealed candidate")
        actual_head = head(repo)
        if actual_head == candidate["parent_commit"] and not decision_path.exists():
            if branch(repo) != value["branch"] or operation["stage"] not in {"prepared", "aborted"}:
                raise ATKError("GIT_OPERATION_INTERRUPTED", "pre-commit operation state is unknown")
            _verify_sealed(repo, candidate_folder, candidate)
            if (changed_paths(repo) | staged_paths(repo)) - set(value["baseline_untracked"]) != set(files):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "candidate workspace changed during commit attempt")
            staged = staged_paths(repo)
            if staged - set(files):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "unknown staged paths require inspection")
            for name in staged:
                state = files[name]
                indexed = _git_file(repo, "", name)
                if (indexed is not None) != state["exists"] or (
                    indexed is not None and digest(indexed) != state["sha256"]
                ):
                    raise ATKError("GIT_OPERATION_INTERRUPTED", "staged content differs from sealed candidate")
            if staged:
                git(repo, "--literal-pathspecs", "restore", "--staged", "--", *sorted(staged))
            if staged_paths(repo):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "candidate index was not restored")
            operation["stage"] = "aborted"
            write_json(folder / "operations" / f"{operation['id']}.json", operation)
            return {"stage": "aborted", "candidate_id": candidate_id}
        if branch(repo) != value["branch"]:
            raise ATKError("GIT_OPERATION_INTERRUPTED", "branch changed during candidate commit")
        _verify_candidate_commit(repo, candidate, files, operation)
        if changed_paths(repo) != set(value["baseline_untracked"]) or staged_paths(repo):
            raise ATKError("GIT_OPERATION_INTERRUPTED", "workspace differs after candidate commit")
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
            raise ATKError("GIT_OPERATION_INTERRUPTED", "saved Decision disagrees with candidate commit")
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


def _verify_rollback_commit(repo: Path, operation: dict) -> None:
    if git(repo, "rev-list", "--parents", "-n", "1", "HEAD").decode().split()[1:] != [operation["before_commit"]]:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "rollback commit parent changed")
    if git(repo, "rev-parse", "HEAD^{tree}") != git(repo, "rev-parse", f"{operation['target_commit']}^{{tree}}"):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "rollback commit tree differs from target checkpoint")
    paths = set(
        filter(None, git(repo, "diff", "--name-only", "-z", "--no-renames", "HEAD^", "HEAD").decode().split("\0"))
    )
    if paths != set(operation["paths"]):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "rollback commit contains unexpected paths")
    message = git(repo, "log", "-1", "--format=%B").decode()
    if f"ATK-Round: {operation['round_id']}" not in message or f"ATK-Rollback: {operation['id']}" not in message:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "rollback commit identity was changed")


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
            raise ATKError("GIT_OPERATION_INTERRUPTED", "completed rollback identity is inconsistent")
        return {"stage": "complete", "decision": decision}
    if value["current_commit"] != operation["before_commit"] or value["pending_candidate_id"]:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "Round changed during rollback")
    if value["active_candidate_ids"][operation["target_index"] :] != operation["withdrawn_candidate_ids"]:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "accepted chain differs from rollback record")
    if branch(repo) != value["branch"]:
        raise ATKError("GIT_OPERATION_INTERRUPTED", "branch changed during rollback")
    paths = operation["paths"]
    if head(repo) == operation["before_commit"] and not decision_path.exists():
        if operation["stage"] not in {"prepared", "aborted"}:
            raise ATKError("GIT_OPERATION_INTERRUPTED", "rollback commit disappeared after recording")
        if staged_paths(repo) - set(paths):
            raise ATKError("GIT_OPERATION_INTERRUPTED", "unknown staged paths require inspection")
        if (changed_paths(repo) | staged_paths(repo)) - set(value["baseline_untracked"]) - set(paths):
            raise ATKError("GIT_OPERATION_INTERRUPTED", "unknown workspace changes appeared during rollback")
        for name in paths:
            known = {
                _git_file(repo, operation["before_commit"], name),
                _git_file(repo, operation["target_commit"], name),
            }
            if content(repo, name) not in known or (
                name in staged_paths(repo) and _git_file(repo, "", name) not in known
            ):
                raise ATKError("GIT_OPERATION_INTERRUPTED", f"unknown rollback content requires inspection: {name}")
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
        if changed_paths(repo) != set(value["baseline_untracked"]) or staged_paths(repo):
            raise ATKError("GIT_OPERATION_INTERRUPTED", "rollback abort did not restore the starting checkpoint")
        operation["stage"] = "aborted"
        write_json(folder / "operations" / f"{operation['id']}.json", operation)
        return {"stage": "aborted", "round_id": value["id"]}
    _verify_rollback_commit(repo, operation)
    if changed_paths(repo) != set(value["baseline_untracked"]) or staged_paths(repo):
        raise ATKError("GIT_OPERATION_INTERRUPTED", "workspace differs after rollback commit")
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
        raise ATKError("GIT_OPERATION_INTERRUPTED", "saved rollback Decision disagrees with commit")
    if not decision_path.exists():
        write_json(decision_path, decision, immutable=True)
    value["active_candidate_ids"] = value["active_candidate_ids"][: operation["target_index"]]
    value["current_commit"] = head(repo)
    value["current_revision_id"] = operation["target_revision_id"]
    _write_round(folder.parent.parent, value)
    operation["stage"] = "complete"
    operation["after_commit"] = value["current_commit"]
    write_json(folder / "operations" / f"{operation['id']}.json", operation)
    return {"stage": "complete", "decision": decision}


def rollback_to(repo: Path, root: Path, request: dict) -> dict:
    with locked(root):
        value = _round(root, request["round_id"])
        if value["status"] not in {"ready", "optimizing", "finalizing"}:
            raise ATKError("WORKSPACE_CONFLICT", "round cannot be rolled back from its current state")
        if value["pending_candidate_id"]:
            raise ATKError("WORKSPACE_CONFLICT", "resolve pending candidate before rollback")
        verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
        if changed_paths(repo) != set(value["baseline_untracked"]):
            raise ATKError("WORKSPACE_CONFLICT", "unaccounted changes block rollback")
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
            raise ATKError("WORKSPACE_CONFLICT", "rollback target is not an earlier active checkpoint")
        changed = set(filter(None, git(repo, "diff", "--name-only", "-z", target_commit, "HEAD").decode().split("\0")))
        plan = read_json(folder / "plan.json")
        if any(
            not _allowed(name, plan["allowed_paths"]) or _allowed(name, plan["protected_paths"]) for name in changed
        ):
            raise ATKError("SCOPE_VIOLATION", "rollback diff includes paths outside candidate scope")
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
            git(repo, "--literal-pathspecs", "add", "-A", "--", *sorted(changed))
        if staged_paths(repo) != changed:
            raise ATKError("GIT_OPERATION_INTERRUPTED", "rollback index differs from expected paths")
        try:
            git(
                repo,
                "commit",
                *(["--allow-empty"] if not changed else []),
                "-m",
                f"revert: [agent] ATK rollback {operation['id']}",
                "-m",
                f"ATK-Round: {value['id']}\nATK-Rollback: {operation['id']}",
            )
        except ATKError as exc:
            raise ATKError("COMMIT_FAILED", f"rollback operation {operation['id']} needs inspection: {exc}") from exc
        operation["stage"] = "committed"
        write_json(op_path, operation)
        _verify_rollback_commit(repo, operation)
        return _recover_rollback_locked(repo, folder, operation)["decision"]


@contextmanager
def temporary_revision(repo: Path, root: Path, round_id: str, target_commit: str):
    """Replay a known checkpoint in the same directory without moving HEAD or index."""
    with locked(root):
        value = _round(root, round_id)
        if value["pending_candidate_id"]:
            raise ATKError("WORKSPACE_CONFLICT", "temporary replay needs no pending candidate")
        verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
        if changed_paths(repo) != set(value["baseline_untracked"]):
            raise ATKError("WORKSPACE_CONFLICT", "unaccounted changes block temporary replay")
        known = {value["baseline_commit"]}
        for candidate_id in value["active_candidate_ids"]:
            decision = read_json(_round_path(root, round_id) / "candidates" / candidate_id / "decision.json")
            known.add(decision["after_commit"])
        if target_commit not in known:
            raise ATKError("REVISION_MISMATCH", "temporary replay target is not a recorded checkpoint")
        paths = set(filter(None, git(repo, "diff", "--name-only", "-z", target_commit, "HEAD").decode().split("\0")))
        for name in paths:
            current = _git_file(repo, "HEAD", name)
            if content(repo, name) != current:
                raise ATKError("WORKSPACE_CONFLICT", f"replay path differs from current commit: {name}")
        preparation = read_json(_round_path(root, round_id) / "plan.json").get("replay_preparation")
        if not preparation:
            raise ATKError("NOT_REPLAYABLE", "Round has no frozen replay preparation")
        operation = {
            "id": new_id("operation"),
            "action": "temporary_replay",
            "stage": "prepared",
            "created_at": now(),
            "before_commit": value["current_commit"],
            "target_commit": target_commit,
            "paths": sorted(paths),
            "replay_preparation_hash": digest(preparation),
        }
        op_path = _round_path(root, round_id) / "operations" / f"{operation['id']}.json"
        write_json(op_path, operation, immutable=True)
        ready = False
        try:
            operation["stage"] = "switching"
            write_json(op_path, operation)
            if paths:
                git(
                    repo,
                    "--literal-pathspecs",
                    "restore",
                    f"--source={target_commit}",
                    "--worktree",
                    "--",
                    *sorted(paths),
                )
            if any(content(repo, name) != _git_file(repo, target_commit, name) for name in paths):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "target source differs after replay switch")
            _prepare_replay(repo, preparation)
            if (
                head(repo) != value["current_commit"]
                or staged_paths(repo)
                or changed_paths(repo) != paths | set(value["baseline_untracked"])
                or any(content(repo, name) != _git_file(repo, target_commit, name) for name in paths)
            ):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "replay preparation changed source or Git state")
            operation["stage"] = "switched"
            write_json(op_path, operation)
            ready = True
            yield
        finally:
            if head(repo) != value["current_commit"] or staged_paths(repo):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "HEAD or index changed during temporary replay")
            if any(
                content(repo, name)
                not in {_git_file(repo, target_commit, name), _git_file(repo, value["current_commit"], name)}
                for name in paths
            ):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "replay source has unknown content; inspection required")
            if paths:
                git(
                    repo,
                    "--literal-pathspecs",
                    "restore",
                    f"--source={value['current_commit']}",
                    "--worktree",
                    "--",
                    *sorted(paths),
                )
            _prepare_replay(repo, preparation)
            if changed_paths(repo) != set(value["baseline_untracked"]):
                raise ATKError("GIT_OPERATION_INTERRUPTED", "temporary replay did not restore starting checkpoint")
            operation["stage"] = "complete" if ready else "aborted"
            write_json(op_path, operation)


def _prepare_replay(repo: Path, preparation: dict) -> None:
    if preparation["mode"] == "stateless":
        return
    try:
        result = subprocess.run(
            preparation["argv"], cwd=repo, capture_output=True, timeout=preparation["timeout_seconds"], check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ATKError("NOT_REPLAYABLE", f"replay preparation failed: {exc}") from exc
    if result.returncode:
        raise ATKError("NOT_REPLAYABLE", f"replay preparation exited with {result.returncode}")


@contextmanager
def execution_revision(repo: Path, root: Path, request: dict):
    """Bind an evaluation to actual source content, switching only known commits."""
    round_id = request.get("round_id")
    if not round_id:
        if not request.get("revision_commit"):
            raise ATKError("REVISION_MISMATCH", "unfrozen run requires an explicit revision_commit")
        verify_repo(repo, expected_head=request["revision_commit"])
        if git(repo, "diff", "--name-only", "-z"):
            raise ATKError("DIRTY_BASELINE", "unfrozen local run requires a clean tracked worktree")
        yield
        if head(repo) != request["revision_commit"] or staged_paths(repo) or git(repo, "diff", "--name-only", "-z"):
            raise ATKError("WORKSPACE_CONFLICT", "runner changed unfrozen source or Git state")
        return
    value = _round(root, round_id)
    revision_id = request["revision_id"]
    if value["pending_candidate_id"]:
        folder = _round_path(root, round_id) / "candidates" / value["pending_candidate_id"]
        candidate = read_json(folder / "candidate.json")
        if candidate["revision_id"] != revision_id:
            raise ATKError("REVISION_MISMATCH", "pending candidate Revision differs from run request")
        verify_repo(repo, expected_head=candidate["parent_commit"], expected_branch=value["branch"])
        _verify_sealed(repo, folder, candidate)
        if changed_paths(repo) - set(value["baseline_untracked"]) != set(candidate["changed_paths"]):
            raise ATKError("WORKSPACE_CONFLICT", "candidate workspace has extra changes")
        yield
        _verify_sealed(repo, folder, candidate)
        if head(repo) != candidate["parent_commit"] or staged_paths(repo):
            raise ATKError("WORKSPACE_CONFLICT", "runner changed candidate Git state")
        return
    checkpoints = {value["baseline_revision_id"]: value["baseline_commit"]}
    for candidate_id in value["active_candidate_ids"]:
        folder = _round_path(root, round_id) / "candidates" / candidate_id
        checkpoints[read_json(folder / "candidate.json")["revision_id"]] = read_json(folder / "decision.json")[
            "after_commit"
        ]
    target = checkpoints.get(revision_id)
    if not target:
        raise ATKError("REVISION_MISMATCH", "run request names an unknown Revision")
    if revision_id == value["current_revision_id"]:
        verify_repo(repo, expected_head=value["current_commit"], expected_branch=value["branch"])
        if changed_paths(repo) != set(value["baseline_untracked"]):
            raise ATKError("WORKSPACE_CONFLICT", "current checkpoint workspace is dirty")
        yield
        if (
            head(repo) != value["current_commit"]
            or staged_paths(repo)
            or changed_paths(repo) != set(value["baseline_untracked"])
        ):
            raise ATKError("WORKSPACE_CONFLICT", "runner changed current checkpoint source")
    else:
        with temporary_revision(repo, root, round_id, target):
            yield
