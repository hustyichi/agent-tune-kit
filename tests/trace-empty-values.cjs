// Check the actual input/output rendering block without browser dependencies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("agent_tune_kit/visualize/assets/trace/app.js", "utf8");
const block = source.slice(source.indexOf("      const value = node[key];"), source.indexOf("      if (ref && data.references"));
for (const key of ["input", "output"]) {
  for (const value of [undefined, null, "", " \n\t", false, 0, [], {}, "hello"]) {
    const context = vm.createContext({
      node: {[key]: value}, key,
      el: (tag, text, className) => ({tag, text, className}),
      pre: value => ({tag: "pre", value}),
    });
    const result = vm.runInContext(block + "\ncontent;", context);
    const empty = value == null || (typeof value === "string" && !value.trim());
    assert.equal(result.tag, empty ? "p" : "pre");
    if (empty) {
      assert.equal(result.className, "muted");
      assert.equal(result.text, value == null ? "原始记录未提供值" : key === "input" ? "输入为空" : "输出为空");
    } else assert.equal(result.value, value);
  }
}
console.log("Empty input/output states passed.");
