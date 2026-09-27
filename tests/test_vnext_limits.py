from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

from agent_tune_kit.checkpoints import create_round, freeze_round, prepare_candidate, seal_candidate
from agent_tune_kit.core import ATKError, digest, read_assessment, store_assessment, validate_evidence
from agent_tune_kit.evidence import import_evidence
from agent_tune_kit.execution import initialize_project, run_evaluation, store_dataset
from agent_tune_kit.governance import (
    compare_and_gate,
    finish_round,
    knowledge_applicability,
    store_diagnosis,
    store_knowledge,
)
from tests.test_vnext_flow import git

SPEC = {
    "version": "v1",
    "boundary": "local fake Agent",
    "dimensions": ["task_success"],
    "dimension_rules": {"task_success": {"validity": "completed response", "attribution": "Agent"}},
    "denominator_rule": "planned Case slots",
}
JUDGER = {
    "version": "v1",
    "readiness": "calibrated",
    "calibration_examples": [
        {"source_ref": "positive", "expected_verdict": "pass", "actual_verdict": "pass"},
        {"source_ref": "negative", "expected_verdict": "fail", "actual_verdict": "fail"},
    ],
}


def project(
    tmp_path: Path,
    script: str,
    rows: list[dict],
    issue_ids: list[str] | None = None,
    extra_files: dict[str, str] | None = None,
    infrastructure_exit_codes: list[int] | None = None,
    external_effects: list[dict] | None = None,
    metric_sources: dict | None = None,
) -> tuple[Path, Path, dict, dict, dict]:
    repo = tmp_path / "agent"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "ATK Test")
    (repo / "agent.py").write_text(script)
    (repo / "prompt.txt").write_text("old")
    for name, body in (extra_files or {}).items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    git(repo, "add", "agent.py", "prompt.txt", *(extra_files or {}))
    git(repo, "commit", "-qm", "initial")
    command = [sys.executable, "agent.py", "{input_file}", "{output_dir}"]
    initialize_project(
        repo,
        {
            "python": sys.executable,
            "command": command,
            "components": [
                {"component_id": "prompt", "role": "prompt", "change_role": "variable", "source_path": "prompt.txt"}
            ],
            "allowed_paths": ["prompt.txt"],
            "protected_paths": ["agent.py"],
            "runtime_notes": "Local fake Agent with no external effects.\n",
            "infrastructure_exit_codes": infrastructure_exit_codes or [],
            "external_effects": external_effects or [],
            "metric_sources": metric_sources or {},
        },
    )
    root = repo / ".atk"
    source = tmp_path / "cases.csv"
    with source.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "input", "usage", "source_group_id"])
        writer.writeheader()
        writer.writerows(rows)
    dataset = store_dataset(
        root,
        {
            "source": str(source),
            "mapping": {"id": "id", "input": "input", "usage": "usage", "source_group_id": "source_group_id"},
        },
    )
    issue_ids = issue_ids or ["issue"]
    round_data = create_round(repo, root, {"issue_ids": issue_ids})
    if "issue" in issue_ids:
        store_diagnosis(
            root,
            {
                "round_id": round_data["id"],
                "issues": [
                    {
                        "id": "issue",
                        "symptom": "target Case fails",
                        "hypothesis": "prompt is insufficient",
                        "competing_explanations": [],
                        "checks": [],
                        "evidence_refs": [],
                        "mechanism_evidence_refs": [],
                        "intervention_validation_refs": [],
                        "root_cause_status": "hypothesis",
                        "intervention_layer": "prompt",
                        "responsible_component": "prompt",
                        "case_ids": [row["id"] for row in rows],
                        "priority": "high",
                        "disposition": "local_candidate",
                        "resolution": "open",
                        "next_action": "evaluate a candidate",
                    }
                ],
            },
        )
    plan = {
        "allowed_paths": ["prompt.txt"],
        "protected_paths": ["agent.py"],
        "issue_ids": issue_ids,
        "evaluation_spec_hash": digest(SPEC),
        "judger_hash": digest(JUDGER),
        "runner_hash": digest(root / "adapters/runner.py"),
        "fixed_context_hash": digest([]),
        "case_ids": [row["id"] for row in rows],
        "protection_case_ids": [row["id"] for row in rows if row["usage"] == "protection"],
        "target_case_ids_by_issue": {"issue": [rows[0]["id"]]},
        "repeatability": "deterministic",
        "repeatability_basis": "Fake Agent branches only on fixed Case input and repository files.",
        "final_repeats": 1,
        "budget": {"executions": 12, "probes": 0, "candidates": 1},
        "replay_preparation": {"mode": "stateless", "reason": "fake Agent has no persistent cache"},
        "commit_authorized": True,
        "rollback_on_failure": "B0",
    }
    return repo, root, dataset, round_data, plan


