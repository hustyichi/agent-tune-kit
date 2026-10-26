#!/usr/bin/env python3
"""Validate bundled Skill contracts and their shared CLI implementations."""

from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "agent_tune_kit/results/evidence.py",
    "agent_tune_kit/execution/tracing.py",
    "templates/.atk/runner/import_evidence.py.md",
    ".codex-plugin/plugin.json",
    "skills/atk-dataset/SKILL.md",
    "skills/atk-dataset/scripts/update_dataset.py",
    "skills/atk-init/SKILL.md",
    "skills/atk-eval/SKILL.md",
    "skills/atk-diagnose/SKILL.md",
    "skills/atk-report/SKILL.md",
    "skills/atk-visualize-failures/SKILL.md",
    "skills/atk-visualize-failures/scripts/generate_failure_browser.py",
    "skills/atk-visualize-dataset/SKILL.md",
    "skills/atk-visualize-dataset/scripts/generate_dataset_browser.py",
    "skills/atk-optimize/SKILL.md",
    "templates/.atk/runner/eval_runner.py.md",
    "templates/.atk/runner/failure_rule.py.md",
    "docs/skill-template-pack-usage.md",
    "docs/shared-versioning-and-confirmation.md",
    "docs/codex_agent_tuning_prd.md",
    "scripts/install_plugin.py",
    "scripts/dev-refresh-install.sh",
    "scripts/check-release.py",
    "scripts/publish-release.py",
    "scripts/publish-pypi.sh",
    "pyproject.toml",
    "agent_tune_kit/__init__.py",
    "agent_tune_kit/installer.py",
    "README.md",
    "README.en.md",
    "agent_tune_kit/cli/__init__.py",
    "agent_tune_kit/cli/__main__.py",
    "agent_tune_kit/cli/dataset.py",
    "agent_tune_kit/cli/evaluation.py",
    "agent_tune_kit/cli/main.py",
    "agent_tune_kit/cli/output.py",
    "agent_tune_kit/cli/results.py",
    "agent_tune_kit/cli/visualize.py",
    "agent_tune_kit/datasets/__init__.py",
    "agent_tune_kit/datasets/prepare.py",
    "agent_tune_kit/datasets/updates.py",
    "agent_tune_kit/datasets/validation.py",
    "agent_tune_kit/errors.py",
    "agent_tune_kit/execution/__init__.py",
    "agent_tune_kit/execution/logs.py",
    "agent_tune_kit/execution/rules.py",
    "agent_tune_kit/execution/runner.py",
    "agent_tune_kit/execution/runtime.py",
    "agent_tune_kit/results/__init__.py",
    "agent_tune_kit/results/judgments.py",
    "agent_tune_kit/results/summary.py",
    "agent_tune_kit/results/versions.py",
    "agent_tune_kit/utils/__init__.py",
    "agent_tune_kit/utils/csv_io.py",
    "agent_tune_kit/utils/files.py",
    "agent_tune_kit/visualize/__init__.py",
    "agent_tune_kit/visualize/dataset.py",
    "agent_tune_kit/visualize/failures.py",
    "agent_tune_kit/visualize/fields.py",
    "agent_tune_kit/visualize/history.py",
    "agent_tune_kit/visualize/render.py",
    "agent_tune_kit/workspace.py",
    "agent_tune_kit/visualize/assets/dataset/app.js",
    "agent_tune_kit/visualize/assets/dataset/page.html",
    "agent_tune_kit/visualize/assets/dataset/styles.css",
    "agent_tune_kit/visualize/assets/dataset/vendor/echarts.min.js",
    "agent_tune_kit/visualize/assets/failures/app.js",
    "agent_tune_kit/visualize/assets/failures/page.html",
    "agent_tune_kit/visualize/assets/failures/styles.css",
]

SKILL_FILES = [
    "skills/atk-dataset/SKILL.md",
    "skills/atk-init/SKILL.md",
    "skills/atk-eval/SKILL.md",
    "skills/atk-diagnose/SKILL.md",
    "skills/atk-report/SKILL.md",
    "skills/atk-visualize-failures/SKILL.md",
    "skills/atk-visualize-dataset/SKILL.md",
    "skills/atk-optimize/SKILL.md",
]

