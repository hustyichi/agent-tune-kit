// Dependency-free behavior checks for the dashboard script; this does not test browser layout.
// Usage: node trace-dashboard.cjs <dashboard-data.json>, where the JSON is the generator's real page data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = ["shared/project-menu.js", "traces/app.js"].map(file =>
  fs.readFileSync(path.join(__dirname, "../agent_tune_kit/visualize/assets", file), "utf8")).join("\n");
const base = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ""; this.className = ""; this.dataset = {}; this.attrs = {}; this.style = {}; this.hidden = false; }
  get options() { return this.children; }
  get selectedOptions() { return this.children.filter(node => node.selected); }
  get lastChild() { return this.children[this.children.length - 1]; }
  append(...items) {
    for (const item of items) {
      const node = typeof item === "string" ? Object.assign(new Node("#text"), {textContent: item}) : item;
      node.parentElement = this; this.children.push(node);
    }
  }
  // Enough of Element.closest for the dashboard's light-dismiss check: "#id", ".class", tag names and [role=...].
  closest(selector) {
    const parts = selector.split(",").map(part => part.trim());
    const hit = node => parts.some(part => part.startsWith("#") ? node.id === part.slice(1)
      : part.startsWith(".") ? node.className.split(/\s+/).includes(part.slice(1))
      : part.startsWith("[role=") ? node.attrs.role === part.slice(6, -1) : node.tag === part);
    for (let node = this; node; node = node.parentElement) if (hit(node)) return node;
    return null;
  }
  replaceChildren(...items) { this.children = []; this.append(...items); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name] ?? null; }
  focus() { this.focused = true; }
  addEventListener(name, fn) { (this.handlers ??= {})[name] = fn; }
  setPointerCapture() {}
  get classList() {
    const node = this, tokens = () => node.className.split(/\s+/).filter(Boolean);
    const write = list => { node.className = list.join(" "); };
    return {
      add: name => write([...new Set([...tokens(), name])]),
      remove: name => write(tokens().filter(item => item !== name)),
      contains: name => tokens().includes(name),
      toggle: (name, force) => { const on = force ?? !tokens().includes(name); write(on ? [...new Set([...tokens(), name])] : tokens().filter(item => item !== name)); return on; },
    };
  }
  set innerHTML(_) { throw new Error("innerHTML must never be used: record text has to stay data"); }
  querySelector(selector) { return all(this).find(node => matches(node, selector)) || null; }
}
const all = node => [node, ...node.children.flatMap(all)];
const matches = (node, selector) => selector.startsWith(".") ? node.className.split(/\s+/).includes(selector.slice(1)) : selector === "[data-key]" ? node.dataset.key !== undefined : false;
const text = node => [node.textContent, ...node.children.map(text)].join(" ").replace(/\s+/g, " ").trim();

