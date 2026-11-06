import hashlib
import json
import re
from pathlib import Path

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.traces import records, store
from agent_tune_kit.visualize import trace

SOURCE = {"type": "local", "instance": "synthetic", "project": "demo", "environment": "test"}
SESSION_ID = "SESSION-synthetic"


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
    assert digests(session_dir) == before
    assert not (tmp_path / ".atk/results").exists()


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
        },
    )
    for number, outcome in ((1, "fail"), (2, "pass")):
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
    assert sorted(p for f in parts for p in f["refs"] + f.get("context_refs", [])) == list(range(len(fields)))
    timed = [f for f in parts if f["kind"] == "actions"]
    assert len(timed) == 4 and all(f["start"] == f["end"] for f in timed)
    assert sum(f["branch"] == "branch" for f in timed) == 1
    assert len([f for f in timed if f["capture_id"] == "CAP-b"]) == 1
    assert any(f["refs"] == [1, 2] and f["context_refs"] == [0] for f in timed)
    assert any(f["kind"] == "untimed" and f["refs"] == [5, 6] for f in parts)
    assert any(f["kind"] == "file" and not f["anchors"] for f in parts)
