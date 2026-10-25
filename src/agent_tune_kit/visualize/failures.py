#!/usr/bin/env python3
"""Generate a dependency-free single-file HTML browser for current ATK failure cases.

The page shell (HTML/CSS/JS) lives in sibling ``assets/`` as plugin-owned files and
is inlined into a single ``.atk/results/vN/failure_cases.html`` at runtime. No
project-local template, no sidecar metadata, no external CDN, no LLM summary.
"""

from __future__ import annotations

import csv
import html
import re
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from agent_tune_kit.errors import UserActionRequired
from agent_tune_kit.results.versions import require_current_file, resolve_current_version
from agent_tune_kit.utils.files import write_text_atomic as write_atomic

from .fields import detect_atk_id_field, normalize_name
from .fields import detect_roles as _detect_roles
from .history import (
    _safe_read_text,
    build_history,
    read_judgments,
    read_report_context,
)
from .render import neutralize_script_close, open_in_browser, safe_json_for_html

RESULTS_DIR = Path(".atk/results")
FAILURE_FILENAME = "failure_cases.csv"
EVAL_FILENAME = "eval_results.csv"
TUNING_PLAN_FILENAME = "tuning_plan.md"
REPORT_FILENAME = "report.md"
OUTPUT_FILENAME = "failure_cases.html"
REPORT_MAX_BYTES = 256 * 1024
REPORT_MAX_EXCERPTS = 5
REPORT_EXCERPT_MAX_CHARS = 800
SNIPPET_MAX_CHARS = 240
PAGE_SIZES = [25, 50, 100, 250]
DEFAULT_PAGE_SIZE = 50

FACET_MAX_UNIQUE = 12
FACET_MIN_UNIQUE = 2

# Bounds for cross-version history reading to avoid runaway memory on huge datasets.
HISTORY_FAILURE_MAX_BYTES = 64 * 1024 * 1024
HISTORY_EVAL_MAX_BYTES = 64 * 1024 * 1024
HISTORY_TUNING_PLAN_MAX_BYTES = 256 * 1024
HISTORY_PERSISTENT_MIN_FAILS = 2
REASON_VALUE_MAX_CHARS = 80
REASON_TOP_N = 12

ASSETS_DIR = resources.files("agent_tune_kit.visualize").joinpath("assets", "failures")
PAGE_TEMPLATE_NAME = "page.html"
STYLES_NAME = "styles.css"
APP_JS_NAME = "app.js"

# Audit markers must remain present verbatim in the generated HTML so the plugin
# self-tests can verify capability anchors regardless of the localized UI text.
AUDIT_MARKERS = [
    "dataset-visualize shell",
    "Agent 评测结果可视化",
    "stat-card-abnormal",
    "field feature analysis",
    "JSON/code enhanced inspector",
    "failure_cases_review.csv export",
    "expected-vs-actual evidence fields",
    "schema-adaptive role switching",
    "auto-detected",
    "manual/unmapped",
    "Bounded report.md context",
    "Search / filter / pagination",
    "No failure rows in current failure_cases.csv",
    "not clickable because it is outside the safe relative path contract",
    "single-file offline static HTML",
]


ROLE_CANDIDATES: dict[str, list[str]] = {
    "id": ["case_id", "failure_id", "id", "source_index", "row_id", "index", "merge_id"],
    "input": ["input", "query", "question", "prompt", "task", "user_input", "instruction", "source", "base"],
    "expected": ["expected", "expected_output", "reference", "ground_truth", "answer", "label", "target"],
    "actual": ["agent_output", "actual", "actual_output", "output", "response", "prediction", "result"],
    "reason": [
        "failure_reason",
        "failure",
        "reason",
        "explanation",
        "root_cause",
        "root-cause",
        "error",
        "analysis",
        "error_message",
        "errot_message",
        "error_type",
        "agent_output_error_message",
    ],
    "log": ["agent_output_log_path", "log_path", "logs", "log", "trace_path"],
}

REPORT_KEYWORDS = (
    "summary",
    "failure",
    "root",
    "cause",
    "tuning",
    "priority",
    "recommend",
    "摘要",
    "异常",
    "失败",
    "原因",
    "根因",
    "优先",
    "调优",
)