def assess(root: Path, batch: dict, expected: dict[str, str]) -> str:
    _, records, _ = validate_evidence(root, batch["id"])
    rows = []
    for record in records.values():
        completed = record["execution"]["status"] == "completed"
        rows.append(
            {
                "record_id": record["id"],
                "dimension": "task_success",
                "validity": "valid" if completed else "unknown",
                "validity_reason": "" if completed else "infrastructure timeout",
                "verdict": "pass"
                if completed and record["output"].strip() == expected[record["case_id"]]
                else "fail"
                if completed
                else "unknown",
                "score": None,
                "reason": "exact match" if completed else "not completed",
                "evidence_refs": [{"batch_id": batch["id"], "evidence_id": record["id"]}],
                "judger_kind": "deterministic",
            }
        )
    return store_assessment(
        root, {"batch_id": batch["id"], "evaluation_spec": SPEC, "judger": JUDGER, "rows": rows}
    ).parent.name


def test_invalid_revision_does_not_consume_execution_budget(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    plan["budget"]["executions"] = 3
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "evaluation",
        "revision_id": round_data["baseline_revision_id"],
        "round_id": round_data["id"],
    }
    (repo / "prompt.txt").write_text("unexpected edit")
    with pytest.raises(ATKError, match="dirty"):
        run_evaluation(root, request)
    usage_path = root / "rounds" / round_data["id"] / "budget-usage.json"
    assert not usage_path.exists()
    assert not list((root / "evidence").glob("batch-*"))

    (repo / "prompt.txt").write_text("old")
    batch = run_evaluation(root, request)
    assert batch["status"] == "sealed"
    assert json.loads(usage_path.read_text())["executions"] == 1


def test_freeze_records_repeatability_and_short_repeat_basis(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    plan.pop("repeatability_basis")
    with pytest.raises(ATKError, match="repeatability_basis"):
        freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    plan["repeatability"] = "unknown"
    plan["repeatability_basis"] = "Real model behavior has not been calibrated."
    plan["final_repeats"] = 2
    with pytest.raises(ATKError, match="short stochastic repeat plan"):
        freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    plan["repeat_plan_basis"] = "Two paired attempts fit the local evaluation budget; no significance claim."
    assert freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})["status"] == "ready"


def test_adapter_declared_infrastructure_exit_is_not_an_agent_failure(tmp_path: Path) -> None:
    marker = tmp_path / "authenticated"
    script = (
        "import json,sys\nfrom pathlib import Path\n"
        "task=json.loads(Path(sys.argv[1]).read_text())\n"
        f"marker=Path({str(marker)!r})\n"
        "if task=='auth' and not marker.exists():\n"
        "    marker.write_text('1'); sys.exit(75)\n"
        "if task=='agent': sys.exit(3)\n"
        "print('ok')\n"
    )
    rows = [
        {"id": "auth", "input": "auth", "usage": "optimization", "source_group_id": "g1"},
        {"id": "agent", "input": "agent", "usage": "protection", "source_group_id": "g2"},
    ]
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows, infrastructure_exit_codes=[75])
    plan["max_retries_per_slot"] = 1
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["auth", "agent"],
        "purpose": "evaluation",
        "revision_id": round_data["baseline_revision_id"],
        "round_id": round_data["id"],
    }
    batch = run_evaluation(root, request)
    _, records, _ = validate_evidence(root, batch["id"])
    statuses = {record["case_id"]: record["execution"]["status"] for record in records.values()}
    assert statuses == {"auth": "infra_error", "agent": "agent_error"}
    execution_ids = {record["case_id"]: record["execution"]["id"] for record in records.values()}
    with pytest.raises(ATKError, match="infrastructure failure"):
        run_evaluation(
            root, {**request, "retry_batch_id": batch["id"], "retry_execution_ids": [execution_ids["agent"]]}
        )
    retry = run_evaluation(
        root, {**request, "retry_batch_id": batch["id"], "retry_execution_ids": [execution_ids["auth"]]}
    )
    _, retried, _ = validate_evidence(root, retry["id"])
    assert {record["execution"]["status"] for record in retried.values() if record["execution"]["retry_of"]} == {
        "completed"
    }


