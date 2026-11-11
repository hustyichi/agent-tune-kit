import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote

import pytest

from agent_tune_kit.cli.main import main
from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.traces import capture as trace_capture
from agent_tune_kit.traces import categories
from agent_tune_kit.traces.records import (
    load_issue,
    save_change,
    save_detection,
    save_diagnosis,
    save_issue,
    save_verification,
)
from agent_tune_kit.traces.store import create_capture, evidence_ref, resolve_session, trace_root
from agent_tune_kit.visualize import trace as session_page
from agent_tune_kit.visualize import traces as dashboard

SOURCE = {"type": "local", "instance": "fixture", "project": "agent", "environment": "test"}
SECRET_BODY = "SECRET-TRACE-BODY-do-not-embed"
SHELL = {"id": "shell-arg-retry", "name": "Shell argument retries", "definition": "Tool calls retried after bad paths."}


def make_session(project, session_id, *, source=SOURCE):
    result = create_capture(
        project,
        session_id,
        source,
        {"trace.json": json.dumps({"sessionId": session_id, "output": SECRET_BODY})},
        completeness={"status": "partial", "gaps": ["Selection only."], "trace_count": 2, "observation_count": 9},
    )
    directory = Path(result["session_dir"])
    return directory, evidence_ref(directory, f"captures/{result['capture_id']}/raw/trace.json", "/output")


def make_v2_session(project, session_id):
    """A natively imported archive, whose directory is named after the escaped Session ID."""
    export = Path(project) / f"export-{abs(hash(session_id))}.json"
    export.write_text(
        json.dumps(
            {
                "trace": {
                    "id": "trace-1",
                    "sessionId": session_id,
                    "observations": [{"id": "obs-1", "traceId": "trace-1", "input": "hi", "output": SECRET_BODY}],
                },
                "export": {},
            }
        )
    )
    result = trace_capture.import_session(project, session_id, SOURCE, export, retention={"allow_body": True})
    export.unlink()
    directory = Path(result["session_dir"])
    return directory, evidence_ref(directory, "raw/trace-1.json")


def find(project, session_id):
    return resolve_session(project, session_id)


def revision(directory, issue_id):
    return load_issue(directory, issue_id)["revision"]


def add_issue(directory, ref, title="Wrong answer", **extra):
    return save_issue(
        directory, {"title": title, "description": "Output differs from the task.", "evidence": [ref], **extra}
    )


def add_diagnosis(directory, issue):
    return save_diagnosis(directory, issue["id"], "# Cause and plan\n\nA cause, a repair and a check.")


def add_verified_change(directory, issue, outcome="pass", targets=None):
    running = save_change(
        directory,
        issue["id"],
        {"status": "running", "before": {"git_head": "abc"}, **({"target_issues": targets} if targets else {})},
    )
    done = save_change(
        directory,
        issue["id"],
        {
            "id": running["id"],
            "revision": running["revision"],
            "status": "completed",
            "candidate": {"git_head": "def"},
            "diff": "+ fix",
            "change_summary": [{"what": "Select the greeting.", "why": "The branch ignored it."}],
            "remaining_verification": ["Confirm the greeting."],
        },
    )
    if outcome is None:
        return done
    started = save_verification(
        directory, issue["id"], done["id"], {"status": "running", "execution": {"command": "run"}}
    )
    conclusions = {target: {"outcome": outcome, "reason": "Observed."} for target in targets or [issue["id"]]}
    save_verification(
        directory,
        issue["id"],
        done["id"],
        {
            "id": started["id"],
            "revision": started["revision"],
            "status": "completed",
            "execution": {"command": "run"},
            "verification_summary": {"method": "Run it.", "sample_scope": "One case.", "observations": ["Seen."]},
            "limitations": [],
            "candidate_loaded": True,
            "loading_evidence": ["Loaded digest def."],
            "conclusions": conclusions,
        },
    )
    return done


def issue_map(data, session=None):
    return {
        (entry["session_id"], issue["id"]): issue
        for entry in data["sessions"]
        if session in (None, entry["session_id"])
        for issue in entry["issues"]
    }


