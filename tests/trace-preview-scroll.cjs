// Offline UI regression on an archived multi-Trace preview with timed events and issue fields.
// node tests/trace-preview-scroll.cjs <preview.html> (requires Playwright + Chromium)
const assert = require("node:assert/strict");
const {pathToFileURL} = require("node:url");
const {chromium} = require("playwright");

async function check(browser, width) {
  const page = await browser.newPage({viewport:{width, height:width < 800 ? 844 : 960}, offline:true});
  page.setDefaultTimeout(5000);
  const errors = [], requests = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("request", r => { if (/^https?:/.test(r.url())) requests.push(r.url()); });
  const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.goto(pathToFileURL(process.argv[2]).href);
  const expected = await page.evaluate(() => {
    const data = JSON.parse(document.getElementById("trace-data").textContent), cap = data.captures.at(-1);
    const rows = cap.nodes.map((n,i) => [i,n]).filter(([,n]) => n.type !== "TRACE");
    const times = rows.map(([i,n]) => [i, Date.parse(n.start_time)]);
    return {count:rows.length, ids:rows.map(([i])=>i), times:Object.fromEntries(times),
      fields:data.issues.reduce((n, item) => n + item.record.evidence.length, 0), traces:new Set(rows.map(([,n])=>n.trace_id)).size,
      issues:data.issues.map(it => it.record.id)};
  });
  assert.equal(await page.locator(".trace-overview").count(), expected.traces);
  assert.equal(await page.locator("#trace-count").textContent(), `${expected.count} 条`);
  assert.equal(await page.locator(".role-row").count(), 0);

  // Reach every original observation by scrolling alone, across window and Trace boundaries.
  const seen = await page.evaluate(async () => {
    const frame = document.getElementById("trace-scroll");
    const found = new Set();
    const step = Math.max(40, Math.floor(frame.clientHeight * .8));
    for (let y = 0; y < frame.scrollHeight + step; y += step) {
      frame.scrollTop = y;
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      for (const row of frame.querySelectorAll(".trace-row")) found.add(Number(row.dataset.nodeIndex));
    }
    return [...found];
  });
  assert.deepEqual([...seen].sort((a,b)=>a-b), expected.ids);
  const times = seen.map(i => expected.times[i]).filter(v => v != null);
  assert.deepEqual(times, [...times].sort((a,b)=>a-b), "Execution list is not chronological");
  await page.locator("#trace-scroll").evaluate(frame => frame.scrollTop = 0); await settle();
  const events = page.locator(".trace-row").filter({has:page.locator(".node-branch", {hasText:/^EVENT$/})});
  const first = events.nth(0), second = events.nth(1);
  const position = () => first.evaluate(el => {
    const r = el.getBoundingClientRect();
    return [scrollX,scrollY,document.getElementById("trace-scroll").scrollTop,document.getElementById("lane-scroll").scrollTop,r.x,r.y,r.width];
  });
  const original = await position();
  for (const row of [first,second]) {
    await row.click({position:{x:22,y:10}}); await settle();
    assert.deepEqual(await position(), original, "Inspecting an event displaced the list or page");
    assert.equal(await page.locator("#drawer-content [data-node-index]").getAttribute("data-node-index"), await row.getAttribute("data-node-index"));
  }
  await page.keyboard.press("Escape"); await settle();
  assert.deepEqual(await position(), original);
  assert.ok(await second.evaluate(el => el === document.activeElement));
  await second.click({position:{x:22,y:10}});
  await page.getByRole("button",{name:"定位到泳道",exact:true}).click();
  assert.equal(await page.locator(".current-tick:visible").getAttribute("data-node-index"), await second.getAttribute("data-node-index"));
  await page.keyboard.press("Escape");

  // From a focused Trace and a conflicting list filter, the round trip must restore both.
  await page.locator("#trace-scroll").evaluate(frame => frame.scrollTop = frame.scrollHeight / 2); await settle();
  const previousTop = await page.locator("#trace-scroll").evaluate(el => el.scrollTop);
  await page.locator(".lane-zoom").nth(1).click();
  assert.equal(await page.locator(".trace-overview").count(), 1);
  await page.locator("#type-filter").selectOption("EVENT");
  await page.locator("#trace-search").fill("read");
  const expectedScope = await page.locator("#trace-filter").inputValue();
  let verified = 0;
  for (const id of expected.issues) {
    await page.locator("#issue-picker summary").click();
    await page.locator(`#lane-issues [data-issue-id="${id}"]`).click();
    await settle();
    assert.equal(await page.locator(".trace-overview").count(), expected.traces);
    const count = await page.evaluate(id => JSON.parse(document.getElementById("trace-data").textContent).issues.find(it=>it.record.id===id).fragments.length,id);
    for (let i=0; i<count; i++) {
      if (i) await page.locator("#fragment-next").click();
      const part = await page.locator("#fragment-position").textContent();
      assert.ok(part.includes(`片段 ${i+1} / ${count}`));
      await page.locator("#fragment-fields").click();
      const links = page.locator("#drawer-content .evidence-link"), keys = await links.evaluateAll(all => all.map(b=>b.dataset.key));
      for (const key of keys) {
        const link = links.nth(keys.indexOf(key));
        const want = await page.evaluate(k => {
          const d = JSON.parse(document.getElementById("trace-data").textContent), ref = d.references[k];
          return {index:ref.location.node_index, pointer:ref.reference.pointer || "", value:ref.preview.text};
        }, key);
        await link.click(); await settle();
        assert.equal(await page.locator("#evidence-field").getAttribute("data-pointer"), want.pointer);
        assert.equal(await page.locator("#evidence-field pre").textContent(), want.value);
        assert.equal(Number(await page.locator("#drawer-content [data-node-index]").getAttribute("data-node-index")), want.index);
        const visibleMark = await page.evaluate(() => {
          const mark = document.querySelector(".current-tick:not([hidden])") || document.querySelector(".root-info[aria-current=true]");
          if (!mark) return false;
          const r = mark.getBoundingClientRect(), sheet = document.getElementById("drawer").getBoundingClientRect();
          return r.top >= document.querySelector(".topbar").getBoundingClientRect().bottom && r.bottom <= innerHeight &&
            (r.right <= sheet.left || r.bottom <= sheet.top);
        });
        assert.ok(visibleMark, "The evidence drawer covered the selected lane mark");
        await page.locator("#return-to-issue").click();
        assert.equal(await page.evaluate(()=>document.activeElement.dataset.key),key);
        verified++;
      }
      await page.keyboard.press("Escape");
    }
    await page.locator("#fragment-exit").click();
    assert.equal(await page.locator("#trace-filter").inputValue(), expectedScope);
    assert.equal(await page.locator("#type-filter").inputValue(),"EVENT");
    assert.equal(await page.locator("#trace-search").inputValue(),"read");
    if (await page.locator("#issue-picker").getAttribute("open") !== null) await page.keyboard.press("Escape");
  }
  await page.locator("#lane-reset").click(); await settle();
  assert.equal(await page.locator("#trace-count").textContent(), `${expected.count} 条`);
  assert.equal(await page.locator("#trace-scroll").evaluate(el=>el.scrollTop), previousTop);
  assert.equal(verified, expected.fields, "Every original issue field must be reachable from a fragment");
  assert.deepEqual(errors,[]); assert.deepEqual(requests,[]);
  console.log(`${width}px: ${seen.length} observations, ${verified} evidence links, stable event clicks`);
  await page.close();
}

(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROME_BIN});
  try { for (const width of [1440,390]) await check(browser,width); }
  finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
