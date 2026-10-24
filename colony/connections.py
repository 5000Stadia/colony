"""Connections: the outside services (an API, a mailbox) a project's agent reaches, granted project by project.

Each connection is an MCP server colony starts for the agent on this machine, with no gateway, daemon or account of
colony's own between them: the project's console is launched with only the connections the person granted it
(claude_args, codex_args). The person's keys live in one file per connection, readable only by them
(~/.config/colony/connections/NAME.env), entered in Settings and never written into a conversation, a command or git;
`colony connect run` reads them as it starts the server (run).

What leaves the person's hands waits on them: a tool the connection holds (one that sends, lists or spends) is not run
when the agent calls it. It becomes a gate on the board with what the agent asked for, and only the person's tap there
runs it (release); its outcome reaches the agent as a note. The agent can't release its own: an answer recorded from
the command line never runs it.

The person's own connectors from their Claude sign-in (their Gmail, Drive) reach every Claude Code console unless
told otherwise; each is granted here the same way, and a console is started denying the ones its project wasn't given.
PROVIDER: only Claude Code brings those in; Codex's ChatGPT apps are not yet sorted per project.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from . import board

HOLD_YES, HOLD_NO = "Do it", "Don't"
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
ALL = "all"


def path():
    return board.home() / "connections.json"


def keys_dir():
    return board.home() / "connections"


@contextmanager
def _lock():
    path().parent.mkdir(parents=True, exist_ok=True)
    with path().with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def load():
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("connections", {})
    data.setdefault("personal", {})
    return data


def _save(data):
    tmp = path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(path())


def change(fn):
    """fn(data) under the lock, the result written whole."""
    with _lock():
        data = load()
        out = fn(data)
        _save(data)
        return out


def _key(root):
    return str(Path(root).resolve())


def _granted(entry, root):
    to = entry.get("projects") or []
    return to == ALL or (root is not None and _key(root) in to)


# --- the person's keys -------------------------------------------------------------------------------------------

def keys_path(name):
    return keys_dir() / f"{name}.env"


def keys(name):
    """The connection's keys, as the person entered them."""
    out = {}
    try:
        for line in keys_path(name).read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip():
                out[k.strip()] = v
    except OSError:
        pass
    return out


def set_key(name, key, value):
    """One key kept, in a file only the person can read; an empty value removes it."""
    have = keys(name)
    if value.strip():
        have[key] = value.strip()
    else:
        have.pop(key, None)
    keys_dir().mkdir(parents=True, exist_ok=True)
    os.chmod(keys_dir(), 0o700)
    p = keys_path(name)
    fd = os.open(p.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(f"{k}={v}\n" for k, v in sorted(have.items())))
    p.with_suffix(".tmp").replace(p)


def missing(name, c=None):
    """The keys it needs that the person hasn't entered yet."""
    c = c or load()["connections"].get(name) or {}
    have = keys(name)
    return [k for k in c.get("keys") or () if not have.get(k)]


# --- what a connection is and who has it ---------------------------------------------------------------------------

def add(name, title, command, *, needs=(), hold=(), tools=None, about=""):
    """A connection: the MCP server to start (its command, pinned), the keys it needs, the tools it holds for the
    person's tap, and (optionally) the only tools it offers. Granted to no project until the person grants it."""
    if not NAME.fullmatch(name):
        raise ValueError("A connection's name is lowercase letters, digits, - or _, as ebay or snackstand-mail.")
    if not command:
        raise ValueError("Give the command that starts its MCP server.")

    def put(data):
        was = data["connections"].get(name) or {}
        data["connections"][name] = {"title": title or name, "command": list(command), "keys": list(needs),
                                     "hold": list(hold), "tools": list(tools) if tools else None, "about": about,
                                     "projects": was.get("projects") or []}
        return data["connections"][name]
    return change(put)


def remove(name):
    change(lambda data: data["connections"].pop(name, None))


def grant(name, root, on=True, personal=False):
    """Give a project a connection (or take it back); root "all" gives it to every project."""
    def put(data):
        group = data["personal" if personal else "connections"]
        if name not in group:
            raise KeyError(name)
        entry = group[name]
        to = entry.get("projects") or []
        if root == ALL:
            entry["projects"] = ALL if on else []
            return
        if to == ALL:
            to = [_key(p) for p in board.projects(board.registry()) if p.exists()]
        to = [p for p in to if p != _key(root)] + ([_key(root)] if on else [])
        entry["projects"] = sorted(to)
    change(put)


