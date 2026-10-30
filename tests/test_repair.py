"""Installation repair lifecycle: private homes and mocked native RPC only."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from colony import board, codex_remote as remote, providers, repair


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        env = patch.dict(os.environ, {'COLONY_BOARD_HOME': str(self.base / 'board')})
        env.start()
        self.addCleanup(env.stop)
        board.save_registry({'roots': [], 'settings': {'providers': ['codex'], 'repair_provider': ''}})
        repair._last = 0
        repair._worker = None
        self.codex = providers.get('codex')
        installed = patch.object(providers, 'installed', return_value=True)
        installed.start()
        self.addCleanup(installed.stop)
        self.addCleanup(self.join)

    def join(self):
        if repair._worker:
            repair._worker.join(5)

    def choose(self, key='codex'):
        reg = board.registry()
        reg['settings']['repair_provider'] = key
        board.save_registry(reg)

    def test_off_and_cached_reads_never_contact_native(self):
        with patch.object(self.codex, 'repair_start') as start, patch.object(self.codex, 'repair_status') as status:
            self.assertEqual(repair.state()['state'], 'off')
            repair.tick()
            self.assertIsNone(repair._worker)
            self.choose()
            repair._record('codex', {'state': 'ready', 'paired': True, 'server': 'secret'})
            self.assertEqual(repair.state()['state'], 'ready')
            self.assertEqual(repair.state()['server'], 'secret')
            start.assert_not_called()
            status.assert_not_called()

    def test_explicit_enable_deduplicates_and_bounds_retries(self):
        entered, release = threading.Event(), threading.Event()
        def start(*args):
            entered.set()
            release.wait(3)
            return {'state': 'ready', 'paired': True}
        with patch.object(self.codex, 'repair_start', side_effect=start) as native:
            repair.enable('codex')
            self.assertTrue(entered.wait(1))
            repair.tick(force=True)
            release.set()
            self.join()
            repair.tick()
            native.assert_called_once()
        self.assertEqual(repair.state()['state'], 'ready')
        self.assertEqual(board.registry()['settings']['repair_provider'], 'codex')
        self.assertEqual(board.registry()['roots'], [])

    def test_two_process_maintenance_lock_prevents_duplicate_start(self):
        self.choose()
        folder = repair._cache().parent
        folder.mkdir(parents=True)
        import fcntl
        with (folder / 'host.lock').open('a') as lock, patch.object(self.codex, 'repair_start') as start:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            repair._refresh(force=True)
            start.assert_not_called()

    def test_failure_is_safe_and_retried_after_interval(self):
        self.choose()
        with patch.object(self.codex, 'repair_start', side_effect=RuntimeError('TOKEN private log')) as start:
            repair._refresh(force=True)
            self.assertEqual(repair.state()['state'], 'offline')
            self.assertNotIn('TOKEN', repair._cache().read_text())
            repair._refresh()
            self.assertEqual(start.call_count, 1)
            repair._refresh(force=True)
            self.assertEqual(start.call_count, 2)

    def test_disable_preserves_busy_work_and_keeps_its_pid(self):
        self.choose('')
        with patch.object(self.codex, 'repair_keeps', return_value={4321}), \
                patch.object(self.codex, 'repair_stop', return_value=False) as stop, \
                patch.object(self.codex, 'repair_start') as start:
            repair._refresh(force=True)
            self.assertEqual(repair.state()['state'], 'stopping')
            self.assertEqual(repair.keeps(), {4321})
            stop.assert_called_once()
            start.assert_not_called()
        with patch.object(self.codex, 'repair_keeps', return_value=set()):
            repair._refresh(force=True)
            self.assertEqual(repair.state()['state'], 'off')

    def test_pair_code_never_persisted_and_claim_does_not_mean_ready(self):
        self.choose()
        result = {'manualPairingCode': 'SECRET-CODE', 'expiresAt': int(time.time()) + 60, 'token': 'hidden'}
        with patch.object(self.codex, 'repair_pair', return_value=result), patch.object(repair, 'tick'):
            self.assertEqual(repair.pair(), {k: result[k] for k in ('manualPairingCode', 'expiresAt')})
        with patch.object(self.codex, 'repair_pair', return_value={'claimed': True}), patch.object(repair, 'tick') as tick:
            self.assertEqual(repair.pair('SECRET-CODE'), {'claimed': True})
            tick.assert_called_once_with(force=True)
        self.assertFalse(repair._cache().exists())
        self.assertFalse(repair.state()['paired'])

    def test_native_pair_rejects_invalid_codes_and_expiries(self):
        client = MagicMock()
        client.__enter__.return_value = client
        future = int(time.time()) + 60
        invalid = [('with spaces', future), ('https://private', future), ('A' * 129, future),
                   ('CODE', True), ('CODE', float(future)), ('CODE', int(time.time()) - 1)]
        with patch.object(remote, 'alive', return_value=True), patch('colony.codex_rpc.Client', return_value=client):
            for code, expiry in invalid:
                with self.subTest(code=code[:20], expiry=expiry):
                    client.call.return_value = {'manualPairingCode': code, 'expiresAt': expiry}
                    with self.assertRaises(RuntimeError):
                        self.codex.repair_pair(self.base)
            client.call.return_value = {'manualPairingCode': 'Ab-12', 'expiresAt': future, 'secret': 'hidden'}
            self.assertEqual(self.codex.repair_pair(self.base), {'manualPairingCode': 'Ab-12', 'expiresAt': future})

    def test_live_status_requires_connected_and_native_clients(self):
        client = MagicMock()
        client.__enter__.return_value = client
        with patch.object(remote, 'alive', return_value=True), patch('colony.codex_rpc.Client', return_value=client):
            for status, clients, expected in [('connected', [], 'unpaired'), ('connected', [{}], 'ready'),
                                               ('connecting', [{}], 'connecting'), ('errored', [{}], 'offline'),
                                               ('disabled', [{}], 'offline')]:
                client.call.side_effect = [{'status': status, 'environmentId': 'PRIVATE'}, {'data': clients}]
                self.assertEqual(self.codex.repair_status(self.base)['state'], expected)
        self.assertNotIn('PRIVATE', repr(repair.state()))

    def test_host_preparation_shares_auth_and_removes_project_automation(self):
        source = self.base / 'source'
        source.mkdir()
        (source / 'auth.json').write_text('{"refresh_token":"private"}')
        (source / 'AGENTS.md').write_text('Engage project and start goals')
        (source / 'config.toml').write_text('notify=["colony","hook"]\ndeveloper_instructions="engage"\n'
                                         '[features]\ngoals=true\nhooks=true\n'
                                         '[hooks]\nSessionStart=[]\n')
        home = self.base / 'repair'
        client = MagicMock()
        client.__enter__.return_value = client
        client.call.return_value = {'status': 'connecting'}
        with patch.object(remote, 'alive', return_value=False), \
                patch.object(remote, 'supported_version', return_value=True), \
                patch.object(self.codex, 'version', return_value='codex 0.166.0'), \
                patch.object(self.codex, 'config_home', return_value=source), \
                patch.object(self.codex, 'repair_status', return_value={'state': 'unpaired'}), \
                patch.object(remote, 'start', return_value=client) as start, \
                patch('colony.codex_rpc.Client', return_value=client):
            self.codex.repair_start(home, {'permissions': 'plan'})
        self.assertTrue((home / 'auth.json').is_symlink())
        self.assertEqual((home / 'auth.json').resolve(), source / 'auth.json')
        self.assertFalse((home / 'AGENTS.md').exists())
        config = remote.user_config(home)
        self.assertNotIn('hooks', config)
        self.assertNotIn('notify', config)
        self.assertNotIn('goals', config['features'])
        passed = start.call_args.kwargs
        self.assertEqual(passed['root'], providers.SOURCE)
        self.assertEqual(passed['cwd'], providers.SOURCE)
        self.assertFalse(passed['project'])
        self.assertFalse(passed['config']['features.hooks'])
        self.assertFalse(passed['config']['features.goals'])
        self.assertEqual(passed['config']['project_doc_max_bytes'], 0)
        self.assertEqual(passed['config']['sandbox_mode'], 'read-only')
        self.assertFalse(any(call.args[0].startswith(('turn/', 'thread/')) for call in client.call.call_args_list))

    def test_detached_start_uses_source_cwd_without_project_environment(self):
        home = self.base / 'host'
        remote.socket_for(home).parent.mkdir(parents=True)
        remote.socket_for(home).touch()
        process = MagicMock(pid=998877)
        process.poll.return_value = None
        self.addCleanup(remote._processes.pop, process.pid, None)
        with patch('subprocess.Popen', return_value=process) as popen, \
                patch.object(remote, 'process_start', return_value='fixture-start'), \
                patch.object(remote, 'fingerprint', return_value='fixture'), \
                patch.object(remote, 'Client'), \
                patch.object(board, 'workdir', return_value=self.base / 'wrong'), \
                patch.dict(os.environ, {'COLONY_PROJECT': 'inherited', 'COLONY_CONSOLE': 'inherited',
                                        'COLONY_ROOT_SCOPE': 'inherited'}):
            remote.start(root=providers.SOURCE, home=home, source=self.base, config={},
                         cwd=providers.SOURCE, project=False)
        args = popen.call_args.kwargs
        self.assertEqual(args['cwd'], providers.SOURCE)
        self.assertTrue(args['start_new_session'])
        self.assertNotIn('COLONY_PROJECT', args['env'])
        self.assertNotIn('COLONY_CONSOLE', args['env'])
        self.assertNotIn('COLONY_ROOT_SCOPE', args['env'])
        self.assertEqual(args['env']['COLONY_BOARD_HOME'], str(board.home()))
        self.assertEqual(args['env']['COLONY_CODEX_SOURCE_HOME'], str(self.base))

    def test_live_host_is_preserved_without_preparing_or_restarting(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.call.return_value = {'status': 'connected'}
        with patch.object(remote, 'alive', return_value=True), \
                patch.object(remote, 'prepare_home') as prepare, patch.object(remote, 'start') as start, \
                patch.object(self.codex, 'repair_status', return_value={'state': 'ready', 'paired': True}), \
                patch('colony.codex_rpc.Client', return_value=client):
            self.codex.repair_start(self.base, {'permissions': 'all'})
            prepare.assert_not_called()
            start.assert_not_called()

    def test_graceful_stop_never_signals_busy_or_unreachable_host(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.call.side_effect = [{'data': ['thread']}, {'thread': {'status': {'type': 'active'}}}]
        with patch.object(remote, 'alive', return_value=True), \
                patch('colony.codex_rpc.Client', return_value=client), patch('os.kill') as kill:
            self.assertFalse(self.codex.repair_stop(self.base))
            kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
