---
name: atk-diagnose
description: Identify failures and uncertain cases from batch results, combining reusable checks with Codex judgment, and investigate initial causes before atk-report.
---

# Agent Tuning — Diagnose Results

## Purpose

Read current `eval_results.csv`, identify confirmed failures and unknown outcomes, and investigate initial causes.
Use one entry point for rules and semantic judgment; the user does not choose a mode or initialize a rule separately.
This Skill maps to `docs/codex_agent_tuning_prd.md` sections 2.4, 4, 5, and 7.
Traceability note: section 2.4 defines unified diagnosis.
`atk-report` still owns the detailed report and cross-version review; `atk-optimize` owns Agent changes.

## Inputs

- Required current `.atk/results/vN/eval_results.csv`, including unique `atk_id` and `agent_output`.
- User criteria, expected-result columns and optional `.atk/context.md`: `Tuning Objective`,
  `Agent Behavior Standard`, `Ground Truth Standard`, and relevant `Tuning Decisions`.
- Optional `.atk/runner/failure_rule.py`, row logs referenced by `log_path`, `app.log`, and relevant source code.
- Shared rules in `docs/shared-versioning-and-confirmation.md`.

## Outputs

- Current `failure_cases.csv`: confirmed failed rows only, preserving all original result columns including `agent_output`.
  Optional explanatory columns may be appended without overwriting source fields.
- Current `judgments.csv`: one row per executed `atk_id`, with columns `atk_id,verdict,reason,results_sha256`.
  `verdict` is `passed`, `failed`, or `unknown`; `reason` names the criterion and observed evidence or missing information.
  Every row carries the SHA-256 of the exact source `eval_results.csv` bytes. Do not modify that source file.
- Optional reusable `.atk/runner/failure_rule.py` only when stable checks justify it.
- Initial findings in the handoff and judgment reasons; no additional diagnosis report or Issue registry.

## Workflow

1. Resolve current version and require its `eval_results.csv`. Inspect fields and existing standards.
   Do not require a universal Schema for expected-result columns. Ask only about ambiguities that affect decisions;
   continue judging unaffected cases, marking the others `unknown` with the unresolved question.
2. Choose checks per requirement, not per batch. Use deterministic checks for explicit hard constraints and current-session
   Codex judgment for semantic requirements. A schema/format pass alone is not task success. An applicable hard failure
   is not overridden by an impression that the answer looks good. Conflicting or inapplicable checks need review.
3. Reuse an existing rule only after checking its criteria, scope and side effects. Prepare or update a rule on demand
   using the template below, then execute it within this Skill. Existing user-authored logic is preserved unless an
   update is authorized. A missing rule never blocks semantic diagnosis. Do not require a separate rule-preparation call.
   For an old Boolean filter, absence from its output is not proof of success: review uncovered requirements and write
   explicit judgments before reporting passes. If a rule covers only part of the criteria, keep other outcomes unknown
   until semantic review completes. Reconcile into one final judgment set, not competing outputs that overwrite each other.
4. Check every executed row, using bounded partitions for larger files. Uninspected, ambiguous or evidence-poor rows remain
   `unknown`; report partial coverage rather than extrapolating a sample. Separate execution errors from business failures
   in `reason`; missing output is not automatically a business failure. Do not silently rerun the Agent or incur probe calls.
5. Inspect available row logs before global logs for important failures; consult relevant code as needed. Distinguish
   observed symptoms from suspected causes and consider explanations that would change the repair location (Agent,
   tool/runtime, dataset or judge). A confirmed bad result can have an unknown cause. Missing logs do not block judging
   independently observable outcomes. Reference existing evidence rather than copying it into new files.
6. Write one `judgments.csv` row per executed ID, including explicit unknowns. Each pass requires coverage of all applicable
   task criteria; each failure has a criterion and evidence. Use a few known positive/negative examples to check new rules
   when available; do not invent calibration evidence or require a new approval ceremony on every run.
7. Regenerate `failure_cases.csv` from exactly the failed IDs, preserving source columns. Unknowns never enter this file,
   so `atk-eval --only-failures` continues to select confirmed failures only. To revisit unknowns, rejudge existing evidence
   or explicitly select cases for a new run; do not silently redefine failure-only execution.
8. Write files through temporary files and atomic replacement. Compute all rows and validate IDs/verdicts before replacement.
   Before replacing the failure table, invalidate old passes with a header-only `judgments.csv`; then replace the failure
   table and finally the complete judgments. If interrupted, remaining unjudged rows stay unknown. Respect user edits.
9. Summarize coverage, failures, unknown IDs/questions, priority findings and evidence paths. Continue with `atk-report`.

## Optional rule template

Template paths are plugin-root-relative, not relative to this Skill directory.
Locate the plugin root by walking upward from this `SKILL.md` until `.codex-plugin/plugin.json`, `skills/`, and `templates/`
are present. Read plugin-root-relative `templates/.atk/runner/failure_rule.py.md` (also `../../templates/.atk/runner/failure_rule.py.md`).
The relative fallback is available, but plugin-root discovery is the preferred resolution rule.
Prepare `.atk/runner/failure_rule.py` only as needed. Its `judge(row)` returns `(verdict, reason)`; unimplemented rules must
not execute. Use the target project's Python runtime: `uv run python`, `.venv/bin/python`, Poetry, then `python3`.
The command is `<python-runtime> .atk/runner/failure_rule.py`; keep dependencies stdlib-first.
Rule results are provisional when semantic requirements remain; finalize both outputs consistently after review.

## Shared version rules

Use `RESULTS_DIR = Path(".atk/results")`, `resolve_current_version(results_dir=RESULTS_DIR)` and
`require_current_file(current_dir, "eval_results.csv")` from the shared rules. Use the numerically largest `vN`,
never fall back to an older version when required current input is missing. Do not create a new result version here.
Do not write `.atk/context.md`; durable standards are inputs, not generated observations.

## Confirmation triggers

Ask when standards conflict in a way that changes the conclusion, replacing files would discard user-authored work,
or a proposed script has unclear/external side effects. Do not repeatedly ask to reuse an unchanged, applicable safe rule.
Routine local outputs with clear semantics do not require separate approvals.

## Failure behavior

Missing results or invalid/duplicate `atk_id` prevents reliable joining: stop and give repair guidance before writing.
An invalid rule or execution failure is not a pass; report the error and retain prior files, or complete independent semantic
judgments with uncovered rows marked unknown. Missing, stale, duplicate or invalid judgments never establish a pass.
Older failure tables remain usable as failure evidence and rerun inputs; do not infer historical passes from their complement.
Report judgment coverage as complete only when every executed row has a valid non-unknown judgment.
Unknown outcomes do not block `atk-report` or discussion of confirmed failures; state the remaining gaps.

## Handoff message

Report current version, applied criteria, checked/total counts, passed/failed/unknown counts, unknown IDs and next checks,
initial cause hypotheses versus evidence, and paths to `failure_cases.csv` and `judgments.csv`.
Give the next command: `atk-report` to generate `.atk/results/vN/report.md`.
