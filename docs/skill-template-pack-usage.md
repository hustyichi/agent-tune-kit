# Agent Tune Kit Local Plugin and Skill Pack Usage

Agent Tune Kit provides a local Codex plugin for evaluating and tuning existing local Agents through the manual loop described in `docs/codex_agent_tuning_prd.md`. The supported setup path is the local plugin installer; do not split-copy individual `skills/*` directories because they reference shared pack assets by relative path.

This pass is a local-product minimum: `.codex-plugin/plugin.json`, personal marketplace registration, one-command installer orchestration, local smoke/status validation, and a guided status Skill. It deliberately avoids public marketplace publishing, brand assets/screenshots, hidden one-click orchestration across the Agent tuning loop, bundled example Agent/data fixtures, automatic Agent tuning workflow rollback, universal schemas, old installer command compatibility, and a full E2E test suite against a real Agent service.

## Local plugin install and smoke

Use the packaged `atk` installer for normal setup; no repository clone is required:

```sh
uv tool install agent-tune-kit
atk install
```

Alternative persistent installation:

```sh
pipx install agent-tune-kit
atk install
```

Default behavior:

- validates the packaged `.codex-plugin/plugin.json`;
- marketplace path: `~/.agents/plugins/marketplace.json`;
- plugin store: `~/plugins`;
- marketplace entry name: `agent-tune-kit`;
- marketplace `source.path`: `./plugins/agent-tune-kit`;
- package installs copy the bundled payload to `~/plugins/agent-tune-kit`; source-checkout developer installs may symlink the repository;
- runs local smoke/status checks by default;
- prints `/plugins` enablement guidance without claiming hidden Codex UI `Installed` state.

Useful commands:

```sh
atk preview --smoke
atk status
```

Conflict behavior:

- `preview` never writes marketplace/plugin-store files;
- interactive terminals prompt before replacing conflicting marketplace/plugin-store state;
- noninteractive destructive replacement requires `--yes --force`;
- `--yes` alone does not replace conflicts;
- `--no-input` never waits for prompts;
- after confirmation, replacement overwrites the existing plugin-store target and updates the marketplace entry;
- after installation validation succeeds, `atk install` clears only the personal marketplace ATK cache, including version-number and `local` directories; installation or validation failure leaves the cache untouched; cleanup failure returns an error;
- cache cleanup rejects paths overlapping the installation, source or marketplace file, rejects symlink cache roots, and does not follow nested symlinks. Finish active ATK tasks before reinstalling.

For isolated smoke tests, use temp paths:

```sh
CODEX_PERSONAL_PLUGIN_CACHE_ROOT=/tmp/agent-tune-cache atk install \
  --marketplace-path /tmp/agent-tune-marketplace.json \
  --plugin-store /tmp/agent-tune-plugins \
  --yes --force
CODEX_PERSONAL_PLUGIN_CACHE_ROOT=/tmp/agent-tune-cache atk status \
  --marketplace-path /tmp/agent-tune-marketplace.json \
  --plugin-store /tmp/agent-tune-plugins
```

The installer writes marketplace JSON atomically where practical and reports smoke status. It does not create a public marketplace package and does not mutate hidden Codex UI enablement state.

After install, refresh `/plugins`, select `Agent Tune Kit`, and enable it if needed. Restart Codex and open a new session. Existing sessions do not automatically replace instructions already read.

`atk status` compares relative paths and SHA-256 hashes for `.codex-plugin/`, `skills/`, `templates/`, `docs/`, `README.md` and `README.en.md`. It detects modified, missing and extra files in those assets, while ignoring repository-only files outside them and generated cache directories. It checks all cached ATK versions, accepting the installed version directory and `local`; older version directories are reported as stale. It never writes files.

- Exit `0`: cached assets match, or no cache has been generated yet. An absent cache is explicitly pending Codex loading, not proof of activation.
- Exit `1`: incomplete installation, CLI/installed version mismatch, stale cache, unreadable files or unsafe paths.
- CLI checks do not observe Codex UI enablement or in-memory session state; verify the actual Skill path and contents in a new session.

Cache location precedence: `CODEX_PERSONAL_PLUGIN_CACHE_ROOT` (the marketplace cache root, with `agent-tune-kit/` appended), otherwise `$CODEX_HOME/plugins/cache/personal`, otherwise `~/.codex/plugins/cache/personal`. Always set an isolated cache root when using temporary marketplace/plugin-store paths; those overrides alone do not isolate the cache.


## Contributor and maintainer packaging notes

For source checkout development, keep the packaged CLI path first but use the wrapper when you need to test checkout behavior explicitly:

