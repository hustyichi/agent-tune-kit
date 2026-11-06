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
  let currentCapture = data.captures.at(-1) || null;
  let issuePage = 0, returnFocus = null, activeIssue = null, issueStage = "issue";
  let currentView = "traces", selectedNode = null, origin = null, issueTrigger = null;
  let investigation = null, scopeBefore = null;
  const captureById = new Map(data.captures.map(c => [c.id, c]));
  const issueById = new Map(data.issues.map(item => [item.record.id, item]));
  const filters = ["trace-filter", "type-filter", "trace-search", "record-mode"];
  const list = $("trace-list"), scroller = $("trace-scroll");
  const scope = () => $("trace-filter").value === "" ? null : Number($("trace-filter").value);
  const timeline = () => currentCapture?.timeline || {tracks:[], order:[]};
  const elapsed = (ms, precise = false) => {
    if (ms == null) return "时间未知";
    ms = Math.round(ms);
    const seconds = precise ? Math.floor(ms / 1000) : Math.round(ms / 1000);
    const time = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    return precise ? `${time}.${String(Math.round(ms) % 1000).padStart(3, "0")}` : time;
  };
  const trackLabel = i => timeline().tracks[i]?.trace_id != null ? `T${i+1}` : "文件";
  const startTime = () => timeline().tracks.find(t => t.start !== null)?.start ?? 0;
  const partNow = () => investigation?.item.fragments[investigation.index];
  const indexedRef = ref => {
    const saved = data.references[refKey(ref)];
    const capture = captureById.get(saved?.location?.capture_id);
    return {saved, capture, node:capture?.nodes[saved.location.node_index]};
  };
  const swim = window.createSwimlanes({
    el, button, elapsed, onPick: pickFromLane, onZoom: focusTrace, onFragment: goFragment,
    onRoot: index => inspectNode(currentCapture.nodes[index], document.activeElement)
  });
  function rememberView() {
    return {view:currentView, capture:currentCapture?.id, filters:filters.map(id => $(id).value),
      selected:selectedNode, top:scroller.scrollTop, lanes:swim.snapshot(), scroll:window.scrollY,
      picker:$("issue-picker").open, focus:document.activeElement};
  }
  function restoreView(state) {
    setView(state.view);
    $("capture-filter").value = state.capture || "";
    chooseCapture();
    filters.forEach((id, i) => { $(id).value = state.filters[i]; });
    selectedNode = state.selected;
    renderTraces(state.top);
    swim.restore(state.lanes);
    $("issue-picker").open = state.picker;
    window.scrollTo(0, state.scroll);
    if (state.focus?.isConnected) state.focus.focus({preventScroll:true});
    else {
      const track = state.focus?.closest(".trace-overview")?.dataset.track;
      const selector = state.focus?.dataset.nodeIndex !== undefined ? `[data-node-index="${state.focus.dataset.nodeIndex}"]` :
        state.focus?.dataset.issueId ? `[data-issue-id="${CSS.escape(state.focus.dataset.issueId)}"]` :
        track != null ? `.trace-overview[data-track="${track}"] .${state.focus.classList.contains("lane-zoom") ? "lane-zoom" : "lane-toggle"}` :
        state.picker ? "#issue-picker summary" : null;
      if (selector) $(state.view === "issues" ? "view-issues" : "view-traces").querySelector(selector)?.focus({preventScroll:true});
    }
  }
  const openDrawer = (title, nodeMode = false, inspecting = false) => {
    if ($("drawer").hidden) returnFocus = document.activeElement;
    $("drawer-title").textContent = title;
    $("drawer-content").replaceChildren();
    $("drawer").classList.toggle("node-drawer", nodeMode);
    $("drawer").classList.toggle("inspecting", inspecting);
    if (!inspecting) $("drawer").classList.remove("above-record");
    $("drawer").setAttribute("aria-modal", String(!inspecting));
    $("drawer").hidden = false;
    $("drawer-backdrop").hidden = inspecting;
    document.body.style.overflow = inspecting ? "" : "hidden";
    $("drawer-content").scrollTop = 0;
    $("drawer-close").focus({preventScroll: true});
  };
  const closeDrawer = (restoreFocus = true) => {
    $("drawer").hidden = $("drawer-backdrop").hidden = true;
    document.body.style.overflow = "";
    activeIssue = null; origin = null;
    if (restoreFocus && returnFocus?.isConnected) returnFocus.focus({preventScroll:true});
    else if (restoreFocus && investigation) $("fragment-fields").focus({preventScroll:true});
    else if (restoreFocus && issueTrigger) document.querySelector(`[data-issue-id="${CSS.escape(issueTrigger)}"]`)?.focus({preventScroll:true});
    issueTrigger = null;
  };
  const backToIssue = () => {
    if (!activeIssue) return;
    const back = button("← 返回问题与证据", () => {
      const stored = origin;
      const item = activeIssue, stage = issueStage;
      if (stored) { investigation = stored.investigation; restoreView(stored.state); }
      origin = null;
      if (stage === "fragment" && investigation) showFragmentEvidence(); else showIssue(item, stage);
      if (stored) [...$("drawer-content").querySelectorAll(".reference")].find(b => b.dataset.key === stored.key)?.focus();
    });
    back.id = "return-to-issue";
    $("drawer-content").append(back);
  };
  const fieldPreview = (ref, saved) => {
    const box = section("命中证据字段");
    box.id = "evidence-field";
    box.dataset.pointer = saved.preview.resolved_pointer || ref.pointer || "";
    const label = box.dataset.pointer || (ref.line ? `第 ${ref.line} 行` : "整个文件");
    box.append(el("code", label), pre(saved.preview.text));
    if (saved.preview.truncated) box.append(el("p", `仅展示前 ${saved.preview.text.length} / ${saved.preview.characters} 个字符。完整字段可从本地归档读取。`, "muted"));
    return box;
  };
  const openReference = ref => {
    const {node, capture, saved} = indexedRef(ref);
    if (activeIssue && !origin) origin = {state:rememberView(), key:refKey(ref), investigation:investigation && {...investigation}};
    if (investigation && node) {
      const item = investigation.item;
      const pos = item.record.evidence.findIndex(evidence => refKey(evidence) === refKey(ref));
      const index = item.fragments.findIndex(part => part.refs.includes(pos) || part.context_refs?.includes(pos));
      if (index >= 0 && index !== investigation.index) {
        investigation.index = index;
        if (currentCapture === capture) renderTraces(scroller.scrollTop);
      } else if (index < 0 && capture !== currentCapture) investigation = null;
    }
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
  function pickFromLane(index) {
    const node = currentCapture.nodes[index];
    if (!streamRows.includes(index) && !timeline().tracks[node.track].roots.includes(index)) {
      clearRecordFilters(); renderTraces();
    }
    selectAndScroll(index);
  }
  function navigateNode(capture, node, ref = null) {
    const index = capture.nodes.indexOf(node);
    if (index < 0) return;
    setView("traces");
    if (currentCapture !== capture) {
      $("capture-filter").value = capture.id; chooseCapture(); renderTraces();
    }
    const hasRow = streamRows.includes(index) || (node.type === "TRACE" && streamRows.some(i => capture.nodes[i].track === node.track));
    if ((scope() !== null && scope() !== node.track) || !hasRow) {
      $("trace-filter").value = ""; clearRecordFilters(); renderTraces();
    }
    selectAndScroll(index);
    showNodeDetails(capture, node, index, ref);
    requestAnimationFrame(() => {
      const mark = swim.scrollSelected();
      if (!mark) return;
      const ceiling = document.querySelector(".topbar").getBoundingClientRect().bottom + 4;
      const bottom = innerWidth <= 800 ? $("drawer").getBoundingClientRect().top - 5 : innerHeight;
      if (mark.top < ceiling) window.scrollBy(0, mark.top-ceiling);
      else if (mark.bottom > bottom) window.scrollBy(0, mark.bottom-bottom);
    });
  }
  function inspectNode(node, trigger) {
    const index = currentCapture.nodes.indexOf(node);
    const newInspector = $("drawer").hidden || !$("drawer").classList.contains("inspecting");
    selectedNode = node; activeIssue = null; origin = null;
    swim.select(index);
    markVisibleRows();
    showNodeDetails(currentCapture, node, index, null, true);
    // Dock away from the tap on phones, then keep that edge while browsing adjacent rows.
    if (newInspector && innerWidth <= 800) {
      const sheet = $("drawer"), bottom = trigger.getBoundingClientRect().bottom;
      sheet.classList.toggle("above-record", bottom + rowHeight > innerHeight - sheet.offsetHeight);
    }
    returnFocus = trigger;
  }
  const showNodeDetails = (capture, node, targetIndex, ref, inspecting = false) => {
    openDrawer(node.name || node.id || "轨迹节点", true, inspecting);
    if (inspecting) $("drawer-content").append(button("定位到泳道", () => navigateNode(capture, node)));
    backToIssue();
    const identification = el("p", `Trace ${node.trace_id || "无归属"} · ${node.type}`, "mono muted");
    identification.dataset.nodeIndex = String(targetIndex);
    identification.dataset.traceId = node.trace_id || "";
    identification.dataset.nodeId = node.id || "";
    $("drawer-content").append(identification);
    if (ref) $("drawer-content").append(fieldPreview(ref, data.references[refKey(ref)]));
    if (node.issue_ids?.length) {
      const cited = section("引用此节点的问题");
      for (const id of node.issue_ids) { const item = issueById.get(id); if (item) cited.append(button(id, () => startIssue(item))); }
      $("drawer-content").append(cited);
    }
    const meta = {id: node.id, trace_id: node.trace_id, type: node.type, start_time: node.start_time, end_time: node.end_time, reference: node.reference};
    if (node.level) meta.level = node.level;
    const metadata = el("details", undefined, "detail-section");
    metadata.append(el("summary", "节点与原始引用"), pre(meta)); $("drawer-content").append(metadata);
    const relations = section("调用关联");
    if (node.paired != null) {
      const partner = capture.nodes[node.paired];
      relations.append(button(`配对动作 · ${partner.name || partner.type}`, () => navigateNode(capture, partner)));
    }
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
      detail.open = true;
      detail.append(el("summary", key === "input" ? "节点输入" : "节点输出"), pre(node[key] ?? "原始记录未提供值"));
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
  for (const capture of data.captures) { const card = overviewCard(capture.current ? "会话轨迹" : `采集 ${capture.id}`, undefined, true); if (capture.error) card.append(el("p", capture.error, "warning")); const detail = el("details"); detail.append(el("summary", "查看获取范围、字段、完整性和摘要"), pre(capture.manifest)); card.append(detail, button("浏览轨迹", () => { $("capture-filter").value = capture.id; investigation = null; scopeBefore = null; chooseCapture(); setView("traces"); renderTraces(); })); }
  const digests = overviewCard("生成时采用的处理记录摘要", undefined, true); const digestDetails = el("details"); digestDetails.append(el("summary", "展开记录与 SHA-256"), pre(data.record_digests)); digests.append(digestDetails); $("view-overview").append(overview);

  const rowHeight = parseInt(getComputedStyle(list).getPropertyValue("--row-height"), 10);
  const searchable = new WeakMap();
  let streamRows = [], windowStart = -1, windowEnd = -1, displayedScope = null;
  const selectedIndex = () => selectedNode ? currentCapture.nodes.indexOf(selectedNode) : -1;

  function clearRecordFilters() {
    for (const id of ["type-filter","trace-search","record-mode"]) $(id).value = "";
  }
  function filteredRows() {
    const query = $("trace-search").value.trim().toLocaleLowerCase();
    const type = $("type-filter").value, keyOnly = $("record-mode").value === "key", track = scope();
    const {order, tracks} = timeline();
    let candidates = order;
    // Trace envelopes live in the row headers, but their saved fields are searchable too.
    if (query || type === "TRACE") {
      const roots = tracks.flatMap(t => t.rows.length ? t.roots : []);
      candidates = order.concat(roots).sort((a,b) => {
        const left = currentCapture.nodes[a], right = currentCapture.nodes[b];
        return (left.at ?? Infinity) - (right.at ?? Infinity) || left.track - right.track || a - b;
      });
    }
    return candidates.filter(i => {
      const n = currentCapture.nodes[i];
      if ((track !== null && n.track !== track) || (type && n.type !== type)) return false;
      if (query) {
        if (!searchable.has(n)) searchable.set(n, pretty(n).toLocaleLowerCase());
        return searchable.get(n).includes(query);
      }
      return !keyOnly || n.level === "ERROR" || !["heartbeat", "healthcheck", "sandking_env.read"].includes(n.name);
    });
  }
  function summaryOf(n) {
    const out = n.output;
    if (n.level === "ERROR") return "运行时错误";
    if (typeof out === "string") return out.slice(0,180).replace(/\s+/g," ").trim();
    if (out && typeof out === "object") {
      for (const k of ["result_summary","outcome","status","finishReason","message","text","stdout"]) {
        if (typeof out[k] === "string" && out[k]) return out[k].slice(0,180).replace(/\s+/g," ").trim();
      }
    }
    if (typeof out === "boolean" || typeof out === "number") return String(out);
    if (n.until > n.at) return `${((n.until-n.at)/1000).toLocaleString(undefined, {maximumFractionDigits:3})} s`;
    return "—";
  }
  function markVisibleRows() {
    const chosen = selectedIndex();
    for (const row of list.querySelectorAll(".trace-row")) row.setAttribute("aria-current", String(Number(row.dataset.nodeIndex) === chosen));
  }
  function paintWindow(force = false) {
    const count = streamRows.length;
    const first = Math.max(0, Math.min(count, Math.floor(scroller.scrollTop / rowHeight)) - 8);
    const last = Math.min(count, first + Math.ceil(scroller.clientHeight / rowHeight) + 17);
    if (!force && first === windowStart && last === windowEnd) return;
    windowStart = first; windowEnd = last;
    const focus = list.contains(document.activeElement) ? document.activeElement.dataset.nodeIndex : null;
    const content = document.createDocumentFragment();
    const part = partNow(), chosen = selectedIndex(), start = startTime();
    for (let pos = first; pos < last; pos++) {
      const i = streamRows[pos], n = currentCapture.nodes[i];
      const row = button("", () => inspectNode(n, row), "row-grid trace-row");
      row.dataset.nodeIndex = i; row.dataset.position = pos;
      row.setAttribute("aria-current", String(i === chosen));
      const time = el("span", elapsed(n.at === null ? null : n.at - start, true), "time-col");
      if (n.start_time) time.title = n.start_time;
      const traceCell = el("span", undefined, "trace-col");
      const tag = el("span", trackLabel(n.track), "trace-tag"); tag.title = n.trace_id || n.reference.path; traceCell.append(tag);
      const action = el("span", undefined, "node-main"), name = n.name || String(n.id || "未命名记录");
      const label = el("span", name, "node-label"); label.title = name;
      action.append(label, el("span", n.type, "node-branch"));
      if (part?.capture_id === currentCapture.id && part.track === n.track) {
        if (part.anchors.includes(i)) action.append(el("span", "证据", "evidence-note"));
        if (part.anchors.includes(i) || (part.start != null && n.at != null && n.at >= part.start && n.at <= part.end && (n.branch || null) === part.branch)) {
          row.classList.add("in-fragment");
        }
      }
      const result = el("span", summaryOf(n), "result-col"); result.title = result.textContent;
      row.append(time, traceCell, action, result); content.append(row);
    }
    if (!count) content.append(el("p", timeline().order.length ? "没有符合筛选条件的记录。" : "此会话没有可浏览的记录。", "empty"));
    list.style.paddingTop = `${first * rowHeight}px`;
    list.style.paddingBottom = `${(count - last) * rowHeight}px`;
    list.replaceChildren(content);
    if (focus != null) list.querySelector(`[data-node-index="${focus}"]`)?.focus({preventScroll:true});
  }
  function selectAndScroll(index) {
    const n = currentCapture.nodes[index];
    selectedNode = n;
    swim.select(index); swim.scrollSelected();
    let pos = streamRows.indexOf(index);
    if (pos < 0 && timeline().tracks[n.track].roots.includes(index)) pos = streamRows.findIndex(i => currentCapture.nodes[i].track === n.track);
    if (pos >= 0) scroller.scrollTop = Math.max(0, (pos - 2) * rowHeight);
    paintWindow(true);
  }
  scroller.addEventListener("scroll", () => paintWindow(), {passive:true});
  new ResizeObserver(() => paintWindow()).observe(scroller);
  scroller.addEventListener("keydown", event => {
    if (!["ArrowDown","ArrowUp","Home","End"].includes(event.key) || !streamRows.length) return;
    const current = Number(document.activeElement.dataset.position ?? Math.floor(scroller.scrollTop / rowHeight));
    const pos = event.key === "Home" ? 0 : event.key === "End" ? streamRows.length-1 :
      Math.max(0, Math.min(streamRows.length-1, current + (event.key === "ArrowUp" ? -1 : 1)));
    event.preventDefault();
    if (pos*rowHeight < scroller.scrollTop) scroller.scrollTop = pos*rowHeight;
    if ((pos+1)*rowHeight > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = (pos+1)*rowHeight - scroller.clientHeight;
    paintWindow();
    list.querySelector(`[data-position="${pos}"]`)?.focus({preventScroll:true});
  });
  function renderTraces(top = 0) {
    const {tracks, order} = timeline();
    displayedScope = scope();
    const track = tracks[displayedScope];
    const parts = (investigation?.item.fragments || []).map((p,i) => [i,p]).filter(([,p]) => p.capture_id === currentCapture?.id);
    swim.render(currentCapture, displayedScope, selectedIndex(), parts, investigation?.index);
    showFragmentBar();
    $("lane-title").textContent = track ? `只看 ${trackLabel(displayedScope)} · ${track.trace_id || "归档材料"}` : "完整会话";
    const coverage = currentCapture?.manifest.completeness || {};
    const native = tracks.filter(t => t.trace_id != null);
    const notes = [track ? `${track.rows.length || track.roots.length} 条记录` : `${native.length} 条 Trace`];
    if (!track) notes.push(native.length && native.length === tracks.length ?
      `${native.reduce((n, t) => n + t.rows.length, 0)} 个观测` : `${order.length} 条记录`);
    const ends = tracks.reduce((hi,t) => Math.max(hi,t.end ?? -Infinity), -Infinity);
    const duration = track ? track.end - track.start : ends - startTime();
    if (Number.isFinite(duration) && (!track || track.start !== null)) notes.push(`观测跨度 ${elapsed(duration)}`);
    if (coverage.status === "partial") notes.push("部分采集");
    $("summary").textContent = notes.join(" · ");
    scroller.parentElement.classList.toggle("single", tracks.length === 1 || displayedScope !== null);
    streamRows = filteredRows();
    $("trace-count").textContent = `${streamRows.length} 条`;
    // Give the scroll container its final extent before restoring a distant row.
    list.style.paddingTop = "0px";
    list.style.paddingBottom = `${streamRows.length * rowHeight}px`;
    scroller.scrollTop = top;
    paintWindow(true);
  }
  function focusTrace(number) {
    if (number === null && scopeBefore) {
      const before = scopeBefore; scopeBefore = null; restoreView(before); return;
    }
    if (displayedScope === null && number !== null) {
      scopeBefore = rememberView();
      scopeBefore.filters[0] = "";
    }
    $("trace-filter").value = number === null ? "" : String(number);
    selectedNode = null; renderTraces();
    if (number !== null) $("lane-reset").focus({preventScroll:true});
  }
  function chooseCapture() {
    currentCapture = captureById.get($("capture-filter").value) || null;
    selectedNode = null; clearRecordFilters();
    const {tracks,order} = timeline();
    const select = $("trace-filter");
    select.replaceChildren(new Option("全部 Trace",""));
    tracks.forEach((t,i) => select.add(new Option(`${trackLabel(i)} · ${t.trace_id || t.file.split("/").pop()}`, String(i))));
    $("trace-choice").hidden = tracks.length < 2;
    options($("type-filter"), [...new Set((currentCapture?.nodes || []).map(n => n.type).filter(Boolean))], "全部类型");
    $("capture-status").replaceChildren();
    if (currentCapture?.error) $("capture-status").append(el("p",currentCapture.error,"warning"));
    const linked = data.issues.filter(item => item.fragments.some(f => f.capture_id === currentCapture?.id));
    $("lane-issue-count").textContent = linked.length;
    const menu = $("lane-issues"); menu.replaceChildren();
    for (const item of linked) {
      const link = button("", () => startIssue(item), "lane-issue");
      link.dataset.issueId = item.record.id;
      link.append(el("strong", item.record.id, "issue-id"), el("span", item.record.title));
      menu.append(link);
    }
    if (!linked.length) {
      const checked = data.session.detections.some(d => currentCapture?.current ? d.inputs?.length : d.captures?.includes(currentCapture?.id));
      menu.append(el("p", checked ? "此会话没有已关联的异常。" : "此会话尚未检查。", "empty"));
    }
  }
  function showFragmentBar() {
    const part = partNow();
    $("fragment-bar").hidden = !part;
    if (!part) return;
    const {item,index} = investigation;
    $("fragment-issue").textContent = `${item.record.id} · ${item.record.title}`;
    $("fragment-issue").title = $("fragment-issue").textContent;
    const labels = {trace:"执行级证据", file:"归档引用", untimed:"时间未知"};
    const range = part.start == null ? labels[part.kind] : `${elapsed(part.start-startTime(),true)} – ${elapsed(part.end-startTime(),true)}`;
    $("fragment-position").textContent = [`片段 ${index+1} / ${item.fragments.length}`, part.track == null ? null : trackLabel(part.track), range].filter(Boolean).join(" · ");
    $("fragment-prev").disabled = index === 0;
    $("fragment-next").disabled = index+1 === item.fragments.length;
    $("fragment-fields").textContent = `片段证据 (${part.refs.length+(part.context_refs?.length || 0)})`;
  }
  function startIssue(item) {
    if (!item.fragments.length) { showIssue(item); return; }
    const before = investigation?.before || rememberView();
    const priorScope = investigation ? investigation.priorScope : scopeBefore;
    if (!$("drawer").hidden) closeDrawer(false);
    $("issue-picker").open = false;
    investigation = {item, before, priorScope, index:0};
    const local = item.fragments.findIndex(f => f.capture_id === currentCapture?.id && f.anchors.length);
    goFragment(local < 0 ? 0 : local);
    if ($("drawer").hidden) $("fragment-fields").focus({preventScroll:true});
  }
  function goFragment(index) {
    const part = investigation?.item.fragments[index];
    if (!part) return;
    if (!$("drawer").hidden) closeDrawer(false);
    investigation.index = index;
    setView("traces");
    if (part.capture_id && currentCapture?.id !== part.capture_id) {
      $("capture-filter").value = part.capture_id; chooseCapture();
    }
    $("trace-filter").value = ""; clearRecordFilters(); selectedNode = null;
    scopeBefore = null; renderTraces();
    if (part.anchors.length) {
      selectAndScroll(part.anchors[0]); $("fragment-bar").scrollIntoView({block:"nearest"});
    } else {
      activeIssue = investigation.item; issueStage = "fragment";
      openReference(investigation.item.record.evidence[part.refs[0]]);
    }
  }
  function showFragmentEvidence() {
    const {item,index} = investigation;
    const part = item.fragments[index];
    activeIssue = item; issueStage = "fragment"; issueTrigger = item.record.id;
    openDrawer(`${item.record.id} · 片段 ${index+1} 的证据`);
    function add(title, positions) {
      if (!positions?.length) return;
      const box = section(title);
      appendReferences(box, positions.map(i => item.record.evidence[i]));
      $("drawer-content").append(box);
    }
    add(part.kind === "trace" ? "执行级证据" : "本段证据", part.refs);
    add("此 Trace 的上下文", part.context_refs);
  }
  function exitIssue() {
    if (!investigation) return;
    const {before,priorScope} = investigation;
    investigation = null; scopeBefore = priorScope;
    if (!$("drawer").hidden) closeDrawer(false);
    restoreView(before);
  }
  $("fragment-prev").onclick = () => goFragment(investigation.index-1);
  $("fragment-next").onclick = () => goFragment(investigation.index+1);
  $("fragment-fields").onclick = showFragmentEvidence;
  $("fragment-detail").onclick = () => showIssue(investigation.item);
  $("fragment-exit").onclick = exitIssue;
  $("lane-reset").onclick = () => focusTrace(null);
  $("capture-filter").closest("label").hidden = Boolean(currentCapture?.current);
  options($("capture-filter"), data.captures.map(c => c.id));
  if (currentCapture) $("capture-filter").value = currentCapture.id;
  $("capture-filter").onchange = () => { investigation = null; scopeBefore = null; chooseCapture(); renderTraces(); };
  $("trace-filter").onchange = () => focusTrace(scope());
  for (const id of ["trace-search","type-filter","record-mode"]) $(id).addEventListener(id === "trace-search" ? "input" : "change", () => renderTraces());
  const renderIssues = () => {
    const text = $("issue-search").value.toLocaleLowerCase(), status = $("issue-status").value;
    const filtered = data.issues.filter(item => (!status || item.record.status === status) && (!text || pretty(item.record).toLocaleLowerCase().includes(text)));
    const pages = Math.max(1, Math.ceil(filtered.length / 20)); issuePage = Math.min(issuePage, pages - 1); $("issue-list").replaceChildren();
    for (const item of filtered.slice(issuePage * 20, (issuePage + 1) * 20)) { const issue = item.record; const card = button("", () => startIssue(item), "issue-card"); card.dataset.issueId = issue.id; const heading = el("div", issue.id, "issue-id"); heading.append(el("span", issue.status || "未提供状态", "badge")); card.append(heading, el("h3", issue.title || "问题"), el("p", String(issue.description || "").slice(0, 260))); $("issue-list").append(card); }
    if (!filtered.length) $("issue-list").append(el("p", data.issues.length ? "没有符合筛选条件的问题。" : data.session.detections.length ? "已保存检测记录，尚无已记录问题；请核对实际检查范围和未读材料。" : "尚未进行异常识别。", "empty"));
    $("issue-count").textContent = `${filtered.length} / ${data.issues.length} 个已记录问题`; $("issue-page").textContent = `${issuePage + 1} / ${pages}`; $("issue-prev").disabled = !issuePage; $("issue-next").disabled = issuePage + 1 >= pages;
  };
  options($("issue-status"), [...new Set(data.issues.map(item => item.record.status).filter(Boolean))], "全部状态"); $("issue-search").oninput = () => { issuePage = 0; renderIssues(); }; $("issue-status").onchange = () => { issuePage = 0; renderIssues(); }; $("issue-prev").onclick = () => { issuePage--; renderIssues(); }; $("issue-next").onclick = () => { issuePage++; renderIssues(); };
  document.addEventListener("click", event => {
    if ($("issue-picker").open && !event.target.closest("#issue-picker")) $("issue-picker").open = false;
    if (!$('drawer').hidden && $('drawer').classList.contains("inspecting") &&
        !event.target.closest("#drawer,.trace-row,#lane-chart,#lane-members")) closeDrawer(false);
  }, true);
  $("drawer-close").onclick = closeDrawer; $("drawer-backdrop").onclick = closeDrawer;
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !$("drawer").hidden) { event.preventDefault(); closeDrawer(); }
    else if (event.key === "Escape" && $("issue-picker").open) { $("issue-picker").open = false; $("issue-picker").querySelector("summary").focus(); }
    else if (event.key === "Escape" && investigation) { event.preventDefault(); exitIssue(); }
    if (event.key === "Tab" && !$("drawer").hidden && $("drawer").getAttribute("aria-modal") === "true") { const focusable = [...$("drawer").querySelectorAll("button,input,select,summary,[tabindex='0']")].filter(node => !node.disabled && node.getClientRects().length); const first = focusable[0], last = focusable[focusable.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }
    if (["ArrowLeft", "ArrowRight"].includes(event.key) && document.activeElement.matches("[data-view]")) { event.preventDefault(); const tabs = [...document.querySelectorAll("[data-view]")], index = tabs.indexOf(document.activeElement); const tab = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length]; tab.click(); tab.focus(); }
  });
  setView("traces"); chooseCapture(); renderTraces(); renderIssues();
})();
