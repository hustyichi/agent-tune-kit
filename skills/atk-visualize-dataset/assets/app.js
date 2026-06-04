(function () {
  "use strict";

  var dataNode = document.getElementById("dataset-data");
  var payload = JSON.parse(dataNode.textContent);
  var roles = JSON.parse(JSON.stringify(payload.roles || {}));
  var facets = payload.facets || [];
  var issueLabels = payload.issueLabels || {};
  var issueOrder = payload.issueOrder || [];
  var config = payload.config || { snippetMaxChars: 240, pageSizes: [25, 50, 100, 250], defaultPageSize: 50 };
  var ECHARTS = (typeof window !== "undefined") ? window.echarts : null;

  var STORAGE_KEY = "atk-dataset-review:" + (payload.datasetPath || "dataset.csv");
  var review = loadReview();

  var state = {
    tab: "rows",
    query: "",
    activeFacets: {},
    issueFilter: "", // "" | <issue code> | "any" | "reviewed" | "unreviewed"
    pageSize: config.defaultPageSize,
    page: 0,
    selectedRowNumber: payload.rows[0] ? payload.rows[0].rowNumber : null,
    showEmptyFields: false,
  };

  var charts = {};

  var ROLE_LIST = ["id", "input", "expected"];
  var ROLE_LABELS = { id: "ID", input: "输入", expected: "ground_truth" };
  var VERDICTS = [
    { key: "ok", label: "符合预期", icon: "✅", cls: "active-ok", dot: "v-ok" },
    { key: "warn", label: "存疑", icon: "⚠️", cls: "active-warn", dot: "v-warn" },
    { key: "bad", label: "需修正", icon: "❌", cls: "active-bad", dot: "v-bad" },
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

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function truncate(s, n) {
    var t = String(s == null ? "" : s).replace(/\s+/g, " ").trim();
    if (t.length <= n) return t;
    return t.slice(0, n - 1) + "…";
  }

  // ---- review persistence (localStorage, offline) ----
  function loadReview() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return {};
      var parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) { return {}; }
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
    if (r.verdict === verdict) verdict = ""; // toggle off
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

  // ---- role helpers ----
  function roleField(role) { return (roles[role] && roles[role].field) || ""; }
  function inputFields() {
    // primary input role + any other non-role textual columns? Keep simple: input role field only.
    var f = roleField("input");
    return f ? [f] : [];
  }

  // ---- filtering ----
  function rowMatchesQuery(row, q) {
    if (!q) return true;
    var lower = q.toLowerCase();
    for (var key in row.values) {
      if ((row.values[key] || "").toLowerCase().indexOf(lower) >= 0) return true;
    }
    return false;
  }
  function rowMatchesFacets(row) {
    for (var field in state.activeFacets) {
      var sel = state.activeFacets[field];
      if (!sel || !sel.length) continue;
      var v = (row.values[field] || "").trim();
      if (sel.indexOf(v) < 0) return false;
    }
    return true;
  }
  function rowMatchesIssue(row) {
    var f = state.issueFilter;
    if (!f) return true;
    if (f === "any") return row.issues && row.issues.length > 0;
    if (f === "reviewed") return !!getVerdict(row);
    if (f === "unreviewed") return !getVerdict(row);
    return row.issues && row.issues.indexOf(f) >= 0;
  }
  function filteredRows() {
    var out = [];
    for (var i = 0; i < payload.rows.length; i++) {
      var r = payload.rows[i];
      if (rowMatchesQuery(r, state.query) && rowMatchesFacets(r) && rowMatchesIssue(r)) out.push(r);
    }
    return out;
  }

  // ---- header meta ----
  function reviewCounts() {
    var c = { ok: 0, warn: 0, bad: 0, total: payload.rows.length };
    for (var i = 0; i < payload.rows.length; i++) {
      var v = getVerdict(payload.rows[i]);
      if (v && c[v] != null) c[v]++;
    }
    c.reviewed = c.ok + c.warn + c.bad;
    return c;
  }
  function issueRowCount() {
    var n = 0;
    for (var i = 0; i < payload.rows.length; i++) if (payload.rows[i].issues && payload.rows[i].issues.length) n++;
    return n;
  }
  function renderMeta() {
    $("meta-name").textContent = payload.datasetName || "dataset.csv";
    $("meta-rows").textContent = payload.rowCount + " 行";
    $("meta-fields").textContent = (payload.fieldnames || []).length + " 列";
    var issues = issueRowCount();
    $("meta-issues").textContent = issues + " 问题行";
    var rc = reviewCounts();
    $("meta-review").textContent = rc.reviewed > 0 ? ("已审 " + rc.reviewed + "/" + rc.total) : "未审阅";
    $("path-context").textContent = payload.datasetPath + "  →  " + payload.output;
    $("stat-total").textContent = payload.rowCount;
    $("stat-fields").textContent = (payload.fieldnames || []).length;
    $("stat-issues").textContent = issues;
    $("stat-review-value").textContent = rc.reviewed;
  }

  // ---- quality filter bar ----
  function renderQualityBar() {
    var bar = $("quality-bar");
    bar.innerHTML = "";
    bar.hidden = false;
    bar.appendChild(el("span", { class: "label", text: "质量检查" }));

    var counts = {};
    var anyIssue = 0;
    for (var i = 0; i < payload.rows.length; i++) {
      var iss = payload.rows[i].issues || [];
      if (iss.length) anyIssue++;
      for (var j = 0; j < iss.length; j++) counts[iss[j]] = (counts[iss[j]] || 0) + 1;
    }

    function chip(code, label, count, disabled) {
      var b = el("button", {
        type: "button",
        class: state.issueFilter === code ? "active" : "",
        title: label,
        onclick: function () {
          state.issueFilter = state.issueFilter === code ? "" : code;
          state.page = 0;
          renderQualityBar();
          renderList();
        },
      }, [label, el("span", { class: "count", text: String(count) })]);
      if (disabled) b.disabled = true;
      return b;
    }

    bar.appendChild(chip("", "全部", payload.rows.length, false));
    bar.appendChild(chip("any", "有问题", anyIssue, anyIssue === 0));
    for (var oi = 0; oi < issueOrder.length; oi++) {
      var code = issueOrder[oi];
      if (!counts[code]) continue;
      bar.appendChild(chip(code, issueLabels[code] || code, counts[code], false));
    }
    var rc = reviewCounts();
    bar.appendChild(chip("reviewed", "已审阅", rc.reviewed, rc.reviewed === 0));
    bar.appendChild(chip("unreviewed", "待审阅", rc.total - rc.reviewed, false));
  }

  // ---- facets ----
  function renderFacets() {
    var container = $("facets");
    container.innerHTML = "";
    if (!facets.length) return;
    for (var i = 0; i < facets.length; i++) {
      (function (facet) {
        var sel = state.activeFacets[facet.field] || [];
        var box = el("div", { class: "facet" });
        var title = el("div", { class: "facet-title" }, [
          el("span", { text: facet.field }),
          sel.length ? el("span", {
            class: "clear", text: "清除", onclick: function () {
              state.activeFacets[facet.field] = [];
              state.page = 0; renderFacets(); renderList();
            }
          }) : null,
        ]);
        box.appendChild(title);
        var opts = el("div", { class: "facet-options" });
        for (var k = 0; k < facet.values.length; k++) {
          (function (entry) {
            var checked = sel.indexOf(entry.value) >= 0;
            var label = el("label", { class: "facet-option" }, [
              el("input", {
                type: "checkbox", checked: checked ? "checked" : null,
                onchange: function (ev) {
                  var cur = state.activeFacets[facet.field] || [];
                  if (ev.target.checked) { if (cur.indexOf(entry.value) < 0) cur.push(entry.value); }
                  else { cur = cur.filter(function (v) { return v !== entry.value; }); }
                  state.activeFacets[facet.field] = cur;
                  state.page = 0; renderList();
                }
              }),
              el("span", { class: "label", text: entry.value || "(空)" }),
              el("span", { class: "count", text: String(entry.count) }),
            ]);
            opts.appendChild(label);
          })(facet.values[k]);
        }
        box.appendChild(opts);
        container.appendChild(box);
      })(facets[i]);
    }
  }

  // ---- list + pagination ----
  function renderList() {
    var rows = filteredRows();
    $("filtered-count").textContent = rows.length + " / " + payload.rows.length;
    $("empty-state-all").hidden = payload.rows.length !== 0;
    $("empty-state-filtered").hidden = !(payload.rows.length !== 0 && rows.length === 0);

    var list = $("row-list");
    list.innerHTML = "";
    var size = state.pageSize;
    var pages = Math.max(1, Math.ceil(rows.length / size));
    if (state.page >= pages) state.page = pages - 1;
    if (state.page < 0) state.page = 0;
    var start = state.page * size;
    var slice = rows.slice(start, start + size);

    var inputF = inputFields()[0];
    for (var i = 0; i < slice.length; i++) {
      (function (row) {
        var idText = (row.atkId != null && row.atkId !== "") ? ("#" + row.atkId) : ("行 " + row.rowNumber);
        var verdict = getVerdict(row);
        var vdot = null;
        if (verdict) {
          var vd = VERDICTS.filter(function (v) { return v.key === verdict; })[0];
          if (vd) vdot = el("span", { class: "verdict-dot " + vd.dot, title: vd.label });
        }
        var badges = el("div", { class: "badges" });
        var iss = row.issues || [];
        for (var b = 0; b < iss.length; b++) {
          var warnish = iss[b] === "gt_too_long" || iss[b] === "gt_too_short" || iss[b] === "duplicate";
          badges.appendChild(el("span", { class: "badge " + (warnish ? "issue-warn" : "issue"), text: issueLabels[iss[b]] || iss[b] }));
        }
        var snippet = inputF ? (row.values[inputF] || "") : firstNonEmpty(row);
        var card = el("button", {
          type: "button",
          class: "case-card" + (row.rowNumber === state.selectedRowNumber ? " active" : ""),
          onclick: function () { state.selectedRowNumber = row.rowNumber; renderList(); renderDetail(); },
        }, [
          el("div", { class: "row1" }, [
            el("span", { class: "title" }, [vdot, document.createTextNode(" " + idText)]),
            el("span", { class: "num", text: "行 " + row.rowNumber }),
          ]),
          iss.length ? badges : null,
          el("div", { class: "snippet", text: truncate(snippet, config.snippetMaxChars) }),
        ]);
        list.appendChild(card);
      })(slice[i]);
    }

    $("page-label").textContent = "第 " + (state.page + 1) + " / " + pages + " 页";
    $("prev").disabled = state.page <= 0;
    $("next").disabled = state.page >= pages - 1;
  }

  function firstNonEmpty(row) {
    for (var i = 0; i < payload.fieldnames.length; i++) {
      var f = payload.fieldnames[i];
      if ((row.values[f] || "").trim()) return row.values[f];
    }
    return "";
  }

  // ---- detail ----
  function findRow(rowNumber) {
    for (var i = 0; i < payload.rows.length; i++) if (payload.rows[i].rowNumber === rowNumber) return payload.rows[i];
    return null;
  }

  function renderDetail() {
    var head = $("detail-head");
    var body = $("detail-body");
    head.innerHTML = "";
    body.innerHTML = "";
    var row = findRow(state.selectedRowNumber);
    if (!row) {
      body.appendChild(el("div", { class: "empty" }, [el("strong", { text: "选择左侧一行查看详情" })]));
      return;
    }
    var idText = (row.atkId != null && row.atkId !== "") ? ("atk_id " + row.atkId) : ("行 " + row.rowNumber);
    head.appendChild(el("div", { class: "title", text: idText }));
    head.appendChild(el("div", { class: "sub", text: "源行号 " + row.rowNumber }));
    var iss = row.issues || [];
    var headBadges = el("div", { class: "actions" });
    for (var b = 0; b < iss.length; b++) {
      var warnish = iss[b] === "gt_too_long" || iss[b] === "gt_too_short" || iss[b] === "duplicate";
      headBadges.appendChild(el("span", { class: "badge " + (warnish ? "issue-warn" : "issue"), text: issueLabels[iss[b]] || iss[b] }));
    }
    head.appendChild(headBadges);

    // input vs ground_truth compare
    var inputF = inputFields()[0];
    var gtF = roleField("expected");
    var io = el("div", { class: "io-grid" });
    io.appendChild(buildPane("输入", inputF ? row.values[inputF] : "", inputF || "(未识别输入列)"));
    var gtVal = gtF ? row.values[gtF] : "";
    var gtPane = el("div", { class: "pane" }, [
      el("div", { class: "pane-head" }, [
        el("span", {}, [el("span", { class: "role-tag", text: "ground_truth" })]),
        el("span", { text: gtF || "(未识别)" }),
      ]),
      el("div", { class: "pane-body" }, [
        el("div", { class: "gt-box" + ((gtVal || "").trim() ? "" : " empty") }, [
          el("div", { class: "label", text: (gtVal || "").trim() ? "GROUND TRUTH" : "GROUND TRUTH 为空" }),
          el("div", { class: "text", text: (gtVal || "").trim() ? gtVal : "（该行缺少期望结果，请补充）" }),
        ]),
      ]),
    ]);
    io.appendChild(gtPane);
    body.appendChild(io);

    // review box
    body.appendChild(buildReviewBox(row));

    // all fields detail (preserve all source columns)
    body.appendChild(buildFieldsBlock(row));
  }

  function buildPane(title, value, sub) {
    return el("div", { class: "pane" }, [
      el("div", { class: "pane-head" }, [
        el("span", {}, [el("span", { class: "role-tag", text: title })]),
        el("span", { text: sub }),
      ]),
      el("div", { class: "pane-body" }, [
        el("pre", { class: "", style: "margin:0;padding:10px;white-space:pre-wrap;overflow-wrap:anywhere;", text: (value || "").trim() ? value : "（空）" }),
      ]),
    ]);
  }

  function buildReviewBox(row) {
    var box = el("div", { class: "review-box" });
    var verdict = getVerdict(row);
    var title = el("div", { class: "review-title" }, [
      el("span", { text: "人工审阅 · 该 ground_truth 是否符合预期？" }),
      el("span", { class: "hint", text: "结果保存在本地浏览器" }),
    ]);
    box.appendChild(title);
    var group = el("div", { class: "verdict-group" });
    for (var i = 0; i < VERDICTS.length; i++) {
      (function (vd) {
        var btn = el("button", {
          type: "button",
          class: "verdict-btn" + (verdict === vd.key ? " " + vd.cls : ""),
          onclick: function () {
            setVerdict(row, vd.key);
            renderDetail(); renderList(); renderMeta(); renderQualityBar();
            if (state.tab === "overview") renderCharts();
          },
        }, [vd.icon + " " + vd.label]);
        group.appendChild(btn);
      })(VERDICTS[i]);
    }
    box.appendChild(group);
    var note = el("textarea", {
      class: "review-note",
      placeholder: "审阅备注（可选）：记录问题、建议修正方式…",
      oninput: function (ev) { setNote(row, ev.target.value); renderMeta(); },
    });
    note.value = getNote(row);
    box.appendChild(note);
    return box;
  }

  function buildFieldsBlock(row) {
    var wrap = el("div", {});
    var toolbar = el("div", { class: "fields-toolbar" }, [
      el("span", { class: "label", text: "全部字段（保留原始列）" }),
      el("label", { class: "facet-option", style: "flex:0 0 auto;" }, [
        el("input", {
          type: "checkbox", checked: state.showEmptyFields ? "checked" : null,
          onchange: function (ev) { state.showEmptyFields = ev.target.checked; renderDetail(); }
        }),
        el("span", { class: "label", text: "显示空字段" }),
      ]),
    ]);
    wrap.appendChild(toolbar);

    var emptyCount = 0;
    for (var i = 0; i < payload.fieldnames.length; i++) {
      var f = payload.fieldnames[i];
      var v = row.values[f];
      var isEmpty = !(v || "").trim();
      if (isEmpty && !state.showEmptyFields) { emptyCount++; continue; }
      wrap.appendChild(buildField(f, v));
    }
    if (emptyCount > 0 && !state.showEmptyFields) {
      wrap.appendChild(el("div", { class: "note-inline", text: emptyCount + " 个空字段已折叠（勾选“显示空字段”展开）" }));
    }
    return wrap;
  }

  function buildField(name, value) {
    var v = value == null ? "" : String(value);
    var det = el("details", { class: "field", open: v.length <= 400 ? "open" : null });
    var summary = el("summary", {}, [
      el("span", { class: "name", text: name }),
      el("span", { class: "meta", text: v.length + " 字符" }),
      el("span", {
        class: "copy", text: "复制",
        onclick: function (ev) { ev.preventDefault(); ev.stopPropagation(); copyText(v, ev.target); }
      }),
    ]);
    det.appendChild(summary);
    det.appendChild(el("div", { class: "content" }, [el("pre", { text: v })]));
    return det;
  }

  function copyText(text, node) {
    function done() { var old = node.textContent; node.textContent = "已复制"; setTimeout(function () { node.textContent = old; }, 1200); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else { fallbackCopy(text); done(); }
  }
  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
  }

  // ---- export review ----
  function exportReview() {
    var headers = ["atk_id", "row_number", "verdict", "note", "detected_issues"];
    var lines = [headers.join(",")];
    for (var i = 0; i < payload.rows.length; i++) {
      var row = payload.rows[i];
      var r = review[rowKey(row)];
      var verdict = r ? (r.verdict || "") : "";
      var note = r ? (r.note || "") : "";
      var issues = (row.issues || []).join("; ");
      if (!verdict && !note && !issues) continue;
      lines.push([
        csvCell(row.atkId == null ? "" : row.atkId),
        csvCell(row.rowNumber),
        csvCell(verdict),
        csvCell(note),
        csvCell(issues),
      ].join(","));
    }
    var blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    var url = URL.createObjectURL(blob);
    var a = el("a", { href: url, download: "dataset_review.csv" });
    document.body.appendChild(a); a.click();
    setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
  }
  function csvCell(v) {
    var s = String(v == null ? "" : v);
    if (/[",\r\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  // ---- drawer ----
  function openDrawer(section) {
    $("drawer").classList.add("open");
    $("drawer-backdrop").classList.add("open");
    var sections = document.querySelectorAll(".drawer-section");
    for (var i = 0; i < sections.length; i++) {
      sections[i].style.display = (sections[i].getAttribute("data-section") === section) ? "block" : "none";
    }
    $("drawer-title").textContent = section === "roles" ? "字段角色映射" : "解析警告";
  }
  function closeDrawer() {
    $("drawer").classList.remove("open");
    $("drawer-backdrop").classList.remove("open");
  }
  function renderDrawerRoles() {
    var container = $("drawer-roles");
    container.innerHTML = "";
    for (var i = 0; i < ROLE_LIST.length; i++) {
      (function (role) {
        var current = roleField(role);
        var source = (roles[role] && roles[role].source) || "manual";
        var row = el("div", { class: "role-row" });
        row.appendChild(el("span", { class: "name", text: ROLE_LABELS[role] }));
        var select = el("select", {
          onchange: function (ev) {
            roles[role] = { field: ev.target.value, source: "manual" };
            renderDetail(); renderList();
          }
        });
        select.appendChild(el("option", { value: "", text: "（未映射）" }));
        for (var k = 0; k < payload.fieldnames.length; k++) {
          var opt = el("option", { value: payload.fieldnames[k], text: payload.fieldnames[k] });
          if (payload.fieldnames[k] === current) opt.selected = true;
          select.appendChild(opt);
        }
        row.appendChild(select);
        row.appendChild(el("span", { class: "source", text: source === "auto" ? "auto-detected" : "manual/unmapped" }));
        container.appendChild(row);
      })(ROLE_LIST[i]);
    }
  }
  function renderDrawerWarnings() {
    var container = $("drawer-warnings");
    container.innerHTML = "";
    var ws = payload.warnings || [];
    if (!ws.length) { container.appendChild(el("div", { class: "note-inline", text: "无解析警告。" })); return; }
    var box = el("div", { class: "warnings" });
    for (var i = 0; i < ws.length; i++) box.appendChild(el("div", { class: "warning", text: ws[i] }));
    container.appendChild(box);
  }

  // ---- tabs + charts ----
  function switchTab(tab) {
    state.tab = tab;
    var tabs = document.querySelectorAll(".app-header .tab");
    for (var i = 0; i < tabs.length; i++) {
      var active = tabs[i].getAttribute("data-tab") === tab;
      tabs[i].classList.toggle("active", active);
      tabs[i].setAttribute("aria-selected", active ? "true" : "false");
    }
    $("tab-overview").hidden = tab !== "overview";
    $("tab-rows").hidden = tab !== "rows";
    if (tab === "overview") { renderKpis(); renderCharts(); }
  }

  function renderKpis() {
    var wrap = $("overview-kpis");
    wrap.innerHTML = "";
    var rc = reviewCounts();
    var kpis = [
      { label: "总行数", value: payload.rowCount, cls: "brand" },
      { label: "字段数", value: (payload.fieldnames || []).length, cls: "" },
      { label: "问题行", value: issueRowCount(), cls: "bad" },
      { label: "已审阅", value: rc.reviewed, sub: rc.total + " 行中", cls: "good" },
      { label: "需修正", value: rc.bad, cls: "bad" },
      { label: "存疑", value: rc.warn, cls: "warn" },
    ];
    for (var i = 0; i < kpis.length; i++) {
      var k = kpis[i];
      wrap.appendChild(el("div", { class: "kpi " + (k.cls || "") }, [
        el("div", { class: "label", text: k.label }),
        el("div", { class: "value", text: String(k.value) }),
        k.sub ? el("div", { class: "sub", text: k.sub }) : null,
      ]));
    }
  }

  function ensureChart(id) {
    if (!ECHARTS) return null;
    if (charts[id]) return charts[id];
    var node = $(id);
    if (!node) return null;
    charts[id] = ECHARTS.init(node);
    return charts[id];
  }
  function emptyChart(id, msg) {
    var node = $(id);
    if (node) { node.className = "chart-body empty-chart"; node.textContent = msg; }
  }

  function renderCharts() {
    if (!ECHARTS) {
      emptyChart("chart-issue-bar", "ECharts 未加载");
      return;
    }
    // issue bar
    var counts = {};
    for (var i = 0; i < payload.rows.length; i++) {
      var iss = payload.rows[i].issues || [];
      for (var j = 0; j < iss.length; j++) counts[iss[j]] = (counts[iss[j]] || 0) + 1;
    }
    var icats = [], ivals = [];
    for (var oi = 0; oi < issueOrder.length; oi++) {
      var code = issueOrder[oi];
      if (!counts[code]) continue;
      icats.push(issueLabels[code] || code); ivals.push(counts[code]);
    }
    if (icats.length) {
      var c1 = ensureChart("chart-issue-bar");
      c1 && c1.setOption({
        grid: { left: 110, right: 24, top: 16, bottom: 24 },
        tooltip: { trigger: "axis" },
        xAxis: { type: "value", minInterval: 1 },
        yAxis: { type: "category", data: icats, inverse: true },
        series: [{ type: "bar", data: ivals, itemStyle: { color: "#b42318" }, barMaxWidth: 22 }],
      });
      $("overview-issue-meta").textContent = icats.length + " 类问题";
    } else { emptyChart("chart-issue-bar", "未检测到质量问题 🎉"); $("overview-issue-meta").textContent = "无"; }

    // facet pie (first facet)
    if (facets.length) {
      var facet = facets[0];
      var pieData = facet.values.map(function (v) { return { name: v.value || "(空)", value: v.count }; });
      var c2 = ensureChart("chart-facet-pie");
      c2 && c2.setOption({
        tooltip: { trigger: "item" },
        legend: { type: "scroll", bottom: 0, textStyle: { fontSize: 11 } },
        series: [{ type: "pie", radius: ["38%", "66%"], center: ["50%", "44%"], data: pieData,
          label: { fontSize: 11 } }],
      });
      $("overview-facet-meta").textContent = facet.field;
    } else { emptyChart("chart-facet-pie", "无可用分类列"); $("overview-facet-meta").textContent = "无"; }

    // gt length histogram
    var gtF = roleField("expected");
    if (gtF) {
      var buckets = [
        { label: "0", lo: 0, hi: 0 },
        { label: "1-20", lo: 1, hi: 20 },
        { label: "21-50", lo: 21, hi: 50 },
        { label: "51-100", lo: 51, hi: 100 },
        { label: "101-200", lo: 101, hi: 200 },
        { label: "201-500", lo: 201, hi: 500 },
        { label: "500+", lo: 501, hi: Infinity },
      ];
      var bvals = buckets.map(function () { return 0; });
      for (var r = 0; r < payload.rows.length; r++) {
        var len = (payload.rows[r].values[gtF] || "").length;
        for (var bk = 0; bk < buckets.length; bk++) {
          if (len >= buckets[bk].lo && len <= buckets[bk].hi) { bvals[bk]++; break; }
        }
      }
      var c3 = ensureChart("chart-length-hist");
      c3 && c3.setOption({
        grid: { left: 40, right: 24, top: 16, bottom: 30 },
        tooltip: { trigger: "axis" },
        xAxis: { type: "category", data: buckets.map(function (b) { return b.label; }) },
        yAxis: { type: "value", minInterval: 1 },
        series: [{ type: "bar", data: bvals, itemStyle: { color: "#3457d5" }, barMaxWidth: 40 }],
      });
    } else { emptyChart("chart-length-hist", "未识别 ground_truth 列"); }

    // review pie
    var rc = reviewCounts();
    var unrev = rc.total - rc.reviewed;
    var rdata = [
      { name: "符合预期", value: rc.ok, itemStyle: { color: "#067647" } },
      { name: "存疑", value: rc.warn, itemStyle: { color: "#b45309" } },
      { name: "需修正", value: rc.bad, itemStyle: { color: "#b42318" } },
      { name: "待审阅", value: unrev, itemStyle: { color: "#cbd5e1" } },
    ].filter(function (d) { return d.value > 0; });
    if (rdata.length) {
      var c4 = ensureChart("chart-review-pie");
      c4 && c4.setOption({
        tooltip: { trigger: "item" },
        legend: { bottom: 0, textStyle: { fontSize: 11 } },
        series: [{ type: "pie", radius: ["38%", "66%"], center: ["50%", "44%"], data: rdata, label: { fontSize: 11 } }],
      });
    } else { emptyChart("chart-review-pie", "暂无审阅数据"); }
  }

  // ---- page size select ----
  function initPageSizes() {
    var sel = $("page-size");
    var sizes = config.pageSizes || [25, 50, 100, 250];
    for (var i = 0; i < sizes.length; i++) {
      var opt = el("option", { value: String(sizes[i]), text: sizes[i] + " / 页" });
      if (sizes[i] === state.pageSize) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.addEventListener("change", function (ev) {
      state.pageSize = parseInt(ev.target.value, 10) || config.defaultPageSize;
      state.page = 0; renderList();
    });
  }

  function bindEvents() {
    $("search").addEventListener("input", function (ev) { state.query = ev.target.value; state.page = 0; renderList(); });
    $("prev").addEventListener("click", function () { state.page--; renderList(); });
    $("next").addEventListener("click", function () { state.page++; renderList(); });
    $("export-review").addEventListener("click", exportReview);
    $("drawer-close").addEventListener("click", closeDrawer);
    $("drawer-backdrop").addEventListener("click", closeDrawer);
    var openers = document.querySelectorAll("[data-open-drawer]");
    for (var i = 0; i < openers.length; i++) {
      (function (btn) {
        btn.addEventListener("click", function () { openDrawer(btn.getAttribute("data-open-drawer")); });
      })(openers[i]);
    }
    var tabs = document.querySelectorAll(".app-header .tab");
    for (var t = 0; t < tabs.length; t++) {
      (function (tabBtn) {
        tabBtn.addEventListener("click", function () { switchTab(tabBtn.getAttribute("data-tab")); });
      })(tabs[t]);
    }
    document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") closeDrawer(); });
    window.addEventListener("resize", function () {
      for (var id in charts) { if (charts[id]) charts[id].resize(); }
    });
  }

  // ---- init ----
  function init() {
    renderMeta();
    renderQualityBar();
    renderFacets();
    initPageSizes();
    renderList();
    renderDetail();
    renderDrawerRoles();
    renderDrawerWarnings();
    bindEvents();
  }

  init();
})();
