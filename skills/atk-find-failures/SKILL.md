---
name: atk-find-failures
description: Use Codex judgment to find failing Agent tuning cases and write abnormal_cases.csv without generating a rule script.
---

# Agent Tuning — Find Failures

## Purpose

Read the current version's `results.csv`, infer or apply the failure criteria, and write failing or abnormal rows to `abnormal_cases.csv` directly. This Skill maps to `docs/codex_agent_tuning_prd.md` sections 2.4, 4, 5, and 7.

This is separate from `atk-find-failures-by-rule`; both write the same current-version filename `abnormal_cases.csv` and either mode may overwrite the other.

Traceability note: section 2.4 defines abnormal-case discovery entries, section 4 defines current-version behavior, and section 7 defines delivery requirements.

## Inputs

- Current version directory resolved from `.atk/results/vN`.
- Required current file: `results.csv`.
- Optional user natural-language abnormal definition.
- Dataset columns preserved in `results.csv`, including required `agent_output`.
- Shared rules in `docs/shared-versioning-and-confirmation.md`.

## Outputs

- Current `.atk/results/vN/abnormal_cases.csv`.
- No `filter_abnormal.py` is required for this mode.

## Workflow

1. Resolve current version with `resolve_current_version()` using `RESULTS_DIR = Path(".atk/results")`.
2. Require `results.csv` with `require_current_file(current_dir, "results.csv")`.
3. Inspect headers and samples to identify original input fields, expected-result fields, and `agent_output`.
4. If the user supplied failure criteria, apply them. Otherwise infer likely failing or abnormal cases from Agent output versus expected results.
5. If criteria or expected-result columns are ambiguous, ask the user for judgment before writing.
6. State that `abnormal_cases.csv` in the current version will be overwritten.
7. Write abnormal rows, preserving all original `results.csv` columns and adding optional explanatory columns only when useful.

## Required failure-finding behavior

- Use the current version directory, not a user-supplied version argument.
- Read current `results.csv` and write current `abnormal_cases.csv`.
- Preserve all source result columns including `agent_output`.
- Overwrite `abnormal_cases.csv`; do not backup or merge. Overwrites are stated before writing.
- Do not require a universal Schema for expected-result columns.

## Shared version rules

Use the canonical helper names and semantics from `docs/shared-versioning-and-confirmation.md`:

- `RESULTS_DIR = Path(".atk/results")`
- `resolve_current_version(results_dir=RESULTS_DIR)`
- `require_current_file(current_dir, filename)`

The current version is the numerically largest `vN` directory even if it is missing downstream files.

## Confirmation triggers

Ask before writing when:

- `results.csv` does not clearly identify expected-result columns;
- failure criteria cannot be inferred from `agent_output` and expected fields;
- multiple interpretations would materially change which rows are abnormal;
- current `abnormal_cases.csv` exists and may contain user-edited content.

## Failure behavior

- Require current `vN/results.csv`; if no current version or missing `results.csv`, stop with repair/rerun guidance.
- If expected-result columns or failure criteria are ambiguous, ask for judgment.
- Overwrite current `abnormal_cases.csv` only after stating the overwrite behavior.
- If dataset volume is too large for safe direct model inspection, propose a bounded sampling/partition plan and ask only if the partitioning could change the result semantics.

## Handoff message

After writing the file, summarize:

- current version;
- criteria used;
- count of abnormal rows written;
- output path `.atk/results/vN/abnormal_cases.csv`;
- any uncertainty or confidence boundary.
