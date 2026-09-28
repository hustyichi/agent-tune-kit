from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_tune_kit.checkpoints import (
    create_round,
    freeze_round,
    inspect_or_recover_operation,
    prepare_candidate,
    seal_candidate,
)
from agent_tune_kit.core import ATKError, digest, read_json, validate_evidence, write_json
from agent_tune_kit.execution import initialize_project, load_cases, run_evaluation, store_dataset
from tests.test_vnext_flow import git
from tests.test_vnext_limits import project


def test_attachment_identity_and_runner_delivery(tmp_path: Path) -> None:
    attachment = tmp_path / "document.txt"
    attachment.write_text("first")
    script = (
        "import json,sys\nfrom pathlib import Path\n"
        "files=json.loads(Path(sys.argv[3]).read_text())\n"
        "print(Path(files[0]['path']).read_text())\n"
    )
    repo, root, _, rnd, plan = project(
        tmp_path, script, [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "g"}]
    )
    config = read_json(root / "project.json")
    config["command"].append("{attachments_file}")
    write_json(root / "project.json", config)
    source = tmp_path / "attached.jsonl"
    source.write_text(json.dumps({"id": "case", "input": "task", "attachments": ["document.txt"]}) + "\n")
    mapping = {"id": "id", "input": "input", "attachments": "attachments"}
    dataset = store_dataset(root, {"source": str(source), "mapping": mapping})
    first_fingerprint = load_cases(root, dataset["id"])["case"]["fingerprint"]
    plan["dataset_id"] = dataset["id"]
    freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "evaluation",
        "round_id": rnd["id"],
        "revision_id": rnd["baseline_revision_id"],
    }
    batch = run_evaluation(root, request)
    record = next(iter(validate_evidence(root, batch["id"])[1].values()))
    assert record["output"].strip() == "first"
    attachment.write_text("other")
    replacement = store_dataset(root, {"source": str(source), "mapping": mapping})
    assert load_cases(root, replacement["id"])["case"]["fingerprint"] != first_fingerprint
    with pytest.raises(ATKError, match="attachment"):
        run_evaluation(root, request)
    attachment.unlink()
    with pytest.raises(ATKError, match="attachment"):
        run_evaluation(root, request)


@pytest.mark.parametrize("changed", ["input", "usage", "source_group_id"])
def test_frozen_case_content_blocks_same_round_replacement(tmp_path: Path, changed: str) -> None:
    rows = [{"id": "case", "input": "old", "usage": "optimization", "source_group_id": "g"}]
    repo, root, dataset, rnd, plan = project(tmp_path, "print('ok')\n", rows)
    freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "evaluation",
        "round_id": rnd["id"],
        "revision_id": rnd["baseline_revision_id"],
    }
    run_evaluation(root, request)
    changed_row = {**rows[0], changed: {"input": "new", "usage": "protection", "source_group_id": "other"}[changed]}
    source = tmp_path / "changed.csv"
    source.write_text("id,input,usage,source_group_id\n" + ",".join(changed_row.values()) + "\n")
    replacement = store_dataset(
        root,
        {"source": str(source), "mapping": {key: key for key in rows[0]}},
    )
    with pytest.raises(ATKError, match="frozen dataset"):
        run_evaluation(root, {**request, "dataset_id": replacement["id"]})
    plan_path = root / "rounds" / rnd["id"] / "plan.json"
    legacy = read_json(plan_path)
    del legacy["case_fingerprints"]
    write_json(plan_path, legacy)
    with pytest.raises(ATKError, match="frozen dataset"):
        run_evaluation(root, request)


