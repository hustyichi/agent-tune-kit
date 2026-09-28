from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent_tune_kit.checkpoints import (
    create_round,
    freeze_round,
    inspect_or_recover_operation,
    prepare_candidate,
    seal_candidate,
    transition_round,
)
from agent_tune_kit.cli import internal_main
from agent_tune_kit.core import ATKError, digest, read_json, store_assessment, validate_evidence, write_json
from agent_tune_kit.evidence import import_evidence
from agent_tune_kit.execution import initialize_project, run_evaluation, store_dataset
from agent_tune_kit.governance import finish_round, store_diagnosis
from tests.test_vnext_boundaries import _handoff_issue
from tests.test_vnext_flow import git
from tests.test_vnext_limits import JUDGER, SPEC, project


def test_analysis_without_git_imports_assesses_diagnoses_and_closes(tmp_path: Path) -> None:
    repo = tmp_path / "analysis"
    repo.mkdir()
    root = repo / ".atk"
    initialize_project(repo, {"analysis_only": True})
    assert not (root / "adapters").exists()
    source = tmp_path / "trace.json"
    source.write_text(json.dumps([{"id": "trace", "input": "task", "output": "tool error"}]))
    batch = import_evidence(
        root,
        {
            "source": str(source),
            "source_kind": "langfuse",
            "source_namespace": "test",
            "adapter_profile": "langfuse_trace_bundle",
            "mapping_version": "v1",
        },
    )
    _, records, _ = validate_evidence(root, batch["id"])
    record_id = next(iter(records))
    ref = {"batch_id": batch["id"], "evidence_id": "trace:trace"}
    assessment = store_assessment(
        root,
        {
            "batch_id": batch["id"],
            "evaluation_spec": SPEC,
            "judger": JUDGER,
            "rows": [
                {
                    "record_id": record_id,
                    "dimension": "task_success",
                    "validity": "unknown",
                    "validity_reason": "no success criterion",
                    "verdict": "unknown",
                    "score": None,
                    "reason": "trace only",
                    "evidence_refs": [ref],
                    "judger_kind": "codex",
                }
            ],
        },
    )
    rnd = create_round(
        repo, root, {"batch_ids": [batch["id"]], "assessment_ids": [assessment.parent.name], "issue_ids": ["tool"]}
    )
    assert rnd["baseline_commit"] is None and rnd["current_revision_id"] is None
    issue = {**_handoff_issue(), "evidence_refs": [ref]}
    assert store_diagnosis(root, {"round_id": rnd["id"], "issues": [issue]})["issues"][0]["id"] == "tool"
    with pytest.raises(ATKError, match="runtime"):
        run_evaluation(root, {})
    with pytest.raises(ATKError, match="not ready"):
        prepare_candidate(repo, root, {"round_id": rnd["id"]})
    request = {"round_id": rnd["id"], "action": "close_without_adoption", "reason": "analysis complete"}
    decision = finish_round(repo, root, request)
    assert decision["after_commit"] is None
    assert finish_round(repo, root, request)["id"] == decision["id"]
    assert not (repo / ".git").exists()
    assert read_json(root / "rounds" / rnd["id"] / "round.json")["status"] == "closed_without_adoption"


