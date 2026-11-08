import json
from pathlib import Path

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.traces import capture, query, records
from agent_tune_kit.traces.store import evidence_ref
from tests.test_trace_capture import POLICY, SOURCE


def timed_export(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text(
        json.dumps(
            {
                "trace": {
                    "id": "trace-1",
                    "sessionId": "session-1",
                    "input": {"goal": "fix greeting"},
                    "output": "42",
                    "observations": [
                        {
                            "id": "agent-1",
                            "type": "AGENT",
                            "name": "ClaudeAgent.query",
                            "startTime": "2026-11-05T10:00:00+08:00",
                            "endTime": "2026-11-05T10:01:00+08:00",
                        },
                        {
                            "id": "iter-1",
                            "type": "SPAN",
                            "name": "agent.iteration #1",
                            "parentObservationId": "agent-1",
                            "startTime": "2026-11-05T10:00:10+08:00",
                            "endTime": "2026-11-05T10:00:40+08:00",
                        },
                        {
                            "id": "llm-1",
                            "type": "GENERATION",
                            "name": "llm.call",
                            "parentObservationId": "iter-1",
                            "startTime": "2026-11-05T10:00:12+08:00",
                            "endTime": "2026-11-05T10:00:30+08:00",
                            "input": {"messages": ["hello"]},
                            "output": "42",
                        },
                        {
                            "id": "result-1",
                            "type": "EVENT",
                            "name": "sdk.result",
                            "parentObservationId": "agent-1",
                            "startTime": "2026-11-05T10:00:50+08:00",
                            "input": {"text": "42"},
                        },
                        {
                            "id": "ui-1",
                            "type": "EVENT",
                            "name": "frontend.ai_message",
                            "parentObservationId": "agent-1",
                            "startTime": "2026-11-05T10:00:51+08:00",
                            "output": "42",
                        },
                    ],
                },
                "export": {"source": "fixture"},
            }
        )
    )
    return path


@pytest.fixture
def session(tmp_path):
    path = timed_export(tmp_path)
    result = capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)
    directory = Path(result["session_dir"])
    return directory


def ref(directory, pointer):
    return evidence_ref(directory, "raw/trace-1.json", pointer)


def test_overview_orders_by_time_and_filters_trace(session):
    view = query.overview(session, limit=10)
    timed = [row for row in view["rows"] if row["type"] != "TRACE"]
    assert [row["node_id"] for row in timed] == ["agent-1", "iter-1", "llm-1", "result-1", "ui-1"]
    assert timed[0]["session_offset_ms"] == 0
    assert timed[1]["session_offset_ms"] == 10_000
    filtered = query.overview(session, trace_id="trace-1", limit=10)
    assert filtered["total"] == 6
    with pytest.raises(UserActionRequired, match="Trace ID"):
        query.overview(session, trace_id="")


def test_node_segment_covers_descendants_and_allows_ancestor_evidence(session):
    payload = {
        "title": "Wrong numeric answer",
        "description": "Final model turn answered 42 instead of a greeting.",
        "segments": [
            {
                "summary": "Model iteration that produced 42",
                "scope": {"node": ref(session, "/trace/observations/1")},
                "evidence": [
                    ref(session, "/trace/observations/2/input"),
                    ref(session, "/trace/observations/2/output"),
                    ref(session, "/trace/observations/0"),
                ],
            }
        ],
    }
    dry = records.save_issue(session, payload, dry_run=True)
    assert dry["dry_run"] is True
    assert dry["segment_coverage"][0]["id"] == "S1"
    assert dry["segment_coverage"][0]["node_count"] == 2
    assert not (session / "issues").exists()
    saved = records.save_issue(session, payload)
    assert saved["segments"][0]["id"] == "S1"
    assert saved["segment_coverage"][0]["duration_ms"] == 30_000
    assert records.load_issue(session, saved["id"])["segments"][0]["summary"].startswith("Model")


def test_range_segment_stays_on_one_trace_and_rejects_outside_evidence(session):
    second = session.parent.parent / "second.json"
    second.write_text(
        json.dumps(
            {
                "trace": {
                    "id": "trace-2",
                    "sessionId": "session-1",
                    "observations": [
                        {
                            "id": "other-1",
                            "type": "EVENT",
                            "name": "noise",
                            "startTime": "2026-11-05T10:00:50+08:00",
                        }
                    ],
                },
                "export": {},
            }
        )
    )
    capture.import_session(session.parent.parent.parent, "session-1", SOURCE, second, retention=POLICY)
    payload = {
        "title": "End messages keep the wrong answer",
        "description": "SDK result and UI message preserve 42.",
        "segments": [
            {
                "summary": "Closing result and UI message",
                "scope": {
                    "from": ref(session, "/trace/observations/3"),
                    "to": ref(session, "/trace/observations/4"),
                },
                "evidence": [
                    ref(session, "/trace/observations/3/input"),
                    ref(session, "/trace/observations/4/output"),
                ],
            }
        ],
    }
    saved = records.save_issue(session, payload)
    assert saved["segment_coverage"][0]["node_count"] == 2
    assert saved["segment_coverage"][0]["trace_id"] == "trace-1"
    with pytest.raises(UserActionRequired, match="inside the scope"):
        records.save_issue(
            session,
            {
                "id": saved["id"],
                "revision": saved["revision"],
                "segments": [
                    {
                        "summary": "Too wide evidence",
                        "scope": {
                            "from": ref(session, "/trace/observations/3"),
                            "to": ref(session, "/trace/observations/4"),
                        },
                        "evidence": [ref(session, "/trace/observations/2/output")],
                    }
                ],
            },
        )
    with pytest.raises(UserActionRequired, match="same Trace"):
        records.save_issue(
            session,
            {
                "title": "Cross",
                "description": "Cross trace endpoints",
                "segments": [
                    {
                        "summary": "Invalid",
                        "scope": {
                            "from": ref(session, "/trace/observations/3"),
                            "to": evidence_ref(session, "raw/trace-2.json", "/trace/observations/0"),
                        },
                        "evidence": [ref(session, "/trace/observations/3")],
                    }
                ],
            },
        )


def test_legacy_issue_without_segments_still_loads(session):
    issue = records.save_issue(
        session,
        {
            "title": "Legacy",
            "description": "Evidence only",
            "evidence": [ref(session, "/trace/output")],
        },
    )
    path = session / f"issues/{issue['id']}/issue.json"
    data = json.loads(path.read_text())
    data.pop("segments", None)
    path.write_text(json.dumps(data))
    loaded = records.load_issue(session, issue["id"])
    assert "segments" not in loaded or loaded.get("segments") == []
    revised = records.save_issue(
        session, {"id": issue["id"], "revision": loaded["revision"], "notes": ["kept without segments"]}
    )
    assert revised["notes"] == ["kept without segments"]
