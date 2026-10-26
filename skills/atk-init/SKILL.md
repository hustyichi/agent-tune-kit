---
name: atk-init
description: Generate a reusable test runner for a local Agent and dataset, preserving source columns and writing versioned eval_results.csv with agent_output.
compatibility: Requires the installed ATK CLI on PATH (dataset, eval, results and visualize commands) and a compatible project Python runtime.
---

# Agent Tuning — Generate Runner

## Purpose

Create `.atk/runner/eval_runner.py` for the target repository. This Skill maps to `docs/codex_agent_tuning_prd.md` sections 2.2, 2.3, 4, 5, and 7. It inspects the local Agent source and evaluation dataset, snapshots a canonical runnable dataset under `.atk/datasets/`, ensures that dataset has a stable `atk_id` column, then generates a stdlib-first runner that reads the stable snapshot and writes `.atk/results/vN/eval_results.csv` incrementally and optional `app.log` without asking the user for a version number.

This is a Codex Skill template. It is copy/register-ready, but it is not a plugin install UX or full automation flow.

Traceability note: section 2.2 defines runner generation, section 4 defines version management, and section 7 defines delivery requirements.

## CLI dependency

Use the installed `atk` CLI from the target project cwd. Check `atk --version` and the required command's `--help` when first used or when compatibility is uncertain. If unavailable, stop and guide installation with `uv tool install agent-tune-kit`, then `atk install`; do not regenerate fixed scripts as a fallback. Installed Skill and CLI must support the same command contract. A successful `atk status` does not prove the current session loaded new instructions.

## Inputs

- Local Agent source files and any project docs needed to infer invocation.
- Evaluation dataset path, typically CSV. Other formats may be supported by adapting the generated script. The source dataset is copied into `.atk/datasets/` as an ATK canonical runnable dataset before the runner is written.
- Optional user instructions about dataset columns, expected outputs, log capture, or Agent runtime.
- Shared rules in `docs/shared-versioning-and-confirmation.md`.
- Script template in plugin-root-relative `templates/.atk/runner/eval_runner.py.md`.

## Shared asset resolution

Template paths are plugin-root-relative, not relative to this Skill directory. Locate the plugin root by walking upward from this `SKILL.md` until finding `.codex-plugin/plugin.json`, `skills/`, and `templates/`.

Then read this file from that plugin root:

- `templates/.atk/runner/eval_runner.py.md`

In the current package layout, this is also reachable from this Skill file as `../../templates/.atk/runner/eval_runner.py.md`, but plugin-root discovery is the preferred resolution rule.

## Outputs

- `.atk/runner/eval_runner.py`
- `.atk/datasets/dataset.csv` containing the ATK canonical runnable dataset used by the generated runner, including a required `atk_id` column.
- No version directory is required before this Skill runs; no version directory is required for generation.
- Runtime results are produced later by running `atk-eval`, which executes the generated script:
  - `.atk/results/vN/eval_results.csv`
  - optional `.atk/results/vN/app.log`
  - V2 independent `executions/<id>/` evidence; per-execution `app.log` referenced by `log_path`

## Workflow

1. Inspect first:
   - locate likely Agent entrypoints, constructors, async/sync call methods, prompt files, tool config, and required environment;
   - inspect the repository's expected Python invocation path (`uv run python`, `.venv/bin/python`, `poetry run python`, or `python3`) and import layout (`src/` layout, package-at-root, or script-only);
   - inspect dataset headers/sample rows and infer input/expected-result fields;
   - inspect logging behavior, Python `logging` logger names, stdout/stderr use, and log file paths;
   - check whether the dataset already has a column named `atk_id`, `agent_output`, or `log_path`.
2. Apply the uncertainty confirmation pattern from `docs/shared-versioning-and-confirmation.md`.
3. Reuse a suitable dataset already prepared by `atk-dataset`: preserve IDs, row order, expected-field names and values.
   Do not repeat ground-truth generation or ask again about fields/criteria already established in the request or context.
   Only unresolved runtime/input mapping questions need clarification. With unchanged inputs, answer-only corrections do not
   require regenerating a compatible runner. Keep answer/criteria columns out of Agent inputs.
   If safe, create `.atk/datasets/` and write the canonical runnable dataset to `.atk/datasets/dataset.csv` before writing the runner:
   - for CSV datasets, preserve all user-provided columns and their order;
   - if the source dataset lacks `atk_id`, append an `atk_id` column and fill it with the source data row number, starting at `1`;
   - if the source dataset already has `atk_id`, reuse it only when values are non-empty, unique positive integers;
   - treat `atk_id` as ATK metadata and do not pass it to the target Agent unless the user explicitly requires it;
   - compute a canonical dataset digest such as `sha256` after `atk_id` normalization;
   - if `.atk/datasets/dataset.csv` does not exist, write the canonical dataset there;
   - if `.atk/datasets/dataset.csv` exists and has identical canonical content, reuse it instead of creating a duplicate;
   - if `.atk/datasets/dataset.csv` exists with different canonical content, ask before overwriting because the fixed name is the canonical runnable dataset slot;
   - keep the runner pointed at `.atk/datasets/dataset.csv`, not the original external path.