PER_FILE_PHRASES = {
    ".codex-plugin/plugin.json": ['"name": "agent-tune-kit"', '"version": "2.1.1"', '"skills": "./skills/"'],
    "skills/atk-dataset/SKILL.md": [
        "## Purpose",
        "## Inputs",
        "## Outputs",
        "## Workflow",
        "## Failure behavior",
        "## CLI dependency",
        "uv tool install agent-tune-kit",
        "Do not create `.atk/results/vN`",
        "Do not run the Agent",
        "Do not run `$atk-eval`",
        "Do not write `failure_cases.csv`",
        "atk dataset update",
        "atk dataset inspect",
    ],
    "skills/atk-init/SKILL.md": [
        "## Purpose",
        "## Inputs",
        "## Outputs",
        "## Workflow",
        "## Failure behavior",
        "## CLI dependency",
        "uv tool install agent-tune-kit",
    ],
    "skills/atk-eval/SKILL.md": [
        "## Purpose",
        "## Inputs",
        "## Outputs",
        "## Workflow",
        "## Failure behavior",
        "## CLI dependency",
        "uv tool install agent-tune-kit",
        "atk eval",
    ],
    "skills/atk-diagnose/SKILL.md": [
        "## Purpose",
        "## Inputs",
        "## Outputs",
        "## Workflow",
        "## Failure behavior",
        "## CLI dependency",
        "uv tool install agent-tune-kit",
        "atk results apply-judgments",
    ],
    "skills/atk-report/SKILL.md": [
        "## Purpose",
        "## Inputs",
        "## Outputs",
        "## Workflow",
        "## Failure behavior",
        "## CLI dependency",
        "uv tool install agent-tune-kit",
        "atk results compare",
    ],
    "skills/atk-visualize-failures/SKILL.md": [
        "## Purpose",
        "## Inputs",
        "## Outputs",
        "## Workflow",
        "## Failure behavior",
        "## CLI dependency",
        "uv tool install agent-tune-kit",
        "atk visualize failures",
    ],
    "skills/atk-visualize-dataset/SKILL.md": [
        "## Purpose",
        "## Inputs",
        "## Outputs",
        "## Workflow",
        "## Failure behavior",
        "## CLI dependency",
        "uv tool install agent-tune-kit",
        "atk visualize dataset",
    ],
    "skills/atk-optimize/SKILL.md": [
        "## Purpose",
        "## Inputs",
        "## Outputs",
        "## Workflow",
        "## Failure behavior",
        "## CLI dependency",
        "uv tool install agent-tune-kit",
    ],
}

PLUGIN_DOC_PHRASES = [
    "local Codex plugin",
    ".codex-plugin/plugin.json",
    "uvx --from agent-tune-kit atk install",
    "uv tool install agent-tune-kit",
    "pipx install agent-tune-kit",
    "atk install",
    "atk preview --smoke",
    "atk status",
    "scripts/install_plugin.py install",
    "source.path",
    "./plugins/agent-tune-kit",
    "--yes --force",
    "Codex UI enablement state",
]

DATASET_ONLY_PRE_RESULTS_SKILL_FILES = {"skills/atk-dataset/SKILL.md", "skills/atk-visualize-dataset/SKILL.md"}

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

NON_LIFECYCLE_SKILL_FILES = DATASET_ONLY_PRE_RESULTS_SKILL_FILES
LIFECYCLE_SKILL_FILES = [p for p in SKILL_FILES if p not in NON_LIFECYCLE_SKILL_FILES]


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
    require(
        bool(re.fullmatch(r"\d+\.\d+\.\d+", str(manifest.get("version", "")))),
        "manifest version must be strict semver",
        errors,
    )
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
    for key in [
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
        "capabilities",
        "defaultPrompt",
    ]:
        require(key in interface, f"manifest interface missing {key}", errors)
    prompts = interface.get("defaultPrompt")
    require(
        isinstance(prompts, list) and 1 <= len(prompts) <= 3, "manifest defaultPrompt must contain 1-3 prompts", errors
    )
    if isinstance(prompts, list):
        for prompt in prompts:
            require(
                isinstance(prompt, str) and len(prompt) <= 128,
                f"manifest defaultPrompt too long or non-string: {prompt!r}",
                errors,
            )
    for asset_field in ["composerIcon", "logo"]:
        asset = interface.get(asset_field)
        if asset:
            require((ROOT / asset).exists(), f"manifest {asset_field} points to missing asset: {asset}", errors)
    screenshots = interface.get("screenshots", [])
    require(isinstance(screenshots, list), "manifest screenshots must be an array", errors)
    if isinstance(screenshots, list):
        for screenshot in screenshots:
            require(
                str(screenshot).startswith("./assets/") and str(screenshot).endswith(".png"),
                f"manifest screenshot must be ./assets/*.png: {screenshot}",
                errors,
            )
            require((ROOT / str(screenshot)).exists(), f"manifest screenshot file missing: {screenshot}", errors)


