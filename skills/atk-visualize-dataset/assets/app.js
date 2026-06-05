(function () {
  "use strict";

  var dataNode = document.getElementById("dataset-data");
  var payload = JSON.parse(dataNode.textContent);
  var roles = JSON.parse(JSON.stringify(payload.roles || {}));
  var facets = payload.facets || [];
  var issueLabels = payload.issueLabels || {};
  var issueOrder = payload.issueOrder || [];
  var config = payload.config || { snippetMaxChars: 240, pageSizes: [25, 50, 100, 250], defaultPageSize: 50 };

  var STORAGE_KEY = "atk-dataset-review:" + (payload.datasetPath || "dataset.csv");
  var review = loadReview();
  var columnMeta = buildColumnMeta();
  var initialGt = roleField("expected");

  var state = {
    tab: "grid",
    query: "",
    activeFacets: {},
    issueFilter: "",
    pageSize: config.defaultPageSize,
    page: 0,
    selectedRowNumber: payload.rows[0] ? payload.rows[0].rowNumber : null,
    sortColumn: "",
    sortDirection: "asc",
    visibleColumns: initialVisibleColumns(),
    selectedColumnName: payload.fieldnames[0] || "",
    columnQuery: "",
    showEmptyFields: false,
  };

  var ROLE_LIST = ["id", "input", "expected"];
  var ROLE_LABELS = { id: "ID", input: "输入", expected: "ground_truth" };
  var VERDICTS = [
    { key: "ok", label: "符合预期", cls: "active-ok", dot: "v-ok" },
    { key: "warn", label: "存疑", cls: "active-warn", dot: "v-warn" },
    { key: "bad", label: "需修正", cls: "active-bad", dot: "v-bad" },
  ];

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

  function truncate(value, maxChars) {
    var text = normalizeText(value).replace(/\s+/g, " ").trim();
    if (text.length <= maxChars) return text;
    return text.slice(0, Math.max(0, maxChars - 1)) + "…";
  }

  function roleField(role) {
    return (roles[role] && roles[role].field) || "";
  }

  function initialVisibleColumns() {
    var out = {};
    var gt = roleField("expected");
    for (var i = 0; i < payload.fieldnames.length; i++) {
      var field = payload.fieldnames[i];
      out[field] = i < 5 || field === gt;
    }
    return out;
  }

  function visibleColumns() {
    var out = [];
    var gt = roleField("expected");
    for (var i = 0; i < payload.fieldnames.length; i++) {
      var field = payload.fieldnames[i];
      if (field === gt) state.visibleColumns[field] = true;
      if (state.visibleColumns[field]) out.push(field);
    }
    return out;
  }

  function buildColumnMeta() {
    var rows = payload.rows || [];
    return (payload.fieldnames || []).map(function (field) {
      var nonEmpty = 0;
      var sampleValues = [];
      var typeCounts = { string: 0, number: 0, boolean: 0, empty: 0 };
      var lengths = [];
      rows.forEach(function (row) {
        var value = row.values ? row.values[field] : "";
        var text = normalizeText(value);
        if (!text.trim()) {
          typeCounts.empty += 1;
          return;
        }
        nonEmpty += 1;
        lengths.push(text.length);
        if (sampleValues.length < 5) sampleValues.push(text);
        if (/^-?\d+(\.\d+)?$/.test(text)) typeCounts.number += 1;
        else if (/^(true|false)$/i.test(text)) typeCounts.boolean += 1;
        else typeCounts.string += 1;
      });
      var inferred = "string";
      if (typeCounts.number > typeCounts.string && typeCounts.number >= typeCounts.boolean) inferred = "number";
      if (typeCounts.boolean > typeCounts.string && typeCounts.boolean > typeCounts.number) inferred = "boolean";
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
    return (row.atkId != null && row.atkId !== "") ? ("id:" + row.atkId + ":row:" + row.rowNumber) : ("row:" + row.rowNumber);
  }

  function getVerdict(row) {
    var r = review[rowKey(row)];
    return r ? r.verdict || "" : "";
  }

  function getNote(row) {
    var r = review[rowKey(row)];
    return r ? r.note || "" : "";
  }

  function setVerdict(row, verdict) {
    var k = rowKey(row);
    var r = review[k] || {};
    if (r.verdict === verdict) verdict = "";
    r.verdict = verdict;
    r.atkId = row.atkId;
    if (!r.verdict && !r.note) delete review[k]; else review[k] = r;
    saveReview();
  }

  function setNote(row, note) {
    var k = rowKey(row);
    var r = review[k] || {};
    r.note = note;
    r.atkId = row.atkId;
    if (!r.verdict && !r.note) delete review[k]; else review[k] = r;
    saveReview();
  }

  function rowMatchesQuery(row, query) {
    if (!query) return true;
    var lower = query.toLowerCase();
    for (var key in row.values) {
      if (normalizeText(row.values[key]).toLowerCase().indexOf(lower) >= 0) return true;
    }
    return false;
  }

  function rowMatchesFacets(row) {
    for (var field in state.activeFacets) {
      var selected = state.activeFacets[field];
      if (!selected || !selected.length) continue;
      if (selected.indexOf((row.values[field] || "").trim()) < 0) return false;
    }
    return true;
  }

  function rowMatchesIssue(row) {
    var filter = state.issueFilter;
    if (!filter) return true;
    if (filter === "any") return row.issues && row.issues.length > 0;
    if (filter === "reviewed") return !!getVerdict(row);
    if (filter === "unreviewed") return !getVerdict(row);
    return row.issues && row.issues.indexOf(filter) >= 0;
  }

  function filteredRows() {
    return payload.rows.filter(function (row) {
      return rowMatchesQuery(row, state.query) && rowMatchesFacets(row) && rowMatchesIssue(row);
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

  function reviewCounts() {
    var counts = { ok: 0, warn: 0, bad: 0, reviewed: 0, total: payload.rows.length };
    payload.rows.forEach(function (row) {
      var verdict = getVerdict(row);
      if (counts[verdict] != null) counts[verdict] += 1;
    });
    counts.reviewed = counts.ok + counts.warn + counts.bad;
    return counts;
  }

  function issueRowCount() {
    var count = 0;
    payload.rows.forEach(function (row) {
      if (row.issues && row.issues.length) count += 1;
    });
    return count;
  }

  function renderMeta() {
    var gt = roleField("expected");
    var gtFilled = 0;
    if (gt) {
      payload.rows.forEach(function (row) {
        if (normalizeText(row.values[gt]).trim()) gtFilled += 1;
      });
    }
    var reviews = reviewCounts();
    $("meta-rows").textContent = payload.rowCount + " 行";
    $("meta-fields").textContent = (payload.fieldnames || []).length + " 列";
    $("meta-issues").textContent = issueRowCount() + " 问题行";
    $("meta-review").textContent = reviews.reviewed ? ("已审 " + reviews.reviewed + "/" + reviews.total) : "未审阅";
    $("path-context").textContent = (payload.datasetPath || "dataset.csv") + " → " + (payload.output || "dataset.html");
    $("standard-column").textContent = gt || "无定位";
    $("stat-total").textContent = payload.rowCount;
    $("stat-fields").textContent = (payload.fieldnames || []).length;
    $("stat-gt").textContent = gt && payload.rowCount ? (Math.round((gtFilled / payload.rowCount) * 100) + "%") : "N/A";
    $("field-count").textContent = (payload.fieldnames || []).length;
  }

  function renderTabs() {
    document.querySelectorAll("[data-tab]").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-tab") === state.tab);
    });
    document.querySelectorAll("[data-view]").forEach(function (view) {
      view.classList.toggle("active", view.getAttribute("data-view") === state.tab);
    });
  }

  function renderQualityBar() {
    var bar = $("quality-bar");
    bar.innerHTML = "";
    bar.hidden = false;
    bar.appendChild(el("span", { class: "label", text: "dataset quality lint" }));
    var counts = {};
    var anyIssue = 0;
    payload.rows.forEach(function (row) {
      var issues = row.issues || [];
      if (issues.length) anyIssue += 1;
      issues.forEach(function (issue) { counts[issue] = (counts[issue] || 0) + 1; });
    });
    function chip(code, label, count, disabled) {
      var button = el("button", {
        type: "button",
        class: state.issueFilter === code ? "active" : "",
        onclick: function () {
          state.issueFilter = state.issueFilter === code ? "" : code;
          state.page = 0;
          renderQualityBar();
          renderTable();
        },
      }, [label, el("span", { class: "count", text: String(count) })]);
      if (disabled) button.disabled = true;
      return button;
    }
    bar.appendChild(chip("", "全部", payload.rows.length, false));
    bar.appendChild(chip("any", "有问题", anyIssue, anyIssue === 0));
    issueOrder.forEach(function (code) {
      if (counts[code]) bar.appendChild(chip(code, issueLabels[code] || code, counts[code], false));
    });
    var rc = reviewCounts();
    bar.appendChild(chip("reviewed", "已审阅", rc.reviewed, rc.reviewed === 0));
    bar.appendChild(chip("unreviewed", "待审阅", rc.total - rc.reviewed, rc.total - rc.reviewed === 0));
  }

  function renderColumnChips() {
    var container = $("column-chips");
    container.innerHTML = "";
    var gt = roleField("expected");
    payload.fieldnames.forEach(function (field) {
      var isVisible = !!state.visibleColumns[field];
      var button = el("button", {
        type: "button",
        class: "column-chip" + (isVisible ? "" : " off") + (field === gt ? " gt" : ""),
        onclick: function () {
          if (field === gt) state.visibleColumns[field] = true;
          else state.visibleColumns[field] = !state.visibleColumns[field];
          renderColumnChips();
          renderTable();
        },
      }, [(isVisible ? "◉ " : "○ ") + field]);
      container.appendChild(button);
    });
  }

  function renderFacets() {
    var container = $("facets");
    container.innerHTML = "";
    facets.forEach(function (facet) {
      var selected = state.activeFacets[facet.field] || [];
      var box = el("div", { class: "facet" });
      box.appendChild(el("div", { class: "facet-title" }, [
        el("span", { text: facet.field }),
        selected.length ? el("span", {
          class: "clear",
          text: "清除",
          onclick: function () {
            state.activeFacets[facet.field] = [];
            state.page = 0;
            renderFacets();
            renderTable();
          },
        }) : null,
      ]));
      var options = el("div", { class: "facet-options" });
      facet.values.forEach(function (entry) {
        var checked = selected.indexOf(entry.value) >= 0;
        options.appendChild(el("label", { class: "facet-option" }, [
          el("input", {
            type: "checkbox",
            checked: checked ? "checked" : null,
            onchange: function (ev) {
              var current = state.activeFacets[facet.field] || [];
              if (ev.target.checked) {
                if (current.indexOf(entry.value) < 0) current.push(entry.value);
              } else {
                current = current.filter(function (value) { return value !== entry.value; });
              }
              state.activeFacets[facet.field] = current;
              state.page = 0;
              renderTable();
            },
          }),
          el("span", { class: "label", text: entry.value || "(空)" }),
          el("span", { class: "count", text: String(entry.count) }),
        ]));
      });
      box.appendChild(options);
      container.appendChild(box);
    });
  }

  function renderTable() {
    var columns = visibleColumns();
    var head = $("table-head");
    var body = $("table-body");
    head.innerHTML = "";
    body.innerHTML = "";
    var headerRow = el("tr");
    headerRow.appendChild(el("th", { class: "row-number-cell", text: "序号" }));
    columns.forEach(function (field) {
      headerRow.appendChild(el("th", {}, [
        el("button", {
          type: "button",
          onclick: function () {
            if (state.sortColumn === field) {
              state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
            } else {
              state.sortColumn = field;
              state.sortDirection = "asc";
            }
            renderTable();
          },
        }, [field + (state.sortColumn === field ? (state.sortDirection === "asc" ? " ↑" : " ↓") : "")]),
      ]));
    });
    headerRow.appendChild(el("th", { text: "审阅/问题" }));
    head.appendChild(headerRow);

    var rows = sortedRows();
    $("filtered-count").textContent = rows.length + " / " + payload.rows.length;
    $("empty-state-all").hidden = payload.rows.length !== 0;
    $("empty-state-filtered").hidden = !(payload.rows.length !== 0 && rows.length === 0);

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
      columns.forEach(function (field) {
        tr.appendChild(el("td", {}, [el("div", { class: "cell-text", text: normalizeText(row.values[field]) || "（空）" })]));
      });
      tr.appendChild(el("td", {}, buildRowBadges(row)));
      body.appendChild(tr);
    });
    $("page-label").textContent = "第 " + (state.page + 1) + " / " + pages + " 页";
    $("prev").disabled = state.page <= 0;
    $("next").disabled = state.page >= pages - 1;
  }

  function buildRowBadges(row) {
    var out = [];
    var verdict = getVerdict(row);
    if (verdict) {
      var meta = VERDICTS.filter(function (candidate) { return candidate.key === verdict; })[0];
      out.push(el("span", { class: "badge", text: meta ? meta.label : verdict }));
    }
    (row.issues || []).slice(0, 3).forEach(function (issue) {
      var warnish = issue === "gt_too_long" || issue === "gt_too_short" || issue === "duplicate";
      out.push(el("span", { class: "badge " + (warnish ? "issue-warn" : "issue"), text: issueLabels[issue] || issue }));
    });
    if (!out.length) out.push(el("span", { class: "badge", text: "未校验" }));
    return out;
  }

  function findRow(rowNumber) {
    for (var i = 0; i < payload.rows.length; i++) {
      if (payload.rows[i].rowNumber === rowNumber) return payload.rows[i];
    }
    return null;
  }

  function openInspector(row) {
    state.selectedRowNumber = row.rowNumber;
    $("inspector-row-badge").textContent = "#" + row.rowNumber;
    $("inspector-title").textContent = "数据集详情";
    $("inspector-subtitle").textContent = row.atkId ? ("atk_id " + row.atkId) : "未识别 atk_id";
    renderInspectorBody(row);
    $("inspector-overlay-pane").hidden = false;
  }

  function closeInspector() {
    $("inspector-overlay-pane").hidden = true;
  }

  function renderInspectorBody(row) {
    var container = $("inspector-body");
    container.innerHTML = "";
    var gtField = roleField("expected");
    var idField = roleField("id");
    var left = el("div", { class: "inspector-column" });
    var right = el("div", { class: "inspector-column" });
    var hiddenEmpty = 0;
    payload.fieldnames.forEach(function (field) {
      if (field === gtField || field === idField) return;
      var value = normalizeText(row.values[field]);
      if (!value.trim() && !state.showEmptyFields) {
        hiddenEmpty += 1;
        return;
      }
      left.appendChild(buildFieldPane(field, value, "输入与上下文参数"));
    });
    left.insertBefore(el("div", { class: "field-switcher" }, [
      el("span", { class: "field-switcher-title", text: "输入与上下文参数 (Inputs & Metadata)" }),
      el("label", { class: "facet-option" }, [
        el("input", {
          type: "checkbox",
          checked: state.showEmptyFields ? "checked" : null,
          onchange: function (ev) {
            state.showEmptyFields = ev.target.checked;
            renderInspectorBody(row);
          },
        }),
        el("span", { class: "label", text: "显示空字段 (" + hiddenEmpty + " 个折叠)" }),
      ]),
    ]), left.firstChild);

    var gtValue = gtField ? normalizeText(row.values[gtField]) : "";
    right.appendChild(el("div", { class: "field-pane gt-box" + (gtValue.trim() ? "" : " empty") }, [
      el("div", { class: "field-pane-head" }, [
        el("span", { text: gtValue.trim() ? "GROUND TRUTH" : "GROUND TRUTH 为空" }),
        el("span", { text: gtField || "未识别 ground_truth 列" }),
      ]),
      el("div", { class: "gt-text", text: gtValue.trim() ? gtValue : "该行缺少期望结果，请补充。" }),
    ]));
    right.appendChild(buildReviewBox(row));
    var meta = "Row Number: " + row.rowNumber;
    if (row.atkId) meta += "\nATK ID: " + row.atkId;
    right.appendChild(buildFieldPane("METADATA", meta, idField || "未识别 ID 列"));
    container.appendChild(left);
    container.appendChild(right);
  }

  function buildFieldPane(title, value, subtitle) {
    return el("div", { class: "field-pane" }, [
      el("div", { class: "field-pane-head" }, [
        el("span", { text: title }),
        el("span", {
          class: "copy",
          text: "复制",
          onclick: function (ev) { copyText(value, ev.target); },
        }),
      ]),
      el("pre", { text: normalizeText(value).trim() ? value : "（空）" }),
    ]);
  }

  function buildReviewBox(row) {
    var box = el("div", { class: "review-box" });
    box.appendChild(el("div", { class: "review-title" }, [
      el("span", { text: "人工审阅工具" }),
      el("span", { text: "本地保存" }),
    ]));
    var group = el("div", { class: "verdict-group" });
    var verdict = getVerdict(row);
    VERDICTS.forEach(function (vd) {
      group.appendChild(el("button", {
        type: "button",
        class: "verdict-btn" + (verdict === vd.key ? " " + vd.cls : ""),
        onclick: function () {
          setVerdict(row, vd.key);
          renderMeta();
          renderQualityBar();
          renderTable();
          renderInspectorBody(row);
        },
      }, [vd.label]));
    });
    box.appendChild(group);
    var note = el("textarea", {
      class: "review-note",
      placeholder: "审阅备注（可选）：记录问题、建议修正方式…",
      oninput: function (ev) {
        setNote(row, ev.target.value);
        renderMeta();
      },
    });
    note.value = getNote(row);
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
        function () { done(fallbackCopy(text)); },
      );
    } else {
      done(fallbackCopy(text));
    }
  }

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = normalizeText(text);
    document.body.appendChild(ta);
    ta.select();
    var copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (e) {
      copied = false;
    }
    document.body.removeChild(ta);
    return copied;
  }

  function exportReview() {
    var headers = ["atk_id", "row_number", "verdict", "note", "detected_issues"];
    var lines = [headers.join(",")];
    payload.rows.forEach(function (row) {
      var r = review[rowKey(row)];
      var verdict = r ? (r.verdict || "") : "";
      var note = r ? (r.note || "") : "";
      var issues = (row.issues || []).join("; ");
      if (!verdict && !note && !issues) return;
      lines.push([
        csvCell(row.atkId == null ? "" : row.atkId),
        csvCell(row.rowNumber),
        csvCell(verdict),
        csvCell(note),
        csvCell(issues),
      ].join(","));
    });
    var blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    var url = URL.createObjectURL(blob);
    var a = el("a", { href: url, download: "dataset_review.csv" });
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }, 0);
  }

  function csvCell(value) {
    var text = normalizeText(value);
    if (/[",\r\n]/.test(text)) return '"' + text.replace(/"/g, '""') + '"';
    return text;
  }

  function renderColumnList() {
    var list = $("column-list");
    list.innerHTML = "";
    var query = state.columnQuery.toLowerCase();
    columnMeta.filter(function (meta) {
      return !query || meta.name.toLowerCase().indexOf(query) >= 0;
    }).forEach(function (meta) {
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
          el("span", { text: meta.type }),
          el("span", { text: "完整度 " + meta.completionRate + "%" }),
        ]),
      ]));
    });
  }

  function renderStats() {
    renderColumnList();
    var detail = $("selected-column-detail");
    var histogram = $("length-histogram");
    detail.innerHTML = "";
    histogram.innerHTML = "";
    var meta = columnMetaFor(state.selectedColumnName);
    if (!meta) {
      detail.appendChild(el("div", { class: "empty-state" }, [el("strong", { text: "请选择字段" })]));
      return;
    }
    var isGt = meta.name === roleField("expected");
    detail.appendChild(el("h3", { text: meta.name }));
    detail.appendChild(el("span", { class: "badge" + (isGt ? " gt" : ""), text: isGt ? "标准参考列" : meta.type }));
    detail.appendChild(el("div", { class: "field-kpis" }, [
      buildFieldKpi("非空", meta.nonEmptyCount + "/" + payload.rows.length),
      buildFieldKpi("完整度", meta.completionRate + "%"),
      buildFieldKpi("平均长度", meta.avgLength + " 字"),
    ]));
    var samples = el("div", { class: "sample-values" });
    meta.sampleValues.forEach(function (value) {
      samples.appendChild(el("span", { class: "badge", text: truncate(value, 80) }));
    });
    detail.appendChild(samples);

    histogram.appendChild(el("h3", { text: "字段长度分布" }));
    var buckets = buildLengthBuckets(meta.name);
    var max = buckets.reduce(function (m, b) { return Math.max(m, b.count); }, 0) || 1;
    var bars = el("div", { class: "bars" });
    buckets.forEach(function (bucket) {
      bars.appendChild(el("div", { class: "bar-row" }, [
        el("span", { text: bucket.label }),
        el("span", { class: "bar-track" }, [
          el("span", { class: "bar-fill", style: "width:" + Math.round((bucket.count / max) * 100) + "%" }),
        ]),
        el("span", { text: String(bucket.count) }),
      ]));
    });
    histogram.appendChild(bars);
  }

  function buildFieldKpi(label, value) {
    return el("div", { class: "field-kpi" }, [
      el("span", { text: label }),
      el("strong", { text: value }),
    ]);
  }

  function buildLengthBuckets(field) {
    var lengths = payload.rows.map(function (row) { return normalizeText(row.values[field]).length; });
    var max = lengths.length ? Math.max.apply(Math, lengths) : 0;
    var step = Math.max(1, Math.ceil(max / 8));
    var buckets = [];
    for (var i = 0; i < 8; i++) {
      buckets.push({ min: i * step, max: (i + 1) * step - 1, count: 0 });
    }
    lengths.forEach(function (len) {
      var index = Math.min(7, Math.floor(len / step));
      buckets[index].count += 1;
    });
    return buckets.map(function (bucket) {
      return { label: bucket.min + "-" + bucket.max + " 字", count: bucket.count };
    });
  }

  function openDrawer(section) {
    $("drawer").classList.add("open");
    $("drawer-backdrop").classList.add("open");
    document.querySelectorAll(".drawer-section").forEach(function (node) {
      node.style.display = node.getAttribute("data-section") === section ? "block" : "none";
    });
    $("drawer-title").textContent = section === "roles" ? "字段角色映射" : "解析警告";
  }

  function closeDrawer() {
    $("drawer").classList.remove("open");
    $("drawer-backdrop").classList.remove("open");
  }

  function renderDrawerRoles() {
    var container = $("drawer-roles");
    container.innerHTML = "";
    ROLE_LIST.forEach(function (role) {
      var current = roleField(role);
      var source = (roles[role] && roles[role].source) || "manual";
      var row = el("div", { class: "role-row" });
      row.appendChild(el("span", { class: "name", text: ROLE_LABELS[role] }));
      var select = el("select", {
        onchange: function (ev) {
          roles[role] = { field: ev.target.value, source: "manual" };
          if (role === "expected") state.visibleColumns[ev.target.value] = true;
          columnMeta = buildColumnMeta();
          renderMeta();
          renderColumnChips();
          renderTable();
          renderStats();
          renderDrawerRoles();
        },
      });
      select.appendChild(el("option", { value: "", text: "（未映射）" }));
      payload.fieldnames.forEach(function (field) {
        var opt = el("option", { value: field, text: field });
        if (field === current) opt.selected = true;
        select.appendChild(opt);
      });
      row.appendChild(select);
      row.appendChild(el("span", { class: "source", text: source === "auto" ? "auto-detected" : "manual/unmapped" }));
      container.appendChild(row);
    });
  }

  function renderDrawerWarnings() {
    var container = $("drawer-warnings");
    container.innerHTML = "";
    var warnings = payload.warnings || [];
    if (!warnings.length) {
      container.appendChild(el("div", { class: "note-inline", text: "无解析警告。" }));
      return;
    }
    warnings.forEach(function (warning) {
      container.appendChild(el("div", { class: "warning", text: warning }));
    });
  }

  function initPageSizes() {
    var select = $("page-size");
    select.innerHTML = "";
    (config.pageSizes || [25, 50, 100, 250]).forEach(function (size) {
      var option = el("option", { value: String(size), text: size + " / 页" });
      if (size === state.pageSize) option.selected = true;
      select.appendChild(option);
    });
  }

  function resetFilters() {
    state.query = "";
    state.activeFacets = {};
    state.issueFilter = "";
    state.sortColumn = "";
    state.sortDirection = "asc";
    state.page = 0;
    $("search").value = "";
    renderQualityBar();
    renderFacets();
    renderTable();
  }

  function bindEvents() {
    $("search").addEventListener("input", function (ev) {
      state.query = ev.target.value;
      state.page = 0;
      renderTable();
    });
    $("column-search").addEventListener("input", function (ev) {
      state.columnQuery = ev.target.value;
      renderColumnList();
    });
    $("clear-filters-btn").addEventListener("click", resetFilters);
    $("prev").addEventListener("click", function () { state.page -= 1; renderTable(); });
    $("next").addEventListener("click", function () { state.page += 1; renderTable(); });
    $("page-size").addEventListener("change", function (ev) {
      state.pageSize = parseInt(ev.target.value, 10) || config.defaultPageSize;
      state.page = 0;
      renderTable();
    });
    $("export-reviewed-dataset-btn").addEventListener("click", exportReview);
    $("inspector-close").addEventListener("click", closeInspector);
    $("inspector-overlay-pane").addEventListener("click", function (ev) {
      if (ev.target === $("inspector-overlay-pane")) closeInspector();
    });
    document.querySelectorAll("[data-tab]").forEach(function (button) {
      button.addEventListener("click", function () {
        state.tab = button.getAttribute("data-tab");
        renderTabs();
      });
    });
    document.querySelectorAll("[data-open-drawer]").forEach(function (button) {
      button.addEventListener("click", function () {
        openDrawer(button.getAttribute("data-open-drawer"));
      });
    });
    $("drawer-close").addEventListener("click", closeDrawer);
    $("drawer-backdrop").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        closeInspector();
        closeDrawer();
      }
    });
  }

  function init() {
    renderMeta();
    renderTabs();
    renderQualityBar();
    renderColumnChips();
    renderFacets();
    initPageSizes();
    renderTable();
    renderStats();
    renderDrawerRoles();
    renderDrawerWarnings();
    bindEvents();
  }

  init();
})();
