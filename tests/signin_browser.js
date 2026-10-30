const {chromium} = require(process.env.COLONY_TEST_PLAYWRIGHT || '/opt/colony-test-playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({executablePath: '/opt/colony-test-chrome/chrome', headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage']});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    page.setDefaultTimeout(8000);
    const errors = [], mutations = [], requests = [];
    page.on('pageerror', error => errors.push(String(error)));
    const row = (state, session) => ({state, checked_at: '2026-10-30T16:00:00Z', since: '2026-10-30T15:00:00Z',
      reason: state === 'reconnect' ? 'Sign in again.' : '', ...(session ? {session} : {})});
    const states = {claude: row('reconnect'), codex: row('unknown')};
    const waiting = provider => ({id: 'fixture-' + provider, state: 'waiting', expires_at: '2099-01-01T00:00:00Z',
      url: provider === 'claude' ? 'https://claude.com/cai/oauth/authorize?fixture=1' : 'https://auth.openai.com/codex/device',
      message: 'Continue in your browser.', ...(provider === 'claude' ? {accept_code: true} : {code: 'ABCD-EFGH'})});
    const reply = route => route.fulfill({status: 200, contentType: 'application/json',
      headers: {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}, body: JSON.stringify({providers: states})});
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      requests.push({path: url.pathname, query: url.search, method: request.method()});
      if (url.pathname === '/signin/status') return reply(route);
      if (url.pathname.startsWith('/signin/') && request.method() === 'POST') {
        const form = new URLSearchParams(request.postData() || '');
        const provider = form.get('provider'), action = url.pathname.split('/').pop();
        mutations.push({action, provider, session: form.get('session'), code: form.get('code')});
        assert(['claude', 'codex'].includes(provider));
        if (action === 'start') states[provider] = row('reconnect', waiting(provider));
        else if (action === 'code') {
          assert.equal(provider, 'claude'); assert.equal(form.get('session'), 'fixture-claude');
          assert.equal(form.get('code'), 'fixture-paste-code');
          states.claude = row('reconnect', {id: 'fixture-claude', state: 'verifying', message: 'Checking sign-in.'});
        } else if (action === 'cancel') {
          assert.equal(form.get('session'), 'fixture-' + provider);
          states[provider] = row('reconnect', {id: 'fixture-' + provider, state: 'cancelled', message: 'Cancelled.'});
        }
        return reply(route);
      }
      // Shell status polling has no connection to the native monitor in this isolated fixture.
      if (url.pathname === '/status') return route.fulfill({contentType: 'application/json',
        body: JSON.stringify({monitor: {state: 'off'}, projects: [], nav: []})});
      if (url.origin !== process.argv[2]) return route.abort();
      return route.continue();
    });
    const card = provider => page.locator('.provider-signin[data-provider="' + provider + '"]');
    const action = (provider, name) => card(provider).locator('[data-signin-action="' + name + '"]');
    const noPrompt = async provider => {
      const values = await card(provider).evaluate(card => ({
        href: card.querySelector('.signin-open')?.getAttribute('href'),
        code: card.querySelector('.signin-code')?.textContent,
        input: card.querySelector('input[name=code]')?.value,
        visible: [...card.querySelectorAll('.signin-open, .signin-code, .signin-submit')].some(el => el.getClientRects().length)
      }));
      assert(!values.href && !values.code && !values.input && !values.visible,
        'terminal sessions remove stale URLs, codes and code entry');
    };
    const recover = async () => page.evaluate(() => {
      document.dispatchEvent(new Event('visibilitychange'));
      window.dispatchEvent(new Event('pageshow'));
      window.dispatchEvent(new Event('focus'));
    });
    const fits = async () => {
      const dimensions = await page.evaluate(() => ({viewport: innerWidth,
        width: document.documentElement.scrollWidth,
        actions: [...document.querySelectorAll('#provider-signins button, #provider-signins .signin-open')]
          .filter(el => el.getClientRects().length && !el.hidden)
          .map(el => { const r = el.getBoundingClientRect(); return {height: r.height, left: r.left, right: r.right}; })}));
      assert(dimensions.width <= dimensions.viewport + 1, 'reconnect page fits a phone without horizontal scrolling');
      assert(dimensions.actions.length, 'the phone exposes reconnect actions');
      for (const target of dimensions.actions) {
        assert(target.height >= 44, 'login actions have a 44px touch target');
        assert(target.left >= -1 && target.right <= dimensions.viewport + 1, 'login actions stay inside viewport');
      }
    };
    await page.goto(process.argv[2] + '/signin');
    await page.locator('#provider-signins').waitFor();
    assert.equal(mutations.length, 0, 'opening the page must not start a sign-in or check');
    await fits();
    assert.match(await card('codex').innerText(), /unknown|unconfirmed|not checked|not yet checked|not been checked|not confirmed/i);
    await action('claude', 'start').evaluate(button => { button.click(); button.click(); });
    await card('claude').locator('.signin-submit input[name=code]').waitFor();
    assert.equal(mutations.filter(x => x.action === 'start').length, 1);
    const link = card('claude').locator('.signin-open');
    assert.match(await link.getAttribute('href'), /^https:\/\/claude\.com\//);
    assert.equal(await link.getAttribute('target'), '_blank');
    assert((await link.getAttribute('rel')).split(/\s+/).includes('noopener'));
    assert((await link.getAttribute('rel')).split(/\s+/).includes('noreferrer'));
    const input = card('claude').locator('.signin-submit input[name=code]');
    assert.equal(await input.getAttribute('type'), 'password');
    assert.equal(await input.getAttribute('autocomplete'), 'off');
    await input.fill('fixture-paste-code');
    await page.waitForTimeout(900); // Existing draft debounce must never persist a native login code.
    assert(!requests.some(x => x.path === '/answers/draft'), 'login codes are excluded from answer drafts');
    assert(!(await page.evaluate(() => JSON.stringify({...localStorage, ...sessionStorage}))).includes('fixture-paste-code'));
    // Repeated activation while a native login is waiting must not create another native session.
    const start = action('claude', 'start');
    if (await start.isVisible() && await start.isEnabled()) await start.tap();
    assert.equal(mutations.filter(x => x.action === 'start').length, 1);
    await input.focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => !document.querySelector('.provider-signin[data-provider="claude"] input[name=code]'));
    assert.equal(mutations.filter(x => x.action === 'code').length, 1, 'keyboard submission sends one code');
    assert(!requests.some(x => x.query.includes('fixture-paste-code')), 'codes never enter URL queries');
    await fits();
    states.claude = row('signed-in', {id: 'fixture-claude', state: 'done', message: 'Signed in.'});
    await recover();
    await page.waitForFunction(() => /signed in/i.test(document.querySelector('.provider-signin[data-provider="claude"]').innerText));
    await noPrompt('claude');
    await action('codex', 'start').tap();
    await card('codex').locator('.signin-code').waitFor();
    assert.equal((await card('codex').locator('.signin-code').innerText()).trim(), 'ABCD-EFGH');
    assert.equal(await card('codex').locator('input[name=code]').count(), 0, 'Codex device code is displayed, not pasted back');
    await page.setViewportSize({width: 844, height: 390});
    await fits();
    await page.reload();
    await card('codex').locator('.signin-code').waitFor();
    assert.equal(mutations.filter(x => x.provider === 'codex' && x.action === 'start').length, 1,
      'refresh recovers the waiting session without starting a second login');
    states.codex = row('signed-in', {id: 'fixture-codex', state: 'done', message: 'Signed in.'});
    await recover();
    await page.waitForFunction(() => /signed in/i.test(document.querySelector('.provider-signin[data-provider="codex"]').innerText));
    await noPrompt('codex');
    states.codex = row('reconnect');
    await recover();
    await action('codex', 'start').waitFor();
    await action('codex', 'start').tap();
    await action('codex', 'cancel').waitFor();
    await action('codex', 'cancel').tap();
    await page.waitForFunction(() => /cancelled/i.test(document.querySelector('.provider-signin[data-provider="codex"]').innerText));
    await noPrompt('codex');
    states.codex = row('reconnect', {id: 'fixture-codex', state: 'expired', message: 'Expired. Start again.'});
    await recover();
    await page.waitForFunction(() => /expired/i.test(document.querySelector('.provider-signin[data-provider="codex"]').innerText));
    await noPrompt('codex');
    states.codex = row('reconnect', {id: 'fixture-codex', state: 'failed',
      message: '<img src=x onerror="window.signinInjected=1">'});
    await recover();
    await page.waitForFunction(() => document.querySelector('.provider-signin[data-provider="codex"]').innerText.includes('<img'));
    assert.equal(await page.locator('.provider-signin img').count(), 0, 'native display text cannot create markup');
    assert.equal(await page.evaluate(() => window.signinInjected), undefined);
    assert.deepEqual(errors, []);
    console.log('PASS phone provider reconnect: Claude paste, Codex device flow, return, refresh, cancel, expiry and private codes');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