function boot(data) {
  const ids = new Map();
  for (const id of ["project-choice", "project-summary", "project-clear", "project-filter", "project-name", "dash-stats", "warnings", "source-choice", "source-filter", "tab-session", "tab-category", "dash-panel", "dash-search", "session-attention", "progress-legend", "filter-note", "dash-grid", "dash-strip", "drawer", "drawer-title", "drawer-title-tools", "drawer-tools", "drawer-content", "drawer-close", "drawer-resizer"]) {
    ids.set(id, Object.assign(new Node("div"), {id}));
  }
  for (const id of ["drawer-title", "drawer-title-tools", "drawer-tools", "drawer-content", "drawer-close", "drawer-resizer"]) ids.get(id).parentElement = ids.get("drawer");
  for (const id of ["project-summary", "project-clear", "project-filter"]) ids.get(id).parentElement = ids.get("project-choice");
  for (const id of ["dash-grid", "dash-strip", "session-attention", "progress-legend", "filter-note"]) ids.get(id).parentElement = ids.get("dash-panel");
  ids.get("dash-search").tag = "input";
  ids.get("source-choice").hidden = true;
  ids.get("drawer").hidden = true;
  const store = new Node("script"); store.textContent = JSON.stringify(data); ids.set("dashboard-data", store);
  const listeners = {document: {}, window: {}};
  const location = {hash: ""};
  const document = {
    getElementById: id => { assert.ok(ids.has(id), `unknown element id ${id}`); return ids.get(id); },
    createElement: tag => new Node(tag),
    createTextNode: value => Object.assign(new Node("#text"), {textContent: value}),
    querySelectorAll: selector => [...ids.values()].flatMap(all).filter(node => matches(node, selector)),
    addEventListener: (name, fn) => { (listeners.document[name] ??= []).push(fn); },
    documentElement: {style: {setProperty() {}}},
    body: new Node("body"),
  };
  const context = vm.createContext({
    document, location, Intl, JSON, Map, Set, Promise, Object, Array, Number, String, Boolean, Date, console,
    history: {replaceState: (_s, _t, next) => { location.hash = next; }},
    navigator: {clipboard: {writeText: async value => { context.copied = value; }}},
    window: {innerWidth: 1280, addEventListener: (name, fn) => { listeners.window[name] = fn; }},
    setTimeout: () => 0,
    URLSearchParams,
    copied: null,
  });
  vm.runInContext(source, context);
  const $ = id => ids.get(id);
  const dispatch = (name, event) => {
    let stopped = false;
    Object.assign(event, {preventDefault() {}, stopPropagation() { stopped = true; }});
    for (const listener of listeners.document[name]) { listener(event); if (stopped) break; }
  };
  return {
    $, location, context,
    cards: () => $("dash-grid").children,
    strip: () => $("dash-strip").children,
    rows: () => all($("drawer-content")).filter(node => matches(node, ".issue-row")),
    open: card => all(card).find(node => matches(node, ".card-open")),
    legend: label => all($("progress-legend")).find(node => node.className.includes("legend-item") && text(node).startsWith(label)),
    search: value => $("dash-search").oninput({target: {value}}),
    go: hash => { location.hash = hash; listeners.window.hashchange(); },
    escape: () => dispatch("keydown", {key: "Escape"}),
    // A mouse click as the document sees it: the press lands on `down`, the click on `up` (the same node by default).
    press: (down, up = down) => { dispatch("pointerdown", {target: down}); dispatch("click", {target: up}); },
  };
}

const selected = id => app.$(id).getAttribute("aria-selected") === "true";
const byDir = (data, id) => data.sessions.find(s => s.session_id === id);
const a = byDir(base, "session-a"), b = byDir(base, "session-b");
const shellId = Object.keys(base.categories)[0];
const shellName = base.categories[shellId].name;

// --- default view, cards and progress ----------------------------------------------------------------------
let app = boot(base);
assert.equal(selected("tab-session"), true, "opens on the session view");
assert.equal(selected("tab-category"), false);
assert.equal(app.$("tab-session").tabIndex, 0, "only the selected tab is in the tab order");
assert.equal(app.$("tab-category").tabIndex, -1);
assert.equal(app.$("dash-panel").getAttribute("aria-labelledby"), "tab-session");
assert.equal(app.$("source-choice").hidden, true, "a single source needs no source filter");
assert.equal(app.cards().length, 2);
assert.equal(app.location.hash, "", "an empty hash already means the default view, so first load leaves the URL alone");
const sessionText = card => text(card);
assert.ok(app.cards().some(card => sessionText(card).includes("3 个问题")));
assert.ok(app.cards().some(card => sessionText(card).includes("1 个问题")));
assert.ok(app.cards().every(card => sessionText(card).includes("local · test")), "source type and environment");
assert.equal(app.$("progress-legend").hidden, true);
assert.equal(app.$("filter-note").hidden, true, "no redundant summary before filtering");
assert.match(text(app.$("session-attention")), /1 个会话存在验证失败的问题/);

// --- issue filters stay in their own view and counts follow the visible rows -----------------------------
app.$("tab-category").onclick();
assert.equal(app.$("session-attention").hidden, true);
assert.equal(app.$("progress-legend").hidden, false);
assert.match(text(app.$("progress-legend")), /处理状态 全部 4/);
assert.match(text(app.$("progress-legend")), /待诊断 1[\s\S]*已诊断 \d[\s\S]*验证失败 1[\s\S]*验证通过 1/);
assert.doesNotMatch(text(app.$("progress-legend")), /修复中|已排除|无法判断/, "empty progress levels are not listed");
app.legend("待诊断").onclick();
assert.match(text(app.strip()[0]), /1 个问题 · 1 个会话/);
app.strip()[0].onclick();
assert.equal(app.rows().length, 1);
assert.match(text(app.$("drawer-content")), /1 个会话 · 1 个问题/);
assert.equal(app.$("filter-note").hidden, false);
assert.equal(app.legend("待诊断").attrs["aria-pressed"], "true");
app.$("tab-session").onclick();
assert.equal(app.cards().length, 2);
assert.equal(app.$("progress-legend").hidden, true);
assert.equal(app.$("filter-note").hidden, true);
app.$("tab-category").onclick();
assert.equal(app.legend("待诊断").attrs["aria-pressed"], "true", "issue selection is remembered");
app.legend("全部").onclick();
assert.equal(app.$("filter-note").hidden, true);
app.$("tab-session").onclick();

