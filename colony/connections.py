"""Connections: the outside services (an API, a mailbox) a project's agent reaches, set once for the colony and
tuned project by project.

Each connection is an MCP server colony starts for the agent on this machine, with no gateway, daemon or account of
colony's own between them. It has its colony-wide settings: whether projects have it (every project, new ones too,
unless switched off there), the person's keys (one file per connection, readable only by them, entered on the
Connections page and never written into a conversation, a command or git), and which of its tools wait for the
person's tap. A project inherits those, and can switch the connection off or give it its own: its own account's keys
and its own holds (SnackStand's own mailbox where every other project shares the person's). A connection with no
server is only keys, for a project's own script (an eBay search): `colony connect --with NAME -- COMMAND` runs it
with them, and nothing it does waits for the person. A console starts with
the connections its project has (claude_args, codex_args); `colony connect run` reads the keys as it starts the
server (run).

What leaves the person's hands waits on them: a held tool is not run when the agent calls it. It becomes a gate on
the board with what the agent asked for, and only the person's tap there runs it (release); its outcome reaches the
agent as a note, and an answer recorded from the command line never runs it. Unless the person lists the holds
themselves, every tool waits except those the server marks read-only (MCP's readOnlyHint), and one it never listed
waits too.

The person's own connectors from their Claude sign-in (their Gmail, Drive) are switched on and off the same way, and
a console is started denying those its project doesn't have; Claude calls them itself, so they can't be held.
PROVIDER: only Claude Code brings those in; Codex's ChatGPT apps are not yet sorted per project.
"""
import fcntl
import hashlib
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


def _shape(entry):
    """An entry in today's shape: `on` for every project without its own choice, `projects` each project's own.
    (First kept as a list of projects, or "all".)"""
    to = entry.get("projects")
    if to == ALL:
        entry.update(on=True, projects={})
    elif isinstance(to, list):
        entry.update(on=False, projects={r: {"on": True} for r in to})
    entry.setdefault("on", True)
    entry.setdefault("projects", {})
    return entry


def load():
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        data = {}
    for group in ("connections", "personal"):
        data.setdefault(group, {})
        for entry in data[group].values():
            _shape(entry)
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


def _own(entry, root):
    return (entry.get("projects") or {}).get(_key(root), {}) if root is not None else {}


def is_on(entry, root):
    """Whether a project has it: its own choice, else the colony-wide one (which a console of no project, the
    steward's, also takes)."""
    mine = _own(entry, root)
    return mine["on"] if "on" in mine else bool(entry.get("on", True))


def has_own(entry, root):
    return bool(_own(entry, root).get("own"))


# --- the person's keys -------------------------------------------------------------------------------------------

def keys_path(name, root=None):
    """The colony-wide keys, or a project's own (named by its folder, never by what's in it)."""
    if root is None:
        return keys_dir() / f"{name}.env"
    return keys_dir() / name / (hashlib.sha256(_key(root).encode()).hexdigest()[:16] + ".env")


def _whose(name, root):
    """Whose keys a project's server starts with: its own, if it has its own settings, else the colony-wide ones."""
    c = load()["connections"].get(name) or {}
    return root if root is not None and has_own(c, root) else None


def keys(name, root=None):
    """The keys kept at that level, as the person entered them."""
    out = {}
    try:
        for line in keys_path(name, root).read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip():
                out[k.strip()] = v
    except OSError:
        pass
    return out


def set_key(name, key, value, root=None):
    """One key kept, in a file only the person can read; an empty value removes it."""
    have = keys(name, root)
    if value.strip():
        have[key] = value.strip()
    else:
        have.pop(key, None)
    p = keys_path(name, root)
    for d in {keys_dir(), p.parent}:
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    fd = os.open(p.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(f"{k}={v}\n" for k, v in sorted(have.items())))
    p.with_suffix(".tmp").replace(p)


def missing(name, c=None, root=None):
    """The keys it needs that aren't entered yet, for this project (its own keys, if it has its own settings)."""
    c = c or load()["connections"].get(name) or {}
    have = keys(name, root if root is not None and has_own(c, root) else None)
    return [k for k in c.get("keys") or () if not have.get(k)]


