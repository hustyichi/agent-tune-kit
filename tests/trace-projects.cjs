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
      {id: "ISSUE-001", projects: [{id: "server", name: "magic-code-server"}]},
      {id: "ISSUE-002", projects: [{id: "other", name: "Other"}]},
      {id: "ISSUE-003", projects: []},
    ].map(({id, projects}) => ({record: {id, title: id}, linked_projects: projects, progress: {key: "pending", label: "待诊断"}, fragments: [{id: "S1", summary: "Evidence", anchors: []}]})),
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
    let investigation = null, selectedNode = null, railFilter = null, previewIssue = null, scopeBefore = null, expandedFolds = new Set(), eventFocus = null;
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

test("project selection matches exact projects, OR, unattributed, clear and progress intersection", () => {
  const app = boot();
  app.select(["workspace"]);
  assert.equal(app.$("rail-count").textContent, "0/3", "a parent does not match a child's issues");
  app.select(["server"]);
  assert.equal(app.$("rail-count").textContent, "1/3");
  app.select(["server", "~unattributed"]);
  assert.deepEqual(app.cards().map(card => card.dataset.issueId), ["ISSUE-001", "ISSUE-003"]);
  app.$("issue-status").value = "已诊断"; app.context.render();
  assert.equal(app.$("rail-count").textContent, "0/3");
  app.$("issue-status").value = "";
  app.$("project-clear").onclick();
  assert.equal(app.$("rail-count").textContent, "3/3");
});

test("a parent project matches only its own directly associated issues", () => {
  const app = boot();
  app.data.issues[1].linked_projects = [{id: "workspace", name: "Workspace"}];
  app.select(["workspace"]);
  assert.deepEqual(app.cards().map(card => card.dataset.issueId), ["ISSUE-002"]);
  app.select(["server"]);
  assert.deepEqual(app.cards().map(card => card.dataset.issueId), ["ISSUE-001"]);
  app.select(["workspace", "server"]);
  assert.deepEqual(app.cards().map(card => card.dataset.issueId), ["ISSUE-001", "ISSUE-002"]);
});

test("project parameters survive rendering, issue and event navigation, and reload", () => {
  const app = boot("#project=server&project=~unattributed");
  for (const action of [() => app.context.syncHash(), () => app.context.selectIssue("ISSUE-001"), () => app.context.selectEvent()]) {
    action();
    assert.deepEqual(new URLSearchParams(app.location.hash.slice(1)).getAll("project"), ["server", "~unattributed"]);
    assert.equal(boot(app.location.hash).$("rail-count").textContent, "2/3");
  }
  assert.equal(new URLSearchParams(app.location.hash.slice(1)).get("event"), "trace-1/obs-1");
});

test("hash navigation restores checkboxes, counts, empty filters and explicit issue visibility", () => {
  const app = boot("#project=server");
  app.go("#project=other");
  assert.deepEqual(app.cards().map(card => card.dataset.issueId), ["ISSUE-002"]);
  assert.deepEqual(app.inputs().filter(input => input.checked).map(input => input.value), ["other"]);
  app.go("#project=server&issue=ISSUE-002");
  assert.deepEqual(app.cards().map(card => card.dataset.issueId), ["ISSUE-001", "ISSUE-002"]);
  app.go("");
  assert.equal(app.$("rail-count").textContent, "3/3");
  assert.equal(app.inputs().some(input => input.checked), false);
  assert.equal(app.$("project-summary").textContent, "关联项目：全部");
});

test("initial deep links keep an explicitly selected issue visible even without fragments", () => {
  const hash = "#project=server&issue=ISSUE-002";
  const app = boot(hash);
  app.data.issues[1].fragments = [];
  app.context.syncHash();
  app.context.applyHash(hash);
  assert.equal(app.location.hash, hash);
  assert.deepEqual(app.cards().map(card => card.dataset.issueId), ["ISSUE-001", "ISSUE-002"]);
});

test("project names render once as links and the dashboard filter", () => {
  const app = boot();
  const card = app.cards()[0];
  assert.equal(text(card).match(/magic-code-server/g).length, 1);
  const links = nodes(card).filter(node => node.tag === "a");
  assert.equal(links.length, 1);
  assert.equal(links[0].textContent, "magic-code-server");
  assert.equal(links[0].href, "../index.html#view=category&project=server");
  app.data.issues[0].linked_projects.push({id: "other", name: "Other"}); app.context.render();
  assert.match(text(app.cards()[0]), /magic-code-server、Other/);
  assert.match(text(app.cards()[2]), /关联项目：待归因/);
});

test("standalone previews keep project names readable without broken dashboard links", () => {
  const app = boot("", false);
  assert.match(text(app.cards()[0]), /关联项目：magic-code-server/);
  assert.equal(nodes(app.cards()[0]).filter(node => node.tag === "a").length, 0);
});

test("issue titles enter fragment navigation directly", () => {
  const app = boot();
  nodes(app.cards()[0]).find(node => node.className === "issue-title").onclick();
  assert.equal(new URLSearchParams(app.location.hash.slice(1)).get("issue"), "ISSUE-001");
});

test("cards lead with the complete title, then projects, and show progress once in the header", () => {
  const app = boot();
  const title = "POS 首版订单的明细、合计和币种不一致，需要核对跨项目长标题与完整内容".repeat(4);
  app.data.issues[0].record.title = title;
  app.data.issues[0].linked_projects.push({id: "long", name: "long-project-name".repeat(12)});
  app.context.render();
  const card = app.cards()[0], [head, body] = card.children;
  assert.equal(text(head).match(/待诊断/g).length, 1);
  assert.equal(text(body).includes("待诊断"), false);
  assert.equal(body.children[0].tag, "h3");
  assert.equal(body.children[0].children[0].textContent, title);
  assert.equal(body.children[0].children[0].title, title);
  assert.equal(body.children[1].className, "issue-projects");
  assert.match(text(body.children[1]), /magic-code-server、long-project-name/);
});

test("two evidence entries stay visible and navigation reveals later entries without losing their index", () => {
  const app = boot();
  app.data.issues[0].fragments = Array.from({length: 4}, (_, i) => ({id: `S${i+1}`, summary: `Evidence ${i+1}`, anchors: []}));
  app.context.render();
  let card = app.cards()[0];
  const list = card.children.find(node => node.className === "seg-list");
  assert.equal(list.children.filter(node => node.className === "seg-chip").length, 2);
  const more = nodes(card).find(node => node.className === "seg-more");
  assert.ok(!more.open);
  assert.equal(more.children[0].textContent, "展开其余 2 条证据");
  more.open = true; more.ontoggle();
  assert.equal(more.children[0].textContent, "收起其余 2 条证据");
  nodes(more).filter(node => node.className === "seg-chip")[1].onclick();
  assert.equal(new URLSearchParams(app.location.hash.slice(1)).get("seg"), "S4");
  app.go("#issue=ISSUE-001&seg=S3");
  card = app.cards()[0];
  assert.equal(nodes(card).find(node => node.className === "seg-more").open, true);
  const chips = nodes(card).filter(node => node.className === "seg-chip");
  assert.deepEqual(chips.map(chip => chip["aria-current"]), ["false", "false", "true", "false"]);
  app.data.issues[0].fragments = [];
  app.context.render();
  assert.equal(nodes(app.cards()[0]).some(node => node.className === "seg-more"), false);
  assert.match(text(app.cards()[0]), /未圈定范围/);
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
