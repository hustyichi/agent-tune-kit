#!/usr/bin/env python3
"""Generate a dependency-free single-file HTML browser for the ATK dataset.

Reads the non-versioned project dataset ``.atk/datasets/dataset.csv`` and renders a
single self-contained ``.atk/datasets/dataset.html`` for fast review of the dataset
content and per-row ground_truth confirmation. The page shell (HTML/CSS/JS) renders a
Dataset Visualizer-style static interface with Data List and Field Feature Analysis
tabs. The shell assets and bundled offline ECharts build live in sibling ``assets/`` as
plugin-owned files and are inlined at runtime: no project-local template, no sidecar
metadata, no external CDN, no user-installed frontend dependency, no LLM summary.

This generator never reads or writes ``.atk/results/vN``; the dataset is not versioned.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import tempfile
import webbrowser
from contextlib import suppress
from pathlib import Path
from typing import Any

DATASETS_DIR = Path(".atk/datasets")
DATASET_FILENAME = "dataset.csv"
OUTPUT_FILENAME = "dataset.html"
DATASET_PATH = DATASETS_DIR / DATASET_FILENAME

SNIPPET_MAX_CHARS = 240
PAGE_SIZES = [25, 50, 100, 250]
DEFAULT_PAGE_SIZE = 50

FACET_MAX_UNIQUE = 12
FACET_MIN_UNIQUE = 2

# Ground-truth length-outlier thresholds (deterministic, median-relative with floors).
GT_SHORT_MEDIAN_FLOOR = 20
GT_SHORT_RATIO = 0.2
GT_LONG_ABS_FLOOR = 200
GT_LONG_RATIO = 5.0

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
PAGE_TEMPLATE_NAME = "page.html"
STYLES_NAME = "styles.css"
APP_JS_NAME = "app.js"
VENDOR_ECHARTS_NAME = "vendor/echarts.min.js"

ATK_ID_FIELD_HINTS = ("atk_id", "atkid")

# Quality issue codes, display order, and Chinese-first labels.
ISSUE_ORDER = [
    "empty_gt",
    "empty_input",
    "dup_id",
    "conflict",
    "duplicate",
    "gt_too_short",
    "gt_too_long",
]
ISSUE_LABELS = {
    "empty_gt": "空 ground truth",
    "empty_input": "空输入",
    "dup_id": "重复/缺失 atk_id",
    "conflict": "冲突样本",
    "duplicate": "完全重复样本",
    "gt_too_short": "ground_truth 过短",
    "gt_too_long": "ground_truth 过长",
}

# Audit markers must remain present verbatim in the generated HTML so the plugin
# self-tests can verify capability anchors regardless of the localized UI text.
AUDIT_MARKERS = [
    "input-vs-ground_truth comparison",
    "schema-adaptive role switching",
    "auto-detected",
    "manual/unmapped",
    "dataset quality lint",
    "ground_truth confirmation",
    "client-side review export",
    "Search / filter / pagination",
    "No data rows in dataset.csv",
    "bundled offline ECharts",
]

ROLE_CANDIDATES: dict[str, list[str]] = {
    "id": ["atk_id", "case_id", "id", "row_id", "index"],
    "input": ["input", "query", "question", "prompt", "task", "user_input", "instruction", "source"],
    "expected": ["ground_truth", "expected", "expected_output", "reference", "answer", "label", "target"],
}


class UserActionRequired(RuntimeError):
    """A user-fixable input or confirmation blocker."""


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def detect_atk_id_field(fieldnames: list[str]) -> str:
    """Return the field name that holds the canonical ATK id, or empty when absent."""
    for field in fieldnames:
        if normalize_name(field) in ATK_ID_FIELD_HINTS:
            return field
    return ""


def detect_roles(fieldnames: list[str]) -> dict[str, dict[str, str]]:
    normalized = {field: normalize_name(field) for field in fieldnames}
    roles: dict[str, dict[str, str]] = {}
    used: set[str] = set()
    for role, candidates in ROLE_CANDIDATES.items():
        exact_candidates = [normalize_name(candidate) for candidate in candidates]
        chosen = ""
        for candidate in exact_candidates:
            for field, norm in normalized.items():
                if field not in used and norm == candidate:
                    chosen = field
                    break
            if chosen:
                break
        if not chosen:
            for candidate in exact_candidates:
                for field, norm in normalized.items():
                    if field not in used and candidate and candidate in norm:
                        chosen = field
                        break
                if chosen:
                    break
        if chosen:
            used.add(chosen)
            roles[role] = {"field": chosen, "source": "auto"}
        else:
            roles[role] = {"field": "", "source": "manual"}
    return roles


def parse_dataset_csv(path: Path) -> tuple[list[str], list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, strict=True)
            fieldnames = list(reader.fieldnames or [])
            if not fieldnames or all(not (name or "").strip() for name in fieldnames):
                raise UserActionRequired("dataset.csv is empty or missing a header; rebuild the dataset.")
            if any(not (name or "").strip() for name in fieldnames):
                raise UserActionRequired("dataset.csv contains blank header names; preserving columns is uncertain.")
            if len(set(fieldnames)) != len(fieldnames):
                raise UserActionRequired("dataset.csv contains duplicate headers; preserving columns is uncertain.")
            rows: list[dict[str, str]] = []
            for row_index, raw_row in enumerate(reader, start=2):
                if None in raw_row:
                    extra_values = raw_row.pop(None)
                    if extra_values:
                        warnings.append(
                            f"Row {row_index} had extra values beyond the header; stored in __extra_values."
                        )
                        raw_row["__extra_values"] = " | ".join(str(value) for value in extra_values)
                        if "__extra_values" not in fieldnames:
                            fieldnames.append("__extra_values")
                rows.append(
                    {name: "" if raw_row.get(name) is None else str(raw_row.get(name, "")) for name in fieldnames}
                )
    except UnicodeDecodeError as exc:
        raise UserActionRequired(f"Could not parse dataset.csv as UTF-8: {exc}") from exc
    except csv.Error as exc:
        raise UserActionRequired(f"Could not parse dataset.csv reliably: {exc}") from exc
    return fieldnames, rows, warnings


def detect_facets(
    fieldnames: list[str], rows: list[dict[str, str]], roles: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    """Pick low-cardinality columns suitable as faceted filters."""
    excluded_fields = set()
    for role in ("id", "input", "expected"):
        field = roles.get(role, {}).get("field", "")
        if field:
            excluded_fields.add(field)
    facets: list[dict[str, Any]] = []
    for field in fieldnames:
        if field in excluded_fields:
            continue
        counts: dict[str, int] = {}
        nonempty = 0
        for row in rows:
            value = (row.get(field) or "").strip()
            if not value:
                continue
            if "\n" in value or len(value) > 80:
                counts = {}
                nonempty = -1
                break
            counts[value] = counts.get(value, 0) + 1
            nonempty += 1
        if nonempty <= 0:
            continue
        if not (FACET_MIN_UNIQUE <= len(counts) <= FACET_MAX_UNIQUE):
            continue
        if len(counts) >= max(2, nonempty):
            continue
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        facets.append({"field": field, "values": [{"value": value, "count": count} for value, count in ordered]})
    return facets


def _normalize_value(value: str) -> str:
    return " ".join(str(value or "").split()).lower()


def _gt_length_bounds(lengths: list[int]) -> tuple[int, int]:
    """Return (short_threshold, long_threshold) for ground_truth length outliers."""
    if not lengths:
        return (0, 0)
    ordered = sorted(lengths)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 == 1 else (ordered[mid - 1] + ordered[mid]) // 2
    short_threshold = 0
    if median >= GT_SHORT_MEDIAN_FLOOR:
        short_threshold = int(median * GT_SHORT_RATIO)
    long_threshold = max(GT_LONG_ABS_FLOOR, int(median * GT_LONG_RATIO))
    return (short_threshold, long_threshold)


def compute_issues(
    rows: list[dict[str, str]], fieldnames: list[str], roles: dict[str, dict[str, str]]
) -> list[list[str]]:
    """Return per-row issue-code lists (dataset quality lint)."""
    id_field = detect_atk_id_field(fieldnames) or roles.get("id", {}).get("field", "")
    input_field = roles.get("input", {}).get("field", "")
    gt_field = roles.get("expected", {}).get("field", "")

    issues: list[list[str]] = [[] for _ in rows]

    # atk_id: missing / non-positive-int / duplicate
    if id_field:
        seen: dict[str, int] = {}
        for idx, row in enumerate(rows):
            raw = (row.get(id_field) or "").strip()
            valid = bool(re.fullmatch(r"[1-9][0-9]*", raw))
            if not valid:
                issues[idx].append("dup_id")
            else:
                seen[raw] = seen.get(raw, 0) + 1
        if seen:
            dup_values = {value for value, count in seen.items() if count > 1}
            if dup_values:
                for idx, row in enumerate(rows):
                    raw = (row.get(id_field) or "").strip()
                    if raw in dup_values and "dup_id" not in issues[idx]:
                        issues[idx].append("dup_id")

    # empty input / empty ground_truth
    if input_field:
        for idx, row in enumerate(rows):
            if not (row.get(input_field) or "").strip():
                issues[idx].append("empty_input")
    if gt_field:
        for idx, row in enumerate(rows):
            if not (row.get(gt_field) or "").strip():
                issues[idx].append("empty_gt")

    # conflict (same input, different non-empty ground_truth) + exact duplicates
    if input_field and gt_field:
        by_input: dict[str, set[str]] = {}
        pair_counts: dict[tuple[str, str], int] = {}
        for row in rows:
            in_norm = _normalize_value(row.get(input_field, ""))
            gt_norm = _normalize_value(row.get(gt_field, ""))
            if not in_norm:
                continue
            if gt_norm:
                by_input.setdefault(in_norm, set()).add(gt_norm)
            pair_counts[(in_norm, gt_norm)] = pair_counts.get((in_norm, gt_norm), 0) + 1
        for idx, row in enumerate(rows):
            in_norm = _normalize_value(row.get(input_field, ""))
            gt_norm = _normalize_value(row.get(gt_field, ""))
            if not in_norm:
                continue
            if gt_norm and len(by_input.get(in_norm, set())) > 1:
                issues[idx].append("conflict")
            if pair_counts.get((in_norm, gt_norm), 0) > 1:
                issues[idx].append("duplicate")

    # ground_truth length outliers
    if gt_field:
        lengths = [len(row.get(gt_field) or "") for row in rows if (row.get(gt_field) or "").strip()]
        short_threshold, long_threshold = _gt_length_bounds(lengths)
        for idx, row in enumerate(rows):
            value = row.get(gt_field) or ""
            if not value.strip():
                continue
            length = len(value)
            if short_threshold and length < short_threshold:
                issues[idx].append("gt_too_short")
            if length > long_threshold:
                issues[idx].append("gt_too_long")

    return issues


def enrich_rows(
    rows: list[dict[str, str]],
    fieldnames: list[str],
    roles: dict[str, dict[str, str]],
    issues: list[list[str]],
) -> list[dict[str, Any]]:
    id_field = detect_atk_id_field(fieldnames) or roles.get("id", {}).get("field", "")
    enriched: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        atk_id = (row.get(id_field) or "").strip() if id_field else ""
        enriched.append(
            {
                "rowNumber": index + 1,
                "atkId": atk_id,
                "values": row,
                "issues": issues[index],
            }
        )
    return enriched


def build_payload(
    dataset_path: Path,
    output_path: Path,
    fieldnames: list[str],
    rows: list[dict[str, str]],
    roles: dict[str, dict[str, str]],
    facets: list[dict[str, Any]],
    issues: list[list[str]],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "datasetName": dataset_path.name,
        "datasetPath": dataset_path.as_posix(),
        "output": output_path.as_posix(),
        "rowCount": len(rows),
        "fieldnames": fieldnames,
        "roles": roles,
        "facets": facets,
        "rows": enrich_rows(rows, fieldnames, roles, issues),
        "issueOrder": ISSUE_ORDER,
        "issueLabels": ISSUE_LABELS,
        "warnings": warnings,
        "config": {
            "snippetMaxChars": SNIPPET_MAX_CHARS,
            "pageSizes": PAGE_SIZES,
            "defaultPageSize": DEFAULT_PAGE_SIZE,
            "facetMaxUnique": FACET_MAX_UNIQUE,
        },
    }


def safe_json_for_html(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def load_asset(name: str) -> str:
    path = ASSETS_DIR / name
    if not path.is_file():
        raise UserActionRequired(f"Missing plugin-owned asset {path}; reinstall the atk-visualize-dataset Skill.")
    return path.read_text(encoding="utf-8")


def neutralize_script_close(text: str) -> str:
    """Prevent any literal </script in inlined CSS/JS from terminating the host script."""
    return re.sub(r"</(script)", r"<\\/\1", text, flags=re.IGNORECASE)


def render_html(payload: dict[str, Any]) -> str:
    template = load_asset(PAGE_TEMPLATE_NAME)
    styles = load_asset(STYLES_NAME)
    app_js = load_asset(APP_JS_NAME)
    vendor_js = load_asset(VENDOR_ECHARTS_NAME)
    title = f"ATK Dataset — {payload['datasetName']}"
    data_json = safe_json_for_html(payload)
    audit_markers = " | ".join(html.escape(marker) for marker in AUDIT_MARKERS)
    return (
        template.replace("__ATK_TITLE__", html.escape(title))
        .replace("__ATK_STYLES__", styles)
        .replace("__ATK_AUDIT_MARKERS__", audit_markers)
        .replace("__ATK_DATA_JSON__", data_json)
        .replace("__ATK_VENDOR_JS__", neutralize_script_close(vendor_js))
        .replace("__ATK_APP_JS__", neutralize_script_close(app_js))
    )


def write_atomic(output_path: Path, content: str) -> None:
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, output_path)
    except OSError:
        if temp_name:
            with suppress(OSError):
                Path(temp_name).unlink(missing_ok=True)
        raise


def open_in_browser(output_path: Path) -> tuple[bool, str]:
    try:
        url = output_path.resolve().as_uri()
    except (OSError, ValueError) as exc:
        return False, f"could not resolve file URI: {exc}"
    try:
        opened = webbrowser.open(url, new=2)
    except webbrowser.Error as exc:
        return False, f"webbrowser raised: {exc}"
    if not opened:
        return False, "no controlling browser available"
    return True, url


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate .atk/datasets/dataset.html from the non-versioned dataset.csv."
    )
    parser.add_argument(
        "--dataset-path",
        default=str(DATASET_PATH),
        help="dataset CSV path relative to target project cwd (default: .atk/datasets/dataset.csv)",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="replace existing dataset.html after Skill-level confirmation"
    )
    parser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        default=True,
        help="open the generated HTML in the default browser after writing (default)",
    )
    parser.add_argument(
        "--no-open",
        dest="open_browser",
        action="store_false",
        help="do not open the generated HTML after writing; useful for headless CI shells",
    )
    return parser.parse_args(argv)


def run(argv: list[str]) -> int:
    args = parse_args(argv)
    dataset_path = Path(args.dataset_path)
    if not dataset_path.exists():
        raise UserActionRequired(
            f"Dataset {dataset_path.as_posix()} is missing; run atk-build-dataset or atk-init first."
        )
    if not dataset_path.is_file():
        raise UserActionRequired(f"Dataset path {dataset_path.as_posix()} is not a file; fix the dataset.")

    output_path = dataset_path.parent / OUTPUT_FILENAME
    if output_path.exists() and not args.overwrite:
        raise UserActionRequired(
            f"Refusing to overwrite existing {output_path.as_posix()}; rerun with --overwrite after confirming."
        )

    fieldnames, rows, warnings = parse_dataset_csv(dataset_path)
    roles = detect_roles(fieldnames)
    facets = detect_facets(fieldnames, rows, roles)
    issues = compute_issues(rows, fieldnames, roles)
    payload = build_payload(dataset_path, output_path, fieldnames, rows, roles, facets, issues, warnings)
    content = render_html(payload)
    write_atomic(output_path, content)

    issue_rows = sum(1 for row_issues in issues if row_issues)
    overwrite_status = "overwrote existing HTML" if args.overwrite else "wrote new HTML"
    print(f"dataset={dataset_path.as_posix()}")
    print(f"rows={len(rows)}")
    print(f"output={output_path.as_posix()}")
    print(f"issue_rows={issue_rows}")
    print(f"facets={len(facets)}")
    print(f"overwrite={overwrite_status}")
    print(
        "features=Dataset Visualizer shell, Data List / Field Feature Analysis tabs, summary counts, "
        "search/filter, column controls, sorting, pagination, input-vs-ground_truth, role switching, "
        "dynamic facets, dataset quality lint, ground_truth confirmation, row inspector detail, "
        "client-side review export, bundled offline ECharts"
    )
    if args.open_browser:
        ok, info = open_in_browser(output_path)
        print(f"browser_open={'ok' if ok else 'skipped'} ({info})")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except UserActionRequired as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Unexpected dataset.html generation error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
