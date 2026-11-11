import json
from pathlib import Path

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.traces.projects import (
    discover_projects,
    linked_projects,
    load_projects,
    project_index,
    save_project,
    validate_links,
)
from agent_tune_kit.traces.records import load_issue, save_diagnosis, save_issue
from agent_tune_kit.traces.store import create_capture, evidence_ref, load_session, trace_root


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


def test_project_links_survive_updates_and_diagnosis_snapshot(tmp_path):
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
            "reason": "The missing input check plausibly explains the failure; historical loading is unverified.",
            "evidence": [ref],
        }
        for project in ["ape", "ot"]
    ]
    issue = save_issue(
        directory, {"title": "Failure", "description": "Failure details", "evidence": [ref], "project_links": links}
    )
    assert [p["id"] for p in linked_projects(directory.parent, issue)] == ["ape", "ot"]
    issue = save_issue(directory, {"id": issue["id"], "revision": issue["revision"], "title": "Updated"})
    assert issue["project_links"] == links
    session = load_session(directory)
    assert "project_links" not in session and "linked_projects" not in session
    assert session["source"]["project"] == "source"
    from agent_tune_kit.visualize.traces import build_dashboard_data

    summary = build_dashboard_data(tmp_path)["sessions"][0]
    assert "linked_projects" not in summary and "project_links" not in summary
    assert [p["id"] for p in summary["issues"][0]["linked_projects"]] == ["ape", "ot"]
    from agent_tune_kit.visualize.trace import build_trace_data

    _, preview = build_trace_data(tmp_path, session_id="one")
    assert preview["issues"][0]["linked_projects"] == summary["issues"][0]["linked_projects"]
    # Pages carry only what they show and search; the registry's paths and entrypoints stay out of them.
    assert summary["issues"][0]["linked_projects"][0] == {"id": "ape", "name": "APE", "aliases": []}
    assert "projects" not in build_dashboard_data(tmp_path) and "projects" not in preview
    assert [(p["id"], p["issues"]) for p in preview["project_index"]] == [("ape", 1), ("ot", 1)]
    assert "sessions" not in preview["project_index"][0]
    with pytest.raises(UserActionRequired):
        save_diagnosis(directory, issue["id"], "Cause and plan", expected_revision=1)
    diagnosis = save_diagnosis(directory, issue["id"], "Cause and plan", expected_revision=issue["revision"])
    frozen = Path(diagnosis["path"]).read_bytes()
    snapshot = json.loads(frozen.decode().split("\n")[0].removeprefix("<!-- atk-issue-snapshot ").removesuffix(" -->"))
    assert snapshot["project_links"] == links
    issue = load_issue(directory, issue["id"])
    save_issue(directory, {"id": issue["id"], "revision": issue["revision"], "project_links": []})
    assert Path(diagnosis["path"]).read_bytes() == frozen
    assert b"project_registry" in frozen


@pytest.mark.parametrize(
    "invalid",
    [
        {"project_id": "unknown"},
        {"reason": " "},
        {"evidence": []},
        {"source_paths": ["../outside"]},
        {"role": "root_cause"},
        {"evidence_state": "supported"},
    ],
)
def test_link_validation_preserves_identity_reason_and_evidence_boundaries(tmp_path, invalid):
    save_project(tmp_path, {"id": "engine", "name": "Engine", "path": "engine"})
    captured = create_capture(
        tmp_path,
        "one",
        {"type": "local", "instance": "test", "project": "source"},
        {"trace.json": '{"output":"wrong"}'},
    )
    directory = Path(captured["session_dir"])
    ref = evidence_ref(directory, f"captures/{captured['capture_id']}/raw/trace.json")
    link = {"project_id": "engine", "reason": "Missing input validation explains the error.", "evidence": [ref]}
    with pytest.raises(UserActionRequired):
        validate_links(directory, [{**link, **invalid}])
    with pytest.raises(UserActionRequired, match="duplicate"):
        validate_links(directory, [link, link])


def test_project_index_offers_only_linked_projects_and_keeps_unregistered_ids(tmp_path):
    save_project(tmp_path, {"id": "ape", "name": "Engine", "abbreviation": "APE", "path": "engine", "aliases": ["eng"]})
    save_project(tmp_path, {"id": "idle", "name": "Idle", "path": "idle", "revision": 1})
    save_project(tmp_path, {"id": "old", "name": "Old", "path": "old", "deprecated": True, "revision": 2})
    root = trace_root(tmp_path)
    issue = {
        "project_links": [
            {"project_id": "ape"},
            {"project_id": "ape"},
            {"project_id": "gone"},
            {"project_id": "old"},
            "broken",
        ]
    }
    linked = linked_projects(root, issue)
    assert linked == [
        {"id": "ape", "name": "APE", "aliases": ["eng"]},
        {"id": "gone", "name": "gone", "aliases": []},
        {"id": "old", "name": "Old", "aliases": []},
    ]
    other = linked_projects(root, {"project_links": [{"project_id": "ape"}]})
    index = project_index(root, [("s1", linked), ("s1", other), ("s2", other), ("s2", [])])
    assert [(p["id"], p["issues"], p["sessions"], p["registered"], p["deprecated"]) for p in index] == [
        ("ape", 3, 2, True, False),
        ("gone", 1, 1, False, False),
        ("old", 1, 1, True, True),
    ], "no idle project; unregistered and deprecated entries go last"


def test_project_index_tells_shared_abbreviations_apart(tmp_path):
    save_project(tmp_path, {"id": "a", "name": "Toolkit", "abbreviation": "TK", "path": "official/toolkit"})
    save_project(tmp_path, {"id": "b", "name": "Toolkit", "abbreviation": "TK", "path": "labs", "revision": 1})
    root = trace_root(tmp_path)
    linked = linked_projects(root, {"project_links": [{"project_id": "a"}, {"project_id": "b"}]})
    assert sorted(p["name"] for p in project_index(root, [("s", linked)])) == ["TK · labs", "TK · toolkit"]
