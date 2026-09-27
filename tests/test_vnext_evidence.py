from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import pytest

from agent_tune_kit.core import (
    ATKError,
    read_assessment,
    render_assessment_html,
    safe_id,
    store_assessment,
    validate_evidence,
)
from agent_tune_kit.evidence import import_evidence, record_source_contract


@pytest.mark.parametrize("extension", ["json", "jsonl", "json.gz"])
def test_trace_bundle_keeps_missing_parent_and_revisions(tmp_path: Path, extension: str) -> None:
    trace = {
        "id": "trace-1",
        "input": {"authorization": "secret", "task": "hello"},
        "output": "api_key=secret-value done",
        "scores": [{"name": "external", "value": 0.7}],
        "observations": [
            {
                "id": "obs-1",
                "traceId": "trace-1",
                "type": "TOOL",
                "parentObservationId": "not-exported",
                "output": "tool result",
            }
        ],
    }
    source = tmp_path / f"trace.{extension}"
    raw = json.dumps(trace).encode()
    if extension == "jsonl":
        source.write_bytes(raw + b"\n")
    elif extension.endswith(".gz"):
        source.write_bytes(gzip.compress(raw))
    else:
        source.write_bytes(raw)
    root = tmp_path / "state"
    request = {
        "source": str(source),
        "source_kind": "langfuse",
        "source_namespace": "sample",
        "adapter_profile": "langfuse_trace_bundle",
        "mapping_version": "1",
    }
    batch = import_evidence(root, request)
    assert import_evidence(root, request)["id"] == batch["id"]
    _, records, index = validate_evidence(root, batch["id"])
    record = next(iter(records.values()))
    assert record["input"]["authorization"] == "[REDACTED]"
    assert "secret-value" not in json.dumps(record)
    assert record["missing"] == ["parent_observations"]
    assert record["execution"] is None
    assert "observation:obs-1" in index
    trace["output"] = "corrected"
    raw = json.dumps(trace).encode()
    if extension == "jsonl":
        source.write_bytes(raw + b"\n")
    elif extension.endswith(".gz"):
        source.write_bytes(gzip.compress(raw))
    else:
        source.write_bytes(raw)
    updated = import_evidence(root, request)
    assert updated["id"] != batch["id"]
    assert updated["supersedes_batch_id"] == batch["id"]
    assert validate_evidence(root, batch["id"])[0]["id"] == batch["id"]


def test_observation_csv_does_not_promote_tool_output_to_trace_output(tmp_path: Path) -> None:
    source = tmp_path / "observations.csv"
    with source.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "traceId", "type", "output", "parentObservationId"])
        writer.writeheader()
        writer.writerow(
            {
                "id": "tool-1",
                "traceId": "trace-1",
                "type": "TOOL",
                "output": "tool says yes",
                "parentObservationId": "missing-root",
            }
        )
    root = tmp_path / "state"
    batch = import_evidence(
        root,
        {
            "source": str(source),
            "source_kind": "langfuse",
            "source_namespace": "sample",
            "adapter_profile": "langfuse_observation_rows",
            "mapping_version": "1",
        },
    )
    _, records, _ = validate_evidence(root, batch["id"])
    record = next(iter(records.values()))
    assert not record["output_present"]
    assert "trace_output" in record["missing"]
    assert record["events"][0]["output"] == "tool says yes"


def test_root_observation_without_parent_is_complete(tmp_path: Path) -> None:
    source = tmp_path / "trace.json"
    source.write_text(
        json.dumps({"id": "trace-1", "input": "task", "output": "done", "observations": [{"id": "root"}]})
    )
    batch = import_evidence(
        tmp_path / "state",
        {
            "source": str(source),
            "source_kind": "langfuse",
            "source_namespace": "sample",
            "adapter_profile": "langfuse_trace_bundle",
            "mapping_version": "1",
        },
    )
    assert next(iter(validate_evidence(tmp_path / "state", batch["id"])[1].values()))["missing"] == []


