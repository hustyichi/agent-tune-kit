// Browser regression on a generated preview with two visible EVENT rows.
// node tests/trace-preview-scroll.cjs <preview.html> (requires Playwright + Chromium)
const assert = require("node:assert/strict");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

async function check(browser, width) {
  const page = await browser.newPage({viewport: {width, height: 960}, offline: true});
  await page.goto(pathToFileURL(process.argv[2]).href);
  const events = page.locator(".trace-row").filter({has: page.locator(".node-branch", {hasText: /^EVENT$/})});
  const first = events.nth(0), second = events.nth(1);
  await first.evaluate(el => window.scrollBy(0, el.getBoundingClientRect().top - 230));
  const settle = () => page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
  const position = () => first.evaluate(el => [scrollY, el.getBoundingClientRect().x, el.getBoundingClientRect().y]);
  await settle();
  const original = await position();
  for (const row of [first, second]) {
    await row.click({position: {x: 70, y: 16}});
    await settle();
    assert.deepEqual(await position(), original);
    assert.equal(await page.locator("#drawer-content [data-node-index]").getAttribute("data-node-index"),
      await row.getAttribute("data-node-index"));
  }
  await page.keyboard.press("Escape");
  await settle();
  assert.deepEqual(await position(), original);
  assert.ok(await second.evaluate(el => el === document.activeElement));
  await second.click({position: {x: 70, y: 16}});
  await page.getByRole("button", {name: "定位到泳道", exact: true}).click();
  assert.ok(await page.locator('#lane-chart [aria-current=true]').count());
  await page.close();
}

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME_BIN});
  try { for (const width of [1680, 390]) await check(browser, width); }
  finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