// --- search matches ids, titles and category names ---------------------------------------------------------
app.search("fails verification");
assert.equal(app.cards().length, 1);
app.search(shellName.toLowerCase());
assert.equal(app.cards().length, 2, "category names are searchable");
app.search("nothing-matches-this");
assert.equal(app.cards().length, 1);
assert.match(text(app.cards()[0]), /没有符合筛选条件的会话/);
app.search("");

// --- session drawer: ordered issues, links, hostile text as data -------------------------------------------
app.open(app.cards().find(card => text(card).includes("3 个问题"))).onclick();
assert.equal(app.$("drawer").hidden, false);
assert.equal(app.$("drawer-title").textContent, a.session_id);
assert.equal(app.location.hash, `#view=session&session=${encodeURIComponent(a.dir)}`);
const rows = app.rows();
assert.equal(rows.length, 3);
const rank = new Map(base.progress_legend.map((entry, index) => [entry.label, index]));
const labels = rows.map(row => text(row).match(/待诊断|已诊断|验证通过|验证失败/)[0]);
assert.deepEqual([...labels].sort((x, y) => rank.get(x) - rank.get(y)), labels, "unfinished work comes first");
for (const row of rows) {
  const link = all(row).find(node => node.tag === "a" && node.className.includes("row-title"));
  assert.match(link.href, new RegExp(`^\\./${a.dir.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/preview\\.html#issue=ISSUE-00[123]$`));
  assert.equal(all(row).filter(node => node.tag === "a").length, 2, "title and the arrow both open the issue");
}
assert.ok(rows.some(row => text(row).includes("</script><img src=x onerror=alert(1)>")), "hostile title shown literally");
assert.ok(!all(app.$("drawer-content")).some(node => node.tag === "img"), "no element is built from record text");
// The header carries only identity and the way in: a copy icon by the title, the session page link by close.
assert.ok(all(app.$("drawer-tools")).some(node => node.tag === "a" && node.textContent.startsWith("打开会话页") && node.href === a.href));
all(app.$("drawer-title-tools")).find(node => node.className.includes("icon-button")).onclick();
assert.equal(app.context.copied, a.session_id);
// Source, scale, archive time and coverage details stay on the session page, not in this preview.
assert.doesNotMatch(text(app.$("drawer-content")), /检测范围与缺口|观测|归档|采集状态|fixture/);
assert.match(text(app.$("drawer-content")), /^尚未检测，问题列表为空不代表会话没有问题/, "no detection recorded yet is the only note");
app.escape();
assert.equal(app.$("drawer").hidden, true);
assert.equal(app.location.hash, "#view=session");

// --- the dashboard drawer is a preview: light dismiss, toggle and switch -----------------------------------
const cardOf = label => app.cards().find(card => text(card).includes(label));
app.open(cardOf("3 个问题")).onclick();
assert.equal(app.open(cardOf("3 个问题")).getAttribute("aria-expanded"), "true");
assert.equal(app.open(cardOf("1 个问题")).getAttribute("aria-expanded"), "false");
app.press(app.$("drawer-content"));
assert.equal(app.$("drawer").hidden, false, "a click inside the drawer keeps it open");
app.press(app.$("drawer-content"), app.$("dash-panel"));
assert.equal(app.$("drawer").hidden, false, "a press that starts in the drawer (text selection, resize) keeps it open");
app.press(app.$("dash-search"));
assert.equal(app.$("drawer").hidden, false, "a click on a search control keeps it open");
app.press(all(cardOf("1 个问题")).find(node => matches(node, ".chips")));
assert.equal(app.$("drawer-title").textContent, a.session_id, "a click in a card's chip gap is not empty space");
app.open(cardOf("1 个问题")).onclick();
assert.equal(app.$("drawer-title").textContent, b.session_id, "another card switches the preview without closing");
app.open(cardOf("1 个问题")).onclick();
assert.equal(app.$("drawer").hidden, true, "pressing the open card again closes its preview");
assert.equal(app.location.hash, "#view=session");
app.open(cardOf("3 个问题")).onclick();
app.press(app.$("dash-grid"));
assert.equal(app.$("drawer").hidden, true, "a click on empty page space closes the preview");
assert.equal(app.location.hash, "#view=session");
app.press(app.$("dash-grid"));
assert.equal(app.$("drawer").hidden, true, "an empty-space click with nothing open is a no-op");