def parse_csv_file(path: Path, filename_for_errors: str) -> tuple[list[str], list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, strict=True)
            fieldnames = list(reader.fieldnames or [])
            if not fieldnames or all(not (name or "").strip() for name in fieldnames):
                raise UserActionRequired(
                    f"{filename_for_errors} is empty or missing a header; run failure finding again."
                )
            if any(not (name or "").strip() for name in fieldnames):
                raise UserActionRequired(
                    f"{filename_for_errors} contains blank header names; preserving columns is uncertain."
                )
            if len(set(fieldnames)) != len(fieldnames):
                raise UserActionRequired(
                    f"{filename_for_errors} contains duplicate headers; preserving columns is uncertain."
                )
            rows: list[dict[str, str]] = []
            for row_index, raw_row in enumerate(reader, start=2):
                if None in raw_row:
                    extra_values = raw_row.pop(None)
                    if extra_values:
                        warnings.append(
                            f"Row {row_index} in {filename_for_errors} had extra values beyond the header; stored in __extra_values."
                        )
                        raw_row["__extra_values"] = " | ".join(str(value) for value in extra_values)
                        if "__extra_values" not in fieldnames:
                            fieldnames.append("__extra_values")
                rows.append(
                    {name: "" if raw_row.get(name) is None else str(raw_row.get(name, "")) for name in fieldnames}
                )
    except UnicodeDecodeError as exc:
        raise UserActionRequired(f"Could not parse {filename_for_errors} as UTF-8: {exc}") from exc
    except csv.Error as exc:
        raise UserActionRequired(f"Could not parse {filename_for_errors} reliably: {exc}") from exc
    return fieldnames, rows, warnings


def parse_failure_csv(path: Path) -> tuple[list[str], list[dict[str, str]], list[str]]:
    return parse_csv_file(path, "failure_cases.csv")


ATK_ID_FIELD_HINTS = ("atk_id", "atkid")


