// Dependency-free checks of the session page's real filter, rail and hash handlers.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../agent_tune_kit/visualize/assets/trace/app.js"), "utf8");
const menuSource = fs.readFileSync(path.join(__dirname, "../agent_tune_kit/visualize/assets/shared/project-menu.js"), "utf8");
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const nodes = node => [node, ...node.children.flatMap(nodes)];
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ""; this.value = ""; this.dataset = {}; }
  append(...items) { for (const item of items) { item.parentElement = this; this.children.push(item); } }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this[name] = value; }
  querySelectorAll(selector) { return nodes(this).filter(node => node.className === selector.slice(1)); }
  focus() { this.focused = true; }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) if (`#${node.id}` === selector) return node;
    return null;
  }
}
const text = node => node.textContent + node.children.map(text).join("");

function boot(hash = "", available = true) {
  const ids = new Map();
  const $ = id => { if (!ids.has(id)) ids.set(id, Object.assign(new Element("div"), {id})); return ids.get(id); };
  $("project-choice").append($("project-summary"), $("project-filter"), $("project-clear"));
  const listeners = {};
  const data = {
    projects: {workspace: {id: "workspace", name: "Workspace"}, server: {id: "server", name: "magic-code-server", parent_id: "workspace"}, other: {id: "other", name: "Other"}},
    issues: [
      {id: "ISSUE-001", roots: [{id: "server", name: "magic-code-server", pending: true}]},
      {id: "ISSUE-002", roots: [{id: "other", name: "Other"}]},
      {id: "ISSUE-003", roots: []},
    ].map(({id, roots}) => ({record: {id, title: id}, root_projects: roots, progress: {key: "pending", label: "待诊断"}, fragments: [{id: "S1", summary: "Evidence", anchors: []}]})),
    categories: {}, dashboard: {available}, captures: [],
  };
  const location = {hash, pathname: "/session/preview.html", search: ""};
  const context = vm.createContext({
    $, data, location, URLSearchParams,
    document: {getElementById: $, createElement: tag => new Element(tag), createTextNode: text => Object.assign(new Element("#text"), {textContent: text}), addEventListener: (name, fn) => { listeners[name] = fn; }},
    window: {addEventListener() {}},
    history: {replaceState: (_s, _t, url) => { location.hash = url.startsWith("#") ? url : ""; }},
    pretty: JSON.stringify, drawLanes() {}, rememberView() {}, showIssue() {}, renderTraces() {},
  });
  vm.runInContext(menuSource, context);
  vm.runInContext(`
    let investigation = null, selectedNode = null, railFilter = null, previewIssue = null, scopeBefore = null, expandedFolds = new Set();
    const currentCapture = null;
    const issueById = new Map(data.issues.map(item => [item.record.id, item]));
    const partNow = () => investigation?.item.fragments[investigation.index];
    const goFragment = index => { investigation.index = index; syncHash(); };
    ${slice("  const el =", "  const markdown =")}
    ${slice("  const projectSelection =", "  const filters =")}
    ${slice("  const progressOf =", "  const shortIssue =")}
    ${slice("  function startIssue(", "  function goFragment(")}
    ${slice("  function syncRailSelection()", '  $("fragment-prev").onclick')}
    ${slice('  $("project-filter").onchange =', '  $("capture-filter").onchange =')}
    this.render = renderIssueRail; this.syncHash = syncHash; this.applyHash = applyHash;
    this.selectIssue = id => startIssue(issueById.get(id));
    this.selectEvent = () => { investigation = null; selectedNode = {trace_id: "trace-1", id: "obs-1"}; syncHash(); };
  `, context);
  context.render();
  return {
    $, data, context, location,
    click: node => listeners.click({target: node}),
    escape: () => { let stopped = false; listeners.keydown({key: "Escape", preventDefault() {}, stopPropagation() { stopped = true; }}); return stopped; },
    cards: () => $("issue-rail-list").children.filter(node => node.className === "issue-card"),
    inputs: () => $("project-filter").children.map(label => label.children[0]),
    go: hash => { location.hash = hash; context.applyHash(); },
    select: values => {
      for (const label of $("project-filter").children) label.children[0].checked = values.includes(label.children[0].value);
      $("project-filter").onchange();
    },
  };
}

test("root selection supports parents, OR, unattributed, clear and progress intersection", () => {
  const app = boot();
  app.select(["workspace"]);
  assert.equal(app.$("rail-count").textContent, "1/3");
  app.select(["workspace", "~unattributed"]);
  assert.deepEqual(app.cards().map(card => card.dataset.issueId), ["ISSUE-001", "ISSUE-003"]);
  app.$("issue-status").value = "已诊断"; app.context.render();
  assert.equal(app.$("rail-count").textContent, "0/3");
  app.$("issue-status").value = "";
  app.$("project-clear").onclick();
  assert.equal(app.$("rail-count").textContent, "3/3");
});

