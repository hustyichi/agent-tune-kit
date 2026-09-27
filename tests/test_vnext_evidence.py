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