@pytest.fixture
def project(tmp_path):
    first, ref = make_session(tmp_path, "session-a")
    pending = add_issue(first, ref, "No diagnosis yet")
    diagnosed = add_issue(first, ref, "Diagnosed only")
    add_diagnosis(first, diagnosed)
    verified = add_issue(first, ref, "Verified and passing")
    add_diagnosis(first, verified)
    add_verified_change(first, verified, "pass")
    second, other = make_session(tmp_path, "session-b")
    failing = add_issue(second, other, "Fails verification")
    add_diagnosis(second, failing)
    add_verified_change(second, failing, "fail")
    for session_id in ("session-a", "session-b"):
        session_page.generate_trace(tmp_path, session_id=session_id, open_browser=False)
    assert {pending["id"], diagnosed["id"], verified["id"], failing["id"]} == {"ISSUE-001", "ISSUE-002", "ISSUE-003"}
    return tmp_path


def test_progress_per_issue_is_derived_from_records_for_every_session(project):
    data = dashboard.build_dashboard_data(project)
    got = {key: issue["progress"] for key, issue in issue_map(data).items()}
    assert got == {
        ("session-a", "ISSUE-001"): "pending",
        ("session-a", "ISSUE-002"): "diagnosed",
        ("session-a", "ISSUE-003"): "pass",
        ("session-b", "ISSUE-001"): "fail",
    }
    summary = dashboard.summarize_dashboard(data)
    assert summary["sessions"] == 2 and summary["issues"] == 4 and summary["uncategorized"] == 4
    assert summary["categories"] == 0 and summary["unreadable_sessions"] == 0
    assert summary["progress"]["pending"] == 1 and summary["progress"]["fail"] == 1 and summary["progress"]["pass"] == 1
    assert sum(summary["progress"].values()) == 4


def test_session_and_dashboard_pages_report_the_same_progress(project):
    data = dashboard.build_dashboard_data(project)
    for entry in data["sessions"]:
        _, view = session_page.build_trace_data(project, session_id=entry["session_id"])
        assert {item["record"]["id"]: item["progress"]["key"] for item in view["issues"]} == {
            issue["id"]: issue["progress"] for issue in entry["issues"]
        }


def newest_verification(directory, issue_id):
    paths = sorted((directory / "issues" / issue_id / "changes").glob("*/verifications/*/result.json"))
    return max(paths, key=lambda path: json.loads(path.read_text())["created_at"])


def test_a_newer_unreadable_verification_is_unknown_on_both_pages(project):
    directory = find(project, "session-a")
    issue = load_issue(directory, "ISSUE-003")
    change_id = issue["changes"][0]["change_id"]
    started = save_verification(directory, "ISSUE-003", change_id, {"status": "running", "execution": {"x": 1}})
    record = newest_verification(directory, "ISSUE-003")
    assert started["id"] in str(record)
    # The newer attempt finishes as a failure but is then damaged: validation drops it from the record status.
    damaged = json.loads(record.read_text())
    damaged.update(status="completed", conclusions={"ISSUE-003": {"outcome": "fail", "reason": "x"}}, revision=0)
    record.write_text(json.dumps(damaged))
    data = dashboard.build_dashboard_data(project)
    _, view = session_page.build_trace_data(project, session_id="session-a")
    page = {item["record"]["id"]: item["progress"]["key"] for item in view["issues"]}
    # The session page can still read the record; the dashboard must not fall back to the older pass.
    assert page["ISSUE-003"] == "fail"
    assert issue_map(data, "session-a")[("session-a", "ISSUE-003")]["progress"] == "fail"
    record.write_text("{ not json")
    data = dashboard.build_dashboard_data(project)
    _, view = session_page.build_trace_data(project, session_id="session-a")
    page = {item["record"]["id"]: item["progress"]["key"] for item in view["issues"]}
    assert page["ISSUE-003"] == "inconclusive"
    assert issue_map(data, "session-a")[("session-a", "ISSUE-003")]["progress"] == "inconclusive"


def test_deeply_nested_record_damages_only_its_own_issue(project):
    healthy = find(project, "session-a")
    other = find(project, "session-b")
    (healthy / "issues/ISSUE-002/issue.json").write_text("[" * 200000)
    data = dashboard.build_dashboard_data(project)
    by_dir = {entry["dir"]: entry for entry in data["sessions"]}
    assert {issue["id"] for issue in by_dir[healthy.name]["issues"]} == {"ISSUE-001", "ISSUE-003"}
    assert by_dir[other.name]["issues"] and by_dir[healthy.name]["problem_count"] == 1


