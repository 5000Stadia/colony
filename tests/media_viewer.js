const {chromium} = require('/opt/colony-test-playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.argv[2], artifacts = process.env.COLONY_TEST_ARTIFACTS;
(async () => {
  const browser = await chromium.launch({executablePath: '/opt/colony-test-chrome/chrome', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport: {width: 390, height: 844}, deviceScaleFactor: 1, isMobile: true, hasTouch: true, acceptDownloads: true});
  const errors = []; page.on('pageerror', error => errors.push(String(error)));
  try {
    await page.goto(base + '/media-fixture');
    await page.locator('[data-media=pdf]').scrollIntoViewIfNeeded();
    await page.waitForSelector('[data-media=pdf] canvas', {timeout: 30000});
    assert(await page.evaluate(() => { const incoming = document.createElement('template'); incoming.innerHTML = mediaIncoming;
      return mediaNormalize(document.getElementById('media-anchor').innerHTML) === mediaNormalize(incoming.innerHTML); }), 'rendered PDF canvas retains its existing Chat turn on refresh');
    assert.equal(await page.locator('.media-expand').count(), 4);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    const metrics = [];
    for (const [kind, index] of [['image', 0], ['table', 0], ['table', 1], ['pdf', 0]]) {
      await page.setViewportSize({width: 390, height: 844});
      const button = page.locator(`[data-media=${kind}] .media-expand`).nth(index);
      await button.scrollIntoViewIfNeeded();
      const before = await page.evaluate(() => ({y: scrollY, anchor: document.getElementById('media-anchor').getBoundingClientRect().top, composer: document.getElementById('media-composer').getBoundingClientRect().top}));
      await button.click(); await page.waitForSelector('.media-viewer[open]');
      if (kind === 'pdf') await page.waitForSelector('.media-content canvas', {timeout: 30000});
      assert.equal(await page.locator('.media-download').isVisible(), kind !== 'table');
      const fit = await page.locator('.media-stage').evaluate(el => ({viewport: el.clientWidth, width: el.firstElementChild.offsetWidth, scrollWidth: el.scrollWidth}));
      assert(Math.abs(fit.viewport - fit.width) <= 1, 'initial content fits viewport');
      assert(fit.scrollWidth <= fit.viewport + 1, 'Fit has no unscaled overflow');
      for (const z of [1.5, 2, 2.5, 3]) {
        await page.locator(`[data-zoom="${z}"]`).click();
        await page.waitForFunction(z => Math.abs(document.querySelector('.media-content').offsetWidth - document.querySelector('.media-stage').clientWidth * z) < 2, z);
      }
      await page.locator('.media-stage').evaluate(el => { el.scrollLeft = 100; el.scrollTop = 100; });
      await page.setViewportSize({width: 844, height: 390});
      await page.waitForFunction(() => document.querySelector('.media-content').offsetWidth > 2400);
      assert(await page.locator('.media-viewer').evaluate(el => el.open));
      assert.equal(await page.locator('[data-zoom="3"]').getAttribute('aria-pressed'), 'true');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways page overflow after rotation');
      if (kind === 'pdf') {
        assert.equal(await page.locator('.media-pdf-page').count(), 3);
        await page.locator('.media-stage').evaluate(el => { el.scrollTop = el.scrollHeight; });
        await page.waitForSelector('.media-pdf-page:last-child canvas');
        assert(await page.locator('.media-content canvas').count() <= 3);
      }
      if (artifacts) { fs.mkdirSync(artifacts, {recursive: true}); await page.screenshot({path: `${artifacts}/${kind}-${index}-zoom3-landscape.png`}); }
      if (kind === 'table') {
        const chart = await page.locator('.media-chart table').evaluate(el => ({width: el.offsetWidth, header: el.rows[0].cells[0].offsetHeight}));
        assert(chart.header < 100, 'chart words remain words instead of letter stacks');
      }
      await page.locator('[data-zoom="1"]').click();
      if (kind === 'pdf') { await page.locator('.media-stage').evaluate(el => { el.scrollTop = 0; }); await page.waitForSelector('.media-pdf-page:first-child canvas'); }
      const reset = await page.locator('.media-stage').evaluate(el => ({viewport: el.clientWidth, width: el.firstElementChild.offsetWidth}));
      assert(Math.abs(reset.viewport - reset.width) <= 1);
      if (artifacts) { fs.mkdirSync(artifacts, {recursive: true}); await page.screenshot({path: `${artifacts}/${kind}-${index}-landscape.png`}); }
      await page.setViewportSize({width: 390, height: 844});
      if (kind !== 'table') {
        const pending = page.waitForEvent('download'); await page.locator('.media-download').click();
        const download = await pending; assert(download.suggestedFilename().endsWith(kind === 'pdf' ? '.pdf' : '.svg'));
      }
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.querySelector('.media-viewer').open);
      const after = await page.evaluate(() => ({y: scrollY, anchor: document.getElementById('media-anchor').getBoundingClientRect().top, composer: document.getElementById('media-composer').getBoundingClientRect().top}));
      assert(Math.abs(before.y - after.y) <= 1, 'Chat scroll preserved');
      assert(Math.abs(before.anchor - after.anchor) <= 1, 'reading anchor preserved');
      assert(Math.abs(before.composer - after.composer) <= 1, 'composer preserved'); metrics.push({kind, before, after, fit});
    }
    // Closing in the rotated orientation preserves the selected picture despite preceding prose reflow.
    await page.setViewportSize({width: 390, height: 844});
    const selected = page.locator('[data-media=image]'); await selected.scrollIntoViewIfNeeded();
    const anchorTop = await selected.evaluate(el => el.getBoundingClientRect().top);
    await selected.locator('.media-expand').click(); await page.setViewportSize({width: 844, height: 390});
    await page.locator('.media-close').click();
    assert(Math.abs(await selected.evaluate(el => el.getBoundingClientRect().top) - anchorTop) <= 1, 'selected media anchor preserved when closing in landscape');
    await page.setViewportSize({width: 390, height: 844});
    // A navigation already fetching when Expand is tapped must not replace the underlying Chat.
    let release, requested, first = true; const ready = new Promise(resolve => requested = resolve);
    await page.route('**/pending-nav', async route => { if (first) { first = false; requested(); await new Promise(resolve => release = resolve); }
      await route.fulfill({contentType: 'text/html', body: '<main id="replaced">Navigated</main>'}); });
    await page.evaluate(url => { window.pendingMediaNav = colonyNav.go(url); }, base + '/pending-nav'); await ready;
    await page.locator('[data-media=image] .media-expand').click(); release();
    await page.evaluate(() => window.pendingMediaNav);
    assert.equal(await page.locator('#media-anchor').count(), 1);
    await page.locator('.media-close').click();
    for (const kind of ['plain', 'abort']) {
      let release, requested; const ready = new Promise(resolve => requested = resolve);
      await page.route('**/pending-' + kind, async route => { requested(); await new Promise(resolve => release = resolve);
        if (kind === 'abort') await route.abort(); else await route.fulfill({contentType: 'text/plain', body: 'A plain response'}); });
      await page.evaluate(url => { window.pendingMediaNav = colonyNav.go(url); }, base + '/pending-' + kind); await ready;
      await page.locator('[data-media=image] .media-expand').click(); release();
      await page.evaluate(() => window.pendingMediaNav);
      assert.equal(await page.locator('#media-anchor').count(), 1, 'failed or non-HTML navigation stays behind viewer');
      await page.evaluate(() => { colonyPlayer.busy = () => false; colonyNav.go('/blocked'); colonyNav.replace('/blocked'); colonyNav.reload(); colonyPlayer.busy = () => true; });
      assert(await page.locator('.media-viewer').evaluate(el => el.open));
      await page.locator('.media-close').click();
    }
    await page.evaluate(url => colonyNav.go(url), base + '/pending-nav');
    assert.equal(await page.locator('#replaced').count(), 1);
    await page.waitForSelector('.media-viewer', {state: 'attached'});
    // The viewer survives navigation's body replacement and also works without native dialog/fullscreen APIs.
    await page.goto(base + '/media-fixture');
    await page.evaluate(() => { HTMLDialogElement.prototype.showModal = undefined; });
    await page.locator('[data-media=image] .media-expand').click();
    assert.equal(await page.locator('main').evaluate(el => el.inert), true);
    assert.equal(await page.locator('.media-viewer').getAttribute('aria-modal'), 'true');
    await page.locator('.media-stage').focus(); await page.keyboard.press('Tab');
    assert(await page.locator('.media-viewer').evaluate(el => el.contains(document.activeElement)));
    await page.locator('.media-close').click();
    assert.equal(await page.locator('main').evaluate(el => el.inert), false);
    assert.deepEqual(errors, []);
    if (artifacts) fs.writeFileSync(`${artifacts}/metrics.json`, JSON.stringify(metrics, null, 2));
    console.log('PASS phone image, chart and multipage PDF; zoom, rotation, downloads, anchors and deferred navigation');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
