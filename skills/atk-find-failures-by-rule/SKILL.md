---
name: atk-find-failures-by-rule
description: Generate or update a reusable rule-based script for finding failing Agent tuning cases in the current results version.
---

# Agent Tuning — Find Failures by Rule

## Purpose

Create or update `.atk/runner/find_failures_by_rule.py` so the user can find failing rows in the current version's `eval_results.csv` and write them to `failure_cases.csv`. This Skill maps to `docs/codex_agent_tuning_prd.md` sections 2.4, 4, 5, and 7.

This Skill generates/reuses a script and instructs the user to run it manually. It does not run `find_failures_by_rule.py` itself in the normal PRD flow.

Traceability note: section 2.4 defines rule-based failure-case discovery, section 4 defines current-version behavior, and section 7 defines delivery requirements.

## Inputs

- Current version directory resolved from `.atk/results/vN`.
- Required current file: `eval_results.csv`.
- User-provided rule description, such as field comparison, thresholds, keyword matches, JSON-path checks, or custom predicates.
- Optional existing `.atk/runner/find_failures_by_rule.py`.
- Shared rules in `docs/shared-versioning-and-confirmation.md`.
- Script template in `templates/.atk/runner/find_failures_by_rule.py.md`.

## Outputs

- `.atk/runner/find_failures_by_rule.py` as a cross-version shared script.
- After the user manually runs the script, current `.atk/results/vN/failure_cases.csv`.
- The output file name is always `failure_cases.csv` and it overwrites the current version's existing file.

## Workflow

1. Resolve current version with `resolve_current_version()` using `RESULTS_DIR = Path(".atk/results")`.
2. Require `eval_results.csv` with `require_current_file(current_dir, "eval_results.csv")`.
3. Inspect `eval_results.csv` headers and sample rows, especially `agent_output` and likely expected-result columns.
4. If `.atk/runner/find_failures_by_rule.py` exists, ask whether to reuse it unchanged or update rule logic.
5. If rule criteria are unclear, ask the user for a concise rule statement.
6. Write/update `find_failures_by_rule.py` from `templates/.atk/runner/find_failures_by_rule.py.md`.
7. Tell the user to run `python3 .atk/runner/find_failures_by_rule.py`.

## Required rule behavior

- Use the current version directory, not a user-supplied version argument.
- Read current `eval_results.csv` and write current `failure_cases.csv`.
- Preserve original result columns in `failure_cases.csv`.
- Default to overwriting `failure_cases.csv`; do not backup or merge.
- Keep `find_failures_by_rule.py` under `.atk/runner/` and reuse it across versions.
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
- an existing `find_failures_by_rule.py` may contain hand-written logic;
- overwriting current `failure_cases.csv` might discard user-edited data.

## Failure behavior

- Require current `vN/eval_results.csv`; if no current version or missing `eval_results.csv`, stop with repair/rerun guidance.
- If existing `find_failures_by_rule.py` exists, ask whether to reuse or update rule logic.
- If rule logic cannot be represented safely, stop and ask for a clarified rule.
- Do not run `find_failures_by_rule.py` yourself in the normal PRD flow; generate/update it and instruct manual execution.

## Handoff message

After generating/updating the script, summarize:

- current version read path;
- rule logic encoded;
- output path `.atk/results/vN/failure_cases.csv`;
- overwrite behavior;
- manual command to run.
