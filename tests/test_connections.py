import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from colony import board, connections
from tests.test_board import BoardBase

# A stand-in MCP server: it offers three tools (only `look` marked read-only), answers `look` with the key it was
# started with, and records each `send` it runs in a file beside it.
SERVER = r'''
import json, os, sys
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    m, out = msg["method"], None
    if m == "initialize":
        out = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "shop", "version": "1"}}
    elif m == "tools/list":
        out = {"tools": [{"name": "look", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
                         {"name": "send", "inputSchema": {"type": "object"}},
                         {"name": "wipe", "inputSchema": {"type": "object"}, "annotations": {"destructiveHint": True}}]}
    elif m == "tools/call":
        name, args = msg["params"]["name"], msg["params"].get("arguments") or {}
        if name == "send":
            with open(os.path.join(os.path.dirname(sys.argv[0]), "sent.txt"), "a") as f:
                f.write(args.get("to", "") + "\n")
        text = os.environ.get("SHOP_TOKEN", "") if name == "look" else f"{name} to {args.get('to')}"
        out = {"content": [{"type": "text", "text": text}]}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": out}), flush=True)
'''


class ConnectionsTest(BoardBase):
    def setUp(self):
        super().setUp()
        board.track(self.root)
        self.server = Path(self.tmp.name) / "shop" / "server.py"
        self.server.parent.mkdir()
        self.server.write_text(SERVER)
        self.sent = self.server.parent / "sent.txt"
        connections.add("shop", "The shop", [sys.executable, str(self.server)], needs=["SHOP_TOKEN"],
                        tools=["look", "send"], about="Searches and sends.")
        self.other = Path(self.tmp.name) / "stall"
        self.other.mkdir()

    def agent(self, root=None, listed=True):
        """The agent's side: colony's connection server as a project's console starts it, spoken to over stdio."""
        cmd = connections.claude_args(root or self.root)
        s = json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]["shop"]
        proc = subprocess.Popen([s["command"], *s["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                env=os.environ.copy())
        self.addCleanup(lambda: (proc.kill(), proc.wait(), proc.stdin.close(), proc.stdout.close()))
        n = iter(range(1, 100))

        def ask(method, **params):
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": next(n), "method": method, "params": params}) + "\n")
            proc.stdin.flush()
            return json.loads(proc.stdout.readline())
        ask("initialize", protocolVersion="2025-06-18", capabilities={}, clientInfo={"name": "t"})
        if listed:
            ask("tools/list")                               # as an agent's program does first
        return ask

    def said(self, reply):
        return reply["result"]["content"][0]["text"]

    def ran(self, gate_id):
        for _ in range(100):
            notes = [n for n in board.notes(self.root) if n["author"] == "colony" and gate_id in n["text"]]
            if notes:
                return notes[0]["text"]
            time.sleep(0.1)

    def test_a_held_call_runs_only_on_the_persons_tap_and_the_keys_never_leave_their_file(self):
        self.assertEqual(connections.claude_args(self.root), [], "every project has it, but not before its key is entered")
        connections.set_key("shop", "SHOP_TOKEN", "s3cret")
        self.assertEqual(os.stat(connections.keys_path("shop")).st_mode & 0o777, 0o600)
        self.assertIn("--mcp-config", connections.claude_args(self.root), "on in every project by default")
        self.assertNotIn("s3cret", " ".join(connections.claude_args(self.root) + connections.codex_args(self.root)),
                         "the key is read as the server starts, never put in a console's command")
        connections.choose_path("shop", self.root, "off")
        self.assertEqual(connections.claude_args(self.root), [], "switched off in this project")
        self.assertIn("--mcp-config", connections.claude_args(self.other), "and only there")
        connections.choose_path("shop", self.root, "inherit")

        ask = self.agent(listed=False)
        self.assertIn("Held", self.said(ask("tools/call", name="look")), "a tool not yet listed waits, unknown")
        self.assertEqual([t["name"] for t in ask("tools/list")["result"]["tools"]], ["look", "send"],
                         "a tool the connection doesn't offer is left out")
        self.assertEqual(connections.load()["connections"]["shop"]["seen"], {"look": True, "send": False, "wipe": False})
        self.assertEqual(self.said(ask("tools/call", name="look")), "s3cret", "what it marks read-only runs at once")
        self.assertIn("error", ask("tools/call", name="wipe"), "one it doesn't offer is refused if called anyway")

        held = self.said(ask("tools/call", name="send", arguments={"to": "ann@example.com"}))
        gate = board.gates(self.root)[-1]
        self.assertIn(gate["id"], held)
        self.assertFalse(self.sent.exists(), "anything not marked read-only waits")
        self.assertEqual(gate["hold"], {"connection": "shop", "tool": "send", "arguments": {"to": "ann@example.com"}})
        self.assertIn("ann@example.com", gate["why"], "the person sees what would go out")
        choices = board.gate_choices(0, gate, "/")
        self.assertIn(connections.HOLD_YES, choices)
        self.assertNotIn("Choose for me", choices, "a held call is theirs to release, never handed back")

        board.answer_gate(self.root, gate["id"], connections.HOLD_YES, tell=False)
        time.sleep(1)
        self.assertFalse(self.sent.exists(), "an answer recorded from the command line (the agent's) never runs it")

        ask("tools/call", name="send", arguments={"to": "bo@example.com"})
        gate = board.gates(self.root)[-1]
        board.answer_gate(self.root, gate["id"], connections.HOLD_YES)
        self.assertIn("ran: send to bo@example.com", self.ran(gate["id"]), "their tap runs it; the agent hears how it went")
        board.answer_gate(self.root, gate["id"], connections.HOLD_YES)        # tapped twice
        time.sleep(1)
        self.assertEqual(self.sent.read_text(), "bo@example.com\n", "sent once")
        ask("tools/call", name="send", arguments={"to": "cy@example.com"})
        board.answer_gate(self.root, board.gates(self.root)[-1]["id"], connections.HOLD_NO)
        time.sleep(1)
        self.assertEqual(self.sent.read_text(), "bo@example.com\n", "their no sends nothing")

        connections.configure("shop", hold=["look"])                          # the holds chosen colony-wide
        ask = self.agent()
        self.assertIn("Held", self.said(ask("tools/call", name="look")))
        self.assertIn("send to dee", self.said(ask("tools/call", name="send", arguments={"to": "dee"})))

    def test_a_project_can_have_its_own_account_and_holds(self):
        connections.set_key("shop", "SHOP_TOKEN", "shared")
        connections.configure("shop", self.other, own=True)
        self.assertEqual(connections.claude_args(self.other), [], "its own settings, but its own key isn't in yet")
        connections.set_key("shop", "SHOP_TOKEN", "stall-only", self.other)
        self.assertNotEqual(connections.keys_path("shop", self.other), connections.keys_path("shop"))
        self.assertEqual(self.said(self.agent(self.other)("tools/call", name="look")), "stall-only")
        self.assertEqual(self.said(self.agent()("tools/call", name="look")), "shared", "the others keep the shared one")
        connections.configure("shop", self.other, hold=[])                     # sends freely there
        self.assertIn("send to x", self.said(self.agent(self.other)("tools/call", name="send", arguments={"to": "x"})))
        self.assertIn("Held", self.said(self.agent()("tools/call", name="send", arguments={"to": "y"})), "still held elsewhere")

    def test_a_service_reached_both_ways_takes_one_path_in_each_project(self):
        listed = ("claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ✔ Connected\n"
                  "claude.ai alphaXiv: https://api.alphaxiv.org/mcp/v1 - ✔ Connected\nblender: /x - ✔ Connected\n")
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, listed, "")):
            connections.discover_personal()
        self.assertEqual(sorted(connections.services()), ["alphaxiv", "gmail", "shop"])
        self.assertEqual(connections.path_for("gmail", self.root), "claude", "a new connector is on everywhere")
        self.assertNotIn("--settings", connections.claude_args(self.root))
        connections.choose_path("gmail", self.other, "off")
        deny = lambda args: json.loads(args[args.index("--settings") + 1])["deniedMcpServers"]
        self.assertEqual(deny(connections.claude_args(self.other)), [{"serverUrl": "https://gmailmcp.googleapis.com/mcp/v1"}])

        connections.add("mail", "Mail", [sys.executable, str(self.server)], service="gmail")
        self.assertEqual(sorted(connections.services()["gmail"]), ["claude", "colony", "title"], "one card, two sources")
        self.assertEqual(connections.path_for("gmail", self.root), "colony", "the one that can wait for a tap wins")
        args = connections.claude_args(self.root)
        self.assertIn('"mail"', args[args.index("--mcp-config") + 1])
        self.assertEqual(deny(args), [{"serverUrl": "https://gmailmcp.googleapis.com/mcp/v1"}],
                         "and the unheld twin is gone, so a held send can't go out the other way")
        connections.choose_path("gmail", self.root, "claude")
        self.assertEqual(connections.path_for("gmail", self.root), "claude")
        self.assertNotIn("--settings", connections.claude_args(self.root))
        self.assertNotIn("--mcp-config", connections.claude_args(self.root))
        board.project_settings(self.other, {"provider": "codex"})
        connections.choose_path("gmail", self.other, "inherit")
        self.assertEqual(connections.path_for("gmail", self.other), "colony", "a Codex project reaches it through colony")
        connections.choose_path("gmail", None, "claude")
        self.assertIsNone(connections.path_for("gmail", self.other), "never through a Claude connector")
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, listed, "")):
            connections.discover_personal()                 # looked up again: the person's choices stand
        self.assertEqual(connections.path_for("gmail", self.root), "claude")

    def test_choices_kept_in_the_first_shape_carry_over(self):
        connections.path().write_text(json.dumps({"connections": {}, "personal": {
            "Gmail": {"url": "https://gmailmcp.googleapis.com/mcp/v1", "projects": [str(self.root.resolve())]},
            "alphaXiv": {"url": "https://api.alphaxiv.org/mcp/v1", "projects": "all"}}}))
        self.assertEqual(connections.path_for("gmail", self.root), "claude")
        self.assertIsNone(connections.path_for("gmail", self.other))
        self.assertEqual(connections.path_for("alphaxiv", self.other), "claude")

    def test_the_pages_take_their_choices(self):
        page = board.connections_page(board.registry())
        self.assertIn("The shop", page)
        self.assertIn("keys missing", page)
        self.assertIn("name='key_SHOP_TOKEN'", page)
        self.assertIn("Everything but what it", page)
        self.assertIn("href='/connections'", board.shell(board.registry(), -2, ""), "in the menu")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        post = lambda path="/connections", **f: urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=urllib.parse.urlencode(f, doseq=True).encode()))
        pid = board.projects().index(self.root)
        post(do="keys", name="shop", key_SHOP_TOKEN="abc")
        self.assertEqual(connections.keys("shop"), {"SHOP_TOKEN": "abc"})
        post(do="keys", name="shop", key_SHOP_TOKEN="")
        self.assertEqual(connections.keys("shop"), {"SHOP_TOKEN": "abc"}, "a field left empty keeps what's kept")
        self.assertNotIn("abc", board.connections_page(board.registry()), "a kept key is never shown back")
        post(do="holds", name="shop", mode="chosen", tool=["send"])
        self.assertEqual(connections.load()["connections"]["shop"]["hold"], ["send"])
        post(do="path", svc="shop", how="off", p=pid)
        self.assertIsNone(connections.path_for("shop", self.root))
        self.assertIn("off", board.project_connections(pid, self.root))
        post(do="path", svc="shop", how="inherit", p=pid)
        post(do="own", name="shop", own="1", p=pid)
        post(do="keys", name="shop", key_SHOP_TOKEN="mine", p=pid)
        self.assertEqual(connections.keys("shop", self.root), {"SHOP_TOKEN": "mine"}, "its own key, apart")
        self.assertEqual(connections.keys("shop"), {"SHOP_TOKEN": "abc"})
        self.assertIn("own settings", board.connections_page(board.registry()), "the project that differs shows")
        self.assertIn("Its own account", board.project_connections(pid, self.root))

        self.assertIn("name='svc' value='shop' checked", board.add_project_page(board.registry()))
        within = Path(self.tmp.name) / "new"
        within.mkdir()
        with patch.object(board.console, "ensure"):
            post("/new", name="Fresh", within=str(within), svc_listed=["shop"])
        self.assertIsNone(connections.path_for("shop", within / "Fresh"), "left unticked as it was added: off there")
