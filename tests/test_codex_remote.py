"""Native Codex integration with a local Responses fixture: no account or model calls."""
from contextlib import contextmanager
import json
import base64
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
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


def own_home(test):
    """A colony home of the test's own, for the length of it."""
    folder = tempfile.TemporaryDirectory(prefix='colony-remote-home-')
    test.addCleanup(folder.cleanup)
    env = patch.dict(os.environ, COLONY_BOARD_HOME=folder.name)
    env.start()
    test.addCleanup(env.stop)
    return Path(folder.name)


def installed():
    # Which Codex versions passed here is the person's own record: read once, as the module loads, and never written.
    forbids = os.environ.pop('COLONY_TEST_FORBIDS', None)
    try:
        return shutil.which('codex') and remote.supported_version(subprocess.run(
            ['codex', '--version'], capture_output=True, text=True).stdout)
    finally:
        if forbids:
            os.environ['COLONY_TEST_FORBIDS'] = forbids


@unittest.skipUnless(installed(), 'native checks require a tested Codex: ' + ', '.join(remote.TESTED_VERSIONS))
class NativeRemoteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='colony-remote-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(('COLONY_', 'OPENAI_', 'CODEX_'))}
        env.update(HOME=str(self.base), CLAUDE_CONFIG_DIR=str(self.base / 'claude'),
                   XDG_CONFIG_HOME=str(self.base / 'config'), XDG_STATE_HOME=str(self.base / 'state'),
                   COLONY_BOARD_HOME=str(self.base / 'board'), CODEX_HOME=str(self.source), PYTHONPATH=str(REPO))
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

    @contextmanager
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

    def test_fresh_consoles_apply_permissions_and_remote_resume_keeps_them(self):
        from colony import providers
        policies = {'ask': ('workspace-write', 'on-request'), 'edits': ('workspace-write', 'never'),
                    'all': ('danger-full-access', 'never'), 'plan': ('read-only', 'on-request')}
        # A native console loads this too; its defaults must not replace the project's choice.
        path = self.source / 'config.toml'
        path.write_text('sandbox_mode="workspace-write"\napproval_policy="never"\n' + path.read_text())
        for mode in ('remote', 'local'):
            for permission, (sandbox, approval) in policies.items():
                with self.subTest(mode=mode, permission=permission):
                    root = self.base / (mode + '-' + permission)
                    root.mkdir()
                    home = remote.home_for(root)
                    settings = dict(self.settings[0], permissions=permission)
                    config = remote.overrides(root, home, settings)
                    config['features.hooks'] = False      # this checks native policy, not hook delivery
                    remote.prepare_home(root, home, self.source)
                    with (home / 'config.toml').open('a') as file:
                        file.write('\n[projects.' + json.dumps(str(root)) + ']\ntrust_level="trusted"\n')
                    client = None
                    try:
                        if mode == 'remote':
                            client = remote.start(root, home, self.source, config)
                            args = ['codex', '--remote', 'unix://' + str(remote.socket_for(home)), '--no-alt-screen',
                                    *remote.flags(remote.console_config(config))]
                        else:
                            args = shlex.split(providers.get('codex').local_command(root.name, settings, root=root))
                            args += ['--no-daemon', '-c', 'features.hooks=false']
                        rows = self.console_turn(args, root, home)
                        policy = next(row['payload'] for row in rows if row['type'] == 'turn_context')
                        self.assertEqual(policy['sandbox_policy']['type'], sandbox)
                        self.assertEqual(policy['approval_policy'], approval)
                        if client:
                            tid = next(row['payload']['id'] for row in rows if row['type'] == 'session_meta')
                            self.assertEqual(remote.attach(client, root, config, tid), tid)
                            args = ['codex', '--remote', 'unix://' + str(remote.socket_for(home)), '--no-alt-screen',
                                    *remote.flags(remote.console_config(config, resume=True)), 'resume', tid]
                            rows = self.console_turn(args, root, home, completed=2)
                            policy = [row['payload'] for row in rows if row['type'] == 'turn_context'][-1]
                            self.assertEqual(policy['sandbox_policy']['type'], sandbox)
                            self.assertEqual(policy['approval_policy'], approval)
                    finally:
                        if client:
                            client.close()
                        remote.stop_host(root)

    def console_turn(self, args, root, home, completed=1):
        """Exercise the native console's thread request; direct RPC misses its permission defaults."""
        import fcntl
        import struct
        import termios
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 160, 0, 0))
        process = None
        output = b''
        try:
            process = subprocess.Popen([*args, 'Say the probe passed.'], cwd=root,
                        env=dict(remote.environment(root, home, self.source), TERM='xterm-256color'),
                        stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            slave = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and process.poll() is None:
                if select.select([master], [], [], .1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    output += chunk
                    if b'\x1b[6n' in chunk:
                        os.write(master, b'\x1b[1;1R')       # terminal cursor query
                for path in home.glob('sessions/**/*.jsonl'):
                    try:
                        rows = [json.loads(line) for line in path.read_text().splitlines()]
                    except ValueError:
                        continue                           # native writer has not finished this line
                    if sum(row.get('payload', {}).get('type') == 'task_complete' for row in rows) >= completed:
                        return rows
            self.fail('Native console did not finish: ' + output.decode(errors='replace')[-3000:])
        finally:
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_two_projects_same_folder_keep_settings_hooks_and_cold_resume(self):
        ids = []
        for root, settings in zip(self.roots, self.settings):
            board.add_note(root, None, 'Private note for ' + root.name, author='person')
            with self.native(root, settings) as (client, config, home):
                tid = remote.attach(client, root, config, None)
                ids.append(tid)
                board.mark_needs_you(root)              # its turn asks the person (colony needs-you)
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
        # Codex 0.165 attaches one conversation to another: attached outside the family, it stays local
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(original / 'state_5.sqlite')) as db:
            attachable = 'attached_thread_parent_id' in [r[1] for r in db.execute('PRAGMA table_info(threads)')]
            if attachable:
                with db:
                    db.execute('UPDATE threads SET attached_thread_parent_id=? WHERE id=?', (tid, unrelated))
                with self.assertRaisesRegex(TransferError, 'Attached'):
                    transfer(original, self.base / 'attached', tid)
                with db:
                    db.execute('UPDATE threads SET attached_thread_parent_id=NULL WHERE id=?', (unrelated,))
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
    def test_remote_flags_keep_each_permission_for_fresh_consoles_only(self):
        import tomllib
        policies = {'ask': ('workspace-write', 'on-request'), 'edits': ('workspace-write', 'never'),
                    'all': ('danger-full-access', 'never'), 'plan': ('read-only', 'on-request')}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, COLONY_BOARD_HOME=folder), \
                patch.object(bench, 'effective', return_value={}):
            root = Path(folder) / 'project'
            root.mkdir()
            board.save_registry({'roots': [], 'settings': {'messaging': False}})
            for permission, (sandbox, approval) in policies.items():
                with self.subTest(permission=permission):
                    config = remote.overrides(root, remote.home_for(root), {'permissions': permission})
                    for values in (config, remote.console_config(config)):
                        args = remote.flags(values)
                        parsed = tomllib.loads('\n'.join(args[1::2]))
                        self.assertEqual(parsed['sandbox_mode'], sandbox)
                        self.assertEqual(parsed['approval_policy'], approval)
                    resumed = remote.console_config(config, resume=True)
                    self.assertNotIn('sandbox_mode', resumed)
                    self.assertNotIn('approval_policy', resumed)
                    self.assertFalse(any(k.startswith('sandbox_workspace_write.') for k in resumed))

    def test_remote_version_gate_allows_only_checked_releases(self):
        own_home(self)
        self.assertTrue(remote.supported_version('codex-cli 0.160.0'))
        self.assertTrue(remote.supported_version('codex-cli 0.159.3'))
        self.assertTrue(remote.supported_version('codex-cli 0.163.0'))
        self.assertFalse(remote.supported_version('codex-cli 0.160.1'))
        self.assertFalse(remote.supported_version('codex-cli 0.159.30'))

    def test_the_attached_console_is_not_given_workspace_roots_the_server_already_has(self):
        from colony import codex_remote
        config = {"model": "gpt-6-astra", "sandbox_workspace_write.writable_roots": ["/x"], "hooks.Stop": [],
                  "sandbox_mode": "danger-full-access", "approval_policy": "never"}
        self.assertEqual(sorted(codex_remote.console_config(config)),
                         ["approval_policy", "hooks.Stop", "model", "sandbox_mode"])
        self.assertEqual(sorted(codex_remote.console_config(config, resume=True)), ["hooks.Stop", "model"])

    def test_a_new_codex_is_checked_once_with_no_tokens_and_admitted_only_if_it_passes(self):
        from colony import providers
        codex = providers.get('codex')
        runs = []

        class Run:
            def __init__(self, cmd, **kw):
                runs.append(cmd[-1])
                self.pid, self.returncode = os.getpid(), 0 if cmd[-1] == '9.9.1' else 1

            def communicate(self, timeout=None):
                return '', 'FAILED (errors=1)'
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, COLONY_BOARD_HOME=folder), \
                patch.object(remote.subprocess, 'Popen', Run):
            self.assertFalse(remote.supported_version('codex-cli 9.9.1'))
            self.assertTrue(remote.check_version('codex-cli 9.9.1')['passed'])
            self.assertTrue(remote.supported_version('codex-cli 9.9.1'), 'passed: admitted for app access')
            self.assertIsNone(remote.check_version('codex-cli 9.9.1'), 'once per version')
            self.assertFalse(remote.check_version('codex-cli 9.9.2')['passed'])
            self.assertFalse(remote.supported_version('codex-cli 9.9.2'), 'failed: stays local')
            self.assertIsNone(remote.check_version('codex-cli 9.9.2'), 'a failure is not rerun every day')
            self.assertIsNone(remote.check_version('codex-cli 0.163.0'), 'a version checked by hand needs nothing')
            self.assertEqual(runs, ['9.9.1', '9.9.2'])
            with patch.object(codex, 'version', return_value='codex-cli 9.9.2'):
                self.assertIn('failed colony', remote.remote_problem(Path(folder) / 'p', {'remote': True}))

    def test_the_doctor_hears_when_a_project_set_for_app_access_cant_have_it(self):
        from colony import providers
        codex = providers.get('codex')
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, COLONY_BOARD_HOME=folder):
            root = Path(folder) / 'codex-project'
            with patch.object(codex, 'version', return_value='codex-cli 9.9.9'):
                self.assertIn('not yet checked', remote.remote_problem(root, {'remote': True}))
                self.assertIsNone(remote.remote_problem(root, {'remote': False}), 'app access off: nothing to miss')
            with patch.object(codex, 'version', return_value='codex-cli ' + remote.TESTED_VERSIONS[-1]):
                self.assertIsNone(remote.remote_problem(root, {'remote': True}))
                remote.report(remote.home_for(root), root, 'local', remote.UNTESTED + '0.159.3')
                self.assertIsNone(remote.remote_problem(root, {'remote': True}), 'its console is closed: moot')
                with patch.object(console, 'running', return_value=True):
                    self.assertIn('runs local', remote.remote_problem(root, {'remote': True}),
                                  'a console started on an untested Codex stays local until its next start')
                remote.report(remote.home_for(root), root, 'pending', 'enrollment unavailable')
                with patch.object(console, 'running', return_value=True), patch.object(remote, 'alive', return_value=False):
                    self.assertIn('runs local', remote.remote_problem(root, {'remote': True}),
                                  'an older pending record whose daemon drained must show the local fallback')
                with patch.object(console, 'running', return_value=True), patch.object(remote, 'alive', return_value=True):
                    self.assertIsNone(remote.remote_problem(root, {'remote': True}),
                                      'a pending live host may still own an active turn')
                remote.report(remote.home_for(root), root, 'local', remote.UNTESTED + '0.159.3')
                board.save_registry({'roots': [], 'settings': {}})
                self.assertIn('available again', codex.stale(root), 'so the watcher reloads it at its next idle moment')
            with patch.object(codex, 'version', return_value='codex-cli 9.9.9'):
                self.assertIsNone(codex.stale(root), 'still untested: a reload would come back local')


    def test_enrollment_fallback_reports_local_after_successful_drain(self):
        from colony import providers
        codex = providers.get('codex')
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, COLONY_BOARD_HOME=folder), \
                patch.object(codex, 'version', return_value='codex-cli ' + remote.TESTED_VERSIONS[-1]), \
                patch.object(codex, 'config_home', return_value=Path(folder) / 'source'), \
                patch.object(remote, 'overrides', side_effect=remote.RemoteError('enrollment unavailable')), \
                patch.object(remote, 'alive', return_value=True), \
                patch.object(remote, 'drain', return_value=True) as drain, \
                patch.object(remote, 'local', return_value=0) as local, patch('builtins.print'):
            root = Path(folder) / 'project'
            self.assertEqual(remote.launch(root, {'remote': True}, resume='existing-thread'), 0)
            status = remote.read_json(remote.home_for(root) / 'colony-status.json')
            self.assertEqual(status['mode'], 'local')
            self.assertEqual(status['reason'], 'enrollment unavailable')
            self.assertEqual(status['thread'], 'existing-thread')
            drain.assert_called_once_with(remote.home_for(root))
            local.assert_called_once()


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
            self.assertIn('colony pair codex-project', json.loads(queued[0])['text'], 'the monitor relays a fresh code on yes')
            self.assertIn('never in a note', json.loads(queued[0])['text'])
            self.assertEqual(remote.pairing_choice(root), 'offered')
            remote.pairing_choice(root, 'deferred')
            remote.offer_pairing(root)
            self.assertEqual((board.home() / 'to_monitor.jsonl').read_text().splitlines(), queued)
            self.assertEqual(remote.pairing_choice(root), 'deferred')
            client.assert_not_called()

    def test_without_the_monitor_the_projects_own_agent_asks(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, COLONY_BOARD_HOME=folder):
            board.save_registry({'roots': [], 'settings': {'provider': 'codex', 'monitor': False}})
            root = Path(folder) / 'codex-project'
            (root / '.board').mkdir(parents=True)
            remote.offer_pairing(root)
            self.assertFalse((board.home() / 'to_monitor.jsonl').exists())
            [note] = board.notes(root)
            self.assertEqual(note['author'], 'colony')
            self.assertIn('colony pair codex-project', note['text'])

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
        own_home(self)
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


