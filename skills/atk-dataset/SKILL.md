---
name: atk-dataset
description: Prepare or revise evaluation datasets from files, examples, rules or feedback, preserving existing fields and IDs; browsing is optional and the Agent is not run.
compatibility: Requires the installed ATK CLI on PATH (dataset, eval, results and visualize commands) and a compatible project Python runtime.
---

# Agent Tuning — Prepare Dataset

## Purpose

Prepare a usable evaluation dataset through one entry point. Import or build samples, clarify only missing judgment
criteria, and apply feedback as needed. Do not require separate build, ground-truth generation or review-writeback calls.
This is dataset-only / pre-results work and may also revise data between runs. Detailed diagnosis remains in `atk-diagnose`.

## CLI dependency

Use the installed `atk` CLI from the target project cwd. Check `atk --version` and the required command's `--help` when first used or when compatibility is uncertain. If unavailable, stop and guide installation with `uv tool install agent-tune-kit`, then `atk install`; do not regenerate fixed scripts as a fallback. Installed Skill and CLI must support the same command contract. A successful `atk status` does not prove the current session loaded new instructions.

## Inputs

- Existing `.atk/datasets/dataset.csv`, a user-provided small source file, representative examples, or business rules.
- Explicit corrections in the conversation, or optional exported `dataset_review.csv` with `atk_id,row_number,review_feedback`.
- Optional `.atk/context.md`: user-confirmed objectives, behavior rules and `Ground Truth Standard`.

## Outputs

- `.atk/datasets/dataset.csv` only by default. Reuse an already suitable dataset without rewriting it.
- `atk_id` contains non-empty, unique positive integers. Preserve all valid existing IDs, row order, column names and values
  outside explicitly requested edits. IDs belong to samples, not their current row positions; never renumber existing rows.
- Keep the existing expected-answer or criteria fields (`expected`, `label`, `acceptance_criteria`, `ground_truth`, etc.).
  Do not duplicate them into a new `ground_truth` column. Add an answer field only when needed and grounded in supplied facts.
- `dataset.html` and browser-exported `dataset_review.csv` remain optional. Do not create `candidate_dataset.csv`, manifests,
  preparation reports or version directories. Update `.atk/context.md` only for a confirmed reusable standard change.

## Workflow

1. Inspect the request, existing dataset, input fields and available judgment criteria. Read local context when present.
   If existing data already meets the request, report its path and next step; do not enrich or regenerate it by default.
2. For new/imported data, preserve the source file and original business fields. Add `atk_id` only when missing, starting at
   1 for new samples. Preserve valid supplied IDs. For explicitly requested additions to existing data, assign unused IDs
   above the current maximum; do not append duplicates or change existing rows. Invalid/duplicate existing IDs need repair
   guidance rather than silent renumbering. Do not automatically merge unrelated datasets.
3. For business examples without a file, start with a small set of useful real cases: common successes, known failures and
   relevant boundary cases. Supplement only from provided rules, keeping synthetic examples distinguishable when needed.
   Do not expand volume or mine large raw production logs by default. Ask only for missing facts that affect the task.
4. Reuse existing answers and acceptance criteria. Exact answers and natural-language criteria may coexist when their
   meaning is clear; do not require a global style choice. Do not copy Agent outputs into trusted answers or invent missing
   domain facts. Missing criteria may stay blank with the gap reported: executable inputs can proceed to evaluation, and
   unsupported verdicts remain unknown in `atk-diagnose`. Never claim the whole dataset is judgeable because inputs exist.
5. For corrections, match feedback to the current sample by stable `atk_id` and inspect its input/context. Feedback describes
   a correction, not necessarily replacement answer text. Resolve only grounded changes; leave ambiguous rows unchanged.
   Use direct conversation feedback without requiring a browser export. For an exported review, verify it belongs to this
   dataset before applying it; IDs alone are not globally unique across projects. Prefer an explicit path or known project
   review. If the source of a Downloads file is unclear, ask instead of choosing solely by timestamp.
6. Apply targeted answer/criteria edits with the bundled helper below. Preserve every unrelated field, sample and original
   input. An explicit request or clear review feedback authorizes the corresponding correction; do not ask for a second
   approval just because an existing value changes. Ask before unrequested replacement or a conflicting standard change.
