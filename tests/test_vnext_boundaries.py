from __future__ import annotations

import sys
from pathlib import Path

import pytest

import agent_tune_kit.checkpoints as checkpoints
from agent_tune_kit.checkpoints import (
    decide_candidate,
    freeze_round,
    inspect_or_recover_operation,
    prepare_candidate,
    rollback_to,
    seal_candidate,
    temporary_revision,
)
from agent_tune_kit.core import ATKError, digest, read_json, validate_evidence, write_json
from agent_tune_kit.execution import run_evaluation
from agent_tune_kit.governance import compare_and_gate, finish_round, store_diagnosis, validate_external_fix
from tests.test_vnext_flow import git
from tests.test_vnext_limits import SPEC, assess, project


def _passing_validation(root: Path, round_data: dict, candidate: dict) -> str:
    validation_id = "validation-test"
    write_json(
        root / "rounds" / round_data["id"] / "validations" / validation_id / "validation.json",
        {
            "result": "pass",
            "left_commit": candidate["parent_commit"],
            "right_revision_id": candidate["revision_id"],
        },
    )
    return validation_id


def _handoff_issue() -> dict:
    return {
        "id": "tool",
        "symptom": "tool fails",
        "hypothesis": "tool defect",
        "competing_explanations": ["Agent input error"],
        "checks": [{"status": "not_run", "reason": "needs service fixture"}],
        "evidence_refs": [],
        "mechanism_evidence_refs": [],
        "intervention_validation_refs": [],
        "root_cause_status": "hypothesis",
        "intervention_layer": "tool",
        "responsible_component": "external-tool",
        "case_ids": ["case"],
        "priority": "high",
        "disposition": "external_handoff",
        "resolution": "open",
        "next_action": "reproduce direct tool call",
        "handoff": {"trigger_input": "task", "expected": "ok", "actual": "error"},
    }


def test_supported_diagnosis_requires_check_evidence(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, dataset, round_data, _ = project(tmp_path, "print('tool error')\n", rows)
    batch = run_evaluation(
        root,
        {
            "dataset_id": dataset["id"],
            "case_ids": ["case"],
            "purpose": "evaluation",
            "revision_id": round_data["baseline_revision_id"],
            "revision_commit": git(repo, "rev-parse", "HEAD"),
        },
    )
    ref = {"batch_id": batch["id"], "evidence_id": next(iter(validate_evidence(root, batch["id"])[2]))}
    issue = {
        **_handoff_issue(),
        "root_cause_status": "supported",
        "mechanism_evidence_refs": [ref],
        "competing_explanations_addressed": "direct tool check rules out Agent input error",
        "checks": [{"status": "completed", "expected": "tool error", "actual": "tool error"}],
    }
    with pytest.raises(ATKError, match="completed check needs"):
        store_diagnosis(root, {"round_id": round_data["id"], "issues": [issue]})
    issue["checks"][0]["evidence_refs"] = [ref]
    stored = store_diagnosis(root, {"round_id": round_data["id"], "issues": [issue]})
    assert stored["issues"][0]["root_cause_status"] == "supported"


def test_external_issue_blocks_normal_keep_but_allows_authorized_workaround(tmp_path: Path) -> None:
    script = "from pathlib import Path\nprint('ok' if Path('prompt.txt').read_text()=='new' else 'bad')\n"
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows, ["issue", "tool"])
    store_diagnosis(
        root,
        {
            "round_id": round_data["id"],
            "issues": [_handoff_issue()],
        },
    )
    plan["blocked_by_issue_ids_by_issue"] = {"issue": ["tool"]}
    plan["workaround_issue_ids"] = ["tool"]
    plan["budget"]["candidates"] = 2
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    with pytest.raises(ATKError, match="out-of-scope"):
        prepare_candidate(
            repo, root, {"round_id": round_data["id"], "primary_issue_id": "tool", "paths": ["prompt.txt"]}
        )
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "evaluation",
        "revision_id": round_data["baseline_revision_id"],
        "round_id": round_data["id"],
    }
    baseline = run_evaluation(root, request)
    left = assess(root, baseline, {"case": "ok"})
    for change_kind in ("fix", "workaround"):
        draft = prepare_candidate(
            repo,
            root,
            {
                "round_id": round_data["id"],
                "primary_issue_id": "issue",
                "paths": ["prompt.txt"],
                "change_kind": change_kind,
            },
        )
        assert draft["blocked_by_issue_ids"] == ["tool"]
        (repo / "prompt.txt").write_text("new")
        sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
        candidate = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
        right = assess(root, candidate, {"case": "ok"})
        validation = compare_and_gate(
            root,
            {
                "round_id": round_data["id"],
                "mode": "incremental",
                "issue_id": "issue",
                "candidate_id": draft["id"],
                "left_assessment_id": left,
                "right_assessment_id": right,
                "left_commit": round_data["baseline_commit"],
            },
        )
        assert validation["result"] == "pass"
        decision = {
            "round_id": round_data["id"],
            "candidate_id": draft["id"],
            "validation_id": validation["id"],
            "reason": "candidate passes, service Issue remains open",
        }
        if change_kind == "fix":
            with pytest.raises(ATKError, match="unresolved external Issue"):
                decide_candidate(repo, root, {**decision, "action": "keep"})
            decide_candidate(repo, root, {**decision, "action": "reject"})
        else:
            decided = decide_candidate(repo, root, {**decision, "action": "keep"})
            assert decided["action"] == "keep"
    assert read_json(root / "rounds" / round_data["id"] / "issues" / "tool" / "revision-1.json")["resolution"] == "open"


