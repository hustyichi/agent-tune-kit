---
name: atk-visualize-failures
description: Generate a dependency-free current-version HTML browser for Agent Tune Kit failure cases.
---

# Agent Tuning — Failure Case Visualization

## Purpose

Generate `.atk/results/vN/failure_cases.html` from the current version's `failure_cases.csv` so reviewers can inspect concrete failures in a modern, searchable single-file browser before tuning. This Skill is an independent optional review stage; it must not be folded into `atk-report` and does not change `atk-report`, `atk-tune`, or failure-finding semantics. Generation is handled by the fixed plugin-owned stdlib script `scripts/generate_failure_browser.py`, not by model-time HTML synthesis, LLM summaries, or a project-local template.

The page shell (HTML/CSS/JS) is shipped as plugin-owned assets under `skills/atk-visualize-failures/assets/` (`page.html`, `styles.css`, `app.js`) and is inlined into the single output HTML at generation time. These assets are plugin-owned, never copied into the user project, and never written outside the current `.atk/results/vN/` directory; the final artifact remains a single self-contained file with zero CDN, zero runtime dependencies, and no sidecar files.

This Skill maps to `docs/codex_agent_tuning_prd.md` sections 2.4, 2.5, 4, and 7, plus the shared current-version rules in `docs/shared-versioning-and-confirmation.md`.

## Inputs

- Current version directory resolved from `.atk/results/vN`.
- Required current file:
  - `failure_cases.csv`
- Optional same-version file:
  - `report.md`
- Shared rules in `docs/shared-versioning-and-confirmation.md`.

## Outputs

- Current `.atk/results/vN/failure_cases.html` only.

Do not create `report_summary.json`, metadata JSON, sidecar data files, package manifests, or dependency files. Do not write outside the resolved current version directory during normal operation.

## Workflow

1. Resolve the installed Skill directory and script path as `skills/atk-visualize-failures/scripts/generate_failure_browser.py` relative to this `SKILL.md`. Run the script from the target project working directory, not from the plugin directory.
2. Resolve the current version using `resolve_current_version()` with `RESULTS_DIR = Path(".atk/results")`. The script's default `--results-dir .atk/results` is relative to the target project cwd.
3. Require the failure CSV with `require_current_file(current_dir, "failure_cases.csv")`. Do not ask the user for a version and do not fall back to an older version when the current file is missing.
4. Set `output_path = current_dir / "failure_cases.html"` and keep all normal output same-version local.
5. If `failure_cases.html` already exists and may contain user edits, ask before overwriting that HTML artifact only. After confirmation, rerun the script with `--overwrite`; do not ask about unrelated current-version files.
6. Invoke the fixed stdlib generator. The Skill must pass `--open` by default so the freshly written HTML is opened in the user's default browser via Python `webbrowser`; omit `--open` only when the user explicitly opts out (for example a headless CI shell):

```sh
python3 <skill-dir>/scripts/generate_failure_browser.py [--overwrite] [--results-dir .atk/results] [--no-report] [--open]
```

