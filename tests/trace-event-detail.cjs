// Exercise the actual Event renderer without browser dependencies; layout is checked separately.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.ownText = ""; this.isConnected = true; }
  set textContent(value) { this.ownText = String(value); this.children = []; }
  get textContent() { return this.ownText + this.children.map(child => typeof child === "string" ? child : child.textContent).join(""); }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this[name] = value; }
  querySelector(selector) { return nodes(this).find(node => selector.startsWith("#") ? node.id === selector.slice(1) : node.tag === selector) || null; }
  scrollIntoView() { this.scrolled = true; }
}
const nodes = element => [element, ...element.children.filter(child => typeof child !== "string").flatMap(nodes)];
const source = fs.readFileSync(path.join(__dirname, "../agent_tune_kit/visualize/assets/trace/app.js"), "utf8");
const helpers = source.slice(source.indexOf("  const pretty ="), source.indexOf("  const markdown ="));
const preview = source.slice(source.indexOf("  const fieldPreview ="), source.indexOf("  const openReference ="));
const renderer = source.slice(source.indexOf("  const payloadContent ="), source.indexOf("  function showSessionInfo("));
const container = new Element("div"), data = {references: {}};
let title, copied, clipboardError = false, returns = 0;
const context = vm.createContext({
  document: {createElement: tag => new Element(tag)}, data,
  $: () => container,
  openDrawer: name => { title = name; container.children = []; },
  backToIssue: () => { returns++; },
  requestAnimationFrame: callback => callback(),
  navigator: {clipboard: {writeText: async text => { if (clipboardError) throw Error("denied"); copied = text; }}},
});
vm.runInContext(`${helpers}\n${preview}\n${renderer}\nthis.render = showNodeDetails; this.refKey = refKey;`, context);
const node = {
  id: "NODE-ID", trace_id: "TRACE-ID", parent_id: "PARENT-ID", name: "mcs.session.transition", type: "EVENT",
  at: 18, level: "ERROR", paired: 1, issue_ids: ["ISSUE-001"], reference: {path: "raw/trace.json", sha256: "hash", pointer: "/trace/observations/0"},
  input: {"mcs.mode": "agent", "mcs.session_id": "session", "mcs.state.from": "IDLE", "mcs.turn_id": "turn"},
  output: {"mcs.state.to": "STREAMING"},
};
const render = (values = {}, ref = null) => { context.render({...node, ...values}, 7, ref); return nodes(container); };
const evidenceRef = (field, value, extra = {}) => {
  const ref = {...node.reference, pointer: node.reference.pointer + field};
  data.references[context.refKey(ref)] = {preview: {text: JSON.stringify(value), ...extra}, location: {field}};
  return ref;
};
const copyButton = label => nodes(container).find(item => item["aria-label"] === `复制${label}`);

(async () => {
  let rendered = render();
  assert.equal(title, node.name);
  assert.equal(rendered.find(item => item.dataset.nodeIndex).dataset.nodeIndex, "7");
  assert.deepEqual(rendered.filter(item => item.tag === "h3").map(item => item.textContent), ["输入", "输出"]);
  assert.deepEqual(rendered.filter(item => item.tag === "pre").map(item => JSON.parse(item.textContent)), [node.input, node.output]);
  assert.equal(rendered.filter(item => item.tag === "section").length, 2);
  assert.ok(!rendered.some(item => ["details", "input", "nav"].includes(item.tag)));
  assert.doesNotMatch(container.textContent, /TRACE-ID|PARENT-ID|ISSUE-001|ERROR|复制引用|调用关联|原始 JSON|节点与原始引用|命中证据字段/);
  await copyButton("输入").onclick();
  assert.deepEqual(JSON.parse(copied), node.input);
  assert.equal(copyButton("输入").textContent, "已复制");
  clipboardError = true;
  await copyButton("输出").onclick();
  assert.match(copyButton("输出").textContent, /复制失败/);
  clipboardError = false;

  for (const key of ["input", "output"]) {
    for (const value of [undefined, null, "", " \n\t", false, 0, [], {}, "hello"]) {
      rendered = render({[key]: value});
      const label = key === "input" ? "输入" : "输出";
      const section = rendered.filter(item => item.tag === "section")[key === "input" ? 0 : 1];
      const empty = value == null || (typeof value === "string" && !value.trim());
      if (empty) assert.match(section.textContent, new RegExp(value == null ? "原始记录未提供值" : `${label}为空`));
      else assert.equal(section.querySelector("pre").textContent, typeof value === "string" ? value : JSON.stringify(value, null, 2));
      if (value == null) assert.equal(copyButton(label), undefined);
      else {
        await copyButton(label).onclick();
        assert.equal(copied, typeof value === "string" ? value : JSON.stringify(value, null, 2));
      }
    }
  }

  const value = {same: "repeat", "a/b": {"~key": ["repeat", {message: "repeat", lines: "a\nb"}]}, empty: {}, array: []};
  for (const field of ["/input", "/input/a~1b/~0key/0", "/input/a~1b/~0key/1", "/input/empty", "/input/array"]) {
    const ref = evidenceRef(field, "preview must not duplicate the input");
    rendered = render({input: value}, ref);
    const mark = container.querySelector("mark");
    assert.ok(mark, field);
    assert.equal(mark.dataset.pointer, ref.pointer);
    assert.equal(mark.scrolled, true);
    assert.equal(rendered.filter(item => item.tag === "mark").length, 1);
    assert.equal(rendered.filter(item => item.tag === "pre")[0].textContent, JSON.stringify(value, null, 2));
    const expected = field.split("/").slice(2).reduce((v, key) => v[key.replace(/~1/g, "/").replace(/~0/g, "~")], value);
    assert.deepEqual(JSON.parse(mark.textContent), expected);
    assert.doesNotMatch(container.textContent, /preview must not duplicate/);
    assert.equal(rendered.filter(item => item.tag === "section").length, 2);
  }
  // Full payloads remain available even when the separately stored evidence preview was truncated.
  const long = "<img src=x onerror=alert(1)>\n" + "payload\n".repeat(2000);
  rendered = render({output: long}, evidenceRef("/output", "prefix", {truncated: true, characters: long.length}));
  assert.equal(container.querySelector("mark").textContent, long);
  assert.ok(!rendered.some(item => item.tag === "img"));
  await copyButton("输出").onclick();
  assert.equal(copied, long);
  assert.doesNotMatch(container.textContent, /仅展示前/);

  for (const [field, value] of [["/metadata/input", "diagnostic"], ["/input/missing", "unmapped"], ["", {source: "whole node"}]]) {
    render({}, evidenceRef(field, value, {truncated: true, characters: 12000}));
    const evidence = container.querySelector("#evidence-field");
    assert.equal(evidence.tag, "section");
    assert.equal(evidence.querySelector("pre").textContent, JSON.stringify(value));
    assert.match(evidence.textContent, /仅展示前/);
  }
  const broken = evidenceRef("/output/missing", "unused");
  data.references[context.refKey(broken)] = {error: "证据不可读取"};
  render({}, broken);
  assert.match(container.textContent, /证据不可读取/);
  assert.ok(returns > 0, "Evidence navigation retains the existing return route");
  console.log("Event details: minimal content, complete payloads, copy, precise inline evidence, empty values and fallback notices passed.");
})().catch(error => { console.error(error); process.exitCode = 1; });
