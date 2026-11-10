// Dependency-free checks of raw Event projection; no model or network APIs are available here.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../agent_tune_kit/visualize/assets/trace/app.js"), "utf8");
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf("  function eventState("), source.indexOf("  function segmentMembership(")), context);
const {eventState, eventPreview, eventDuration} = context;

for (const node of [
  {level:"ERROR"}, {level:"error"},
  {output:{status:"failed"}}, {output:{outcome:"error"}}, {input:{result:" FAILURE "}},
  {output:{success:false}}, {output:{is_error:true}}, {output:{isError:true}},
  {output:{error:"permission denied"}}, {output:{error:{message:"denied"}}},
  {output:{status:"success", success:false}},
]) assert.equal(eventState(node).failed, true, JSON.stringify(node));
for (const node of [
  {}, {level:"WARN"}, {output:"error: an example"}, {output:{message:"failed is a status"}},
  {name:"mcs.sdk.tool.error"}, {issue_ids:["ISSUE-001"]}, {output:{success:"false"}},
  ...[null, "", " \n", false, 0, {}, []].map(error => ({output:{error}})),
]) assert.equal(eventState(node).failed, false, JSON.stringify(node));
assert.equal(eventState({output:{success:true}}).label, "成功");
assert.equal(eventState({output:{status:"pending"}}).label, "pending");
assert.equal(eventState({output:{status:"__proto__"}}).label, "__proto__");

assert.equal(eventPreview({output:" a\n b "}), "a b");
for (const value of [false, 0]) assert.equal(eventPreview({output:value}), String(value));
for (const output of [undefined, null, "", " \n", [], {}]) assert.equal(eventPreview({output}), "未记录可预览内容");
assert.equal(eventPreview({input:{command:"rm example"}, output:{success:false, stderr:"Permission denied"}}), "Permission denied");
assert.equal(eventPreview({level:"ERROR", output:{error:{message:"Connection refused"}, message:"tool completed"}}), "Connection refused");
for (const error of [true, false, 0]) assert.equal(eventPreview({level:"ERROR",output:{error,message:"Actual failure reason"}}), "Actual failure reason");
assert.equal(eventPreview({input:{command:"cat example"}, output:{stdout:"sample"}}), "命令：cat example");
assert.equal(eventPreview({input:{file_path:"/tmp/example.txt"}}), "文件：/tmp/example.txt");
assert.equal(eventPreview({input:{"mcs.state.from":"IDLE"}, output:{"mcs.state.to":"STREAMING"}}), "IDLE → STREAMING");
assert.equal(eventPreview({input:{result:"success",format:"v2",key_id:"not a useful preview"},output:""}), "输入 · result=success · format=v2");
assert.equal(eventPreview({output:{"mcs.workspace.scope":"project","mcs.workspace.project_count":0}}), "scope=project · project_count=0");
assert.equal(eventPreview({output:{usedTokens:0,maxTokens:500000,percentage:"0%"}}), "0 / 500000 tokens · 0%");
assert.equal(eventPreview({input:{message:{content:[{type:"text",text:"Original assistant words"}]}},output:""}), "输入 · Original assistant words");
assert.equal(eventPreview({output:{ops:[{path:"/message",value:"Original frontend words"}]}}), "Original frontend words");
assert.equal(eventPreview({output:{ops:[{path:"",value:[{description:"Original thought"}]}]}}), "Original thought");
assert.equal(eventPreview({input:{message:{content:[{type:"tool_use",name:"Read",input:{file_path:"/tmp/example"}}]}}}), "输入 · Read · /tmp/example");
assert.equal(eventPreview({input:{subtype:"init",model:"test-model"}}), "subtype=init · model=test-model");
assert.equal(eventPreview({output:{result_summary:"Stored upstream text"}}), "Stored upstream text");
assert.equal(eventPreview({output:{count:0,cached:false}}), "输出 · count=0 · cached=false");
assert.equal(eventPreview({input:"Original input"}), "输入 · Original input");
const literal = "<img src=x onerror=alert(1)>";
assert.equal(eventPreview({output:literal}), literal);
assert.equal(eventPreview({output:"x".repeat(10000)}).length, 500);
let deep = {text:"deep"}; for (let i = 0; i < 100; i++) deep = {content:deep};
assert.equal(eventPreview({output:deep}), "未记录可预览内容");

assert.equal(eventDuration({at:10,until:21}), "11 ms");
assert.equal(eventDuration({at:0,until:2500,output:{duration_ms:99}}), "2.5 s");
assert.equal(eventDuration({output:{duration_ms:0}}), "0 ms");
assert.equal(eventDuration({input:{durationMs:3}}), "3 ms");
assert.equal(eventDuration({at:null,until:200}), "");
for (const duration_ms of [-1, "50", null, Infinity, NaN]) assert.equal(eventDuration({output:{duration_ms}}), "");

const sample = {input:{message:{content:[{text:"unchanged"}]}},output:{status:"success",duration_ms:0}};
const before = JSON.stringify(sample);
eventState(sample); eventPreview(sample); eventDuration(sample);
assert.equal(JSON.stringify(sample), before, "Projection must not mutate archived fields");
console.log("Event raw previews, failure states and duration checks passed.");
