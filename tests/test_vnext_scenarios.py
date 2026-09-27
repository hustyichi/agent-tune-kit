"""Whole-round checks for cumulative and final results."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_tune_kit.checkpoints import decide_candidate, freeze_round, prepare_candidate, rollback_to, seal_candidate
from agent_tune_kit.core import ATKError, digest, read_json, write_json
from agent_tune_kit.execution import run_evaluation
from agent_tune_kit.governance import compare_and_gate, finish_round
from tests.test_vnext_flow import git
from tests.test_vnext_limits import assess, project


def test_ten_case_cumulative_gain_counts_only_new_fixes(tmp_path: Path) -> None:
    script = (
        "import json,sys\nfrom pathlib import Path\n"
        "case=json.loads(Path(sys.argv[1]).read_text())\n"
        "state=Path('prompt.txt').read_text()\n"
        "enabled='1,2,3,4,5,6' if state=='old' else state\n"
        "print('ok' if case in enabled.split(',') else 'bad')\n"
    )
    rows = [
        {
            "id": str(index),
            "input": str(index),
            "usage": "protection" if index in {2, 3} else "optimization",
            "source_group_id": f"g{index}",
        }
        for index in range(1, 11)
    ]
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows)
    round_file = root / "rounds" / round_data["id"] / "round.json"
    plan["protection_case_ids"] = ["2", "3"]
    plan["target_case_ids_by_issue"] = {"issue": ["7", "8", "9", "10"]}
    plan["budget"] = {"executions": 60, "probes": 0, "candidates": 4}
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    expected = {str(index): "ok" for index in range(1, 11)}
    base_request = {
        "dataset_id": dataset["id"],
        "case_ids": ["2", "3", "7", "8", "9", "10"],
        "purpose": "evaluation",
        "round_id": round_data["id"],
    }

    def evaluated(revision_id: str, *, final: bool = False) -> str:
        batch = run_evaluation(
            root,
            {
                **base_request,
                "case_ids": [str(index) for index in range(1, 11)] if final else base_request["case_ids"],
                "phase": "final" if final else "incremental",
                "revision_id": revision_id,
            },
        )
        return assess(root, batch, expected)

    parent_assessment = evaluated(round_data["baseline_revision_id"])
    parent_commit = round_data["baseline_commit"]
    kept = []
    states = [
        ("1,2,3,4,5,6,7,8", "pass", ["7", "8"]),
        ("1,3,4,5,6,7,8,9", "regression", ["9"]),
        ("1,2,3,4,5,6,7,8,9", "pass", ["9"]),
        ("1,2,4,5,6,7,8,9,10", "regression", ["10"]),
    ]
    for state, result, fixed in states:
        draft = prepare_candidate(
            repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
        )
        assert draft["parent_commit"] == parent_commit
        (repo / "prompt.txt").write_text(state)
        sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
        candidate_assessment = evaluated(sealed["revision_id"])
        validation = compare_and_gate(
            root,
            {
                "round_id": round_data["id"],
                "mode": "incremental",
                "issue_id": "issue",
                "candidate_id": draft["id"],
                "left_assessment_id": parent_assessment,
                "right_assessment_id": candidate_assessment,
                "left_commit": parent_commit,
            },
        )
        assert validation["result"] == result
        assert validation["fixed_case_ids"] == fixed
        decision = decide_candidate(
            repo,
            root,
            {
                "round_id": round_data["id"],
                "candidate_id": draft["id"],
                "action": "keep" if result == "pass" else "reject",
                "validation_id": validation["id"],
                "reason": "synthetic cumulative check",
            },
        )
        if result == "pass":
            kept.append(draft["id"])
            parent_commit = decision["after_commit"]
            parent_assessment = candidate_assessment
        else:
            assert git(repo, "rev-parse", "HEAD") == parent_commit
    assert len(kept) == 2
    final_left = evaluated(round_data["baseline_revision_id"], final=True)
    current = read_json(round_file)
    final_right = evaluated(current["current_revision_id"], final=True)
    validation = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "final",
            "left_assessment_id": final_left,
            "right_assessment_id": final_right,
            "left_commit": round_data["baseline_commit"],
        },
    )
    assert validation["result"] == "pass"
    assert validation["fixed_case_ids"] == ["7", "8", "9"]
    assert sum(score["right"] for score in validation["case_scores"].values()) == 9
    finish_round(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "action": "complete",
            "validation_id": validation["id"],
            "reason": "9 of 10; three net fixes",
        },
    )


def test_incremental_pass_cannot_finish_when_final_replay_fails(tmp_path: Path) -> None:
    marker = tmp_path / "new-attempts"
    script = (
        "import json,sys\nfrom pathlib import Path\n"
        "case=json.loads(Path(sys.argv[1]).read_text())\n"
        f"marker=Path({str(marker)!r})\n"
        "if case=='protect': print('ok')\n"
        "elif Path('prompt.txt').read_text()=='new':\n"
        "    count=int(marker.read_text()) if marker.exists() else 0\n"
        "    marker.write_text(str(count+1))\n"
        "    print('ok' if count==0 else 'bad')\n"
        "else: print('bad')\n"
    )
    rows = [
        {"id": "target", "input": "target", "usage": "optimization", "source_group_id": "g1"},
        {"id": "protect", "input": "protect", "usage": "protection", "source_group_id": "g2"},
    ]
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows)
    plan["repeatability"] = "unknown"
    plan["final_repeats"] = 2
    plan["budget"] = {"executions": 16, "probes": 0, "candidates": 2}
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["target", "protect"],
        "purpose": "evaluation",
        "round_id": round_data["id"],
    }
    baseline = run_evaluation(root, {**request, "revision_id": round_data["baseline_revision_id"]})
    left = assess(root, baseline, {"target": "ok", "protect": "ok"})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    candidate = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
    right = assess(root, candidate, {"target": "ok", "protect": "ok"})
    incremental = compare_and_gate(
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
    assert incremental["result"] == "pass"
    decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": draft["id"],
            "action": "keep",
            "validation_id": incremental["id"],
            "reason": "incremental improvement",
        },
    )
    final_left = run_evaluation(
        root,
        {
            **request,
            "phase": "final",
            "repeats": 2,
            "revision_id": round_data["baseline_revision_id"],
        },
    )
    final_right = run_evaluation(
        root,
        {
            **request,
            "phase": "final",
            "repeats": 2,
            "revision_id": sealed["revision_id"],
        },
    )
    final = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "final",
            "left_assessment_id": assess(root, final_left, {"target": "ok", "protect": "ok"}),
            "right_assessment_id": assess(root, final_right, {"target": "ok", "protect": "ok"}),
            "left_commit": round_data["baseline_commit"],
        },
    )
    assert final["result"] == "no_effect"
    with pytest.raises(ATKError, match="passing final validation"):
        finish_round(
            repo,
            root,
            {
                "round_id": round_data["id"],
                "action": "complete",
                "validation_id": final["id"],
                "reason": "incremental pass",
            },
        )
    rollback = rollback_to(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "target_commit": round_data["baseline_commit"],
            "reason": "frozen rule requires B0 after final failure",
        },
    )
    assert rollback["withdrawn_candidate_ids"] == [draft["id"]]
    assert git(repo, "show", "HEAD:prompt.txt") == "old"
    rebuilt = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    assert rebuilt["parent_commit"] == rollback["after_commit"]
    assert (repo / "prompt.txt").read_text() == "old"
    (repo / "prompt.txt").write_text("new-again")
    rebuilt = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": rebuilt["id"]})
    assert rebuilt["revision_id"] != sealed["revision_id"]
    rebuilt_batch = run_evaluation(root, {**request, "revision_id": rebuilt["revision_id"]})
    rebuilt_validation = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "incremental",
            "issue_id": "issue",
            "candidate_id": rebuilt["id"],
            "left_assessment_id": left,
            "right_assessment_id": assess(root, rebuilt_batch, {"target": "ok", "protect": "ok"}),
            "left_commit": rollback["after_commit"],
        },
    )
    assert rebuilt_validation["result"] == "no_effect"
    decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": rebuilt["id"],
            "action": "reject",
            "validation_id": rebuilt_validation["id"],
            "reason": "rebuilt content lacks effect",
        },
    )
    finish_round(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "action": "close_without_adoption",
            "reason": "final result did not reproduce",
        },
    )


def test_unknown_fixed_component_and_config_drift_block_comparison(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    script = "from pathlib import Path\nprint('ok' if Path('prompt.txt').read_text()=='new' else 'bad')\n"
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows)
    project_path = root / "project.json"
    config = read_json(project_path)
    config["components"].append({"component_id": "service", "role": "tool", "change_role": "fixed"})
    write_json(project_path, config)
    plan["fixed_context_hash"] = digest(
        [
            {
                "component_id": "service",
                "role": "tool",
                "change_role": "fixed",
                "source_path": None,
                "actual_sha256": None,
                "identity_status": "unknown",
            }
        ]
    )
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "evaluation",
        "round_id": round_data["id"],
    }
    baseline = run_evaluation(root, {**request, "revision_id": round_data["baseline_revision_id"]})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    candidate = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
    validation = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "incremental",
            "issue_id": "issue",
            "candidate_id": draft["id"],
            "left_assessment_id": assess(root, baseline, {"case": "ok"}),
            "right_assessment_id": assess(root, candidate, {"case": "ok"}),
            "left_commit": round_data["baseline_commit"],
        },
    )
    assert validation["result"] == "insufficient"
    decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": draft["id"],
            "action": "reject",
            "validation_id": validation["id"],
            "reason": "fixed identity unknown",
        },
    )
    config["command"] = [config["python"], "another-agent.py"]
    write_json(project_path, config)
    with pytest.raises(ATKError, match="configuration changed"):
        run_evaluation(root, {**request, "revision_id": round_data["baseline_revision_id"]})
