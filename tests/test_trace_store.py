import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.traces.store import (
    create_capture,
    evidence_ref,
    finish_capture,
    init_workspace,
    load_session,
    prepare_capture,
    read_capture,
    read_capture_file,
    resolve_session,
    session_key,
    validate_ref,
)

SOURCE = {"type": "local", "instance": "fixture", "project": "agent"}


def capture(tmp_path, **kwargs):
    return create_capture(
        tmp_path,
        "session-1",
        SOURCE,
        {"trace.json": '{"sessionId":"session-1","input":{"x/y":["ok"]}}'},
        scope={"files": "selected"},
        completeness={"status": "partial"},
        **kwargs,
    )


def test_capture_stable_identity_new_snapshots_and_batch_isolation(tmp_path):
    first = capture(tmp_path)
    manifest = Path(first["manifest_path"]).read_bytes()
    second = capture(tmp_path)
    assert first["session_key"] == second["session_key"]
    assert first["capture_id"] != second["capture_id"]
    assert Path(first["manifest_path"]).read_bytes() == manifest
    assert load_session(first["session_dir"])["captures"] == [first["capture_id"], second["capture_id"]]
    assert not (tmp_path / ".atk/results").exists()
    assert not (tmp_path / ".atk/runner").exists()
    assert not (Path(first["session_dir"]) / "issues").exists()
    assert resolve_session(tmp_path, "session-1") == Path(first["session_dir"])
    assert session_key("session-1", SOURCE) != session_key("session-1", {**SOURCE, "project": "other"})


def test_source_disambiguation_and_no_substitute_identity(tmp_path):
    capture(tmp_path)
    create_capture(tmp_path, "session-1", {**SOURCE, "project": "other"}, {}, {})
    with pytest.raises(UserActionRequired, match="ambiguous"):
        resolve_session(tmp_path, "session-1")
    assert resolve_session(tmp_path, "session-1", SOURCE).name == session_key("session-1", SOURCE)
    with pytest.raises(UserActionRequired, match="Session ID"):
        create_capture(tmp_path, "", SOURCE, {})
    with pytest.raises(UserActionRequired, match="instance and project"):
        create_capture(tmp_path, "session-2", {"type": "local"}, {})


def test_capture_sealed_partial_running_and_integrity(tmp_path):
    prepared = prepare_capture(tmp_path, "session-1", SOURCE)
    assert read_capture(prepared["session_dir"])["status"] == "running"
    result = finish_capture(
        prepared["session_dir"],
        prepared["capture_id"],
        {"trace.json": "{}"},
        status="partial",
        errors=["later page unavailable"],
    )
    assert read_capture(result["session_dir"])["status"] == "partial"
    with pytest.raises(UserActionRequired, match="sealed"):
        finish_capture(result["session_dir"], result["capture_id"], {})
    path = Path(result["capture_dir"]) / "raw/trace.json"
    path.write_text("{ }")
    with pytest.raises(UserActionRequired, match="integrity"):
        read_capture(result["session_dir"])


def test_manifest_tampering_is_detected(tmp_path):
    result = capture(tmp_path)
    path = Path(result["manifest_path"])
    manifest = json.loads(path.read_text())
    manifest["completeness"]["status"] = "complete"
    path.write_text(json.dumps(manifest))
    with pytest.raises(UserActionRequired, match="integrity"):
        read_capture(result["session_dir"])


def test_evidence_references_bind_saved_bytes_and_json_pointer(tmp_path):
    result = capture(tmp_path)
    relative = f"captures/{result['capture_id']}/raw/trace.json"
    ref = evidence_ref(result["session_dir"], relative, "/input/x~1y/0")
    assert validate_ref(result["session_dir"], ref).name == "trace.json"
    assert read_capture_file(result["session_dir"], result["capture_id"], "trace.json")
    for pointer in ("/absent", "/input/x~1y/01", "/input/x~2y", "not-a-pointer"):
        with pytest.raises(UserActionRequired):
            evidence_ref(result["session_dir"], relative, pointer)
    with pytest.raises(UserActionRequired):
        validate_ref(result["session_dir"], {**ref, "sha256": "wrong"})


@pytest.mark.parametrize("name", ["../outside", "/outside", "nested/../../outside", "a\\b", ""])
def test_capture_rejects_unsafe_paths(tmp_path, name):
    with pytest.raises(UserActionRequired, match="Unsafe"):
        create_capture(tmp_path, "session-1", SOURCE, {name: b"private"})
    assert not (tmp_path / "outside").exists()


def test_trace_workspace_symlink_rejected_before_private_write(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / ".atk").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(UserActionRequired, match="symbolic"):
        capture(tmp_path)
    assert not list(elsewhere.iterdir())


