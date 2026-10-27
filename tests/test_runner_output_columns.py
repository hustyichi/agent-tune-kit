import json

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.execution.runner import open_results_writer
from agent_tune_kit.utils.csv_io import parse_csv
from tests.test_cli_workflow import result_fixture, run_cli, successful, write_adapter, write_csv
from tests.test_execution_tracing import v2_adapter


@pytest.mark.parametrize("adapter", [write_adapter, v2_adapter], ids=["v1", "v2"])
@pytest.mark.parametrize("flags", [(), ("--check",)], ids=["eval", "check"])
@pytest.mark.parametrize("column", ["agent_output", "output_status", "error_type", "error_message", "log_path"])
def test_reserved_columns_stop_evaluation_without_calls_or_file_changes(tmp_path, adapter, flags, column):
    runner = adapter(tmp_path)
    with runner.open("a") as handle:
        handle.write(
            "def call_agent(value, **kwargs):\n"
            "    from pathlib import Path\n"
            "    Path('CALLED').touch()\n"
            "    return value\n"
        )
    dataset = tmp_path / ".atk/datasets/dataset.csv"
    write_csv(dataset, ["atk_id", "input", column], [{"atk_id": "1", "input": "hello", column: "source-value"}])
    result_fixture(tmp_path)
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    result = run_cli(tmp_path, "eval", *flags)

    assert result.returncode == 2, result.stderr
    assert ".atk/datasets/dataset.csv" in result.stderr
    assert column in result.stderr
    assert "rename" in result.stderr.lower()
    assert not (tmp_path / "CALLED").exists()
    assert sorted(path.name for path in (tmp_path / ".atk/results").iterdir()) == ["v1"]
    assert {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_preflight_lists_all_conflicting_columns(tmp_path):
    write_adapter(tmp_path)
    dataset = tmp_path / ".atk/datasets/dataset.csv"
    write_csv(
        dataset,
        ["atk_id", "input", "error_message", "output_status"],
        [{"atk_id": "1", "input": "hello", "error_message": "note", "output_status": "reviewed"}],
    )

    result = run_cli(tmp_path, "eval", "--check")

    assert result.returncode == 2
    assert "error_message, output_status" in result.stderr
    assert not (tmp_path / ".atk/results").exists()


@pytest.mark.parametrize(
    "fields",
    [["input", column] for column in ["agent_output", "output_status", "error_type", "error_message", "log_path"]]
    + [["input", "input"]],
)
@pytest.mark.parametrize("existing", [False, True])
def test_writer_rejects_duplicate_columns_before_opening_output(tmp_path, fields, existing):
    output = tmp_path / "eval_results.csv"
    original = b"existing result must survive\n"
    if existing:
        output.write_bytes(original)

    with pytest.raises(UserActionRequired, match="duplicate"):
        _, handle, _ = open_results_writer(tmp_path, fields, [])
        handle.close()

    if existing:
        assert output.read_bytes() == original
    else:
        assert not output.exists()


@pytest.mark.parametrize("adapter", [write_adapter, v2_adapter], ids=["v1", "v2"])
def test_evaluation_preserves_nonconflicting_source_schema_and_values(tmp_path, adapter):
    adapter(tmp_path)
    source = {
        "atk_id": "1",
        "review_status": "人工已审核",
        "input": "hello,\nworld",
        "expected": "reference",
        "output_status_note": "keep this status note",
        "agent_output_note": "keep this output note",
    }
    dataset = tmp_path / ".atk/datasets/dataset.csv"
    write_csv(dataset, list(source), [source])
    original = dataset.read_bytes()

    result = successful(tmp_path, "eval")
    fields, rows = parse_csv(tmp_path / ".atk/results" / result["version"] / "eval_results.csv")

    assert fields == list(source) + ["agent_output", "output_status", "error_type", "error_message", "log_path"]
    assert len(rows) == 1
    assert {field: rows[0][field] for field in source} == source
    assert json.loads(rows[0]["agent_output"]) == {"answer": source["input"]}
    assert rows[0]["output_status"] == "ok"
    assert dataset.read_bytes() == original
    assert successful(tmp_path, "results", "inspect")["executed"] == 1


def test_dataset_preparation_preserves_business_columns_reserved_by_evaluation(tmp_path):
    source = tmp_path / "source.csv"
    row = {"atk_id": "1", "input": "hello", "output_status": "reviewed"}
    write_csv(source, list(row), [row])
    original = source.read_bytes()

    successful(tmp_path, "dataset", "prepare", "--source", str(source))

    assert parse_csv(tmp_path / ".atk/datasets/dataset.csv") == (list(row), [row])
    assert source.read_bytes() == original
    assert not (tmp_path / ".atk/results").exists()
