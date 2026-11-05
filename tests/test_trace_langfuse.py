import json
import urllib.error

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.traces import langfuse, store


def config(monkeypatch):
    monkeypatch.setenv("TEST_LF_PUBLIC", "pk-lf-PUBLICSECRET")
    monkeypatch.setenv("TEST_LF_SECRET", "sk-lf-SECRETSECRET")
    return {
        "source": {
            "type": "langfuse",
            "instance": "https://langfuse.invalid",
            "project": "project-1",
            "environment": "test",
        },
        "credentials": {"public_key_env": "TEST_LF_PUBLIC", "secret_key_env": "TEST_LF_SECRET"},
        "retention": {"allow_body": True},
        "from_time": "2026-11-01T00:00:00+08:00",
        "to_time": "2026-11-02T00:00:00+08:00",
    }


def row(**updates):
    return {
        "id": "obs-1",
        "traceId": "trace-1",
        "sessionId": "session-1",
        "projectId": "project-1",
        "environment": "test",
        "input": "request",
        "output": "response",
        **updates,
    }


def test_empty_page_with_cursor_continues_and_credentials_never_saved(tmp_path, monkeypatch):
    cfg = config(monkeypatch)
    pages = [
        {"data": [], "meta": {"cursor": "next"}},
        {"data": [row(output="sk-lf-SECRETSECRET", metadata={"authorization": "secret"})], "meta": {"cursor": None}},
    ]
    calls = []

    def get(instance, params, credentials):
        calls.append(params.copy())
        return pages.pop(0)

    monkeypatch.setattr(langfuse, "_get_page", get)
    result = langfuse.fetch_session(tmp_path, "session-1", cfg)
    assert len(calls) == 2 and calls[1]["cursor"] == "next"
    assert calls[0]["sessionId"] == "session-1" and "io" in calls[0]["fields"].split(",")
    assert result["manifest"]["completeness"]["pagination_completed"]
    assert result["observation_count"] == 1
    assert all(b"SECRETSECRET" not in path.read_bytes() for path in (tmp_path / ".atk").rglob("*") if path.is_file())
    assert not (tmp_path / ".atk/results").exists()


def test_late_failure_keeps_approved_pages_and_records_partial(tmp_path, monkeypatch):
    cfg = config(monkeypatch)
    calls = 0

    def get(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"data": [row()], "meta": {"cursor": "next"}}
        raise UserActionRequired("Langfuse read failed with HTTP 503; response body and credentials were omitted.")

    monkeypatch.setattr(langfuse, "_get_page", get)
    result = langfuse.fetch_session(tmp_path, "session-1", cfg)
    manifest = store.read_capture(result["session_dir"], result["capture_id"])
    assert manifest["completeness"]["status"] == "partial" and len(manifest["files"]) == 1
    assert manifest["errors"]


@pytest.mark.parametrize(
    "changes", [{"sessionId": "other"}, {"sessionId": None}, {"projectId": "other"}, {"environment": "other"}]
)
def test_foreign_rows_are_never_archived(tmp_path, monkeypatch, changes):
    cfg = config(monkeypatch)
    monkeypatch.setattr(langfuse, "_get_page", lambda *args: {"data": [row(**changes)], "meta": {"cursor": None}})
    result = langfuse.fetch_session(tmp_path, "session-1", cfg)
    assert result["manifest"]["status"] == "failed" and not result["manifest"]["files"]


def test_time_range_and_retention_fail_before_request_or_archive(tmp_path, monkeypatch):
    cfg = config(monkeypatch)
    cfg.pop("from_time")
    with pytest.raises(UserActionRequired, match="from_time"):
        langfuse.fetch_session(tmp_path, "session-1", cfg)
    assert not (tmp_path / ".atk").exists()
    cfg["from_time"] = "2026-11-01T00:00:00+08:00"
    cfg["retention"] = {}
    with pytest.raises(UserActionRequired, match="retention"):
        langfuse.fetch_session(tmp_path, "session-1", cfg)
    assert not (tmp_path / ".atk").exists()


def test_repeated_cursor_and_conflicting_duplicates_stop(tmp_path, monkeypatch):
    cfg = config(monkeypatch)
    pages = [
        {"data": [row()], "meta": {"cursor": "same"}},
        {"data": [row(output="conflict")], "meta": {"cursor": "same"}},
    ]
    monkeypatch.setattr(langfuse, "_get_page", lambda *args: pages.pop(0))
    result = langfuse.fetch_session(tmp_path, "session-1", cfg)
    assert result["observation_count"] == 1 and result["manifest"]["errors"]
    assert len(result["manifest"]["files"]) == 1


def test_download_for_verification_does_not_create_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = config(monkeypatch)
    monkeypatch.setattr(langfuse, "_get_page", lambda *args: {"data": [row()], "meta": {"cursor": None}})
    result = langfuse.download_session(cfg, "session-1")
    assert result["files"] and result["scope"]["parameters"]["sessionId"] == "session-1"
    assert not (tmp_path / ".atk").exists()


def test_http_errors_do_not_include_body_or_request_details(monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raise urllib.error.HTTPError("https://secret.invalid/token", 401, "SECRET", {}, None)

    monkeypatch.setattr(langfuse.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(UserActionRequired) as error:
        langfuse._get_page("https://langfuse.invalid", {"sessionId": "session-1"}, ("PUBLIC", "SECRET"))
    assert "SECRET" not in str(error.value) and "secret.invalid" not in str(error.value)
    assert langfuse._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid") is None


def test_interruption_keeps_already_received_pages(tmp_path, monkeypatch):
    cfg = config(monkeypatch)
    calls = 0

    def get(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"data": [row()], "meta": {"cursor": "next"}}
        raise KeyboardInterrupt

    monkeypatch.setattr(langfuse, "_get_page", get)
    result = langfuse.fetch_session(tmp_path, "session-1", cfg)
    assert result["manifest"]["status"] == "interrupted"
    assert len(result["manifest"]["files"]) == 1


def test_unrecognized_source_fields_cannot_leak_credentials_to_manifest(tmp_path, monkeypatch):
    cfg = config(monkeypatch)
    cfg["source"]["secret_key"] = "ARBITRARY-SECRET"
    cfg["server_version"] = "sk-lf-SECRETSECRET"
    monkeypatch.setattr(langfuse, "_get_page", lambda *args: {"data": [row()], "meta": {"cursor": None}})
    result = langfuse.fetch_session(tmp_path, "session-1", cfg)
    saved = json.dumps(result["manifest"])
    assert "ARBITRARY-SECRET" not in saved and "SECRETSECRET" not in saved
