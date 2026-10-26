---
name: atk-eval
description: Run the generated Agent tuning test runner and summarize the current version results.
compatibility: Requires the installed ATK CLI on PATH (dataset, eval, results and visualize commands) and a compatible project Python runtime.
---

# Agent Tuning — Evaluate Agent

## Purpose

Use this Skill when a user wants the short ATK command for running the generated Agent tuning test runner. It maps to `docs/codex_agent_tuning_prd.md` sections 2.3, 4, 5, and 7.

This Skill executes `atk eval` using the project-local `.atk/runner/eval_runner.py` adapter after `atk-init` has generated it. Evaluation and explicitly requested local evidence imports allocate protected versions. It should use the target repository's Python runtime, not blindly assume system `python3` has the project's dependencies.

Traceability note: section 2.3 defines manual batch execution, section 4 defines version management, and section 7 defines delivery requirements.

## CLI dependency

Use the installed `atk` CLI from the target project cwd. Check `atk --version` and the required command's `--help` when first used or when compatibility is uncertain. If unavailable, stop and guide installation with `uv tool install agent-tune-kit`, then `atk install`; do not regenerate fixed scripts as a fallback. Installed Skill and CLI must support the same command contract. A successful `atk status` does not prove the current session loaded new instructions.

## Inputs

- Target Agent repository path or current working directory.
- Required runner file: `.atk/runner/eval_runner.py`.
- Shared rules in `docs/shared-versioning-and-confirmation.md`.

## Outputs

- Runtime results created by `eval_runner.py`:
  - `.atk/results/vN/eval_results.csv`
  - optional `.atk/results/vN/app.log`
  - V2 `run.json` and `executions/<id>/` evidence, linked from `log_path`; legacy row logs remain supported
- A concise run summary with the version directory and next recommended Skill.

## Workflow

1. Confirm `.atk/runner/eval_runner.py` exists in the current target repository.
2. The CLI discovers the project runtime using these priorities; inspect it when needed and pass `--python <interpreter>` for an explicit override:
   - if `uv.lock` or `pyproject.toml` with uv-managed usage is present, prefer `uv run python`;
   - else if `.venv/bin/python` exists, prefer `.venv/bin/python`;
   - else if Poetry metadata and lockfiles are present, prefer `poetry run python`;
   - otherwise use `python3`.
3. Execute the runner with the selected runtime:

   ```sh
   atk eval
   ```

   If the user supplies extra runner flags such as `--limit 5`, `--offset 10`, or `--concurrency 4`, pass them through after the script path:

   ```sh
   atk eval --limit 5 --concurrency 4
   ```

   If the user asks to rerun only the previous failure set, pass `--only-failures` through. The runner uses valid failed `atk_id` values from the current evaluation target to select rows from `.atk/datasets/dataset.csv`; it does not run `failure_cases.csv` as an input dataset:

   ```sh
   atk eval --only-failures
   ```

4. Bind the version returned by the command; inspect `latest_run` and `evaluation_target` separately with `atk results inspect --json`.
5. Confirm whether `eval_results.csv` exists in that version.
6. Inspect row-log status:
   - if `eval_results.csv` contains non-empty `log_path` values and referenced files exist under `.atk/results/vN/logs/`, report row logs as active;
   - if runner output contains the row-log downgrade message, report row logs as downgraded and explain that no per-row files are expected;
   - otherwise report row logs as unavailable and mention `app.log` fallback when present.
7. Summarize the output path and recommend `atk-diagnose` as the usual next step.

## Local evidence import

When the user asks to import an already available local run, use
`atk results import-evidence --source <directory> --adapter <trusted-project-file> --json`.
The adapter executes trusted Python, not sandboxed data; never load code named by the evidence itself.
This path does not run the Agent, fetch traces or run business checks. Evidence-only output is valid without CSV;
query by execution ID. Identical imports reuse a verified sealed package; changed material and post-seal additions
are rejected. Report source limitations separately from import success. Do not clean up partial scenes or unknown occupation.

## Shared version rules

Use the canonical helper names and semantics from `docs/shared-versioning-and-confirmation.md`:

- `RESULTS_DIR = Path(".atk/results")`
- `list_version_dirs(results_dir=RESULTS_DIR)`
- `allocate_next_results_version(results_dir=RESULTS_DIR)`

Evaluation and explicitly requested local evidence imports create protected result versions. This Skill is a short command surface for executing that runner; it does not choose a version itself.

## Confirmation triggers

Ask before running only when:

- `.atk/runner/eval_runner.py` is missing and `atk-init` has not been run;
- the current directory does not appear to be the intended target Agent repository;
- the runner appears hand-edited in a way that may execute external production systems, destructive writes, or credential-gated actions;
- the user explicitly asked for a dry run or inspection only.

Do not ask for a version number.
Do not ask before passing through safe runner controls such as `--limit`, `--offset`, `--concurrency`, `--only-failures`, or `--no-progress` when the user requested them.

## Failure behavior

- If the runner is missing, stop and tell the user to run `atk-init` first.
- If the runner fails with `ModuleNotFoundError` or dependency import errors under bare `python3`, retry once with the best project runtime discovered from the repository (`uv run python`, `.venv/bin/python`, or `poetry run python`) before reporting failure.
- If the runner still cannot import the target Agent, report this as an `atk-init` generation/runtime inference problem and recommend regenerating the runner after updating setup rules.
- If the runner exits non-zero, report the failure output and report any available Run/execution scene even when CSV was not produced; do not claim execution completed.
- If `eval_results.csv` is missing from the current version after execution, tell the user the run did not complete and point to the runner output for repair.
- If `--only-failures` cannot find a prior `failure_cases.csv`, the file lacks `atk_id`, or any failure `atk_id` is absent from `.atk/datasets/dataset.csv`, report the runner error and do not silently fall back to a full run.
- If row-level logging was downgraded under `--concurrency > 1` because concurrent row logging is disabled, do not treat missing `logs/row_*.log` files as a failure; report the downgrade and suggest serial execution or enabling the generated concurrent row-log flag when trustworthy same-process Python logging evidence is needed.
- If `log_path` contains non-empty paths but referenced files are missing, report this as a runner generation bug and recommend regenerating or repairing `.atk/runner/eval_runner.py`.
- If the user interrupts the run, do not clean up the partial version. Inspect whether the current version contains a partial `eval_results.csv` and report the number of rows written if it can be read.
- Do not clean up partial version directories after failure.

## Handoff message

After a successful run, summarize:

- command executed;
- any runner flags used, especially `--limit`, `--offset`, `--concurrency`, or `--only-failures`;
- current version directory;
- output path `.atk/results/vN/eval_results.csv`;
- whether optional `app.log` was produced;
- row-log status: active, downgraded, or unavailable; when active, mention that `log_path` contains relative POSIX paths such as `logs/row_000001.log`;
- next recommended Skill: `atk-diagnose`.
