from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_tune_kit.checkpoints import (
    create_round,
    decide_candidate,
    execution_revision,
    freeze_round,
    prepare_candidate,
    rollback_to,
    seal_candidate,
)
from agent_tune_kit.core import ATKError, digest, store_assessment, validate_evidence
from agent_tune_kit.execution import initialize_project, run_evaluation, store_dataset
from agent_tune_kit.governance import compare_and_gate, finish_round


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo).decode().strip()


def assessment_for(root: Path, batch: dict, spec: dict, judger: dict, expected: dict[str, str]) -> str:
    _, records, _ = validate_evidence(root, batch["id"])
    rows = []
    for record in records.values():
        actual = record["output"].strip()
        rows.append(
            {
                "record_id": record["id"],
                "dimension": "task_success",
                "validity": "valid",
                "validity_reason": "",
                "verdict": "pass" if actual == expected[record["case_id"]] else "fail",
                "score": None,
                "reason": "exact match",
                "evidence_refs": [{"batch_id": batch["id"], "evidence_id": record["id"]}],
                "judger_kind": "deterministic",
            }
        )
    path = store_assessment(root, {"batch_id": batch["id"], "evaluation_spec": spec, "judger": judger, "rows": rows})
    return path.parent.name


def test_unfrozen_run_requires_commit_and_detects_source_mutation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "ATK Test")
    source = repo / "agent.txt"
    source.write_text("original")
    git(repo, "add", "agent.txt")
    git(repo, "commit", "-qm", "initial")
    with pytest.raises(ATKError, match="revision_commit"), execution_revision(repo, repo / ".atk", {}):
        pass
    with (
        pytest.raises(ATKError, match="runner changed"),
        execution_revision(repo, repo / ".atk", {"revision_commit": git(repo, "rev-parse", "HEAD")}),
    ):
        source.write_text("mutated")


