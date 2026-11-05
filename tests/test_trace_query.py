import json
from pathlib import Path

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.traces import capture, query
from tests.test_trace_capture import POLICY, SOURCE, export_file


def test_overview_search_and_pointer_read_saved_bytes(tmp_path):
    source = export_file(tmp_path, input="user prompt", output="unrelated answer")
    result = capture.import_session(tmp_path, "session-1", SOURCE, source, retention=POLICY)
    root = result["session_dir"]
    view = query.overview(root, limit=1)
    assert view["total"] == 2 and view["unread_nodes"] == 1
    found = query.search(root, "WORLD")
    assert found["total"] == 1
    reference = found["rows"][0]["ref"]
    assert reference["pointer"] == "/trace/observations/0"
    detail = query.show(root, reference, limit=1000)
    assert json.loads(detail["text"])["output"] == "world"
    truncated = query.show(root, reference, limit=5)
    assert truncated["truncated"] and truncated["next_offset"] == 5
    source.write_text("changed original")
    assert "world" in query.show(root, reference)["text"]


def test_reference_tampering_and_unsafe_paths_fail(tmp_path):
    source = export_file(tmp_path)
    result = capture.import_session(tmp_path, "session-1", SOURCE, source, retention=POLICY)
    root = result["session_dir"]
    reference = query.nodes(root)[0]["ref"]
    with pytest.raises(UserActionRequired):
        query.show(root, {**reference, "path": "../../trace.json"})
    with pytest.raises(UserActionRequired):
        query.show(root, {**reference, "sha256": "0" * 64})
    path = Path(root) / reference["path"]
    path.write_text('{"trace":{"id":"forged"}}')
    with pytest.raises(UserActionRequired):
        query.show(root, reference)


def test_json_pointer_escape_segments_and_bad_paging(tmp_path):
    assert query._pointer({"a/b": {"~": "value"}}, "/a~1b/~0") == "value"
    with pytest.raises(UserActionRequired):
        query._pointer({}, "/absent")
    with pytest.raises(UserActionRequired):
        query.overview(tmp_path, offset=-1)