def test_analysis_can_bind_clean_b0_without_losing_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "analysis"
    repo.mkdir()
    root = repo / ".atk"
    initialize_project(repo, {"analysis_only": True, "redact_keys": ["private_field"]})
    rnd = create_round(repo, root, {"issue_ids": ["tool"]})
    stored = store_diagnosis(root, {"round_id": rnd["id"], "issues": [_handoff_issue()]})
    with pytest.raises(ATKError, match="runtime"):
        freeze_round(repo, root, {"round_id": rnd["id"], "plan": {}})
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "prompt.txt").write_text("old")
    git(repo, "add", "prompt.txt")
    git(repo, "commit", "-qm", "baseline")
    initialize_project(
        repo,
        {
            "configure_runtime": True,
            "python": sys.executable,
            "command": [sys.executable, "-c", "print('ok')"],
            "components": [],
            "allowed_paths": ["prompt.txt"],
            "protected_paths": [],
            "external_effects": [],
            "runtime_notes": "local process",
        },
    )
    assert read_json(root / "project.json")["redact_keys"] == ["private_field"]
    cases = tmp_path / "cases.csv"
    cases.write_text("id,input,usage,source_group_id\ncase,case,optimization,g\n")
    dataset = store_dataset(
        root,
        {
            "source": str(cases),
            "mapping": {"id": "id", "input": "input", "usage": "usage", "source_group_id": "source_group_id"},
        },
    )
    plan = {
        "dataset_id": dataset["id"],
        "allowed_paths": ["prompt.txt"],
        "protected_paths": [],
        "issue_ids": ["tool"],
        "evaluation_spec_hash": digest(SPEC),
        "judger_hash": digest(JUDGER),
        "runner_hash": digest(root / "adapters/runner.py"),
        "fixed_context_hash": digest([]),
        "case_ids": ["case"],
        "protection_case_ids": [],
        "repeatability": "deterministic",
        "repeatability_basis": "fixed output",
        "final_repeats": 1,
        "budget": {"executions": 2, "candidates": 1, "probes": 0},
        "replay_preparation": {"mode": "stateless", "reason": "no cache"},
        "commit_authorized": True,
        "rollback_on_failure": "B0",
    }
    (repo / "prompt.txt").write_text("dirty")
    with pytest.raises(ATKError, match="changed"):
        freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    (repo / "prompt.txt").write_text("old")
    import agent_tune_kit.checkpoints as checkpoints

    original = checkpoints._write_round
    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "_write_round", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError):
            freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    assert checkpoints._write_round is original
    baseline = read_json(root / "rounds" / rnd["id"] / "plan.json")["baseline"]
    frozen = freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    assert frozen["baseline_revision_id"] == baseline["baseline_revision_id"]
    assert frozen["baseline_commit"] == git(repo, "rev-parse", "HEAD")
    assert read_json(root / "rounds" / rnd["id"] / "issues/tool/revision-1.json") == stored["issues"][0]