@pytest.mark.parametrize("final_action", ["complete", "rollback"])
def test_local_prompt_candidate_is_compared_and_committed(tmp_path: Path, final_action: str) -> None:
    repo = tmp_path / "agent"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "ATK Test")
    (repo / "prompt.txt").write_text("old")
    (repo / "agent.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "x=json.loads(Path(sys.argv[1]).read_text())\n"
        "print('hello' if x=='hello' and Path('prompt.txt').read_text()=='new' else 'safe' if x=='safe' else 'bad')\n"
    )
    git(repo, "add", "agent.py", "prompt.txt")
    git(repo, "commit", "-qm", "initial")
    initialize_project(
        repo,
        {
            "python": sys.executable,
            "command": [sys.executable, "agent.py", "{input_file}"],
            "components": [
                {"component_id": "prompt", "role": "prompt", "change_role": "variable", "source_path": "prompt.txt"}
            ],
            "allowed_paths": ["prompt.txt"],
            "protected_paths": ["agent.py"],
            "runtime_notes": "Fake Agent reads prompt.txt for every task. No external effects.\n",
        },
    )
    root = repo / ".atk"
    source = tmp_path / "cases.csv"
    with source.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "input", "expected", "usage"])
        writer.writeheader()
        writer.writerows(
            [
                {"id": "target", "input": "hello", "expected": "hello", "usage": "optimization"},
                {"id": "protect", "input": "safe", "expected": "safe", "usage": "protection"},
            ]
        )
    dataset = store_dataset(
        root,
        {"source": str(source), "mapping": {"id": "id", "input": "input", "expected": "expected", "usage": "usage"}},
    )
    round_data = create_round(repo, root, {"issue_ids": ["issue-prompt"]})
    spec = {
        "version": "v1",
        "boundary": "local fake Agent",
        "dimensions": ["task_success"],
        "dimension_rules": {
            "task_success": {"validity": "completed response", "attribution": "Agent", "applicability": "all Cases"}
        },
        "denominator_rule": "all valid planned Case slots",
        "primary_dimension": "task_success",
    }
    judger = {
        "version": "exact-v1",
        "readiness": "calibrated",
        "calibration_examples": [
            {"source_ref": "confirmed-positive", "expected_verdict": "pass", "actual_verdict": "pass"},
            {"source_ref": "confirmed-negative", "expected_verdict": "fail", "actual_verdict": "fail"},
        ],
    }
    plan = {
        "allowed_paths": ["prompt.txt"],
        "protected_paths": ["agent.py"],
        "issue_ids": ["issue-prompt"],
        "evaluation_spec_hash": digest(spec),
        "judger_hash": digest(judger),
        "runner_hash": digest(root / "adapters/runner.py"),
        "fixed_context_hash": digest([]),
        "case_ids": ["target", "protect"],
        "protection_case_ids": ["protect"],
        "target_case_ids_by_issue": {"issue-prompt": ["target"]},
        "repeatability": "deterministic",
        "final_repeats": 1,
        "budget": {"executions": 20, "candidates": 2},
        "replay_preparation": {"mode": "stateless", "reason": "fake Agent has no persistent cache"},
        "commit_authorized": True,
        "rollback_on_failure": "B0",
    }
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    baseline = run_evaluation(
        root,
        {
            "dataset_id": dataset["id"],
            "case_ids": ["target", "protect"],
            "repeats": 1,
            "purpose": "evaluation",
            "revision_id": round_data["baseline_revision_id"],
            "round_id": round_data["id"],
        },
    )
    expected = {"target": "hello", "protect": "safe"}
    baseline_assessment = assessment_for(root, baseline, spec, judger, expected)
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue-prompt", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    candidate_batch = run_evaluation(
        root,
        {
            "dataset_id": dataset["id"],
            "case_ids": ["target", "protect"],
            "repeats": 1,
            "purpose": "evaluation",
            "revision_id": sealed["revision_id"],
            "round_id": round_data["id"],
        },
    )
    candidate_assessment = assessment_for(root, candidate_batch, spec, judger, expected)
    with pytest.raises(ATKError, match="Revision"):
        compare_and_gate(
            root,
            {
                "round_id": round_data["id"],
                "mode": "incremental",
                "issue_id": "issue-prompt",
                "candidate_id": draft["id"],
                "left_assessment_id": candidate_assessment,
                "right_assessment_id": candidate_assessment,
                "left_commit": round_data["baseline_commit"],
            },
        )
    validation = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "incremental",
            "issue_id": "issue-prompt",
            "candidate_id": draft["id"],
            "left_assessment_id": baseline_assessment,
            "right_assessment_id": candidate_assessment,
            "left_commit": round_data["baseline_commit"],
        },
    )
    assert validation["result"] == "pass"
    decision = decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": draft["id"],
            "action": "keep",
            "validation_id": validation["id"],
            "reason": "target fixed; protection unchanged",
        },
    )
    assert decision["after_commit"] == git(repo, "rev-parse", "HEAD")
    assert git(repo, "show", "HEAD:prompt.txt") == "new"
    bad_draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue-prompt", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("bad2")
    bad_sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": bad_draft["id"]})
    bad_batch = run_evaluation(
        root,
        {
            "dataset_id": dataset["id"],
            "case_ids": ["target", "protect"],
            "repeats": 1,
            "purpose": "evaluation",
            "round_id": round_data["id"],
            "revision_id": bad_sealed["revision_id"],
        },
    )
    bad_assessment = assessment_for(root, bad_batch, spec, judger, expected)
    bad_validation = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "incremental",
            "issue_id": "issue-prompt",
            "candidate_id": bad_draft["id"],
            "left_assessment_id": candidate_assessment,
            "right_assessment_id": bad_assessment,
            "left_commit": decision["after_commit"],
        },
    )
    assert bad_validation["result"] == "regression"
    decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": bad_draft["id"],
            "action": "reject",
            "validation_id": bad_validation["id"],
            "reason": "target regressed",
        },
    )
    assert git(repo, "rev-parse", "HEAD") == decision["after_commit"]
    assert (repo / "prompt.txt").read_text() == "new"
    final_baseline = run_evaluation(
        root,
        {
            "phase": "final",
            "dataset_id": dataset["id"],
            "case_ids": ["target", "protect"],
            "repeats": 1,
            "purpose": "evaluation",
            "round_id": round_data["id"],
            "revision_id": round_data["baseline_revision_id"],
        },
    )
    assert (repo / "prompt.txt").read_text() == "new"
    final_baseline_assessment = assessment_for(root, final_baseline, spec, judger, expected)
    final_current = run_evaluation(
        root,
        {
            "phase": "final",
            "dataset_id": dataset["id"],
            "case_ids": ["target", "protect"],
            "repeats": 1,
            "purpose": "evaluation",
            "round_id": round_data["id"],
            "revision_id": sealed["revision_id"],
        },
    )
    with pytest.raises(ATKError, match="already run"):
        run_evaluation(
            root,
            {
                "phase": "final",
                "dataset_id": dataset["id"],
                "case_ids": ["target", "protect"],
                "repeats": 1,
                "purpose": "evaluation",
                "round_id": round_data["id"],
                "revision_id": sealed["revision_id"],
            },
        )
    final_current_assessment = assessment_for(root, final_current, spec, judger, expected)
    final_validation = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "final",
            "left_assessment_id": final_baseline_assessment,
            "right_assessment_id": final_current_assessment,
            "left_commit": round_data["baseline_commit"],
        },
    )
    assert final_validation["result"] == "pass"
    if final_action == "complete":
        finish_round(
            repo,
            root,
            {
                "round_id": round_data["id"],
                "action": "complete",
                "validation_id": final_validation["id"],
                "reason": "all frozen cases passed",
            },
        )
        assert json.loads((root / "rounds" / round_data["id"] / "round.json").read_text())["status"] == "completed"
    else:
        restored = rollback_to(
            repo,
            root,
            {
                "round_id": round_data["id"],
                "target_commit": round_data["baseline_commit"],
                "reason": "exercise accepted-chain recovery",
            },
        )
        assert restored["withdrawn_candidate_ids"] == [draft["id"]]
        assert (repo / "prompt.txt").read_text() == "old"
        assert git(repo, "rev-parse", "HEAD") != round_data["baseline_commit"]
        assert git(repo, "rev-parse", "HEAD^{tree}") == git(
            repo, "rev-parse", f"{round_data['baseline_commit']}^{{tree}}"
        )