def test_unknown_runner_execution_status_cannot_seal_a_batch(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    runner = root / "adapters/runner.py"
    source = runner.read_text()
    assert '"status": status' in source
    runner.write_text(source.replace('"status": status', '"status": "infrastructure_error"'))
    plan["runner_hash"] = digest(runner)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    batch = run_evaluation(
        root,
        {
            "dataset_id": dataset["id"],
            "case_ids": ["case"],
            "purpose": "evaluation",
            "revision_id": round_data["baseline_revision_id"],
            "round_id": round_data["id"],
        },
    )
    _, records, _ = validate_evidence(root, batch["id"])
    assert batch["status"] == "partial"
    assert batch["invalid_execution_status_ids"] == list(records)


def test_infra_error_cannot_be_counted_as_a_case_fix(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    script = (
        "import sys\nfrom pathlib import Path\nif Path('prompt.txt').read_text() == 'old': sys.exit(75)\nprint('ok')\n"
    )
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows, infrastructure_exit_codes=[75])
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "evaluation",
        "revision_id": round_data["baseline_revision_id"],
        "round_id": round_data["id"],
    }
    baseline = run_evaluation(root, request)
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    candidate = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})

    def score_as_valid(batch: dict, verdict: str) -> str:
        _, records, _ = validate_evidence(root, batch["id"])
        record = next(iter(records.values()))
        return store_assessment(
            root,
            {
                "batch_id": batch["id"],
                "evaluation_spec": SPEC,
                "judger": JUDGER,
                "rows": [
                    {
                        "record_id": record["id"],
                        "dimension": "task_success",
                        "validity": "valid",
                        "validity_reason": "",
                        "verdict": verdict,
                        "score": None,
                        "reason": "deliberately optimistic scorer",
                        "evidence_refs": [{"batch_id": batch["id"], "evidence_id": record["id"]}],
                        "judger_kind": "deterministic",
                    }
                ],
            },
        ).parent.name

    result = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "incremental",
            "issue_id": "issue",
            "candidate_id": draft["id"],
            "left_assessment_id": score_as_valid(baseline, "fail"),
            "right_assessment_id": score_as_valid(candidate, "pass"),
            "left_commit": round_data["baseline_commit"],
        },
    )
    assert result["result"] == "insufficient"
    assert result["outcome_counts"]["unknown"] == 1


