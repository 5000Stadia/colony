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

from colony import board, cli, handover, monitor, providers


class SetupLinksTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        env = patch.dict(os.environ, COLONY_BOARD_HOME=str(self.base / "board"),
                         CLAUDE_CONFIG_DIR=str(self.base / "claude"))
        env.start()
        self.addCleanup(env.stop)

    def start(self, installed, choice=None):
        args = argparse.Namespace(no_monitor=False, foreground=False, program=choice,
                                  port=8877, lan=False, local=False)
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
        return json.loads((board.home() / "to_monitor.jsonl").read_text().splitlines()[0])["text"]

    def assert_program_settled(self, text, key):
        self.assertEqual(board.registry()["settings"]["provider"], key)
        self.assertIn(f"Colony uses {providers.get(key).label}, chosen at start.", text)
        self.assertIn("Do not ask which program again", text)
        self.assertIn("starting with the introduction", text)
        self.assertIn("install and sign in to another later", text)
        self.assertIn("Settings → Agent programs", text)
        self.assertNotIn("starting with which agent programs", text)

    def test_explicit_start_choice_is_not_asked_again_when_both_are_installed(self):
        self.assert_program_settled(self.start(["claude", "codex"], "codex"), "codex")

    def test_only_codex_is_not_asked_again(self):
        self.assert_program_settled(self.start(["codex"]), "codex")

    def test_only_claude_is_not_asked_again(self):
        self.assert_program_settled(self.start(["claude"]), "claude")

    def test_start_default_is_also_settled_before_setup(self):
        self.assert_program_settled(self.start(["claude", "codex"]), "claude")

    def test_steward_brief_uses_named_board_links_and_skips_the_settled_question(self):
        board.save_registry({"roots": [], "settings": {"lan": False, "provider": "codex"}})
        monitor.brief()
        brief = monitor.brief_path().read_text()
        self.assertIn("`/?n=NAME`, using its folder", brief)
        self.assertIn("[Weekend recipes](/?n=Weekend%20recipes)", brief)
        self.assertIn("Do not ask which program again", brief)
        self.assertIn("Settings → Agent", brief)


if __name__ == "__main__":
    unittest.main()