// --- search narrows the session drawer and says so ---------------------------------------------------------
app.open(app.cards().find(card => text(card).includes("3 个问题"))).onclick();
app.search("diagnosed only");
assert.equal(app.rows().length, 1);
assert.match(text(app.$("drawer-content")), /问题 1 \/ 3（已按筛选）/);
assert.doesNotMatch(text(app.cards()[0]), /未分类/, "category chips follow the same search as the issue count");
app.search("");
app.escape();

// --- category chip jumps to the category view and opens its drawer -----------------------------------------
const chip = all(app.cards().find(card => text(card).includes(shellName))).find(node => node.tag === "button" && node.className.includes("category-tag"));
chip.onclick();
assert.equal(selected("tab-category"), true);
assert.equal(app.$("drawer-title").textContent, shellName);
assert.equal(app.location.hash, `#view=category&category=${encodeURIComponent(shellId)}`);
app.escape();

// --- category view -------------------------------------------------------------------------------------------
app.$("tab-category").onclick();
assert.equal(app.location.hash, "#view=category");
assert.equal(app.cards().length, 1);
assert.match(text(app.cards()[0]), new RegExp(`${shellName}[\\s\\S]*2 个会话 · 2 个问题`));
assert.equal(app.strip().length, 1);
assert.match(text(app.strip()[0]), /未分类[\s\S]*2 个问题[\s\S]*下次检测时补充/);
app.open(app.cards()[0]).onclick();
assert.equal(app.$("drawer-title").textContent, shellName);
assert.equal(app.rows().length, 2);
assert.ok(app.rows().every(row => !all(row).some(node => node.className.includes("category-tag"))), "no repeated category chip inside its own drawer");
const groups = all(app.$("drawer-content")).filter(node => node.className.includes("group-heading"));
assert.equal(groups.length, 2, "issues are grouped per session");
app.legend("验证失败").onclick();
assert.equal(app.rows().length, 1);
assert.match(text(app.$("drawer-content")), /1 个会话 · 1 个问题/);
app.legend("验证失败").onclick();
app.escape();
assert.equal(app.strip()[0].tag, "button", "the uncategorized strip is itself the control");
app.strip()[0].onclick();
assert.equal(app.$("drawer-title").textContent, "未分类");
assert.equal(app.rows().length, 2);
assert.equal(app.strip()[0].getAttribute("aria-expanded"), "true");
app.strip()[0].onclick();
assert.equal(app.$("drawer").hidden, true, "the uncategorized strip toggles its preview like a card");
app.open(app.cards()[0]).onclick();
app.open(app.cards()[0]).onclick();
assert.equal(app.$("drawer").hidden, true, "a category card toggles its preview");
assert.equal(app.location.hash, "#view=category");

// --- deep links ------------------------------------------------------------------------------------------------
app.go(`#view=category&category=${shellId}`);
assert.equal(app.$("drawer").hidden, false);
assert.equal(app.$("drawer-title").textContent, shellName);
app.go("#view=category&category=~uncategorized");
assert.equal(app.$("drawer-title").textContent, "未分类");
app.go("#view=session&session=does-not-exist");
assert.equal(app.$("drawer").hidden, true);
assert.equal(selected("tab-session"), true);
app.go("#view=category&category=made-up");
assert.equal(app.$("drawer").hidden, true, "an unknown category does not open an empty drawer");
app.go(`#view=session&session=${encodeURIComponent(b.dir)}`);
assert.equal(app.$("drawer-title").textContent, b.session_id);
app.go("");
assert.equal(app.$("drawer").hidden, true);
assert.equal(selected("tab-session"), true, "no hash means the default session view");

// --- copy ------------------------------------------------------------------------------------------------------
app = boot(base);
const first = app.cards()[0];
all(first).find(node => node.className.includes("icon-button")).onclick();
// The click hands the text to the clipboard before its first await, so no deferred assertion is needed.
assert.equal(app.context.copied, byDir(base, first.dataset.key === a.dir ? "session-a" : "session-b").session_id);

