# Agent Tune Kit Local Plugin and Skill Pack Usage

Agent Tune Kit now provides a local Codex plugin for the manual Agent tuning loop described in `docs/codex_agent_tuning_prd.md`. The same files also remain usable through a legacy copy/register boundary: keep `skills/`, `templates/`, and `docs/` together because individual Skill directories reference shared pack assets by relative path.

This pass is a local-product minimum: `.codex-plugin/plugin.json`, personal marketplace registration, smoke validation, and a guided start Skill. It deliberately avoids public marketplace publishing, brand assets/screenshots, one-click orchestration, bundled example Agent/data fixtures, auto rollback, universal schemas, and a full E2E test suite.

## Local plugin install and smoke

Preview first; dry-run is the default:

```sh
python3 scripts/validate_skill_pack.py
python3 scripts/install_plugin.py --dry-run --smoke
```

Install only after the preview is acceptable:

```sh
python3 scripts/install_plugin.py --apply --smoke
```

Default behavior:

- marketplace path: `~/.agents/plugins/marketplace.json`;
- plugin store: `~/plugins`;
- marketplace entry name: `agent-tune-kit`;
- marketplace `source.path`: `./plugins/agent-tune-kit`;
- default install target: `~/plugins/agent-tune-kit` as a symlink to this repository;
- `--copy` is an explicit copy fallback;
- `--force` is required before replacing an existing same-name marketplace entry pointing elsewhere or a plugin-store target.

For isolated smoke tests, use temp paths:

```sh
python3 scripts/install_plugin.py \
  --marketplace-path /tmp/agent-tune-marketplace.json \
  --plugin-store /tmp/agent-tune-plugins \
  --apply \
  --smoke
```

The installer writes marketplace JSON atomically where practical and reports smoke cleanup status. It does not create a public marketplace package.

After `--apply --smoke`, the plugin is available in the Personal marketplace but not enabled yet. Open `/plugins`, select `Agent Tune Kit`, and install/enable it there. The Skill commands become available after the plugin status changes from `Available` to `Installed`.

## Copy/register boundary

If you do not want plugin registration, use the legacy copy/register path: copy or register the repository-native pack as a whole. Do not copy a single `skills/*` directory by itself unless you also preserve the referenced shared docs and templates or intentionally inline them. This keeps existing Skill users compatible while the plugin path adds local Codex UI discovery.

## What is included

- `.codex-plugin/plugin.json` — local plugin manifest using `skills: "./skills/"`.
- `skills/atk-start/SKILL.md` — guided router/status Skill that recommends the next step without bypassing confirmation gates.
- `skills/atk-setup/SKILL.md` — generate `agent-tuning/runner/test_runner.py`.
- `skills/atk-run/SKILL.md` — run `agent-tuning/runner/test_runner.py` through a short Skill command and summarize the current results version.
- `skills/atk-filter-rules/SKILL.md` — generate or update `agent-tuning/runner/filter_abnormal.py` for manual rule-based filtering.
- `skills/atk-filter/SKILL.md` — inspect current `results.csv` and write current `abnormal_cases.csv` using model judgment.
- `skills/atk-report/SKILL.md` — write current `report.md`, including adjacent-version validation when possible.
- `skills/atk-apply/SKILL.md` — tune the target Agent and write current `tuning_plan.md`.
- `templates/agent-tuning/runner/test_runner.py.md` — script template preserving original dataset columns and appending `agent_output`.
- `templates/agent-tuning/runner/filter_abnormal.py.md` — stdlib CSV rule-filter template.
- `docs/shared-versioning-and-confirmation.md` — shared current/new version semantics and confirmation triggers.
- `scripts/install_plugin.py` — safe local marketplace installer/smoke tool.
- `scripts/validate_skill_pack.py` — lightweight static checker for this local plugin and legacy pack.

## Manual 2.2 → 2.6 loop

1. Prepare the local Agent service and evaluation dataset.
2. Trigger `atk-start` to inspect state and route to the right stage.
3. Trigger `atk-setup` in Codex. The Skill reads the Agent source and dataset, asks only about unsafe ambiguity, then writes `agent-tuning/runner/test_runner.py`.
4. Trigger `atk-run`. It executes `python3 agent-tuning/runner/test_runner.py`; the runner creates or reuses a version directory and writes `agent-tuning/results/vN/results.csv` plus optional `app.log`.
5. Choose one abnormal filtering entry:
   - Trigger `atk-filter-rules`, then manually run `python3 agent-tuning/runner/filter_abnormal.py` to write `abnormal_cases.csv`.
   - Or trigger `atk-filter` to write `abnormal_cases.csv` directly from the current `results.csv`.
6. Trigger `atk-report` to create `agent-tuning/results/vN/report.md`. From `v2` onward, it compares the current version with the previous existing version and reads the previous `tuning_plan.md` when available.
7. Trigger `atk-apply` to change the Agent and write `agent-tuning/results/vN/tuning_plan.md`.
8. Optionally create a user git commit/checkpoint. Rollback remains user-git-only guidance; this plugin does not automate restore.
9. Run the same loop again. The next test run creates `v{N+1}` when the current max version already has `results.csv`.

## Version example: v1 → v2

- First test run: no version exists, so `test_runner.py` creates `agent-tuning/results/v1/results.csv`.
- First report: no previous version exists, so `v1/report.md` is a single-version report.
- First tuning: writes `v1/tuning_plan.md` with `## 目标异常清单`, `## 调优手段`, and `## 关联改动`.
- Second test run: `v1/results.csv` exists, so the runner creates `v2/results.csv`.
- Second report: `v2/report.md` reads `v1/tuning_plan.md` and classifies each target as `已解决`, `部分解决`, `未解决`, or `无法判断`.

## Static validation

Run:

```sh
python3 scripts/validate_skill_pack.py
git diff --check
find skills templates docs scripts .codex-plugin -maxdepth 4 -type f | sort
grep -R "agent_output\|abnormal_cases.csv\|tuning_plan.md\|目标异常清单\|调优手段\|关联改动\|source.path" skills templates docs README.md README.zh-CN.md .codex-plugin scripts
```

`validate_skill_pack.py` checks required files, required sections, manifest fields, installer behavior, PRD traceability, canonical version helper names and snippets, output paths, non-goal boundaries, and each Skill's precondition/failure behavior.