def test_shared_change_gives_each_target_issue_its_own_verification_result(tmp_path):
    directory, ref = make_session(tmp_path, "shared")
    left, right = add_issue(directory, ref, "Left"), add_issue(directory, ref, "Right")
    for issue in (left, right):
        add_diagnosis(directory, issue)
    running = save_change(
        directory,
        left["id"],
        {"status": "running", "before": {"git_head": "abc"}, "target_issues": [left["id"], right["id"]]},
    )
    done = save_change(
        directory,
        left["id"],
        {
            "id": running["id"],
            "revision": running["revision"],
            "status": "completed",
            "candidate": {"git_head": "def"},
            "diff": "+ fix",
            "change_summary": [{"what": "Shared fix.", "why": "One cause."}],
            "remaining_verification": ["Check both."],
        },
    )
    started = save_verification(directory, left["id"], done["id"], {"status": "running", "execution": {"c": 1}})
    save_verification(
        directory,
        left["id"],
        done["id"],
        {
            "id": started["id"],
            "revision": started["revision"],
            "status": "completed",
            "execution": {"c": 1},
            "verification_summary": {"method": "Run.", "sample_scope": "One.", "observations": ["Seen."]},
            "limitations": [],
            "candidate_loaded": True,
            "loading_evidence": ["loaded"],
            "conclusions": {
                left["id"]: {"outcome": "pass", "reason": "ok"},
                right["id"]: {"outcome": "fail", "reason": "still wrong"},
            },
        },
    )
    got = {
        issue["id"]: issue["progress"] for issue in dashboard.build_dashboard_data(tmp_path)["sessions"][0]["issues"]
    }
    assert got == {left["id"]: "pass", right["id"]: "fail"}


def test_pending_and_excluded_progress_and_unverified_change(tmp_path):
    directory, ref = make_session(tmp_path, "mixed")
    waiting = add_issue(directory, ref, "Changed, not verified")
    add_diagnosis(directory, waiting)
    add_verified_change(directory, waiting, None)
    withdrawn = add_issue(directory, ref, "Withdrawn")
    add_diagnosis(directory, withdrawn)
    save_issue(directory, {"id": withdrawn["id"], "revision": withdrawn["revision"] + 1, "status": "excluded"})
    got = {
        issue["id"]: issue["progress"] for issue in dashboard.build_dashboard_data(tmp_path)["sessions"][0]["issues"]
    }
    assert got == {waiting["id"]: "awaiting", withdrawn["id"]: "excluded"}


def test_categories_are_counted_and_unknown_ids_still_display(project):
    categories.save_category(project, SHELL)
    directory = find(project, "session-b")
    save_issue(directory, {"id": "ISSUE-001", "revision": revision(directory, "ISSUE-001"), "category": SHELL["id"]})
    data = dashboard.build_dashboard_data(project)
    assert data["categories"][SHELL["id"]]["name"] == SHELL["name"]
    assert issue_map(data)[("session-b", "ISSUE-001")]["category"] == SHELL["id"]
    assert dashboard.summarize_dashboard(data)["categories"] == 1
    # An issue that names a category the vocabulary no longer lists is shown by ID, never dropped.
    path = trace_root(project) / categories.FILENAME
    path.write_text(json.dumps({"schema_version": 1, "revision": 9, "categories": []}))
    data = dashboard.build_dashboard_data(project)
    assert issue_map(data)[("session-b", "ISSUE-001")]["category"] == SHELL["id"] and data["categories"] == {}
    path.write_text("{ not json")
    data = dashboard.build_dashboard_data(project)
    assert data["categories"] == {} and any("vocabulary" in warning for warning in data["warnings"])


def test_zero_issue_session_is_listed_with_only_its_latest_detection_status(tmp_path):
    directory, _ = make_v2_session(tmp_path, "quiet")
    entry = dashboard.build_dashboard_data(tmp_path)["sessions"][0]
    assert entry["issues"] == [] and entry["error"] is None and entry["detection"] is None
    save_detection(
        directory,
        {"status": "completed", "scope": {"focus": "checked the greeting"}, "gaps": ["Screenshots were not retained."]},
    )
    entry = dashboard.build_dashboard_data(tmp_path)["sessions"][0]
    # The dashboard only says whether the list can be trusted; focus, gaps, scale and capture coverage stay on the
    # session page, so they are not copied into the project-wide view.
    assert entry["detection"] == {"status": "completed"}
    for removed in ("traces", "observations", "files", "archived_ms", "capture"):
        assert removed not in entry
    assert entry["source"] == {key: SOURCE[key] for key in SOURCE}


