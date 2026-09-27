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
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from colony import board, console, mail, monitor, providers  # noqa: E402
import base64, socket, time  # noqa: E402

ROADMAP = """# Roadmap

A tool for my plants.

## M1 — v1: it works for me

- [x] R1 add plants
- [~] R2 water log
- [ ] R3 reminders
"""


class BoardBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        os.environ["COLONY_BOARD_HOME"] = str(base / "home")
        # A test board sees no one's real project folders and, unless a test says so, posts no mail.
        board.save_registry({"roots": [], "settings": {"messaging": False}})
        # Nor does it ever start a real agent: a console that a test starts, here or in a `colony` it runs,
        # is a stand-in, and teardown ends every session its projects left.
        self._command, console.COMMAND = console.COMMAND, "sleep 60"
        os.environ["COLONY_CONSOLE_CMD"] = "sleep 60"
        self.root = base / "plants"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.root / "CLAUDE.md").write_text("Our own rules.\n")
        (self.root / ".claude").mkdir()
        (self.root / ".claude" / "settings.json").write_text(json.dumps({"model": "x"}))
        (self.root / "ROADMAP.md").write_text(ROADMAP)
        self.commit("start")

    def tearDown(self):
        for d in Path(self.tmp.name).iterdir():
            console.stop(d)
        console.COMMAND = self._command
        os.environ.pop("COLONY_CONSOLE_CMD", None)
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


class BoardTest(BoardBase):
    def test_track_adds_to_what_the_project_has(self):
        board.track(self.root)
        self.assertTrue((self.root / "CLAUDE.md").read_text().startswith("Our own rules."))
        self.assertIn("## This project is part of a colony", (self.root / "CLAUDE.md").read_text())
        cfg = json.loads((self.root / ".claude" / "settings.json").read_text())
        self.assertEqual(cfg["model"], "x")
        commands = [h["command"] for e in cfg["hooks"]["UserPromptSubmit"] for h in e["hooks"]]
        self.assertEqual(commands, ["colony notes --deliver"])
        board.track(self.root)                                  # twice changes nothing
        cfg = json.loads((self.root / ".claude" / "settings.json").read_text())
        self.assertEqual(len(cfg["hooks"]["SessionStart"]), 1)
        self.assertEqual(board.registry()["projects"], [str(self.root)])

    def test_the_persons_open_notes_are_listed_and_none_is_lost_with_its_item(self):
        board.track(self.root)
        kept = board.add_note(self.root, {"item": "R1"}, "use the wording from the brief")
        board.add_note(self.root, {"item": "R9"}, "an item later dropped from the plan")
        page = board.render(board.registry(), 0, "")
        self.assertIn("Your notes, not yet acted on (2)", page)
        self.assertIn("Notes on items no longer on the roadmap (R9)", page)
        board.append(self.root, "notes.jsonl", {"type": "addressed", "of": kept["id"], "at": board.now(), "text": "done"})
        page = board.render(board.registry(), 0, "")
        self.assertIn("Your notes, not yet acted on (1)", page)
        self.assertIn("an item later dropped from the plan", page.split("no longer on the roadmap")[1])

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


class ConsoleTest(BoardBase):
    def serve(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.httpd.server_address[1]

    def tearDown(self):
        console.stop(self.root)
        if hasattr(self, "httpd"):
            self.httpd.shutdown()
            self.httpd.server_close()
        super().tearDown()

    def handshake(self, port, origin, token):
        s = socket.create_connection(("127.0.0.1", port))
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET /console/ws?p=0&t={token} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                   f"Origin: {origin}\r\n\r\n").encode())
        return s, s.recv(4096).decode(errors="replace")

    def test_the_console_answers_only_this_page_with_its_token(self):
        board.track(self.root)
        port = self.serve()
        _, head = self.handshake(port, "http://evil.example", console.TOKEN)
        self.assertIn("403", head.splitlines()[0])
        _, head = self.handshake(port, f"http://127.0.0.1:{port}", "wrong")
        self.assertIn("403", head.splitlines()[0])
        self.assertFalse(console.live(self.root), "a refused connection starts nothing")

    def test_the_console_bridges_to_the_projects_own_session_and_outlives_the_browser(self):
        board.track(self.root)
        console.COMMAND = "cat"
        port = self.serve()
        s, head = self.handshake(port, f"http://127.0.0.1:{port}", console.TOKEN)
        self.assertIn("101", head.splitlines()[0])
        msg = json.dumps({"i": "hello board\r"}).encode()
        mask = os.urandom(4)
        s.sendall(bytes([0x81, 0x80 | len(msg)]) + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(msg)))
        seen, deadline = b"", time.time() + 10
        while b"hello board" not in seen and time.time() < deadline:
            s.settimeout(1)
            try:
                seen += s.recv(65536)
            except socket.timeout:
                pass
        self.assertIn(b"hello board", seen)
        s.close()
        time.sleep(0.5)
        self.assertTrue(console.live(self.root), "closing the browser detaches; the session keeps running")
        self.assertIn("sdot idle", board.sidebar(board.registry(), 0))


