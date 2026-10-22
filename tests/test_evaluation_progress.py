from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_tune_kit.checkpoints import temporary_revision
from agent_tune_kit.cli import internal_main
from agent_tune_kit.core import ATKError, read_json, store_assessment, validate_evidence, write_json
from agent_tune_kit.execution import inspect_evaluation, preflight_evaluation, run_evaluation, store_dataset
from tests.test_execution_observability import event_script, setup_project
from tests.test_vnext_concurrency import setup_run
from tests.test_vnext_flow import git
from tests.test_vnext_limits import JUDGER, SPEC


def full_capture_project(tmp_path: Path, suffix: str = "") -> tuple[Path, dict]:
    repo, request = setup_project(
        tmp_path,
        event_script(extra={"phase": "work"})
        + "(Path(sys.argv[2])/'generated.txt').write_text('actual product')\n"
        + suffix,
        {"format": "atk_json", "required_stages": ["work"]},
    )
    return repo / ".atk", request


def test_preflight_reports_all_effects_attachments_and_revision_without_execution(tmp_path):
    repo, request = setup_project(tmp_path, "raise RuntimeError('must not run')", {"format": "text"})
    root = repo / ".atk"
    project = read_json(root / "project.json")
    project["external_effects"] = [
        {"name": name} for name in ["gateway", "telemetry", "platform", "state", "git", "index"]
    ]
    write_json(root / "project.json", project)
    attachments = [tmp_path / name for name in ["one.txt", "two.txt"]]
    for path in attachments:
        path.write_text("frozen")
    source = tmp_path / "attachments.jsonl"
    source.write_text(
        "\n".join(
            json.dumps({"id": str(i), "input": "task", "usage": "optimization", "attachments": [str(path)]})
            for i, path in enumerate(attachments)
        )
    )
    dataset = store_dataset(
        root, {"source": str(source), "mapping": {key: key for key in ["id", "input", "usage", "attachments"]}}
    )
    request.update(dataset_id=dataset["id"], case_ids=["0", "1"], concurrency=0)
    attachments[0].write_text("changed")
    attachments[1].unlink()
    (repo / "agent.py").write_text("print('dirty')")
    (root / "adapters/logging_json.py").unlink()
    with pytest.raises(ATKError) as caught:
        run_evaluation(root, request)
    report = caught.value.artifact_refs[0]
    assert len(report["blockers"]) == 11
    assert {item["check"] for item in report["blockers"]} >= {"revision", "capture", "concurrency"}
    assert all(
        name in str(report["blockers"]) for name in ["gateway", "telemetry", "platform", "state", "git", "index"]
    )
    assert len(report["case_statuses"]) == 2
    assert all(item["status"] == "blocked" for item in report["case_statuses"])
    assert report["started_count"] == 0 and not report["batch_complete"]
    assert read_json(Path(report["report_path"])) == report
    assert not (root / "evidence").exists()


def test_preflight_aggregates_frozen_plan_and_budget_checks_without_reserving(tmp_path):
    root, request = setup_run(tmp_path, "print('never')", 1, ["a"])
    round_path = root / "rounds" / request["round_id"]
    before = {str(path): path.read_bytes() for path in round_path.rglob("*") if path.is_file()}
    report = preflight_evaluation(root, {**request, "concurrency": 2, "timeout_seconds": 99, "repeats": 10})
    messages = " ".join(item["message"] for item in report["blockers"])
    assert "concurrency" in messages and "超时" in messages and "预算" in messages
    assert before == {str(path): path.read_bytes() for path in round_path.rglob("*") if path.is_file()}
    assert not (root / "evidence").exists()
    # Read-only replay must not create an operation or run preparation.
    with temporary_revision(
        root.parent, root, request["round_id"], git(root.parent, "rev-parse", "HEAD"), check_only=True
    ):
        pass
    assert before == {str(path): path.read_bytes() for path in round_path.rglob("*") if path.is_file()}


def test_cli_failure_returns_saved_preflight_and_all_blockers(tmp_path, capsys):
    repo, request = setup_project(tmp_path, "print('never')")
    path, output = tmp_path / "request.json", tmp_path / "response.json"
    write_json(path, {**request, "project_path": str(repo), "concurrency": 0, "timeout_seconds": 0})
    assert internal_main(["run_evaluation", "--request", str(path), "--output", str(output)]) == 1
    response = read_json(output)
    assert response["status"] == "error"
    report = response["artifact_refs"][0]
    assert len(report["blockers"]) == 2
    assert Path(report["report_path"]).is_file()
    capsys.readouterr()


