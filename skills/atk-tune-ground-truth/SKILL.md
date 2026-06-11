---
name: atk-tune-ground-truth
description: Tune `.atk/datasets/dataset.csv` ground_truth values from dataset review feedback exported by `atk-visualize-dataset`.
---

# Agent Tuning — Tune Ground Truth

## Purpose

Tune the canonical `ground_truth` values in `.atk/datasets/dataset.csv` from reviewer feedback exported by
`atk-visualize-dataset`. This is a dataset-only / pre-results Skill: it updates `.atk/datasets/dataset.csv`, but it does
not create `.atk/results/vN`, does not run the Agent or eval runner, does not run `$atk-run`, and does not write
`failure_cases.csv`.

Use this after a reviewer opens `.atk/datasets/dataset.html`, records feedback about incorrect, weak, incomplete, or
unreasonable `ground_truth`, and exports `dataset_review.csv`. Treat `review_feedback` as the user's judgment about how
the original `ground_truth` should be corrected or adjusted. Do not copy the feedback text directly into `ground_truth`
unless the feedback itself clearly states that exact desired value.

## Inputs

- Required existing dataset: `.atk/datasets/dataset.csv`.
- Required `atk_id` column with non-empty, unique positive integers.
- Review CSV from `atk-visualize-dataset` with `atk_id`, `row_number`, and `review_feedback`.
- Optional local private tuning context: `.atk/context.md`, especially `Ground Truth Standard` and prior
  `Tuning Decisions`.
- Optional explicit review path, normally unnecessary when the exported file is in `.atk/datasets/` or Downloads.

Review file resolution order:

1. Explicit review path if the user provided one.
2. `.atk/datasets/dataset_review.csv`.
3. The newest structurally valid `dataset_review*.csv` under the user's `~/Downloads` directory whose reviewed
   `atk_id` values exist in the current dataset.

Do not use fingerprint validation in this Skill. The safety check is structural validation plus `atk_id` matching
against the current `.atk/datasets/dataset.csv`.

## Outputs

- Updated `.atk/datasets/dataset.csv` with tuned `ground_truth` values.
- Optional `.atk/context.md` update when review feedback changes the confirmed `Ground Truth Standard`.
- Preserve all original columns and relative order where practical.
- If `ground_truth` is absent, append it after existing columns.
- No output under `.atk/results/`.
- No `failure_cases.csv` writing.

Do not create `candidate_dataset.csv` or another alternate dataset filename as the normal output path.

## Workflow

1. Inspect the user's request and locate `.atk/datasets/dataset.csv`.
2. Stop with repair guidance if the dataset is missing; suggest `$atk-build-dataset` or `$atk-init` as the upstream
   repair path.
3. Validate that `atk_id` exists and contains non-empty, unique positive integers.
4. Locate the review CSV:
   - use an explicit user-provided path when present;
   - otherwise prefer `.atk/datasets/dataset_review.csv`;
   - otherwise search `~/Downloads` for `dataset_review*.csv` and use the newest structurally valid file that matches
     current dataset `atk_id` values.
5. Run the bundled stdlib helper from the target project working directory to resolve and inspect the review context:

```sh
python3 <skill-dir>/scripts/tune_ground_truth.py --dump-context
```

   Pass `--review-path <path>` only when the user supplied an explicit file path. The helper prints JSON containing the
   dataset rows tied to each non-empty `review_feedback`.
6. Read `.atk/context.md` if it exists. Apply only user-confirmed `Ground Truth Standard` and `Tuning Decisions`;
   ignore dataset metadata, result paths, run logs, or generated statistics if present.
7. For each reviewed row, use Codex judgment to produce a corrected `ground_truth` by combining:
   - the original dataset row;
   - the existing `ground_truth`, if present;
   - the user's `review_feedback` as a correction or adjustment instruction.
8. Write a temporary updates CSV with headers `atk_id,ground_truth`. Include only rows where a corrected `ground_truth`
   can be generated from the feedback and row context. Leave ambiguous or under-specified rows out of the updates CSV
   and report them as unresolved.
9. Apply updates through the helper so path resolution, validation, column preservation, and atomic dataset writeback
   remain deterministic:

```sh
python3 <skill-dir>/scripts/tune_ground_truth.py --updates-path <updates.csv>
```

   Include `--review-path <path>` if the review file was explicitly selected.
10. The writeback is automatic once corrected values have been generated. Do not ask for another confirmation merely
   because existing `ground_truth` values are updated; the exported `review_feedback` is the user's correction signal
   for this Skill.
11. If review feedback changes the user-confirmed standard rather than only row values, update the `Ground Truth
    Standard` section in `.atk/context.md` and append a concise `Tuning Decisions` entry. Do not record routine review
    row counts, field names, result versions, or generated statistics there.
12. Handoff based on result freshness:
    - recommend `$atk-visualize-dataset` if the user wants to re-review the tuned dataset;
    - recommend `$atk-run` when no current evaluation exists or when existing `eval_results.csv` predates this
      ground_truth tuning;
    - recommend `$atk-find-failures` when current `eval_results.csv` already reflects the tuned dataset and the user
      wants failure discovery.

## Confirmation triggers

Ask before writing only when:

- multiple valid Downloads candidates are indistinguishable by modification time or the user explicitly asks to choose
  among them;
- an explicit review path points to a file whose reviewed `atk_id` values do not match the current dataset;
- duplicate review feedback exists for the same `atk_id`;
- the dataset has invalid `atk_id` values;
- a corrected `ground_truth` would require domain facts not present in the row, existing `ground_truth`, or
  `review_feedback`.
- review feedback conflicts with the existing `.atk/context.md` `Ground Truth Standard` and the user has not clearly
  changed the standard.

Do not ask merely because the review file came from Downloads, because existing `ground_truth` values will be updated,
or because `ground_truth` must be appended.

## Failure behavior

- Missing `.atk/datasets/dataset.csv`: leave files unchanged and suggest `$atk-build-dataset` or `$atk-init`.
- Missing review export: leave files unchanged and tell the user to export `dataset_review.csv` from
  `$atk-visualize-dataset` or pass `--review-path`.
- Missing or invalid `atk_id`: leave files unchanged and provide repair guidance for non-empty, unique positive
  integers.
- Review `atk_id` mismatch: leave files unchanged and ask for the correct review file.
- Ambiguous feedback: omit that row from the updates CSV, leave its dataset row unchanged, and report it as unresolved.
- Helper write failure: leave any existing `dataset.csv` untouched when possible and remove temporary files when safe.

## Handoff message

After updating the dataset, summarize:

- dataset path `.atk/datasets/dataset.csv`;
- review path used, including whether it came from `.atk/datasets/` or Downloads;
- local context path `.atk/context.md` when a `Ground Truth Standard` was read or updated;
- number of review rows, updated rows, and unresolved rows;
- whether `ground_truth` was appended or already existed;
- a concise sample of representative changes;
- assumptions or unresolved feedback that were left unchanged;
- next step:
  - run `$atk-visualize-dataset` to re-review the tuned dataset;
  - run `$atk-run` when no current evaluation exists or existing results predate tuning;
  - run `$atk-find-failures` when current results already reflect the tuned dataset.

## V1 boundaries

- Do not create `.atk/results/vN`.
- Do not run the Agent.
- Do not run the eval runner.
- Do not run `$atk-run`.
- Do not write `failure_cases.csv`.
- Do not create alternate canonical dataset filenames.
- Do not add fingerprint validation.
- Do not copy `review_feedback` directly into `ground_truth` unless it clearly states the exact desired value.
- Do not use `.atk/context.md` for dataset metadata, result versions, or run logs.