class GlanceTest(BoardBase):
    def tearDown(self):
        console.stop(self.root)
        super().tearDown()

    def test_the_status_is_read_off_the_screen(self):
        claude = providers.get("claude")
        self.assertEqual(claude.classify("✻ Reading files… (esc to interrupt)"), "working")
        self.assertEqual(claude.classify("Do you want to make this edit?\n❯ 1. Yes"), "needs you")
        self.assertEqual(claude.classify("│ > │"), "idle")

    def test_a_running_session_shows_its_last_lines_and_the_board_serves_them(self):
        board.track(self.root)
        console.COMMAND = "sh -c 'echo first line; echo second line; echo esc to interrupt; sleep 30'"
        console.ensure(self.root)
        time.sleep(1)
        snap = console.snapshot(self.root)
        self.assertEqual(snap["state"], "working")
        self.assertIn("second line", snap["lines"])
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            [s] = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/status").read())["projects"]
            self.assertEqual(s["state"], "working")
            page = urllib.request.urlopen(f"http://127.0.0.1:{port}/?p=0").read().decode()
            self.assertIn("id='peek-0'", page)
            self.assertIn("second line", page)
        finally:
            httpd.shutdown()
            httpd.server_close()
        console.stop(self.root)
        self.assertEqual(console.snapshot(self.root)["state"], "off")


class MonitorTest(BoardBase):
    def setUp(self):
        super().setUp()
        board.track(self.root)
        self.saved = (console.snapshot, console.type_into, monitor.snapshot)

    def tearDown(self):
        console.snapshot, console.type_into, monitor.snapshot = self.saved
        console.stop(self.root)
        super().tearDown()

    def test_the_monitor_wakes_only_when_a_project_needs_the_person_or_finishes(self):
        screens = iter(["working", "working", "needs you", "needs you", "working", "idle"])
        console.snapshot = lambda root, lines=6, name=None: {"state": next(screens), "lines": ["last line"]}
        sent = []
        console.type_into = lambda name, text: sent.append(text)
        monitor.snapshot = lambda: {"state": "idle", "lines": []}
        w = monitor.Watcher(quiet=0)
        for _ in range(6):
            w.tick()
        self.assertEqual(len(sent), 2, "busy work wakes nothing; needs-you and finished each wake it once")
        self.assertIn("plants needs you", sent[0])
        self.assertIn("plants finished a turn", sent[1])
        self.assertIn("helm is off", sent[0])

    def test_events_wait_until_the_monitor_is_free_and_a_new_gate_wakes_it(self):
        console.snapshot = lambda root, lines=6, name=None: {"state": "working", "lines": []}
        sent, busy = [], {"state": "working", "lines": []}
        console.type_into = lambda name, text: sent.append(text)
        monitor.snapshot = lambda: busy
        w = monitor.Watcher(quiet=0)
        w.tick()
        board.append(self.root, "gates.jsonl", {"type": "gate", "id": "g1", "at": board.now(), "question": "Ship it?"})
        w.tick()
        self.assertEqual(sent, [], "the monitor is busy: the event waits")
        busy["state"] = "idle"
        monitor.helm(True)
        w.tick()
        self.assertEqual(len(sent), 1)
        self.assertIn("opened a gate: Ship it?", sent[0])
        self.assertIn("You hold the helm", sent[0])

    def test_tell_new_and_helm_from_the_command_line(self):
        env = {"COLONY_CONSOLE_CMD": "cat"}
        run = lambda *a: subprocess.run([sys.executable, "-m", "colony", *a], cwd=self.root, capture_output=True,
                                        text=True, env=dict(os.environ, PYTHONPATH=str(ROOT), **env))
        self.assertIn("sent to plants", run("tell", "plants", "please add reminders").stdout)
        time.sleep(0.5)
        self.assertIn("please add reminders", "\n".join(console.snapshot(self.root)["lines"]))
        out = run("new", "fresh", "--in", str(self.root.parent))
        self.assertIn("fresh created", out.stdout, out.stderr)
        fresh = self.root.parent / "fresh"
        self.assertTrue((fresh / "ROADMAP.md").exists())
        self.assertTrue(console.live(fresh))
        console.stop(fresh)
        self.assertIn("helm is with the person", run("helm").stdout)
        self.assertIn("holds the helm", run("helm", "on").stdout)
        self.assertTrue(monitor.helm())


