import argparse
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from colony import board, cli, handover, providers


class SetupLinksTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        env = patch.dict(os.environ, COLONY_BOARD_HOME=str(self.base / "board"),
                         CLAUDE_CONFIG_DIR=str(self.base / "claude"))
        env.start()
        self.addCleanup(env.stop)

    def start(self, installed):
        args = argparse.Namespace(foreground=False, port=8877, lan=False, local=False)
        with ExitStack() as stack:
            stack.enter_context(patch.object(providers, "installed", lambda p: providers.key(p) in installed))
            for key in installed:
                cls = type(providers.get(key))
                stack.enter_context(patch.object(cls, "signed_in", return_value=True))
                stack.enter_context(patch.object(cls, "answers", return_value=dict(ok=True)))
            stack.enter_context(patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)))
            stack.enter_context(patch.object(cli.sys.stdin, "isatty", return_value=False))
            stack.enter_context(patch.object(handover, "started", return_value=False))
            stack.enter_context(patch.object(cli, "_free", return_value=True))
            stack.enter_context(patch.object(cli, "_launch", return_value=False))
            stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(cli.cmd_board(args), 1, "stop before launching a server")

    def assert_program_settled(self, key):
        self.assertEqual(board.registry()["settings"]["provider"], key)

    def test_only_codex_is_settled_at_start(self):
        self.start(["codex"])
        self.assert_program_settled("codex")

    def test_only_claude_is_settled_at_start(self):
        self.start(["claude"])
        self.assert_program_settled("claude")

    def test_both_installed_settle_on_the_default_with_no_terminal_to_ask(self):
        self.start(["claude", "codex"])
        self.assert_program_settled("claude")


if __name__ == "__main__":
    unittest.main()