# --- what a connection is and who has it ---------------------------------------------------------------------------

def add(name, title, command, *, needs=(), hold=None, tools=None, about="", service=None, by=None):
    """A connection: the MCP server to start (its command, pinned), the keys it needs, the tools that wait for the
    person's tap (None: all but those the server marks read-only), and optionally the only tools it offers. Every
    project has it once its keys are entered, unless the person switches it off there. by: the project that asked for
    it, whose work the steward's audit weighs it against (audit)."""
    if not NAME.fullmatch(name):
        raise ValueError("A connection's name is lowercase letters, digits, - or _, as ebay or snackstand-mail.")
    if not command and not needs:
        raise ValueError("Give the command that starts its MCP server, or the keys a script of the project's uses.")

    def put(data):
        was = data["connections"].get(name) or {}
        data["connections"][name] = {"title": title or name, "command": list(command), "keys": list(needs),
                                     "hold": list(hold) if hold else None, "tools": list(tools) if tools else None,
                                     "about": about, "service": slug(service or name),
                                     # a new command runs nowhere until the person has seen it (OpenClaw's lesson:
                                     # what an agent installs is the attack surface); the same command stays approved
                                     "approved": bool(was.get("approved")) and list(was.get("command") or []) == list(command),
                                     "on": was.get("on", True), "projects": was.get("projects") or {},
                                     "seen": was.get("seen") or {}, "audit": was.get("audit"),
                                     "by": str(by) if by else was.get("by")}
        return data["connections"][name]
    return change(put)


def approve(name):
    """The person's yes to a connection an agent added: its command may run, once the steward's audit has run and not
    refused it."""
    from . import audit
    a = audit.current(load()["connections"][name])
    if not a or a.get("state") == "running" or audit.refused(a):
        raise ValueError(f"{name} can't be approved {'while its audit runs' if not audit.refused(a) else 'after its audit refused it'}.")
    configure(name, approved=True)


def remove(name):
    change(lambda data: data["connections"].pop(name, None))


def configure(name, root=None, *, personal=False, **what):
    """Change a connection's settings, colony-wide (root None) or for one project. on: has it (None, at a project:
    back to the colony-wide choice); own: its own keys and holds; hold: the tools that wait for the tap (None: by
    the server's own marks)."""
    def put(data):
        entry = data["personal" if personal else "connections"][name]
        target = entry if root is None else entry["projects"].setdefault(_key(root), {})
        for k, v in what.items():
            if v is None and root is not None and k == "on":
                target.pop("on", None)
            else:
                target[k] = v
        if root is not None and not target:
            entry["projects"].pop(_key(root), None)
    change(put)


def pick(root, ticked, listed):
    """The person's ticks as a project is added: each listed service left unticked is off there."""
    for svc in set(listed) - set(ticked):
        if svc in services():
            choose_path(svc, root, "off")


def granted(root):
    """The connections this project's agent is started with: approved by the person, on there, with every key it
    needs entered."""
    return {n: c for n, c in sorted(load()["connections"].items())
            if c.get("approved", True) and is_on(c, root) and not missing(n, c, root)}


def servers(root):
    """The granted connections a console starts as MCP servers: all but those that are only keys."""
    return {n: c for n, c in granted(root).items() if c.get("command")}


def holds(c, root):
    """The tools a project's calls wait on the person for: its own list, else the colony-wide one; None means by the
    server's own marks."""
    mine = _own(c, root)
    if mine.get("own") and "hold" in mine:
        return mine["hold"]
    return c.get("hold")


# --- the person's own connectors (PROVIDER: Claude Code) ------------------------------------------------------------

LISTED = re.compile(r"^claude\.ai (?P<name>.+?): (?P<url>https://\S+) - ")


