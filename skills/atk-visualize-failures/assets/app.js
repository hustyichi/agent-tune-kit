(function () {
  "use strict";

  var dataNode = document.getElementById("failure-data");
  var payload = JSON.parse(dataNode.textContent);
  var roles = JSON.parse(JSON.stringify(payload.roles || {}));
  var facets = payload.facets || [];

  var state = {
    query: "",
    activeFacets: {}, // {field: Set<string>}
    pageSize: payload.config.defaultPageSize,
    page: 0,
    selectedRowNumber: payload.rows[0] ? payload.rows[0].rowNumber : null,
    diffMode: "split", // split | unified | plain
    showEmptyFields: false,
  };

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
    renderRolesDrawer();
    renderReportDrawer();
    renderWarningsDrawer();
    bindControls();
    render();
  }

  function renderHeaderMeta() {
    $("path-context").textContent = payload.currentDir + " → " + payload.output;
    $("meta-version").textContent = payload.version;
    $("meta-rows").textContent = payload.rowCount + " 行";
    $("meta-fields").textContent = payload.fieldnames.length + " 列";
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
    var diffButtons = document.querySelectorAll("[data-diff-mode]");
    for (var i = 0; i < diffButtons.length; i++) {
      diffButtons[i].addEventListener("click", function (ev) {
        state.diffMode = ev.currentTarget.getAttribute("data-diff-mode");
        renderDetail();
      });
    }
    $("toggle-empty-fields").addEventListener("change", function (ev) {
      state.showEmptyFields = !!ev.target.checked;
      renderDetail();
    });
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

  init();
})();
