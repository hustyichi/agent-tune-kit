import json

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results import versions
from tests.test_execution_evidence import evidence


def test_nonempty_without_csv_is_never_reused(tmp_path):
    first = tmp_path / "v1"
    first.mkdir()
    (first / "app.log").write_text("preserve")
    assert versions.allocate_next_results_version(tmp_path).name == "v2"
    assert (first / "app.log").read_text() == "preserve"


def test_project_occupation_is_exclusive_and_owner_only(tmp_path):
    assert hasattr(versions, "write_occupation")
    with (
        versions.write_occupation(tmp_path),
        pytest.raises(UserActionRequired, match="occupation"),
        versions.write_occupation(tmp_path),
    ):
        pytest.fail("Two writers entered")
    with pytest.raises(UserActionRequired, match="release"), versions.write_occupation(tmp_path):
        lock = tmp_path / ".writer.json"
        lock.write_text('{"owner":"other"}')
    assert lock.exists()
    with pytest.raises(UserActionRequired), versions.write_occupation(tmp_path):
        pytest.fail("Unknown owner was reclaimed")


def test_evidence_only_does_not_hide_evaluation_and_damage_blocks_selection(tmp_path):
    module = evidence()
    assert hasattr(module, "RunEvidence")
    (tmp_path / "v1").mkdir()
    run = module.RunEvidence(tmp_path / "v2", run_kind="evidence-only", origin="imported", policy={})
    run.seal("completed")
    assert versions.resolve_current_version(tmp_path).name == "v2"
    assert versions.resolve_evaluation_version(tmp_path).name == "v1"
    assert versions.resolve_previous_version(tmp_path / "v3", tmp_path).name == "v1"
    (tmp_path / "v3").mkdir()
    assert versions.resolve_evaluation_version(tmp_path).name == "v3"
    (tmp_path / "v3/run.json").write_text("broken")
    with pytest.raises(UserActionRequired):
        versions.resolve_evaluation_version(tmp_path)


def test_run_indexes_single_execution_and_preserves_sample_mapping(tmp_path):
    module = evidence()
    assert hasattr(module, "RunEvidence")
    run = module.RunEvidence(tmp_path / "v1", policy={"retain": lambda role, value: value})
    item = run.execution(["1", "2"])
    item.save("input", "hello", path="input.json")
    item.seal("completed")
    run.seal("completed")
    result = module.inspect_run(run.directory)
    assert result["sample_executions"]["1"] == result["sample_executions"]["2"]
    assert module.locate_execution(run.directory, atk_id="2") == item.directory
    manifest = json.loads((run.directory / "run.json").read_text())
    assert manifest["sealed"]
    (item.directory / "input.json").write_text("damaged")
    with pytest.raises(UserActionRequired):
        module.inspect_run(run.directory)


def test_explicit_version_cannot_escape_results(tmp_path):
    assert hasattr(versions, "resolve_evaluation_version")
    with pytest.raises(UserActionRequired):
        versions.resolve_evaluation_version(tmp_path, version="../v1")


def test_broken_manifest_symlink_is_not_legacy(tmp_path):
    root = tmp_path / "v1"
    root.mkdir()
    (root / "run.json").symlink_to(root / "missing")
    with pytest.raises(UserActionRequired):
        versions.resolve_evaluation_version(tmp_path)
