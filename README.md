# Agent tune kit

English | [简体中文](README.zh-CN.md)

Codex Skill template pack for iterative local Agent tuning.

## MVP scope

This repository currently delivers a repository-native Codex Skill template pack, not a production plugin installer. The pack is copy/register-ready as a whole repository-native Skill pack and includes complete Skill templates, reusable runner script templates, shared versioning rules, uncertainty-confirmation patterns, usage docs, and lightweight static checks.

Explicit MVP non-goals: no plugin install UX, no marketplace/manifest packaging, no one-click orchestration, no universal Schema requirement, no bundled example Agent/data fixtures, no automatic rollback or baseline restore, and no full E2E test suite.

## Who this is for

Use this pack when you have a local Agent implementation and an evaluation dataset, and you want Codex to help you run a manual, repeatable tuning loop:

1. generate a project-local test runner;
2. run the Agent against the dataset;
3. identify abnormal cases;
4. generate a report with root-cause analysis and adjacent-version validation;
5. apply focused Agent tuning;
6. repeat with versioned results under `agent-tuning/results/vN/`.

## Prerequisites

- Codex with Skill support, or a repository-local workflow that can load these `skills/*/SKILL.md` files.
- Python 3 for generated runner/filter scripts and static validation.
- A target local Agent project that Codex can inspect and edit.
- An evaluation dataset, preferably CSV. Other formats can be supported by adapting the generated runner.
- A normal user-managed git workflow for checkpoints and rollback. This pack does not automate rollback.

## Quickstart

1. Read the full usage guide: `docs/skill-template-pack-usage.md`.
2. Copy/register the whole pack or keep `skills/`, `templates/`, and `docs/` together; individual Skill directories reference shared pack assets by relative path.
3. Validate the pack before use:

   ```sh
   python3 scripts/validate_skill_pack.py
   git diff --check
   ```

4. In the target Agent project, trigger `agent-tuning-generate-runner` in Codex. Provide or point Codex to the Agent source and evaluation dataset. The Skill writes `agent-tuning/runner/test_runner.py`.
5. Manually run the generated runner:

   ```sh
   python3 agent-tuning/runner/test_runner.py
   ```

   The runner writes `agent-tuning/results/v1/results.csv` on the first run, with all original dataset columns plus the required `agent_output` column. It may also write `app.log` when reliable log capture is available.

6. Create `abnormal_cases.csv` for the current version using one abnormal filtering Skill:
   - `agent-tuning-filter-abnormal-rules`: Codex generates or updates `agent-tuning/runner/filter_abnormal.py`; then you manually run `python3 agent-tuning/runner/filter_abnormal.py`.
   - `agent-tuning-filter-abnormal-llm`: Codex reads the current `results.csv` and writes `abnormal_cases.csv` directly.
7. Trigger `agent-tuning-report` to write `agent-tuning/results/vN/report.md` with statistics, abnormal-case analysis, root-cause hypotheses, and adjacent-version tuning validation when a previous `tuning_plan.md` exists.
8. Trigger `agent-tuning-apply-tuning` to tune the Agent and write `agent-tuning/results/vN/tuning_plan.md` with the required headings `## 目标异常清单`, `## 调优手段`, and `## 关联改动`.
9. Repeat the loop. `test_runner.py` creates the next `vN` when the current version already has `results.csv`; report generation from `v2` onward validates whether the previous tuning goals were resolved.

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
- `docs/codex_agent_tuning_prd.md`
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

## Validate the pack

```sh
python3 scripts/validate_skill_pack.py
git diff --check
```

The validator fails loudly when required Skill sections, PRD references, version helper snippets, output paths, non-goals, or tuning/report contracts are missing.