def test_external_fix_new_baseline_needs_direct_and_end_to_end_evidence(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, dataset, first, plan = project(tmp_path, "print('ok')\n", rows, ["tool"])
    store_diagnosis(root, {"round_id": first["id"], "issues": [_handoff_issue()]})
    freeze_round(repo, root, {"round_id": first["id"], "plan": plan})
    finish_round(repo, root, {"round_id": first["id"], "action": "close_without_adoption", "reason": "tool owner"})
    second = checkpoints.create_round(
        repo,
        root,
        {"issue_ids": ["tool"], "previous_round_id": first["id"], "external_fix_identity": "tool@fixed"},
    )
    plan.update(
        {
            "allowed_paths": [],
            "commit_authorized": False,
            "budget": {"executions": 1, "probes": 1},
            "probe_permissions": [
                {
                    "id": "direct-tool",
                    "kind": "direct_component",
                    "component_identity": "tool@fixed",
                    "evaluation_spec_hash": digest(SPEC),
                    "command": [sys.executable, "-c", "print('ok')"],
                    "command_hash": digest([sys.executable, "-c", "print('ok')"]),
                    "runner_hash": digest(root / "adapters" / "runner.py"),
                    "case_ids": ["case"],
                    "isolation_ref": "local fixture",
                }
            ],
        }
    )
    freeze_round(repo, root, {"round_id": second["id"], "plan": plan})
    base_request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "revision_id": second["baseline_revision_id"],
        "round_id": second["id"],
    }
    batch = run_evaluation(root, {**base_request, "purpose": "evaluation", "phase": "external_fix"})
    assessment_id = assess(root, batch, {"case": "ok"})
    evidence_id = next(iter(validate_evidence(root, batch["id"])[2]))
    with pytest.raises(ATKError, match="component probe"):
        validate_external_fix(
            root,
            {
                "round_id": second["id"],
                "assessment_id": assessment_id,
                "direct_assessment_id": assessment_id,
                "direct_evidence_refs": [{"batch_id": batch["id"], "evidence_id": evidence_id}],
            },
        )
    probe = run_evaluation(
        root,
        {**base_request, "purpose": "diagnostic_probe", "probe_authorization_id": "direct-tool"},
    )
    direct_refs = [{"batch_id": probe["id"], "evidence_id": next(iter(validate_evidence(root, probe["id"])[2]))}]
    failed_direct = assess(root, probe, {"case": "wrong"})
    with pytest.raises(ATKError, match="passing authorized"):
        validate_external_fix(
            root,
            {
                "round_id": second["id"],
                "assessment_id": assessment_id,
                "direct_assessment_id": failed_direct,
                "direct_evidence_refs": direct_refs,
            },
        )
    direct_assessment_id = assess(root, probe, {"case": "ok"})
    validation = validate_external_fix(
        root,
        {
            "round_id": second["id"],
            "assessment_id": assessment_id,
            "direct_assessment_id": direct_assessment_id,
            "direct_evidence_refs": direct_refs,
        },
    )
    assert validation["result"] == "pass"
    fix = {
        "new_round_id": second["id"],
        "component_identity": "tool@fixed",
        "direct_evidence_refs": direct_refs,
        "end_to_end_validation_id": validation["id"],
    }
    with pytest.raises(ATKError, match="did not cover"):
        store_diagnosis(
            root,
            {"round_id": first["id"], "issues": [{**_handoff_issue(), "resolution": "resolved", "external_fix": fix}]},
        )
    finish_round(
        repo,
        root,
        {
            "round_id": second["id"],
            "action": "complete_external_fix",
            "validation_id": validation["id"],
            "reason": "verified",
        },
    )
    resolved = store_diagnosis(
        root,
        {"round_id": first["id"], "issues": [{**_handoff_issue(), "resolution": "resolved", "external_fix": fix}]},
    )
    assert resolved["issues"][0]["resolution"] == "resolved"


