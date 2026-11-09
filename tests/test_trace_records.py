import json
from pathlib import Path

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.traces.records import (
    load_change,
    load_issue,
    save_change,
    save_detection,
    save_diagnosis,
    save_issue,
    save_verification,
    status,
)
from agent_tune_kit.traces.store import create_capture, evidence_ref, load_session, validate_ref


@pytest.fixture
def archive(tmp_path):
    result = create_capture(
        tmp_path,
        "session-1",
        {"type": "local", "instance": "fixture", "project": "agent"},
        {"trace.json": '{"sessionId":"session-1","output":"wrong"}'},
    )
    directory = Path(result["session_dir"])
    ref = evidence_ref(directory, f"captures/{result['capture_id']}/raw/trace.json", "/output")
    return directory, result, ref


def new_issue(archive, title="Wrong answer"):
    directory, _, ref = archive
    return save_issue(
        directory, {"title": title, "description": "Output differs from the requested task.", "evidence": [ref]}
    )


def new_change(archive, targets=None):
    directory, _, _ = archive
    issue = new_issue(archive)
    save_diagnosis(directory, issue["id"], "# Root cause and plan\n\nObserved fact, hypothesis, repair, verification.")
    running = save_change(
        directory,
        issue["id"],
        {"status": "running", "before": {"git_head": "abc"}, **({"target_issues": targets} if targets else {})},
    )
    completed = save_change(
        directory,
        issue["id"],
        {
            "id": running["id"],
            "revision": running["revision"],
            "status": "completed",
            "candidate": {"git_head": "def", "diff_sha256": "123"},
            "diff": "+ corrected implementation",
        },
    )
    return issue, completed


def test_zero_issues_and_partial_detection_are_preserved(archive):
    directory, result, _ = archive
    first = save_detection(
        directory,
        {
            "captures": [result["capture_id"]],
            "scope": {"nodes": ["first"]},
            "status": "completed",
            "gaps": ["remaining body unread"],
            "issue_ids": [],
        },
    )
    second = save_detection(
        directory,
        {
            "captures": [result["capture_id"]],
            "scope": {"nodes": ["second"]},
            "status": "interrupted",
            "gaps": ["interrupted"],
        },
    )
    assert len(load_session(directory)["detections"]) == 2
    assert not (directory / "issues").exists()
    with pytest.raises(UserActionRequired, match="immutable"):
        save_detection(directory, {"id": first["id"], "revision": first["revision"], "status": "completed"})
    assert second["inputs"][0]["sha256"]


def test_issue_updates_require_revision_and_preserve_history(archive):
    directory, _, _ = archive
    first = new_issue(archive)
    second = new_issue(archive, "Another issue")
    revised = save_issue(
        directory,
        {
            "id": first["id"],
            "revision": first["revision"],
            "title": "Refined symptom",
            "status": "excluded",
            "notes": ["User clarified expected behavior."],
        },
    )
    assert revised["history"][0]["title"] == "Wrong answer"
    assert load_issue(directory, second["id"])["title"] == "Another issue"
    with pytest.raises(UserActionRequired, match="revision"):
        save_issue(directory, {"id": first["id"], "revision": first["revision"], "title": "stale"})


def test_diagnosis_and_adopted_basis_are_fixed(archive):
    directory, _, _ = archive
    issue, change = new_change(archive)
    adopted = change["diagnoses"][issue["id"]]
    before = validate_ref(directory, adopted).read_bytes()
    save_diagnosis(directory, issue["id"], "A new hypothesis and plan.")
    assert validate_ref(directory, adopted).read_bytes() == before
    assert load_change(directory, issue["id"], change["id"])["diagnoses"][issue["id"]] == adopted
    with pytest.raises(UserActionRequired, match="immutable"):
        save_change(
            directory, issue["id"], {"id": change["id"], "revision": change["revision"], "candidate": {"new": True}}
        )


