---
name: atk-visualize-dataset
description: Generate a dependency-free HTML browser for the ATK dataset to review rows and confirm ground_truth.
---

# Agent Tuning — Dataset Visualization

## Purpose

Generate `.atk/datasets/dataset.html` from the non-versioned project dataset `.atk/datasets/dataset.csv` so a
reviewer can quickly browse the generated dataset, confirm whether each row's ground_truth matches expectations, and
spot dataset quality problems such as incorrect, unreasonable, or missing ground_truth before tuning. This is an
optional, pre-init review Skill that operates only on the dataset stage; it does not read or write `.atk/results/vN`,
does not participate in result versioning, and does not change `atk-build-dataset`, `atk-init`, or any failure-finding
semantics. Generation is handled by the fixed plugin-owned stdlib script `scripts/generate_dataset_browser.py`, not by
model-time HTML synthesis, LLM summaries, or a project-local template.

The page shell (HTML/CSS/JS) is shipped as plugin-owned assets under `skills/atk-visualize-dataset/assets/`
(`page.html`, `styles.css`, `app.js`) plus the bundled offline ECharts build retained at
`skills/atk-visualize-dataset/assets/vendor/echarts.min.js` for the self-contained asset contract. All four files are
inlined into the single output HTML at generation time. These assets are plugin-owned, never copied into the user
project, and never written outside the `.atk/datasets/` directory; the final artifact remains a single self-contained
file with zero CDN, zero runtime dependencies, no sidecar files, no per-project asset copies, and no requirement for the
reviewer to install Node, React, Vite, or any frontend package.

The visual target is a static, vanilla-JS adaptation of the external `dataset-visualize` experience: prioritize the
orange/slate product feel, sticky app header, two-tab dataset browsing/field-analysis workflow, polished grid controls,
and row inspector interactions while omitting browser-upload, Gemini/AI Studio, dataset-editing, and source-level React
requirements.

## Inputs

- Dataset CSV resolved from `--dataset-path`, default `.atk/datasets/dataset.csv`.
- Required current file:
  - `.atk/datasets/dataset.csv`

No version directory is required and no other ATK files are read. The dataset is intentionally non-versioned and lives
under `.atk/datasets/`, parallel to `atk-build-dataset`.

## Outputs

- `.atk/datasets/dataset.html` only.

Do not create `dataset_summary.json`, metadata JSON, sidecar data files, package manifests, or dependency files.
Do not write outside the `.atk/datasets/` directory during normal operation. The client-side review export
(`dataset_review.csv`) is produced by the reviewer's browser via a download, not by this Skill or the generator.

## Workflow

1. Resolve the installed Skill directory and script path as `skills/atk-visualize-dataset/scripts/generate_dataset_browser.py`
   relative to this `SKILL.md`. Run the script from the target project working directory, not from the plugin directory.
2. Require the dataset CSV at `--dataset-path` (default `.atk/datasets/dataset.csv`). If it is missing, stop and tell the
   user to run `atk-build-dataset` or `atk-init` first; do not fabricate a dataset.
3. Set the output to `dataset.html` next to the dataset CSV and keep all normal output inside `.atk/datasets/`.
4. If `dataset.html` already exists and may contain user edits, ask before overwriting that HTML artifact only. After
   confirmation, rerun the script with `--overwrite`; do not ask about unrelated dataset files.
5. Invoke the fixed stdlib generator. The generator opens the freshly written HTML in the user's default browser via
   Python `webbrowser` by default; pass `--no-open` only when the user explicitly opts out (for example a headless CI
   shell). `--open` remains accepted as an explicit compatibility flag:

```sh
python3 <skill-dir>/scripts/generate_dataset_browser.py [--dataset-path .atk/datasets/dataset.csv] [--overwrite] [--open|--no-open]
```

6. Interpret exit codes: `0` means HTML was written (and, when `--open` was passed, a browser open was attempted; the
   open result is reported on the `browser_open=...` stdout line and is non-fatal); `2` means a user-action/input
   blocker such as a missing `dataset.csv`, overwrite refusal, or unreliable CSV structure; `1` means an unexpected
   generation error.
7. The script parses `dataset.csv` with Python stdlib `csv.DictReader`, preserving all source columns and tolerating
   varied datasets. Keep arbitrary schemas intact rather than assuming a universal schema.
8. The script generates dependency-free static HTML using only the Python standard library. It uses safe JSON/HTML
   embedding, including `html.escape` for directly interpolated HTML and protection for `</script>`, `<`, `>`, `&`,
   U+2028, and U+2029 in embedded data, and neutralizes any literal `</script` inside inlined CSS/JS.
