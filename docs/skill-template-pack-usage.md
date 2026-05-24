# Agent tune kit Skill Template Pack Usage

This pack provides repository-native Codex Skill templates for the manual Agent tuning loop described in `docs/codex_agent_tuning_prd.md`. It is meant to be copied or registered as a whole pack by the user; keep `skills/`, `templates/`, and `docs/` together because individual Skill directories reference shared pack assets by relative path. It deliberately avoids plugin install UX, marketplace metadata, one-click orchestration, example Agent/data fixtures, auto rollback, universal schemas, and a full E2E test suite.

## Copy/register boundary

Use this MVP as a repository-native template pack. Do not copy a single `skills/*` directory by itself unless you also preserve the referenced shared docs and templates or intentionally inline them. This keeps the first pass lightweight without adding plugin install UX.

## What is included

- `skills/agent-tuning-generate-runner/SKILL.md` — generate `agent-tuning/runner/test_runner.py`.
- `skills/agent-tuning-filter-abnormal-rules/SKILL.md` — generate or update `agent-tuning/runner/filter_abnormal.py` for manual rule-based filtering.
- `skills/agent-tuning-filter-abnormal-llm/SKILL.md` — inspect current `results.csv` and write current `abnormal_cases.csv` using model judgment.
- `skills/agent-tuning-report/SKILL.md` — write current `report.md`, including adjacent-version validation when possible.
- `skills/agent-tuning-apply-tuning/SKILL.md` — tune the target Agent and write current `tuning_plan.md`.
- `templates/agent-tuning/runner/test_runner.py.md` — script template preserving original dataset columns and appending `agent_output`.
- `templates/agent-tuning/runner/filter_abnormal.py.md` — stdlib CSV rule-filter template.
- `docs/shared-versioning-and-confirmation.md` — shared current/new version semantics and confirmation triggers.
- `scripts/validate_skill_pack.py` — lightweight static checker for this template pack.

## Manual 2.2 → 2.6 loop

1. Prepare the local Agent service and evaluation dataset.
2. Trigger `agent-tuning-generate-runner` in Codex. The Skill reads the Agent source and dataset, asks only about unsafe ambiguity, then writes `agent-tuning/runner/test_runner.py`.
3. Manually run `python3 agent-tuning/runner/test_runner.py`. The runner creates or reuses a version directory and writes `agent-tuning/results/vN/results.csv` plus optional `app.log`.
4. Choose one abnormal filtering entry:
   - Trigger `agent-tuning-filter-abnormal-rules`, then manually run `python3 agent-tuning/runner/filter_abnormal.py` to write `abnormal_cases.csv`.
   - Or trigger `agent-tuning-filter-abnormal-llm` to write `abnormal_cases.csv` directly from the current `results.csv`.
5. Trigger `agent-tuning-report` to create `agent-tuning/results/vN/report.md`. From `v2` onward, it compares the current version with the previous existing version and reads the previous `tuning_plan.md` when available.
6. Trigger `agent-tuning-apply-tuning` to change the Agent and write `agent-tuning/results/vN/tuning_plan.md`.
7. Optionally create a user git commit/checkpoint. Rollback remains user-git-only guidance; this pack does not automate restore.
8. Run the same loop again. The next test run creates `v{N+1}` when the current max version already has `results.csv`.

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
find skills templates docs scripts -maxdepth 4 -type f | sort
grep -R "agent_output\|abnormal_cases.csv\|tuning_plan.md\|目标异常清单\|调优手段\|关联改动" skills templates docs README.md
```

`validate_skill_pack.py` checks required files, required sections, PRD traceability, canonical version helper names and snippets, output paths, non-goal boundaries, and each Skill's precondition/failure behavior.
