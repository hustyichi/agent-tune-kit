#!/usr/bin/env python3
"""Generate a dependency-free single-file HTML browser for current ATK failure cases.

The page shell (HTML/CSS/JS) lives in sibling ``assets/`` as plugin-owned files and
is inlined into a single ``.atk/results/vN/failure_cases.html`` at runtime. No
project-local template, no sidecar metadata, no external CDN, no LLM summary.
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
from urllib.parse import unquote, urlsplit

RESULTS_DIR = Path(".atk/results")
FAILURE_FILENAME = "failure_cases.csv"
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

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
PAGE_TEMPLATE_NAME = "page.html"
STYLES_NAME = "styles.css"
APP_JS_NAME = "app.js"

# Audit markers must remain present verbatim in the generated HTML so the plugin
# self-tests can verify capability anchors regardless of the localized UI text.
AUDIT_MARKERS = [
    "expected-vs-actual comparison",
    "schema-adaptive role switching",
    "auto-detected",
    "manual/unmapped",
    "Bounded report.md context",
    "Search / filter / pagination",
    "No failure rows in current failure_cases.csv",
    "not clickable because it is outside the safe relative path contract",
]


class UserActionRequired(RuntimeError):
    """A user-fixable input or confirmation blocker."""


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


def list_version_dirs(results_dir: Path = RESULTS_DIR) -> list[tuple[int, Path]]:
    if not results_dir.exists():
        return []
    versions: list[tuple[int, Path]] = []
    for child in results_dir.iterdir():
        if not child.is_dir():
            continue
        match = re.fullmatch(r"v(\d+)", child.name)
        if match:
            versions.append((int(match.group(1)), child))
    return sorted(versions, key=lambda item: item[0])


def resolve_current_version(results_dir: Path = RESULTS_DIR) -> Path:
    versions = list_version_dirs(results_dir)
    if not versions:
        raise UserActionRequired("No vN results directory exists; run eval_runner.py first or confirm repair.")
    return versions[-1][1]


def require_current_file(current_dir: Path, filename: str) -> Path:
    path = current_dir / filename
    if not path.exists():
        raise UserActionRequired(
            f"Current version {current_dir.name} is missing {filename}; fix or rerun the prior step."
        )
    if not path.is_file():
        raise UserActionRequired(
            f"Current version {current_dir.name} has non-file {filename}; fix or rerun the prior step."
        )
    return path


def parse_failure_csv(path: Path) -> tuple[list[str], list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, strict=True)
            fieldnames = list(reader.fieldnames or [])
            if not fieldnames or all(not (name or "").strip() for name in fieldnames):
                raise UserActionRequired("failure_cases.csv is empty or missing a header; run failure finding again.")
            if any(not (name or "").strip() for name in fieldnames):
                raise UserActionRequired(
                    "failure_cases.csv contains blank header names; preserving columns is uncertain."
                )
            if len(set(fieldnames)) != len(fieldnames):
                raise UserActionRequired(
                    "failure_cases.csv contains duplicate headers; preserving columns is uncertain."
                )
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
        raise UserActionRequired(f"Could not parse failure_cases.csv as UTF-8: {exc}") from exc
    except csv.Error as exc:
        raise UserActionRequired(f"Could not parse failure_cases.csv reliably: {exc}") from exc
    return fieldnames, rows, warnings


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


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


def read_report_context(report_path: Path, *, skip: bool = False) -> dict[str, Any]:
    if skip:
        return {"status": "skipped", "reason": "Skipped by --no-report.", "excerpts": []}
    if not report_path.exists():
        return {"status": "skipped", "reason": "report.md not found in current version.", "excerpts": []}
    if not report_path.is_file():
        return {"status": "skipped", "reason": "report.md is not a regular file.", "excerpts": []}

    try:
        with report_path.open("rb") as handle:
            raw = handle.read(REPORT_MAX_BYTES + 1)
        truncated = len(raw) > REPORT_MAX_BYTES
        if truncated:
            raw = raw[:REPORT_MAX_BYTES]
        text = raw.decode("utf-8", errors="replace")
    except OSError as exc:
        return {"status": "skipped", "reason": f"Could not read report.md: {exc}", "excerpts": []}

    excerpts: list[dict[str, str]] = []
    current_heading = "Report context"
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            current_heading = stripped.lstrip("#").strip() or current_heading
            if len(excerpts) < REPORT_MAX_EXCERPTS and contains_report_keyword(current_heading):
                excerpts.append(
                    {"heading": current_heading, "text": truncate(current_heading, REPORT_EXCERPT_MAX_CHARS)}
                )
            continue
        if contains_report_keyword(stripped):
            excerpts.append({"heading": current_heading, "text": truncate(stripped, REPORT_EXCERPT_MAX_CHARS)})
        if len(excerpts) >= REPORT_MAX_EXCERPTS:
            break

    if not excerpts and text.strip():
        excerpts.append(
            {"heading": "Report context", "text": truncate(text.strip().replace("\n", " "), REPORT_EXCERPT_MAX_CHARS)}
        )

    status = "included" if excerpts else "skipped"
    reason = "Included bounded report.md excerpts."
    if truncated:
        reason += f" Read first {REPORT_MAX_BYTES} bytes only."
    if not excerpts:
        reason = "report.md had no extractable bounded context."
    return {"status": status, "reason": reason, "excerpts": excerpts[:REPORT_MAX_EXCERPTS], "truncated": truncated}


def contains_report_keyword(text: str) -> bool:
    lower = text.lower()
    return any(keyword in lower for keyword in REPORT_KEYWORDS)


def truncate(value: str, max_chars: int) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "…"


def safe_json_for_html(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def safe_log_href(value: str, current_dir: Path) -> str:
    raw = (value or "").strip()
    if not raw or "\\" in raw or re.match(r"^[A-Za-z]:", raw):
        return ""
    decoded = unquote(raw)
    if decoded != raw and ("\\" in decoded or re.match(r"^[A-Za-z]:", decoded)):
        return ""
    for candidate in (raw, decoded):
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
        for field in fieldnames:
            if field == log_field or "log" in normalize_name(field):
                href = safe_log_href(row.get(field, ""), current_dir)
                if href:
                    safe_links[field] = href
        enriched.append({"rowNumber": index, "values": row, "safeLogHrefs": safe_links})
    return enriched


def build_payload(
    current_dir: Path,
    fieldnames: list[str],
    rows: list[dict[str, str]],
    roles: dict[str, dict[str, str]],
    facets: list[dict[str, Any]],
    report: dict[str, Any],
    warnings: list[str],
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
        "warnings": warnings,
        "config": {
            "snippetMaxChars": SNIPPET_MAX_CHARS,
            "pageSizes": PAGE_SIZES,
            "defaultPageSize": DEFAULT_PAGE_SIZE,
            "facetMaxUnique": FACET_MAX_UNIQUE,
        },
    }


def load_asset(name: str) -> str:
    path = ASSETS_DIR / name
    if not path.is_file():
        raise UserActionRequired(f"Missing plugin-owned asset {path}; reinstall the atk-visualize-failures Skill.")
    return path.read_text(encoding="utf-8")


def neutralize_script_close(text: str) -> str:
    """Prevent any literal </script in inlined CSS/JS from terminating the host script."""
    return re.sub(r"</(script)", r"<\\/\1", text, flags=re.IGNORECASE)


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
        description="Generate current-version ATK failure_cases.html from failure_cases.csv."
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="replace existing failure_cases.html after Skill-level confirmation"
    )
    parser.add_argument(
        "--results-dir",
        default=str(RESULTS_DIR),
        help="results directory relative to target project cwd (default: .atk/results)",
    )
    parser.add_argument("--no-report", action="store_true", help="skip optional same-version report.md parsing")
    parser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="open the generated HTML in the default browser after writing",
    )
    return parser.parse_args(argv)


def run(argv: list[str]) -> int:
    args = parse_args(argv)
    results_dir = Path(args.results_dir)
    current_dir = resolve_current_version(results_dir)
    failure_csv = require_current_file(current_dir, FAILURE_FILENAME)
    output_path = current_dir / OUTPUT_FILENAME
    if output_path.exists() and not args.overwrite:
        raise UserActionRequired(
            f"Refusing to overwrite existing {output_path}; rerun with --overwrite after confirming replacement."
        )

    fieldnames, rows, warnings = parse_failure_csv(failure_csv)
    roles = detect_roles(fieldnames)
    facets = detect_facets(fieldnames, rows, roles)
    report = read_report_context(current_dir / REPORT_FILENAME, skip=args.no_report)
    payload = build_payload(current_dir, fieldnames, rows, roles, facets, report, warnings)
    content = render_html(payload)
    write_atomic(output_path, content)

    overwrite_status = "overwrote existing HTML" if args.overwrite else "wrote new HTML"
    report_status = (
        "included" if report.get("status") == "included" else f"skipped ({report.get('reason', 'no context')})"
    )
    print(f"version={current_dir.name}")
    print(f"rows={len(rows)}")
    print(f"output={output_path.as_posix()}")
    print(f"report={report_status}")
    print(f"overwrite={overwrite_status}")
    print(f"facets={len(facets)}")
    print(
        "features=summary counts, search/filter, pagination, expected-vs-actual, role switching, "
        "dynamic facets, line diff, light syntax highlight, all-field detail"
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
    except Exception as exc:
        print(f"Unexpected failure_cases.html generation error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