// --- several sources add a source filter ---------------------------------------------------------------------
const split = JSON.parse(JSON.stringify(base));
split.sessions.find(s => s.session_id === "session-b").source.environment = "prod";
app = boot(split);
assert.equal(app.$("source-choice").hidden, false);
assert.equal(app.$("source-filter").children.length, 3, "all sources plus one per source");
const option = app.$("source-filter").children.find(node => text(node).includes("prod"));
app.$("source-filter").value = option.value;
app.$("source-filter").onchange();
assert.equal(app.cards().length, 1);
assert.match(text(app.$("dash-stats")), /2 会话/, "header totals stay project-wide");
assert.match(text(app.$("session-attention")), /1 个会话存在验证失败的问题/);
app.$("tab-category").onclick();
assert.equal(text(app.$("progress-legend")).includes("验证失败 1"), true);
assert.equal(text(app.$("progress-legend")).includes("验证通过"), false, "the legend follows the source scope");

// --- unreadable sessions and empty sessions stay visible -----------------------------------------------------
const odd = JSON.parse(JSON.stringify(base));
odd.sessions.push({dir: "broken", href: "./broken/preview.html", session_id: "broken", error: "Missing or damaged Trace record: session.json", source: {}, issues: [], problems: [], problem_count: 0, preview: {exists: false, stale: false}});
odd.sessions.push({...JSON.parse(JSON.stringify(a)), dir: "quiet", href: "./quiet/preview.html", session_id: "quiet", issues: [], preview: {exists: false, stale: true}, problem_count: 2, problems: ["ISSUE-009: bad"]});
app = boot(odd);
assert.equal(app.cards().length, 4);
assert.match(text(app.$("session-attention")), /2 个会话有记录不可读取/);
// A saved issue filter must not hide empty or damaged sessions after switching back.
app.$("tab-category").onclick();
app.legend("待诊断").onclick();
app.$("tab-session").onclick();
assert.equal(app.cards().length, 4);
app.search("quiet");
assert.deepEqual(app.cards().map(card => card.dataset.key), ["quiet"]);
assert.match(text(app.$("session-attention")), /1 个会话有记录不可读取/);
app.search("");
assert.equal(app.cards().length, 4);
const broken = app.cards().find(card => card.dataset.key === "broken");
assert.match(text(broken), /记录不可读取[\s\S]*Missing or damaged/);
const quiet = app.cards().find(card => card.dataset.key === "quiet");
assert.match(text(quiet), /无保留问题（不代表会话正确）[\s\S]*预览缺失[\s\S]*2 条记录读取失败/);
app.open(broken).onclick();
assert.match(text(app.$("drawer-content")), /会话记录不可读取/);
assert.ok(!all(app.$("drawer")).some(node => node.textContent.startsWith("打开会话页")), "no link to a page that cannot exist");
assert.ok(all(app.$("drawer-title-tools")).some(node => node.className.includes("icon-button")), "the ID can still be copied");
app.escape();
app.open(quiet).onclick();
assert.equal(all(app.$("drawer-tools")).filter(node => node.tag === "a").length, 1, "switching sessions does not stack header links");
assert.match(text(app.$("drawer-content")), /会话页尚未生成/);
const regenerate = all(app.$("drawer-content")).find(node => node.tag === "button" && node.textContent === "复制生成命令");
regenerate.onclick();
assert.equal(app.context.copied, "atk visualize trace --session-id quiet --overwrite --no-open");
assert.match(text(app.$("drawer-content")), /2 条记录读取失败/);
assert.match(text(app.$("drawer-content")), /无保留问题（不代表会话正确）/);
app.escape();
// A finished detection adds no note; any other status warns that the list may be incomplete.
const checked = JSON.parse(JSON.stringify(base));
checked.sessions.find(s => s.session_id === "session-a").detection = {status: "completed"};
checked.sessions.find(s => s.session_id === "session-b").detection = {status: "partial"};
app = boot(checked);
app.go(`#view=session&session=${encodeURIComponent(a.dir)}`);
assert.equal(all(app.$("drawer-content")).filter(node => node.className.includes("warning")).length, 0, "a complete, fresh session shows no notes");
app.go(`#view=session&session=${encodeURIComponent(b.dir)}`);
assert.match(text(app.$("drawer-content")), /最近一次检测部分完成，问题可能不全/);
app = boot(odd);
app.open(app.cards().find(card => card.dataset.key === "quiet")).onclick();
assert.equal(app.rows().length, 0);

