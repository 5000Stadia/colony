"""Polling coalesces slow work without holding up messages or leaking another board's health."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from colony import board, console, monitor, signin

ROOT = Path(__file__).resolve().parents[1]


class PollingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, COLONY_BOARD_HOME=str(self.folder / 'board')))
        board.save_registry({'roots': [], 'settings': {'active_hours': 'off', 'messaging': False}})
        self.root = self.folder / 'project'
        self.root.mkdir()
        self.reg = board.registry()
        self.clock = [10.0]
        self.stack.enter_context(patch.object(board.time, 'monotonic', side_effect=lambda: self.clock[0]))
        self.snap = {'state': 'idle', 'lines': []}
        self.snapshot = self.stack.enter_context(patch.object(console, 'snapshot', side_effect=lambda p: self.snap))
        self.moments = self.stack.enter_context(patch.object(board, 'moments', return_value=[]))
        for target, name, value in ((board, 'name_of', 'Fixture'), (board, 'at_work', 'R1 sample'),
                                    (board, 'nav_order', []), (monitor, 'involved', True),
                                    (monitor, 'snapshot', {'state': 'off', 'lines': []}),
                                    (signin, 'states', {'claude': {'state': 'reconnect'}})):
            self.stack.enter_context(patch.object(target, name, return_value=value))
        board._STATUS.clear()
        self.addCleanup(board._STATUS.clear)

    def payload(self, reg=None, roots=None):
        return json.loads(board.status_payload(self.reg if reg is None else reg, [self.root] if roots is None else roots))

    def test_one_snapshot_supplies_both_status_and_waiting_count(self):
        data = self.payload()
        self.snapshot.assert_called_once_with(self.root)
        self.moments.assert_called_once_with(self.root, self.snap)
        self.assertEqual(data['projects'][0]['state'], 'idle')
        self.assertEqual(data['reconnect'], ['Claude Code'])
        self.assertEqual(data['monitor']['state'], 'off')

    def test_slow_status_work_is_single_flight_across_concurrent_devices(self):
        started, release = threading.Event(), threading.Event()
        barrier = threading.Barrier(8)
        def snapshot(root):
            started.set()
            if not release.wait(3):
                raise AssertionError('The status stand-in was not released')
            return self.snap
        self.snapshot.side_effect = snapshot
        def request():
            barrier.wait(timeout=3)
            return board.status_payload(self.reg, [self.root])
        with ThreadPoolExecutor(max_workers=8) as pool:
            pending = [pool.submit(request) for _ in range(8)]
            self.assertTrue(started.wait(3))
            release.set()
            values = [future.result(timeout=3) for future in pending]
        self.assertEqual(len(set(values)), 1)
        self.snapshot.assert_called_once_with(self.root)

    def test_cache_expires_and_cannot_leak_between_registry_project_or_board_home(self):
        self.payload()
        self.snap = {'state': 'working', 'lines': []}
        self.assertEqual(self.payload()['projects'][0]['state'], 'idle')
        self.clock[0] += board.STATUS_SECONDS + .01
        self.assertEqual(self.payload()['projects'][0]['state'], 'working')
        changed = copy.deepcopy(self.reg)
        changed['settings']['provider'] = 'codex'
        self.payload(changed)
        self.assertEqual(self.snapshot.call_count, 3)
        other = self.folder / 'other-project'
        other.mkdir()
        self.payload(changed, [other])
        self.assertEqual(self.snapshot.call_count, 4)
        alternate = self.folder / 'another-board'
        with patch.dict(os.environ, COLONY_BOARD_HOME=str(alternate)):
            self.assertEqual(self.payload(changed, [other])['board'], str(alternate))
        self.assertEqual(self.snapshot.call_count, 5)

    def test_failed_build_releases_status_lock_and_does_not_cache_the_error(self):
        self.snapshot.side_effect = OSError('fixture status unavailable')
        with self.assertRaises(OSError):
            self.payload()
        self.snapshot.side_effect = lambda root: self.snap
        self.assertEqual(self.payload()['projects'][0]['state'], 'idle')
        self.assertEqual(self.snapshot.call_count, 2)

    def serve_browser(self):
        pages = {}
        nav = "<div class='navrow'><a class='proj' href='/?p=0&view=chat'><span class='pname'>First</span> <span id='dot-0' class='sdot idle'></span></a>" \
              "<a class='proj' href='/?p=1&view=chat'><span class='pname'>Second</span></a></div><span id='sline-0'></span>"
        with patch.object(board, 'sidebar', return_value=nav), patch.object(board, 'own_colour', return_value=''), \
                patch.object(board, 'proj_attr', return_value=''), patch.object(board, 'trouble_bar', return_value=''), \
                patch.object(board, 'signin_notice', return_value=''), patch.object(board, 'chat_feed', return_value="<div class='msg'><div class='say' data-k='fixture'>Initial reply.</div></div>"), \
                patch('colony.voice.ready', return_value=False):
            for key in ('0', '1'):
                chat = board.chat_page(key, self.root)
                pages['/?p=' + key + '&view=chat'] = board.shell(self.reg, -1, chat).encode()
            current = "<h1>Current items</h1><div id='current'><p>Original item</p></div>" + \
                      "<script>" + board.KEEP_CURRENT + ";keepCurrent(document.getElementById('current'), '/fixture/current');</script>"
            pages['/fixture/current-page'] = board.shell(self.reg, -1, current).encode()
        class Handler(board.Handler):
            def _get(self):
                if self.path in pages:
                    return self._send(200, pages[self.path])
                return super()._get()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:' + str(server.server_address[1])

    @unittest.skipUnless(os.environ.get('COLONY_BROWSER_TEST') == '1', 'requires isolated Chrome/Playwright')
    def test_slow_polling_failure_navigation_send_and_stop_in_real_page_scripts(self):
        # The browser's simulated clock must not replace socket-server timeout accounting.
        self.stack.enter_context(patch.object(board.time, 'monotonic', __import__('time').perf_counter))
        result = subprocess.run(['node', str(ROOT / 'tests/polling_browser.js'), self.serve_browser()],
                                text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS polling backpressure', result.stdout)
