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

# A stand-in MCP server: it offers three tools, answers `look` with the key it was started with, and records each
# `send` it runs in a file beside it.
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
        out = {"tools": [{"name": n, "inputSchema": {"type": "object"}} for n in ("look", "send", "wipe")]}
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
        connections.add("shop", "The shop", [sys.executable, str(self.server)], needs=["SHOP_TOKEN"], hold=["send"],
                        tools=["look", "send"], about="Searches and sends.")

    def agent(self):
        """The agent's side: colony's connection server as its console starts it, spoken to over stdio."""
        cmd = connections.claude_args(self.root)
        servers = json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]
        s = servers["shop"]
        proc = subprocess.Popen([s["command"], *s["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                env=os.environ.copy())
        self.addCleanup(lambda: (proc.kill(), proc.wait(), proc.stdin.close(), proc.stdout.close()))
        n = iter(range(1, 100))

        def ask(method, **params):
            i = next(n)
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
            proc.stdin.flush()
            return json.loads(proc.stdout.readline())
        ask("initialize", protocolVersion="2025-06-18", capabilities={}, clientInfo={"name": "t"})
        return ask

    def test_a_held_call_runs_only_on_the_persons_tap_and_the_keys_never_leave_their_file(self):
        self.assertEqual(connections.claude_args(self.root), [], "not granted: the console isn't started with it")
        connections.set_grants("shop", [self.root])
        self.assertEqual(connections.claude_args(self.root), [], "granted, but its key isn't entered yet")
        connections.set_key("shop", "SHOP_TOKEN", "s3cret")
        self.assertEqual(os.stat(connections.keys_path("shop")).st_mode & 0o777, 0o600)
        self.assertNotIn("s3cret", " ".join(connections.claude_args(self.root) + connections.codex_args(self.root)),
                         "the key is read as the server starts, never put in a console's command")

        ask = self.agent()
        names = [t["name"] for t in ask("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["look", "send"], "a tool the connection doesn't offer is left out")
        self.assertEqual(ask("tools/call", name="look")["result"]["content"][0]["text"], "s3cret")
        self.assertIn("error", ask("tools/call", name="wipe"), "and refused if called anyway")

        held = ask("tools/call", name="send", arguments={"to": "ann@example.com"})["result"]["content"][0]["text"]
        gate = board.gates(self.root)[-1]
        self.assertIn(f"gate {gate['id']}", held)
        self.assertFalse(self.sent.exists(), "held: nothing sent yet")
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
        for _ in range(100):
            ran = [n for n in board.notes(self.root) if n["author"] == "colony" and gate["id"] in n["text"]]
            if ran:
                break
            time.sleep(0.1)
        self.assertIn("ran: send to bo@example.com", ran[0]["text"], "their tap runs it; the agent hears how it went")
        board.answer_gate(self.root, gate["id"], connections.HOLD_YES)        # tapped twice
        time.sleep(1)
        self.assertEqual(self.sent.read_text(), "bo@example.com\n", "sent once")

        ask("tools/call", name="send", arguments={"to": "cy@example.com"})
        board.answer_gate(self.root, board.gates(self.root)[-1]["id"], connections.HOLD_NO)
        time.sleep(1)
        self.assertEqual(self.sent.read_text(), "bo@example.com\n", "their no sends nothing")

    def test_each_console_starts_with_its_grants_and_without_the_persons_connectors_it_wasnt_given(self):
        connections.set_key("shop", "SHOP_TOKEN", "s3cret")
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        connections.set_grants("shop", [self.root])
        listed = ("claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ✔ Connected\n"
                  "claude.ai alphaXiv: https://api.alphaxiv.org/mcp/v1 - ✔ Connected\nblender: /x - ✔ Connected\n")
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, listed, "")):
            connections.discover_personal()
        self.assertEqual(sorted(connections.load()["personal"]), ["Gmail", "alphaXiv"])
        connections.grant("alphaXiv", connections.ALL, personal=True)
        connections.grant("Gmail", other, personal=True)

        mine, theirs = connections.claude_args(self.root), connections.claude_args(other)
        self.assertIn("--mcp-config", mine)
        self.assertNotIn("--mcp-config", theirs)
        deny = lambda args: json.loads(args[args.index("--settings") + 1])["deniedMcpServers"]
        self.assertEqual(deny(mine), [{"serverUrl": "https://gmailmcp.googleapis.com/mcp/v1"}])
        self.assertNotIn("--settings", theirs, "given Gmail, and alphaXiv goes to every project")
        self.assertTrue(any(a.startswith("mcp_servers.shop.command=") for a in connections.codex_args(self.root)))
        self.assertEqual(connections.codex_args(other), [])

        from colony import providers
        cmd = providers.get("claude").command("plants", {}, root=self.root)
        self.assertIn("--mcp-config", cmd)
        self.assertIn("gmailmcp", cmd)
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, listed, "")):
            connections.discover_personal()                 # looked up again: the person's grants stand
        self.assertEqual(connections.load()["personal"]["Gmail"]["projects"], [str(other.resolve())])

    def test_settings_takes_their_keys_and_who_has_each(self):
        page = board.settings_page(board.registry())
        self.assertIn("Connections", page)
        self.assertIn("The shop", page)
        self.assertIn("keys missing", page)
        self.assertIn("name='key_SHOP_TOKEN'", page)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        post = lambda **f: urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/connections", data=urllib.parse.urlencode(f, doseq=True).encode()))
        post(do="keys", name="shop", key_SHOP_TOKEN="abc")
        self.assertEqual(connections.keys("shop"), {"SHOP_TOKEN": "abc"})
        post(do="keys", name="shop", key_SHOP_TOKEN="")
        self.assertEqual(connections.keys("shop"), {"SHOP_TOKEN": "abc"}, "a field left empty keeps what's kept")
        post(do="grant", name="shop", project=[str(self.root)])
        self.assertIn("shop", connections.granted(self.root))
        post(do="grant", name="shop", all="1")
        self.assertEqual(connections.load()["connections"]["shop"]["projects"], connections.ALL)
        post(do="grant", name="shop")
        self.assertEqual(connections.granted(self.root), {}, "nothing ticked: no project has it")
        page = board.settings_page(board.registry())
        self.assertNotIn("abc", page, "a kept key is never shown back")