def discover_personal():
    """The connectors the person's Claude sign-in brings, as Claude Code lists them; a new one is on in every project
    until switched off. Slow (it checks each one), so the board calls it in the background."""
    try:
        out = subprocess.run(["claude", "mcp", "list"], capture_output=True, text=True, timeout=90,
                             cwd=str(board.home())).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = {m["name"]: m["url"] for m in map(LISTED.match, out.splitlines()) if m}

    def put(data):
        for name, url in found.items():
            entry = data["personal"].setdefault(name, {"on": True, "projects": {}})
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
    """The person's own connectors this console is started without: every one off in its project, and every one whose
    service the project reaches through colony instead (one path to a service, so a held send has no unheld twin)."""
    data = load()
    through_colony = {c.get("service") or n for n, c in servers(root).items()} if root is not None else set()
    return [{"serverUrl": c["url"]} for n, c in sorted(data["personal"].items())
            if c.get("url") and (not is_on(c, root) or personal_service(n, c) in through_colony)]


# --- services: one card for each, whichever sources reach it ---------------------------------------------------------

# the person's own connectors, known by where they are served
SERVED = {"gmailmcp.googleapis.com": "gmail", "drivemcp.googleapis.com": "google-drive",
          "calendarmcp.googleapis.com": "google-calendar"}


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def personal_service(name, entry):
    host = (entry.get("url") or "").split("/")[2:3]
    return SERVED.get(host[0] if host else "", slug(name))


def services():
    """Each service with its sources: colony's connection and the person's own connector, either or both."""
    data, out = load(), {}
    for n, c in data["connections"].items():
        out.setdefault(c.get("service") or n, {"title": c["title"]})["colony"] = n
    for n, c in data["personal"].items():
        s = out.setdefault(personal_service(n, c), {"title": n})
        s["claude"] = n
    return dict(sorted(out.items(), key=lambda kv: kv[1]["title"].lower()))


def path_for(svc, root):
    """How a project reaches a service: "colony", "claude" (the person's own connector, in a Claude Code project
    only) or None. colony's wins where both are on, being the one that can wait for the person's tap."""
    s, data = services().get(svc) or {}, load()
    if s.get("colony") and s["colony"] in granted(root):
        return "colony"
    if s.get("claude") and is_on(data["personal"][s["claude"]], root):
        from . import providers
        if root is None or providers.of(root) is providers.get("claude"):
            return "claude"
    return None


def path_for_choice(s, root):
    """What a service's radio shows as chosen: in a project with no choice of its own, "inherit"."""
    data = load()
    if root is not None and not any(
            "on" in _own(data[g].get(s.get(k)) or {}, root) for g, k in (("connections", "colony"), ("personal", "claude"))):
        return "inherit"
    way = path_for(s["id"], root)
    if way is None and s.get("colony") and is_on(data["connections"][s["colony"]], root):
        way = "colony"                               # chosen, waiting for its keys or the person's approval
    return way or "off"


def choose_path(svc, root, how):
    """The person's choice for a service, colony-wide (root None) or in one project: "colony", "claude", "off", or
    (in a project) "inherit"."""
    s = services()[svc]
    inherit = how == "inherit"
    if s.get("colony"):
        configure(s["colony"], root, on=None if inherit else how == "colony")
    if s.get("claude"):
        configure(s["claude"], root, personal=True, on=None if inherit else how in ("claude", "colony"))


# --- starting a console with them ----------------------------------------------------------------------------------

def _server(name, root):
    from .providers import own_module
    return own_module("colony.connections") + [name, str(Path(root).resolve())]


def claude_args(root):
    """Claude Code's launch flags: the granted connections as MCP servers, and the person's own connectors this
    project wasn't given denied."""
    out = []
    mine = servers(root) if root else {}
    if mine:
        started = {n: {"command": _server(n, root)[0], "args": _server(n, root)[1:]} for n in mine}
        out += ["--mcp-config", json.dumps({"mcpServers": started}, sort_keys=True)]
    deny = denied(root)
    if deny:
        out += ["--settings", json.dumps({"deniedMcpServers": deny}, sort_keys=True)]
    return out


