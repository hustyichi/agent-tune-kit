from __future__ import annotations

import csv
import sys
from pathlib import Path

from agent_tune_kit.checkpoints import create_round, freeze_round, prepare_candidate, seal_candidate
from agent_tune_kit.core import digest
from agent_tune_kit.execution import initialize_project, run_evaluation, store_dataset
from agent_tune_kit.governance import compare_and_gate
from tests.test_vnext_flow import assessment_for, git


def test_business_skill_must_be_loaded_at_each_revision(tmp_path: Path) -> None:
    repo = tmp_path / "agent"
    (repo / "skills" / "reply").mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "ATK Test")
    skill = repo / "skills" / "reply" / "SKILL.md"
    skill.write_text("old")
    (repo / "agent.py").write_text(
        "import hashlib,json,sys\nfrom pathlib import Path\n"
        "skill=Path('skills/reply/SKILL.md')\n"
        "data=skill.read_bytes()\n"
        "out=Path(sys.argv[2])\n"
        "(out/'loading.json').write_text(json.dumps([{'component_id':'business-skill','state':'loaded',"
        "'fingerprint':hashlib.sha256(data).hexdigest()}]))\n"
        "print('hello' if data==b'new' else 'bad')\n"
    )
    git(repo, "add", "agent.py", "skills/reply/SKILL.md")
    git(repo, "commit", "-qm", "initial")
    initialize_project(
        repo,
        {
            "python": sys.executable,
            "command": [sys.executable, "agent.py", "{input_file}", "{output_dir}"],
            "components": [
                {
                    "component_id": "business-skill",
                    "role": "skill",
                    "change_role": "variable",
                    "source_path": "skills/reply/SKILL.md",
                }
            ],
            "allowed_paths": ["skills/reply"],
            "protected_paths": ["agent.py"],
            "runtime_notes": "Agent reads the Skill during each fresh process and emits loading.json.\n",
        },
    )
    root = repo / ".atk"
    source = tmp_path / "cases.csv"
    with source.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "input", "expected", "usage"])
        writer.writeheader()
        writer.writerow({"id": "case-1", "input": "hi", "expected": "hello", "usage": "optimization"})
    dataset = store_dataset(
        root,
        {"source": str(source), "mapping": {"id": "id", "input": "input", "expected": "expected", "usage": "usage"}},
    )
    round_data = create_round(repo, root, {"issue_ids": ["skill-rule"]})
    spec = {
        "version": "v1",
        "boundary": "local skill Agent",
        "dimensions": ["task_success"],
        "dimension_rules": {"task_success": {"validity": "completed response", "attribution": "Agent"}},
        "denominator_rule": "all planned slots",
    }
    judger = {
        "version": "exact-v1",
        "readiness": "calibrated",
        "calibration_examples": [
            {"source_ref": "positive", "expected_verdict": "pass", "actual_verdict": "pass"},
            {"source_ref": "negative", "expected_verdict": "fail", "actual_verdict": "fail"},
        ],
    }
    plan = {
        "allowed_paths": ["skills/reply"],
        "protected_paths": ["agent.py"],
        "issue_ids": ["skill-rule"],
        "evaluation_spec_hash": digest(spec),
        "judger_hash": digest(judger),
        "runner_hash": digest(root / "adapters/runner.py"),
        "fixed_context_hash": digest([]),
        "case_ids": ["case-1"],
        "protection_case_ids": ["case-1"],
        "target_case_ids_by_issue": {"skill-rule": ["case-1"]},
        "required_loaded_component_ids": ["business-skill"],
        "repeatability": "deterministic",
        "final_repeats": 1,
        "budget": {"executions": 4},
        "commit_authorized": True,
        "rollback_on_failure": "B0",
    }
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    base = run_evaluation(
        root,
        {
            "dataset_id": dataset["id"],
            "case_ids": ["case-1"],
            "repeats": 1,
            "purpose": "evaluation",
            "round_id": round_data["id"],
            "revision_id": round_data["baseline_revision_id"],
        },
    )
    left = assessment_for(root, base, spec, judger, {"case-1": "hello"})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "skill-rule", "paths": ["skills/reply/SKILL.md"]}
    )
    skill.write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    candidate = run_evaluation(
        root,
        {
            "dataset_id": dataset["id"],
            "case_ids": ["case-1"],
            "repeats": 1,
            "purpose": "evaluation",
            "round_id": round_data["id"],
            "revision_id": sealed["revision_id"],
        },
    )
    right = assessment_for(root, candidate, spec, judger, {"case-1": "hello"})
    validation = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "incremental",
            "issue_id": "skill-rule",
            "candidate_id": draft["id"],
            "left_assessment_id": left,
            "right_assessment_id": right,
            "left_commit": round_data["baseline_commit"],
        },
    )
    assert validation["result"] == "pass"
