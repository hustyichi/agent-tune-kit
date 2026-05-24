# Agent Tune Kit

English | [简体中文](README.md)

Agent Tune Kit is a **local Codex plugin** that helps you evaluate and improve the quality of your own local Agent.

If you already have a working Agent but are not sure where it fails, why it fails, or what to tune next, this project lets Codex help you run a complete loop: batch test the Agent, find failure cases, write an analysis report, tune the Agent, and verify the next run.

Its main advantage is a **low-friction start**. You do not need to design a complex evaluation schema or expose a universal Agent interface first. Bring a local Agent project and a small evaluation dataset; Codex reads the code and data samples, then generates the project-specific runner and tuning workflow.

## Who it is for

Use this if you have:

- a local Agent, chatbot, tool-using Agent, or RAG Agent;
- a few test questions, sample inputs, expected answers, or human-judgable results;
- a need to quickly find weak spots and let Codex help tune prompts, code, parameters, or tool configuration;
- a desire to keep each tuning loop traceable with result files and reports.

You do not need a full evaluation platform to start. For the first validation, 5 to 20 CSV rows are enough.

## Prerequisites

You only need:

- Codex with local plugin/Skill support.
- Python 3.
- A local Agent project that Codex can inspect and edit.
- A simple evaluation dataset, preferably CSV. Column names do not need to follow a strict Schema; Codex will infer inputs and expected results where possible.

Create a git checkpoint before tuning if you want an easy rollback path. Agent Tune Kit does not automate Agent tuning rollback; installer rollback only restores local marketplace/plugin-store install state.

## Quickstart: install the plugin

Clone the repository first, then enter the project directory:

```sh
git clone git@github.com:hustyichi/agent-tune-kit.git
cd agent-tune-kit
```

Then run the main install command:

```sh
python3 scripts/install_plugin.py install
```

The installer validates the manifest, adds the plugin to the Personal marketplace, writes or updates `~/.agents/plugins/marketplace.json`, and runs local smoke/status checks by default. It proves local files and marketplace state only; it does not bypass or modify hidden Codex UI enablement state.

Useful helper commands:

```sh
python3 scripts/install_plugin.py preview --smoke   # preview only; no writes
python3 scripts/install_plugin.py status            # read local install status and next steps
python3 scripts/install_plugin.py rollback --backup <backup-id>  # restore installer-managed local install state only
```

When an existing marketplace/plugin-store conflict is found, interactive terminals prompt before replacement. Noninteractive replacement requires `--yes --force`; destructive replacement creates a backup first and prints a rollback command. The installer supports explicit subcommands only and does not keep old entry points; use `preview` for no-write preview.

After install, Agent Tune Kit should be visible/available in `/plugins`.

You still need to enable it in Codex:

```text
/plugins
```

Select `Agent Tune Kit` in the plugin list and follow the UI prompt to install/enable it. After you enable it in the UI, `$atk-status` and the other Skill commands should appear in autocomplete.

If the plugin is enabled in `/plugins` but `$atk-status` still does not appear in the current session, that is expected: Codex usually loads plugin Skills when a session starts, so newly enabled plugins may not be hot-loaded into an already running session. Restart Codex, or close the current Codex session and reopen this project, then type `$atk-status` again to verify.

If your environment cannot use local plugins, do not split-copy individual `skills/*` directories; this repository now treats the local Codex plugin install path as the only recommended entry point.

## Minimal tuning loop

Run these steps in **your Agent repository**, not in this Agent Tune Kit repository.

### 1. Generate a test runner

Run:

```text
$atk-setup
```

Point Codex to your Agent entrypoint and evaluation dataset. Codex generates:

```text
.atk/runner/test_runner.py
```

The runner keeps your original dataset columns and adds the Agent's actual output as `agent_output`.

### 2. Run the Agent on the dataset

Run:

```text
$atk-run
```

This writes:

```text
.atk/results/v1/results.csv
```

### 3. Find failing cases

For the simplest path, let Codex judge which cases failed:

```text
$atk-find-failures
```

If you already have a clear rule, use the rule-based Skill instead:

```text
$atk-find-failures-by-rule
```

The failing cases are written to:

```text
.atk/results/v1/failure_cases.csv
```

### 4. Generate the analysis report

Run:

```text
$atk-report
```

Codex writes:

```text
.atk/results/v1/report.md
```

The report summarizes test results, failure cases, likely causes, and recommended tuning priorities.

### 5. Let Codex tune the Agent

Run:

```text
$atk-tune
```

Codex edits the Agent based on the report and records the tuning plan in:

```text
.atk/results/v1/tuning_plan.md
```

## Verify that tuning worked

After tuning, run the test again:

```text
$atk-run
```

This creates `.atk/results/v2/results.csv`. Then run:

```text
$atk-find-failures
$atk-report
```

Starting with the second loop, the report reads the previous `tuning_plan.md` and tells you whether the target failures were resolved, partially resolved, unresolved, or impossible to judge.

## Expected output

```text
.atk/
├── runner/
│   ├── test_runner.py
│   └── filter_abnormal.py
└── results/
    ├── v1/
    │   ├── results.csv
    │   ├── failure_cases.csv
    │   ├── report.md
    │   └── tuning_plan.md
    └── v2/
        └── ...
```

Most users only need to read `results.csv`, `failure_cases.csv`, and `report.md`. Version directories are managed automatically.

## Available Skills

- `$atk-status`: inspect progress and recommend the next step.
- `$atk-setup`: generate a test runner for the current Agent.
- `$atk-run`: run the test runner and create the current result version.
- `$atk-find-failures`: let Codex identify failing cases.
- `$atk-find-failures-by-rule`: identify failing cases with explicit rules.
- `$atk-report`: generate analysis and cross-loop validation.
- `$atk-tune`: tune the Agent and record the tuning plan.
