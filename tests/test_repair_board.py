"""Installation repair access routes and phone UI; native hosts are always stand-ins."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer

from colony import board


def repair_state(enabled=False, state='off', **extra):
    return dict(enabled=enabled, provider='codex', state=state,
                message='Repair access is off.' if not enabled else 'Native host ready.',
                paired=False, server=None, checked_at=None, **extra)


class RepairBoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, COLONY_BOARD_HOME=str(Path(self.tmp.name) / 'board'))
        env.start(); self.addCleanup(env.stop)
        board.save_registry({'roots': [], 'settings': {'active_hours': 'off', 'messaging': False, 'lan': False}})
        self.current = repair_state()
        self.calls = []
        self.action_error = None
        self.code = 'REPAIR-TEST-1234'

    def fixture(self):
        from colony import repair, signin

        def action(name):
            def perform(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                if self.action_error:
                    raise self.action_error
                if name == 'enable':
                    self.current = repair_state(True, 'starting')
                elif name == 'disable':
                    self.current = repair_state()
                elif name == 'pair':
                    return ({'claimed': True} if args and args[0] is not None else
                            {'manualPairingCode': self.code, 'expiresAt': int(time.time()) + 120})
                return dict(self.current)
            return perform

        patches = [patch.object(repair, 'state', side_effect=lambda: dict(self.current)),
                   patch.object(repair, 'available', return_value=['codex']),
                   patch.object(signin, 'states', return_value={}),
                   patch.object(board, 'sidebar', return_value=''),
                   patch.object(board, 'trouble_bar', return_value='')]
        for name in ('enable', 'disable', 'tick', 'pair'):
            patches.append(patch.object(repair, name, side_effect=action(name)))
        for item in patches:
            item.start(); self.addCleanup(item.stop)
        return repair

    def start_server(self):
        class Handler(board.Handler):
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])
        return self.base

    def request(self, path, form=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        outgoing = {'Origin': self.base}
        outgoing.update(headers or {})
        data = None if form is None else urlencode(form, doseq=True) if isinstance(form, dict) else form
        if data is not None:
            outgoing['Content-Type'] = 'application/x-www-form-urlencoded'
        try:
            conn.request('GET' if form is None else 'POST', path, data, outgoing)
            response = conn.getresponse()
            return response.status, dict(response.headers), response.read().decode()
        finally:
            conn.close()

    def assert_private(self, headers):
        self.assertEqual(headers.get('Cache-Control'), 'no-store')
        self.assertEqual(headers.get('Referrer-Policy'), 'no-referrer')

    def test_page_and_health_are_passive_cached_reads(self):
        self.fixture(); self.start_server()
        for path in ('/repair', '/repair/status'):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200)
            self.assert_private(headers)
            if path.endswith('/status'):
                self.assertIn('application/json', headers.get('Content-Type', ''))
                self.assertEqual(json.loads(body), self.current)
            else:
                self.assertIn('Colony repair access', body)
                self.assertIn('/signin', body, 'native account sign-in has its own page')
            self.assertNotIn(self.code, body)
        for path in ('/repair/pair', '/repair/pair/status?code=' + self.code):
            status, _, body = self.request(path)
            self.assertIn(status, (404, 405), 'GET cannot mint or check a pairing code')
            self.assertNotIn(self.code, body)
        self.assertEqual(self.calls, [], 'rendering and health polling never start/check/pair a native host')

    def test_mutations_dispatch_only_their_bounded_arguments_and_pairing_is_transient(self):
        self.fixture(); self.start_server()
        actions = [('/repair/enable', {'provider': 'codex'}, ('enable', ('codex',), {})),
                   ('/repair/check', {}, ('tick', (), {'force': True})),
                   ('/repair/pair', {}, ('pair', (), {})),
                   ('/repair/pair/status', {'code': self.code}, ('pair', (self.code,), {})),
                   ('/repair/disable', {}, ('disable', (), {}))]
        for path, form, expected in actions:
            with self.subTest(path=path):
                status, headers, body = self.request(path, dict(form, command='must-not-run', host='evil.example'))
                self.assertEqual(status, 200)
                self.assert_private(headers)
                self.assertEqual(self.calls[-1], expected)
                result = json.loads(body)
                self.assertEqual(result['state'], self.current)
                if path == '/repair/pair':
                    self.assertEqual(result['pairing']['manualPairingCode'], self.code)
                    self.assertGreater(result['pairing']['expiresAt'], time.time())
                else:
                    self.assertNotIn(self.code, body)
                    self.assertNotIn('pairing', result)
                    if path.endswith('/status'):
                        self.assertIs(result['claimed'], True)
                for get in ('/repair', '/repair/status'):
                    self.assertNotIn(self.code, self.request(get)[2], 'pairing codes never enter a later GET response')
        records = ''.join(p.read_text(errors='replace') for p in Path(self.tmp.name).rglob('*') if p.is_file())
        self.assertNotIn(self.code, records, 'HTTP pairing must not persist a code in board state')

    def test_invalid_provider_and_pairing_codes_are_rejected_before_native_dispatch(self):
        self.fixture(); self.start_server()
        for form in ({}, {'provider': ''}, {'provider': 'shell'}, {'provider': '../codex'},
                     {'provider': 'codex --help'}, {'provider': ['codex', 'codex']}):
            with self.subTest(form=form):
                self.assertIn(self.request('/repair/enable', form)[0], (400, 409))
        for form in ({}, {'code': ''}, {'code': 'A' * 129}, {'code': 'bad code'}, {'code': 'bad/code'},
                     {'code': 'bad_code'}, {'code': ['FIRST-CODE', 'SECOND-CODE']}):
            with self.subTest(form=form):
                status, _, body = self.request('/repair/pair/status', form)
                self.assertIn(status, (400, 409))
                self.assertNotIn('FIRST-CODE', body)
                self.assertNotIn('SECOND-CODE', body)
        self.assertEqual(self.calls, [])

    def test_origin_host_and_tailscale_guard_every_mutation(self):
        self.fixture(); self.start_server()
        paths = ('/repair/enable', '/repair/disable', '/repair/check', '/repair/pair', '/repair/pair/status')
        for path in paths:
            for headers in ({'Origin': 'https://evil.example'}, {'Origin': 'null'},
                            {'Host': 'evil.example', 'Origin': 'http://evil.example'}):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.request(path, {'provider': 'codex', 'code': self.code}, headers)[0], 403)
        reg = board.registry(); reg['settings']['tailscale'] = 'fixture-tailnet'; board.save_registry(reg)
        with patch('colony.tailscale.own_device', return_value=False):
            for path in ('/repair', '/repair/status'):
                self.assertEqual(self.request(path)[0], 403)
            for path in paths:
                self.assertEqual(self.request(path, {'provider': 'codex', 'code': self.code})[0], 403)
        self.assertEqual(self.calls, [])
        self.server.lan = True
        host = '100.90.80.70:' + str(self.server.server_address[1])
        with patch('colony.tailscale.own_device', return_value=True):
            status, _, _ = self.request('/repair/enable', {'provider': 'codex'},
                                        {'Host': host, 'Origin': 'https://' + host})
        self.assertEqual(status, 200, 'the person can enable access through their existing remote transport')

    def test_native_errors_and_display_fields_do_not_leak_or_create_markup(self):
        self.fixture(); self.start_server()
        for error in (ValueError('private-native-detail ' + self.code), RuntimeError('private-native-detail ' + self.code)):
            self.action_error = error
            status, headers, body = self.request('/repair/pair', {})
            self.assertIn(status, (400, 409, 500, 503))
            self.assert_private(headers)
            self.assertNotIn('private-native-detail', body)
            self.assertNotIn(self.code, body)
        self.action_error = None
        attack = '</script><img src=x onerror="window.repairInjected=1">'
        self.current['message'] = attack; self.current['server'] = attack
        status, _, body = self.request('/repair')
        self.assertEqual(status, 200)
        self.assertNotIn(attack, body)
        self.assertNotIn('<img src=x', body)

    @unittest.skipUnless(os.environ.get('COLONY_BROWSER_TEST') == '1', 'requires isolated Chrome/Playwright')
    def test_phone_enable_pair_disable_expiry_and_departure_keep_codes_private(self):
        self.fixture(); self.start_server()
        result = subprocess.run(['node', '-e', PHONE_SCRIPT, self.base], text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS phone installation repair access', result.stdout)


PHONE_SCRIPT = r'''
const {chromium} = require('/opt/colony-test-playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({executablePath:'/opt/colony-test-chrome/chrome',headless:true,
    args:['--no-sandbox','--disable-dev-shm-usage']});
  try {
    const page = await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    page.setDefaultTimeout(7000);
    await page.clock.install({time:new Date('2026-10-30T20:00:00Z')});
    const errors=[], actions=[], requests=[];
    page.on('pageerror',e=>errors.push(String(e)));
    let state={enabled:false,provider:'codex',state:'off',message:'Repair access is off.',paired:false,server:null,checked_at:null};
    let expires=Date.parse('2026-10-30T20:02:00Z')/1000;
    const code='PRIVATE-PAIR-1234';
    await page.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url());
      requests.push({path:url.pathname,query:url.search,body:request.postData()||''});
      if(url.pathname==='/repair/status')return route.fulfill({contentType:'application/json',body:JSON.stringify(state)});
      if(url.pathname.startsWith('/repair/')&&request.method()==='POST'){
        actions.push(url.pathname);
        if(url.pathname==='/repair/enable')state={...state,enabled:true,state:'starting',message:'Starting native repair access in the background.'};
        if(url.pathname==='/repair/disable')state={...state,enabled:false,state:'off',message:'Repair access is off.',paired:false};
        const result={state};
        if(url.pathname==='/repair/pair')result.pairing={manualPairingCode:code,expiresAt:expires};
        if(url.pathname==='/repair/pair/status')result.claimed=false;
        return route.fulfill({contentType:'application/json',body:JSON.stringify(result)});
      }
      if(url.pathname==='/status')return route.fulfill({contentType:'application/json',body:JSON.stringify({monitor:{state:'off'},projects:[],nav:[]})});
      if(url.pathname==='/chat/line')return route.fulfill({contentType:'application/json',body:JSON.stringify({line:[],projects:{},total:0,ready:false})});
      if(url.origin!==process.argv[1])return route.abort();
      return route.continue();
    });
    const main=page.locator('main');
    const enable=()=>main.getByRole('button',{name:/enable/i});
    const disable=()=>main.getByRole('button',{name:/disable/i});
    const pair=()=>main.getByRole('button',{name:/pair|connect.*chatgpt/i});
    const text=()=>main.innerText();
    const recover=async()=>page.evaluate(()=>{document.dispatchEvent(new Event('visibilitychange'));window.dispatchEvent(new Event('pageshow'));window.dispatchEvent(new Event('focus'));});
    const absent=async()=>page.waitForFunction(code=>!document.querySelector('main').innerText.includes(code),code);
    const fits=async()=>{
      const d=await main.evaluate(main=>({width:document.documentElement.scrollWidth,viewport:innerWidth,
        buttons:[...main.querySelectorAll('button')].filter(x=>x.getClientRects().length&&!x.hidden).map(x=>{const r=x.getBoundingClientRect();return {height:r.height,left:r.left,right:r.right};})}));
      assert(d.width<=d.viewport+1,'repair access fits the phone');
      for(const b of d.buttons){assert(b.height>=44,'actions have 44px touch targets');assert(b.left>=-1&&b.right<=d.viewport+1);}
    };
    await page.goto(process.argv[1]+'/repair');
    await main.getByRole('heading',{name:'Colony repair access'}).waitFor();
    assert.deepEqual(actions,[],'opening repair access must not start a host or create pairing');
    await fits();
    await enable().evaluate(b=>{b.click();b.click();});
    await page.waitForFunction(()=>document.querySelector('main').innerText.includes('background'));
    assert.equal(actions.filter(x=>x==='/repair/enable').length,1);
    assert(!actions.some(x=>x.includes('/pair')),'enabling does not pair before the person is ready');
    state={...state,state:'unpaired',message:'Native host ready. Pair with ChatGPT.',server:'Fixture repair host'};
    await recover();await page.clock.runFor(10000);
    await pair().first().tap();
    await page.waitForFunction(code=>document.querySelector('main').innerText.includes(code),code);
    assert.match(await text(),/Add manually/i);
    await fits();
    assert(!(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).includes(code),'pairing never enters browser storage');
    assert(!requests.some(x=>x.query.includes(code)||x.path==='/answers/draft'||x.path==='/chat/send'),'pairing never enters URLs, drafts or chat');
    await page.clock.runFor(5000);
    assert(actions.some(x=>x==='/repair/pair/status'),'only an active pairing code is checked');
    await disable().tap();await absent();
    const checks=actions.filter(x=>x==='/repair/pair/status').length;
    await page.clock.runFor(10000);
    assert.equal(actions.filter(x=>x==='/repair/pair/status').length,checks,'disabled access stops pairing checks');
    await enable().tap();await page.waitForFunction(()=>document.querySelector('main').innerText.includes('background'));
    state={...state,state:'unpaired',message:'Native host ready. Pair with ChatGPT.'};await recover();await page.clock.runFor(10000);
    expires=(await page.evaluate(()=>Date.now()))/1000+3;
    await pair().first().tap();await page.waitForFunction(code=>document.querySelector('main').innerText.includes(code),code);
    await page.clock.runFor(5000);await absent();
    expires=(await page.evaluate(()=>Date.now()))/1000+120;
    await pair().first().tap();await page.waitForFunction(code=>document.querySelector('main').innerText.includes(code),code);
    await page.evaluate(()=>document.dispatchEvent(new Event('colony-leave')));await absent();
    assert(!(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).includes(code));
    assert.deepEqual(errors,[]);
    console.log('PASS phone installation repair access: passive health, background enable, explicit pairing and private cleanup');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