def test_init_is_local_idempotent_and_preserves_manual_notes(tmp_path):
    (tmp_path / ".gitignore").write_text("# private\n*.csv\n")
    result = init_workspace(tmp_path, {"source": SOURCE}, "# My Agent\n")
    ignore = (tmp_path / ".gitignore").read_text()
    assert ignore.startswith("# private\n*.csv\n")
    assert ignore.endswith("/.atk/traces/\n")
    assert result["gitignore_updated"]
    repeated = init_workspace(tmp_path, {"other": "manual"})
    assert not repeated["gitignore_updated"]
    assert Path(result["project_path"]).read_text() == "# My Agent\n"
    assert json.loads(Path(result["config_path"]).read_text())["source"] == SOURCE
    assert {path.name for path in Path(result["trace_root"]).iterdir()} == {"project.md", "config.json"}
    with pytest.raises(UserActionRequired, match="preserve"):
        init_workspace(tmp_path, project_text="overwrite")


@pytest.fixture
def git_project(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("Git is required to test ignore rules")
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    subprocess.run(["git", "init", "--quiet", "--template="], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "core.excludesFile", os.devnull], cwd=tmp_path, check=True)
    return tmp_path


@pytest.mark.parametrize(
    ("rules", "added"),
    [
        (b"/.atk/\nother-output/", b""),
        (b".atk/\n", b""),
        (b"/.atk/traces/\n# keep this comment\n", b""),
        (b"/.atk/traces/\n!/.atk/traces/\n", b"/.atk/traces/\n"),
        (b"# /.atk/traces/\n", b"/.atk/traces/\n"),
        (b"other-output/", b"\n/.atk/traces/\n"),
        (b"other-output/\r\n", b"/.atk/traces/\r\n"),
    ],
)
def test_init_respects_effective_git_ignore_rules(git_project, rules, added):
    ignore = git_project / ".gitignore"
    ignore.write_bytes(rules)
    assert init_workspace(git_project)["gitignore_updated"] is bool(added)
    assert not init_workspace(git_project)["gitignore_updated"]
    assert ignore.read_bytes() == rules + added
    subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "--", ".atk/traces/example/raw/trace.json"],
        cwd=git_project,
        check=True,
    )


def test_init_reports_nested_ignore_conflict_before_writing_archive(git_project):
    atk_dir = git_project / ".atk"
    atk_dir.mkdir()
    (atk_dir / ".gitignore").write_text("!traces/\n")
    for _ in range(2):
        with pytest.raises(UserActionRequired, match="still does not ignore"):
            init_workspace(git_project)
    assert (git_project / ".gitignore").read_text() == "/.atk/traces/\n"
    assert not (atk_dir / "traces").exists()


def test_init_without_git_requires_a_whole_ignore_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    ignore = tmp_path / ".gitignore"
    ignore.write_text("# /.atk/traces/\n")
    assert init_workspace(tmp_path)["gitignore_updated"]
    assert not init_workspace(tmp_path)["gitignore_updated"]
    assert ignore.read_text() == "# /.atk/traces/\n/.atk/traces/\n"


@pytest.mark.parametrize(
    "config",
    [
        {"secret_key": "private"},
        {"secret_key_env": "sk-lf-123"},
        {"url": "https://name:secret@example.test"},
        {"url": "https://example.test?token=secret"},
    ],
)
def test_init_rejects_secret_config_without_persisting(tmp_path, config):
    with pytest.raises(UserActionRequired):
        init_workspace(tmp_path, config)
    assert not (tmp_path / ".atk").exists()


def test_init_placeholder_can_be_filled_and_conflict_has_no_side_effects(tmp_path):
    initial = init_workspace(tmp_path, {"source": SOURCE, "retention": {"allow_body": True, "manual": "keep"}})
    init_workspace(tmp_path, {"retention": {"max_string_chars": 500}}, "# Investigated project\n")
    config_path = Path(initial["config_path"])
    assert json.loads(config_path.read_text())["retention"] == {
        "allow_body": True,
        "manual": "keep",
        "max_string_chars": 500,
    }
    before = config_path.read_bytes()
    (tmp_path / ".gitignore").write_text("# manually changed since init\n")
    with pytest.raises(UserActionRequired, match="preserve"):
        init_workspace(tmp_path, {"source": {**SOURCE, "project": "different"}}, "# Conflicting project\n")
    assert config_path.read_bytes() == before
    assert (tmp_path / ".gitignore").read_text() == "# manually changed since init\n"


def test_init_reports_credential_reference_availability_without_values(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_TRACE_PUBLIC_KEY", "pk-lf-local-test")
    monkeypatch.delenv("TEST_TRACE_SECRET_KEY", raising=False)
    result = init_workspace(
        tmp_path, {"langfuse": {"public_key_env": "TEST_TRACE_PUBLIC_KEY", "secret_key_env": "TEST_TRACE_SECRET_KEY"}}
    )
    assert result["credential_references"] == [
        {"environment_variable": "TEST_TRACE_PUBLIC_KEY", "available": True},
        {"environment_variable": "TEST_TRACE_SECRET_KEY", "available": False},
    ]
    assert result["connection_verified"] is False
    assert "pk-lf-local-test" not in json.dumps(result)


def test_load_session_rejects_damaged_nested_fields(tmp_path):
    result = capture(tmp_path)
    path = Path(result["session_dir"]) / "session.json"
    original = path.read_text()
    for field, value in (("revision", None), ("captures", [{}]), ("detections", [{}])):
        record = json.loads(original)
        record[field] = value
        path.write_text(json.dumps(record))
        with pytest.raises(UserActionRequired):
            load_session(result["session_dir"])
