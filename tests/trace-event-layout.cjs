// node tests/trace-event-layout.cjs <preview.html with an ERROR Event> (requires Playwright + Chromium)
const assert = require("node:assert/strict");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROME_BIN});
  try {
    for (const width of [1280,1512,1920,390]) {
      const page = await browser.newPage({viewport:{width,height:982}, offline:true});
      page.setDefaultTimeout(5000);
      const errors = [], requests = [];
      page.on("pageerror", error => errors.push(error.message));
      page.on("request", request => { if (/^https?:/.test(request.url())) requests.push(request.url()); });
      await page.goto(pathToFileURL(process.argv[2]).href);
      assert.equal(await page.locator(".stream-header .result-col").textContent(), "内容预览");
      const failure = await page.evaluate(() => {
        const capture = JSON.parse(document.getElementById("trace-data").textContent).captures.at(-1);
        const index = capture.timeline.order.find(i => capture.nodes[i].level === "ERROR");
        return {index, id:capture.nodes[index]?.id};
      });
      assert.ok(failure.id, "Use a fixture containing an explicitly failed Event");
      if (width > 800) {
        for (const collapsed of [false,true]) {
          if (await page.locator("#rail-toggle").getAttribute("aria-expanded") === String(collapsed)) await page.locator("#rail-toggle").click();
          const sizes = await page.evaluate(() => {
            const row = document.querySelector(".trace-row");
            const action = row.querySelector(".node-main").getBoundingClientRect();
            const preview = row.querySelector(".result-col").getBoundingClientRect();
            return {height:row.getBoundingClientRect().height, action:action.width, preview:preview.width, overflow:row.scrollWidth > row.clientWidth};
          });
          assert.equal(sizes.height, 40, "Fixed row height must match virtual scrolling");
          assert.ok(sizes.action >= 210 && sizes.action <= 320);
          assert.ok(sizes.preview > sizes.action, "Give content more space than action names");
          assert.equal(sizes.overflow, false);
        }
        await page.locator("#trace-filter").selectOption("0");
        assert.ok(await page.locator(".stream.single").count());
        assert.equal(await page.locator(".trace-row .trace-col").first().isVisible(), false);
        await page.locator("#trace-filter").selectOption("");
      }
      await page.locator("#trace-search").fill(failure.id);
      const row = page.locator(`.trace-row[data-node-index="${failure.index}"]`);
      await row.waitFor();
      assert.ok((await row.getAttribute("class")).includes("is-error"));
      assert.ok(await row.getByRole("img",{name:"执行异常"}).isVisible());
      assert.equal(await row.locator(".event-status").textContent(), "失败");
      assert.equal(await row.locator(".event-meta").isVisible(), width > 800);
      const appearance = () => row.evaluate(el => {
        const style = getComputedStyle(el);
        const rgb = style.backgroundColor.match(/\d+/g).map(Number);
        return {redTint:rgb[0] > rgb[1] && rgb[0] > rgb[2], marker:style.boxShadow !== "none", background:style.backgroundColor};
      });
      await page.mouse.move(0,0);
      const idle = await appearance();
      assert.ok(idle.redTint && idle.marker);
      await row.hover();
      const hover = await appearance();
      assert.ok(hover.redTint && hover.marker);
      assert.notEqual(hover.background,idle.background);
      await row.click();
      assert.equal(await row.getAttribute("aria-current"),"true");
      assert.ok(await page.locator("#drawer").isVisible());
      assert.ok(new URL(page.url()).hash.startsWith("#event="));
      const selected = await appearance();
      assert.ok(selected.redTint && selected.marker);
      assert.notEqual(selected.background,hover.background);
      // Fragment highlighting must not erase execution-failure markers.
      await row.evaluate(el => el.classList.add("in-fragment"));
      const fragment = await appearance();
      assert.ok(fragment.redTint && fragment.marker);
      assert.ok(await row.getByRole("img",{name:"执行异常"}).isVisible());
      assert.deepEqual(errors,[]);
      assert.deepEqual(requests,[], "Event previews must stay offline");
      console.log(`${width}px: column sizing, failure states, selection and offline rendering passed`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
