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
  const NONE = "~uncategorized"; // cannot collide with a kebab-case category ID
  const ORDER = new Map(data.progress_legend.map((entry, index) => [entry.key, index]));
  const LABEL = new Map(data.progress_legend.map(entry => [entry.key, entry.label]));
  const STATUS = {completed: "已完成", partial: "部分完成", failed: "失败", blocked: "受阻", interrupted: "已中断", running: "进行中"};
  const state = {view: "session", q: "", progress: "", source: "", drawer: null};
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
  const scaleText = s => s.traces != null && s.observations != null ? `${s.traces} Trace · ${s.observations} 观测` : `${s.files} 个归档文件`;
  const statusText = value => STATUS[value] || value || "未记录";

  // --- derived search text and source scope ---------------------------------------------------------------------
  const sourceKey = source => ["type", "instance", "project", "environment"].map(key => source?.[key] || "").join("\u0000");
  for (const s of data.sessions) {
    s._source = s.error ? "" : sourceKey(s.source);
    s._text = [s.session_id, s.dir, ...Object.values(s.source || {})].join("\n").toLowerCase();
    for (const issue of s.issues) {
      issue._text = [issue.id, issue.title, issue.category || "", issue.category ? categoryName(issue.category) : "未分类", LABEL.get(issue.progress)].join("\n").toLowerCase();
    }
  }
  const scoped = () => data.sessions.filter(s => !state.source || s._source === state.source);
  const matchIssue = (s, issue) =>
    (!state.progress || issue.progress === state.progress) && (!state.q || s._text.includes(state.q) || issue._text.includes(state.q));
  const matchSession = s => s.issues.some(issue => matchIssue(s, issue)) ||
    (!s.issues.length && !state.progress && (!state.q || s._text.includes(state.q)));
  const byProgress = (a, b) => ORDER.get(a.progress) - ORDER.get(b.progress) || a.id.localeCompare(b.id, undefined, {numeric: true});

  // --- small building blocks ------------------------------------------------------------------------------------
  const countBy = issues => { const counts = new Map(); for (const issue of issues) counts.set(issue.progress, (counts.get(issue.progress) || 0) + 1); return counts; };
  const topProgress = counts => [...counts].sort((a, b) => b[1] - a[1] || ORDER.get(a[0]) - ORDER.get(b[0])).slice(0, 2).map(([key, n]) => `${LABEL.get(key)} ${n}`).join(" · ");
  const miniBar = counts => {
    const bar = el("div", undefined, "mini-bar");
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
    chip.title = `查看“${categoryName(id)}”的同类异常`;
    return chip;
  };
  const empty = text => el("div", text, "empty-dash");

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
  function renderTabs() {
    for (const [id, view] of [["tab-session", "session"], ["tab-category", "category"]]) {
      const tab = $(id);
      tab.classList.toggle("active", state.view === view);
      tab.setAttribute("aria-selected", String(state.view === view));
    }
  }
  function renderProgress() {
    const counts = countBy(scoped().flatMap(s => s.issues));
    const total = [...counts.values()].reduce((sum, n) => sum + n, 0);
    const bar = $("progress-bar"), legend = $("progress-legend");
    bar.replaceChildren(); legend.replaceChildren();
    bar.setAttribute("aria-label", total ? data.progress_legend.filter(e => counts.get(e.key)).map(e => `${e.label} ${counts.get(e.key)}`).join("，") : "暂无问题");
    bar.hidden = !total;
    for (const {key, label} of data.progress_legend) {
      const n = counts.get(key);
      if (!n) continue;
      const segment = el("i"); segment.className = `tone-${key}`; segment.style.flex = String(n); segment.title = `${label} ${n}`;
      bar.append(segment);
      const item = button("", () => { state.progress = state.progress === key ? "" : key; render(); }, "legend-item");
      item.setAttribute("aria-pressed", String(state.progress === key));
      item.append(el("span", undefined, `swatch tone-${key}`), document.createTextNode(`${label} ${n}`));
      legend.append(item);
    }
    if (!total) legend.append(el("span", "尚无保留问题（不代表会话正确）", "muted"));
  }
  function renderNote() {
    const note = $("filter-note");
    note.replaceChildren();
    note.hidden = !state.progress;
    if (!state.progress) return;
    note.append(document.createTextNode(`仅显示实际进度为“${LABEL.get(state.progress)}”的问题`), button("清除筛选", () => { state.progress = ""; render(); }));
  }

  // --- session cards --------------------------------------------------------------------------------------------
  function sessionCard(s) {
    const card = el("article", undefined, "dash-card session-card");
    card.dataset.key = s.dir;
    if (state.view === "session" && state.drawer === s.dir) card.setAttribute("aria-current", "true");
    const head = el("div", undefined, "card-head");
    const open = button("", () => setView("session", s.dir), "card-open");
    const title = el("span", shortId(s.session_id), "card-title mono"); title.title = s.session_id;
    open.append(title); open.setAttribute("aria-label", `查看会话 ${s.session_id}`);
    head.append(open, copyButton(s.session_id, "复制 Session ID"));
    card.append(head);
    if (s.error) {
      card.append(el("div", undefined, "chips")); card.lastChild.append(flag("记录不可读取", true));
      card.append(el("div", s.error, "card-definition"));
      return card;
    }
    card.append(el("div", `${sourceText(s.source)} · 更新 ${when(s.updated_ms)}`, "card-meta"));
    if (s.issues.length) {
      const counts = countBy(s.issues);
      const row = el("div", undefined, "card-count");
      row.append(el("strong", `${s.issues.length} 个问题`), el("span", topProgress(counts), "muted"));
      card.append(row, miniBar(counts));
    } else card.append(el("div", "无保留问题（不代表会话正确）", "card-count muted"));
    const perCategory = new Map(); let none = 0;
    for (const issue of s.issues) issue.category ? perCategory.set(issue.category, (perCategory.get(issue.category) || 0) + 1) : none++;
    const entries = [...perCategory].sort((a, b) => b[1] - a[1] || categoryName(a[0]).localeCompare(categoryName(b[0])));
    if (none) entries.push([NONE, none]);
    if (entries.length) {
      const chips = el("div", undefined, "chips");
      for (const [id, n] of entries.slice(0, 3)) {
        if (id === NONE) { chips.append(el("span", `未分类 ${n}`, "category-tag plain")); continue; }
        const chip = button(`${categoryName(id)} ${n}`, () => setView("category", id), "category-tag");
        chip.title = `查看“${categoryName(id)}”的同类异常`; chips.append(chip);
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
    if (state.view === "category" && state.drawer === key) card.setAttribute("aria-current", "true");
    const head = el("div", undefined, "card-head");
    const open = button("", () => setView("category", key), "card-open");
    const title = el("span", categoryName(key), "card-title"); title.title = categoryName(key);
    open.append(title); open.setAttribute("aria-label", `查看异常分类 ${categoryName(key)}`);
    head.append(open);
    if (info?.deprecated) head.append(flag("已弃用"));
    card.append(head, el("div", info ? info.definition : "分类词表中没有这条分类，按分类 ID 显示。", "card-definition"));
    const counts = countBy(items.map(item => item.issue));
    const row = el("div", undefined, "card-count");
    row.append(el("strong", `${new Set(items.map(item => item.s.dir)).size} 个会话 · ${items.length} 个问题`), el("span", `最近 ${when(latestOf(items) < 0 ? null : latestOf(items))}`, "muted"));
    card.append(row, miniBar(counts), el("div", topProgress(counts), "card-meta"));
    return card;
  }
  function renderCategories(grid, strip) {
    const visible = [...categoryGroups()].filter(([, items]) => items.some(({s, issue}) => matchIssue(s, issue)));
    const real = visible.filter(([key]) => key !== NONE).sort((a, b) => {
      const sessions = items => new Set(items.map(item => item.s.dir)).size;
      return Number(Boolean(data.categories[a[0]]?.deprecated)) - Number(Boolean(data.categories[b[0]]?.deprecated)) ||
        sessions(b[1]) - sessions(a[1]) || b[1].length - a[1].length || latestOf(b[1]) - latestOf(a[1]) ||
        categoryName(a[0]).localeCompare(categoryName(b[0]));
    });
    for (const [key, items] of real) grid.append(categoryCard(key, items));
    if (!real.length) grid.append(empty(state.progress || state.q ? "没有符合筛选条件的分类。" : "还没有异常分类。检测时会为问题选择分类，同类异常会在这里汇总。"));
    const none = visible.find(([key]) => key === NONE);
    if (!none) return;
    const all = categoryGroups().get(NONE);
    const node = button("", () => setView("category", NONE), "dash-card dash-strip");
    node.dataset.key = NONE;
    if (state.view === "category" && state.drawer === NONE) node.setAttribute("aria-current", "true");
    node.append(el("strong", "未分类"), el("span", `${all.length} 个问题 · ${new Set(all.map(item => item.s.dir)).size} 个会话`), el("span", "相关会话下次检测时补充分类", "muted"));
    strip.append(node);
  }

  // --- drawer ---------------------------------------------------------------------------------------------------
  function issueRow(s, issue, withCategory) {
    const row = el("div", undefined, "issue-row");
    const dot = el("span", undefined, `progress-dot ${issue.progress}`); dot.title = LABEL.get(issue.progress);
    const href = `${s.href}#issue=${encodeURIComponent(issue.id)}`;
    const title = el("a", issue.title || issue.id, "row-title"); title.href = href; title.title = `${issue.id} ${issue.title}`;
    const jump = el("a", "↗", "row-jump"); jump.href = href; jump.setAttribute("aria-label", `在会话页打开 ${issue.id}`);
    // Title gets the full first line and may wrap; category and progress sit on their own line beneath it.
    const meta = el("div", undefined, "row-meta");
    if (withCategory) meta.append(categoryChip(issue.category));
    meta.append(el("span", LABEL.get(issue.progress), "row-progress"));
    row.append(dot, el("span", shortIssue(issue.id), "issue-id-text"), title, jump, meta);
    return row;
  }
  function issueList(box, s, issues, withCategory) {
    const rows = issues.filter(issue => matchIssue(s, issue)).sort(byProgress);
    const list = el("div", undefined, "issue-list");
    if (!rows.length) list.append(el("div", issues.length ? "没有符合筛选条件的问题。" : "无保留问题。", "empty"));
    for (const issue of rows) list.append(issueRow(s, issue, withCategory));
    box.append(list);
    return {shown: rows.length, total: issues.length};
  }
  const textDetails = (title, build) => { const node = el("details", undefined, "detail-section"); node.append(el("summary", title)); build(node); return node; };
  const bullets = (node, items) => { const list = el("ul"); for (const item of items) list.append(el("li", item)); node.append(list); };

  function sessionDrawer(s, box) {
    $("drawer-title").textContent = s.session_id;
    const actions = el("div", undefined, "drawer-actions");
    actions.append(copyButton(s.session_id, "复制 Session ID", "复制 Session ID"));
    if (!s.error) { const open = el("a", "打开会话页 ↗", "link-button"); open.href = s.href; actions.append(open); }
    box.append(actions);
    if (s.error) { box.append(el("p", `会话记录不可读取：${s.error}`, "warning")); return; }
    const meta = el("div", undefined, "drawer-meta");
    for (const text of [sourceText(s.source), scaleText(s), `归档 ${when(s.archived_ms)}`, `更新 ${when(s.updated_ms)}`]) meta.append(el("span", text));
    box.append(meta);
    const origin = [s.source.instance, s.source.project].filter(Boolean).join(" / ");
    if (origin) box.append(el("p", origin, "mono muted"));
    if (!s.preview.exists || s.preview.stale) {
      const note = el("p", s.preview.exists ? "会话页早于最近一次记录写入。刷新命令：" : "会话页尚未生成。生成命令：", "warning");
      note.append(el("code", `atk visualize trace --session-id ${s.session_id} --overwrite --no-open`));
      box.append(note);
    }
    if (s.problem_count) {
      box.append(textDetails(`记录读取失败 ${s.problem_count}`, node => {
        bullets(node, s.problems);
        if (s.problem_count > s.problems.length) node.append(el("p", `另有 ${s.problem_count - s.problems.length} 条未显示。`, "muted"));
      }));
    }
    box.append(textDetails("检测范围与缺口", node => {
      if (s.detection) {
        node.append(el("p", `检测状态：${statusText(s.detection.status)}`));
        if (s.detection.focus) node.append(el("p", `关注点：${s.detection.focus}`));
        if (s.detection.gaps.length) { node.append(el("p", "检测缺口：")); bullets(node, s.detection.gaps); }
      } else node.append(el("p", "尚未记录检测。", "muted"));
      const coverage = {complete: "完整", partial: "部分", unknown: "未确定"}[s.capture.completeness] || s.capture.completeness || "未记录";
      node.append(el("p", `采集状态：${statusText(s.capture.status)} · 完整性：${coverage}`));
      if (s.capture.gaps.length) { node.append(el("p", "采集缺口：")); bullets(node, s.capture.gaps); }
      if (!s.issues.length) node.append(el("p", "没有保留问题不代表会话正确，请结合检测范围与缺口判断。", "muted"));
    }));
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
    meta.append(el("span", `${new Set(items.map(item => item.s.dir)).size} 个会话 · ${items.length} 个问题`));
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
    box.replaceChildren();
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
    const parts = [`view=${state.view}`];
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
    const view = params.get("view") === "category" ? "category" : "session";
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
    renderTabs(); renderProgress(); renderNote();
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
  $("tab-session").onclick = () => setView("session");
  $("tab-category").onclick = () => setView("category");
  $("dash-search").oninput = event => { state.q = event.target.value.trim().toLowerCase(); render(); };
  $("drawer-close").onclick = closeDrawer;
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && state.drawer !== null) { event.preventDefault(); closeDrawer(); }
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
