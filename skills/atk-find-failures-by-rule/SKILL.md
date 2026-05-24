---
name: atk-find-failures-by-rule
description: Generate or update a reusable rule-based script for finding failing Agent tuning cases in the current results version.
---

# Agent Tuning — Find Failures by Rule

## Purpose

Create or update `.atk/runner/filter_abnormal.py` so the user can find failing or abnormal rows in the current version's `results.csv` and write them to `abnormal_cases.csv`. This Skill maps to `docs/codex_agent_tuning_prd.md` sections 2.4, 4, 5, and 7.

This Skill generates/reuses a script and instructs the user to run it manually. It does not run `filter_abnormal.py` itself in the normal PRD flow.

Traceability note: section 2.4 defines rule-based abnormal-case discovery, section 4 defines current-version behavior, and section 7 defines delivery requirements.

## Inputs

- Current version directory resolved from `.atk/results/vN`.
- Required current file: `results.csv`.
- User-provided rule description, such as field comparison, thresholds, keyword matches, JSON-path checks, or custom predicates.
- Optional existing `.atk/runner/filter_abnormal.py`.
- Shared rules in `docs/shared-versioning-and-confirmation.md`.
- Script template in `templates/.atk/runner/filter_abnormal.py.md`.

## Outputs

- `.atk/runner/filter_abnormal.py` as a cross-version shared script.
- After the user manually runs the script, current `.atk/results/vN/abnormal_cases.csv`.
- The output file name is always `abnormal_cases.csv` and it overwrites the current version's existing file.

## Workflow

1. Resolve current version with `resolve_current_version()` using `RESULTS_DIR = Path(".atk/results")`.
2. Require `results.csv` with `require_current_file(current_dir, "results.csv")`.
3. Inspect `results.csv` headers and sample rows, especially `agent_output` and likely expected-result columns.
4. If `.atk/runner/filter_abnormal.py` exists, ask whether to reuse it unchanged or update rule logic.
5. If rule criteria are unclear, ask the user for a concise rule statement.
6. Write/update `filter_abnormal.py` from `templates/.atk/runner/filter_abnormal.py.md`.
7. Tell the user to run `python3 .atk/runner/filter_abnormal.py`.

## Required rule behavior

- Use the current version directory, not a user-supplied version argument.
- Read current `results.csv` and write current `abnormal_cases.csv`.
- Preserve original result columns in `abnormal_cases.csv`.
- Default to overwriting `abnormal_cases.csv`; do not backup or merge.
- Keep `filter_abnormal.py` under `.atk/runner/` and reuse it across versions.
- Keep dependencies stdlib-first unless the target project already requires more.

## Shared version rules

Use the canonical helper names and semantics from `docs/shared-versioning-and-confirmation.md`:

- `RESULTS_DIR = Path(".atk/results")`
- `resolve_current_version(results_dir=RESULTS_DIR)`
- `require_current_file(current_dir, filename)`

Do not fall back to an older version if current `results.csv` is missing.

## Confirmation triggers

Ask before changing the script when:

- there is no explicit or inferable abnormal rule;
- expected-result columns or `agent_output` semantics are unclear;
- an existing `filter_abnormal.py` may contain hand-written logic;
- overwriting current `abnormal_cases.csv` might discard user-edited data.

## Failure behavior

- Require current `vN/results.csv`; if no current version or missing `results.csv`, stop with repair/rerun guidance.
- If existing `filter_abnormal.py` exists, ask whether to reuse or update rule logic.
- If rule logic cannot be represented safely, stop and ask for a clarified rule.
- Do not run `filter_abnormal.py` yourself in the normal PRD flow; generate/update it and instruct manual execution.

## Handoff message

After generating/updating the script, summarize:

- current version read path;
- rule logic encoded;
- output path `.atk/results/vN/abnormal_cases.csv`;
- overwrite behavior;
- manual command to run.
