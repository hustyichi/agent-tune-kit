// node tests/trace-controls.cjs <preview.html> <index.html> (requires Playwright + Chromium)
const assert = require("node:assert/strict");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

async function check(browser, file, viewport, dashboard) {
  const page = await browser.newPage({viewport, offline: true});
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(pathToFileURL(file).href);
  if (dashboard) await page.locator("#tab-category").click();
  const heights = await page.locator("input:not([type=checkbox]):visible, select:visible").evaluateAll(items =>
    items.map(item => [item.id, item.getBoundingClientRect().height]));
  assert.ok(heights.every(([, height]) => height === 34), JSON.stringify(heights));
  assert.equal(await page.locator("#issue-status, #project-choice, .project-menu").count(), 0, "dropdown filters are gone");
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "page overflows horizontally");
  // Exercise many long project choices even when the supplied archive links few or no projects.
  const legend = page.locator("#project-legend");
  await legend.evaluate(box => {
    box.hidden = false;
    for (let i = 0; i < 24; i++) {
      const item = document.createElement("button");
      item.type = "button"; item.className = "legend-item project-item"; item.setAttribute("role", "radio");
      item.setAttribute("aria-checked", String(i === 3));
      const name = document.createElement("span"); name.className = "project-name"; name.textContent = `测试项目-${i}-` + "long-project-name".repeat(8);
      const count = document.createElement("span"); count.className = "project-count"; count.textContent = String(i);
      item.append(name, count); box.append(item);
    }
  });
  const bounds = await legend.evaluate(box => {
    const r = box.getBoundingClientRect();
    const items = [...box.querySelectorAll(".project-item")].map(node => node.getBoundingClientRect());
    return {
      inside: items.every(item => item.left >= r.left - 0.5 && item.right <= r.right + 0.5),
      wraps: new Set(items.map(item => Math.round(item.top))).size > 1,
      counts: [...box.querySelectorAll(".project-count")].every(node => node.getBoundingClientRect().width > 0),
      page: document.documentElement.scrollWidth <= innerWidth,
    };
  });
  assert.deepEqual(bounds, {inside: true, wraps: true, counts: true, page: true}, "long project names truncate and the row wraps");
  assert.deepEqual(errors, []);
  console.log(`${dashboard ? "dashboard" : "detail"} ${viewport.width}×${viewport.height}: control heights and project choice layout passed`);
  await page.close();
}

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME_BIN});
  try {
    for (const [width, height] of [[1440,960], [1024,600], [800,600], [390,844], [320,568], [1024,360]]) {
      for (const [index, file] of process.argv.slice(2).entries()) await check(browser, file, {width, height}, index === 1);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
