# Agent Tune Kit Local Plugin and Skill Pack Usage

Agent Tune Kit provides a local Codex plugin for the manual Agent tuning loop described in `docs/codex_agent_tuning_prd.md`. The supported setup path is the local plugin installer; do not split-copy individual `skills/*` directories because they reference shared pack assets by relative path.

This pass is a local-product minimum: `.codex-plugin/plugin.json`, personal marketplace registration, one-command installer orchestration, local smoke/status validation, installer-state backup/rollback, and a guided status Skill. It deliberately avoids public marketplace publishing, brand assets/screenshots, hidden one-click orchestration across the Agent tuning loop, bundled example Agent/data fixtures, automatic Agent tuning workflow rollback, universal schemas, old installer command compatibility, and a full E2E test suite against a real Agent service.

## Local plugin install and smoke

Use the main command for normal setup:

```sh
python3 scripts/install_plugin.py install
```

Default behavior:

- validates `.codex-plugin/plugin.json`;
- marketplace path: `~/.agents/plugins/marketplace.json`;
- plugin store: `~/plugins`;
- marketplace entry name: `agent-tune-kit`;
- marketplace `source.path`: `./plugins/agent-tune-kit`;
- default install target: `~/plugins/agent-tune-kit` as a symlink to this repository;
- runs local smoke/status checks by default;
- prints `/plugins` enablement guidance without claiming hidden Codex UI `Installed` state.

Useful commands:

```sh
python3 scripts/install_plugin.py preview --smoke
python3 scripts/install_plugin.py status
python3 scripts/install_plugin.py rollback --backup <backup-id>
```

Conflict and rollback behavior:

- `preview` never writes marketplace/plugin-store files or backups;
- interactive terminals prompt before replacing conflicting marketplace/plugin-store state;
- noninteractive destructive replacement requires `--yes --force`;
- `--yes` alone does not replace conflicts;
- `--no-input` never waits for prompts;
- destructive replacement creates a backup under `~/.agents/plugins/backups/agent-tune-kit/<backup-id>/` by default;
- rollback restores only installer-managed marketplace/plugin-store state, not Agent tuning workflow changes.

For isolated smoke tests, use temp paths:

```sh
python3 scripts/install_plugin.py install \
  --marketplace-path /tmp/agent-tune-marketplace.json \
  --plugin-store /tmp/agent-tune-plugins \
  --backup-root /tmp/agent-tune-backups \
  --yes --force
python3 scripts/install_plugin.py status \
  --marketplace-path /tmp/agent-tune-marketplace.json \
  --plugin-store /tmp/agent-tune-plugins
```

The installer writes marketplace JSON atomically where practical and reports smoke status. It does not create a public marketplace package and does not mutate hidden Codex UI enablement state.

After install, the plugin should be visible/available in the Personal marketplace. Open `/plugins`, select `Agent Tune Kit`, and enable it there if needed. If `$atk-status` does not appear in autocomplete after enabling the plugin, restart Codex or open a new Codex session for the project. Current Codex sessions may not hot-load Skills from a plugin that was enabled after the session started.

## Repository layout boundary

Do not copy a single `skills/*` directory by itself; keep `skills/`, `templates/`, and `docs/` together unless a future packaging pass intentionally inlines those shared assets. The plugin installer is the supported setup path; this project does not keep old installer command compatibility before launch.

## What is included

- `.codex-plugin/plugin.json` — local plugin manifest using `skills: "./skills/"`.
- `skills/atk-status/SKILL.md` — guided router/status Skill that recommends the next step without bypassing confirmation gates.
- `skills/atk-init/SKILL.md` — generate `.atk/runner/eval_runner.py`.
- `skills/atk-run/SKILL.md` — run `.atk/runner/eval_runner.py` through a short Skill command and summarize the current results version.
- `skills/atk-find-failures-by-rule/SKILL.md` — generate or update `.atk/runner/find_failures_by_rule.py` for rule-based failure finding.
- `skills/atk-find-failures/SKILL.md` — inspect current `eval_results.csv` and write current `failure_cases.csv` using model judgment.
- `skills/atk-report/SKILL.md` — write current `report.md`, including adjacent-version validation when possible.
- `skills/atk-tune/SKILL.md` — tune the target Agent and write current `tuning_plan.md`.
- `templates/.atk/runner/eval_runner.py.md` — script template preserving original dataset columns and appending `agent_output`.
- `templates/.atk/runner/find_failures_by_rule.py.md` — stdlib CSV rule-filter template.
- `docs/shared-versioning-and-confirmation.md` — shared current/new version semantics and confirmation triggers.
- `scripts/install_plugin.py` — safe local marketplace installer/smoke/status/rollback tool.
- `scripts/validate_skill_pack.py` — lightweight static checker for this local plugin pack.

## Manual 2.2 → 2.6 loop

1. Prepare the local Agent service and evaluation dataset.
2. Trigger `atk-status` to inspect state and route to the right stage.
3. Trigger `atk-init` in Codex. The Skill reads the Agent source and dataset, asks only about unsafe ambiguity, then writes `.atk/runner/eval_runner.py`.
4. Trigger `atk-run`. It executes `python3 .atk/runner/eval_runner.py`; the runner creates or reuses a version directory and writes `.atk/results/vN/eval_results.csv` plus optional `app.log`.
5. Choose one failure-finding entry:
   - Trigger `atk-find-failures-by-rule`, then manually run `python3 .atk/runner/find_failures_by_rule.py` to write `failure_cases.csv`.
   - Or trigger `atk-find-failures` to write `failure_cases.csv` directly from the current `eval_results.csv`.
6. Trigger `atk-report` to create `.atk/results/vN/report.md`. From `v2` onward, it compares the current version with the previous existing version and reads the previous `tuning_plan.md` when available.
7. Trigger `atk-tune` to change the Agent and write `.atk/results/vN/tuning_plan.md`.
8. Optionally create a user git commit/checkpoint. Agent tuning rollback remains user-git-only guidance; this plugin does not automate Agent code restore.
9. Run the same loop again. The next test run creates `v{N+1}` when the current max version already has `eval_results.csv`.

## Version example: v1 → v2

- First test run: no version exists, so `eval_runner.py` creates `.atk/results/v1/eval_results.csv`.
- First report: no previous version exists, so `v1/report.md` is a single-version report.
- First tuning: writes `v1/tuning_plan.md` with `## 目标异常清单`, `## 调优手段`, and `## 关联改动`.
- Second test run: `v1/eval_results.csv` exists, so the runner creates `v2/eval_results.csv`.
- Second report: `v2/report.md` reads `v1/tuning_plan.md` and classifies each target as `已解决`, `部分解决`, `未解决`, or `无法判断`.

## Static validation

Run:

```sh
python3 scripts/validate_skill_pack.py
git diff --check
find skills templates docs scripts .codex-plugin -maxdepth 4 -type f | sort
grep -R "agent_output\|failure_cases.csv\|tuning_plan.md\|目标异常清单\|调优手段\|关联改动\|source.path" skills templates docs README.md README.zh-CN.md .codex-plugin scripts
```

`validate_skill_pack.py` checks required files, required sections, manifest fields, installer behavior, PRD traceability, canonical version helper names and snippets, output paths, non-goal boundaries, and each Skill's precondition/failure behavior.
