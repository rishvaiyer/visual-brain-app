// Films the silent demo: browse a page, hover an image, click Save, open the board.
// Usage: node film.js <extension dir> <out dir> <pairing key file>
// Needs a Visual Brain server on 127.0.0.1:8777 and the sample site on 127.0.0.1:8793.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const [EXT, OUT, KEY_FILE] = process.argv.slice(2).map((p) => path.resolve(p));
const BOARD = "http://127.0.0.1:8777";
const SITE = "http://127.0.0.1:8793/";
const W = 1280, H = 720;

const CURSOR = `
(() => {
  const c = document.createElement("div");
  c.id = "__vb_cursor";
  c.style.cssText = "position:fixed;left:0;top:0;width:22px;height:22px;pointer-events:none;z-index:2147483647;transition:transform .06s linear;filter:drop-shadow(0 1px 2px rgba(0,0,0,.35))";
  c.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M5 3l14 8-6.5 1.5L10 19z" fill="#fff" stroke="#111" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  const ring = document.createElement("div");
  ring.style.cssText = "position:fixed;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;border:2px solid #d98b5a;pointer-events:none;z-index:2147483646;opacity:0;transition:opacity .35s,transform .35s";
  const add = () => { if (document.body && !c.isConnected) { document.body.append(c, ring); } };
  document.addEventListener("DOMContentLoaded", add); add();
  window.addEventListener("mousemove", (e) => { add(); c.style.transform = "translate(" + e.clientX + "px," + e.clientY + "px)"; }, true);
  window.addEventListener("mousedown", (e) => { ring.style.left = e.clientX + "px"; ring.style.top = e.clientY + "px"; ring.style.opacity = "1"; ring.style.transform = "scale(.5)"; requestAnimationFrame(() => { ring.style.opacity = "0"; ring.style.transform = "scale(1.4)"; }); }, true);
})();`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function glide(page, x, y, ms = 650) {
  const steps = Math.max(12, Math.round(ms / 16));
  await page.mouse.move(x, y, { steps });
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const ctx = await chromium.launchPersistentContext(path.join(OUT, "profile"), {
    channel: "chromium", headless: true, viewport: { width: W, height: H }, deviceScaleFactor: 1,
    recordVideo: { dir: path.join(OUT, "raw"), size: { width: W, height: H } },
    args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`, "--hide-scrollbars"],
  });
  await ctx.addInitScript(CURSOR);
  let [sw] = ctx.serviceWorkers();
  if (!sw) sw = await ctx.waitForEvent("serviceworker");
  const key = fs.readFileSync(KEY_FILE, "utf8").trim();
  await sw.evaluate(({ url, key }) => chrome.storage.local.set({ settings: { backendUrl: url, pairingKey: key, favorite: false } }), { url: BOARD, key });
  await sw.evaluate(() => chrome.runtime.sendMessage({ type: "VB_REFRESH_BOARDS" }).catch(() => {}));

  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.mouse.move(640, 400);

  // 1. Browsing a website.
  await page.goto(SITE, { waitUntil: "networkidle" });
  await sleep(1400);
  await page.mouse.wheel(0, 140); await sleep(700);

  const saveButton = page.getByRole("button", { name: "Save image to Visual Brain" });
  async function clipImage(n) {
    const img = page.locator("article img").nth(n);
    await img.scrollIntoViewIfNeeded();
    const b = await img.boundingBox();
    await glide(page, b.x + b.width * 0.55, b.y + b.height * 0.55, 700);
    await sleep(650);
    const bb = await saveButton.boundingBox();
    await glide(page, bb.x + bb.width / 2, bb.y + bb.height / 2, 450);
    await sleep(250);
    await page.mouse.down(); await sleep(90); await page.mouse.up();
    await sleep(1500);
  }
  await clipImage(0);
  await clipImage(4);
  await page.mouse.wheel(0, 420); await sleep(700);
  await clipImage(7);

  // 2. The board.
  await page.goto(BOARD + "/board", { waitUntil: "networkidle" });
  await sleep(1800);
  const items = page.locator(".item.image");
  await items.first().waitFor();
  const b = await items.first().boundingBox();
  await glide(page, b.x + b.width / 2, b.y + b.height / 2, 700);
  await page.mouse.down();
  await glide(page, b.x + b.width / 2 + 260, b.y + b.height / 2 + 120, 900);
  await page.mouse.up();
  await sleep(900);

  // 3. Search.
  const q = page.locator("#visual-query");
  const qb = await q.boundingBox();
  await glide(page, qb.x + 60, qb.y + qb.height / 2, 600);
  await page.mouse.down(); await page.mouse.up();
  await page.keyboard.type("sunday", { delay: 110 });
  await sleep(1700);
  await page.keyboard.press("Escape");
  await glide(page, 640, 500, 500);
  await page.locator("#visual-clear").click().catch(() => {});
  await sleep(900);

  // 4. Save As.
  const save = page.locator("#save-as");
  const sb = await save.boundingBox();
  await glide(page, sb.x + sb.width / 2, sb.y + sb.height / 2, 600);
  await page.mouse.down(); await page.mouse.up();
  await sleep(1600);
  await page.keyboard.press("Escape");
  await glide(page, 760, 420, 500);
  await sleep(1600);

  const video = page.video();
  await ctx.close();
  const file = await video.path();
  fs.copyFileSync(file, path.join(OUT, "demo.webm"));
  console.log("video:", path.join(OUT, "demo.webm"));
})().catch((e) => { console.error(e); process.exit(1); });
