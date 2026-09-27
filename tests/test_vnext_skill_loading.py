from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

from agent_tune_kit.checkpoints import create_round, freeze_round, prepare_candidate, seal_candidate
from agent_tune_kit.core import digest
from agent_tune_kit.execution import initialize_project, run_evaluation, store_dataset
from agent_tune_kit.governance import compare_and_gate
from tests.test_vnext_flow import assessment_for, git, record_local_issue


@pytest.mark.parametrize(
    "load_mode", ["direct", "linked_source", "wrong_install", "staged_copy", "stale_build", "missing_path"]
)
def test_business_skill_must_be_loaded_at_each_revision(tmp_path: Path, load_mode: str) -> None:
    repo = tmp_path / "agent"
    (repo / "skills" / "reply").mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "ATK Test")
    skill = repo / "skills" / "reply" / "SKILL.md"
    skill.write_text("old")
    installed_copy = tmp_path / "installed-copy" / "SKILL.md"
    installed_copy.parent.mkdir()
    if load_mode == "linked_source":
        installed_copy.symlink_to(skill)
    else:
        installed_copy.write_text("old" if load_mode == "stale_build" else "new")
    (repo / "agent.py").write_text(
        "import hashlib,json,sys\nfrom pathlib import Path\n"
        "skill=Path('skills/reply/SKILL.md')\n"
        f"installed=Path({str(installed_copy)!r})\n"
        f"mode={load_mode!r}\n"
        "if mode=='staged_copy' and skill.read_bytes()==b'new': installed.write_bytes(skill.read_bytes())\n"
        "loaded=installed if mode!='direct' and skill.read_bytes()==b'new' else skill\n"
        "data=loaded.read_bytes()\n"
        "out=Path(sys.argv[2])\n"
        "event={'component_id':'business-skill','state':'loaded','fingerprint':hashlib.sha256(data).hexdigest(),"
        "'resolved_path':str(loaded.resolve())}\n"
        "if mode=='missing_path': event.pop('resolved_path')\n"
        "if mode in ('staged_copy','stale_build') and loaded==installed: "
        "event['staged_from_path']=str(skill.resolve())\n"
        "(out/'loading.json').write_text(json.dumps([event]))\n"
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
            "external_effects": [],
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
    record_local_issue(root, round_data["id"], "skill-rule", ["case-1"])
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
        "repeatability_basis": "Fake Agent reads one fixed Skill file and follows deterministic branches.",
        "final_repeats": 1,
        "budget": {"executions": 4},
        "replay_preparation": {"mode": "stateless", "reason": "fixture has no persistent cache"},
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
    assert validation["result"] == (
        "insufficient" if load_mode in {"wrong_install", "stale_build", "missing_path"} else "pass"
    )
    if validation["result"] == "insufficient":
        assert any("case-1 repeat 1 lacks verified loading: business-skill" in gap for gap in validation["limitations"])