def test_retry_keeps_all_attempts_and_budget_blocks_extra_run(tmp_path: Path) -> None:
    marker = tmp_path / "timeout-once"
    script = (
        "import json,sys,time\nfrom pathlib import Path\n"
        "task=json.loads(Path(sys.argv[1]).read_text())\n"
        f"marker=Path({str(marker)!r})\n"
        "if task=='target' and Path('prompt.txt').read_text()=='old' and not marker.exists():\n"
        "    marker.write_text('1'); time.sleep(1)\n"
        "print('ok' if Path('prompt.txt').read_text()=='new' and task=='target' else "
        "'safe' if task=='protect' else 'bad')\n"
    )
    rows = [
        {"id": "target", "input": "target", "usage": "optimization", "source_group_id": "g1"},
        {"id": "protect", "input": "protect", "usage": "protection", "source_group_id": "g2"},
    ]
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows)
    plan["budget"]["executions"] = 13
    plan["timeout_seconds"] = 0.2
    plan["max_retries_per_slot"] = 1
    plan["incremental_repeats"] = 2
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    base_request = {
        "dataset_id": dataset["id"],
        "case_ids": ["target", "protect"],
        "repeats": 2,
        "purpose": "evaluation",
        "revision_id": round_data["baseline_revision_id"],
        "round_id": round_data["id"],
        "timeout_seconds": 0.2,
    }
    base = run_evaluation(root, base_request)
    assert base["record_count"] == 4
    _, records, _ = validate_evidence(root, base["id"])
    timed_out = next(record for record in records.values() if record["execution"]["status"] == "timeout")
    retry = run_evaluation(
        root,
        {**base_request, "retry_batch_id": base["id"], "retry_execution_ids": [timed_out["execution"]["id"]]},
    )
    assert retry["record_count"] == 5 and retry["supersedes_batch_id"] == base["id"]
    _, retried_records, _ = validate_evidence(root, retry["id"])
    assert sum(record["execution"]["retry_of"] is not None for record in retried_records.values()) == 1
    retried_execution_id = next(
        record["execution"]["id"] for record in retried_records.values() if record["execution"]["retry_of"]
    )
    with pytest.raises(ATKError, match="infrastructure failure"):
        run_evaluation(
            root,
            {**base_request, "retry_batch_id": retry["id"], "retry_execution_ids": [retried_execution_id]},
        )
    left = assess(root, retry, {"target": "ok", "protect": "safe"})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    candidate = run_evaluation(root, {**base_request, "revision_id": sealed["revision_id"]})
    right = assess(root, candidate, {"target": "ok", "protect": "safe"})
    result = compare_and_gate(
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
    assert result["result"] == "pass"
    assert result["case_distributions"]["target"] == {
        "left": {"pass": 0, "fail": 2, "unknown": 0},
        "right": {"pass": 2, "fail": 0, "unknown": 0},
    }
    assert result["outcome_counts"] == {
        "fixed": 1,
        "regressed": 0,
        "persistent_failure": 0,
        "stable_success": 1,
        "unchanged_mixed": 0,
        "unknown": 0,
    }
    assert result["coverage"] == {"complete_cases": 2, "planned_cases": 2, "planned_repeats_per_case": 2}
    assert result["metrics"]["cost"] == {"left_total": None, "right_total": None}
    assert result["metrics"]["duration_seconds"]["left_total"] is not None
    assert json.loads((root / "rounds" / round_data["id"] / "budget-usage.json").read_text())["executions"] == 9
    with pytest.raises(ATKError, match="budget"):
        run_evaluation(root, {**base_request, "revision_id": sealed["revision_id"], "case_ids": ["target"]})


def test_probe_permission_and_isolated_budget(tmp_path: Path) -> None:
    script = "import sys\nprint('ok')\n"
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows)
    plan["budget"] = {"executions": 2, "probes": 1}
    plan["probe_permissions"] = [
        {
            "id": "check-1",
            "command_hash": digest(json.loads((root / "project.json").read_text())["command"]),
            "runner_hash": digest(root / "adapters/runner.py"),
            "case_ids": ["case"],
            "isolation_ref": "read-only local fake Agent",
        }
    ]
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "diagnostic_probe",
        "revision_id": round_data["baseline_revision_id"],
        "round_id": round_data["id"],
    }
    with pytest.raises(ATKError, match="not authorized"):
        run_evaluation(root, request)
    probe = run_evaluation(root, {**request, "probe_authorization_id": "check-1"})
    assert probe["purpose"] == "diagnostic_probe"
    with pytest.raises(ATKError, match="probes budget"):
        run_evaluation(root, {**request, "probe_authorization_id": "check-1"})


def test_direct_probe_runs_frozen_script_instead_of_agent(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, round_data, plan = project(tmp_path, "print('agent')\n", rows)
    scripts = root / "probes"
    scripts.mkdir()
    script = scripts / "tool.py"
    script.write_text("print('tool')\n")
    command = [sys.executable, ".atk/probes/tool.py"]
    plan["budget"]["probes"] = 2
    plan["probe_permissions"] = [
        {
            "id": "direct-tool",
            "command": command,
            "command_hash": digest(command),
            "runner_hash": digest(root / "adapters/runner.py"),
            "case_ids": ["case"],
            "isolation_ref": "local read-only fixture",
            "working_directory": ".",
            "script_path": "tool.py",
            "script_sha256": digest(script),
            "timeout_seconds": 5,
            "max_calls": 1,
        }
    ]
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "diagnostic_probe",
        "revision_id": round_data["baseline_revision_id"],
        "round_id": round_data["id"],
        "probe_authorization_id": "direct-tool",
        "command": [sys.executable, "agent.py"],
    }
    batch = run_evaluation(root, request)
    _, records, _ = validate_evidence(root, batch["id"])
    assert next(iter(records.values()))["output"].strip() == "tool"
    assert batch["probe_config"]["command_hash"] == digest(command)
    assert batch["probe_config"]["script_sha256"] == digest(script)
    with pytest.raises(ATKError, match="max_calls"):
        run_evaluation(root, request)
    script.write_text("print('changed')\n")
    with pytest.raises(ATKError, match="not authorized"):
        run_evaluation(root, request)