def set_grants(name, roots, personal=False):
    """The projects given a connection, set whole from the person's ticks in Settings: a list, or "all"."""
    def put(data):
        data["personal" if personal else "connections"][name]["projects"] = (
            ALL if roots == ALL else sorted({_key(r) for r in roots}))
    change(put)


def granted(root):
    """The connections this project's agent is started with: given to it, with every key it needs entered."""
    return {n: c for n, c in sorted(load()["connections"].items()) if _granted(c, root) and not missing(n, c)}


def held(name, tool):
    return tool in ((load()["connections"].get(name) or {}).get("hold") or ())


# --- the person's own connectors (PROVIDER: Claude Code) ------------------------------------------------------------

LISTED = re.compile(r"^claude\.ai (?P<name>.+?): (?P<url>https://\S+) - ")


def discover_personal():
    """The connectors the person's Claude sign-in brings, as Claude Code lists them; a new one is granted to no
    project. Slow (it checks each one), so the board calls it in the background."""
    try:
        out = subprocess.run(["claude", "mcp", "list"], capture_output=True, text=True, timeout=90,
                             cwd=str(board.home())).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = {m["name"]: m["url"] for m in map(LISTED.match, out.splitlines()) if m}

    def put(data):
        for name, url in found.items():
            entry = data["personal"].setdefault(name, {"projects": []})
            entry["url"] = url
        return found
    return change(put)


_looking = threading.Lock()


def refresh_personal(every=6 * 3600):
    """discover_personal in the background, at most every few hours."""
    import time
    stamp = board.home() / "connections-looked"
    try:
        if time.time() - stamp.stat().st_mtime < every:
            return
    except OSError:
        pass
    if not _looking.acquire(blocking=False):
        return

    def go():
        try:
            if discover_personal() is not None:
                stamp.parent.mkdir(parents=True, exist_ok=True)
                stamp.touch()
        finally:
            _looking.release()
    threading.Thread(target=go, daemon=True).start()


def denied(root):
    """The person's own connectors this console is started without: every one not granted to its project."""
    return [{"serverUrl": c["url"]} for n, c in sorted(load()["personal"].items())
            if c.get("url") and not _granted(c, root)]


# --- starting a console with them ----------------------------------------------------------------------------------

def _server(name, root):
    from .providers import own_module
    return own_module("colony.connections") + [name, str(Path(root).resolve())]


def claude_args(root):
    """Claude Code's launch flags: the granted connections as MCP servers, and the person's own connectors this
    project wasn't given denied."""
    out = []
    mine = granted(root) if root else {}
    if mine:
        servers = {n: {"command": _server(n, root)[0], "args": _server(n, root)[1:]} for n in mine}
        out += ["--mcp-config", json.dumps({"mcpServers": servers}, sort_keys=True)]
    deny = denied(root)
    if deny:
        out += ["--settings", json.dumps({"deniedMcpServers": deny}, sort_keys=True)]
    return out


def codex_args(root):
    """Codex's launch overrides: the granted connections as MCP servers."""
    from .codex_remote import toml
    out = []
    for n in (granted(root) if root else {}):
        cmd = _server(n, root)
        out += ["-c", f"mcp_servers.{n}.command={toml(cmd[0])}", "-c", f"mcp_servers.{n}.args={toml(cmd[1:])}",
                "-c", f"mcp_servers.{n}.env_vars={toml(['COLONY_BOARD_HOME'])}"]   # Codex passes on only what it's told
    return out


# --- running one: the agent's side ---------------------------------------------------------------------------------

