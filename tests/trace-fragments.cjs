// Exercise the real fragment navigation, projection and view restoration without browser dependencies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../agent_tune_kit/visualize/assets/trace/app.js"), "utf8");
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));

function boot() {
  const elements = new Map();
  const $ = id => {
    if (!elements.has(id)) elements.set(id, {
      value: "", hidden: false, disabled: false, title: "", style: {}, scrollTop: 0, textContent: "", attrs: {},
      parentElement: {classList: {toggle() {}}}, focus() {}, scrollIntoView() {},
      setAttribute(name, value) { this.attrs[name] = String(value); }, getAttribute(name) { return this.attrs[name] ?? null; },
    });
    return elements.get(id);
  };
  const nodes = Array.from({length: 12}, (_, i) => ({id: `N${i}`, at: i * 1000, until: i * 1000 + 500, track: i % 2, type: "EVENT", name: "read"}));
  const capture = {id: "CAP", nodes, manifest: {completeness: {}}, timeline: {
    order: nodes.map((_, i) => i),
    tracks: [0, 1].map(track => ({trace_id: `T${track}`, start: track * 1000, end: 11500,
      roots: [], rows: nodes.flatMap((n, i) => n.track === track ? [i] : [])})),
  }};
  const fragment = (id, anchors) => ({id, anchors, capture_id: "CAP", scope_kind: "node", kind: "segment", refs: [],
    track: anchors[0] % 2, start: anchors[0] * 1000, end: anchors.at(-1) * 1000 + 500, summary: id});
  const item = {record: {id: "ISSUE-001", title: "Concurrent steps"}, fragments: [fragment("S1", [2, 6]), fragment("S2", [3, 7]), fragment("S3", [6, 8])]};
  const data = {captures: [capture], issues: [item]};
  const context = vm.createContext({
    $, data, pretty: JSON.stringify, document: {activeElement: null},
    window: {scrollY: 17, scrollTo(_x, y) { this.scrollY = y; }},
    swim: {snapshot: () => ({top: 9}), restore() {}, select() {}, scrollSelected() {}},
    chooseCapture() {}, drawLanes() {}, paintWindow() {}, syncRailSelection() {}, syncHash() {},
    closeDrawer() { $("drawer").hidden = true; },
    openEventDrawer(node) { context.openedNode = node; },
    showIssue(issue) { context.openedIssue = issue.record.id; },
    evidenceRefAt: (_item, pos) => ({pointer: `/evidence/${pos}`}),
    openReference(ref) { context.openedReference = ref; },
  });
  vm.runInContext(`
    let currentCapture = data.captures[0], investigation = null, scopeBefore = null, previewIssue = null, eventFocus = null;
    let selectedNode = null, expandedFolds = new Set(), activeIssue = null, issueStage = null;
    let streamRows = [], displayRows = [], displayedScope = null;
    const filters = ["trace-filter", "type-filter", "trace-search", "record-mode"];
    const list = $("trace-list"), scroller = $("trace-scroll"), rowHeight = 40, searchable = new WeakMap();
    const scope = () => $("trace-filter").value === "" ? null : Number($("trace-filter").value);
    const timeline = () => currentCapture.timeline, startTime = () => 0, elapsed = String, trackLabel = i => "T" + i;
    const partNow = () => investigation?.item.fragments[investigation.index];
    const selectedIndex = () => currentCapture.nodes.indexOf(selectedNode);
    ${slice("  function rememberView()", "  const setDrawerWidth =")}
    ${slice("  function pickFromLane(", "  function navigateNode(")}
    ${slice("  function clearRecordFilters()", "  function previewText(")}
    ${slice("  function segmentMembership(", "  function fieldNotes(")}
    ${slice("  function buildDisplayRows(", "  function markVisibleRows(")}
    ${slice("  function selectAndScroll(", '  scroller.addEventListener("scroll"')}
    ${slice("  function renderTraces(", "  function chooseCapture(")}
    ${slice("  function showFragmentBar()", "  function showFragmentEvidence()")}
    ${slice("  function exitIssue()", "  function syncRailSelection()")}
    this.inspect = () => ({displayRows, selected: selectedIndex(), investigation, eventFocus});
    this.expand = key => { expandedFolds.add(key); renderTraces(); };
  `, context);
  context.renderTraces();
  return {context, $, item, capture, rows: () => JSON.parse(JSON.stringify(context.inspect().displayRows))};
}
const visible = rows => rows.filter(row => row.kind === "row").map(row => row.index);
const retained = rows => rows.flatMap(row => row.kind === "row" ? [row.index] : row.kind === "fold" && !row.expanded ? row.indices : []);

