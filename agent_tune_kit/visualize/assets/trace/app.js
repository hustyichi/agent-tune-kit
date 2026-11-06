(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("trace-data").textContent);
  const $ = id => document.getElementById(id);
  const pretty = value => typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const refKey = ref => JSON.stringify([ref.path, ref.sha256, ref.pointer ?? "", ref.line ?? null, ref.bytes ?? null]);
  const el = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; };
  const button = (text, action, className) => { const node = el("button", text, className); node.type = "button"; node.onclick = action; return node; };
  const pre = value => el("pre", pretty(value));
  const section = (title, value) => { const node = el("section", undefined, "detail-section"); node.append(el("h3", title)); if (value !== undefined) node.append(pre(value)); return node; };
  const options = (select, values, all) => { select.replaceChildren(); if (all !== undefined) { const item = el("option", all); item.value = ""; select.append(item); } for (const value of values) { const item = el("option", value); item.value = value; select.append(item); } };
  const references = value => { const result = []; const seen = new Set(); const walk = item => { if (Array.isArray(item)) item.forEach(walk); else if (item && typeof item === "object") { if (typeof item.path === "string" && typeof item.sha256 === "string") { const key = refKey(item); if (!seen.has(key)) { seen.add(key); result.push(item); } } else Object.values(item).forEach(walk); } }; walk(value); return result; };
  let currentCapture = data.captures.length ? data.captures[data.captures.length - 1] : null;
  let tracePage = 0, issuePage = 0, returnFocus = null, activeIssue = null, issueStage = "issue";

  let currentView = "traces", selectedNode = null, origin = null;
  let issueTrigger = null;
  const issueById = new Map(data.issues.map(item => [item.record.id, item]));
  const captureById = new Map(data.captures.map(c => [c.id, c]));
  const issuesByCapture = new Map(data.captures.map(c => [c.id, new Map()]));
  for (const item of data.issues) {
    for (const ref of references(item.record.evidence)) {
      const saved = data.references[refKey(ref)];
      const counts = issuesByCapture.get(saved?.capture_id || saved?.location?.capture_id);
      if (counts) counts.set(item, (counts.get(item) || 0) + 1);
    }
  }
  const indexedRef = ref => {
    const saved = data.references[refKey(ref)];
    const capture = captureById.get(saved?.location?.capture_id);
    const node = capture?.nodes[saved.location.node_index];
    return {saved, capture, node};
  };
  const swim = window.createSwimlanes({
    issuesForNode: node => node.issue_ids || [],
    onNode: node => navigateNode(currentCapture, node),
  });
  const filters = ["trace-filter", "type-filter", "trace-search", "page-size"];
  const rememberView = () => ({
    view: currentView, capture: currentCapture?.id, filters: filters.map(id => $(id).value),
    page: tracePage, lanes: swim.snapshot(), selected: selectedNode, scroll: window.scrollY
  });
  const restoreView = state => {
    setView(state.view);
    $("capture-filter").value = state.capture || "";
    chooseCapture();
    filters.forEach((id, i) => { $(id).value = state.filters[i]; });
    tracePage = state.page;
    selectedNode = state.selected;
    renderTraces();
    swim.restore(state.lanes);
    window.scrollTo(0, state.scroll);
  };
  const openDrawer = (title, nodeMode = false) => {
    if ($("drawer").hidden) returnFocus = document.activeElement;
    $("drawer-title").textContent = title;
    $("drawer-content").replaceChildren();
    $("drawer").classList.toggle("node-drawer", nodeMode);
    $("drawer").hidden = false;
    $("drawer-backdrop").hidden = false;
    document.body.style.overflow = "hidden";
    $("drawer-content").scrollTop = 0;
    $("drawer-close").focus({preventScroll: true});
  };
  const closeDrawer = () => {
    const fromNode = $("drawer").classList.contains("node-drawer");
    $("drawer").hidden = $("drawer-backdrop").hidden = true;
    document.body.style.overflow = "";
    activeIssue = null;
    origin = null;
    if (fromNode && selectedNode) swim.focusSelected();
    else if (returnFocus?.isConnected) returnFocus.focus({preventScroll:true});
    else if (issueTrigger) document.querySelector(`[data-issue-id="${CSS.escape(issueTrigger)}"]`)?.focus({preventScroll:true});
    issueTrigger = null;
  };
  const backToIssue = () => {
    if (!activeIssue) return;
    const back = button("← 返回问题与证据", () => {
      const stored = origin;
      if (stored) restoreView(stored.state);
      origin = null;
      showIssue(activeIssue, issueStage);
      if (stored) [...$("drawer-content").querySelectorAll(".reference")].find(b => b.dataset.key === stored.key)?.focus();
    });
    back.id = "return-to-issue";
    $("drawer-content").append(back);
  };
  const fieldPreview = (ref, saved) => {
    const box = section("命中证据字段");
    box.id = "evidence-field";
    box.dataset.pointer = ref.pointer || "";
    const label = ref.pointer || (ref.line ? `第 ${ref.line} 行` : "整个文件");
    box.append(el("code", label), pre(saved.preview.text));
    if (saved.preview.truncated) box.append(el("p", `仅展示前 ${saved.preview.text.length} / ${saved.preview.characters} 个字符。完整字段可从本地归档读取。`, "muted"));
    return box;
  };
  const openReference = ref => {
    const {node, capture, saved} = indexedRef(ref);
    if (activeIssue && !origin) origin = {state: rememberView(), key: refKey(ref)};
    if (node && saved.preview && !saved.error) { navigateNode(capture, node, ref); return; }
    openDrawer(saved?.preview ? "归档证据" : "证据不可定位");
    backToIssue();
    if (saved?.preview) $("drawer-content").append(fieldPreview(ref, saved));
    else $("drawer-content").append(el("p", saved?.error || "此引用没有可用的归档预览。", "warning"));
    $("drawer-content").append(section("原始引用", ref));
  };
  const appendReferences = (parent, value) => {
    for (const ref of references(value)) {
      const {node, saved} = indexedRef(ref);
      const where = ref.pointer || (ref.line ? `第 ${ref.line} 行` : ref.path);
      const label = node ? `${node.type === "TRACE" ? "Trace" : (node.name || node.type)} · ${String(node.trace_id || "").slice(0, 8)}` : (saved?.error ? "证据不可定位" : "归档材料");
      const link = button("", () => openReference(ref), "reference evidence-link");
      link.append(el("strong", label), el("code", where));
      link.dataset.key = refKey(ref);
      link.dataset.pointer = ref.pointer || "";
      parent.append(link);
    }
  };
  const recordCard = (title, record) => {
    const card = section(title); card.classList.add("record");
    if (record.error) card.append(el("p", record.error, "warning"));
    const content = record.record || record.text;
    if (content !== undefined) {
      if (typeof content === "string") card.append(pre(content));
      else {
        const summary = [];
        for (const key of ["status", "target_issues", "candidate_loaded"]) if (content[key] !== undefined) summary.push(`${key}: ${pretty(content[key])}`);
        card.append(el("p", summary.join(" · "), "record-meta"));
        if (content.candidate) card.append(section("候选身份", content.candidate));
        if (content.execution) card.append(section("实际执行", content.execution));
        if (content.conclusions) for (const [issueId, conclusion] of Object.entries(content.conclusions)) { const outcome = el("div", undefined, "outcome"); outcome.append(el("strong", `${issueId} · ${conclusion.outcome || "inconclusive"}`), el("span", conclusion.reason || "请阅读完整验证条件和依据。")); card.append(outcome); }
        if (content.limitations && content.limitations.length) card.append(section("局限与未执行部分", content.limitations));
        const details = el("details"); details.append(el("summary", "展开完整记录"), pre(content)); card.append(details);
      }
    }
    appendReferences(card, record);
    return card;
  };
  const showIssue = (item, stage = "issue") => {
    if (!activeIssue || activeIssue.record.id !== item.record.id) {
      origin = null;
      issueTrigger = item.record.id;
    }
    activeIssue = item; issueStage = stage;
    const issue = item.record;
    openDrawer(`${issue.id} · ${issue.title || "问题详情"}`);
    const tabs = el("nav", undefined, "stage-tabs"); tabs.setAttribute("aria-label", "问题处理阶段");
    for (const [key, label] of [["issue", "问题与证据"], ["diagnoses", "诊断与方案"], ["changes", "修改候选"], ["verifications", "实际验证"]]) { const tab = button(label, () => showIssue(item, key), key === stage ? "active" : ""); tab.setAttribute("aria-pressed", String(key === stage)); tabs.append(tab); }
    $("drawer-content").append(tabs);
    if (stage === "issue") {
      const description = section("问题描述");
      description.append(el("p", issue.description || issue.title));
      const evidence = section("关联证据");
      appendReferences(evidence, issue.evidence);
      $("drawer-content").append(description, evidence);
      const details = el("details", undefined, "detail-section");
      details.append(el("summary", "完整问题记录"), pre(issue)); $("drawer-content").append(details);
      return;
    }
    if (stage === "diagnoses") {
      if (!item.diagnoses.length) $("drawer-content").append(el("p", "尚未记录诊断与修复／验证方案。", "empty"));
      for (const diagnosis of item.diagnoses) $("drawer-content").append(recordCard(diagnosis.reference.path, diagnosis));
    } else if (stage === "changes") {
      if (!item.changes.length) $("drawer-content").append(el("p", "尚未记录实际修改。局部检查不代表 Agent 效果验证。", "empty"));
      for (const change of item.changes) $("drawer-content").append(recordCard(change.reference.path, change));
    } else {
      const seen = new Set(); let count = 0;
      for (const change of item.changes) for (const verification of change.verifications || []) { const key = refKey(verification.reference); if (!seen.has(key)) { seen.add(key); count++; $("drawer-content").append(recordCard(verification.reference.path, verification)); } }
      if (!count) $("drawer-content").append(el("p", "尚未记录实际验证；不能据修改完成判断问题已经解决。", "empty"));
    }
  };
  const navigateNode = (capture, node, ref = null) => {
    const targetIndex = capture.nodes.indexOf(node);
    if (targetIndex < 0) return;
    if (currentCapture !== capture) {
      $("capture-filter").value = capture.id;
      chooseCapture();
    }
    setView("traces");
    if (!filteredNodes().includes(node)) {
      for (const id of ["type-filter", "trace-filter", "trace-search"]) $(id).value = "";
    }
    selectedNode = node;
    const rows = filteredNodes();
    tracePage = Math.floor(rows.indexOf(node) / Number($("page-size").value));
    renderTraces(rows);
    swim.reveal(node);
    openDrawer(node.name || node.id || "轨迹节点", true);
    // The mobile sheet changes the chart viewport. Scroll after its resize without moving dialog focus.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (selectedNode === node && !$("drawer").hidden && $("drawer").classList.contains("node-drawer")) swim.scrollSelected();
    }));
    backToIssue();
    const identification = el("p", `${capture.id} · Trace ${node.trace_id || "无归属"} · ${node.type}`, "mono muted");
    identification.dataset.nodeIndex = String(targetIndex);
    identification.dataset.traceId = node.trace_id || "";
    identification.dataset.nodeId = node.id || "";
    $("drawer-content").append(identification);
    if (ref) $("drawer-content").append(fieldPreview(ref, data.references[refKey(ref)]));
    if (node.issue_ids?.length) {
      const cited = section("引用此节点的问题");
      for (const id of node.issue_ids) { const item = issueById.get(id); if (item) cited.append(button(id, () => showIssue(item))); }
      $("drawer-content").append(cited);
    }
    const meta = {capture: capture.id, id: node.id, trace_id: node.trace_id, type: node.type, start_time: node.start_time, end_time: node.end_time, reference: node.reference};
    if (node.level) meta.level = node.level;
    const metadata = el("details", undefined, "detail-section");
    metadata.append(el("summary", "节点与原始引用"), pre(meta)); $("drawer-content").append(metadata);
    const relations = section("调用关联");
    const siblings = node.trace_id ? capture.nodes.filter(item => item.trace_id === node.trace_id) : [];
    const wrapper = siblings.find(item => item.type === "TRACE");
    if (wrapper && wrapper !== node) relations.append(button("所属 Trace", () => navigateNode(capture, wrapper)));
    const byId = new Map();
    for (const item of siblings) if (item.type !== "TRACE" && item.id) {
      if (byId.has(item.id)) byId.set(item.id, null); else byId.set(item.id, item);
    }
    const seen = new Set([node]);
    let cursor = node;
    if (!node.trace_id && node.parent_id) relations.append(el("p", "缺少 Trace 归属，无法确认父子关系。", "muted"));
    while (node.trace_id && cursor.parent_id) {
      const next = byId.get(cursor.parent_id);
      if (!next || seen.has(next)) {
        const why = next ? "父子关系成环" : byId.has(cursor.parent_id) ? "同一 Trace 内有重复的父 ID" : "父节点未归档";
        relations.append(el("p", why, "warning")); break;
      }
      if (cursor === node) relations.append(button(`父节点 · ${next.name || next.id}`, () => navigateNode(capture, next)));
      seen.add(next); cursor = next;
    }
    if (node.type !== "TRACE" && node.id && byId.get(node.id) === node) {
      const links = el("div", undefined, "parent-links");
      for (const child of siblings.filter(item => item.parent_id === node.id && !seen.has(item))) {
        links.append(button(`子节点 · ${child.name || child.id}`, () => navigateNode(capture, child)));
      }
      if (links.childElementCount) relations.append(links);
    } else if (node.trace_id && node.type !== "TRACE" && node.id) relations.append(el("p", "节点 ID 重复，子节点归属不确定。", "warning"));
    if (relations.childElementCount > 1) $("drawer-content").append(relations);
    for (const key of ["input", "output"]) {
      const detail = el("details", undefined, "detail-section");
      detail.append(el("summary", key === "input" ? "节点输入" : "节点输出"));
      detail.addEventListener("toggle", () => {
        if (detail.open && detail.childElementCount === 1) detail.append(pre(node[key] ?? "原始记录未提供值"));
      });
      $("drawer-content").append(detail);
    }
  };
  const setView = view => { currentView = view; for (const item of ["overview", "traces", "issues"]) { $(`view-${item}`).hidden = item !== view; $(`tab-${item}`).classList.toggle("active", item === view); $(`tab-${item}`).setAttribute("aria-selected", String(item === view)); } };
  for (const tab of document.querySelectorAll("[data-view]")) tab.onclick = () => setView(tab.dataset.view);
  $("session-title").textContent = data.session.session_id;
  $("session-source").textContent = Object.entries(data.source).filter(([, value]) => value).map(([key, value]) => `${key}: ${value}`).join(" · ");
  $("generated").textContent = `生成于 ${data.generated_at}`;

  for (const warning of data.warnings) $("warnings").append(el("p", warning, "warning"));
  const overview = el("div", undefined, "overview-grid");
  const overviewCard = (title, value, wide = false) => { const card = section(title, value); card.className = `overview-card${wide ? " wide" : ""}`; overview.append(card); return card; };
  overviewCard("来源与会话身份", {session_id: data.session.session_id, session_key: data.session.session_key, source: data.source});
  overviewCard("项目与运行线索", data.project || "未初始化项目上下文；这不阻塞已有轨迹分析。");
  const coverage = overviewCard("实际检查覆盖", undefined, true);
  coverage.append(el("p", data.session.detections.length ? "以下为各次检测的输入、检查范围与缺口；不会因重采或零问题而自动扩大覆盖。" : "尚未记录异常识别；当前只提供原始采集材料。", "muted"));
  for (const detection of data.session.detections) { const detail = el("details"); detail.append(el("summary", `${detection.id || "检查记录"} · ${detection.status || "状态未提供"}`), pre(detection)); appendReferences(detail, detection); coverage.append(detail); }
  for (const capture of data.captures) { const card = overviewCard(`采集 ${capture.id}`, undefined, true); if (capture.error) card.append(el("p", capture.error, "warning")); const detail = el("details"); detail.append(el("summary", "查看获取范围、字段、完整性和摘要"), pre(capture.manifest)); card.append(detail, button("浏览此快照", () => { $("capture-filter").value = capture.id; chooseCapture(); setView("traces"); })); }
  const digests = overviewCard("生成时采用的处理记录摘要", undefined, true); const digestDetails = el("details"); digestDetails.append(el("summary", "展开记录与 SHA-256"), pre(data.record_digests)); digests.append(digestDetails); $("view-overview").append(overview);

  let cachedSearch = null;
  const filteredNodes = () => {
    const text = $("trace-search").value.trim().toLocaleLowerCase(), trace = $("trace-filter").value, type = $("type-filter").value;
    const all = currentCapture?.nodes || [];
    if (!text && !trace && !type) return all;
    if (cachedSearch?.all === all && cachedSearch.text === text && cachedSearch.trace === trace && cachedSearch.type === type) return cachedSearch.rows;
    const rows = all.filter(node => (!trace || node.trace_id === trace) && (!type || node.type === type) && (!text || pretty(node).toLocaleLowerCase().includes(text)));
    cachedSearch = {all, text, trace, type, rows};
    return rows;
  };
  const renderTraces = (filtered = filteredNodes()) => {
    const all = currentCapture?.nodes || [];
    swim.render(currentCapture, filtered, selectedNode);
    const size = Number($("page-size").value), pages = Math.max(1, Math.ceil(filtered.length / size));
    tracePage = Math.max(0, Math.min(tracePage, pages - 1));
    $("trace-list").replaceChildren();
    for (const node of filtered.slice(tracePage * size, (tracePage + 1) * size)) {
      const row = button("", () => navigateNode(currentCapture, node), "trace-row");
      row.setAttribute("aria-current", String(node === selectedNode));
      const main = el("div", undefined, "node-main");
      main.append(el("div", node.name || node.id || "未命名节点", "node-label"), el("div", `${node.id || "无节点 ID"} · Trace ${node.trace_id || "未提供"} · ${node.start_time || "未提供时间"}`, "node-meta"));
      row.append(el("div", node.type || "node", "node-branch"), main, el("span", "查看 →", "muted")); $("trace-list").append(row);
    }
    if (!filtered.length) $("trace-list").append(el("p", all.length ? "没有符合筛选条件的节点。" : "此快照没有可浏览节点。", "empty"));
    $("trace-count").textContent = `${filtered.length} / ${all.length} 项（含 Trace 与附件）`; $("trace-page").textContent = `${tracePage + 1} / ${pages}`; $("trace-prev").disabled = !tracePage; $("trace-next").disabled = tracePage + 1 >= pages;
  };
  const chooseCapture = () => {
    currentCapture = captureById.get($("capture-filter").value) || null;
    selectedNode = null; tracePage = 0; cachedSearch = null;
    const nodes = currentCapture?.nodes || [], manifest = currentCapture?.manifest || {}, scope = manifest.completeness || {};
    const traces = [...new Set(nodes.map(node => node.trace_id).filter(Boolean))];
    options($("trace-filter"), traces, "全部 Trace");
    options($("type-filter"), [...new Set(nodes.map(node => node.type).filter(Boolean))], "全部类型");
    const linked = issuesByCapture.get(currentCapture?.id) || new Map();
    $("summary").replaceChildren();
    for (const [value, label] of [[scope.trace_count ?? traces.length, "Trace"], [scope.observation_count ?? "—", "观测节点"], [linked.size, "关联问题"]]) {
      const stat = el("div", undefined, "stat"); stat.append(el("strong", value), el("span", label)); $("summary").append(stat);
    }
    $("lane-issues").replaceChildren();
    $("lane-issue-count").textContent = linked.size;
    for (const [item, count] of linked) {
      const link = button("", () => showIssue(item, "issue"), "lane-issue");
      link.dataset.issueId = item.record.id;
      link.append(el("strong", item.record.id, "issue-id"), el("span", item.record.title), el("small", `${count} 处证据`, "muted"));
      $("lane-issues").append(link);
    }
    const checks = data.session.detections.filter(item => item.captures?.includes(currentCapture?.id));
    if (!linked.size) $("lane-issues").append(el("p", checks.length ? "此快照没有已关联的问题证据。" : "此快照尚未检查。", "muted"));
    $("capture-status").replaceChildren();
    if (currentCapture) {
      if (currentCapture.error) $("capture-status").append(el("p", currentCapture.error, "warning"));
      const localized = {partial:"部分", complete:"完整", completed:"已完成"};
      const coverage = checks.length ? [...new Set(checks.map(i => localized[i.status] || i.status || "未提供"))].join(" / ") : "尚未检查";
      const timestamp = manifest.completed_at || manifest.started_at;
      $("capture-status").append(el("p", [timestamp, `采集范围：${localized[scope.status] || scope.status || "未提供"}`, `检测覆盖：${coverage}`].filter(Boolean).join(" · "), "muted"));
    }
    renderTraces();
  };
  options($("capture-filter"), data.captures.map(capture => capture.id)); if (currentCapture) $("capture-filter").value = currentCapture.id; $("capture-filter").onchange = chooseCapture;
  for (const id of ["trace-search", "trace-filter", "type-filter", "page-size"]) $(id).addEventListener(id === "trace-search" ? "input" : "change", () => { tracePage = 0; renderTraces(); });
  $("trace-prev").onclick = () => { tracePage--; renderTraces(); }; $("trace-next").onclick = () => { tracePage++; renderTraces(); };
  const renderIssues = () => {
    const text = $("issue-search").value.toLocaleLowerCase(), status = $("issue-status").value;
    const filtered = data.issues.filter(item => (!status || item.record.status === status) && (!text || pretty(item.record).toLocaleLowerCase().includes(text)));
    const pages = Math.max(1, Math.ceil(filtered.length / 20)); issuePage = Math.min(issuePage, pages - 1); $("issue-list").replaceChildren();
    for (const item of filtered.slice(issuePage * 20, (issuePage + 1) * 20)) { const issue = item.record; const card = button("", () => showIssue(item), "issue-card"); card.dataset.issueId = issue.id; const heading = el("div", issue.id, "issue-id"); heading.append(el("span", issue.status || "未提供状态", "badge")); card.append(heading, el("h3", issue.title || "问题"), el("p", String(issue.description || "").slice(0, 260))); $("issue-list").append(card); }
    if (!filtered.length) $("issue-list").append(el("p", data.issues.length ? "没有符合筛选条件的问题。" : data.session.detections.length ? "已保存检测记录，尚无已记录问题；请核对实际检查范围和未读材料。" : "尚未进行异常识别。", "empty"));
    $("issue-count").textContent = `${filtered.length} / ${data.issues.length} 个已记录问题`; $("issue-page").textContent = `${issuePage + 1} / ${pages}`; $("issue-prev").disabled = !issuePage; $("issue-next").disabled = issuePage + 1 >= pages;
  };
  options($("issue-status"), [...new Set(data.issues.map(item => item.record.status).filter(Boolean))], "全部状态"); $("issue-search").oninput = () => { issuePage = 0; renderIssues(); }; $("issue-status").onchange = () => { issuePage = 0; renderIssues(); }; $("issue-prev").onclick = () => { issuePage--; renderIssues(); }; $("issue-next").onclick = () => { issuePage++; renderIssues(); };
  $("drawer-close").onclick = closeDrawer; $("drawer-backdrop").onclick = closeDrawer;
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !$("drawer").hidden) { event.preventDefault(); closeDrawer(); }
    if (event.key === "Tab" && !$("drawer").hidden) { const focusable = [...$("drawer").querySelectorAll("button,input,select,summary,[tabindex='0']")].filter(node => !node.disabled && node.getClientRects().length); const first = focusable[0], last = focusable[focusable.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }
    if (["ArrowLeft", "ArrowRight"].includes(event.key) && document.activeElement.matches("[data-view]")) { event.preventDefault(); const tabs = [...document.querySelectorAll("[data-view]")], index = tabs.indexOf(document.activeElement); const tab = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length]; tab.click(); tab.focus(); }
  });
  setView("traces"); chooseCapture(); renderIssues();
})();
