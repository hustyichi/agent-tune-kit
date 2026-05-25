#!/usr/bin/env python3
"""Static validation for the Agent Tune Kit local Codex plugin.

This intentionally avoids third-party dependencies and checks source/template/plugin
contracts. It does not run an end-to-end Agent tuning flow.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    ".codex-plugin/plugin.json",
    "skills/atk-status/SKILL.md",
    "skills/atk-init/SKILL.md",
    "skills/atk-run/SKILL.md",
    "skills/atk-init-failure-rule/SKILL.md",
    "skills/atk-find-failures-by-rule/SKILL.md",
    "skills/atk-find-failures/SKILL.md",
    "skills/atk-report/SKILL.md",
    "skills/atk-visualize-failures/SKILL.md",
    "skills/atk-tune/SKILL.md",
    "templates/.atk/runner/eval_runner.py.md",
    "templates/.atk/runner/failure_rule.py.md",
    "docs/skill-template-pack-usage.md",
    "docs/shared-versioning-and-confirmation.md",
    "docs/codex_agent_tuning_prd.md",
    "scripts/install_plugin.py",
    "README.md",
    "README.en.md",
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
    "RESULTS_DIR = Path(\".atk/results\")",
    "def list_version_dirs(results_dir=RESULTS_DIR)",
    "def resolve_current_version(results_dir=RESULTS_DIR)",
    "def resolve_previous_version(current_dir, results_dir=RESULTS_DIR)",
    "def require_current_file(current_dir, filename)",
    "def allocate_next_results_version(results_dir=RESULTS_DIR)",
    "No vN results directory exists; run eval_runner.py first or confirm repair.",
    "Current version {current_dir.name} is missing {filename}; fix or rerun the prior step.",
    ".atk/results/vN/eval_results.csv",
    ".atk/datasets/",
    ".atk/results/vN/failure_cases.csv",
    ".atk/results/vN/failure_cases.html",
    ".atk/results/vN/report.md",
    ".atk/results/vN/tuning_plan.md",
    "agent_output",
    "agent_output_log_path",
    "logs/row_",
    "failure_cases.csv",
    "tuning_plan.md",
]

NON_GOALS = [
    "no public marketplace",
    "no brand assets",
    "no hidden one-click orchestration",
    "no universal Schema",
    "no bundled example Agent/data fixtures",
    "no automatic Agent tuning workflow rollback",
    "no old installer command compatibility",
    "no full E2E test suite",
]

PLUGIN_DOC_PHRASES = [
    "local Codex plugin",
    ".codex-plugin/plugin.json",
    "scripts/install_plugin.py install",
    "scripts/install_plugin.py preview --smoke",
    "scripts/install_plugin.py status",
    "scripts/install_plugin.py rollback --backup",
    "explicit subcommands only",
    "source.path",
    "./plugins/agent-tune-kit",
    "--yes --force",
    "Codex UI enablement state",
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

PER_FILE_PHRASES = {
    ".codex-plugin/plugin.json": [
        '"name": "agent-tune-kit"',
        '"version": "0.3.5"',
        '"skills": "./skills/"',
        '"displayName": "Agent Tune Kit"',
        '"defaultPrompt"',
    ],
    "scripts/install_plugin.py": [
        "argparse",
        "install",
        "preview",
        "status",
        "rollback",
        "--backup",
        "--backup-root",
        "--yes",
        "--no-input",
        "--force",
        "--marketplace-path",
        "--plugin-store",
        "--copy",
        "--smoke",
        "SOURCE_PATH = f\"./plugins/{PLUGIN_NAME}\"",
        "DEFAULT_BACKUP_ROOT",
        "write_json_atomic",
        "refusing destructive replacement",
        "newer unrelated state",
        "symlink",
        "copy fallback",
    ],
    "skills/atk-status/SKILL.md": [
        "atk-status",
        "router/status guide",
        "does not bypass existing confirmation triggers",
        "does not perform full automatic tuning",
        "atk-init",
        "atk-run",
        "atk-init-failure-rule",
        "atk-find-failures-by-rule",
        "atk-find-failures",
        "atk-report",
        "atk-visualize-failures",
        "atk-tune",
        "RESULTS_DIR = Path(\".atk/results\")",
        "failure_cases.html",
        "non-blocking review step",
    ],
    "skills/atk-init/SKILL.md": [
        ".atk/runner/eval_runner.py",
        "Preserve all original dataset columns",
        "Append the fixed actual-output column `agent_output`",
        "agent_output_log_path",
        "CONCURRENT_ROW_LOGGING_ENABLED",
        "no version directory is required",
        "ask the user to confirm before writing `eval_runner.py`",
        "Agent invocation, dataset path/format, log source, Python logger names, or `agent_output` / `agent_output_log_path` column conflict",
        "Snapshot the input dataset into `.atk/datasets/`",
    ],
    "skills/atk-run/SKILL.md": [
        "atk-run",
        ".atk/runner/eval_runner.py",
        "<python-runtime> .atk/runner/eval_runner.py",
        "uv run python",
        "--limit",
        "--concurrency",
        "agent_output_log_path",
        "row-log status: active, downgraded, or unavailable",
        "RESULTS_DIR = Path(\".atk/results\")",
        "If the runner is missing",
        "next recommended Skill: `atk-find-failures`",
    ],
    "skills/atk-init-failure-rule/SKILL.md": [
        "atk-init-failure-rule",
        ".atk/runner/failure_rule.py",
        "templates/.atk/runner/failure_rule.py.md",
        "This Skill only prepares the rule script",
        "does not write `.atk/results/vN/failure_cases.csv`",
        "Tell the user to run `atk-find-failures-by-rule`",
        "require_current_file(current_dir, \"eval_results.csv\")",
    ],
    "skills/atk-find-failures-by-rule/SKILL.md": [
        ".atk/runner/failure_rule.py",
        "require_current_file(current_dir, \"eval_results.csv\")",
        "This Skill only runs an existing rule script",
        "does not create or update `.atk/runner/failure_rule.py`",
        "run `atk-init-failure-rule` first",
        "<python-runtime> .atk/runner/failure_rule.py",
        "If `.atk/runner/failure_rule.py` contains `TODO_AGENT_TUNING`",
        "current `failure_cases.csv` already exists",
    ],
    "skills/atk-find-failures/SKILL.md": [
        "write failing rows to `failure_cases.csv` directly",
        "expected-result columns or failure criteria are ambiguous",
        "Overwrites",
        "No `failure_rule.py` is required",
        "preserving all original `eval_results.csv` columns",
    ],
    "skills/atk-report/SKILL.md": [
        "require_current_file(current_dir, \"eval_results.csv\")",
        "require_current_file(current_dir, \"failure_cases.csv\")",
        "resolve_previous_version(current_dir)",
        "已解决",
        "部分解决",
        "未解决",
        "无法判断",
        "agent_output_log_path",
        "fall back to `app.log`",
        "degrade to single-version or lower-confidence report with explicit explanation",
    ],
    "skills/atk-visualize-failures/SKILL.md": [
        "atk-visualize-failures",
        "require_current_file(current_dir, \"failure_cases.csv\")",
        "failure_cases.html",
        "report.md",
        "best-effort",
        "non-blocking",
        "csv.DictReader",
        "html.escape",
        "summary counts",
        "search/filter",
        "expandable/detail rows",
        "expected_output",
        "agent_output",
        "agent_output_log_path",
        "Do not create `report_summary.json`",
        "metadata JSON",
        "Do not write outside the resolved current version directory",
        "same-version",
        "ask before overwriting that HTML artifact only",
        "never change `atk-report` behavior",
    ],
    "skills/atk-tune/SKILL.md": [
        "require_current_file(current_dir, \"report.md\")",
        "## 目标异常清单",
        "## 调优手段",
        "## 关联改动",
        "user-git-only guidance",
        "never perform automatic rollback",
    ],
    "templates/.atk/runner/eval_runner.py.md": [
        "DATASETS_DIR = Path(\".atk/datasets\")",
        "DATASET_PATH = DATASETS_DIR / \"TODO_AGENT_TUNING_DATASET_SNAPSHOT\"",
        "def allocate_next_results_version(results_dir: Path = RESULTS_DIR) -> Path",
        "class AgentExecutionError(RuntimeError)",
        "parser.add_argument(",
        "--limit",
        "--concurrency",
        "ThreadPoolExecutor",
        "os.fsync(handle.fileno())",
        "except UserActionRequired:",
        "except AgentExecutionError as exc:",
        "agent_output",
        "agent_output_log_path",
        "PYTHON_LOGGING_CAPTURE_ENABLED",
        "ROW_LOGGER_NAMES",
        "ROW_LOG_FORMAT",
        "ROW_LOG_LEVEL",
        "ROW_LOGS_DIRNAME",
        "ATKRowLogHandler",
        "contextvars",
        "CONCURRENT_ROW_LOGGING_ENABLED",
        "row_{source_index:06d}.log",
        "relative_path.as_posix()",
        "ROW_LOGGING_CONCURRENCY_DOWNGRADE_MESSAGE",
        "agent_output_status",
        "agent_output_error_type",
        "Preserves all original dataset columns",
        "Current max version",
    ],
    "templates/.atk/runner/failure_rule.py.md": [
        "def resolve_current_version(results_dir=RESULTS_DIR)",
        "def require_current_file(current_dir, filename)",
        "FAILURE_FILENAME = \"failure_cases.csv\"",
        "Overwrote {failure_path}",
        "TODO_AGENT_TUNING",
        "raise UserActionRequired(\"TODO_AGENT_TUNING: implement confirmed failure rule before running.\")",
    ],
    "docs/shared-versioning-and-confirmation.md": [
        "Current version vs new version creation",
        "Only `eval_runner.py` creates or reuses result versions",
        "Do not filter current-version selection by required files",
        "never fall back to an older version",
        "agent_output_log_path",
        "logs/row_{source_index:06d}.log",
        "Per-Skill preconditions and failure behavior",
        ".atk/results/vN/failure_cases.html",
        "atk-visualize-failures",
        "best-effort and non-blocking",
        "local Codex plugin",
        "python3 scripts/install_plugin.py install",
        "python3 scripts/install_plugin.py status",
        "python3 scripts/install_plugin.py rollback --backup <backup-id>",
    ],
    "docs/skill-template-pack-usage.md": [
        "Local plugin install and smoke",
        "Repository layout boundary",
        "keep `skills/`, `templates/`, and `docs/` together",
        "Manual 2.2 → 2.6 loop",
        "v1 → v2",
        "agent_output_log_path",
        "logs/row_{source_index:06d}.log",
        "python3 scripts/validate_skill_pack.py",
        "skills/atk-visualize-failures/SKILL.md",
        "failure_cases.html",
        "same-version `report.md` is optional best-effort context",
        "python3 scripts/install_plugin.py install",
        "python3 scripts/install_plugin.py status",
        "python3 scripts/install_plugin.py rollback --backup <backup-id>",
    ],
    "README.md": [
        "本地 Codex 插件",
        "python3 scripts/install_plugin.py install",
        "python3 scripts/install_plugin.py status",
        "python3 scripts/install_plugin.py rollback --backup <backup-id>",
        "快速开始",
        "使用前准备",
        "atk-status",
        "atk-init",
        "atk-run",
        "atk-init-failure-rule",
        "atk-find-failures-by-rule",
        "atk-find-failures",
        "atk-report",
        "atk-visualize-failures",
        "failure_cases.html",
        "atk-tune",
    ],
    "README.en.md": [
        "local Codex plugin",
        "python3 scripts/install_plugin.py install",
        "python3 scripts/install_plugin.py status",
        "python3 scripts/install_plugin.py rollback --backup <backup-id>",
        "Quickstart",
        "Prerequisites",
        "atk-status",
        "atk-init",
        "atk-run",
        "atk-init-failure-rule",
        "atk-find-failures-by-rule",
        "atk-find-failures",
        "atk-report",
        "atk-visualize-failures",
        "failure_cases.html",
        "atk-tune",
    ],
    "README.zh-CN.md": [
        "本地 Codex 插件",
        "python3 scripts/install_plugin.py install",
        "python3 scripts/install_plugin.py status",
        "python3 scripts/install_plugin.py rollback --backup <backup-id>",
        "快速开始",
        "使用前准备",
        "atk-status",
        "atk-init",
        "atk-run",
        "atk-init-failure-rule",
        "atk-find-failures-by-rule",
        "atk-find-failures",
        "atk-report",
        "atk-visualize-failures",
        "failure_cases.html",
        "atk-tune",
    ],
}

VERSION_HELPER_SNIPPETS = {
    "templates/.atk/runner/eval_runner.py.md": [
        "RESULTS_DIR = Path(\".atk/results\")",
        "def list_version_dirs(results_dir: Path = RESULTS_DIR) -> list[tuple[int, Path]]:",
        "if not results_dir.exists():\n        return []",
        "def allocate_next_results_version(results_dir: Path = RESULTS_DIR) -> Path:",
        "target = results_dir / \"v1\"",
        "target = results_dir / f\"v{max_n + 1}\" if (current / \"eval_results.csv\").exists() else current",
    ],
    "templates/.atk/runner/failure_rule.py.md": [
        "RESULTS_DIR = Path(\".atk/results\")",
        "def list_version_dirs(results_dir=RESULTS_DIR):",
        "if not results_dir.exists():\n        return []",
        "def resolve_current_version(results_dir=RESULTS_DIR):",
        "def require_current_file(current_dir, filename):",
        "raise UserActionRequired(f\"Current version {current_dir.name} is missing {filename}; fix or rerun the prior step.\")",
    ],
    "docs/shared-versioning-and-confirmation.md": [
        "RESULTS_DIR = Path(\".atk/results\")",
        "DATASETS_DIR = Path(\".atk/datasets\")",
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


def validate_manifest(errors: list[str]) -> None:
    path = ROOT / ".codex-plugin/plugin.json"
    try:
        manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"manifest JSON invalid or unreadable: {exc}")
        return

    require(manifest.get("name") == "agent-tune-kit", "manifest name must be agent-tune-kit", errors)
    require(bool(re.fullmatch(r"\d+\.\d+\.\d+", str(manifest.get("version", "")))), "manifest version must be strict semver", errors)
    require(manifest.get("skills") == "./skills/", "manifest skills must be ./skills/", errors)
    require("hooks" not in manifest, "manifest must not include unsupported hooks field", errors)
    for optional_path_field in ["apps", "mcpServers"]:
        value = manifest.get(optional_path_field)
        if value:
            require((ROOT / value).exists(), f"manifest {optional_path_field} points to missing file: {value}", errors)

    author = manifest.get("author")
    require(isinstance(author, dict) and bool(author.get("name")), "manifest author.name is required", errors)
    require(bool(manifest.get("description")), "manifest description is required", errors)
    require("TODO" not in json.dumps(manifest), "manifest must not contain TODO placeholders", errors)

    interface = manifest.get("interface")
    require(isinstance(interface, dict), "manifest interface must be an object", errors)
    if not isinstance(interface, dict):
        return
    for key in ["displayName", "shortDescription", "longDescription", "developerName", "category", "capabilities", "defaultPrompt"]:
        require(key in interface, f"manifest interface missing {key}", errors)
    prompts = interface.get("defaultPrompt")
    require(isinstance(prompts, list) and 1 <= len(prompts) <= 3, "manifest defaultPrompt must contain 1-3 prompts", errors)
    if isinstance(prompts, list):
        for prompt in prompts:
            require(isinstance(prompt, str) and len(prompt) <= 128, f"manifest defaultPrompt too long or non-string: {prompt!r}", errors)
    for asset_field in ["composerIcon", "logo"]:
        asset = interface.get(asset_field)
        if asset:
            require((ROOT / asset).exists(), f"manifest {asset_field} points to missing asset: {asset}", errors)
    screenshots = interface.get("screenshots", [])
    require(isinstance(screenshots, list), "manifest screenshots must be an array", errors)
    if isinstance(screenshots, list):
        for screenshot in screenshots:
            require(str(screenshot).startswith("./assets/") and str(screenshot).endswith(".png"), f"manifest screenshot must be ./assets/*.png: {screenshot}", errors)
            require((ROOT / str(screenshot)).exists(), f"manifest screenshot file missing: {screenshot}", errors)


def validate_installer(errors: list[str]) -> None:
    text = read_rel("scripts/install_plugin.py") if (ROOT / "scripts/install_plugin.py").exists() else ""
    require("DEFAULT_MARKETPLACE = Path(\"~/.agents/plugins/marketplace.json\")" in text, "installer must default to personal marketplace", errors)
    require("DEFAULT_PLUGIN_STORE = Path(\"~/plugins\")" in text, "installer must default to ~/plugins", errors)
    for phrase in ["AVAILABLE", "ON_INSTALL", "category", "Coding", "atomic", "os.replace", "smoke-resolved plugin path", "Codex UI boundary", "--yes --force", "rollback complete"]:
        require(phrase in text, f"installer missing behavior phrase: {phrase}", errors)


def main() -> int:
    errors: list[str] = []

    for rel in REQUIRED_FILES:
        path = ROOT / rel
        require(path.exists(), f"missing required file: {rel}", errors)

    existing_texts = {rel: read_rel(rel) for rel in REQUIRED_FILES if (ROOT / rel).exists()}
    all_text = "\n".join(existing_texts.values())

    for rel in SKILL_FILES:
        text = existing_texts.get(rel, "")
        require(text.startswith("---\n"), f"{rel} missing YAML front matter", errors)
        for section in REQUIRED_SKILL_SECTIONS:
            require(section in text, f"{rel} missing section {section}", errors)
        require("docs/shared-versioning-and-confirmation.md" in text, f"{rel} missing shared version doc reference", errors)
        require("RESULTS_DIR = Path(\".atk/results\")" in text, f"{rel} missing canonical RESULTS_DIR", errors)
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
        for rel in ["README.md", "README.en.md", "README.zh-CN.md", "docs/shared-versioning-and-confirmation.md", "docs/skill-template-pack-usage.md", "docs/codex_agent_tuning_prd.md"]
    )
    for phrase in NON_GOALS:
        require(phrase.lower() in docs_and_readme.lower(), f"missing non-goal documentation: {phrase}", errors)
    for phrase in PLUGIN_DOC_PHRASES:
        require(phrase.lower() in docs_and_readme.lower(), f"missing plugin documentation phrase: {phrase}", errors)

    for phrase in PRD_REFERENCES:
        require(contains_any(all_text, [phrase]), f"missing PRD traceability phrase: {phrase}", errors)

    tuning_text = existing_texts.get("skills/atk-tune/SKILL.md", "")
    for heading in ["## 目标异常清单", "## 调优手段", "## 关联改动"]:
        require(heading in tuning_text, f"tuning Skill missing exact heading {heading}", errors)

    report_text = existing_texts.get("skills/atk-report/SKILL.md", "")
    for status in ["已解决", "部分解决", "未解决", "无法判断"]:
        require(status in report_text, f"report Skill missing cross-version status {status}", errors)

    runner_template = existing_texts.get("templates/.atk/runner/eval_runner.py.md", "")
    require(
        "return list(source_fieldnames) + [\"agent_output\"] + fixed_output_fields + auxiliary_fields"
        in runner_template,
        "runner template must append agent_output, fixed status fields, and auxiliary agent_output_* columns after original columns",
        errors,
    )
    require(
        "\"agent_output\"" in runner_template and "reserved_output_fields" in runner_template,
        "runner template must guard source agent_output conflict",
        errors,
    )
    require(
        "AGENT_OUTPUT_LOG_PATH_FIELD = \"agent_output_log_path\"" in runner_template,
        "runner template must define stable agent_output_log_path field",
        errors,
    )
    require(
        "DATASETS_DIR = Path(\".atk/datasets\")" in runner_template
        and "DATASET_PATH = DATASETS_DIR / \"TODO_AGENT_TUNING_DATASET_SNAPSHOT\"" in runner_template,
        "runner template must read the dataset from a .atk/datasets snapshot",
        errors,
    )
    require(
        "PYTHON_LOGGING_CAPTURE_ENABLED" in runner_template
        and "ROW_LOGGER_NAMES" in runner_template
        and "ROW_LOG_FORMAT" in runner_template
        and "ROW_LOG_LEVEL" in runner_template
        and "ROW_LOGS_DIRNAME" in runner_template
        and "CONCURRENT_ROW_LOGGING_ENABLED" in runner_template,
        "runner template must expose row-log generated config constants",
        errors,
    )
    require("contextvars" in runner_template and "ContextVar" in runner_template, "runner template must use contextvars for row attribution", errors)
    require("class ATKRowLogHandler(logging.Handler)" in runner_template, "runner template must use an ATK-owned stdlib logging.Handler router for row logs", errors)
    require("log_path.open(\"w\", encoding=\"utf-8\").close()" in runner_template, "runner template must truncate row logs on reused partial versions", errors)
    require("_ACTIVE_ROW_LOG_CONTEXT.reset(token)" in runner_template, "runner template must reset row logging context tokens in finally", errors)
    require("row_{source_index:06d}.log" in runner_template, "runner template must use source-row-numbered row log filenames", errors)
    require(".as_posix()" in runner_template, "runner template must serialize row-log CSV paths as POSIX paths", errors)
    require("AGENT_OUTPUT_LOG_PATH_FIELD" in runner_template and "reserved_output_fields" in runner_template, "runner template must guard agent_output_log_path source conflicts", errors)
    require("ROW_LOGGING_CONCURRENCY_DOWNGRADE_MESSAGE" in runner_template and "sys.__stderr__" in runner_template, "runner template must report concurrency row-log downgrade outside app.log redirect", errors)
    require("logger.setLevel(ROW_LOG_LEVEL)" in runner_template and "logger.setLevel(previous_levels[logger])" in runner_template, "runner template must restore row logger levels", errors)
    require("logger.addHandler(row_handler)" in runner_template and "logger.removeHandler(row_handler)" in runner_template, "runner template must install and remove the ATK row handler once per run", errors)
    require("except Exception as exc" not in runner_template, "runner template must not catch broad Exception and mask configuration failures", errors)
    require(
        "except UserActionRequired:" in runner_template
        and "# Configuration/TODO/confirmation failures must stop the run" in runner_template,
        "runner template must propagate UserActionRequired before row-error handling",
        errors,
    )
    require("--limit" in runner_template and "--offset" in runner_template, "runner template must support bounded runs", errors)
    require("--concurrency" in runner_template and "ThreadPoolExecutor" in runner_template, "runner template must support concurrent runs", errors)
    require("row_logging_enabled=row_logging_enabled" in runner_template, "concurrent worker path must receive row logging state instead of forcing false", errors)
    require("writer.writerow(result_row)" in runner_template and "os.fsync(handle.fileno())" in runner_template, "runner template must write and flush results incrementally", errors)

    init_rules_skill = existing_texts.get("skills/atk-init-failure-rule/SKILL.md", "")
    rules_skill = existing_texts.get("skills/atk-find-failures-by-rule/SKILL.md", "")
    llm_skill = existing_texts.get("skills/atk-find-failures/SKILL.md", "")
    require("atk-init-failure-rule" in init_rules_skill, "rules initializer Skill identity missing", errors)
    require("atk-find-failures-by-rule" in rules_skill, "rules failure-finding Skill identity missing", errors)
    require("atk-find-failures" in llm_skill, "LLM failure-finding Skill identity missing", errors)
    require("failure_rule.py" in init_rules_skill and "failure_rule.py" in rules_skill, "rules initializer and executor must share failure_rule.py", errors)
    require("failure_cases.csv" in rules_skill and "failure_cases.csv" in llm_skill, "both failure-finding Skills must write failure_cases.csv", errors)

    filter_template = existing_texts.get("templates/.atk/runner/failure_rule.py.md", "")
    require("Conservative placeholder" not in filter_template, "filter template must not ship a runnable placeholder heuristic", errors)

    validate_manifest(errors)
    validate_installer(errors)

    if errors:
        print("Agent Tune Kit validation FAILED", file=sys.stderr)
        for error in errors:
            if error:
                print(f"- {error}", file=sys.stderr)
        return 1

    print("Agent Tune Kit validation passed")
    print(f"Checked {len(REQUIRED_FILES)} files, {len(SKILL_FILES)} Skill templates, plugin manifest, and installer tooling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