class FoldersTest(BoardBase):
    def post(self, port, url, **form):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{url}", data=urllib.parse.urlencode(form).encode())
        return urllib.request.urlopen(req)

    def test_every_subfolder_of_a_project_folder_is_a_project(self):
        shelf = Path(self.tmp.name) / "shelf"
        (shelf / "novel").mkdir(parents=True)
        (shelf / "shop").mkdir()
        (shelf / ".hidden").mkdir()
        reg = board.registry()
        reg["roots"] = [str(shelf)]
        board.save_registry(reg)
        names = [p.name for p in board.projects()]
        self.assertEqual(names, ["novel", "shop"])
        self.assertTrue((shelf / "novel" / "ROADMAP.md").exists(), "found and put on the board")
        self.assertEqual(board.registry()["projects"], [], "found in a folder, not listed one by one")

    def test_the_browser_adds_a_folder_creates_a_project_and_settings_manage_folders(self):
        shelf = Path(self.tmp.name) / "shelf"
        shelf.mkdir()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        console.COMMAND = "cat"
        try:
            page = urllib.request.urlopen(f"http://127.0.0.1:{port}/add?dir={urllib.parse.quote(self.tmp.name)}").read().decode()
            self.assertIn("plants/", page)
            self.post(port, "/add", path=str(self.root))
            self.assertIn(str(self.root), board.registry()["projects"])
            self.post(port, "/new", within=str(shelf), name="fresh idea")
            self.assertTrue((shelf / "fresh idea" / "ROADMAP.md").exists())
            self.assertTrue(console.live(shelf / "fresh idea"))
            console.stop(shelf / "fresh idea")
            self.post(port, "/roots", add=str(shelf))
            self.post(port, "/roots", default=str(shelf))
            self.assertEqual(board.registry()["new_root"], str(shelf))
            self.assertIn("fresh idea", [p.name for p in board.projects()])
            self.post(port, "/roots", untrack=str(self.root))
            self.assertNotIn(self.root, board.projects())
            self.assertTrue(self.root.exists(), "removing from the board leaves the files")
        finally:
            httpd.shutdown()
            httpd.server_close()


