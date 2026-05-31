from __future__ import annotations

import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_rel(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_atk_new_agent_skill_is_pre_init_scaffold() -> None:
    text = read_rel("skills/atk-new-agent/SKILL.md")
    for phrase in [
        "name: atk-new-agent",
        "pre-init scaffold",
        ".atk/specs/agent_spec.md",
        "Never write `.atk/datasets/original.csv`",
        "run_agent(input_data: dict[str, str]) -> str",
        "$atk-init Agent 入口是 agent.py 的 run_agent",
    ]:
        assert phrase in text
    assert 'RESULTS_DIR = Path(".atk/results")' not in text
    assert "docs/shared-versioning-and-confirmation.md" not in text


def test_generated_agent_templates_expose_runtime_contract() -> None:
    agent_template = read_rel("templates/agent/agent.py.md")
    run_template = read_rel("templates/agent/run_agent.py.md")
    pyproject_template = read_rel("templates/agent/pyproject.toml.md")
    env_template = read_rel("templates/agent/env.example.md")

    for phrase in [
        "def run_agent(input_data: dict[str, str]) -> str",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "AgentConfigurationError",
    ]:
        assert phrase in agent_template
    assert "from agent import AgentConfigurationError, run_agent" in run_template
    assert "openai>=1.0.0" in pyproject_template
    assert "python-dotenv>=1.0.0" in pyproject_template
    assert "OPENAI_BASE_URL=https://api.openai.com/v1" in env_template


def test_generated_agent_smoke_fails_cleanly_without_credentials(tmp_path: Path) -> None:
    shutil.copyfile(ROOT / "templates/agent/agent.py.md", tmp_path / "agent.py")
    shutil.copyfile(ROOT / "templates/agent/run_agent.py.md", tmp_path / "run_agent.py")
    (tmp_path / ".atk/specs").mkdir(parents=True)
    (tmp_path / ".atk/specs/agent_spec.md").write_text("# Agent Spec\n\n## User Intent\nSmoke test\n", encoding="utf-8")

    py_compile.compile(str(tmp_path / "agent.py"), doraise=True)
    py_compile.compile(str(tmp_path / "run_agent.py"), doraise=True)

    result = subprocess.run(
        [sys.executable, "run_agent.py", "--input", "hello"],
        cwd=tmp_path,
        env={},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 2
    assert "Configuration error:" in result.stderr
    assert "OPENAI_API_KEY" in result.stderr


def test_generated_agent_fails_cleanly_when_spec_is_missing(tmp_path: Path) -> None:
    shutil.copyfile(ROOT / "templates/agent/agent.py.md", tmp_path / "agent.py")
    shutil.copyfile(ROOT / "templates/agent/run_agent.py.md", tmp_path / "run_agent.py")

    result = subprocess.run(
        [sys.executable, "run_agent.py", "--input", "hello"],
        cwd=tmp_path,
        env={
            "OPENAI_API_KEY": "test-key",
            "OPENAI_MODEL": "test-model",
            "OPENAI_BASE_URL": "https://example.invalid/v1",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 2
    assert "Configuration error:" in result.stderr
    assert ".atk/specs/agent_spec.md" in result.stderr
