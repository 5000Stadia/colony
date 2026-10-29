const {chromium} = require('/opt/colony-test-playwright');
const fs = require('fs');
(async () => {
  const browser = await chromium.launch({executablePath: '/opt/colony-test-chrome/chrome',
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
  const [base, out, secureBase] = process.argv.slice(2), errors = [], metrics = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    localStorage.setItem('aloud', '1'); localStorage.setItem('automode', 'text');
    const W = window.WebSocket;
    window.fixtureSockets = [];
    window.WebSocket = class extends W {
      constructor(...a) { super(...a); window.fixtureSockets.push(this); }
    };
  });
  const assert = (ok, why) => { if (!ok) throw new Error(why); };
  const ready = async name => {
    try {
      await page.waitForFunction(() => window.fixtureTerminal && fixtureTerminal.element.isConnected && [...Array(fixtureTerminal.buffer.active.length)].some((_, i) =>
        fixtureTerminal.buffer.active.getLine(i).translateToString().includes('Console recovery fixture ready')), null, {timeout: 6000});
      await page.waitForFunction(() => {
        const t = document.querySelector('#term').getBoundingClientRect(), k = document.querySelector('.keys').getBoundingClientRect();
        return t.bottom <= k.top + 2 && k.height > 0;
      }, null, {timeout: 6000});
    } catch (e) {
      await page.screenshot({path: out + '/console-' + name + '-failed.png', fullPage: true});
      throw new Error(name + ' did not show shell output; browser errors: ' + errors.join('; '));
    }
    await page.waitForTimeout(100); // the phone switches to its fullscreen layout on the next fit
    const state = await page.evaluate(() => {
      const rect = s => { const r = document.querySelector(s).getBoundingClientRect(); return {top: r.top, bottom: r.bottom, width: r.width, height: r.height}; };
      return {focus: document.body.classList.contains('focus'), term: rect('#term'), keys: rect('.keys'), tabs: rect('.tabs'),
        screen: rect('.xterm-screen'), cols: fixtureTerminal.cols, rows: fixtureTerminal.rows,
        css: getComputedStyle(document.querySelector('.xterm-viewport')).position,
        fixedTabs: getComputedStyle(document.querySelector('.tabs')).position === 'fixed', height: innerHeight,
        overlayTop: Math.min(...[...document.querySelectorAll('#gc, #cplayer')].filter(el => !el.hidden && el.offsetHeight)
          .map(el => el.getBoundingClientRect().top)),
        connected: fixtureSockets.filter(s => s.readyState === 1).length};
    });
    metrics.push({name, ...state});
    fs.writeFileSync(out + '/console-metrics.json', JSON.stringify(metrics, null, 2));
    await page.screenshot({path: out + '/console-' + name + '.png'});
    assert(state.focus && state.cols > 20 && state.rows > 3 && state.css === 'absolute', name + ' must initialize and load terminal CSS');
    assert(state.term.bottom <= state.keys.top + 2 && state.keys.bottom <= (state.fixedTabs ? state.tabs.top : state.height) + 2,
      name + ' keys and terminal must fit above tabs: ' + JSON.stringify(state));
    assert(state.keys.bottom <= state.overlayTop + 2, name + ' Auto controls must leave terminal keys usable');
    assert(state.connected === 1, name + ' needs exactly one attached browser terminal');
  };
  await page.goto(base + '/fixture/console');
  await ready('cold-phone');
  await page.goto(base + '/fixture/chat');
  await page.locator('nav').click(); // allow the real Auto player to keep its silent connection alive
  await page.waitForFunction(() => colonyPlayer.busy());
  await page.locator('a', {hasText: 'Console'}).click();
  await ready('player-navigation');
  await page.setViewportSize({width: 844, height: 390});
  await page.waitForTimeout(150);
  await ready('landscape');
  await page.setViewportSize({width: 390, height: 844});
  await page.waitForTimeout(150);
  await page.evaluate(() => {
    fixtureSockets.at(-1).close();
    document.dispatchEvent(new Event('visibilitychange'));
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.waitForFunction(() => fixtureSockets.length >= 2 && fixtureSockets.at(-1).readyState === 1);
  await ready('reconnected');
  await page.locator('.xterm-helper-textarea').focus();
  await page.keyboard.type("printf 'phone input reached shell\\n'");
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => [...Array(fixtureTerminal.buffer.active.length)].some((_, i) =>
    fixtureTerminal.buffer.active.getLine(i).translateToString().trim() === 'phone input reached shell'));
  // Typing can pause Auto; keep its navigation boundary active to exercise detaching in the same document.
  await page.evaluate(() => { colonyPlayer.busy = () => true; });
  const socketsBeforeLeaving = await page.evaluate(() => fixtureSockets.length);
  await page.locator('a', {hasText: 'Chat'}).click();
  await page.waitForURL('**/fixture/chat');
  await page.waitForTimeout(1000);
  assert(await page.evaluate(n => fixtureSockets.length === n && fixtureSockets.every(s => s.readyState > 1), socketsBeforeLeaving),
    'leaving Console must detach its WebSocket without replacing the player document');
  await page.locator('a', {hasText: 'Console'}).click();
  await ready('return-to-console');
  await page.evaluate(() => { document.querySelector('#gc textarea').style.height = '110px'; colonyPlayer.placed(); });
  await ready('expanded-auto-composer');
  await page.locator('a', {hasText: 'Chat'}).click();
  await page.waitForURL('**/fixture/chat');
  let release, started;
  const pending = new Promise(r => { release = r; }), loading = new Promise(r => { started = r; });
  await page.route('**/fixture/xterm.js', async route => { started(); await pending; await route.continue(); });
  await page.evaluate(() => { window.slowConsole = colonyNav.go('/fixture/console'); });
  await loading;
  await page.evaluate(() => colonyNav.go('/fixture/chat'));
  const countAfterNewerPage = await page.evaluate(() => fixtureSockets.length);
  release();
  await page.evaluate(() => window.slowConsole);
  assert(await page.locator('#term').count() === 0 && page.url().endsWith('/fixture/chat'), 'a newer page wins while a Console dependency is pending');
  assert(await page.evaluate(n => fixtureSockets.length === n, countAfterNewerPage), 'a stale Console must not attach a terminal on the newer page');
  await page.unroute('**/fixture/xterm.js');
  assert(errors.length === 0, 'browser errors: ' + errors.join('; '));
  const failed = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
  const failedErrors = [];
  failed.on('pageerror', e => failedErrors.push(e.message));
  await failed.route('**/fixture/*.js', route => route.abort());
  await failed.goto(base + '/fixture/console');
  assert(await failed.locator('#console-state').isVisible() && await failed.locator('#console-state a').textContent() === 'reload Console',
    'a failed dependency needs a readable way to retry');
  assert(failedErrors.length === 0, 'a dependency failure must not crash initialization');
  await failed.screenshot({path: out + '/console-dependency-failed.png'});
  await failed.close();
  const secure = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, ignoreHTTPSErrors: true});
  let secureSocket = '';
  secure.on('websocket', socket => { secureSocket = socket.url(); });
  await secure.goto(secureBase + '/fixture/console');
  await secure.waitForFunction(() => window.fixtureTerminal && [...Array(fixtureTerminal.buffer.active.length)].some((_, i) =>
    fixtureTerminal.buffer.active.getLine(i).translateToString().includes('Console recovery fixture ready')));
  assert(secureSocket.startsWith('wss://'), 'HTTPS Console must connect with a secure WebSocket');
  await secure.screenshot({path: out + '/console-https.png'});
  await secure.close();
  fs.writeFileSync(out + '/console-metrics.json', JSON.stringify(metrics, null, 2));
  await browser.close();
  console.log('PASS phone Console cold, player navigation, rotation and reconnect');
})().catch(e => { console.error(e); process.exit(1); });