4. Use `atk dataset prepare --source <csv>` for canonical CSV persistence, preserving the reuse/overwrite rules above. If safe, create `.atk/runner/` and write the thin `eval_runner.py` adapter from plugin-root-relative `templates/.atk/runner/eval_runner.py.md`.
5. Keep the generated script project-local and low dependency. Prefer Python stdlib plus the target project environment.
6. First run `atk eval --check --json` to verify imports and one-row input shaping without creating results. Verify the generated runner with the lightest check that proves the handoff is usable:
   - syntax check with the same interpreter shape expected for execution;
   - import/load checks for the runner and target Agent entrypoint;
   - dataset load and one-row input-shaping check.
   - if a smoke execution is needed, run a bounded command such as `.atk/runner/eval_runner.py --limit 1`, record the exact result version that existed before and after the command, then clean up only the result directory created or reused solely for that smoke check.
   - after smoke cleanup, confirm `.atk/results/` has returned to the pre-smoke version state. Do not remove any pre-existing version that contained user data before the smoke command.
7. Tell the user the next command is `atk-eval`.

## Required evaluation behavior

- Keep `atk_truth`, answers and hidden tests out of Agent inputs. The managed runner strips atk_truth; verify the
  project whitelist for remaining reference fields and legacy runners. Read plugin-root `docs/truth-and-assessment.md`
  when connecting trust-aware data. Preserve metadata in canonical data/results; init must not silently confirm it.

These are shared-engine guarantees. Generate only project adaptation and configuration; do not copy version allocation, CSV writers, worker pools or logging routing into the adapter.

- Preserve all original dataset columns and their order, with the ATK metadata column `atk_id` appended when the source dataset does not already provide it.
- Require `.atk/datasets/dataset.csv` to contain `atk_id`; generated runners should stop with repair guidance if that column is missing, empty, duplicated, or not a positive integer.
- Append the fixed actual-output column `agent_output`.
- Append the stable row-log evidence column `log_path`. V2 uses `executions/<id>/app.log`; legacy V1 row paths remain readable.
- If Agent output has multiple fields, serialize the complete structured result as JSON in `agent_output`; do not add auxiliary `agent_output_*` columns.
- If the input dataset already contains `agent_output` or `log_path`, ask the user to confirm a rename strategy before writing the script.
- Write the input dataset into `.atk/datasets/` during `atk-init`, specifically `.atk/datasets/dataset.csv`, as the ATK canonical runnable dataset with `atk_id`. Reuse it when canonical content is identical and ask before overwriting different canonical content. The generated runner must read this fixed `.atk/datasets/` dataset so later source dataset moves do not break `atk-eval`.
- Automatically allocate the output version with `allocate_next_results_version()`.
- Use `RESULTS_DIR = Path(".atk/results")`.
- Do not require a user-supplied version argument or result path.
- Support bounded runs with `--limit N` and `--offset N` so users can smoke-test expensive Agents before full execution.
- Treat bounded smoke runs during `atk-init` as temporary validation artifacts that must be removed before handoff when they were created solely by the init smoke check.
- Support concurrent runs with `--concurrency N`, defaulting to conservative serial execution when concurrency is not requested.
- Write `eval_results.csv` incrementally row-by-row, flushing after each row, so interrupted long runs leave inspectable partial evidence.
- Emit visible per-row progress by default, with a `--no-progress` option for quiet runs.
- Do not clean up partial version directories on crash.
- Generate the V2 marker and `EVIDENCE` declaration from the template. Define safe retention for context, input/output, events, result rows and declared snapshots before real execution; do not use a permissive catch-all for unknown material.
- Declare logging sources and optional Loguru explicitly. ATK adds its own handler/sink without changing existing logger levels or exits. Excluded DEBUG records, queues without propagated identity and unmanaged background work remain coverage gaps.
- Use the optional observer at existing public calls. Preserve business retry/stream behavior; propagate ContextVar state per internal thread submission and finish managed background work before returning.
- For external local evidence, generate an explicit trusted importer instead of wrapping an empty Agent call. Inspect `docs/execution-evidence-and-tracing-plan.md` section 6.5; never fetch remote traces or execute commands from native manifests.
- Add the target repository import roots required by the Agent before importing local modules. For Python `src/` layout projects, generated runners should add `REPO_ROOT / "src"` to `sys.path`; for package-at-root projects, add `REPO_ROOT` when needed.
- If the target project declares a managed runtime (`uv`, Poetry, pipenv, `.venv`, etc.), record that execution command in the setup summary and generate a runner that can be executed by that runtime. Do not assume bare system `python3` has project dependencies.
- For sync Agents, generated runners should be concurrency-ready with a standard-library worker pool. For async Agents, use an explicit async runner (`asyncio.run(...)`) with an async semaphore or equivalent bounded concurrency. In both cases, preserve incremental row writes from a single writer path.

