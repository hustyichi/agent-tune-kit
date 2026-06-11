from __future__ import annotations

import py_compile
import shutil
import subprocess
import sys
import types
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
        "Never write `.atk/datasets/dataset.csv`",
        "Risk-triggered confirmation policy",
        "No risk-triggered confirmation needed",
        "do not ask the user to confirm obvious, low-risk, or inspectable facts",
        "do not present default assumptions as user intent",
        "run `uv sync` from the target repository root",
        "`uv sync` status",
        "run_agent(input_data: dict[str, str]) -> str",
        "$atk-init Agent 入口是 agent.py 的 run_agent",
    ]:
        assert phrase in text
    assert 'RESULTS_DIR = Path(".atk/results")' not in text
    assert "docs/shared-versioning-and-confirmation.md" not in text


def test_atk_build_dataset_skill_is_pre_init_dataset_builder() -> None:
    text = read_rel("skills/atk-build-dataset/SKILL.md")
    text_lower = text.lower()
    for phrase in [
        "name: atk-build-dataset",
        "pre-init",
        ".atk/datasets/dataset.csv",
        "atk_id",
        "unique positive integers",
        "at least one clear input column",
        "expected output or acceptance standard",
        "dynamic business columns",
        "1-3 targeted questions",
        "input fields are unclear",
        "expected-output semantics are unclear",
        "multiple incompatible Agent tasks",
        "domain facts not provided by the user",
        "main successful flow",
        "boundary input",
        "missing or ambiguous information",
        "refusal, uncertainty, or unsupported request",
        "output format constraint",
        "business risk",
        "ask before overwriting",
        "large-scale default synthetic expansion",
        "if an Agent exists",
        "initialize batch evaluation",
        "if no Agent exists",
        "$atk-new-agent",
        "if Agent existence is unclear",
        "$atk-init",
    ]:
        assert phrase in text
    for phrase in [
        "do not automatically merge or append",
        "do not create `candidate_dataset.csv`",
    ]:
        assert phrase in text_lower
    assert "production-log parsing is not supported in the first version" in text
    for section in [
        "## Purpose",
        "## Inputs",
        "## Outputs",
        "## Workflow",
        "## Confirmation triggers",
        "## Failure behavior",
        "## Handoff message",
    ]:
        assert section in text
    assert 'RESULTS_DIR = Path(".atk/results")' not in text
    assert "docs/shared-versioning-and-confirmation.md" not in text
    assert ".atk/specs/agent_spec.md" not in text
    assert "Never write `.atk/datasets/dataset.csv`" not in text


def test_atk_build_ground_truth_skill_is_dataset_enricher() -> None:
    text = read_rel("skills/atk-build-ground-truth/SKILL.md")
    for phrase in [
        "name: atk-build-ground-truth",
        "existing `.atk/datasets/dataset.csv`",
        "dataset-only / pre-results",
        "ground_truth",
        "valid `atk_id`",
        "unique positive integers",
        "exact answer",
        "natural-language acceptance criteria",
        "global ground-truth style",
        "dataset-wide",
        "candidate modification summary",
        "affected row counts and representative examples",
        "overwrite existing `ground_truth`",
        "semantically replace expected-like fields",
        "multiple incompatible Agent tasks",
        "required domain facts are absent",
        "Do not create `.atk/results/vN`",
        "Do not run the Agent",
        "Do not run `$atk-run`",
        "Do not write `failure_cases.csv`",
        "Do not change `atk-find-failures` behavior in v1",
        "eval_results.csv` predates dataset enrichment",
        ".atk/context.md",
        "Ground Truth Standard",
        "Do not use `.atk/context.md` for dataset metadata",
    ]:
        assert phrase in text
    for section in [
        "## Purpose",
        "## Inputs",
        "## Outputs",
        "## Workflow",
        "## Confirmation triggers",
        "## Failure behavior",
        "## Handoff message",
    ]:
        assert section in text
    assert 'RESULTS_DIR = Path(".atk/results")' not in text
    assert "docs/shared-versioning-and-confirmation.md" not in text
    assert ".atk/specs/agent_spec.md" not in text
    assert "Never write `.atk/datasets/dataset.csv`" not in text


