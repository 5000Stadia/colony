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
  try {
    await page.goto(base + '/fixture/reading-source');
    await page.waitForFunction(() => fixtureSaid.some(x => x.text.includes('First part')));
    await page.evaluate(() => fixtureSaid.find(x => x.text.includes('First part')).onend());
    await Promise.race([arrived, new Promise((_, fail) => setTimeout(() => fail(new Error('Auto navigation did not start')), 7000))]);
    await page.mouse.move(200, 500); await page.mouse.wheel(0, 300); await page.waitForTimeout(60);
    assert.equal(await page.evaluate(() => colonyReadingFollow.canFollow()), false);
    await page.evaluate(() => fixtureNow += 4000);
    await page.touchscreen.tap(210, 490);
    assert.equal(await page.evaluate(() => colonyReadingFollow.remaining()), 5000, 'repeated touch extends the grace');
    await page.evaluate(() => fixtureNow += 1000);
    release(); await page.waitForFunction(() => !document.documentElement.classList.contains('going'));
    assert(page.url().endsWith('/fixture/reading-source'), 'Auto fetch must not commit after a recent scroll/touch');
    assert.equal(await page.evaluate(() => colonyPlayer.state().reading.k), 'part2', 'held navigation preserves the current audio part');
    assert.equal(await page.evaluate(() => colonyPlayer.state().playing), true);
    assert.equal(await page.evaluate(() => colonyReadingFollow.remaining()), 4000);
    await page.evaluate(() => fixtureNow += 4001);
    assert.equal(await page.evaluate(() => colonyReadingFollow.canFollow()), true);
    // Natural part completion retries normal Auto follow after the grace, without changing the selected reply.
    await page.evaluate(() => fixtureSaid.find(x => x.text.includes('Second part')).onend());
    await page.waitForURL('**/fixture/reading-chat');
    await page.waitForFunction(() => document.querySelector('#chat') && colonyPlayer.state().reading.k === 'part3');
    await page.waitForTimeout(100);
    assert.equal(await page.evaluate(() => colonyReadingFollow.canFollow()), true, 'programmatic reading scroll must not create its own hold');
    // Ordinary navigation still acts on the same click that starts the grace.
    await page.locator('#fixture-manual').click(); await page.waitForURL('**/fixture/reading-manual');
    assert.equal(await page.evaluate(() => colonyReadingFollow.canFollow()), false);
    await page.evaluate(() => {
      fixtureNow += 20000;
      fixtureSaid.find(x => x.text.includes('Third part')).onend();
    });
    await page.waitForFunction(() => colonyPlayer.state().reading.k === 'part4');
    assert(page.url().endsWith('/fixture/reading-manual'), 'Auto must leave non-Chat browsing alone beyond five seconds');
    assert.equal(await page.evaluate(() => colonyReadingFollow.inChat()), false);
    // Real History playback is an explicit choice, but audio-only keeps the current view.
    await page.evaluate(() => colonyPlayer.setMode('audio'));
    const historyPlay = async () => {
      await page.locator('.cpwhat').click(); await page.locator('.cpheardopen').click();
      await page.locator('.cpheardrows li').first().click();
      await page.waitForFunction(() => colonyPlayer.state().reading.k === 'explicit');
    };
    await historyPlay();
    assert(page.url().endsWith('/fixture/reading-manual'), 'audio-only History playback does not navigate');
    await page.evaluate(() => colonyPlayer.setMode('both'));
    await historyPlay(); await page.waitForURL('**/fixture/reading-chat');
    await page.waitForFunction(() => document.querySelector('#chat') && colonyPlayer.state().reading.k === 'explicit');
    assert.equal(await page.evaluate(() => colonyReadingFollow.canFollow()), false, 'explicit History bypasses its click grace without clearing it');
    // The actual Chat sentence/section scroll helpers respect a later real user movement.
    await page.mouse.move(120, 260); await page.mouse.wheel(0, -10000); await page.waitForTimeout(120);
    await page.touchscreen.tap(120, 260);
    await page.evaluate(() => window.scrollTo({top: scrollY, behavior: 'instant'}));
    await page.waitForTimeout(60);
    // Observe actual production scroll requests while preserving the browser's native implementation.
    const before = await page.evaluate(() => { window.fixtureScrolls = []; const original = window.scrollBy; window.scrollBy = (...args) => { fixtureScrolls.push({args,allowed:colonyPlayer.canFollow(),left:colonyReadingFollow.remaining()}); return original.apply(window, args); }; return scrollY; });
    await page.evaluate(() => {
      const u = fixtureSaid.findLast(x => x.text.includes('Opening sentence'));
      u.onboundary({charIndex: u.text.indexOf('Last sentence')});
    });
    await page.waitForTimeout(100);
    const after = await page.evaluate(() => ({y: scrollY, left: colonyReadingFollow.remaining()}));
    assert(Math.abs(after.y - before) < 2, 'sentence follow stays still during interaction grace: ' + JSON.stringify({before, after}));
    assert.equal(await page.evaluate(() => fixtureScrolls.length), 0, 'no sentence scroll request during the grace');
    await page.evaluate(() => {
      fixtureNow += 5001;
      const u = fixtureSaid.findLast(x => x.text.includes('Opening sentence'));
      u.onboundary({charIndex: 0}); u.onboundary({charIndex: u.text.indexOf('Last sentence')});
    });
    assert.equal(await page.evaluate(() => colonyPlayer.canFollow()), true, 'after grace Chat following is allowed again');
    assert.equal(await page.evaluate(() => colonyReadingFollow.canFollow()), true, 'resumed sentence scroll does not re-arm its own hold');
    assert.equal(await page.evaluate(() => colonyPlayer.state().reading.k), 'explicit');
    if (process.env.COLONY_TEST_ARTIFACTS) {
      fs.mkdirSync(process.env.COLONY_TEST_ARTIFACTS, {recursive: true});
      await page.screenshot({path: process.env.COLONY_TEST_ARTIFACTS + '/reading-follow-390.png'});
    }
    await page.locator('#fixture-manual').click(); await page.waitForURL('**/fixture/reading-manual');
    await page.evaluate(() => { fixtureNow += 20000; fixtureSaid.findLast(x => x.text.includes('Opening sentence')).onend(); });
    await page.waitForFunction(() => colonyPlayer.state().reading.k === 'explicit2');
    assert(page.url().endsWith('/fixture/reading-manual'), 'later parts of explicit History playback do not retain the initial navigation permission');
    let releaseText, sawText;
    textGate = new Promise(ok => { releaseText = ok; }); const loadingText = new Promise(ok => { sawText = ok; }); textRequested = sawText;
    await page.locator('.cpwhat').click(); await page.locator('.cpheardopen').click(); await page.locator('.cpheardrows li').first().click();
    await loadingText;
    await page.touchscreen.tap(120, 200); await page.evaluate(() => fixtureNow += 20000); releaseText();
    await page.waitForFunction(() => colonyPlayer.state().reading.k === 'explicit');
    assert(page.url().endsWith('/fixture/reading-manual'), 'a later user action revokes a History jump whose text was still loading');
    // Once NAV commits the DOM, a touch may hold following but must not leave dependency scripts half initialized.
    await page.evaluate(() => colonyNav.go('/fixture/reading-chat'));
    await page.waitForURL('**/fixture/reading-chat'); await page.evaluate(() => fixtureNow += 5001);
    let releaseDependency, gotDependency;
    const dependency = new Promise(ok => { releaseDependency = ok; }), dependencyRequested = new Promise(ok => { gotDependency = ok; });
    await page.route('**/fixture/reading-dependency.js', async route => { gotDependency(); await dependency; await route.fulfill({contentType:'application/javascript',body:'window.fixtureDependencyReady=true;'}); });
    await page.evaluate(() => { window.fixturePendingNav = colonyNav.go('/fixture/reading-external', {automatic:true}); });
    await dependencyRequested; await page.getByRole('heading', {name:'Dependency Chat'}).waitFor();
    await page.touchscreen.tap(120, 200); releaseDependency(); await page.evaluate(() => fixturePendingNav);
    assert.equal(await page.evaluate(() => fixtureDependencyReady && fixtureActivated === 1), true, 'later scripts initialize after a touch during an awaited dependency');
    assert(page.url().endsWith('/fixture/reading-external'));
    assert.deepEqual(errors, []);
    console.log('PASS five-second Auto reading grace, delayed navigation, explicit choices and sentence following');
  } catch (e) { console.error('Page errors:', errors); console.error('Player:', await page.evaluate(() => ({url: location.href, state: window.colonyPlayer && colonyPlayer.state(), chat: !!document.getElementById('chat')}))); throw e; } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
