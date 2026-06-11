---
name: atk-find-failures
description: Use Codex judgment to find failing Agent tuning cases and write failure_cases.csv without generating a rule script.
---

# Agent Tuning — Find Failures

## Purpose

Read the current version's `eval_results.csv`, infer or apply the failure criteria, and write failing rows to `failure_cases.csv` directly. This Skill maps to `docs/codex_agent_tuning_prd.md` sections 2.4, 4, 5, and 7.

This is separate from `atk-find-failures-by-rule`; both write the same current-version filename `failure_cases.csv` and either mode may overwrite the other.

Traceability note: section 2.4 defines failure-case discovery entries, section 4 defines current-version behavior, and section 7 defines delivery requirements.

## Inputs

- Current version directory resolved from `.atk/results/vN`.
- Required current file: `eval_results.csv`.
- Optional user natural-language failure definition.
- Optional local private tuning context: `.atk/context.md`, especially `Tuning Objective`, `Agent Behavior Standard`,
  and `Ground Truth Standard`.
- Dataset columns preserved in `eval_results.csv`, including required `agent_output`.
- Shared rules in `docs/shared-versioning-and-confirmation.md`.

## Outputs

- Current `.atk/results/vN/failure_cases.csv`.
- No `failure_rule.py` is required for this mode.

## Workflow

1. Resolve current version with `resolve_current_version()` using `RESULTS_DIR = Path(".atk/results")`.
2. Require `eval_results.csv` with `require_current_file(current_dir, "eval_results.csv")`.
3. Read `.atk/context.md` if it exists. Apply only durable standards and decisions: `Tuning Objective`,
   `Agent Behavior Standard`, `Ground Truth Standard`, and relevant `Tuning Decisions`.
4. Inspect headers and samples to identify original input fields, expected-result fields, and `agent_output`.
5. If the user supplied failure criteria, apply them. Otherwise infer likely failing cases from Agent output versus expected results, using local context standards as tie-breakers when they are relevant.
6. If criteria, expected-result columns, or local context standards are ambiguous, ask the user for judgment before writing.
7. State that `failure_cases.csv` in the current version will be overwritten.
8. Write failure rows, preserving all original `eval_results.csv` columns and adding optional explanatory columns only when useful.

## Required failure-finding behavior

- Use the current version directory, not a user-supplied version argument.
- Read current `eval_results.csv` and write current `failure_cases.csv`.
- Preserve all source result columns including `agent_output`.
- Overwrite `failure_cases.csv`; do not backup or merge. Overwrites are stated before writing.
- Do not require a universal Schema for expected-result columns.
- Do not write `.atk/context.md`; this Skill consumes local standards but failure rows belong in `failure_cases.csv`.

## Shared version rules

Use the canonical helper names and semantics from `docs/shared-versioning-and-confirmation.md`:

- `RESULTS_DIR = Path(".atk/results")`
- `resolve_current_version(results_dir=RESULTS_DIR)`
- `require_current_file(current_dir, filename)`

The current version is the numerically largest `vN` directory even if it is missing downstream files.

## Confirmation triggers

Ask before writing when:

- `eval_results.csv` does not clearly identify expected-result columns;
- failure criteria cannot be inferred from `agent_output` and expected fields;
- `.atk/context.md` standards conflict with the current user request or result evidence;
- multiple interpretations would materially change which rows are failures;
- current `failure_cases.csv` exists and may contain user-edited content.

## Failure behavior

- Require current `vN/eval_results.csv`; if no current version or missing `eval_results.csv`, stop with repair/rerun guidance.
- If expected-result columns or failure criteria are ambiguous, ask for judgment.
- Overwrite current `failure_cases.csv` only after stating the overwrite behavior.
- If dataset volume is too large for safe direct model inspection, propose a bounded sampling/partition plan and ask only if the partitioning could change the result semantics.

## Handoff message

After writing the file, summarize:

- current version;
- criteria used;
- local context standards applied, if `.atk/context.md` existed;
- count of failure rows written;
- output path `.atk/results/vN/failure_cases.csv`;
- any uncertainty or confidence boundary;
- next command: `atk-report` to generate `.atk/results/vN/report.md`.