def test_calibration_does_not_claim_execution_and_assessment_finishes_separately(tmp_path):
    root, request = full_capture_project(tmp_path)
    report = preflight_evaluation(root, {**request, "evaluation_spec": SPEC, "judger": JUDGER})
    assert report["stages"] == {
        "preparation": "ready",
        "calibration": "calibrated",
        "execution": "not_started",
        "scoring": "not_started",
    }
    assert not report["batch_complete"] and not (root / "evidence").exists()
    batch = run_evaluation(root, request)
    progress = batch["evaluation_status"]
    assert progress["batch_complete"] and not progress["evaluation_complete"]
    assert progress["started_count"] == progress["planned_count"] == 2
    assert read_json(Path(progress["summary_ref"])) == progress
    _, records, _ = validate_evidence(root, batch["id"])
    rows = [
        {
            "record_id": rid,
            "dimension": "task_success",
            "validity": "valid",
            "validity_reason": "observed",
            "verdict": "pass",
            "score": 1,
            "reason": "fixture output",
            "evidence_refs": [{"batch_id": batch["id"], "evidence_id": rid}],
            "judger_kind": "deterministic",
        }
        for rid in records
    ]
    assessment = store_assessment(
        root, {"batch_id": batch["id"], "evaluation_spec": SPEC, "judger": JUDGER, "rows": rows}
    )
    scored = inspect_evaluation(root, {"batch_id": batch["id"], "assessment_id": assessment.parent.name})
    assert scored["evaluation_complete"]
    assert scored["stages"]["scoring"] == "completed"
    for case in scored["case_statuses"]:
        paths = {Path(entry["path"]).name: Path(entry["path"]) for entry in case["files"]}
        assert {"stdout.log", "output.txt", "events.jsonl", "generated.txt"} <= paths.keys()
        assert paths["generated.txt"].read_text() == "actual product"
        assert "done" in paths["events.jsonl"].read_text()
    # Queries verify actual retained files, not a previously successful summary.
    paths["generated.txt"].write_text("tampered")
    with pytest.raises(ATKError, match="指纹"):
        inspect_evaluation(root, {"batch_id": batch["id"]})


@pytest.mark.parametrize("capture", [{}, {"format": "text"}, {"format": "text", "required_stages": ["missing"]}])
def test_sealed_batch_and_returncode_zero_do_not_hide_missing_trajectory(tmp_path, capture):
    repo, request = setup_project(tmp_path, "print('answer')", capture)
    batch = run_evaluation(repo / ".atk", request)
    assert batch["status"] == "sealed"
    progress = batch["evaluation_status"]
    assert progress["stages"]["execution"] == "completed"
    assert not progress["batch_complete"] and progress["evidence_status"] == "incomplete"
    assert all(item["trajectory_status"] == "missing_or_undeclared" for item in progress["case_statuses"])


def test_agent_error_is_a_finished_execution_not_a_pass(tmp_path):
    root, request = full_capture_project(tmp_path, "sys.exit(1)\n")
    batch = run_evaluation(root, request)
    assert batch["evaluation_status"]["batch_complete"]
    assert not batch["evaluation_status"]["evaluation_complete"]
    assert all(item["execution_status"] == "agent_error" for item in batch["evaluation_status"]["case_statuses"])


def test_interruption_keeps_partial_output_logs_and_not_started_slots(tmp_path):
    root, request = full_capture_project(tmp_path, "print('in progress',flush=True)\nimport time\ntime.sleep(5)\n")
    batch = run_evaluation(root, {**request, "concurrency": 1, "batch_timeout_seconds": 1})
    assert batch["status"] == "interrupted"
    progress = batch["evaluation_status"]
    assert not progress["batch_complete"]
    assert {item["execution_status"] for item in progress["case_statuses"]} == {"unknown", "not_started"}
    case = next(item for item in progress["case_statuses"] if item["execution_status"] == "unknown")
    paths = {Path(entry["path"]).name: Path(entry["path"]) for entry in case["files"]}
    assert paths["generated.txt"].read_text() == "actual product"
    assert "in progress" in paths["stdout.log"].read_text()
    assert "done" in paths["events.jsonl"].read_text()
    assert (root / "evidence" / batch["id"] / "runner.stderr.log").is_file()


def test_per_case_timeout_is_not_complete_despite_sealed_records(tmp_path):
    root, request = full_capture_project(tmp_path, "import time\ntime.sleep(2)\n")
    batch = run_evaluation(root, {**request, "timeout_seconds": 0.1})
    assert batch["status"] == "sealed"
    assert not batch["evaluation_status"]["batch_complete"]
    assert all(item["execution_status"] == "timeout" for item in batch["evaluation_status"]["case_statuses"])


def test_post_execution_identity_failure_keeps_failure_receipt_and_raw_files(tmp_path):
    repo, request = setup_project(
        tmp_path, "from pathlib import Path\nPath('agent.py').write_text('mutated')\nprint('actually ran')"
    )
    with pytest.raises(ATKError, match="源码") as caught:
        run_evaluation(repo / ".atk", request)
    receipt = caught.value.artifact_refs[0]
    assert not receipt["batch_complete"] and receipt["execution_status"] == "unknown"
    folder = Path(receipt["evidence_dir"])
    assert (folder / "request.json").is_file() and (folder / "records.jsonl").is_file()
    assert list(folder.glob("attempts/*/stdout.log"))
    assert Path(receipt["report_path"]).is_file()


