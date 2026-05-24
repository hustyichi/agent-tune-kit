---
name: atk-run
description: Run the generated Agent tuning test runner and summarize the current version results.
---

# Agent Tuning — Run Tests

## Purpose

Use this Skill when a user wants the short ATK command for running the generated Agent tuning test runner. It maps to `docs/codex_agent_tuning_prd.md` sections 2.3, 4, 5, and 7.

This Skill executes the project-local `agent-tuning/runner/test_runner.py` after `atk-setup` has generated it. It keeps `test_runner.py` as the only component that creates or reuses result versions.

Traceability note: section 2.3 defines manual batch execution, section 4 defines version management, and section 7 defines delivery requirements.

## Inputs

- Target Agent repository path or current working directory.
- Required runner file: `agent-tuning/runner/test_runner.py`.
- Shared rules in `docs/shared-versioning-and-confirmation.md`.

## Outputs

- Runtime results created by `test_runner.py`:
  - `agent-tuning/results/vN/results.csv`
  - optional `agent-tuning/results/vN/app.log`
- A concise run summary with the version directory and next recommended Skill.

## Workflow

1. Confirm `agent-tuning/runner/test_runner.py` exists in the current target repository.
2. Execute:

   ```sh
   python3 agent-tuning/runner/test_runner.py
   ```

3. Read the runner output and inspect `agent-tuning/results/` to identify the numerically largest `vN` directory.
4. Confirm whether `results.csv` exists in that version.
5. Summarize the output path and recommend `atk-filter` as the usual next step.

## Shared version rules

Use the canonical helper names and semantics from `docs/shared-versioning-and-confirmation.md`:

- `RESULTS_DIR = Path("agent-tuning/results")`
- `list_version_dirs(results_dir=RESULTS_DIR)`
- `allocate_next_results_version(results_dir=RESULTS_DIR)`

Only `test_runner.py` creates or reuses result versions. This Skill is a short command surface for executing that runner; it does not choose a version itself.

## Confirmation triggers

Ask before running only when:

- `agent-tuning/runner/test_runner.py` is missing and `atk-setup` has not been run;
- the current directory does not appear to be the intended target Agent repository;
- the runner appears hand-edited in a way that may execute external production systems, destructive writes, or credential-gated actions;
- the user explicitly asked for a dry run or inspection only.

Do not ask for a version number.

## Failure behavior

- If the runner is missing, stop and tell the user to run `atk-setup` first.
- If the runner exits non-zero, report the failure output and do not claim a result version was produced unless `results.csv` exists.
- If `results.csv` is missing from the current version after execution, tell the user the run did not complete and point to the runner output for repair.
- Do not clean up partial version directories after failure.

## Handoff message

After a successful run, summarize:

- command executed;
- current version directory;
- output path `agent-tuning/results/vN/results.csv`;
- whether optional `app.log` was produced;
- next recommended Skill: `atk-filter`.
