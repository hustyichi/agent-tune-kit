---
name: atk-apply
description: Apply Agent improvements from the current report and write a fixed-heading tuning_plan.md for the next iteration's validation.
---

# Agent Tuning — Apply Tuning

## Purpose

Use current `report.md` to improve the local Agent, then write `.atk/results/vN/tuning_plan.md` for the next iteration. This Skill maps to `docs/codex_agent_tuning_prd.md` sections 2.6, 4, 5, and 7.

The Skill may adjust prompts, code, parameters, tool config, or other Agent implementation details as appropriate. It does not implement automatic rollback, baseline management, or historical restore; rollback is user-git-only guidance.

Traceability note: section 2.6 defines Agent tuning, section 4 defines current-version behavior, and section 7 defines delivery requirements.

## Inputs

- Current version directory resolved from `.atk/results/vN`.
- Required current file: `report.md`.
- Target Agent source files and configuration.
- Optional existing test results, abnormal cases, logs, and previous reports for context.
- Shared rules in `docs/shared-versioning-and-confirmation.md`.

## Outputs

- Modified Agent source/config files as needed.
- Current `.atk/results/vN/tuning_plan.md`.

## Workflow

1. Resolve current version with `resolve_current_version()` using `RESULTS_DIR = Path(".atk/results")`.
2. Require `report.md` with `require_current_file(current_dir, "report.md")`.
3. Read the report and prioritize unresolved problems,新增问题, and high-confidence root causes.
4. Inspect relevant Agent implementation files before editing.
5. Make focused, reviewable changes. Do not ask for item-by-item confirmation unless the action is destructive, credential-gated, or materially scope-changing.
6. Run the smallest relevant validation available in the target repo.
7. Write `tuning_plan.md` in the current version directory with exact headings:
   - `## 目标异常清单`
   - `## 调优手段`
   - `## 关联改动`
8. Suggest a user git commit/checkpoint in the final handoff; do not perform automatic rollback or baseline restore.

## Required tuning_plan.md structure

```markdown
# Tuning Plan - <current version>

## 目标异常清单
- <problem summary + triggering input feature / expected result / actual output; include natural ID if present>

## 调优手段
- <prompt/code/parameter/tool-config change and rationale>

## 关联改动
- <file path and optional user-provided git commit hash>
```

The section titles are exact contract strings for the next `atk-report` run. Content may remain free-form Markdown; do not force a universal Schema.

## Shared version rules

Use the canonical helper names and semantics from `docs/shared-versioning-and-confirmation.md`:

- `RESULTS_DIR = Path(".atk/results")`
- `resolve_current_version(results_dir=RESULTS_DIR)`
- `require_current_file(current_dir, filename)`

The next test run creates a new version only when `test_runner.py` sees current `results.csv`. This Skill writes into the current version and never asks the user for a version argument.

## Confirmation triggers

Ask only when:

- `report.md` points to destructive, credential-gated, or external-production changes;
- multiple incompatible tuning directions have similar evidence and choosing one would materially change scope;
- a file targeted for edit appears unrelated to the Agent or contains user-protected regions;
- overwriting `tuning_plan.md` would discard user-authored content.

## Failure behavior

- Require current `report.md`; if missing, stop and tell the user to run report generation first.
- If the report has insufficient evidence, make no code changes; write or propose a conservative `tuning_plan.md` only when it truthfully records the uncertainty.
- If validation cannot run, state why and provide the next-best static check.
- Only suggest user git commits/checkpoints; never perform automatic rollback, baseline restore, or hidden historical recovery.

## Handoff message

After applying tuning, summarize:

- current version;
- files changed;
- validation run and result;
- output path `.atk/results/vN/tuning_plan.md`;
- suggested next step: run `atk-run` for the next version and optionally create a git commit.
