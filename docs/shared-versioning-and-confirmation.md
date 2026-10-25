# Shared Versioning and Confirmation Rules

This document is the single source of truth for the Agent Tune Kit Skills. It extracts the version rules and uncertainty-confirmation behavior from `docs/codex_agent_tuning_prd.md` so every Skill uses the same terms when the repo is loaded as a local Codex plugin.

## Delivery boundary

This repository ships a local Codex plugin: `.codex-plugin/plugin.json`, complete `SKILL.md` files, reusable script templates, docs, safe personal marketplace installer/smoke/status tooling, and static validation. Individual Skill directories depend on shared `docs/` and `templates/` assets unless a future packaging pass inlines them.

Non-goals for this pass:

- no public marketplace publishing or shared catalog release;
- no brand assets, logo files, screenshots, or public listing polish;
- no hidden one-click orchestration or full automation across the 2.2 → 2.6 Agent tuning loop;
- no old installer command compatibility before launch;
- no universal Schema requirement for Agent inputs, datasets, metrics, or expected-result fields;
- no bundled example Agent/data fixtures;
- no automatic Agent tuning workflow rollback, baseline restore, or historical code recovery; Agent code rollback is user-git-only guidance;
- no full E2E test suite against a real Agent service.

## Plugin loading

- Local plugin manifest: `.codex-plugin/plugin.json` with `skills: "./skills/"`.
- Packaged installer command: `atk`, exposed by the `agent-tune-kit` Python package.
- No-clone install command: `uvx --from agent-tune-kit atk install`.
- Persistent install command: `uv tool install agent-tune-kit` then `atk install`; `pipx install agent-tune-kit` is also supported.
- Personal marketplace developer wrapper: `scripts/install_plugin.py` for source checkouts only.
- Default marketplace `source.path`: `./plugins/agent-tune-kit`.
- Main install command: `atk install`; successful installation validation invalidates personal ATK caches, including same-version copies. Restart Codex and open a new session after enabling the plugin.
- Status command: `atk status`; read-only payload/cache content comparison. Exit `0` means matching cache or pending cache generation, not confirmed session activation. Exit `1` means incomplete installation, version/content mismatch or inspection error.
- Contributor fallback: `python3 scripts/install_plugin.py install` and `python3 scripts/install_plugin.py status` remain wrappers around the packaged CLI.

## Canonical paths

- Shared runner scripts: `.atk/runner/`
- Canonical runnable datasets used by generated runners: `.atk/datasets/`
- Local private tuning consensus: `.atk/context.md`
- Versioned results: `.atk/results/vN/`
- Test runner output: `.atk/results/vN/eval_results.csv`
- Optional run log: `.atk/results/vN/app.log`; when same-process Python `logging` is configured, runners must write global logging records here before considering stdout/stderr fallback or existing Agent log files
- Optional row logs: `.atk/results/vN/logs/row_{source_index:06d}.log`, referenced from `log_path`, for configured same-process Python `logging` records emitted under an active ATK row context
- Failure cases: `.atk/results/vN/failure_cases.csv`
- Failure case visualization: `.atk/results/vN/failure_cases.html`
- Report: `.atk/results/vN/report.md`
- Tuning plan: `.atk/results/vN/tuning_plan.md`

## Dataset-only / pre-results Skills

`atk-dataset` prepares or revises `.atk/datasets/dataset.csv` without running the Agent or creating result versions.
It combines sample preparation, grounded answer enrichment and feedback corrections internally. Existing suitable data is
reused; preserve all valid IDs, input values, row order and original field names outside explicitly requested changes.
Use existing expected/label/acceptance_criteria/ground_truth fields directly; do not duplicate them into ground_truth or
require one global answer style. Uncertain answers remain blank/unchanged and are reported, not invented.

