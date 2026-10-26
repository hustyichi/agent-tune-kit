import hashlib
import json

from tests.test_cli_workflow import read_csv, run_cli, successful, write_csv
from tests.test_evidence_import import import_fixture
from tests.test_execution_evidence import evidence
from tests.test_execution_tracing import v2_adapter


def test_bound_judgments_survive_new_import_but_not_changed_evidence(tmp_path):
    v2_adapter(tmp_path)
    successful(tmp_path, "eval")
    root = tmp_path / ".atk/results/v1"
    run = evidence().inspect_run(root)
    digest = hashlib.sha256((root / "eval_results.csv").read_bytes()).hexdigest()
    rows = []
    for aid, identity in run["sample_executions"].items():
        item = run["execution_details"][identity]
        artifact = next(file for file in item["files"] if file["role"] == "output")
        refs = [
            {
                "run_id": run["run_id"],
                "execution_id": identity,
                "evidence_id": item["evidence_id"],
                "artifact_id": artifact["artifact_id"],
                "sha256": artifact["sha256"],
            }
        ]
        rows.append(
            dict(atk_id=aid, verdict="failed", reason="observed", results_sha256=digest, evidence_refs=json.dumps(refs))
        )
    incoming = tmp_path / "judgments.csv"
    write_csv(incoming, list(rows[0]), rows)
    source, adapter = import_fixture(tmp_path)
    successful(tmp_path, "results", "import-evidence", "--source", str(source), "--adapter", str(adapter))
    successful(tmp_path, "results", "apply-judgments", "--version", "v1", "--judgments-path", str(incoming))
    assert read_csv(root / "judgments.csv")[0]["evidence_refs"]
    assert successful(tmp_path, "results", "inspect")["evaluation_target"]["failed"] == 3
    directory = evidence().locate_execution(root, atk_id="1")
    (directory / "output.json").write_text('"changed"')
    state = successful(tmp_path, "results", "inspect")["evaluation_target"]
    assert state["failed"] == 2 and state["unknown"] == 1
    page = successful(tmp_path, "visualize", "failures", "--no-open")
    html = (root / "failure_cases.html").read_text()
    assert page["version"] == "v1"
    assert "evidence" in html
    assert (
        run_cli(tmp_path, "results", "apply-judgments", "--version", "v1", "--judgments-path", str(incoming)).returncode
        != 0
    )


def test_failed_table_cannot_restore_new_format_verdict(tmp_path):
    v2_adapter(tmp_path)
    successful(tmp_path, "eval")
    root = tmp_path / ".atk/results/v1"
    rows = read_csv(root / "eval_results.csv")
    write_csv(root / "failure_cases.csv", list(rows[0]), rows)
    state = successful(tmp_path, "results", "inspect")
    assert state["failed"] == 0 and state["unknown"] == 3
    result = run_cli(tmp_path, "eval", "--only-failures")
    assert result.returncode != 0
    assert not (tmp_path / ".atk/results/v2").exists()
