(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("dashboard-data").textContent);
  const $ = id => document.getElementById(id);
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const button = (text, action, className) => { const node = el("button", text, className); node.type = "button"; node.onclick = action; return node; };

  // Project choice is single: one linked project, or all issues. Only projects some issue links to are offered.
  const PROJECTS = data.project_index || [];
  const PROJECT = new Map(PROJECTS.map(entry => [entry.id, entry]));
  // Older links may carry several projects (or the removed empty-association value): keep the first one still offered.
  const projectFrom = params => params.getAll("project").find(id => PROJECT.has(id)) || "";
  const projectMatches = item => state.view === "session" || !state.project || (item.linked_projects || []).some(project => project.id === state.project);
  const projectNames = item => (item.linked_projects || []).map(p => p.name);
  const projectText = item => projectNames(item).join("、") || "无";
  const projectLabel = entry => `${entry.name}${entry.deprecated ? "（已弃用）" : entry.registered === false ? "（未登记）" : ""}`;


  const NONE = "~uncategorized"; // cannot collide with a kebab-case category ID
  const ORDER = new Map(data.progress_legend.map((entry, index) => [entry.key, index]));
  const LABEL = new Map(data.progress_legend.map(entry => [entry.key, entry.label]));
  const STATUS = {completed: "已完成", partial: "部分完成", failed: "失败", blocked: "受阻", interrupted: "已中断", running: "进行中"};
  const state = {view: "session", q: "", progress: "", project: "", source: "", drawer: null};
  let shown = null;

  const categoryName = id => data.categories[id]?.name || id;
  const shortId = id => id.length > 24 ? `${id.slice(0, 8)}…${id.slice(-8)}` : id;
  const shortIssue = id => id.replace(/^ISSUE-/, "") || id;
  const stamp = new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false});
  const when = ms => {
    if (ms == null) return "时间未记录";
    const part = Object.fromEntries(stamp.formatToParts(new Date(ms)).map(item => [item.type, item.value]));
    return `${part.month}-${part.day} ${part.hour === "24" ? "00" : part.hour}:${part.minute}`;
  };
  const sourceText = source => [source.type, source.environment].filter(Boolean).join(" · ") || "来源未记录";
  const statusText = value => STATUS[value] || value || "未记录";

  // --- derived search text and source scope ---------------------------------------------------------------------
  const sourceKey = source => ["type", "instance", "project", "environment"].map(key => source?.[key] || "").join("\u0000");
  for (const s of data.sessions) {
    s._source = s.error ? "" : sourceKey(s.source);
    s._text = [s.session_id, s.dir, ...Object.values(s.source || {})].join("\n").toLowerCase();
    for (const issue of s.issues) {
      issue._text = [projectText(issue), ...(issue.linked_projects || []).flatMap(p => p.aliases || []), issue.id, issue.title, issue.category || "", issue.category ? categoryName(issue.category) : "未分类", LABEL.get(issue.progress)].join("\n").toLowerCase();
    }
  }
  const scoped = () => data.sessions.filter(s => !state.source || s._source === state.source);
  const matchIssue = (s, issue) =>
    projectMatches(issue) && (state.view === "session" || !state.progress || issue.progress === state.progress) && (!state.q || s._text.includes(state.q) || issue._text.includes(state.q));
  const matchSession = s => s.issues.some(issue => matchIssue(s, issue)) ||
    (state.view === "session" && !s.issues.length && (!state.q || s._text.includes(state.q)));
  const byProgress = (a, b) => ORDER.get(a.progress) - ORDER.get(b.progress) || a.id.localeCompare(b.id, undefined, {numeric: true});

  // --- small building blocks ------------------------------------------------------------------------------------
  const countBy = issues => { const counts = new Map(); for (const issue of issues) counts.set(issue.progress, (counts.get(issue.progress) || 0) + 1); return counts; };
  const topProgress = counts => [...counts].sort((a, b) => b[1] - a[1] || ORDER.get(a[0]) - ORDER.get(b[0])).slice(0, 2).map(([key, n]) => `${LABEL.get(key)} ${n}`).join(" · ");
  const miniBar = counts => {
    const bar = el("div", undefined, "mini-bar");
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", `问题处理分布：${[...counts].map(([key, n]) => `${LABEL.get(key)} ${n}`).join("，")}`);
    for (const [key, n] of [...counts].sort((a, b) => ORDER.get(a[0]) - ORDER.get(b[0]))) {
      const segment = el("i"); segment.className = `tone-${key}`; segment.style.flex = String(n); segment.title = `${LABEL.get(key)} ${n}`;
      bar.append(segment);
    }
    return bar;
  };
  const flag = (text, bad) => el("span", text, bad ? "flag bad" : "flag");
  const copyText = async text => {
    try { await navigator.clipboard.writeText(text); return true; } catch { /* file:// pages may refuse; fall back below */ }
    try {
      const area = el("textarea"); area.value = text; area.setAttribute("readonly", "");
      area.style.cssText = "position:fixed;opacity:0"; document.body.append(area); area.select();
      const ok = document.execCommand("copy"); area.remove(); return ok;
    } catch { return false; }
  };
  const copyButton = (text, label, caption) => {
    const node = button(caption ?? "⧉", async () => {
      const ok = await copyText(text);
      node.textContent = ok ? (caption ? "已复制" : "✓") : (caption ? "复制失败" : "×");
      setTimeout(() => { node.textContent = caption ?? "⧉"; }, 1500);
    }, caption ? undefined : "icon-button");
    node.title = label; node.setAttribute("aria-label", label);
    return node;
  };
  const categoryChip = id => {
    if (!id) return el("span", "未分类", "category-tag plain");
    const chip = button(categoryName(id), () => setView("category", id), "category-tag");
    chip.title = `查看“${categoryName(id)}”的同类问题`;
    return chip;
  };
  const empty = text => el("div", text, "empty-dash");
  // A card's own control previews it in the drawer; pressing it again while that preview is open closes it.
  const isOpen = (view, key) => state.view === view && state.drawer === key;
  const previewButton = (view, key, label, className) => {
    const node = button("", () => (isOpen(view, key) ? closeDrawer() : setView(view, key)), className);
    if (label) node.setAttribute("aria-label", label);
    node.setAttribute("aria-controls", "drawer");
    node.setAttribute("aria-expanded", String(isOpen(view, key)));
    return node;
  };

  // --- grouping by category -------------------------------------------------------------------------------------
  function categoryGroups() {
    const groups = new Map();
    for (const s of scoped()) for (const issue of s.issues) {
      const key = issue.category || NONE;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push({s, issue});
    }
    return groups;
  }
  const latestOf = items => items.reduce((max, {issue}) => issue.updated_ms != null && issue.updated_ms > max ? issue.updated_ms : max, -1);

  // --- page chrome ----------------------------------------------------------------------------------------------
  const TABS = [["tab-session", "session"], ["tab-category", "category"]];
  function renderTabs() {
    for (const [id, view] of TABS) {
      const tab = $(id), selected = state.view === view;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected) $("dash-panel").setAttribute("aria-labelledby", id);
    }
  }
  // Tablist keyboard pattern: arrows (wrapping), Home and End move to and activate the neighbouring tab.
  function onTabKey(event) {
    const at = TABS.findIndex(([, view]) => view === state.view);
    const steps = {ArrowLeft: at - 1, ArrowRight: at + 1, Home: 0, End: TABS.length - 1};
    if (!(event.key in steps)) return;
    event.preventDefault();
    const [id, view] = TABS[(steps[event.key] + TABS.length) % TABS.length];
    if (view !== state.view) setView(view);
    $(id).focus();
  }
  function renderSessionAttention() {
    const attention = $("session-attention");
    attention.replaceChildren();
    if (state.view === "session") {
      const sessions = scoped().filter(matchSession);
      const uncertain = sessions.filter(s => s.issues.some(issue => issue.progress === "inconclusive")).length;
      const failed = sessions.filter(s => s.issues.some(issue => issue.progress === "fail")).length;
      const broken = sessions.filter(s => s.error || s.problem_count).length;
      if (uncertain) attention.append(el("span", `${uncertain} 个会话存在“无法判断”的验证结论`, "attention-note"));
      if (failed) attention.append(el("span", `${failed} 个会话存在验证失败的问题`, "attention-note"));
      if (broken) attention.append(el("span", `${broken} 个会话有记录不可读取，问题统计可能不全`, "attention-note"));
    }
    attention.hidden = !attention.children.length;
  }
  const searchMatches = (s, issue) => !state.q || s._text.includes(state.q) || issue._text.includes(state.q);
  const chooseProject = id => { state.project = state.project === id ? "" : id; syncHash(); render(); };
  // Project counts follow source and search only: neither the progress choice nor the project choice changes them, so
  // every project keeps its real size while one is chosen.
  function renderProjects() {
    const legend = $("project-legend");
    legend.replaceChildren();
    if (state.view !== "category") { legend.hidden = true; return; }
    const issues = scoped().flatMap(s => s.issues.filter(issue => searchMatches(s, issue)));
    const counts = new Map();
    for (const issue of issues) for (const project of issue.linked_projects || []) {
      if (PROJECT.has(project.id)) counts.set(project.id, (counts.get(project.id) || 0) + 1);
    }
    const order = new Map(PROJECTS.map((entry, index) => [entry.id, index]));
    const shown = PROJECTS.filter(entry => counts.get(entry.id) || entry.id === state.project).sort((a, b) =>
      Number(Boolean(a.deprecated)) - Number(Boolean(b.deprecated)) || Number(a.registered === false) - Number(b.registered === false) ||
      (counts.get(b.id) || 0) - (counts.get(a.id) || 0) || order.get(a.id) - order.get(b.id));
    // One project that holds every issue in the source scope filters nothing, so the row would only repeat the total. This
    // is judged before search, so the row does not vanish while typing.
    const inScope = scoped().flatMap(s => s.issues);
    const linkedIn = new Set(inScope.flatMap(issue => (issue.linked_projects || []).map(p => p.id)).filter(id => PROJECT.has(id)));
    const only = linkedIn.size === 1 ? [...linkedIn][0] : null;
    const useless = !linkedIn.size || (only !== null && !state.project && inScope.every(issue => (issue.linked_projects || []).some(p => p.id === only)));
    legend.hidden = useless;
    if (useless) return;
    legend.setAttribute("role", "radiogroup");
    legend.append(el("span", "关联项目", "filter-caption"));
    const choice = (text, id, title) => {
      const item = button("", () => chooseProject(id), "legend-item project-item");
      item.setAttribute("role", "radio");
      item.setAttribute("aria-checked", String(state.project === id));
      if (title) item.title = title;
      item.append(...text);
      return item;
    };
    legend.append(choice([document.createTextNode(`全部 ${issues.length}`)], "", "显示全部问题"));
    for (const entry of shown) {
      const n = counts.get(entry.id) || 0;
      const sessions = new Set(scoped().filter(s => s.issues.some(issue => searchMatches(s, issue) && (issue.linked_projects || []).some(p => p.id === entry.id))).map(s => s.dir)).size;
      const name = el("span", projectLabel(entry), "project-name");
      const item = choice([name, el("span", `${n}`, "project-count"), el("span", `· ${sessions} 会话`, "muted")], entry.id,
        state.project === entry.id ? `取消筛选 ${projectLabel(entry)}` : `只看关联 ${projectLabel(entry)} 的问题`);
      if (!n) item.classList.add("is-empty");
      legend.append(item);
    }
    if (issues.some(issue => (issue.linked_projects || []).length > 1)) legend.append(el("span", "部分问题关联多个项目，各项目数量不能相加", "filter-hint muted"));
  }
  function renderProgress() {
    const legend = $("progress-legend");
    legend.replaceChildren();
    legend.hidden = state.view !== "category";
    if (legend.hidden) return;
    const counts = countBy(scoped().flatMap(s => s.issues.filter(issue => projectMatches(issue) && searchMatches(s, issue))));
    const total = [...counts.values()].reduce((sum, n) => sum + n, 0);
    legend.append(el("span", "处理状态", "filter-caption"));
    for (const {key, label} of [{key: "", label: "全部"}, ...data.progress_legend]) {
      const n = key ? counts.get(key) : total;
      if (key && !n) continue;
      const item = button("", () => { state.progress = state.progress === key ? "" : key; render(); }, "legend-item");
      item.setAttribute("aria-pressed", String(state.progress === key));
      if (key) item.append(el("span", undefined, `swatch tone-${key}`));
      item.append(document.createTextNode(`${label} ${n}`));
      legend.append(item);
    }
  }
  function renderNote() {
    const note = $("filter-note");
    note.replaceChildren();
    const progress = state.view === "category" && state.progress;
    const project = state.view === "category" && PROJECT.get(state.project);
    note.hidden = !(state.q || state.source || progress || project);
    if (note.hidden) return;
    const matched = scoped().flatMap(s => s.issues.filter(issue => matchIssue(s, issue)));
    note.append(el("span", `当前筛选：${scoped().filter(matchSession).length} 个会话 · ${matched.length} 个问题 `));
    if (project) note.append(document.createTextNode(`关联项目：${projectLabel(project)} `));
    if (progress) note.append(document.createTextNode(`处理状态：${LABEL.get(state.progress)}`));
    note.append(button("清除筛选", () => {
      state.q = ""; state.source = ""; $("dash-search").value = ""; $("source-filter").value = "";
      if (state.view === "category") { state.progress = ""; state.project = ""; }
      syncHash(); render();
    }));
  }

  // --- session cards --------------------------------------------------------------------------------------------
  function sessionCard(s) {
    const card = el("article", undefined, "dash-card session-card");
    card.dataset.key = s.dir;
    if (isOpen("session", s.dir)) card.setAttribute("aria-current", "true");
    const head = el("div", undefined, "card-head");
    const open = previewButton("session", s.dir, `查看会话 ${s.session_id}`, "card-open");
    const title = el("span", shortId(s.session_id), "card-title mono"); title.title = s.session_id;
    open.append(title);
    head.append(open, copyButton(s.session_id, "复制 Session ID"));
    card.append(head);
    if (s.error) {
      card.append(el("div", undefined, "chips")); card.lastChild.append(flag("记录不可读取", true));
      card.append(el("div", s.error, "card-definition"));
      return card;
    }
    card.append(el("div", `${sourceText(s.source)} · 更新 ${when(s.updated_ms)}`, "card-meta"));
    const matched = s.issues.filter(issue => matchIssue(s, issue));
    if (matched.length) {
      const counts = countBy(matched);
      const row = el("div", undefined, "card-count");
      row.append(el("strong", `${matched.length} 个问题`), el("span", topProgress(counts), "muted"));
      card.append(row, miniBar(counts));
    } else card.append(el("div", "无保留问题（不代表会话正确）", "card-count muted"));
    const perCategory = new Map(); let none = 0;
    for (const issue of matched) issue.category ? perCategory.set(issue.category, (perCategory.get(issue.category) || 0) + 1) : none++;
    const entries = [...perCategory].sort((a, b) => b[1] - a[1] || categoryName(a[0]).localeCompare(categoryName(b[0])));
    if (none) entries.push([NONE, none]);
    if (entries.length) {
      const chips = el("div", undefined, "chips");
      for (const [id, n] of entries.slice(0, 3)) {
        if (id === NONE) { chips.append(el("span", `未分类 ${n}`, "category-tag plain")); continue; }
        const chip = button(`${categoryName(id)} ${n}`, () => setView("category", id), "category-tag");
        chip.title = `查看“${categoryName(id)}”的同类问题`; chips.append(chip);
      }
      if (entries.length > 3) chips.append(el("span", `+${entries.length - 3}`, "category-tag plain"));
      card.append(chips);
    }
    const flags = el("div", undefined, "chips");
    if (!s.preview.exists) flags.append(flag("预览缺失"));
    else if (s.preview.stale) flags.append(flag("预览需刷新"));
    if (s.problem_count) flags.append(flag(`${s.problem_count} 条记录读取失败`, true));
    if (flags.children.length) card.append(flags);
    return card;
  }
  function renderSessions(grid) {
    const list = scoped().filter(matchSession);
    if (!list.length) { grid.append(empty("没有符合筛选条件的会话。")); return; }
    for (const s of list) grid.append(sessionCard(s));
  }

  // --- category cards -------------------------------------------------------------------------------------------
  function categoryCard(key, items) {
    const info = data.categories[key];
    const card = el("article", undefined, "dash-card category-card");
    card.dataset.key = key;
    if (info?.deprecated) card.dataset.deprecated = "true";
    if (isOpen("category", key)) card.setAttribute("aria-current", "true");
    const head = el("div", undefined, "card-head");
    const open = previewButton("category", key, `查看问题分类 ${categoryName(key)}`, "card-open");
    const title = el("span", categoryName(key), "card-title"); title.title = categoryName(key);
    open.append(title);
    head.append(open);
    if (info?.deprecated) head.append(flag("已弃用"));
    card.append(head, el("div", info ? info.definition : "分类词表中没有这条分类，按分类 ID 显示。", "card-definition"));
    const counts = countBy(items.map(item => item.issue));
    const row = el("div", undefined, "card-count");
    row.append(el("strong", `${new Set(items.map(item => item.s.dir)).size} 个会话 · ${items.length} 个问题`), el("span", `最近 ${when(latestOf(items) < 0 ? null : latestOf(items))}`, "muted"));
    card.append(projectLine(items.map(({issue}) => issue)));
    card.append(row, miniBar(counts), el("div", topProgress(counts), "card-meta"));
    return card;
  }
  // The distinct projects behind a group of issues; each offered name narrows the issue view to that project.
  function projectLine(issues) {
    const line = el("p", "关联项目：", "card-projects");
    const seen = new Map();
    for (const issue of issues) for (const project of issue.linked_projects || []) if (!seen.has(project.id)) seen.set(project.id, project);
    if (!seen.size) { line.append(document.createTextNode("无")); return line; }
    [...seen.values()].forEach((project, index) => {
      if (index) line.append(document.createTextNode("、"));
      const entry = PROJECT.get(project.id);
      if (!entry || state.project === project.id) { line.append(el("span", project.name, state.project === project.id ? "project-link current" : undefined)); return; }
      const link = button(project.name, () => chooseProject(project.id), "project-link");
      link.title = `只看关联 ${projectLabel(entry)} 的问题`;
      line.append(link);
    });
    return line;
  }
  function renderCategories(grid, strip) {
    const visible = [...categoryGroups()].map(([key, items]) => [key, items.filter(({s, issue}) => matchIssue(s, issue))]).filter(([, items]) => items.length);
    const real = visible.filter(([key]) => key !== NONE).sort((a, b) => {
      const sessions = items => new Set(items.map(item => item.s.dir)).size;
      return Number(Boolean(data.categories[a[0]]?.deprecated)) - Number(Boolean(data.categories[b[0]]?.deprecated)) ||
        sessions(b[1]) - sessions(a[1]) || b[1].length - a[1].length || latestOf(b[1]) - latestOf(a[1]) ||
        categoryName(a[0]).localeCompare(categoryName(b[0]));
    });
    for (const [key, items] of real) grid.append(categoryCard(key, items));
    if (!real.length) grid.append(empty(state.progress || state.q ? "没有符合筛选条件的分类。" : "还没有问题分类。检测时会为问题选择分类，同类问题会在这里汇总。"));
    const none = visible.find(([key]) => key === NONE);
    if (!none) return;
    const all = none[1];
    const node = previewButton("category", NONE, null, "dash-card dash-strip");
    node.dataset.key = NONE;
    if (isOpen("category", NONE)) node.setAttribute("aria-current", "true");
    node.append(el("strong", "未分类"), el("span", `${all.length} 个问题 · ${new Set(all.map(item => item.s.dir)).size} 个会话`), el("span", "相关会话下次检测时补充分类", "muted"));
    strip.append(node);
  }

  // --- drawer ---------------------------------------------------------------------------------------------------
  function issueRow(s, issue, withCategory) {
    const row = el("div", undefined, "issue-row");
    const dot = el("span", undefined, `progress-dot ${issue.progress}`); dot.title = LABEL.get(issue.progress);
    const href = `${s.href}#issue=${encodeURIComponent(issue.id)}${state.view === "category" && state.project ? `&project=${encodeURIComponent(state.project)}` : ""}`;
    const title = el("a", issue.title || issue.id, "row-title"); title.href = href; title.title = `${issue.id} ${issue.title}`;
    const jump = el("a", "↗", "row-jump"); jump.href = href; jump.setAttribute("aria-label", `在会话页打开 ${issue.id}`);
    // Title gets the full first line and may wrap; category and progress sit on their own line beneath it.
    const meta = el("div", undefined, "row-meta");
    meta.append(el("span", `关联项目：${projectText(issue)}`, "project-tag"));
    if (withCategory) meta.append(categoryChip(issue.category));
    meta.append(el("span", LABEL.get(issue.progress), "row-progress"));
    row.append(dot, el("span", shortIssue(issue.id), "issue-id-text"), title, jump, meta);
    return row;
  }
  function issueList(box, s, issues, withCategory) {
    const rows = issues.filter(issue => matchIssue(s, issue)).sort(byProgress);
    const list = el("div", undefined, "issue-list");
    if (!rows.length) list.append(el("div", issues.length ? "没有符合筛选条件的问题。" : "无保留问题（不代表会话正确）。", "empty"));
    for (const issue of rows) list.append(issueRow(s, issue, withCategory));
    box.append(list);
    return {shown: rows.length, total: issues.length};
  }
  const textDetails = (title, build) => { const node = el("details", undefined, "detail-section"); node.append(el("summary", title)); build(node); return node; };
  const bullets = (node, items) => { const list = el("ul"); for (const item of items) list.append(el("li", item)); node.append(list); };

  // The session drawer is a preview: the header carries identity and the way in; below it at most a few one-line
  // notes appear, and only when something makes the issue list untrustworthy or needs an action.
  function sessionNotes(s, box) {
    if (!s.detection) box.append(el("p", "尚未检测，问题列表为空不代表会话没有问题。", "warning"));
    else if (s.detection.status !== "completed") {
      box.append(el("p", `最近一次检测${statusText(s.detection.status)}，问题可能不全。`, "warning"));
    }
    if (s.problem_count) {
      const node = textDetails(`${s.problem_count} 条记录读取失败`, inner => {
        bullets(inner, s.problems);
        if (s.problem_count > s.problems.length) inner.append(el("p", `另有 ${s.problem_count - s.problems.length} 条未显示。`, "muted"));
      });
      node.classList.add("warning"); box.append(node);
    }
    if (!s.preview.exists || s.preview.stale) {
      const command = `atk visualize trace --session-id ${s.session_id} --overwrite --no-open`;
      const note = el("p", s.preview.exists ? "会话页已过期，不含最近的记录。" : "会话页尚未生成。", "warning note-row");
      const copy = copyButton(command, s.preview.exists ? "复制刷新命令" : "复制生成命令", s.preview.exists ? "复制刷新命令" : "复制生成命令");
      copy.title = command; note.append(copy); box.append(note);
    }
  }
  function sessionDrawer(s, box) {
    $("drawer-title").textContent = s.session_id;
    $("drawer-title-tools").append(copyButton(s.session_id, "复制 Session ID"));
    if (s.error) { box.append(el("p", `会话记录不可读取：${s.error}`, "warning")); return; }
    const open = el("a", "打开会话页 ↗", "link-button primary"); open.href = s.href;
    $("drawer-tools").append(open);
    sessionNotes(s, box);
    const heading = el("div", undefined, "list-heading");
    const label = el("span", "问题"); heading.append(label); box.append(heading);
    const counts = issueList(box, s, s.issues, true);
    label.textContent = counts.shown < counts.total ? `问题 ${counts.shown} / ${counts.total}（已按筛选）` : `问题 ${counts.total}`;
  }

  function categoryDrawer(key, box) {
    const items = categoryGroups().get(key) || [];
    const info = data.categories[key];
    $("drawer-title").textContent = key === NONE ? "未分类" : categoryName(key);
    const meta = el("div", undefined, "drawer-meta");
    if (key !== NONE) meta.append(el("span", key, "mono"));
    if (info?.deprecated) meta.append(flag("已弃用"));
    const matching = items.filter(({s, issue}) => matchIssue(s, issue));
    meta.append(el("span", `${new Set(matching.map(item => item.s.dir)).size} 个会话 · ${matching.length} 个问题`));
    box.append(meta);
    box.append(el("p", key === NONE ? "这些问题还没有分类。相关会话下次运行检测时会补充；分类只用于展示和导航，不影响诊断、修复和验证。" : info ? info.definition : "分类词表中没有这条分类，按分类 ID 显示。"));
    if (!items.length) { box.append(el("p", "这个分类下暂无问题。", "muted")); return; }
    const heading = el("div", undefined, "list-heading"); const label = el("span", "问题"); heading.append(label); box.append(heading);
    let shownCount = 0;
    for (const s of scoped()) {
      const mine = items.filter(item => item.s === s).map(item => item.issue);
      if (!mine.some(issue => matchIssue(s, issue))) continue;
      const group = el("div", undefined, "group-heading");
      const name = el("span", shortId(s.session_id), "mono"); name.title = s.session_id;
      const open = el("a", "打开会话页 ↗", "link-button"); open.href = s.href;
      group.append(name, open); box.append(group);
      shownCount += issueList(box, s, mine, false).shown;
    }
    label.textContent = shownCount < items.length ? `问题 ${shownCount} / ${items.length}（已按筛选）` : `问题 ${items.length}`;
    if (!shownCount) box.append(el("p", "没有符合筛选条件的问题。", "muted"));
  }

  function renderDrawer() {
    const drawer = $("drawer");
    if (state.drawer === null) { drawer.hidden = true; shown = null; return; }
    const session = state.view === "session" ? data.sessions.find(s => s.dir === state.drawer) : null;
    if (state.view === "session" && !session) { state.drawer = null; drawer.hidden = true; shown = null; return; }
    const id = `${state.view}:${state.drawer}`, fresh = shown !== id;
    const box = $("drawer-content"), keep = fresh ? 0 : box.scrollTop;
    box.replaceChildren(); $("drawer-title-tools").replaceChildren(); $("drawer-tools").replaceChildren();
    if (session) sessionDrawer(session, box); else categoryDrawer(state.drawer, box);
    drawer.hidden = false; box.scrollTop = keep; shown = id;
    if (fresh) $("drawer-close").focus({preventScroll: true});
  }
  function closeDrawer() {
    if (state.drawer === null) return;
    const key = state.drawer;
    state.drawer = null; syncHash(); render();
    const holder = [...document.querySelectorAll("[data-key]")].find(node => node.dataset.key === key);
    (holder?.querySelector(".card-open") || holder)?.focus({preventScroll: true});
  }

  // --- navigation -----------------------------------------------------------------------------------------------
  function syncHash() {
    const parts = [`view=${state.view}`, ...(state.view === "category" && state.project ? [`project=${encodeURIComponent(state.project)}`] : [])];
    if (state.drawer !== null) parts.push(`${state.view}=${encodeURIComponent(state.drawer)}`);
    const next = `#${parts.join("&")}`;
    if (location.hash !== next) history.replaceState(null, "", next);
  }
  function setView(view, drawer = null) {
    state.view = view; state.drawer = drawer;
    syncHash(); render();
  }
  function applyHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    const project = projectFrom(params);
    state.project = project;
    const view = params.get("view") === "category" || (project && !params.get("session")) ? "category" : "session";
    let drawer = null;
    if (view === "session") {
      const key = params.get("session");
      if (key && data.sessions.some(s => s.dir === key)) drawer = key;
    } else {
      const key = params.get("category");
      if (key && (key === NONE ? categoryGroups().has(NONE) : data.categories[key] || categoryGroups().has(key))) drawer = key;
    }
    state.view = view; state.drawer = drawer;
    render();
  }

  function render() {
    renderTabs(); renderSessionAttention(); renderProjects(); renderProgress(); renderNote();
    const grid = $("dash-grid"), strip = $("dash-strip");
    grid.replaceChildren(); strip.replaceChildren();
    if (state.view === "session") renderSessions(grid); else renderCategories(grid, strip);
    renderDrawer();
  }

  // --- start ----------------------------------------------------------------------------------------------------
  $("project-name").textContent = data.project;
  $("project-name").hidden = !data.project;
  const allIssues = data.sessions.reduce((sum, s) => sum + s.issues.length, 0);
  $("dash-stats").textContent = `${data.sessions.length} 会话 · ${allIssues} 问题 · 生成于 ${data.generated_at.slice(5, 16).replace("T", " ")}`;
  for (const text of data.warnings) $("warnings").append(el("p", text, "warning"));
  const sources = new Map();
  for (const s of data.sessions) if (s._source && !sources.has(s._source)) sources.set(s._source, s.source);
  if (sources.size > 1) {
    const base = source => sourceText(source);
    const seen = new Map([...sources.values()].map(source => [base(source), 0]));
    for (const source of sources.values()) seen.set(base(source), seen.get(base(source)) + 1);
    const select = $("source-filter");
    const all = el("option", "全部来源"); all.value = ""; select.append(all);
    for (const [key, source] of sources) {
      const option = el("option", seen.get(base(source)) > 1 ? `${base(source)} · ${source.project || source.instance}` : base(source));
      option.value = key; select.append(option);
    }
    $("source-choice").hidden = false;
    select.onchange = () => { state.source = select.value; render(); };
  }
  for (const [id, view] of TABS) {
    $(id).onclick = () => setView(view);
    $(id).onkeydown = onTabKey;
  }
  $("dash-search").oninput = event => { state.q = event.target.value.trim().toLowerCase(); render(); };
  $("drawer-close").onclick = closeDrawer;
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && state.drawer !== null) { event.preventDefault(); closeDrawer(); }
  });
  // Light dismiss: the drawer is a preview, so a click on empty page space closes it. Clicks inside the drawer, on a
  // card (including gaps between its chips) or on any control (filters, search, tabs, links) keep their own meaning;
  // cards switch the preview instead. The press must also start outside, so a text selection or a resize drag that
  // ends outside never closes it.
  const KEEPS_DRAWER = "#drawer, .dash-card, a, button, input, select, textarea, label, summary, [role=tab]";
  const dismissable = target => !target?.closest?.(KEEPS_DRAWER);
  let pressedOutside = false;
  document.addEventListener("pointerdown", event => { pressedOutside = state.drawer !== null && dismissable(event.target); });
  document.addEventListener("click", event => {
    const close = pressedOutside && state.drawer !== null && dismissable(event.target);
    pressedOutside = false;
    if (close) closeDrawer();
  });
  let resizing = false;
  $("drawer-resizer").addEventListener("pointerdown", event => { resizing = true; $("drawer-resizer").setPointerCapture(event.pointerId); event.preventDefault(); });
  window.addEventListener("pointermove", event => {
    if (!resizing) return;
    const width = Math.max(320, Math.min(window.innerWidth * 0.92, window.innerWidth - event.clientX));
    document.documentElement.style.setProperty("--drawer-width", `${width}px`);
  });
  window.addEventListener("pointerup", () => { resizing = false; });
  window.addEventListener("hashchange", applyHash);
  applyHash();
})();