test("grouped events reuse the list, keep exact membership and restore prior filters and position", () => {
  const app = boot();
  app.capture.nodes[0].level = "ERROR";
  app.$("record-mode").value = "error";
  app.$("trace-search").value = "read";
  app.context.selectAndScroll(0);
  app.context.renderTraces(80);
  app.capture.nodes[2].at = app.capture.nodes[1].at;
  app.capture.nodes[4].branch = "child";
  app.context.focusEvents([0, 2]);
  assert.deepEqual(visible(app.rows()), [0, 2], "Concurrent and child events outside the clicked group stay excluded");
  assert.equal(app.$("records-title").textContent, "事件 · T0");
  assert.equal(app.$("event-range").textContent, "所选时段 0 – 2500");
  assert.equal(app.$("trace-count").textContent, "2 条事件");
  assert.equal(app.$("event-reset").textContent, "返回筛选结果");
  assert.equal(app.$("drawer").hidden, true);
  assert.equal(app.context.inspect().selected, -1, "Opening a group must not select or open its first event");
  app.$("record-mode").value = "error";
  app.context.renderTraces();
  assert.deepEqual(visible(app.rows()), [0]);
  assert.equal(app.$("trace-count").textContent, "1 / 2 条事件");
  app.$("trace-search").value = "no match";
  app.context.renderTraces();
  assert.equal(app.$("trace-count").textContent, "0 / 2 条事件");
  assert.equal(app.$("event-reset").hidden, false);
  app.context.focusEvents([6, 8]);
  assert.deepEqual(visible(app.rows()), [6, 8]);
  app.context.exitEventFocus();
  assert.equal(app.$("event-reset").hidden, true);
  assert.equal(app.$("event-range").hidden, true);
  assert.equal(app.$("trace-search").value, "read");
  assert.equal(app.$("record-mode").value, "error");
  assert.equal(app.$("trace-scroll").scrollTop, 80);
  assert.equal(app.context.inspect().selected, 0);
});

test("event focus and issue navigation restore each other without losing expanded context", () => {
  const app = boot();
  app.context.startIssue(app.item);
  app.context.expand(app.rows().find(row => row.kind === "fold").key);
  const before = app.rows();
  app.context.renderTraces(40);
  app.context.focusEvents([0, 2, 4]);
  assert.equal(app.$("fragment-bar").hidden, true);
  assert.equal(app.$("event-reset").textContent, "返回问题片段");
  assert.deepEqual(visible(app.rows()), [0, 2, 4]);
  app.context.startIssue({...app.item, record:{id:"ISSUE-002", title:"Another issue"}});
  assert.equal(app.$("event-reset").hidden, true);
  assert.equal(app.$("fragment-bar").hidden, false);
  app.context.exitIssue();
  assert.deepEqual(visible(app.rows()), [0, 2, 4]);
  assert.equal(app.$("event-reset").hidden, false);
  app.context.exitEventFocus();
  assert.equal(app.context.inspect().investigation.item.record.id, "ISSUE-001");
  assert.deepEqual(app.rows(), before);
  assert.equal(app.$("trace-scroll").scrollTop, 40);
});

test("untimed groups work, and explicit Trace or outside-event navigation clears event focus", () => {
  const app = boot();
  for (const i of [2, 4]) Object.assign(app.capture.nodes[i], {at:null, until:null});
  app.context.focusEvents([2, 4]);
  assert.equal(app.$("event-range").textContent, "时间未知");
  assert.deepEqual(visible(app.rows()), [2, 4]);
  app.context.focusTrace(1);
  assert.equal(app.$("event-reset").hidden, true);
  assert.deepEqual(visible(app.rows()), [1, 3, 5, 7, 9, 11]);
  app.context.focusTrace(null);
  assert.equal(app.$("event-reset").hidden, true);
  app.context.focusEvents([2, 4]);
  app.context.pickFromLane(8);
  assert.equal(app.$("event-reset").hidden, true);
  assert.equal(app.context.inspect().selected, 8);
  assert.equal(app.context.openedNode.id, "N8");
});

