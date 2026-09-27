"""Run target Agents through real local import/link paths, then check ATK's revision gate."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_tune_kit.checkpoints import freeze_round, prepare_candidate, seal_candidate
from agent_tune_kit.core import digest, read_json, validate_evidence, write_json
from agent_tune_kit.execution import run_evaluation
from agent_tune_kit.governance import compare_and_gate
from tests.test_vnext_limits import assess, project


def run_revision_pair(
    repo: Path, root: Path, dataset: dict, round_data: dict, plan: dict, expected_result: str = "pass"
) -> tuple[dict, dict]:
    runtime = next(
        component for component in read_json(root / "project.json")["components"] if component["change_role"] == "fixed"
    )
    plan.update(
        {
            "allowed_paths": ["skills/reply/SKILL.md"],
            "protected_paths": ["agent.py", "agent.js", "fixture_agent/__init__.py"],
            "required_loaded_component_ids": ["business-skill", "agent-runtime"],
            "fixed_context_hash": digest(
                [
                    {
                        **runtime,
                        "actual_sha256": digest(repo / runtime["source_path"]),
                        "identity_status": "available",
                    }
                ]
            ),
        }
    )
    freeze_round(repo, root, {"round_id": round_data["id"], "plan": plan})
    request = {
        "dataset_id": dataset["id"],
        "case_ids": ["case"],
        "purpose": "evaluation",
        "round_id": round_data["id"],
        "revision_id": round_data["baseline_revision_id"],
    }
    baseline = run_evaluation(root, request)
    left = assess(root, baseline, {"case": "hello"})
    draft = prepare_candidate(
        repo, root, {"round_id": round_data["id"], "primary_issue_id": "issue", "paths": ["skills/reply/SKILL.md"]}
    )
    (repo / "skills/reply/SKILL.md").write_text("new")
    candidate = seal_candidate(repo, root, {"round_id": round_data["id"], "candidate_id": draft["id"]})
    request["revision_id"] = candidate["revision_id"]
    updated = run_evaluation(root, request)
    right = assess(root, updated, {"case": "hello"})
    validation = compare_and_gate(
        root,
        {
            "round_id": round_data["id"],
            "mode": "incremental",
            "issue_id": "issue",
            "candidate_id": draft["id"],
            "left_assessment_id": left,
            "right_assessment_id": right,
            "left_commit": round_data["baseline_commit"],
        },
    )
    assert validation["result"] == expected_result
    return baseline, updated


def configure_skill(root: Path, python: str, command: list[str], runtime_source: str) -> None:
    config = read_json(root / "project.json")
    config["python"] = python
    config["command"] = command
    config["components"] = [
        {
            "component_id": "business-skill",
            "role": "skill",
            "change_role": "variable",
            "source_path": "skills/reply/SKILL.md",
        },
        {"component_id": "agent-runtime", "role": "agent_code", "change_role": "fixed", "source_path": runtime_source},
    ]
    config["allowed_paths"] = ["skills/reply/SKILL.md"]
    config["protected_paths"] = ["agent.py", "agent.js", "fixture_agent/__init__.py"]
    write_json(root / "project.json", config)


def loaded_record(root: Path, batch: dict) -> dict:
    _, records, _ = validate_evidence(root, batch["id"])
    return next(iter(records.values()))


@pytest.mark.parametrize("wrong_copy", [False, True])
def test_node_local_link_reads_candidate_revision(tmp_path: Path, wrong_copy: bool) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    link = tmp_path / "linked-agent.cjs"
    agent = (
        "const fs = require('fs');\n"
        "const crypto = require('crypto');\n"
        "const path = require('path');\n"
        f"const linked = require({json.dumps(str(link))});\n"
        f"const entry = fs.realpathSync(require.resolve({json.dumps(str(link))}));\n"
        "const skill = path.resolve('skills/reply/SKILL.md');\n"
        "const data = linked.readSkill(skill);\n"
        "const event = {component_id: 'business-skill', state: 'loaded', "
        "fingerprint: crypto.createHash('sha256').update(data).digest('hex'), "
        "resolved_path: fs.realpathSync(skill), entry_source_path: entry};\n"
        "const runtime = {component_id: 'agent-runtime', state: 'loaded', "
        "fingerprint: crypto.createHash('sha256').update(fs.readFileSync(entry)).digest('hex'), "
        "resolved_path: entry};\n"
        "fs.writeFileSync(path.join(process.argv[3], 'loading.json'), JSON.stringify([event, runtime]));\n"
        "console.log(data.toString() === 'new' ? 'hello' : 'bad');\n"
    )
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, dataset, round_data, plan = project(
        tmp_path,
        "print('unused')\n",
        rows,
        extra_files={
            "agent.js": agent,
            "linked-agent.cjs": "const fs = require('fs'); exports.readSkill = path => fs.readFileSync(path);\n",
            "skills/reply/SKILL.md": "old",
        },
    )
    target = repo / "linked-agent.cjs"
    if wrong_copy:
        target = tmp_path / "wrong-agent.cjs"
        target.write_bytes((repo / "linked-agent.cjs").read_bytes())
    link.symlink_to(target)
    configure_skill(root, sys.executable, [node, "agent.js", "{input_file}", "{output_dir}"], "linked-agent.cjs")
    baseline, updated = run_revision_pair(
        repo, root, dataset, round_data, plan, "insufficient" if wrong_copy else "pass"
    )
    assert loaded_record(root, baseline)["output"].strip() == "bad"
    candidate_record = loaded_record(root, updated)
    assert candidate_record["output"].strip() == "hello"
    assert candidate_record["loading_evidence"][0]["entry_source_path"] == str(target)


def test_python_editable_install_reads_candidate_revision(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is not installed")
    agent = (
        "import fixture_agent, hashlib, json, sys\n"
        "from pathlib import Path\n"
        "source = Path(fixture_agent.__file__).resolve()\n"
        "skill = source.parent.parent / 'skills/reply/SKILL.md'\n"
        "data = skill.read_bytes()\n"
        "event = {'component_id': 'business-skill', 'state': 'loaded', "
        "'fingerprint': hashlib.sha256(data).hexdigest(), "
        "'resolved_path': str(skill.resolve()), 'entry_source_path': str(source)}\n"
        "runtime = {'component_id': 'agent-runtime', 'state': 'loaded', "
        "'fingerprint': hashlib.sha256(source.read_bytes()).hexdigest(), 'resolved_path': str(source)}\n"
        "(Path(sys.argv[2]) / 'loading.json').write_text(json.dumps([event, runtime]))\n"
        "print('hello' if data == b'new' else 'bad')\n"
    )
    rows = [{"id": "case", "input": "task", "usage": "optimization", "source_group_id": "group"}]
    repo, root, dataset, round_data, plan = project(
        tmp_path,
        agent,
        rows,
        extra_files={
            "pyproject.toml": '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
            '[project]\nname = "atk-target-fixture"\nversion = "0.0.1"\n'
            '[tool.hatch.build.targets.wheel]\npackages = ["fixture_agent"]\n',
            "fixture_agent/__init__.py": "VALUE = 1\n",
            "skills/reply/SKILL.md": "old",
        },
    )
    environment = tmp_path / "target-venv"
    subprocess.run([uv, "venv", "--python", sys.executable, str(environment)], check=True, capture_output=True)
    python = environment / "bin" / "python"
    subprocess.run(
        [uv, "pip", "install", "--offline", "--python", str(python), "-e", str(repo)],
        check=True,
        capture_output=True,
        env={**os.environ, "UV_OFFLINE": "1"},
    )
    configure_skill(
        root, str(python), [str(python), "-B", "agent.py", "{input_file}", "{output_dir}"], "fixture_agent/__init__.py"
    )
    baseline, updated = run_revision_pair(repo, root, dataset, round_data, plan)
    assert loaded_record(root, baseline)["output"].strip() == "bad"
    candidate_record = loaded_record(root, updated)
    assert candidate_record["output"].strip() == "hello"
    assert candidate_record["loading_evidence"][0]["entry_source_path"] == str(repo / "fixture_agent/__init__.py")
