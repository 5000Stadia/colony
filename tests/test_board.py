import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from colony import board  # noqa: E402

ROADMAP = """# Roadmap

A tool for my plants.

## M1 — v1: it works for me

- [x] R1 add plants
- [~] R2 water log
- [ ] R3 reminders
"""


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        os.environ["COLONY_BOARD_HOME"] = str(base / "home")
        self.root = base / "plants"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.root / "CLAUDE.md").write_text("Our own rules.\n")
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "settings.json").write_text(json.dumps({"model": "x"}))
        (self.root / "ROADMAP.md").write_text(ROADMAP)
        self.commit("start")

    def tearDown(self):
        os.environ.pop("COLONY_BOARD_HOME", None)
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True).stdout

    def commit(self, msg):
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg, "--allow-empty")

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "colony", *args], cwd=self.root, capture_output=True, text=True,
                              env=dict(os.environ, PYTHONPATH=str(ROOT)))

    def test_track_adds_to_what_the_project_has(self):
        board.track(self.root)
        self.assertTrue((self.root / "CLAUDE.md").read_text().startswith("Our own rules."))
        self.assertIn("## The board", (self.root / "CLAUDE.md").read_text())
        cfg = json.loads((self.root / ".claude" / "settings.json").read_text())
        self.assertEqual(cfg["model"], "x")
        commands = [h["command"] for e in cfg["hooks"]["UserPromptSubmit"] for h in e["hooks"]]
        self.assertEqual(commands, ["colony notes --deliver"])
        board.track(self.root)                                  # twice changes nothing
        cfg = json.loads((self.root / ".claude" / "settings.json").read_text())
        self.assertEqual(len(cfg["hooks"]["SessionStart"]), 1)
        self.assertEqual(board.registry()["projects"], [str(self.root)])

    def test_notes_reach_the_agent_when_they_are_relevant(self):
        board.track(self.root)
        board.add_note(self.root, {"commit": "abc1234"}, "the log format is wrong")
        board.add_note(self.root, {"item": "R3"}, "reminders by email, not SMS")
        out = self.cli("notes", "--deliver").stdout
        self.assertIn("the log format is wrong", out, "past work: on the next turn")
        self.assertNotIn("reminders by email", out, "a future item's note waits for the item")
        self.assertEqual(self.cli("notes", "--deliver").stdout, "", "each note is handed over once")
        (self.root / "ROADMAP.md").write_text(ROADMAP.replace("- [ ] R3", "- [~] R3"))
        self.assertIn("reminders by email", self.cli("notes", "--deliver").stdout, "the item started: now it is due")

    def test_no_note_waits_forever(self):
        board.track(self.root)
        board.add_note(self.root, {"item": "R3"}, "keep it simple")
        (self.root / "ROADMAP.md").write_text(ROADMAP.replace("- [ ] R3 reminders\n", ""))
        self.assertIn("keep it simple", self.cli("notes", "--deliver").stdout, "its item left the roadmap: due now")
        n = board.notes(self.root)[0]
        self.assertIn("keep it simple", self.cli("notes", "--deliver", "--session").stdout,
                      "delivered but not acted on: repeated at the start of the next session")
        self.cli("noted", n["id"], "kept it to one screen")
        self.assertEqual(self.cli("notes", "--deliver", "--session").stdout, "")
        self.assertEqual(board.notes(self.root)[0]["reply"], "kept it to one screen")

    def test_a_gate_waits_on_the_person_and_its_answer_reaches_the_agent(self):
        board.track(self.root)
        self.cli("gate", "Delete the old data format?", "--item", "R2", "--why", "migration drops history")
        [g] = board.gates(self.root)
        self.assertIsNone(g["answer"])
        board.answer_gate(self.root, g["id"], "keep history; migrate it")
        self.assertEqual(board.gates(self.root)[0]["answer"], "keep history; migrate it")
        self.assertIn("keep history; migrate it", self.cli("notes", "--deliver").stdout)

    def test_since_you_were_last_here(self):
        board.track(self.root)
        self.commit("tracked")
        reg = board.registry()
        reg["seen"][str(self.root)] = {"at": board.now(), "head": self.git("rev-parse", "HEAD").strip()}
        board.save_registry(reg)
        (self.root / "ROADMAP.md").write_text(ROADMAP.replace("- [~] R2", "- [x] R2"))
        self.commit("water log done")
        s = board.since(self.root, board.registry()["seen"][str(self.root)])
        self.assertEqual([c[2] for c in s["commits"]], ["water log done"])
        self.assertEqual(s["moved"], [("R2", "doing", "done", "water log")])
        html_ = board.render(board.registry(), 0)
        for part in ("Since you were last here", "doing → done", "Waiting on you", "Roadmap", "R3", "History"):
            self.assertIn(part, html_)

    def test_the_board_answers_only_itself(self):
        board.track(self.root)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            data = urllib.parse.urlencode({"p": 0, "kind": "item", "ref": "R3", "text": "from evil"}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/note", data=data, headers={"Origin": "http://evil.example"})
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(req)
            ok = urllib.request.Request(f"http://127.0.0.1:{port}/note", data=urllib.parse.urlencode(
                {"p": 0, "kind": "item", "ref": "R3", "text": "from the page"}).encode())
            urllib.request.urlopen(ok)
            self.assertEqual([n["text"] for n in board.notes(self.root)], ["from the page"])
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