class SettingsTest(BoardBase):
    def test_settings_shape_new_consoles_and_the_monitor_can_read_and_change_them(self):
        saved, console.COMMAND = console.COMMAND, None
        try:
            self.assertEqual(console.command("plants"), "claude --remote-control plants", "remote is on by default")
            run = lambda *a: subprocess.run([sys.executable, "-m", "colony", *a], cwd=self.root, capture_output=True,
                                            text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))
            self.assertIn("remote     on", run("settings").stdout)
            run("settings", "remote", "off")
            run("settings", "model", "claude-opus-5-5")
            run("settings", "effort", "medium")
            self.assertEqual(console.command("plants"), "claude --model claude-opus-5-5 --effort medium")
            elsewhere = Path(self.tmp.name) / "work"
            run("settings", "new-folder", str(elsewhere))
            reg = board.registry()
            self.assertEqual(reg["new_root"], str(elsewhere))
            self.assertIn(str(elsewhere), reg["roots"])
            self.assertIn("no setting", run("settings", "colour", "blue").stderr)
            self.assertIn("colony settings", monitor.ROLE, "the monitor knows the settings")
        finally:
            console.COMMAND = saved

    def test_the_front_page_is_the_monitor_and_settings_save_from_the_page(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        opener = urllib.request.build_opener(type("NoRedirect", (urllib.request.HTTPRedirectHandler,),
                                                  {"redirect_request": lambda *a: None}))
        try:
            with self.assertRaises(urllib.error.HTTPError) as r:
                opener.open(f"http://127.0.0.1:{port}/")
            self.assertEqual(r.exception.headers["Location"], "/monitor")
            data = urllib.parse.urlencode({"monitor": "on", "model": "", "effort": "high", "new_root": ""}).encode()
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/options", data=data))
            s = board.registry()["settings"]
            self.assertEqual((s["remote"], s["effort"]), (False, "high"), "an unticked box turns remote off")
        finally:
            httpd.shutdown()
            httpd.server_close()


class ProjectSettingsTest(BoardBase):
    def test_a_project_chooses_for_itself_and_falls_back_to_the_global_settings(self):
        board.track(self.root)
        saved, console.COMMAND = console.COMMAND, None
        try:
            self.assertEqual(console.command("plants", self.root), "claude --remote-control plants")
            board.project_settings(self.root, {"permissions": "edits", "remote": "off", "effort": "high"})
            self.assertEqual(console.command("plants", self.root), "claude --permission-mode acceptEdits --effort high")
            board.set_setting("model", "claude-opus-5-5")
            self.assertIn("--model claude-opus-5-5", console.command("plants", self.root), "unset here: the global one")
            board.project_settings(self.root, {"permissions": ""})
            self.assertNotIn("--permission-mode", console.command("plants", self.root), "blank returns to global")
            with self.assertRaises(KeyError):
                board.project_settings(self.root, {"permissions": "everything"})
        finally:
            console.COMMAND = saved

    def test_a_new_project_names_its_provider_and_model_and_another_provider_can_join(self):
        class Other:                       # what another CLI supplies to join: start, wire, read the screen
            label, models, efforts = "Other CLI", [("big-1", "Big 1")], ["deep"]
            own_defaults = lambda self: {"model": None, "effort": None}
            model_name = lambda self, v: v
            command = lambda self, label, s: f"other --model {s.get('model')}"
            wire = lambda self, root, protocol: (root / "AGENTS.md").write_text(protocol)
            wired = lambda self, root: (root / "AGENTS.md").exists()
            classify = lambda self, screen: "idle"
        providers.PROVIDERS["other"] = Other()
        saved = console.COMMAND
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            page = urllib.request.urlopen(f"http://127.0.0.1:{port}/add?for=project&dir={self.tmp.name}").read().decode()
            self.assertIn(">Claude Code</option>", page)
            self.assertIn("<option value='claude-opus-5-5'>Opus 5.5</option>", page, "exact models are suggested, by name")
            self.assertIn("<option value='claude' selected>Claude Code</option>", page, "a new project starts filled in")
            self.assertNotIn(">global<", page)
            for name, provider in (("seeds", "claude"), ("soil", "other")):
                data = urllib.parse.urlencode({"within": self.tmp.name, "name": name, "provider": provider, "model": "big"}).encode()
                urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/new", data=data))
            seeds, soil = Path(self.tmp.name) / "seeds", Path(self.tmp.name) / "soil"
            console.COMMAND = None                         # only to read the command each would start with
            self.assertIn("This project is part of a colony", (seeds / "CLAUDE.md").read_text())
            self.assertEqual(console.command("seeds", seeds), "claude --remote-control seeds --model big")
            self.assertTrue((soil / "AGENTS.md").exists() and not (soil / "CLAUDE.md").exists(), "wired by its own provider")
            self.assertEqual(console.command("soil", soil), "other --model big")
            with self.assertRaises(KeyError):
                board.project_settings(seeds, {"provider": "nobody"})
        finally:
            del providers.PROVIDERS["other"]
            console.COMMAND = saved
            httpd.shutdown()
            httpd.server_close()

    def test_the_urls_name_this_machine_and_the_network(self):
        board.set_setting("lan", "on")
        us = board.urls(8790)
        self.assertEqual(us[0], "http://127.0.0.1:8790/")
        self.assertTrue(all(u.startswith("http://") and u.endswith(":8790/") for u in us))
        board.set_setting("lan", "off")
        self.assertEqual(board.urls(8790), ["http://127.0.0.1:8790/"])


class MessagingTest(BoardBase):
    def test_the_person_asks_one_project_to_message_another_through_its_own_agent(self):
        other = Path(self.tmp.name) / "shop"
        other.mkdir()
        board.track(self.root)
        board.track(other)
        typed = []
        saved = (console.type_into, console.ensure)
        console.type_into = lambda name, text: typed.append((name, text))
        console.ensure = lambda root, name=None, label=None: console.session_name(root)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            page = urllib.request.urlopen(f"http://127.0.0.1:{port}/?p=0&view=console").read().decode()
            self.assertIn("What should this agent message them about?", page)
            self.assertIn("data-k='down'", page, "a phone can answer an on-screen choice")
            self.assertIn("down: '\\x1b[B'", page)
            self.assertIn(">shop</option>", page)
            data = urllib.parse.urlencode({"p": 0, "to": 1, "text": "which CSV columns do you export?"}).encode()
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/message", data=data))
            [(name, text)] = typed
            self.assertEqual(name, console.session_name(self.root), "the sending project's own agent gets it")
            self.assertIn("which CSV columns do you export?", text)
            self.assertIn("colony send shop --ask", text)
        finally:
            console.type_into, console.ensure = saved
            httpd.shutdown()
            httpd.server_close()

    def test_a_question_and_its_answer_travel_between_two_projects(self):
        board.set_setting("messaging", "on")
        shop = Path(self.tmp.name) / "shop"
        shop.mkdir()
        board.track(self.root)
        board.track(shop)
        run = lambda cwd, *a: subprocess.run([sys.executable, "-m", "colony", *a], cwd=cwd, capture_output=True,
                                             text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))
        self.assertIn("sent", run(self.root, "send", "shop", "Which CSV columns do you export?", "--ask").stdout)
        got = run(shop, "notes", "--deliver").stdout
        self.assertIn("plants asks: Which CSV columns do you export?", got)
        self.assertEqual(run(shop, "notes", "--deliver").stdout, "", "handed over once")
        self.assertIn("haven't answered", run(shop, "notes", "--deliver", "--session").stdout, "an open question is repeated")
        [q] = mail.inbox(shop)
        self.assertIn("answered", run(shop, "reply", q["id"], "sku,name,qty,price").stdout)
        self.assertIn("shop writes: sku,name,qty,price", run(self.root, "notes", "--deliver").stdout)
        self.assertEqual(mail.unanswered(shop), [])
        self.assertIn("Which CSV columns", run(self.root, "mail").stdout, "the sender keeps its side of the thread")

    def test_projects_inside_another_repository_are_their_own_roots(self):
        board.set_setting("messaging", "on")
        outer = Path(self.tmp.name) / "outer"
        (outer / "projects" / "a").mkdir(parents=True)
        (outer / "projects" / "b").mkdir()
        subprocess.run(["git", "init", "-q", str(outer)], check=True)
        reg = board.registry()
        reg["roots"] = [str(outer / "projects")]
        board.save_registry(reg)
        self.assertEqual([p.name for p in board.projects()], ["a", "b"])
        self.assertTrue((outer / "projects" / "a" / ".board").is_dir())
        self.assertFalse((outer / ".board").exists(), "the outer repository is not made a project")
        self.assertEqual(board.root_of(outer / "projects" / "a"), outer / "projects" / "a")
        run = lambda cwd, *a: subprocess.run([sys.executable, "-m", "colony", *a], cwd=cwd, capture_output=True,
                                             text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))
        run(outer / "projects" / "a", "send", "b", "hello from a")
        self.assertIn("a writes: hello from a", run(outer / "projects" / "b", "notes", "--deliver").stdout)

    def test_messaging_can_be_switched_off(self):
        board.track(self.root)
        with self.assertRaises(PermissionError):
            mail.send("plants", "hello")