def test_selection_reason_survives_updates_and_is_frozen_in_diagnosis(archive):
    directory, _, _ = archive
    issue = new_issue(archive)
    assert "selection_reason" not in issue  # Older records remain usable.
    issue = save_issue(
        directory, {"id": issue["id"], "revision": issue["revision"], "selection_reason": "Repeated task failure."}
    )
    dry = save_issue(
        directory,
        {"id": issue["id"], "revision": issue["revision"], "selection_reason": "Explicit user focus."},
        dry_run=True,
    )
    assert dry["selection_reason"] == "Explicit user focus."
    assert load_issue(directory, issue["id"])["selection_reason"] == "Repeated task failure."
    saved = save_diagnosis(directory, issue["id"], "Cause unconfirmed; check the loaded Skill.")
    diagnosis = Path(saved["path"]).read_bytes()
    snapshot = json.loads(diagnosis.decode().split(" -->", 1)[0].removeprefix("<!-- atk-issue-snapshot "))
    assert snapshot["selection_reason"] == "Repeated task failure."
    current = load_issue(directory, issue["id"])
    revised = save_issue(
        directory,
        {"id": issue["id"], "revision": current["revision"], "selection_reason": "User focus; recurrence disproven."},
    )
    assert revised["history"][-1]["selection_reason"] == "Repeated task failure."
    revised = save_issue(directory, {"id": issue["id"], "revision": revised["revision"], "status": "investigating"})
    assert revised["selection_reason"] == "User focus; recurrence disproven."
    assert Path(saved["path"]).read_bytes() == diagnosis


@pytest.mark.parametrize("reason", [None, "", "  ", 1, [], {}])
def test_invalid_selection_reason_does_not_revise_issue(archive, reason):
    directory, _, _ = archive
    issue = new_issue(archive)
    path = directory / "issues" / issue["id"] / "issue.json"
    before = path.read_bytes()
    with pytest.raises(UserActionRequired, match="selection_reason"):
        save_issue(directory, {"id": issue["id"], "revision": issue["revision"], "selection_reason": reason})
    assert path.read_bytes() == before


def test_shared_change_saved_once_and_links_all_target_issues(archive):
    directory, _, _ = archive
    first = new_issue(archive)
    second = new_issue(archive, "Related symptom")
    for issue in (first, second):
        save_diagnosis(directory, issue["id"], "A shared cause and repair with individual verification.")
    change = save_change(
        directory,
        first["id"],
        {"status": "running", "target_issues": [first["id"], second["id"]], "before": {"git_head": "abc"}},
    )
    left = load_issue(directory, first["id"])["changes"]
    right = load_issue(directory, second["id"])["changes"]
    assert left == right
    assert left[0]["owner_issue"] == first["id"]
    assert not (directory / f"issues/{second['id']}/changes").exists()
    assert set(change["diagnoses"]) == {first["id"], second["id"]}


def test_verifications_remain_independent_and_do_not_rewrite_candidate(archive):
    directory, _, _ = archive
    issue, change = new_change(archive)
    change_bytes = Path(change["path"]).read_bytes()
    for outcome in ("fail", "pass"):
        running = save_verification(
            directory, issue["id"], change["id"], {"status": "running", "execution": {"command": "run agent"}}
        )
        result = save_verification(
            directory,
            issue["id"],
            change["id"],
            {
                "id": running["id"],
                "revision": running["revision"],
                "status": "completed",
                "execution": {"command": "run agent", "run_id": outcome},
                "candidate_loaded": True,
                "loading_evidence": ["Loaded artifact digest def from /agent/runtime."],
                "conclusions": {issue["id"]: {"outcome": outcome, "reason": "Observed output."}},
            },
        )
        validate_ref(directory, result["change"])
        with pytest.raises(UserActionRequired, match="immutable"):
            save_verification(
                directory,
                issue["id"],
                change["id"],
                {"id": result["id"], "revision": result["revision"], "status": "completed"},
            )
    assert Path(change["path"]).read_bytes() == change_bytes
    assert len(list((Path(change["path"]).parent / "verifications").glob("*/result.json"))) == 2
    assert load_issue(directory, issue["id"])["status"] == "open"


def test_missing_loading_is_inconclusive_and_new_identity_preserved(archive):
    directory, _, _ = archive
    issue, change = new_change(archive)
    running = save_verification(
        directory, issue["id"], change["id"], {"status": "running", "execution": {"command": "run agent"}}
    )
    data = {
        "id": running["id"],
        "revision": running["revision"],
        "status": "completed",
        "execution": {"command": "run agent", "session_id": "actual-new-session"},
        "candidate_loaded": False,
        "conclusions": {issue["id"]: {"outcome": "pass", "reason": "Different output."}},
    }
    with pytest.raises(UserActionRequired, match="inconclusive"):
        save_verification(directory, issue["id"], change["id"], data)
    data["conclusions"][issue["id"]]["outcome"] = "inconclusive"
    saved = save_verification(directory, issue["id"], change["id"], data)
    assert saved["execution"]["session_id"] == "actual-new-session"
    assert len(load_session(directory)["captures"]) == 1


