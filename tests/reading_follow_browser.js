const {chromium} = require('/opt/colony-test-playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
  const browser = await chromium.launch({executablePath: '/opt/colony-test-chrome/chrome', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
  const base = process.argv[2], errors = []; page.setDefaultTimeout(7000);
  page.on('pageerror', e => errors.push(String(e)));
  await page.addInitScript(() => {
    window.fixtureNow = 1000000; Date.now = () => window.fixtureNow;
    window.fixtureSaid = [];
    HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
    HTMLMediaElement.prototype.pause = function () {};
    const speech = Object.assign(new EventTarget(), {getVoices: () => [],
      speak(u) { if (u.text) fixtureSaid.push(u); if (u.onstart) u.onstart(); }, cancel() {}, pause() {}, resume() {}});
    Object.defineProperty(window, 'speechSynthesis', {value: speech});
    localStorage.setItem('aloud', '1'); localStorage.setItem('automode', 'both');
    localStorage.setItem('armed', JSON.stringify(fixtureNow - 1000)); localStorage.setItem('alive', JSON.stringify(fixtureNow));
    if (!sessionStorage.getItem('fixtureStarted')) {
      sessionStorage.setItem('fixtureStarted', '1');
      localStorage.setItem('linenow', JSON.stringify({N: {id: '0|A', p: '0', t: 'A', name: 'plants', href: '/fixture/reading-chat',
        kvoice: 'am_echo', kokoro: false, seat: 'auto', named: false, j: 0, piece: 0,
        blocks: [{k: 'part1', text: 'First part. Another sentence.'}, {k: 'part2', text: 'Second part. More sentences.'}, {k: 'part3', text: 'Third part. New sentences.'}, {k: 'part4', text: 'Fourth part. More words.'}]},
        S: {want: ['0|A'], play: [], auto: [['0|A', fixtureNow]], begun: ['0|A']}, sounding: true, saved: fixtureNow}));
    }
  });
  await page.route('**/chat/line?**', route => {
    const history = new URL(route.request().url()).searchParams.has('heard');
    return route.fulfill({contentType: 'application/json', body: JSON.stringify({line: [],
      heard: history ? [{p: '0', t: 'E', at: new Date().toISOString(), w: 'Opening sentence.'}] : [],
      projects: {'0': {name: 'plants', href: '/fixture/reading-chat'}}, total: 0, ready: false})});
  });
  let textGate = null, textRequested = null;
  await page.route('**/chat/text?**', async route => {
    if (textRequested) textRequested(); if (textGate) await textGate;
    await route.fulfill({contentType: 'application/json', body: JSON.stringify({blocks: [{k: 'explicit', text: 'Opening sentence. Last sentence.'}, {k: 'explicit2', text: 'Next selected part. Continue reading.'}]})});
  });
  await page.route('**/chat/feed?**', route => route.fulfill({status: 304, body: ''}));
  let release, requested;
  const arrived = new Promise(ok => { requested = ok; }), held = new Promise(ok => { release = ok; });
  let first = true;
  await page.route('**/fixture/reading-chat', async route => {
    if (first) { first = false; requested(); await held; }
    await route.continue();
  });
  const watch = () => page.evaluate(() => { window.fixtureScrolls = [];
    if (!window.fixtureWatched) { window.fixtureWatched = true; const original = window.scrollBy;
      window.scrollBy = (...args) => { fixtureScrolls.push(args); return original.apply(window, args); }; } });
  const manual = async () => { await page.locator('#fixture-manual').click(); await page.waitForURL('**/fixture/reading-manual'); };
  const on = () => page.evaluate(() => colonyPlayer.following());
  try {
    // Follow on, a reply being read from another page: the page is on its way to that reply's chat as the part begins.
    await page.goto(base + '/fixture/reading-source');
    await page.waitForFunction(() => fixtureSaid.some(x => x.text.includes('First part')));
    await Promise.race([arrived, new Promise((_, fail) => setTimeout(() => fail(new Error('Follow did not start')), 7000))]);
    // A scroll and a touch while it is on its way hold nothing: there is no wait after the person's hand any more.
    await page.mouse.move(200, 500); await page.mouse.wheel(0, 300); await page.touchscreen.tap(210, 490);
    release(); await page.waitForURL('**/fixture/reading-chat');
    await page.waitForFunction(() => document.querySelector('[data-k=part1]')?.classList.contains('reading'));
    assert.equal(await page.evaluate(() => colonyPlayer.state().playing), true);
    assert.equal(await on(), true, 'a page the reading opened leaves the switch on');
    // In its chat, the next part is brought into view at once, a touch just before or not.
    await watch(); await page.touchscreen.tap(120, 260);
    await page.evaluate(() => fixtureSaid.find(x => x.text.includes('First part')).onend());
    await page.waitForFunction(() => colonyPlayer.state().reading.k === 'part2' && fixtureScrolls.length > 0);
    await page.waitForFunction(() => document.querySelector('[data-k=part2]').classList.contains('reading'));
    // Tapped off: the reading goes on, and nothing moves or marks the page.
    await page.locator('.cpbar .cpfollow').click(); assert.equal(await on(), false); await watch();
    await page.evaluate(() => fixtureSaid.find(x => x.text.includes('Second part')).onend());
    await page.waitForFunction(() => colonyPlayer.state().reading.k === 'part3'); await page.waitForTimeout(100);
    assert.equal(await page.evaluate(() => fixtureScrolls.length), 0, 'off: no scroll at a new part');
    assert.equal(await page.locator('.say.reading').count(), 0);
    // Off, a reply played from History is heard where the person is.
    await manual();
    const historyPlay = async () => {
      await page.locator('.cpwhat').click(); await page.locator('.cpheardopen').click();
      await page.locator('.cpheardrows li').first().click();
      await page.waitForFunction(() => colonyPlayer.state().reading.k === 'explicit');
    };
    await historyPlay(); await page.waitForTimeout(100);
    assert(page.url().endsWith('/fixture/reading-manual'), 'off: History playback does not move the page');
    // Tapped on: the page goes to what is being read, and to each part after it.
    await page.locator('.cpbar .cpfollow').click(); await page.waitForURL('**/fixture/reading-chat');
    await page.waitForFunction(() => document.querySelector('[data-k=explicit]')?.classList.contains('reading'));
    if (process.env.COLONY_TEST_ARTIFACTS) {
      fs.mkdirSync(process.env.COLONY_TEST_ARTIFACTS, {recursive: true});
      await page.screenshot({path: process.env.COLONY_TEST_ARTIFACTS + '/reading-follow-390.png'});
    }
    // Leaving that chat by hand while it reads turns the switch off: later parts leave the person where they went.
    await manual(); assert.equal(await on(), false);
    await page.evaluate(() => fixtureSaid.findLast(x => x.text.includes('Opening sentence')).onend());
    await page.waitForFunction(() => colonyPlayer.state().reading.k === 'explicit2'); await page.waitForTimeout(100);
    assert(page.url().endsWith('/fixture/reading-manual'));
    // A page the reading opens finishes loading its scripts even where a touch lands while one is still awaited.
    await page.evaluate(() => colonyPlayer.setFollow(true)); await page.waitForURL('**/fixture/reading-chat');
    let releaseDependency, gotDependency;
    const dependency = new Promise(ok => { releaseDependency = ok; }), dependencyRequested = new Promise(ok => { gotDependency = ok; });
    await page.route('**/fixture/reading-dependency.js', async route => { gotDependency(); await dependency; await route.fulfill({contentType:'application/javascript',body:'window.fixtureDependencyReady=true;'}); });
    await page.evaluate(() => { window.fixturePendingNav = colonyNav.go('/fixture/reading-external', {automatic:true}); });
    await dependencyRequested; await page.getByRole('heading', {name:'Dependency Chat'}).waitFor();
    await page.touchscreen.tap(120, 200); releaseDependency(); await page.evaluate(() => fixturePendingNav);
    assert.equal(await page.evaluate(() => fixtureDependencyReady && fixtureActivated === 1), true, 'later scripts initialize after a touch during an awaited dependency');
    assert(page.url().endsWith('/fixture/reading-external'));
    assert.equal(await on(), true);
    assert.deepEqual(errors, []);
    console.log('PASS follow by the switch: from anywhere when on, never when off, off when left by hand');
  } catch (e) { console.error('Page errors:', errors); console.error('Player:', await page.evaluate(() => ({url: location.href, state: window.colonyPlayer && colonyPlayer.state(), chat: !!document.getElementById('chat')}))); throw e; } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