def test_local_context_contract_is_private_and_standard_focused() -> None:
    text = read_rel("docs/local-context.md")
    shared_text = read_rel("docs/shared-versioning-and-confirmation.md")
    read_only_skill = read_rel("skills/atk-find-failures/SKILL.md")
    tune_skill = read_rel("skills/atk-tune/SKILL.md")
    report_skill = read_rel("skills/atk-report/SKILL.md")
    tune_gt_skill = read_rel("skills/atk-tune-ground-truth/SKILL.md")

    for phrase in [
        "`./.atk/context.md` is a local, private tuning-consensus document",
        "It is not a dataset metadata registry, run log, or team collaboration file.",
        "## Tuning Objective",
        "## Agent Behavior Standard",
        "## Ground Truth Standard",
        "## User Feedback",
        "## Tuning Decisions",
        "dataset path, row count, headers, field types",
        "result version, latest run path, metrics snapshots",
        "Missing `.atk/context.md` must never block a Skill.",
    ]:
        assert phrase in text

    assert "Local private tuning consensus: `.atk/context.md`" in shared_text
    assert "must not become a registry for dataset headers, field" in shared_text
    assert "It should not write `.atk/context.md`." in shared_text

    assert "Do not write `.atk/context.md`" in read_only_skill
    assert "Do not modify `Ground Truth Standard`" in tune_skill
    assert "Append to `.atk/context.md` only when" in report_skill
    assert "changes the confirmed `Ground Truth Standard`" in tune_gt_skill
    assert "Do not use `.atk/context.md` for dataset metadata" in tune_gt_skill


def test_generated_agent_templates_expose_runtime_contract() -> None:
    agent_template = read_rel("templates/agent/agent.py")
    run_template = read_rel("templates/agent/run_agent.py")
    pyproject_template = read_rel("templates/agent/pyproject.toml")
    env_template = read_rel("templates/agent/.env.example")

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
    assert "ATK new Agent normally runs `uv sync`" in read_rel("templates/agent/README.md")


def test_generated_agent_emits_observable_logs_for_each_input_item(tmp_path: Path, monkeypatch, caplog) -> None:
    shutil.copyfile(ROOT / "templates/agent/agent.py", tmp_path / "agent.py")
    (tmp_path / ".atk/specs").mkdir(parents=True)
    (tmp_path / ".atk/specs/agent_spec.md").write_text("# Agent Spec\n\n## User Intent\nSmoke test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")

    class FakeCompletions:
        def create(self, **_kwargs):
            message = types.SimpleNamespace(content="fake output")
            choice = types.SimpleNamespace(message=message)
            return types.SimpleNamespace(choices=[choice])

    class FakeOpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    caplog.set_level("INFO", logger="atk.generated_agent")

    import agent

    assert agent.run_agent({"atk_id": "42", "question": "hello"}) == "fake output"

    messages = "\n".join(record.getMessage() for record in caplog.records if record.name == "atk.generated_agent")
    assert "event=agent_run_start" in messages
    assert "event=agent_run_complete" in messages
    assert "atk_id=42" in messages
    assert "input_fields=atk_id,question" in messages


def test_generated_agent_smoke_fails_cleanly_without_credentials(tmp_path: Path) -> None:
    shutil.copyfile(ROOT / "templates/agent/agent.py", tmp_path / "agent.py")
    shutil.copyfile(ROOT / "templates/agent/run_agent.py", tmp_path / "run_agent.py")
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
    shutil.copyfile(ROOT / "templates/agent/agent.py", tmp_path / "agent.py")
    shutil.copyfile(ROOT / "templates/agent/run_agent.py", tmp_path / "run_agent.py")

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
