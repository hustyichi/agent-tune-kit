# Shared Versioning and Confirmation Rules

This document is the single source of truth for the Agent Tune Kit Skills. It extracts the version rules and uncertainty-confirmation behavior from `docs/codex_agent_tuning_prd.md` so every Skill uses the same terms when the repo is loaded as a local Codex plugin.

## Delivery boundary

This repository ships a local Codex plugin: `.codex-plugin/plugin.json`, complete `SKILL.md` files, reusable script templates, docs, safe personal marketplace installer/smoke/status/rollback tooling, and static validation. Individual Skill directories depend on shared `docs/` and `templates/` assets unless a future packaging pass inlines them.

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
- Personal marketplace installer: `scripts/install_plugin.py`.
- Default marketplace `source.path`: `./plugins/agent-tune-kit`.
- Main install command: `python3 scripts/install_plugin.py install`.
- Status command: `python3 scripts/install_plugin.py status`.
- Installer rollback command: `python3 scripts/install_plugin.py rollback --backup <backup-id>`.

## Canonical paths

- Shared runner scripts: `.atk/runner/`
- Versioned results: `.atk/results/vN/`
- Test runner output: `.atk/results/vN/results.csv`
- Optional run log: `.atk/results/vN/app.log`
- Failure cases: `.atk/results/vN/failure_cases.csv`
- Report: `.atk/results/vN/report.md`
- Tuning plan: `.atk/results/vN/tuning_plan.md`

## Current version vs new version creation

All non-runner Skills use the current-version rule: the current version is the numerically largest existing `.atk/results/vN` directory where `N` is a positive integer. Do not filter current-version selection by required files. If the current version is missing the required input file for a module, stop and ask the user to repair or rerun that module; never fall back to an older version.

Only `test_runner.py` creates or reuses result versions:

- If no `vN` exists, create `v1`.
- If the largest `vN` already contains `results.csv`, create `v{N+1}`.
- If the largest `vN` does not contain `results.csv`, reuse that directory and overwrite partial intermediates as needed.
- Do not ask the user for a version number or result directory in the normal flow.
- Do not clean up an incomplete directory automatically after script failure.
- Runners should write `results.csv` incrementally and flush after each row. A user interruption or per-run failure may leave a partial `results.csv`; downstream Skills should report missing/incomplete evidence instead of deleting or silently treating partial output as a complete evaluation.
- Runners should support `--limit` and `--offset` for bounded smoke runs while preserving the same version allocation rules.
- Runners should support `--concurrency` for faster batch execution. Concurrent runners must keep CSV writes on one writer path and flush after each completed row; with concurrency greater than 1, output rows may be written in completion order unless the generated runner explicitly preserves dataset order.

## Canonical version helper pseudocode

All Skill templates and script templates must use these helper names and semantics.

```python
from pathlib import Path

RESULTS_DIR = Path(".atk/results")

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
        raise UserActionRequired("No vN results directory exists; run test_runner.py first or confirm repair.")
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
    # Used only by test_runner.py.
    versions = list_version_dirs(results_dir)
    if not versions:
        target = results_dir / "v1"
    else:
        max_n, current = versions[-1]
        target = results_dir / f"v{max_n + 1}" if (current / "results.csv").exists() else current
    target.mkdir(parents=True, exist_ok=True)
    return target
```

## Uncertainty confirmation pattern

Each Skill should inspect repository files first, state the evidence it found, and ask a concise confirmation question only when acting without confirmation would likely corrupt data, write the wrong files, or misinterpret evaluation results.

Ask before proceeding when any of these remain unresolved after inspection:

- Agent invocation path, callable signature, required environment, or working directory;
- target project Python runtime or import roots needed to load local Agent code;
- dataset path, file format, encoding, delimiter, or field semantics;
- an existing dataset column named `agent_output` conflicts with the required actual-output column;
- app log source cannot be reliably captured or the capture method could alter Agent behavior;
- failure criteria, expected-result columns, or pass/fail semantics are ambiguous;
- current/previous-version sample matching is unreliable;
- an existing `filter_abnormal.py` should be reused or updated;
- overwriting `failure_cases.csv`, `report.md`, or `tuning_plan.md` would discard user edits not generated by the flow.

Do not ask for confirmation for routine, reversible local file generation when the target path and input semantics are already clear.

## Per-Skill preconditions and failure behavior

- `atk-status`: no version directory is required. It inspects `.atk/` state and recommends the next Skill or manual command without bypassing confirmation triggers.
- `atk-init`: no version directory is required. If Agent invocation, target runtime/import roots, dataset path/format, log source, or `agent_output` column conflict cannot be inferred safely, ask the user to confirm before writing `.atk/runner/test_runner.py`. Generated runners should support `--limit`/`--offset`/`--concurrency`, write results incrementally, and be import-checked under the inferred project runtime without invoking the Agent.
- `atk-run`: require `.atk/runner/test_runner.py`; execute it as the short command surface for batch testing using the target repository's Python runtime when available (`uv run python`, `.venv/bin/python`, Poetry, then `python3`). Pass through safe runner flags such as `--limit`, `--offset`, and `--concurrency`. The runner remains the only component that creates or reuses result versions. If the runner fails or no current `results.csv` is produced, report the failure and do not clean up partial version directories. If a partial `results.csv` exists after interruption/failure, report it explicitly.
- `atk-find-failures-by-rule`: require current `vN/results.csv`; if no current version or missing `results.csv`, stop with repair/rerun guidance. If existing `.atk/runner/filter_abnormal.py` exists, ask whether to reuse or update rule logic. This Skill generates or updates the script and instructs the user to run it manually; it does not run `filter_abnormal.py` itself in the normal PRD flow.
- `atk-find-failures`: require current `vN/results.csv`; if expected-result columns or failure criteria are ambiguous, ask for judgment. It writes `failure_cases.csv` in the current version and states that the file is overwritten.
- `atk-report`: require current `results.csv` and `failure_cases.csv`; `app.log` is optional. If previous version lacks `tuning_plan.md` or sample matching is unreliable, degrade to single-version or lower-confidence report with explicit explanation, not silent failure.
- `atk-tune`: require current `report.md`; if missing, stop and tell the user to run report generation first. After changes, write `tuning_plan.md` with the exact headings `## 目标异常清单`, `## 调优手段`, and `## 关联改动`. Suggest user git commits/checkpoints; do not perform automatic Agent tuning workflow rollback/baseline restore.