def test_pending_candidate_replays_parent_and_recovers_added_deleted_paths(tmp_path: Path) -> None:
    rows = [{"id": "case", "input": "x", "usage": "optimization", "source_group_id": "g"}]
    script = (
        "from pathlib import Path\n"
        "print('baseline' if Path('delete.txt').exists() and not Path('new.txt').exists() else 'candidate')\n"
    )
    repo, root, dataset, rnd, plan = project(tmp_path, script, rows, extra_files={"delete.txt": "old"})
    plan["allowed_paths"] = ["delete.txt", "new.txt"]
    freeze_round(repo, root, {"round_id": rnd["id"], "plan": plan})
    draft = prepare_candidate(
        repo, root, {"round_id": rnd["id"], "primary_issue_id": "issue", "paths": ["delete.txt", "new.txt"]}
    )
    (repo / "delete.txt").unlink()
    (repo / "new.txt").write_text("new")
    sealed = seal_candidate(repo, root, {"round_id": rnd["id"], "candidate_id": draft["id"]})
    before = git(repo, "rev-parse", "HEAD")
    request = {"dataset_id": dataset["id"], "case_ids": ["case"], "purpose": "evaluation", "round_id": rnd["id"]}
    for revision_id, expected in [(rnd["baseline_revision_id"], "baseline"), (sealed["revision_id"], "candidate")]:
        batch = run_evaluation(root, {**request, "revision_id": revision_id})
        record = next(iter(validate_evidence(root, batch["id"])[1].values()))
        assert record["output"].strip() == expected
        assert git(repo, "rev-parse", "HEAD") == before
        assert not (repo / "delete.txt").exists() and (repo / "new.txt").read_text() == "new"
    worker = (
        "import os\nfrom pathlib import Path\n"
        "from agent_tune_kit.checkpoints import temporary_revision\n"
        f"with temporary_revision(Path({str(repo)!r}), Path({str(root)!r}), {rnd['id']!r}, "
        f"{rnd['baseline_commit']!r}): os._exit(17)\n"
    )
    assert subprocess.run([sys.executable, "-c", worker], cwd=repo, check=False).returncode == 17
    operations = [
        read_json(path)
        for path in (root / "rounds" / rnd["id"] / "operations").glob("*.json")
        if read_json(path)["action"] == "temporary_replay" and read_json(path)["stage"] == "switched"
    ]
    assert len(operations) == 1
    assert (repo / "delete.txt").read_text() == "old" and not (repo / "new.txt").exists()
    assert (
        inspect_or_recover_operation(repo, root, {"round_id": rnd["id"], "operation_id": operations[0]["id"]})["stage"]
        == "aborted"
    )
    assert not (repo / "delete.txt").exists() and (repo / "new.txt").read_text() == "new"
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "diff", "--cached", "--name-only") == ""


def test_non_git_analysis_probe_uses_authorization_budget_and_component_identity(tmp_path: Path) -> None:
    repo = tmp_path / "no-git"
    repo.mkdir()
    script = repo / "tool.py"
    script.write_text("print('probe ok')\n")
    command = [sys.executable, "tool.py"]
    initialize_project(
        repo,
        {
            "analysis_only": True,
            "python": sys.executable,
            "command": command,
            "components": [{"component_id": "tool", "role": "tool", "change_role": "fixed", "source_path": "tool.py"}],
            "runtime_notes": "local probe",
            "external_effects": [],
        },
    )
    root = repo / ".atk"
    source = tmp_path / "cases.csv"
    source.write_text("id,input,usage,source_group_id\ncase,x,optimization,g\n")
    dataset = store_dataset(
        root,
        {"source": str(source), "mapping": {key: key for key in ("id", "input", "usage", "source_group_id")}},
    )
    plan = {
        "budget": {"probes": 1},
        "probe_permissions": [
            {
                "id": "local",
                "command_hash": digest(command),
                "runner_hash": digest(root / "adapters" / "runner.py"),
                "case_ids": ["case"],
                "isolation_ref": "local fixture",
                "max_calls": 1,
            }
        ],
    }
    rnd = create_round(repo, root, {"analysis_plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "diagnostic_probe",
        "round_id": rnd["id"],
        "revision_id": None,
    }
    with pytest.raises(ATKError, match="not authorized"):
        run_evaluation(root, request)
    batch = run_evaluation(root, {**request, "probe_authorization_id": "local"})
    assert batch["revision_id"] is None
    assert batch["actual_components"][0]["actual_sha256"] == digest(script)
    record = next(iter(validate_evidence(root, batch["id"])[1].values()))
    assert record["output"].strip() == "probe ok"
    with pytest.raises(ATKError, match="probes budget"):
        run_evaluation(root, {**request, "probe_authorization_id": "local"})
    with pytest.raises(ATKError, match="runtime setup"):
        run_evaluation(root, {**request, "purpose": "evaluation"})
    assert not (repo / ".git").exists()
