---
name: atk-new-agent
description: Create a minimal runnable OpenAI-compatible Agent project from a dataset and user intent, then hand off to atk-init.
---

# Agent Tuning — ATK new Agent

## Purpose

Create a minimal runnable Agent project for users who have an evaluation dataset but do not yet have an Agent implementation. This Skill is a pre-init scaffold step: it inspects the source dataset, builds a risk-aware inference brief, asks only for user judgment that would materially change Agent behavior, writes a Markdown design spec to `.atk/specs/agent_spec.md`, generates a small uv-managed Python Agent, and then hands off to `atk-init`.

This Skill does not create result versions and does not write `.atk/datasets/original.csv`. The existing `atk-init` Skill remains the only canonical owner of `.atk/datasets/original.csv`, `atk_id` normalization, and `.atk/runner/eval_runner.py` generation.

## Inputs

- A dataset path, typically CSV.
- Optional natural-language intent, task description, desired Agent behavior, expected output style, or model/runtime preference.
- User answers for high-impact human-judgment decisions that cannot be safely inferred from the dataset.
- Template files under plugin-root-relative `templates/agent/`:
  - `templates/agent/agent.py`
  - `templates/agent/run_agent.py`
  - `templates/agent/pyproject.toml`
  - `templates/agent/.env.example`
  - `templates/agent/README.md`

## Shared asset resolution

Template paths are plugin-root-relative, not relative to this Skill directory. Locate the plugin root by walking upward from this `SKILL.md` until finding `.codex-plugin/plugin.json`, `skills/`, and `templates/`.

Then read these files from that plugin root:

- `templates/agent/agent.py`
- `templates/agent/run_agent.py`
- `templates/agent/pyproject.toml`
- `templates/agent/.env.example`
- `templates/agent/README.md`

In the current package layout, these are also reachable from this Skill file as `../../templates/agent/...`, but plugin-root discovery is the preferred resolution rule.

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
2. Build a risk-aware inference brief before asking anything:
   - summarize inferred task goal, likely input fields, likely expected-output/reference fields, expected output shape, non-goals, decision boundaries, and runtime assumptions;
   - classify each inference as one of `dataset-derived`, `user-provided`, or `default assumption`;
   - identify high-impact uncertainty: any unresolved assumption about task goal, output shape, success criteria, non-goals, decision boundaries, external knowledge, tools, or dependencies that could materially change generated Agent behavior.
3. Ask only risk-triggered confirmation questions:
   - ask one focused question at a time;
   - do not ask for dataset facts that can be inspected directly;
   - do not ask for routine approval to proceed when no high-impact uncertainty exists;
   - ask only when an answer would materially change Agent purpose, output style, success criteria, non-goals, decision boundaries, external knowledge/tool use, or dependency choices;
   - if no risk-triggered confirmation is needed, continue automatically and record `No risk-triggered confirmation needed` in the handoff summary.
4. Write `.atk/specs/agent_spec.md` in Markdown with these exact sections:
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
5. In the spec, make the basis of each major decision explicit:
   - mark facts that came from inspected dataset headers/sample rows as dataset-derived;
   - mark decisions answered by the user as user-confirmed;
   - mark safe defaults as default assumptions;
   - do not present default assumptions as user intent.
6. Generate the minimal project files from plugin-root-relative `templates/agent/`.
7. Keep the generated Agent simple:
   - expose `run_agent(input_data: dict[str, str]) -> str` from `agent.py`;
   - use the OpenAI SDK and `python-dotenv`;
   - read `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL`;
   - return a single string output;
   - fail with actionable configuration errors before any network call when required environment is missing.
8. Verify without requiring credentials or network:
   - compile generated Python files when possible;
   - run the CLI far enough to prove imports and argument parsing work;
   - if credentials are missing, treat a clear missing-configuration error as an acceptable MVP smoke result.
9. Tell the user the next command is `atk-init`, not `atk-run`.

## Risk-triggered confirmation policy

Default to automatic progress after inspecting the dataset. Do not create a brainstorming-style approval gate and do not ask the user to confirm obvious, low-risk, or inspectable facts.

Ask a concise question only when the missing answer could make the generated Agent meaningfully wrong. High-impact uncertainty includes:

- task goal ambiguity, such as whether the Agent should classify, extract, answer, rewrite, rank, or critique;
- output shape ambiguity, such as plain text versus JSON, labels only versus explanation plus label, or strict formatting requirements;
- success criteria ambiguity, such as exact-match behavior, semantic similarity, rubric-like scoring, or using a reference column only as loose guidance;
- non-goal or boundary ambiguity, such as whether the Agent may use model world knowledge beyond the row, whether it must refuse unsupported claims, or whether it should avoid explanations;
- external capability ambiguity, such as RAG, web access, tool use, UI behavior, multi-provider abstraction, or dependencies beyond `openai` and `python-dotenv`;
- multiple plausible input or expected-output fields where choosing the wrong field would change the task.

Do not ask when:

- the answer is directly inspectable from the dataset path, headers, sample rows, existing files, or user prompt;
- the decision only affects routine file creation, template copying, directory creation, import checks, or missing credential smoke behavior;
- the default OpenAI-compatible runtime is sufficient and the user did not request another runtime;
- the only remaining uncertainty is low-impact wording that can be recorded as a default assumption in `.atk/specs/agent_spec.md`.

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
- task goal, output shape, success criteria, non-goals, or decision boundaries are ambiguous enough that the generated Agent could be meaningfully wrong;
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
