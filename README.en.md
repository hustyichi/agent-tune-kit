# Agent Tune Kit

English | [简体中文](README.md)

[![PyPI](https://img.shields.io/pypi/v/agent-tune-kit.svg)](https://pypi.org/project/agent-tune-kit/)

Agent Tune Kit is a **local Codex plugin** for moving your existing local Agent from "it runs" to "it can be evaluated, diagnosed, and iteratively tuned."

It focuses on two jobs: first turn evaluation data into a reusable, human-reviewable asset; then connect Agent batch evaluation, failure discovery, reporting, failure review, and Codex-driven tuning into a repeatable loop.

## Architecture

![Agent Tune Kit workflow: prepare data with atk-dataset and connect the Agent with atk-init as needed, then iterate through atk-eval → atk-diagnose → atk-report → atk-optimize → re-evaluation. Dataset and result browsers are optional. Unknown never counts as passed.](docs/assets/architecture.en.svg)

## Who It Is For

For developers with an existing local Agent who want to evaluate and tune it. To start, prepare:

- A local Agent, chatbot, tool-using Agent, or RAG Agent.
- A small evaluation dataset, preferably CSV; 5 to 20 rows are enough to start.
- Inputs, expected answers, or human-checkable results.
- A desire to let Codex help locate weak spots and tune prompts, code, parameters, or tool configuration.

## Project Value

Agent Tune Kit is not just a way to run one test. It separates Agent tuning into two clear paths:

- **Dataset Preparation**: prepare a dataset from business context, examples, or rules; preserve existing answer fields and fill grounded gaps in judgment criteria; review the dataset in local HTML; and correct expected results from human feedback. The dataset is stored under `.atk/datasets/` and is not tied to a single evaluation run.
- **Agent Evaluation and Tuning**: connect an existing Agent to a runner, run batch evaluation, find failure cases, generate an analysis report, review failures in local HTML, and let Codex tune the Agent from evidence. Each run writes to `.atk/results/vN/` so you can validate whether later versions actually improve.

This turns Agent tuning from one-off subjective trial and error into an engineering workflow with samples, results, reports, and tuning records.

## Install

Install the ATK CLI, then install its bundled Skills:

```sh
uv tool install agent-tune-kit
atk install
```

Or use `pipx`:

```sh
pipx install agent-tune-kit
atk install
```

Legacy one-shot installation remains available: `uvx --from agent-tune-kit atk install`. Skills require the persistent `atk` command on PATH.

To install from local source, update your checkout to the latest code on your chosen branch, then run from the **ATK repository root**:

```sh
uv tool install --reinstall .
atk install
```

`.` builds and installs the ATK package from the current directory; `--reinstall` includes local changes even when the version is unchanged. Rerun both commands after updating the code. `atk install` clears all ATK cache versions in the personal marketplace after installation validation succeeds, including same-version reinstalls. Failed installation preserves the cache; failed cache cleanup returns an error.

After installation, refresh the plugin list in Codex:

```text
/plugins
```

Select and enable `Agent Tune Kit`, then restart Codex and open a new session. Existing sessions do not automatically replace instructions already read; finish active ATK tasks before reinstalling.

Run `atk status` to compare installed and cached plugin contents. It reports pending Codex loading when no cache exists. Incomplete installation, stale/unreadable caches and version mismatches return exit code `1`; matching or pending caches return `0`. This does not confirm plugin enablement or activation in a session. See the [installation and cache notes](docs/skill-template-pack-usage.md#local-plugin-install-and-smoke).

## Two Core Paths

Run these commands in **your Agent project**, not in this repository.

Ideally, you already have a local Agent project that Codex can inspect and edit, plus an evaluation dataset. CSV is recommended, but column names do not need to follow a strict schema; Codex will infer inputs, expected results, and evaluation shape from the data.

### Path A: Dataset preparation (as needed)

Use one entry point to prepare or revise data; reuse suitable existing data directly:

```text
$atk-dataset Prepare data/eval.csv for evaluation. Preserve existing fields and answers; fill only grounded gaps in the judgment criteria.
```

You can also supply business examples or directly request a correction for a specific atk_id. Sample construction, grounded criteria and feedback updates happen within the same Skill as needed.

- The default artifact remains `.atk/datasets/dataset.csv`, preserving existing columns, valid IDs, row order and unrelated values.
- Reuse `expected`, `label`, `acceptance_criteria` or `ground_truth`; no mandatory canonical answer column or global answer style.
- Missing trustworthy criteria remain gaps and produce unknown judgments downstream, not invented answers.
- Call `$atk-visualize-dataset` only when human browsing helps. Existing HTML and `dataset_review.csv` exports remain supported; return feedback to `$atk-dataset`. Direct feedback needs no export.

Preparation does not run the Agent or create result versions. Continue to `$atk-init` when not connected, or `$atk-eval` with a compatible runner. Dataset/criteria corrections never rewrite historical results or prove Agent improvement.

### Path B: Agent Evaluation and Tuning

Use this loop when you already have a runnable Agent and an evaluation dataset:

```text
$atk-init My Agent entrypoint is scripts/agent.py and the evaluation dataset is data/eval.csv
$atk-eval
$atk-diagnose
$atk-report
$atk-visualize-failures
$atk-optimize
```

| Command | Purpose | Key output |
| --- | --- | --- |
| `$atk-init` | Connect an existing Agent and dataset, generate a V2 adapter with an evidence retention policy, and normalize the dataset into ATK's fixed location | `.atk/runner/eval_runner.py`, `.atk/datasets/dataset.csv` |
| `$atk-eval` | Run batch evaluation through the shared engine, allocate a result version, and retain execution evidence | `.atk/results/vN/eval_results.csv`, `run.json`, `executions/` |
| `$atk-diagnose` | Identify failures and investigate initial causes | `.atk/results/vN/failure_cases.csv`, `judgments.csv` |
| `$atk-report` | Generate the current-loop analysis report and cross-version validation when a prior loop exists | `.atk/results/vN/report.md` |
| `$atk-visualize-failures` | Generate local offline HTML for searching, filtering, and reviewing failure cases | `.atk/results/vN/failure_cases.html` |
| `$atk-optimize` | Tune prompts, code, parameters, or tool configuration from the report and failure evidence | Agent edits, `.atk/results/vN/tuning_plan.md` |

`$atk-diagnose` combines deterministic checks and Codex semantic judgment. It prepares or reuses rules internally when needed; no mode selection is required.
It keeps confirmed failures in `failure_cases.csv` and records every executed case in `judgments.csv` (passed/failed/unknown, with reasons).
Unknowns never count as passes or enter `--only-failures`. Legacy results still show known failures, but missing judgments cannot prove success.
Continue with `$atk-report` for detailed analysis and cross-version review, then `$atk-optimize`.

#### Verify Improvement

After tuning, run another loop. The common path is to rerun only the prior failures:

```text
$atk-eval --only-failures
$atk-diagnose
$atk-report
```

New results are written to a new `.atk/results/vN/`. Nonempty versions are protected; an empty highest version directory can be reused. For V2 results, `--only-failures` selects valid failed judgments from the current evaluation target and maps them back to `.atk/datasets/dataset.csv` by `atk_id`; stale judgments and unknowns are excluded. Legacy results continue to supply failure IDs from `failure_cases.csv`. Evidence-only imports do not become evaluation targets.

Starting with the second loop, when a prior `tuning_plan.md` exists and experimental conditions are comparable, `$atk-report` tells you whether the target issues were resolved, partially resolved, unresolved, or impossible to judge. Rerunning failures checks those targets, not the full-dataset pass rate; run `$atk-eval` again when a full regression check is needed.

### Local Tuning Context

ATK can optionally read local private `.atk/context.md` to preserve user-confirmed tuning objectives, Agent behavior standards, `ground_truth` judgment standards, human feedback, and tuning decisions. It is not a dataset metadata or run-log file: field names, row counts, result paths, execution metrics, and other facts recoverable from `.atk/datasets/`, `.atk/results/`, or runner files should not be recorded there.

`$atk-dataset` updates reusable standards when confirmed by the user; `$atk-diagnose`, `$atk-report` and `$atk-optimize` use them. Missing `.atk/context.md` does not block the workflow.

### Ground-Truth Review and Judge Calibration

Datasets can record provenance and valid confirmations per answer field. Changes to inputs, answers, or relevant standards make confirmations stale. `atk dataset update --review-path dataset_review.csv` can import confirmations without changing answers. `atk results calibrate` checks a rule or semantic judge against reviewed outputs without running the Agent or allocating an evaluation version.

Judgments can bind standard and judge snapshots. Rejudgments preserve history, and comparisons expose differences affecting comparability. Existing datasets remain readable without being automatically marked confirmed. See [the trust contract](docs/truth-and-assessment.md) for review records, calibration inputs, and judgment fields.

## Output Structure

```text
.atk/
├── context.md             # Optional local tuning agreements
├── datasets/
│   ├── dataset.csv        # ATK runnable dataset with atk_id
│   └── dataset.html       # Optional dataset browser
├── runner/
│   ├── eval_runner.py     # Project adapter
│   └── failure_rule.py    # Rule generated as needed
└── results/
    ├── v1/
    │   ├── eval_results.csv
    │   ├── run.json       # V2 Run manifest
    │   ├── executions/    # Independent V2 execution evidence
    │   │   └── <id>/
    │   │       ├── execution.json
    │   │       ├── input.json
    │   │       ├── output.json
    │   │       ├── context/
    │   │       ├── events.jsonl
    │   │       ├── app.log
    │   │       └── artifacts/
    │   ├── judgments.csv
    │   ├── failure_cases.csv
    │   ├── failure_cases.html
    │   ├── report.md
    │   └── tuning_plan.md
    └── v2/
        └── ...
```

Common output files:

- `eval_results.csv`: source fields plus actual `agent_output`, `log_path`, and other execution fields. ATK excludes `atk_truth` from Agent inputs; the adapter must still exclude answer columns.
- `run.json`, `executions/`: V2 Run manifest and independent execution evidence; one business Case can map to several samples. V1 results do not require these files.
- `judgments.csv`: passed/failed/unknown verdicts, reasons, and traceable references. Rejudgments archive prior records under `judgment-history/`.
- `failure_cases.csv`: rows selected as failures.
- `failure_cases.html`: optional failure review page.
- `report.md`: analysis and tuning recommendations.
- `tuning_plan.md`: what Codex changed and why.

This is a typical evaluation layout; steps produce artifacts as needed. Evidence-only imports can omit `eval_results.csv`. Interrupted or failed runs preserve available evidence without proving completion.

## Common Skills

- `$atk-dataset`: prepare or revise evaluation data while preserving fields and IDs, with feedback applied as needed.
- `$atk-visualize-dataset`: render `.atk/datasets/dataset.csv` into a local HTML browser for quickly reviewing rows and expected-result fields.
- `$atk-init`: generate the project adapter, connect the Agent, and configure evidence retention.
- `$atk-eval`: run evaluation and create a new result version.
- `$atk-diagnose`: identify failures and unknowns using rules and semantic judgment, with initial cause investigation.
- `$atk-report`: generate analysis and cross-loop validation.
- `$atk-visualize-failures`: generate the failure review HTML page.
- `$atk-optimize`: tune the Agent based on the report.

## Shared CLI capabilities

Fixed operations are maintained in the Python package; Skills retain semantic diagnosis, reporting and Agent optimization. Run these commands from the Agent project:

```sh
atk dataset prepare --source data/eval.csv
atk dataset inspect --direct --field expected --json
atk dataset update --direct --field expected --updates-path updates.csv
atk dataset update --review-path dataset_review.csv --field expected
atk eval --check --json
atk eval --limit 5 --concurrency 2
atk results inspect --json
atk results run-rule --version v1
atk results apply-judgments --version v1 --judgments-path judgments.csv --source-results .atk/results/v1/eval_results.csv
atk results compare --version v1 --ids 1 2 --json
atk results calibrate --samples-path calibration.csv --rule-path .atk/runner/failure_rule.py --output-path calibration-r1.csv --json
atk visualize dataset --no-open
atk visualize failures --version v1 --no-open
```

Read `evaluation_target.version` with `atk results inspect --json`, then replace `v1` and the corresponding paths with that version. `run-rule` needs an existing rule; `$atk-diagnose` performs semantic diagnosis. Add `--overwrite` for an existing browser page only after confirming replacement is safe.

New project adapters delegate to the shared engine. Legacy full runners can continue while all result versions use the legacy format; migrate to a managed adapter before writing alongside V2 or imported evidence Runs. Use `--help` on each command for its options.

## Local Execution Evidence

New adapters declare `ATK_MANAGED_RUNNER = True`, `ATK_ADAPTER_VERSION = 2`, and an `EVIDENCE["retain"]` callback. Each execution saves input/output, context snapshots, structured events, and artifacts under `.atk/results/vN/executions/<id>/`; multiple samples in one business Case can share an execution. Retention processes evidence before persistence. Model, tool, and other call tracing requires explicit instrumentation in the project adapter.

Existing V1 adapters remain supported; historical results are not migrated automatically.

```sh
atk results evidence --version v1 --atk-id 1 --json
atk results evidence --execution-dir ./copied-execution --json
atk results import-evidence --source ./native-run --adapter ./importer.py --json
```

`atk results inspect --json` separates `latest_run` from `evaluation_target`. Evidence-only imports do not displace the latter and cannot be scored directly. Diagnosis, reports, and evidence queries bind the evaluation version; changed evidence invalidates dependent judgments.

Importing an existing local run does not require another Agent call. Import adapters execute trusted Python without a sandbox. Identical source material reuses a verified sealed package; changed material and post-seal additions are rejected. Import does not fetch remote evidence or clean up automatically. Capture coverage, successful persistence, and business verdicts are reported separately.

See the [execution evidence documentation](docs/execution-evidence-and-tracing-plan.md) for interfaces, source limitations, recovery, and validation, and the [import adapter template](templates/.atk/runner/import_evidence.py.md) for external imports.
