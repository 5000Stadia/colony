"""Lingering: background work a project left running that has gone quiet is named to it once, by rule, with no model.

A command an agent put in the background, or a helper it started, can outlive its use: a waiter whose run ended
elsewhere, a test server, a helper stuck on nothing. Age says nothing about it (Bookflow's suites run 4-6 hours, a
builder helper 10), so colony watches what each does. A command is quiet while it and everything it started gain no
CPU time and write nothing (to its output file, a pipe, anywhere: the kernel counts each process's writes); a
helper is quiet while its own transcript is not written and no command of the project's is stirring. Once something has been quiet two hours and the agent has sat idle half an hour (with
nothing typed in its box and no one looking at it), colony types one line into its console naming what is quiet,
so the agent stops what is done and leaves what is meant to stay. Each thing is named once, ever: that memory is the
project's lingering.jsonl. Reading costs no tokens: /proc and file times, every five minutes.
PROVIDER: each program's commands(pane) and helpers: Claude Code's shell snapshot, its rows under the box and their
transcripts; Codex's app-server children (on a pipe or a terminal of their own) and its thread store.
"""
import os
import re
import time
from pathlib import Path

from . import board, console

QUIET = 2 * 3600             # seconds without CPU or output before it is named
IDLE = 30 * 60               # the agent idle this long first: one waiting on a run it is watching is not interrupted
EVERY = 5 * 60               # how often it looks
STIR = 2.0                   # CPU seconds gained between looks that count as work (a resting server takes less)
_seen, _idle, _looked = {}, {}, [0.0]