def test_replay_preparation_switches_cache_and_restores_after_failure(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    with (repo / ".git" / "info" / "exclude").open("a") as handle:
        handle.write("\ncache.txt\nfail-prep\n")
    script = (
        "from pathlib import Path\n"
        "value=Path('prompt.txt').read_text()\n"
        "if value=='old' and Path('fail-prep').exists(): raise SystemExit(2)\n"
        "Path('cache.txt').write_text(value)\n"
    )
    plan["replay_preparation"] = {"mode": "command", "argv": [sys.executable, "-c", script], "timeout_seconds": 2}
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    validation_id = _passing_validation(root, round_data, sealed)
    decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": draft["id"],
            "action": "keep",
            "validation_id": validation_id,
            "reason": "fixture",
        },
    )
    current_commit = git(repo, "rev-parse", "HEAD")
    with temporary_revision(repo, root, round_data["id"], round_data["baseline_commit"]):
        assert (repo / "prompt.txt").read_text() == "old"
        assert (repo / "cache.txt").read_text() == "old"
        assert git(repo, "rev-parse", "HEAD") == current_commit
    assert (repo / "prompt.txt").read_text() == (repo / "cache.txt").read_text() == "new"
    (repo / "fail-prep").write_text("")
    with (
        pytest.raises(ATKError, match="replay preparation exited"),
        temporary_revision(repo, root, round_data["id"], round_data["baseline_commit"]),
    ):
        pass
    assert (repo / "prompt.txt").read_text() == (repo / "cache.txt").read_text() == "new"
    assert git(repo, "rev-parse", "HEAD") == current_commit
    replays = [
        read_json(path)
        for path in (root / "rounds" / round_data["id"] / "operations").glob("*.json")
        if read_json(path)["action"] == "temporary_replay"
    ]
    assert {operation["stage"] for operation in replays} == {"complete", "aborted"}


def test_replay_restores_file_added_after_baseline(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    plan["allowed_paths"].append("extra.txt")
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["extra.txt"]}
    )
    (repo / "extra.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": draft["id"],
            "action": "keep",
            "validation_id": _passing_validation(root, round_data, sealed),
            "reason": "fixture",
        },
    )
    current_commit = git(repo, "rev-parse", "HEAD")
    with temporary_revision(repo, root, round_data["id"], round_data["baseline_commit"]):
        assert not (repo / "extra.txt").exists()
        assert git(repo, "rev-parse", "HEAD") == current_commit
    assert (repo / "extra.txt").read_text() == "new"
    assert git(repo, "rev-parse", "HEAD") == current_commit