`atk-visualize-dataset` is optional. Direct conversation feedback needs no HTML or review export. Exported
`dataset_review.csv` keeps its existing atk_id,row_number,review_feedback format; verify its source and intended samples.
For targeted edits, `skills/atk-dataset/scripts/update_dataset.py` preserves the CSV and writes atomically. Select the
existing answer column with `--field`; use `--direct` only for explicit feedback/enrichment without an exported review.
Without a field argument the helper retains the original ground_truth default. Existing review discovery remains available,
but do not automatically apply an unrelated Downloads export solely because its IDs match.

No extra metadata/report artifacts are required. Preserve historical eval_results.csv and judgments when revising data;
run subsequent evaluations with the new dataset and distinguish criteria corrections from Agent improvements.
For existing compatible runners, proceed to atk-eval; otherwise use atk-init for input mapping and connection.
`atk-new-agent` remains an optional pre-init scaffold when the user needs an Agent built.

## Local private tuning context

`.atk/context.md` is an optional local, private tuning-consensus document. Its contract is defined in
`docs/local-context.md`. Skills may read it for user-confirmed tuning objectives, Agent behavior standards,
`ground_truth` standards, feedback, and tuning decisions. It must not become a registry for dataset headers, field
types, row counts, result versions, run logs, or other facts that can be recovered from `.atk/datasets/`,
`.atk/results/`, or runner files.

Missing `.atk/context.md` is never a blocker. When present, Skills should treat it as durable user guidance and cite any
context-derived assumptions in their handoff. A Skill should write the file only when it is recording a
user-confirmed standard, durable feedback item, or tuning decision that future loops should preserve.

## Current version vs new version creation

All non-runner Skills use the current-version rule: the current version is the numerically largest existing `.atk/results/vN` directory where `N` is a positive integer. Do not filter current-version selection by required files. If the current version is missing the required input file for a module, stop and ask the user to repair or rerun that module; never fall back to an older version.

Only `eval_runner.py` creates or reuses result versions:

- If no `vN` exists, create `v1`.
- If the largest `vN` already contains `eval_results.csv`, create `v{N+1}`.
- If the largest `vN` does not contain `eval_results.csv`, reuse that directory and overwrite partial intermediates as needed.
- Do not ask the user for a version number or result directory in the normal flow.
- Do not clean up an incomplete directory automatically after script failure.
- Runners should write `eval_results.csv` incrementally and flush after each row. A user interruption or per-run failure may leave a partial `eval_results.csv`; downstream Skills should report missing/incomplete evidence instead of deleting or silently treating partial output as a complete evaluation.
- Runners should support `--limit` and `--offset` for bounded smoke runs while preserving the same version allocation rules. Because bounded smoke runs still execute `eval_runner.py`, they create or reuse `.atk/results/vN` like any other run. If `atk-init` performs such a smoke run for validation, it must record the pre-smoke version state and clean up only the version directory created or reused solely by that smoke run before handing off.
- Runners should support `--concurrency` for faster batch execution. Concurrent runners must keep CSV writes on one writer path and flush after each completed row; with concurrency greater than 1, output rows may be written in completion order unless the generated runner explicitly preserves dataset order.
- Runners should support `--only-failures` to rerun only samples whose `atk_id` appears in the latest prior `failure_cases.csv`. This mode must select rows from `.atk/datasets/dataset.csv`; it must not execute `failure_cases.csv` as an input dataset. Resolve the prior failure set by finding the newest existing version containing `failure_cases.csv` before writing result rows for the new run. If no prior failure file exists, it lacks `atk_id`, contains no rows, or references `atk_id` values absent from the canonical dataset, stop instead of falling back to a full run.

## Dataset canonicalization rules

`atk-init` must write the user-provided evaluation dataset into `.atk/datasets/` as an ATK canonical runnable dataset before writing `.atk/runner/eval_runner.py`. The generated runner must read that project-local dataset, not the original source path, so future source-file moves do not break `atk-eval`.

Use a fixed canonical dataset slot:

- Always use `.atk/datasets/dataset.csv` for the init-time canonical runnable dataset.
- The canonical dataset must contain a stable ATK identity column named `atk_id`.
- If the source dataset lacks `atk_id`, append `atk_id` and fill it from the source data row number, starting at `1`.
- If the source dataset already has `atk_id`, reuse it only when every value is a non-empty, unique positive integer.
- Preserve all user-provided columns and their relative order; treat `atk_id` as ATK metadata rather than Agent input unless the user explicitly says otherwise.
- If `.atk/datasets/dataset.csv` does not exist, write the canonical dataset there.
- If it exists and the canonical content is identical, reuse it and do not create a duplicate.
- If it exists with different canonical content, ask before overwriting because future dataset subsets depend on this fixed semantic name.
- Compare canonical content with a reliable digest such as `sha256`, optionally using file size as a fast precheck.
- If the canonical dataset cannot be written or content comparison cannot be completed safely, stop before writing the runner instead of pointing the runner at the external source dataset.

Generated runners should define `DATASETS_DIR = Path(".atk/datasets")`, set `DATASET_PATH = DATASETS_DIR / "dataset.csv"`, and require a valid `atk_id` column before executing rows.

## Canonical version helper pseudocode

All Skill templates and script templates must use these helper names and semantics.

```python
from pathlib import Path

RESULTS_DIR = Path(".atk/results")
DATASETS_DIR = Path(".atk/datasets")

class UserActionRequired(RuntimeError):
    """Raised when the user must repair inputs or confirm an unsafe inference."""


def list_version_dirs(results_dir=RESULTS_DIR):
    # Return [(number, path)] for directories named vN where N is a positive integer.
    # Missing results_dir means no versions exist yet; runner first run will create v1.
    if not results_dir.exists():
        return []
    return sorted(
        (int(path.name[1:]), path)
        for path in results_dir.iterdir()
        if path.is_dir() and path.name.startswith("v") and path.name[1:].isdigit() and int(path.name[1:]) > 0
    )


def resolve_current_version(results_dir=RESULTS_DIR):
    # Used by every non-runner Skill. Do not filter by required files.
    versions = list_version_dirs(results_dir)
    if not versions:
        raise UserActionRequired("No vN results directory exists; run eval_runner.py first or confirm repair.")
    return versions[-1][1]


def resolve_previous_version(current_dir, results_dir=RESULTS_DIR):
    # Used by report Skill for adjacent-version comparison.
    versions = list_version_dirs(results_dir)
    prior = [path for _, path in versions if path != current_dir and int(path.name[1:]) < int(current_dir.name[1:])]
    return prior[-1] if prior else None


def require_current_file(current_dir, filename):
    # Missing inputs are blockers; never fall back to an older version.
    path = current_dir / filename
    if not path.exists():
        raise UserActionRequired(f"Current version {current_dir.name} is missing {filename}; fix or rerun the prior step.")
    return path


def allocate_next_results_version(results_dir=RESULTS_DIR):
    # Used only by eval_runner.py.
    versions = list_version_dirs(results_dir)
    if not versions:
        target = results_dir / "v1"
    else:
        max_n, current = versions[-1]
        target = results_dir / f"v{max_n + 1}" if (current / "eval_results.csv").exists() else current
    target.mkdir(parents=True, exist_ok=True)
    return target
```

## Uncertainty confirmation pattern

Each Skill should inspect repository files first, state the evidence it found, and ask a concise confirmation question only when acting without confirmation would likely corrupt data, write the wrong files, or misinterpret evaluation results.

Ask before proceeding when any of these remain unresolved after inspection:

- Agent invocation path, callable signature, required environment, or working directory;
- target project Python runtime or import roots needed to load local Agent code;
- dataset path, file format, encoding, delimiter, or field semantics;
- a proposed dataset answer would require unavailable domain facts or conflict with confirmed business criteria;
- an existing dataset column named `agent_output` conflicts with the required actual-output column, or `log_path` conflicts with the required row-log evidence column;
- app log source, Python `logging` logger names, or row-log capture method cannot be reliably captured or could alter Agent behavior;
- failure criteria, expected-result columns, or pass/fail semantics are ambiguous;
- `.atk/context.md` contains standards that conflict with the current user request or dataset review feedback;
- existing answers or criteria would be replaced beyond the user-authorized corrections;
- current/previous-version sample matching is unreliable;
- an existing `failure_rule.py` should be reused or updated by `atk-diagnose`;
- executing `failure_rule.py` would overwrite an existing `failure_cases.csv`;
- overwriting `failure_cases.csv`, `failure_cases.html`, `report.md`, or `tuning_plan.md` would discard user edits not generated by the flow.

Do not ask for confirmation for routine, reversible local file generation when the target path and input semantics are already clear.

## Judgment coverage

`judgments.csv` is the explicit outcome record for the same version's `eval_results.csv`; `failure_cases.csv` remains
its confirmed-failure subset and the input to `--only-failures`. The judgment reason records criteria, evidence and any
initial cause hypothesis separately. Rule setup and execution are internal to `atk-diagnose`, not user-selected modes.

Reports and browsers must never infer a pass from absence in `failure_cases.csv`. Require a unique valid `passed`
judgment with a non-empty reason and matching `results_sha256`. Missing/stale/invalid/duplicate verdicts are unknown;
a contradiction with the failure table cannot establish a pass and needs review. Unknowns are neither passes nor failures.
Report executed, judged, passed, failed and unknown counts separately; specify denominators and do not claim full coverage
from sampling. Existing result/failure files remain readable, but old results without explicit judgments cannot prove passes.

For cross-version targets, all target IDs must have current valid judgments: all passed means resolved, all failed means
unresolved, and a mix means partial. Any unexecuted or unknown target makes the conclusion indeterminate. A new regression
requires an explicit prior pass; absence from an old failure table is not sufficient. Changed standards/data prevent a
causal improvement claim without compatible reassessment. Do not modify the Agent or standard to make a judgment pass.

## Per-Skill preconditions and failure behavior