@pytest.mark.parametrize("protected", [False, True])
def test_external_write_requires_recorded_protection_before_replay(tmp_path: Path, protected: bool) -> None:
    effect = {"name": "write customer database"}
    if protected:
        effect["protection"] = {"kind": "stub", "evidence_ref": "runtime.md#database-stub"}
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, round_data, _ = project(tmp_path, "print('ok')\n", rows, external_effects=[effect])
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "evaluation",
        "revision_id": round_data["baseline_revision_id"],
        "revision_commit": git(repo, "rev-parse", "HEAD"),
    }
    if not protected:
        with pytest.raises(ATKError, match="external effect lacks recorded protection"):
            run_evaluation(root, request)
        assert not (root / "evidence").exists()
    else:
        batch = run_evaluation(root, request)
        assert batch["status"] == "sealed"


def test_init_requires_explicit_external_effect_declaration(tmp_path: Path) -> None:
    repo = tmp_path / "agent"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "ATK Test")
    (repo / "agent.py").write_text("print('ok')\n")
    git(repo, "add", "agent.py")
    git(repo, "commit", "-qm", "initial")
    with pytest.raises(ATKError, match="external_effects"):
        initialize_project(
            repo,
            {
                "python": sys.executable,
                "command": [sys.executable, "agent.py"],
                "components": [],
                "allowed_paths": ["agent.py"],
                "protected_paths": [],
                "runtime_notes": "No external writes.\n",
            },
        )
    assert not (repo / ".atk").exists()


@pytest.mark.parametrize("trusted", [False, True])
def test_efficiency_gate_and_missing_metric(tmp_path: Path, trusted: bool) -> None:
    script = (
        "import json,sys\nfrom pathlib import Path\n"
        "(Path(sys.argv[2])/'metrics.json').write_text(json.dumps({'cost':0,'tool_calls':1}))\n"
        "print('ok')\n"
    )
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, round_data, plan = project(
        tmp_path,
        script,
        rows,
        metric_sources={"cost": {"source": "adapter_meter", "evidence_ref": "runtime.md#billing-meter"}},
    )
    if trusted:
        runner = root / "adapters" / "runner.py"
        code = runner.read_text()
        code = code.replace(
            "def main() -> int:",
            "_run_one = run_one\n"
            "def run_one(attempt, case, config, output, timeout):\n"
            "    record = _run_one(attempt, case, config, output, timeout)\n"
            "    record['metrics']['cost'] = 2 if (Path(config['workspace_path']) / 'prompt.txt').read_text() == 'old' else 1\n"
            "    record['metric_sources']['cost'] = 'adapter_meter'\n"
            "    return record\n\n"
            "def main() -> int:",
        )
        runner.write_text(code)
        plan["runner_hash"] = digest(runner)
    plan.update({"objective": "efficiency", "efficiency_metric": "cost", "metric_limits": {"max_total_cost": 1.5}})
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "evaluation",
        "revision_id": round_data["baseline_revision_id"],
        "round_id": round_data["id"],
    }
    base = run_evaluation(root, request)
    left = assess(root, base, {"case": "ok"})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    candidate = run_evaluation(root, {**request, "revision_id": sealed["revision_id"]})
    right = assess(root, candidate, {"case": "ok"})
    comparison = {
        "round_id": round_data["id"],
        "mode": "incremental",
        "issue_id": "issue",
        "candidate_id": draft["id"],
        "left_assessment_id": left,
        "right_assessment_id": right,
        "left_commit": round_data["baseline_commit"],
    }
    result = compare_and_gate(root, comparison)
    if not trusted:
        assert result["result"] == "insufficient"
        assert result["metrics"]["cost"] == {"left_total": None, "right_total": None}
        assert any("trusted source" in reason for reason in result["limitations"])
        return
    assert result["result"] == "pass"
    assert result["metrics"]["cost"] == {"left_total": 2.0, "right_total": 1.0}
    _, rows = read_assessment(root, right)
    rows[0]["verdict"] = "unknown"
    rows[0]["evidence_refs"] = json.loads(rows[0]["evidence_refs"])
    unknown_right = store_assessment(
        root, {"batch_id": candidate["id"], "evaluation_spec": SPEC, "judger": JUDGER, "rows": rows}
    ).parent.name
    incomplete = compare_and_gate(root, {**comparison, "right_assessment_id": unknown_right})
    assert incomplete["result"] == "insufficient"
    assert incomplete["case_distributions"]["case"]["right"] == {"pass": 0, "fail": 0, "unknown": 1}
    assert incomplete["outcome_counts"]["unknown"] == 1
    assert incomplete["coverage"]["complete_cases"] == 0


