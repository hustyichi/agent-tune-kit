import json
from pathlib import Path

from agent_tune_kit.cli.main import main


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


def invoke(capsys, *args):
    code = main([*args, "--json"])
    output = capsys.readouterr()
    assert code == 0, output.err
    return json.loads(output.out)


def test_trace_cli_records_real_session_without_batch_outputs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    source = {"type": "langfuse", "instance": "https://example.invalid", "project": "demo", "environment": "test"}
    config = write_json(tmp_path / "config.json", {"source": source, "retention": {"allow_body": True}})
    invoke(capsys, "trace", "init", "--config", config)
    export = write_json(
        tmp_path / "export.json",
        {
            "trace": {
                "id": "t1",
                "sessionId": "s1",
                "input": "find the order",
                "output": "unrelated answer",
                "observations": [],
            }
        },
    )
    captured = invoke(capsys, "trace", "import", "--session-id", "s1", "--path", export)
    session = Path(captured["session_dir"])
    raw = session / "raw" / "t1.json"
    ref = invoke(
        capsys,
        "trace",
        "ref",
        "--session-id",
        "s1",
        "--path",
        str(raw.relative_to(session)),
        "--pointer",
        "/trace/output",
    )
    ref_path = write_json(tmp_path / "ref.json", ref)
    shown = invoke(capsys, "trace", "show", "--session-id", "s1", "--ref", ref_path)
    assert "unrelated answer" in json.dumps(shown)
    issue_data = write_json(
        tmp_path / "issue.json",
        {
            "title": "Result differs from the request",
            "description": "The final output is unrelated to the requested order.",
            "selection_reason": "The requested task failed; cause is still unknown.",
            "evidence": [ref],
        },
    )
    issue = invoke(capsys, "trace", "issue", "--session-id", "s1", "--data", issue_data)
    assert issue["selection_reason"] == "The requested task failed; cause is still unknown."
    issue_id = issue["id"]
    diagnosis_path = tmp_path / "diagnosis.md"
    diagnosis_path.write_text(
        "# Root cause and plan\n\nThe response is unrelated. Its cause is unconfirmed; inspect message routing before changing code.\n",
        encoding="utf-8",
    )
    invoke(capsys, "trace", "diagnose", "--session-id", "s1", "--issue-id", issue_id, "--file", str(diagnosis_path))
    detection = write_json(
        tmp_path / "detection.json",
        {
            "scope": {"read": "trace input and output", "unread": "other sessions"},
            "status": "completed",
            "gaps": ["No runtime source evidence"],
        },
    )
    invoke(capsys, "trace", "detect", "--session-id", "s1", "--data", detection)
    status = invoke(capsys, "trace", "status", "--session-id", "s1")
    assert issue_id in json.dumps(status)
    change_data = write_json(
        tmp_path / "change.json",
        {"status": "running", "before": {"git": "known-baseline"}},
    )
    change_args = ["trace", "change", "--session-id", "s1", "--issue-id", issue_id, "--data", change_data]
    change = invoke(capsys, *change_args)
    write_json(
        Path(change_data),
        {
            "id": change["id"],
            "revision": change["revision"],
            "status": "completed",
            "candidate": {"sha256": "test-candidate-identity"},
            "diff": "synthetic fixture: restore the expected message selection",
        },
    )
    change = invoke(capsys, *change_args)
    candidate_bytes = Path(change["path"]).read_bytes()
    verify_data = write_json(tmp_path / "verify.json", {"status": "running", "execution": {"command": "fixture-check"}})
    verify_args = [
        "trace",
        "verify",
        "--session-id",
        "s1",
        "--issue-id",
        issue_id,
        "--change-id",
        change["id"],
        "--data",
        verify_data,
    ]
    verification = invoke(capsys, *verify_args)
    write_json(
        Path(verify_data),
        {
            "id": verification["id"],
            "revision": verification["revision"],
            "status": "completed",
            "candidate_loaded": False,
            "conclusions": {
                issue_id: {"outcome": "inconclusive", "reason": "Fixture validates recording; no Agent was executed."}
            },
            "limitations": ["No business verification"],
        },
    )
    final = invoke(capsys, *verify_args)
    assert final["conclusions"][issue_id]["outcome"] == "inconclusive"
    assert Path(change["path"]).read_bytes() == candidate_bytes
    preview = invoke(capsys, "visualize", "trace", "--session-id", "s1", "--no-open")
    assert ".html" in json.dumps(preview)
    assert (session / "preview.html").is_file()
    assert not (tmp_path / ".atk/results").exists()
    assert not (tmp_path / ".atk/datasets").exists()
    assert not (tmp_path / ".atk/runner").exists()


def test_trace_cli_missing_identity_and_unknown_format_leave_no_archive(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    source = write_json(tmp_path / "source.json", {"type": "local", "instance": "fixture", "project": "demo"})
    retention = write_json(tmp_path / "retention.json", {"allow_body": True})
    unknown = write_json(tmp_path / "unknown.json", {"trace_id": "only-a-trace"})
    assert (
        main(
            [
                "trace",
                "import",
                "--session-id",
                "s1",
                "--source",
                source,
                "--path",
                unknown,
                "--retention",
                retention,
                "--json",
            ]
        )
        == 2
    )
    assert not (tmp_path / ".atk/traces").exists()
    assert capsys.readouterr().out == ""