class MailWakeTest(BoardBase):
    def setUp(self):
        super().setUp()
        board.set_setting("messaging", "on")
        board.track(self.root)
        self.saved = (console.snapshot, console.type_into, console.ensure, monitor.snapshot)
        self.typed, self.started, self.state = [], [], {"state": "idle"}
        console.snapshot = lambda root, lines=6, name=None: {"state": self.state["state"], "lines": []}
        console.type_into = lambda name, text: self.typed.append(text)
        console.ensure = lambda root, name=None, label=None: self.started.append(root)
        monitor.snapshot = lambda: {"state": "working", "lines": []}

    def tearDown(self):
        console.snapshot, console.type_into, console.ensure, monitor.snapshot = self.saved
        super().tearDown()

    def test_mail_wakes_an_idle_project_once_and_starts_one_that_is_off(self):
        w = monitor.Watcher()
        mail.send("plants", "hello")
        w.mail()
        w.mail()
        self.assertEqual(self.typed, ["[colony] You have mail from another project in the colony."], "one nudge per message")
        self.state["state"] = "off"
        mail.send("plants", "and again")
        w.mail()
        self.assertEqual(self.started, [self.root], "a project that isn't running is started for its mail")

    def test_a_note_left_while_the_agent_is_idle_does_not_wait_for_the_person_to_type(self):
        w = monitor.Watcher()
        board.add_note(self.root, {"item": "R3"}, "for when reminders start")        # R3 not started: it waits
        w.mail()
        self.assertEqual(self.typed, [])
        board.add_note(self.root, None, "add a one-line README")
        w.mail()
        self.assertEqual(self.typed, ["[colony] You have a note from the person on the board."])

    def test_busy_projects_wait_unless_the_mail_is_urgent(self):
        w = monitor.Watcher()
        self.state["state"] = "working"
        mail.send("plants", "when you get a moment")
        w.mail()
        self.assertEqual(self.typed, [], "a busy session is not interrupted")
        mail.send("plants", "stop: the export format changed", urgent=True)
        w.mail()
        self.assertEqual(len(self.typed), 1, "urgent mail is typed in even mid-turn")