def test_a_damaged_session_or_issue_never_hides_healthy_ones(project):
    healthy = find(project, "session-a")  # resolve before the damage: legacy lookup scans every sibling
    other = find(project, "session-b")
    broken = trace_root(project) / "broken"
    broken.mkdir()
    (broken / "session.json").write_text("{ not json")
    (healthy / "issues/ISSUE-002/issue.json").write_text("{ not json")
    data = dashboard.build_dashboard_data(project)
    by_dir = {entry["dir"]: entry for entry in data["sessions"]}
    assert by_dir["broken"]["error"] and by_dir["broken"]["issues"] == []
    assert str(project) not in by_dir["broken"]["error"]
    mine = by_dir[healthy.name]
    assert {issue["id"] for issue in mine["issues"]} == {"ISSUE-001", "ISSUE-003"}
    assert mine["problem_count"] == 1 and "ISSUE-002" in mine["problems"][0]
    assert by_dir[other.name]["issues"]
    assert dashboard.summarize_dashboard(data)["unreadable_sessions"] == 1


def test_preview_staleness_follows_the_newest_record(project):
    directory = find(project, "session-a")
    entry = next(e for e in dashboard.build_dashboard_data(project)["sessions"] if e["dir"] == directory.name)
    assert entry["preview"] == {"exists": True, "stale": False}
    old = (directory / "preview.html").stat().st_mtime - 3600
    os.utime(directory / "preview.html", (old, old))
    stale = next(e for e in dashboard.build_dashboard_data(project)["sessions"] if e["dir"] == directory.name)
    assert stale["preview"] == {"exists": True, "stale": True}
    (directory / "preview.html").unlink()
    missing = next(e for e in dashboard.build_dashboard_data(project)["sessions"] if e["dir"] == directory.name)
    assert missing["preview"] == {"exists": False, "stale": True}
    assert dashboard.summarize_dashboard(dashboard.build_dashboard_data(project))["stale_previews"] == 1


def import_timed(project, session_id, *times):
    rows = [{"id": f"obs-{i}", "traceId": "trace-1", "sessionId": session_id, **row} for i, row in enumerate(times)]
    export = Path(project) / f"{session_id}.json"
    export.write_text(json.dumps({"data": rows}))
    result = trace_capture.import_session(project, session_id, SOURCE, export, retention={"allow_body": True})
    export.unlink()
    return Path(result["session_dir"])


def test_sessions_show_and_sort_by_when_they_ran_not_when_they_were_processed(tmp_path):
    first = import_timed(
        tmp_path,
        "ran-first",
        {"startTime": "2026-11-06T05:00:00+00:00", "endTime": "2026-11-06T05:30:00+00:00"},
        {"startTime": "2026-11-06T04:55:00+00:00"},
    )
    import_timed(tmp_path, "ran-later", {"startTime": "2026-11-09T07:00:00+00:00"})
    import_timed(tmp_path, "untimed", {"startTime": "not-a-time"})
    # Processing the earlier session last must not move it ahead of the session that ran later.
    add_issue(first, evidence_ref(first, "raw/trace-1.json"), "Processed most recently")
    sessions = dashboard.build_dashboard_data(tmp_path)["sessions"]
    assert [e["session_id"] for e in sessions] == ["ran-later", "ran-first", "untimed"]
    by_id = {e["session_id"]: e for e in sessions}
    start = int(datetime(2026, 11, 6, 4, 55, tzinfo=UTC).timestamp() * 1000)
    end = int(datetime(2026, 11, 6, 5, 30, tzinfo=UTC).timestamp() * 1000)
    assert (by_id["ran-first"]["started_ms"], by_id["ran-first"]["ended_ms"]) == (start, end)
    assert by_id["untimed"]["started_ms"] is None and by_id["untimed"]["ended_ms"] is None
    for entry in sessions:
        assert "updated_ms" not in entry
        assert all("updated_ms" not in issue for issue in entry["issues"])


def test_links_use_the_escaped_archive_directory_name(tmp_path):
    directory, ref = make_v2_session(tmp_path, "weird id/with spaces#中文")
    add_issue(directory, ref)
    entry = dashboard.build_dashboard_data(tmp_path)["sessions"][0]
    assert entry["session_id"] == "weird id/with spaces#中文"
    assert directory.name != entry["session_id"] and "/" not in directory.name
    assert entry["href"] == f"./{quote(directory.name, safe='')}/preview.html"
    assert "/" not in entry["href"][2:].split("/preview.html")[0] and " " not in entry["href"]
    # The link must actually reach the file the session page writes.
    page = Path(
        session_page.generate_trace(tmp_path, session_id=entry["session_id"], open_browser=False)["output_path"]
    )
    assert (trace_root(tmp_path) / unquote(entry["href"][2:])).resolve() == page.resolve()