def test_commit_recovery_records_original_commit_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    validation_id = _passing_validation(root, round_data, sealed)
    original_write_json = checkpoints.write_json

    def interrupt_decision(path: Path, value: dict, *, immutable: bool = False) -> None:
        if path.name == "decision.json":
            raise RuntimeError("simulated process interruption after commit")
        original_write_json(path, value, immutable=immutable)

    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "write_json", interrupt_decision)
        with pytest.raises(RuntimeError, match="simulated process interruption"):
            decide_candidate(
                repo,
                root,
                {
                    "round_id": round_data["id"],
                    "candidate_id": draft["id"],
                    "action": "keep",
                    "validation_id": validation_id,
                    "reason": "fixture",
                },
            )
    committed = git(repo, "rev-parse", "HEAD")
    operation = next((root / "rounds" / round_data["id"] / "operations").glob("*.json"))
    result = inspect_or_recover_operation(repo, root, {"round_id": round_data["id"], "operation_id": operation.stem})
    assert result["decision"]["after_commit"] == committed
    assert (
        inspect_or_recover_operation(repo, root, {"round_id": round_data["id"], "operation_id": operation.stem})[
            "stage"
        ]
        == "complete"
    )
    assert git(repo, "rev-parse", "HEAD") == committed


def test_commit_hook_cannot_add_unsealed_path(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho changed > agent.py\ngit add agent.py\n")
    hook.chmod(0o755)
    with pytest.raises(ATKError, match="unsealed paths"):
        decide_candidate(
            repo,
            root,
            {
                "round_id": round_data["id"],
                "candidate_id": draft["id"],
                "action": "keep",
                "validation_id": _passing_validation(root, round_data, sealed),
                "reason": "fixture",
            },
        )
    stored = read_json(root / "rounds" / round_data["id"] / "round.json")
    assert stored["current_commit"] == round_data["baseline_commit"]
    assert stored["pending_candidate_id"] == draft["id"]
    operation = next((root / "rounds" / round_data["id"] / "operations").glob("*.json"))
    with pytest.raises(ATKError, match="unsealed paths"):
        inspect_or_recover_operation(repo, root, {"round_id": round_data["id"], "operation_id": operation.stem})


def test_rejected_commit_hook_can_unstage_only_sealed_paths(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    request = {
        "round_id": round_data["id"],
        "candidate_id": draft["id"],
        "action": "keep",
        "validation_id": _passing_validation(root, round_data, sealed),
        "reason": "fixture",
    }
    with pytest.raises(ATKError, match="commit failed"):
        decide_candidate(repo, root, request)
    operation = next((root / "rounds" / round_data["id"] / "operations").glob("*.json"))
    result = inspect_or_recover_operation(repo, root, {"round_id": round_data["id"], "operation_id": operation.stem})
    assert result["stage"] == "aborted"
    assert git(repo, "diff", "--cached", "--name-only") == ""
    assert (repo / "prompt.txt").read_text() == "new"
    hook.unlink()
    decision = decide_candidate(repo, root, request)
    assert decision["action"] == "keep"


def test_decision_written_before_round_update_can_recover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "_write_round", lambda *_: (_ for _ in ()).throw(RuntimeError("interrupted")))
        with pytest.raises(RuntimeError, match="interrupted"):
            decide_candidate(
                repo,
                root,
                {
                    "round_id": round_data["id"],
                    "candidate_id": draft["id"],
                    "action": "keep",
                    "validation_id": _passing_validation(root, round_data, sealed),
                    "reason": "fixture",
                },
            )
    operation = next((root / "rounds" / round_data["id"] / "operations").glob("*.json"))
    result = inspect_or_recover_operation(repo, root, {"round_id": round_data["id"], "operation_id": operation.stem})
    assert result["stage"] == "complete"
    assert read_json(root / "rounds" / round_data["id"] / "round.json")["current_commit"] == git(
        repo, "rev-parse", "HEAD"
    )


def test_final_completion_rejects_wrong_revision(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": draft["id"],
            "action": "keep",
            "validation_id": _passing_validation(root, round_data, sealed),
            "reason": "fixture",
        },
    )
    write_json(
        root / "rounds" / round_data["id"] / "validations" / "wrong-final" / "validation.json",
        {
            "round_id": round_data["id"],
            "mode": "final",
            "result": "pass",
            "left_revision_id": round_data["baseline_revision_id"],
            "right_revision_id": "another-revision",
            "final_commit": git(repo, "rev-parse", "HEAD"),
            "plan_hash": digest(plan),
        },
    )
    with pytest.raises(ATKError, match="does not match current Round"):
        finish_round(
            repo,
            root,
            {"round_id": round_data["id"], "action": "complete", "validation_id": "wrong-final", "reason": ""},
        )


