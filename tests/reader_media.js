const {chromium} = require('/opt/colony-test-playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const [base, documentId, imageId, pdfId] = process.argv.slice(2), artifacts = process.env.COLONY_TEST_ARTIFACTS;
(async () => {
  const browser = await chromium.launch({executablePath: '/opt/colony-test-chrome/chrome', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, acceptDownloads: true});
  const errors = []; page.on('pageerror', error => errors.push(String(error)));
  try {
    await page.goto(base + '/?p=0&view=showcase');
    await page.locator(`#s-${documentId} a.open`).click();
    await page.waitForSelector('.reader [data-media=table]');
    assert(await page.locator('.readplay').isVisible(), 'document audio remains available');
    const play = await page.locator('.readplay').boundingBox(), edit = await page.locator('.readedit').boundingBox(), article = await page.locator('.reader').boundingBox();
    assert(edit.y < article.y && play.y < article.y, 'Edit and Play lead the document');
    assert(Math.abs(edit.y - play.y) <= 2, 'Edit and Play share the same action row');
    assert(play.x + play.width <= edit.x, 'document actions do not overlap');
    assert(play.height >= 44 && edit.height >= 44, 'document actions have touch-sized targets');
    if (artifacts) { fs.mkdirSync(artifacts, {recursive: true}); await page.screenshot({path: `${artifacts}/reader-actions-390.png`}); }
    await page.locator('.readfoot').scrollIntoViewIfNeeded();
    const comment = await page.locator('.readcomment > summary').boundingBox(), exits = await page.locator('.readfoot .readnav').boundingBox();
    assert(comment.y + comment.height <= exits.y, 'footer navigation stays below Comment');
    await page.locator('.readcomment > summary').click();
    await page.locator('.readcomment textarea').fill('A sample comment draft.');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'expanded comment fits the phone');
    if (artifacts) await page.screenshot({path: `${artifacts}/reader-footer-390.png`});
    await page.locator('.readcomment > summary').click();
    assert.equal(await page.locator('.reader .media-expand').count(), 4);
    assert.equal(await page.locator('.reader h1').getAttribute('id'), 'sample-inventory');
    assert.equal(await page.locator('.reader h2').getAttribute('id'), 'finish');
    const tables = page.locator('.reader .mdtable');
    assert.equal(await tables.count(), 2);
    const metrics = [];
    for (let i = 0; i < 2; i++) {
      await tables.nth(i).scrollIntoViewIfNeeded();
      const before = await page.evaluate(() => ({x: scrollX, y: scrollY, composer: document.getElementById('gc').getBoundingClientRect().top}));
      const dimensions = await tables.nth(i).evaluate(el => ({width: el.clientWidth, content: el.scrollWidth, headerHeight: el.querySelector('th').offsetHeight}));
      assert(dimensions.content > dimensions.width, 'wide table has its own scroll surface');
      assert(dimensions.headerHeight < 80, 'headers remain words, not letter stacks');
      const otherBefore = await tables.nth(1 - i).evaluate(el => el.scrollLeft);
      await tables.nth(i).evaluate(el => el.scrollLeft = 120);
      assert(await tables.nth(i).evaluate(el => el.scrollLeft) > 0, 'selected table scrolls horizontally');
      assert.equal(await tables.nth(1 - i).evaluate(el => el.scrollLeft), otherBefore, 'other table stays put');
      const after = await page.evaluate(() => ({x: scrollX, y: scrollY, composer: document.getElementById('gc').getBoundingClientRect().top}));
      assert.deepEqual(after, before, 'table scroll never moves page or composer');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways page overflow');
      metrics.push({i, dimensions, before, after});
    }
    if (artifacts) { fs.mkdirSync(artifacts, {recursive: true}); await page.screenshot({path: `${artifacts}/reader-inline-390.png`}); }
    for (const [kind, index] of [['table', 0], ['table', 1], ['image', 0], ['pdf', 0]]) {
      await page.setViewportSize({width: 390, height: 844});
      const media = page.locator(`.reader [data-media=${kind}]`).nth(index);
      await media.scrollIntoViewIfNeeded();
      if (kind === 'pdf') await media.locator('canvas').waitFor();
      const anchor = await media.evaluate(el => el.getBoundingClientRect().top);
      await media.locator('.media-expand').click();
      await page.waitForSelector('.media-viewer[open]');
      if (kind === 'pdf') await page.waitForSelector('.media-content canvas');
      const fit = await page.locator('.media-stage').evaluate(el => ({width: el.clientWidth, content: el.firstElementChild.offsetWidth, extent: el.scrollWidth}));
      assert(Math.abs(fit.width - fit.content) <= 1 && fit.extent <= fit.width + 1);
      for (const zoom of [1.5, 2, 2.5, 3]) await page.locator(`[data-zoom="${zoom}"]`).click();
      await page.setViewportSize({width: 844, height: 390});
      await page.waitForFunction(() => document.querySelector('.media-content').offsetWidth > 2400);
      assert.equal(await page.locator('[data-zoom="3"]').getAttribute('aria-pressed'), 'true');
      assert(await page.locator('.media-viewer').evaluate(el => el.open));
      assert.equal(await page.locator('.media-download').isVisible(), kind !== 'table');
      if (kind !== 'table') {
        const waiting = page.waitForEvent('download'); await page.locator('.media-download').click();
        const download = await waiting; assert(download.suggestedFilename().endsWith(kind === 'pdf' ? '.pdf' : '.svg'));
      }
      if (artifacts) await page.screenshot({path: `${artifacts}/reader-${kind}-${index}-landscape.png`});
      await page.keyboard.press('Escape');
      assert(Math.abs(await media.evaluate(el => el.getBoundingClientRect().top) - anchor) <= 1, 'reader media anchor survives closing while rotated');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    }
    // Direct Showcase media opens a Colony wrapper; underlying resources remain raw file URLs.
    for (const [id, kind] of [[imageId, 'image'], [pdfId, 'pdf']]) {
      await page.goto(base + '/?p=0&view=showcase');
      const opener = kind === 'image' ? page.locator(`#s-${id} a.shotlink`) : page.locator(`#s-${id} a.open`);
      if (kind === 'image') assert(!await opener.locator('img').getAttribute('src').then(src => src.includes('viewer=')));
      await opener.click(); await page.waitForSelector(`.reader [data-media=${kind}]`);
      assert(page.url().includes('viewer=1'));
      const source = await page.locator(`.reader [data-media=${kind}]`).getAttribute('data-src');
      assert(!source.includes('viewer='));
      await page.locator('.reader .media-expand').click();
      if (kind === 'pdf') await page.waitForSelector('.media-content canvas');
      const waiting = page.waitForEvent('download'); await page.locator('.media-download').click(); await waiting;
      await page.locator('.media-close').click();
      assert.equal(await page.locator('.readcomment form').getAttribute('action'), '/showcase/comment');
    }
    // The reader's visible exits use the logical project, even after registry order changes while it is open.
    await page.goto(base + '/?p=0&view=showcase');
    await page.locator(`#s-${documentId} a.open`).click();
    await page.waitForSelector('.readtop .back');
    assert((await page.locator('.readtop .back').textContent()).includes('Back to Showcase'));
    await page.request.post(base + '/fixture/reorder');
    await page.evaluate(() => { colonyPlayer.busy = () => true; }); // exercise production manual navigation while Auto/player is active
    await page.locator('.readtop .back').click();
    await page.waitForURL('**/reader/return?**');
    assert.equal(await page.locator('body').getAttribute('data-project'), 'plants');
    assert.equal(await page.locator(`#s-${documentId}`).count(), 1);
    await page.locator(`#s-${documentId} a.open`).click();
    await page.locator('.readtop .readproject').click();
    await page.waitForURL('**/reader/return?**');
    assert.equal(await page.locator('body').getAttribute('data-project'), 'plants');
    assert(page.url().includes('view=overview'));
    assert.deepEqual(errors, []);
    if (artifacts) fs.writeFileSync(`${artifacts}/reader-metrics.json`, JSON.stringify(metrics, null, 2));
    console.log('PASS Showcase reader media and independent table scrolling; zoom, rotation, raw downloads and document audio retained');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
