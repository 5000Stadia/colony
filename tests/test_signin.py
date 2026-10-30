"""Native sign-in uses disposable local stand-ins, never an account or model call."""
import concurrent.futures
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from colony import board, providers, signin


class NativeProvider:
    program = 'fake-native-login'
    label = 'Fake'

    def __init__(self, folder):
        self.folder = folder
        self.status = False
        self.verified = True
        self.completions = 0
        self.mode = 'input'
        self.probes = 0

    def signed_in(self):
        self.probes += 1
        return self.status

    def signin_verified(self):
        return self.verified

    def signin_complete(self):
        self.completions += 1

    def signin_command(self):
        return [sys.executable, '-u', '-c', r'''
import os, subprocess, sys, time
assert os.getcwd() == os.environ['EXPECTED_LOGIN_CWD']
assert 'FAKE_API_TOKEN' not in os.environ
mode = sys.argv[1]
if mode == 'descendant':
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    with open('descendant.pid', 'w') as handle:
        handle.write(str(child.pid))
if mode == 'fail':
    print('SECRET-native-error-account@example.com', flush=True)
    sys.exit(1)
# Deliberately no newline, including an ANSI escape split across writes.
for fragment in ['SECRET-private-token account@example.com\\n', '\x1b[', '32mhttps://auth.example.test/device', '\x1b[0m', ' DEVICE-CODE ', ' enter-code:']:
    sys.stdout.write(fragment)
    sys.stdout.flush()
    time.sleep(.02)
if mode == 'input':
    value = sys.stdin.readline()
    sys.exit(0 if value == 'valid_auth_code\n' else 2)
time.sleep(60)
''', self.mode]

    def signin_environment(self):
        return {'PATH': os.environ['PATH'], 'EXPECTED_LOGIN_CWD': str(self.folder)}

    def signin_prompt(self, text):
        result = {}
        if 'https://auth.example.test/device' in text:
            result['url'] = 'https://auth.example.test/device'
        if 'DEVICE-CODE' in text:
            result['code'] = 'DEVICE-CODE'
        if 'enter-code:' in text:
            result['accept_code'] = True
        return result

    def signin_input(self, code):
        if not isinstance(code, str) or not re.fullmatch(r'[a-z_]{1,100}', code):
            raise ValueError('Invalid authorization code.')
        return code + '\n'


class SigninTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.provider = NativeProvider(self.folder)
        self.patches = [patch.dict(os.environ, COLONY_BOARD_HOME=str(self.folder), FAKE_API_TOKEN='SECRET-env'),
                        patch.object(providers, 'PROVIDERS', {'fake': self.provider}),
                        patch.object(providers, 'usable', return_value=True),
                        patch.object(providers, 'signin_eligible', return_value=True, create=True)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.stop_sessions)

    def stop_sessions(self):
        session = signin._slot('fake')
        if session:
            if session['state'] in signin._ACTIVE:
                signin.cancel('fake', session['id'])
            session['worker'].join(timeout=3)
        if signin._checker:
            signin._checker.join(timeout=3)
        signin._sessions.pop((str(self.folder), 'fake'), None)

    def wait_for(self, predicate):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            value = signin.state('fake')
            if predicate(value):
                return value
            time.sleep(.01)
        self.fail('Sign-in stand-in did not reach the expected lifecycle state.')

    def waiting(self):
        return self.wait_for(lambda row: bool((row['session'] or {}).get('accept_code')))

    def test_cached_native_checks_and_unknown_never_claim_success(self):
        self.assertEqual(signin.state('fake')['state'], 'unknown')
        self.assertEqual(self.provider.probes, 0)
        self.provider.status = True
        self.assertEqual(signin.check('fake')['state'], 'signed-in')
        signin.check('fake')
        self.assertEqual(self.provider.probes, 1)
        self.provider.status = None
        self.assertEqual(signin.check('fake', force=True)['state'], 'unknown')
        self.provider.status = False
        row = signin.check('fake', force=True)
        self.assertEqual(row['state'], 'reconnect')
        self.provider.status = None
        self.assertEqual(signin.check('fake', force=True)['since'], row['since'])
        self.assertIn('fake', board.signed_out())

    def test_runtime_authentication_is_sticky_and_safe(self):
        problem = {'code': 'authentication', 'reason': 'SECRET-api-token account@example.com'}
        row = signin.observe('fake', problem, 'SECRET-screen-contents')
        self.assertEqual(row['state'], 'reconnect')
        self.provider.status = True
        self.assertEqual(signin.check('fake', force=True)['state'], 'reconnect')
        saved = (self.folder / 'signin-state.json').read_text()
        self.assertNotIn('SECRET', saved)
        self.assertNotIn('account@example.com', saved)
        self.assertEqual(len(json.loads(saved)['fake']['markers'][0]), 64)
        before = (self.folder / 'signin-state.json').stat().st_mtime_ns
        signin.observe('fake', problem, 'SECRET-screen-contents')
        self.assertEqual((self.folder / 'signin-state.json').stat().st_mtime_ns, before)
        for code in ('authorization', 'usage-limit', 'network', 'unknown'):
            signin.observe('fake', {'code': code}, code)
        self.assertEqual(len(json.loads(saved)['fake']['markers']), 1)

    def test_legacy_signed_out_is_preserved_before_any_new_checks(self):
        board.save_json(self.folder / 'signed-out.json', {'fake': '2026-10-29 12:30'})
        row = signin.state('fake')
        self.assertEqual(row['state'], 'reconnect')
        self.assertEqual(row['since'], '2026-10-29 12:30')
        self.provider.status = None
        self.assertEqual(signin.check('fake', force=True)['state'], 'reconnect')

    def test_parallel_taps_share_one_process_and_chunked_prompt(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            snapshots = list(pool.map(lambda _: signin.start('fake'), range(8)))
        self.assertEqual(len({row['session']['id'] for row in snapshots}), 1)
        row = self.waiting()
        self.assertEqual(row['session']['url'], 'https://auth.example.test/device')
        self.assertEqual(row['session']['code'], 'DEVICE-CODE')
        self.assertEqual(row['session']['state'], 'waiting')
        self.assertNotIn('SECRET', json.dumps(row))
        self.assertNotIn('account@example.com', json.dumps(row))
        self.assertFalse((self.folder / 'signin-state.json').exists())
        self.assertFalse((self.folder / 'signed-out.json').exists())

    def test_verified_login_clears_failure_but_old_screen_cannot_relatch(self):
        problem = {'code': 'authentication'}
        signin.observe('fake', problem, 'old-screen')
        signin.start('fake')
        row = self.waiting()
        session_id = row['session']['id']
        signin.submit('fake', session_id, 'valid_auth_code')
        row = self.wait_for(lambda row: row['session']['state'] == 'done')
        self.assertEqual(row['state'], 'signed-in')
        self.assertEqual(self.provider.completions, 1)
        self.assertIsNotNone(row['connected_at'])
        self.assertNotIn('fake', board.signed_out())
        self.assertNotIn('url', row['session'])
        self.assertNotIn('code', row['session'])
        signin.observe('fake', problem, 'old-screen')
        self.assertEqual(signin.state('fake')['state'], 'signed-in')
        signin.observe('fake', problem, 'new-screen')
        self.assertEqual(signin.state('fake')['state'], 'reconnect')
        for path in self.folder.glob('*.json'):
            saved = path.read_text()
            self.assertNotIn('https://', saved)
            self.assertNotIn('DEVICE-CODE', saved)
            self.assertNotIn('valid_auth_code', saved)

    def test_cancel_kills_process_and_preserves_healthy_prior_credentials(self):
        self.provider.status = True
        signin.check('fake')
        self.provider.mode = 'wait'
        signin.start('fake')
        row = self.waiting()
        process = signin._slot('fake')['process']
        session_id = row['session']['id']
        row = signin.cancel('fake', session_id)
        self.assertEqual(row['state'], 'signed-in')
        self.assertEqual(row['session']['state'], 'cancelled')
        self.assertNotIn('url', row['session'])
        self.assertIsNotNone(process.poll())
        self.assertFalse(signin._slot('fake')['worker'].is_alive())
        for method in (signin.cancel,):
            with self.assertRaises(ValueError):
                method('fake', session_id)
        self.assertEqual(self.provider.completions, 0)

    def test_expiry_kills_process_and_new_session_has_a_new_id(self):
        self.provider.mode = 'wait'
        with patch.object(signin, 'SESSION_SECONDS', .3):
            before = signin.start('fake')
        self.waiting()
        worker = signin._slot('fake')['worker']
        row = self.wait_for(lambda row: row['session']['state'] == 'expired')
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertNotIn('code', row['session'])
        after = signin.start('fake')
        self.assertNotEqual(before['session']['id'], after['session']['id'])
        with self.assertRaises(ValueError):
            signin.cancel('fake', before['session']['id'])

    def test_code_submission_requires_active_matching_session_and_valid_input(self):
        signin.start('fake')
        session = self.waiting()['session']
        for session_id, code in [('', 'valid_auth_code'), ('wrong', 'valid_auth_code'),
                                 (session['id'], 'a\nmalicious'), (session['id'], 'a\rb')]:
            with self.assertRaises(ValueError):
                signin.submit('fake', session_id, code)
        signin.submit('fake', session['id'], 'valid_auth_code')
        with self.assertRaises(ValueError):
            signin.submit('fake', session['id'], 'valid_auth_code')
        self.wait_for(lambda row: row['session']['state'] == 'done')

    def test_exit_zero_requires_native_verification(self):
        self.provider.verified = False
        signin.start('fake')
        session_id = self.waiting()['session']['id']
        signin.submit('fake', session_id, 'valid_auth_code')
        row = self.wait_for(lambda row: row['session']['state'] == 'failed')
        self.assertEqual(row['state'], 'unknown')
        self.assertEqual(self.provider.completions, 0)
        self.assertNotIn('url', row['session'])

    def test_native_failure_and_spawn_failure_are_safe_and_retryable(self):
        self.provider.mode = 'fail'
        first = signin.start('fake')['session']['id']
        row = self.wait_for(lambda row: row['session']['state'] == 'failed')
        self.assertNotIn('SECRET', json.dumps(row))
        self.assertNotIn('account@example.com', json.dumps(row))
        with patch.object(signin.subprocess, 'Popen', side_effect=OSError('SECRET-in-exception')):
            self.assertNotEqual(signin.start('fake')['session']['id'], first)
            self.wait_for(lambda row: row['session']['state'] == 'failed')
        self.assertEqual(self.provider.completions, 0)

    def test_adapter_invalid_url_is_not_exposed(self):
        for url in ('http://auth.example.test', 'https://user:pass@auth.example.test',
                    'javascript:alert(1)', 'https://auth.example.test/\nSECRET'):
            with patch.object(self.provider, 'signin_prompt', return_value={'url': url, 'raw': 'SECRET'}):
                self.assertEqual(signin._safe_prompt(self.provider, 'native output'), {})
        with patch.object(self.provider, 'signin_prompt', return_value={}):
            self.assertEqual(signin._safe_prompt(self.provider, 'https://untrusted.example/SECRET'), {})

    def test_background_checks_deduplicate_and_fixture_ticks_skip_native_probes(self):
        entered, release = threading.Event(), threading.Event()
        def status():
            entered.set()
            release.wait(timeout=3)
            return False
        with patch.object(self.provider, 'signed_in', side_effect=status) as native:
            try:
                signin.request_check()
                self.assertTrue(entered.wait(timeout=2))
                for _ in range(10):
                    signin.request_check()
                self.assertEqual(native.call_count, 1)
            finally:
                release.set()
                signin._checker.join(timeout=2)
        with patch.dict(os.environ, COLONY_CONSOLE_CMD='fixture'), \
                patch.object(signin, 'request_check') as scheduled:
            signin.tick()
        scheduled.assert_not_called()

    def test_ineligible_provider_never_probed_or_started(self):
        with patch.object(providers, 'signin_eligible', return_value=False):
            self.assertEqual(signin.states(), {})
            signin.check(force=True)
            signin.request_check()
            with self.assertRaises(ValueError):
                signin.start('fake')
        self.assertEqual(self.provider.probes, 0)
        with self.assertRaises(ValueError):
            signin.start('unknown')

    def test_inflight_old_status_cannot_overwrite_a_new_runtime_failure(self):
        entered, release = threading.Event(), threading.Event()
        def status():
            entered.set()
            release.wait(timeout=3)
            return True
        with patch.object(self.provider, 'signed_in', side_effect=status):
            worker = threading.Thread(target=signin.check)
            worker.start()
            self.assertTrue(entered.wait(timeout=2))
            signin.observe('fake', {'code': 'authentication'}, 'new-failure')
            release.set()
            worker.join(timeout=2)
        self.assertEqual(signin.state('fake')['state'], 'reconnect')

    def test_cancel_cleans_native_descendants_as_well_as_supervisor(self):
        self.provider.mode = 'descendant'
        signin.start('fake')
        session = self.waiting()['session']
        child = int((self.folder / 'descendant.pid').read_text())
        signin.cancel('fake', session['id'])
        until = time.monotonic() + 2
        while time.monotonic() < until:
            status = Path('/proc') / str(child) / 'stat'
            if not status.exists() or status.read_text().split()[2] == 'Z':
                break
            time.sleep(.01)
        else:
            self.fail('Native login descendant survived cancellation.')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux parent-death protection')
    def test_supervisor_rejects_already_dead_parent(self):
        # This is the startup race in a server crash: parent changed before prctl.
        process = subprocess.Popen([sys.executable, '-I', '-c', signin._SUPERVISOR,
                                    str(os.getpid() + 999999), sys.executable, '-c',
                                    'raise RuntimeError("native child must never start")'],
                                   start_new_session=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, cwd=self.folder)
        try:
            output, _ = process.communicate(timeout=2)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, 9)
                process.wait(timeout=2)
        self.assertEqual(process.returncode, -9)
        self.assertEqual(output, b'')

    def test_login_verification_precedes_source_override_cleanup(self):
        order = []
        def verified():
            order.append('verified')
            return True
        def complete():
            self.assertEqual(order, ['verified'])
            order.append('complete')
        with patch.object(self.provider, 'signin_verified', side_effect=verified), \
                patch.object(self.provider, 'signin_complete', side_effect=complete):
            signin.start('fake')
            session = self.waiting()['session']
            signin.submit('fake', session['id'], 'valid_auth_code')
            self.wait_for(lambda row: row['session']['state'] == 'done')
        self.assertEqual(order, ['verified', 'complete'])

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux parent-death protection')
    def test_native_login_dies_when_board_parent_is_killed(self):
        native = "import os,time; open('native-ready','w').write(str(os.getpid())); time.sleep(60)"
        launcher = ("import os,subprocess,sys,time; "
                    "p=subprocess.Popen([sys.executable,'-I','-c'," + repr(signin._SUPERVISOR) +
                    ",str(os.getpid()),sys.executable,'-c'," + repr(native) +
                    "],start_new_session=True); print(p.pid,flush=True); time.sleep(60)")
        parent = subprocess.Popen([sys.executable, '-I', '-c', launcher], cwd=self.folder,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            supervisor = int(parent.stdout.readline())
            until = time.monotonic() + 2
            ready = self.folder / 'native-ready'
            while time.monotonic() < until and not ready.exists():
                time.sleep(.01)
            self.assertTrue(ready.exists())
            child = int(ready.read_text())
            parent.kill()
            parent.wait(timeout=2)
            for pid in (supervisor, child):
                until = time.monotonic() + 2
                while time.monotonic() < until:
                    status = Path('/proc') / str(pid) / 'stat'
                    if not status.exists() or status.read_text().split()[2] == 'Z':
                        break
                    time.sleep(.01)
                else:
                    self.fail('Native login survived the board parent being killed.')
        finally:
            if parent.poll() is None:
                parent.kill()
            parent.wait(timeout=2)
            parent.stdout.close()