def test_trial_override_keeps_original_result_and_cannot_claim_normal_completion(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    validation_id = "not-proven"
    write_json(
        root / "rounds" / round_data["id"] / "validations" / validation_id / "validation.json",
        {
            "result": "no_effect",
            "left_commit": draft["parent_commit"],
            "right_revision_id": sealed["revision_id"],
        },
    )
    request = {
        "round_id": round_data["id"],
        "candidate_id": draft["id"],
        "action": "keep",
        "validation_id": validation_id,
        "reason": "trial",
        "override": True,
    }
    with pytest.raises(ATKError, match="explicit user action"):
        decide_candidate(repo, root, request)
    authorization = {"source": "user", "action": "trial_commit", "reason": "trial", "risk": "no measured benefit"}
    decision = decide_candidate(repo, root, {**request, "override_authorization": authorization})
    assert decision["validation_result"] == "no_effect"
    assert decision["override_authorization"] == authorization
    with pytest.raises(ATKError, match="passing final validation"):
        finish_round(repo, root, {"round_id": round_data["id"], "action": "complete", "reason": "trial"})
    with pytest.raises(ATKError, match="explicit user action"):
        finish_round(
            repo,
            root,
            {
                "round_id": round_data["id"],
                "action": "complete_with_override",
                "reason": "trial",
                "validation_missing_reason": "final budget not spent",
            },
        )
    retained = finish_round(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "action": "complete_with_override",
            "reason": "trial",
            "validation_missing_reason": "final budget not spent",
            "override_authorization": {
                "source": "user",
                "action": "retain_without_final_pass",
                "reason": "temporary local trial",
                "risk": "no final validation",
            },
        },
    )
    assert retained["validation_result"] is None
    assert retained["override"] is True
    assert read_json(root / "rounds" / round_data["id"] / "round.json")["status"] == "completed_with_override"


def test_interrupted_rollback_refuses_unknown_content_then_restores_start(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": draft["id"],
            "action": "keep",
            "validation_id": _passing_validation(root, round_data, sealed),
            "reason": "fixture",
        },
    )
    before = git(repo, "rev-parse", "HEAD")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(ATKError, match="rollback operation"):
        rollback_to(
            repo,
            root,
            {"round_id": round_data["id"], "target_commit": round_data["baseline_commit"], "reason": "fixture"},
        )
    operation = next(
        path
        for path in (root / "rounds" / round_data["id"] / "operations").glob("*.json")
        if read_json(path)["action"] == "rollback_to"
    )
    (repo / "prompt.txt").write_text("unknown")
    with pytest.raises(ATKError, match="unknown rollback content"):
        inspect_or_recover_operation(repo, root, {"round_id": round_data["id"], "operation_id": operation.stem})
    assert (repo / "prompt.txt").read_text() == "unknown"
    (repo / "prompt.txt").write_text("old")
    result = inspect_or_recover_operation(repo, root, {"round_id": round_data["id"], "operation_id": operation.stem})
    assert result["stage"] == "aborted"
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "diff", "--cached", "--name-only") == ""
    assert (repo / "prompt.txt").read_text() == "new"
    hook.unlink()
    restored = rollback_to(
        repo,
        root,
        {"round_id": round_data["id"], "target_commit": round_data["baseline_commit"], "reason": "retry"},
    )
    assert restored["withdrawn_candidate_ids"] == [draft["id"]]


