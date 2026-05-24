# Agent tune kit

English | [简体中文](README.zh-CN.md)

Agent Tune Kit is a **local Codex plugin** that helps you evaluate and improve the quality of your own local Agent.

If you already have a working Agent but are not sure where it fails, why it fails, or what to tune next, this project lets Codex help you run a complete loop: batch test the Agent, find abnormal cases, write an analysis report, tune the Agent, and verify the next run.

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

Create a git checkpoint before tuning if you want an easy rollback path. Agent Tune Kit does not automate rollback.

## Quickstart: install the plugin

Clone the repository first, then enter the project directory:

```sh
git clone git@github.com:hustyichi/agent-tune-kit.git
cd agent-tune-kit
```

Then run validation and the install preview:

```sh
python3 scripts/validate_skill_pack.py
python3 scripts/install_plugin.py --dry-run --smoke
```

If the preview looks right, install it:

```sh
python3 scripts/install_plugin.py --apply --smoke
```

After installation, Codex can discover the tuning Skills. The installer writes or updates `~/.agents/plugins/marketplace.json` and keeps marketplace `source.path` as `./plugins/agent-tune-kit`.

If your environment cannot use local plugins, use the legacy copy/register path: copy or register this pack as a whole while keeping `skills/`, `templates/`, and `docs/` together.

## Fastest way to validate the flow

Run these steps in **your Agent repository**, not in this Agent Tune Kit repository.

### 1. Let Codex inspect the current state

Open your Agent project in Codex and run:

```text
atk-start
```

It tells you which step should come next. On a fresh project, it usually recommends generating the test runner.

### 2. Generate a test runner

Run:

```text
atk-setup
```

Point Codex to your Agent entrypoint and evaluation dataset. Codex generates:

```text
agent-tuning/runner/test_runner.py
```

The runner keeps your original dataset columns and adds the Agent's actual output as `agent_output`.

### 3. Run the Agent on the dataset

Run:

```text
atk-run
```

This writes:

```text
agent-tuning/results/v1/results.csv
```

### 4. Find abnormal cases

For the simplest path, let Codex judge abnormal cases:

```text
atk-filter
```

If you already have a clear rule, use the rule-based Skill instead:

```text
atk-filter-rules
```

The abnormal cases are written to:

```text
agent-tuning/results/v1/abnormal_cases.csv
```

### 5. Generate the analysis report

Run:

```text
atk-report
```

Codex writes:

```text
agent-tuning/results/v1/report.md
```

The report summarizes test results, abnormal cases, likely causes, and recommended tuning priorities.

### 6. Let Codex tune the Agent

Run:

```text
atk-apply
```

Codex edits the Agent based on the report and records the tuning plan in:

```text
agent-tuning/results/v1/tuning_plan.md
```

## Verify that tuning worked

After tuning, run the test again:

```text
atk-run
```

This creates `agent-tuning/results/v2/results.csv`. Then run:

```text
atk-filter
atk-report
```

Starting with the second loop, the report reads the previous `tuning_plan.md` and tells you whether the target failures were resolved, partially resolved, unresolved, or impossible to judge.

## One-loop cheat sheet

```text
atk-start
atk-setup
atk-run
atk-filter
atk-report
atk-apply
```

Start the next loop by running `atk-run` again.

## Expected output

```text
agent-tuning/
├── runner/
│   ├── test_runner.py
│   └── filter_abnormal.py
└── results/
    ├── v1/
    │   ├── results.csv
    │   ├── abnormal_cases.csv
    │   ├── report.md
    │   └── tuning_plan.md
    └── v2/
        └── ...
```

Most users only need to read `results.csv`, `abnormal_cases.csv`, and `report.md`. Version directories are managed automatically.

## Available Skills

- `atk-start`: inspect progress and recommend the next step.
- `atk-setup`: generate a test runner for the current Agent.
- `atk-run`: run the test runner and create the current result version.
- `atk-filter`: let Codex identify abnormal cases.
- `atk-filter-rules`: identify abnormal cases with explicit rules.
- `atk-report`: generate analysis and cross-loop validation.
- `atk-apply`: tune the Agent and record the tuning plan.

## Current scope

This repository ships as a local Codex plugin with `.codex-plugin/plugin.json`, seven Skills, reusable runner/filter templates, shared versioning rules, docs, a safe personal marketplace installer/smoke tool, and static validation.

Out of scope for this pass: no public marketplace release, no brand assets/screenshots, no one-click orchestration, no universal Schema requirement, no bundled example Agent/data fixtures, no automatic rollback or baseline restore, and no full E2E test suite.

## Included files

- `.codex-plugin/plugin.json`
- `skills/atk-start/SKILL.md`
- `skills/atk-setup/SKILL.md`
- `skills/atk-run/SKILL.md`
- `skills/atk-filter-rules/SKILL.md`
- `skills/atk-filter/SKILL.md`
- `skills/atk-report/SKILL.md`
- `skills/atk-apply/SKILL.md`
- `templates/agent-tuning/runner/test_runner.py.md`
- `templates/agent-tuning/runner/filter_abnormal.py.md`
- `docs/shared-versioning-and-confirmation.md`
- `docs/skill-template-pack-usage.md`
- `docs/codex_agent_tuning_prd.md`
- `scripts/install_plugin.py`
- `scripts/validate_skill_pack.py`

## Validate and smoke the plugin

```sh
python3 scripts/validate_skill_pack.py
git diff --check
python3 scripts/install_plugin.py --dry-run --smoke
python3 scripts/install_plugin.py --marketplace-path /tmp/agent-tune-marketplace.json --plugin-store /tmp/agent-tune-plugins --apply --smoke
```

The validator fails loudly when required Skill sections, manifest fields, installer behavior, PRD references, version helper snippets, output paths, non-goals, or tuning/report contracts are missing.
