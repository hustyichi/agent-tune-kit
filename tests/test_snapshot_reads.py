from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.execution.runner import run_evaluation
from tests.test_execution_evidence import evidence
from tests.test_execution_tracing import v2_adapter


def test_snapshot_read_ignores_access_time_changes(tmp_path, monkeypatch):
    adapter = v2_adapter(tmp_path)
    with adapter.open("a") as handle:
        handle.write("EVIDENCE['snapshots'] = ['prompt.txt']\n")
    (tmp_path / "prompt.txt").write_text("before")
    original_stat = Path.stat
    reads = 0

    def stat(path, *args, **kwargs):
        nonlocal reads
        result = original_stat(path, *args, **kwargs)
        if path.name != "prompt.txt":
            return result
        reads += 1
        fields = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
        fields.update(st_atime=result.st_atime + reads, st_atime_ns=result.st_atime_ns + reads)
        return SimpleNamespace(**fields)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "stat", stat)

    result = run_evaluation(limit=1, no_progress=True)

    assert result["rows"] == 1
    root = tmp_path / ".atk/results/v1"
    directory = evidence().locate_execution(root, atk_id="1")
    manifest = evidence().inspect_execution(directory)
    snapshot = next(item for item in manifest["files"] if item.get("source", {}).get("path") == "prompt.txt")
    assert (directory / snapshot["path"]).read_text() == "before"
    assert "snapshot_drift" not in str(manifest["coverage"])


def test_snapshot_source_changed_during_read_still_stops_before_agent(tmp_path, monkeypatch):
    adapter = v2_adapter(tmp_path)
    with adapter.open("a") as handle:
        handle.write("EVIDENCE['snapshots'] = ['prompt.txt']\n")
    (tmp_path / "prompt.txt").write_text("before")
    original_read = Path.read_bytes

    def read(path):
        data = original_read(path)
        if path.name == "prompt.txt":
            path.write_text("changed")
        return data

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "read_bytes", read)

    with pytest.raises(UserActionRequired, match="preserved scene"):
        run_evaluation(limit=1, no_progress=True)

    assert not list((tmp_path / ".atk/results/v1").glob("executions/*"))
