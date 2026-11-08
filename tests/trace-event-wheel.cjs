// node tests/trace-event-wheel.cjs <long native trace preview.html> (requires Playwright + Chromium)
const assert = require("node:assert/strict");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

async function check(browser, url, viewport) {
  const page = await browser.newPage({viewport, offline:true});
  page.setDefaultTimeout(5000);
  try {
    await page.goto(url);
    const list = page.locator("#trace-scroll");
    const row = list.locator(".trace-row").nth(2);
    if (viewport.width <= 800) {
      await page.evaluate(() => window.scrollTo(0, document.scrollingElement.scrollHeight));
    }
    await row.scrollIntoViewIfNeeded();
    const index = await row.getAttribute("data-node-index");
    const box = await row.boundingBox();
    const point = {x:box.x + 22, y:box.y + box.height / 2};
    await page.mouse.click(point.x, point.y);
    const drawer = page.locator("#drawer");
    await drawer.waitFor({state:"visible"});
    const selected = drawer.locator("[data-node-index]");
    assert.equal(await selected.getAttribute("data-node-index"), index);
    const hash = new URL(page.url()).hash;
    assert.ok(hash.startsWith("#event="));

    const top = await list.evaluate(el => el.scrollTop);
    await page.mouse.wheel(0, 240);
    const down = await page.waitForFunction(previous => {
      const current = document.getElementById("trace-scroll").scrollTop;
      return current > previous && current;
    }, top, {timeout:2000});
    await page.mouse.wheel(0, -240);
    await page.waitForFunction(previous => document.getElementById("trace-scroll").scrollTop < previous,
      await down.jsonValue(), {timeout:2000});
    assert.ok(await drawer.isVisible());
    assert.equal(await selected.getAttribute("data-node-index"), index);
    assert.equal(new URL(page.url()).hash, hash);
    if (viewport.width <= 800) {
      const {y} = await drawer.boundingBox();
      await page.keyboard.press("ArrowDown");
      assert.equal((await drawer.boundingBox()).y, y, "Browsing adjacent Events moved the drawer");
    }
    console.log(`${viewport.width}×${viewport.height}: Event list scrolls in both directions with details open`);
  } finally {
    await page.close();
  }
}

(async () => {
  const url = pathToFileURL(process.argv[2]).href;
  const browser = await chromium.launch({executablePath:process.env.CHROME_BIN});
  try {
    for (const viewport of [{width:1440,height:900}, {width:390,height:844}]) await check(browser, url, viewport);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
