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
from agent_tune_kit.core import ATKError, digest, read_json, validate_evidence, write_json
from agent_tune_kit.evidence import record_source_contract
from agent_tune_kit.execution import run_evaluation
from agent_tune_kit.governance import compare_and_gate, finish_round, store_diagnosis
from tests.test_vnext_flow import assessment_for, git
from tests.test_vnext_limits import SPEC, assess, project


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
