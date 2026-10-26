import importlib
import importlib.util
import json
import shutil

import pytest

from agent_tune_kit.errors import UserActionRequired


def evidence():
    assert importlib.util.find_spec("agent_tune_kit.results.evidence"), "Independent evidence storage is missing"
    return importlib.import_module("agent_tune_kit.results.evidence")


def allow(role, value):
    return value


def make_execution(tmp_path, **policy):
    module = evidence()
    return module.ExecutionEvidence(
        tmp_path / "execution", run_id="run-test", sample_ids=["1"], policy={"retain": allow, **policy}
    )


def test_copied_execution_is_independent_and_changes_are_detected(tmp_path):
    item = make_execution(tmp_path)
    item.save("input", {"question": "你好"}, path="input.json")
    item.event("call.started", {"source": "fixture"})
    item.save("output", {"answer": "好"}, path="output.json")
    item.seal("completed")
    copied = tmp_path / "copy"
    shutil.copytree(item.directory, copied)
    shutil.rmtree(item.directory)
    view = evidence().inspect_execution(copied)
    assert view["status"] == "completed"
    assert view["sealed"] is True
    (copied / "output.json").write_text("{}")
    with pytest.raises(UserActionRequired, match="integrity"):
        evidence().inspect_execution(copied)


def test_sealed_execution_rejects_late_writes(tmp_path):
    item = make_execution(tmp_path)
    item.seal("completed")
    before = (item.directory / "execution.json").read_bytes()
    with pytest.raises(UserActionRequired, match="sealed"):
        item.event("late", {})
    assert (item.directory / "execution.json").read_bytes() == before


def test_paths_and_symlinks_cannot_escape_execution(tmp_path):
    item = make_execution(tmp_path)
    with pytest.raises(UserActionRequired, match="path"):
        item.save("output", "secret", path="../outside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (item.directory / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UserActionRequired, match="path"):
        item.save("output", "secret", path="escape/file")
    assert not list(outside.iterdir())


def test_unknown_schema_and_missing_manifest_never_become_legacy(tmp_path):
    item = make_execution(tmp_path)
    path = item.directory / "execution.json"
    data = json.loads(path.read_text())
    data["schema_version"] = 999
    path.write_text(json.dumps(data))
    with pytest.raises(UserActionRequired, match="schema"):
        evidence().inspect_execution(item.directory)
    path.unlink()
    with pytest.raises(UserActionRequired):
        evidence().inspect_execution(item.directory)


def test_open_events_allow_only_incomplete_tail(tmp_path):
    item = make_execution(tmp_path)
    item.event("first", {})
    path = item.directory / "events.jsonl"
    with path.open("ab") as handle:
        handle.write(b'{"kind":')
    result = evidence().read_events(item.directory)
    assert len(result["events"]) == 1
    assert result["incomplete_tail"]
    path.write_bytes(b"broken\n" + path.read_bytes())
    with pytest.raises(UserActionRequired, match="event"):
        evidence().read_events(item.directory)


def test_text_pagination_uses_characters_and_binds_hash(tmp_path):
    item = make_execution(tmp_path)
    artifact = item.save("text", "你好🙂abc", media_type="text/plain")
    item.seal("completed")
    result = evidence().read_artifact(item.directory, artifact["artifact_id"], offset=1, limit=2)
    assert result["text"] == "好🙂"
    assert result["next_offset"] == 3
    assert result["sha256"] == artifact["sha256"]


def test_retention_failure_never_writes_secret_or_unsafe_error(tmp_path):
    def reject(role, value):
        raise ValueError("SECRET")

    item = make_execution(tmp_path, retain=reject)
    with pytest.raises(UserActionRequired, match="retention") as exc:
        item.save("input", "SECRET", path="input.json")
    assert "SECRET" not in str(exc.value)
    assert all(b"SECRET" not in p.read_bytes() for p in item.directory.rglob("*") if p.is_file())
    assert not (item.directory / "input.json").exists()


def test_budget_and_nonfinite_json_fail_without_registering_artifact(tmp_path):
    item = make_execution(tmp_path, max_bytes=10)
    with pytest.raises(UserActionRequired):
        item.save("input", "x" * 100)
    with pytest.raises(UserActionRequired):
        item.save("output", float("nan"))
    assert not item.manifest["files"]


def test_private_files_and_no_implicit_overwrite(tmp_path):
    item = make_execution(tmp_path)
    item.save("input", "value", path="input.json")
    assert (item.directory.stat().st_mode & 0o777) == 0o700
    assert ((item.directory / "input.json").stat().st_mode & 0o777) == 0o600
    with pytest.raises(UserActionRequired):
        item.save("input", "replacement", path="input.json")
    with pytest.raises(UserActionRequired):
        make_execution(tmp_path)


def test_native_json_bytes_preserve_original_format(tmp_path):
    item = make_execution(tmp_path)
    raw = b'{ "native": true }\n'
    entry = item.save("audit", raw, media_type="application/json")
    assert (item.directory / entry["path"]).read_bytes() == raw


def test_event_body_cannot_override_identity(tmp_path):
    item = make_execution(tmp_path, retain=lambda role, value: dict(value, seq=999, execution_id="wrong"))
    item.event("test", {})
    event = json.loads((item.directory / "events.jsonl").read_text())
    assert event["seq"] == 1 and event["execution_id"] == item.manifest["execution_id"]


def test_seal_rejects_modified_previously_saved_artifact(tmp_path):
    item = make_execution(tmp_path)
    item.save("input", "original", path="input.json")
    (item.directory / "input.json").write_text('"changed"')
    with pytest.raises(UserActionRequired, match="integrity"):
        item.seal("completed")
    assert not json.loads((item.directory / "execution.json").read_text())["sealed"]


def test_streamed_artifact_respects_budget_and_is_not_registered_on_failure(tmp_path):
    item = make_execution(tmp_path, max_bytes=5, retain_chunks=lambda role, chunks: chunks)
    assert hasattr(item, "save_chunks")
    with pytest.raises(UserActionRequired, match="budget"):
        item.save_chunks("audit", iter([b"abc", b"def"]), media_type="application/octet-stream")
    assert not item.manifest["files"]
    item = evidence().ExecutionEvidence(
        tmp_path / "stream",
        run_id="run",
        sample_ids=[],
        policy={"retain": allow, "retain_chunks": lambda role, chunks: chunks},
    )
    entry = item.save_chunks("audit", iter([b"abc", b"def"]), media_type="application/octet-stream")
    item.seal("completed")
    assert (item.directory / entry["path"]).read_bytes() == b"abcdef"


def test_multiple_executions_share_one_content_budget(tmp_path):
    run = evidence().RunEvidence(tmp_path / "v1", policy={"retain": allow, "max_bytes": 8})
    run.execution(["1"]).save("input", "1234", media_type="text/plain")
    with pytest.raises(UserActionRequired, match="budget"):
        run.execution(["2"]).save("input", "12345", media_type="text/plain")


def test_reader_rejects_unlisted_event_stream_after_seal(tmp_path):
    item = make_execution(tmp_path)
    item.seal("completed")
    (item.directory / "events.jsonl").write_text('{"seq":1}\n')
    with pytest.raises(UserActionRequired):
        evidence().inspect_execution(item.directory)


def test_optional_ref_cannot_bypass_missing_new_manifest(tmp_path):
    from agent_tune_kit.results.versions import run_kind

    root = tmp_path / "v1"
    root.mkdir()
    (root / "judgments.csv").write_text("atk_id,verdict,reason,results_sha256,evidence_refs\n")
    with pytest.raises(UserActionRequired):
        run_kind(root)
