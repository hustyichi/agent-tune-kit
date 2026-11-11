import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.evidence import write_manifest
from agent_tune_kit.traces import capture as trace_capture
from agent_tune_kit.traces import query, records, store
from agent_tune_kit.visualize import trace

SOURCE = {"type": "local", "instance": "synthetic", "project": "demo", "environment": "test"}
SESSION_ID = "SESSION-synthetic"


def test_event_details_show_complete_payloads_and_inline_evidence():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the dependency-free renderer check")
    subprocess.run([node, str(Path(__file__).with_name("trace-event-detail.cjs"))], check=True)


def test_stage_cards_render_concise_evidence_without_raw_records():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the dependency-free renderer check")
    subprocess.run([node, str(Path(__file__).with_name("trace-stage-cards.cjs"))], check=True)


def test_session_project_filter_and_links():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the dependency-free renderer check")
    subprocess.run([node, str(Path(__file__).with_name("trace-projects.cjs"))], check=True)


def test_record_order_uses_instants_not_random_ids_or_timezone_strings():
    entries = [
        {"record": {"id": "VER-a", "created_at": "2026-01-01T00:30:00Z"}},
        {"record": {"id": "VER-b", "created_at": "unknown"}},
        {"record": {"id": "VER-z", "created_at": "2026-01-01T08:00:00+08:00"}},
    ]
    assert [item["record"]["id"] for item in sorted(entries, key=trace._record_time)] == ["VER-z", "VER-a", "VER-b"]


def capture(project, capture_id="CAP-001", *, observations=2):
    document = {
        "trace": {
            "id": "TRACE-1",
            "sessionId": SESSION_ID,
            "input": "Return the user's greeting",
            "output": "</script><img src=x onerror=alert(1)>",
            "observations": [
                {
                    "id": f"OBS-{index}",
                    "type": "GENERATION",
                    "parentObservationId": "missing" if index == 0 else "OBS-0",
                    "name": f"Model {index}",
                    "input": {"messages": ["hello"]},
                    "output": "hello",
                }
                for index in range(observations)
            ],
        },
        "export": {"scope": "selected trace only"},
    }
    return store.create_capture(
        project,
        SESSION_ID,
        SOURCE,
        {"trace.json": json.dumps(document)},
        scope={"format": "langfuse-export", "selected_traces": ["TRACE-1"]},
        completeness={"status": "partial", "gaps": ["Session completeness is unknown."]},
        capture_id=capture_id,
    )


def payload(html):
    match = re.search(r'<script id="trace-data" type="application/json">(.*?)</script>', html, re.S)
    assert match
    return json.loads(match[1])


def digests(directory):
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file() and path.name != "preview.html"
    }