7. Use `atk dataset prepare --source <prepared.csv>` for canonical creation/import; reuse identical content, pass `--append` for authorized additions or `--overwrite` only for authorized replacement. When creating/importing a dataset, validate CSV structure, IDs and input/answer roles before atomic replacement. Preserve
   an existing canonical dataset unless replacement was requested; if the mapping is unchanged, reuse it. Do not send answer
   fields or ATK metadata to the Agent as input. `atk-init` handles the actual input mapping and runner connection.
8. If feedback changes the confirmed `Ground Truth Standard`, update that section of `.atk/context.md` and a concise
   `Tuning Decisions` entry. Do not use `.atk/context.md` for dataset metadata, row counts or routine run summaries.
9. Finish with the dataset path, row count, input/criteria fields, changed IDs and remaining gaps. Human browsing is optional,
   not a completion gate. No automatic Agent execution, mandatory HTML/export cycle or additional preparation report.

## Targeted writeback

For source-backed preparation and review, read plugin-root `docs/truth-and-assessment.md` (also
`../../docs/truth-and-assessment.md`). Propose required conditions, allowed variations, hard failures and unknown
conditions from supplied evidence; show source/version and unresolved conflicts alongside candidate answers.
Use `atk dataset inspect --direct --field <answer> --input-fields <business-fields...> --json` for current bindings.
Record explicit decisions using the extended review CSV and `atk dataset update --review-path <csv>`; an updates
file is optional for confirmation-only operations. Dialogue confirmation can produce this CSV without HTML.
Never fabricate reviewer identity or treat self-check as confirmation. A correction does not confirm newly generated
text. `atk_truth` holds per-answer evidence and review bindings; inspect detects stale input/answer/standard content.
Report scoped confirmation and pending/disputed/stale gaps separately. Do not manually edit the metadata column.

Use `atk dataset inspect` and `atk dataset update`; the old `scripts/update_dataset.py` is a compatibility entrypoint only.
Prepare a temporary updates CSV containing `atk_id` and the exact target answer field, with only authorized, grounded edits.
The default field is `ground_truth` for existing workflows; always select the existing answer field when it has another name.
Answer updates affect only the selected field; explicit reviews also update `atk_truth`. Empty answer updates are skipped;
do not use this helper to clear fields or edit sample IDs.

Direct feedback or grounded enrichment, without a review file:

```sh
atk dataset update --direct --field expected --updates-path <updates.csv>
```

Existing browser feedback (legacy three-column feedback stays readable; new exports add bindings):

```sh
atk dataset inspect --review-path <dataset_review.csv> --json
atk dataset update --review-path <dataset_review.csv> --field ground_truth --updates-path <updates.csv>
```

`--dataset-path` defaults to `.atk/datasets/dataset.csv`. `atk dataset inspect --direct --json` inspects it without requiring review feedback.
The helper validates IDs and feedback scope, preserves column order, and writes atomically. A no-op leaves file bytes unchanged.
Keep the temporary update file outside `.atk/datasets/`; remove only this task's disposable file after successful verification.
Do not delete user feedback, source data or existing results.

## Confirmation triggers

Ask when input meaning is unresolved, facts needed for a proposed answer are missing, review provenance is unclear,
standards conflict, or a proposed replacement exceeds the user's request. Consolidate related questions; do not ask about
inspectable fields, creating an output directory, a global answer style, or routine already-authorized corrections.

## Failure behavior

Invalid CSV or ambiguous sample identity prevents safe writeback: leave the original file unchanged and explain the repair.
A failed helper does not authorize a manual unchecked overwrite. Partial domain uncertainty leaves affected answers unchanged
or blank; it does not block unrelated valid samples. Do not turn an unknown expected result into a fabricated label.
Do not create `.atk/results/vN`. Do not run the Agent. Do not run `$atk-eval`. Do not write `failure_cases.csv`.

## Handoff message

Report what was reused/changed, path, row count, input/criteria fields and unresolved IDs/questions. Select one next step:
- Agent and compatible runner already connected: `$atk-eval` when new data should be tested.
- Agent exists but is not connected, or input fields changed: `$atk-init` using this dataset.
- No Agent exists: explain that an existing runnable Agent is required before `$atk-init`.
- User wants visual review: optional `$atk-visualize-dataset`; later feedback returns to `$atk-dataset`.

Existing result versions retain their original inputs/expected values and judgments. Never rewrite them when updating data.
Changed data/criteria require subsequent evaluation with the new data before claiming comparable results; a corrected answer
is not evidence of Agent improvement. If data is unchanged and current results already reflect it, continue to `atk-diagnose`.