def _start(name):
    c = load()["connections"][name]
    keys_dir().mkdir(parents=True, exist_ok=True)
    return c, subprocess.Popen(c["command"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               env={**os.environ, **keys(name)}, cwd=str(keys_dir()))


def _hold(root, name, c, tool, arguments):
    """A held call put to the person: the gate shows what the agent asked for; their tap runs it."""
    shown = json.dumps(arguments or {}, indent=1, ensure_ascii=False)
    gate = board.add_gate(root, f"{c['title']}: {tool}?", None,
                          f"The agent asks to run {tool} on {c['title']} with:\n{shown}\n\n"
                          f"\"{HOLD_YES}\" runs it now; its outcome reaches the agent as a note.",
                          consultation=None, points=None, options=[HOLD_YES, HOLD_NO],
                          hold={"connection": name, "tool": tool, "arguments": arguments or {}})
    return (f"Held for the person: {c['title']} {tool} runs only when they tap \"{HOLD_YES}\" on gate {gate['id']}. "
            f"Its outcome, or their no, reaches you as a note; go on with other work meanwhile.")


def run(name, root):
    """The MCP server between the agent and the service: started with the person's keys, passing everything through
    except the tools it doesn't offer (left out of its list, refused if called) and the ones it holds (put to the
    person instead of run)."""
    c, proc = _start(name)
    offer = set(c["tools"]) if c.get("tools") else None
    hold = set(c.get("hold") or ())
    lists, out_lock = set(), threading.Lock()
    out = sys.stdout.buffer

    def send(msg):
        with out_lock:
            out.write((json.dumps(msg) if isinstance(msg, dict) else msg.decode().rstrip("\n")).encode() + b"\n")
            out.flush()

    def down():
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                send(line)
                continue
            if offer is not None and isinstance(msg, dict) and msg.get("id") in lists and "result" in msg:
                lists.discard(msg["id"])
                msg["result"]["tools"] = [t for t in msg["result"].get("tools") or [] if t.get("name") in offer]
            send(msg)
        os._exit(0)
    threading.Thread(target=down, daemon=True).start()

    for line in sys.stdin.buffer:
        try:
            msg = json.loads(line)
        except ValueError:
            msg = None
        if isinstance(msg, dict) and msg.get("method") == "tools/list":
            lists.add(msg.get("id"))
        elif isinstance(msg, dict) and msg.get("method") == "tools/call":
            tool = (msg.get("params") or {}).get("name")
            if offer is not None and tool not in offer:
                send({"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32602, "message": f"No tool {tool}."}})
                continue
            if tool in hold:
                text = _hold(root, name, c, tool, (msg.get("params") or {}).get("arguments"))
                send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"content": [{"type": "text", "text": text}]}})
                continue
        proc.stdin.write(line if line.endswith(b"\n") else line + b"\n")
        proc.stdin.flush()
    proc.stdin.close()
    proc.wait()


# --- running one: the person's tap ---------------------------------------------------------------------------------

def call(name, tool, arguments, timeout=180):
    """One tool call made directly (the person's tap on a held one): the server started, greeted, asked, stopped.
    Returns (text, failed)."""
    c, proc = _start(name)
    reply = {}

    def ask(msg):
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()

    def read(want):
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == want and ("result" in msg or "error" in msg):
                return msg
        return {"error": {"message": "the server stopped without answering"}}

    def go():
        ask({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "colony", "version": "1"}}})
        read(1)
        ask({"jsonrpc": "2.0", "method": "notifications/initialized"})
        ask({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}})
        reply.update(read(2))
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(timeout)
    proc.kill()
    proc.wait()
    for pipe in (proc.stdin, proc.stdout):
        try:
            pipe.close()
        except OSError:
            pass
    if not reply:
        return f"no answer within {timeout} seconds", True
    if "error" in reply:
        return str(reply["error"].get("message") or reply["error"]), True
    res = reply["result"]
    text = "\n".join(p.get("text", "") for p in res.get("content") or [] if p.get("type") == "text").strip()
    return text or json.dumps(res.get("structuredContent") or res)[:4000], bool(res.get("isError"))


def release(root, gate):
    """The person tapped "Do it" on a held call: run it, and tell the agent how it went."""
    h = gate["hold"]
    title = (load()["connections"].get(h["connection"]) or {}).get("title", h["connection"])
    try:
        text, failed = call(h["connection"], h["tool"], h["arguments"])
    except (OSError, KeyError) as err:
        text, failed = f"{err.__class__.__name__}: {err}", True
    board.add_note(root, {"gate": gate["id"], "item": gate.get("item")},
                   f"{title} {h['tool']}, which the person released on gate {gate['id']}, "
                   + ("failed: " if failed else "ran: ") + text[:4000], author="colony")


def answered(root, gate, text):
    """After the person's answer from the board: a held call runs on their "Do it", in the background."""
    if gate.get("hold") and text.strip() == HOLD_YES:
        threading.Thread(target=release, args=(root, gate), daemon=True).start()


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
