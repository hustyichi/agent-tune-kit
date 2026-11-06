import hashlib
import json
from pathlib import Path

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.evidence import ExecutionEvidence
from agent_tune_kit.traces import capture, store

SOURCE = {"type": "local", "instance": "fixture", "project": "demo", "environment": "test"}
POLICY = {"allow_body": True}


def export_file(tmp_path, session_id="session-1", **trace):
    path = tmp_path / "trace.json"
    path.write_text(
        json.dumps(
            {
                "trace": {
                    "id": "trace-1",
                    "sessionId": session_id,
                    "observations": [{"id": "obs-1", "traceId": "trace-1", "input": "hello", "output": "world"}],
                    **trace,
                },
                "export": {"source": "fixture"},
            }
        )
    )
    return path


def test_import_without_init_preserves_original_and_is_partial(tmp_path):
    path = export_file(tmp_path)
    original = path.read_bytes()
    result = capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)
    assert path.read_bytes() == original
    assert result["trace_count"] == 1 and result["observation_count"] == 1
    manifest = store.load_session(result["session_dir"])["archive"]
    assert manifest["completeness"]["status"] == "partial"
    assert not (tmp_path / ".atk/results").exists()
    assert not (Path(result["session_dir"]) / "issues").exists()
    assert "/.atk/traces/" in (tmp_path / ".gitignore").read_text()


def test_retention_is_checked_before_any_new_copy(tmp_path):
    path = export_file(tmp_path, password="DO-NOT-SAVE")
    with pytest.raises(UserActionRequired, match="retention"):
        capture.import_session(tmp_path, "session-1", SOURCE, path)
    assert not (tmp_path / ".atk").exists()
    result = capture.import_session(
        tmp_path,
        "session-1",
        SOURCE,
        path,
        retention={"allow_body": True, "redact_keys": ["input"], "max_string_chars": 15},
    )
    bodies = [p.read_bytes() for p in Path(result["session_dir"]).rglob("*") if p.is_file()]
    assert all(b"DO-NOT-SAVE" not in value for value in bodies)
    assert any(b"REDACTED" in value for value in bodies)


@pytest.mark.parametrize("mode", ["wrong-session", "missing-session", "unknown-format", "symlink"])
def test_invalid_local_identity_does_not_create_archive(tmp_path, mode):
    path = export_file(
        tmp_path, "other" if mode == "wrong-session" else None if mode == "missing-session" else "session-1"
    )
    if mode == "unknown-format":
        path.write_text('{"arbitrary":true}')
    if mode == "symlink":
        link = tmp_path / "linked.json"
        link.symlink_to(path)
        path = link
    with pytest.raises(UserActionRequired):
        capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)
    assert not (tmp_path / ".atk").exists()


def test_log_requires_hash_bound_association_and_keeps_partial_status(tmp_path):
    path = tmp_path / "run.log"
    path.write_text("local run output")
    association = {
        "session_id": "session-1",
        "basis": "runtime export associates this execution",
        "files": [{"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}],
    }
    result = capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY, association=association)
    assert result["manifest"]["completeness"]["status"] == "partial"
    path.write_text("changed")
    with pytest.raises(UserActionRequired):
        capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY, association=association)


def test_atk_execution_keeps_native_format_and_requires_session_evidence(tmp_path):
    directory = tmp_path / "execution"
    execution = ExecutionEvidence(
        directory, run_id="run-1", sample_ids=["1"], policy={"retain": lambda role, value: value}
    )
    execution.save("input", {"prompt": "hello"}, path="input.json")
    execution.save("output", {"text": "world"}, path="output.json")
    execution.event("tool.result", {"session_id": "session-1", "value": "done"})
    execution.seal("completed")
    original = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    result = capture.import_session(tmp_path, "session-1", SOURCE, directory, retention=POLICY)
    files = [p for p in Path(result["session_dir"]).rglob("*") if p.is_file()]
    event_path = next(p for p in files if p.name == "events.jsonl")
    assert json.loads(event_path.read_text())["kind"] == "tool.result"
    provenance = json.loads(next(p for p in files if p.name == "provenance.json").read_text())
    assert provenance["original_evidence_id"] == execution.manifest["evidence_id"]
    assert provenance["transformed_copy"]
    assert original == {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    with pytest.raises(UserActionRequired, match="Session"):
        capture.import_session(tmp_path, "different", SOURCE, directory, retention=POLICY)


def test_json_encoded_io_is_redacted_without_changing_string_type():
    result, transformations = capture.retain({"input": '{"api_key":"NEVER-SAVE","prompt":"hi"}'}, POLICY)
    assert isinstance(result["input"], str)
    assert json.loads(result["input"]) == {"api_key": "[REDACTED]", "prompt": "hi"}
    assert "serialized JSON retention" in transformations


@pytest.mark.parametrize("field,value", [("projectId", "foreign"), ("environment", "prod")])
def test_export_source_ownership_is_validated_before_archive(tmp_path, field, value):
    path = export_file(tmp_path, **{field: value})
    with pytest.raises(UserActionRequired, match="source"):
        capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)
    assert not (tmp_path / ".atk").exists()