def test_holdout_exposure_cannot_be_reused_in_new_round(tmp_path: Path) -> None:
    rows = [{"id": "hold", "input": "case", "usage": "holdout", "source_group_id": "shared"}]
    repo, root, dataset, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    plan["holdout_milestone_id"] = "milestone-1"
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["hold"],
        "purpose": "evaluation",
        "revision_id": round_data["baseline_revision_id"],
        "round_id": round_data["id"],
        "holdout_milestone_id": "milestone-1",
    }
    run_evaluation(root, request)
    run_evaluation(root, request)  # the other side of the same frozen milestone
    with pytest.raises(ATKError, match="holdout was exposed"):
        prepare_candidate(
            repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
        )
    finish_round(repo, root, {"round_id": round_data["id"], "action": "close_without_adoption", "reason": "done"})
    next_round = create_round(repo, root, {"issue_ids": ["issue"]})
    plan["holdout_milestone_id"] = "milestone-2"
    freeze_round(repo, root, {"round_id": next_round["id"], "plan": plan})
    with pytest.raises(ATKError, match="exposed"):
        run_evaluation(
            root,
            {
                **request,
                "round_id": next_round["id"],
                "revision_id": next_round["baseline_revision_id"],
                "holdout_milestone_id": "milestone-2",
            },
        )


def test_holdout_evidence_used_for_knowledge_cannot_be_reused(tmp_path: Path) -> None:
    rows = [{"id": "hold", "input": "case", "usage": "holdout", "source_group_id": "shared"}]
    repo, root, dataset, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    plan["holdout_milestone_id"] = "milestone-1"
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["hold"],
        "purpose": "evaluation",
        "revision_id": round_data["baseline_revision_id"],
        "round_id": round_data["id"],
        "holdout_milestone_id": "milestone-1",
    }
    batch = run_evaluation(root, request)
    knowledge = store_knowledge(
        root,
        {
            "knowledge": {
                "status": "provisional",
                "applicability": "case",
                "component_hashes": {"agent": "v1"},
                "contract_hashes": {"task": "v1"},
                "judger_hash": "v1",
                "evidence_refs": [{"batch_id": batch["id"], "evidence_id": batch["evidence_index"][0]["evidence_id"]}],
                "contrary_refs": [],
                "candidate_ids": [],
                "validation_ids": [],
                "body": "The holdout result informed this rule.",
            }
        },
    )
    assert knowledge["optimization_source_group_ids"] == ["shared"]
    with pytest.raises(ATKError, match="used for optimization"):
        run_evaluation(root, request)


def test_imported_knowledge_needs_source_group_before_holdout(tmp_path: Path) -> None:
    rows = [{"id": "hold", "input": "case", "usage": "holdout", "source_group_id": "shared"}]
    repo, root, dataset, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    source = tmp_path / "trace.json"
    source.write_text(json.dumps({"id": "trace-1", "input": "case", "output": "done"}))
    batch = import_evidence(
        root,
        {
            "source": str(source),
            "source_kind": "langfuse",
            "source_namespace": "fixture",
            "adapter_profile": "langfuse_trace_bundle",
            "mapping_version": "1",
        },
    )
    ref = {"batch_id": batch["id"], "evidence_id": batch["evidence_index"][0]["evidence_id"]}
    knowledge = {
        "status": "provisional",
        "applicability": "case",
        "component_hashes": {"agent": "v1"},
        "contract_hashes": {"task": "v1"},
        "judger_hash": "v1",
        "evidence_refs": [ref],
        "contrary_refs": [],
        "candidate_ids": [],
        "validation_ids": [],
        "body": "Trace informed this rule.",
    }
    with pytest.raises(ATKError, match="source group"):
        store_knowledge(root, {"knowledge": knowledge})
    knowledge["evidence_refs"] = [{**ref, "source_group_id": "shared"}]
    saved = store_knowledge(root, {"knowledge": knowledge})
    assert saved["optimization_source_group_ids"] == ["shared"]
    plan["holdout_milestone_id"] = "milestone-1"
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    with pytest.raises(ATKError, match="used for optimization"):
        run_evaluation(
            root,
            {
                "dataset_id": dataset["id"],
                "case_ids": ["hold"],
                "purpose": "evaluation",
                "revision_id": round_data["baseline_revision_id"],
                "round_id": round_data["id"],
                "holdout_milestone_id": "milestone-1",
            },
        )


