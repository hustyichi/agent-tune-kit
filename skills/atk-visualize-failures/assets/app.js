(function () {
  "use strict";

  var dataNode = document.getElementById("failure-data");
  var payload = JSON.parse(dataNode.textContent);
  var roles = JSON.parse(JSON.stringify(payload.roles || {}));
  var facets = payload.facets || [];
  var history = payload.history || { status: "skipped", versions: [], perCase: [] };
  var ECHARTS = (typeof window !== "undefined") ? window.echarts : null;

  var perCaseById = {};
  if (history.perCase && history.perCase.length) {
    for (var pci = 0; pci < history.perCase.length; pci++) {
      perCaseById[history.perCase[pci].atkId] = history.perCase[pci];
    }
  }
  var regressionSet = new Set((history.buckets && history.buckets.newRegressions) || []);
  var persistentSet = new Set((history.buckets && history.buckets.persistent) || []);
  var prevTargetSolved = new Set();
  var prevTargetUnsolved = new Set();
  var prevTargets = (history.previousTargets && history.previousTargets.targets) || [];
  for (var pti = 0; pti < prevTargets.length; pti++) {
    var t = prevTargets[pti];
    if (!t.atkIds) continue;
    for (var pi = 0; pi < t.atkIds.length; pi++) {
      if (t.resolution === "resolved") prevTargetSolved.add(t.atkIds[pi]);
      else if (t.resolution === "unresolved" || t.resolution === "partial") {
        if (t.stillFailing && t.stillFailing.indexOf(t.atkIds[pi]) >= 0) prevTargetUnsolved.add(t.atkIds[pi]);
      }
    }
  }

  var state = {
    tab: "cases",
    query: "",
    activeFacets: {},
    crossFilter: "", // "" | regression | persistent | prev-unsolved | prev-solved
    pageSize: payload.config.defaultPageSize,
    page: 0,
    selectedRowNumber: payload.rows[0] ? payload.rows[0].rowNumber : null,
    diffMode: "split",
    showEmptyFields: false,
  };

  var charts = {}; // id -> ECharts instance

  var ROLE_LIST = ["id", "input", "expected", "actual", "reason", "log"];
  var ROLE_LABELS = {
    id: "ID",
    input: "输入",
    expected: "期望",
    actual: "实际",
    reason: "失败原因",
    log: "日志",
  };

  var $ = function (id) { return document.getElementById(id); };
  var el = function (tag, props, children) {
    var node = document.createElement(tag);
    if (props) {
      for (var k in props) {
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
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function roleField(role) {
    return (roles[role] && roles[role].field) || "";
  }
  function rowValue(row, role) {
    var f = roleField(role);
    return f ? (row.values[f] || "") : "";
  }
  function allText(row) {
    var out = [];
    for (var i = 0; i < payload.fieldnames.length; i++) {
      out.push(row.values[payload.fieldnames[i]] || "");
    }
    return out.join("\n").toLowerCase();
  }

  function snippet(s, max) {
    var n = max || payload.config.snippetMaxChars;
    var t = String(s == null ? "" : s).replace(/\s+/g, " ").trim();
    return t.length > n ? t.slice(0, n - 1) + "…" : t;
  }

  function isCodeLike(value) {
    if (!value) return false;
    var s = String(value);
    if (s.indexOf("\n") < 0) return false;
    if (/[{};()]/.test(s)) return true;
    if (/^[ \t]{2,}\S/m.test(s)) return true;
    return false;
  }

  // ---------- light syntax highlight (JS/TS/Java-ish) ----------
  var KEYWORDS = (
    "function|return|if|else|for|while|do|switch|case|default|break|continue|" +
    "try|catch|finally|throw|new|delete|typeof|instanceof|in|of|" +
    "const|let|var|class|extends|implements|interface|enum|export|import|from|as|" +
    "public|private|protected|static|final|abstract|void|true|false|null|undefined|" +
    "this|super|async|await|yield|module|package|namespace"
  );
  var KW_RE = new RegExp("\\b(" + KEYWORDS + ")\\b", "g");

  function highlight(value) {
    // Escape first, then re-tokenize on safe text.
    var s = escapeHtml(value);
    // Strings: backtick/double/single, simple (non-multiline backticks handled greedily within)
    s = s.replace(/(`(?:[^`\\]|\\.)*`|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g, function (m) {
      return '<span class="tok-str">' + m + "</span>";
    });
    // Block comments
    s = s.replace(/\/\*[\s\S]*?\*\//g, function (m) {
      return '<span class="tok-com">' + m + "</span>";
    });
    // Line comments (avoid touching inside already-tagged spans is hard; use plain regex)
    s = s.replace(/(^|[^:])\/\/[^\n]*/g, function (m, p1) {
      return p1 + '<span class="tok-com">' + m.slice(p1.length) + "</span>";
    });
    // Numbers
    s = s.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tok-num">$1</span>');
    // Keywords
    s = s.replace(KW_RE, '<span class="tok-kw">$1</span>');
    return s;
  }

  // ---------- LCS line diff ----------
  function lcsDiff(aLines, bLines) {
    var n = aLines.length, m = bLines.length;
    // dp[i][j] = LCS length of a[i..] and b[j..]
    var dp = new Array(n + 1);
    for (var i = 0; i <= n; i++) dp[i] = new Int32Array(m + 1);
    for (var i2 = n - 1; i2 >= 0; i2--) {
      for (var j = m - 1; j >= 0; j--) {
        if (aLines[i2] === bLines[j]) dp[i2][j] = dp[i2 + 1][j + 1] + 1;
        else dp[i2][j] = dp[i2 + 1][j] >= dp[i2][j + 1] ? dp[i2 + 1][j] : dp[i2][j + 1];
      }
    }
    var ops = [];
    var ai = 0, bi = 0;
    while (ai < n && bi < m) {
      if (aLines[ai] === bLines[bi]) {
        ops.push({ type: "eq", a: aLines[ai], b: bLines[bi], ai: ai + 1, bi: bi + 1 });
        ai++; bi++;
      } else if (dp[ai + 1][bi] >= dp[ai][bi + 1]) {
        ops.push({ type: "del", a: aLines[ai], ai: ai + 1 });
        ai++;
      } else {
        ops.push({ type: "add", b: bLines[bi], bi: bi + 1 });
        bi++;
      }
    }
    while (ai < n) { ops.push({ type: "del", a: aLines[ai], ai: ai + 1 }); ai++; }
    while (bi < m) { ops.push({ type: "add", b: bLines[bi], bi: bi + 1 }); bi++; }
    return ops;
  }

  function renderDiffSplit(ops, codeLike) {
    var hl = codeLike ? highlight : escapeHtml;
    var leftRows = [], rightRows = [];
    for (var i = 0; i < ops.length; i++) {
      var op = ops[i];
      if (op.type === "eq") {
        leftRows.push('<tr class="line-eq"><td class="ln">' + op.ai + '</td><td>' + hl(op.a) + '</td></tr>');
        rightRows.push('<tr class="line-eq"><td class="ln">' + op.bi + '</td><td>' + hl(op.b) + '</td></tr>');
      } else if (op.type === "del") {
        leftRows.push('<tr class="line-del"><td class="ln">' + op.ai + '</td><td>' + hl(op.a) + '</td></tr>');
        rightRows.push('<tr><td class="ln"></td><td></td></tr>');
      } else {
        leftRows.push('<tr><td class="ln"></td><td></td></tr>');
        rightRows.push('<tr class="line-add"><td class="ln">' + op.bi + '</td><td>' + hl(op.b) + '</td></tr>');
      }
    }
    return {
      left: '<table class="code-table">' + leftRows.join("") + "</table>",
      right: '<table class="code-table">' + rightRows.join("") + "</table>",
    };
  }

  function renderDiffUnified(ops, codeLike) {
    var hl = codeLike ? highlight : escapeHtml;
    var rows = [];
    for (var i = 0; i < ops.length; i++) {
      var op = ops[i];
      if (op.type === "eq") {
        rows.push('<tr class="line-eq"><td class="ln">' + op.ai + "</td><td class=\"ln\">" + op.bi + "</td><td>  " + hl(op.a) + "</td></tr>");
      } else if (op.type === "del") {
        rows.push('<tr class="line-del"><td class="ln">' + op.ai + '</td><td class="ln"></td><td>- ' + hl(op.a) + "</td></tr>");
      } else {
        rows.push('<tr class="line-add"><td class="ln"></td><td class="ln">' + op.bi + '</td><td>+ ' + hl(op.b) + "</td></tr>");
      }
    }
    return '<table class="code-table">' + rows.join("") + "</table>";
  }

  function renderPlain(value, codeLike) {
    if (!value) return '<div class="empty" style="padding:18px;">（空）</div>';
    if (codeLike) {
      var lines = String(value).split("\n");
      var rows = [];
      for (var i = 0; i < lines.length; i++) {
        rows.push('<tr class="line-eq"><td class="ln">' + (i + 1) + "</td><td>" + highlight(lines[i]) + "</td></tr>");
      }
      return '<table class="code-table">' + rows.join("") + "</table>";
    }
    return '<pre style="margin:0;padding:10px;white-space:pre-wrap;overflow-wrap:anywhere;">' + escapeHtml(value) + "</pre>";
  }

  // ---------- rendering ----------
  function categoryBadgeClass(value) {
    var v = String(value || "").toLowerCase();
    if (/ok|pass|success|成功|通过/.test(v)) return "ok";
    if (/error|fail|exception|异常|失败|错误/.test(v)) return "bad";
    if (/warn|skip|partial|警告|跳过|部分/.test(v)) return "warn";
    return "";
  }

  function init() {
    renderHeaderMeta();
    renderStats();
    renderPageSizeOptions();
    renderFacets();
    renderCrossFilterBar();
    renderRolesDrawer();
    renderReportDrawer();
    renderWarningsDrawer();
    bindControls();
    bindTabs();
    renderOverviewTab();
    renderCrossTab();
    render();
    // Default to overview tab when cross-version history is present and useful
    if (history.status === "included" && (history.versions || []).length >= 2) {
      switchTab("overview");
    }
  }

  function renderHeaderMeta() {
    $("path-context").textContent = payload.currentDir + " → " + payload.output;
    $("meta-version").textContent = payload.version;
    $("meta-rows").textContent = payload.rowCount + " 行";
    $("meta-fields").textContent = payload.fieldnames.length + " 列";
    var nVersions = (history.versions || []).length;
    var hist = $("meta-history");
    if (history.status === "included" && nVersions >= 2) {
      hist.textContent = "跨版本 · v1.." + payload.version + " 共 " + nVersions + " 个";
    } else if (history.status === "included" && nVersions === 1) {
      hist.textContent = "单版本（未发现历史 vN）";
    } else {
      hist.textContent = "history: " + (history.reason || "skipped");
    }
  }

  function renderStats() {
    var rowsTotal = payload.rowCount;
    $("stat-total").textContent = rowsTotal;
    $("stat-fields").textContent = payload.fieldnames.length;
    // Pick a representative categorical stat if any single-facet exists, else show report status
    var primary = facets[0];
    if (primary && primary.values.length) {
      $("stat-primary-label").textContent = primary.field;
      $("stat-primary-value").textContent = primary.values.length + " 类";
    } else {
      $("stat-primary-label").textContent = "字段角色";
      var mapped = 0;
      for (var i = 0; i < ROLE_LIST.length; i++) if (roleField(ROLE_LIST[i])) mapped++;
      $("stat-primary-value").textContent = mapped + " / " + ROLE_LIST.length;
    }
    $("stat-report-label").textContent = "报告上下文";
    $("stat-report-value").textContent = (payload.report && payload.report.status === "included") ? "已纳入" : "未纳入";
  }

  function renderPageSizeOptions() {
    var sel = $("page-size");
    sel.innerHTML = "";
    var sizes = payload.config.pageSizes;
    for (var i = 0; i < sizes.length; i++) {
      var o = document.createElement("option");
      o.value = sizes[i];
      o.textContent = sizes[i] + " / 页";
      if (sizes[i] === state.pageSize) o.selected = true;
      sel.appendChild(o);
    }
  }

  function renderFacets() {
    var host = $("facets");
    host.innerHTML = "";
    if (!facets.length) {
      host.appendChild(el("div", { class: "muted", style: "font-size:12px;color:var(--muted);padding:4px 2px;" }, ["未检测到适合做分类过滤的字段。"]));
      return;
    }
    for (var i = 0; i < facets.length; i++) {
      var f = facets[i];
      var facet = el("div", { class: "facet" });
      var title = el("div", { class: "facet-title" }, [
        el("span", null, [f.field]),
        el("span", { class: "clear", "data-clear": f.field, onclick: clearFacet }, ["清除"]),
      ]);
      facet.appendChild(title);
      var opts = el("div", { class: "facet-options" });
      for (var j = 0; j < f.values.length; j++) {
        var v = f.values[j];
        var labelText = v.value || "（空）";
        var optionId = "facet-" + i + "-" + j;
        var checked = state.activeFacets[f.field] && state.activeFacets[f.field].has(v.value);
        var label = el("label", { class: "facet-option", for: optionId });
        var input = el("input", { type: "checkbox", id: optionId, "data-field": f.field, "data-value": v.value });
        if (checked) input.checked = true;
        input.addEventListener("change", toggleFacet);
        label.appendChild(input);
        label.appendChild(el("span", { class: "label", title: labelText }, [labelText]));
        label.appendChild(el("span", { class: "count" }, [String(v.count)]));
        opts.appendChild(label);
      }
      facet.appendChild(opts);
      host.appendChild(facet);
    }
  }

  function clearFacet(ev) {
    var field = ev.target.getAttribute("data-clear");
    delete state.activeFacets[field];
    state.page = 0;
    renderFacets();
    render();
  }

  function toggleFacet(ev) {
    var field = ev.target.getAttribute("data-field");
    var value = ev.target.getAttribute("data-value");
    if (!state.activeFacets[field]) state.activeFacets[field] = new Set();
    var set = state.activeFacets[field];
    if (set.has(value)) set.delete(value);
    else set.add(value);
    if (!set.size) delete state.activeFacets[field];
    state.page = 0;
    render();
  }

  function renderRolesDrawer() {
    var host = $("drawer-roles");
    host.innerHTML = "";
    for (var i = 0; i < ROLE_LIST.length; i++) {
      var role = ROLE_LIST[i];
      var row = el("div", { class: "role-row" });
      row.appendChild(el("div", { class: "name" }, [ROLE_LABELS[role] + " · " + role]));
      var sel = el("select", { "data-role": role });
      var empty = document.createElement("option");
      empty.value = ""; empty.textContent = "— 未映射 —";
      sel.appendChild(empty);
      for (var j = 0; j < payload.fieldnames.length; j++) {
        var f = payload.fieldnames[j];
        var o = document.createElement("option");
        o.value = f; o.textContent = f;
        if (roleField(role) === f) o.selected = true;
        sel.appendChild(o);
      }
      sel.addEventListener("change", onRoleChange);
      row.appendChild(sel);
      var src = (roles[role] && roles[role].source) || "manual";
      row.appendChild(el("div", { class: "source", "data-role-src": role }, [src === "auto" ? "自动识别" : (roleField(role) ? "手动" : "manual/unmapped")]));
      host.appendChild(row);
    }
  }

  function onRoleChange(ev) {
    var role = ev.target.getAttribute("data-role");
    var value = ev.target.value;
    roles[role] = { field: value, source: "manual" };
    var srcEl = document.querySelector('[data-role-src="' + role + '"]');
    if (srcEl) srcEl.textContent = value ? "手动" : "manual/unmapped";
    render();
  }

  function renderReportDrawer() {
    var host = $("drawer-report");
    host.innerHTML = "";
    var rep = payload.report || {};
    if (!rep.excerpts || !rep.excerpts.length) {
      host.appendChild(el("div", { style: "font-size:12.5px;color:var(--muted);" }, ["未纳入 report.md：" + (rep.reason || "无")]));
      return;
    }
    var list = el("div", { class: "report-list" });
    for (var i = 0; i < rep.excerpts.length; i++) {
      var item = rep.excerpts[i];
      list.appendChild(el("div", { class: "report-item" }, [
        el("strong", null, [item.heading]),
        el("div", { class: "text" }, [item.text]),
      ]));
    }
    host.appendChild(list);
    if (rep.reason) host.appendChild(el("div", { style: "color:var(--muted);font-size:12px;margin-top:6px;" }, [rep.reason]));
  }

  function renderWarningsDrawer() {
    var host = $("drawer-warnings");
    host.innerHTML = "";
    var ws = payload.warnings || [];
    if (!ws.length) {
      host.appendChild(el("div", { style: "font-size:12.5px;color:var(--muted);" }, ["无解析警告。"]));
      return;
    }
    for (var i = 0; i < ws.length; i++) {
      host.appendChild(el("div", { class: "warning" }, [ws[i]]));
    }
  }

  function bindControls() {
    $("search").addEventListener("input", function (ev) {
      state.query = ev.target.value.toLowerCase();
      state.page = 0;
      render();
    });
    $("page-size").addEventListener("change", function (ev) {
      state.pageSize = Number(ev.target.value) || payload.config.defaultPageSize;
      state.page = 0;
      render();
    });
    $("prev").addEventListener("click", function () { state.page = Math.max(0, state.page - 1); render(); });
    $("next").addEventListener("click", function () { state.page += 1; render(); });
    // Note: diff-mode buttons and #toggle-empty-fields are created dynamically by
    // renderDetail() and bound there; do not bind them here (they may not exist yet).
    var drawerOpenButtons = document.querySelectorAll("[data-open-drawer]");
    for (var k = 0; k < drawerOpenButtons.length; k++) {
      drawerOpenButtons[k].addEventListener("click", function (ev) {
        var target = ev.currentTarget.getAttribute("data-open-drawer");
        openDrawer(target);
      });
    }
    $("drawer-close").addEventListener("click", closeDrawer);
    $("drawer-backdrop").addEventListener("click", closeDrawer);
  }

  function openDrawer(section) {
    var drawer = $("drawer");
    drawer.classList.add("open");
    $("drawer-backdrop").classList.add("open");
    var sections = document.querySelectorAll(".drawer-section");
    for (var i = 0; i < sections.length; i++) {
      sections[i].style.display = (sections[i].getAttribute("data-section") === section || section === "all") ? "block" : "none";
    }
    $("drawer-title").textContent = section === "roles" ? "字段角色映射" : section === "report" ? "report.md 摘录" : section === "warnings" ? "解析警告" : "设置";
  }
  function closeDrawer() {
    $("drawer").classList.remove("open");
    $("drawer-backdrop").classList.remove("open");
  }

  function filteredRows() {
    var out = [];
    var hasFacetFilter = false;
    for (var k in state.activeFacets) { hasFacetFilter = true; break; }
    var crossPredicate = null;
    if (state.crossFilter && history.atkIdField) {
      if (state.crossFilter === "regression") crossPredicate = function (aid) { return regressionSet.has(aid); };
      else if (state.crossFilter === "persistent") crossPredicate = function (aid) { return persistentSet.has(aid); };
      else if (state.crossFilter === "prev-unsolved") crossPredicate = function (aid) { return prevTargetUnsolved.has(aid); };
      else if (state.crossFilter === "prev-solved") crossPredicate = function (aid) { return prevTargetSolved.has(aid); };
    }
    for (var i = 0; i < payload.rows.length; i++) {
      var row = payload.rows[i];
      if (state.query && allText(row).indexOf(state.query) < 0) continue;
      if (hasFacetFilter) {
        var ok = true;
        for (var f in state.activeFacets) {
          var set = state.activeFacets[f];
          if (!set.has(row.values[f] || "")) { ok = false; break; }
        }
        if (!ok) continue;
      }
      if (crossPredicate) {
        var aid = atkIdOfRow(row);
        if (!aid || !crossPredicate(aid)) continue;
      }
      out.push(row);
    }
    return out;
  }

  function render() {
    var rows = filteredRows();
    var maxPage = Math.max(0, Math.ceil(rows.length / state.pageSize) - 1);
    if (state.page > maxPage) state.page = maxPage;
    var start = state.page * state.pageSize;
    var visible = rows.slice(start, start + state.pageSize);

    $("filtered-count").textContent = rows.length + " / " + payload.rowCount;

    var emptyAll = $("empty-state-all");
    var emptyFiltered = $("empty-state-filtered");
    emptyAll.hidden = payload.rowCount !== 0;
    emptyFiltered.hidden = !(payload.rowCount > 0 && rows.length === 0);

    var list = $("case-list");
    list.innerHTML = "";
    for (var i = 0; i < visible.length; i++) {
      list.appendChild(buildCaseCard(visible[i]));
    }

    $("page-label").textContent = rows.length ? ("第 " + (state.page + 1) + " / " + (maxPage + 1) + " 页") : "无匹配";
    $("prev").disabled = state.page <= 0;
    $("next").disabled = state.page >= maxPage;

    if (rows.length && !rows.some(function (r) { return r.rowNumber === state.selectedRowNumber; })) {
      state.selectedRowNumber = rows[0].rowNumber;
    }
    renderDetail();
  }

  function buildCaseCard(row) {
    var title = rowValue(row, "id") || ("第 " + row.rowNumber + " 行");
    var reason = rowValue(row, "reason");
    var snippetText = snippet(reason || rowValue(row, "input") || firstNonEmpty(row), 160);
    var card = el("button", {
      type: "button",
      class: "case-card" + (row.rowNumber === state.selectedRowNumber ? " active" : ""),
      "data-row": row.rowNumber,
    });
    card.addEventListener("click", function () {
      state.selectedRowNumber = row.rowNumber;
      render();
    });
    card.appendChild(el("div", { class: "row1" }, [
      el("span", { class: "title", title: title }, [title]),
      el("span", { class: "num" }, ["#" + row.rowNumber]),
    ]));
    var aid = atkIdOfRow(row);
    var xvBadges = buildCrossVersionBadges(aid);
    if (xvBadges) card.appendChild(xvBadges);
    var badges = el("div", { class: "badges" });
    var added = 0;
    for (var i = 0; i < facets.length && added < 3; i++) {
      var f = facets[i].field;
      var v = row.values[f];
      if (!v) continue;
      var cls = "badge " + categoryBadgeClass(v);
      badges.appendChild(el("span", { class: cls.trim(), title: f + ": " + v }, [v]));
      added++;
    }
    if (added) card.appendChild(badges);
    if (snippetText) card.appendChild(el("div", { class: "snippet" }, [snippetText]));
    return card;
  }

  function atkIdOfRow(row) {
    var f = history.atkIdField || "";
    if (!f) return "";
    return (row.values[f] || "").trim();
  }

  function buildCrossVersionBadges(aid) {
    if (!aid) return null;
    var wrap = el("div", { class: "xv-badges" });
    var any = false;
    if (regressionSet.has(aid)) { wrap.appendChild(el("span", { class: "badge xv-regression", title: "上一版本未出现，本轮新回归" }, ["新回归"])); any = true; }
    if (persistentSet.has(aid)) {
      var pc = perCaseById[aid];
      var k = pc ? pc.failedCount : 0;
      wrap.appendChild(el("span", { class: "badge xv-persistent", title: "在 " + k + " 个版本中都是失败" }, ["顽固 · K=" + k]));
      any = true;
    }
    if (prevTargetUnsolved.has(aid)) { wrap.appendChild(el("span", { class: "badge xv-prev-unsolved", title: "上一轮 tuning_plan.md 点名要修，本轮仍在失败列表" }, ["上轮目标·未解决"])); any = true; }
    return any ? wrap : null;
  }

  function firstNonEmpty(row) {
    for (var i = 0; i < payload.fieldnames.length; i++) {
      var v = row.values[payload.fieldnames[i]];
      if (v) return v;
    }
    return "";
  }

  function currentRow() {
    for (var i = 0; i < payload.rows.length; i++) {
      if (payload.rows[i].rowNumber === state.selectedRowNumber) return payload.rows[i];
    }
    return payload.rows[0] || null;
  }

  function renderDetail() {
    var row = currentRow();
    var head = $("detail-head");
    var body = $("detail-body");
    if (!row) {
      head.innerHTML = '<div class="title">暂无可显示的失败行</div>';
      body.innerHTML = '<div class="empty"><strong>当前 failure_cases.csv 无行</strong><span>请运行 atk-find-failures 或 atk-find-failures-by-rule 后再来。</span></div>';
      return;
    }
    var idValue = rowValue(row, "id") || ("第 " + row.rowNumber + " 行");
    head.innerHTML = "";
    head.appendChild(el("div", { class: "title", title: idValue }, [idValue]));
    var subParts = ["#" + row.rowNumber];
    var inputF = roleField("input");
    if (inputF && row.values[inputF]) subParts.push(inputF + ": " + snippet(row.values[inputF], 80));
    head.appendChild(el("div", { class: "sub" }, [subParts.join(" · ")]));
    var badgeBar = el("div", { class: "actions" });
    for (var i = 0; i < facets.length; i++) {
      var fname = facets[i].field;
      var v = row.values[fname];
      if (!v) continue;
      badgeBar.appendChild(el("span", { class: ("badge " + categoryBadgeClass(v)).trim(), title: fname }, [v]));
    }
    head.appendChild(badgeBar);

    body.innerHTML = "";

    // Compare
    var expected = rowValue(row, "expected");
    var actual = rowValue(row, "actual");
    var compareSection = el("section");
    var toolbar = el("div", { class: "compare-toolbar" });
    var roleHint = el("div", { class: "roles" }, [
      "期望: " + (roleField("expected") || "未映射") + "  ·  实际: " + (roleField("actual") || "未映射"),
    ]);
    var seg = el("div", { class: "seg" });
    var modes = [["split", "并排"], ["unified", "行内 diff"], ["plain", "原文"]];
    for (var m = 0; m < modes.length; m++) {
      var btn = el("button", {
        type: "button",
        "data-diff-mode": modes[m][0],
        class: state.diffMode === modes[m][0] ? "active" : "",
      }, [modes[m][1]]);
      btn.addEventListener("click", function (ev) {
        state.diffMode = ev.currentTarget.getAttribute("data-diff-mode");
        renderDetail();
      });
      seg.appendChild(btn);
    }
    toolbar.appendChild(seg);
    toolbar.appendChild(roleHint);
    compareSection.appendChild(toolbar);

    var codeLike = isCodeLike(expected) || isCodeLike(actual);
    var compareContainer = el("div", { class: "compare " + state.diffMode });
    if (state.diffMode === "plain") {
      compareContainer.appendChild(buildPane("期望 (expected)", renderPlain(expected, codeLike)));
      compareContainer.appendChild(buildPane("实际 (actual)", renderPlain(actual, codeLike)));
    } else {
      var aLines = String(expected || "").split("\n");
      var bLines = String(actual || "").split("\n");
      var ops = lcsDiff(aLines, bLines);
      if (state.diffMode === "split") {
        var s = renderDiffSplit(ops, codeLike);
        compareContainer.appendChild(buildPane("期望 (expected)", s.left));
        compareContainer.appendChild(buildPane("实际 (actual)", s.right));
      } else {
        compareContainer.appendChild(buildPane("expected ↔ actual diff", renderDiffUnified(ops, codeLike)));
      }
    }
    compareSection.appendChild(compareContainer);
    body.appendChild(compareSection);

    // Reason
    var reason = rowValue(row, "reason");
    if (reason) {
      body.appendChild(el("div", { class: "reason-box" }, [
        el("div", { class: "label" }, ["失败原因 · " + (roleField("reason") || "reason")]),
        el("div", { class: "text" }, [reason]),
      ]));
    }

    // Log
    var logField = roleField("log");
    var logValue = logField ? row.values[logField] : "";
    if (logValue) {
      var href = (row.safeLogHrefs || {})[logField];
      var box = el("div", { class: "log-link" });
      box.appendChild(el("span", { class: "badge muted" }, ["日志"]));
      if (href) {
        var a = el("a", { href: href, target: "_blank", rel: "noopener" }, [logValue]);
        box.appendChild(a);
      } else {
        box.appendChild(el("span", { class: "muted-note", title: "not clickable because it is outside the safe relative path contract" }, [
          logValue + "（不可点击：路径不在安全相对路径范围内 / not clickable because it is outside the safe relative path contract）",
        ]));
      }
      body.appendChild(box);
    }

    // All fields
    var fieldsSection = el("section");
    fieldsSection.appendChild(el("div", { class: "panel-header", style: "border:0;padding:6px 0;background:transparent;" }, [
      el("span", null, ["全部字段"]),
      el("span", { class: "hint" }, ["共 " + payload.fieldnames.length + " 列"]),
    ]));
    var toolbar2 = el("div", { class: "fields-toolbar" });
    var lab = el("label", { class: "label" });
    var cb = el("input", { type: "checkbox", id: "toggle-empty-fields" });
    if (state.showEmptyFields) cb.checked = true;
    cb.addEventListener("change", function (ev) {
      state.showEmptyFields = !!ev.target.checked;
      renderDetail();
    });
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode(" 显示空字段"));
    toolbar2.appendChild(lab);
    fieldsSection.appendChild(toolbar2);

    var sorted = payload.fieldnames.slice().sort(function (a, b) {
      var va = row.values[a] || "", vb = row.values[b] || "";
      if (!!va === !!vb) return b.length - a.length === 0 ? 0 : (vb.length - va.length);
      return va ? -1 : 1;
    });
    var emptyCount = 0;
    for (var f = 0; f < sorted.length; f++) {
      var fname2 = sorted[f];
      var v = row.values[fname2] || "";
      if (!v) {
        emptyCount++;
        if (!state.showEmptyFields) continue;
      }
      fieldsSection.appendChild(buildFieldRow(fname2, v));
    }
    if (emptyCount && !state.showEmptyFields) {
      fieldsSection.appendChild(el("div", { style: "color:var(--muted);font-size:12px;padding:6px 2px;" }, [
        emptyCount + " 个空字段已折叠。",
      ]));
    }
    body.appendChild(fieldsSection);
  }

  function buildPane(label, innerHtml) {
    var pane = el("div", { class: "pane" });
    pane.appendChild(el("div", { class: "pane-head" }, [
      el("span", null, [label]),
    ]));
    var bodyDiv = el("div", { class: "pane-body" });
    bodyDiv.innerHTML = innerHtml;
    pane.appendChild(bodyDiv);
    return pane;
  }

  function buildFieldRow(name, value) {
    var det = el("details", { class: "field" });
    var sum = el("summary");
    sum.appendChild(el("span", { class: "name" }, [name]));
    var meta = value ? (value.length > 80 ? Math.round(value.length / 1024 * 10) / 10 + " KB" : value.length + " 字符") : "空";
    sum.appendChild(el("span", { class: "meta" }, [meta]));
    if (value) {
      var copy = el("span", { class: "copy" }, ["复制"]);
      copy.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(value);
            copy.textContent = "已复制";
            setTimeout(function () { copy.textContent = "复制"; }, 1200);
          }
        } catch (e) { /* ignore */ }
      });
      sum.appendChild(copy);
    }
    det.appendChild(sum);
    var content;
    if (!value) {
      content = el("div", { class: "content plain" });
      content.appendChild(el("pre", null, ["（空）"]));
    } else if (isCodeLike(value)) {
      content = el("div", { class: "content" });
      content.innerHTML = renderPlain(value, true);
    } else {
      content = el("div", { class: "content plain" });
      var pre = el("pre");
      pre.textContent = value;
      content.appendChild(pre);
    }
    det.appendChild(content);
    return det;
  }

  // ===== Tabs =====
  function bindTabs() {
    var btns = document.querySelectorAll(".tab[data-tab]");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function (ev) {
        switchTab(ev.currentTarget.getAttribute("data-tab"));
      });
    }
  }
  function switchTab(tab) {
    state.tab = tab;
    var btns = document.querySelectorAll(".tab[data-tab]");
    for (var i = 0; i < btns.length; i++) {
      var active = btns[i].getAttribute("data-tab") === tab;
      btns[i].classList.toggle("active", active);
      btns[i].setAttribute("aria-selected", active ? "true" : "false");
    }
    var panels = document.querySelectorAll(".tab-panel");
    for (var j = 0; j < panels.length; j++) {
      var id = panels[j].id; // "tab-overview" etc
      panels[j].hidden = id !== ("tab-" + tab);
    }
    // ECharts must be resized after becoming visible
    setTimeout(function () {
      for (var k in charts) { try { charts[k].resize(); } catch (e) { /* ignore */ } }
    }, 30);
  }

  // ===== Cross-filter bar (cases tab) =====
  function renderCrossFilterBar() {
    var bar = $("cross-filter-bar");
    if (!history.atkIdField) {
      bar.hidden = true;
      return;
    }
    var counts = {
      regression: regressionSet.size,
      persistent: persistentSet.size,
      "prev-unsolved": prevTargetUnsolved.size,
      "prev-solved": prevTargetSolved.size,
    };
    if (!counts.regression && !counts.persistent && !counts["prev-unsolved"] && !counts["prev-solved"]) {
      bar.hidden = true;
      return;
    }
    bar.hidden = false;
    bar.innerHTML = "";
    bar.appendChild(el("span", { class: "label" }, ["跨版本筛选"]));
    var options = [
      ["", "全部 (" + payload.rowCount + ")"],
      ["regression", "新回归 (" + counts.regression + ")"],
      ["persistent", "顽固 (" + counts.persistent + ")"],
      ["prev-unsolved", "上轮·未解决 (" + counts["prev-unsolved"] + ")"],
      ["prev-solved", "上轮·已解决 (" + counts["prev-solved"] + ")"],
    ];
    for (var i = 0; i < options.length; i++) {
      var key = options[i][0];
      var label = options[i][1];
      var btn = el("button", {
        type: "button",
        "data-cross": key,
        class: state.crossFilter === key ? "active" : "",
      }, [label]);
      if (key && key !== "" && counts[key] === 0) btn.disabled = true;
      btn.addEventListener("click", function (ev) {
        state.crossFilter = ev.currentTarget.getAttribute("data-cross");
        state.page = 0;
        renderCrossFilterBar();
        render();
      });
      bar.appendChild(btn);
    }
  }

  // ===== Overview tab =====
  function renderOverviewTab() {
    renderKpis("overview-kpis", buildOverviewKpis());
    var reasonField = roleField("reason");
    var reasonCounts = {};
    if (reasonField) {
      for (var i = 0; i < payload.rows.length; i++) {
        var v = (payload.rows[i].values[reasonField] || "").trim();
        if (!v) continue;
        var key = v.length > 80 ? v.slice(0, 79) + "…" : v;
        reasonCounts[key] = (reasonCounts[key] || 0) + 1;
      }
    }
    var reasonItems = Object.keys(reasonCounts).map(function (k) { return [k, reasonCounts[k]]; })
      .sort(function (a, b) { return b[1] - a[1]; })
      .slice(0, 12);
    $("overview-reason-meta").textContent = reasonField ? ("字段：" + reasonField) : "未识别 reason 字段";
    renderHorizontalBar("chart-reason-bar", reasonItems, "失败数");

    var primary = facets[0];
    $("overview-facet-meta").textContent = primary ? ("字段：" + primary.field) : "未发现分类字段";
    if (primary) {
      var pieData = primary.values.slice(0, 12).map(function (v) { return { name: v.value, value: v.count }; });
      renderPie("chart-facet-pie", pieData);
    } else {
      renderEmpty("chart-facet-pie", "无低基数分类字段");
    }

    renderLineDiffHistogram();
  }

  function buildOverviewKpis() {
    var total = payload.rowCount;
    var nVersions = (history.versions || []).length;
    var regression = regressionSet.size;
    var persistent = persistentSet.size;
    var hit = history.previousTargets && history.previousTargets.summary;
    var hitText = "—";
    if (hit) {
      var totalTargets = hit.resolved + hit.partial + hit.unresolved + hit.indeterminate;
      if (totalTargets > 0) {
        hitText = hit.resolved + " / " + totalTargets;
      }
    }
    var items = [
      { label: "当前版本失败总数", value: String(total), sub: payload.version, cls: "brand" },
      { label: "新回归 (vs 上一版本)", value: String(regression), sub: nVersions >= 2 ? ("基线 " + history.versions[history.versions.length - 2].name) : "无上一版本", cls: regression > 0 ? "bad" : "good" },
      { label: "顽固 case (≥" + (history.persistentMinFails || 2) + " 次失败)", value: String(persistent), sub: "在多个版本中重复失败", cls: persistent > 0 ? "warn" : "good" },
      { label: "上轮调优目标命中", value: hitText, sub: "resolved / total", cls: "brand" },
    ];
    return items;
  }

  function renderLineDiffHistogram() {
    var expected = roleField("expected");
    var actual = roleField("actual");
    if (!expected || !actual) {
      renderEmpty("chart-line-diff", "未映射 expected / actual 角色");
      return;
    }
    var buckets = {}; // diff -> count
    var min = 0, max = 0;
    for (var i = 0; i < payload.rows.length; i++) {
      var r = payload.rows[i];
      var e = String(r.values[expected] || "").split("\n").length;
      var a = String(r.values[actual] || "").split("\n").length;
      var diff = a - e;
      // clamp huge diffs to keep buckets meaningful
      if (diff > 50) diff = 50;
      if (diff < -50) diff = -50;
      buckets[diff] = (buckets[diff] || 0) + 1;
      if (diff < min) min = diff;
      if (diff > max) max = diff;
    }
    if (max - min === 0 && (buckets[0] || 0) === 0) {
      renderEmpty("chart-line-diff", "暂无数据");
      return;
    }
    var labels = [];
    var values = [];
    for (var k = min; k <= max; k++) {
      labels.push(String(k));
      values.push(buckets[k] || 0);
    }
    renderVerticalBarColored("chart-line-diff", labels, values, function (i) {
      var k = parseInt(labels[i], 10);
      if (k > 0) return "#cf222e";
      if (k < 0) return "#1a7f37";
      return "#94a3b8";
    });
  }

  // ===== Cross-version tab =====
  function renderCrossTab() {
    if (history.status !== "included" || !(history.versions && history.versions.length)) {
      renderKpis("cross-kpis", [{ label: "跨版本上下文", value: "未启用", sub: history.reason || "history skipped", cls: "warn" }]);
      $("cross-banner").hidden = false;
      $("cross-banner").textContent = "跨版本视图不可用：" + (history.reason || "history skipped or no prior vN found");
      return;
    }
    var versions = history.versions;
    var hasAtk = !!history.atkIdField;
    var anySubset = versions.some(function (v) { return v.isSubsetRun; });
    var banner = $("cross-banner");
    if (anySubset) {
      banner.hidden = false;
      banner.textContent = "subset run indicator · 检测到部分版本执行的样本数显著少于上一版本（很可能来自 atk-run --only-failures），趋势绝对值不可直接同口径比较；请同时看失败率折线。";
    } else if (!hasAtk) {
      banner.hidden = false;
      banner.textContent = "当前 failure_cases.csv 未发现 atk_id 列，three-state per-case status 与目标命中率分析已降级。";
    } else {
      banner.hidden = true;
    }

    renderKpis("cross-kpis", buildCrossKpis());
    renderTrend(versions);
    renderReasonStack(versions);
    renderTargetTable();
    renderPersistentTable();
  }

  function buildCrossKpis() {
    var versions = history.versions;
    var nVersions = versions.length;
    var current = versions[nVersions - 1];
    var prev = nVersions >= 2 ? versions[nVersions - 2] : null;
    var trendArrow = "—";
    if (prev) {
      if (current.failedCount > prev.failedCount) trendArrow = "↑ +" + (current.failedCount - prev.failedCount);
      else if (current.failedCount < prev.failedCount) trendArrow = "↓ -" + (prev.failedCount - current.failedCount);
      else trendArrow = "→ 持平";
    }
    var hit = history.previousTargets && history.previousTargets.summary;
    var hitText = "—", hitClass = "brand";
    if (hit) {
      var total = hit.resolved + hit.partial + hit.unresolved + hit.indeterminate;
      if (total > 0) {
        var pct = Math.round((hit.resolved / total) * 100);
        hitText = pct + "% (" + hit.resolved + "/" + total + ")";
        hitClass = pct >= 70 ? "good" : pct >= 40 ? "warn" : "bad";
      }
    }
    return [
      { label: "对比基线", value: prev ? prev.name : "—", sub: prev ? (prev.failedCount + " 失败 · 当前 " + current.failedCount) : "无上一版本", cls: "brand" },
      { label: "失败数变化 (vs 上一版本)", value: trendArrow, sub: prev ? (prev.name + " → " + current.name) : "—", cls: trendArrow.indexOf("↓") === 0 ? "good" : trendArrow.indexOf("↑") === 0 ? "bad" : "brand" },
      { label: "上轮目标命中率", value: hitText, sub: history.previousTargets && history.previousTargets.previousVersion ? ("来源 " + history.previousTargets.previousVersion + "/tuning_plan.md") : "无 tuning_plan.md", cls: hitClass },
      { label: "顽固 case (≥" + (history.persistentMinFails || 2) + " 次)", value: String(persistentSet.size), sub: "全部历史", cls: persistentSet.size ? "warn" : "good" },
    ];
  }

  function renderTrend(versions) {
    var meta = $("trend-meta");
    var names = versions.map(function (v) { return v.name + (v.isSubsetRun ? " ·子集" : ""); });
    var failed = versions.map(function (v) { return v.failedCount; });
    var tested = versions.map(function (v) { return v.evalAvailable ? v.testedCount : null; });
    var failRate = versions.map(function (v) {
      if (!v.evalAvailable || !v.testedCount) return null;
      return Math.round((v.failedCount / v.testedCount) * 1000) / 10;
    });
    meta.textContent = "全部历史 v1..vN";
    if (!ECHARTS) { renderEmpty("chart-trend", "ECharts 未加载"); return; }
    var chart = ensureChart("chart-trend");
    chart.setOption({
      grid: { left: 50, right: 60, top: 36, bottom: 36 },
      tooltip: { trigger: "axis" },
      legend: { top: 4 },
      xAxis: { type: "category", data: names, axisLabel: { color: "#475569" } },
      yAxis: [
        { type: "value", name: "数量", position: "left" },
        { type: "value", name: "失败率(%)", position: "right", min: 0, max: 100, splitLine: { show: false } },
      ],
      series: [
        { name: "执行数", type: "line", data: tested, smooth: true, symbol: "circle", color: "#3457d5" },
        { name: "失败数", type: "bar", data: failed, color: "#fca5a5", barWidth: 18 },
        { name: "失败率(%)", type: "line", yAxisIndex: 1, data: failRate, smooth: true, symbol: "diamond", color: "#b42318" },
      ],
    }, true);
  }

  function renderReasonStack(versions) {
    var top = history.topReasons || [];
    if (!top.length) { renderEmpty("chart-reason-stack", "未识别 reason 字段或样本不足"); return; }
    $("stack-meta").textContent = "Top " + top.length + " 原因类目";
    if (!ECHARTS) { renderEmpty("chart-reason-stack", "ECharts 未加载"); return; }
    var names = versions.map(function (v) { return v.name; });
    var series = top.map(function (reason) {
      return {
        name: reason,
        type: "bar",
        stack: "reason",
        data: versions.map(function (v) { return v.reasonCounts[reason] || 0; }),
        emphasis: { focus: "series" },
      };
    });
    var chart = ensureChart("chart-reason-stack");
    chart.setOption({
      grid: { left: 50, right: 16, top: 60, bottom: 36 },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      legend: { top: 4, type: "scroll" },
      xAxis: { type: "category", data: names },
      yAxis: { type: "value", name: "次数" },
      series: series,
    }, true);
  }

  function renderTargetTable() {
    var host = $("targets-body");
    var pt = history.previousTargets || {};
    host.innerHTML = "";
    if (!pt.previousVersion) {
      host.appendChild(el("div", { class: "note-inline" }, ["无上一版本，跳过命中率分析。"]));
      $("targets-meta").textContent = "n/a";
      return;
    }
    if (!pt.available) {
      host.appendChild(el("div", { class: "note-inline" }, [pt.reason || "上一版本 tuning_plan.md 未找到。"]));
      $("targets-meta").textContent = "源：" + pt.previousVersion + "/tuning_plan.md（未找到）";
      return;
    }
    $("targets-meta").textContent = "源：" + pt.previousVersion + "/tuning_plan.md";
    var s = pt.summary || {};
    host.appendChild(el("div", { class: "status-legend" }, [
      el("span", null, [el("span", { class: "status-cell status-passed" }), "已解决 " + (s.resolved || 0)]),
      el("span", null, [el("span", { class: "status-cell status-failed" }), "未解决 " + (s.unresolved || 0)]),
      el("span", null, [el("span", { class: "status-cell status-failed", style: "background:#fef3c7;border-color:#f59e0b;" }), "部分解决 " + (s.partial || 0)]),
      el("span", null, [el("span", { class: "status-cell status-not_tested" }), "无法判断 " + (s.indeterminate || 0)]),
    ]));
    if (!pt.targets || !pt.targets.length) {
      host.appendChild(el("div", { class: "note-inline" }, ["未在 ## 目标异常清单 下提取到 bullet。"]));
      return;
    }
    var table = el("table", { class: "target-table" });
    var thead = el("thead", null, [el("tr", null, [
      el("th", null, ["状态"]),
      el("th", null, ["目标描述"]),
      el("th", null, ["atk_id"]),
      el("th", null, ["详情"]),
    ])]);
    table.appendChild(thead);
    var tbody = el("tbody");
    var resCN = { resolved: "已解决", partial: "部分解决", unresolved: "未解决", indeterminate: "无法判断" };
    for (var i = 0; i < pt.targets.length; i++) {
      var t = pt.targets[i];
      var tr = el("tr", { class: "target-row" });
      tr.appendChild(el("td", null, [el("span", { class: "res-" + t.resolution }, [resCN[t.resolution] || t.resolution])]));
      tr.appendChild(el("td", { class: "text" }, [t.text || "—"]));
      tr.appendChild(el("td", { class: "ids" }, [(t.atkIds || []).join(", ") || "—"]));
      tr.appendChild(el("td", null, [t.detail || ""]));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    host.appendChild(table);
  }

  function renderPersistentTable() {
    var host = $("persistent-body");
    host.innerHTML = "";
    if (!history.atkIdField) {
      host.appendChild(el("div", { class: "note-inline" }, ["无 atk_id 列，无法计算顽固 case。"]));
      $("persistent-meta").textContent = "n/a";
      return;
    }
    var rows = (history.perCase || []).filter(function (c) { return c.failedCount >= (history.persistentMinFails || 2); })
      .sort(function (a, b) { return b.failedCount - a.failedCount; });
    $("persistent-meta").textContent = rows.length + " 条 · 阈值 ≥" + (history.persistentMinFails || 2);
    if (!rows.length) {
      host.appendChild(el("div", { class: "note-inline" }, ["未发现满足阈值的顽固 case。"]));
      return;
    }
    var versions = (history.versions || []).map(function (v) { return v.name; });
    host.appendChild(el("div", { class: "status-legend" }, [
      el("span", null, [el("span", { class: "status-cell status-failed" }), "failed"]),
      el("span", null, [el("span", { class: "status-cell status-passed" }), "passed"]),
      el("span", null, [el("span", { class: "status-cell status-not_tested" }), "not_tested"]),
      el("span", null, [el("span", { class: "status-cell status-unknown" }), "unknown"]),
    ]));
    var table = el("table", { class: "persistent-table" });
    var thead = el("thead", null, [el("tr", null, [
      el("th", null, ["atk_id"]),
      el("th", null, ["失败次数"]),
      el("th", null, ["首次出现"]),
      el("th", null, ["跨版本状态 v1 → " + payload.version]),
    ])]);
    table.appendChild(thead);
    var tbody = el("tbody");
    var maxRows = 200;
    for (var i = 0; i < rows.length && i < maxRows; i++) {
      var c = rows[i];
      var tr = el("tr");
      tr.appendChild(el("td", { class: "atk-id" }, [c.atkId]));
      tr.appendChild(el("td", null, [el("span", { class: "count-pill" }, [String(c.failedCount) + " / " + c.testedCount])]));
      tr.appendChild(el("td", null, [c.firstSeenVersion || "—"]));
      var strip = el("td");
      var bar = el("span", { class: "status-strip" });
      for (var vi = 0; vi < versions.length; vi++) {
        var st = c.statuses[versions[vi]] || "unknown";
        bar.appendChild(el("span", { class: "status-cell status-" + st, title: versions[vi] + ": " + st }));
      }
      strip.appendChild(bar);
      tr.appendChild(strip);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    host.appendChild(table);
    if (rows.length > maxRows) {
      host.appendChild(el("div", { class: "note-inline" }, ["仅显示前 " + maxRows + " 条，剩余 " + (rows.length - maxRows) + " 条折叠。"]));
    }
  }

  // ===== ECharts helpers =====
  function ensureChart(id) {
    if (!ECHARTS) return null;
    var node = $(id);
    if (!node) return null;
    if (charts[id]) { try { charts[id].dispose(); } catch (e) {} }
    var c = ECHARTS.init(node, null, { renderer: "canvas" });
    charts[id] = c;
    return c;
  }
  function renderEmpty(id, text) {
    var node = $(id);
    if (!node) return;
    if (charts[id]) { try { charts[id].dispose(); } catch (e) {} delete charts[id]; }
    node.innerHTML = "";
    node.classList.add("empty-chart");
    node.appendChild(document.createTextNode(text || "暂无数据"));
  }
  function renderHorizontalBar(id, items, seriesName) {
    if (!items || !items.length) { renderEmpty(id, "暂无数据"); return; }
    if (!ECHARTS) { renderEmpty(id, "ECharts 未加载"); return; }
    var names = items.map(function (it) { return it[0]; });
    var values = items.map(function (it) { return it[1]; });
    var chart = ensureChart(id);
    chart.setOption({
      grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: { type: "value", name: seriesName || "" },
      yAxis: { type: "category", data: names.reverse(), axisLabel: { interval: 0, formatter: function (v) { return v.length > 28 ? v.slice(0, 27) + "…" : v; } } },
      series: [{ name: seriesName || "count", type: "bar", data: values.reverse(), color: "#3457d5", barMaxWidth: 18 }],
    }, true);
  }
  function renderVerticalBarColored(id, labels, values, colorFn) {
    if (!ECHARTS) { renderEmpty(id, "ECharts 未加载"); return; }
    var data = values.map(function (v, i) { return { value: v, itemStyle: { color: colorFn(i) } }; });
    var chart = ensureChart(id);
    chart.setOption({
      grid: { left: 40, right: 16, top: 16, bottom: 36 },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: { type: "category", data: labels, name: "行数差 (actual - expected)", nameLocation: "middle", nameGap: 24 },
      yAxis: { type: "value", name: "case 数" },
      series: [{ type: "bar", data: data, barMaxWidth: 18 }],
    }, true);
  }
  function renderPie(id, data) {
    if (!data || !data.length) { renderEmpty(id, "暂无数据"); return; }
    if (!ECHARTS) { renderEmpty(id, "ECharts 未加载"); return; }
    var chart = ensureChart(id);
    chart.setOption({
      tooltip: { trigger: "item" },
      legend: { type: "scroll", bottom: 0 },
      series: [{
        type: "pie",
        radius: ["35%", "65%"],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 4, borderColor: "#fff", borderWidth: 2 },
        label: { formatter: "{b}: {c}" },
        data: data,
      }],
    }, true);
  }
  function renderKpis(hostId, items) {
    var host = $(hostId);
    if (!host) return;
    host.innerHTML = "";
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      host.appendChild(el("div", { class: "kpi " + (it.cls || "") }, [
        el("div", { class: "label" }, [it.label]),
        el("div", { class: "value" }, [String(it.value)]),
        el("div", { class: "sub" }, [it.sub || ""]),
      ]));
    }
  }

  window.addEventListener("resize", function () {
    for (var k in charts) { try { charts[k].resize(); } catch (e) {} }
  });

  init();
})();
