const {chromium, webkit} = require(process.env.COLONY_TEST_PLAYWRIGHT || '/opt/colony-test-playwright');
const assert = require('node:assert/strict');
(async () => {
  const engines = [['Chromium', chromium, '/opt/colony-test-chrome/chrome']];
  if (process.env.COLONY_TEST_WEBKIT) engines.push(['WebKit', webkit, process.env.COLONY_TEST_WEBKIT]);
  for (const [name, engine, executablePath] of engines) {
    const browser = await engine.launch({executablePath, headless: true, args: name === 'Chromium' ? ['--no-sandbox', '--disable-dev-shm-usage'] : []});
    try {
      const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
      page.setDefaultTimeout(6000);
      const errors = [], choosers = [], uploads = [];
      page.on('pageerror', error => errors.push(String(error)));
      page.on('filechooser', chooser => choosers.push(chooser));
      await page.addInitScript(() => {
        // A mobile browser harness cannot summon an OS keyboard. Reproduce its viewport and blur-driven resize.
        const vv = new EventTarget(); let keyboard = false;
        Object.defineProperties(vv, {height: {get: () => keyboard ? 440 : 844}, width: {get: () => 390},
          offsetTop: {get: () => 0}, pageTop: {get: () => scrollY}, scale: {get: () => 1}});
        Object.defineProperty(window, 'visualViewport', {value: vv});
        window.fixtureBlurs = 0;
        document.addEventListener('focusin', e => { if (e.target.matches('textarea')) { keyboard = true; vv.dispatchEvent(new Event('resize')); } });
        document.addEventListener('focusout', e => { if (e.target.matches('textarea')) { fixtureBlurs++; keyboard = false; vv.dispatchEvent(new Event('resize')); } });
        if (localStorage.getItem('aloud') === null) localStorage.setItem('aloud', '0');
      });
      await page.route('**/chat/send', async route => { uploads.push(route.request().postDataBuffer().toString()); await route.fulfill({status: 200, body: 'ok'}); });
      const open = async (formSelector, buttonSelector) => {
        const ta = page.locator(formSelector + ' textarea').first(), button = page.locator(buttonSelector);
        await ta.fill('A draft with a selected word');
        await button.evaluate(b => b.scrollIntoView({block: 'center'}));
        await ta.evaluate(t => { t.focus({preventScroll: true}); t.setSelectionRange(15, 23, 'backward'); window.fixtureBlurs = 0; });
        await page.waitForTimeout(400); // production keyboard layout settles
        assert(await button.isVisible(), name + ' plus stays visible while typing');
        const state = () => ta.evaluate(t => ({text: t.value, start: t.selectionStart, end: t.selectionEnd,
          direction: t.selectionDirection, blurs: fixtureBlurs, focused: document.activeElement === t}));
        const draft = await state(), before = choosers.length;
        assert.equal(draft.focused, true); assert.equal(draft.blurs, 0);
        await button.tap();
        await page.waitForTimeout(150);
        assert.equal(choosers.length, before + 1, name + ' one tap must open one native picker for ' + formSelector);
        assert.deepEqual(await state(), draft,
          name + ' activation must preserve focus, draft and selection before keyboard collapse');
        return {ta, chooser: choosers.at(-1)};
      };
      for (const [form, button, send] of [['#composer', '#composer .attach', '#composer .send'], ['#gc', '#gc .gcattach', '#gc .gcsend']]) {
        await page.goto(process.argv[2] + '/?p=0&view=chat');
        if (form === '#gc') { await page.evaluate(() => { localStorage.setItem('aloud', '1'); localStorage.setItem('automode', 'text'); }); await page.reload(); }
        const first = await open(form, button);
        // Playwright's empty selection closes its intercepted native picker; the real cancel event also must be inert.
        await first.chooser.setFiles([]);
        await page.locator(form + ' input[type=file]').dispatchEvent('cancel');
        assert.equal(await first.ta.inputValue(), 'A draft with a selected word');
        const second = await open(form, button);
        await second.chooser.setFiles({name: 'fixture.txt', mimeType: 'text/plain', buffer: Buffer.from('One attachment.\n')});
        assert.equal(await page.locator(form + ' input[type=file]').evaluate(f => f.files.length), 1);
        const before = uploads.length;
        await page.locator(send).tap();
        await page.waitForTimeout(200);
        assert.equal(uploads.length, before + 1, name + ' Send uploads exactly once');
        assert.equal((uploads.at(-1).match(/filename="fixture.txt"/g) || []).length, 1);
        assert(uploads.at(-1).includes('A draft with a selected word'));
      }
      await page.evaluate(() => localStorage.setItem('aloud', '0'));
      await page.reload();
      const inline = await open('#chat form.add', '#chat form.add .attach');
      await inline.chooser.setFiles([]);
      // Desktop assistive activation uses the same synchronous picker path.
      await page.locator('#chat form.add .attach').focus();
      const before = choosers.length;
      await page.keyboard.press('Enter');
      await page.waitForTimeout(150);
      assert.equal(choosers.length, before + 1, name + ' Enter opens one picker');
      await choosers.at(-1).setFiles([]);
      assert.deepEqual(errors, []);
      console.log('PASS first-tap attachments ' + name + ': Chat, global, inline, cancel, repeat and single upload');
    } finally { await browser.close(); }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