7. Interpret exit codes: `0` means HTML was written (and, when `--open` was passed, a browser open was attempted; the open result is reported on the `browser_open=...` stdout line and is non-fatal); `2` means a user-action/input blocker such as missing current `failure_cases.csv`, overwrite refusal, or unreliable CSV structure; `1` means an unexpected generation error.
8. The script parses `failure_cases.csv` with Python stdlib `csv.DictReader`, preserving all source columns and tolerating varied datasets. Keep arbitrary schemas intact rather than assuming a universal schema.
9. The script generates dependency-free static HTML using only the Python standard library. It must use safe JSON/HTML embedding, including `html.escape` for directly interpolated HTML and protection for `</script>`, `<`, `>`, `&`, U+2028, and U+2029 in embedded data.
10. Inline plugin-owned `assets/page.html`, `assets/styles.css`, and `assets/app.js` into the single output HTML; do not add runtime/package dependencies, external CDN links, sidecar asset files, or per-project copies. The generator must neutralize any literal `</script` inside inlined CSS/JS so the host page cannot be terminated by user data.
11. Include summary counts, visible current version/path context, a client-side search/filter control, pagination with default page size 50, expected-vs-actual comparison with a line-level LCS diff (split/inline/raw modes), schema-adaptive role switching surfaced through a settings drawer, dynamic categorical facet filters auto-detected from low-cardinality columns (between 2 and 12 distinct non-empty values, skipping role-mapped free-text fields), light client-side syntax highlight for code-like fields, expandable/detail rows for long fields with empty-field folding and a copy button, and a Chinese-first UI tuned to the typical reviewer.
12. Prioritize likely review columns when present, without requiring them: expected/expected_output, `agent_output`, failure/failure_reason/explanation/root-cause-like columns, stable IDs such as case_id/id/merge_id, input/query/task columns, and `agent_output_log_path`. Label role mappings as auto-detected or manually selected in the generated frontend, and let reviewers remap them through the settings drawer for this session only.
13. Preserve all source columns in the detail view so no failure evidence is lost; sort non-empty fields first, fold empty fields under a count, and never drop or rename source columns.
14. Best-effort parse optional same-version `report.md` for bounded excerpts only: read at most 256 KiB, include at most 5 excerpts, and cap each excerpt at 800 characters. Link or reference `report.md` rather than embedding the full report body.
15. If `report.md` is missing, malformed, too large to parse safely, or unparseable, continue and note in the HTML that report context was skipped. Report parsing is optional and non-blocking.
16. Emit safe relative log links only for trusted same-version relative paths such as `logs/row_000001.log`; keep unsafe absolute URLs, scheme-relative URLs, parent traversal, URL-encoded traversal, backslashes, or Windows drive paths as non-clickable evidence text.
17. Write `failure_cases.html` atomically where practical, for example by writing a temporary file in `current_dir` and replacing the final path.
18. Do not create a project-local visualization template, `.atk/visualize_config.json`, LLM summaries, sidecar metadata JSON, package manifests, or dependency files.

## Shared version rules

Use the canonical helper names and semantics from `docs/shared-versioning-and-confirmation.md`:

- `RESULTS_DIR = Path(".atk/results")`
- `resolve_current_version(results_dir=RESULTS_DIR)`
- `require_current_file(current_dir, filename)`

Current version means the numerically largest existing `.atk/results/vN` directory. This Skill reads current `failure_cases.csv`, optionally reads same-version `report.md`, and writes current `failure_cases.html`; it never creates a new version and never falls back to an older version.

## Confirmation triggers

Ask before writing only when:

- overwriting existing `failure_cases.html` might discard user-edited visualization notes;
- `failure_cases.csv` is malformed enough that preserving rows/columns is uncertain.

Do not ask merely because same-version `report.md` is missing or unparseable; that context is best-effort and non-blocking. Do not ask merely because field names are nonstandard; the generated frontend provides schema-adaptive role switching for temporary review mapping.

## Failure behavior

- Require current `failure_cases.csv`; if missing, stop and tell the user to run `atk-find-failures` or `atk-find-failures-by-rule` first.
- If `failure_cases.csv` is empty but has headers, still write a valid `failure_cases.html` with zero failure rows and a clear empty-state summary.
- If optional `report.md` is missing, malformed, too large, or unparseable, continue and explicitly note that report context was skipped.
- If HTML generation fails after a temporary file is written, remove the temporary file when safe and leave any existing `failure_cases.html` untouched.
- Never create metadata JSON, never add dependencies, and never change `atk-report` behavior.

## Handoff message

After writing the visualization, summarize:

- current version and row count;
- output path `.atk/results/vN/failure_cases.html`;
- whether optional `report.md` context was included or skipped;
- whether the HTML includes summary counts, search/filter, pagination, expected-vs-actual comparison with line diff, schema-adaptive role switching, dynamic categorical facets, light syntax highlight, and expandable/detail rows;
- the `browser_open=...` line from stdout so the user knows whether the page was auto-opened (the Skill should pass `--open` by default; if the open attempt is skipped, tell the user to open the printed file path manually);
- whether the next useful review step is `atk-tune` after inspecting the failures.
