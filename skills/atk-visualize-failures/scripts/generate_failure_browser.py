#!/usr/bin/env python3
"""Generate a dependency-free HTML browser for current ATK failure cases."""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import tempfile
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


class UserActionRequired(RuntimeError):
    """A user-fixable input or confirmation blocker."""


ROLE_CANDIDATES: dict[str, list[str]] = {
    "id": ["case_id", "failure_id", "id", "source_index", "row_id", "index"],
    "input": ["input", "query", "question", "prompt", "task", "user_input", "instruction"],
    "expected": ["expected", "expected_output", "reference", "ground_truth", "answer", "label"],
    "actual": ["agent_output", "actual", "actual_output", "output", "response", "prediction", "result"],
    "reason": ["failure_reason", "failure", "reason", "explanation", "root_cause", "root-cause", "error", "analysis"],
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
        raise UserActionRequired(f"Current version {current_dir.name} is missing {filename}; fix or rerun the prior step.")
    if not path.is_file():
        raise UserActionRequired(f"Current version {current_dir.name} has non-file {filename}; fix or rerun the prior step.")
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
                raise UserActionRequired("failure_cases.csv contains blank header names; preserving columns is uncertain.")
            if len(set(fieldnames)) != len(fieldnames):
                raise UserActionRequired("failure_cases.csv contains duplicate headers; preserving columns is uncertain.")
            rows: list[dict[str, str]] = []
            for row_index, raw_row in enumerate(reader, start=2):
                if None in raw_row:
                    extra_values = raw_row.pop(None)
                    if extra_values:
                        warnings.append(f"Row {row_index} had extra values beyond the header; stored in __extra_values.")
                        raw_row["__extra_values"] = " | ".join(str(value) for value in extra_values)
                        if "__extra_values" not in fieldnames:
                            fieldnames.append("__extra_values")
                rows.append({name: "" if raw_row.get(name) is None else str(raw_row.get(name, "")) for name in fieldnames})
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
                excerpts.append({"heading": current_heading, "text": truncate(current_heading, REPORT_EXCERPT_MAX_CHARS)})
            continue
        if contains_report_keyword(stripped):
            excerpts.append({"heading": current_heading, "text": truncate(stripped, REPORT_EXCERPT_MAX_CHARS)})
        if len(excerpts) >= REPORT_MAX_EXCERPTS:
            break

    if not excerpts and text.strip():
        excerpts.append({"heading": "Report context", "text": truncate(text.strip().replace("\n", " "), REPORT_EXCERPT_MAX_CHARS)})

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
        if any(part in {"..", ""} for part in Path(candidate).parts if part != "."):
            if ".." in Path(candidate).parts:
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


def enrich_rows(rows: list[dict[str, str]], fieldnames: list[str], roles: dict[str, dict[str, str]], current_dir: Path) -> list[dict[str, Any]]:
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


def build_payload(current_dir: Path, fieldnames: list[str], rows: list[dict[str, str]], roles: dict[str, dict[str, str]], report: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    return {
        "version": current_dir.name,
        "currentDir": current_dir.as_posix(),
        "output": (current_dir / OUTPUT_FILENAME).as_posix(),
        "rowCount": len(rows),
        "fieldnames": fieldnames,
        "roles": roles,
        "rows": enrich_rows(rows, fieldnames, roles, current_dir),
        "report": report,
        "warnings": warnings,
        "config": {
            "snippetMaxChars": SNIPPET_MAX_CHARS,
            "pageSizes": PAGE_SIZES,
            "defaultPageSize": DEFAULT_PAGE_SIZE,
        },
    }


def generate_html(payload: dict[str, Any]) -> str:
    data_json = safe_json_for_html(payload)
    title = f"ATK Failure Cases — {html.escape(str(payload['version']))}"
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; --bg:#f6f7fb; --panel:#fff; --text:#172033; --muted:#667085; --line:#d8deea; --brand:#3457d5; --bad:#b42318; --ok:#067647; --chip:#eef2ff; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; background:var(--bg); color:var(--text); }}
    header {{ padding:24px 28px; background:linear-gradient(135deg,#172033,#3457d5); color:white; }}
    header h1 {{ margin:0 0 8px; font-size:24px; }}
    header p {{ margin:4px 0; opacity:.9; }}
    main {{ padding:20px; max-width:1400px; margin:0 auto; }}
    .grid {{ display:grid; grid-template-columns: 360px 1fr; gap:16px; align-items:start; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; box-shadow:0 1px 2px rgba(16,24,40,.05); }}
    .panel h2 {{ font-size:16px; margin:0; padding:14px 16px; border-bottom:1px solid var(--line); }}
    .panel-body {{ padding:14px 16px; }}
    .stats {{ display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:10px; margin-bottom:16px; }}
    .stat {{ padding:12px; border-radius:12px; background:white; border:1px solid var(--line); }}
    .stat strong {{ display:block; font-size:22px; }}
    .muted {{ color:var(--muted); }}
    .controls {{ display:grid; grid-template-columns: 1fr 160px 150px; gap:10px; margin-bottom:12px; }}
    input, select, button {{ font:inherit; border:1px solid var(--line); border-radius:10px; background:white; padding:9px 10px; }}
    button {{ cursor:pointer; }}
    .case-list {{ display:flex; flex-direction:column; gap:10px; }}
    .case-card {{ text-align:left; border:1px solid var(--line); border-radius:12px; background:white; padding:12px; cursor:pointer; width:100%; }}
    .case-card.active {{ border-color:var(--brand); box-shadow:0 0 0 2px rgba(52,87,213,.12); }}
    .case-title {{ display:flex; justify-content:space-between; gap:8px; font-weight:700; margin-bottom:6px; }}
    .case-snippet {{ color:var(--muted); font-size:13px; line-height:1.35; overflow-wrap:anywhere; }}
    .badge {{ display:inline-flex; align-items:center; gap:4px; padding:2px 7px; border-radius:999px; background:var(--chip); color:#273a8a; font-size:12px; white-space:nowrap; }}
    .pager {{ display:flex; justify-content:space-between; align-items:center; gap:8px; margin-top:12px; }}
    .detail-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
    .compare {{ min-height:140px; padding:12px; border:1px solid var(--line); border-radius:12px; background:#fcfcfd; white-space:pre-wrap; overflow-wrap:anywhere; }}
    .compare h3 {{ margin:0 0 8px; font-size:14px; }}
    .field {{ margin:10px 0; border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
    .field summary {{ cursor:pointer; padding:10px 12px; background:#f9fafb; font-weight:650; }}
    .field pre {{ margin:0; padding:12px; white-space:pre-wrap; overflow-wrap:anywhere; max-height:360px; overflow:auto; }}
    .roles {{ display:grid; grid-template-columns:1fr; gap:8px; }}
    .role-row {{ display:grid; grid-template-columns:86px 1fr; align-items:center; gap:8px; }}
    .report-item {{ border-left:3px solid var(--brand); padding:8px 10px; margin:8px 0; background:#f8faff; }}
    .warning {{ color:#8a4b00; background:#fff7e6; border:1px solid #fedf89; padding:8px 10px; border-radius:10px; margin:8px 0; }}
    .empty {{ padding:28px; text-align:center; color:var(--muted); }}
    @media (max-width: 980px) {{ .grid, .detail-grid {{ grid-template-columns:1fr; }} .controls {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <p>Dependency-free static browser for current-version failure_cases.csv. No sidecar metadata, no CDN, no LLM summary.</p>
    <p id=\"path-context\"></p>
  </header>
  <main>
    <section class=\"stats\" aria-label=\"summary counts\">
      <div class=\"stat\"><span class=\"muted\">Rows</span><strong id=\"stat-rows\">0</strong></div>
      <div class=\"stat\"><span class=\"muted\">Fields</span><strong id=\"stat-fields\">0</strong></div>
      <div class=\"stat\"><span class=\"muted\">Report</span><strong id=\"stat-report\">Skipped</strong></div>
    </section>
    <section class=\"grid\">
      <aside class=\"panel\">
        <h2>Search / filter / pagination</h2>
        <div class=\"panel-body\">
          <div class=\"controls\">
            <input id=\"search\" type=\"search\" placeholder=\"Search all fields…\" aria-label=\"Search all fields\">
            <select id=\"role-filter\" aria-label=\"Filter by role\"><option value=\"all\">All cases</option><option value=\"has-reason\">Has reason</option><option value=\"has-log\">Has log</option></select>
            <select id=\"page-size\" aria-label=\"Page size\"></select>
          </div>
          <div id=\"empty-state\" class=\"empty\" hidden>No failure rows in current failure_cases.csv.</div>
          <div id=\"case-list\" class=\"case-list\"></div>
          <div class=\"pager\">
            <button id=\"prev\" type=\"button\">Previous</button>
            <span id=\"page-label\" class=\"muted\"></span>
            <button id=\"next\" type=\"button\">Next</button>
          </div>
        </div>
      </aside>
      <section class=\"panel\">
        <h2>Single-case detail review</h2>
        <div class=\"panel-body\">
          <section class=\"panel\" style=\"box-shadow:none;margin-bottom:12px;\">
            <h2>Schema role mapping <span class=\"muted\">(auto-detected; schema-adaptive role switching is manual in this page only)</span></h2>
            <div id=\"roles\" class=\"panel-body roles\"></div>
          </section>
          <div id=\"warnings\"></div>
          <div id=\"report\"></div>
          <div class=\"detail-grid\" aria-label=\"expected-vs-actual comparison\">
            <div class=\"compare\"><h3>Expected</h3><div id=\"expected\"></div></div>
            <div class=\"compare\"><h3>Actual</h3><div id=\"actual\"></div></div>
          </div>
          <div id=\"reason\" class=\"warning\" hidden></div>
          <div id=\"log-link\" style=\"margin:12px 0;\"></div>
          <h2 style=\"padding-left:0;border-bottom:0;\">All preserved fields</h2>
          <div id=\"fields\"></div>
        </div>
      </section>
    </section>
  </main>
  <script type=\"application/json\" id=\"failure-data\">{data_json}</script>
  <script>
    const payload = JSON.parse(document.getElementById('failure-data').textContent);
    const roles = JSON.parse(JSON.stringify(payload.roles));
    let query = '';
    let roleFilter = 'all';
    let pageSize = payload.config.defaultPageSize;
    let page = 0;
    let selectedRowNumber = payload.rows[0]?.rowNumber || null;

    const $ = (id) => document.getElementById(id);
    const roleNames = ['id','input','expected','actual','reason','log'];
    const esc = (value) => String(value ?? '').replace(/[&<>\"']/g, (ch) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[ch]));
    const snippet = (value) => {{ const text = String(value ?? '').replace(/\\s+/g, ' ').trim(); return text.length > payload.config.snippetMaxChars ? text.slice(0, payload.config.snippetMaxChars - 1) + '…' : text; }};
    const roleField = (role) => roles[role]?.field || '';
    const rowValue = (row, role) => row?.values?.[roleField(role)] || '';
    const allText = (row) => payload.fieldnames.map((field) => row.values[field] || '').join('\\n').toLowerCase();
    const currentRow = () => payload.rows.find((row) => row.rowNumber === selectedRowNumber) || payload.rows[0] || null;

    function init() {{
      $('path-context').textContent = `${{payload.currentDir}} → ${{payload.output}}`;
      $('stat-rows').textContent = payload.rowCount;
      $('stat-fields').textContent = payload.fieldnames.length;
      $('stat-report').textContent = payload.report.status === 'included' ? 'Included' : 'Skipped';
      for (const size of payload.config.pageSizes) {{
        const option = document.createElement('option'); option.value = size; option.textContent = `${{size}} / page`; option.selected = size === pageSize; $('page-size').appendChild(option);
      }}
      renderRoles(); renderWarnings(); renderReport(); bindControls(); render();
    }}

    function bindControls() {{
      $('search').addEventListener('input', (event) => {{ query = event.target.value.toLowerCase(); page = 0; render(); }});
      $('role-filter').addEventListener('change', (event) => {{ roleFilter = event.target.value; page = 0; render(); }});
      $('page-size').addEventListener('change', (event) => {{ pageSize = Number(event.target.value) || payload.config.defaultPageSize; page = 0; render(); }});
      $('prev').addEventListener('click', () => {{ page = Math.max(0, page - 1); render(); }});
      $('next').addEventListener('click', () => {{ page += 1; render(); }});
    }}

    function renderRoles() {{
      $('roles').innerHTML = roleNames.map((role) => `
        <label class=\"role-row\"><span>${{esc(role)}} <span class=\"badge\" id=\"source-${{role}}\">${{roles[role]?.source === 'auto' ? 'auto-detected' : 'manual'}}</span></span>
          <select data-role=\"${{role}}\"><option value=\"\">— not mapped —</option>${{payload.fieldnames.map((field) => `<option value=\"${{esc(field)}}\" ${{field === roles[role]?.field ? 'selected' : ''}}>${{esc(field)}}</option>`).join('')}}</select>
        </label>`).join('');
      $('roles').querySelectorAll('select[data-role]').forEach((select) => select.addEventListener('change', (event) => {{
        const role = event.target.dataset.role; roles[role] = {{field: event.target.value, source: 'manual'}}; $(`source-${{role}}`).textContent = event.target.value ? 'manual' : 'manual/unmapped'; render();
      }}));
    }}

    function renderWarnings() {{
      const items = [...(payload.warnings || [])];
      if (payload.report.status !== 'included') items.push(`Report context skipped: ${{payload.report.reason}}`);
      $('warnings').innerHTML = items.map((item) => `<div class=\"warning\">${{esc(item)}}</div>`).join('');
    }}

    function renderReport() {{
      if (!payload.report.excerpts?.length) {{ $('report').innerHTML = ''; return; }}
      $('report').innerHTML = `<section class=\"panel\" style=\"box-shadow:none;margin-bottom:12px;\"><h2>Bounded report.md context</h2><div class=\"panel-body\">${{payload.report.excerpts.map((item) => `<div class=\"report-item\"><strong>${{esc(item.heading)}}</strong><br>${{esc(item.text)}}</div>`).join('')}}</div></section>`;
    }}

    function filteredRows() {{
      return payload.rows.filter((row) => {{
        if (query && !allText(row).includes(query)) return false;
        if (roleFilter === 'has-reason' && !rowValue(row, 'reason')) return false;
        if (roleFilter === 'has-log' && !row.safeLogHrefs?.[roleField('log')]) return false;
        return true;
      }});
    }}

    function render() {{
      const rows = filteredRows();
      const maxPage = Math.max(0, Math.ceil(rows.length / pageSize) - 1); page = Math.min(page, maxPage);
      const visible = rows.slice(page * pageSize, page * pageSize + pageSize);
      $('empty-state').hidden = payload.rows.length !== 0;
      $('case-list').innerHTML = visible.map((row) => cardHtml(row)).join('');
      $('case-list').querySelectorAll('button[data-row]').forEach((button) => button.addEventListener('click', () => {{ selectedRowNumber = Number(button.dataset.row); renderDetail(); render(); }}));
      $('page-label').textContent = rows.length ? `Page ${{page + 1}} / ${{maxPage + 1}} · ${{rows.length}} shown` : 'No matching rows';
      $('prev').disabled = page <= 0; $('next').disabled = page >= maxPage;
      if (!rows.some((row) => row.rowNumber === selectedRowNumber)) selectedRowNumber = rows[0]?.rowNumber || payload.rows[0]?.rowNumber || null;
      renderDetail();
    }}

    function cardHtml(row) {{
      const title = rowValue(row, 'id') || `Row ${{row.rowNumber}}`;
      const body = rowValue(row, 'input') || rowValue(row, 'reason') || payload.fieldnames.map((field) => row.values[field]).find(Boolean) || '';
      return `<button type=\"button\" class=\"case-card ${{row.rowNumber === selectedRowNumber ? 'active' : ''}}\" data-row=\"${{row.rowNumber}}\"><div class=\"case-title\"><span>${{esc(title)}}</span><span class=\"badge\">#${{row.rowNumber}}</span></div><div class=\"case-snippet\">${{esc(snippet(body))}}</div></button>`;
    }}

    function renderDetail() {{
      const row = currentRow();
      if (!row) {{ $('expected').textContent = 'No failure rows.'; $('actual').textContent = 'No failure rows.'; $('fields').innerHTML = ''; $('reason').hidden = true; $('log-link').innerHTML = ''; return; }}
      $('expected').textContent = rowValue(row, 'expected') || 'No expected role mapped.';
      $('actual').textContent = rowValue(row, 'actual') || 'No actual role mapped.';
      const reason = rowValue(row, 'reason'); $('reason').hidden = !reason; $('reason').textContent = reason ? `Failure reason: ${{reason}}` : '';
      const logField = roleField('log'); const href = row.safeLogHrefs?.[logField];
      $('log-link').innerHTML = href ? `<a href=\"${{esc(href)}}\" target=\"_blank\" rel=\"noopener\">Open row log: ${{esc(row.values[logField])}}</a>` : (logField && row.values[logField] ? `<span class=\"muted\">Log path is shown as evidence but is not clickable because it is outside the safe relative path contract: ${{esc(row.values[logField])}}</span>` : '');
      $('fields').innerHTML = payload.fieldnames.map((field) => `<details class=\"field\"><summary>${{esc(field)}}</summary><pre>${{esc(row.values[field] || '')}}</pre></details>`).join('');
    }}

    init();
  </script>
</body>
</html>
"""


def write_atomic(output_path: Path, content: str) -> None:
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_path.parent, prefix=f".{output_path.name}.", suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, output_path)
    except OSError:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate current-version ATK failure_cases.html from failure_cases.csv.")
    parser.add_argument("--overwrite", action="store_true", help="replace existing failure_cases.html after Skill-level confirmation")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR), help="results directory relative to target project cwd (default: .atk/results)")
    parser.add_argument("--no-report", action="store_true", help="skip optional same-version report.md parsing")
    return parser.parse_args(argv)

def run(argv: list[str]) -> int:
    args = parse_args(argv)
    results_dir = Path(args.results_dir)
    current_dir = resolve_current_version(results_dir)
    failure_csv = require_current_file(current_dir, FAILURE_FILENAME)
    output_path = current_dir / OUTPUT_FILENAME
    if output_path.exists() and not args.overwrite:
        raise UserActionRequired(f"Refusing to overwrite existing {output_path}; rerun with --overwrite after confirming replacement.")

    fieldnames, rows, warnings = parse_failure_csv(failure_csv)
    roles = detect_roles(fieldnames)
    report = read_report_context(current_dir / REPORT_FILENAME, skip=args.no_report)
    payload = build_payload(current_dir, fieldnames, rows, roles, report, warnings)
    content = generate_html(payload)
    write_atomic(output_path, content)

    overwrite_status = "overwrote existing HTML" if args.overwrite else "wrote new HTML"
    report_status = "included" if report.get("status") == "included" else f"skipped ({report.get('reason', 'no context')})"
    print(f"version={current_dir.name}")
    print(f"rows={len(rows)}")
    print(f"output={output_path.as_posix()}")
    print(f"report={report_status}")
    print(f"overwrite={overwrite_status}")
    print("features=summary counts, search/filter, pagination, expected-vs-actual, role switching, all-field detail")
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