def codex_args(root):
    """Codex's launch overrides: the granted connections as MCP servers."""
    from .codex_remote import toml
    out = []
    for n in (servers(root) if root else {}):
        cmd = _server(n, root)
        out += ["-c", f"mcp_servers.{n}.command={toml(cmd[0])}", "-c", f"mcp_servers.{n}.args={toml(cmd[1:])}",
                "-c", f"mcp_servers.{n}.env_vars={toml(['COLONY_BOARD_HOME'])}"]   # Codex passes on only what it's told
    return out


# --- running one: the agent's side ---------------------------------------------------------------------------------

PLAIN = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TZ", "SHELL", "TERM",
         "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "NODE_PATH", "VIRTUAL_ENV")


def _start(name, root):
    """The server, with a plain environment and its own keys: none of the sign-ins and tokens the agent's program
    runs with."""
    c = load()["connections"][name]
    keys_dir().mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k in PLAIN}
    return c, subprocess.Popen(c["command"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               env={**env, **keys(name, _whose(name, root))}, cwd=str(keys_dir()))


def with_keys(name, root, command):
    """A project's own script run with a keys-only connection's keys in its environment, its output passed on with
    them taken out. A server's keys reach only its server, so what it holds still waits for the person."""
    c = granted(root).get(name)
    if not c:
        raise ValueError(f"{name} isn't here: approved by the person, on in this project, with its keys entered "
                         f"(colony connect lists what is).")
    if c.get("command"):
        raise ValueError(f"{name} is reached through its own tools; its keys go only to its server.")
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                            env={**os.environ, **keys(name, _whose(name, root))})
    with proc.stdout:
        for line in proc.stdout:
            sys.stdout.write(redact(name, root, line))
            sys.stdout.flush()
    return proc.wait()


def redact(name, root, text):
    """What a service said, with its keys taken out before it reaches the agent."""
    for v in sorted(keys(name, _whose(name, root)).values(), key=len, reverse=True):
        if len(v) >= 6:
            text = text.replace(v, "•••")
    return text


def _seen(name, tools):
    """The tools a server listed, and which it marks read-only, kept for the Connections page. A mark is the server's
    word, so it only ever tightens: a tool once listed as acting is never taken for read-only later."""
    before = (load()["connections"].get(name) or {}).get("seen") or {}
    marks = {t.get("name"): bool((t.get("annotations") or {}).get("readOnlyHint")) and before.get(t.get("name"), True)
             for t in tools if t.get("name")}

    def put(data):
        if name in data["connections"] and data["connections"][name].get("seen") != marks:
            data["connections"][name]["seen"] = marks
    if marks != (load()["connections"].get(name) or {}).get("seen"):
        change(put)
    return marks


INSTRUCTIONS = ("Colony runs this connection for the person. A tool marked (held) doesn't run when called: it waits for "
                "their tap on the board, and its outcome arrives later as a note. Don't retry a held call or reach the "
                "service another way; carry on with other work.")


def _hold(root, name, c, tool, arguments):
    """A held call put to the person: the gate shows what the agent asked for; their tap runs it. Asked again while
    it waits, it is the same gate."""
    want = {"connection": name, "tool": tool, "arguments": arguments or {}}
    gate = next((g for g in board.gates(root) if g.get("hold") == want and not g["answer"]), None)
    if gate is None:
        gate = _new_hold(root, name, c, tool, arguments)
    return (f"Held, not run: {tool} waits for the person's tap (gate {gate['id']}). Its outcome arrives as a note. "
            f"Don't retry it or go another way; carry on, or end your turn if nothing else is left.")


def _new_hold(root, name, c, tool, arguments):
    shown = json.dumps(arguments or {}, indent=1, ensure_ascii=False)
    return board.add_gate(root, f"{c['title']}: {tool}?", None,
                          f"The agent asks to run {tool} on {c['title']} with:\n{shown}\n\n"
                          f"\"{HOLD_YES}\" runs it now; its outcome reaches the agent as a note.",
                          consultation=None, points=None, options=[HOLD_YES, HOLD_NO],
                          hold={"connection": name, "tool": tool, "arguments": arguments or {}})