def test_capture_only_preview_is_safe_offline_and_read_only(tmp_path):
    result = capture(tmp_path, observations=70)
    session_dir = Path(result["session_dir"])
    project_context = "Project notes are not part of the session preview."
    project_path = session_dir.parent / "project.md"
    project_path.write_text(project_context)
    before = digests(session_dir)
    generated = trace.generate_trace(tmp_path, session_id=SESSION_ID, open_browser=False)
    html = Path(generated["output_path"]).read_text()
    data = payload(html)
    assert data["issues"] == []
    assert len(data["captures"][0]["nodes"]) == 71
    assert data["captures"][0]["manifest"]["completeness"]["status"] == "partial"
    assert "</script><img src=x onerror=alert(1)>" not in html
    assert data["captures"][0]["nodes"][0]["output"] == "</script><img src=x onerror=alert(1)>"
    assert "connect-src 'none'" in html
    assert not re.search(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://', html)
    assert "fetch(" not in html and "XMLHttpRequest" not in html
    assert "session-info" not in html and "showSessionInfo" not in html
    assert not {"project", "source", "record_digests"} & data.keys()
    assert project_context not in html
    assert project_path.read_text() == project_context
    assert digests(session_dir) == before
    assert not (tmp_path / ".atk/results").exists()


def test_diagnosis_preview_removes_only_the_generated_snapshot(tmp_path):
    session_dir = Path(capture(tmp_path)["session_dir"])
    evidence = store.evidence_ref(session_dir, "captures/CAP-001/raw/trace.json", "/trace/output")
    records.save_issue(
        session_dir, {"id": "ISSUE-001", "title": "Greeting", "description": "Mismatch", "evidence": [evidence]}
    )
    body = "# Finding\n\n<!-- atk-issue-snapshot {} -->\n\n**Repair:** Preserve punctuation."
    saved = records.save_diagnosis(session_dir, "ISSUE-001", body)
    issue_path = session_dir / "issues/ISSUE-001/issue.json"
    issue = records.load_issue(session_dir, "ISSUE-001")
    legacy_path = "issues/ISSUE-001/diagnoses/DIA-legacy.md"
    legacy = "# Legacy finding\n\nPlain Markdown without a snapshot."
    (session_dir / legacy_path).write_text(legacy, encoding="utf-8")
    legacy_ref = store.evidence_ref(session_dir, legacy_path)
    issue["diagnoses"].append(legacy_ref)
    write_manifest(issue_path, issue)

    before = digests(session_dir)
    output = trace.generate_trace(tmp_path, session_id=SESSION_ID, open_browser=False)
    data = payload(Path(output["output_path"]).read_text())
    diagnoses = data["issues"][0]["diagnoses"]
    assert diagnoses[0].pop("snapshot")["project_links"] == []
    assert diagnoses == [
        {"reference": saved["ref"], "text": body},
        {"reference": legacy_ref, "text": legacy},
    ]
    assert digests(session_dir) == before


def test_preview_keeps_selection_reason_as_data_and_supports_legacy_issue(tmp_path):
    session_dir = Path(capture(tmp_path)["session_dir"])
    evidence = store.evidence_ref(session_dir, "captures/CAP-001/raw/trace.json", "/trace/output")
    reason = 'Repeated failure; </script><img src=x onerror="alert(1)">'
    records.save_issue(
        session_dir,
        {"title": "Selected", "description": "Observed failure", "selection_reason": reason, "evidence": [evidence]},
    )
    records.save_issue(session_dir, {"title": "Legacy", "description": "Older issue", "evidence": [evidence]})
    before = digests(session_dir)
    result = trace.generate_trace(tmp_path, session_id=SESSION_ID, open_browser=False)
    html = Path(result["output_path"]).read_text()
    issues = payload(html)["issues"]
    assert issues[0]["record"]["selection_reason"] == reason
    assert "selection_reason" not in issues[1]["record"]
    assert reason not in html
    assert digests(session_dir) == before


def test_diagnosis_source_links_stay_within_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    session_dir = Path(capture(project)["session_dir"])
    evidence = store.evidence_ref(session_dir, "captures/CAP-001/raw/trace.json", "/trace/output")
    records.save_issue(
        session_dir, {"id": "ISSUE-001", "title": "Greeting", "description": "Mismatch", "evidence": [evidence]}
    )
    source = project / "src" / "file (trace) #1.py"
    source.parent.mkdir()
    source.write_text("# source", encoding="utf-8")
    outside = tmp_path / "private.py"
    outside.write_text("# private", encoding="utf-8")
    (project / "escape.py").symlink_to(outside)
    (project / ".private.py").write_text("# private", encoding="utf-8")
    relative = quote(source.relative_to(project).as_posix(), safe="/") + "#L2"
    legacy = quote(source.as_posix(), safe="/") + ":2"
    body = f"""## Issue description

Mismatch.

## Root cause

[Current]({relative}) [Legacy]({legacy})
[Escaped](escape.py#L2) [Hidden](.private.py#L2) [Missing](missing.py#L2)
[Outside](../private.py#L2) [Remote](//example.com/a.py#L2) [Scheme](file:///private.py)

## Minimal fix

Preserve punctuation.
"""
    records.save_diagnosis(session_dir, "ISSUE-001", body)
    changed_file = project / "src" / "changed.py"
    changed_file.write_text("# changed", encoding="utf-8")
    records.save_change(
        session_dir,
        "ISSUE-001",
        {
            "status": "running",
            "before": {"commit": "base"},
            "change_summary": [
                {
                    "what": "Update greeting selection.",
                    "why": "The original selector ignored the request.",
                    "files": [
                        "src/changed.py#L1",
                        relative,
                        "escape.py",
                        "../private.py",
                        ".private.py",
                        "missing.py",
                        "https://example.com",
                    ],
                }
            ],
        },
    )
    output = project / "review" / "preview.html"
    trace.generate_trace(project, session_id=SESSION_ID, output_path=output, open_browser=False)
    links = payload(output.read_text())["source_links"]
    assert links == {relative: f"../{relative}", legacy: f"../{relative}", "src/changed.py#L1": "../src/changed.py#L1"}


def test_all_issue_stages_and_shared_change_show_each_verification(tmp_path):
    result = capture(tmp_path)
    session_dir = Path(result["session_dir"])
    evidence = store.evidence_ref(session_dir, "captures/CAP-001/raw/trace.json", "/trace/output")
    for issue_id in ("ISSUE-001", "ISSUE-002"):
        records.save_issue(
            session_dir,
            {
                "id": issue_id,
                "title": "Unexpected greeting",
                "description": "The output does not match the requested greeting.",
                "evidence": [evidence],
            },
        )
        records.save_diagnosis(
            session_dir,
            issue_id,
            "# Evidence and plan\n\nInspect greeting selection; preserve punctuation.",
            diagnosis_id="DIA-001",
        )
    change = records.save_change(
        session_dir,
        "ISSUE-001",
        {
            "id": "CHG-001",
            "status": "running",
            "target_issues": ["ISSUE-001", "ISSUE-002"],
            "before": {"commit": "synthetic-base"},
        },
    )
    records.save_change(
        session_dir,
        "ISSUE-001",
        {
            "id": "CHG-001",
            "revision": change["revision"],
            "status": "completed",
            "candidate": {"commit": "synthetic-candidate"},
            "diff": {"summary": "Use the requested greeting"},
            "change_summary": [{"what": "Use the requested greeting", "why": "Request selection was ignored"}],
            "remaining_verification": ["Return the requested greeting."],
        },
    )
    for number, outcome in ((2, "fail"), (1, "pass")):
        verification_id = f"VER-{number:03}"
        started = records.save_verification(
            session_dir,
            "ISSUE-001",
            "CHG-001",
            {"id": verification_id, "status": "running", "execution": {"command": "synthetic-test"}},
        )
        records.save_verification(
            session_dir,
            "ISSUE-001",
            "CHG-001",
            {
                "id": verification_id,
                "revision": started["revision"],
                "status": "completed",
                "candidate_loaded": True,
                "loading_evidence": ["synthetic-candidate was loaded"],
                "verification_summary": {
                    "method": "Run both issue cases with the fixed candidate.",
                    "sample_scope": "Two new cases; no prior samples included.",
                    "observations": [f"Both cases {outcome}."],
                },
                "limitations": [],
                "conclusions": {
                    issue: {"outcome": outcome, "reason": "synthetic test result"}
                    for issue in ("ISSUE-001", "ISSUE-002")
                },
            },
        )
    records.save_detection(
        session_dir,
        {"id": "DET-001", "status": "completed", "captures": ["CAP-001"], "scope": "Selected synthetic observations"},
    )
    before = digests(session_dir)
    generated = trace.generate_trace(tmp_path, session_id=SESSION_ID, open_browser=False)
    data = payload(Path(generated["output_path"]).read_text())
    assert len(data["issues"]) == 2
    for issue in data["issues"]:
        assert len(issue["diagnoses"]) == 1
        assert len(issue["changes"]) == 1
        assert len(issue["changes"][0]["verifications"]) == 2
        assert [
            item["record"]["conclusions"]["ISSUE-001"]["outcome"] for item in issue["changes"][0]["verifications"]
        ] == ["fail", "pass"]
    assert data["issues"][0]["changes"][0]["reference"] == data["issues"][1]["changes"][0]["reference"]
    assert data["warnings"] == []
    assert digests(session_dir) == before


def test_missing_reference_and_damaged_capture_keep_healthy_evidence_visible(tmp_path):
    first = capture(tmp_path)
    session_dir = Path(first["session_dir"])
    evidence = store.evidence_ref(session_dir, "captures/CAP-001/raw/trace.json", "/trace/output")
    records.save_issue(
        session_dir, {"title": "Question mismatch", "description": "A synthetic issue", "evidence": [evidence]}
    )
    capture(tmp_path, "CAP-002")
    (session_dir / "captures/CAP-001/raw/trace.json").write_text("changed")
    generated = trace.generate_trace(tmp_path, session_id=SESSION_ID, open_browser=False)
    data = payload(Path(generated["output_path"]).read_text())
    assert data["captures"][0]["error"]
    assert data["captures"][1]["nodes"]
    assert data["issues"][0]["record"]["title"] == "Question mismatch"
    assert any(item.get("error") for item in data["references"].values())
    assert data["warnings"]


def test_overwrite_protection_and_failed_rebuild_preserve_old_page(tmp_path, monkeypatch):
    result = capture(tmp_path)
    generated = trace.generate_trace(tmp_path, session_id=SESSION_ID, open_browser=False)
    output = Path(generated["output_path"])
    original = output.read_bytes()
    with pytest.raises(UserActionRequired, match="already exists"):
        trace.generate_trace(tmp_path, session_id=SESSION_ID, open_browser=False)

    def fail(_data):
        raise ValueError("synthetic rendering error")

    monkeypatch.setattr(trace, "safe_json_for_html", fail)
    with pytest.raises(ValueError, match="synthetic rendering error"):
        trace.generate_trace(tmp_path, session_id=SESSION_ID, overwrite=True, open_browser=False)
    assert output.read_bytes() == original
    protected = Path(result["session_dir"]) / "captures/CAP-001/raw/evidence.html"
    with pytest.raises(UserActionRequired, match="protect saved evidence"):
        trace.generate_trace(tmp_path, session_id=SESSION_ID, output_path=protected, overwrite=True, open_browser=False)


def test_open_failure_still_delivers_preview(tmp_path, monkeypatch):
    capture(tmp_path)
    monkeypatch.setattr(trace, "open_in_browser", lambda _path: (False, "browser unavailable"))
    result = trace.generate_trace(tmp_path, session_id=SESSION_ID)
    assert Path(result["output_path"]).is_file()
    assert not result["opened"]
    assert any("browser unavailable" in warning for warning in result["warnings"])


def test_jsonl_references_keep_line_identity():
    assert trace._reference_key({"path": "events.jsonl", "sha256": "x", "line": 1}) != trace._reference_key(
        {"path": "events.jsonl", "sha256": "x", "line": 2}
    )


def test_issue_fields_locate_the_original_capture_node_and_keep_exact_preview(tmp_path):
    session_dir = Path(capture(tmp_path, observations=11)["session_dir"])
    capture(tmp_path, "CAP-002", observations=11)
    path = "captures/CAP-001/raw/trace.json"
    field = store.evidence_ref(session_dir, path, "/trace/observations/10/input/messages/0")
    root_field = store.evidence_ref(session_dir, path, "/trace/output")
    file_ref = store.evidence_ref(session_dir, path)
    old = store.evidence_ref(session_dir, path, "/trace/observations/1/output")
    records.save_issue(session_dir, {"id": "ISSUE-001", "title": "Old", "description": "Old", "evidence": [old]})
    records.save_issue(session_dir, {"id": "ISSUE-001", "revision": 1, "evidence": [field, root_field, file_ref]})
    records.save_issue(session_dir, {"id": "ISSUE-002", "title": "Also", "description": "Also", "evidence": [field]})

    _, data = trace.build_trace_data(tmp_path, session_id=SESSION_ID)
    saved = data["references"]
    located = saved[trace._reference_key(field)]
    assert json.loads(located["preview"]["text"]) == "hello"
    assert located["location"] == {"capture_id": "CAP-001", "node_index": 11, "field": "/input/messages/0"}
    assert saved[trace._reference_key(root_field)]["location"]["node_index"] == 0
    assert saved[trace._reference_key(file_ref)]["capture_id"] == "CAP-001"
    assert "location" not in saved[trace._reference_key(file_ref)]
    first, second = data["captures"]
    assert first["nodes"][11]["issue_ids"] == ["ISSUE-001", "ISSUE-002"]
    assert first["nodes"][0]["issue_ids"] == ["ISSUE-001"]
    assert "issue_ids" not in first["nodes"][2]  # Superseded issue evidence is still viewable in history.
    assert all("issue_ids" not in node for node in second["nodes"])


def test_jsonl_issue_location_distinguishes_the_line_from_the_file(tmp_path):
    events = "\n".join(json.dumps({"event_id": i, "level": "ERROR"}) for i in (1, 2))
    session_dir = Path(
        store.create_capture(
            tmp_path,
            SESSION_ID,
            SOURCE,
            {"events.jsonl": events},
            scope={},
            completeness={},
            capture_id="CAP-lines",
        )["session_dir"]
    )
    file_ref = store.evidence_ref(session_dir, "captures/CAP-lines/raw/events.jsonl")
    line_ref = {**file_ref, "line": 2}
    records.save_issue(
        session_dir, {"title": "On line two", "description": "Example", "evidence": [line_ref, file_ref]}
    )
    _, data = trace.build_trace_data(tmp_path, session_id=SESSION_ID)
    assert data["references"][trace._reference_key(line_ref)]["location"] == {
        "capture_id": "CAP-lines",
        "node_index": 1,
        "field": "",
    }
    assert "location" not in data["references"][trace._reference_key(file_ref)]
    first, second = data["captures"][0]["nodes"]
    assert first["level"] == "ERROR" and "issue_ids" not in first
    assert len(second["issue_ids"]) == 1


def test_session_timeline_interleaves_traces_and_links_only_explicit_child_work(tmp_path):
    def observation(identity, clock, **extra):
        return {"id": identity, "name": identity, "type": "EVENT", "startTime": "2026-01-01T" + clock + "Z", **extra}

    traces = {
        "late": [
            observation("ordinary-agent", "12:00:03", type="AGENT"),
            observation("delegated", "12:00:01", parent_tool_use_id="dispatch-A"),
            observation("child", "12:00:02", parentObservationId="delegated"),
            observation("backwards", "12:00:04", endTime="2026-01-01T12:00:00Z"),
        ],
        "early": [
            observation(
                "result",
                "12:00:02.001",
                name="frontend.tool_result",
                output={"type": "tool_result", "tool_use_id": "call"},
            ),
            observation(
                "request", "12:00:00", name="frontend.tool_use", output={"type": "tool_use", "tool_use_id": "call"}
            ),
        ],
        "empty": [],
    }
    files = {
        key + ".json": json.dumps({"trace": {"id": key, "name": "same name", "observations": rows}})
        for key, rows in traces.items()
    }
    store.create_capture(tmp_path, SESSION_ID, SOURCE, files, scope={}, completeness={}, capture_id="CAP-timeline")
    _, data = trace.build_trace_data(tmp_path, session_id=SESSION_ID)
    cap = data["captures"][0]
    nodes, timeline = cap["nodes"], cap["timeline"]
    tracks = timeline["tracks"]
    assert [t["trace_id"] for t in tracks] == ["early", "late", "empty"]
    assert tracks[1]["start"] < tracks[0]["end"]  # The two executions overlap on the same axis.
    ordered = [nodes[i] for i in timeline["order"]]
    assert [n["id"] for n in ordered] == [
        "request",
        "delegated",
        "child",
        "result",
        "ordinary-agent",
        "backwards",
        "empty",
    ]
    assert [n["id"] for n in ordered if n.get("branch") == "dispatch-A"] == ["delegated", "child"]
    assert nodes[ordered[0]["paired"]] is ordered[3]
    assert nodes[ordered[3]["paired"]] is ordered[0]
    assert ordered[-2]["at"] is None and ordered[-1]["at"] is None


def test_local_timestamps_keep_order_segments_and_preview(tmp_path):
    start = datetime(2026, 11, 6, 5, 37, 39, 580000)
    middle = datetime(2026, 11, 6, 5, 37, 40, 580000).astimezone(UTC).isoformat()
    rows = [
        {"id": "a-end", "startTime": "2026-11-06T05:37:41.580000", "endTime": "2026-11-06 05:37:41.709000"},
        {"id": "middle", "startTime": middle},
        {"id": "z-start", "startTime": "2026-11-06 05:37:39.580000"},
        {"id": "invalid", "startTime": "not-a-time"},
        {"id": "missing"},
    ]
    document = {"data": [{**row, "traceId": "TRACE-1", "sessionId": SESSION_ID} for row in rows]}
    source = tmp_path / "observations.json"
    source.write_text(json.dumps(document))
    saved = trace_capture.import_session(tmp_path, SESSION_ID, SOURCE, source, retention={"allow_body": True})
    session_dir = Path(saved["session_dir"])
    overview = query.overview(session_dir)
    assert [row["id"] for row in overview["rows"][:3]] == ["z-start", "middle", "a-end"]
    assert [row["session_offset_ms"] for row in overview["rows"]] == [0, 1000, 2000, None, None]

    refs = {node["id"]: node["reference"] for node in query.nodes(session_dir)}
    issue = records.save_issue(
        session_dir,
        {
            "title": "Local time range",
            "description": "Mix local and explicitly zoned observations.",
            "segments": [
                {
                    "summary": "Start through end",
                    "scope": {"from": refs["z-start"], "to": refs["a-end"]},
                    "evidence": [refs["middle"]],
                }
            ],
        },
    )
    assert issue["segment_coverage"][0]["node_count"] == 3
    assert issue["segment_coverage"][0]["duration_ms"] == 2129
    before = digests(session_dir)
    generated = trace.generate_trace(tmp_path, session_id=SESSION_ID, open_browser=False)
    data = payload(Path(generated["output_path"]).read_text())
    cap = data["captures"][0]
    nodes = {node["id"]: node for node in cap["nodes"]}
    assert nodes["z-start"]["at"] == round(start.timestamp() * 1_000_000) / 1000
    assert nodes["invalid"]["at"] is None and nodes["missing"]["at"] is None
    track = cap["timeline"]["tracks"][0]
    assert track["end"] - track["start"] == 2129
    part = data["issues"][0]["fragments"][0]
    assert part["kind"] == "segment" and part["duration_ms"] == 2129
    assert {cap["nodes"][i]["id"] for i in part["anchors"]} == {"z-start", "middle", "a-end"}
    assert data["warnings"] == []
    assert digests(session_dir) == before


def test_issue_ranges_split_idle_gaps_branches_and_captures_and_keep_every_field(tmp_path):
    rows = [
        {"id": "first", "name": "start", "startTime": "2026-01-01T00:00:01Z", "input": 1, "output": 2},
        {"id": "second", "name": "later", "startTime": "2026-01-01T00:10:00Z"},
        {"id": "child", "name": "delegated", "startTime": "2026-01-01T00:00:01Z", "subagent_id": "branch"},
        {"id": "unknown", "name": "no time", "input": 1, "output": 2},
    ]
    files = {"trace.json": json.dumps({"trace": {"id": "T", "input": "request", "observations": rows}})}
    for cid in ("CAP-a", "CAP-b"):
        cap = store.create_capture(tmp_path, SESSION_ID, SOURCE, files, scope={}, completeness={}, capture_id=cid)
    session = Path(cap["session_dir"])
    fields = [
        (0, "/trace/input"),
        (0, "/trace/observations/0/input"),
        (0, "/trace/observations/0/output"),
        (0, "/trace/observations/1"),
        (0, "/trace/observations/2"),
        (0, "/trace/observations/3/input"),
        (0, "/trace/observations/3/output"),
        (1, "/trace/observations/0/input"),
        (0, None),
    ]
    evidence = []
    for capture_index, pointer in fields:
        cid = ("CAP-a", "CAP-b")[capture_index]
        path = f"captures/{cid}/raw/trace.json"
        evidence.append(store.evidence_ref(session, path, pointer) if pointer else store.evidence_ref(session, path))
    records.save_issue(session, {"title": "Check", "description": "Synthetic", "evidence": evidence})
    _, data = trace.build_trace_data(tmp_path, session_id=SESSION_ID)
    parts = data["issues"][0]["fragments"]
    # Without recorded segments, each located field stays a single unscoped point.
    assert sorted(p for f in parts for p in f["refs"] + f.get("context_refs", [])) == list(range(len(fields)))
    assert all(f.get("unscoped") or f["kind"] == "file" for f in parts)
    timed = [f for f in parts if f["kind"] == "point"]
    assert len(timed) >= 4 and all(f["start"] == f["end"] for f in timed)
    assert any(f["kind"] == "untimed" and set(f["refs"]) >= {5, 6} for f in parts)
    assert any(f["kind"] == "file" and not f["anchors"] for f in parts)
    assert any(f["capture_id"] == "CAP-b" for f in timed)


def test_recorded_segments_render_as_fragments(tmp_path):
    document = {
        "trace": {
            "id": "TRACE-1",
            "sessionId": SESSION_ID,
            "input": "Return the user's greeting",
            "output": "ok",
            "observations": [
                {
                    "id": "OBS-0",
                    "type": "AGENT",
                    "name": "agent",
                    "startTime": "2026-01-01T00:00:00Z",
                    "endTime": "2026-01-01T00:01:00Z",
                },
                {
                    "id": "OBS-1",
                    "type": "SPAN",
                    "name": "iter",
                    "parentObservationId": "OBS-0",
                    "startTime": "2026-01-01T00:00:10Z",
                    "endTime": "2026-01-01T00:00:40Z",
                    "output": "42",
                },
                {
                    "id": "OBS-2",
                    "type": "GENERATION",
                    "name": "llm.call",
                    "parentObservationId": "OBS-1",
                    "startTime": "2026-01-01T00:00:12Z",
                    "endTime": "2026-01-01T00:00:30Z",
                    "input": {"messages": ["hello"]},
                    "output": "42",
                },
            ],
        },
        "export": {"scope": "selected trace only"},
    }
    result = store.create_capture(
        tmp_path,
        SESSION_ID,
        SOURCE,
        {"trace.json": json.dumps(document)},
        scope={"format": "langfuse-export", "selected_traces": ["TRACE-1"]},
        completeness={"status": "partial", "gaps": ["Session completeness is unknown."]},
        capture_id="CAP-001",
    )
    session_dir = Path(result["session_dir"])
    path = "captures/CAP-001/raw/trace.json"
    node = store.evidence_ref(session_dir, path, "/trace/observations/1")
    field = store.evidence_ref(session_dir, path, "/trace/observations/2/output")
    records.save_issue(
        session_dir,
        {
            "id": "ISSUE-SEG",
            "title": "Segmented",
            "description": "Has an authored range",
            "segments": [
                {
                    "summary": "Model iteration",
                    "scope": {"node": node},
                    "evidence": [field],
                }
            ],
        },
    )
    _, data = trace.build_trace_data(tmp_path, session_id=SESSION_ID)
    parts = data["issues"][0]["fragments"]
    assert parts and parts[0]["id"] == "S1"
    assert parts[0]["kind"] == "segment"
    assert parts[0]["start"] is not None and parts[0]["end"] is not None
    assert parts[0]["summary"].startswith("Model")
    assert data["issues"][0]["evidence"]


def test_issue_rail_hover_does_not_rebuild_rail_or_event_list():
    # Rebuilding the rail under the pointer replaced the buttons between mousedown and mouseup,
    # so clicks on 定位 / segment chips never fired. Hover may redraw the swimlanes only.
    js = (Path(trace.__file__).parent / "assets/trace/app.js").read_text()

    def body(start):
        index = js.index(start)
        depth = 0
        for pos in range(js.index("{", index), len(js)):
            depth += {"{": 1, "}": -1}.get(js[pos], 0)
            if depth == 0:
                return js[index : pos + 1]
        raise AssertionError(start)

    for handler in ("card.onmouseenter = () =>", "card.onmouseleave = () =>"):
        code = body(handler)
        assert "drawLanes()" in code
        assert "renderTraces" not in code and "renderIssueRail" not in code
    assert "renderIssueRail" not in body("function renderTraces(")
    assert "renderIssueRail" not in body("function startIssue(")
