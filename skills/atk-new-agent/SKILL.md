---
name: atk-new-agent
description: Create a minimal runnable OpenAI-compatible Agent project from a dataset and user intent, then hand off to atk-init.
---

# Agent Tuning — ATK new Agent

## Purpose

Create a minimal runnable Agent project for users who have an evaluation dataset but do not yet have an Agent implementation. This Skill is a pre-init scaffold step: it inspects the source dataset, interviews the user for intent and boundaries, writes a Markdown design spec to `.atk/specs/agent_spec.md`, generates a small uv-managed Python Agent, and then hands off to `atk-init`.

This Skill does not create result versions and does not write `.atk/datasets/original.csv`. The existing `atk-init` Skill remains the only canonical owner of `.atk/datasets/original.csv`, `atk_id` normalization, and `.atk/runner/eval_runner.py` generation.

## Inputs

- A dataset path, typically CSV.
- Optional natural-language intent, task description, desired Agent behavior, expected output style, or model/runtime preference.
- User answers for human-judgment decisions that cannot be safely inferred from the dataset.
- Template files under `templates/agent/`:
  - `agent.py`
  - `run_agent.py`
  - `pyproject.toml`
  - `.env.example`
  - `README.md`

## Outputs

- `.atk/specs/agent_spec.md`, containing the user intent, dataset understanding, input fields, expected output, behavior boundaries, non-goals, OpenAI-compatible runtime, implementation plan, and ATK handoff.
- A minimal generated Agent project in the target repository root:
  - `agent.py`
  - `run_agent.py`
  - `pyproject.toml`
  - `.env.example`
  - `README.md`
- A final handoff message that names `agent.py::run_agent` and gives the recommended `atk-init` prompt.

## Workflow

1. Inspect first:
   - locate the dataset path from the user request or nearby project files;
   - read CSV headers and a small representative sample;
   - infer likely input fields, expected-output/reference fields, and task type;
   - check whether target output files already exist.
2. Interview only for user judgment:
   - ask one focused question at a time;
   - do not ask for dataset facts that can be inspected directly;
   - confirm Agent purpose, target user, expected output style, non-goals, and decision boundaries;
   - confirm OpenAI-compatible runtime assumptions when they are not already stated.
3. Write `.atk/specs/agent_spec.md` in Markdown with these exact sections:
   - `# Agent Spec`
   - `## User Intent`
   - `## Dataset Understanding`
   - `## Input Fields`
   - `## Expected Output`
   - `## Agent Behavior`
   - `## Non-Goals`
   - `## Decision Boundaries`
   - `## OpenAI-Compatible Runtime`
   - `## Initial Implementation Plan`
   - `## ATK Evaluation Handoff`
4. Generate the minimal project files from `templates/agent/`.
5. Keep the generated Agent simple:
   - expose `run_agent(input_data: dict[str, str]) -> str` from `agent.py`;
   - use the OpenAI SDK and `python-dotenv`;
   - read `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL`;
   - return a single string output;
   - fail with actionable configuration errors before any network call when required environment is missing.
6. Verify without requiring credentials or network:
   - compile generated Python files when possible;
   - run the CLI far enough to prove imports and argument parsing work;
   - if credentials are missing, treat a clear missing-configuration error as an acceptable MVP smoke result.
7. Tell the user the next command is `atk-init`, not `atk-run`.

## Confirmation triggers

Ask before writing or overwriting any of these existing files:

- `agent.py`
- `run_agent.py`
- `pyproject.toml`
- `README.md`
- `.env.example`
- `.atk/specs/agent_spec.md`

Ask before continuing if:

- the dataset path cannot be found;
- the dataset format, encoding, delimiter, headers, or sample rows cannot be inspected safely;
- input fields or expected-output fields are ambiguous enough that the generated Agent behavior would materially change;
- the user appears to expect RAG, external tools, web UI, multi-provider abstraction, or full tuning-loop orchestration in the first version;
- adding dependencies beyond `openai` and `python-dotenv` appears necessary.

Do not ask before creating missing directories such as `.atk/specs/`.

## Failure behavior

- If the dataset cannot be inspected, do not generate a misleading Agent project.
- If overwrite confirmation is required and not provided, stop before writing the conflicting files.
- If intent, expected output, or non-goals remain materially unclear after focused questioning, write no project files and explain the missing decision.
- If generated files cannot be compiled or imported, fix the generated files before handing off.
- Never write `.atk/datasets/original.csv`; that path belongs to `atk-init`.
- Never silently impose RAG, tool-use, UI, or multi-provider architecture.

## Handoff message

After generation, summarize:

- dataset path inspected;
- generated spec path `.atk/specs/agent_spec.md`;
- generated Agent entrypoint `agent.py::run_agent`;
- generated runtime command, usually `uv run python run_agent.py --input hello`;
- whether smoke verification compiled/imported successfully or stopped at an expected missing-configuration error;
- next command:

```text
$atk-init Agent 入口是 agent.py 的 run_agent，评估数据是 <dataset-path>
```