def test_import_revisions_when_mapping_or_redaction_changes(tmp_path: Path) -> None:
    source = tmp_path / "results.csv"
    source.write_text("question,answer,alternate,private_note\nhello,first,second,customer name\n")
    root = tmp_path / "state"
    request = {
        "source": str(source),
        "source_kind": "batch_results",
        "source_namespace": "sample",
        "mapping_version": "1",
        "mapping": {"input": "question", "output": "answer"},
    }
    first = import_evidence(root, request)
    mapped = import_evidence(root, {**request, "mapping": {"input": "question", "output": "alternate"}})
    assert mapped["id"] != first["id"]
    assert mapped["supersedes_batch_id"] == first["id"]
    assert next(iter(validate_evidence(root, mapped["id"])[1].values()))["output"] == "second"
    assert (
        import_evidence(root, {**request, "mapping": {"input": "question", "output": "alternate"}})["id"]
        == mapped["id"]
    )
    redacted = import_evidence(root, {**request, "redact_keys": ["answer"]})
    assert redacted["id"] != first["id"]
    assert redacted["redaction"]["extra_keys"] == ["answer"]
    assert next(iter(validate_evidence(root, redacted["id"])[1].values()))["output"] == "[REDACTED]"


def test_trace_bundle_joins_separate_observations_and_scores(tmp_path: Path) -> None:
    source = tmp_path / "export"
    source.mkdir()
    (source / "traces.json").write_text(
        json.dumps(
            [
                {
                    "trace_key": "trace-1",
                    "input": None,
                    "output": "done",
                    "timestamp": "2026-09-26T09:59:59Z",
                    "endTime": "unknown",
                    "level": "WARNING",
                    "statusMessage": "source warning",
                    "model": "model-x",
                    "metadata": {"accessToken": "secret", "totalTokens": 2, "token_usage": {"input_tokens": 1}},
                }
            ]
        )
    )
    (source / "observations.csv").write_text(
        "id,trace_id,parent_observation_id,type,name,start_time,end_time,level,status_message,model,usage\n"
        'obs-1,trace-1,,GENERATION,answer,2026-09-26T10:00:00Z,not-a-time,ERROR,warning,model-x,"{""totalTokens"":2}"\n'
    )
    (source / "scores.json").write_text(
        json.dumps([{"id": "score-1", "traceId": "trace-1", "name": "review", "value": 0.7}])
    )
    request = {
        "source": str(source),
        "source_kind": "langfuse",
        "source_namespace": "sample",
        "adapter_profile": "langfuse_trace_bundle",
        "mapping_version": "1",
        "mapping": {"trace": {"id": "trace_key"}},
        "file_roles": {"traces.json": "trace", "observations.csv": "observation", "scores.json": "score"},
        "json_columns": ["usage"],
    }
    root = tmp_path / "state"
    batch = import_evidence(root, request)
    _, records, index = validate_evidence(root, batch["id"])
    assert len(records) == 1
    record = next(iter(records.values()))
    assert record["input_present"] and record["input"] is None
    assert record["raw_started_at"] == "2026-09-26T09:59:59Z"
    assert record["started_at"] == "2026-09-26T09:59:59+00:00"
    assert record["raw_ended_at"] == "unknown" and record["ended_at"] is None
    assert record["source_status"] == {"level": "WARNING", "message": "source warning"}
    assert record["runtime_metadata"]["model"] == "model-x"
    assert record["runtime_metadata"]["metadata"] == {
        "accessToken": "[REDACTED]",
        "totalTokens": 2,
        "token_usage": {"input_tokens": 1},
    }
    assert record["external_scores"] == [{"id": "score-1", "traceId": "trace-1", "name": "review", "value": 0.7}]
    event = record["events"][0]
    assert event["event_id"] == "obs-1"
    assert event["parent_event_id"] is None
    assert event["event_type"] == "GENERATION"
    assert event["event_name"] == "answer"
    assert event["started_at"] == "2026-09-26T10:00:00+00:00"
    assert event["raw_started_at"] == "2026-09-26T10:00:00Z"
    assert event["ended_at"] is None and event["raw_ended_at"] == "not-a-time"
    assert event["source_status"] == {"level": "ERROR", "message": "warning"}
    assert event["runtime_metadata"]["usage"] == {"totalTokens": 2}
    assert "observation:obs-1" in index and "score:score-1" in index
    assert batch["mapping_version"] == "1" and batch["file_roles"] == request["file_roles"]
    assessment = store_assessment(
        root,
        {
            "batch_id": batch["id"],
            "evaluation_spec": {
                "version": "v1",
                "boundary": "imported trace",
                "dimensions": ["task_success"],
                "dimension_rules": {"task_success": {"validity": "trace output present", "attribution": "unknown"}},
                "denominator_rule": "one trace",
            },
            "judger": {"version": "v1", "readiness": "uncalibrated"},
            "rows": [
                {
                    "record_id": record["id"],
                    "dimension": "task_success",
                    "validity": "valid",
                    "validity_reason": "",
                    "verdict": "pass",
                    "score": None,
                    "reason": "trace output present",
                    "evidence_refs": [{"batch_id": batch["id"], "evidence_id": "observation:obs-1"}],
                    "judger_kind": "deterministic",
                }
            ],
        },
    )
    assert assessment.is_file()
    with pytest.raises(ATKError, match="unknown Trace"):
        (source / "scores.json").write_text(json.dumps([{"id": "score-1", "traceId": "missing", "value": 0.7}]))
        import_evidence(root, request)


