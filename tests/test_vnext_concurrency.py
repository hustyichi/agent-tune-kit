from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent_tune_kit.checkpoints import freeze_round
from agent_tune_kit.core import ATKError, read_json, validate_evidence
from agent_tune_kit.execution import run_evaluation
from tests.test_vnext_limits import project


def setup_run(tmp_path: Path, script: str, concurrency: int, ids: list[str]) -> tuple[Path, dict]:
    rows = [{"id": cid, "input": cid, "usage": "optimization", "source_group_id": cid} for cid in ids]
    repo, root, dataset, rnd, plan = project(tmp_path, script, rows)
    plan["concurrency"] = concurrency
    plan["budget"]["executions"] = 3 * len(ids)
    freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    return root, {
        "dataset_id": dataset["id"],
        "case_ids": ids,
        "purpose": "evaluation",
        "round_id": rnd["id"],
        "revision_id": rnd["baseline_revision_id"],
        "concurrency": concurrency,
    }


@pytest.mark.parametrize("concurrency", [1, 2])
def test_default_runner_obeys_concurrency_and_attempt_identities(tmp_path: Path, concurrency: int) -> None:
    root, request = setup_run(
        tmp_path, "import time\ntime.sleep(0.3)\nprint('ok')\n", concurrency, ["a", "b", "c", "d"]
    )
    batch = run_evaluation(root, request)
    assert batch["status"] == "sealed"
    assert batch["concurrency"] == concurrency
    _, records, _ = validate_evidence(root, batch["id"])
    assert len({r["execution"]["id"] for r in records.values()}) == 4
    events = sorted(
        [
            (r["execution"][field], step)
            for r in records.values()
            for field, step in (("started_at", 1), ("ended_at", -1))
        ]
    )
    active = peak = 0
    for _, step in events:
        active += step
        peak = max(peak, active)
    assert peak == concurrency and active == 0
    status = read_json(root / "evidence" / batch["id"] / "batch.json")
    assert not status["running_record_ids"] and not status["not_started_record_ids"]
    assert set(status["completed_record_ids"]) == set(records)


@pytest.mark.parametrize("concurrency", [0, -1, True, 1.5, "2", None, 2])
def test_invalid_or_unfrozen_concurrency_does_not_reserve_budget(tmp_path: Path, concurrency: object) -> None:
    root, request = setup_run(tmp_path, "print('ok')", 1, ["a"])
    with pytest.raises(ATKError, match="concurrency"):
        run_evaluation(root, {**request, "concurrency": concurrency})
    assert not (root / "rounds" / request["round_id"] / "budget-usage.json").exists()
    assert not list((root / "evidence").glob("batch-*"))


def test_parallel_attempt_timeout_keeps_other_results(tmp_path: Path) -> None:
    script = "import json,sys,time\nfrom pathlib import Path\nx=json.loads(Path(sys.argv[1]).read_text())\ntime.sleep(3 if x=='slow' else 0.1)\nprint('ok')\n"
    root, request = setup_run(tmp_path, script, 2, ["slow", "fast", "next"])
    batch = run_evaluation(root, {**request, "timeout_seconds": 1})
    _, records, _ = validate_evidence(root, batch["id"])
    assert batch["status"] == "sealed"
    assert {r["case_id"]: r["execution"]["status"] for r in records.values()} == {
        "slow": "timeout",
        "fast": "completed",
        "next": "completed",
    }


def test_parallel_batch_timeout_stops_agent_process_groups(tmp_path: Path) -> None:
    script = "import sys,time,subprocess\nfrom pathlib import Path\np=Path(sys.argv[2])\n(p/'started').write_text('yes')\nsubprocess.Popen([sys.executable,'-c',\"import time;from pathlib import Path;time.sleep(2);Path(\"+repr(str(p/'leak'))+\").write_text('bad')\"])\ntime.sleep(10)\n"
    root, request = setup_run(tmp_path, script, 2, ["a", "b", "c"])
    batch = run_evaluation(root, {**request, "batch_timeout_seconds": 1})
    assert batch["status"] == "interrupted"
    assert len(batch["unknown_record_ids"]) == 2
    assert len(batch["not_started_record_ids"]) == 1
    attempts = root / "evidence" / batch["id"] / "attempts"
    assert len(list(attempts.glob("*/started"))) == 2
    time.sleep(1.2)
    assert not list(attempts.glob("*/leak"))


def test_parallel_runner_failure_preserves_prefix_and_pending_status(tmp_path: Path) -> None:
    script = "import json,sys,time\nfrom pathlib import Path\nx=json.loads(Path(sys.argv[1]).read_text())\np=Path(sys.argv[2])\ntime.sleep(0.1 if x=='fast' else 0.5 if x=='bad' else 10)\nif x=='bad': (p/'loading.json').write_text('{')\nprint('ok')\n"
    root, request = setup_run(tmp_path, script, 2, ["fast", "bad", "slow", "never"])
    batch = run_evaluation(root, request)
    _, records, _ = validate_evidence(root, batch["id"])
    assert batch["status"] == "partial"
    assert [r["case_id"] for r in records.values()] == ["fast"]
    assert len(batch["unknown_record_ids"]) == 2
    assert len(batch["not_started_record_ids"]) == 1
