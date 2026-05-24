---
name: atk-init-failure-rule
description: Generate or update the reusable rule script that classifies Agent tuning eval rows as failures.
---

# Agent Tuning — Init Failure Rule

## Purpose

Create or update `.atk/runner/failure_rule.py` so a reusable, project-local rule can classify rows from the current version's `eval_results.csv` as failure cases. This Skill maps to `docs/codex_agent_tuning_prd.md` sections 2.4, 4, 5, and 7.

This Skill only prepares the rule script. It does not execute `.atk/runner/failure_rule.py` and does not write `.atk/results/vN/failure_cases.csv`; use `atk-find-failures-by-rule` for execution.

Traceability note: section 2.4 defines rule-based failure-case discovery, section 4 defines current-version behavior, and section 7 defines delivery requirements.

## Inputs

- Current version directory resolved from `.atk/results/vN`.
- Required current file: `eval_results.csv`.
- User-provided rule description, such as field comparison, thresholds, keyword matches, JSON-path checks, or custom predicates.
- Optional existing `.atk/runner/failure_rule.py`.
- Shared rules in `docs/shared-versioning-and-confirmation.md`.
- Script template in `templates/.atk/runner/failure_rule.py.md`.

## Outputs

- `.atk/runner/failure_rule.py` as a cross-version shared rule script.
- No `failure_cases.csv` is written by this Skill.

## Workflow

1. Resolve current version with `resolve_current_version()` using `RESULTS_DIR = Path(".atk/results")`.
2. Require `eval_results.csv` with `require_current_file(current_dir, "eval_results.csv")`.
3. Inspect `eval_results.csv` headers and sample rows, especially `agent_output` and likely expected-result columns.
4. If `.atk/runner/failure_rule.py` exists, ask whether to reuse it unchanged or update rule logic.
5. If rule criteria are unclear, ask the user for a concise rule statement.
6. Write/update `failure_rule.py` from `templates/.atk/runner/failure_rule.py.md`.
7. Tell the user to run `atk-find-failures-by-rule` to execute the rule script and write `failure_cases.csv`.

## Required rule behavior

- Use the current version directory, not a user-supplied version argument.
- Read current `eval_results.csv` and write current `failure_cases.csv` when executed later by `atk-find-failures-by-rule`.
- Preserve original result columns in `failure_cases.csv`.
- Default to overwriting `failure_cases.csv` during execution; do not backup or merge.
- Keep `failure_rule.py` under `.atk/runner/` and reuse it across versions.
- Keep dependencies stdlib-first unless the target project already requires more.

## Shared version rules

Use the canonical helper names and semantics from `docs/shared-versioning-and-confirmation.md`:

- `RESULTS_DIR = Path(".atk/results")`
- `resolve_current_version(results_dir=RESULTS_DIR)`
- `require_current_file(current_dir, filename)`

Do not fall back to an older version if current `eval_results.csv` is missing.

## Confirmation triggers

Ask before changing the script when:

- there is no explicit or inferable failure rule;
- expected-result columns or `agent_output` semantics are unclear;
- an existing `failure_rule.py` may contain hand-written logic;
- replacing `.atk/runner/failure_rule.py` would discard user-edited rule logic.

## Failure behavior

- Require current `vN/eval_results.csv`; if no current version or missing `eval_results.csv`, stop with repair/rerun guidance.
- If existing `failure_rule.py` exists, ask whether to reuse or update rule logic.
- If rule logic cannot be represented safely, stop and ask for a clarified rule.
- Do not run `failure_rule.py` yourself in this initialization Skill; generate/update it and instruct the user to run `atk-find-failures-by-rule`.

## Handoff message

After generating/updating the script, summarize:

- current version read path used to infer fields;
- rule logic encoded;
- script path `.atk/runner/failure_rule.py`;
- output path that execution will write: `.atk/results/vN/failure_cases.csv`;
- next command: `atk-find-failures-by-rule`.