def test_rollback_commit_recovery_keeps_one_restoration_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": draft["id"],
            "action": "keep",
            "validation_id": _passing_validation(root, round_data, sealed),
            "reason": "fixture",
        },
    )

    def interrupt_round(*_: object) -> None:
        raise RuntimeError("rollback status interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "_write_round", interrupt_round)
        with pytest.raises(RuntimeError, match="rollback status interrupted"):
            rollback_to(
                repo,
                root,
                {"round_id": round_data["id"], "target_commit": round_data["baseline_commit"], "reason": "fixture"},
            )
    committed = git(repo, "rev-parse", "HEAD")
    operation = next(
        path
        for path in (root / "rounds" / round_data["id"] / "operations").glob("*.json")
        if read_json(path)["action"] == "rollback_to"
    )
    result = inspect_or_recover_operation(repo, root, {"round_id": round_data["id"], "operation_id": operation.stem})
    assert result["stage"] == "complete"
    assert result["decision"]["after_commit"] == committed
    assert git(repo, "rev-parse", "HEAD") == committed
    assert read_json(root / "rounds" / round_data["id"] / "round.json")["active_candidate_ids"] == []


def test_literal_special_path_and_unknown_file_survive_candidate_gate(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    special = "literal [*] 中文.txt"
    plan["allowed_paths"].append(special)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    unknown = repo / "unknown.txt"
    unknown.write_text("do not touch")
    with pytest.raises(ATKError, match="unaccounted"):
        prepare_candidate(repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": [special]})
    assert unknown.read_text() == "do not touch"
    unknown.unlink()
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": [special]}
    )
    (repo / special).write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    assert (root / "rounds" / round_data["id"] / "candidates" / draft["id"] / "files" / special).read_text() == "new"
    decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": draft["id"],
            "action": "keep",
            "validation_id": _passing_validation(root, round_data, sealed),
            "reason": "fixture",
        },
    )
    assert git(repo, "show", f"HEAD:{special}") == "new"
    rollback_to(
        repo,
        root,
        {"round_id": round_data["id"], "target_commit": round_data["baseline_commit"], "reason": "fixture"},
    )
    assert not (repo / special).exists()


def test_runner_commit_blocks_evaluation_without_advancing_round(tmp_path: Path) -> None:
    script = (
        "import subprocess\nfrom pathlib import Path\n"
        "Path('prompt.txt').write_text('rogue')\n"
        "subprocess.run(['git', 'add', 'prompt.txt'], check=True)\n"
        "subprocess.run(['git', 'commit', '-m', 'rogue'], check=True, capture_output=True)\n"
        "print('ok')\n"
    )
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    with pytest.raises(ATKError, match="runner changed current checkpoint"):
        run_evaluation(
            root,
            {
                "dataset_id": dataset["id"],
                "case_ids": ["case"],
                "purpose": "evaluation",
                "revision_id": round_data["baseline_revision_id"],
                "round_id": round_data["id"],
            },
        )
    assert (
        read_json(root / "rounds" / round_data["id"] / "round.json")["current_commit"] == round_data["baseline_commit"]
    )
    assert git(repo, "rev-parse", "HEAD") != round_data["baseline_commit"]


def test_runner_untracked_output_blocks_pending_candidate_evidence(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    script = "from pathlib import Path\nPath('unexpected.txt').write_text('output')\nprint('ok')\n"
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    with pytest.raises(ATKError, match="runner changed candidate Git state"):
        run_evaluation(
            root,
            {
                "dataset_id": dataset["id"],
                "case_ids": ["case"],
                "purpose": "evaluation",
                "round_id": round_data["id"],
                "revision_id": sealed["revision_id"],
            },
        )
    assert (repo / "unexpected.txt").read_text() == "output"
    assert read_json(root / "rounds" / round_data["id"] / "round.json")["pending_candidate_id"] == draft["id"]


def test_staged_baseline_scope_and_sealed_content_are_hard_gates(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    (repo / "prompt.txt").write_text("staged")
    git(repo, "add", "prompt.txt")
    with pytest.raises(ATKError, match="staged changes"):
        freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    assert git(repo, "diff", "--cached", "--name-only") == "prompt.txt"
    git(repo, "restore", "--staged", "prompt.txt")
    git(repo, "restore", "prompt.txt")
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    (repo / "agent.py").write_text("print('unauthorized')\n")
    with pytest.raises(ATKError, match="differ from declared paths"):
        seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    git(repo, "restore", "agent.py")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    (repo / "prompt.txt").write_text("modified-after-seal")
    with pytest.raises(ATKError, match="changed after sealing"):
        decide_candidate(
            repo,
            root,
            {
                "round_id": round_data["id"],
                "candidate_id": draft["id"],
                "action": "keep",
                "validation_id": _passing_validation(root, round_data, sealed),
                "reason": "fixture",
            },
        )
    assert git(repo, "rev-parse", "HEAD") == round_data["baseline_commit"]
