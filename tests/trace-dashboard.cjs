// Dependency-free behavior checks for the dashboard script; this does not test browser layout.
// Usage: node trace-dashboard.cjs <dashboard-data.json>, where the JSON is the generator's real page data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../agent_tune_kit/visualize/assets/traces/app.js"), "utf8");
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
  for (const id of ["project-choice", "project-summary", "project-clear", "project-filter", "project-name", "dash-stats", "warnings", "source-choice", "source-filter", "tab-session", "tab-category", "dash-panel", "dash-search", "progress-bar", "progress-legend", "filter-note", "dash-grid", "dash-strip", "drawer", "drawer-title", "drawer-content", "drawer-close", "drawer-resizer"]) {
    ids.set(id, Object.assign(new Node("div"), {id}));
  }
  for (const id of ["drawer-title", "drawer-content", "drawer-close", "drawer-resizer"]) ids.get(id).parentElement = ids.get("drawer");
  for (const id of ["dash-grid", "dash-strip", "progress-bar", "progress-legend", "filter-note"]) ids.get(id).parentElement = ids.get("dash-panel");
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
    addEventListener: (name, fn) => { listeners.document[name] = fn; },
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
  return {
    $, location, context,
    cards: () => $("dash-grid").children,
    strip: () => $("dash-strip").children,
    rows: () => all($("drawer-content")).filter(node => matches(node, ".issue-row")),
    open: card => all(card).find(node => matches(node, ".card-open")),
    legend: label => all($("progress-legend")).find(node => node.className.includes("legend-item") && text(node).startsWith(label)),
    search: value => $("dash-search").oninput({target: {value}}),
    go: hash => { location.hash = hash; listeners.window.hashchange(); },
    escape: () => listeners.document.keydown({key: "Escape", preventDefault() {}}),
    // A mouse click as the document sees it: the press lands on `down`, the click on `up` (the same node by default).
    press: (down, up = down) => { listeners.document.pointerdown({target: down}); listeners.document.click({target: up}); },
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
assert.match(text(app.$("progress-legend")), /待诊断 1[\s\S]*已诊断 \d[\s\S]*验证失败 1[\s\S]*验证通过 1/);
assert.doesNotMatch(text(app.$("progress-legend")), /修复中|已排除|无法判断/, "empty progress levels are not listed");
assert.equal(app.$("progress-bar").children.length, all(app.$("progress-legend")).filter(n => n.className.includes("swatch")).length);

// --- progress legend filters both the grid and the stats --------------------------------------------------
app.legend("待诊断").onclick();
assert.equal(app.cards().length, 1);
assert.match(text(app.cards()[0]), new RegExp(a.session_id.slice(0, 8)));
assert.equal(app.$("filter-note").hidden, false);
assert.equal(app.legend("待诊断").attrs["aria-pressed"], "true");
app.legend("待诊断").onclick();
assert.equal(app.cards().length, 2);
assert.equal(app.$("filter-note").hidden, false);

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
assert.match(text(app.$("drawer-content")), /检测范围与缺口/);
assert.ok(all(app.$("drawer-content")).some(node => node.tag === "a" && node.textContent.startsWith("打开会话页") && node.href === a.href));
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
app.press(app.legend("待诊断"));
assert.equal(app.$("drawer").hidden, false, "a click on a control (here a progress filter) keeps it open");
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

// --- a progress filter narrows the drawer and says so ------------------------------------------------------
app.open(app.cards().find(card => text(card).includes("3 个问题"))).onclick();
app.legend("待诊断").onclick();
assert.equal(app.rows().length, 1);
assert.match(text(app.$("drawer-content")), /问题 1 \/ 3（已按筛选）/);
app.legend("待诊断").onclick();
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
assert.equal(text(app.$("progress-legend")).includes("验证失败 1"), true);
assert.equal(text(app.$("progress-legend")).includes("验证通过"), false, "the legend follows the source scope");

// --- unreadable sessions and empty sessions stay visible -----------------------------------------------------
const odd = JSON.parse(JSON.stringify(base));
odd.sessions.push({dir: "broken", href: "./broken/preview.html", session_id: "broken", error: "Missing or damaged Trace record: session.json", source: {}, issues: [], problems: [], problem_count: 0, preview: {exists: false, stale: false}});
odd.sessions.push({...JSON.parse(JSON.stringify(a)), dir: "quiet", href: "./quiet/preview.html", session_id: "quiet", issues: [], preview: {exists: false, stale: true}, problem_count: 2, problems: ["ISSUE-009: bad"]});
app = boot(odd);
assert.equal(app.cards().length, 4);
// A progress filter asks about issues, so sessions without any issue (or without readable records) drop out;
// a text search still finds them by Session ID so they can be inspected.
app.legend("待诊断").onclick();
assert.deepEqual(app.cards().map(card => card.dataset.key), [a.dir]);
app.legend("待诊断").onclick();
app.search("quiet");
assert.deepEqual(app.cards().map(card => card.dataset.key), ["quiet"]);
app.search("");
assert.equal(app.cards().length, 4);
const broken = app.cards().find(card => card.dataset.key === "broken");
assert.match(text(broken), /记录不可读取[\s\S]*Missing or damaged/);
const quiet = app.cards().find(card => card.dataset.key === "quiet");
assert.match(text(quiet), /无保留问题（不代表会话正确）[\s\S]*预览缺失[\s\S]*2 条记录读取失败/);
app.open(broken).onclick();
assert.match(text(app.$("drawer-content")), /会话记录不可读取/);
assert.ok(!all(app.$("drawer-content")).some(node => node.className.includes("link-button") && node.textContent.startsWith("打开会话页")), "no link to a page that cannot exist");
app.escape();
app.open(quiet).onclick();
assert.match(text(app.$("drawer-content")), /生成命令：[\s\S]*atk visualize trace --session-id quiet --overwrite --no-open/);
assert.match(text(app.$("drawer-content")), /记录读取失败 2/);
assert.match(text(app.$("drawer-content")), /没有保留问题不代表会话正确/);
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
assert.match(text(app.$("progress-legend")), /尚无保留问题（不代表会话正确）/);
app.$("tab-category").onclick();
assert.match(text(app.cards()[0]), /还没有异常分类/);
assert.equal(app.strip().length, 0);

console.log("Dashboard rendering: views, filters, drawers, deep links, sources, unreadable and empty sessions passed.");

const attributed = JSON.parse(JSON.stringify(base));
attributed.projects = {ape: {id: "ape", name: "APE"}, ot: {id: "ot", name: "OT"}};
for (const session of attributed.sessions) for (const issue of session.issues) issue.root_projects = [{id: "ape", name: "APE", pending: true}];
app = boot(attributed);
app.go("#view=session&project=ot");
assert.equal(app.rows().length, 0);
assert.equal(app.cards().filter(card => card.dataset.key).length, 0);
app.go("#view=session&project=ape");
assert.ok(app.cards().length > 0);
assert.match(text(app.cards()[0]), /APE（待确认）/);
assert.equal(selected("tab-category"), true, "legacy project links open issue grouping");
app.$("tab-session").onclick();
assert.equal(app.cards().length, base.sessions.length);
assert.equal(app.$("project-choice").hidden, true);
assert.ok(app.cards().every(card => !text(card).includes("根因项目")));
assert.equal(app.location.hash, "#view=session");
app.$("tab-category").onclick();
assert.equal(app.$("project-choice").hidden, false);
assert.match(app.location.hash, /project=ape/);
app.$("project-clear").onclick();
assert.doesNotMatch(app.location.hash, /project=/);
assert.equal(app.$("project-summary").textContent, "异常根因项目：全部");

// Different issues in one session can have different projects or remain unattributed.
const mixed = JSON.parse(JSON.stringify(base));
mixed.projects = {a: {id: "a", name: "Alpha"}, b: {id: "b", name: "Beta"}};
mixed.sessions[0].issues[0].root_projects = [{id: "a", name: "Alpha"}];
mixed.sessions[0].issues[1].root_projects = [{id: "b", name: "Beta"}];
mixed.sessions[0].issues[2].root_projects = [];
app = boot(mixed);
app.go("#view=category&project=a");
assert.match(text(app.$("filter-note")), /1 个会话 · 1 个问题/);
app.$("tab-session").onclick();
assert.match(text(app.$("filter-note")), /2 个会话 · 4 个问题/);
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
assert.equal(app.$("project-summary").textContent, "异常根因项目：已选 1 项");
app.$("project-choice").open = true;
app.$("project-choice").onkeydown({key: "Escape"});
assert.equal(app.$("project-choice").open, false);
assert.equal(app.$("project-summary").focused, true);
