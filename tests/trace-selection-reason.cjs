// node tests/trace-selection-reason.cjs <preview.html> (requires Playwright + Chromium)
const assert = require("node:assert/strict");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROME_BIN});
  try {
    for (const width of [1440,390]) {
      const page = await browser.newPage({viewport:{width,height:960}, offline:true});
      page.setDefaultTimeout(5000);
      const errors = [], requests = [], dialogs = [];
      page.on("pageerror", e => errors.push(e.message));
      page.on("request", r => { if (/^https?:/.test(r.url())) requests.push(r.url()); });
      page.on("dialog", async d => { dialogs.push(d.message()); await d.dismiss(); });
      await page.goto(pathToFileURL(process.argv[2]).href);
      const issues = await page.evaluate(() => JSON.parse(document.getElementById("trace-data").textContent).issues.map(i => i.record));
      assert.ok(issues.some(i => i.selection_reason), "Include a reason in the fixture");
      assert.ok(issues.some(i => !("selection_reason" in i)), "Include a legacy issue in the fixture");
      for (const issue of issues) {
        await page.locator(`.issue-card[data-issue-id="${issue.id}"]`).getByRole("button", {name:"详情",exact:true}).click();
        const reason = page.locator("#drawer-content > section").filter({has:page.getByRole("heading",{name:"保留理由",exact:true})});
        if (issue.selection_reason) {
          assert.ok(await reason.isVisible());
          assert.equal(await reason.locator("p").textContent(), issue.selection_reason);
          assert.equal(await reason.locator("p > *").count(), 0, "The reason must render as literal text");
        } else {
          assert.equal(await reason.count(), 0, "Do not invent reasons for legacy records");
        }
        await page.keyboard.press("Escape");
      }
      assert.deepEqual(errors, []);
      assert.deepEqual(requests, []);
      assert.deepEqual(dialogs, []);
      console.log(`${width}px: reasons visible as literal text; legacy issues preserved; offline`);
      await page.close();
    }
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
