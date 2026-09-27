from __future__ import annotations

import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import agent_tune_kit.checkpoints as checkpoints
import agent_tune_kit.core as core
import agent_tune_kit.governance as governance
from agent_tune_kit.checkpoints import (
    decide_candidate,
    freeze_round,
    inspect_or_recover_operation,
    prepare_candidate,
    rollback_to,
    seal_candidate,
    temporary_revision,
)
from agent_tune_kit.core import ATKError, digest, read_assessment, read_json, validate_evidence, write_json
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


def _install_smudge_filter(repo: Path, tmp_path: Path, target: str) -> None:
    script = tmp_path / "filter.py"
    script.write_text(
        "import sys\n"
        "data=sys.stdin.buffer.read()\n"
        "if sys.argv[1]=='smudge' and data==sys.argv[2].encode(): data+=b'!'\n"
        "if sys.argv[1]=='clean' and data.endswith(b'!'): data=data[:-1]\n"
        "sys.stdout.buffer.write(data)\n"
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    git(repo, "config", "filter.atk.smudge", f"{command} smudge {target}")
    git(repo, "config", "filter.atk.clean", f"{command} clean {target}")
    (repo / ".git" / "info" / "attributes").write_text("prompt.txt filter=atk\n")


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
    issue["root_cause_status"] = "inconclusive"
    issue["checks"] = [{"status": "not_run", "reason": "tool fixture unavailable"}]
    issue["mechanism_evidence_refs"] = []
    issue["next_action"] = "run a bounded direct tool check"
    unresolved = store_diagnosis(root, {"round_id": round_data["id"], "issues": [issue]})
    assert unresolved["issues"][0]["root_cause_status"] == "inconclusive"


def test_multi_issue_diagnosis_validates_every_issue_before_writing(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    _, root, _, round_data, _ = project(tmp_path, "print('ok')\n", rows, issue_ids=["tool-a", "tool-b"])
    first = {**_handoff_issue(), "id": "tool-a"}
    second = {**_handoff_issue(), "id": "tool-b", "root_cause_status": "supported"}
    with pytest.raises(ATKError, match="duplicate Issue ID"):
        store_diagnosis(root, {"round_id": round_data["id"], "issues": [first, first]})
    with pytest.raises(ATKError, match="supported mechanism"):
        store_diagnosis(root, {"round_id": round_data["id"], "issues": [first, second]})
    assert not (root / "rounds" / round_data["id"] / "issues" / "tool-a").exists()
    second["root_cause_status"] = "hypothesis"
    saved = store_diagnosis(root, {"round_id": round_data["id"], "issues": [first, second]})["issues"]
    assert [(issue["id"], issue["revision"]) for issue in saved] == [("tool-a", 1), ("tool-b", 1)]


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
    rows = [
        {"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"},
        {"id": "protect", "input": "safe", "usage": "protection", "source_group_id": "other"},
    ]
    agent = (
        "import json, subprocess, sys\nfrom pathlib import Path\n"
        "task = json.loads(Path(sys.argv[1]).read_text())\n"
        "result = subprocess.run([sys.executable, 'tool.py', task], capture_output=True, text=True)\n"
        "print(result.stdout.strip())\n"
    )
    repo, root, dataset, first, plan = project(
        tmp_path,
        agent,
        rows,
        ["tool"],
        extra_files={"tool.py": "import sys\nprint('error' if sys.argv[1] == 'task' else 'ok')\n"},
    )
    config = read_json(root / "project.json")
    config["components"].append(
        {"component_id": "tool", "role": "tool", "change_role": "fixed", "source_path": "tool.py"}
    )
    write_json(root / "project.json", config)

    def fixed_context_hash() -> str:
        return digest(
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
    plan["fixed_context_hash"] = fixed_context_hash()
    plan["budget"]["probes"] = 1
    plan["probe_permissions"] = [
        {
            "id": "initial-direct",
            "command": command,
            "command_hash": digest(command),
            "runner_hash": digest(root / "adapters" / "runner.py"),
            "case_ids": ["case"],
            "isolation_ref": "read-only local fixture",
            "timeout_seconds": 5,
            "max_calls": 1,
        }
    ]
    freeze_round(repo, root, {"round_id": first["id"], "plan": plan})
    initial_request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case", "protect"],
        "revision_id": first["baseline_revision_id"],
        "round_id": first["id"],
    }
    initial = run_evaluation(root, {**initial_request, "purpose": "evaluation"})
    initial_records = validate_evidence(root, initial["id"])[1]
    assert {record["case_id"]: record["output"].strip() for record in initial_records.values()} == {
        "case": "error",
        "protect": "ok",
    }
    initial_assessment = assess(root, initial, {"case": "ok", "protect": "ok"})
    _, initial_rows = read_assessment(root, initial_assessment)
    assert {initial_records[row["record_id"]]["case_id"]: row["verdict"] for row in initial_rows} == {
        "case": "fail",
        "protect": "pass",
    }
    direct = run_evaluation(
        root,
        {
            **initial_request,
            "case_ids": ["case"],
            "purpose": "diagnostic_probe",
            "probe_authorization_id": "initial-direct",
        },
    )
    direct_record = next(iter(validate_evidence(root, direct["id"])[1].values()))
    assert direct_record["output"].strip() == "error"
    direct_ref = {"batch_id": direct["id"], "evidence_id": direct_record["id"]}
    issue = {
        **_handoff_issue(),
        "root_cause_status": "supported",
        "competing_explanations_addressed": "direct tool call fails before Agent handoff",
        "checks": [{"status": "completed", "expected": "ok", "actual": "error", "evidence_refs": [direct_ref]}],
        "evidence_refs": [
            {"batch_id": initial["id"], "evidence_id": record["id"]}
            for record in initial_records.values()
            if record["case_id"] == "case"
        ],
        "mechanism_evidence_refs": [direct_ref],
    }
    store_diagnosis(root, {"round_id": first["id"], "issues": [issue]})
    finish_round(repo, root, {"round_id": first["id"], "action": "close_without_adoption", "reason": "tool owner"})
    (repo / "tool.py").write_text("print('ok')\n")
    git(repo, "add", "tool.py")
    git(repo, "commit", "-qm", "repair tool")
    fixed_identity = digest(repo / "tool.py")
    second = checkpoints.create_round(
        repo,
        root,
        {"issue_ids": ["tool"], "previous_round_id": first["id"], "external_fix_identity": fixed_identity},
    )
    plan.update(
        {
            "allowed_paths": [],
            "commit_authorized": False,
            "fixed_context_hash": fixed_context_hash(),
            "budget": {"executions": 2, "probes": 1},
            "probe_permissions": [
                {
                    "id": "direct-tool",
                    "kind": "direct_component",
                    "component_identity": fixed_identity,
                    "evaluation_spec_hash": digest(SPEC),
                    "command": command,
                    "command_hash": digest(command),
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
        "case_ids": ["case", "protect"],
        "revision_id": second["baseline_revision_id"],
        "round_id": second["id"],
    }
    batch = run_evaluation(root, {**base_request, "purpose": "evaluation", "phase": "external_fix"})
    assessment_id = assess(root, batch, {"case": "ok", "protect": "ok"})
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
        {**base_request, "case_ids": ["case"], "purpose": "diagnostic_probe", "probe_authorization_id": "direct-tool"},
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
        "component_identity": fixed_identity,
        "direct_evidence_refs": direct_refs,
        "end_to_end_validation_id": validation["id"],
    }
    with pytest.raises(ATKError, match="did not cover"):
        store_diagnosis(
            root,
            {"round_id": first["id"], "issues": [{**issue, "resolution": "resolved", "external_fix": fix}]},
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
        {"round_id": first["id"], "issues": [{**issue, "resolution": "resolved", "external_fix": fix}]},
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


def test_crashed_temporary_replay_restores_only_known_source(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    with (repo / ".git" / "info" / "exclude").open("a") as handle:
        handle.write("\ncache.txt\n")
    script = "from pathlib import Path; Path('cache.txt').write_text(Path('prompt.txt').read_text())"
    plan["replay_preparation"] = {"mode": "command", "argv": [sys.executable, "-c", script], "timeout_seconds": 2}
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
    current_commit = git(repo, "rev-parse", "HEAD")
    worker = (
        "import os\n"
        "from pathlib import Path\n"
        "from agent_tune_kit.checkpoints import temporary_revision\n"
        f"with temporary_revision(Path({str(repo)!r}), Path({str(root)!r}), {round_data['id']!r}, "
        f"{round_data['baseline_commit']!r}): os._exit(17)\n"
    )
    crashed = subprocess.run([sys.executable, "-c", worker], cwd=repo, capture_output=True, check=False)
    assert crashed.returncode == 17, crashed.stderr.decode()
    operations = [
        read_json(path)
        for path in (root / "rounds" / round_data["id"] / "operations").glob("*.json")
        if read_json(path)["action"] == "temporary_replay"
    ]
    assert len(operations) == 1 and operations[0]["stage"] == "switched"
    assert (repo / "prompt.txt").read_text() == (repo / "cache.txt").read_text() == "old"
    request = {"round_id": round_data["id"], "operation_id": operations[0]["id"]}
    (repo / "prompt.txt").write_text("third-party")
    with pytest.raises(ATKError, match="unknown content"):
        inspect_or_recover_operation(repo, root, request)
    assert (repo / "prompt.txt").read_text() == "third-party"
    (repo / "prompt.txt").write_text("old")
    assert inspect_or_recover_operation(repo, root, request)["stage"] == "aborted"
    assert inspect_or_recover_operation(repo, root, request)["stage"] == "aborted"
    assert (repo / "prompt.txt").read_text() == (repo / "cache.txt").read_text() == "new"
    assert git(repo, "rev-parse", "HEAD") == current_commit


def test_replay_preparation_timeout_stops_child_writes(tmp_path: Path) -> None:
    marker = tmp_path / "late-write.txt"
    child = f"import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).write_text('late')"
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(10)"
    with pytest.raises(ATKError, match="replay preparation timed out"):
        checkpoints._prepare_replay(
            tmp_path,
            {"mode": "command", "argv": [sys.executable, "-c", parent], "timeout_seconds": 1},
        )
    time.sleep(2.2)
    assert not marker.exists()


def test_timed_out_agent_cannot_write_into_next_case(tmp_path: Path) -> None:
    marker = tmp_path / "late-agent-write.txt"
    child = f"import time; from pathlib import Path; time.sleep(1.4); Path({str(marker)!r}).write_text('late')"
    script = (
        "import json, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "task=json.loads(Path(sys.argv[1]).read_text())\n"
        f"if task=='first':\n subprocess.Popen([sys.executable, '-c', {child!r}])\n time.sleep(10)\n"
        f"else:\n time.sleep(0.8)\n print('polluted' if Path({str(marker)!r}).exists() else 'clean')\n"
    )
    rows = [
        {"id": name, "input": name, "usage": "optimization", "source_group_id": name} for name in ("first", "second")
    ]
    repo, root, dataset, round_data, _ = project(tmp_path, script, rows)
    batch = run_evaluation(
        root,
        {
            "dataset_id": dataset["id"],
            "case_ids": ["first", "second"],
            "purpose": "evaluation",
            "revision_id": round_data["baseline_revision_id"],
            "revision_commit": git(repo, "rev-parse", "HEAD"),
            "timeout_seconds": 1,
            "batch_timeout_seconds": 5,
        },
    )
    _, records, _ = validate_evidence(root, batch["id"])
    by_case = {record["case_id"]: record for record in records.values()}
    assert batch["status"] == "sealed"
    assert by_case["first"]["execution"]["status"] == "timeout"
    assert by_case["second"]["execution"]["status"] == "completed"
    assert by_case["second"]["output"].strip() == "clean"
    assert not marker.exists()


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
    request_path, output_path = tmp_path / "recovery-request.json", tmp_path / "recovery-output.json"
    write_json(
        request_path,
        {
            "project_path": str(repo),
            "round_id": round_data["id"],
            "operation_id": operation.stem,
        },
    )
    resumed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_tune_kit.cli",
            "internal",
            "inspect_or_recover_operation",
            "--request",
            str(request_path),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stderr
    result = read_json(output_path)["artifact_refs"][0]
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


@pytest.mark.parametrize("action", ["reject", "defer"])
def test_interrupted_restoration_resumes_known_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows, extra_files={"second.txt": "old"})
    plan["allowed_paths"].append("second.txt")
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt", "second.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    (repo / "second.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    original_git = checkpoints.git
    restores = 0

    def interrupt_second_restore(path: Path, *args: str, ok: bool = True) -> bytes:
        nonlocal restores
        if "restore" in args and "--worktree" in args:
            restores += 1
            if restores == 2:
                raise RuntimeError("interrupted")
        return original_git(path, *args, ok=ok)

    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "git", interrupt_second_restore)
        with pytest.raises(RuntimeError, match="interrupted"):
            decide_candidate(
                repo,
                root,
                {
                    "round_id": round_data["id"],
                    "candidate_id": draft["id"],
                    "action": action,
                    "validation_id": _passing_validation(root, round_data, sealed),
                    "reason": "fixture",
                },
            )
    operation = next((root / "rounds" / round_data["id"] / "operations").glob("*.json"))
    assert {"prompt.txt": (repo / "prompt.txt").read_text(), "second.txt": (repo / "second.txt").read_text()} == {
        "prompt.txt": "old",
        "second.txt": "new",
    }
    request = {"round_id": round_data["id"], "operation_id": operation.stem}
    result = inspect_or_recover_operation(repo, root, request)
    assert result["stage"] == "complete"
    assert result["decision"]["action"] == action
    assert inspect_or_recover_operation(repo, root, request) == result
    assert (repo / "prompt.txt").read_text() == (repo / "second.txt").read_text() == "old"
    assert read_json(root / "rounds" / round_data["id"] / "round.json")["pending_candidate_id"] is None


@pytest.mark.parametrize("action", ["keep", "reject"])
def test_round_saved_before_operation_completion_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    original_write_json = checkpoints.write_json

    def interrupt_complete(path: Path, value: dict, *, immutable: bool = False) -> None:
        if path.parent.name == "operations" and value.get("stage") == "complete":
            raise RuntimeError("interrupted")
        original_write_json(path, value, immutable=immutable)

    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "write_json", interrupt_complete)
        with pytest.raises(RuntimeError, match="interrupted"):
            decide_candidate(
                repo,
                root,
                {
                    "round_id": round_data["id"],
                    "candidate_id": draft["id"],
                    "action": action,
                    "validation_id": _passing_validation(root, round_data, sealed),
                    "reason": "fixture",
                },
            )
    operation = next((root / "rounds" / round_data["id"] / "operations").glob("*.json"))
    current_commit = git(repo, "rev-parse", "HEAD")
    request = {"round_id": round_data["id"], "operation_id": operation.stem}
    result = inspect_or_recover_operation(repo, root, request)
    assert result["stage"] == "complete"
    assert result["decision"]["action"] == action
    assert inspect_or_recover_operation(repo, root, request) == result
    assert git(repo, "rev-parse", "HEAD") == current_commit
    assert read_json(operation)["stage"] == "complete"


@pytest.mark.parametrize("tamper", ["content", "index"])
def test_interrupted_restoration_refuses_unknown_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "_restore_parent", lambda *_: (_ for _ in ()).throw(RuntimeError("interrupted")))
        with pytest.raises(RuntimeError, match="interrupted"):
            decide_candidate(
                repo,
                root,
                {
                    "round_id": round_data["id"],
                    "candidate_id": draft["id"],
                    "action": "reject",
                    "validation_id": _passing_validation(root, round_data, sealed),
                    "reason": "fixture",
                },
            )
    if tamper == "content":
        (repo / "prompt.txt").write_text("third")
    else:
        git(repo, "add", "prompt.txt")
    operation = next((root / "rounds" / round_data["id"] / "operations").glob("*.json"))
    with pytest.raises(ATKError, match="unknown restoration content|staged changes must be resolved"):
        inspect_or_recover_operation(repo, root, {"round_id": round_data["id"], "operation_id": operation.stem})
    assert (repo / "prompt.txt").read_text() == ("third" if tamper == "content" else "new")
    assert read_json(root / "rounds" / round_data["id"] / "round.json")["pending_candidate_id"] == draft["id"]


@pytest.mark.parametrize("restore_kind", ["reject", "rollback", "replay"])
def test_git_smudge_cannot_hide_restored_content_drift(tmp_path: Path, restore_kind: str) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    request = {
        "round_id": round_data["id"],
        "candidate_id": draft["id"],
        "action": "reject" if restore_kind == "reject" else "keep",
        "validation_id": _passing_validation(root, round_data, sealed),
        "reason": "fixture",
    }
    if restore_kind != "reject":
        decide_candidate(repo, root, request)
    current_commit = git(repo, "rev-parse", "HEAD")
    _install_smudge_filter(repo, tmp_path, "new" if restore_kind == "replay" else "old")
    with pytest.raises(ATKError, match="restored content differs"):
        if restore_kind == "reject":
            decide_candidate(repo, root, request)
        elif restore_kind == "rollback":
            rollback_to(
                repo,
                root,
                {"round_id": round_data["id"], "target_commit": round_data["baseline_commit"], "reason": "fixture"},
            )
        else:
            with temporary_revision(repo, root, round_data["id"], round_data["baseline_commit"]):
                assert (repo / "prompt.txt").read_text() == "old"
    assert git(repo, "rev-parse", "HEAD") == current_commit
    assert git(repo, "diff", "--name-only") == ("prompt.txt" if restore_kind == "rollback" else "")
    assert (repo / "prompt.txt").read_text() == ("new!" if restore_kind == "replay" else "old!")
    stored = read_json(root / "rounds" / round_data["id"] / "round.json")
    assert stored["current_commit"] == current_commit
    assert stored["pending_candidate_id"] == (draft["id"] if restore_kind == "reject" else None)


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


def test_running_evaluation_blocks_candidate_operations(tmp_path: Path) -> None:
    started = tmp_path / "agent-started"
    release = tmp_path / "agent-release"
    script = (
        "import time\nfrom pathlib import Path\n"
        f"Path({str(started)!r}).write_text('started')\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(0.01)\n"
        "print('ok')\n"
    )
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, dataset, round_data, plan = project(tmp_path, script, rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "evaluation",
        "revision_id": round_data["baseline_revision_id"],
        "round_id": round_data["id"],
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_evaluation, root, request)
        try:
            deadline = time.monotonic() + 5
            while not started.exists() and not future.done() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert started.exists()
            with pytest.raises(ATKError, match="another ATK operation is running"):
                prepare_candidate(
                    repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
                )
            with pytest.raises(ATKError, match="another ATK operation is running"):
                store_diagnosis(root, {"round_id": round_data["id"], "issues": []})
        finally:
            release.write_text("go")
        assert future.result(timeout=10)["status"] == "sealed"
    assert (
        prepare_candidate(
            repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
        )["content_status"]
        == "draft"
    )


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_disk_write_failure_preserves_prior_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    state = tmp_path / "state.json"
    write_json(state, {"value": "before"})
    original_files = set(tmp_path.iterdir())

    def disk_error(*_: object) -> None:
        raise OSError("simulated disk write failure")

    with monkeypatch.context() as patch:
        if failure == "fsync":
            patch.setattr(core.os, "fsync", disk_error)
        else:
            patch.setattr(Path, "replace", disk_error)
        with pytest.raises(OSError, match="simulated disk write failure"):
            write_json(state, {"value": "after"})
    assert read_json(state) == {"value": "before"}
    assert set(tmp_path.iterdir()) == original_files


def test_freeze_round_resumes_only_matching_plan_after_status_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    request = {"round_id": round_data["id"], "plan": plan}
    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "_write_round", lambda *_: (_ for _ in ()).throw(OSError("status write failed")))
        with pytest.raises(OSError, match="status write failed"):
            freeze_round(repo, root, request)
    folder = root / "rounds" / round_data["id"]
    frozen = read_json(folder / "plan.json")
    assert read_json(folder / "round.json")["status"] == "analysis_only"
    with pytest.raises(ATKError, match="partial frozen plan differs"):
        freeze_round(repo, root, {"round_id": round_data["id"], "plan": {**plan, "final_repeats": 2}})
    assert read_json(folder / "plan.json") == frozen
    assert freeze_round(repo, root, request)["status"] == "ready"
    assert read_json(folder / "plan.json") == frozen


def test_freeze_round_rejects_second_active_round(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, first, plan = project(tmp_path, "print('ok')\n", rows)
    second = checkpoints.create_round(repo, root, {"issue_ids": ["issue"]})
    freeze_round(repo, root, {"round_id": first["id"], "plan": plan})
    with pytest.raises(ATKError, match="another active round"):
        freeze_round(repo, root, {"round_id": second["id"], "plan": plan})
    assert read_json(root / "rounds" / second["id"] / "round.json")["status"] == "analysis_only"
    assert not (root / "rounds" / second["id"] / "plan.json").exists()


def test_prepare_candidate_reuses_draft_after_round_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows, extra_files={"other.txt": "old"})
    plan["allowed_paths"].append("other.txt")
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "_write_round", lambda *_: (_ for _ in ()).throw(OSError("status write failed")))
        with pytest.raises(OSError, match="status write failed"):
            prepare_candidate(repo, root, request)
    folder = root / "rounds" / round_data["id"]
    orphan = next((folder / "candidates").glob("*/draft.json"))
    assert read_json(folder / "round.json")["pending_candidate_id"] is None
    with pytest.raises(ATKError, match="unregistered candidate draft differs"):
        prepare_candidate(repo, root, {**request, "paths": ["other.txt"]})
    assert prepare_candidate(repo, root, request)["id"] == orphan.parent.name
    assert read_json(folder / "round.json")["candidate_ids"] == [orphan.parent.name]
    assert len(list((folder / "candidates").glob("*/draft.json"))) == 1


def test_replacement_candidate_records_validated_lineage_and_patch(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    plan["budget"]["candidates"] = 2
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    first = prepare_candidate(repo, root, request)
    (repo / "prompt.txt").write_text("first")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": first["id"]})
    assert sealed["supersedes"] is None
    assert (root / "rounds" / round_data["id"] / sealed["patch_ref"]).is_file()
    decide_candidate(
        repo,
        root,
        {
            "round_id": round_data["id"],
            "candidate_id": first["id"],
            "action": "reject",
            "validation_id": _passing_validation(root, round_data, sealed),
            "reason": "try a different rule",
        },
    )
    with pytest.raises(ATKError, match="related Issues are outside"):
        prepare_candidate(repo, root, {**request, "related_issue_ids": ["unknown"]})
    with pytest.raises(ATKError, match="superseded candidate is outside"):
        prepare_candidate(repo, root, {**request, "supersedes": "candidate-unknown"})
    replacement = prepare_candidate(repo, root, {**request, "supersedes": first["id"]})
    assert replacement["supersedes"] == first["id"]
    assert replacement["parent_commit"] == first["parent_commit"]


@pytest.mark.parametrize("after_write", [False, True])
def test_finish_round_reuses_final_decision_after_status_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_write: bool
) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {"round_id": round_data["id"], "action": "close_without_adoption", "reason": "no candidate"}
    original_write = governance.write_json

    def interrupt(path: Path, value: dict, *, immutable: bool = False) -> None:
        if path.name == "round.json":
            if after_write:
                original_write(path, value, immutable=immutable)
            raise OSError("status write failed")
        original_write(path, value, immutable=immutable)

    with monkeypatch.context() as patch:
        patch.setattr(governance, "write_json", interrupt)
        with pytest.raises(OSError, match="status write failed"):
            finish_round(repo, root, request)
    folder = root / "rounds" / round_data["id"]
    saved = next((folder / "decisions").glob("*.json"))
    with pytest.raises(ATKError, match="saved final decision differs"):
        finish_round(repo, root, {**request, "reason": "different"})
    assert finish_round(repo, root, request)["id"] == saved.stem
    assert read_json(folder / "round.json")["status"] == "closed_without_adoption"
    assert len(list((folder / "decisions").glob("*.json"))) == 1


def test_interrupted_runner_keeps_running_and_not_started_attempts(tmp_path: Path) -> None:
    rows = [
        {"id": "first", "input": "first", "usage": "optimization", "source_group_id": "g1"},
        {"id": "second", "input": "second", "usage": "optimization", "source_group_id": "g2"},
    ]
    repo, root, dataset, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    runner = root / "adapters" / "runner.py"
    runner.write_text(
        "import argparse, json, sys\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--request')\n"
        "parser.add_argument('--output')\n"
        "args = parser.parse_args()\n"
        "request = json.loads(Path(args.request).read_text())\n"
        "ids = [item['record_id'] for item in request['attempts']]\n"
        "batch = {'batch_id': request['batch_id'], 'planned_record_ids': ids, "
        "'completed_record_ids': [], 'running_record_id': ids[0], "
        "'not_started_record_ids': ids[1:], 'actual_components': []}\n"
        "(Path(args.output) / 'batch.json').write_text(json.dumps(batch))\n"
        "sys.exit(3)\n"
    )
    plan["runner_hash"] = digest(runner)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    batch = run_evaluation(
        root,
        {
            "dataset_id": dataset["id"],
            "case_ids": ["first", "second"],
            "purpose": "evaluation",
            "revision_id": round_data["baseline_revision_id"],
            "round_id": round_data["id"],
        },
    )
    attempts = read_json(root / "evidence" / batch["id"] / "request.json")["attempts"]
    assert batch["status"] == "partial"
    assert batch["record_count"] == 0
    assert batch["unknown_record_ids"] == [attempts[0]["record_id"]]
    assert batch["not_started_record_ids"] == [attempts[1]["record_id"]]
    assert batch["missing_runner_artifacts"] == ["records.jsonl"]
    assert (
        batch["post_run_component_drift"][0]["reason"]
        == "runner component inventory differs from project configuration"
    )
    assert validate_evidence(root, batch["id"])[1] == {}


@pytest.mark.parametrize("kind", ["invalid_json", "missing_component_id"])
def test_invalid_runner_batch_keeps_partial_evidence(tmp_path: Path, kind: str) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    runner = root / "adapters" / "runner.py"
    runner.write_text(
        "import argparse, json\n"
        "from pathlib import Path\n"
        "parser=argparse.ArgumentParser()\n"
        "parser.add_argument('--request')\n"
        "parser.add_argument('--output')\n"
        "args=parser.parse_args()\n"
        "request=json.loads(Path(args.request).read_text())\n"
        "ids=[item['record_id'] for item in request['attempts']]\n"
        "batch={'batch_id': request['batch_id'], 'planned_record_ids': ids, 'completed_record_ids': [], "
        "'running_record_id': None, 'not_started_record_ids': ids, "
        "'actual_components': [{'role': 'prompt', 'change_role': 'variable', "
        "'source_path': 'prompt.txt', 'actual_sha256': 'incorrect'}]}\n"
        f"payload='{{bad' if {kind!r}=='invalid_json' else json.dumps(batch)\n"
        "(Path(args.output)/'batch.json').write_text(payload)\n"
    )
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
    assert batch["status"] == "partial"
    assert batch["invalid_runner_artifacts"] == (["batch.json"] if kind == "invalid_json" else [])
    assert validate_evidence(root, batch["id"])[1] == {}
    if kind == "invalid_json":
        assert (root / "evidence" / batch["id"] / "batch.json").read_text() == "{bad"
    else:
        assert any(
            item.get("reason") == "runner component inventory differs from project configuration"
            for item in batch["post_run_component_drift"]
        )


def test_truncated_runner_record_preserves_completed_prefix(tmp_path: Path) -> None:
    rows = [
        {"id": "first", "input": "first", "usage": "optimization", "source_group_id": "g1"},
        {"id": "second", "input": "second", "usage": "optimization", "source_group_id": "g2"},
    ]
    repo, root, dataset, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    runner = root / "adapters" / "runner.py"
    runner.write_text(
        "import argparse, json, sys\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--request')\n"
        "parser.add_argument('--output')\n"
        "args = parser.parse_args()\n"
        "request = json.loads(Path(args.request).read_text())\n"
        "first, second = request['attempts']\n"
        "output = Path(args.output)\n"
        "batch = {'batch_id': request['batch_id'], "
        "'planned_record_ids': [first['record_id'], second['record_id']], "
        "'completed_record_ids': [first['record_id']], 'running_record_id': second['record_id'], "
        "'not_started_record_ids': [], 'actual_components': []}\n"
        "(output / 'batch.json').write_text(json.dumps(batch))\n"
        "execution = {'id': first['execution_id'], 'batch_id': request['batch_id'], "
        "'record_id': first['record_id'], 'case_id': first['case_id'], "
        "'case_fingerprint': first['case_fingerprint'], 'repeat_index': first['repeat_index'], "
        "'retry_of': first['retry_of'], 'purpose': request['purpose'], "
        "'revision_id': request['revision_id']}\n"
        "record = {'id': first['record_id'], 'case_id': first['case_id'], 'execution': execution}\n"
        "(output / 'records.jsonl').write_bytes(json.dumps(record).encode() + b'\\n{\"id\":')\n"
        "sys.exit(3)\n"
    )
    plan["runner_hash"] = digest(runner)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    batch = run_evaluation(
        root,
        {
            "dataset_id": dataset["id"],
            "case_ids": ["first", "second"],
            "purpose": "evaluation",
            "revision_id": round_data["baseline_revision_id"],
            "round_id": round_data["id"],
        },
    )
    attempts = read_json(root / "evidence" / batch["id"] / "request.json")["attempts"]
    folder = root / "evidence" / batch["id"]
    assert batch["status"] == "partial"
    assert batch["record_count"] == 1
    assert batch["completed_record_ids"] == [attempts[0]["record_id"]]
    assert batch["unknown_record_ids"] == [attempts[1]["record_id"]]
    assert batch["incomplete_records_sha256"] == digest(folder / "records.incomplete.jsonl")
    assert set(validate_evidence(root, batch["id"])[1]) == {attempts[0]["record_id"]}


def test_runner_cannot_change_preallocated_attempt_identity(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, dataset, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    runner = root / "adapters" / "runner.py"
    runner.write_text(
        "import argparse, json\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--request')\n"
        "parser.add_argument('--output')\n"
        "args = parser.parse_args()\n"
        "request = json.loads(Path(args.request).read_text())\n"
        "attempt = request['attempts'][0]\n"
        "output = Path(args.output)\n"
        "batch = {'batch_id': request['batch_id'], 'planned_record_ids': [attempt['record_id']], "
        "'completed_record_ids': [attempt['record_id']], 'running_record_id': None, "
        "'not_started_record_ids': [], 'actual_components': []}\n"
        "(output / 'batch.json').write_text(json.dumps(batch))\n"
        "execution = {'id': attempt['execution_id'], 'batch_id': request['batch_id'], "
        "'record_id': attempt['record_id'], 'case_id': attempt['case_id'], "
        "'case_fingerprint': 'wrong', 'repeat_index': attempt['repeat_index'], "
        "'retry_of': attempt['retry_of'], 'purpose': request['purpose'], "
        "'revision_id': request['revision_id']}\n"
        "record = {'id': attempt['record_id'], 'case_id': attempt['case_id'], 'execution': execution}\n"
        "(output / 'records.jsonl').write_text(json.dumps(record) + '\\n')\n"
    )
    plan["runner_hash"] = digest(runner)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    with pytest.raises(ATKError, match="runner changed an attempt identity"):
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


def test_batch_timeout_stops_runner_child_processes(tmp_path: Path) -> None:
    started = tmp_path / "child-started"
    leaked = tmp_path / "child-survived"
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, dataset, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    runner = root / "adapters" / "runner.py"
    runner.write_text(
        "import argparse, json, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--request')\n"
        "parser.add_argument('--output')\n"
        "args = parser.parse_args()\n"
        "request = json.loads(Path(args.request).read_text())\n"
        "attempt = request['attempts'][0]\n"
        "batch = {'batch_id': request['batch_id'], 'planned_record_ids': [attempt['record_id']], "
        "'completed_record_ids': [], 'running_record_id': attempt['record_id'], "
        "'not_started_record_ids': [], 'actual_components': []}\n"
        "(Path(args.output) / 'batch.json').write_text(json.dumps(batch))\n"
        f"child = \"from pathlib import Path; import time; Path({str(started)!r}).write_text('yes'); "
        f"time.sleep(3); Path({str(leaked)!r}).write_text('yes')\"\n"
        "subprocess.Popen([sys.executable, '-c', child])\n"
        "time.sleep(10)\n"
    )
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
            "batch_timeout_seconds": 2,
        },
    )
    assert batch["status"] == "interrupted"
    assert started.exists()
    time.sleep(1.2)
    assert not leaked.exists()


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


