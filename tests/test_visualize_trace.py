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
