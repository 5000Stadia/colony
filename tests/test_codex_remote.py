"""Native Codex integration with a local Responses fixture: no account or model calls."""
from contextlib import contextmanager
import json
import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from colony import board, bench, console, codex_remote as remote
from colony.codex_rpc import Client
from colony.codex_transfer import transfer, TransferError, writers

REPO = Path(__file__).resolve().parent.parent


class Responses(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if self.path == '/token':
            with self.server.auth_lock:
                requested = json.loads(body)['refresh_token']
                valid = requested == 'fixture-refresh-' + str(self.server.refreshes)
                if valid:
                    self.server.refreshes += 1
                    result = dict(access_token=self.server.jwt(self.server.refreshes),
                                  refresh_token='fixture-refresh-' + str(self.server.refreshes),
                                  id_token=self.server.jwt(0))
                else:
                    result = {'error': {'code': 'refresh_token_reused', 'message': 'fixture rotation'}}
            data = json.dumps(result).encode()
            self.send_response(200 if valid else 400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.server.requests.append(body)
        self.server.entered.set()
        self.server.release.wait(20)
        events = [{'type': 'response.created', 'response': {'id': 'resp'}}]
        if self.server.question:
            self.server.question = False
            item = dict(type='function_call', name='request_user_input', call_id='color-call',
                        arguments=json.dumps({'questions': [{'id': 'color', 'header': 'Color',
                                                               'question': 'Which color?',
                                                               'options': [{'label': 'Blue', 'description': 'Blue paint'},
                                                                           {'label': 'Red', 'description': 'Red paint'}]}]}))
        else:
            item = dict(type='message', role='assistant', id='message',
                        content=[dict(type='output_text', text=self.server.answer)])
        tokens = self.server.usage_sequence.pop(0) if getattr(self.server, 'usage_sequence', []) else 0
        events += [{'type': 'response.output_item.done', 'item': item},
                   {'type': 'response.completed', 'response': {'id': 'resp', 'usage': {
                       'input_tokens': tokens, 'output_tokens': 0, 'total_tokens': tokens}}}]
        data = ''.join('data: ' + json.dumps(e) + '\n\n' for e in events).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def installed():
    return shutil.which('codex') and any(v in subprocess.run(
        ['codex', '--version'], capture_output=True, text=True).stdout for v in ('0.159.3', '0.160.0'))


@unittest.skipUnless(installed(), 'native checks require Codex 0.159.3 or 0.160.0')
class NativeRemoteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='colony-remote-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(('COLONY_', 'OPENAI_', 'CODEX_'))}
        env.update(COLONY_BOARD_HOME=str(self.base / 'board'), CODEX_HOME=str(self.source), PYTHONPATH=str(REPO))
        self.environment = patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Responses)
        self.server.requests, self.server.question = [], False
        self.server.entered, self.server.release = threading.Event(), threading.Event()
        self.server.release.set()
        self.server.answer = 'The probe passed. Which color would you like?'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.source.joinpath('auth.json').write_text('{}')
        self.source.joinpath('config.toml').write_text(
            'model_provider="fixture"\n[model_providers.fixture]\nname="Fixture"\n'
            f'base_url="http://127.0.0.1:{self.server.server_port}"\n'
            'wire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n')
        board.save_registry({'roots': [], 'settings': {'messaging': False, 'provider': 'codex', 'trust': True}})
        self.work = self.base / 'work'
        self.work.mkdir()
        self.roots = [self.base / name for name in ('first', 'second')]
        for root in self.roots:
            (root / '.board').mkdir(parents=True)
            (root / '.board' / 'settings.json').write_text(json.dumps({'provider': 'codex', 'workdir': str(self.work)}))
            (root / 'ROADMAP.md').write_text('# Test\n\n## M1 — Test\n\n- [~] R1 Test\n')
        self.settings = [dict(model='gpt-6-astra', effort='high', permissions='all', remote=True),
                         dict(model='gpt-5.6-luna', effort='low', permissions='plan', remote=True)]
        for root, settings in zip(self.roots, self.settings):
            bench.set_plan(root, 'routine', settings['model'], settings['effort'])

    @contextmanager
    def test_a_new_conversation_is_adopted_from_its_first_hook(self):
        root = Path(self.tmp.name) / 'fresh'
        root.mkdir(exist_ok=True)
        from colony import context
        with patch.object(context, 'seat', return_value='console'), patch.object(context, 'is_monitor', return_value=False), \
                patch.dict(os.environ, COLONY_CONSOLE='console'):
            home = remote.home_for(root); home.mkdir(parents=True, exist_ok=True)
            self.assertTrue(remote.accepts_hook(root, {'session_id': 'first', 'transcript_path': '/x'}))
            self.assertEqual(remote.read_json(home / 'colony-thread.json')['thread'], 'first')
            self.assertFalse(remote.accepts_hook(root, {'session_id': 'other', 'transcript_path': '/y'}))

    def native(self, root, settings, home=None, prepare=True):
        home = home or remote.home_for(root)
        config = remote.overrides(root, home, settings)
        if prepare:
            remote.prepare_home(root, home, self.source)
        with Client(command=['codex', 'app-server', '--stdio', *remote.flags(config)],
                    env=remote.environment(root, home, self.source), cwd=self.work) as client:
            remote.trust_hooks(client, root, config)
            yield client, config, home

    def turn(self, client, tid, text, **params):
        result = client.call('turn/start', dict(threadId=tid, input=[dict(type='text', text=text, text_elements=[])], **params))
        deadline = time.monotonic() + 30
        while True:
            event = client.receive(deadline)
            if event.get('method') == 'turn/completed':
                self.assertEqual(event['params']['turn']['status'], 'completed')
                return result['turn']['id']

    def test_two_projects_same_folder_keep_settings_hooks_and_cold_resume(self):
        ids = []
        for root, settings in zip(self.roots, self.settings):
            board.add_note(root, None, 'Private note for ' + root.name, author='person')
            with self.native(root, settings) as (client, config, home):
                tid = remote.attach(client, root, config, None)
                ids.append(tid)
                self.turn(client, tid, 'App words for ' + root.name)
                self.assertEqual(board.read(root, 'said.jsonl')[0]['text'], 'App words for ' + root.name)
                self.assertIn('probe passed', board.read(root, 'said.jsonl')[-1]['reply'])
                self.assertTrue(board.asks(root), 'final question reaches the board')
                self.assertTrue(board.notes(root)[0]['delivered_at'])
                overlay = Path(config['agents.colony-routine.config_file']).read_text()
                self.assertIn(settings['model'], overlay)
                self.assertIn(settings['effort'], overlay)
            with self.native(root, settings) as (client, config, home):
                self.assertEqual(remote.attach(client, root, config, tid), tid)
                self.turn(client, tid, 'After cold resume ' + root.name)
                texts = [r['text'] for r in board.read(root, 'said.jsonl') if 'text' in r]
                self.assertEqual(texts, ['App words for ' + root.name, 'After cold resume ' + root.name])
        self.assertNotEqual(ids[0], ids[1])
        for i, request in enumerate(self.server.requests):
            own = 'first' if i < 2 else 'second'
            self.assertIn(('Private note for ' + own).encode(), request)
            self.assertNotIn(('Private note for ' + ('second' if own == 'first' else 'first')).encode(), request)

    def test_compaction_restores_bounded_verbatim_context_once_and_survives_resume(self):
        from colony import context
        from colony.codex_transfer import atomic_json
        root, settings = self.roots[0], self.settings[0]
        words = 'Keep  exact\n\nΩ spacing. ' + ' x' * 2250
        with self.native(root, settings) as (client, config, home):
            tid = remote.attach(client, root, config, None)
            self.turn(client, tid, words)
            atomic_json(context.file(root), dict(job=dict(id='1234567890abcdef', session=tid,
                        phase='ready', carry='Already sent invoice; do not resend.' + ' z' * 1400, written='now')))
            client.call('thread/compact/start', dict(threadId=tid))
            deadline = time.monotonic() + 30
            while client.receive(deadline).get('method') != 'turn/completed':
                pass
            self.turn(client, tid, 'Continue without repeating completed work.')
            text = self.server.requests[-1].decode()
            self.assertEqual(text.count('colony-restored-1234567890abcdef'), 1)
            body = json.loads(text)
            restored = [c['text'] for m in body['input'] for c in m.get('content', [])
                        if 'colony-restored-' in c.get('text', '')]
            self.assertIn(words, restored[0])
            self.assertIn('Already sent invoice; do not resend.', restored[0])
            self.assertNotIn('persisted-output', restored[0])
            self.assertEqual(context.read(context.file(root))['job']['phase'], 'restored')
        with self.native(root, settings, home=home, prepare=False) as (client, config, home):
            self.assertEqual(remote.attach(client, root, config, tid), tid)
            self.turn(client, tid, 'Continue after server restart.')
            self.assertEqual(self.server.requests[-1].decode().count('colony-restored-1234567890abcdef'), 1)

    def test_automatic_compaction_restores_before_same_turn_continues(self):
        from colony import context
        root, settings = self.roots[0], self.settings[0]
        # First response asks a tool question and crosses the native auto-compact threshold.
        self.server.question = True
        self.server.usage_sequence = [120_000, 0, 0]
        original = remote.overrides
        def config(*args):
            return dict(original(*args), model_auto_compact_token_limit=100_000)
        with patch.object(remote, 'overrides', side_effect=config), self.native(root, settings) as (client, cfg, home):
            tid = remote.attach(client, root, cfg, None)
            client.call('turn/start', dict(threadId=tid, input=[dict(type='text', text='Ask me to choose paint, then finish.', text_elements=[])],
                        collaborationMode=dict(mode='plan', settings=dict(model=settings['model'], reasoning_effort='low', developer_instructions=None))))
            deadline = time.monotonic() + 45
            while True:
                event = client.receive(deadline)
                if event.get('method') == 'item/tool/requestUserInput':
                    client.send(dict(id=event['id'], result=dict(answers={'color': {'answers': ['Blue']}})))
                if event.get('method') == 'turn/completed':
                    self.assertEqual(event['params']['turn']['status'], 'completed')
                    break
            job = context.read(context.file(root))['job']
            self.assertTrue(job['automatic'])
            self.assertEqual(job['phase'], 'restored')
            text = self.server.requests[-1].decode()
            self.assertEqual(text.count('colony-restored-' + job['id']), 1)
            self.assertTrue('Ask me to choose paint, then finish.' in text, 'active input survives native automatic compaction')

    def test_transfer_preserves_id_and_original_history_and_excludes_another_project(self):
        root, settings = self.roots[0], self.settings[0]
        original = remote.home_for(root)
        with self.native(root, settings) as (client, config, home):
            tid = remote.attach(client, root, config, None)
            self.turn(client, tid, 'History to keep')
            path = Path(client.call('thread/read', {'threadId': tid})['thread']['path'])
            unrelated = remote.attach(client, root, config, None)
            self.turn(client, unrelated, 'Other project words')
            with self.assertRaisesRegex(TransferError, 'writer'):
                transfer(home, self.base / 'blocked', tid)
        history = path.read_bytes()
        destination = self.base / 'transferred'
        remote.prepare_home(root, destination, self.source)
        transfer(original, destination, tid)
        transfer(original, destination, tid)          # retry is a no-op, never copies an older source again
        # Source history is temporarily unavailable; the new runtime must be independent.
        parked = original / 'sessions-parked'
        (original / 'sessions').rename(parked)
        try:
            with self.native(root, settings, destination) as (client, config, home):
                self.assertEqual(remote.attach(client, root, config, tid), tid)
                self.turn(client, tid, 'After migration')
                all_threads = client.call('thread/list', {})['data']
                self.assertNotIn(unrelated, [t['id'] for t in all_threads])
                self.assertIn(b'History to keep', self.server.requests[-1])
        finally:
            parked.rename(original / 'sessions')
        self.assertEqual(path.read_bytes(), history, 'destination turn never writes original rollout')
        with self.native(root, settings) as (client, config, home):
            with self.assertRaisesRegex(Exception, 'archived'):
                remote.attach(client, root, config, tid)

    def test_live_interactive_question_reaches_board_before_stop(self):
        root, settings = self.roots[0], self.settings[0]
        self.server.question, self.server.answer = True, 'Paint selected.'
        with self.native(root, settings) as (client, config, home):
            tid = remote.attach(client, root, config, None)
            client.call('turn/start', {'threadId': tid, 'input': [dict(type='text', text='Choose paint', text_elements=[])],
                                      'collaborationMode': {'mode': 'plan', 'settings': {
                                          'model': settings['model'], 'reasoning_effort': settings['effort'],
                                          'developer_instructions': None}}})
            deadline = time.monotonic() + 30
            while True:
                event = client.receive(deadline)
                if event.get('method') == 'item/tool/requestUserInput':
                    self.assertIn('Which color?', board.asks(root)[0]['text'])
                    client.send({'id': event['id'], 'result': {'answers': {'color': {'answers': ['Blue']}}}})
                if event.get('method') == 'turn/completed':
                    break
            self.assertFalse(board.asks(root))
            self.assertTrue(any('Blue' in r.get('text', '') for r in board.read(root, 'said.jsonl')))

    def test_owned_daemon_keeps_active_app_work_and_drains_only_after_completion(self):
        root, settings = self.roots[0], self.settings[0]
        home = remote.home_for(root)
        config = remote.overrides(root, home, settings)
        remote.prepare_home(root, home, self.source)
        self.server.release.clear()
        try:
            client = remote.start(root, home, self.source, config)
        except Exception:
            Path('/tmp/r66-daemon-failure.log').write_text((home / 'colony-daemon.log').read_text())
            record = remote.read_json(home / 'colony-daemon.json')
            if remote.alive(home):
                import signal
                os.kill(record['pid'], signal.SIGHUP)
            raise
        try:
            remote.trust_hooks(client, root, config)
            tid = remote.attach(client, root, config, None)
            client.call('turn/start', {'threadId': tid, 'input': [dict(type='text', text='Work from app', text_elements=[])]})
            self.assertTrue(self.server.entered.wait(10))
            self.assertFalse(remote.drain(home, timeout=1))
            self.assertTrue(remote.alive(home))
            self.assertEqual(sum(s['running'] for s in remote.statuses()), 1)
            self.server.release.set()
            deadline = time.monotonic() + 30
            while client.receive(deadline).get('method') != 'turn/completed':
                pass
            self.assertTrue(remote.drain(home))
            self.assertFalse(remote.alive(home))
            with self.native(root, dict(settings, remote=False)) as (local, config, _):
                self.assertEqual(remote.attach(local, root, config, tid), tid)
                self.turn(local, tid, 'Continue locally after remote off')
                self.assertIn(b'Work from app', self.server.requests[-1])
        finally:
            self.server.release.set()
            client.close()
            remote.drain(home)

    def test_shared_auth_refresh_keeps_links_and_original_home_usable(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import ExitStack
        self.server.auth_lock, self.server.refreshes = threading.Lock(), 0
        def jwt(number):
            payload = {'email': 'fixture@example.invalid', 'exp': int(time.time()) + 86400,
                       'jti': str(number), 'https://api.openai.com/auth': {
                           'chatgpt_account_id': 'fixture-account', 'chatgpt_plan_type': 'plus'}}
            return 'e30.' + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=') + '.fixture'
        self.server.jwt = jwt
        auth = dict(auth_mode='chatgpt', tokens=dict(id_token=jwt(0), access_token=jwt(0),
                    refresh_token='fixture-refresh-0', account_id='fixture-account'),
                    last_refresh=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        (self.source / 'auth.json').write_text(json.dumps(auth))
        config_path = self.source / 'config.toml'
        config_path.write_text(config_path.read_text().replace('requires_openai_auth=false', 'requires_openai_auth=true'))
        endpoint = f'http://127.0.0.1:{self.server.server_port}/token'
        with patch.dict(os.environ, CODEX_REFRESH_TOKEN_URL_OVERRIDE=endpoint), ExitStack() as stack:
            clients = []
            for root, settings in zip(self.roots, self.settings):
                client, _, home = stack.enter_context(self.native(root, settings))
                clients.append(client)
                self.assertTrue((home / 'auth.json').is_symlink())
            original = stack.enter_context(Client(command=['codex', 'app-server', '--stdio'],
                                                    env=dict(os.environ, CODEX_HOME=str(self.source)), cwd=self.work))
            clients.append(original)
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(lambda c: c.call('getAuthStatus', {'refreshToken': True, 'includeToken': False}), clients))
            for client in clients:
                self.assertEqual(client.call('getAuthStatus', {'refreshToken': True, 'includeToken': False})['authMethod'], 'chatgpt')
            for root in self.roots:
                self.assertTrue((remote.home_for(root) / 'auth.json').is_symlink(), 'native refresh writes through the link')
            saved = json.loads((self.source / 'auth.json').read_text())
            self.assertEqual(saved['tokens']['refresh_token'], 'fixture-refresh-' + str(self.server.refreshes))
            self.assertGreater(self.server.refreshes, 0)


if __name__ == '__main__':
    unittest.main()


class ConsoleConfigTest(unittest.TestCase):
    def test_remote_version_gate_allows_only_checked_releases(self):
        self.assertTrue(remote.supported_version('codex-cli 0.160.0'))
        self.assertTrue(remote.supported_version('codex-cli 0.159.3'))
        self.assertFalse(remote.supported_version('codex-cli 0.160.1'))
        self.assertFalse(remote.supported_version('codex-cli 0.159.30'))

    def test_the_attached_console_is_not_given_workspace_roots_the_server_already_has(self):
        from colony import codex_remote
        config = {"model": "gpt-6-astra", "sandbox_workspace_write.writable_roots": ["/x"], "hooks.Stop": [],
                  "sandbox_mode": "danger-full-access", "approval_policy": "never"}
        self.assertEqual(sorted(codex_remote.console_config(config)), ["hooks.Stop", "model"])


class PairingTest(unittest.TestCase):
    def test_first_connection_offers_once_through_claude_monitor_without_creating_code(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, COLONY_BOARD_HOME=folder), patch.object(remote, 'Client') as client:
            board.save_registry({'roots': [], 'settings': {'provider': 'claude'}})
            root = Path(folder) / 'codex-project'
            remote.offer_pairing(root)
            remote.offer_pairing(root)
            queued = (board.home() / 'to_monitor.jsonl').read_text().splitlines()
            self.assertEqual(len(queued), 1)
            self.assertIn('even if you run through Claude', json.loads(queued[0])['text'])
            self.assertEqual(remote.pairing_choice(root), 'offered')
            remote.pairing_choice(root, 'deferred')
            remote.offer_pairing(root)
            self.assertEqual((board.home() / 'to_monitor.jsonl').read_text().splitlines(), queued)
            self.assertEqual(remote.pairing_choice(root), 'deferred')
            client.assert_not_called()

    def test_pair_and_check_use_existing_project_socket_and_only_return_manual_code(self):
        with patch.object(remote, 'home_for', return_value=Path('/owned/project')), \
                patch.object(remote, 'alive', return_value=True), patch.object(remote, 'Client') as client:
            rpc = client.return_value.__enter__.return_value
            expiry = int(time.time()) + 180
            rpc.call.return_value = dict(manualPairingCode='TEST-CODE', pairingCode='opaque-secret',
                                         expiresAt=expiry, environmentId='not-for-browser')
            self.assertEqual(remote.pair('/project'), dict(manualPairingCode='TEST-CODE', expiresAt=expiry))
            client.assert_called_with(remote.socket_for('/owned/project'), timeout=40)
            rpc.call.assert_called_once_with('remoteControl/pairing/start', {'manualCode': True})
            rpc.call.reset_mock()
            rpc.call.return_value = {'claimed': True}
            self.assertEqual(remote.pair('/project', 'TEST-CODE'), {'claimed': True})
            rpc.call.assert_called_once_with('remoteControl/pairing/status', {'manualPairingCode': 'TEST-CODE'})

    def test_unstarted_host_and_bad_results_do_not_create_a_daemon_or_leak_native_errors(self):
        with patch.object(remote, 'alive', return_value=False), patch.object(remote, 'Client') as client:
            with self.assertRaisesRegex(remote.RemoteError, 'Open this project'):
                remote.pair('/project')
            client.assert_not_called()
        with patch.object(remote, 'alive', return_value=True), patch.object(remote, 'Client') as client:
            rpc = client.return_value.__enter__.return_value
            for result in ({}, {'manualPairingCode': 'OLD', 'expiresAt': 1}):
                rpc.call.return_value = result
                with self.assertRaisesRegex(remote.RemoteError, 'usable pairing code'):
                    remote.pair('/project')
            rpc.call.side_effect = remote.RPCError('upstream body: private-code')
            with self.assertRaisesRegex(remote.RemoteError, '^Could not reach Codex pairing') as caught:
                remote.pair('/project')
            self.assertNotIn('private-code', str(caught.exception))
