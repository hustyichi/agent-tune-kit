// node tests/trace-scroll-layout.cjs <preview with long lists and multi-segment issues>
const assert = require("node:assert/strict");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");
const settle = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const metrics = page => page.evaluate(() => {
  const rail = document.getElementById("issue-rail-list"), events = document.getElementById("trace-scroll");
  const main = document.querySelector(".main-pane"), stream = document.querySelector(".stream");
  return {
    page: document.scrollingElement.scrollHeight, width: document.documentElement.scrollWidth,
    blank: main.getBoundingClientRect().bottom - stream.getBoundingClientRect().bottom,
    rail: rail.scrollTop, events: events.scrollTop, eventHeight: events.clientHeight,
    overflows: events.scrollHeight > events.clientHeight, mainScroll: main.scrollTop,
    range: document.getElementById("event-visible-range")?.textContent,
    hint: document.getElementById("event-scroll-hint")?.textContent,
  };
});

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME_BIN});
  try {
    const page = await browser.newPage({viewport:{width:1512,height:736}, offline:true});
    page.setDefaultTimeout(5000);
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    const url = pathToFileURL(process.argv[2]).href;
    await page.goto(url);
    const expected = await page.evaluate(() => {
      const data = JSON.parse(document.getElementById("trace-data").textContent), capture = data.captures.at(-1);
      return {issue:data.issues.at(-1).record.id, event:capture.nodes[capture.timeline.order.at(-1)].id, order:capture.timeline.order};
    });
    const events = page.locator("#trace-scroll"), rail = page.locator("#issue-rail-list");
    for (const viewport of [{width:1512,height:736}, {width:1440,height:900}, {width:1280,height:600}]) {
      await page.setViewportSize(viewport);
      for (const [longIssues, longEvents] of [[true,true], [true,false], [false,true], [false,false]]) {
        await page.goto(url);
        await page.locator("#issue-search").fill(longIssues ? "" : expected.issue);
        await page.locator("#trace-search").fill(longEvents ? "" : expected.event);
        await settle(page);
        const state = await metrics(page);
        assert.equal(state.page, viewport.height, "Issue content must not stretch the desktop page");
        assert.ok(state.blank <= 15, `Unused space outside the Event panel: ${state.blank}px`);
        assert.ok(state.eventHeight >= 200);
        assert.ok(state.width <= viewport.width);
        assert.equal(state.overflows, longEvents);
        assert.equal(await page.locator("#timeline-panel").isVisible(), viewport.height > 680);
        if (!longEvents) assert.equal(state.hint, "已显示全部 1 条");
      }
    }
    await page.setViewportSize({width:1512,height:736});
    await page.goto(url); await settle(page);
    await rail.hover(); await page.mouse.wheel(0, 500);
    await page.waitForFunction(() => document.getElementById("issue-rail-list").scrollTop > 100);
    const railTop = (await metrics(page)).rail;
    assert.equal((await metrics(page)).events, 0);
    await events.hover(); await page.mouse.wheel(0, 400);
    await page.waitForFunction(() => document.getElementById("trace-scroll").scrollTop > 100);
    assert.equal((await metrics(page)).rail, railTop);

    // A one-row movement inside the overscan window must still update the range label.
    await events.evaluate(el => el.scrollTop = 40); await settle(page);
    assert.match((await metrics(page)).range, /^第 2–/);
    await events.evaluate(el => el.scrollTop = 0); await settle(page);
    await page.locator(".trace-row").first().click();
    await page.locator("#drawer").waitFor({state:"visible"});
    await page.keyboard.press("Escape");
    assert.equal((await metrics(page)).events, 0);
    assert.equal((await metrics(page)).rail, railTop);

    const locate = page.locator(".issue-card").last().locator(".card-actions button").first();
    await locate.scrollIntoViewIfNeeded();
    const before = await metrics(page);
    await locate.click(); await settle(page);
    assert.ok(await page.locator("#fragment-bar").isVisible());
    await page.setViewportSize({width:1512,height:1100}); await settle(page);
    assert.equal((await metrics(page)).hint, "片段已完整显示 · 可展开上下文");
    await page.locator("#fragment-context").click(); await settle(page);
    assert.ok((await metrics(page)).overflows);
    await page.locator("#fragment-context").click();
    await page.setViewportSize({width:1512,height:736}); await settle(page);
    await page.locator("#fragment-next").click(); await settle(page);
    assert.equal((await metrics(page)).mainScroll, 0);
    await page.locator("#fragment-exit").click(); await settle(page);
    assert.equal((await metrics(page)).events, before.events);
    assert.equal((await metrics(page)).rail, before.rail);

    const seen = await events.evaluate(async frame => {
      const seen = new Set(), step = Math.max(40, Math.floor(frame.clientHeight * .8));
      for (let top = 0; top < frame.scrollHeight + step; top += step) {
        frame.scrollTop = top;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        frame.querySelectorAll(".trace-row").forEach(row => seen.add(Number(row.dataset.nodeIndex)));
      }
      return [...seen];
    });
    assert.deepEqual(seen, expected.order, "Every event must remain reachable in chronological order");
    assert.equal((await metrics(page)).hint, "已到列表末尾");
    assert.ok(await events.evaluate(frame => frame.querySelector(".trace-row:last-child").getBoundingClientRect().bottom <= frame.getBoundingClientRect().bottom + 1));
    await page.locator("#trace-search").fill("__no_matching_event__"); await settle(page);
    assert.equal(await page.locator(".trace-row").count(), 0);
    assert.equal((await metrics(page)).hint, "");
    await page.locator("#trace-search").fill(""); await settle(page);
    const expandedHeight = (await metrics(page)).eventHeight;
    await page.locator("#timeline-toggle").click(); await settle(page);
    assert.ok((await metrics(page)).eventHeight > expandedHeight);
    await page.setViewportSize({width:1512,height:600}); await settle(page);
    await page.setViewportSize({width:1512,height:900}); await settle(page);
    assert.equal(await page.locator("#timeline-toggle").getAttribute("aria-expanded"), "false");
    await page.locator("#timeline-toggle").click();

    for (const viewport of [{width:390,height:844}, {width:1280,height:480}]) {
      await page.setViewportSize(viewport); await page.goto(url); await settle(page);
      const state = await metrics(page);
      assert.ok(state.width <= viewport.width);
      assert.ok(state.eventHeight >= 180);
      await events.evaluate(el => el.scrollTop = el.scrollHeight); await settle(page);
      assert.equal((await metrics(page)).hint, "已到列表末尾");
    }
    assert.deepEqual(errors, []);
    console.log("Independent panes, four length combinations, virtual rows, navigation, end states and responsive layout passed");
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
