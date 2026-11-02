/* Run on Ryzen: NODE_PATH=.tools/node_modules node tests/ui_extension_source.cjs */
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.route('https://source.example/**', route => route.fulfill({
      contentType: 'text/html',
      body: `<!doctype html><title>Fallback title</title>
        <meta property="og:title" content="Maker's ceramic lamp">
        <meta name="author" content="Example Maker">
        <meta name="license" content="CC BY 4.0">
        <link rel="license" href="https://creativecommons.org/licenses/by/4.0/">
        <img id="sample" src="https://source.example/lamp.jpg" alt="Lamp" style="width:240px;height:240px">`
    }));
    await page.goto('https://source.example/lamp');
    await page.evaluate(() => {
      window.captures = [];
      window.chrome = {runtime: {
        sendMessage: async message => {
          if (message.type === 'VB_GET_CAPTURE_SETTINGS') return {backendOrigin: 'http://127.0.0.1:8777'};
          if (message.type === 'VB_CAPTURE') {window.captures.push(message.capture);return {ok: true};}
          return {ok: true};
        },
        onMessage: {addListener: () => {}}
      }};
      window.VisualBrainCaptureTarget = {find: event => {
        const element = event.target.closest?.('#sample');
        return element ? {kind: 'image', element} : null;
      }};
    });
    await page.addScriptTag({path: path.resolve(__dirname, '../extension/content.js')});
    await page.locator('#sample').dispatchEvent('pointerover');
    await page.locator('#__visual_brain_capture_ui__').evaluate(host => host.shadowRoot.getElementById('save').click());
    await page.waitForFunction(() => window.captures.length === 1);
    const capture = await page.evaluate(() => window.captures[0]);
    assert.equal(capture.source_title, "Maker's ceramic lamp");
    assert.equal(capture.source_author, 'Example Maker');
    assert.equal(capture.source_license, 'CC BY 4.0');
    assert.equal(capture.source_license_url, 'https://creativecommons.org/licenses/by/4.0/');
    assert.equal(capture.source_url, 'https://source.example/lamp');
    console.log('PASS source details travel from page metadata into extension capture');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error);process.exitCode = 1;});