@pytest.mark.parametrize(
    "alias",
    [
        "./prompt.txt",
        "nested//file.txt",
        "nested/./file.txt",
        "prompt.txt/",
        ".ATK/state.json",
        ".GIT/config",
        "C:\\secrets\\file.txt",
        "C:relative.txt",
        "nested\\file.txt",
    ],
)
def test_candidate_path_aliases_are_rejected(tmp_path: Path, alias: str) -> None:
    with pytest.raises(ATKError, match="unsafe path"):
        checkpoints.safe_path(tmp_path, alias)


def test_candidate_path_cannot_differ_only_by_case(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows, extra_files={"Example.txt": "base"})
    plan["allowed_paths"].append("example.txt")
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    with pytest.raises(ATKError, match="case or Unicode alias"):
        prepare_candidate(
            repo,
            root,
            {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["example.txt"]},
        )
    assert read_json(root / "rounds" / round_data["id"] / "round.json")["pending_candidate_id"] is None


@pytest.mark.parametrize(
    "interrupt_at,after_write", [("revision.json", False), ("candidate.json", False), ("candidate.json", True)]
)
def test_seal_candidate_resumes_only_matching_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt_at: str, after_write: bool
) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    original_write = checkpoints.write_json

    def interrupt(path: Path, value: dict, *, immutable: bool = False) -> None:
        if path.name == interrupt_at:
            if after_write:
                original_write(path, value, immutable=immutable)
            raise RuntimeError("seal interrupted")
        original_write(path, value, immutable=immutable)

    request = {"round_id": round_data["id"], "candidate_id": draft["id"]}
    with monkeypatch.context() as patch:
        patch.setattr(checkpoints, "write_json", interrupt)
        with pytest.raises(RuntimeError, match="seal interrupted"):
            seal_candidate(repo, root, request)
    folder = root / "rounds" / round_data["id"] / "candidates" / draft["id"]
    previous_revision = read_json(folder / "revision.json")["id"] if (folder / "revision.json").exists() else None
    if interrupt_at == "revision.json":
        (repo / "prompt.txt").write_text("other")
        with pytest.raises(ATKError, match="partial seal differs"):
            seal_candidate(repo, root, request)
        (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, request)
    assert sealed["revision_id"] == read_json(folder / "revision.json")["id"]
    if previous_revision:
        assert sealed["revision_id"] == previous_revision
    assert seal_candidate(repo, root, request) == sealed
    assert (folder / "files" / "prompt.txt").read_text() == "new"
    if interrupt_at == "revision.json":
        (folder / "files" / "prompt.txt").write_text("tampered")
        with pytest.raises(ATKError, match="partial seal differs"):
            seal_candidate(repo, root, request)
        with pytest.raises(ATKError, match="sealed candidate copy changed"):
            decide_candidate(
                repo,
                root,
                {
                    **request,
                    "action": "keep",
                    "validation_id": _passing_validation(root, round_data, sealed),
                    "reason": "fixture",
                },
            )


def test_unexpected_index_and_head_drift_block_candidate_keep(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, _, round_data, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["prompt.txt"]}
    )
    (repo / "prompt.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    request = {
        "round_id": round_data["id"],
        "candidate_id": draft["id"],
        "action": "keep",
        "validation_id": _passing_validation(root, round_data, sealed),
        "reason": "fixture",
    }
    intruder = repo / "intruder.txt"
    intruder.write_text("third-party content")
    git(repo, "add", "intruder.txt")
    with pytest.raises(ATKError, match="staged changes"):
        decide_candidate(repo, root, request)
    assert git(repo, "diff", "--cached", "--name-only") == "intruder.txt"
    git(repo, "commit", "-qm", "third-party commit")
    with pytest.raises(ATKError, match="HEAD differs"):
        decide_candidate(repo, root, request)
    assert intruder.read_text() == "third-party content"
    assert not (root / "rounds" / round_data["id"] / "candidates" / draft["id"] / "decision.json").exists()
