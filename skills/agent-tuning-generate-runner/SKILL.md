---
name: agent-tuning-generate-runner
description: Generate a reusable test runner for a local Agent and dataset, preserving source columns and writing versioned results.csv with agent_output.
---

# Agent Tuning — Generate Runner

## Purpose

Create `agent-tuning/runner/test_runner.py` for the target repository. This Skill maps to `docs/codex_agent_tuning_prd.md` sections 2.2, 2.3, 4, 5, and 7. It inspects the local Agent source and evaluation dataset, then generates a stdlib-first runner that writes `agent-tuning/results/vN/results.csv` and optional `app.log` without asking the user for a version number.

This is a Codex Skill template. It is copy/register-ready, but it is not a plugin install UX or full automation flow.

Traceability note: section 2.2 defines runner generation, section 4 defines version management, and section 7 defines delivery requirements.

## Inputs

- Local Agent source files and any project docs needed to infer invocation.
- Evaluation dataset path, typically CSV. Other formats may be supported by adapting the generated script.
- Optional user instructions about dataset columns, expected outputs, log capture, or Agent runtime.
- Shared rules in `docs/shared-versioning-and-confirmation.md`.
- Script template in `templates/agent-tuning/runner/test_runner.py.md`.

## Outputs

- `agent-tuning/runner/test_runner.py`
- No version directory is required before this Skill runs; no version directory is required for generation.
- Runtime results are produced later by manually running the generated script:
  - `agent-tuning/results/vN/results.csv`
  - optional `agent-tuning/results/vN/app.log`

## Workflow

1. Inspect first:
   - locate likely Agent entrypoints, constructors, async/sync call methods, prompt files, tool config, and required environment;
   - inspect dataset headers/sample rows and infer input/expected-result fields;
   - inspect logging behavior, stdout/stderr use, and log file paths;
   - check whether the dataset already has a column named `agent_output`.
2. Apply the uncertainty confirmation pattern from `docs/shared-versioning-and-confirmation.md`.
3. If safe, create `agent-tuning/runner/` and write `test_runner.py` from `templates/agent-tuning/runner/test_runner.py.md`.
4. Keep the generated script project-local and low dependency. Prefer Python stdlib plus the target project environment.
5. Tell the user the next manual command is `python3 agent-tuning/runner/test_runner.py`.

## Required runner behavior

- Preserve all original dataset columns and their order.
- Append the fixed actual-output column `agent_output`.
- If Agent output has multiple fields, serialize the primary result as JSON in `agent_output` or add auxiliary `agent_output_*` columns.
- If the input dataset already contains `agent_output`, ask the user to confirm a rename strategy before writing the script.
- Automatically allocate the output version with `allocate_next_results_version()`.
- Use `RESULTS_DIR = Path("agent-tuning/results")`.
- Do not require a user-supplied version argument or result path.
- Do not clean up partial version directories on crash.
- Capture `app.log` only when a reliable source is found; otherwise omit it and explain why.

## Shared version rules

Use the canonical helper names and semantics from `docs/shared-versioning-and-confirmation.md`:

- `RESULTS_DIR = Path("agent-tuning/results")`
- `list_version_dirs(results_dir=RESULTS_DIR)`
- `allocate_next_results_version(results_dir=RESULTS_DIR)`

The runner is the only module that creates a new version. If the largest `vN` contains `results.csv`, create `v{N+1}`. If it does not contain `results.csv`, reuse it.

## Confirmation triggers

Ask the user before writing `test_runner.py` if any of these cannot be inferred safely:

- Agent invocation, callable signature, required environment, working directory, or async handling;
- dataset path, format, encoding, delimiter, input fields, or expected-result fields;
- log source or capture method;
- existing dataset column named `agent_output` and the rename strategy;
- whether writing `agent-tuning/runner/test_runner.py` would overwrite a hand-edited runner.

Do not ask about routine creation of `agent-tuning/runner/` or version-number selection.

## Failure behavior

- If Agent invocation, dataset path/format, log source, or `agent_output` column conflict cannot be inferred safely, stop and ask the user to confirm before writing `test_runner.py`.
- If required source files or dataset are missing, report the missing path and do not create a misleading runner.
- If an existing runner appears hand-edited, summarize the diff/intent and ask before overwrite.
- Never silently impose a universal Schema on the dataset or Agent.

## Handoff message

After writing the runner, summarize:

- inferred Agent entrypoint and dataset path;
- preserved source columns and appended `agent_output` behavior;
- whether `app.log` will be captured;
- manual command to run;
- expected next output path `agent-tuning/results/vN/results.csv`.