class StewardRemoteTest(unittest.TestCase):
    def setUp(self):
        from colony import monitor, providers
        self.monitor, self.codex = monitor, providers.get('codex')
        self.tmp = tempfile.TemporaryDirectory(prefix='colony-steward-remote-test-')
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.source = base / 'source'
        self.source.mkdir()
        self.source.joinpath('auth.json').write_text('{}')
        env = patch.dict(os.environ, HOME=str(base), COLONY_BOARD_HOME=str(base / 'board'),
                         CLAUDE_CONFIG_DIR=str(base / 'claude'), CODEX_HOME=str(self.source),
                         COLONY_CODEX_SOURCE_HOME=str(self.source))
        env.start()
        self.addCleanup(env.stop)
        board.save_registry({'roots': [], 'settings': {'provider': 'codex', 'remote': True,
                                                       'steward_name': 'Juniper', 'messaging': False}})
        self.root = monitor.home()
        (self.root / '.board').mkdir(parents=True)
        self.project = base / 'project'
        (self.project / '.board').mkdir(parents=True)
        self.project_record = self.project / '.board' / 'settings.json'
        self.project_record.write_text('{"provider":"codex"}')
        self.project_home = remote.home_for(self.project)
        remote.atomic_json(self.project_home / 'colony-thread.json', {'thread': 'project-thread'})
        self.settings = dict(model='fixture', effort='high', permissions='all', remote=True)

    def test_launch_passes_permissions_only_to_a_fresh_remote_console(self):
        class Exec(BaseException):
            pass
        with patch.object(self.codex, 'version', return_value='codex-cli ' + remote.TESTED_VERSIONS[-1]), \
                patch.object(bench, 'effective', return_value={}), patch.object(remote, 'alive', return_value=False), \
                patch.object(remote, 'trust_hooks'), patch.object(remote, 'transfer'), \
                patch.object(remote, 'offer_pairing'), patch.object(remote, 'start') as start, \
                patch.object(remote, 'Client') as client, patch.object(remote.os, 'execvpe', side_effect=Exec) as execute:
            rpc = start.return_value.__enter__.return_value
            rpc.call.return_value = dict(model='fixture', reasoningEffort='high', approvalPolicy='never',
                                         sandbox={'type': 'dangerFullAccess'}, thread={'id': 'saved-thread'})
            client.return_value.__enter__.return_value.call.return_value = {'status': 'connected'}
            for resume in (None, 'saved-thread'):
                with self.subTest(resume=resume), self.assertRaises(Exec):
                    remote.launch(self.root, self.settings, resume)
                args = execute.call_args.args[1]
                self.assertEqual('sandbox_mode="danger-full-access"' in args, resume is None)
                self.assertEqual('approval_policy="never"' in args, resume is None)
                self.assertFalse(any(arg.startswith('sandbox_workspace_write.') for arg in args))
                if resume:
                    self.assertEqual(args[-2:], ['resume', resume])
                    rpc.call.assert_any_call('thread/resume', dict(cwd=str(self.root), model='fixture',
                        approvalPolicy='never', sandbox='danger-full-access',
                        config={'model_reasoning_effort': 'high'}, threadId=resume, excludeTurns=True))

    def test_console_launch_uses_steward_folder_and_resumes_its_conversation(self):
        import shlex
        from colony import context, selection, providers
        transcript = self.source / 'rollout.jsonl'
        transcript.write_text('{"type":"session_meta"}\n')
        remote.atomic_json(context.file(self.root, 'context-session.json'),
                           dict(provider='codex', id='steward-thread', path=str(transcript)))
        with patch.object(console, 'COMMAND', None), patch.object(selection, 'migrate'), \
                patch.object(self.monitor, 'choice', return_value=('fixture', 'high', None)), \
                patch.object(providers, 'installed', return_value=True), \
                patch.object(console, 'running', return_value=False), \
                patch.object(console, 'contained', side_effect=lambda cmd, name: cmd), \
                patch.object(console, 'fingerprint', return_value='fingerprint'), \
                patch.object(console, 'scrollable'), patch.object(console.subprocess, 'run') as run:
            console.ensure(self.root, self.monitor.name(), 'monitor')
            cmd = run.call_args_list[0].args[0]
            self.assertEqual(cmd[cmd.index('-c') + 1], str(self.root))
            launch = shlex.split(cmd[cmd.index('-e') + 4].split('\n')[0])
            self.assertIn('colony.codex_remote', launch)
            self.assertEqual(launch[launch.index('--project') + 1], str(self.root))
            self.assertEqual(launch[launch.index('--resume') + 1], 'steward-thread')
            self.assertTrue(json.loads(launch[launch.index('--settings') + 1])['remote'])
            run.reset_mock()
            board.set_setting('provider', 'claude')
            with patch.object(providers.get('claude'), 'token', return_value=None):
                command = console.command('monitor', folder=self.root)
            self.assertNotIn('colony.codex_remote', command)
            self.assertIn('--remote-control Juniper', command)

    def test_host_launch_and_resume_use_only_the_stewards_home_and_seat(self):
        from unittest.mock import Mock
        from colony import context
        class Exec(BaseException):
            pass
        home = remote.home_for(self.root)
        self.assertEqual(home, board.home() / 'codex-remote' / 'steward')
        self.assertNotEqual(home, self.project_home)
        socket = remote.socket_for(home)
        socket.parent.mkdir(parents=True)
        socket.touch()
        transcript = home / 'rollout.jsonl'
        transcript.write_text('{"type":"session_meta"}\n')
        process = Mock(pid=987654321)
        process.poll.return_value = None
        self.addCleanup(remote._processes.pop, process.pid, None)
        def reply(method, params=None):
            if method == 'remoteControl/status/read':
                return dict(status='connected', serverName='fixture host')
            if method == 'thread/resume':
                return dict(model='fixture', reasoningEffort='high', approvalPolicy='never',
                            sandbox={'type': 'dangerFullAccess'},
                            thread={'id': 'steward-thread', 'path': str(transcript)})
            return {}
        with patch.object(self.codex, 'version', return_value='codex-cli ' + remote.TESTED_VERSIONS[-1]), \
                patch.object(bench, 'effective', return_value={}), \
                patch.object(remote, 'alive', return_value=False), \
                patch.object(remote, 'process_start', return_value='fixture-start'), \
                patch.object(remote, 'trust_hooks'), patch.object(remote, 'transfer') as transfer_thread, \
                patch.object(remote, 'Client') as client, \
                patch.object(remote.subprocess, 'Popen', return_value=process) as popen, \
                patch.object(remote.os, 'execvpe', side_effect=Exec) as execute:
            rpc = client.return_value.__enter__.return_value
            rpc.call.side_effect = reply
            with self.assertRaises(Exec):
                remote.launch(self.root, self.settings, 'steward-thread')
            transfer_thread.assert_called_once_with(self.source, home, 'steward-thread')
            args, kw = popen.call_args
            self.assertEqual(args[0][:2], ['codex', 'app-server'])
            self.assertIn('unix://' + str(socket), args[0])
            self.assertEqual(kw['cwd'], self.root)
            self.assertEqual(kw['env']['CODEX_HOME'], str(home))
            self.assertEqual(kw['env']['COLONY_PROJECT'], str(self.root))
            self.assertEqual(kw['env']['COLONY_CONSOLE'], self.monitor.name())
            self.assertEqual(execute.call_args.args[2]['COLONY_CONSOLE'], self.monitor.name())
            self.assertIn('steward-thread', execute.call_args.args[1])
            rpc.call.assert_any_call('thread/resume', dict(cwd=str(self.root), model='fixture',
                approvalPolicy='never', sandbox='danger-full-access', config={'model_reasoning_effort': 'high'},
                threadId='steward-thread', excludeTurns=True))
            rpc.call.assert_any_call('thread/name/set', {'threadId': 'steward-thread', 'name': 'Juniper'})
        self.assertEqual(context.session(self.root)['id'], 'steward-thread')
        self.assertEqual(remote.status(self.root)['mode'], 'connected')
        self.assertEqual(remote.read_json(self.project_home / 'colony-thread.json'), {'thread': 'project-thread'})
        self.assertEqual(self.project_record.read_text(), '{"provider":"codex"}')
        self.assertFalse((self.project_home / 'colony-daemon.json').exists())
        self.assertEqual(remote.pairing_choice(self.root), 'offered')
        self.assertIn('steward page', (board.home() / 'to_monitor.jsonl').read_text())

    def test_remote_off_never_starts_a_host_and_keeps_owned_history(self):
        home = remote.home_for(self.root)
        home.mkdir(parents=True)
        (home / 'colony-owned').touch()
        with patch.object(self.codex, 'version', return_value='codex-cli ' + remote.TESTED_VERSIONS[-1]), \
                patch.object(remote, 'alive', return_value=False), patch.object(remote, 'start') as start, \
                patch.object(remote, 'local', return_value=0) as local:
            self.assertEqual(remote.launch(self.root, dict(self.settings, remote=False), 'steward-thread'), 0)
            start.assert_not_called()
            local.assert_called_once_with(self.root, dict(self.settings, remote=False), 'steward-thread', home)
            self.assertEqual(remote.status(self.root)['mode'], 'off')

    def test_explicit_stop_ends_only_steward_host_and_claude_has_no_host(self):
        with patch.object(self.monitor.subprocess, 'run') as run, patch.object(remote, 'stop_host') as stop, \
                patch.object(console, 'running_program', return_value=None):
            self.monitor.stop()
            run.assert_called_once_with(['tmux', 'kill-session', '-t', self.monitor.name()], capture_output=True)
            stop.assert_called_once_with(self.root)
            stop.reset_mock()
            board.set_setting('provider', 'claude')
            self.monitor.stop()
            stop.assert_not_called()
        self.assertEqual(remote.read_json(self.project_home / 'colony-thread.json'), {'thread': 'project-thread'})

    def test_hooks_adopt_only_the_steward_conversation_and_name_it_for_the_app(self):
        with patch.dict(os.environ, COLONY_CONSOLE=self.monitor.name()), patch.object(remote, 'Client') as client:
            self.assertTrue(remote.accepts_hook(self.root, {'session_id': 'steward-thread'}))
            client.return_value.__enter__.return_value.call.assert_called_once_with(
                'thread/name/set', {'threadId': 'steward-thread', 'name': 'Juniper'})
            self.assertFalse(remote.accepts_hook(self.root, {'session_id': 'another-thread'}))
            self.assertFalse(remote.accepts_hook(self.root, {'session_id': 'helper', 'agent_id': 'helper'}))
            self.assertTrue(remote.accepts_hook(self.root, {'session_id': 'steward-thread'}))
        with patch.dict(os.environ, COLONY_CONSOLE=console.session_name(self.project)):
            self.assertFalse(remote.accepts_hook(self.root, {'session_id': 'steward-thread'}))

    def test_page_and_setup_name_the_pairing_place(self):
        with patch.object(board, 'MONITOR', None), patch.object(board, 'switch_line', return_value=''), \
                patch.object(board, 'shell', side_effect=lambda reg, pid, body, **kw: body), \
                patch.object(remote, 'alive', return_value=True), patch.object(remote, 'paired', return_value=False), \
                patch('colony.codex_rpc.Client') as client:
            client.return_value.__enter__.return_value.call.return_value = {'status': 'connected'}
            page = board.monitor_page(board.registry(), view='console')
            self.assertIn('In ChatGPT: not paired yet', page)
            self.assertIn('Connect to ChatGPT', page)
            self.assertIn("name='project' value='" + str(self.root) + "'", page)
            self.assertIn('Juniper', board.codex_pairing_options(board.registry()))
            self.assertIn('Connect to ChatGPT', self.monitor.ROLE)
            board.set_setting('provider', 'claude')
            self.assertNotIn('Connect to ChatGPT', board.monitor_page(board.registry(), view='console'))

    def test_cleanup_keeps_and_stops_steward_without_touching_project_host(self):
        home = remote.home_for(self.root)
        for root, folder, pid in ((self.root, home, 11001), (self.project, self.project_home, 11002)):
            remote.atomic_json(folder / 'colony-daemon.json', {'project': str(root), 'pid': pid, 'start': 'fixture'})
        live = {home, self.project_home}
        def kill(pid, sig):
            self.assertEqual(pid, 11001)
            live.discard(home)
        with patch.object(remote, 'alive', side_effect=lambda folder: folder in live), \
                patch.object(remote.os, 'kill', side_effect=kill) as signal_host, \
                patch.object(self.monitor.subprocess, 'run'), patch.object(console, 'running_program', return_value='codex'):
            self.assertEqual(remote.hosts({str(self.root), str(self.project)}), {11001, 11002})
            self.assertEqual(remote.let_go({str(self.root), str(self.project)}), [])
            self.monitor.stop()
            self.assertEqual(signal_host.call_count, 1)
            self.assertEqual(live, {self.project_home})
            self.assertEqual(remote.hosts({str(self.root), str(self.project)}), {11002})

    def test_turning_remote_off_drains_only_the_steward_host(self):
        home = remote.home_for(self.root)
        with patch.object(self.codex, 'version', return_value='codex-cli ' + remote.TESTED_VERSIONS[-1]), \
                patch.object(remote, 'alive', return_value=True), patch.object(remote, 'start') as start, \
                patch.object(remote, 'drain', return_value=True) as drain, patch.object(remote, 'local', return_value=0):
            self.assertEqual(remote.launch(self.root, dict(self.settings, remote=False), 'steward-thread'), 0)
            drain.assert_called_once_with(home)
            start.assert_not_called()

    def test_steward_pairing_routes_use_its_existing_host_and_reject_other_folders(self):
        import urllib.request
        import urllib.error
        import urllib.parse
        server = ThreadingHTTPServer(('127.0.0.1', 0), board.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_address[1]}'
        def post(path, root=None, origin=None, **values):
            data = urllib.parse.urlencode(dict(project=str(root or self.root), **values)).encode()
            return urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers={'Origin': origin or base}))
        with patch.object(remote, 'alive', return_value=True), patch.object(remote, 'Client') as client:
            rpc = client.return_value.__enter__.return_value
            rpc.call.return_value = dict(manualPairingCode='STEWARD-CODE', expiresAt=int(time.time()) + 180)
            with post('/codex-pair') as response:
                page = response.read().decode()
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertIn('Pair Juniper with ChatGPT', page)
                self.assertIn('STEWARD-CODE', page)
                self.assertIn('Add manually', page)
                self.assertIn("href='/steward'", page)
            client.assert_called_once_with(remote.socket_for(remote.home_for(self.root)), timeout=40)
            rpc.call.return_value = {'claimed': True}
            with post('/codex-pair/status', code='STEWARD-CODE') as response:
                self.assertEqual(json.load(response), {'claimed': True})
            self.assertEqual(remote.pairing_choice(self.root), 'paired')
            self.assertIsNone(remote.pairing_choice(self.project))
            self.assertNotIn('STEWARD-CODE', (remote.home_for(self.root) / 'colony-pairing.json').read_text())
            client.reset_mock()
            for values, expected in (({'root': self.project}, 404), ({'origin': 'https://elsewhere.example'}, 403)):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    post('/codex-pair', **values)
                self.assertEqual(caught.exception.code, expected)
            board.set_setting('provider', 'claude')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                post('/codex-pair')
            self.assertEqual(caught.exception.code, 404)
            client.assert_not_called()

    def test_stop_uses_running_provider_while_a_switch_is_pending(self):
        board.set_setting('provider', 'claude')
        with patch.object(self.monitor.subprocess, 'run'), patch.object(remote, 'stop_host') as stop, \
                patch.object(console, 'running_program', return_value='codex'):
            self.monitor.stop()
            stop.assert_called_once_with(self.root)

    def test_first_session_hook_keeps_steward_bookmark_across_provider_switch(self):
        from types import SimpleNamespace
        from colony import cli, context, providers, selection
        paths = {'codex': self.source / 'codex.jsonl', 'claude': self.source / 'claude.jsonl'}
        paths['codex'].write_text('{"type":"session_meta"}\n')
        paths['claude'].write_text('{"type":"user","message":{"content":"Hello"}}\n')
        with patch.dict(os.environ, COLONY_CONSOLE=self.monitor.name(), COLONY_PROJECT=str(self.root)), \
                patch.object(remote, 'Client'), patch.object(providers, 'discover'), patch('builtins.print'), \
                patch.object(Path, 'cwd', return_value=self.root):
            for family in ('codex', 'claude'):
                board.set_setting('provider', family)
                payload = dict(session_id=family + '-steward', transcript_path=str(paths[family]),
                               cwd=str(self.root), hook_event_name='SessionStart')
                with patch.object(cli, '_hook_input', return_value=payload):
                    cli.cmd_notes(SimpleNamespace(deliver=True, session=True, console=family))
                self.assertEqual(console.saved_conversation(self.root, family)['conversation'], family + '-steward')
        board.set_setting('provider', 'codex')
        self.assertEqual(context.session(self.root)['id'], 'codex-steward')
        with patch.object(console, 'COMMAND', None), patch.object(selection, 'migrate'), \
                patch.object(self.monitor, 'choice', return_value=('fixture', 'high', None)):
            self.assertIn('--resume codex-steward', console.command('monitor', folder=self.root))
