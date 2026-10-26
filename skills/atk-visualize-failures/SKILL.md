---
name: atk-visualize-failures
description: Generate a dependency-free current-version HTML browser for Agent Tune Kit failure cases.
compatibility: Requires the installed ATK CLI on PATH (dataset, eval, results and visualize commands) and a compatible project Python runtime.
---

# Agent Tuning — Failure Case Visualization

## Purpose

Generate the optional current-version failure browser for human review before optimization. Fixed parsing, rendering, safety and frontend assets are maintained in the installed ATK package. Do not synthesize HTML, copy frontend assets into the project or install frontend dependencies.

## CLI dependency

Use the installed `atk` CLI from the target project cwd. Check `atk --version` and the command's `--help` when needed. If absent, guide installation with `uv tool install agent-tune-kit`, then `atk install`; do not regenerate fixed scripts. CLI/Skill contracts must be compatible. A successful `atk status` does not prove the current session loaded updated instructions.

## Inputs

Current `.atk/results/vN/failure_cases.csv`; optional same-version `report.md` and bounded, read-only historical metadata. Use the evaluation_target from inspect, skipping only verified evidence-only Runs and never an incomplete evaluation. Follow `docs/shared-versioning-and-confirmation.md`.

## Outputs

Only `.atk/results/vN/failure_cases.html`, a self-contained offline HTML file. Review CSV downloads are created by the browser. Do not create metadata JSON, package manifests, sidecar assets, LLM summaries or project-local visualization templates.

## Workflow

1. Confirm the required CSV exists; if missing, guide the prior data preparation or diagnosis step. Do not fabricate input.
2. Preserve any existing HTML that may contain user edits. Use `--overwrite` only when replacement is already authorized or has been confirmed.
3. Invoke the fixed CLI from the target project:

```sh
atk visualize failures [--results-dir .atk/results] [--version <bound-version>] [--overwrite] [--no-report] [--no-history] [--open|--no-open]
```

4. Browser opening is the default. Use `--no-open` only when the user opts out or requests headless operation. An unsuccessful open is non-fatal; report the output path for manual opening.
5. The CLI preserves source columns and supplies field role switching, search/filter, sorting, pagination, field analysis, row inspection and browser-local review notes/export. Describe the available evidence without claiming that structural checks prove semantic correctness.
6. For feedback, export failure_cases_review.csv; inspect evidence and continue to `atk-optimize` after `atk-report`. Direct conversation feedback remains usable without an export.

## Confirmation triggers

Ask only when overwriting would discard user edits, input structure prevents reliable preservation, or the intended input is ambiguous. Nonstandard field names alone are not a blocker. Respect already-authorized replacements.

## Failure behavior

Exit 0 means generation succeeded; 2 identifies an input/overwrite blocker; 1 indicates an unexpected failure. Existing output remains intact on failed generation. Empty data with headers still produces a valid empty state. Blank/duplicate headers prevent reliable generation. Do not silently switch inputs, create result versions, alter CSV data or change diagnosis/report behavior.

## Shared version rules

Read current failure_cases.csv only. Optional report context and bounded history are best-effort and non-blocking; missing optional data does not prevent rendering. `--no-report` skips report excerpts; `--no-history` skips historical metadata. Preserve explicit passed/failed/unknown semantics. Missing or stale judgments never imply a pass; legacy known failures remain reviewable. Safe same-version log links and nested JSON/code evidence remain available. Never modify the report or choose an older version to bypass a missing current failure table.

New evidence rows include bounded execution previews, coverage and CLI references. An invalid evidence reference makes the dependent verdict unknown in the current page and history. Verify linked artifacts through the evidence reader; do not infer completeness from a log link.

## Handoff message

Report input/version, row count, output path, overwrite status, browser-open result and next review step. Mention detected dataset quality issues or skipped report/history context when applicable. Explain how to export feedback; do not claim semantic correctness from the page's structural lint or a smaller failure count.