def test_status_keeps_healthy_evidence_when_issue_is_damaged(archive):
    directory, _, _ = archive
    issue = new_issue(archive)
    Path(issue["path"]).write_text("damaged")
    result = status(directory)
    assert len(result["captures"]) == 1
    assert result["warnings"][0]["issue_id"] == issue["id"]


def test_mutations_refuse_unrecorded_changes_and_unrecorded_runs(archive):
    directory, _, _ = archive
    issue = new_issue(archive)
    save_diagnosis(directory, issue["id"], "Cause and plan")
    with pytest.raises(UserActionRequired, match="running change"):
        save_change(
            directory,
            issue["id"],
            {"status": "completed", "before": {"head": "abc"}, "candidate": {"head": "def"}, "diff": "+fix"},
        )
    running = save_change(directory, issue["id"], {"status": "running", "before": {"head": "abc"}})
    with pytest.raises(UserActionRequired, match="fixed candidate"):
        save_verification(directory, issue["id"], running["id"], {"status": "running", "execution": {}})


def test_valid_json_with_missing_issue_fields_cannot_hide_healthy_capture(archive):
    directory, _, _ = archive
    issue_path = directory / "issues/ISSUE-001/issue.json"
    issue_path.parent.mkdir(parents=True)
    issue_path.write_text('{"schema_version":1,"id":"ISSUE-001"}')
    result = status(directory)
    assert len(result["captures"]) == 1
    assert not result["issues"]
    assert result["warnings"][0]["issue_id"] == "ISSUE-001"
    with pytest.raises(UserActionRequired, match="structure"):
        load_issue(directory, "ISSUE-001")


def test_status_reads_all_verifications_without_changing_candidate(archive):
    directory, _, _ = archive
    issue, change = new_change(archive)
    run = save_verification(
        directory, issue["id"], change["id"], {"status": "running", "execution": {"command": "run"}}
    )
    result = status(directory)
    assert result["changes"][0]["id"] == change["id"]
    assert result["changes"][0]["verification_records"][0]["id"] == run["id"]
    assert not result["warnings"]
    Path(run["path"]).write_text('{"schema_version":1}')
    damaged = status(directory)
    assert len(damaged["captures"]) == 1
    assert len(damaged["issues"]) == 1
    assert damaged["warnings"][0]["verification_id"] == run["id"]


def test_load_change_rejects_damaged_nested_fields(archive):
    import json

    directory, _, _ = archive
    issue, change = new_change(archive)
    path = Path(change["path"])
    record = json.loads(path.read_text())
    record["diagnoses"] = "damaged"
    path.write_text(json.dumps(record))
    with pytest.raises(UserActionRequired, match="structure"):
        load_change(directory, issue["id"], change["id"])
    result = status(directory)
    assert result["captures"] and result["issues"]
    assert result["warnings"][0]["change_id"] == change["id"]


def test_concurrent_process_cannot_overwrite_same_revision(archive):
    import json
    import subprocess
    import sys

    from agent_tune_kit.traces.store import session_write_lock

    directory, _, _ = archive
    issue = new_issue(archive)
    pending = {"id": issue["id"], "revision": issue["revision"], "title": "Concurrent update"}
    script = """
import json
import sys
from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.traces.records import save_issue
try:
    save_issue(sys.argv[1], json.loads(sys.argv[2]))
except UserActionRequired as exc:
    print(exc)
    raise SystemExit(2)
"""
    command = [sys.executable, "-c", script, str(directory), json.dumps(pending)]
    with session_write_lock(directory):
        blocked = subprocess.run(command, capture_output=True, text=True, check=False)
    assert blocked.returncode == 2
    assert "write is in progress" in blocked.stdout
    save_issue(directory, {**pending, "title": "Authoritative update"})
    stale = subprocess.run(command, capture_output=True, text=True, check=False)
    assert stale.returncode == 2
    assert "revision" in stale.stdout
    assert load_issue(directory, issue["id"])["title"] == "Authoritative update"
