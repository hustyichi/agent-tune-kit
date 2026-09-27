from __future__ import annotations

from pathlib import Path

import pytest

from agent_tune_kit.checkpoints import cancel_draft, decide_candidate, freeze_round, prepare_candidate, seal_candidate
from agent_tune_kit.core import ATKError, digest, read_json, store_assessment, validate_evidence, write_json
from agent_tune_kit.execution import run_evaluation
from agent_tune_kit.governance import compare_and_gate, store_diagnosis
from tests.test_vnext_limits import JUDGER, project


def _rows(batch: dict, root: Path, spec: dict, primary: str, secondary: str) -> str:
    _, records, _ = validate_evidence(root, batch["id"])
    rows = []
    for record in records.values():
        for dimension, verdict in (("task_success", primary), ("evidence_trust", secondary)):
            rows.append(
                {
                    "record_id": record["id"],
                    "dimension": dimension,
                    "validity": "unknown" if verdict == "unknown" else "valid",
                    "validity_reason": "unverified" if verdict == "unknown" else "",
                    "verdict": verdict,
                    "score": None,
                    "reason": "fixture",
                    "evidence_refs": [{"batch_id": batch["id"], "evidence_id": record["id"]}],
                    "judger_kind": "deterministic",
                }
            )
    return store_assessment(
        root, {"batch_id": batch["id"], "evaluation_spec": spec, "judger": JUDGER, "rows": rows}
    ).parent.name


def _sealed_candidate(repo: Path, root: Path, round_id: str) -> dict:
    draft = prepare_candidate(repo, root, {"round_id": round_id, "primary_issue_id": "issue", "paths": ["prompt.txt"]})
    (repo / "prompt.txt").write_text("new")
    return seal_candidate(repo, root, {"round_id": round_id, "candidate_id": draft["id"]})


@pytest.mark.parametrize("secondary,expected", [("unknown", "insufficient"), ("fail", "regression")])
def test_frozen_dimension_gate_cannot_ignore_unknown_or_regression(
    tmp_path: Path, secondary: str, expected: str
) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, rnd, plan = project(tmp_path, "print('ok')", rows)
    spec = {
        "version": "v1",
        "boundary": "fake",
        "dimensions": ["task_success", "evidence_trust"],
        "dimension_rules": {"task_success": {}, "evidence_trust": {}},
        "denominator_rule": "slots",
    }
    plan["evaluation_spec_hash"] = digest(spec)
    freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    base_request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "evaluation",
        "round_id": rnd["id"],
        "revision_id": rnd["baseline_revision_id"],
    }
    baseline = run_evaluation(root, base_request)
    left = _rows(baseline, root, spec, "fail", "pass")
    candidate = _sealed_candidate(repo, root, rnd["id"])
    changed = run_evaluation(root, {**base_request, "revision_id": candidate["revision_id"]})
    right = _rows(changed, root, spec, "pass", secondary)
    request = {
        "round_id": rnd["id"],
        "mode": "incremental",
        "candidate_id": candidate["id"],
        "issue_id": "issue",
        "left_assessment_id": left,
        "right_assessment_id": right,
        "left_commit": rnd["baseline_commit"],
    }
    with pytest.raises(ATKError, match="frozen primary"):
        compare_and_gate(root, {**request, "dimension": "evidence_trust"})
    assert compare_and_gate(root, request)["result"] == expected


def test_candidate_issue_and_target_are_bound(tmp_path: Path) -> None:
    rows = [
        {"id": "a", "input": "a", "usage": "optimization", "source_group_id": "ga"},
        {"id": "b", "input": "b", "usage": "optimization", "source_group_id": "gb"},
    ]
    repo, root, dataset, rnd, plan = project(tmp_path, "print('ok')", rows, ["issue", "other"])
    store_diagnosis(
        root,
        {
            "round_id": rnd["id"],
            "issues": [
                {
                    "id": "other",
                    "symptom": "b fails",
                    "hypothesis": "prompt",
                    "competing_explanations": [],
                    "checks": [],
                    "evidence_refs": [],
                    "mechanism_evidence_refs": [],
                    "intervention_validation_refs": [],
                    "root_cause_status": "hypothesis",
                    "intervention_layer": "prompt",
                    "responsible_component": "prompt",
                    "case_ids": ["b"],
                    "priority": "high",
                    "disposition": "local_candidate",
                    "resolution": "open",
                    "next_action": "test prompt",
                }
            ],
        },
    )
    plan["target_case_ids_by_issue"]["other"] = ["b"]
    freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["a", "b"],
        "purpose": "evaluation",
        "round_id": rnd["id"],
        "revision_id": rnd["baseline_revision_id"],
    }
    base = run_evaluation(root, request)
    from tests.test_vnext_limits import assess

    left = assess(root, base, {"a": "wrong", "b": "ok"})
    candidate = _sealed_candidate(repo, root, rnd["id"])
    changed = run_evaluation(root, {**request, "revision_id": candidate["revision_id"]})
    right = assess(root, changed, {"a": "ok", "b": "ok"})
    with pytest.raises(ATKError, match="validation Issue differs"):
        compare_and_gate(
            root,
            {
                "round_id": rnd["id"],
                "mode": "incremental",
                "candidate_id": candidate["id"],
                "issue_id": "other",
                "left_assessment_id": left,
                "right_assessment_id": right,
                "left_commit": rnd["baseline_commit"],
            },
        )