// --- tab keyboard: arrows wrap and activate, Home/End jump, other keys pass through -------------------------
app = boot(base);
const key = (id, name) => { let prevented = false; app.$(id).onkeydown({key: name, preventDefault() { prevented = true; }}); return prevented; };
assert.equal(key("tab-session", "ArrowRight"), true);
assert.equal(selected("tab-category"), true);
assert.equal(app.$("tab-category").focused, true, "focus follows the activated tab");
assert.equal(app.$("dash-panel").getAttribute("aria-labelledby"), "tab-category");
assert.equal(app.location.hash, "#view=category");
key("tab-category", "ArrowRight");
assert.equal(selected("tab-session"), true, "ArrowRight wraps back to the first tab");
key("tab-session", "ArrowLeft");
assert.equal(selected("tab-category"), true, "ArrowLeft wraps to the last tab");
key("tab-category", "Home");
assert.equal(selected("tab-session"), true);
key("tab-session", "End");
assert.equal(selected("tab-category"), true);
assert.equal(key("tab-category", "Tab"), false, "Tab leaves the tablist untouched");
app.$("tab-session").onclick();
assert.equal(selected("tab-session"), true);
assert.equal(app.$("tab-session").tabIndex, 0, "clicking moves the tab stop back");

// --- empty project -----------------------------------------------------------------------------------------------
const nothing = JSON.parse(JSON.stringify(base));
for (const s of nothing.sessions) s.issues = [];
app = boot(nothing);
assert.equal(app.$("session-attention").hidden, true);
assert.equal(app.$("progress-legend").hidden, true);
app.$("tab-category").onclick();
assert.match(text(app.$("progress-legend")), /全部 0/);
assert.match(text(app.cards()[0]), /还没有问题分类/);
assert.equal(app.strip().length, 0);

console.log("Dashboard rendering: views, filters, drawers, deep links, sources, unreadable and empty sessions passed.");

const attributed = JSON.parse(JSON.stringify(base));
attributed.projects = {ape: {id: "ape", name: "APE"}, ot: {id: "ot", name: "OT"}};
for (const session of attributed.sessions) for (const issue of session.issues) issue.linked_projects = [{id: "ape", name: "APE"}];
app = boot(attributed);
app.go("#view=session&project=ot");
assert.equal(app.rows().length, 0);
assert.equal(app.cards().filter(card => card.dataset.key).length, 0);
app.go("#view=session&project=ape");
assert.ok(app.cards().length > 0);
assert.match(text(app.cards()[0]), /APE/);
assert.equal(selected("tab-category"), true, "legacy project links open issue grouping");
app.$("tab-session").onclick();
assert.equal(app.cards().length, base.sessions.length);
assert.equal(app.$("project-choice").hidden, true);
assert.ok(app.cards().every(card => !text(card).includes("关联项目")));
assert.equal(app.location.hash, "#view=session");
app.$("tab-category").onclick();
assert.equal(app.$("project-choice").hidden, false);
assert.match(app.location.hash, /project=ape/);
app.$("project-clear").onclick();
assert.doesNotMatch(app.location.hash, /project=/);
assert.equal(app.$("project-summary").textContent, "关联项目：全部");