def validate_installer(errors: list[str]) -> None:
    wrapper_text = read_rel("scripts/install_plugin.py") if (ROOT / "scripts/install_plugin.py").exists() else ""
    text = read_rel("agent_tune_kit/installer.py") if (ROOT / "agent_tune_kit/installer.py").exists() else ""
    require("agent_tune_kit.cli" in wrapper_text, "wrapper must delegate to packaged atk CLI", errors)
    require(
        'DEFAULT_MARKETPLACE = Path("~/.agents/plugins/marketplace.json")' in text,
        "installer must default to personal marketplace",
        errors,
    )
    require('DEFAULT_PLUGIN_STORE = Path("~/plugins")' in text, "installer must default to ~/plugins", errors)
    for phrase in [
        "AVAILABLE",
        "ON_INSTALL",
        "category",
        "Coding",
        "atomic",
        "os.replace",
        "smoke-resolved plugin path",
        "Codex UI boundary",
        "--yes --force",
        "PayloadSource",
        "package-resource",
    ]:
        require(phrase in text, f"installer missing behavior phrase: {phrase}", errors)


def validate_plugin_root_template_resolution(existing_texts: dict[str, str], errors: list[str]) -> None:
    required_resolution_phrases = [
        "plugin-root-relative",
        "Template paths are plugin-root-relative, not relative to this Skill directory.",
        "Locate the plugin root by walking upward",
        ".codex-plugin/plugin.json",
        "plugin-root discovery is the preferred resolution rule",
    ]
    for rel in SKILL_FILES:
        text = existing_texts.get(rel, "")
        if "templates/" not in text:
            continue
        for phrase in required_resolution_phrases:
            require(phrase in text, f"{rel} missing plugin-root template resolution phrase: {phrase}", errors)


def main():
    errors = []
    for rel in REQUIRED_FILES:
        require((ROOT / rel).is_file(), f"missing required file: {rel}", errors)
    texts = {rel: read_rel(rel) for rel in REQUIRED_FILES if (ROOT / rel).is_file()}
    for rel, phrases in PER_FILE_PHRASES.items():
        for phrase in phrases:
            require(phrase in texts.get(rel, ""), f"{rel} missing contract: {phrase}", errors)
    for rel in SKILL_FILES:
        text = texts.get(rel, "")
        require(text.startswith("---\n"), f"{rel} missing front matter", errors)
        require("compatibility:" in text, f"{rel} must declare CLI dependency", errors)
    validate_manifest(errors)
    validate_installer(errors)
    validate_plugin_root_template_resolution(texts, errors)
    docs = "\n".join(
        texts.get(rel, "")
        for rel in [
            "README.md",
            "README.en.md",
            "docs/shared-versioning-and-confirmation.md",
            "docs/skill-template-pack-usage.md",
            "docs/codex_agent_tuning_prd.md",
        ]
    )
    for phrase in NON_GOALS + PLUGIN_DOC_PHRASES:
        require(phrase.lower() in docs.lower(), f"missing documentation contract: {phrase}", errors)
    version = tomllib.loads(read_rel("pyproject.toml"))["project"]["version"]
    manifest = json.loads(read_rel(".codex-plugin/plugin.json"))
    require(manifest["version"] == version, "package/plugin version mismatch", errors)
    for name, marker, entry in [
        ("eval_runner", "ATK_MANAGED_RUNNER", "call_agent"),
        ("failure_rule", "ATK_MANAGED_RULE", "judge"),
    ]:
        rel = f"templates/.atk/runner/{name}.py.md"
        match = re.search(r"```python\n(.*?)\n```", texts.get(rel, ""), re.S)
        require(bool(match), f"{rel} needs executable Python template", errors)
        if match:
            try:
                tree = ast.parse(match.group(1))
                functions = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                require(entry in functions and marker in match.group(1), f"{rel} missing adapter contract", errors)
                if name == "eval_runner":
                    require(
                        "ATK_ADAPTER_VERSION = 2" in match.group(1) and "retain" in functions,
                        f"{rel} must declare V2 and explicit retention",
                        errors,
                    )
                require(
                    not {"allocate_next_results_version", "run_rows", "write_failures"} & functions,
                    f"{rel} duplicates shared engine",
                    errors,
                )
                require(
                    "TODO_AGENT_TUNING" in match.group(1) and "UserActionRequired" in match.group(1),
                    f"{rel} must reject unimplemented invocation",
                    errors,
                )
            except SyntaxError as exc:
                errors.append(f"{rel} invalid Python: {exc}")
    for heading in ["## 目标异常清单", "## 调优手段", "## 关联改动"]:
        require(heading in texts.get("skills/atk-optimize/SKILL.md", ""), f"missing tuning heading {heading}", errors)
    for status in ["已解决", "部分解决", "未解决", "无法判断"]:
        require(status in texts.get("skills/atk-report/SKILL.md", ""), f"missing comparison status {status}", errors)
    if errors:
        print("Agent Tune Kit validation failed", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Agent Tune Kit validation passed")
    print(f"Checked {len(REQUIRED_FILES)} files, {len(SKILL_FILES)} Skill templates and shared CLI resources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