def test_timeout_is_frozen_and_draft_can_be_cancelled_safely(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, rnd, plan = project(tmp_path, "print('ok')", rows)
    plan["timeout_seconds"] = 1
    freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "evaluation",
        "round_id": rnd["id"],
        "revision_id": rnd["baseline_revision_id"],
    }
    with pytest.raises(ATKError, match="timeout differs"):
        run_evaluation(root, {**request, "timeout_seconds": 99})
    assert not (root / "rounds" / rnd["id"] / "budget-usage.json").exists()
    draft = prepare_candidate(repo, root, {"round_id": rnd["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]})
    (repo / "prompt.txt").write_text("unknown")
    with pytest.raises(ATKError, match="restore or inspect"):
        cancel_draft(repo, root, {"round_id": rnd["id"], "candidate_id": draft["id"], "reason": "no change"})
    (repo / "prompt.txt").write_text("old")
    cancellation = cancel_draft(repo, root, {"round_id": rnd["id"], "candidate_id": draft["id"], "reason": "no change"})
    assert (
        cancel_draft(repo, root, {"round_id": rnd["id"], "candidate_id": draft["id"], "reason": "no change"})
        == cancellation
    )
    assert read_json(root / "rounds" / rnd["id"] / "round.json")["pending_candidate_id"] is None


def test_interrupted_final_batch_resumes_only_pending_slots(tmp_path: Path) -> None:
    marker = tmp_path / "once"
    script = (
        "import json,sys,time\nfrom pathlib import Path\n"
        "task=json.loads(Path(sys.argv[1]).read_text())\n"
        f"marker=Path({str(marker)!r})\n"
        "if task=='slow' and not marker.exists(): marker.write_text('started'); time.sleep(3)\n"
        "print('ok')\n"
    )
    rows = [
        {"id": name, "input": name, "usage": "optimization", "source_group_id": name}
        for name in ("quick", "slow", "later")
    ]
    repo, root, dataset, rnd, plan = project(tmp_path, script, rows)
    plan["budget"]["executions"] = 9
    freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    candidate = _sealed_candidate(repo, root, rnd["id"])
    validation_id = "validation-fixture"
    write_json(
        root / "rounds" / rnd["id"] / "validations" / validation_id / "validation.json",
        {
            "result": "pass",
            "left_commit": candidate["parent_commit"],
            "right_revision_id": candidate["revision_id"],
            "candidate_id": candidate["id"],
            "issue_id": candidate["primary_issue_id"],
            "target_case_ids": candidate["target_case_ids"],
        },
    )
    decide_candidate(
        repo,
        root,
        {
            "round_id": rnd["id"],
            "candidate_id": candidate["id"],
            "action": "keep",
            "validation_id": validation_id,
            "reason": "fixture",
        },
    )
    request = {
        "dataset_id": dataset["id"],
        "case_ids": [row["id"] for row in rows],
        "purpose": "evaluation",
        "phase": "final",
        "round_id": rnd["id"],
        "revision_id": candidate["revision_id"],
        "batch_timeout_seconds": 1,
    }
    interrupted = run_evaluation(root, request)
    assert interrupted["status"] == "interrupted"
    assert interrupted["unknown_record_ids"] and interrupted["not_started_record_ids"]
    follow = {**request, "continue_batch_id": interrupted["id"], "batch_timeout_seconds": 5}
    with pytest.raises(ATKError, match="explicit rerun authorization"):
        run_evaluation(root, follow)
    authorization = {
        "source": "user",
        "batch_id": interrupted["id"],
        "record_ids": interrupted["unknown_record_ids"],
        "reason": "process was terminated",
        "risk": "may repeat a side effect",
    }
    resumed = run_evaluation(root, {**follow, "unknown_execution_authorization": authorization})
    assert resumed["status"] == "sealed"
    assert resumed["supersedes_batch_id"] == interrupted["id"]
    assert resumed["record_count"] == len(rows)
    assert len(resumed["completed_record_ids"]) == len(rows)
    assert read_json(root / "rounds" / rnd["id"] / "budget-usage.json")["executions"] == 5
