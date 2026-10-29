const {chromium} = require('/opt/colony-test-playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
  const browser = await chromium.launch({executablePath: '/opt/colony-test-chrome/chrome', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
  const errors = []; page.on('pageerror', e => errors.push(String(e)));
  try {
    await page.addInitScript(() => {
      // Browser boundaries only: real player and opener scripts, silent controllable local output.
      localStorage.setItem('aloud', '1'); localStorage.setItem('automode', 'audio');
      HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
      HTMLMediaElement.prototype.pause = function () {};
      const speech = Object.assign(new EventTarget(), {getVoices: () => [], speak(u) { if (u.onstart) u.onstart(); }, cancel() {}, pause() {}, resume() {}});
      Object.defineProperty(window, 'speechSynthesis', {value: speech});
    });
    await page.goto(process.argv[2] + '/open?p=0&f=listening.md');
    await page.getByRole('button', {name: 'Play document', exact: true}).click();
    await page.waitForFunction(() => colonyPlayer.state().document && !colonyPlayer.state().document.pending);
    assert.equal(await page.evaluate(() => colonyPlayer.auto()), true);
    await page.locator('.cpwhat').click();
    await page.getByRole('button', {name: '2. Closing', exact: true}).click();
    await page.waitForFunction(() => colonyPlayer.state().reading.j === 1);
    assert.equal(await page.getByRole('button', {name: '2. Closing', exact: true}).getAttribute('aria-current'), 'true');
    await page.getByRole('button', {name: '1. Opening', exact: true}).click();
    await page.waitForFunction(() => colonyPlayer.state().reading.j === 0);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '390 px reader fits horizontally');
    if (process.env.COLONY_TEST_ARTIFACTS) {
      fs.mkdirSync(process.env.COLONY_TEST_ARTIFACTS, {recursive: true});
      await page.screenshot({path: process.env.COLONY_TEST_ARTIFACTS + '/document-sections-390.png'});
    }
    await page.getByRole('button', {name: 'Finish document', exact: true}).click();
    assert.equal(await page.evaluate(() => colonyPlayer.state().document), null);
    assert.equal(await page.evaluate(() => colonyPlayer.auto()), true);
    assert.deepEqual(errors, []);
    process.stdout.write('PASS phone document player and sections\n');
  } catch (error) { console.error('Page errors:', errors); throw error; } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
