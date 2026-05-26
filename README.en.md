# Agent Tune Kit

English | [简体中文](README.md)

[![PyPI](https://img.shields.io/pypi/v/agent-tune-kit.svg)](https://pypi.org/project/agent-tune-kit/)

Agent Tune Kit is a **local Codex plugin** for evaluating and tuning your own local Agent.

If you already have a working Agent but do not know where it fails, why it fails, or what to change next, Agent Tune Kit helps you run the full loop: batch test the Agent, find failure cases, generate a report, let Codex tune the Agent, and verify the next run.

## Who It Is For

Use it if you have:

- A local Agent, chatbot, tool-using Agent, or RAG Agent.
- A small evaluation dataset, preferably CSV; 5 to 20 rows are enough to start.
- Inputs, expected answers, or human-checkable results.
- A desire to let Codex help locate weak spots and tune prompts, code, parameters, or tool configuration.

## Prerequisites

You need:

- Codex with local plugin or Skill support.
- Python 3.
- A local Agent project that Codex can inspect and edit.
- A simple evaluation dataset, preferably CSV. Column names do not need to follow a strict schema; Codex will infer inputs and expected results where possible.

Create a git checkpoint before tuning so you can compare or roll back Agent changes.

## Install

Normal users do not need to clone this repository. Run:

```sh
uvx --from agent-tune-kit atk install
```

To keep the `atk` command available:

```sh
uv tool install agent-tune-kit
atk install
```

Or use `pipx`:

```sh
pipx install agent-tune-kit
atk install
```

After installation, open the plugin list in Codex:

```text
/plugins
```

Select and enable `Agent Tune Kit`. If `$atk-status` and other completions do not appear immediately after enabling, restart Codex or reopen the current project session.

## Minimal Tuning Loop

Run these commands in **your Agent project**, not in this repository.

### 1. Initialize

Tell Codex where your Agent starts and where the evaluation data lives:

```text
$atk-init My Agent entrypoint is scripts/agent.py and the evaluation dataset is data/eval.csv
```

Codex generates:

```text
.atk/runner/eval_runner.py
```

### 2. Run Evaluation

```text
$atk-run
```

Results are written to:

```text
.atk/results/v1/eval_results.csv
```

### 3. Find Failures

Let Codex judge which rows failed:

```text
$atk-find-failures
```

If you already have a clear rule, create the rule script first and then apply it:

```text
$atk-init-failure-rule rule: mark a row as failed when expected differs from agent_output
$atk-find-failures-by-rule
```

Failure cases are written to:

```text
.atk/results/v1/failure_cases.csv
```

### 4. Generate Report

```text
$atk-report
```

The report is written to:

```text
.atk/results/v1/report.md
```

It summarizes results, failure cases, likely causes, and recommended tuning priorities.

### 5. Optional: Browse Failures

```text
$atk-visualize-failures
```

This creates a local HTML page:

```text
.atk/results/v1/failure_cases.html
```

Use it to search, filter, and manually review failure cases.

### 6. Let Codex Tune the Agent

```text
$atk-tune
```

Codex edits your Agent based on the report and records the tuning plan:

```text
.atk/results/v1/tuning_plan.md
```

## Verify Improvement

After tuning, run another loop:

```text
$atk-run
$atk-find-failures
$atk-report
```

New results are written to `.atk/results/v2/`. Starting with the second loop, the report compares against the previous `tuning_plan.md` and tells you whether the target issues were resolved, partially resolved, unresolved, or impossible to judge.

## Files You Usually Need

```text
.atk/
├── runner/
│   ├── eval_runner.py
│   └── failure_rule.py
└── results/
    ├── v1/
    │   ├── eval_results.csv
    │   ├── failure_cases.csv
    │   ├── failure_cases.html
    │   ├── report.md
    │   └── tuning_plan.md
    └── v2/
        └── ...
```

Most users only need:

- `eval_results.csv`: actual Agent output for each row.
- `failure_cases.csv`: rows selected as failures.
- `failure_cases.html`: optional failure review page.
- `report.md`: analysis and tuning recommendations.
- `tuning_plan.md`: what Codex changed and why.

## Common Skills

- `$atk-status`: inspect progress and suggest the next step.
- `$atk-init`: generate the test runner.
- `$atk-run`: run evaluation and create a new result version.
- `$atk-find-failures`: let Codex identify failure cases.
- `$atk-init-failure-rule`: create or update the failure rule.
- `$atk-find-failures-by-rule`: apply the rule to identify failures.
- `$atk-report`: generate analysis and cross-loop validation.
- `$atk-visualize-failures`: generate the failure review HTML page.
- `$atk-tune`: tune the Agent based on the report.

## Troubleshooting

- `$atk-status` is missing: confirm the plugin is enabled in `/plugins`, then restart Codex or reopen the project session.
- You are not sure what to do next: run `$atk-status`.
- You want to check local install state: run `atk status`.
- You want to preview install effects first: run `atk preview --smoke`.

For contributor development, clone this repository and run `uv sync` followed by `uv run atk install`.
