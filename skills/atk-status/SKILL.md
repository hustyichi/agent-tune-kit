---
name: atk-status
description: Inspect Agent tune kit state and guide the next safe step without bypassing existing stage Skills or confirmation gates.
---

# Agent Tuning Status

## Purpose

Use this Skill when a user wants to inspect or resume an Agent tune kit workflow from the local Codex plugin. It is a router/status guide for the existing stage Skills, not a hidden orchestrator. It inspects the target project's `.atk/` state, explains the next recommended action, and points the user to the correct Skill or manual command.

This Skill preserves the existing stage Skill contracts:

- `atk-setup`
- `atk-run`
- `atk-filter-rules`
- `atk-filter`
- `atk-report`
- `atk-tune`

It does not bypass existing confirmation triggers, does not perform full automatic tuning, and does not run the 2.2 → 2.6 loop end-to-end.

## Inputs

- Target Agent repository path or current working directory.
- Optional evaluation dataset path or user description of the dataset.
- Optional user preference for abnormal filtering mode: rules or LLM judgment.
- Existing `.atk/` directory state, if any.

## Outputs

- A concise status summary of detected `.atk/runner/` and `.atk/results/vN/` artifacts.
- A next-step recommendation naming one of the stage Skills or a manual command.
- Any confirmation question needed before the recommended next step is safe.

## Workflow

1. Inspect the current repository before asking questions:
   - Does `.atk/runner/test_runner.py` exist?
   - Does `.atk/runner/filter_abnormal.py` exist?
   - Does `.atk/results/` contain `vN` directories?
   - For the numerically largest current version, are `results.csv`, `abnormal_cases.csv`, `report.md`, and `tuning_plan.md` present?
2. Apply the shared current-version semantics from `docs/shared-versioning-and-confirmation.md`:
   - `RESULTS_DIR = Path(".atk/results")`
   - non-runner Skills use the numerically largest existing `vN` as current;
   - only `test_runner.py` creates or reuses result versions.
3. Recommend the next step:
   - no runner: trigger `atk-setup`;
   - runner exists but no current `results.csv`: trigger `atk-run`;
   - current `results.csv` exists but no `abnormal_cases.csv`: choose `atk-filter-rules` or `atk-filter`;
   - current `abnormal_cases.csv` exists but no `report.md`: trigger `atk-report`;
   - current `report.md` exists but no `tuning_plan.md`: trigger `atk-tune`;
   - current `tuning_plan.md` exists: optionally create a user git checkpoint, then trigger `atk-run` to create the next version.
4. Keep guidance over hidden automation: this Skill may summarize commands and Skill names, but it does not run generated runner/filter scripts unless the user explicitly asks in the target workflow.

## Shared version rules

Read `docs/shared-versioning-and-confirmation.md` as the single source of truth. In short:

```python
RESULTS_DIR = Path(".atk/results")
```

- Current version means the numerically largest existing `.atk/results/vN` directory.
- Do not filter current-version selection by required files.
- Missing current-version inputs are blockers for the corresponding stage; never fall back to an older version.
- New version creation is owned only by `.atk/runner/test_runner.py`.

## Confirmation triggers

Ask a concise question only when inspection cannot safely decide:

- whether the user wants rule-based or LLM-based abnormal filtering;
- whether an existing partial `.atk/` directory belongs to this workflow;
- whether to treat a missing current-version file as a rerun/repair task or move to a different target project;
- whether a generated script should be run now when that would execute the user's Agent or overwrite current outputs.

## Failure behavior

- If no Agent project or dataset can be identified, stop with a recommendation to provide the target Agent path and dataset path, then use `atk-setup`.
- If `.atk/results/` has malformed version directories, ignore non-`vN` names and report the evidence.
- If the current version is missing a required file, direct the user to the prior stage instead of silently using an older version.
- If executing a command would alter user data or invoke the Agent, do not do it from this router Skill unless the user explicitly requested that execution.