def run(name, root):
    """The MCP server between the agent and the service: started with the person's keys, passing everything through
    except the tools it doesn't offer (left out of its list, refused if called) and the ones it holds (put to the
    person instead of run)."""
    c, proc = _start(name, root)
    offer = set(c["tools"]) if c.get("tools") else None
    listed = holds(c, root)
    reads = {t for t, ro in (c.get("seen") or {}).items() if ro}     # by the server's marks: only its read-only run
    waits = (lambda tool: tool in listed) if listed is not None else (lambda tool: tool not in reads)
    lists, greets, out_lock = set(), set(), threading.Lock()
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
            if isinstance(msg, dict) and msg.get("id") in greets and isinstance(msg.get("result"), dict):
                greets.discard(msg["id"])
                own = msg["result"].get("instructions")
                msg["result"]["instructions"] = INSTRUCTIONS + (f"\n\n{own}" if own else "")
            if isinstance(msg, dict) and msg.get("id") in lists and isinstance(msg.get("result"), dict):
                lists.discard(msg["id"])
                every = msg["result"].get("tools") or []
                marks = _seen(name, every)
                reads.clear()
                reads.update(t for t, ro in marks.items() if ro)
                every = [t for t in every if offer is None or t.get("name") in offer]
                for tool in every:
                    if waits(tool.get("name")):
                        tool["description"] = "(held) " + (tool.get("description") or "")
                msg["result"]["tools"] = every
            send(msg)
        os._exit(0)
    threading.Thread(target=down, daemon=True).start()

    for line in sys.stdin.buffer:
        try:
            msg = json.loads(line)
        except ValueError:
            msg = None
        if isinstance(msg, dict) and msg.get("method") == "initialize":
            greets.add(msg.get("id"))
        elif isinstance(msg, dict) and msg.get("method") == "tools/list":
            lists.add(msg.get("id"))
        elif isinstance(msg, dict) and msg.get("method") == "tools/call":
            tool = (msg.get("params") or {}).get("name")
            if offer is not None and tool not in offer:
                send({"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32602, "message": f"No tool {tool}."}})
                continue
            if waits(tool):
                text = _hold(root, name, c, tool, (msg.get("params") or {}).get("arguments"))
                send({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"content": [{"type": "text", "text": text}]}})
                continue
        proc.stdin.write(line if line.endswith(b"\n") else line + b"\n")
        proc.stdin.flush()
    proc.stdin.close()
    proc.wait()


# --- running one: the person's tap ---------------------------------------------------------------------------------

def call(name, tool, arguments, root=None, timeout=180):
    """One tool call made directly (the person's tap on a held one): the server started with the project's keys,
    greeted, asked, stopped. Returns (text, failed)."""
    c, proc = _start(name, root)
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
        text, failed = call(h["connection"], h["tool"], h["arguments"], root)
    except (OSError, KeyError) as err:
        text, failed = f"{err.__class__.__name__}: {err}", True
    board.add_note(root, {"gate": gate["id"], "item": gate.get("item")},
                   f"Gate {gate['id']}: the person released {title} {h['tool']}; it "
                   + ("failed: " if failed else "ran: ") + redact(h["connection"], root, text)[:4000], author="colony")


def answered(root, gate, text):
    """After the person's answer from the board: a held call runs on their "Do it", in the background; anything else
    leaves it unrun, and the agent hears so, with their words where they said more than no."""
    if not gate.get("hold"):
        return
    if text.strip() == HOLD_YES:
        threading.Thread(target=release, args=(root, gate), daemon=True).start()
        return
    h = gate["hold"]
    title = (load()["connections"].get(h["connection"]) or {}).get("title", h["connection"])
    said = "" if text.strip() == HOLD_NO else f" They said: {text.strip()}"
    board.add_note(root, {"gate": gate["id"], "item": gate.get("item")},
                   f"Gate {gate['id']}: the person declined {title} {h['tool']}; it was not run. Don't work around it.{said}", author="colony")


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