```sh
uv sync
uv run atk preview --smoke
CODEX_PERSONAL_PLUGIN_CACHE_ROOT=/tmp/agent-tune-cache uv run atk install --copy --marketplace-path /tmp/agent-tune-marketplace.json --plugin-store /tmp/agent-tune-plugins --yes --force
python3 scripts/install_plugin.py install  # contributor wrapper only
```

Same-version updates are handled by `atk install` automatically. The dev refresh helper now only delegates to `atk install --copy --yes --force` and `atk status`; it does not delete caches before installation:

```sh
scripts/dev-refresh-install.sh
```

For isolated local checks, pass the same installer path overrides; the script forwards them to `atk install` and `atk status`:

```sh
CODEX_PERSONAL_PLUGIN_CACHE_ROOT=/tmp/agent-tune-cache \
  scripts/dev-refresh-install.sh \
  --marketplace-path /tmp/agent-tune-marketplace.json \
  --plugin-store /tmp/agent-tune-plugins
```

Before publishing or TestPyPI verification, run:

```sh
uv sync
uv run pytest
uv build
```

Then inspect the wheel/sdist for `agent_tune_kit/plugin_payload/agent-tune-kit/.codex-plugin/plugin.json`, install the wheel or sdist into a temporary environment, and run `atk preview`, `atk status`, and `atk install` from outside the repository. TestPyPI publication is a maintainer release step, not part of ordinary local plugin use.

## Repository layout boundary

Do not copy a single `skills/*` directory by itself; keep `skills/`, `templates/`, and `docs/` together unless a future packaging pass intentionally inlines those shared assets. The plugin installer is the supported setup path; this project does not keep old installer command compatibility before launch.

## What is included

- `.codex-plugin/plugin.json` — local plugin manifest using `skills: "./skills/"`.
- `skills/atk-dataset/SKILL.md` — prepare/reuse data, clarify grounded criteria and apply direct or exported feedback without a mandatory review cycle.
- `skills/atk-dataset/scripts/update_dataset.py` — compatibility entrypoint for CLI dataset updates.
- `skills/atk-visualize-dataset/SKILL.md` — run the fixed stdlib generator script to write `.atk/datasets/dataset.html` as an optional, dependency-free Dataset Visualizer-style static browser for `.atk/datasets/dataset.csv`, focused on reviewing rows and expected-result fields.
- `skills/atk-init/SKILL.md` — generate `.atk/runner/eval_runner.py`.
- `skills/atk-eval/SKILL.md` — run `.atk/runner/eval_runner.py` through a short Skill command and summarize the current results version.
- `skills/atk-diagnose/SKILL.md` — combine checks and semantic judgment, prepare/reuse rules internally, and write confirmed failures plus explicit judgments.
- `skills/atk-report/SKILL.md` — write current `report.md`, including adjacent-version validation when possible.
- `skills/atk-visualize-failures/SKILL.md` — run the fixed stdlib generator script to write current `failure_cases.html` as an optional, dependency-free browser for current `failure_cases.csv`.
- `skills/atk-optimize/SKILL.md` — tune the target Agent and write current `tuning_plan.md`.
- `templates/.atk/runner/eval_runner.py.md` — script template preserving original dataset columns and appending `agent_output` plus `log_path`.
- `templates/.atk/runner/failure_rule.py.md` — project-specific rule adapter for `atk results run-rule`.
- `docs/shared-versioning-and-confirmation.md` — shared current/new version semantics and confirmation triggers.
- `scripts/install_plugin.py` — safe local marketplace installer/smoke/status tool.
- `scripts/validate_skill_pack.py` — lightweight static checker for this local plugin pack.

## Manual 2.2 → 2.6 loop

