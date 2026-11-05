(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("trace-data").textContent);
  const $ = id => document.getElementById(id);
  const pretty = value => typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const refKey = ref => JSON.stringify([ref.path, ref.sha256, ref.pointer || "", ref.line || null]);
  const el = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; };
  const button = (text, action, className) => { const node = el("button", text, className); node.type = "button"; node.onclick = action; return node; };
  const pre = value => el("pre", pretty(value));
  const section = (title, value) => { const node = el("section", undefined, "detail-section"); node.append(el("h3", title)); if (value !== undefined) node.append(pre(value)); return node; };
  const options = (select, values, all) => { select.replaceChildren(); if (all !== undefined) { const item = el("option", all); item.value = ""; select.append(item); } for (const value of values) { const item = el("option", value); item.value = value; select.append(item); } };
  const references = value => { const result = []; const seen = new Set(); const walk = item => { if (Array.isArray(item)) item.forEach(walk); else if (item && typeof item === "object") { if (typeof item.path === "string" && typeof item.sha256 === "string") { const key = refKey(item); if (!seen.has(key)) { seen.add(key); result.push(item); } } else Object.values(item).forEach(walk); } }; walk(value); return result; };
  let currentCapture = data.captures.length ? data.captures[data.captures.length - 1] : null;
  let tracePage = 0, issuePage = 0, returnFocus = null, activeIssue = null, issueStage = "issue";
  const nodeIndex = new Map();
  for (const capture of data.captures) for (const node of capture.nodes) if (node.reference) nodeIndex.set(refKey(node.reference), {capture, node});
  const findNode = (capture, id, trace) => capture.nodes.find(node => node.id === id && (!trace || node.trace_id === trace));
  const openDrawer = title => { if ($("drawer").hidden) returnFocus = document.activeElement; $("drawer-title").textContent = title; $("drawer-content").replaceChildren(); $("drawer").hidden = false; $("drawer-backdrop").hidden = false; document.body.style.overflow = "hidden"; $("drawer-close").focus(); };
  const closeDrawer = () => { $("drawer").hidden = true; $("drawer-backdrop").hidden = true; document.body.style.overflow = ""; activeIssue = null; if (returnFocus && returnFocus.isConnected) returnFocus.focus(); };
  const backToIssue = () => { if (activeIssue) $("drawer-content").append(button("← 返回问题阶段", () => showIssue(activeIssue, issueStage))); };
  const openReference = ref => {
    const indexed = nodeIndex.get(refKey(ref));
    if (indexed) { showNode(indexed.capture, indexed.node); return; }
    openDrawer("原始证据定位"); backToIssue(); $("drawer-content").append(pre(ref));
    const saved = data.references[refKey(ref)];
    if (!saved || saved.error) $("drawer-content").append(el("p", saved ? saved.error : "此引用未嵌入或不可用，请按本地路径核对原始归档。", "warning"));
    else $("drawer-content").append(pre(saved.preview));
  };
  const appendReferences = (parent, value) => { for (const ref of references(value)) parent.append(button(`${ref.path}${ref.pointer || ""}`, () => openReference(ref), "reference mono")); };
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
    activeIssue = item; issueStage = stage;
    const issue = item.record;
    openDrawer(`${issue.id} · ${issue.title || "问题详情"}`);
    const tabs = el("nav", undefined, "stage-tabs"); tabs.setAttribute("aria-label", "问题处理阶段");
    for (const [key, label] of [["issue", "问题与证据"], ["diagnoses", "诊断与方案"], ["changes", "修改候选"], ["verifications", "实际验证"]]) { const tab = button(label, () => showIssue(item, key), key === stage ? "active" : ""); tab.setAttribute("aria-pressed", String(key === stage)); tabs.append(tab); }
    $("drawer-content").append(tabs);
    if (stage === "issue") { $("drawer-content").append(recordCard("问题记录", {record: issue})); return; }
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
  const showNode = (capture, node) => {
    openDrawer(node.name || node.id || "轨迹节点"); backToIssue();
    $("drawer-content").append(section("原始定位", {capture: capture.id, id: node.id, trace_id: node.trace_id, type: node.type, start_time: node.start_time || "未提供时间", end_time: node.end_time, reference: node.reference}));
    const relations = section("调用关联（按原生父子 ID）");
    const traceRoot = capture.nodes.find(item => item.type === "TRACE" && item.trace_id === node.trace_id && item.id !== node.id);
    if (traceRoot) relations.append(button(`所属 Trace · ${traceRoot.id}`, () => showNode(capture, traceRoot)));
    if (node.parent_id) { const parent = findNode(capture, node.parent_id, node.trace_id); relations.append(parent ? button(`父节点 · ${parent.name || parent.id}`, () => showNode(capture, parent)) : el("p", `父节点 ${node.parent_id} 不在此快照材料中。`, "warning")); }
    else relations.append(el("p", "原始记录未声明父节点；不据此合成最终答案。", "muted"));
    const children = capture.nodes.filter(child => child.parent_id === node.id && child.trace_id === node.trace_id);
    const links = el("div", undefined, "parent-links"); for (const child of children) links.append(button(`子节点 · ${child.name || child.id}`, () => showNode(capture, child))); relations.append(links); $("drawer-content").append(relations);
    for (const key of ["input", "output"]) { const detail = el("details", undefined, "detail-section"); detail.append(el("summary", `${key === "input" ? "输入" : "输出"} · 按需展开`)); detail.addEventListener("toggle", () => { if (detail.open && detail.childElementCount === 1) detail.append(pre(node[key] === undefined ? "原始记录未提供此字段" : node[key])); }); $("drawer-content").append(detail); }
    if (node.reference) $("drawer-content").append(pre(node.reference));
  };
  const setView = view => { for (const item of ["overview", "traces", "issues"]) { $(`view-${item}`).hidden = item !== view; $(`tab-${item}`).classList.toggle("active", item === view); $(`tab-${item}`).setAttribute("aria-selected", String(item === view)); } };
  for (const tab of document.querySelectorAll("[data-view]")) tab.onclick = () => setView(tab.dataset.view);
  $("session-title").textContent = data.session.session_id;
  $("session-source").textContent = Object.entries(data.source).filter(([, value]) => value).map(([key, value]) => `${key}: ${value}`).join(" · ");
  $("generated").textContent = `生成于 ${data.generated_at}`;
  for (const [value, label] of [[data.captures.length, "采集快照"], [data.issues.length, "已记录问题"], [data.session.detections.length, "检查记录"]]) { const stat = el("div", undefined, "stat"); stat.append(el("strong", value), el("span", label)); $("summary").append(stat); }
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

  const renderTraces = () => {
    const text = $("trace-search").value.toLocaleLowerCase(), trace = $("trace-filter").value, type = $("type-filter").value;
    const all = currentCapture ? currentCapture.nodes : [];
    const filtered = all.filter(node => (!trace || node.trace_id === trace) && (!type || node.type === type) && (!text || pretty(node).toLocaleLowerCase().includes(text)));
    const size = Number($("page-size").value), pages = Math.max(1, Math.ceil(filtered.length / size)); tracePage = Math.min(tracePage, pages - 1);
    $("trace-list").replaceChildren();
    for (const node of filtered.slice(tracePage * size, (tracePage + 1) * size)) { const row = button("", () => showNode(currentCapture, node), "trace-row"); const main = el("div", undefined, "node-main"); main.append(el("div", node.name || node.id || "未命名节点", "node-label"), el("div", `${node.id || "无节点 ID"} · Trace ${node.trace_id || "未提供"} · ${node.start_time || "未提供时间"}`, "node-meta")); if (node.parent_id && !findNode(currentCapture, node.parent_id, node.trace_id)) main.append(el("div", `缺失父节点 ${node.parent_id}`, "node-meta")); row.append(el("div", node.type || "node", "node-branch"), main, el("span", "查看 →", "muted")); $("trace-list").append(row); }
    if (!filtered.length) $("trace-list").append(el("p", all.length ? "没有符合筛选条件的节点。" : "此快照没有可浏览节点；请查看采集状态与缺口。", "empty"));
    $("trace-count").textContent = `${filtered.length} / ${all.length} 个节点`; $("trace-page").textContent = `${tracePage + 1} / ${pages}`; $("trace-prev").disabled = !tracePage; $("trace-next").disabled = tracePage + 1 >= pages;
  };
  const chooseCapture = () => { currentCapture = data.captures.find(item => item.id === $("capture-filter").value) || null; tracePage = 0; const nodes = currentCapture ? currentCapture.nodes : []; options($("trace-filter"), [...new Set(nodes.map(node => node.trace_id).filter(Boolean))], "全部 Trace"); options($("type-filter"), [...new Set(nodes.map(node => node.type).filter(Boolean))], "全部类型"); $("capture-status").replaceChildren(); if (currentCapture) { const manifest = currentCapture.manifest; $("capture-status").append(el("p", currentCapture.error || `采集状态：${manifest.status || "未提供"}。完整性仅针对声明范围；检测覆盖请查看会话概览。`, currentCapture.error ? "warning" : "muted")); if (!data.session.detections.some(record => Array.isArray(record.captures) && record.captures.includes(currentCapture.id))) $("capture-status").append(el("p", "此快照尚未绑定检测覆盖记录；其他快照的问题不表示此快照已完成分析。", "warning")); } renderTraces(); };
  options($("capture-filter"), data.captures.map(capture => capture.id)); if (currentCapture) $("capture-filter").value = currentCapture.id; $("capture-filter").onchange = chooseCapture;
  for (const id of ["trace-search", "trace-filter", "type-filter", "page-size"]) $(id).addEventListener(id === "trace-search" ? "input" : "change", () => { tracePage = 0; renderTraces(); });
  $("trace-prev").onclick = () => { tracePage--; renderTraces(); }; $("trace-next").onclick = () => { tracePage++; renderTraces(); };
  const renderIssues = () => {
    const text = $("issue-search").value.toLocaleLowerCase(), status = $("issue-status").value;
    const filtered = data.issues.filter(item => (!status || item.record.status === status) && (!text || pretty(item.record).toLocaleLowerCase().includes(text)));
    const pages = Math.max(1, Math.ceil(filtered.length / 20)); issuePage = Math.min(issuePage, pages - 1); $("issue-list").replaceChildren();
    for (const item of filtered.slice(issuePage * 20, (issuePage + 1) * 20)) { const issue = item.record; const card = button("", () => showIssue(item), "issue-card"); const heading = el("div", issue.id, "issue-id"); heading.append(el("span", issue.status || "未提供状态", "badge")); card.append(heading, el("h3", issue.title || "问题"), el("p", String(issue.description || "").slice(0, 260))); $("issue-list").append(card); }
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
  chooseCapture(); renderIssues();
})();