def test_missing_or_empty_archive_is_a_clear_error(tmp_path):
    with pytest.raises(UserActionRequired, match="No Trace archive"):
        dashboard.generate_traces(tmp_path, open_browser=False)
    trace_root(tmp_path).mkdir(parents=True)
    with pytest.raises(UserActionRequired, match="No archived sessions"):
        dashboard.generate_traces(tmp_path, open_browser=False)


def payload(html):
    match = re.search(r'<script id="dashboard-data" type="application/json">(.*?)</script>', html, re.S)
    assert match
    return json.loads(match[1])


def test_generated_page_is_safe_offline_and_leaves_every_record_untouched(project):
    before = {path: path.read_bytes() for path in trace_root(project).rglob("*") if path.is_file()}
    # Hostile record text must stay data: it may not close the script element or break out of it.
    directory = find(project, "session-b")
    save_issue(
        directory,
        {
            "id": "ISSUE-001",
            "revision": revision(directory, "ISSUE-001"),
            "title": "</script><img src=x onerror=alert(1)>& __DATA__ __APP__",
        },
    )
    before = {path: path.read_bytes() for path in trace_root(project).rglob("*") if path.is_file()}
    result = dashboard.generate_traces(project, open_browser=False)
    output = Path(result["output_path"])
    assert output == trace_root(project) / "index.html" and output.stat().st_size < 200_000
    html = output.read_text(encoding="utf-8")
    assert re.search(r'id="tab-category"[^>]*>问题</button>', html)
    assert "connect-src 'none'" in html and "default-src 'none'" in html
    assert SECRET_BODY not in html, "raw trace bodies must never be embedded"
    assert html.count("</script>") == 2 and "<img src=x" not in html
    assert payload(html)["sessions"][0]["issues"] or payload(html)["sessions"][1]["issues"]
    assert any("__DATA__ __APP__" in issue["title"] for entry in payload(html)["sessions"] for issue in entry["issues"])
    assert not re.search(r"__(STYLES|DATA|APP)__", html.replace("__DATA__ __APP__", "").replace("\\u2028", ""))
    assert {path: path.read_bytes() for path in before} == before
    assert {path for path in trace_root(project).rglob("*") if path.is_file()} == {*before, output}


def test_embedded_data_carries_only_safe_source_fields_and_no_credentials(tmp_path):
    source = {**SOURCE, "instance": "https://example.invalid"}
    directory, ref = make_session(tmp_path, "safe", source=source)
    add_issue(directory, ref)
    trace_root(tmp_path).joinpath("config.json").write_text(
        json.dumps({"credentials": {"secret_key_env": "LF_SECRET"}})
    )
    html = Path(dashboard.generate_traces(tmp_path, open_browser=False)["output_path"]).read_text(encoding="utf-8")
    assert "LF_SECRET" not in html and "credentials" not in html
    assert payload(html)["sessions"][0]["source"] == source


def test_overwrite_protection_failed_rebuild_and_symlink_refusal(project, monkeypatch):
    first = Path(dashboard.generate_traces(project, open_browser=False)["output_path"])
    original = first.read_bytes()
    with pytest.raises(UserActionRequired, match="already exists"):
        dashboard.generate_traces(project, open_browser=False)
    assert first.read_bytes() == original

    def fail(_data):
        raise ValueError("synthetic rendering error")

    monkeypatch.setattr(dashboard, "safe_json_for_html", fail)
    with pytest.raises(ValueError, match="synthetic rendering error"):
        dashboard.generate_traces(project, overwrite=True, open_browser=False)
    assert first.read_bytes() == original
    monkeypatch.undo()
    first.unlink()
    target = project / "elsewhere.html"
    target.write_text("keep")
    first.symlink_to(target)
    with pytest.raises(UserActionRequired, match="symlink"):
        dashboard.generate_traces(project, overwrite=True, open_browser=False)
    assert target.read_text() == "keep"


def test_open_failure_still_delivers_the_dashboard(project, monkeypatch):
    monkeypatch.setattr(dashboard, "open_in_browser", lambda _path: (False, "browser unavailable"))
    result = dashboard.generate_traces(project)
    assert Path(result["output_path"]).is_file() and result["opened"] is False
    assert any("browser unavailable" in warning for warning in result["warnings"])


