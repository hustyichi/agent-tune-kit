import json

import pytest

from tests.test_cli_workflow import run_cli
from tests.test_execution_tracing import v2_adapter


@pytest.mark.parametrize("concurrency", [1, 2])
@pytest.mark.parametrize("quiet", [False, True])
def test_v2_progress_counts_executions_and_samples_without_polluting_json(tmp_path, concurrency, quiet):
    path = v2_adapter(tmp_path, "def call_agent(value, *, execution): return value\n")
    with path.open("a") as handle:
        handle.write(
            "def plan_executions(selected_rows, all_rows):\n"
            "    return [{'sample_ids': ['1', '2'], 'source': {'input': 'first'}}, "
            "{'sample_ids': ['3'], 'source': {'input': 'second'}}]\n"
            "def map_agent_output(raw, item):\n"
            "    return {aid: {'output': raw, 'location': ''} for aid in item['sample_ids']}\n"
        )
    flags = ["--no-progress"] if quiet else []

    result = run_cli(tmp_path, "eval", "--concurrency", str(concurrency), *flags, "--json")

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert (summary["executions"], summary["rows"]) == (2, 3)
    if quiet:
        assert result.stderr == ""
    else:
        assert result.stderr.count("Starting execution ") == 2
        assert result.stderr.count("Completed executions ") == 2
        assert "Starting execution 1/2 (2 scored samples)" in result.stderr
        assert "Completed executions 2/2; scored samples 3/3" in result.stderr


def test_v2_check_does_not_emit_execution_progress_or_create_results(tmp_path):
    v2_adapter(tmp_path)

    result = run_cli(tmp_path, "eval", "--check", "--json")

    assert result.returncode == 0
    assert json.loads(result.stdout)["checked"]
    assert result.stderr == ""
    assert not (tmp_path / ".atk/results").exists()
