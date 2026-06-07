(function () {
  "use strict";

  var dataNode = document.getElementById("failure-data");
  var payload = JSON.parse(dataNode.textContent);
  var roles = JSON.parse(JSON.stringify(payload.roles || {}));
  var config = payload.config || { snippetMaxChars: 240, pageSizes: [25, 50, 100, 250], defaultPageSize: 50 };

  var STORAGE_KEY = "atk-failure-review:" + (payload.currentDir || payload.version || "failure_cases.csv");
  var review = loadReview();
  var columnMeta = buildColumnMeta();
  var globalSearch = "";

  var ROLE_LIST = ["id", "input", "expected", "actual", "reason", "log"];
  var ROLE_LABELS = {
    id: "ID",
    input: "输入",
    expected: "期望",
    actual: "实际输出",
    reason: "异常原因",
    log: "日志",
  };

  var state = {
    tab: "grid",
    query: "",
    pageSize: config.defaultPageSize || 50,
    page: 0,
    selectedRowNumber: payload.rows && payload.rows[0] ? payload.rows[0].rowNumber : null,
    sortColumn: "",
    sortDirection: "asc",
    visibleColumns: initialVisibleColumns(),
    selectedColumnName: (payload.fieldnames || [])[0] || "",
    columnQuery: "",
    showEmptyFields: false,
    statusFilter: "all",
  };

  var $ = function (id) { return document.getElementById(id); };
  var el = function (tag, props, children) {
    var node = document.createElement(tag);
    if (props) {
      for (var k in props) {
        if (props[k] == null) continue;
        if (k === "class") node.className = props[k];
        else if (k === "text") node.textContent = props[k];
        else if (k === "html") node.innerHTML = props[k];
        else if (k.indexOf("on") === 0) node.addEventListener(k.slice(2), props[k]);
        else node.setAttribute(k, props[k]);
      }
    }
    if (children) {
      for (var i = 0; i < children.length; i++) {
        var c = children[i];
        if (c == null) continue;
        if (typeof c === "string") node.appendChild(document.createTextNode(c));
        else node.appendChild(c);
      }
    }
    return node;
  };

  function normalizeText(value) {
    return String(value == null ? "" : value);
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch] || ch;
    });
  }

  function roleField(role) {
    return (roles[role] && roles[role].field) || "";
  }

  function rowValue(row, role) {
    var field = roleField(role);
    return field ? normalizeText(row.values[field]) : "";
  }

  function rowId(row) {
    return rowValue(row, "id") || String(row.rowNumber);
  }

  function truncate(value, maxChars) {
    var text = normalizeText(value).replace(/\s+/g, " ").trim();
    if (text.length <= maxChars) return text;
    return text.slice(0, Math.max(0, maxChars - 1)) + "…";
  }

  function isCodeLike(value) {
    if (!value) return false;
    var s = String(value);
    if (s.indexOf("```") >= 0) return true;
    if (s.indexOf("\n") < 0) return false;
    if (/[{};()]/.test(s)) return true;
    if (/^[ \t]{2,}\S/m.test(s)) return true;
    return false;
  }

  var KEYWORDS = (
    "function|return|if|else|for|while|do|switch|case|default|break|continue|" +
    "try|catch|finally|throw|new|delete|typeof|instanceof|in|of|" +
    "const|let|var|class|extends|implements|interface|enum|export|import|from|as|" +
    "public|private|protected|static|final|abstract|void|true|false|null|undefined|" +
    "this|super|async|await|yield|module|package|namespace|def|print"
  );
  var KW_RE = new RegExp("\\b(" + KEYWORDS + ")\\b", "g");

  function stripMarkdownFence(value) {
    var s = normalizeText(value).trim();
    var match = s.match(/^```[a-zA-Z0-9_-]*\n([\s\S]*?)\n```$/);
    return match ? match[1] : value;
  }

  function highlightCode(value) {
    var tokens = [];
    function tokenKey(index) {
      var letters = "";
      var n = index;
      do {
        letters = String.fromCharCode(97 + (n % 26)) + letters;
        n = Math.floor(n / 26) - 1;
      } while (n >= 0);
      return "\u0000TOK" + letters + "END\u0000";
    }
    function stash(cls, text) {
      var key = tokenKey(tokens.length);
      tokens.push({ key: key, html: '<span class="' + cls + '">' + escapeHtml(text) + '</span>' });
      return key;
    }
    var s = normalizeText(stripMarkdownFence(value));
    s = s.replace(/\/\*[\s\S]*?\*\//g, function (m) {
      return stash("tok-com", m);
    });
    s = s.replace(/(^|[^:])\/\/[^\n]*/g, function (m, p1) {
      return p1 + stash("tok-com", m.slice(p1.length));
    });
    s = s.replace(/(`(?:[^`\\]|\\.)*`|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g, function (m) {
      return stash("tok-str", m);
    });
    s = escapeHtml(s);
    s = s.replace(KW_RE, '<span class="tok-kw">$1</span>');
    s = s.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-num">$1</span>');
    tokens.forEach(function (token) {
      s = s.split(token.key).join(token.html);
    });
    return s;
  }

  function splitHtmlIntoLines(html) {
    var lines = [];
    var currentLine = "";
    var activeSpans = [];
    var i = 0;
    while (i < html.length) {
      if (html[i] === '<') {
        var endIdx = html.indexOf('>', i);
        if (endIdx === -1) {
          currentLine += html.slice(i);
          break;
        }
        var tag = html.slice(i, endIdx + 1);
        i = endIdx + 1;
        if (tag.indexOf('</span') === 0) {
          activeSpans.pop();
          currentLine += tag;
        } else if (tag.indexOf('<span') === 0) {
          activeSpans.push(tag);
          currentLine += tag;
        } else {
          currentLine += tag;
        }
      } else if (html[i] === '\n') {
        for (var j = activeSpans.length - 1; j >= 0; j--) {
          currentLine += '</span>';
        }
        lines.push(currentLine);
        currentLine = "";
        for (var k = 0; k < activeSpans.length; k++) {
          currentLine += activeSpans[k];
        }
        i++;
      } else {
        currentLine += html[i];
        i++;
      }
    }
    for (var j = activeSpans.length - 1; j >= 0; j--) {
      currentLine += '</span>';
    }
    lines.push(currentLine);
    return lines;
  }

  function renderCodeHtml(value) {
    var highlighted = highlightCode(value);
    var lines = splitHtmlIntoLines(highlighted);
    var htmlLines = lines.map(function (line) {
      return '<span class="code-line"><span class="code-line-content">' + (line || " ") + '</span></span>';
    });
    return '<code class="code-block-with-lines">' + htmlLines.join('') + '</code>';
  }

  function formatJsonToHtml(val, indent) {
    indent = indent || "";
    var nextIndent = indent + "  ";
    if (val === null) return '<span class="tok-kw">null</span>';
    if (typeof val === "boolean") return '<span class="tok-kw">' + val + '</span>';
    if (typeof val === "number") return '<span class="tok-num">' + val + '</span>';
    if (typeof val === "string") return '<span class="tok-str">' + escapeHtml(JSON.stringify(val)) + '</span>';
    if (Array.isArray(val)) {
      if (val.length === 0) return "[]";
      var items = [];
      for (var i = 0; i < val.length; i++) items.push(nextIndent + formatJsonToHtml(val[i], nextIndent));
      return "[\n" + items.join(",\n") + "\n" + indent + "]";
    }
    if (typeof val === "object") {
      var keys = Object.keys(val);
      if (keys.length === 0) return "{}";
      var pairs = [];
      for (var j = 0; j < keys.length; j++) {
        var k = keys[j];
        pairs.push(nextIndent + '<span class="tok-key">' + escapeHtml(JSON.stringify(k)) + '</span>: ' + formatJsonToHtml(val[k], nextIndent));
      }
      return "{\n" + pairs.join(",\n") + "\n" + indent + "}";
    }
    return escapeHtml(String(val));
  }

  function parseJsonValue(value) {
    var text = normalizeText(value).trim();
    if (!(text.indexOf("{") === 0 || text.indexOf("[") === 0)) return null;
    try {
      var parsed = JSON.parse(text);
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch (e) {
      return null;
    }
  }

  function initialVisibleColumns() {
    var out = {};
    var fieldnames = payload.fieldnames || [];
    var important = [roleField("id"), roleField("input"), roleField("expected"), roleField("actual"), roleField("reason"), roleField("log")];
    var abnormalField = getAbnormalField();
    for (var i = 0; i < fieldnames.length; i++) {
      var field = fieldnames[i];
      out[field] = i < 6 || important.indexOf(field) >= 0 || field === abnormalField;
    }
    return out;
  }

  function visibleColumns() {
    var out = [];
    var fieldnames = payload.fieldnames || [];
    for (var i = 0; i < fieldnames.length; i++) {
      var field = fieldnames[i];
      if (state.visibleColumns[field]) out.push(field);
    }
    return out;
  }

  function buildColumnMeta() {
    var rows = payload.rows || [];
    return (payload.fieldnames || []).map(function (field) {
      var nonEmpty = 0;
      var sampleValues = [];
      var typeCounts = { string: 0, number: 0, boolean: 0, json: 0, empty: 0 };
      var lengths = [];
      rows.forEach(function (row) {
        var text = normalizeText(row.values ? row.values[field] : "");
        if (!text.trim()) {
          typeCounts.empty += 1;
          return;
        }
        nonEmpty += 1;
        lengths.push(text.length);
        if (sampleValues.length < 6) sampleValues.push(text);
        if (parseJsonValue(text)) typeCounts.json += 1;
        else if (/^-?\d+(\.\d+)?$/.test(text)) typeCounts.number += 1;
        else if (/^(true|false|yes|no|0|1)$/i.test(text)) typeCounts.boolean += 1;
        else typeCounts.string += 1;
      });
      var inferred = "string";
      if (typeCounts.json > 0 && typeCounts.json >= typeCounts.string) inferred = "json";
      else if (typeCounts.number > typeCounts.string && typeCounts.number >= typeCounts.boolean) inferred = "number";
      else if (typeCounts.boolean > typeCounts.string && typeCounts.boolean > typeCounts.number) inferred = "boolean";
      var avg = lengths.length ? Math.round(lengths.reduce(function (a, b) { return a + b; }, 0) / lengths.length) : 0;
      return {
        name: field,
        type: inferred,
        nonEmptyCount: nonEmpty,
        completionRate: rows.length ? Math.round((nonEmpty / rows.length) * 100) : 0,
        sampleValues: sampleValues,
        minLength: lengths.length ? Math.min.apply(Math, lengths) : 0,
        maxLength: lengths.length ? Math.max.apply(Math, lengths) : 0,
        avgLength: avg,
      };
    });
  }

  function columnMetaFor(field) {
    for (var i = 0; i < columnMeta.length; i++) {
      if (columnMeta[i].name === field) return columnMeta[i];
    }
    return null;
  }

  function loadReview() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return {};
      var parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) {
      return {};
    }
  }

  function saveReview() {
    try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(review)); } catch (e) {}
  }

  function rowKey(row) {
    return "failure:" + rowId(row) + ":row:" + row.rowNumber;
  }

  function getNote(row) {
    var r = review[rowKey(row)];
    return r ? r.note || "" : "";
  }

  function getStatus(row) {
    var r = review[rowKey(row)];
    return r ? r.status || "" : "";
  }

  function setNote(row, note) {
    var k = rowKey(row);
    var r = review[k] || {};
    r.note = note;
    r.caseId = rowId(row);
    if (!r.status && !r.note) delete review[k]; else review[k] = r;
    saveReview();
  }

  function setStatus(row, status) {
    var k = rowKey(row);
    var r = review[k] || {};
    r.status = status;
    r.caseId = rowId(row);
    if (!r.status && !r.note) delete review[k]; else review[k] = r;
    saveReview();
  }

  function allRowText(row) {
    var out = [];
    (payload.fieldnames || []).forEach(function (field) { out.push(row.values[field] || ""); });
    return out.join("\n").toLowerCase();
  }

  function rowMatchesQuery(row, query) {
    if (!query) return true;
    return allRowText(row).indexOf(query.toLowerCase()) >= 0;
  }

  function getAbnormalField() {
    var abnormalField = "";
    (payload.fieldnames || []).forEach(function (field) {
      var norm = field.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      if (!abnormalField && (norm === "is_abnormal" || norm === "abnormal" || norm === "is_failure")) abnormalField = field;
    });
    return abnormalField;
  }

  function filteredRows() {
    var abnormalField = getAbnormalField();
    return (payload.rows || []).filter(function (row) {
      if (!rowMatchesQuery(row, state.query)) return false;
      if (state.statusFilter !== "all") {
        var value = abnormalField ? normalizeText(row.values[abnormalField]).trim().toLowerCase() : "";
        var isAbnormal = ["true", "1", "yes", "y", "异常", "失败", "fail", "failed", "bad"].indexOf(value) >= 0;
        if (state.statusFilter === "abnormal" && !isAbnormal) return false;
        if (state.statusFilter === "normal" && isAbnormal) return false;
      }
      return true;
    });
  }

  function sortedRows() {
    var rows = filteredRows().slice();
    if (!state.sortColumn) return rows;
    rows.sort(function (a, b) {
      var av = normalizeText(a.values[state.sortColumn]).toLowerCase();
      var bv = normalizeText(b.values[state.sortColumn]).toLowerCase();
      var an = Number(av);
      var bn = Number(bv);
      var result;
      if (!Number.isNaN(an) && !Number.isNaN(bn) && av !== "" && bv !== "") result = an - bn;
      else result = av < bv ? -1 : av > bv ? 1 : 0;
      return state.sortDirection === "asc" ? result : -result;
    });
    return rows;
  }

  function abnormalCount() {
    var rows = payload.rows || [];
    var abnormalField = "";
    (payload.fieldnames || []).forEach(function (field) {
      var norm = field.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      if (!abnormalField && (norm === "is_abnormal" || norm === "abnormal" || norm === "is_failure")) abnormalField = field;
    });
    if (!abnormalField) return rows.length;
    var count = 0;
    rows.forEach(function (row) {
      var value = normalizeText(row.values[abnormalField]).trim().toLowerCase();
      if (["true", "1", "yes", "y", "异常", "失败", "fail", "failed", "bad"].indexOf(value) >= 0) count += 1;
    });
    return count;
  }

  function reviewedCount() {
    var count = 0;
    (payload.rows || []).forEach(function (row) {
      var r = review[rowKey(row)];
      if (r && (r.status || r.note)) count += 1;
    });
    return count;
  }

  function setText(id, text) {
    var node = $(id);
    if (node) node.textContent = text;
  }

  function renderMeta() {
    var total = payload.rowCount || (payload.rows || []).length;
    var abnormal = abnormalCount();
    var rate = total ? Math.round((abnormal / total) * 100) : 0;
    setText("path-context", (payload.currentDir || "当前版本") + " → " + (payload.output || "failure_cases.html"));
    setText("standard-column", roleField("expected") || roleField("actual") || "无定位");
    setText("stat-total", total);
    setText("stat-fields", (payload.fieldnames || []).length);
    setText("stat-abnormal", abnormal);
    setText("stat-abnormal-rate", "失败率 " + rate + "%");
    setText("field-count", (payload.fieldnames || []).length);
  }

  function renderTabs() {
    document.querySelectorAll("[data-tab]").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-tab") === state.tab);
    });
    document.querySelectorAll("[data-view]").forEach(function (view) {
      view.classList.toggle("active", view.getAttribute("data-view") === state.tab);
    });
  }

  function renderColumnChips() {
    var container = $("column-chips");
    if (!container) return;
    container.innerHTML = "";
    var roleFields = {};
    ROLE_LIST.forEach(function (role) { if (roleField(role)) roleFields[roleField(role)] = role; });
    (payload.fieldnames || []).forEach(function (field) {
      var isVisible = !!state.visibleColumns[field];
      var role = roleFields[field] || "";
      var button = el("button", {
        type: "button",
        class: "column-chip" + (isVisible ? "" : " off") + ((role === "expected" || role === "actual") ? " gt" : ""),
        onclick: function () {
          state.visibleColumns[field] = !state.visibleColumns[field];
          renderColumnChips();
          renderTable();
        },
      }, [(isVisible ? "◉ " : "○ ") + field + (role ? " · " + ROLE_LABELS[role] : "")]);
      container.appendChild(button);
    });
  }

  function renderCell(row, field) {
    var value = normalizeText(row.values[field]);
    var normField = field.toLowerCase().replace(/[^a-z0-9]+/g, "_");
    if (normField === "is_abnormal" || normField === "abnormal" || normField === "is_failure") {
      var isAbnormal = ["true", "1", "yes", "y", "异常", "失败", "fail", "failed", "bad"].indexOf(value.toLowerCase()) >= 0;
      if (isAbnormal) {
        return el("div", { class: "cell-text" }, [
          el("span", { class: "badge issue", text: "● 异常" })
        ]);
      } else {
        return el("div", { class: "cell-text" }, [
          el("span", { class: "badge ok", text: "● 正常" })
        ]);
      }
    }
    var safeHref = row.safeLogHrefs && row.safeLogHrefs[field];
    if (safeHref) {
      return el("div", { class: "cell-text" }, [
        el("a", { href: safeHref, target: "_blank", rel: "noopener noreferrer", text: value || safeHref }),
      ]);
    }
    return el("div", { class: "cell-text", text: truncate(value || "（空）", config.snippetMaxChars || 240) });
  }

  function renderTable() {
    var head = $("table-head");
    var body = $("table-body");
    if (!head || !body) return;
    head.innerHTML = "";
    body.innerHTML = "";
    var columns = visibleColumns();
    var headerRow = el("tr");
    headerRow.appendChild(el("th", { class: "row-number-cell", text: "序号" }));
    columns.forEach(function (field) {
      headerRow.appendChild(el("th", {}, [
        el("button", {
          type: "button",
          onclick: function () {
            if (state.sortColumn === field) state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
            else {
              state.sortColumn = field;
              state.sortDirection = "asc";
            }
            renderTable();
          },
        }, [field + (state.sortColumn === field ? (state.sortDirection === "asc" ? " ↑" : " ↓") : "")]),
      ]));
    });
    head.appendChild(headerRow);

    var rows = sortedRows();
    setText("filtered-count", rows.length + " / " + (payload.rows || []).length);
    if ($("empty-state-all")) $("empty-state-all").hidden = (payload.rows || []).length !== 0;
    if ($("empty-state-filtered")) $("empty-state-filtered").hidden = !((payload.rows || []).length !== 0 && rows.length === 0);

    var pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
    if (state.page >= pages) state.page = pages - 1;
    if (state.page < 0) state.page = 0;
    var start = state.page * state.pageSize;
    rows.slice(start, start + state.pageSize).forEach(function (row) {
      var tr = el("tr", {
        class: row.rowNumber === state.selectedRowNumber ? "selected" : "",
        onclick: function () {
          state.selectedRowNumber = row.rowNumber;
          openInspector(row);
          renderTable();
        },
      });
      tr.appendChild(el("td", { class: "row-number-cell", text: "#" + row.rowNumber }));
      columns.forEach(function (field) { tr.appendChild(el("td", {}, [renderCell(row, field)])); });
      body.appendChild(tr);
    });
    var startRow = rows.length ? (state.page * state.pageSize + 1) : 0;
    var endRow = Math.min(rows.length, (state.page + 1) * state.pageSize);
    setText("pager-info", "显示为 " + startRow + " 至 " + endRow + " / 共 " + rows.length + " 条结果");
    setText("page-label", (state.page + 1) + " / " + pages + " 页");
    if ($("first")) $("first").disabled = state.page <= 0;
    if ($("prev")) $("prev").disabled = state.page <= 0;
    if ($("next")) $("next").disabled = state.page >= pages - 1;
    if ($("last")) $("last").disabled = state.page >= pages - 1;
  }

  function openInspector(row) {
    state.selectedRowNumber = row.rowNumber;
    setText("inspector-row-badge", "#" + row.rowNumber);
    setText("inspector-title", "异常数据详情");
    setText("inspector-subtitle", "case " + rowId(row) + " · " + (roleField("reason") || "保留全部 failure_cases.csv 字段"));
    renderInspectorBody(row);
    if ($("inspector-overlay-pane")) $("inspector-overlay-pane").hidden = false;
  }

  function closeInspector() {
    if ($("inspector-overlay-pane")) $("inspector-overlay-pane").hidden = true;
  }

  function roleForField(field) {
    for (var i = 0; i < ROLE_LIST.length; i++) {
      var role = ROLE_LIST[i];
      if (roleField(role) === field) return role;
    }
    return "";
  }

  function renderComparisonPaneContent(value) {
    var valStr = normalizeText(value).trim();
    var wrapper = el("div", { class: "comparison-content-inner" });
    if (!valStr) {
      wrapper.appendChild(el("pre", { text: "（空）" }));
      return wrapper;
    }
    var parsed = parseJsonValue(valStr);
    if (parsed) {
      var preJson = el("pre", { class: "json-pre" });
      preJson.innerHTML = formatJsonToHtml(parsed, "");
      wrapper.appendChild(preJson);
      extractCodeBlocks(parsed).forEach(function (block) {
        wrapper.appendChild(el("div", { class: "sub-code-pane" }, [
          el("div", { class: "sub-code-pane-head" }, [
            el("span", { text: "代码段 (" + block.key + ")" }),
            el("span", { class: "copy", text: "复制", onclick: function (ev) { copyText(block.value, ev.target); } }),
          ]),
          el("pre", { class: "code-pre", html: renderCodeHtml(block.value) }),
        ]));
      });
    } else if (isCodeLike(valStr)) {
      wrapper.appendChild(el("pre", { class: "code-pre", html: renderCodeHtml(valStr) }));
    } else {
      wrapper.appendChild(el("pre", { text: valStr }));
    }
    return wrapper;
  }

  function buildComparisonCard(row) {
    var expectedField = roleField("expected");
    var actualField = roleField("actual");
    if (!expectedField && !actualField) return null;

    var expectedValue = expectedField ? normalizeText(row.values[expectedField]) : "";
    var actualValue = actualField ? normalizeText(row.values[actualField]) : "";

    var card = el("div", { class: "comparison-card" }, [
      el("div", { class: "comparison-header" }, [
        el("div", { class: "comparison-title" }, [
          el("span", {
            html: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color: var(--brand); margin-right: 4px; vertical-align: middle;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>'
          }),
          el("span", { text: "核心输出结果比较 (RESULT COMPARISON)" })
        ]),
        el("span", { class: "comparison-subtitle", text: "左右滑轨对照比对" })
      ])
    ]);

    var body = el("div", { class: "comparison-body" });

    // Left Column (Agent 输出结果)
    var agentPane = el("div", { class: "comparison-pane agent-pane" });
    var agentHead = el("div", { class: "comparison-pane-head" }, [
      el("span", { class: "comparison-pane-title" }, [
        el("span", {
          html: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color: #64748b; margin-right: 4px; vertical-align: middle;"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>'
        }),
        el("span", { text: "Agent 输出结果" })
      ]),
      el("button", {
        type: "button",
        class: "copy-btn",
        text: "复制结果",
        onclick: function (ev) { copyText(actualValue, ev.target); }
      })
    ]);
    var agentContent = el("div", { class: "comparison-content" });
    agentContent.appendChild(renderComparisonPaneContent(actualValue));
    agentPane.appendChild(agentHead);
    agentPane.appendChild(agentContent);

    // Right Column (标准答案 (Ground Truth))
    var gtPane = el("div", { class: "comparison-pane gt-pane" });
    var gtHead = el("div", { class: "comparison-pane-head" }, [
      el("span", { class: "comparison-pane-title" }, [
        el("span", {
          html: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color: #d97706; margin-right: 4px; vertical-align: middle;"><circle cx="12" cy="8" r="7"></circle><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"></polyline></svg>'
        }),
        el("span", { text: "标准答案 (Ground Truth)" })
      ]),
      el("button", {
        type: "button",
        class: "copy-btn",
        text: "复制标准答案",
        onclick: function (ev) { copyText(expectedValue, ev.target); }
      })
    ]);
    var gtContent = el("div", { class: "comparison-content" });
    gtContent.appendChild(renderComparisonPaneContent(expectedValue));
    gtPane.appendChild(gtHead);
    gtPane.appendChild(gtContent);

    body.appendChild(agentPane);
    body.appendChild(gtPane);
    card.appendChild(body);
    return card;
  }

  function buildStatusBanner(row, isAbnormal, reasonField) {
    var banner = el("div", { class: "status-banner " + (isAbnormal ? "abnormal" : "normal") });
    
    var iconContainer = el("div", { class: "status-banner-icon" });
    if (isAbnormal) {
      iconContainer.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>';
    } else {
      iconContainer.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>';
    }
    
    var content = el("div", { class: "status-banner-content" });
    var header = el("div", { class: "status-banner-header" });
    var title = el("span", { class: "status-banner-title" });
    var badge = el("span", { class: "status-banner-badge" });
    
    if (isAbnormal) {
      title.textContent = "检测到合并异常 (ABNORMAL RUN)";
      badge.textContent = "FAIL";
      header.appendChild(title);
      header.appendChild(badge);
      content.appendChild(header);
      
      var reasonText = reasonField ? normalizeText(row.values[reasonField]) : "";
      var descBox = el("div", { class: "status-banner-desc-box" });
      var label = el("strong", { class: "status-banner-desc-label" }, ["异常原因描述："]);
      var textVal = el("span", { class: "status-banner-desc-val", text: reasonText || "无异常原因描述" });
      descBox.appendChild(label);
      descBox.appendChild(textVal);
      content.appendChild(descBox);
    } else {
      title.textContent = "模型对标测试完美通过 (NORMAL RUN)";
      badge.textContent = "PASS";
      header.appendChild(title);
      header.appendChild(badge);
      content.appendChild(header);
      
      var descVal = el("div", { class: "status-banner-desc-val text-only", text: "自动测试比对发现：Agent 输出与 ground_truth 完全符合期望约束或断言条件。" });
      content.appendChild(descVal);
    }
    
    banner.appendChild(iconContainer);
    banner.appendChild(content);
    return banner;
  }

  function buildLogCard(logContent) {
    var card = el("div", { class: "log-card" }, [
      el("div", { class: "log-card-header" }, [
        el("span", { class: "log-card-icon", text: ">_" }),
        el("span", { class: "log-card-title", text: "AGENT 本次执行日志 (AGENT CONSOLE LOGS)" })
      ]),
      el("div", { class: "log-card-body" }, [
        el("pre", {}, [
          el("code", { text: logContent })
        ])
      ])
    ]);
    return card;
  }

  function renderInspectorBody(row) {
    var container = $("inspector-body");
    if (!container) return;
    container.innerHTML = "";
    var left = el("div", { class: "inspector-column" });
    var right = el("div", { class: "inspector-column" });
    var fields = payload.fieldnames || [];

    var expectedField = roleField("expected");
    var actualField = roleField("actual");
    var reasonField = roleField("reason");
    var logField = roleField("log");

    var abnormalField = getAbnormalField();
    var abnormalValue = abnormalField ? normalizeText(row.values[abnormalField]).trim().toLowerCase() : "";
    var isAbnormal = ["true", "1", "yes", "y", "异常", "失败", "fail", "failed", "bad"].indexOf(abnormalValue) >= 0;

    var statusBanner = buildStatusBanner(row, isAbnormal, reasonField);
    if (statusBanner) {
      right.appendChild(statusBanner);
    }

    var comparisonCard = buildComparisonCard(row);
    if (comparisonCard) {
      right.appendChild(comparisonCard);
    }

    if (row.logContent) {
      var logCard = buildLogCard(row.logContent);
      if (logCard) {
        right.appendChild(logCard);
      }
    }

    fields.forEach(function (field) {
      if (field === expectedField || field === actualField || field === reasonField || field === logField) return;

      var value = normalizeText(row.values[field]);
      var role = roleForField(field);
      var pane = buildFieldPane(field, value, role ? ROLE_LABELS[role] : "原始字段");
      if (role === "expected" || role === "actual" || role === "reason") right.appendChild(pane);
      else left.appendChild(pane);
    });
    container.appendChild(left);
    container.appendChild(right);
  }

  function buildFieldPane(title, value, subtitle) {
    var valStr = normalizeText(value).trim();
    var pane = el("div", { class: "field-pane" }, [
      el("div", { class: "field-pane-head" }, [
        el("span", { text: title }),
        el("span", { text: subtitle || "原始字段" }),
        el("span", {
          class: "copy",
          text: "复制",
          onclick: function (ev) { copyText(value, ev.target); },
        }),
      ]),
    ]);
    if (!valStr) {
      pane.appendChild(el("pre", { text: "（空）" }));
      return pane;
    }

    var parsed = parseJsonValue(valStr);
    if (parsed) {
      var preJson = el("pre", { class: "json-pre" });
      preJson.innerHTML = formatJsonToHtml(parsed, "");
      pane.appendChild(preJson);
      extractCodeBlocks(parsed).forEach(function (block) {
        pane.appendChild(el("div", { class: "sub-code-pane" }, [
          el("div", { class: "sub-code-pane-head" }, [
            el("span", { text: "代码段 (" + block.key + ")" }),
            el("span", { class: "copy", text: "复制", onclick: function (ev) { copyText(block.value, ev.target); } }),
          ]),
          el("pre", { class: "code-pre", html: renderCodeHtml(block.value) }),
        ]));
      });
    } else if (isCodeLike(valStr)) {
      pane.appendChild(el("pre", { class: "code-pre", html: renderCodeHtml(valStr) }));
    } else {
      pane.appendChild(el("pre", { text: valStr }));
    }
    return pane;
  }

  function extractCodeBlocks(value) {
    var blocks = [];
    function walk(node, path) {
      if (blocks.length >= 8) return;
      if (typeof node === "string") {
        if (isCodeLike(node)) blocks.push({ key: path || "value", value: node });
        return;
      }
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node)) {
        for (var i = 0; i < node.length; i++) walk(node[i], path + "[" + i + "]");
      } else {
        Object.keys(node).forEach(function (key) { walk(node[key], path ? path + "." + key : key); });
      }
    }
    walk(value, "");
    return blocks;
  }

  function buildReviewBox(row) {
    var box = el("div", { class: "review-box" });
    box.appendChild(el("div", { class: "review-title" }, [
      el("span", { text: "人工复核" }),
      el("span", { text: "localStorage 本地保存" }),
    ]));
    var select = el("select", {
      class: "review-status",
      onchange: function (ev) {
        setStatus(row, ev.target.value);
        renderMeta();
      },
    }, [
      el("option", { value: "", text: "未审阅" }),
      el("option", { value: "confirmed_failure", text: "确认异常" }),
      el("option", { value: "false_positive", text: "误报/非异常" }),
      el("option", { value: "needs_followup", text: "需继续分析" }),
    ]);
    select.value = getStatus(row);
    var note = el("textarea", {
      class: "review-note",
      placeholder: "审阅备注（可选）：记录异常原因、修复建议或复核结论…",
      oninput: function (ev) {
        setNote(row, ev.target.value);
        renderMeta();
      },
    });
    note.value = getNote(row);
    box.appendChild(select);
    box.appendChild(note);
    return box;
  }

  function copyText(text, node) {
    var old = node.textContent;
    function done(ok) {
      node.textContent = ok ? "已复制" : "复制失败";
      setTimeout(function () { node.textContent = old; }, 1200);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(normalizeText(text)).then(
        function () { done(true); },
        function () { done(fallbackCopy(text)); }
      );
    } else done(fallbackCopy(text));
  }

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = normalizeText(text);
    document.body.appendChild(ta);
    ta.select();
    var copied = false;
    try { copied = document.execCommand("copy"); } catch (e) { copied = false; }
    document.body.removeChild(ta);
    return copied;
  }

  function csvCell(value) {
    var text = normalizeText(value);
    if (/^[\s]*[=+\-@]/.test(text)) text = "'" + text;
    if (/[",\r\n]/.test(text)) return '"' + text.replace(/"/g, '""') + '"';
    return text;
  }

  function exportReview() {
    var headers = (payload.fieldnames || []).slice();
    headers.push("review_status", "review_note");
    var lines = [headers.map(csvCell).join(",")];
    (payload.rows || []).forEach(function (row) {
      var r = review[rowKey(row)] || {};
      var values = (payload.fieldnames || []).map(function (field) { return csvCell(row.values[field]); });
      values.push(csvCell(r.status || ""), csvCell(r.note || ""));
      lines.push(values.join(","));
    });
    var blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    var url = URL.createObjectURL(blob);
    var a = el("a", { href: url, download: "failure_cases_review.csv" });
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }, 0);
  }

  function renderColumnList() {
    var list = $("column-list");
    if (!list) return;
    list.innerHTML = "";
    var query = state.columnQuery.toLowerCase();
    columnMeta.filter(function (meta) {
      return !query || meta.name.toLowerCase().indexOf(query) >= 0;
    }).forEach(function (meta) {
      var role = roleForField(meta.name);
      list.appendChild(el("button", {
        type: "button",
        class: "column-item" + (meta.name === state.selectedColumnName ? " active" : ""),
        onclick: function () {
          state.selectedColumnName = meta.name;
          renderStats();
        },
      }, [
        el("span", { class: "column-item-name", text: meta.name }),
        el("span", { class: "column-item-meta" }, [
          el("span", { text: role ? ROLE_LABELS[role] : meta.type }),
          el("span", { text: "完整度 " + meta.completionRate + "%" }),
        ]),
      ]));
    });
  }

  function renderStats() {
    renderMeta();
    renderColumnList();
    var detail = $("selected-column-detail");
    var histogram = $("length-histogram");
    if (!detail || !histogram) return;
    detail.innerHTML = "";
    histogram.innerHTML = "";
    histogram.style.display = "none"; // Hide the old separate histogram card
    
    var meta = columnMetaFor(state.selectedColumnName);
    if (!meta) {
      detail.appendChild(el("div", { class: "empty-state" }, [el("strong", { text: "请选择字段" })]));
      return;
    }
    
    var role = roleForField(meta.name);
    var labelText = role ? ROLE_LABELS[role] : meta.type;
    var badgeClass = "badge" + (role ? " gt" : "");
    var totalRows = (payload.rows || []).length;
    
    // Create Header Left
    var headerLeft = el("div", { class: "field-detail-header-left" }, [
      el("div", { class: "field-name-row" }, [
        el("span", { class: "field-icon-wrap", html: '<svg class="field-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>' }),
        el("span", { class: "field-name", text: meta.name })
      ]),
      el("div", { class: "field-meta-row" }, [
        el("span", { text: "数据类型: " }),
        el("span", { class: "meta-highlight type-tag", text: labelText }),
        el("span", { text: " • 填充完整度: " }),
        el("span", { class: "meta-highlight", text: meta.completionRate + "% (" + meta.nonEmptyCount + "/" + totalRows + ")" })
      ])
    ]);
    
    // Create Header Right (KPIs)
    var headerRight = el("div", { class: "field-detail-header-right" }, [
      el("div", { class: "kpi-col" }, [
        el("span", { class: "kpi-label", text: "最小长度" }),
        el("span", { class: "kpi-val" }, [
          document.createTextNode(meta.minLength + " "),
          el("span", { class: "unit", text: "字" })
        ])
      ]),
      el("div", { class: "kpi-divider" }),
      el("div", { class: "kpi-col" }, [
        el("span", { class: "kpi-label", text: "平均长度" }),
        el("span", { class: "kpi-val" }, [
          document.createTextNode(meta.avgLength + " "),
          el("span", { class: "unit", text: "字" })
        ])
      ]),
      el("div", { class: "kpi-divider" }),
      el("div", { class: "kpi-col" }, [
        el("span", { class: "kpi-label", text: "最大长度" }),
        el("span", { class: "kpi-val" }, [
          document.createTextNode(meta.maxLength + " "),
          el("span", { class: "unit", text: "字" })
        ])
      ])
    ]);
    
    var header = el("div", { class: "field-detail-header" }, [headerLeft, headerRight]);
    var hr = el("hr", { class: "field-detail-hr" });
    
    // Chart Panel (Left Column)
    var buckets = buildLengthBuckets(meta.name);
    var maxCount = buckets.reduce(function (m, b) { return Math.max(m, b.count); }, 0) || 1;
    
    var yGrid = el("div", { class: "y-grid-lines" }, [
      el("div", { class: "grid-line" }),
      el("div", { class: "grid-line" }),
      el("div", { class: "grid-line" }),
      el("div", { class: "grid-line" })
    ]);
    
    var barsContainer = el("div", { class: "bars-container" });
    buckets.forEach(function (bucket) {
      var heightPercent = Math.round((bucket.count / maxCount) * 100);
      var barFill = el("div", {
        class: "bar-fill-vertical",
        style: "height: " + heightPercent + "%"
      });
      var barTooltip = el("div", { class: "bar-tooltip", text: bucket.count + " 条" });
      var barWrapper = el("div", { class: "bar-wrapper" }, [barTooltip, barFill]);
      var barLabel = el("span", { class: "bar-label", text: bucket.label });
      
      var barCol = el("div", { class: "bar-col" }, [barWrapper, barLabel]);
      barsContainer.appendChild(barCol);
    });
    
    var chartContainer = el("div", { class: "chart-container" }, [yGrid, barsContainer]);
    var chartFooter = el("div", { class: "chart-footer", text: "横坐标：单条数据字符数 (分 " + buckets.length + " 个长度区间)" });
    
    var chartPanel = el("div", { class: "field-detail-chart-panel" }, [
      el("h4", { class: "panel-title", text: "文本字数长度分布" }),
      chartContainer,
      chartFooter
    ]);
    
    // Samples Panel (Right Column)
    var samplesContainer = el("div", { class: "samples-container" });
    var displaySamples = meta.sampleValues.slice(0, 3);
    displaySamples.forEach(function (value, index) {
      var bodyContainer = el("div", { class: "sample-item-body" });
      bodyContainer.appendChild(renderValueContent(value));
      var sampleCard = el("div", { class: "sample-item-card" }, [
        el("div", { class: "sample-item-header", text: "样本 #" + (index + 1) }),
        bodyContainer
      ]);
      samplesContainer.appendChild(sampleCard);
    });
    
    var samplesPanel = el("div", { class: "field-detail-samples-panel" }, [
      el("h4", { class: "panel-title", text: "样例数据提取 (前 " + displaySamples.length + " 条)" }),
      samplesContainer
    ]);
    
    // Body Layout
    var body = el("div", { class: "field-detail-body" }, [chartPanel, samplesPanel]);
    
    detail.appendChild(header);
    detail.appendChild(hr);
    detail.appendChild(body);
  }

  function renderValueContent(value) {
    var valStr = normalizeText(value).trim();
    var container = el("div", { class: "formatted-content-wrap" });
    if (!valStr) {
      container.appendChild(el("pre", { text: "（空）" }));
      return container;
    }

    var parsed = parseJsonValue(valStr);
    if (parsed) {
      var preJson = el("pre", { class: "json-pre" });
      preJson.innerHTML = formatJsonToHtml(parsed, "");
      container.appendChild(preJson);
      extractCodeBlocks(parsed).forEach(function (block) {
        container.appendChild(el("div", { class: "sub-code-pane" }, [
          el("div", { class: "sub-code-pane-head" }, [
            el("span", { text: "代码段 (" + block.key + ")" }),
            el("span", { class: "copy", text: "复制", onclick: function (ev) { copyText(block.value, ev.target); } }),
          ]),
          el("pre", { class: "code-pre", html: renderCodeHtml(block.value) }),
        ]));
      });
    } else if (isCodeLike(valStr)) {
      container.appendChild(el("pre", { class: "code-pre", html: renderCodeHtml(valStr) }));
    } else {
      container.appendChild(el("pre", { text: valStr }));
    }
    return container;
  }

  function buildFieldKpi(label, value) {
    return el("div", { class: "field-kpi" }, [el("span", { text: label }), el("strong", { text: value })]);
  }

  function buildLengthBuckets(field) {
    var lengths = (payload.rows || []).map(function (row) { return normalizeText(row.values[field]).length; });
    if (!lengths.length) return [];
    var min = Math.min.apply(Math, lengths);
    var max = Math.max.apply(Math, lengths);
    
    var buckets = [];
    if (max - min <= 8) {
      // Create a bucket for each integer length from min to max
      for (var l = min; l <= max; l++) {
        buckets.push({ label: String(l), count: 0, min: l, max: l });
      }
      lengths.forEach(function (len) {
        for (var b = 0; b < buckets.length; b++) {
          if (buckets[b].min === len) {
            buckets[b].count += 1;
            break;
          }
        }
      });
    } else {
      // Group into 8 buckets
      var range = max - min + 1;
      var step = Math.max(1, Math.ceil(range / 8));
      for (var i = 0; i < 8; i++) {
        var bMin = min + i * step;
        var bMax = min + (i + 1) * step - 1;
        if (i === 7) bMax = max;
        var label = bMin === bMax ? String(bMin) : (bMin + "-" + bMax);
        buckets.push({ label: label, count: 0, min: bMin, max: bMax });
      }
      lengths.forEach(function (len) {
        var placed = false;
        for (var b = 0; b < buckets.length; b++) {
          if (len >= buckets[b].min && len <= buckets[b].max) {
            buckets[b].count += 1;
            placed = true;
            break;
          }
        }
        if (!placed && buckets.length > 0) {
          buckets[buckets.length - 1].count += 1;
        }
      });
    }
    return buckets;
  }



  function initPageSizes() {
    var select = $("page-size");
    if (!select) return;
    select.innerHTML = "";
    (config.pageSizes || [25, 50, 100, 250]).forEach(function (size) {
      var option = el("option", { value: String(size), text: size + " 条" });
      if (size === state.pageSize) option.selected = true;
      select.appendChild(option);
    });
  }

  function getStatusCounts() {
    var rows = payload.rows || [];
    var abnormalField = getAbnormalField();
    var counts = { all: rows.length, normal: 0, abnormal: 0 };
    rows.forEach(function (row) {
      var value = abnormalField ? normalizeText(row.values[abnormalField]).trim().toLowerCase() : "";
      var isAbnormal = ["true", "1", "yes", "y", "异常", "失败", "fail", "failed", "bad"].indexOf(value) >= 0;
      if (isAbnormal) counts.abnormal += 1;
      else counts.normal += 1;
    });
    return counts;
  }

  function renderStatusFilters() {
    var container = $("status-filter-group");
    if (!container) return;
    container.innerHTML = "";
    var counts = getStatusCounts();

    var allBtn = el("button", {
      type: "button",
      class: "status-filter-btn" + (state.statusFilter === "all" ? " active" : ""),
      onclick: function () {
        state.statusFilter = "all";
        state.page = 0;
        renderStatusFilters();
        renderTable();
      }
    }, ["全部结果 (" + counts.all + ")"]);

    var normalBtn = el("button", {
      type: "button",
      class: "status-filter-btn" + (state.statusFilter === "normal" ? " active" : ""),
      onclick: function () {
        state.statusFilter = "normal";
        state.page = 0;
        renderStatusFilters();
        renderTable();
      }
    }, [
      el("span", { html: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>' }),
      "正常 (" + counts.normal + ")"
    ]);

    var abnormalBtn = el("button", {
      type: "button",
      class: "status-filter-btn" + (state.statusFilter === "abnormal" ? " active" : ""),
      onclick: function () {
        state.statusFilter = "abnormal";
        state.page = 0;
        renderStatusFilters();
        renderTable();
      }
    }, [
      el("span", { html: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle;"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>' }),
      "异常 (" + counts.abnormal + ")"
    ]);

    container.appendChild(allBtn);
    container.appendChild(normalBtn);
    container.appendChild(abnormalBtn);
  }

  function resetFilters() {
    state.query = "";
    globalSearch = "";
    state.sortColumn = "";
    state.sortDirection = "asc";
    state.page = 0;
    state.statusFilter = "all";
    if ($("search")) $("search").value = "";
    renderStatusFilters();
    renderTable();
  }

  function bindEvents() {
    if ($("search")) $("search").addEventListener("input", function (ev) {
      globalSearch = ev.target.value;
      state.query = globalSearch;
      state.page = 0;
      renderTable();
    });
    if ($("column-search")) $("column-search").addEventListener("input", function (ev) {
      state.columnQuery = ev.target.value;
      renderColumnList();
    });
    if ($("clear-filters-btn")) $("clear-filters-btn").addEventListener("click", resetFilters);
    if ($("first")) $("first").addEventListener("click", function () { state.page = 0; renderTable(); });
    if ($("prev")) $("prev").addEventListener("click", function () { state.page -= 1; renderTable(); });
    if ($("next")) $("next").addEventListener("click", function () { state.page += 1; renderTable(); });
    if ($("last")) $("last").addEventListener("click", function () {
      var rows = sortedRows();
      var pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
      state.page = pages - 1;
      renderTable();
    });
    if ($("page-size")) $("page-size").addEventListener("change", function (ev) {
      state.pageSize = parseInt(ev.target.value, 10) || config.defaultPageSize || 50;
      state.page = 0;
      renderTable();
    });
    if ($("inspector-close")) $("inspector-close").addEventListener("click", closeInspector);
    if ($("inspector-overlay-pane")) $("inspector-overlay-pane").addEventListener("click", function (ev) {
      if (ev.target === $("inspector-overlay-pane")) closeInspector();
    });
    document.querySelectorAll("[data-tab]").forEach(function (button) {
      button.addEventListener("click", function () {
        state.tab = button.getAttribute("data-tab");
        renderTabs();
      });
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        closeInspector();
      }
    });
  }

  function init() {
    renderMeta();
    renderTabs();
    renderColumnChips();
    initPageSizes();
    renderStatusFilters();
    renderTable();
    renderStats();
    bindEvents();
  }

  init();
})();