test("project parameters survive rendering, issue and event navigation, and reload", () => {
  const app = boot("#project=workspace&project=~unattributed");
  for (const action of [() => app.context.syncHash(), () => app.context.selectIssue("ISSUE-001"), () => app.context.selectEvent()]) {
    action();
    assert.deepEqual(new URLSearchParams(app.location.hash.slice(1)).getAll("project"), ["workspace", "~unattributed"]);
    assert.equal(boot(app.location.hash).$("rail-count").textContent, "2/3");
  }
  assert.equal(new URLSearchParams(app.location.hash.slice(1)).get("event"), "trace-1/obs-1");
});

test("hash navigation restores checkboxes, counts, empty filters and explicit issue visibility", () => {
  const app = boot("#project=workspace");
  app.go("#project=other");
  assert.deepEqual(app.cards().map(card => card.dataset.issueId), ["ISSUE-002"]);
  assert.deepEqual(app.inputs().filter(input => input.checked).map(input => input.value), ["other"]);
  app.go("#project=workspace&issue=ISSUE-002");
  assert.deepEqual(app.cards().map(card => card.dataset.issueId), ["ISSUE-001", "ISSUE-002"]);
  app.go("");
  assert.equal(app.$("rail-count").textContent, "3/3");
  assert.equal(app.inputs().some(input => input.checked), false);
  assert.equal(app.$("project-summary").textContent, "关联项目：全部");
});

test("initial deep links keep an explicitly selected issue visible even without fragments", () => {
  const hash = "#project=workspace&issue=ISSUE-002";
  const app = boot(hash);
  app.data.issues[1].fragments = [];
  app.context.syncHash();
  app.context.applyHash(hash);
  assert.equal(app.location.hash, hash);
  assert.deepEqual(app.cards().map(card => card.dataset.issueId), ["ISSUE-001", "ISSUE-002"]);
});

test("project names render once as links with pending status and the dashboard filter", () => {
  const app = boot();
  const card = app.cards()[0];
  assert.equal(text(card).match(/magic-code-server/g).length, 1);
  const links = nodes(card).filter(node => node.tag === "a");
  assert.equal(links.length, 1);
  assert.equal(links[0].textContent, "magic-code-server（待确认）");
  assert.equal(links[0].href, "../index.html#view=category&project=server");
  app.data.issues[0].root_projects.push({id: "other", name: "Other"}); app.context.render();
  assert.match(text(app.cards()[0]), /magic-code-server（待确认）、Other/);
  assert.match(text(app.cards()[2]), /关联项目：待归因/);
});

test("standalone previews keep project names readable without broken dashboard links", () => {
  const app = boot("", false);
  assert.match(text(app.cards()[0]), /关联项目：magic-code-server（待确认）/);
  assert.equal(nodes(app.cards()[0]).filter(node => node.tag === "a").length, 0);
});

test("issue titles enter fragment navigation directly", () => {
  const app = boot();
  nodes(app.cards()[0]).find(node => node.className === "issue-title").onclick();
  assert.equal(new URLSearchParams(app.location.hash.slice(1)).get("issue"), "ISSUE-001");
});

test("back to dashboard opens the session view without a drawer", () => {
  const app = boot();
  vm.runInContext(slice("\n  if (data.dashboard.available) {", "  for (const warning of data.warnings)"), app.context);
  assert.equal(app.$("dashboard-link").href, "../index.html#view=session");
  assert.equal(app.$("dashboard-link").hidden, false);
});

test("the shared project menu keeps internal clicks and dismisses outside clicks and Escape", () => {
  const app = boot(), choice = app.$("project-choice");
  for (const node of [app.inputs()[0], app.$("project-clear"), app.$("project-summary")]) {
    choice.open = true; app.click(node);
    assert.equal(choice.open, true);
  }
  for (const id of ["issue-search", "issue-status", "issue-rail-list", "trace-list"]) {
    choice.open = true; app.click(app.$(id));
    assert.equal(choice.open, false);
  }
  choice.open = true;
  assert.equal(app.escape(), true, "Escape must not also exit the active issue");
  assert.equal(choice.open, false);
  assert.equal(app.$("project-summary").focused, true);
  assert.equal(app.escape(), false, "Escape passes through when the menu is closed");
});