## Shared version rules

Use the canonical helper names and semantics from `docs/shared-versioning-and-confirmation.md`:

- `RESULTS_DIR = Path(".atk/results")`
- `DATASETS_DIR = Path(".atk/datasets")`
- `list_version_dirs(results_dir=RESULTS_DIR)`
- `allocate_next_results_version(results_dir=RESULTS_DIR)`

Evaluation execution is the only operation that creates a new version; generated adapters delegate fixed behavior to the shared ATK engine. If the largest `vN` contains `eval_results.csv`, create `v{N+1}`. If it does not contain `eval_results.csv`, reuse it.

## Confirmation triggers

Ask the user before writing `eval_runner.py` if any of these cannot be inferred safely:

- Agent invocation, callable signature, required environment, working directory, or async handling;
- target interpreter/runtime command or import roots needed to load the Agent;
- dataset path, format, encoding, delimiter, input fields, or expected-result fields;
- whether an existing `.atk/datasets/dataset.csv` path should be reused or overwritten when content comparison cannot be completed safely or shows different content;
- log source, Python logger names, or capture method;
- existing dataset column named `atk_id` with invalid/non-unique values, or existing dataset column named `agent_output` or `log_path` and the rename strategy;
- whether writing `.atk/runner/eval_runner.py` would overwrite a hand-edited runner.

Do not ask about routine creation of `.atk/runner/` or version-number selection.

## Failure behavior

- If Agent invocation, dataset path/format, log source, Python logger names, invalid `atk_id`, or `agent_output` / `log_path` column conflict cannot be inferred safely, stop and ask the user to confirm before writing `eval_runner.py`.
- If required source files or dataset are missing, report the missing path and do not create a misleading runner.
- If the dataset cannot be written into `.atk/datasets/` as canonical `dataset.csv` with `atk_id`, or duplicate-content comparison cannot be completed safely, report the reason and do not write a runner that points at the external source dataset.
- If the generated runner cannot import the target Agent under the inferred project runtime, fix the import/runtime inference before handing off.
- If a smoke execution during `atk-init` creates or reuses a result directory that cannot be confidently identified as init-owned temporary output, do not delete it; report the version impact explicitly and explain why cleanup was skipped.
- If an existing runner appears hand-edited, summarize the diff/intent and ask before overwrite.
- Never silently impose a universal Schema on the dataset or Agent.

## Handoff message

After writing the runner, summarize:

- inferred Agent entrypoint, original dataset path, and fixed `.atk/datasets/dataset.csv` canonical dataset path;
- inferred execution command/runtime, including whether bare `python3` is safe or a project runner such as `uv run python` is required;
- preserved source columns, required `atk_id` behavior, and appended `agent_output` behavior;
- appended `log_path` behavior, including whether row logs will be active, downgraded, or unavailable;
- bounded-run flags (`--limit`, `--offset`) and incremental `eval_results.csv` write behavior;
- concurrency flag (`--concurrency`) and whether output row order is serial order or completion order when concurrency is greater than 1;
- whether `app.log` will be captured, including whether it is sourced from Python `logging`, stdout/stderr fallback, or an existing Agent log file, and whether row-specific Python logging files under `logs/` will be captured;
- whether a smoke run was executed, which temporary result version it produced, and whether that version was cleaned up;
- next command to run: `atk-eval`;
- expected next output path `.atk/results/v1/eval_results.csv` when no prior result version exists, otherwise the path selected by the normal runner allocation rule;
- do not say a smoke test has already occupied `v1` when the smoke-created version was cleaned up before handoff.