def test_git_analysis_round_closes_before_freeze(tmp_path: Path) -> None:
    repo, root, _, rnd, _ = project(
        tmp_path, "print('ok')", [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    )
    before = git(repo, "rev-parse", "HEAD")
    finish_round(repo, root, {"round_id": rnd["id"], "action": "close_without_adoption", "reason": "no change needed"})
    assert git(repo, "rev-parse", "HEAD") == before


@pytest.mark.parametrize("sealed", [False, True])
def test_pause_preserves_candidate_and_resume_checks_content(tmp_path: Path, sealed: bool) -> None:
    repo, root, dataset, rnd, plan = project(
        tmp_path, "print('ok')", [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    )
    freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    candidate = prepare_candidate(
        repo, root, {"round_id": rnd["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("candidate")
    if sealed:
        seal_candidate(repo, root, {"round_id": rnd["id"], "candidate_id": candidate["id"]})
    paused = transition_round(repo, root, {"round_id": rnd["id"], "action": "pause", "reason": "review"})
    assert paused["status"] == "paused" and paused["pending_candidate_id"] == candidate["id"]
    assert create_round(repo, root, {"analysis_only": True})["baseline_commit"] is None
    with pytest.raises(ATKError, match="optimizing"):
        seal_candidate(repo, root, {"round_id": rnd["id"], "candidate_id": candidate["id"]})
    with pytest.raises(ATKError, match="paused"):
        run_evaluation(
            root,
            {
                "round_id": rnd["id"],
                "dataset_id": dataset["id"],
                "case_ids": ["case"],
                "revision_id": rnd["baseline_revision_id"],
                "purpose": "evaluation",
            },
        )
    (repo / "prompt.txt").write_text("unexpected")
    with pytest.raises(ATKError, match="changed"):
        transition_round(repo, root, {"round_id": rnd["id"], "action": "resume", "reason": "continue"})
    (repo / "prompt.txt").write_text("candidate")
    resumed = transition_round(repo, root, {"round_id": rnd["id"], "action": "resume", "reason": "continue"})
    assert resumed["status"] == "optimizing"
    assert resumed["transitions"][-1]["action"] == "resume"


def test_resume_requires_recovered_operation_and_unchanged_git(tmp_path: Path) -> None:
    repo, root, _, rnd, plan = project(
        tmp_path, "print('ok')", [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    )
    freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    op = root / "rounds" / rnd["id"] / "operations/op.json"
    write_json(op, {"id": "op", "stage": "prepared"})
    paused = transition_round(repo, root, {"round_id": rnd["id"], "action": "pause", "reason": "interrupted"})
    assert paused["pause"]["blocker"]["code"] == "GIT_OPERATION_INTERRUPTED"
    with pytest.raises(ATKError, match="recover operation"):
        transition_round(repo, root, {"round_id": rnd["id"], "action": "resume", "reason": "continue"})
    write_json(op, {"id": "op", "stage": "aborted"})
    git(repo, "switch", "-qc", "other")
    with pytest.raises(ATKError, match="branch"):
        transition_round(repo, root, {"round_id": rnd["id"], "action": "resume", "reason": "continue"})
    git(repo, "switch", "-q", rnd["branch"])
    assert (
        transition_round(repo, root, {"round_id": rnd["id"], "action": "resume", "reason": "continue"})["status"]
        == "ready"
    )


def test_transition_cli_and_atomic_pause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "analysis"
    repo.mkdir()
    root = repo / ".atk"
    initialize_project(repo, {"analysis_only": True})
    rnd = create_round(repo, root, {})
    import agent_tune_kit.checkpoints as checkpoints

    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "_write_round", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError):
            transition_round(repo, root, {"round_id": rnd["id"], "action": "pause", "reason": "review"})
    assert read_json(root / "rounds" / rnd["id"] / "round.json")["status"] == "analysis_only"
    req, out = tmp_path / "request.json", tmp_path / "response.json"
    write_json(req, {"project_path": str(repo), "round_id": rnd["id"], "action": "pause", "reason": "review"})
    assert internal_main(["transition_round", "--request", str(req), "--output", str(out)]) == 0
    assert read_json(out)["artifact_refs"][0]["status"] == "paused"
    assert (
        transition_round(repo, root, {"round_id": rnd["id"], "action": "resume", "reason": "continue"})["status"]
        == "analysis_only"
    )


def test_external_fix_can_follow_closed_analysis_round(tmp_path: Path) -> None:
    repo, root, _, initial, plan = project(
        tmp_path,
        "print('ok')",
        [
            {"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"},
            {"id": "protect", "input": "safe", "usage": "protection", "source_group_id": "p"},
        ],
        ["tool"],
    )
    finish_round(
        repo, root, {"round_id": initial["id"], "action": "close_without_adoption", "reason": "import instead"}
    )
    analysis = create_round(
        repo,
        root,
        {
            "analysis_only": True,
            "issue_ids": ["tool"],
            "analysis_plan": {"protection_case_ids": ["protect"]},
        },
    )
    store_diagnosis(root, {"round_id": analysis["id"], "issues": [_handoff_issue()]})
    finish_round(repo, root, {"round_id": analysis["id"], "action": "close_without_adoption", "reason": "tool owner"})
    with pytest.raises(ATKError, match="cannot be reused"):
        create_round(repo, root, {"previous_round_id": analysis["id"], "reuse_revision": True})
    followup = create_round(
        repo,
        root,
        {
            "previous_round_id": analysis["id"],
            "issue_ids": ["tool"],
            "external_fix_identity": "fixed-tool-v2",
        },
    )
    plan.update(allowed_paths=[], commit_authorized=False, protection_case_ids=[])
    with pytest.raises(ATKError, match="retain affected and protection"):
        freeze_round(repo, root, {"round_id": followup["id"], "plan": plan})
    plan["protection_case_ids"] = ["protect"]
    assert freeze_round(repo, root, {"round_id": followup["id"], "plan": plan})["status"] == "ready"


def test_paused_round_allows_switch_but_resume_checks_active_round_and_runtime(tmp_path: Path) -> None:
    repo, root, _, first, plan = project(
        tmp_path, "print('ok')", [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    )
    freeze_round(repo, root, {"round_id": first["id"], "plan": plan})
    transition_round(repo, root, {"round_id": first["id"], "action": "pause", "reason": "switch task"})
    second = create_round(repo, root, {"issue_ids": ["issue"]})
    freeze_round(repo, root, {"round_id": second["id"], "plan": plan})
    resume = {"round_id": first["id"], "action": "resume", "reason": "return"}
    with pytest.raises(ATKError, match="other active"):
        transition_round(repo, root, resume)
    with pytest.raises(ATKError, match="other active"):
        inspect_or_recover_operation(repo, root, {"round_id": first["id"], "operation_id": "pending"})
    finish_round(repo, root, {"round_id": second["id"], "action": "close_without_adoption", "reason": "done"})
    config = read_json(root / "project.json")
    write_json(root / "project.json", {**config, "command": ["changed"]})
    with pytest.raises(ATKError, match="frozen runtime changed"):
        transition_round(repo, root, resume)
    write_json(root / "project.json", config)
    runner = root / "adapters/runner.py"
    source = runner.read_text()
    runner.write_text(source + "\n# changed\n")
    with pytest.raises(ATKError, match="frozen runtime changed"):
        transition_round(repo, root, resume)
    runner.write_text(source)
    assert transition_round(repo, root, resume)["status"] == "ready"