test("failure-only filtering matches error styling and combines with Trace, type and search", () => {
  const app = boot();
  app.capture.nodes[0].level = "error";
  app.capture.nodes[2].output = {status: "failed"};
  Object.assign(app.capture.nodes[3], {type: "TOOL", output: {success: false}});
  app.capture.nodes[4].output = {error: "denied"};
  app.capture.nodes[6].output = {message: "error is just text"};
  app.capture.nodes[7].issue_ids = ["ISSUE-001"];
  app.$("record-mode").value = "error";
  app.context.renderTraces();
  assert.deepEqual(visible(app.rows()), [0, 2, 3, 4]);
  assert.equal(app.$("trace-count").textContent, "失败 4 / 12 条");
  app.$("trace-search").value = "read";
  app.context.renderTraces();
  assert.deepEqual(visible(app.rows()), [0, 2, 3, 4], "Search must not include matching normal Events");
  app.$("type-filter").value = "EVENT";
  app.context.renderTraces();
  assert.deepEqual(visible(app.rows()), [0, 2, 4]);
  app.$("trace-filter").value = "1";
  app.context.renderTraces();
  assert.deepEqual(visible(app.rows()), []);
  assert.equal(app.$("trace-count").textContent, "失败 0 / 5 条", "The total counts T1 EVENT rows; node 3 is a TOOL");
  app.$("type-filter").value = "TOOL";
  app.context.renderTraces();
  assert.deepEqual(visible(app.rows()), [3]);
  app.$("trace-filter").value = "";
  app.context.clearRecordFilters();
  app.context.renderTraces();
  assert.deepEqual(visible(app.rows()), app.capture.timeline.order);
});

test("issue navigation restores failure-only filtering and fragment expansion cannot reveal normal Events", () => {
  const app = boot();
  for (const i of [2, 4, 7]) app.capture.nodes[i].level = "ERROR";
  app.$("record-mode").value = "error";
  app.context.renderTraces();
  app.context.startIssue(app.item);
  assert.equal(app.$("record-mode").value, "");
  app.$("record-mode").value = "error";
  app.context.renderTraces();
  assert.deepEqual(visible(app.rows()), [2, 7]);
  assert.deepEqual(retained(app.rows()), [2, 4, 7]);
  app.context.expand(app.rows().find(row => row.kind === "fold").key);
  assert.deepEqual(visible(app.rows()), [2, 4, 7]);
  app.context.exitIssue();
  assert.equal(app.$("record-mode").value, "error");
  assert.deepEqual(visible(app.rows()), [2, 4, 7]);
  assert.equal(app.$("trace-count").textContent, "失败 3 / 12 条");
});

test("the failure switch counts within the other filters, stays visible at zero and restores with the view", () => {
  const app = boot();
  const pressed = () => [app.$("record-all").getAttribute("aria-pressed"), app.$("record-failed").getAttribute("aria-pressed")];
  assert.equal(app.$("record-failed-count").textContent, "0");
  assert.equal(app.$("record-failed").disabled, true, "Zero failures are shown, not hidden, and cannot be chosen");
  assert.deepEqual(pressed(), ["true", "false"]);
  for (const i of [1, 2, 4]) app.capture.nodes[i].level = "ERROR";
  app.context.renderTraces();
  assert.equal(app.$("record-failed-count").textContent, "3");
  assert.equal(app.$("record-failed").disabled, false);
  assert.equal(app.$("trace-count").textContent, "12 条", "Counting failures must not filter the list");
  app.$("trace-filter").value = "0";
  app.context.renderTraces();
  assert.equal(app.$("record-failed-count").textContent, "2", "Count follows the focused Trace before the switch is used");
  app.context.setRecordMode("error");
  assert.deepEqual(pressed(), ["false", "true"]);
  assert.deepEqual(visible(app.rows()), [2, 4]);
  assert.equal(app.$("trace-count").textContent, "失败 2 / 6 条");
  app.$("trace-search").value = "no match";
  app.context.renderTraces();
  assert.equal(app.$("record-failed-count").textContent, "0");
  assert.equal(app.$("record-failed").disabled, false, "An active switch stays usable at zero so the selection is visible");
  app.$("trace-search").value = "";
  app.context.focusEvents([0, 2, 6]);
  assert.equal(app.$("record-mode").value, "", "Opening an event group clears the switch like other record filters");
  assert.equal(app.$("record-failed-count").textContent, "1", "Count stays inside the chosen group");
  app.context.exitEventFocus();
  assert.equal(app.$("record-mode").value, "error");
  assert.deepEqual(pressed(), ["false", "true"]);
  app.context.setRecordMode("");
  assert.deepEqual(pressed(), ["true", "false"]);
  assert.deepEqual(visible(app.rows()), [0, 2, 4, 6, 8, 10]);
});

