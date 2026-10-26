import json

from agent_tune_kit.execution import runner


def test_managed_input_excludes_truth_but_result_preserves_it(tmp_path, monkeypatch):
    captured = []

    def build(row):
        captured.append(dict(row))
        return row["input"]

    # The V1 engine installs these globals when loading a project adapter.
    monkeypatch.setattr(runner, "build_agent_input", build, raising=False)
    monkeypatch.setattr(runner, "call_agent", lambda value: {"answer": value}, raising=False)
    row = {"atk_id": "1", "input": "hello", "atk_truth": "review metadata"}
    result = runner.build_result_row(1, row, version_dir=tmp_path, row_logging_enabled=False)
    assert "atk_truth" not in captured[0]
    assert result["atk_truth"] == row["atk_truth"]
    assert row["atk_truth"] == "review metadata"
    assert json.loads(result["agent_output"]) == {"answer": "hello"}