1. Use `atk-dataset` only when evaluation data needs preparation or revision. Reuse existing data and expected fields; do not regenerate trusted answers or invent missing facts. Preserve valid IDs and original inputs. Direct feedback needs no review CSV.
2. Optional exported `dataset_review.csv` remains supported. Update the existing answer column with `update_dataset.py --field <column>`; use `--direct` for explicit feedback without an export. Unknown criteria can remain gaps without blocking other usable samples.
3. Use `atk-visualize-dataset` only when visual review is helpful. Its existing HTML and export format remain available; they are not prerequisites for evaluation.
4. If no compatible runner is connected, trigger `atk-init` in Codex. Reuse data prepared by `atk-dataset` without repeating confirmed questions. The Skill reads the Agent source and dataset, asks only about unsafe ambiguity, validates/normalizes `.atk/datasets/dataset.csv` as the ATK canonical runnable dataset with a required `atk_id` column, then writes `.atk/runner/eval_runner.py`.
5. Trigger `atk-eval`. It executes `atk eval`; the runner creates or reuses a version directory and writes `.atk/results/vN/eval_results.csv` plus optional `app.log`. After a failure set exists, `atk-eval --only-failures` reruns only rows whose `atk_id` appears in the latest prior `failure_cases.csv`, selecting those rows from `.atk/datasets/dataset.csv` rather than executing the result CSV as input. When same-process Python `logging` capture is configured, global logging records are written to `.atk/results/vN/app.log`; row-attributed records also create `.atk/results/vN/logs/row_{source_index:06d}.log` files and record relative POSIX paths in `log_path` for serial runs and for `--concurrency > 1` while the generated concurrent row-log flag is enabled. The row router only writes records emitted under an active ATK row context; stdout/stderr, subprocess, multiprocess, and post-row background logs remain out of row-log scope. If concurrent row logging is disabled, `--concurrency > 1` visibly downgrades and no row logs are created while global Python logging still writes `app.log`.
6. Trigger unified diagnosis:
   - Use `atk-diagnose` once: it prepares/reuses rules as needed and combines their evidence with semantic judgment.
   - `failure_cases.csv` remains the confirmed-failure subset; `judgments.csv` records passed/failed/unknown, reasons and the source CSV hash. Missing judgments never imply passes.
7. Trigger `atk-report` to create `.atk/results/vN/report.md`. From `v2` onward, it compares the current version with the previous existing version and reads the previous `tuning_plan.md` when available. Reports prefer row-specific files referenced by `log_path` and fall back to `app.log` when row logs are unavailable.
8. Optionally trigger `atk-visualize-failures` to create `.atk/results/vN/failure_cases.html`. It can run any time current `failure_cases.csv` exists; same-version `report.md` is optional best-effort context and never blocks CSV visualization. The Skill calls `atk visualize failures` (the old `scripts/generate_failure_browser.py` remains a compatibility entrypoint) to produce deterministic HTML with expected-vs-actual review, search/filter/pagination, schema-adaptive role switching, and no LLM summaries, project-local template, dependencies, or sidecar metadata.
9. Trigger `atk-optimize` to change the Agent and write `.atk/results/vN/tuning_plan.md`.
10. Optionally create a user git commit/checkpoint. Agent tuning rollback remains user-git-only guidance; this plugin does not automate Agent code restore.
11. Run the same loop again. The next test run creates `v{N+1}` when the current max version already has `eval_results.csv`.

## Version example: v1 → v2

- First test run: no version exists, so `eval_runner.py` creates `.atk/results/v1/eval_results.csv`.
- First report: no previous version exists, so `v1/report.md` is a single-version report.
- First tuning: writes `v1/tuning_plan.md` with `## 目标异常清单`, `## 调优手段`, and `## 关联改动`.
- Second test run: `v1/eval_results.csv` exists, so the runner creates `v2/eval_results.csv`.
- Second report: `v2/report.md` reads `v1/tuning_plan.md` and classifies each target as `已解决`, `部分解决`, `未解决`, or `无法判断`.

## Static validation

Run:

```sh
python3 scripts/validate_skill_pack.py
git diff --check
find skills templates docs scripts .codex-plugin -maxdepth 4 -type f | sort
grep -R "agent_output\|ground_truth\|failure_cases.csv\|failure_cases.html\|tuning_plan.md\|目标异常清单\|调优手段\|关联改动\|source.path" skills templates docs README.md README.en.md .codex-plugin scripts
```

`validate_skill_pack.py` checks required files, required sections, manifest fields, installer behavior, PRD traceability, canonical version helper names and snippets, output paths, non-goal boundaries, dataset-only/pre-results classifications such as `atk-dataset`, and each Skill's precondition/failure behavior.

Legacy one-shot installation remains available: `uvx --from agent-tune-kit atk install`. Skills now require the persistent `atk` command on PATH.

## Shared CLI capabilities

Install the ATK CLI first, then run `atk install` to install its bundled Skills. Fixed operations are maintained in the Python package; Skills retain semantic diagnosis, reporting and Agent optimization.

```sh
atk dataset prepare --source data/eval.csv
atk dataset inspect --direct --json
atk dataset update --direct --field expected --updates-path updates.csv
atk eval --check --json
atk eval --limit 5 --concurrency 2
atk results inspect --json
atk results run-rule
atk results apply-judgments --judgments-path judgments.csv --source-results .atk/results/v1/eval_results.csv
atk results compare --ids 1 2 --json
atk visualize dataset --no-open
atk visualize failures --no-open
```

These commands run from the Agent project. New project adapters delegate to the shared engine; existing full runners remain executable. Dataset fields, IDs, result paths, unknown verdicts, logs and offline review pages retain their contracts. Use `--help` on each command for its options.
