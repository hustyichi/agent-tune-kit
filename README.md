# Agent tune kit

English | [简体中文](README.zh-CN.md)

Local Codex plugin for iterative local Agent tuning, with a legacy copy/register path for users who only want the Skill template pack.

## Current scope

This repository now ships as a local Codex plugin with `.codex-plugin/plugin.json`, six Skills, reusable runner/filter templates, shared versioning rules, docs, a safe personal marketplace installer/smoke tool, and static validation. The original five stage Skills and relative `skills/`, `templates/`, and `docs/` contracts remain valid for legacy copy/register use.

Out of scope for this pass: no public marketplace release, no brand assets/screenshots, no one-click orchestration, no universal Schema requirement, no bundled example Agent/data fixtures, no automatic rollback or baseline restore, and no full E2E test suite.

## Who this is for

Use Agent Tune Kit when you have a local Agent implementation and an evaluation dataset, and you want Codex to help you run a manual, repeatable tuning loop:

1. start or resume the loop with `agent-tuning-start`;
2. generate a project-local test runner;
3. run the Agent against the dataset;
4. identify abnormal cases;
5. generate a report with root-cause analysis and adjacent-version validation;
6. apply focused Agent tuning;
7. repeat with versioned results under `agent-tuning/results/vN/`.

## Prerequisites

- Codex with local plugin/Skill support, or a Skill environment that can load these `skills/*/SKILL.md` files.
- Python 3 for installer tooling, generated runner/filter scripts, and static validation.
- A target local Agent project that Codex can inspect and edit.
- An evaluation dataset, preferably CSV. Other formats can be supported by adapting the generated runner.
- A normal user-managed git workflow for checkpoints and rollback. This plugin does not automate rollback.

## Quickstart

1. Validate the repo contents:

   ```sh
   python3 scripts/validate_skill_pack.py
   git diff --check
   ```

2. Preview local plugin registration. Dry-run is the default:

   ```sh
   python3 scripts/install_plugin.py --dry-run --smoke
   ```

3. Install into the personal marketplace only when the preview is acceptable:

   ```sh
   python3 scripts/install_plugin.py --apply --smoke
   ```

   The installer writes or updates `~/.agents/plugins/marketplace.json`, keeps marketplace `source.path` as `./plugins/agent-tune-kit`, and points `~/plugins/agent-tune-kit` at this repo by symlink. Use `--copy` for explicit copy fallback and `--force` only to replace an existing same-name entry or plugin-store target.

4. Legacy copy/register fallback: copy or register the whole repository-native pack and keep `skills/`, `templates/`, and `docs/` together. Do not copy one Skill directory by itself unless you also preserve or inline shared assets.

5. In Codex, trigger `agent-tuning-start` to inspect the target project's `agent-tuning/` state and recommend the next stage.

6. For a new target project, trigger `agent-tuning-generate-runner`. Provide or point Codex to the Agent source and evaluation dataset. The Skill writes `agent-tuning/runner/test_runner.py`.

7. Manually run the generated runner:

   ```sh
   python3 agent-tuning/runner/test_runner.py
   ```

   The runner writes `agent-tuning/results/v1/results.csv` on the first run, with all original dataset columns plus the required `agent_output` column. It may also write `app.log` when reliable log capture is available.

8. Create `abnormal_cases.csv` for the current version using one abnormal filtering Skill:
   - `agent-tuning-filter-abnormal-rules`: Codex generates or updates `agent-tuning/runner/filter_abnormal.py`; then you manually run `python3 agent-tuning/runner/filter_abnormal.py`.
   - `agent-tuning-filter-abnormal-llm`: Codex reads the current `results.csv` and writes `abnormal_cases.csv` directly.
9. Trigger `agent-tuning-report` to write `agent-tuning/results/vN/report.md` with statistics, abnormal-case analysis, root-cause hypotheses, and adjacent-version tuning validation when a previous `tuning_plan.md` exists.
10. Trigger `agent-tuning-apply-tuning` to tune the Agent and write `agent-tuning/results/vN/tuning_plan.md` with the required headings `## 目标异常清单`, `## 调优手段`, and `## 关联改动`.
11. Repeat the loop. `test_runner.py` creates the next `vN` when the current version already has `results.csv`; report generation from `v2` onward validates whether the previous tuning goals were resolved.

## Included files

- `.codex-plugin/plugin.json`
- `skills/agent-tuning-start/SKILL.md`
- `skills/agent-tuning-generate-runner/SKILL.md`
- `skills/agent-tuning-filter-abnormal-rules/SKILL.md`
- `skills/agent-tuning-filter-abnormal-llm/SKILL.md`
- `skills/agent-tuning-report/SKILL.md`
- `skills/agent-tuning-apply-tuning/SKILL.md`
- `templates/agent-tuning/runner/test_runner.py.md`
- `templates/agent-tuning/runner/filter_abnormal.py.md`
- `docs/shared-versioning-and-confirmation.md`
- `docs/skill-template-pack-usage.md`
- `docs/codex_agent_tuning_prd.md`
- `scripts/install_plugin.py`
- `scripts/validate_skill_pack.py`

## Output layout in the target Agent project

```text
agent-tuning/
├── runner/
│   ├── test_runner.py
│   └── filter_abnormal.py        # only when rule-based filtering is used
└── results/
    ├── v1/
    │   ├── results.csv
    │   ├── app.log               # optional
    │   ├── abnormal_cases.csv
    │   ├── report.md
    │   └── tuning_plan.md
    └── v2/
        └── ...
```

## Versioning rules users need to know

- The generated runner is the only component that creates or reuses result versions.
- If no `vN` exists, the runner creates `v1`.
- If the largest `vN` already contains `results.csv`, the runner creates `v{N+1}`.
- If the largest `vN` does not contain `results.csv`, the runner reuses that directory.
- Non-runner Skills always use the numerically largest existing `vN` as the current version and never fall back to an older version when a required file is missing.

## Validate and smoke the plugin

```sh
python3 scripts/validate_skill_pack.py
git diff --check
python3 scripts/install_plugin.py --dry-run --smoke
python3 scripts/install_plugin.py --marketplace-path /tmp/agent-tune-marketplace.json --plugin-store /tmp/agent-tune-plugins --apply --smoke
```

The validator fails loudly when required Skill sections, manifest fields, installer behavior, PRD references, version helper snippets, output paths, non-goals, or tuning/report contracts are missing.