9. Inline plugin-owned `assets/page.html`, `assets/styles.css`, `assets/app.js`, and `assets/vendor/echarts.min.js`
   into the single output HTML; do not add runtime/package dependencies, external CDN links, sidecar asset files, or
   per-project copies.
10. Detect schema roles without requiring them: `atk_id`, an input column, and a ground_truth/expected column from
    candidates such as `expected`, `expected_output`, `ground_truth`, `answer`, `label`, or `target`. Label role
    mappings as auto-detected or manually selected in the generated frontend, and let reviewers remap them through the
    settings drawer for this session only.
11. Surface an **input-vs-ground_truth comparison** in the detail view so reviewers can confirm whether each
    ground_truth matches expectations, with a dedicated ground_truth confirmation panel that highlights empty
    ground_truth.
12. Compute a **dataset quality lint** per row and expose it as a one-click quality filter bar and row/field summaries.
    Detected
    issues include: empty ground_truth, empty input, duplicate or missing/non-positive-integer `atk_id`, conflicting
    samples (same input but different ground_truth), exact duplicate samples, and ground_truth length outliers
    (too short / too long).
13. Render the migrated static browser shell with a Chinese-first **数据列表 / 字段特征分析** tab structure, orange/slate
    sticky header, summary cards, client-side search/filter controls, low-cardinality facet filters, column visibility
    controls, sortable/paginated table browsing with default page size 50, and a slide-in row inspector that preserves
    all source columns with empty-field folding and copy affordances.
14. Provide a **client-side review export**: per-row verdict buttons (符合预期 / 存疑 / 需修正) plus a free-text note
    persisted in the reviewer's browser `localStorage` (keyed by `atk_id`), and an export button that downloads
    `dataset_review.csv` (`atk_id`, `row_number`, `verdict`, `note`, `detected_issues`) entirely client-side. This keeps
    the artifact offline and backend-free; the export feeds back into `atk-build-dataset` to fix issues, so no new
    editing Skill is introduced.
15. Render a field-analysis tab using the static payload and vanilla JS: field role mapping, row/field/ground_truth
    KPI cards, detected issue summaries, categorical facet summaries, and per-field statistics. Do not promise a
    runtime React app, browser-upload flow, AI Studio integration, or in-browser dataset editing.
16. Write `dataset.html` atomically where practical, for example by writing a temporary file in the dataset directory
    and replacing the final path.
17. Do not create a project-local visualization template, `.atk/visualize_config.json`, LLM summaries, sidecar metadata
    JSON, package manifests, or dependency files.

## Confirmation triggers

Ask before writing only when:

- overwriting existing `dataset.html` might discard user-edited visualization notes;
- `dataset.csv` is malformed enough that preserving rows/columns is uncertain.

Do not ask merely because field names are nonstandard; the generated frontend provides schema-adaptive role switching
for temporary review mapping. Do not ask about obvious, low-risk mechanics.

## Failure behavior

- Require `.atk/datasets/dataset.csv` (or the `--dataset-path` target); if missing, stop and tell the user to run
  `atk-build-dataset` or `atk-init` first.
- If `dataset.csv` is empty but has headers, still write a valid `dataset.html` with zero data rows and a clear
  empty-state summary.
- If `dataset.csv` has blank or duplicate headers, stop with a clear message because preserving columns is uncertain.
- If HTML generation fails after a temporary file is written, remove the temporary file when safe and leave any existing
  `dataset.html` untouched.
- Never create metadata JSON, never add dependencies, and never change `atk-build-dataset` or `atk-init` behavior.

## Handoff message

After writing the visualization, summarize:

- dataset path and row count;
- output path `.atk/datasets/dataset.html`;
- the number of rows flagged by the dataset quality lint and which issue categories appeared;
- whether the HTML includes the migrated Dataset Visualizer shell, 数据列表 / 字段特征分析 tabs, summary counts,
  search/filter, column controls, pagination, input-vs-ground_truth comparison, ground_truth confirmation,
  schema-adaptive role switching, dynamic categorical facets, dataset quality lint, row inspector details,
  client-side review export, and the self-contained bundled assets;
- the `browser_open=...` line from stdout so the user knows whether the page was auto-opened (the Skill should pass
  `--open` by default; if the open attempt is skipped, tell the user to open the printed file path manually);
- the next useful step: review the dataset, export `dataset_review.csv` for any incorrect or unreasonable
  ground_truth, and run `atk-build-dataset` to fix flagged rows, or proceed to `atk-init` when the dataset looks correct.