test("selecting an issue focuses all its fragments and preserves interleaved records", () => {
  const app = boot();
  assert.equal(app.$("fragment-bar").hidden, true);
  assert.equal(app.$("trace-count").textContent, "12 条");
  app.context.startIssue(app.item);
  assert.deepEqual(visible(app.rows()), [2, 3, 6, 7, 8]);
  assert.deepEqual(retained(app.rows()), Array.from({length: 12}, (_, i) => i));
  assert.equal(app.rows().filter(row => row.kind === "header").length, 3);
  assert.equal(app.$("trace-count").textContent, "片段内 5 条 · 已折叠 7 条");
  assert.match(app.$("fragment-position").textContent, /问题片段 · 共 3 个片段/);
  assert.equal(app.context.inspect().selected, 2);
  app.context.goFragment(1);
  assert.equal(app.context.inspect().selected, 3);
  assert.deepEqual(visible(app.rows()), [2, 3, 6, 7, 8]);
});

test("local expansion, complete context and returning to fragments update counts and retain selection", () => {
  const app = boot();
  app.context.startIssue(app.item);
  const gap = app.rows().find(row => row.kind === "fold" && row.indices.includes(4));
  app.context.expand(gap.key);
  assert.equal(app.$("trace-count").textContent, "片段内 5 条 · 已折叠 5 条 · 已展开上下文 2 条");
  assert.deepEqual(retained(app.rows()), Array.from({length: 12}, (_, i) => i));
  app.context.toggleFragmentContext();
  assert.equal(visible(app.rows()).length, 12);
  assert.equal(app.$("trace-count").textContent, "共 12 条 · 片段内 5 条");
  assert.equal(app.$("fragment-context").textContent, "返回问题片段");
  assert.equal(app.context.inspect().investigation.item.record.id, "ISSUE-001");
  app.context.toggleFragmentContext();
  assert.deepEqual(visible(app.rows()), [2, 3, 6, 7, 8]);
  assert.equal(app.$("fragment-context").textContent, "查看完整上下文");
  app.context.selectAndScroll(5);
  assert.ok(visible(app.rows()).includes(5), "Evidence navigation must expand a folded target");
});

test("issue switching defaults to fragments and exit restores original filters and scroll", () => {
  const app = boot();
  app.$("trace-filter").value = "1";
  app.$("type-filter").value = "EVENT";
  app.$("trace-search").value = "read";
  app.context.renderTraces(80);
  app.context.startIssue(app.item, 1);
  assert.equal(app.context.inspect().selected, 3);
  assert.equal(app.$("trace-filter").value, "");
  assert.equal(app.$("trace-search").value, "");
  app.context.toggleFragmentContext();
  app.context.startIssue({...app.item, record: {id: "ISSUE-002", title: "Another issue"}});
  assert.equal(app.context.inspect().investigation.showContext, false);
  app.context.exitIssue();
  assert.equal(app.$("fragment-bar").hidden, true);
  assert.equal(app.$("trace-filter").value, "1");
  assert.equal(app.$("type-filter").value, "EVENT");
  assert.equal(app.$("trace-search").value, "read");
  assert.equal(app.$("trace-scroll").scrollTop, 80);
  assert.equal(app.$("trace-count").textContent, "6 条");
  assert.equal(app.$("records-title").textContent, "事件 · T1");
  assert.equal(app.$("trace-choice").hidden, false);
});

