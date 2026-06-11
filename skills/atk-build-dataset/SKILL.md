---
name: atk-build-dataset
description: Build `.atk/datasets/dataset.csv` from user business context as a pre-init ATK dataset builder.
---

# Agent Tuning — Build Dataset

## Purpose

Build a small, coverage-oriented ATK evaluation dataset from the user's actual business context before `$atk-init`
generates the runner. This is a pre-init dataset builder: it may author `.atk/datasets/dataset.csv`, but it does not
create `.atk/runner/eval_runner.py`, does not create `.atk/results/vN`, and does not participate in result versioning.

The goal is quality before volume. Prefer 10-30 high-value rows that expose useful tuning behavior over large-scale
default synthetic expansion. The dataset should focus on the user's input fields, expected output or acceptance
standard, and the scenarios most likely to reveal Agent weaknesses.

## Inputs

- User-provided business context, such as a natural-language description, example inputs and desired outputs, process
  rules, acceptance criteria, partial tables, or a small existing file.
- Optional user instructions about desired columns, expected output style, scenarios, risks, or dataset size.
- Optional local private tuning context: `.atk/context.md`, only for user-confirmed objectives, behavior standards, or
  `ground_truth` standards that should shape dataset examples.
- Existing target-project files only when needed to understand current `.atk/` state or avoid overwriting data.

## Outputs

- `.atk/datasets/dataset.csv`

The CSV must include:

- `atk_id` with non-empty, unique positive integers;
- at least one clear input column;
- at least one expected output or acceptance standard, or human-reviewable outcome column when the user provided enough
  evidence to define one safely;
- dynamic business columns based on the user's context.

Do not infer or invent a canonical `ground_truth` column by default. Only write `ground_truth` when the user explicitly
provides `ground_truth`, a correct-result label, exact expected answers, or a clear judgment policy for what counts as
correct. If the user only provides a business domain or input examples, ask for the missing correct-result semantics or
write non-canonical helper columns such as `expected`, `acceptance_criteria`, or `notes` only when they are grounded in
the user's provided facts.

Optional helper columns such as `scenario`, `priority`, and `notes` may be added when they make the dataset easier to
review. They are not a strict global business schema, and they should not be described as mandatory for every project.

Do not create `candidate_dataset.csv` or another alternate dataset filename. Do not automatically merge or append to
an existing dataset.

Production-log parsing is not supported in the first version. If the user has logs, ask for summarized examples or a
small curated sample rather than trying to parse raw production logs.

## Workflow

1. Inspect the user's request and any referenced small input files.
2. If `.atk/context.md` exists, read it as optional tuning guidance. Use only durable standards and decisions from it;
   do not treat it as a source of dataset headers, field types, row counts, or run history.
3. Check whether `.atk/datasets/dataset.csv` already exists.
   - If it exists, stop and ask before overwriting.
   - Do not silently merge, append, rename, or create a candidate file.
4. Determine whether the dataset can be built safely:
   - identify the Agent input field or fields;
   - identify any user-provided expected output, acceptance standard, or correct-result judgment policy;
   - decide whether a canonical `ground_truth` column is explicitly authorized; if not, do not create one;
   - identify key business scenarios or risks;
   - detect whether the request describes multiple incompatible Agent tasks;
   - detect whether generated examples would require domain facts not provided by the user.
5. If required meaning is unclear, ask 1-3 targeted questions before writing. Prioritize:
   - input fields are unclear;
   - expected-output semantics are unclear; expected-output or correct-result semantics are unclear;
   - key scenarios or risks cannot be inferred safely.
6. Generate a compact dataset that covers, unless the user narrows scope:
   - main successful flow;
   - boundary input;
   - missing or ambiguous information;
   - refusal, uncertainty, or unsupported request;
   - output format constraint;
   - business risk when provided by the user.
7. Write `.atk/datasets/dataset.csv` with stable `atk_id` values starting at `1`, unless the user supplied valid
   unique positive integers that should be preserved.
8. Keep column names practical and business-specific. For a simple chatbot, `input` and `expected` may be enough.
   For structured tasks, use columns such as `question`, `user_type`, `order_status`, or other names that match the
   user's description. Use `ground_truth` only when the user explicitly supplied or requested that canonical semantics.
9. Do not write `.atk/context.md` from this Skill. If dataset construction reveals a useful standard, mention it as a
   suggested context note in the handoff instead of silently persisting it.
10. Determine the next-step handoff based on whether an Agent implementation is already available:
   - if an Agent exists, tell the user to run `$atk-init` to initialize batch evaluation with the new dataset;
   - if no Agent exists, tell the user to run `$atk-new-agent` to create an Agent from the dataset first;
   - if Agent existence cannot be confirmed, explain both possible next steps instead of choosing one.

## Confirmation triggers

Ask before writing when:

- `.atk/datasets/dataset.csv` already exists and would be overwritten;
- input fields are unclear;
- expected-output semantics are unclear; expected-output or correct-result semantics are unclear;
- a `ground_truth` column would require guessing the correct answer or judgment policy;
- the user describes multiple incompatible Agent tasks;
- generated examples would require domain facts not provided by the user;
- the requested size or coverage conflicts with quality-first dataset construction.

Do not ask about obvious, low-risk mechanics such as creating `.atk/datasets/` when the dataset file does not exist.

## Failure behavior

- If the user does not provide enough information to identify at least one input column and one expected output or
  acceptance standard, ask a targeted clarification question and do not write a misleading dataset.
- If a requested or likely `ground_truth` value would require guessing the expected correct result, do not write it.
  Ask for explicit answers, acceptance criteria, or a judgment policy, or recommend `$atk-build-ground-truth` after the
  dataset exists.
- If the user declines overwriting an existing `.atk/datasets/dataset.csv`, leave it unchanged and explain that the
  first version has no automatic merge or append support.
- If the request depends on unavailable domain facts, ask for those facts or narrow the dataset to examples that can
  be grounded in the provided context.
- If the user asks for large-scale default synthetic expansion, explain that the first version is designed for small,
  high-value diagnostic datasets and ask for an explicit target size before going beyond the default range.
- If the user asks to parse production logs, explain that production-log parsing is not supported in the first version
  and request summarized examples or a small curated sample instead.

## Handoff message

After writing the dataset, summarize:

- output path `.atk/datasets/dataset.csv`;
- row count and generated `atk_id` behavior;
- input and expected-output columns;
- whether `ground_truth` was omitted because no explicit correct-result semantics were provided, or included because
  the user explicitly supplied the correct-result policy;
- local context standards applied, if `.atk/context.md` existed;
- any suggested context note that the user may want future ground-truth or tuning steps to preserve;
- coverage categories included;
- any assumptions or unfilled domain facts;
- optional review step: run `$atk-visualize-dataset` to open a local HTML browser of `.atk/datasets/dataset.csv` for
  quickly inspecting rows and spotting missing or questionable expected-result fields before initializing evaluation;
  if the dataset needs canonical `ground_truth`, run `$atk-build-ground-truth` with explicit correct-result semantics;
- next step based on Agent availability:
  - if an Agent exists: run `$atk-init`, using the newly created `.atk/datasets/dataset.csv` to initialize batch
    evaluation;
  - if no Agent exists: run `$atk-new-agent` to create an Agent from `.atk/datasets/dataset.csv`, then continue to
    `$atk-init`;
  - if Agent existence is unclear: mention both options and ask the user to choose the one that matches their project.
