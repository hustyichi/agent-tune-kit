"""Whole-round checks for cumulative and final results."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_tune_kit.checkpoints import (
    create_round,
    decide_candidate,
    freeze_round,
    prepare_candidate,
    rollback_to,
    seal_candidate,
)
from agent_tune_kit.core import ATKError, digest, read_json, store_assessment, validate_evidence, write_json
from agent_tune_kit.evidence import record_source_contract
from agent_tune_kit.execution import run_evaluation, store_dataset
from agent_tune_kit.governance import compare_and_gate, finish_round, store_diagnosis
from tests.test_vnext_flow import assessment_for, git, record_local_issue
from tests.test_vnext_limits import JUDGER, SPEC, assess, project


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
    assert validation["outcome_counts"] == {
        "fixed": 3,
        "regressed": 0,
        "persistent_failure": 1,
        "stable_success": 6,
        "unchanged_mixed": 0,
        "unknown": 0,
    }
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


def test_two_independent_issues_build_one_cumulative_checkpoint_chain(tmp_path: Path) -> None:
    script = (
        "import json, sys\nfrom pathlib import Path\n"
        "task = json.loads(Path(sys.argv[1]).read_text())\n"
        "print('safe' if task == 'safe' else Path(f'rule-{task}.txt').read_text().strip())\n"
    )
    rows = [
        {"id": "a", "input": "a", "usage": "optimization", "source_group_id": "ga"},
        {"id": "b", "input": "b", "usage": "optimization", "source_group_id": "gb"},
        {"id": "protect", "input": "safe", "usage": "protection", "source_group_id": "gp"},
    ]
    repo, root, dataset, round_data, plan = project(
        tmp_path,
        script,
        rows,
        issue_ids=["a", "b"],
        extra_files={"rule-a.txt": "bad", "rule-b.txt": "bad", "contract.md": "a and b must return good.\n"},
    )
    paths = ["rule-a.txt", "rule-b.txt"]
    config = read_json(root / "project.json")
    config["allowed_paths"] = paths
    config["protected_paths"] = ["agent.py", "contract.md"]
    config["components"] = [
        {"component_id": name, "role": "prompt", "change_role": "variable", "source_path": name} for name in paths
    ]
    write_json(root / "project.json", config)
    plan.update(
        allowed_paths=paths,
        protected_paths=["agent.py", "contract.md"],
        target_case_ids_by_issue={"a": ["a"], "b": ["b"]},
        budget={"executions": 18, "probes": 0, "candidates": 2},
    )
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["a", "b", "protect"],
        "purpose": "evaluation",
        "round_id": round_data["id"],
    }
    expected = {"a": "good", "b": "good", "protect": "safe"}
    baseline = run_evaluation(root, {**request, "revision_id": round_data["baseline_revision_id"]})
    baseline_records = {record["case_id"]: record for record in validate_evidence(root, baseline["id"])[1].values()}
    assert {case_id: baseline_records[case_id]["output"].strip() for case_id in ("a", "b")} == {
        "a": "bad",
        "b": "bad",
    }
    issues = []
    for issue_id in ("a", "b"):
        path = f"rule-{issue_id}.txt"
        source = record_source_contract(
            repo,
            root,
            {
                "source_path": path,
                "start_line": 1,
                "end_line": 1,
                "component_id": path,
                "source_revision": git(repo, "rev-parse", "HEAD"),
                "artifact_identity": digest(repo / path),
                "round_id": round_data["id"],
            },
        )
        observed = {"batch_id": baseline["id"], "evidence_id": baseline_records[issue_id]["id"]}
        mechanism = {"batch_id": source["id"], "evidence_id": next(iter(validate_evidence(root, source["id"])[2]))}
        issues.append(
            {
                "id": issue_id,
                "symptom": f"Case {issue_id} returns bad",
                "hypothesis": f"{path} supplies the wrong value",
                "competing_explanations": ["the other rule file caused it"],
                "competing_explanations_addressed": "Agent selects one rule file by the Case input",
                "checks": [{"status": "completed", "expected": "good", "actual": "bad", "evidence_refs": [observed]}],
                "evidence_refs": [observed],
                "mechanism_evidence_refs": [mechanism],
                "intervention_validation_refs": [],
                "root_cause_status": "supported",
                "intervention_layer": "prompt",
                "responsible_component": path,
                "case_ids": [issue_id],
                "priority": "high",
                "disposition": "local_candidate",
                "resolution": "open",
                "next_action": f"correct {path}",
            }
        )
    assert {
        issue["id"] for issue in store_diagnosis(root, {"round_id": round_data["id"], "issues": issues})["issues"]
    } == {
        "a",
        "b",
    }
    parent_commit = round_data["baseline_commit"]
    parent_assessment = assess(root, baseline, expected)
    for issue_id in ("a", "b"):
        path = f"rule-{issue_id}.txt"
        draft = prepare_candidate(
            repo, root, {"round_id": round_data["id"], "primary_issue_id": issue_id, "paths": [path]}
        )
        assert draft["parent_commit"] == parent_commit
        (repo / path).write_text("good")
        sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
        batch = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
        current_assessment = assess(root, batch, expected)
        validation = compare_and_gate(
            root,
            {
                "round_id": round_data["id"],
                "mode": "incremental",
                "issue_id": issue_id,
                "candidate_id": draft["id"],
                "left_assessment_id": parent_assessment,
                "right_assessment_id": current_assessment,
                "left_commit": parent_commit,
            },
        )
        assert validation["result"] == "pass"
        assert validation["fixed_case_ids"] == [issue_id]
        decision = decide_candidate(
            repo,
            root,
            {
                "round_id": round_data["id"],
                "candidate_id": draft["id"],
                "action": "keep",
                "validation_id": validation["id"],
                "reason": "independent issue repaired",
            },
        )
        parent_commit = decision["after_commit"]
        parent_assessment = current_assessment
    final_left = run_evaluation(root, {**request, "phase": "final", "revision_id": round_data["baseline_revision_id"]})
    final_right = run_evaluation(
        root,
        {
            **request,
            "phase": "final",
            "revision_id": read_json(root / "rounds" / round_data["id"] / "round.json")["current_revision_id"],
        },
    )
    final = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "final",
            "left_assessment_id": assess(root, final_left, expected),
            "right_assessment_id": assess(root, final_right, expected),
            "left_commit": round_data["baseline_commit"],
        },
    )
    assert final["result"] == "pass"
    assert final["fixed_case_ids"] == ["a", "b"]
    finish_round(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "action": "complete",
            "validation_id": final["id"],
            "reason": "both issues fixed",
        },
    )


def test_rollback_withdraws_accepted_suffix_and_requires_fresh_candidate_evidence(tmp_path: Path) -> None:
    script = (
        "import json,sys\nfrom pathlib import Path\n"
        "case=json.loads(Path(sys.argv[1]).read_text())\n"
        "print('ok' if case in Path('prompt.txt').read_text().split(',') else 'bad')\n"
    )
    rows = [
        {"id": str(index), "input": str(index), "usage": "optimization", "source_group_id": f"g{index}"}
        for index in range(1, 4)
    ]
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows)
    plan["target_case_ids_by_issue"] = {"issue": ["1", "2", "3"]}
    plan["budget"] = {"executions": 24, "probes": 0, "candidates": 3}
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["1", "2", "3"],
        "purpose": "evaluation",
        "round_id": round_data["id"],
    }

    def assessed(revision_id: str) -> str:
        return assess(
            root,
            run_evaluation(root, {**request, "revision_id": revision_id}),
            {key: "ok" for key in request["case_ids"]},
        )

    parent_assessment = assessed(round_data["baseline_revision_id"])
    accepted = []
    for state in ("1", "1,2"):
        draft = prepare_candidate(
            repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
        )
        (repo / "prompt.txt").write_text(state)
        sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
        right = assessed(sealed["revision_id"])
        validation = compare_and_gate(
            root,
            {
                "round_id": round_data["id"],
                "mode": "incremental",
                "issue_id": "issue",
                "candidate_id": draft["id"],
                "left_assessment_id": parent_assessment,
                "right_assessment_id": right,
                "left_commit": draft["parent_commit"],
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
                "reason": "synthetic gain",
            },
        )
        accepted.append((draft, sealed, right, decision))
        parent_assessment = right

    first, second = accepted
    restored = rollback_to(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "target_commit": first[3]["after_commit"],
            "reason": "withdraw second gain",
        },
    )
    current = read_json(root / "rounds" / round_data["id"] / "round.json")
    assert restored["withdrawn_candidate_ids"] == [second[0]["id"]]
    assert current["active_candidate_ids"] == [first[0]["id"]]
    assert current["current_revision_id"] == first[1]["revision_id"]
    assert current["current_commit"] == restored["after_commit"] == git(repo, "rev-parse", "HEAD")
    assert (repo / "prompt.txt").read_text() == "1"

    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    assert draft["parent_commit"] == restored["after_commit"]
    assert draft["parent_revision_id"] == first[1]["revision_id"]
    (repo / "prompt.txt").write_text("1,3")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    right = assessed(sealed["revision_id"])
    comparison = {
        "round_id": round_data["id"],
        "mode": "incremental",
        "issue_id": "issue",
        "candidate_id": draft["id"],
        "right_assessment_id": right,
        "left_commit": draft["parent_commit"],
    }
    with pytest.raises(ATKError, match="assessment Revision does not match candidate"):
        compare_and_gate(root, {**comparison, "left_assessment_id": second[2]})
    validation = compare_and_gate(root, {**comparison, "left_assessment_id": first[2]})
    assert validation["result"] == "pass"
    assert validation["fixed_case_ids"] == ["3"]


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
    plan["repeatability_basis"] = "The fake Agent increments a counter and changes its answer across attempts."
    plan["final_repeats"] = 2
    plan["repeat_plan_basis"] = "Two paired attempts demonstrate this deterministic fixture's varying responses."
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


def test_new_judger_reassesses_prior_execution_without_rerunning_agent(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    script = "from pathlib import Path\nprint(Path('prompt.txt').read_text())\n"
    repo, root, dataset, first, plan = project(tmp_path, script, rows)
    plan["budget"] = {"executions": 4, "probes": 0, "candidates": 1}
    freeze_round(repo, root, {"round_id": first["id"], "plan": plan})
    request = {"dataset_id": dataset["id"], "case_ids": ["case"], "purpose": "evaluation", "round_id": first["id"]}
    baseline = run_evaluation(root, {**request, "revision_id": first["baseline_revision_id"]})
    draft = prepare_candidate(
        repo, root, {"round_id": first["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": first["id"], "candidate_id": draft["id"]})
    candidate = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
    old_baseline_assessment = assess(root, baseline, {"case": "new"})
    old_candidate_assessment = assess(root, candidate, {"case": "new"})
    incremental = compare_and_gate(
        root,
        {
            "round_id": first["id"],
            "mode": "incremental",
            "issue_id": "issue",
            "candidate_id": draft["id"],
            "left_assessment_id": old_baseline_assessment,
            "right_assessment_id": old_candidate_assessment,
            "left_commit": first["baseline_commit"],
        },
    )
    assert incremental["result"] == "pass"
    decide_candidate(
        repo,
        root,
        {
            "round_id": first["id"],
            "candidate_id": draft["id"],
            "action": "keep",
            "validation_id": incremental["id"],
            "reason": "first rule passes",
        },
    )
    final_left = run_evaluation(root, {**request, "phase": "final", "revision_id": first["baseline_revision_id"]})
    final_right = run_evaluation(root, {**request, "phase": "final", "revision_id": sealed["revision_id"]})
    final = compare_and_gate(
        root,
        {
            "round_id": first["id"],
            "mode": "final",
            "left_assessment_id": assess(root, final_left, {"case": "new"}),
            "right_assessment_id": assess(root, final_right, {"case": "new"}),
            "left_commit": first["baseline_commit"],
        },
    )
    finish_round(
        repo, root, {"round_id": first["id"], "action": "complete", "validation_id": final["id"], "reason": "pass"}
    )
    original_config = read_json(root / "project.json")
    write_json(root / "project.json", {**original_config, "command": [original_config["python"], "another.py"]})
    with pytest.raises(ATKError, match="cannot be reused"):
        create_round(
            repo,
            root,
            {
                "issue_ids": ["new-rule"],
                "previous_round_id": first["id"],
                "reuse_revision": True,
            },
        )
    write_json(root / "project.json", original_config)
    second = create_round(
        repo,
        root,
        {
            "issue_ids": ["new-rule"],
            "previous_round_id": first["id"],
            "reuse_revision": True,
        },
    )
    record_local_issue(root, second["id"], "new-rule", ["case"])
    assert second["baseline_revision_id"] == sealed["revision_id"]
    new_spec = {
        **SPEC,
        "version": "v2",
        "dimension_rules": {
            "task_success": {"validity": "completed response", "attribution": "Agent", "expected": "better"}
        },
    }
    new_judger = {
        "version": "v2",
        "readiness": "calibrated",
        "calibration_examples": [
            {"source_ref": "positive", "expected_verdict": "pass", "actual_verdict": "pass"},
            {"source_ref": "negative", "expected_verdict": "fail", "actual_verdict": "fail"},
        ],
    }
    next_plan = {
        **plan,
        "issue_ids": ["new-rule"],
        "evaluation_spec_hash": digest(new_spec),
        "judger_hash": digest(new_judger),
        "target_case_ids_by_issue": {"new-rule": ["case"]},
    }
    freeze_round(repo, root, {"round_id": second["id"], "plan": next_plan})
    before = len(list((root / "evidence").glob("*/manifest.json")))
    rejudged = assessment_for(root, candidate, new_spec, new_judger, {"case": "better"})
    assert len(list((root / "evidence").glob("*/manifest.json"))) == before
    rebuilt = prepare_candidate(
        repo,
        root,
        {
            "round_id": second["id"],
            "primary_issue_id": "new-rule",
            "paths": ["prompt.txt"],
        },
    )
    (repo / "prompt.txt").write_text("better")
    rebuilt = seal_candidate(repo, root, {"round_id": second["id"], "candidate_id": rebuilt["id"]})
    new_batch = run_evaluation(
        root,
        {
            "dataset_id": dataset["id"],
            "case_ids": ["case"],
            "purpose": "evaluation",
            "round_id": second["id"],
            "revision_id": rebuilt["revision_id"],
        },
    )
    validation = compare_and_gate(
        root,
        {
            "round_id": second["id"],
            "mode": "incremental",
            "issue_id": "new-rule",
            "candidate_id": rebuilt["id"],
            "left_assessment_id": rejudged,
            "right_assessment_id": assessment_for(root, new_batch, new_spec, new_judger, {"case": "better"}),
            "left_commit": second["baseline_commit"],
        },
    )
    assert validation["result"] == "pass"


def test_changed_case_input_needs_new_execution_on_both_sides(tmp_path: Path) -> None:
    script = (
        "import json,sys\nfrom pathlib import Path\n"
        "case=json.loads(Path(sys.argv[1]).read_text())\n"
        "print('ok' if case=='new-input' and Path('prompt.txt').read_text()=='new' else 'bad')\n"
    )
    rows = [{"id": "case", "input": "old-input", "usage": "optimization", "source_group_id": "g"}]
    repo, root, old_dataset, first, plan = project(tmp_path, script, rows)
    freeze_round(repo, root, {"round_id": first["id"], "plan": plan})
    changed_source = tmp_path / "changed-cases.csv"
    changed_source.write_text("id,input,usage,source_group_id\ncase,new-input,optimization,g\n")
    new_dataset = store_dataset(
        root,
        {
            "source": str(changed_source),
            "mapping": {"id": "id", "input": "input", "usage": "usage", "source_group_id": "source_group_id"},
        },
    )

    def assessed(dataset_id: str, round_id: str, revision_id: str) -> str:
        batch = run_evaluation(
            root,
            {
                "dataset_id": dataset_id,
                "case_ids": ["case"],
                "purpose": "evaluation",
                "round_id": round_id,
                "revision_id": revision_id,
            },
        )
        return assess(root, batch, {"case": "ok"})

    old_left = assessed(old_dataset["id"], first["id"], first["baseline_revision_id"])
    draft = prepare_candidate(
        repo, root, {"round_id": first["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": first["id"], "candidate_id": draft["id"]})
    new_right = assessed(new_dataset["id"], first["id"], sealed["revision_id"])
    request = {
        "round_id": first["id"],
        "mode": "incremental",
        "issue_id": "issue",
        "candidate_id": draft["id"],
        "left_assessment_id": old_left,
        "right_assessment_id": new_right,
        "left_commit": first["baseline_commit"],
    }
    mismatch = compare_and_gate(root, request)
    assert mismatch["result"] == "insufficient"
    assert mismatch["case_distributions"]["case"] == {
        "left": {"pass": 0, "fail": 0, "unknown": 1},
        "right": {"pass": 0, "fail": 0, "unknown": 1},
    }
    assert mismatch["outcome_counts"]["unknown"] == 1
    assert mismatch["coverage"] == {"complete_cases": 0, "planned_cases": 1, "planned_repeats_per_case": 1}
    decide_candidate(
        repo,
        root,
        {
            "round_id": first["id"],
            "candidate_id": draft["id"],
            "action": "reject",
            "validation_id": mismatch["id"],
            "reason": "Case input changed",
        },
    )
    finish_round(
        repo, root, {"round_id": first["id"], "action": "close_without_adoption", "reason": "rebuild comparison"}
    )

    second = create_round(repo, root, {"issue_ids": ["issue"], "previous_round_id": first["id"]})
    record_local_issue(root, second["id"], "issue", ["case"])
    freeze_round(repo, root, {"round_id": second["id"], "plan": plan})
    fresh_left = assessed(new_dataset["id"], second["id"], second["baseline_revision_id"])
    rebuilt = prepare_candidate(
        repo, root, {"round_id": second["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    rebuilt = seal_candidate(repo, root, {"round_id": second["id"], "candidate_id": rebuilt["id"]})
    fresh_right = assessed(new_dataset["id"], second["id"], rebuilt["revision_id"])
    validation = compare_and_gate(
        root,
        {
            **request,
            "round_id": second["id"],
            "candidate_id": rebuilt["id"],
            "left_assessment_id": fresh_left,
            "right_assessment_id": fresh_right,
            "left_commit": second["baseline_commit"],
        },
    )
    assert validation["result"] == "pass"
    assert validation["fixed_case_ids"] == ["case"]


@pytest.mark.parametrize("fault_layer", ["tool", "runtime"])
def test_direct_probe_separates_tool_failure_from_runtime_handoff(tmp_path: Path, fault_layer: str) -> None:
    tool_output = "error" if fault_layer == "tool" else "ok"
    tool_script = (
        "import sys\n"
        "if sys.argv[1]=='valid':\n"
        f"    print({tool_output!r})\n"
        f"    raise SystemExit({1 if fault_layer == 'tool' else 0})\n"
    )
    agent_script = (
        "import json,subprocess,sys\nfrom pathlib import Path\n"
        "task=json.loads(Path(sys.argv[1]).read_text())\n"
        "result=subprocess.run([sys.executable,'tool.py',task],capture_output=True,text=True)\n"
        + ("print('error')\n" if fault_layer == "runtime" else "print(result.stdout.strip())\n")
    )
    rows = [{"id": "case", "input": "valid", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, round_data, plan = project(
        tmp_path,
        agent_script,
        rows,
        extra_files={"tool.py": tool_script, "contract.md": "Valid requests return ok.\n"},
    )
    config_path = root / "project.json"
    config = read_json(config_path)
    config["components"].append(
        {
            "component_id": "tool",
            "role": "tool",
            "change_role": "fixed",
            "source_path": "tool.py",
        }
    )
    write_json(config_path, config)
    plan["fixed_context_hash"] = digest(
        [
            {
                "component_id": "tool",
                "role": "tool",
                "change_role": "fixed",
                "source_path": "tool.py",
                "actual_sha256": digest(repo / "tool.py"),
                "identity_status": "available",
            }
        ]
    )
    command = [sys.executable, "tool.py", "{input}"]
    plan["budget"]["probes"] = 1
    plan["probe_permissions"] = [
        {
            "id": "direct",
            "command": command,
            "command_hash": digest(command),
            "runner_hash": digest(root / "adapters" / "runner.py"),
            "case_ids": ["case"],
            "isolation_ref": "read-only local fixture",
            "timeout_seconds": 5,
            "max_calls": 1,
        }
    ]
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "revision_id": round_data["baseline_revision_id"],
        "round_id": round_data["id"],
    }
    agent_batch = run_evaluation(root, {**request, "purpose": "evaluation"})
    direct_batch = run_evaluation(
        root,
        {
            **request,
            "purpose": "diagnostic_probe",
            "probe_authorization_id": "direct",
        },
    )
    agent_ref = {
        "batch_id": agent_batch["id"],
        "evidence_id": next(iter(validate_evidence(root, agent_batch["id"])[2])),
    }
    direct_ref = {
        "batch_id": direct_batch["id"],
        "evidence_id": next(iter(validate_evidence(root, direct_batch["id"])[2])),
    }
    contract = record_source_contract(
        repo,
        root,
        {
            "source_path": "contract.md",
            "start_line": 1,
            "end_line": 1,
            "component_id": "tool",
            "source_revision": git(repo, "rev-parse", "HEAD"),
            "artifact_identity": digest(repo / "tool.py"),
            "round_id": round_data["id"],
        },
    )
    contract_ref = {"batch_id": contract["id"], "evidence_id": next(iter(validate_evidence(root, contract["id"])[2]))}
    mechanism_refs = [contract_ref, direct_ref]
    if fault_layer == "runtime":
        runtime_source = record_source_contract(
            repo,
            root,
            {
                "source_path": "agent.py",
                "start_line": 4,
                "end_line": 5,
                "component_id": "runtime",
                "source_revision": git(repo, "rev-parse", "HEAD"),
                "artifact_identity": digest(repo / "agent.py"),
                "round_id": round_data["id"],
            },
        )
        mechanism_refs.append(
            {
                "batch_id": runtime_source["id"],
                "evidence_id": next(iter(validate_evidence(root, runtime_source["id"])[2])),
            }
        )
    issue = {
        "id": fault_layer,
        "symptom": "Agent returned error for a valid request",
        "hypothesis": f"{fault_layer} changed the result",
        "competing_explanations": ["other layer failed"],
        "competing_explanations_addressed": "direct tool response compared with Agent response",
        "checks": [
            {
                "status": "completed",
                "expected": "ok",
                "actual": "error" if fault_layer == "tool" else "ok",
                "evidence_refs": [direct_ref],
            }
        ],
        "evidence_refs": [agent_ref],
        "mechanism_evidence_refs": mechanism_refs,
        "intervention_validation_refs": [],
        "root_cause_status": "supported",
        "intervention_layer": fault_layer,
        "responsible_component": fault_layer,
        "case_ids": ["case"],
        "priority": "high",
        "disposition": "external_handoff" if fault_layer == "tool" else "local_candidate",
        "resolution": "open",
        "next_action": "repair the responsible layer",
    }
    if fault_layer == "tool":
        issue["handoff"] = {"trigger_input": "valid", "expected": "ok", "actual": "error"}
    saved = store_diagnosis(root, {"round_id": round_data["id"], "issues": [issue]})
    assert saved["issues"][0]["intervention_layer"] == fault_layer


def test_external_fixed_artifact_drift_invalidates_candidate_gain(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    script = "from pathlib import Path\nprint('ok' if Path('prompt.txt').read_text()=='new' else 'bad')\n"
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows)
    external = tmp_path / "service-artifact.txt"
    external.write_text("v1")
    config_path = root / "project.json"
    config = read_json(config_path)
    config["components"].append(
        {
            "component_id": "service",
            "role": "tool",
            "change_role": "fixed",
            "source_path": str(external),
        }
    )
    write_json(config_path, config)
    plan["fixed_context_hash"] = digest(
        [
            {
                "component_id": "service",
                "role": "tool",
                "change_role": "fixed",
                "source_path": str(external),
                "actual_sha256": digest(external),
                "identity_status": "available",
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
    external.write_text("v2")
    candidate = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
    assert baseline["fixed_context_hash"] != candidate["fixed_context_hash"]
    with pytest.raises(ATKError, match="fixed component identity differs"):
        compare_and_gate(
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


def test_fixed_service_version_is_probed_before_and_after_batch(tmp_path: Path) -> None:
    version_file = tmp_path / "service-version.txt"
    drift_flag = tmp_path / "change-service-version"
    version_file.write_text("v1")
    script = (
        "from pathlib import Path\n"
        f"version=Path({str(version_file)!r})\n"
        f"drift_flag=Path({str(drift_flag)!r})\n"
        "if Path('prompt.txt').read_text()=='new' and drift_flag.exists() and version.read_text()=='v1': "
        "version.write_text('v2')\n"
        "print('ok' if Path('prompt.txt').read_text()=='new' else 'bad')\n"
    )
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, round_data, plan = project(
        tmp_path,
        script,
        rows,
        extra_files={"version.py": f"from pathlib import Path\nprint(Path({str(version_file)!r}).read_text())\n"},
    )
    command = [sys.executable, "version.py"]
    config_path = root / "project.json"
    config = read_json(config_path)
    config["components"].append(
        {
            "component_id": "service",
            "role": "tool",
            "change_role": "fixed",
            "version_command": command,
            "expected_version": "v1",
        }
    )
    write_json(config_path, config)
    plan["fixed_context_hash"] = digest(
        [
            {
                "component_id": "service",
                "role": "tool",
                "change_role": "fixed",
                "source_path": None,
                "actual_sha256": None,
                "identity_status": "verified",
                "expected_version": "v1",
                "actual_version": "v1",
                "post_run_actual_version": "v1",
                "identity_basis": {"method": "version_command", "command_hash": digest(command)},
            }
        ]
    )
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {"dataset_id": dataset["id"], "case_ids": ["case"], "purpose": "evaluation", "round_id": round_data["id"]}
    baseline = run_evaluation(root, {**request, "revision_id": round_data["baseline_revision_id"]})
    assert baseline["fixed_context_hash"] == plan["fixed_context_hash"]
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    stable = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
    baseline_assessment = assess(root, baseline, {"case": "ok"})
    assert stable["fixed_context_hash"] == plan["fixed_context_hash"]
    assert (
        compare_and_gate(
            root,
            {
                "round_id": round_data["id"],
                "mode": "incremental",
                "issue_id": "issue",
                "candidate_id": draft["id"],
                "left_assessment_id": baseline_assessment,
                "right_assessment_id": assess(root, stable, {"case": "ok"}),
                "left_commit": round_data["baseline_commit"],
            },
        )["result"]
        == "pass"
    )
    version_file.write_text("v2")
    changed = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
    assert changed["actual_components"][1]["actual_version"] == "v2"
    assert changed["actual_components"][1]["identity_issue"] == "expected_version_mismatch"
    with pytest.raises(ATKError, match="fixed component identity differs"):
        compare_and_gate(
            root,
            {
                "round_id": round_data["id"],
                "mode": "incremental",
                "issue_id": "issue",
                "candidate_id": draft["id"],
                "left_assessment_id": baseline_assessment,
                "right_assessment_id": assess(root, changed, {"case": "ok"}),
                "left_commit": round_data["baseline_commit"],
            },
        )
    version_file.write_text("v1")
    drift_flag.write_text("yes")
    drifted = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
    assert drifted["status"] == "partial"
    assert drifted["actual_components"][1]["actual_version"] == "v1"
    assert drifted["actual_components"][1]["post_run_actual_version"] == "v2"
    assert drifted["post_run_component_drift"][0]["component_id"] == "service"
    version_file.write_text("v1 invalid")
    unknown = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
    assert unknown["actual_components"][1]["identity_status"] == "unknown"
    assert unknown["actual_components"][1]["identity_issue"] == "before_invalid_output"
    config["components"][1]["version_command"] = "python version.py"
    write_json(config_path, config)
    with pytest.raises(ATKError, match="invalid version command"):
        run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})


def test_fixed_file_changed_during_batch_cannot_support_candidate_gain(tmp_path: Path) -> None:
    external = tmp_path / "service-artifact.txt"
    external.write_text("v1")
    script = (
        "from pathlib import Path\n"
        f"service=Path({str(external)!r})\n"
        "if Path('prompt.txt').read_text()=='old': service.write_text('v2')\n"
        "print('ok' if Path('prompt.txt').read_text()=='new' else 'bad')\n"
    )
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows)
    config_path = root / "project.json"
    config = read_json(config_path)
    component = {
        "component_id": "service",
        "role": "tool",
        "change_role": "fixed",
        "source_path": str(external),
    }
    config["components"].append(component)
    write_json(config_path, config)
    plan["fixed_context_hash"] = digest(
        [{**component, "actual_sha256": digest(external), "identity_status": "available"}]
    )
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {"dataset_id": dataset["id"], "case_ids": ["case"], "purpose": "evaluation", "round_id": round_data["id"]}
    baseline = run_evaluation(root, {**request, "revision_id": round_data["baseline_revision_id"]})
    assert baseline["status"] == "partial"
    assert baseline["post_run_component_drift"] == [
        {
            "component_id": "service",
            "source_path": str(external),
            "before_sha256": digest(b"v1"),
            "after_sha256": digest(external),
        }
    ]

    external.write_text("v1")
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    candidate = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
    assert candidate["status"] == "sealed"
    assert candidate["fixed_context_hash"] == baseline["fixed_context_hash"]
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


def test_service_failure_remains_valid_failure_while_skill_dimension_is_unknown(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, round_data, plan = project(tmp_path, "print('service_error')\n", rows)
    spec = {
        **SPEC,
        "boundary": "service and Skill observed separately",
        "dimensions": ["service_reliability", "skill_behavior"],
        "dimension_rules": {
            "service_reliability": {"validity": "service response observed", "attribution": "service"},
            "skill_behavior": {"validity": "service returned usable data", "attribution": "Skill"},
        },
    }
    plan["evaluation_spec_hash"] = digest(spec)
    plan["primary_dimension"] = "service_reliability"
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

    def boundary_assessment(batch: dict) -> str:
        _, records, _ = validate_evidence(root, batch["id"])
        record_id = next(iter(records))
        ref = [{"batch_id": batch["id"], "evidence_id": record_id}]
        judgment = []
        for dimension, validity, verdict in [
            ("service_reliability", "valid", "fail"),
            ("skill_behavior", "unknown", "unknown"),
        ]:
            judgment.append(
                {
                    "record_id": record_id,
                    "dimension": dimension,
                    "validity": validity,
                    "validity_reason": "service blocked observation" if validity == "unknown" else "",
                    "verdict": verdict,
                    "score": None,
                    "reason": "observed service error",
                    "evidence_refs": ref,
                    "judger_kind": "deterministic",
                }
            )
        return store_assessment(
            root,
            {
                "batch_id": batch["id"],
                "evaluation_spec": spec,
                "judger": JUDGER,
                "rows": judgment,
            },
        ).parent.name

    left, right = boundary_assessment(baseline), boundary_assessment(candidate)
    common = {
        "round_id": round_data["id"],
        "mode": "incremental",
        "issue_id": "issue",
        "candidate_id": draft["id"],
        "left_assessment_id": left,
        "right_assessment_id": right,
        "left_commit": round_data["baseline_commit"],
    }
    assert compare_and_gate(root, {**common, "dimension": "service_reliability"})["result"] == "insufficient"
    with pytest.raises(ATKError, match="frozen primary"):
        compare_and_gate(root, {**common, "dimension": "skill_behavior"})


@pytest.mark.parametrize("fault_layer", ["skill", "agent_code"])
def test_wrong_skill_rule_is_distinct_from_agent_ignoring_correct_rule(tmp_path: Path, fault_layer: str) -> None:
    skill_text = "red" if fault_layer == "skill" else "blue"
    prelude = "import json,sys\nfrom pathlib import Path\ncase=json.loads(Path(sys.argv[1]).read_text())\n"
    corrected_script = prelude + "print('safe' if case=='safe' else Path('business_skill.md').read_text().strip())\n"
    script = corrected_script if fault_layer == "skill" else prelude + "print('safe' if case=='safe' else 'red')\n"
    rows = [
        {"id": "case", "input": "valid", "usage": "optimization", "source_group_id": "g1"},
        {"id": "protect", "input": "safe", "usage": "protection", "source_group_id": "g2"},
    ]
    repo, root, dataset, round_data, plan = project(
        tmp_path,
        script,
        rows,
        issue_ids=[fault_layer],
        extra_files={"business_skill.md": skill_text, "contract.md": "Valid requests must return blue.\n"},
    )
    changed_path = "business_skill.md" if fault_layer == "skill" else "agent.py"
    config_path = root / "project.json"
    config = read_json(config_path)
    config["allowed_paths"] = [changed_path]
    config["protected_paths"] = ["contract.md"]
    config["components"] = [
        {
            "component_id": fault_layer,
            "role": fault_layer,
            "change_role": "variable",
            "source_path": changed_path,
        }
    ]
    write_json(config_path, config)
    plan["allowed_paths"] = [changed_path]
    plan["protected_paths"] = ["contract.md"]
    plan["protection_case_ids"] = ["protect"]
    plan["target_case_ids_by_issue"] = {fault_layer: ["case"]}
    plan["budget"] = {"executions": 8, "probes": 0, "candidates": 1}
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case", "protect"],
        "purpose": "evaluation",
        "round_id": round_data["id"],
    }
    batch = run_evaluation(
        root,
        {**request, "revision_id": round_data["baseline_revision_id"]},
    )
    baseline_assessment = assess(root, batch, {"case": "blue", "protect": "safe"})
    record = next(record for record in validate_evidence(root, batch["id"])[1].values() if record["case_id"] == "case")
    assert record["output"].strip() == "red"
    observed = {"batch_id": batch["id"], "evidence_id": record["id"]}

    def source_ref(path: str, component: str, line: int) -> dict:
        manifest = record_source_contract(
            repo,
            root,
            {
                "source_path": path,
                "start_line": line,
                "end_line": line,
                "component_id": component,
                "source_revision": git(repo, "rev-parse", "HEAD"),
                "artifact_identity": digest(repo / path),
                "round_id": round_data["id"],
            },
        )
        return {"batch_id": manifest["id"], "evidence_id": next(iter(validate_evidence(root, manifest["id"])[2]))}

    contract_ref = source_ref("contract.md", "business-contract", 1)
    skill_ref = source_ref("business_skill.md", "business-skill", 1)
    code_ref = source_ref("agent.py", "agent-code", 4)
    issue = {
        "id": fault_layer,
        "symptom": "valid request returned red instead of blue",
        "hypothesis": "wrong Skill rule" if fault_layer == "skill" else "Agent ignored correct Skill rule",
        "competing_explanations": ["the other layer caused the mismatch"],
        "competing_explanations_addressed": "contract, Skill text, Agent code, and output are compared",
        "checks": [{"status": "completed", "expected": "blue", "actual": "red", "evidence_refs": [observed]}],
        "evidence_refs": [observed],
        "mechanism_evidence_refs": [contract_ref, skill_ref, code_ref],
        "intervention_validation_refs": [],
        "root_cause_status": "supported",
        "intervention_layer": fault_layer,
        "responsible_component": fault_layer,
        "case_ids": ["case"],
        "priority": "high",
        "disposition": "local_candidate",
        "resolution": "open",
        "next_action": "repair the responsible layer",
    }
    saved = store_diagnosis(root, {"round_id": round_data["id"], "issues": [issue]})
    assert saved["issues"][0]["intervention_layer"] == fault_layer

    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": fault_layer, "paths": [changed_path]}
    )
    (repo / changed_path).write_text("blue" if fault_layer == "skill" else corrected_script)
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    candidate = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
    candidate_assessment = assess(root, candidate, {"case": "blue", "protect": "safe"})
    incremental = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "incremental",
            "issue_id": fault_layer,
            "candidate_id": draft["id"],
            "left_assessment_id": baseline_assessment,
            "right_assessment_id": candidate_assessment,
            "left_commit": round_data["baseline_commit"],
        },
    )
    assert incremental["result"] == "pass"
    assert incremental["fixed_case_ids"] == ["case"]
    assert incremental["regressed_case_ids"] == []
    decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": draft["id"],
            "action": "keep",
            "validation_id": incremental["id"],
            "reason": "known-cause synthetic repair",
        },
    )
    final_left = run_evaluation(root, {**request, "phase": "final", "revision_id": round_data["baseline_revision_id"]})
    final_right = run_evaluation(root, {**request, "phase": "final", "revision_id": sealed["revision_id"]})
    final = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "final",
            "left_assessment_id": assess(root, final_left, {"case": "blue", "protect": "safe"}),
            "right_assessment_id": assess(root, final_right, {"case": "blue", "protect": "safe"}),
            "left_commit": round_data["baseline_commit"],
        },
    )
    assert final["result"] == "pass"
    finish_round(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "action": "complete",
            "validation_id": final["id"],
            "reason": "target fixed and protection retained",
        },
    )