// Different issues in one session can have different projects or remain unattributed.
const mixed = JSON.parse(JSON.stringify(base));
mixed.projects = {
  workspace: {id: "workspace", name: "Workspace"},
  a: {id: "a", name: "Alpha", parent_id: "workspace"},
  b: {id: "b", name: "Beta", parent_id: "a"},
};
mixed.sessions[0].issues[0].linked_projects = [{id: "a", name: "Alpha"}];
mixed.sessions[0].issues[1].linked_projects = [{id: "b", name: "Beta"}];
mixed.sessions[0].issues[2].linked_projects = [];
app = boot(mixed);
app.go("#view=category&project=workspace");
assert.match(text(app.$("filter-note")), /0 个会话 · 0 个问题/, "ancestors do not inherit project attribution");
assert.equal(app.cards().filter(card => card.dataset.key).length, 0);
assert.equal(app.strip().length, 0);
app.go("#view=category&project=b");
assert.match(text(app.$("filter-note")), /1 个会话 · 1 个问题/);
assert.match(text(app.cards()[0]), /关联项目：Beta/);
app.go("#view=category&project=a&project=b");
assert.match(text(app.$("filter-note")), /1 个会话 · 2 个问题/);
app.go("#view=category&project=a");
assert.match(text(app.$("filter-note")), /1 个会话 · 1 个问题/, "a parent matches only its own directly associated issue");
app.$("tab-session").onclick();
assert.equal(app.$("filter-note").hidden, true);
assert.equal(app.cards().length, 2);
app.open(app.cards()[0]).onclick();
assert.equal(app.rows().length, 3);
assert.ok(app.rows().every(row => all(row).filter(n => n.tag === "a").every(n => !n.href.includes("&project="))));
app.go("#view=category&project=~unattributed");
assert.match(text(app.$("filter-note")), /2 个会话 · 2 个问题/);
const betaInput = app.$("project-filter").children.find(label => label.children[0].value === "b").children[0];
app.$("project-clear").onclick();
betaInput.checked = true;
betaInput.onchange();
assert.match(app.location.hash, /project=b/);
assert.match(text(app.$("filter-note")), /1 个会话 · 1 个问题/);
assert.equal(app.$("project-summary").textContent, "关联项目：已选 1 项");
app.$("project-choice").open = true;
app.escape();
assert.equal(app.$("project-choice").open, false);
assert.equal(app.$("project-summary").focused, true);
app.$("project-choice").open = true;
app.press(betaInput);
assert.equal(app.$("project-choice").open, true, "clicking a project checkbox keeps the menu open");
app.press(app.$("project-clear"));
assert.equal(app.$("project-choice").open, true, "clearing the selection keeps the menu open");
app.press(app.$("dash-search"));
assert.equal(app.$("project-choice").open, false, "clicking another control closes the menu");
app.$("project-choice").open = true;
app.press(app.$("dash-panel"));
assert.equal(app.$("project-choice").open, false, "clicking page content closes the menu");

// Attention counts sessions once, follows search, and is absent from the issue view.
const uncertain = JSON.parse(JSON.stringify(base));
uncertain.sessions[0].issues.forEach(issue => { issue.progress = "inconclusive"; });
app = boot(uncertain);
assert.match(text(app.$("session-attention")), /1 个会话存在“无法判断”的验证结论/);
app.search("nothing-matches-this");
assert.equal(app.$("session-attention").hidden, true);
all(app.$("filter-note")).find(node => node.tag === "button").onclick();
assert.equal(app.$("session-attention").hidden, false);
assert.equal(app.$("filter-note").hidden, true);
app.$("tab-category").onclick();
assert.equal(app.$("session-attention").hidden, true);

// Category order uses only matching issues, even if a different category has a larger unfiltered history.
const ranked = JSON.parse(JSON.stringify(base));
ranked.categories.other = {name: "Other", definition: "Another category"};
ranked.sessions = [a, b].map((s, index) => ({...JSON.parse(JSON.stringify(s)), issues: [
  {id: "ISSUE-001", title: "Keep", category: shellId, progress: "pending", updated_ms: 1},
  {id: "ISSUE-002", title: "Old", category: "other", progress: "diagnosed", updated_ms: 2},
  {id: "ISSUE-003", title: "Old", category: "other", progress: "diagnosed", updated_ms: 2},
  ...(index ? [] : [{id: "ISSUE-004", title: "Keep", category: "other", progress: "pending", updated_ms: 3}]),
]}));
app = boot(ranked);
app.$("tab-category").onclick();
assert.equal(app.cards()[0].dataset.key, "other");
app.legend("待诊断").onclick();
assert.equal(app.cards()[0].dataset.key, shellId);
assert.match(text(app.cards()[0]), /2 个会话 · 2 个问题/);

const sharedProjects = JSON.parse(JSON.stringify(mixed));
sharedProjects.sessions[0].issues[0].category = sharedProjects.sessions[0].issues[1].category;
sharedProjects.sessions[0].issues[0].linked_projects.push({id: "b", name: "Beta"});
app = boot(sharedProjects);
app.go("#view=category&project=a&project=b");
assert.match(text(app.$("filter-note")), /1 个会话 · 2 个问题/, "multiple associations count an issue once");
assert.match(text(app.cards()[0]), /关联项目：Alpha、Beta/);
assert.equal(text(app.cards()[0]).match(/Beta/g).length, 1, "shared project names appear once per category");
