---
name: atk-build-ground-truth
description: Enrich an existing `.atk/datasets/dataset.csv` with a canonical `ground_truth` column as a dataset-only, pre-results ATK Skill.
---

# Agent Tuning — Build Ground Truth

## Purpose

Enrich an existing ATK canonical dataset with a dataset-wide-consistent `ground_truth` column before result generation. This is a dataset-only / pre-results Skill: it updates `.atk/datasets/dataset.csv`, but it does not create `.atk/results/vN`, does not run the Agent or eval runner, does not run `$atk-run`, does not write `failure_cases.csv`, and does not change `atk-find-failures` behavior in v1.

Use this when the user already has an ATK dataset but failure judgment is unstable because expected-result semantics are missing, inconsistent, or too weak. The goal is to make downstream evaluation review clearer while preserving ATK's schema-adaptive dataset design.

## Inputs

- Required existing dataset: `.atk/datasets/dataset.csv`.
- Required `atk_id` column with non-empty, unique positive integers.
- Optional user-provided business context, acceptance rules, examples, or small curated samples.
- Optional expected-like source columns such as `expected`, `expected_output`, `label`, `answer`, `target`, `acceptance_criteria`, `notes`, or other business-specific columns.

Do not treat `ground_truth` as a universal requirement for every ATK dataset. It is the canonical output column when this Skill is used.

## Outputs

- Updated `.atk/datasets/dataset.csv` with canonical `ground_truth` values.
- Preserve all original columns and relative order where practical.
- If `ground_truth` is absent, append it after existing columns.
- No output under `.atk/results/`.
- No `failure_cases.csv` writing.

Do not create `candidate_dataset.csv` or another alternate dataset filename as the normal output path.

## Workflow

1. Inspect the user's request and locate `.atk/datasets/dataset.csv`.
2. Stop with repair guidance if the dataset is missing; suggest `$atk-build-dataset` or `$atk-init` as the upstream repair path.
3. Validate that `atk_id` exists and contains non-empty, unique positive integers. If invalid, stop and explain how to repair the canonical dataset contract before enrichment.
4. Inspect headers and representative rows to identify input fields, expected-like source fields, existing `ground_truth` values, and any business context columns.
5. Ask the user to select one global ground-truth style before generating values:
   - exact answer; or
   - natural-language acceptance criteria.
6. Determine whether safe generation is possible:
   - enough row context exists to avoid inventing missing domain facts;
   - rows describe one compatible Agent task rather than multiple incompatible tasks;
   - the selected style can be applied consistently across the dataset;
   - existing expected-like columns can be used without unconfirmed semantic replacement.
7. Generate row-level `ground_truth` values following the selected global or dataset-wide style.
8. Before writing, present a candidate modification summary and ask for explicit confirmation if writing would overwrite existing `ground_truth`, normalize existing `ground_truth`, or semantically replace expected-like fields. For large datasets, include affected row counts and representative examples instead of every changed row.
9. Write `.atk/datasets/dataset.csv` only after required confirmations are satisfied.
10. Handoff based on result freshness:
    - recommend `$atk-run` if evaluation has not been run yet or if existing `eval_results.csv` predates dataset enrichment;
    - recommend `$atk-find-failures` if current `eval_results.csv` already reflects the enriched dataset and the user wants failure discovery.

## Confirmation triggers

Ask before writing when:

- the global `ground_truth` style is not selected;
- exact-answer versus natural-language acceptance criteria style is ambiguous;
- existing `ground_truth` values would be overwritten or normalized;
- existing expected-like columns conflict with generated values;
- expected-like fields would be semantically replaced;
- dataset rows imply multiple incompatible Agent tasks;
- required domain facts are absent;
- generated values would mix exact answer and acceptance criteria styles.

Do not ask about routine creation of the `ground_truth` column when the dataset is valid, the user selected a global style, and no overwrite or semantic replacement risk is present.

## Failure behavior

- Missing `.atk/datasets/dataset.csv`: leave files unchanged and suggest `$atk-build-dataset` or `$atk-init`.
- Missing or invalid `atk_id`: leave files unchanged and provide repair guidance for non-empty, unique positive integers.
- Ambiguous style: ask one targeted question; do not infer silently.
- Unsafe overwrite or semantic replacement: leave the dataset unchanged unless the user explicitly confirms after reviewing the candidate modification summary.
- Multiple incompatible tasks: ask the user to split the dataset or choose the task boundary before enrichment.
- Missing domain facts: ask for the missing facts or limit `ground_truth` generation to rows grounded in provided context.
- Large raw logs: ask for summarized examples or a small curated sample instead of mining production logs automatically.

## Handoff message

After updating the dataset, summarize:

- output path `.atk/datasets/dataset.csv`;
- number of rows enriched and `atk_id` validation outcome;
- selected global ground-truth style;
- source columns used to derive `ground_truth`;
- whether existing `ground_truth` or expected-like semantics were overwritten, normalized, or preserved;
- candidate modification summary evidence when confirmation was required, including affected row counts and representative examples;
- assumptions, missing domain facts, or rows left unchanged;
- next step:
  - run `$atk-run` when no current evaluation exists or when existing `eval_results.csv` predates dataset enrichment;
  - run `$atk-find-failures` when current `eval_results.csv` already reflects the enriched dataset and the user wants failure discovery.

## V1 boundaries

- Do not create `.atk/results/vN`.
- Do not run the Agent.
- Do not run the eval runner.
- Do not run `$atk-run`.
- Do not write `failure_cases.csv`.
- Do not change `atk-find-failures` behavior in v1.
- Do not silently mix exact-answer and natural-language acceptance-criteria styles.
- Do not make `ground_truth` a universal schema requirement for all ATK workflows.