- `atk-dataset`: reuse or prepare dataset.csv, then apply grounded feedback directly or from the existing review export. Preserve valid IDs, row order, business fields and values outside requested corrections. Missing criteria remain unknown downstream. No mandatory HTML, global ground-truth style or export cycle. Use update_dataset.py for targeted answer edits; protect user data and historical results. Only durable confirmed standard changes belong in context.md. After a brief coverage/gap summary, hand off to atk-init if not connected or input mapping changed, otherwise atk-eval. Unchanged current results can proceed to atk-diagnose.
- `atk-init`: no version directory is required. If Agent invocation, target runtime/import roots, dataset path/format, `atk_id` creation/validation, log source, Python `logging` logger names, existing `.atk/datasets/dataset.csv` overwrite semantics, or `agent_output` / `log_path` column conflict cannot be inferred safely, ask the user to confirm before writing `.atk/runner/eval_runner.py`. Generated runners should support `--limit`/`--offset`/`--concurrency`, write results incrementally, require valid `atk_id`, add `log_path`, create trustworthy row logs for configured same-process Python logging capture when an ATK row context is active, write same-process Python logging records to global `app.log`, keep stdout/stderr/subprocess/multiprocess/post-row background logs out of row files, and be import-checked under the inferred project runtime. If `atk-init` runs `.atk/runner/eval_runner.py --limit 1` or any other runner smoke command during verification, it must clean up the smoke-created result directory when that directory is confidently init-owned temporary output; if cleanup is unsafe because the directory pre-existed or contains user data, report the version impact instead of deleting it.
- `atk-eval`: require `.atk/runner/eval_runner.py`; execute it as the short command surface for batch testing using the target repository's Python runtime when available (`uv run python`, `.venv/bin/python`, Poetry, then `python3`). Pass through safe runner flags such as `--limit`, `--offset`, `--concurrency`, and `--only-failures`. The runner remains the only component that creates or reuses result versions. If the runner fails or no current `eval_results.csv` is produced, report the failure and do not clean up partial version directories. If `--only-failures` cannot resolve prior failed `atk_id` values back to `.atk/datasets/dataset.csv`, report the error and do not silently run the full dataset. If configured row logging is downgraded under `--concurrency > 1` because concurrent row logging is disabled, report that no `logs/row_*.log` files are expected and suggest serial execution or enabling the generated concurrent row-log flag for same-process Python logging evidence. If a partial `eval_results.csv` exists after interruption/failure, report it explicitly.
- `atk-diagnose`: require current `eval_results.csv` and unique `atk_id`; apply user/context standards. Combine deterministic checks with current-session semantic judgment, preparing/reusing `failure_rule.py` only on demand within the same Skill. Preserve original columns in `failure_cases.csv`, containing only confirmed failures. Record every executed ID in `judgments.csv` with `atk_id,verdict,reason,results_sha256`; verdicts are `passed`, `failed`, or `unknown`, and the hash is SHA-256 of the exact input CSV bytes. Missing/uninspected/ambiguous rows remain unknown. Compute and validate output before writing; invalidate old judgments, atomically replace failures, then atomically replace judgments. It should not write `.atk/context.md`. After writing `failure_cases.csv`, tell the user to run `atk-report` next.
- `atk-report`: require current `eval_results.csv` and `failure_cases.csv`; row logs and `app.log` are optional. Read optional `.atk/context.md` before root-cause analysis so the report can distinguish Agent failures, `ground_truth` standard conflicts, and dataset-standard repair needs. Prefer existing files referenced by `log_path` for per-row failure attribution, then fall back to `app.log`. If previous version lacks `tuning_plan.md` or sample matching is unreliable, degrade to single-version or lower-confidence report with explicit explanation, not silent failure. Append to `.atk/context.md` only for durable user-confirmed feedback or tuning decisions, not for routine run summaries.
- `atk-visualize-failures`: require current `failure_cases.csv`; optional same-version `report.md` context is best-effort and non-blocking. Generate current `failure_cases.html` only through the fixed plugin-owned stdlib script `scripts/generate_failure_browser.py`, with escaped dependency-free static HTML, expected-vs-actual detail review, search/filter/pagination, schema-adaptive role switching, and safe relative log links. Confirm before overwriting an existing `failure_cases.html` that may contain user edits. If `report.md` is missing or unparseable, continue and note that report context was skipped.
- `atk-visualize-dataset`: no version directory is required; it operates on the non-versioned `.atk/datasets/` directory. Require `.atk/datasets/dataset.csv`; if it is missing or its header cannot be parsed, stop with guidance to run `atk-dataset` first. Generate `.atk/datasets/dataset.html` only through the fixed plugin-owned stdlib script `scripts/generate_dataset_browser.py`, with escaped dependency-free Dataset Visualizer-style static HTML, Data List / Field Feature Analysis tabs, column visibility controls, sorting/pagination, row detail drawer, input-vs-ground_truth comparison for confirming ground_truth correctness, dataset quality lint, schema-adaptive role switching, and client-side review export. Confirm before overwriting an existing `dataset.html` that may contain user edits. This Skill does not require user-installed frontend dependencies and does not read or write any `.atk/results/vN` version directory.
- `atk-optimize`: require current `report.md`; if missing, stop and tell the user to run report generation first. Read optional `.atk/context.md` and preserve `Tuning Objective`, `Agent Behavior Standard`, `Ground Truth Standard`, and prior `Tuning Decisions` when changing the Agent. Do not modify `Ground Truth Standard`; route standard changes to `atk-dataset`. After changes, write `tuning_plan.md` with the exact headings `## 目标异常清单`, `## 调优手段`, and `## 关联改动`. Suggest user git commits/checkpoints; do not perform automatic Agent tuning workflow rollback/baseline restore.
