---
name: agent-tuning-start
description: Guide the next safe step in the Agent tune kit loop without bypassing the existing stage Skills or their confirmation gates.
---

# Agent Tuning Start

## Purpose

Use this Skill when a user wants to start or resume an Agent tune kit workflow from the local Codex plugin. It is a router/status guide for the existing stage Skills, not a hidden orchestrator. It inspects the target project's `agent-tuning/` state, explains the next recommended action, and points the user to the correct Skill or manual command.

This Skill preserves the existing five Skill contracts:

- `agent-tuning-generate-runner`
- `agent-tuning-filter-abnormal-rules`
- `agent-tuning-filter-abnormal-llm`
- `agent-tuning-report`
- `agent-tuning-apply-tuning`

It does not bypass existing confirmation triggers, does not perform full automatic tuning, and does not run the 2.2 → 2.6 loop end-to-end.

## Inputs

- Target Agent repository path or current working directory.
- Optional evaluation dataset path or user description of the dataset.
- Optional user preference for abnormal filtering mode: rules or LLM judgment.
- Existing `agent-tuning/` directory state, if any.

## Outputs

- A concise status summary of detected `agent-tuning/runner/` and `agent-tuning/results/vN/` artifacts.
- A next-step recommendation naming one of the stage Skills or a manual command.
- Any confirmation question needed before the recommended next step is safe.

## Workflow

1. Inspect the current repository before asking questions:
   - Does `agent-tuning/runner/test_runner.py` exist?
   - Does `agent-tuning/runner/filter_abnormal.py` exist?
   - Does `agent-tuning/results/` contain `vN` directories?
   - For the numerically largest current version, are `results.csv`, `abnormal_cases.csv`, `report.md`, and `tuning_plan.md` present?
2. Apply the shared current-version semantics from `docs/shared-versioning-and-confirmation.md`:
   - `RESULTS_DIR = Path("agent-tuning/results")`
   - non-runner Skills use the numerically largest existing `vN` as current;
   - only `test_runner.py` creates or reuses result versions.
3. Recommend the next step:
   - no runner: trigger `agent-tuning-generate-runner`;
   - runner exists but no current `results.csv`: manually run `python3 agent-tuning/runner/test_runner.py`;
   - current `results.csv` exists but no `abnormal_cases.csv`: choose `agent-tuning-filter-abnormal-rules` or `agent-tuning-filter-abnormal-llm`;
   - current `abnormal_cases.csv` exists but no `report.md`: trigger `agent-tuning-report`;
   - current `report.md` exists but no `tuning_plan.md`: trigger `agent-tuning-apply-tuning`;
   - current `tuning_plan.md` exists: optionally create a user git checkpoint, then manually run `python3 agent-tuning/runner/test_runner.py` to create the next version.
4. Keep guidance over hidden automation: this Skill may summarize commands and Skill names, but it does not run generated runner/filter scripts unless the user explicitly asks in the target workflow.

## Shared version rules

Read `docs/shared-versioning-and-confirmation.md` as the single source of truth. In short:

```python
RESULTS_DIR = Path("agent-tuning/results")
```

- Current version means the numerically largest existing `agent-tuning/results/vN` directory.
- Do not filter current-version selection by required files.
- Missing current-version inputs are blockers for the corresponding stage; never fall back to an older version.
- New version creation is owned only by `agent-tuning/runner/test_runner.py`.

## Confirmation triggers

Ask a concise question only when inspection cannot safely decide:

- whether the user wants rule-based or LLM-based abnormal filtering;
- whether an existing partial `agent-tuning/` directory belongs to this workflow;
- whether to treat a missing current-version file as a rerun/repair task or move to a different target project;
- whether a generated script should be run now when that would execute the user's Agent or overwrite current outputs.

## Failure behavior

- If no Agent project or dataset can be identified, stop with a recommendation to provide the target Agent path and dataset path, then use `agent-tuning-generate-runner`.
- If `agent-tuning/results/` has malformed version directories, ignore non-`vN` names and report the evidence.
- If the current version is missing a required file, direct the user to the prior stage instead of silently using an older version.
- If executing a command would alter user data or invoke the Agent, do not do it from this router Skill unless the user explicitly requested that execution.