@pytest.mark.parametrize("interrupted", [False, True])
def test_required_native_trace_and_unconfigured_products_are_preserved(tmp_path, interrupted):
    repo, request = setup_project(
        tmp_path,
        "import json,sys\nfrom pathlib import Path\np=Path(sys.argv[2])\n"
        "(p/'trace.json').write_text(json.dumps({'tool':'actual tool','result':'ok'}))\n"
        "(p/'product.bin').write_bytes(b'actual binary product')\n(p/'service.log').write_text('executed\\n')\nprint('done')\n"
        + ("import time\ntime.sleep(5)\n" if interrupted else ""),
        {
            "format": "native",
            "logs": [{"glob": "service.log", "format": "text", "required": True}],
            "artifacts": [{"glob": "trace.json", "role": "events", "required": True}],
        },
    )
    batch = run_evaluation(
        repo / ".atk", {**request, "concurrency": 1, "batch_timeout_seconds": 1 if interrupted else 10}
    )
    assert batch["evaluation_status"]["batch_complete"] is not interrupted
    for case in batch["evaluation_status"]["case_statuses"]:
        if not case["files"]:
            assert case["execution_status"] == "not_started"
            continue
        files = {Path(file["path"]).name: file for file in case["files"]}
        from agent_tune_kit.evidence import read_native_evidence, view_execution

        view = view_execution(repo / ".atk", {"batch_id": batch["id"], "execution_id": case["execution_id"]})
        assert files["product.bin"]["evidence_id"] in {file["evidence_id"] for file in view["files"]}
        detail = read_native_evidence(
            repo / ".atk", {"batch_id": batch["id"], "evidence_id": files["trace.json"]["evidence_id"]}
        )
        assert detail["value"]["tool"] == "actual tool"


def test_retry_retains_failed_attempt_but_completes_recovered_slot(tmp_path):
    from agent_tune_kit.checkpoints import create_round, freeze_round
    from agent_tune_kit.core import digest
    from tests.test_vnext_limits import project

    root, request = full_capture_project(
        tmp_path,
        "marker=Path('.atk/attempted-'+case)\nfailed=not marker.exists()\nmarker.write_text('attempted')\nsys.exit(75 if failed else 0)\n",
    )
    # Build the usual frozen plan, then bind it to this instrumented fixture.
    separate = tmp_path / "plan-fixture"
    separate.mkdir()
    _, _, _, _, plan = project(
        separate, "print('plan')", [{"id": "a", "input": "a", "usage": "optimization", "source_group_id": "a"}]
    )
    config = read_json(root / "project.json")
    config["infrastructure_exit_codes"] = [75]
    config["allowed_paths"] = ["agent.py"]
    config["protected_paths"] = []
    write_json(root / "project.json", config)
    rnd = create_round(root.parent, root, {"issue_ids": []})
    plan.update(
        dataset_id=request["dataset_id"],
        case_ids=["a", "b"],
        allowed_paths=["agent.py"],
        protected_paths=[],
        issue_ids=[],
        target_case_ids_by_issue={},
        runner_hash=digest(root / "adapters/runner.py"),
        max_retries_per_slot=1,
        concurrency=2,
        timeout_seconds=3,
    )
    freeze_round(root.parent, root, {"round_id": rnd["id"], "plan": plan})
    request.update(round_id=rnd["id"], revision_id=rnd["baseline_revision_id"])
    batch = run_evaluation(root, request)
    assert not batch["evaluation_status"]["batch_complete"]
    retry_ids = [case["execution_id"] for case in batch["evaluation_status"]["case_statuses"]]
    retried = run_evaluation(root, {**request, "retry_batch_id": batch["id"], "retry_execution_ids": retry_ids})
    progress = retried["evaluation_status"]
    assert progress["batch_complete"] and progress["slot_count"] == 2 and progress["planned_count"] == 4
    assert sum(case["superseded"] for case in progress["case_statuses"]) == 2
    assert {case["execution_status"] for case in progress["case_statuses"] if case["superseded"]} == {"infra_error"}


def test_empty_required_trace_cannot_be_hidden_by_observed_stages(tmp_path):
    root, request = full_capture_project(tmp_path, "(Path(sys.argv[2])/'trace.json').write_text('')\n")
    config = read_json(root / "project.json")
    config["observability"]["artifacts"] = [{"glob": "trace.json", "role": "trajectory", "required": True}]
    write_json(root / "project.json", config)
    batch = run_evaluation(root, request)
    assert not batch["evaluation_status"]["batch_complete"]
    assert all(
        "empty_required_trajectory:trajectory" in case["issues"] for case in batch["evaluation_status"]["case_statuses"]
    )


def test_runner_cannot_report_complete_after_losing_stdout(tmp_path):
    root, request = full_capture_project(tmp_path)
    runner = root / "adapters/runner.py"
    runner.write_text(
        runner.read_text().replace("    captured = {", '    (task_dir / "stdout.log").unlink()\n    captured = {')
    )
    batch = run_evaluation(root, request)
    assert batch["status"] == "sealed"
    assert not batch["evaluation_status"]["batch_complete"]
    assert all("stdout.log" in case["missing"] for case in batch["evaluation_status"]["case_statuses"])