def test_root_observation_mapping_requires_unique_named_root(tmp_path: Path) -> None:
    source = tmp_path / "trace.json"
    trace = {
        "id": "trace-1",
        "observations": [
            {"id": "tool", "name": "tool", "type": "TOOL", "output": "not final"},
            {"id": "agent", "name": "agent", "type": "SPAN", "input": "task", "output": "final"},
        ],
    }
    source.write_text(json.dumps(trace))
    root = tmp_path / "state"
    request = {
        "source": str(source),
        "source_kind": "langfuse",
        "source_namespace": "sample",
        "adapter_profile": "langfuse_trace_bundle",
        "mapping_version": "1",
    }
    default = import_evidence(root, request)
    default_record = next(iter(validate_evidence(root, default["id"])[1].values()))
    assert not default_record["output_present"]
    mapped = import_evidence(root, {**request, "mapping": {"root_observation_name": "agent"}})
    mapped_record = next(iter(validate_evidence(root, mapped["id"])[1].values()))
    assert mapped_record["input"] == "task" and mapped_record["output"] == "final"
    assert mapped_record["output_source"] == "observation:agent"
    trace["observations"].append({"id": "other-agent", "name": "agent", "output": "ambiguous"})
    source.write_text(json.dumps(trace))
    ambiguous = import_evidence(root, {**request, "mapping": {"root_observation_name": "agent"}})
    ambiguous_record = next(iter(validate_evidence(root, ambiguous["id"])[1].values()))
    assert not ambiguous_record["output_present"]
    assert "trace_output" in ambiguous_record["missing"]


def test_observation_rows_use_declared_field_aliases_and_reject_conflicts(tmp_path: Path) -> None:
    source = tmp_path / "observations.json"
    source.write_text(
        json.dumps(
            [
                {
                    "observation_key": "obs-1",
                    "trace_key": "trace-1",
                    "kind": "SPAN",
                    "label": "agent",
                    "started": "2026-09-26T10:00:00Z",
                    "input": "task",
                    "output": "done",
                }
            ]
        )
    )
    root = tmp_path / "state"
    request = {
        "source": str(source),
        "source_kind": "langfuse",
        "source_namespace": "sample",
        "adapter_profile": "langfuse_observation_rows",
        "mapping_version": "2",
        "mapping": {
            "observation": {
                "id": "observation_key",
                "traceId": "trace_key",
                "type": "kind",
                "name": "label",
                "startTime": "started",
            },
            "root_observation_name": "agent",
        },
    }
    batch = import_evidence(root, request)
    _, records, index = validate_evidence(root, batch["id"])
    record = next(iter(records.values()))
    assert record["source_trace_id"] == "trace-1"
    assert record["input"] == "task" and record["output"] == "done"
    assert record["events"][0]["started_at"] == "2026-09-26T10:00:00+00:00"
    assert "observation:obs-1" in index
    source.write_text(json.dumps([{"id": "wrong", "observation_key": "obs-1", "trace_key": "trace-1"}]))
    with pytest.raises(ATKError, match="conflicting field mapping"):
        import_evidence(root, request)


def test_assessment_requires_evidence_for_its_own_record(tmp_path: Path) -> None:
    source = tmp_path / "results.csv"
    source.write_text("input,output\nfirst,one\nsecond,two\n")
    root = tmp_path / "state"
    batch = import_evidence(
        root,
        {
            "source": str(source),
            "source_kind": "batch_results",
            "source_namespace": "sample",
            "mapping_version": "1",
            "mapping": {"input": "input", "output": "output"},
        },
    )
    record_ids = list(validate_evidence(root, batch["id"])[1])
    rows = [
        {
            "record_id": record_id,
            "dimension": "task_success",
            "validity": "valid",
            "validity_reason": "",
            "verdict": "pass",
            "score": None,
            "reason": "source output present",
            "evidence_refs": [{"batch_id": batch["id"], "evidence_id": record_ids[1 - index]}],
            "judger_kind": "deterministic",
        }
        for index, record_id in enumerate(record_ids)
    ]
    request = {
        "batch_id": batch["id"],
        "evaluation_spec": {
            "version": "v1",
            "boundary": "imported results",
            "dimensions": ["task_success"],
            "dimension_rules": {"task_success": {"validity": "output present", "attribution": "unknown"}},
            "denominator_rule": "all rows",
        },
        "judger": {"version": "v1", "readiness": "uncalibrated"},
        "rows": rows,
    }
    with pytest.raises(ATKError, match="own record"):
        store_assessment(root, request)
    for row in rows:
        row["evidence_refs"].append({"batch_id": batch["id"], "evidence_id": row["record_id"]})
    assert store_assessment(root, request).is_file()


