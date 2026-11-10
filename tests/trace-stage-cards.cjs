// Dependency-free rendering checks; this does not test browser layout.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ""; this.className = ""; this.classList = {add: name => { this.className += ` ${name}`; }}; }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this[name] = value; }
}
const source = fs.readFileSync(path.join(__dirname, "../agent_tune_kit/visualize/assets/trace/app.js"), "utf8");
const helpers = source.slice(source.indexOf("  const el ="), source.indexOf("  const markdown ="));
const cards = source.slice(source.indexOf("  const textPresent ="), source.indexOf("  function appendDiagnoses("));
const showIssue = source.slice(source.indexOf("  const showIssue ="), source.indexOf("  function pickFromLane("));
const progress = source.slice(source.indexOf("  const progressOf ="), source.indexOf("  const shortIssue ="));
const container = new Element("div");
const context = vm.createContext({
  document: {createElement: tag => new Element(tag)},
  pretty: value => JSON.stringify(value),
  $: () => container,
  openDrawer: () => { container.children = []; },
  sourceLinks: new Map([["src/reader.ts#L3", "../../../src/reader.ts#L3"]]),
  markdown: {normalizeLink: value => value},
  location: {protocol: "file:"},
});
vm.runInContext(`let activeIssue, returnToDiagnosis, origin, issueTrigger, issueStage;
${helpers}\n${cards}\n${showIssue}\n${progress}
this.showIssue = showIssue; this.progressOf = progressOf;`, context);
const nodes = node => [node, ...node.children.flatMap(nodes)];
const text = node => nodes(node).map(item => item.textContent).join("\n");
function renderStage(item, stage) {
  context.showIssue(item, stage);
  assert.ok(!nodes(container).some(node => ["pre", "details"].includes(node.tag)), "No raw records or folded technical payloads");
  return container;
}

const change = {record: {
  status: "completed", created_at: "2026-01-01T01:00:00Z",
  candidate: {commit: "HIDDEN_CANDIDATE"}, diff: "HIDDEN_DIFF", checks: ["HIDDEN_CHECK"], proposal: "HIDDEN_PROPOSAL",
  change_summary: [
    {what: "Use an absolute root.", why: "Preparation does not change cwd.", files: ["src/reader.ts#L3", "missing.ts"]},
    {what: "Check the loaded prompt.", why: "A source edit alone does not prove loading."},
  ],
  remaining_verification: ["Confirm the first read succeeds."],
}, verifications: []};
const item = {record: {id: "ISSUE-001", title: "Wrong path"}, changes: [change], diagnoses: []};
let rendered = renderStage(item, "changes");
const pairs = nodes(rendered).filter(node => node.className === "change-item");
assert.equal(pairs.length, 2);
assert.match(text(pairs[0]), /Use an absolute root\.[\s\S]*Preparation does not change cwd\./);
assert.match(text(pairs[1]), /Check the loaded prompt\.[\s\S]*A source edit alone/);
assert.match(text(rendered), /待验证内容[\s\S]*Confirm the first read/);
assert.match(text(rendered), /missing.ts（文件不存在或不在可链接范围）/);
assert.equal(nodes(rendered).find(node => node.tag === "a").href, "../../../src/reader.ts#L3");
assert.doesNotMatch(text(rendered), /HIDDEN_/);
assert.equal(nodes(rendered).filter(node => node.tag === "button").length, 4, "Only stage tabs, no material buttons");

const verification = {record: {
  status: "completed", created_at: "2026-01-01T02:00:00Z", candidate_loaded: true,
  candidate: {sha256: "HIDDEN_HASH"}, execution: {command: "HIDDEN_COMMAND"}, evidence: [{path: "HIDDEN_PATH", sha256: "HIDDEN_HASH"}],
  verification_summary: {
    method: "Compare proposed calls; do not execute the shell.",
    sample_scope: "8 new requests; 12 cumulative including the prior 4.",
    observations: ["Root-bound proposals improved from 0/3 to 1/3.", "<img src=x onerror=alert(1)>"],
  },
  conclusions: {
    "ISSUE-001": {outcome: "inconclusive", reason: "Actual reads were not executed."},
    "ISSUE-002": {outcome: "pass", reason: "OTHER_ISSUE_REASON"},
  },
  limitations: ["No full Agent replay."],
}};
change.verifications = [verification, {...verification, record: {...verification.record, created_at: "2026-01-01T03:00:00Z"}}];
rendered = renderStage(item, "verifications");
assert.equal(nodes(rendered).filter(node => node.textContent.startsWith("对应修改 ·")).length, 1);
assert.equal(nodes(rendered).filter(node => node.textContent.startsWith("验证 ·")).length, 2);
assert.match(text(rendered), /执行已完成[\s\S]*效果结论：证据不足，尚不能确认修复效果/);
assert.match(text(rendered), /8 new requests; 12 cumulative including the prior 4/);
assert.match(text(rendered), /No full Agent replay/);
assert.doesNotMatch(text(rendered), /OTHER_ISSUE_REASON|HIDDEN_/);
const literal = nodes(rendered).find(node => node.textContent === "<img src=x onerror=alert(1)>");
assert.equal(literal.children.length, 0);
assert.equal(context.progressOf(item).label, "有验证记录");
for (const outcome of ["pass", "fail"]) {
  verification.record.conclusions["ISSUE-001"] = {outcome, reason: "Scoped result."};
  assert.match(text(renderStage(item, "verifications")), new RegExp(`效果结论：${outcome === "pass" ? "通过" : "未通过"}`));
}

delete change.record.change_summary;
change.record.remaining_verification = "Legacy pending check.";
rendered = renderStage(item, "changes");
assert.match(text(rendered), /未记录实际修改及原因的摘要/);
assert.match(text(rendered), /Legacy pending check/);
assert.doesNotMatch(text(rendered), /HIDDEN_PROPOSAL/);
delete verification.record.verification_summary;
delete verification.record.limitations;
verification.record.execution.method = "Legacy single-step comparison.";
verification.record.status = "running";
verification.record.conclusions = {};
change.verifications = [verification];
rendered = renderStage(item, "verifications");
assert.match(text(rendered), /执行进行中[\s\S]*效果结论：尚无结论/);
assert.match(text(rendered), /Legacy single-step comparison/);
assert.match(text(rendered), /请勿直接合计各次结果/);
assert.match(text(rendered), /未记录验证局限，不代表已完整验证/);
rendered = renderStage({...item, changes: [{error: "HIDDEN_PATH"}]}, "changes");
assert.match(text(rendered), /修改记录不可读取/);
assert.doesNotMatch(text(rendered), /HIDDEN_PATH/);
rendered = renderStage({...item, changes: []}, "verifications");
assert.match(text(rendered), /尚未记录实际验证/);
console.log("Stage rendering: concise changes, causal pairs, scoped conclusions, samples, links and legacy states passed.");

module.exports = {renderStage, text, nodes};
