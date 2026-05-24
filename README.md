# Agent tune kit

Codex Skill template pack for iterative local Agent tuning.

## MVP scope

This repository currently delivers a repository-native Codex Skill template pack, not a production plugin installer. The pack is copy/register-ready as a whole repository-native Skill pack and includes complete Skill templates, reusable runner script templates, shared versioning rules, uncertainty-confirmation patterns, usage docs, and lightweight static checks.

Explicit MVP non-goals: no plugin install UX, no marketplace/manifest packaging, no one-click orchestration, no universal Schema requirement, no bundled example Agent/data fixtures, no automatic rollback or baseline restore, and no full E2E test suite.

## Quickstart

1. Read `docs/skill-template-pack-usage.md`.
2. Copy/register the whole pack or keep `skills/`, `templates/`, and `docs/` together; individual Skill directories reference shared pack assets by relative path.
3. Start with `agent-tuning-generate-runner` to create `agent-tuning/runner/test_runner.py` for your local Agent and dataset.
4. Manually run the generated runner to create `agent-tuning/results/v1/results.csv` with a required `agent_output` column.
5. Use either abnormal filtering Skill to write `abnormal_cases.csv` in the current version.
6. Use `agent-tuning-report` to write `report.md` and `agent-tuning-apply-tuning` to tune the Agent and write `tuning_plan.md`.
7. Repeat the loop; `test_runner.py` creates the next `vN` when the current version already has `results.csv`.

## Included files

- `skills/agent-tuning-generate-runner/SKILL.md`
- `skills/agent-tuning-filter-abnormal-rules/SKILL.md`
- `skills/agent-tuning-filter-abnormal-llm/SKILL.md`
- `skills/agent-tuning-report/SKILL.md`
- `skills/agent-tuning-apply-tuning/SKILL.md`
- `templates/agent-tuning/runner/test_runner.py.md`
- `templates/agent-tuning/runner/filter_abnormal.py.md`
- `docs/shared-versioning-and-confirmation.md`
- `docs/skill-template-pack-usage.md`
- `scripts/validate_skill_pack.py`

## Validate the pack

```sh
python3 scripts/validate_skill_pack.py
git diff --check
```

The validator fails loudly when required Skill sections, PRD references, version helper snippets, output paths, non-goals, or tuning/report contracts are missing.