def test_export_envelope_cannot_contradict_native_identity(tmp_path):
    path = export_file(tmp_path)
    value = json.loads(path.read_text())
    value["export"]["session_id"] = "other"
    path.write_text(json.dumps(value))
    with pytest.raises(UserActionRequired, match="Session"):
        capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)


def test_conflicting_export_files_reject_whole_local_import(tmp_path):
    path = export_file(tmp_path)
    second = tmp_path / "other.json"
    value = json.loads(path.read_text())
    value["trace"]["observations"][0]["output"] = "conflicting"
    second.write_text(json.dumps(value))
    with pytest.raises(UserActionRequired, match="duplicate"):
        capture.import_session(tmp_path, "session-1", SOURCE, [path, second], retention=POLICY)
    assert not (tmp_path / ".atk").exists()


def test_sealed_observation_capture_package_can_be_imported(tmp_path):
    project = tmp_path / "original"
    project.mkdir()
    document = {"data": [{"id": "obs-1", "traceId": "trace-1", "sessionId": "session-1"}], "meta": {"cursor": None}}
    original = store.create_capture(
        project,
        "session-1",
        SOURCE,
        {"pages/000001.json": json.dumps(document)},
        scope={"kind": "fixture"},
        completeness={"status": "declared-range"},
    )
    imported = capture.import_session(tmp_path, "session-1", SOURCE, original["capture_dir"], retention=POLICY)
    assert imported["observation_count"] == 1
    assert imported["manifest"]["completeness"]["status"] == "partial"
    page = next(Path(original["capture_dir"]).glob("raw/pages/*.json"))
    page.write_text("changed")
    with pytest.raises(UserActionRequired, match="integrity"):
        capture.import_session(tmp_path, "session-1", SOURCE, original["capture_dir"], retention=POLICY)