def test_a_session_preview_cannot_be_redirected_over_the_dashboard(project):
    dashboard.generate_traces(project, open_browser=False)
    with pytest.raises(UserActionRequired, match="protect saved evidence"):
        session_page.generate_trace(
            project,
            session_id="session-a",
            output_path=trace_root(project) / "index.html",
            overwrite=True,
            open_browser=False,
        )


def test_session_ids_cannot_collide_with_the_dashboard_or_vocabulary_files(tmp_path):
    for reserved in ("index.html", "INDEX.HTML", "categories.json"):
        directory, _ = make_v2_session(tmp_path, reserved)
        assert directory.name != reserved and directory.name.casefold() not in {"index.html", "categories.json"}
    dashboard.generate_traces(tmp_path, open_browser=False)
    assert len(dashboard.build_dashboard_data(tmp_path)["sessions"]) == 3


def test_session_page_links_back_only_when_the_dashboard_exists(project):
    def view():
        _, data = session_page.build_trace_data(project, session_id="session-a")
        return data

    generated = session_page.generate_trace(project, session_id="session-a", overwrite=True, open_browser=False)
    assert (
        json.loads(
            re.search(
                r'id="trace-data" type="application/json">(.*?)</script>',
                Path(generated["output_path"]).read_text(),
                re.S,
            )[1]
        )["dashboard"]["available"]
        is False
    )
    dashboard.generate_traces(project, open_browser=False)
    generated = session_page.generate_trace(project, session_id="session-a", overwrite=True, open_browser=False)
    embedded = json.loads(
        re.search(
            r'id="trace-data" type="application/json">(.*?)</script>', Path(generated["output_path"]).read_text(), re.S
        )[1]
    )
    assert embedded["dashboard"] == {"available": True, "session": Path(generated["output_path"]).parent.name}
    # A preview written somewhere else has no ../index.html beside it, so it must not claim one.
    elsewhere = project / "elsewhere" / "copy.html"
    elsewhere.parent.mkdir()
    other = session_page.generate_trace(project, session_id="session-a", output_path=elsewhere, open_browser=False)
    assert (
        json.loads(
            re.search(
                r'id="trace-data" type="application/json">(.*?)</script>', Path(other["output_path"]).read_text(), re.S
            )[1]
        )["dashboard"]["available"]
        is False
    )
    assert view()["dashboard"]["available"] is False


def test_cli_command_generates_and_reports_a_summary(project, monkeypatch, capsys):
    monkeypatch.chdir(project)
    assert main(["visualize", "traces", "--no-open", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["sessions"] == 2 and summary["issues"] == 4 and summary["opened"] is False
    assert summary["progress"]["diagnosed"] == 1 and summary["uncategorized"] == 4
    assert summary["projects"] == [], "no issue links a project, so none is reported"
    assert main(["visualize", "traces", "--no-open", "--json"]) == 2  # exists, so --overwrite is required
    assert "--overwrite" in capsys.readouterr().err
    assert main(["visualize", "traces", "--no-open", "--overwrite", "--json"]) == 0


def test_dashboard_script_renders_both_views_filters_and_deep_links(project, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the dependency-free renderer check")
    # The script runs against the real generator's data: categories spanning two sessions, an uncategorized issue
    # in each, and a hostile title that must stay literal text.
    categories.save_category(project, SHELL)
    first, second = find(project, "session-a"), find(project, "session-b")
    for directory, issue_id, extra in (
        (first, "ISSUE-002", {"category": SHELL["id"]}),
        (second, "ISSUE-001", {"category": SHELL["id"]}),
        (first, "ISSUE-001", {"title": "</script><img src=x onerror=alert(1)>"}),
    ):
        save_issue(directory, {"id": issue_id, "revision": revision(directory, issue_id), **extra})
    page = Path(dashboard.generate_traces(project, open_browser=False)["output_path"]).read_text(encoding="utf-8")
    assert 'id="session-attention"' in page and 'aria-label="处理状态"' in page
    assert 'id="progress-bar"' not in page and 'id="view-overview"' not in page
    data = tmp_path / "dashboard-data.json"
    data.write_text(json.dumps(payload(page)), encoding="utf-8")
    subprocess.run([node, str(Path(__file__).with_name("trace-dashboard.cjs")), str(data)], check=True)
