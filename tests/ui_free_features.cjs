/* Browser review on Ryzen: NODE_PATH=.tools/node_modules node tests/ui_free_features.cjs */
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.VISUAL_BRAIN_TEST_URL || 'http://127.0.0.1:8877';
const root = path.resolve(__dirname, '..');
const evidence = path.join(root, '.runs', 'screenshots');
fs.mkdirSync(evidence, {recursive: true});

(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream']});
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, deviceScaleFactor: 1});
  const errors = [];
  page.on('pageerror', error => errors.push(error.stack || error.message));
  try {
    await page.goto(base + '/board');
    await page.locator('#boards [data-board]').first().waitFor();
    const sample = page.locator('#empty-sample');
    if (await sample.isVisible()) await sample.click();
    await page.locator('#refresh-library').click();
    const boardId = await page.evaluate(async () => {
      const data = await (await fetch('/api/bootstrap')).json();
      return data.items.find(item => item.kind === 'image')?.board_id;
    });
    if (!boardId) throw new Error('No sample image board available');
    await page.evaluate(id => {state.boardId = id;state.selected.clear();renderAll();}, boardId);

    await page.locator('#open-taste-map').click();
    await page.locator('.taste-group').first().waitFor();
    await page.screenshot({path: path.join(evidence, 'taste-desktop.png')});
    await page.locator('#close-taste-map').click();

    await page.locator('#open-point-reopen').click();
    await page.locator('#point-file-input').setInputFiles(path.join(root, 'demo-seed', 'images', 'e0084.jpg'));
    await page.locator('.point-match').first().waitFor();
    await page.screenshot({path: path.join(evidence, 'point-desktop.png')});
    await page.locator('#close-point-reopen').click();

    await page.locator('#open-point-reopen').click();
    await page.locator('#point-start').click();
    await page.waitForFunction(() => document.querySelector('#point-video').videoWidth > 0);
    if (await page.locator('#point-capture').isDisabled()) throw new Error('Camera frame cannot be captured');
    await page.locator('#point-capture').click();
    await page.locator('#point-status').getByText('The frame was not saved.', {exact: false}).waitFor();
    await page.locator('#point-video').evaluate(video => {window.__testCameraTrack = video.srcObject.getVideoTracks()[0];});
    await page.locator('#close-point-reopen').click();
    await page.waitForFunction(() => document.querySelector('#point-video').srcObject === null);
    const cameraStopped = await page.evaluate(() => window.__testCameraTrack.readyState === 'ended');
    if (!cameraStopped) throw new Error('Camera track remained open after closing search');

    await page.locator('#open-walk-board').click();
    await page.locator('.walk-panel').first().waitFor();
    const walkSettled = page.locator('#walk-scene').evaluate(el => new Promise(resolve => el.addEventListener('transitionend', resolve, {once:true})));
    await page.locator('#walk-next').click();
    await walkSettled;
    const walkRect = await page.locator('.walk-panel').nth(1).evaluate(el => {
      const r = el.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};
    });
    if (walkRect.w < 20 || walkRect.x > 1440 || walkRect.x + walkRect.w < 0) throw new Error('Walk panel outside view: ' + JSON.stringify(walkRect));
    await page.screenshot({path: path.join(evidence, 'walk-desktop.png')});
    await page.locator('#close-walk').click();

    await page.locator('#open-critique').click();
    await page.locator('.critique-target').first().evaluate(target => {
      const rect = target.getBoundingClientRect();
      target.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: rect.left + 18, clientY: rect.top + 18}));
    });
    await page.locator('#critique-body').fill('Please adjust this spot.');
    await page.locator('#critique-post').click();
    await page.locator('.critique-note').first().waitFor();
    await page.locator('#critique-create-link').click();
    await page.locator('#critique-link-row:not([hidden])').waitFor();
    const privateUrl = await page.locator('#critique-link').inputValue();
    await page.screenshot({path: path.join(evidence, 'critique-owner-desktop.png')});
    await page.locator('#close-critique').click();
    const guest = await browser.newPage({viewport: {width: 1280, height: 800}});
    guest.on('pageerror', error => errors.push(error.message));
    await guest.goto(privateUrl);
    await guest.locator('.review-object').first().waitFor();
    await guest.screenshot({path: path.join(evidence, 'critique-guest-desktop.png')});
    await guest.close();

    await page.locator('#model-file-input').setInputFiles(process.env.VISUAL_BRAIN_TEST_MODEL_FILE || path.join(root, '.runs', 'Box.glb'));
    await page.waitForFunction(() => [...state.selected].some(id => state.items.find(item => item.id === id)?.kind === 'model'));
    const importedModelId = await page.evaluate(() => state.items.find(item => state.selected.has(item.id) && item.kind === 'model').state.model_id);
    await page.locator(`[data-open-model="${importedModelId}"]`).click();
    await page.locator('#model-dialog[open]').waitFor();
    await page.locator('#model-detail-viewer').evaluate(async viewer => {
      await customElements.whenDefined('model-viewer');
      if (!viewer.modelIsVisible) await new Promise((resolve, reject) => {viewer.addEventListener('load', resolve, {once:true});viewer.addEventListener('error', reject, {once:true});});
    });
    await page.locator('#model-height').fill('100');
    await page.locator('#model-height').dispatchEvent('change');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    if (errors.length) throw new Error('After first height: ' + errors.join(' | '));
    const firstScale = await page.locator('#model-detail-viewer').evaluate(viewer => Number(viewer.scale.split(' ')[0]));
    await page.locator('#model-height').fill('200');
    await page.locator('#model-height').dispatchEvent('change');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    if (errors.length) throw new Error('After second height: ' + errors.join(' | '));
    const secondScale = await page.locator('#model-detail-viewer').evaluate(viewer => Number(viewer.scale.split(' ')[0]));
    if (Math.abs(secondScale / firstScale - 2) > .01) throw new Error(`Room placement scale drifted: ${firstScale} → ${secondScale}`);
    const imageDownload = page.waitForEvent('download');
    await page.locator('#snapshot-model').click();
    const savedImage = await imageDownload;
    if (!savedImage.suggestedFilename().endsWith('-view.png')) throw new Error('3D view did not download as PNG');
    const imagePath = path.join(evidence, 'model-snapshot.png');
    await savedImage.saveAs(imagePath);
    if (!fs.readFileSync(imagePath).subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])))
      throw new Error('Saved 3D view is not a PNG image');
    await page.screenshot({path: path.join(evidence, 'model-desktop.png')});
    await page.locator('#close-model-dialog').click();

    await page.locator('.item.image').first().click();
    await page.locator('[data-action="source-trail"]').click();
    await page.locator('#asset-source-details[open]').waitFor();
    await page.screenshot({path: path.join(evidence, 'source-desktop.png')});
    await page.locator('#asset-cancel').click();

    await page.setViewportSize({width: 390, height: 844});
    await page.locator('#sidebar-toggle').click();
    await page.locator('#open-taste-map').click();
    await page.locator('.taste-group').first().waitFor();
    await page.screenshot({path: path.join(evidence, 'taste-mobile.png')});
    await page.locator('#close-taste-map').click();
    await page.locator('#open-walk-board').click();
    const mobileWalkRect = await page.locator('.walk-panel').first().evaluate(el => {
      const rect = el.getBoundingClientRect(); return {x: rect.x, width: rect.width};
    });
    if (mobileWalkRect.width < 220 || mobileWalkRect.x < 0 || mobileWalkRect.x + mobileWalkRect.width > 390)
      throw new Error('Mobile walkthrough card is too small or clipped: ' + JSON.stringify(mobileWalkRect));
    await page.screenshot({path: path.join(evidence, 'walk-mobile.png')});
    await page.locator('#close-walk').click();
    await page.locator('#open-point-reopen').click();
    await page.screenshot({path: path.join(evidence, 'point-mobile.png')});
    await page.locator('#close-point-reopen').click();
    await page.locator('#open-critique').click();
    await page.locator('.critique-object').first().waitFor();
    await page.screenshot({path: path.join(evidence, 'critique-mobile.png')});
    await page.locator('#close-critique').click();
    if (importedModelId) {
      await page.evaluate(id => window.openModelDetail(id), importedModelId);
      await page.waitForFunction(() => document.querySelector('#model-detail-viewer').loaded, null, {timeout: 15000});
      await page.locator('#model-detail-viewer').evaluate(async viewer => {
        await viewer.updateComplete;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      await page.screenshot({path: path.join(evidence, 'model-mobile.png')});
      await page.locator('#close-model-dialog').click();
    }
    const generated = await page.evaluate(() => {
      const entry = state.models.find(model => model.source_asset_id && model.status === 'ready');
      const item = state.items.find(item => item.kind === 'model' && item.state.model_id === entry?.id);
      return item ? {boardId: item.board_id, modelId: entry.id} : null;
    });
    if (generated) {
      await page.setViewportSize({width: 1440, height: 900});
      await page.evaluate(id => {state.boardId = id;state.selected.clear();renderAll();}, generated.boardId);
      await page.locator('.item.model [data-open-model]').first().click();
      await page.locator('#model-detail-viewer').evaluate(async viewer => {
        await customElements.whenDefined('model-viewer');
        if (!viewer.modelIsVisible) await new Promise((resolve, reject) => {viewer.addEventListener('load', resolve, {once:true});viewer.addEventListener('error', reject, {once:true});});
        if (!(viewer.getDimensions()?.y > 0)) throw new Error('Generated model has no visible height');
      });
      await page.screenshot({path: path.join(evidence, 'model-generated.png')});
      await page.locator('#close-model-dialog').click();
    }
    if (process.env.VISUAL_BRAIN_VIEW_MODEL) {
      await page.evaluate(id => window.openModelDetail(id), process.env.VISUAL_BRAIN_VIEW_MODEL);
      await page.locator('#model-detail-viewer').evaluate(async viewer => {
        if (!viewer.modelIsVisible) await new Promise((resolve, reject) => {viewer.addEventListener('load', resolve, {once:true});viewer.addEventListener('error', reject, {once:true});});
      });
      await page.screenshot({path: path.join(evidence, 'model-compare.png')});
      await page.locator('#close-model-dialog').click();
    }
    if (process.env.VISUAL_BRAIN_TEST_3D_CREATE) {
      const source = await page.evaluate(() => {
        const image = state.items.find(item => item.kind === 'image' && item.board_id === state.boardId);
        state.selected = new Set([image.id]);renderAll();return state.boardId;
      });
      await page.locator('[data-action="go-3d"]').click();
      await page.waitForFunction(previous => state.boardId !== previous && state.items.some(item => item.kind === 'model' && item.board_id === state.boardId), source);
    }
    if (errors.length) throw new Error('Page errors: ' + errors.join(' | '));
    console.log(JSON.stringify({status: 'ok', screenshots: evidence, privateReviewOpened: true}));
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
