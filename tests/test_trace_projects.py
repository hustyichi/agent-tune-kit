from pathlib import Path

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.traces.projects import discover_projects, load_projects, root_projects, save_project
from agent_tune_kit.traces.records import load_issue, save_diagnosis, save_issue
from agent_tune_kit.traces.store import create_capture, evidence_ref, trace_root


def test_registry_boundaries_and_discovery(tmp_path):
    (tmp_path / ".gitmodules").write_text('[submodule "engine"]\npath = engine\n')
    candidates = discover_projects(tmp_path)["projects"]
    assert len(candidates) == 2 and not candidates[1]["readable"]
    save_project(tmp_path, {"id": "ape", "name": "Engine", "path": "engine"})
    with pytest.raises(UserActionRequired):
        save_project(tmp_path, {"id": "outside", "name": "Outside", "path": "../outside", "revision": 1})
    with pytest.raises(UserActionRequired):
        save_project(tmp_path, {"id": "ape", "parent_id": "ape", "revision": 1})
    assert load_projects(trace_root(tmp_path))["revision"] == 1


def test_attribution_history_snapshot_and_root_only(tmp_path):
    save_project(tmp_path, {"id": "ape", "name": "APE", "path": "ape"})
    save_project(tmp_path, {"id": "ot", "name": "OT", "path": "ot", "revision": 1})
    captured = create_capture(
        tmp_path,
        "one",
        {"type": "local", "instance": "test", "project": "source"},
        {"trace.json": '{"output":"wrong"}'},
    )
    directory = Path(captured["session_dir"])
    ref = evidence_ref(directory, f"captures/{captured['capture_id']}/raw/trace.json")
    links = [
        {
            "project_id": project,
            "role": role,
            "evidence_state": "hypothesis",
            "reason": "Observed failure",
            "evidence": [ref],
        }
        for project, role in [("ape", "root_cause"), ("ot", "improvement")]
    ]
    issue = save_issue(
        directory, {"title": "Failure", "description": "Failure details", "evidence": [ref], "project_links": links}
    )
    assert [p["id"] for p in root_projects(directory.parent, issue)] == ["ape"]
    issue = save_issue(directory, {"id": issue["id"], "revision": issue["revision"], "title": "Updated"})
    assert issue["project_links"] == links
    with pytest.raises(UserActionRequired):
        save_diagnosis(directory, issue["id"], "Cause and plan", expected_revision=1)
    diagnosis = save_diagnosis(directory, issue["id"], "Cause and plan", expected_revision=issue["revision"])
    frozen = Path(diagnosis["path"]).read_bytes()
    issue = load_issue(directory, issue["id"])
    save_issue(directory, {"id": issue["id"], "revision": issue["revision"], "project_links": []})
    assert Path(diagnosis["path"]).read_bytes() == frozen
    assert b"project_registry" in frozen
