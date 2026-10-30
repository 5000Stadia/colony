"""Native adapter and watcher reconnect contracts, without native auth or inference."""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from colony import board, codex_remote, console, lead, monitor, providers, signin, usage


class SigninIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, COLONY_BOARD_HOME=str(self.folder / 'board')))
        board.save_registry({'roots': [], 'settings': {'active_hours': 'off', 'messaging': False,
                                                     'monitor': False, 'auto_update': False}})
        self.claude, self.codex = providers.get('claude'), providers.get('codex')

    def patch(self, target, name, **kwargs):
        return self.stack.enter_context(patch.object(target, name, **kwargs))

    def test_native_prompt_parsers_accept_only_official_authorization_endpoints(self):
        for domain, path in (('claude.com', '/cai/oauth/authorize'), ('platform.claude.com', '/oauth/authorize')):
            url = f'https://{domain}{path}?client_id=fixture&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&state=fixture'
            prompt = self.claude.signin_prompt('\x1b[32mIf the browser did not open, visit: ' + url + '\x1b[0m\nPaste code here >')
            self.assertEqual(prompt, {'url': url, 'accept_code': True})
        for code in ('ABCD-1234', 'ABCD-12345'):
            text = '\x1b[1mhttps://auth.openai.com/codex/device\x1b[0m\n\n  \x1b[32m' + code + '\x1b[0m\n'
            self.assertEqual(self.codex.signin_prompt(text), {
                'url': 'https://auth.openai.com/codex/device', 'code': code, 'accept_code': False})
        for provider, good in ((self.claude, 'https://claude.com/cai/oauth/authorize'),
                               (self.codex, 'https://auth.openai.com/codex/device')):
            domain, path = good[8:].split('/', 1)
            bad = [good.replace('https:', 'http:'), good.replace(domain, domain + '.evil.example'),
                   'https://fixture@' + domain + '/' + path, 'https://' + domain + '@evil.example/' + path,
                   'https://' + domain + ':8443/' + path, 'javascript:' + good,
                   'https://' + domain + '/unrelated', good + '#fragment',
                   'https://' + domain + '/' + path + '\\evil', 'https://' + domain + '/' + path + '/extra']
            for url in bad:
                with self.subTest(provider=provider.program, url=url):
                    self.assertEqual(provider.signin_prompt(url + '\nABCD-1234\n'), {})

    def test_claude_code_input_is_one_line_and_validation_never_echoes_it(self):
        code = 'fixture-authorization#fixture-state'
        self.assertEqual(self.claude.signin_input('  ' + code + '  '), code + '\n')
        for bad in ('fixture-authorization', 'fixture-code\nsecond-line', 'fixture-code\rsecond-line', 'fixture-code\x00',
                    'fixture-code;command', 'short', 'x' * 4097):
            with self.subTest(length=len(bad)):
                with self.assertRaises(ValueError) as error:
                    self.claude.signin_input(bad)
                self.assertNotIn(bad, str(error.exception))
        with self.assertRaises(ValueError):
            self.codex.signin_input(code)

    def test_native_environments_strip_overrides_and_use_canonical_codex_home(self):
        canonical = self.folder / 'canonical-codex'
        inherited = self.folder / 'inherited-host-home'
        credentials = {key: 'fixture-private-override' for key in (
            'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_API_KEY',
            'CLAUDE_CODE_OAUTH_REFRESH_TOKEN', 'CLAUDE_CODE_OAUTH_SCOPES',
            'OPENAI_API_KEY', 'OPENAI_ACCESS_TOKEN', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN',
            'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY')}
        with patch.dict(os.environ, dict(credentials, COLONY_CODEX_SOURCE_HOME=str(canonical), CODEX_HOME=str(inherited))):
            for provider in (self.claude, self.codex):
                env = provider.signin_environment()
                self.assertFalse(set(credentials) & env.keys())
                self.assertEqual(env['BROWSER'], 'true')
            self.assertEqual(self.codex.signin_environment()['CODEX_HOME'], str(canonical))
            seen = []
            def native_status(argv, **kwargs):
                seen.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 0, '', 'Logged in using ChatGPT')
            self.assertTrue(self.codex.signed_in(run=native_status))
            self.assertEqual(seen[0][0], ['codex', 'login', 'status'])
            self.assertEqual(seen[0][1]['env']['CODEX_HOME'], str(canonical))

    def test_claude_verification_ignores_saved_override_but_normal_status_uses_it(self):
        self.claude.set_token('fixture-saved-token')
        seen = []
        def native_status(argv, **kwargs):
            seen.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, json.dumps({'loggedIn': True}), '')
        with patch.dict(os.environ, CLAUDE_CODE_OAUTH_TOKEN='fixture-inherited-token', ANTHROPIC_API_KEY='fixture-key'):
            self.assertTrue(self.claude.signed_in(run=native_status))
            self.assertEqual(seen[-1][1]['env']['CLAUDE_CODE_OAUTH_TOKEN'], 'fixture-saved-token')
            with patch('subprocess.run', side_effect=native_status):
                self.assertTrue(self.claude.signin_verified())
            self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', seen[-1][1]['env'])
            self.assertNotIn('ANTHROPIC_API_KEY', seen[-1][1]['env'])
            self.assertEqual(seen[-1][0], ['claude', 'auth', 'status', '--json'])
        self.assertEqual(self.claude.token(), 'fixture-saved-token', 'verification alone must not retire credentials')
        self.claude.signin_complete()
        self.assertIsNone(self.claude.token())

    def observation_fixture(self, screens=None, ended=None, running_key='claude', desired_key=None):
        self.clock = [100000.0]
        self.projects = [self.folder / 'project']
        self.projects[0].mkdir()
        self.nm = 'fixture-project-console'
        self.starts = {self.nm: 90000.0}
        self.screens = screens if screens is not None else {self.nm: '  ⎿ API Error: 401 unauthorized\n────────\n❯\n────────'}
        self.ended = ended or {}
        self.running_key, self.desired_key = running_key, desired_key or running_key
        self.patch(board, 'projects', return_value=self.projects)
        self.patch(console, 'session_name', side_effect=lambda root: self.nm)
        self.patch(console, 'running', side_effect=lambda nm: nm in self.screens)
        self.patch(console, 'running_program', side_effect=lambda nm: self.running_key)
        self.patch(console, 'provider', side_effect=lambda nm: providers.get(self.desired_key))
        self.patch(providers, 'of', side_effect=lambda root=None: providers.get(self.desired_key))
        self.patch(console, 'screen', side_effect=lambda nm: self.screens[nm])
        self.patch(console, 'died', side_effect=lambda nm: self.ended.get(nm))
        self.patch(console, 'age', side_effect=lambda nm: self.clock[0] - self.starts.get(nm, 0))
        self.patch(monitor.time, 'time', side_effect=lambda: self.clock[0])
        self.native_tick = self.patch(signin, 'tick')
        self.observed = self.patch(signin, 'observe')
        self.queued = self.patch(monitor, 'queue', side_effect=AssertionError('sign-in detection must not queue an agent'))
        self.patch(console, 'type_into', side_effect=AssertionError('sign-in detection must not type into an agent'))
        self.watcher = monitor.Watcher(enabled=False)
        self.watcher.signin_observed = 0
        return self.watcher

    def test_watcher_auth_detection_works_with_steward_off_muted_and_is_rate_limited(self):
        watcher = self.observation_fixture()
        with patch.object(monitor, 'muted', return_value=True):
            watcher.signin()
            watcher.signin()
        self.native_tick.assert_called()
        self.assertEqual(self.observed.call_count, 1, 'native scheduling continues but screens are observed at most every 30 seconds')
        self.assertEqual(self.observed.call_args.args[0], 'claude')
        self.assertEqual(self.observed.call_args.args[1]['code'], 'authentication')
        self.queued.assert_not_called()

    def test_watcher_uses_running_provider_during_pending_switch(self):
        watcher = self.observation_fixture(running_key='claude', desired_key='codex')
        watcher.signin()
        self.observed.assert_called_once()
        self.assertEqual(self.observed.call_args.args[0], 'claude')

    def test_verified_dead_console_is_attributed_to_its_last_running_provider(self):
        watcher = self.observation_fixture(screens={}, running_key='claude', desired_key='codex')
        self.ended[self.nm] = {'at': self.clock[0], 'status': 1, 'lines': ['API Error: 401 unauthorized']}
        watcher.signin()
        self.observed.assert_called_once()
        self.assertEqual(self.observed.call_args.args[0], 'claude',
                         'a queued provider switch does not change the source of the dead console failure')

    def test_post_login_old_live_and_dead_screens_cannot_relatch_reconnect(self):
        watcher = self.observation_fixture()
        connected = datetime.fromtimestamp(self.clock[0] - 100, timezone.utc).isoformat()
        with patch.object(signin, 'states', return_value={'claude': {'state': 'signed-in', 'connected_at': connected}}):
            watcher.signin()
            self.observed.assert_not_called()
            self.screens.clear()
            self.ended[self.nm] = {'at': self.clock[0] - 1000, 'status': 1, 'lines': ['API Error: 401 unauthorized']}
            self.clock[0] += 31
            watcher.signin()
            self.observed.assert_not_called()
            self.ended[self.nm]['at'] = self.clock[0]
            self.clock[0] += 31
            watcher.signin()
            self.observed.assert_called_once()

    def test_watcher_false_auth_alarms_are_rejected_by_real_provider_parsers(self):
        watcher = self.observation_fixture()
        samples = [('claude', '  ⎿ API Error: 403 forbidden\n────────\n❯'),
                   ('claude', '  ⎿ API Error: 429 rate limit\n────────\n❯'),
                   ('claude', '  ⎿ API Error: Connection error.\n────────\n❯'),
                   ('claude', '● Example output:\n  ⎿ API Error: 401 unauthorized\n────────\n❯'),
                   ('claude', '● Example: Not logged in · Please run /login\n────────\n❯'),
                   ('codex', '■ HTTP 403 forbidden\n›'),
                   ('codex', '■ HTTP 429 rate limited\n›'),
                   ('codex', '• Example output:\n■ HTTP 401 unauthorized\n›'),
                   ('codex', '• Working (3s • esc to interrupt)\n■ HTTP 401 unauthorized\n›')]
        for key, text in samples:
            with self.subTest(provider=key, text=text):
                self.running_key = self.desired_key = key
                self.screens[self.nm] = text
                self.clock[0] += 31
                watcher.signin()
                self.observed.assert_not_called()

    def test_watcher_detects_live_signedout_footer_and_verified_dead_401(self):
        watcher = self.observation_fixture()
        self.screens[self.nm] = '────────\n❯\n────────\nNot logged in · Please run /login'
        watcher.signin()
        self.observed.assert_called_once()
        self.observed.reset_mock()
        self.screens.clear()
        self.ended[self.nm] = {'at': self.clock[0], 'status': 1, 'lines': ['API Error: 401 unauthorized']}
        self.clock[0] += 31
        watcher.signin()
        self.observed.assert_called_once()
        self.assertEqual(self.observed.call_args.args[0], 'claude')
        self.observed.reset_mock()
        self.ended.clear()
        self.clock[0] += 31
        watcher.signin()
        self.observed.assert_not_called()

    def test_observation_marker_is_stable_for_old_screen_but_changes_with_console_generation(self):
        watcher = self.observation_fixture()
        watcher.signin()
        first = self.observed.call_args.args[2]
        self.clock[0] += 31
        watcher.signin()
        self.assertEqual(self.observed.call_args.args[2], first)
        self.starts[self.nm] = self.clock[0] - 2
        self.clock[0] += 31
        watcher.signin()
        self.assertNotEqual(self.observed.call_args.args[2], first,
                            'identical 401 output from a newly started console is a new authentication failure')

    def reload_fixture(self):
        now = 100000.0
        self.projects = [self.folder / 'older', self.folder / 'newer']
        for root in self.projects:
            root.mkdir()
        self.patch(board, 'projects', return_value=self.projects)
        self.patch(console, 'session_name', side_effect=lambda root: 'fixture-' + root.name)
        self.patch(console, 'running', side_effect=lambda nm: nm in ('fixture-older', 'fixture-newer'))
        self.patch(console, 'switching', return_value=None)
        self.patch(console, 'providers_of', return_value=self.claude)
        self.patch(console, 'running_program', return_value='claude')
        self.patch(monitor.time, 'time', return_value=now)
        self.patch(console, 'age', side_effect=lambda nm: 1000 if nm == 'fixture-older' else 5)
        self.snapshots = self.patch(console, 'snapshot', return_value={'state': 'idle', 'lines': []})
        self.drafting = self.patch(console, 'drafting', return_value=False)
        self.attached = self.patch(console, 'attached', return_value=False)
        self.room = self.patch(console, 'room', return_value=True)
        self.busy = self.patch(console, 'busy', return_value=None)
        self.paused = self.patch(usage, 'paused', return_value={})
        self.refresh = self.patch(type(self.claude), 'signin_refresh', return_value=True)
        self.reload = self.patch(console, 'reload')
        connected = datetime.fromtimestamp(now - 100, timezone.utc).isoformat()
        self.patch(signin, 'states', return_value={'claude': {'state': 'signed-in', 'connected_at': connected}})
        self.watcher = monitor.Watcher(enabled=False)
        self.watcher.current_checked = now
        return self.watcher

    def test_reconnect_reloads_only_older_idle_console_without_auto_update_or_changing_goal_pause(self):
        watcher = self.reload_fixture()
        root = str(self.projects[0].resolve())
        board.save_json(lead.location(), {'fixture': dict(lead.info(self.projects[0]),
                        goals={root: {'objective': 'Keep the current native goal', 'last_status': 'paused'}}, paused=True)})
        saved_goal = lead.location().read_bytes()
        settings = (board.home() / 'board.json').read_bytes()
        watcher.current()
        self.reload.assert_called_once_with(self.projects[0], 'fixture-older', None)
        self.refresh.assert_called_once_with(self.projects[0])
        self.assertEqual(lead.location().read_bytes(), saved_goal)
        self.assertEqual((board.home() / 'board.json').read_bytes(), settings)

    def test_reconnect_respects_busy_draft_attached_memory_and_usage_pause_guards(self):
        watcher = self.reload_fixture()
        cases = [(self.snapshots, {'state': 'working', 'lines': []}), (self.drafting, True),
                 (self.attached, True), (self.room, False), (self.busy, 'background command'),
                 (self.paused, {str(self.projects[0]): {'provider': 'claude', 'window': '5-hour'}}),
                 (self.refresh, False)]
        for guard, blocked in cases:
            with self.subTest(guard=guard):
                original = guard.return_value
                guard.return_value = blocked
                watcher.current()
                self.reload.assert_not_called()
                guard.return_value = original

    def test_reconnect_reloads_at_most_one_console_per_pass(self):
        watcher = self.reload_fixture()
        with patch.object(console, 'age', return_value=1000):
            watcher.current()
        self.assertEqual(self.reload.call_count, 1)

    def test_codex_refresh_uses_graceful_drain_and_leaves_raced_work_running(self):
        host = self.folder / 'host'
        with patch.object(codex_remote, 'home_for', return_value=host), patch.object(codex_remote, 'drain', return_value=False) as drain:
            self.assertFalse(self.codex.signin_refresh(self.folder))
            drain.assert_called_once_with(host)
        client = MagicMock()
        with patch.object(codex_remote, 'alive', return_value=True), \
                patch.object(codex_remote, 'read_json', return_value={'pid': 12345}), \
                patch.object(codex_remote, 'Client', return_value=client), \
                patch.object(codex_remote, 'idle', return_value=True), patch.object(codex_remote.os, 'kill') as kill:
            self.assertFalse(codex_remote.drain(host, timeout=0))
            kill.assert_called_once_with(12345, signal.SIGHUP)
