#!/usr/bin/env python3
"""Static validation for the Agent tune kit Skill template pack.

This intentionally avoids third-party dependencies and checks only source/template
contracts. It does not run an end-to-end Agent tuning flow.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "skills/agent-tuning-generate-runner/SKILL.md",
    "skills/agent-tuning-filter-abnormal-rules/SKILL.md",
    "skills/agent-tuning-filter-abnormal-llm/SKILL.md",
    "skills/agent-tuning-report/SKILL.md",
    "skills/agent-tuning-apply-tuning/SKILL.md",
    "templates/agent-tuning/runner/test_runner.py.md",
    "templates/agent-tuning/runner/filter_abnormal.py.md",
    "docs/skill-template-pack-usage.md",
    "docs/shared-versioning-and-confirmation.md",
    "docs/codex_agent_tuning_prd.md",
    "README.md",
    "README.zh-CN.md",
]

SKILL_FILES = [path for path in REQUIRED_FILES if path.startswith("skills/")]

REQUIRED_SKILL_SECTIONS = [
    "## Purpose",
    "## Inputs",
    "## Outputs",
    "## Workflow",
    "## Shared version rules",
    "## Confirmation triggers",
    "## Failure behavior",
]

GLOBAL_PHRASES = [
    "RESULTS_DIR = Path(\"agent-tuning/results\")",
    "def list_version_dirs(results_dir=RESULTS_DIR)",
    "def resolve_current_version(results_dir=RESULTS_DIR)",
    "def resolve_previous_version(current_dir, results_dir=RESULTS_DIR)",
    "def require_current_file(current_dir, filename)",
    "def allocate_next_results_version(results_dir=RESULTS_DIR)",
    "No vN results directory exists; run test_runner.py first or confirm repair.",
    "Current version {current_dir.name} is missing {filename}; fix or rerun the prior step.",
    "agent-tuning/results/vN/results.csv",
    "agent-tuning/results/vN/abnormal_cases.csv",
    "agent-tuning/results/vN/report.md",
    "agent-tuning/results/vN/tuning_plan.md",
    "agent_output",
    "abnormal_cases.csv",
    "tuning_plan.md",
]

NON_GOALS = [
    "no plugin install UX",
    "no one-click orchestration",
    "no universal Schema",
    "no bundled example Agent/data fixtures",
    "no automatic rollback",
    "no full E2E test suite",
]

PRD_REFERENCES = [
    "docs/codex_agent_tuning_prd.md",
    "section 2.2",
    "section 2.4",
    "section 2.5",
    "section 2.6",
    "section 4",
    "section 7",
]

FORBIDDEN_MVP_PATH_PARTS = [
    ".codex-plugin",
    "marketplace",
]

FORBIDDEN_MVP_FILENAMES = [
    "plugin.json",
    "install.sh",
    "installer.sh",
]

PER_FILE_PHRASES = {
    "skills/agent-tuning-generate-runner/SKILL.md": [
        "agent-tuning/runner/test_runner.py",
        "Preserve all original dataset columns",
        "Append the fixed actual-output column `agent_output`",
        "no version directory is required",
        "ask the user to confirm before writing `test_runner.py`",
        "Agent invocation, dataset path/format, log source, or `agent_output` column conflict",
    ],
    "skills/agent-tuning-filter-abnormal-rules/SKILL.md": [
        "agent-tuning/runner/filter_abnormal.py",
        "require_current_file(current_dir, \"results.csv\")",
        "It does not run `filter_abnormal.py` itself",
        "ask whether to reuse or update rule logic",
        "manual execution",
        "overwrites the current version's existing file",
    ],
    "skills/agent-tuning-filter-abnormal-llm/SKILL.md": [
        "write `abnormal_cases.csv` directly",
        "expected-result columns or abnormal criteria are ambiguous",
        "Overwrites",
        "No `filter_abnormal.py` is required",
        "preserving all original `results.csv` columns",
    ],
    "skills/agent-tuning-report/SKILL.md": [
        "require_current_file(current_dir, \"results.csv\")",
        "require_current_file(current_dir, \"abnormal_cases.csv\")",
        "resolve_previous_version(current_dir)",
        "已解决",
        "部分解决",
        "未解决",
        "无法判断",
        "degrade to single-version or lower-confidence report with explicit explanation",
    ],
    "skills/agent-tuning-apply-tuning/SKILL.md": [
        "require_current_file(current_dir, \"report.md\")",
        "## 目标异常清单",
        "## 调优手段",
        "## 关联改动",
        "user-git-only guidance",
        "never perform automatic rollback",
    ],
    "templates/agent-tuning/runner/test_runner.py.md": [
        "DATASET_PATH = Path(\"TODO_AGENT_TUNING_DATASET_PATH\")",
        "def allocate_next_results_version(results_dir=RESULTS_DIR)",
        "class AgentExecutionError(RuntimeError)",
        "except UserActionRequired:",
        "except AgentExecutionError as exc:",
        "agent_output",
        "agent_output_status",
        "agent_output_error_type",
        "Preserves all original dataset columns",
        "Current max version",
    ],
    "templates/agent-tuning/runner/filter_abnormal.py.md": [
        "def resolve_current_version(results_dir=RESULTS_DIR)",
        "def require_current_file(current_dir, filename)",
        "ABNORMAL_FILENAME = \"abnormal_cases.csv\"",
        "Overwrote {abnormal_path}",
        "TODO_AGENT_TUNING",
        "raise UserActionRequired(\"TODO_AGENT_TUNING: implement confirmed abnormal rule before running.\")",
    ],
    "docs/shared-versioning-and-confirmation.md": [
        "Current version vs new version creation",
        "Only `test_runner.py` creates or reuses result versions",
        "Do not filter current-version selection by required files",
        "never fall back to an older version",
        "Per-Skill preconditions and failure behavior",
        "whole repository-native pack",
    ],
    "docs/skill-template-pack-usage.md": [
        "Copy/register boundary",
        "keep `skills/`, `templates/`, and `docs/` together",
        "Manual 2.2 → 2.6 loop",
        "v1 → v2",
        "python3 scripts/validate_skill_pack.py",
    ],
    "README.md": [
        "Codex Skill template pack",
        "keep `skills/`, `templates/`, and `docs/` together",
        "Quickstart",
        "Prerequisites",
        "agent-tuning-generate-runner",
        "agent-tuning-filter-abnormal-rules",
        "agent-tuning-filter-abnormal-llm",
        "agent-tuning-report",
        "agent-tuning-apply-tuning",
        "python3 scripts/validate_skill_pack.py",
    ],
    "README.zh-CN.md": [
        "Codex Skill 模板包",
        "保持 `skills/`、`templates/`、`docs/`",
        "快速开始",
        "使用前准备",
        "agent-tuning-generate-runner",
        "agent-tuning-filter-abnormal-rules",
        "agent-tuning-filter-abnormal-llm",
        "agent-tuning-report",
        "agent-tuning-apply-tuning",
        "python3 scripts/validate_skill_pack.py",
    ],
}

VERSION_HELPER_SNIPPETS = {
    "templates/agent-tuning/runner/test_runner.py.md": [
        "RESULTS_DIR = Path(\"agent-tuning/results\")",
        "def list_version_dirs(results_dir=RESULTS_DIR):",
        "if not results_dir.exists():\n        return []",
        "def allocate_next_results_version(results_dir=RESULTS_DIR):",
        "target = results_dir / \"v1\"",
        "target = results_dir / f\"v{max_n + 1}\" if (current / \"results.csv\").exists() else current",
    ],
    "templates/agent-tuning/runner/filter_abnormal.py.md": [
        "RESULTS_DIR = Path(\"agent-tuning/results\")",
        "def list_version_dirs(results_dir=RESULTS_DIR):",
        "if not results_dir.exists():\n        return []",
        "def resolve_current_version(results_dir=RESULTS_DIR):",
        "def require_current_file(current_dir, filename):",
        "raise UserActionRequired(f\"Current version {current_dir.name} is missing {filename}; fix or rerun the prior step.\")",
    ],
    "docs/shared-versioning-and-confirmation.md": [
        "RESULTS_DIR = Path(\"agent-tuning/results\")",
        "def list_version_dirs(results_dir=RESULTS_DIR):",
        "if not results_dir.exists():\n        return []",
        "def resolve_current_version(results_dir=RESULTS_DIR):",
        "def resolve_previous_version(current_dir, results_dir=RESULTS_DIR):",
        "def require_current_file(current_dir, filename):",
        "def allocate_next_results_version(results_dir=RESULTS_DIR):",
    ],
}


def read_rel(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def contains_any(text: str, options: list[str]) -> bool:
    lower = text.lower()
    return any(option.lower() in lower for option in options)


def main() -> int:
    errors: list[str] = []

    for rel in REQUIRED_FILES:
        path = ROOT / rel
        require(path.exists(), f"missing required file: {rel}", errors)

    existing_texts = {rel: read_rel(rel) for rel in REQUIRED_FILES if (ROOT / rel).exists()}
    all_text = "\n".join(existing_texts.values())

    for path in ROOT.rglob("*"):
        if ".git" in path.parts or ".omx" in path.parts:
            continue
        rel = path.relative_to(ROOT)
        lowered_parts = {part.lower() for part in rel.parts}
        lowered_name = path.name.lower()
        for forbidden in FORBIDDEN_MVP_PATH_PARTS:
            require(forbidden not in lowered_parts, f"forbidden MVP packaging path present: {rel}", errors)
        require(lowered_name not in FORBIDDEN_MVP_FILENAMES, f"forbidden MVP packaging/installer file present: {rel}", errors)

    for rel in SKILL_FILES:
        text = existing_texts.get(rel, "")
        require(text.startswith("---\n"), f"{rel} missing YAML front matter", errors)
        for section in REQUIRED_SKILL_SECTIONS:
            require(section in text, f"{rel} missing section {section}", errors)
        require("docs/shared-versioning-and-confirmation.md" in text, f"{rel} missing shared version doc reference", errors)
        require("RESULTS_DIR = Path(\"agent-tuning/results\")" in text, f"{rel} missing canonical RESULTS_DIR", errors)
        require("Failure behavior" in text and "Confirmation triggers" in text, f"{rel} missing precondition/failure behavior", errors)

    for phrase in GLOBAL_PHRASES:
        require(phrase in all_text, f"missing global phrase/snippet: {phrase}", errors)

    for rel, phrases in PER_FILE_PHRASES.items():
        text = existing_texts.get(rel, "")
        for phrase in phrases:
            require(phrase in text, f"{rel} missing phrase: {phrase}", errors)

    for rel, snippets in VERSION_HELPER_SNIPPETS.items():
        text = existing_texts.get(rel, "")
        for snippet in snippets:
            require(snippet in text, f"{rel} missing canonical version helper snippet: {snippet}", errors)

    docs_and_readme = "\n".join(
        existing_texts.get(rel, "")
        for rel in ["README.md", "README.zh-CN.md", "docs/shared-versioning-and-confirmation.md", "docs/skill-template-pack-usage.md", "docs/codex_agent_tuning_prd.md"]
    )
    for phrase in NON_GOALS:
        require(phrase.lower() in docs_and_readme.lower(), f"missing MVP non-goal documentation: {phrase}", errors)

    for phrase in PRD_REFERENCES:
        require(contains_any(all_text, [phrase]), f"missing PRD traceability phrase: {phrase}", errors)

    tuning_text = existing_texts.get("skills/agent-tuning-apply-tuning/SKILL.md", "")
    for heading in ["## 目标异常清单", "## 调优手段", "## 关联改动"]:
        require(heading in tuning_text, f"tuning Skill missing exact heading {heading}", errors)

    report_text = existing_texts.get("skills/agent-tuning-report/SKILL.md", "")
    for status in ["已解决", "部分解决", "未解决", "无法判断"]:
        require(status in report_text, f"report Skill missing cross-version status {status}", errors)

    runner_template = existing_texts.get("templates/agent-tuning/runner/test_runner.py.md", "")
    require("list(source_fieldnames) + [\"agent_output\"] + auxiliary_fields" in runner_template, "runner template must append agent_output and auxiliary agent_output_* columns after original columns", errors)
    require("if \"agent_output\" in fieldnames" in runner_template, "runner template must guard source agent_output conflict", errors)
    require("except Exception as exc" not in runner_template, "runner template must not catch broad Exception and mask configuration failures", errors)
    require("except UserActionRequired:\n            # Configuration/TODO/confirmation failures must stop the run" in runner_template, "runner template must propagate UserActionRequired before row-error handling", errors)

    rules_skill = existing_texts.get("skills/agent-tuning-filter-abnormal-rules/SKILL.md", "")
    llm_skill = existing_texts.get("skills/agent-tuning-filter-abnormal-llm/SKILL.md", "")
    require("agent-tuning-filter-abnormal-rules" in rules_skill, "rules abnormal Skill identity missing", errors)
    require("agent-tuning-filter-abnormal-llm" in llm_skill, "LLM abnormal Skill identity missing", errors)
    require("abnormal_cases.csv" in rules_skill and "abnormal_cases.csv" in llm_skill, "both abnormal Skills must write abnormal_cases.csv", errors)

    filter_template = existing_texts.get("templates/agent-tuning/runner/filter_abnormal.py.md", "")
    require("Conservative placeholder" not in filter_template, "filter template must not ship a runnable placeholder heuristic", errors)

    if errors:
        print("Skill pack validation FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Skill pack validation passed")
    print(f"Checked {len(REQUIRED_FILES)} files and {len(SKILL_FILES)} Skill templates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