def test_export_summary_is_verified_against_selected_original_bytes(tmp_path):
    trace = export_file(tmp_path)
    summary = tmp_path / "_export_summary.json"
    summary.write_text(
        json.dumps(
            {
                "session_id": "session-1",
                "environment": "test",
                "trace_count": 1,
                "traces": [
                    {
                        "trace_id": "trace-1",
                        "output_path": str(trace),
                        "bytes": trace.stat().st_size,
                        "sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )
    original = summary.read_bytes()
    result = capture.import_session(tmp_path, "session-1", SOURCE, [trace, summary], retention=POLICY)
    assert result["observation_count"] == 1
    assert summary.read_bytes() == original
    summary_input = next(
        item for item in result["manifest"]["scope"]["inputs"] if item["format"] == "langfuse-export-summary"
    )
    assert summary_input["verified_selected_files"] == [str(trace)]
    trace.write_text(trace.read_text() + " ")
    with pytest.raises(UserActionRequired, match="SHA-256"):
        capture.import_session(tmp_path, "session-1", SOURCE, [trace, summary], retention=POLICY)


def test_scalar_jsonl_is_rejected_before_any_archive_is_created(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("1\n")
    association = {
        "session_id": "session-1",
        "basis": "runtime manifest",
        "files": [{"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}],
    }
    with pytest.raises(UserActionRequired, match="object"):
        capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY, association=association)
    assert not (tmp_path / ".atk").exists()


def test_associated_csv_secret_columns_are_redacted(tmp_path):
    path = tmp_path / "rows.csv"
    path.write_text("prompt,password\nhello,PRIVATE-PASSWORD\n")
    association = {
        "session_id": "session-1",
        "basis": "runtime manifest",
        "files": [{"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}],
    }
    result = capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY, association=association)
    saved = next(Path(result["session_dir"]).rglob("rows.csv")).read_text()
    assert "PRIVATE-PASSWORD" not in saved and "[REDACTED]" in saved


def test_private_key_fields_and_pem_strings_are_excluded():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nPRIVATE-KEY-BODY\n-----END RSA PRIVATE KEY-----"
    retained, _ = capture.retain(
        {"private_key": pem, "client_secret": "CLIENT-SECRET", "accessToken": "ACCESS-SECRET", "output": pem}, POLICY
    )
    assert retained["private_key"] == retained["client_secret"] == retained["accessToken"] == "[REDACTED]"
    assert retained["output"] == "[REDACTED PRIVATE KEY]"


def test_source_atime_changes_do_not_look_like_modified_input(tmp_path, monkeypatch):
    path = tmp_path / "source.json"
    path.write_text('{"safe":true}')
    original_stat = Path.stat
    reads = 0

    class Stat:
        def __init__(self, value, atime):
            self.value = value
            self.st_atime_ns = atime

        def __getattr__(self, name):
            return getattr(self.value, name)

    def changing_atime(target, *args, **kwargs):
        nonlocal reads
        value = original_stat(target, *args, **kwargs)
        if target == path:
            reads += 1
            return Stat(value, reads)
        return value

    monkeypatch.setattr(Path, "stat", changing_atime)
    assert capture.read_local(path) == b'{"safe":true}'


def test_reimport_updates_by_native_id_and_keeps_field_evidence(tmp_path):
    from agent_tune_kit.traces import query, records
    from agent_tune_kit.visualize.trace import build_trace_data

    path = export_file(tmp_path, input="expected")
    directory = Path(capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)["session_dir"])
    assert directory.name == "session-1"
    raw = directory / "raw/trace-1.json"
    assert raw.is_file() and not (directory / "captures").exists()
    ref = store.evidence_ref(directory, "raw/trace-1.json", "/trace/input")
    records.save_issue(directory, {"title": "Check input", "description": "Inspect field", "evidence": [ref]})
    before = raw.stat().st_mtime_ns
    capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)
    assert raw.stat().st_mtime_ns == before
    export_file(tmp_path, id="second", observations=[])
    capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)
    export_file(tmp_path, input="expected", output="updated")
    capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)
    assert sorted(p.name for p in raw.parent.glob("*.json")) == ["second.json", "trace-1.json"]
    assert json.loads(raw.read_bytes())["trace"]["output"] == "updated"
    assert json.loads(query.show(directory, ref)["text"]) == "expected"
    _, data = build_trace_data(tmp_path, session_id="session-1")
    assert data["issues"][0]["fragments"][0]["anchors"]
    export_file(tmp_path, input="modified")
    capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)
    with pytest.raises(UserActionRequired, match="重新检测"):
        query.show(directory, ref)
    _, data = build_trace_data(tmp_path, session_id="session-1")
    assert len(data["issues"]) == 1 and data["warnings"]
    path.write_text("{broken")
    with pytest.raises(UserActionRequired):
        capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)
    assert json.loads(raw.read_bytes())["trace"]["input"] == "modified"


def test_pages_are_grouped_per_trace_across_pagination(tmp_path):
    paths = []
    for index, (trace_id, obs) in enumerate([("a", "1"), ("b", "1"), ("a", "2")]):
        p = tmp_path / f"page{index}.json"
        p.write_text(json.dumps({"data": [{"id": obs, "traceId": trace_id, "sessionId": "s"}]}))
        paths.append(p)
    d = Path(capture.import_session(tmp_path, "s", SOURCE, paths, retention=POLICY)["session_dir"])
    assert [row["id"] for row in json.loads((d / "raw/a.json").read_text())["data"]] == ["1", "2"]
    capture.import_session(tmp_path, "s", SOURCE, [paths[2]], retention=POLICY)
    assert [row["id"] for row in json.loads((d / "raw/a.json").read_text())["data"]] == ["2"]
    assert (d / "raw/b.json").is_file()


def test_legacy_migration_preserves_issue_fields_and_diagnosis(tmp_path):
    from agent_tune_kit.traces import migrate, records
    from agent_tune_kit.visualize.trace import build_trace_data

    raw = export_file(tmp_path)
    old = store.create_capture(tmp_path, "session-1", SOURCE, {"export.json": raw.read_bytes()})
    ref = store.evidence_ref(old["session_dir"], f"captures/{old['capture_id']}/raw/export.json", "/trace/id")
    issue = records.save_issue(old["session_dir"], {"title": "T", "description": "D", "evidence": [ref]})
    records.save_diagnosis(old["session_dir"], issue["id"], "Review " + ref["path"])
    moved = migrate.migrate(tmp_path, "session-1")
    assert Path(moved["session_dir"]).name == "session-1"
    _, data = build_trace_data(tmp_path, session_id="session-1")
    assert data["issues"][0]["fragments"][0]["anchors"] and not data["warnings"]
    assert "raw/trace-1.json" in data["issues"][0]["diagnoses"][0]["text"]
    assert migrate.migrate(tmp_path, "session-1")["already_current"]


def test_reordered_observations_resolve_by_id_and_refresh_preview(tmp_path):
    from agent_tune_kit.traces import query, records
    from agent_tune_kit.visualize.trace import generate_trace

    rows = [{"id": "a", "output": "first"}, {"id": "b", "output": "second"}]
    path = export_file(tmp_path, observations=rows)
    directory = Path(capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)["session_dir"])
    ref = store.evidence_ref(directory, "raw/trace-1.json", "/trace/observations/0/output")
    records.save_issue(directory, {"title": "Check", "description": "Check A", "evidence": [ref]})
    generate_trace(tmp_path, session_id="session-1", open_browser=False)
    export_file(tmp_path, observations=rows[::-1])
    updated = capture.import_session(tmp_path, "session-1", SOURCE, path, retention=POLICY)
    assert updated["preview"]["warnings"] == []
    shown = query.show(directory, ref)
    assert json.loads(shown["text"]) == "first" and shown["resolved_pointer"] == "/trace/observations/1/output"
    assert '"resolved_pointer"' in (directory / "preview.html").read_text()
