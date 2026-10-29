"""First-tap file picking with the production mobile composers."""
import json
import os
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from colony import board
from tests.test_board import BoardBase, ROOT


class AttachmentActivationTest(BoardBase):
    @unittest.skipUnless(os.environ.get('COLONY_BROWSER_TEST'), 'requires isolated browsers')
    def test_phone_first_tap_keeps_draft_and_opens_one_picker(self):
        board.track(self.root)
        gate = dict(id='fixture-question', question='A fixture question?', answer='', options=[], rounds=[], default='')
        inline = "<details class='needs' open><summary>Needs you</summary><details class='need' open>" + board.gate_body(self.root, 0, gate, '/?p=0&view=chat') + '</details></details>'
        with patch.object(board, 'chat_feed', return_value=inline), patch('colony.voice.ready', return_value=False):
            chat = board.chat_page('0', self.root)
        page = board.shell(board.registry(), 0, chat).encode()
        class Handler(board.Handler):
            def _get(self):
                if self.path == '/?p=0&view=chat':
                    return self._send(200, page)
                if self.path.startswith('/chat/line'):
                    data = json.dumps(dict(line=[], projects={'0': dict(name='Fixture', href='/?p=0&view=chat')}, total=0, ready=False)).encode()
                    self.send_response(200); self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data))); self.end_headers()
                    return self.wfile.write(data)
                if self.path.startswith('/chat/feed'):
                    return self._send(200, inline.encode())
                return super()._get()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        result = subprocess.run(['node', str(ROOT / 'tests/attachment_browser.js'),
                                 f'http://127.0.0.1:{server.server_address[1]}'],
                                text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS first-tap attachments', result.stdout)