class ServerTest(BoardBase):
    def run_cli(self, *a):
        return subprocess.run([sys.executable, "-m", "colony", *a], cwd=self.root, capture_output=True, text=True,
                              env=dict(os.environ, PYTHONPATH=str(ROOT)))

    def tearDown(self):
        subprocess.run(["tmux", "kill-session", "-t", board.scoped("board-server")], capture_output=True)
        super().tearDown()

    def test_the_board_runs_on_its_own_restarts_and_the_doctor_checks_it(self):
        board.track(self.root)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = str(s.getsockname()[1])
        s.close()
        self.assertNotEqual(board.scoped("board-server"), "board-server", "a test board never touches the real one")
        self.assertIn("the board is running", self.run_cli("board", "--port", port, "--no-monitor").stdout)
        doctor = self.run_cli("doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout)
        self.assertIn(f"board answers on port {port}", doctor.stdout)
        self.assertIn("plants: wired", doctor.stdout)
        self.assertIn("restarted", self.run_cli("restart").stdout)
        self.assertEqual(self.run_cli("doctor").returncode, 0)
        (self.root / "CLAUDE.md").write_text("Our own rules.\n")
        broken = self.run_cli("doctor")
        self.assertEqual(broken.returncode, 1)
        self.assertIn("board wiring is missing", broken.stdout)
        stopped = self.run_cli("stop").stdout
        self.assertIn(board.scoped("board-server"), stopped)
        self.assertNotEqual(subprocess.run(["tmux", "has-session", "-t", board.scoped("board-server")]).returncode, 0)

    def test_a_port_held_by_another_program_is_never_mistaken_for_the_board(self):
        other = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)      # answers every request with an error page
        threading.Thread(target=other.serve_forever, daemon=True).start()
        port = str(other.server_address[1])
        try:
            (board.home() / "server.json").write_text(json.dumps(["--port", port, "--local", "--no-monitor"]))
            self.assertIn("does not answer", self.run_cli("doctor").stdout)
            moved = self.run_cli("board", "--port", port, "--no-monitor")     # taken: it moves to a free one
            self.assertEqual(moved.returncode, 0, moved.stderr)
            self.assertIn(f"port {port} is taken", moved.stdout)
            self.assertNotIn(f":{port}/", moved.stdout.split("using", 1)[1])
            self.assertIn("board answers", self.run_cli("doctor").stdout)
        finally:
            other.shutdown()
            other.server_close()


if __name__ == "__main__":
    unittest.main()