test("Trace focus, switching and issue navigation restore the complete session view", () => {
  const app = boot();
  assert.equal(app.$("scope-title").textContent, "完整会话");
  assert.equal(app.$("trace-choice").hidden, true);
  assert.equal(app.$("lane-reset").hidden, true);
  app.$("type-filter").value = "EVENT";
  app.$("trace-search").value = "read";
  app.context.renderTraces();
  app.context.selectAndScroll(6);
  const top = app.$("trace-scroll").scrollTop;
  app.context.focusTrace(0);
  assert.equal(app.$("scope-title").textContent, "聚焦");
  assert.equal(app.$("trace-choice").hidden, false);
  assert.equal(app.$("lane-reset").hidden, false);
  assert.equal(app.$("records-title").textContent, "事件 · T0");
  assert.deepEqual(visible(app.rows()), [0, 2, 4, 6, 8, 10]);
  // A native select changes its value before invoking the shared focus handler.
  app.$("trace-filter").value = "1";
  app.context.focusTrace(1);
  assert.equal(app.$("records-title").textContent, "事件 · T1");
  assert.deepEqual(visible(app.rows()), [1, 3, 5, 7, 9, 11]);
  app.context.startIssue(app.item, 1);
  assert.equal(app.$("trace-choice").hidden, true);
  assert.equal(app.$("lane-reset").hidden, true);
  assert.equal(app.$("records-title").textContent, "事件");
  assert.deepEqual(visible(app.rows()), [2, 3, 6, 7, 8]);
  app.context.exitIssue();
  assert.equal(app.$("trace-filter").value, "1");
  assert.equal(app.$("trace-choice").hidden, false);
  app.$("trace-search").value = "no match";
  app.context.renderTraces();
  assert.equal(app.$("records-title").textContent, "事件 · T1");
  assert.equal(app.$("trace-count").textContent, "0 条");
  app.context.focusTrace(null);
  assert.equal(app.$("trace-filter").value, "");
  assert.equal(app.$("trace-choice").hidden, true);
  assert.equal(app.$("lane-reset").hidden, true);
  assert.equal(app.$("type-filter").value, "EVENT");
  assert.equal(app.$("trace-search").value, "read");
  assert.equal(app.$("trace-scroll").scrollTop, top);
  assert.equal(app.context.inspect().selected, 6);
  assert.equal(app.$("records-title").textContent, "事件");
  assert.equal(app.$("trace-count").textContent, "12 条");
});

test("a single Trace needs no focus switch or return control", () => {
  const app = boot();
  app.capture.timeline.tracks = app.capture.timeline.tracks.slice(0, 1);
  app.capture.timeline.order = app.capture.timeline.tracks[0].rows;
  app.context.renderTraces();
  assert.equal(app.$("scope-title").textContent, "完整会话");
  assert.equal(app.$("trace-choice").hidden, true);
  assert.equal(app.$("lane-reset").hidden, true);
  assert.equal(app.$("trace-count").textContent, "6 条");
});

test("legacy evidence and issues without locatable fragments explain their state", () => {
  const app = boot();
  const legacy = {record: {id: "OLD", title: "Legacy"}, fragments: [{capture_id: "CAP", anchors: [4], refs: [0], kind: "point", unscoped: true}]};
  app.context.startIssue(legacy);
  assert.match(app.$("fragment-position").textContent, /该问题尚未圈定片段/);
  assert.equal(app.$("trace-count").textContent, "关联证据 1 条 · 已折叠 11 条");
  assert.deepEqual(visible(app.rows()), [4]);
  app.context.startIssue({record: legacy.record, fragments: [{capture_id: "CAP", anchors: [], refs: [0], kind: "file"}]});
  assert.equal(app.context.openedReference.pointer, "/evidence/0");
  assert.equal(app.$("fragment-context").hidden, true);
  assert.equal(visible(app.rows()).length, 12);
  app.capture.nodes.push({id: "ROOT", type: "TRACE", track: 0});
  app.capture.timeline.tracks[0].roots.push(12);
  app.context.startIssue({record: legacy.record, fragments: [{capture_id: "CAP", anchors: [12], refs: [1], kind: "trace", unscoped: true}]});
  assert.equal(app.context.openedReference.pointer, "/evidence/1", "Trace-level evidence opens its details when it has no Event row");
  app.context.startIssue({record: legacy.record, fragments: []});
  assert.equal(app.context.openedIssue, "OLD");
  assert.equal(app.$("fragment-position").textContent, "该问题尚未圈定片段");
  assert.equal(app.$("fragment-fields").hidden, true);
  assert.equal(app.context.inspect().selected, -1);
  assert.equal(visible(app.rows()).length, 12);
  app.context.exitIssue();
  assert.equal(app.$("fragment-bar").hidden, true);
});
