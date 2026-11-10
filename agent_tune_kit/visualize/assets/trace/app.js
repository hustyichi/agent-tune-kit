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
  const markdown = window.markdownit({html:false});
  markdown.renderer.rules.image = (tokens, index) => markdown.utils.escapeHtml(tokens[index].content);
  const sourceLinks = new Map(Object.entries(data.source_links).map(([href, target]) => [markdown.normalizeLink(href), target]));
  const options = (select, values, all) => { select.replaceChildren(); if (all !== undefined) { const item = el("option", all); item.value = ""; select.append(item); } for (const value of values) { const item = el("option", value); item.value = value; select.append(item); } };
  const references = value => { const result = []; const seen = new Set(); const walk = item => { if (Array.isArray(item)) item.forEach(walk); else if (item && typeof item === "object") { if (typeof item.path === "string" && typeof item.sha256 === "string") { const key = refKey(item); if (!seen.has(key)) { seen.add(key); result.push(item); } } else Object.values(item).forEach(walk); } }; walk(value); return result; };
  const RAIL_KEY = "atk-trace-rail-collapsed";
  let currentCapture = data.captures.at(-1) || null;
  let returnFocus = null, activeIssue = null, issueStage = "issue", drawerMode = null;
  let selectedNode = null, origin = null, issueTrigger = null;
  let returnToDiagnosis = null;
  let investigation = null, scopeBefore = null, previewIssue = null, expandedFolds = new Set(), railFilter = null;
  const captureById = new Map(data.captures.map(c => [c.id, c]));
  const issueById = new Map(data.issues.map(item => [item.record.id, item]));

  const projectSelection = new Set(new URLSearchParams(location.hash.slice(1)).getAll("project"));
  const projectMatches = item => !projectSelection.size || [...projectSelection].some(id => {
    const roots = item.root_projects || [];
    if (id === "~unattributed") return !roots.length;
    return roots.some(root => { let current = root.id; const seen = new Set(); while (current && !seen.has(current)) { if (current === id) return true; seen.add(current); current = data.projects?.[current]?.parent_id; } return false; });
  });
  const projectInputs = [];
  for (const p of [{id: "~unattributed", name: "待归因"}, ...Object.values(data.projects || {})]) {
    const label = el("label"), input = el("input");
    input.type = "checkbox"; input.value = p.id;
    input.checked = projectSelection.has(p.id);
    label.append(input, document.createTextNode(p.abbreviation || p.name));
    $("project-filter").append(label); projectInputs.push(input);
    input.onchange = () => $("project-filter").onchange();
  }
  const updateProjectSummary = () => {
    $("project-summary").textContent = projectSelection.size ? `关联项目：已选 ${projectSelection.size} 项` : "关联项目：全部";
  };
  updateProjectSummary();
  $("project-clear").onclick = () => { projectInputs.forEach(input => { input.checked = false; }); $("project-filter").onchange(); };
  $("project-choice").onkeydown = event => { if (event.key === "Escape") { $("project-choice").open = false; $("project-summary").focus(); } };
  document.addEventListener("click", event => {
    if (!event.target.closest("#project-choice")) $("project-choice").open = false;
  });

  const filters = ["trace-filter", "type-filter", "trace-search", "record-mode"];
  const list = $("trace-list"), scroller = $("trace-scroll");
  const scope = () => $("trace-filter").value === "" ? null : Number($("trace-filter").value);
  const timeline = (capture = currentCapture) => capture?.timeline || {tracks:[], order:[]};
  const elapsed = (ms, precise = false) => {
    if (ms == null) return "时间未知";
    ms = Math.round(ms);
    const seconds = precise ? Math.floor(ms / 1000) : Math.round(ms / 1000);
    const time = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    return precise ? `${time}.${String(Math.round(ms) % 1000).padStart(3, "0")}` : time;
  };
  const trackLabel = (i, capture) => timeline(capture).tracks[i]?.trace_id != null ? `T${i+1}` : "文件";
  const startTime = capture => timeline(capture).tracks.find(t => t.start !== null)?.start ?? 0;
  const partNow = () => investigation?.item.fragments[investigation.index];
  const issueEvidence = item => item.evidence || [];
  const evidenceRefAt = (item, pos) => issueEvidence(item)[pos]?.reference;
  const indexedRef = ref => {
    const saved = data.references[refKey(ref)];
    const capture = captureById.get(saved?.location?.capture_id);
    return {saved, capture, node:capture?.nodes[saved.location.node_index]};
  };
  const progressOf = item => ({label: item.progress.label, klass: item.progress.key});
  const dashboardHash = params => `../index.html#${params}`;
  const projectLabels = item => {
    const row = el("p", "关联项目：", "issue-projects");
    const projects = item.root_projects || [];
    if (!projects.length) row.append(document.createTextNode("待归因"));
    projects.forEach((project, index) => {
      if (index) row.append(document.createTextNode("、"));
      const name = project.name + (project.pending ? "（待确认）" : "");
      const label = el(data.dashboard.available ? "a" : "span", name);
      if (data.dashboard.available) {
        label.href = dashboardHash(`view=category&project=${encodeURIComponent(project.id)}`);
        label.title = "在大盘中查看此项目的问题";
      }
      row.append(label);
    });
    return row;
  };
  const categoryTag = id => {
    if (!id) return null;
    const info = data.categories[id];
    const text = `${info ? info.name : id}${info?.deprecated ? "（已弃用）" : ""}`;
    if (!data.dashboard.available) return el("span", text, "category-tag");
    const link = el("a", text, "category-tag");
    link.href = dashboardHash(`view=category&category=${encodeURIComponent(id)}`);
    link.title = "在大盘中查看这类异常";
    return link;
  };
  const shortIssue = id => String(id || "").replace(/^ISSUE-/, "") || id;
  const issueCounts = () => {
    const counts = new Map();
    for (const item of data.issues) {
      const tracks = new Set();
      for (const part of item.fragments) if (part.capture_id === currentCapture?.id && part.track != null) tracks.add(part.track);
      for (const track of tracks) counts.set(track, (counts.get(track) || 0) + 1);
    }
    return counts;
  };
  const swim = window.createSwimlanes({
    el, button, elapsed, startTime, onPick: pickFromLane, onZoom: focusTrace, onFragment: goFragment,
    onRoot: index => openEventDrawer(currentCapture.nodes[index], document.activeElement),
    onIssueBadge: track => {
      $("issue-search").value = "";
      $("issue-status").value = "";
      railFilter = {track, ids: data.issues.filter(item => item.fragments.some(part => part.capture_id === currentCapture?.id && part.track === track)).map(item => item.record.id)};
      renderIssueRail();
    },
  });
  function rememberView() {
    return {
      capture: currentCapture?.id,
      filters: filters.map(id => $(id).value),
      segmentsOnly: $("segments-only").checked,
      selected: selectedNode,
      top: scroller.scrollTop,
      lanes: swim.snapshot(),
      scroll: window.scrollY,
      focus: document.activeElement,
    };
  }
  function restoreView(state) {
    $("capture-filter").value = state.capture || "";
    chooseCapture();
    filters.forEach((id, i) => { $(id).value = state.filters[i]; });
    $("segments-only").checked = Boolean(state.segmentsOnly);
    selectedNode = state.selected;
    renderTraces(state.top);
    swim.restore(state.lanes);
    window.scrollTo(0, state.scroll);
    if (state.focus?.isConnected) state.focus.focus({preventScroll:true});
  }
  const setDrawerWidth = width => {
    const clamped = Math.max(320, Math.min(window.innerWidth * 0.92, width));
    document.documentElement.style.setProperty("--drawer-width", `${clamped}px`);
  };
  const openDrawer = (title, mode = "detail") => {
    const drawer = $("drawer");
    if (drawer.hidden) returnFocus = document.activeElement;
    drawerMode = mode;
    $("drawer-title").textContent = title;
    $("drawer-content").replaceChildren();
    drawer.classList.remove("above-record");
    drawer.hidden = false;
    $("drawer-content").scrollTop = 0;
    $("drawer-close").focus({preventScroll: true});
  };
  const closeDrawer = (restoreFocus = true) => {
    $("drawer").hidden = true;
    drawerMode = null;
    activeIssue = null; origin = null;
    returnToDiagnosis = null;
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
      const pos = issueEvidence(item).findIndex(saved => refKey(saved.reference) === refKey(ref));
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
  const textPresent = value => typeof value === "string" && value.trim();
  const textItems = value => (Array.isArray(value) ? value : [value]).filter(textPresent);
  const recordTime = record => {
    const date = new Date(record?.created_at);
    return Number.isNaN(date.getTime()) ? "时间未记录" : date.toLocaleString("zh-CN", {hour12:false});
  };
  const stateLabel = status => ({running:"进行中", completed:"已完成", failed:"失败", blocked:"受阻", interrupted:"已中断"})[status] || "状态未记录";
  const proseSection = (title, value, missing) => {
    const box = section(title), items = textItems(value);
    if (items.length === 1) box.append(el("p", items[0]));
    else if (items.length) {
      const list = el("ul");
      for (const item of items) list.append(el("li", item));
      box.append(list);
    } else box.append(el("p", missing, "muted"));
    return box;
  };
  const changeCard = change => {
    const record = change.record || {};
    const card = section(`修改 · ${recordTime(record)}`); card.classList.add("stage-record");
    card.append(el("p", `修改${stateLabel(record.status)} · 效果另见实际验证`, "record-meta"));
    if (change.error) card.append(el("p", "修改记录不可读取，请检查本地归档。", "warning"));
    const entries = Array.isArray(record.change_summary) ? record.change_summary : [];
    if (!entries.length) card.append(el("p", "未记录实际修改及原因的摘要。原方案不能作为已完成修改的依据；请在本地核对归档。", "muted"));
    for (const entry of entries) {
      const item = el("div", undefined, "change-item");
      item.append(proseSection("修改了什么", entry?.what, "未记录实际修改说明。"));
      item.append(proseSection("为什么修改", entry?.why, "未记录与根因对应的修改理由。"));
      for (const path of textItems(entry?.files)) {
        const target = sourceLinks.get(markdown.normalizeLink(path));
        let label = path;
        try { label = decodeURI(path); } catch { /* Keep malformed escapes as text. */ }
        const row = el("p", undefined, "source-file");
        if (target && location.protocol === "file:") {
          const link = el("a", label); link.href = target; link.target = "_blank"; link.rel = "noopener noreferrer";
          row.append(link);
        } else row.append(el("span", `${label}（${target ? "请从本地预览打开" : "文件不存在或不在可链接范围"}）`, "muted"));
        item.append(row);
      }
      card.append(item);
    }
    card.append(proseSection("待验证内容", record.remaining_verification, "未记录待验证行为与成功条件。"));
    return card;
  };
  const verificationCard = (verification, issueId) => {
    const record = verification.record || {}, summary = record.verification_summary || {};
    const card = section(`验证 · ${recordTime(record)}`); card.classList.add("stage-record");
    card.append(el("p", `执行${stateLabel(record.status)}`, "record-meta"));
    if (verification.error) card.append(el("p", "验证记录不可读取，请检查本地归档。", "warning"));
    const conclusion = record.conclusions?.[issueId];
    const outcome = el("div", undefined, "outcome");
    const label = {pass:"通过", fail:"未通过", inconclusive:"证据不足，尚不能确认修复效果"}[conclusion?.outcome];
    outcome.append(el("strong", `效果结论：${label || "尚无结论"}`));
    if (textPresent(conclusion?.reason)) outcome.append(el("p", conclusion.reason));
    card.append(outcome);
    card.append(proseSection("如何验证", summary.method || record.execution?.method, "未记录简短的验证方法与执行范围。"));
    card.append(proseSection("样本口径", summary.sample_scope, "未记录新增与累计样本的关系；请勿直接合计各次结果。"));
    card.append(proseSection("观察结果", summary.observations, "未记录观察结果摘要，请在本地核对执行材料。"));
    card.append(proseSection("局限与未覆盖范围", record.limitations,
      Array.isArray(record.limitations) ? "未声明额外局限；结论仅适用于上述验证范围。" : "未记录验证局限，不代表已完整验证。"));
    return card;
  };
  function appendVerifications(item) {
    const container = $("drawer-content");
    let count = 0;
    for (const change of item.changes) {
      if (!change.verifications?.length) continue;
      const group = section(`对应修改 · ${recordTime(change.record)}`);
      for (const verification of change.verifications) {
        group.append(verificationCard(verification, item.record.id)); count++;
      }
      container.append(group);
    }
    if (!count) container.append(el("p", "尚未记录实际验证；不能据修改完成判断问题已经解决。", "empty"));
  }
  function appendDiagnoses(item) {
    const container = $("drawer-content");
    if (!item.diagnoses.length) {
      container.append(el("p", "尚未记录诊断与修复／验证方案。", "empty"));
      return;
    }
    for (const [index, diagnosis] of item.diagnoses.slice().reverse().entries()) {
      const snapshot = diagnosis.snapshot;
      const body = diagnosis.error ? el("p", diagnosis.error, "warning") : renderDiagnosis(diagnosis.text);
      const headings = [...body.children].filter(node => node.tagName === "H2").map(node => node.textContent);
      const structured = headings.join("|") === "异常描述|异常根因|最小修复方案"
        || headings.join("|") === "Issue description|Root cause|Minimal fix";
      if (index === 0 && structured) {
        container.append(body);
        continue;
      }
      if (index === 0) {
        const description = section("异常描述");
        description.append(el("p", item.record.description || item.record.title));
        for (const [position, part] of item.fragments.entries()) {
          const cite = button(part.id || `片段 ${position + 1}`, () => openDiagnosisSegment(item, position, cite), "diagnosis-cite");
          description.append(cite);
        }
        container.append(description);
      }
      const details = el("details", undefined, "detail-section");
      if (snapshot?.project_links) {
        const entries = new Map((snapshot.project_registry?.projects || []).map(p => [p.id, p]));
        const names = snapshot.project_links.filter(p => p.role === "root_cause").map(p => { const entry = entries.get(p.project_id); return (entry?.abbreviation || entry?.name || p.project_id) + (p.evidence_state === "supported" ? "" : "（待确认）"); });
        body.prepend(el("p", `诊断时的关联项目：${names.join("、") || "待归因"}`));
      }
      const name = diagnosis.reference.path.split("/").pop();
      details.append(el("summary", `展开原诊断 · ${name}`), body);
      container.append(details);
    }
  }
  function renderDiagnosis(text) {
    const body = el("div", undefined, "diagnosis-markdown");
    const template = document.createElement("template");
    template.innerHTML = markdown.render(text);
    for (const link of template.content.querySelectorAll("a[href]")) {
      const href = link.getAttribute("href");
      const source = location.protocol === "file:" && sourceLinks.get(href);
      if (source || /^https?:\/\//i.test(href)) {
        if (source) link.setAttribute("href", source);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      } else {
        let path = href;
        try { path = decodeURI(href); } catch { /* Keep malformed escape sequences as text. */ }
        const locationText = el("code", path);
        locationText.hidden = true;
        const copy = button("复制路径", async () => {
          try { await navigator.clipboard.writeText(path); copy.textContent = "已复制"; }
          catch { locationText.hidden = false; }
        });
        link.replaceWith(...link.childNodes, " ", el("code", path.split("/").pop() || path), copy, locationText);
      }
    }
    for (const table of template.content.querySelectorAll("table")) {
      const scroll = el("div", undefined, "diagnosis-table");
      scroll.tabIndex = 0;
      scroll.setAttribute("aria-label", "诊断表格");
      table.before(scroll);
      scroll.append(table);
    }
    body.append(template.content);
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.data.includes("ISSUE-") && !node.parentElement.closest("pre, a")) nodes.push(node);
    }
    for (const node of nodes) {
      const parts = document.createDocumentFragment();
      let offset = 0;
      for (const match of node.data.matchAll(/\b(ISSUE-\d+)\/(S\d+)\b/g)) {
        const item = issueById.get(match[1]);
        const index = item?.fragments.findIndex(part => part.id === match[2]);
        if (index === undefined || index < 0) continue;
        const cite = button(match[0], () => openDiagnosisSegment(item, index, cite), "diagnosis-cite");
        parts.append(node.data.slice(offset, match.index), cite);
        offset = match.index + match[0].length;
      }
      if (offset) {
        parts.append(node.data.slice(offset));
        node.replaceWith(parts);
      }
    }
    return body;
  }
  function openDiagnosisSegment(item, index, trigger) {
    const drawerContent = $("drawer-content");
    const sourceIssue = activeIssue, view = rememberView(), priorScope = scopeBefore;
    const previous = investigation && {...investigation};
    const top = drawerContent.scrollTop;
    const citeIndex = [...drawerContent.querySelectorAll(".diagnosis-cite")].indexOf(trigger);
    returnToDiagnosis = () => {
      investigation = previous;
      scopeBefore = priorScope;
      restoreView(view);
      showIssue(sourceIssue, "diagnoses");
      const restored = drawerContent.querySelectorAll(".diagnosis-cite")[citeIndex];
      const details = restored?.closest("details");
      if (details) details.open = true;
      drawerContent.scrollTop = top;
      restored?.focus({preventScroll:true});
    };
    startIssue(item, index);
    origin = null;
    showFragmentEvidence();
  }
  const showIssue = (item, stage = "issue") => {
    returnToDiagnosis = null;
    if (!activeIssue || activeIssue.record.id !== item.record.id) {
      origin = null;
      issueTrigger = item.record.id;
    }
    activeIssue = item; issueStage = stage;
    const issue = item.record;
    openDrawer(`${issue.id} · ${issue.title || "问题详情"}`, "issue");
    const tabs = el("nav", undefined, "stage-tabs"); tabs.setAttribute("aria-label", "问题处理阶段");
    for (const [key, label] of [["issue", "问题与片段"], ["diagnoses", "诊断与方案"], ["changes", "修改候选"], ["verifications", "实际验证"]]) {
      const tab = button(label, () => showIssue(item, key), key === stage ? "active" : "");
      tab.setAttribute("aria-pressed", String(key === stage)); tabs.append(tab);
    }
    $("drawer-content").append(tabs);
    if (stage === "issue") {
      const description = section("问题描述");
      description.append(projectLabels(item), el("p", issue.description || issue.title));
      $("drawer-content").append(description);
      if (typeof issue.selection_reason === "string" && issue.selection_reason.trim()) {
        const reason = section("保留理由");
        reason.append(el("p", issue.selection_reason));
        $("drawer-content").append(reason);
      }
      const segments = issue.segments || [];
      if (segments.length) {
        const box = section("关联片段");
        segments.forEach((segment, index) => {
          const row = el("div", undefined, "detail-section");
          const title = `${segment.id || `S${index + 1}`} · ${segment.summary || "未命名片段"}`;
          row.append(button(title, () => startIssue(item, item.fragments.findIndex(part => part.id === segment.id || (!part.id && index === item.fragments.indexOf(part))))));
          appendReferences(row, segment.evidence || []);
          box.append(row);
        });
        $("drawer-content").append(box);
      }
      if ((issue.evidence || []).length) {
        const evidence = section(segments.length ? "额外证据" : "关联证据");
        appendReferences(evidence, issue.evidence);
        $("drawer-content").append(evidence);
      }
      if (!segments.length && !(issue.evidence || []).length) $("drawer-content").append(el("p", "尚未记录片段或证据。", "empty"));
      const details = el("details", undefined, "detail-section");
      details.append(el("summary", "完整问题记录"), pre(issue)); $("drawer-content").append(details);
      return;
    }
    if (stage === "diagnoses") {
      appendDiagnoses(item);
    } else if (stage === "changes") {
      if (!item.changes.length) $("drawer-content").append(el("p", "尚未记录实际修改。局部检查不代表 Agent 效果验证。", "empty"));
      for (const change of item.changes) $("drawer-content").append(changeCard(change));
    } else {
      appendVerifications(item);
    }
  };
  function pickFromLane(index) {
    const node = currentCapture.nodes[index];
    if (!streamRows.includes(index) && !timeline().tracks[node.track].roots.includes(index)) {
      clearRecordFilters(); renderTraces();
    }
    selectAndScroll(index);
    openEventDrawer(node, document.activeElement);
  }
  function navigateNode(capture, node, ref = null) {
    const index = capture.nodes.indexOf(node);
    if (index < 0) return;
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
  function openEventDrawer(node, trigger) {
    const drawer = $("drawer");
    const keepAbove = drawer.contains(trigger) && drawer.classList.contains("above-record");
    const index = currentCapture.nodes.indexOf(node);
    selectedNode = node;
    swim.select(index);
    markVisibleRows();
    showNodeDetails(currentCapture, node, index, null);
    const coversRow = innerWidth <= 800 && scroller.contains(trigger) &&
      trigger.getBoundingClientRect().bottom > drawer.getBoundingClientRect().top;
    if (keepAbove || coversRow) drawer.classList.add("above-record");
    returnFocus = trigger;
    syncHash();
  }
  const copyReference = ref => {
    const payload = JSON.stringify({path: ref.path, sha256: ref.sha256, pointer: ref.pointer || undefined, line: ref.line || undefined}, null, 2);
    const done = () => {};
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(payload).then(done, done);
  };
  const showNodeDetails = (capture, node, targetIndex, ref) => {
    openDrawer(node.name || node.id || "轨迹节点", "event");
    backToIssue();
    const crumbs = el("div", undefined, "breadcrumb");
    const siblings = node.trace_id ? capture.nodes.filter(item => item.trace_id === node.trace_id) : [];
    const byId = new Map();
    for (const item of siblings) if (item.type !== "TRACE" && item.id) {
      if (byId.has(item.id)) byId.set(item.id, null); else byId.set(item.id, item);
    }
    const chain = [];
    let cursor = node;
    const seen = new Set();
    while (cursor && !seen.has(cursor)) {
      seen.add(cursor);
      chain.unshift(cursor);
      cursor = cursor.parent_id ? byId.get(cursor.parent_id) : null;
    }
    for (const item of chain) {
      crumbs.append(button(item.name || item.id || item.type, () => navigateNode(capture, item)));
    }
    $("drawer-content").append(crumbs);
    const identification = el("p", `Trace ${node.trace_id || "无归属"} · ${node.type} · ${elapsed(node.at == null ? null : node.at - startTime(), true)}`, "mono muted");
    identification.dataset.nodeIndex = String(targetIndex);
    identification.dataset.traceId = node.trace_id || "";
    identification.dataset.nodeId = node.id || "";
    $("drawer-content").append(identification);
    if (node.reference) {
      $("drawer-content").append(button("复制引用", () => copyReference(node.reference), "copy-ref"));
    }
    if (ref) $("drawer-content").append(fieldPreview(ref, data.references[refKey(ref)]));
    if (node.issue_ids?.length) {
      const cited = section("引用此节点的问题");
      for (const id of node.issue_ids) {
        const item = issueById.get(id);
        if (!item) continue;
        const bits = [];
        for (const part of item.fragments) {
          if (part.capture_id !== capture.id || !part.anchors.includes(targetIndex)) continue;
          const fields = part.refs.map(pos => {
            const saved = issueEvidence(item)[pos];
            return saved?.location?.node_index === targetIndex ? (saved.location.field || saved.reference.pointer || "") : "";
          }).filter(Boolean);
          bits.push(`${part.id || "片段"}${fields.length ? " · " + fields.join(", ") : ""}`);
        }
        cited.append(button(`${id}${bits.length ? " · " + bits.join("；") : ""}`, () => startIssue(item)));
      }
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
    const wrapper = siblings.find(item => item.type === "TRACE");
    if (wrapper && wrapper !== node) relations.append(button("所属 Trace", () => navigateNode(capture, wrapper)));
    if (!node.trace_id && node.parent_id) relations.append(el("p", "缺少 Trace 归属，无法确认父子关系。", "muted"));
    if (node.type !== "TRACE" && node.id && byId.get(node.id) === node) {
      const links = el("div", undefined, "parent-links");
      for (const child of siblings.filter(item => item.parent_id === node.id && item !== node)) {
        links.append(button(`子节点 · ${child.name || child.id}`, () => navigateNode(capture, child)));
      }
      if (links.childElementCount) relations.append(links);
    }
    if (relations.childElementCount > 1) $("drawer-content").append(relations);
    const search = el("input", undefined, "drawer-search");
    search.type = "search"; search.placeholder = "在详情中搜索";
    $("drawer-content").append(search);
    const bodies = [];
    for (const key of ["input", "output"]) {
      const detail = el("details", undefined, "detail-section");
      detail.open = true;
      const content = pre(node[key] ?? "原始记录未提供值");
      if (ref && data.references[refKey(ref)]?.location?.field?.includes(`/${key}`)) detail.classList.add("highlight-field");
      detail.append(el("summary", key === "input" ? "节点输入" : "节点输出"), content);
      bodies.push(detail);
      $("drawer-content").append(detail);
    }
    const raw = el("details", undefined, "detail-section");
    raw.append(el("summary", "原始 JSON"), pre(node));
    bodies.push(raw);
    $("drawer-content").append(raw);
    search.oninput = () => {
      const q = search.value.trim().toLocaleLowerCase();
      for (const box of bodies) box.hidden = Boolean(q) && !pretty(box.textContent).toLocaleLowerCase().includes(q);
    };
  };
  function showSessionInfo() {
    openDrawer("会话信息", "session");
    const overview = el("div", undefined, "overview-grid");
    const overviewCard = (title, value) => { const card = section(title, value); card.className = "overview-card"; overview.append(card); return card; };
    overviewCard("来源与会话身份", {session_id: data.session.session_id, session_key: data.session.session_key, source: data.source});
    overviewCard("项目与运行线索", data.project || "未初始化项目上下文；这不阻塞已有轨迹分析。");
    const coverage = overviewCard("实际检查覆盖");
    coverage.append(el("p", data.session.detections.length ? "以下为各次检测的输入、检查范围与缺口；不会因重采或零问题而自动扩大覆盖。" : "尚未记录异常识别；当前只提供原始采集材料。", "muted"));
    for (const detection of data.session.detections) {
      const detail = el("details"); detail.append(el("summary", `${detection.id || "检查记录"} · ${detection.status || "状态未提供"}`), pre(detection)); appendReferences(detail, detection); coverage.append(detail);
    }
    for (const capture of data.captures) {
      const card = overviewCard(capture.current ? "会话轨迹" : `采集 ${capture.id}`);
      if (capture.error) card.append(el("p", capture.error, "warning"));
      const detail = el("details"); detail.append(el("summary", "查看获取范围、字段、完整性和摘要"), pre(capture.manifest));
      card.append(detail, button("浏览此快照", () => {
        $("capture-filter").value = capture.id; investigation = null; scopeBefore = null; chooseCapture(); renderTraces(); closeDrawer(false);
      }));
    }
    const digests = overviewCard("生成时采用的处理记录摘要");
    const digestDetails = el("details"); digestDetails.append(el("summary", "展开记录与 SHA-256"), pre(data.record_digests)); digests.append(digestDetails);
    $("drawer-content").append(overview);
  }

  $("session-title").textContent = data.session.session_id;
  if (data.dashboard.available) {
    const back = $("dashboard-link");
    back.href = dashboardHash("view=session");
    back.hidden = false;
  }
  for (const warning of data.warnings) $("warnings").append(el("p", warning, "warning"));

  const rowHeight = parseInt(getComputedStyle(list).getPropertyValue("--row-height"), 10) || 40;
  const searchable = new WeakMap();
  let streamRows = [], displayRows = [], windowStart = -1, windowEnd = -1, displayedScope = null;
  const selectedIndex = () => selectedNode ? currentCapture.nodes.indexOf(selectedNode) : -1;

  function clearRecordFilters() {
    for (const id of ["type-filter","trace-search","record-mode"]) $(id).value = "";
  }
  function filteredRows() {
    const query = $("trace-search").value.trim().toLocaleLowerCase();
    const type = $("type-filter").value, keyOnly = $("record-mode").value === "key", track = scope();
    const {order, tracks} = timeline();
    let candidates = order;
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
  function segmentMembership(index) {
    if (!investigation) return null;
    const item = investigation.item;
    for (let i = 0; i < item.fragments.length; i++) {
      const part = item.fragments[i];
      if (part.capture_id !== currentCapture?.id) continue;
      if (part.anchors.includes(index)) return {index: i, part, anchor: true};
      if (part.start != null) {
        const n = currentCapture.nodes[index];
        if (n.at != null && n.track === part.track && n.at >= part.start && n.at <= part.end && (n.branch || null) === (part.branch || null)) {
          return {index: i, part, anchor: false};
        }
      }
    }
    return null;
  }
  function fieldNotes(index, part) {
    if (!investigation || !part) return [];
    const notes = [];
    for (const pos of part.refs || []) {
      const saved = issueEvidence(investigation.item)[pos];
      if (saved?.location?.node_index === index) {
        const field = saved.location.field || saved.reference.pointer || "";
        const name = field.split("/").filter(Boolean).pop() || field || "证据";
        notes.push(name);
      }
    }
    return notes;
  }
  function buildDisplayRows(rows) {
    if (!investigation || !rows.length) return rows.map(index => ({kind: "row", index}));
    const only = $("segments-only").checked;
    const item = investigation.item;
    const local = item.fragments
      .map((part, fragIndex) => ({part, fragIndex}))
      .filter(({part}) => part.capture_id === currentCapture?.id);
    if (!local.length) return rows.map(index => ({kind: "row", index}));
    const ordered = [...rows];
    const rowPos = new Map(ordered.map((index, pos) => [index, pos]));
    const result = [];
    const covered = new Set();
    const headers = [];
    for (const {part, fragIndex} of local) {
      const members = ordered.filter(i => {
        if (part.anchors.includes(i)) return true;
        if (only) return false;
        const n = currentCapture.nodes[i];
        return part.start != null && n.at != null && n.track === part.track && n.at >= part.start && n.at <= part.end;
      });
      if (!members.length && !(part.unscoped && part.anchors.some(i => rowPos.has(i)))) continue;
      const first = members[0] ?? part.anchors.find(i => rowPos.has(i));
      if (first == null) continue;
      headers.push({fragIndex, part, at: rowPos.get(first), members});
    }
    headers.sort((a, b) => a.at - b.at || a.fragIndex - b.fragIndex);
    let cursor = 0;
    function emitFold(between) {
      if (!between.length) return;
      if (!only) {
        for (const index of between) result.push({kind: "row", index, membership: segmentMembership(index)});
        return;
      }
      const key = `${between[0]}:${between[between.length - 1]}`;
      if (expandedFolds.has(key)) {
        for (const index of between) result.push({kind: "row", index, membership: segmentMembership(index)});
        result.push({kind: "fold", key, indices: between, expanded: true});
        return;
      }
      const first = currentCapture.nodes[between[0]], last = currentCapture.nodes[between[between.length - 1]];
      const gap = first?.at != null && last?.until != null ? last.until - first.at : null;
      result.push({kind: "fold", key, indices: between, count: between.length, gap});
    }
    for (const header of headers) {
      const firstPos = Math.min(...header.members.map(i => rowPos.get(i)));
      if (firstPos > cursor) emitFold(ordered.slice(cursor, firstPos).filter(i => !covered.has(i)));
      result.push({kind: "header", fragIndex: header.fragIndex, part: header.part});
      for (const index of header.members) {
        if (covered.has(index)) continue;
        result.push({kind: "row", index, fragIndex: header.fragIndex, part: header.part});
        covered.add(index);
      }
      cursor = Math.max(cursor, Math.max(...header.members.map(i => rowPos.get(i))) + 1);
    }
    if (cursor < ordered.length) emitFold(ordered.slice(cursor).filter(i => !covered.has(i)));
    if (!only) {
      // Keep every filtered row exactly once, in time order, with headers before their first member.
      const byIndex = new Map();
      for (const entry of result) if (entry.kind === "row") byIndex.set(entry.index, entry);
      const headersByFirst = new Map();
      for (const header of headers) {
        const first = header.members.find(i => byIndex.has(i));
        if (first != null) headersByFirst.set(first, header);
      }
      const merged = [];
      for (const index of ordered) {
        const header = headersByFirst.get(index);
        if (header) merged.push({kind: "header", fragIndex: header.fragIndex, part: header.part});
        merged.push(byIndex.get(index) || {kind: "row", index, membership: segmentMembership(index)});
      }
      return merged;
    }
    return result;
  }

  function markVisibleRows() {
    const chosen = selectedIndex();
    for (const row of list.querySelectorAll(".trace-row")) row.setAttribute("aria-current", String(Number(row.dataset.nodeIndex) === chosen));
  }
  function paintWindow(force = false) {
    const count = displayRows.length;
    const first = Math.max(0, Math.min(count, Math.floor(scroller.scrollTop / rowHeight)) - 8);
    const last = Math.min(count, first + Math.ceil(scroller.clientHeight / rowHeight) + 17);
    if (!force && first === windowStart && last === windowEnd) return;
    windowStart = first; windowEnd = last;
    const focus = list.contains(document.activeElement) ? document.activeElement.dataset.nodeIndex : null;
    const content = document.createDocumentFragment();
    const chosen = selectedIndex(), start = startTime();
    for (let pos = first; pos < last; pos++) {
      const item = displayRows[pos];
      if (item.kind === "header") {
        const part = item.part;
        const row = el("div", undefined, "segment-header");
        row.append(el("span", part.summary || (part.unscoped ? "未圈定范围" : "片段")));
        const meta = el("span", undefined, "seg-meta");
        meta.append(el("span", `${part.node_count || part.anchors.length || 0} 节点`));
        if (part.duration_ms != null) meta.append(el("span", elapsed(part.duration_ms)));
        row.append(meta);
        content.append(row);
        continue;
      }
      if (item.kind === "fold") {
        const row = button("", () => {
          if (item.expanded) expandedFolds.delete(item.key); else expandedFolds.add(item.key);
          renderTraces(scroller.scrollTop);
        }, "row-grid fold-row");
        const label = item.expanded
          ? `⋯ 收起已展开的 ${item.indices.length} 条 ⋯`
          : `⋯ 折叠 ${item.count} 条${item.gap != null ? ` · 空档 ${elapsed(item.gap)}` : ""} ⋯`;
        row.append(el("span", label));
        content.append(row);
        continue;
      }
      const i = item.index, n = currentCapture.nodes[i];
      const row = button("", () => openEventDrawer(n, row), "row-grid trace-row");
      row.dataset.nodeIndex = i; row.dataset.position = pos;
      row.setAttribute("aria-current", String(i === chosen));
      const time = el("span", elapsed(n.at === null ? null : n.at - start, true), "time-col");
      if (n.start_time) time.title = n.start_time;
      const traceCell = el("span", undefined, "trace-col");
      const tag = el("span", trackLabel(n.track), "trace-tag"); tag.title = n.trace_id || n.reference.path; traceCell.append(tag);
      const action = el("span", undefined, "node-main"), name = n.name || String(n.id || "未命名记录");
      const label = el("span", name, "node-label"); label.title = name;
      action.append(label, el("span", n.type, "node-branch"));
      const membership = item.part
        ? {part: item.part, index: item.fragIndex, anchor: item.part.anchors.includes(i)}
        : (item.membership || segmentMembership(i));
      if (membership?.part) {
        row.classList.add("in-fragment");
        for (const note of fieldNotes(i, membership.part)) action.append(el("span", `★ ${note}`, "evidence-note"));
      }
      if (n.issue_ids?.length) {
        for (const id of n.issue_ids.slice(0, 3)) action.append(el("span", shortIssue(id), "issue-pill"));
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
    let pos = displayRows.findIndex(item => item.kind === "row" && item.index === index);
    if (pos < 0 && timeline().tracks[n.track].roots.includes(index)) {
      pos = displayRows.findIndex(item => item.kind === "row" && currentCapture.nodes[item.index].track === n.track);
    }
    if (pos >= 0) scroller.scrollTop = Math.max(0, (pos - 2) * rowHeight);
    paintWindow(true);
  }
  scroller.addEventListener("scroll", () => paintWindow(), {passive:true});
  new ResizeObserver(() => paintWindow()).observe(scroller);
  scroller.addEventListener("keydown", event => {
    if (!["ArrowDown","ArrowUp","Home","End"].includes(event.key) || !displayRows.length) return;
    const current = Number(document.activeElement.dataset.position ?? Math.floor(scroller.scrollTop / rowHeight));
    let pos = current;
    const step = dir => {
      let next = pos;
      do { next += dir; } while (next >= 0 && next < displayRows.length && displayRows[next].kind !== "row");
      return next;
    };
    pos = event.key === "Home" ? displayRows.findIndex(item => item.kind === "row")
      : event.key === "End" ? (() => { for (let i = displayRows.length - 1; i >= 0; i--) if (displayRows[i].kind === "row") return i; return -1; })()
      : step(event.key === "ArrowUp" ? -1 : 1);
    if (pos < 0 || pos >= displayRows.length || displayRows[pos].kind !== "row") return;
    event.preventDefault();
    if (pos*rowHeight < scroller.scrollTop) scroller.scrollTop = pos*rowHeight;
    if ((pos+1)*rowHeight > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = (pos+1)*rowHeight - scroller.clientHeight;
    paintWindow();
    list.querySelector(`[data-position="${pos}"]`)?.focus({preventScroll:true});
  });
  // Redraw only the swimlanes. Hover previews use this so the issue rail and Event list stay
  // in place; rebuilding them under the pointer swallowed the click on rail buttons.
  function drawLanes() {
    const local = item => (item?.fragments || []).map((p,i) => [i,p]).filter(([,p]) => p.capture_id === currentCapture?.id);
    const previewParts = !investigation && previewIssue ? local(previewIssue) : [];
    swim.render(currentCapture, displayedScope, selectedIndex(), local(investigation?.item), investigation?.index, issueCounts(), previewParts);
  }
  function renderTraces(top = 0) {
    const {tracks, order} = timeline();
    displayedScope = scope();
    const track = tracks[displayedScope];
    drawLanes();
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
    $("session-stats").textContent = `${native.length} Trace · ${order.length} 观测 · ${data.issues.length} 问题`;
    scroller.parentElement.classList.toggle("single", tracks.length === 1 || displayedScope !== null);
    streamRows = filteredRows();
    displayRows = buildDisplayRows(streamRows);
    $("trace-count").textContent = `${streamRows.length} 条`;
    list.style.paddingTop = "0px";
    list.style.paddingBottom = `${displayRows.length * rowHeight}px`;
    scroller.scrollTop = top;
    paintWindow(true);
    syncRailSelection();
    syncHash();
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
    selectedNode = null; clearRecordFilters(); expandedFolds = new Set();
    const {tracks} = timeline();
    const select = $("trace-filter");
    select.replaceChildren(new Option("全部 Trace",""));
    tracks.forEach((t,i) => select.add(new Option(`${trackLabel(i)} · ${t.trace_id || t.file.split("/").pop()}`, String(i))));
    $("trace-choice").hidden = tracks.length < 2;
    options($("type-filter"), [...new Set((currentCapture?.nodes || []).map(n => n.type).filter(Boolean))], "全部类型");
    $("capture-status").replaceChildren();
    if (currentCapture?.error) $("capture-status").append(el("p",currentCapture.error,"warning"));
  }
  function showFragmentBar() {
    const part = partNow();
    $("fragment-bar").hidden = !part;
    if (!part) return;
    const {item,index} = investigation;
    $("fragment-issue").textContent = `${item.record.id} · ${item.record.title}`;
    $("fragment-issue").title = $("fragment-issue").textContent;
    const labels = {trace:"执行级证据", file:"归档引用", untimed:"时间未知", point:"未圈定范围", segment:"片段"};
    const range = part.start == null ? (part.unscoped ? "未圈定范围" : labels[part.kind] || part.kind) : `${elapsed(part.start-startTime(),true)} – ${elapsed(part.end-startTime(),true)}`;
    const title = part.summary || `片段 ${index+1}`;
    $("fragment-position").textContent = [`${title} · ${index+1} / ${item.fragments.length}`, part.track == null ? null : trackLabel(part.track), range].filter(Boolean).join(" · ");
    $("fragment-prev").disabled = index === 0;
    $("fragment-next").disabled = index+1 === item.fragments.length;
    $("fragment-fields").textContent = `片段证据 (${part.refs.length+(part.context_refs?.length || 0)})`;
  }
  function startIssue(item, fragmentIndex = null) {
    if (!item.fragments.length) { showIssue(item); syncRailSelection(); return; }
    const before = investigation?.before || rememberView();
    const priorScope = investigation ? investigation.priorScope : scopeBefore;
    investigation = {item, before, priorScope, index:0};
    previewIssue = null;
    const preferred = fragmentIndex != null && fragmentIndex >= 0 ? fragmentIndex
      : item.fragments.findIndex(f => f.capture_id === currentCapture?.id && f.anchors.length);
    goFragment(preferred < 0 ? 0 : preferred);
    if ($("drawer").hidden) $("fragment-fields").focus({preventScroll:true});
  }
  function goFragment(index) {
    const part = investigation?.item.fragments[index];
    if (!part) return;
    investigation.index = index;
    if (part.capture_id && currentCapture?.id !== part.capture_id) {
      $("capture-filter").value = part.capture_id; chooseCapture();
    }
    $("trace-filter").value = ""; clearRecordFilters(); selectedNode = null;
    scopeBefore = null; renderTraces();
    if (part.anchors.length) {
      selectAndScroll(part.anchors[0]); $("fragment-bar").scrollIntoView({block:"nearest"});
    } else {
      const ref = evidenceRefAt(investigation.item, part.refs[0]);
      activeIssue = investigation.item; issueStage = "fragment";
      if (ref) openReference(ref);
      else showIssue(investigation.item);
    }
    syncHash();
  }
  function showFragmentEvidence() {
    const {item,index} = investigation;
    const part = item.fragments[index];
    activeIssue = item; issueStage = "fragment"; issueTrigger = item.record.id;
    openDrawer(`${item.record.id} · ${part.id || `片段 ${index+1}`} 的证据`, "issue");
    if (returnToDiagnosis) $("drawer-content").append(button("← 返回诊断", returnToDiagnosis));
    if (part.summary) $("drawer-content").append(el("p", part.summary, "muted"));
    function add(title, positions) {
      if (!positions?.length) return;
      const box = section(title);
      appendReferences(box, positions.map(i => evidenceRefAt(item, i)).filter(Boolean));
      $("drawer-content").append(box);
    }
    add(part.kind === "trace" ? "执行级证据" : "本段证据", part.refs);
    add("此 Trace 的上下文", part.context_refs);
  }
  function exitIssue() {
    if (!investigation) return;
    const {before,priorScope} = investigation;
    investigation = null; scopeBefore = priorScope; expandedFolds = new Set();
    restoreView(before);
    syncRailSelection();
    syncHash();
  }
  function syncRailSelection() {
    const activeId = investigation?.item.record.id;
    for (const card of $("issue-rail-list").querySelectorAll(".issue-card")) {
      const mine = card.dataset.issueId === activeId;
      card.setAttribute("aria-current", String(mine));
      card.querySelectorAll(".seg-chip").forEach((chip, index) => chip.setAttribute("aria-current", String(mine && investigation.index === index)));
    }
  }
  function renderIssueRail() {
    const text = $("issue-search").value.toLocaleLowerCase();
    const status = $("issue-status").value;
    const filtered = data.issues.filter(item => {
      if (!projectMatches(item) && new URLSearchParams(location.hash.slice(1)).get("issue") !== item.record.id) return false;
      if (railFilter && !railFilter.ids.includes(item.record.id)) return false;
      const progress = progressOf(item).label;
      if (status && progress !== status) return false;
      if (!text) return true;
      return pretty(item.record).toLocaleLowerCase().includes(text) || progress.toLocaleLowerCase().includes(text);
    });
    $("rail-count").textContent = `${filtered.length}/${data.issues.length}`;
    const box = $("issue-rail-list"); box.replaceChildren();
    if (railFilter) {
      const note = el("p", `仅显示 ${trackLabel(railFilter.track)} 关联的问题 `, "muted rail-filter");
      note.append(button("显示全部", () => { railFilter = null; renderIssueRail(); }));
      box.append(note);
    }
    if (!filtered.length) {
      box.append(el("p", data.issues.length ? "没有符合筛选条件的问题。" : data.session.detections.length ? "已保存检测记录，尚无保留问题；请查看检查范围与缺口。" : "尚未进行异常识别。", "empty"));
      return;
    }
    for (const item of filtered) {
      const issue = item.record;
      const progress = progressOf(item);
      const card = el("article", undefined, "issue-card");
      card.dataset.issueId = issue.id;
      const head = el("div", undefined, "issue-card-head");
      head.append(el("span", issue.id, "issue-id-text"), el("span", undefined, `progress-dot ${progress.klass}`));
      const body = el("div", undefined, "issue-card-body");
      body.append(projectLabels(item));
      body.append(el("h3", issue.title || "问题"), el("div", progress.label, "issue-progress"));
      const tag = categoryTag(issue.category);
      if (tag) body.append(tag);
      const segs = el("div", undefined, "seg-list");
      const parts = item.fragments.length ? item.fragments : [{summary: "未圈定范围", unscoped: true}];
      parts.forEach((part, index) => {
        const summary = part.summary || (part.unscoped ? "未圈定范围" : `片段 ${index + 1}`);
        const chip = button("", () => startIssue(item, index), `seg-chip${part.unscoped ? " unscoped" : ""}`);
        chip.append(el("span", summary, "seg-chip-summary"));
        chip.title = summary;
        if (part.track != null) {
          const capture = captureById.get(part.capture_id);
          const time = elapsed(part.start == null ? null : part.start - startTime(capture), true);
          const meta = `${trackLabel(part.track, capture)} · ${time}`;
          chip.append(el("span", meta, "seg-chip-meta mono"));
          chip.title += ` · ${meta}`;
        }
        segs.append(chip);
      });
      const actions = el("div", undefined, "card-actions");
      actions.append(
        button("定位", () => startIssue(item)),
        button("详情", () => { if (investigation?.item.record.id !== issue.id) startIssue(item); showIssue(item); })
      );
      card.append(head, body, segs, actions);
      card.onmouseenter = () => {
        if (investigation || previewIssue === item) return;
        previewIssue = item;
        drawLanes();
      };
      card.onmouseleave = () => {
        if (previewIssue !== item) return;
        previewIssue = null;
        drawLanes();
      };
      box.append(card);
    }
    syncRailSelection();
  }
  function syncHash() {
    const parts = [...projectSelection].map(id => `project=${encodeURIComponent(id)}`);
    if (investigation) {
      parts.push(`issue=${encodeURIComponent(investigation.item.record.id)}`);
      const part = partNow();
      if (part?.id) parts.push(`seg=${encodeURIComponent(part.id)}`);
      else parts.push(`seg=${investigation.index + 1}`);
    } else if (selectedNode?.trace_id && selectedNode?.id != null) {
      parts.push(`event=${encodeURIComponent(`${selectedNode.trace_id}/${selectedNode.id}`)}`);
    }
    const next = parts.length ? `#${parts.join("&")}` : "";
    if (location.hash !== next) history.replaceState(null, "", next || location.pathname + location.search);
  }
  function applyHash(hash = location.hash) {
    // Initial rendering can normalize the URL before the saved deep link is applied.
    if (hash !== location.hash) history.replaceState(null, "", hash || location.pathname + location.search);
    const raw = hash.slice(1);
    const params = new URLSearchParams(raw.includes("=") ? raw : `issue=${raw}`);
    projectSelection.clear(); params.getAll("project").forEach(id => projectSelection.add(id));
    for (const input of projectInputs) input.checked = projectSelection.has(input.value);
    updateProjectSummary();
    const issueId = params.get("issue");
    const seg = params.get("seg");
    const event = params.get("event");
    if (issueId) {
      const item = issueById.get(issueId);
      if (!item) { renderIssueRail(); return; }
      let index = 0;
      if (seg) {
        const byId = item.fragments.findIndex(part => part.id === seg);
        index = byId >= 0 ? byId : Math.max(0, Number(seg) - 1);
      }
      startIssue(item, index);
      renderIssueRail();
      return;
    }
    if (event) {
      const [traceId, nodeId] = event.split("/");
      for (const capture of data.captures) {
        const node = capture.nodes.find(item => String(item.trace_id) === traceId && String(item.id) === nodeId);
        if (node) { navigateNode(capture, node); renderIssueRail(); return; }
      }
    }
    renderIssueRail();
  }

  $("fragment-prev").onclick = () => goFragment(investigation.index-1);
  $("fragment-next").onclick = () => goFragment(investigation.index+1);
  $("fragment-fields").onclick = showFragmentEvidence;
  $("fragment-detail").onclick = () => showIssue(investigation.item);
  $("fragment-exit").onclick = exitIssue;
  $("lane-reset").onclick = () => focusTrace(null);
  $("session-info").onclick = showSessionInfo;
  $("capture-filter").closest("label").hidden = Boolean(currentCapture?.current);
  options($("capture-filter"), data.captures.map(c => c.id));
  if (currentCapture) $("capture-filter").value = currentCapture.id;
  $("project-filter").onchange = () => { projectSelection.clear(); for (const input of projectInputs) if (input.checked) projectSelection.add(input.value); updateProjectSummary(); const params = new URLSearchParams(location.hash.slice(1)); params.delete("project"); for (const id of projectSelection) params.append("project", id); history.replaceState(null, "", `#${params}`); renderIssueRail(); };
  $("capture-filter").onchange = () => { investigation = null; scopeBefore = null; railFilter = null; chooseCapture(); renderIssueRail(); renderTraces(); };
  $("trace-filter").onchange = () => focusTrace(scope());
  for (const id of ["trace-search","type-filter","record-mode"]) $(id).addEventListener(id === "trace-search" ? "input" : "change", () => renderTraces());
  $("segments-only").onchange = () => renderTraces(scroller.scrollTop);
  const presentProgress = new Set(data.issues.map(item => item.progress.label));
  options($("issue-status"), data.progress_legend.map(entry => entry.label).filter(label => presentProgress.has(label)), "全部进度");
  $("issue-search").oninput = () => { railFilter = null; renderIssueRail(); };
  $("issue-status").onchange = () => { railFilter = null; renderIssueRail(); };
  const collapsed = localStorage.getItem(RAIL_KEY) === "1";
  document.body.classList.toggle("rail-collapsed", collapsed);
  $("rail-toggle").setAttribute("aria-expanded", String(!collapsed));
  $("rail-toggle").onclick = () => {
    const next = !document.body.classList.contains("rail-collapsed");
    document.body.classList.toggle("rail-collapsed", next);
    $("rail-toggle").setAttribute("aria-expanded", String(!next));
    $("rail-toggle").title = next ? "展开问题栏" : "收起问题栏";
    localStorage.setItem(RAIL_KEY, next ? "1" : "0");
  };
  let resizing = false;
  $("drawer-resizer").addEventListener("pointerdown", event => {
    resizing = true;
    $("drawer-resizer").setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  window.addEventListener("pointermove", event => {
    if (!resizing) return;
    setDrawerWidth(window.innerWidth - event.clientX);
  });
  window.addEventListener("pointerup", () => { resizing = false; });
  $("drawer-close").onclick = () => closeDrawer();
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      if (!$("drawer").hidden) { event.preventDefault(); closeDrawer(); return; }
      if (investigation) { event.preventDefault(); exitIssue(); return; }
    }
    if (!$("drawer").hidden && drawerMode === "event" && ["ArrowDown", "ArrowUp"].includes(event.key)) {
      if (!displayRows.length) return;
      const current = selectedIndex();
      const positions = displayRows.map((item, pos) => item.kind === "row" ? pos : -1).filter(pos => pos >= 0);
      const here = positions.findIndex(pos => displayRows[pos].index === current);
      const next = positions[Math.max(0, Math.min(positions.length - 1, (here < 0 ? 0 : here) + (event.key === "ArrowDown" ? 1 : -1)))];
      if (next == null) return;
      event.preventDefault();
      const index = displayRows[next].index;
      selectAndScroll(index);
      openEventDrawer(currentCapture.nodes[index], document.activeElement);
    }
  });
  window.addEventListener("hashchange", () => applyHash());
  const initialHash = location.hash;
  chooseCapture(); renderIssueRail(); renderTraces(); applyHash(initialHash);
})();
