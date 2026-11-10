// node tests/trace-controls.cjs <preview.html> <index.html> (requires Playwright + Chromium)
const assert = require("node:assert/strict");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

async function check(browser, file, viewport, dashboard) {
  const page = await browser.newPage({viewport, offline: true});
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.goto(pathToFileURL(file).href);
  if (dashboard) await page.locator("#tab-category").click();
  const summary = page.locator("#project-summary"), choice = page.locator("#project-choice");
  const menu = page.locator(".project-menu");
  const heights = await page.locator('input:not([type=checkbox]):visible, select:visible, #project-summary').evaluateAll(items =>
    items.map(item => [item.id, item.getBoundingClientRect().height]));
  assert.ok(heights.every(([, height]) => height === 34), JSON.stringify(heights));
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "page overflows horizontally");
  // Exercise long lists and names even when the supplied archive has only a few projects.
  await page.locator("#project-filter").evaluate(filter => {
    for (let i = 0; i < 24; i++) {
      const label = document.createElement("label"), input = document.createElement("input");
      input.type = "checkbox";
      label.append(input, `测试项目-${i}-` + "long-project-name".repeat(8));
      filter.append(label);
    }
  });
  const insideViewport = async () => {
    await settle();
    const bounds = await menu.evaluate(node => {
      const r = node.getBoundingClientRect();
      return {left: r.left, right: r.right, top: r.top, bottom: r.bottom,
        width: innerWidth, height: innerHeight, overflow: node.scrollWidth > node.clientWidth,
        reachable: [[r.left + 2, r.top + 2], [r.right - 2, r.bottom - 2]].every(([x,y]) => node.contains(document.elementFromPoint(x,y)))};
    });
    assert.ok(bounds.left >= 8 && bounds.right <= bounds.width - 8 && bounds.top >= 8 && bounds.bottom <= bounds.height - 8, JSON.stringify(bounds));
    assert.equal(bounds.overflow, false, "project names overflow the menu");
    assert.equal(bounds.reachable, true, "another element covers the menu");
  };
  await summary.click();
  await insideViewport();
  const checkbox = page.locator("#project-filter input").last();
  assert.deepEqual(await checkbox.evaluate(node => [node.offsetWidth, node.offsetHeight]), [16, 16]);
  await checkbox.check();
  assert.equal(await choice.getAttribute("open"), "", "checkbox selection closes the menu");
  await insideViewport();
  await page.locator("#project-clear").click();
  assert.equal(await choice.getAttribute("open"), "", "clear closes the menu");
  const outside = page.locator(dashboard ? "#tab-category" : "#issue-search");
  await outside.evaluate(node => node.addEventListener("click", event => event.stopPropagation(), {once: true}));
  await outside.click();
  assert.equal(await choice.getAttribute("open"), null, "outside click must close even when bubbling is stopped");
  await summary.click();
  await page.evaluate(() => {
    window.escapedToPage = false;
    document.addEventListener("keydown", () => { window.escapedToPage = true; }, {once: true});
  });
  await page.keyboard.press("Escape");
  assert.equal(await choice.getAttribute("open"), null);
  assert.equal(await summary.evaluate(node => node === document.activeElement), true);
  assert.equal(await page.evaluate(() => window.escapedToPage), false, "Escape must not also close page details");
  await summary.click();
  await page.locator(dashboard ? "#dash-stats" : "#lane-title").click();
  assert.equal(await choice.getAttribute("open"), null, "clicking page content closes the menu");
  // Force both viewport edges and a lower trigger, independent of the archive's content height.
  await choice.evaluate(node => { node.style.cssText = "position:fixed;right:8px;bottom:8px;z-index:25"; });
  await summary.click();
  await insideViewport();
  assert.ok((await menu.boundingBox()).y < (await summary.boundingBox()).y, "menu should open upwards near the bottom");
  await page.setViewportSize({width: 320, height: 360});
  await insideViewport();
  await page.keyboard.press("Escape");
  assert.deepEqual(errors, []);
  console.log(`${dashboard ? "dashboard" : "detail"} ${viewport.width}×${viewport.height}: controls, menu bounds, scroll, resize and dismissal passed`);
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
