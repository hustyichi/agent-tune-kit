// node tests/trace-issue-cards.cjs <preview.html with a timed issue fragment> [screenshot directory]
// Requires Playwright + Chromium, like the other browser layout checks.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");

const input = fs.readFileSync(process.argv[2], "utf8");
const pattern = /(<script id="trace-data" type="application\/json">)([\s\S]*?)(<\/script>)/;
const data = JSON.parse(input.match(pattern)[2]);
const item = data.issues.find(issue => issue.fragments.some(part => part.anchors.length && part.start != null));
assert.ok(item, "Supply a generated preview with a timed issue fragment");
const fragment = item.fragments.find(part => part.anchors.length && part.start != null);
const longTitle = "POS 首版订单 mock 的明细、合计和币种不一致，需要核对多个项目与完整订单数据。".repeat(5);
const projects = [
  {id: "server", name: "magic-code-server"},
  {id: "long", name: "long-project-name".repeat(10)},
  {id: "pos", name: "POS 订单前端"},
];
item.record.title = longTitle;
item.record.category = "mock";
item.linked_projects = projects;
item.fragments = Array.from({length: 4}, (_, i) => ({...fragment, id: `S${i+1}`, summary: `证据 ${i+1}：核对订单金额、税额和底部合计，保留长摘要的完整定位入口。`}));
data.projects = Object.fromEntries(projects.map(project => [project.id, project]));
data.categories.mock = {name: "Mock 业务数据不一致"};
data.dashboard.available = true;
const normal = structuredClone(item);
normal.record.id = "ISSUE-NORMAL";
normal.record.title = "POS 首版订单 mock 的明细、合计和币种不一致";
normal.linked_projects = [];
normal.fragments = normal.fragments.slice(0, 2);
data.issues = [item, normal];
const html = input.replace(pattern, (_all, start, _data, end) => start + JSON.stringify(data).replaceAll("<", "\\u003c") + end);

(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROME_BIN});
  try {
    for (const [width, height] of [[1920,1080], [1440,960], [1280,900], [1024,600], [960,800], [800,600], [390,844], [320,568], [320,360]]) {
      const page = await browser.newPage({viewport:{width,height}});
      page.setDefaultTimeout(5000);
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      await page.route("**/*", route => route.request().url().startsWith("http://atk-layout.test/")
        ? route.fulfill({contentType:"text/html", body:html}) : route.abort());
      await page.goto("http://atk-layout.test/preview.html");
      const card = page.locator(".issue-card").first();
      assert.equal(await card.locator(".seg-chip:visible").count(), 2);
      assert.equal(await card.locator(".issue-title").getAttribute("title"), longTitle);
      assert.equal(await card.locator(".issue-projects a").count(), 3);
      assert.ok((await card.locator(".issue-projects a").first().getAttribute("href")).includes("project=server"));
      const bounds = await page.evaluate(() => {
        const card = document.querySelector(".issue-card"), title = card.querySelector("h3"), projects = card.querySelector(".issue-projects");
        const rail = document.getElementById("issue-rail"), main = document.querySelector(".main-pane");
        const titleButton = card.querySelector(".issue-title");
        const cards = [...document.querySelectorAll(".issue-card")].map(node => node.getBoundingClientRect());
        return {
          pageOverflow: document.documentElement.scrollWidth > innerWidth,
          cardOverflow: card.scrollWidth > card.clientWidth,
          projectOverflow: projects.scrollWidth > projects.clientWidth,
          projectAfterTitle: projects.getBoundingClientRect().top >= title.getBoundingClientRect().bottom,
          titleHeight: titleButton.getBoundingClientRect().height,
          titleFullHeight: titleButton.scrollHeight,
          lineHeight: parseFloat(getComputedStyle(titleButton).lineHeight),
          railWidth: rail.getBoundingClientRect().width,
          stacked: main.getBoundingClientRect().top >= rail.getBoundingClientRect().bottom,
          cardsOverlap: cards[1].top < cards[0].bottom,
        };
      });
      assert.equal(bounds.pageOverflow, false, JSON.stringify(bounds));
      assert.equal(bounds.cardOverflow || bounds.projectOverflow || bounds.cardsOverlap, false, JSON.stringify(bounds));
      assert.ok(bounds.projectAfterTitle);
      assert.ok(bounds.titleHeight <= bounds.lineHeight * 3 + 1);
      assert.ok(bounds.titleFullHeight > bounds.titleHeight, "Long titles must be clamped without losing their full text");
      if (width > 960) assert.equal(bounds.railWidth, Math.max(320, Math.min(400, width * .25)));
      else assert.ok(bounds.stacked);
      if (process.argv[3] && [1440,390,320].includes(width) && height > 400) {
        fs.mkdirSync(process.argv[3], {recursive:true});
        await page.screenshot({path:path.join(process.argv[3], `issue-cards-${width}.png`)});
      }
      await card.locator(".seg-more > summary").click();
      assert.equal(await card.locator(".seg-chip:visible").count(), 4);
      await card.locator(".seg-chip").nth(3).click();
      assert.equal(new URLSearchParams(new URL(page.url()).hash.slice(1)).get("seg"), "S4");
      assert.equal(await card.locator(".seg-chip").nth(3).getAttribute("aria-current"), "true");
      await page.goto(`http://atk-layout.test/preview.html#issue=${encodeURIComponent(item.record.id)}&seg=S3`);
      assert.equal(await card.locator(".seg-more").getAttribute("open"), "");
      assert.equal(await card.locator(".seg-chip").nth(2).getAttribute("aria-current"), "true");
      await card.getByRole("button", {name:"详情", exact:true}).click();
      assert.ok((await page.locator("#drawer-title").textContent()).includes(longTitle));
      await page.locator("#drawer-close").click();
      await page.locator("#rail-toggle").click();
      assert.equal(await card.locator(".issue-progress-label").isVisible(), false);
      if (width > 960) assert.equal((await page.locator("#issue-rail").boundingBox()).width, 44);
      await page.locator("#rail-toggle").click();
      assert.equal(await card.locator(".issue-title").isVisible(), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.locator("#fragment-exit").click();
      await page.locator("#issue-search").fill("ISSUE-NORMAL");
      assert.equal(await page.locator(".issue-card").count(), 1);
      assert.equal(await page.locator(".issue-card .seg-chip:visible").count(), 2);
      if (process.argv[3] && width === 1440) {
        await page.locator("#issue-rail").screenshot({path:path.join(process.argv[3], "issue-card-final.png")});
      }
      assert.deepEqual(errors, []);
      console.log(`${width}×${height}: title, projects, evidence navigation, details and collapse passed`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