def test_knowledge_input_scope_or_unknown_identity_needs_revalidation(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    _, root, dataset, round_data, _ = project(tmp_path, "print('ok')\n", rows)
    batch = run_evaluation(
        root,
        {
            "dataset_id": dataset["id"],
            "case_ids": ["case"],
            "purpose": "evaluation",
            "revision_id": round_data["baseline_revision_id"],
            "revision_commit": round_data["baseline_commit"],
        },
    )
    identity = {
        "applicability": {"task_kind": "math"},
        "component_hashes": {"agent": "v1"},
        "contract_hashes": {"task": "v1"},
        "judger_hash": "v1",
    }
    knowledge = store_knowledge(
        root,
        {
            "knowledge": {
                **identity,
                "status": "validated_in_scope",
                "evidence_refs": [{"batch_id": batch["id"], "evidence_id": batch["evidence_index"][0]["evidence_id"]}],
                "contrary_refs": [],
                "candidate_ids": [],
                "validation_ids": [],
                "body": "Applies to math tasks.",
            }
        },
    )
    request = {"knowledge_id": knowledge["id"], "current_identity": identity}
    assert knowledge["applicability_hash"] == digest(identity["applicability"])
    assert knowledge_applicability(root, request)["applicability"] == "validated_in_scope"
    assert (
        knowledge_applicability(
            root, {**request, "current_identity": {**identity, "applicability": {"task_kind": "code"}}}
        )["applicability"]
        == "needs_revalidation"
    )
    assert knowledge_applicability(root, {**request, "current_identity": {}})["applicability"] == "needs_revalidation"


def test_dataset_rejects_unhashable_source_group(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    _, root, _, _, _ = project(tmp_path, "print('ok')\n", rows)
    source = tmp_path / "bad.jsonl"
    source.write_text(json.dumps({"id": "bad", "input": "case", "source_group_id": ["g"]}) + "\n")
    with pytest.raises(ATKError, match="invalid source group"):
        store_dataset(
            root,
            {"source": str(source), "mapping": {"id": "id", "input": "input", "source_group_id": "source_group_id"}},
        )


def test_knowledge_rejects_missing_links_and_unsupported_validated_state(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "case", "usage": "optimization", "source_group_id": "g"}]
    _, root, _, round_data, _ = project(tmp_path, "print('ok')\n", rows)
    knowledge = {
        "status": "provisional",
        "applicability": "case",
        "component_hashes": {"agent": "v1"},
        "contract_hashes": {"task": "v1"},
        "judger_hash": "v1",
        "evidence_refs": [],
        "contrary_refs": [],
        "candidate_ids": ["candidate-missing"],
        "validation_ids": [],
        "body": "A finding.",
    }
    with pytest.raises(ATKError, match="Candidate"):
        store_knowledge(root, {"knowledge": knowledge})
    fake = root / "rounds" / round_data["id"] / "candidates" / "candidate-missing" / "candidate.json"
    fake.parent.mkdir(parents=True)
    fake.write_text(json.dumps({"id": "candidate-missing"}))
    with pytest.raises(ATKError, match="invalid identity"):
        store_knowledge(root, {"knowledge": knowledge})
    knowledge["candidate_ids"] = []
    knowledge["validation_ids"] = ["validation-missing"]
    with pytest.raises(ATKError, match="Validation"):
        store_knowledge(root, {"knowledge": knowledge})
    knowledge["validation_ids"] = []
    knowledge["status"] = "validated_in_scope"
    with pytest.raises(ATKError, match="supporting evidence"):
        store_knowledge(root, {"knowledge": knowledge})