def test_artifact_id_cannot_escape_workspace() -> None:
    with pytest.raises(ATKError, match="unsafe artifact ID"):
        safe_id("../../outside")


def test_source_contract_keeps_source_and_artifact_identities_separate(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "contract.py"
    source.write_text("# supports direct call\napi_key='secret-value'\n# rejects malformed input\n")
    root = repo / ".atk"
    manifest = record_source_contract(
        repo,
        root,
        {
            "source_path": "contract.py",
            "start_line": 1,
            "end_line": 3,
            "component_id": "tool",
            "source_revision": "source@v1",
            "artifact_identity": "binary@v2",
        },
    )
    _, records, index = validate_evidence(root, manifest["id"])
    record = next(iter(records.values()))
    assert record["source_revision"] == "source@v1"
    assert record["artifact_identity"] == "binary@v2"
    assert next(iter(index.values()))["origin"] == "source_contract"
    assert "secret-value" not in (root / "evidence" / manifest["id"] / "records.jsonl").read_text()
    assert "[REDACTED SECRET LINE]" in record["excerpt"]
    with pytest.raises(ATKError, match="inside the target project"):
        record_source_contract(
            repo,
            root,
            {
                "source_path": "../outside",
                "start_line": 1,
                "end_line": 1,
                "component_id": "tool",
                "source_revision": "source@v1",
            },
        )


def test_assessment_csv_is_authoritative_and_tampering_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "results.csv"
    source.write_text("input,output\nhi,hello\n")
    root = tmp_path / "state"
    batch = import_evidence(
        root,
        {
            "source": str(source),
            "source_kind": "batch_results",
            "source_namespace": "sample",
            "mapping_version": "1",
            "mapping": {"input": "input", "output": "output"},
        },
    )
    _, records, _ = validate_evidence(root, batch["id"])
    record_id = next(iter(records))
    assessment_request = {
        "batch_id": batch["id"],
        "evaluation_spec": {
            "version": "v1",
            "boundary": "imported result",
            "dimensions": ["task_success"],
            "dimension_rules": {"task_success": {"validity": "source present", "attribution": "unknown"}},
            "denominator_rule": "valid imported records",
        },
        "judger": {"version": "v1", "readiness": "uncalibrated"},
        "rows": [
            {
                "record_id": record_id,
                "dimension": "task_success",
                "validity": "valid",
                "validity_reason": "",
                "verdict": "pass",
                "score": None,
                "reason": "<script>alert(1)</script>",
                "evidence_refs": [{"batch_id": batch["id"], "evidence_id": record_id}],
                "judger_kind": "semantic",
            }
        ],
    }
    with pytest.raises(ATKError, match="invalid evidence reference"):
        store_assessment(root, {**assessment_request, "rows": [{**assessment_request["rows"][0], "evidence_refs": []}]})
    bad_judger = {
        "version": "bad-v1",
        "readiness": "calibrated",
        "calibration_examples": [
            {"source_ref": "positive", "expected_verdict": "pass", "actual_verdict": "pass"},
            {"source_ref": "negative", "expected_verdict": "fail", "actual_verdict": "pass"},
        ],
    }
    with pytest.raises(ATKError, match="matched positive and negative"):
        store_assessment(root, {**assessment_request, "judger": bad_judger})
    path = store_assessment(root, assessment_request)
    manifest, rows = read_assessment(root, path.parent.name)
    assert manifest["judger_readiness"] == "uncalibrated"
    assert rows[0]["verdict"] == "pass"
    report = render_assessment_html(root, path.parent.name).read_text()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report
    assert "<script>" not in report
    csv_path = path.parent / "assessment.csv"
    csv_path.write_text(csv_path.read_text().replace("pass", "fail"))
    with pytest.raises(ATKError, match="fingerprint mismatch"):
        read_assessment(root, path.parent.name)
