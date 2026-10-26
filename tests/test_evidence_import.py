import shutil

from tests.test_cli_workflow import result_fixture, run_cli, successful


def import_fixture(project):
    source = project / "native"
    source.mkdir()
    (source / "events.jsonl").write_text('{"tool_use_id":"tool-1","turn_id":"turn-1"}\n')
    (source / "output.txt").write_text("你好🙂")
    adapter = project / "importer.py"
    adapter.write_text(
        "ATK_IMPORT_VERSION = 1\n"
        "EVIDENCE = {'retain': lambda role, value: value}\n"
        "def load_evidence(source_root):\n"
        "    import json\n"
        "    return {'source_system': 'magic-fixture', 'source_run_id': 'native-run', 'executions': [\n"
        "        {'source_id': 'case-1', 'status': 'completed', 'input': {'case': 'one'}, 'output': 'reply',\n"
        "         'coverage': {'status': 'partial', 'sources': [{'source': 'frontend', 'filtered': True}], 'gaps': ['missing trace']},\n"
        "         'files': [{'path': 'events.jsonl', 'role': 'native-events', 'media_type': 'application/x-ndjson'},\n"
        "                   {'path': 'output.txt', 'role': 'result', 'media_type': 'text/plain'}],\n"
        "         'events': [{'kind': 'tool.result', 'attributes': json.loads((source_root / 'events.jsonl').read_text()),\n"
        "                     'source': {'file': 'events.jsonl', 'line': 1}}]}]}\n"
    )
    return source, adapter


def test_import_is_idempotent_and_independently_readable(tmp_path):
    source, adapter = import_fixture(tmp_path)
    original = {path.name: path.read_bytes() for path in source.iterdir()}
    result = successful(tmp_path, "results", "import-evidence", "--source", str(source), "--adapter", str(adapter))
    root = tmp_path / ".atk/results" / result["version"]
    assert not (root / "eval_results.csv").exists()
    assert result["run_kind"] == "evidence-only"
    again = successful(tmp_path, "results", "import-evidence", "--source", str(source), "--adapter", str(adapter))
    assert again["version"] == result["version"] and again["reused"]
    execution = next((root / "executions").iterdir())
    copied = tmp_path / "copy"
    shutil.copytree(execution, copied)
    view = successful(tmp_path, "results", "evidence", "--execution-dir", str(copied))
    assert view["origin"] == "imported"
    assert view["coverage"]["gaps"] == ["missing trace"]
    events = successful(tmp_path, "results", "evidence", "--execution-dir", str(copied), "--after-seq", "0")
    assert events["events"][0]["attributes"]["tool_use_id"] == "tool-1"
    assert original == {path.name: path.read_bytes() for path in source.iterdir()}
    (source / "output.txt").write_text("late trace or changed output")
    failed = run_cli(tmp_path, "results", "import-evidence", "--source", str(source), "--adapter", str(adapter))
    assert failed.returncode != 0
    assert len(list((tmp_path / ".atk/results").glob("v*"))) == 1


def test_import_source_escape_is_rejected_without_reading_outside(tmp_path):
    source, adapter = import_fixture(tmp_path)
    outside = tmp_path / "secret.txt"
    outside.write_text("SECRET")
    (source / "output.txt").unlink()
    (source / "output.txt").symlink_to(outside)
    result = run_cli(tmp_path, "results", "import-evidence", "--source", str(source), "--adapter", str(adapter))
    assert result.returncode != 0 and "SECRET" not in result.stderr


def test_inspect_separates_latest_run_from_evaluation_target(tmp_path):
    result_fixture(tmp_path)
    source, adapter = import_fixture(tmp_path)
    successful(tmp_path, "results", "import-evidence", "--source", str(source), "--adapter", str(adapter))
    state = successful(tmp_path, "results", "inspect")
    assert state["latest_run"]["version"] == "v2"
    assert state["evaluation_target"]["version"] == "v1"
    assert state["evaluation_target"]["executed"] == 3


def test_explicit_inspect_can_read_evidence_only_run(tmp_path):
    source, adapter = import_fixture(tmp_path)
    successful(tmp_path, "results", "import-evidence", "--source", str(source), "--adapter", str(adapter))
    state = successful(tmp_path, "results", "inspect", "--version", "v1")
    assert state["version"] == "v1"
    assert state["evaluation_target"] is None


def test_import_event_iterator_is_not_materialized_as_a_list(tmp_path):
    source, adapter = import_fixture(tmp_path)
    with adapter.open("a") as handle:
        handle.write(
            "\noriginal_load = load_evidence\n"
            "class Events:\n"
            "    def __iter__(self):\n"
            "        for i in range(100): yield {'kind': 'native', 'attributes': {'index': i}}\n"
            "    def __len__(self): raise RuntimeError('Do not materialize source events')\n"
            "def load_evidence(source_root):\n"
            "    payload = original_load(source_root)\n"
            "    payload['executions'][0]['events'] = Events()\n"
            "    return payload\n"
        )
    result = successful(tmp_path, "results", "import-evidence", "--source", str(source), "--adapter", str(adapter))
    root = tmp_path / ".atk/results" / result["version"]
    item = next((root / "executions").iterdir())
    from tests.test_execution_evidence import evidence

    events = evidence().read_events(item, limit=1000)["events"]
    assert len([event for event in events if event["kind"] == "native"]) == 100


def test_magic_two_case_fixture_preserves_native_ids_and_file_phases(tmp_path):
    import json

    source, adapter = import_fixture(tmp_path)
    payload = {"source_system": "magic-synthetic", "source_run_id": "two-cases", "executions": []}
    for number, tool_count in enumerate([11, 16]):
        before, after = f"before-{number}.txt", f"after-{number}.txt"
        (source / before).write_text("before")
        (source / after).write_text("after")
        payload["executions"].append(
            {
                "source_id": f"case-{number}",
                "status": "completed",
                "coverage": {
                    "status": "partial",
                    "sources": [{"source": "frontend", "filtered": True}],
                    "gaps": ["trace missing", "actor of file change unknown"],
                },
                "files": [
                    {"path": before, "role": "workspace", "phase": "before"},
                    {"path": after, "role": "workspace", "phase": "after"},
                ],
                "events": [
                    {
                        "kind": kind,
                        "attributes": {
                            "tool_use_id": f"tool-{i}",
                            "session": f"session-{number}",
                            "turn": f"turn-{number}",
                            "event_id": f"native-{i}-{kind}",
                        },
                    }
                    for i in range(tool_count)
                    for kind in ["tool.started", "tool.result"]
                ],
            }
        )
    (source / "native.json").write_text(json.dumps(payload))
    adapter.write_text(
        "import json\nATK_IMPORT_VERSION=1\nEVIDENCE={'retain':lambda role,value:value}\n"
        "def load_evidence(root): return json.loads((root/'native.json').read_text())\n"
    )
    result = successful(tmp_path, "results", "import-evidence", "--source", str(source), "--adapter", str(adapter))
    from tests.test_execution_evidence import evidence

    root = tmp_path / ".atk/results" / result["version"]
    run = evidence().inspect_run(root)
    counts = []
    for relative in run["executions"].values():
        item = evidence().inspect_execution(root / relative)
        assert {entry["source"]["phase"] for entry in item["files"] if entry["role"] == "workspace"} == {
            "before",
            "after",
        }
        events = evidence().read_events(root / relative, limit=1000)["events"]
        counts.append(len([event for event in events if event["kind"] == "tool.result"]))
        assert "actor of file change unknown" in item["coverage"]["gaps"]
    assert sorted(counts) == [11, 16]
