"""Provider reconnect routes and phone flow, with no native auth or live board access."""
import http.client
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer

from colony import board

ROOT = Path(__file__).resolve().parents[1]


def provider_state(state='reconnect', session=None):
    value = {'state': state, 'checked_at': '2026-10-30T16:00:00Z',
             'since': '2026-10-30T15:00:00Z',
             'reason': 'Sign in again.' if state == 'reconnect' else ''}
    if session is not None:
        value['session'] = session
    return value


class SigninBoardTest(unittest.TestCase):
    """Use real request parsing and page scripts; every provider action is a stand-in."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'COLONY_BOARD_HOME': str(Path(self.tmp.name) / 'board')})
        self.env.start()
        self.addCleanup(self.env.stop)
        board.save_registry({'roots': [], 'settings': {'active_hours': 'off', 'messaging': False, 'lan': False}})
        self.states = {'claude': provider_state(), 'codex': provider_state('unknown')}
        self.calls = []
        self.action_error = None
        self.server = None

    def start_server(self):
        class Handler(board.Handler):
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])
        return self.base

    def request(self, path, form=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        h = {'Origin': self.base}
        h.update(headers or {})
        data = None if form is None else (urlencode(form, doseq=True) if isinstance(form, dict) else form)
        if data is not None:
            h['Content-Type'] = 'application/x-www-form-urlencoded'
        try:
            conn.request('GET' if form is None else 'POST', path, data, h)
            response = conn.getresponse()
            return response.status, dict(response.headers), response.read().decode()
        finally:
            conn.close()

    def fixture(self):
        """Patch only the signin public boundary, not Handler's security or rendering."""
        from colony import signin
        def snapshot(key):
            if key not in self.states:
                raise ValueError('Unknown provider')
            return dict(self.states[key])

        def action(name):
            def perform(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                if self.action_error:
                    raise self.action_error
                return snapshot(args[0])
            return perform

        patches = [patch.object(signin, 'states', side_effect=lambda: dict(self.states)),
                   patch.object(signin, 'state', side_effect=snapshot)]
        for name in ('start', 'submit', 'cancel', 'check', 'tick'):
            patches.append(patch.object(signin, name, side_effect=action(name)))
        if hasattr(signin, 'request_check'):
            patches.append(patch.object(signin, 'request_check', side_effect=action('check')))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return signin

    def test_status_and_page_are_passive_cached_reads_with_private_response_headers(self):
        self.fixture()
        self.start_server()
        for path in ('/signin/status', '/signin'):
            status, headers, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertEqual(headers.get('Cache-Control'), 'no-store')
            self.assertEqual(headers.get('Referrer-Policy'), 'no-referrer')
            if path.endswith('/status'):
                self.assertIn('application/json', headers.get('Content-Type', ''))
                self.assertEqual(json.loads(body), {'providers': self.states})
            else:
                self.assertIn('provider-signins', body)
                self.assertIn("data-provider='claude'", body)
                self.assertIn("data-provider='codex'", body)
        self.assertEqual(self.calls, [], 'page loads and polls must never run native auth or login')

    def test_actions_accept_only_provider_session_and_code_parameters(self):
        self.fixture()
        self.start_server()
        forms = [('/signin/start', {'provider': 'claude'}, 'start', ('claude',)),
                 ('/signin/code', {'provider': 'claude', 'session': 'fixture-session', 'code': 'fixture-code'},
                  'submit', ('claude', 'fixture-session', 'fixture-code')),
                 ('/signin/cancel', {'provider': 'codex', 'session': 'fixture-session'},
                  'cancel', ('codex', 'fixture-session')),
                 ('/signin/check', {'provider': 'codex'}, 'check', ('codex',))]
        for path, form, action, args in forms:
            with self.subTest(path=path):
                form.update(command='must-not-run', host='evil.example')
                status, headers, body = self.request(path, form)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {'providers': self.states})
                self.assertEqual(headers.get('Cache-Control'), 'no-store')
                self.assertEqual(headers.get('Referrer-Policy'), 'no-referrer')
                self.assertEqual(self.calls[-1][0:2], (action, args))
                self.assertFalse(self.calls[-1][2], 'arbitrary native options must not pass through')

    def test_invalid_and_stale_requests_fail_without_leaking_exception_or_codes(self):
        self.fixture()
        self.start_server()
        for provider in ('', 'shell', 'claude --help', '../codex'):
            for path in ('/signin/start', '/signin/check', '/signin/code', '/signin/cancel'):
                with self.subTest(provider=provider, path=path):
                    status, _, body = self.request(path, {'provider': provider, 'session': 'stale', 'code': 'fixture-code'})
                    self.assertIn(status, (400, 409))
                    self.assertNotIn('fixture-code', body)
        self.assertEqual(self.calls, [], 'unknown providers must be rejected before dispatch')
        self.action_error = ValueError('sensitive-native-detail fixture-code')
        status, _, body = self.request('/signin/code', {'provider': 'claude', 'session': 'stale', 'code': 'fixture-code'})
        self.assertEqual(status, 409)
        self.assertNotIn('sensitive-native-detail', body)
        self.assertNotIn('fixture-code', body)
        self.action_error = RuntimeError('private traceback fixture-code')
        status, _, body = self.request('/signin/start', {'provider': 'claude'})
        self.assertIn(status, (400, 409, 500, 503))
        self.assertNotIn('private traceback', body)
        self.assertNotIn('fixture-code', body)

    def test_origin_host_and_tailscale_rejections_happen_before_actions(self):
        self.fixture()
        self.start_server()
        for path in ('/signin/start', '/signin/check', '/signin/code', '/signin/cancel'):
            for headers in ({'Origin': 'https://evil.example'}, {'Origin': 'null'},
                            {'Host': 'evil.example', 'Origin': 'http://evil.example'}):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.request(path, {'provider': 'claude'}, headers)[0], 403)
        reg = board.registry()
        reg['settings']['tailscale'] = 'fixture-tailnet'
        board.save_registry(reg)
        with patch('colony.tailscale.own_device', return_value=False):
            self.assertEqual(self.request('/signin/status')[0], 403)
            self.assertEqual(self.request('/signin/start', {'provider': 'claude'})[0], 403)
        self.assertEqual(self.calls, [])
        # The existing remote transport permits HTTPS Origin from Tailscale Serve.
        self.server.lan = True
        host = '100.90.80.70:' + str(self.server.server_address[1])
        with patch('colony.tailscale.own_device', return_value=True):
            status, _, _ = self.request('/signin/start', {'provider': 'codex'},
                                        {'Host': host, 'Origin': 'https://' + host})
        self.assertEqual(status, 200)
        self.assertEqual(self.calls[-1][0:2], ('start', ('codex',)))

    def test_native_display_fields_cannot_escape_the_page(self):
        self.fixture()
        self.start_server()
        attack = '</script><img src=x onerror="window.signinInjected=1">'
        self.states['claude']['reason'] = attack
        self.states['claude']['session'] = {
            'id': 'fixture-session', 'state': 'waiting', 'message': attack,
            'accept_code': True, 'url': 'https://claude.com/cai/oauth/authorize?fixture=1'}
        status, _, body = self.request('/signin')
        self.assertEqual(status, 200)
        self.assertNotIn(attack, body, 'native messages must be text, including inside initial JSON')
        self.assertNotIn('<img src=x', body)

    def test_global_notice_is_only_for_a_reconnect_state(self):
        self.fixture()
        with patch.object(board, 'sidebar', return_value=''), patch.object(board, 'trouble_bar', return_value=''):
            for state, expected in (('signed-in', False), ('unknown', False), ('reconnect', True)):
                self.states = {'claude': provider_state(state)}
                markup = board.shell(board.registry(), -1, '<p>Fixture</p>')
                notice = re.search(r"<div\b[^>]*id=['\"]signin-notice['\"][^>]*>", markup)
                self.assertIsNotNone(notice)
                self.assertEqual(' hidden' not in notice[0], expected, state)
                if expected:
                    self.assertIn('/signin', markup)

    @unittest.skipUnless(os.environ.get('COLONY_BROWSER_TEST') == '1', 'requires isolated Chrome/Playwright')
    def test_phone_reconnect_submit_return_refresh_and_terminal_cleanup(self):
        self.fixture()
        self.start_server()
        result = subprocess.run(['node', str(ROOT / 'tests/signin_browser.js'), self.base],
                                text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS phone provider reconnect', result.stdout)