def _stat(pid):
    """(start time, CPU seconds so far) of a process, from /proc, or None once it is gone."""
    try:
        f = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(f[19]), (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


def _output(pid):
    """The file its output goes to, if a file: Claude Code writes a background command's to its task's file."""
    try:
        out = os.readlink(f"/proc/{pid}/fd/1")
        return out if os.path.isfile(out) else None
    except OSError:
        return None


def _pane(name):
    import subprocess
    pane = subprocess.run(["tmux", "display", "-p", "-t", name, "#{pane_pid}"], capture_output=True, text=True).stdout.strip()
    return int(pane) if pane.isdigit() else None


def _cmdline(pid):
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").replace("\0", " ").strip()
    except OSError:
        return ""


def _written(pid):
    """Bytes a process has written so far, wherever to, from /proc: a pipe's progress shows without reading it."""
    try:
        return int(next(l for l in Path(f"/proc/{pid}/io").read_text().splitlines() if l.startswith("wchar:")).split()[1])
    except (OSError, StopIteration, ValueError, IndexError):
        return 0


def _entry(pid, what):
    st = _stat(pid)
    if not st:
        return None
    family = console._descendants(pid)
    cpu = sum((_stat(p) or (0, 0))[1] for p in family)
    return {"pid": pid, "start": st[0], "cpu": cpu, "wrote": sum(_written(p) for p in family), "what": what, "out": _output(pid)}


def commands(root, name):
    """The background commands a console runs: [{pid, start, cpu, what, out}], each with all it started, as its
    program runs them (PROVIDER: commands(pane))."""
    from . import providers
    pane = _pane(name)
    p = providers.of(root)
    return [c for c in (p.commands(pane) if pane else []) if c]


def claude_commands(pane):
    """Claude Code runs each command in a bash that sources its shell snapshot and evals the command."""
    out = []
    for pid in console._descendants(pane):
        cmd = _cmdline(pid)
        if "/shell-snapshots/snapshot-" in cmd:
            m = re.search(r"eval '(.*?)' < /dev/null && pwd", cmd, re.S) or re.search(r"eval '(.*)", cmd, re.S)
            out.append(_entry(pid, (m.group(1).replace("'\"'\"'", "'") if m else cmd).strip()))
    return out


def codex_commands(pane):
    """Codex's app-server runs each command as its own child, in a session of its own with nothing on its input;
    or on a terminal of its own (a command run with one); its servers (MCP) it talks to through their input. Their
    output is a pipe the app-server reads: not read here, since that would take it, but the bytes written count."""
    out = []
    try:
        own_tty = os.readlink(f"/proc/{pane}/fd/0")
    except OSError:
        own_tty = None
    for server in (p for p in console._descendants(pane) if re.match(r"(\S*/)?codex app-server\b", _cmdline(p))):
        for pid in console._descendants(server)[1:]:
            try:
                f = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                own, parent = int(f[3]) == pid, int(f[1]) == server       # its session's leader, the server's child
                given = os.readlink(f"/proc/{pid}/fd/0")
                quiet_input = given == "/dev/null" or (given.startswith("/dev/pts/") and given != own_tty)
            except (OSError, ValueError, IndexError):
                continue
            if own and parent and quiet_input:
                out.append(_entry(pid, _cmdline(pid)))           # its output, a pipe, is no file: None
    return out


def _mtime(path):
    try:
        return os.path.getmtime(path) if path else 0
    except OSError:
        return 0


def quiet(root, name, now, snap):
    """What this console runs behind that has been quiet QUIET or longer: [{key, kind, what, since}]. A command first
    seen (the board just started) counts as stirring: it is watched a full QUIET before it can be named."""
    from . import chat, context
    found, stirring = [], False
    for c in commands(root, name):
        key = f"cmd:{c['pid']}:{c['start']}"
        was, wrote = _seen.get((name, key)), (_mtime(c["out"]), c.get("wrote", 0))
        if not was or c["cpu"] - was["cpu"] >= STIR or wrote != was["wrote"]:
            _seen[(name, key)] = was = {"cpu": c["cpu"], "wrote": wrote, "since": now}
            stirring = True
        was["cpu"] = c["cpu"]
        if now - was["since"] >= QUIET:
            task = Path(c["out"]).stem if c["out"] else ""
            found.append({"key": key, "kind": "command", "what": c["what"], "task": task, "since": was["since"]})
    helping = chat.helpers(snap)
    if helping and not stirring:
        # each helper's own transcript where its program names it (Codex), else all of the session's (Claude Code's)
        s = context.session(root)
        subs = Path(s["path"]).with_suffix("") / "subagents" if s and s.get("path") else None
        files = list(subs.glob("agent-*.jsonl")) if subs and subs.is_dir() else []
        every = max((_mtime(f) for f in files), default=0)
        for h in helping:
            last = _mtime(h["transcript"]) if h.get("transcript") else every
            if last and now - last >= QUIET:
                found.append({"key": f"helper:{h['name']}:{h['what']}", "kind": "helper", "what": h["what"] or h["name"],
                              "name": h["name"], "since": last})
    return found


def told(root):
    return {r["key"] for r in board.read(root, "lingering.jsonl") if r.get("key")}


def line(items, now):
    """The one line typed into its console."""
    ago = lambda t: board.took(now - t)

    def one(i):
        if i["kind"] == "helper":
            return f"helper {i['name']} \"{board.short(i['what'], 60)}\" (its transcript silent {ago(i['since'])})"
        task = f", task {i['task']}" if i["task"] else ""
        return f"`{board.short(' '.join(i['what'].split()), 80)}` (no CPU or output for {ago(i['since'])}{task})"
    return ("[colony] Still running in the background and quiet: " + "; ".join(one(i) for i in items) + ". Stop what is "
            "done or forgotten; if one is meant to stay or waits on work still going elsewhere, leave it: it won't be "
            "named again.")


def tick(now=None):
    """Called by the watcher: name, once, the quiet background work of each project idle long enough."""
    from . import providers, usage
    now = time.time() if now is None else now
    if now - _looked[0] < EVERY:
        return
    _looked[0] = now
    for root in board.projects():
        if not root.exists():
            continue
        name, key = console.session_name(root), str(root)
        try:
            if not console.running(name):
                _idle.pop(key, None)
                continue
            snap = console.snapshot(root, lines=4)
            items, state = quiet(root, name, now, snap), snap["state"]
        except Exception:
            continue
        if state != "idle" or console.drafting(name) or console.attached(name):
            _idle.pop(key, None)
            continue
        first = _idle.setdefault(key, now)
        if now - first < IDLE or usage.paused().get(providers.key(providers.of(root))):
            continue
        fresh = [i for i in items if i["key"] not in told(root)]
        if fresh and console.type_into(name, line(fresh, now)):
            for i in fresh:
                board.append(root, "lingering.jsonl", {"at": board.now(), "key": i["key"], "kind": i["kind"], "what": i["what"][:200]})