def detect_facets(
    fieldnames: list[str], rows: list[dict[str, str]], roles: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    """Pick low-cardinality columns suitable as faceted filters.

    Skip role fields whose meaning is per-row free text (input/expected/actual/reason/id).
    Keep ``log`` out as well because each path is unique.
    """
    excluded_fields = set()
    for role in ("id", "input", "expected", "actual", "reason", "log"):
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
                # treat as free text, not categorical
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
            # every row unique -> not a useful facet
            continue
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        facets.append({"field": field, "values": [{"value": value, "count": count} for value, count in ordered]})
    return facets


# --- cross-version history -------------------------------------------------


_ATK_ID_TOKEN_RE = re.compile(
    r"(?:atk[_\s-]?id|case[_\s-]?id|id)\s*[:=#]?\s*[`\"\']?\s*([A-Za-z0-9_\-.]+)",
    re.IGNORECASE,
)
_BARE_ID_RE = re.compile(r"`([A-Za-z0-9_\-.]{1,64})`")


def safe_log_href(value: str, current_dir: Path) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    decode_stages = [raw]
    decoded = raw
    for _ in range(5):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decode_stages.append(next_decoded)
        decoded = next_decoded
    else:
        if unquote(decoded) != decoded:
            return ""

    for candidate in decode_stages:
        if "\\" in candidate or re.match(r"^[A-Za-z]:", candidate):
            return ""
        split = urlsplit(candidate)
        if split.scheme or split.netloc or candidate.startswith("/") or candidate.startswith("//"):
            return ""
        candidate_parts = Path(candidate).parts
        if any(part in {"..", ""} for part in candidate_parts if part != ".") and ".." in candidate_parts:
            return ""

    current_prefix = current_dir.as_posix().rstrip("/") + "/"
    href = decoded
    if href.startswith("./"):
        href = href[2:]
    if href.startswith(current_prefix):
        href = href[len(current_prefix) :]
    parts = Path(href).parts
    if not parts or ".." in parts or any(part == "" for part in parts):
        return ""
    if parts[0] == ".atk":
        return ""
    return Path(*parts).as_posix()


def enrich_rows(
    rows: list[dict[str, str]], fieldnames: list[str], roles: dict[str, dict[str, str]], current_dir: Path
) -> list[dict[str, Any]]:
    log_field = roles.get("log", {}).get("field", "")
    enriched: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        safe_links: dict[str, str] = {}
        log_content = ""
        for field in fieldnames:
            if field == log_field or "log" in normalize_name(field):
                href = safe_log_href(row.get(field, ""), current_dir)
                if href:
                    safe_links[field] = href
                    log_file_path = current_dir / href
                    if log_file_path.is_file():
                        try:
                            content, truncated = _safe_read_text(log_file_path, 65536)
                            if truncated:
                                content += "\n... [LOG TRUNCATED - OVER 64KB] ..."
                            log_content = content
                        except Exception:
                            log_content = ""
        enriched.append({"rowNumber": index, "values": row, "safeLogHrefs": safe_links, "logContent": log_content})
    return enriched


def build_payload(
    current_dir: Path,
    fieldnames: list[str],
    rows: list[dict[str, str]],
    roles: dict[str, dict[str, str]],
    facets: list[dict[str, Any]],
    report: dict[str, Any],
    warnings: list[str],
    history: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": current_dir.name,
        "currentDir": current_dir.as_posix(),
        "output": (current_dir / OUTPUT_FILENAME).as_posix(),
        "rowCount": len(rows),
        "fieldnames": fieldnames,
        "roles": roles,
        "facets": facets,
        "rows": enrich_rows(rows, fieldnames, roles, current_dir),
        "report": report,
        "history": _serialize_history_for_payload(history),
        "warnings": warnings,
        "config": {
            "snippetMaxChars": SNIPPET_MAX_CHARS,
            "pageSizes": PAGE_SIZES,
            "defaultPageSize": DEFAULT_PAGE_SIZE,
            "facetMaxUnique": FACET_MAX_UNIQUE,
            "persistentMinFails": HISTORY_PERSISTENT_MIN_FAILS,
        },
    }


def _serialize_history_for_payload(history: dict[str, Any]) -> dict[str, Any]:
    """Convert sets to lists and drop generator-only structures so JSON encoding is safe."""
    if not history:
        return {"status": "skipped", "reason": "no history", "versions": []}
    return {
        "status": history.get("status", "skipped"),
        "reason": history.get("reason", ""),
        "atkIdField": history.get("atkIdField", ""),
        "currentVersion": history.get("currentVersion", ""),
        "versions": history.get("versions", []),
        "perCase": history.get("perCase", []),
        "previousTargets": history.get("previousTargets", {}),
        "buckets": history.get("buckets", {"newRegressions": [], "persistent": []}),
        "topReasons": history.get("topReasons", []),
        "persistentMinFails": history.get("persistentMinFails", HISTORY_PERSISTENT_MIN_FAILS),
    }


def load_asset(name: str) -> str:
    path = ASSETS_DIR / name
    if not path.is_file():
        raise UserActionRequired(f"Missing plugin-owned asset {path}; reinstall the atk-visualize-failures Skill.")
    return path.read_text(encoding="utf-8")


def render_html(payload: dict[str, Any]) -> str:
    template = load_asset(PAGE_TEMPLATE_NAME)
    styles = load_asset(STYLES_NAME)
    app_js = load_asset(APP_JS_NAME)
    title = f"ATK Failure Cases — {payload['version']}"
    data_json = safe_json_for_html(payload)
    audit_markers = " | ".join(html.escape(marker) for marker in AUDIT_MARKERS)
    rendered = (
        template.replace("__ATK_TITLE__", html.escape(title))
        .replace("__ATK_STYLES__", styles)
        .replace("__ATK_AUDIT_MARKERS__", audit_markers)
        .replace("__ATK_DATA_JSON__", data_json)
        .replace("__ATK_APP_JS__", neutralize_script_close(app_js))
    )
    return rendered


def generate_failures(
    results_dir=RESULTS_DIR, *, overwrite=False, no_report=False, no_history=False, open_browser=True
):
    results_dir = Path(results_dir)
    current_dir = resolve_current_version(results_dir)
    failure_csv = require_current_file(current_dir, FAILURE_FILENAME)
    output_path = current_dir / OUTPUT_FILENAME
    if output_path.exists() and not overwrite:
        raise UserActionRequired(
            f"Refusing to overwrite existing {output_path}; rerun with --overwrite after confirming replacement."
        )

    failure_fieldnames, failure_rows, failure_warnings = parse_failure_csv(failure_csv)
    warnings = list(failure_warnings)

    eval_csv = current_dir / EVAL_FILENAME
    if eval_csv.is_file():
        eval_fieldnames, eval_rows, eval_warnings = parse_csv_file(eval_csv, EVAL_FILENAME)
        warnings.extend(eval_warnings)

        failure_atk_field = detect_atk_id_field(failure_fieldnames)
        eval_atk_field = detect_atk_id_field(eval_fieldnames)

        if not failure_atk_field:
            failure_atk_field = detect_roles(failure_fieldnames).get("id", {}).get("field", "")
        if not eval_atk_field:
            eval_atk_field = detect_roles(eval_fieldnames).get("id", {}).get("field", "")

        failures_by_id = {}
        for row in failure_rows:
            if failure_atk_field:
                aid = row.get(failure_atk_field, "").strip()
                if aid:
                    failures_by_id[aid] = row

        fieldnames = list(eval_fieldnames)
        for f in failure_fieldnames:
            if f not in fieldnames:
                fieldnames.append(f)

        abnormal_field = ""
        for f in fieldnames:
            if normalize_name(f) in ("is_abnormal", "abnormal", "is_failure"):
                abnormal_field = f
                break
        if not abnormal_field:
            abnormal_field = "is_abnormal"
            fieldnames.append(abnormal_field)

        judgments = read_judgments(current_dir)
        rows = []
        for erow in eval_rows:
            aid = erow.get(eval_atk_field, "").strip() if eval_atk_field else ""
            mrow = {f: erow.get(f, "") for f in eval_fieldnames}
            for f in fieldnames:
                if f not in mrow:
                    mrow[f] = ""

            if aid and aid in failures_by_id:
                frow = failures_by_id[aid]
                for f in failure_fieldnames:
                    if frow.get(f):
                        mrow[f] = frow[f]
                mrow[abnormal_field] = "true"
            else:
                verdict = judgments.get(aid, "unknown")
                mrow[abnormal_field] = {"passed": "false", "failed": "true"}.get(verdict, "unknown")
            rows.append(mrow)
    else:
        fieldnames = list(failure_fieldnames)
        abnormal_field = "is_abnormal"
        if abnormal_field not in fieldnames:
            fieldnames.append(abnormal_field)
        rows = []
        for row in failure_rows:
            mrow = dict(row)
            mrow[abnormal_field] = "true"
            rows.append(mrow)

    roles = detect_roles(fieldnames)
    facets = detect_facets(fieldnames, rows, roles)
    report = read_report_context(current_dir / REPORT_FILENAME, skip=no_report)
    history = build_history(results_dir, current_dir, fieldnames, rows, roles, skip=no_history)
    payload = build_payload(current_dir, fieldnames, rows, roles, facets, report, warnings, history)
    content = render_html(payload)
    write_atomic(output_path, content)

    history_versions = len(history.get("versions", []))
    history_status = history.get("status", "skipped")
    history_label = (
        (f"included ({history_versions} versions)" if history_versions > 1 else "single-version (no prior vN found)")
        if history_status == "included"
        else f"skipped ({history.get('reason', 'no history')})"
    )
    browser = open_in_browser(output_path) if open_browser else (False, "disabled")
    return {
        "warnings": warnings,
        "features": "dataset-visualize shell, stat-card-abnormal, search/filter, pagination, field feature analysis, inspector overlay, role switching, JSON/code rendering, review export, safe log links, bounded report excerpts",
        "version": current_dir.name,
        "rows": len(rows),
        "output": str(output_path),
        "report": report.get("status", "skipped")
        if report.get("status") == "included"
        else "skipped (" + report.get("reason", "no context") + ")",
        "history": history_label,
        "facets": len(facets),
        "overwrite": "overwrote existing HTML" if overwrite else "wrote new HTML",
        "browser_open": browser[0],
        "browser_info": browser[1],
    }


def detect_roles(fieldnames):
    return _detect_roles(fieldnames, ROLE_CANDIDATES)
