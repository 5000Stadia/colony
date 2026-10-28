"""Restarting the machine, or shutting it down, with the projects parked first, at no token cost of colony's own.

The person, 2026-10-28: "an option to restart from a colony function, not from an agent, but token free, which does a
bit of a round Robin request to notify projects, and to ask projects to inform a response that restart is OK. So the
projects have a chance to park their stuff. Also, with a further notification that if it doesn't get parked within the
next hour that it will force a restart. Then when the function gets an all clear then the system can restart. And if
it doesn't get an all clear, it restarts in an hour anyways."

Only the person starts one (colony reboot, or Settings), never an agent. Each project whose console runs, in turn, a
minute apart: one idle with nothing running is clear as it is, unasked; one at work is asked to park, the words typed
into its console so it reads them within its turn: let what runs finish or stop it at a clean point, commit, start
nothing new, then say so with colony noted. It is parked once it says so, or once its turn has ended with nothing left
running (as closing a project reads it). All parked, the machine restarts; at the hour, regardless. Cancel stops it,
and those asked hear so. After the restart, autostart.py starts colony and recovery.rebooted brings each console back.
"""
import json
import secrets
import subprocess
import time
from pathlib import Path

from . import board, console

WITHIN = 3600                # seconds the projects have to park before the machine restarts regardless
EVERY = 60                   # seconds between one project asked and the next
SETTLE = 2                   # looks idle with nothing running, a minute after the ask, that count as parked


def _path():
    return board.home() / "machine.json"


def state():
    """The restart under way, or None: {mode, started, by, queue, asked, parked, ...}."""
    try:
        s = json.loads(_path().read_text())
    except (OSError, ValueError):
        return None
    return None if s.get("done") and not s.get("failed") else s


def _save(s):
    board.home().mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(s, indent=1))


def clock(t):
    return time.strftime("%I:%M %p", time.localtime(t)).lstrip("0")


def start(off=False, within=WITHIN, now=None):
    """Begin: every project whose console runs is in the queue, in the board's order. The one under way, if any."""
    now = now or time.time()
    s = state()
    if s and not s.get("failed"):
        return s
    queue = [str(p) for p in board.projects() if p.exists() and console.running(console.session_name(p))]
    s = {"mode": "off" if off else "restart", "started": now, "by": now + within, "queue": queue, "asked": {},
         "asked_when": {}, "asked_at": 0, "parked": {}, "looks": {}}
    _save(s)
    return s


def cancel():
    """Stop it: each project asked hears that it can carry on. What was cancelled, or None."""
    s = state()
    if not s:
        return None
    for root in s["asked"]:
        if Path(root).exists():
            board.add_note(Path(root), None, "The person cancelled the machine's restart: carry on as before.",
                           author="colony", quiet=True)
    _path().unlink()
    return s


def words(s, nid):
    """What a project at work is asked."""
    by, off = clock(s["by"]), s["mode"] == "off"
    what, then = ("shutting this machine down", "shuts down") if off else ("restarting this machine", "restarts")
    return (f"[colony] The person is {what}, by {by} at the latest. Park your work: let what is running finish, or stop "
            f"it at a clean point; commit what works; start nothing new. Then say it is safe: colony noted {nid} "
            f"\"parked: where things stand\". If it isn't parked by {by}, the machine {then} anyway and whatever is still "
            f"running stops. Afterwards colony brings this console back, in this conversation.")


def _at_rest(root):
    """Nothing of the project's in flight: its turn ended, its program runs nothing apart, no command or helper left
    working behind its console."""
    from . import chat, lingering
    name = console.session_name(root)
    if not console.running(name):
        return True
    snap = console.snapshot(root, lines=4)
    if snap["state"] != "idle" or not board.at_rest(root) or chat.helpers(snap):
        return False
    try:
        return not lingering.commands(root, name)
    except Exception:
        return True


def _power(mode):
    """Restart the machine, or shut it down: its own systemctl, then sudo where the person allows it unasked. The
    error, or None."""
    cmd = ["systemctl", "poweroff" if mode == "off" else "reboot"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        r = subprocess.run(["sudo", "-n", *cmd], capture_output=True, text=True)
    return (r.stderr or r.stdout or "it refused").strip()[:300] if r.returncode else None


def tick(now=None):
    """Called by the watcher: the next project asked, who is parked counted, and the machine restarted once all are or
    the hour is up. What was done, or None."""
    s = state()
    if not s or s.get("failed"):
        return None
    now = now or time.time()
    on, did = {str(p): p for p in board.projects()}, None
    for r in s["queue"]:
        if r in s["parked"]:
            continue
        p = on.get(r)
        if p is None or not console.running(console.session_name(p)):
            s["parked"][r] = "not running"
            continue
        nid = s["asked"].get(r)
        if not nid:
            if _at_rest(p):
                s["parked"][r] = "idle, nothing running"
            elif now - s["asked_at"] >= EVERY:
                # a note, so its answer (colony noted) is kept with it; typed at once, so an agent at work reads it within
                # its turn, and marked handed over, so its hooks don't hand it over again
                nid = "n" + secrets.token_hex(3)
                text = words(s, nid)
                board.append(p, "notes.jsonl", {"type": "note", "id": nid, "at": board.now(), "author": "colony",
                                                "anchor": None, "text": text, "kind": "park"})
                if console.type_into(console.session_name(p), text):
                    board.append(p, "notes.jsonl", {"type": "delivered", "of": nid, "at": board.now()})
                s["asked"][r], s["asked_when"][r], s["asked_at"] = nid, now, now
                did = f"asked {p.name} to park"
            continue
        note = next((n for n in board.notes(p) if n["id"] == nid), None)
        if note and note.get("addressed_at"):
            s["parked"][r] = note.get("reply") or "parked"
            continue
        settled = now - s["asked_when"].get(r, now) >= EVERY and _at_rest(p)
        s["looks"][r] = s["looks"].get(r, 0) + 1 if settled else 0
        if s["looks"][r] >= SETTLE:
            s["parked"][r] = "its turn ended with nothing running"
    clear = all(r in s["parked"] for r in s["queue"])
    if clear or now >= s["by"]:
        s.update(done=now, why="all parked" if clear else "the hour was up")
        _save(s)
        err = _power(s["mode"])
        if err:
            from . import monitor
            s["failed"] = err
            _save(s)
            monitor.queue(f"Colony couldn't {'shut down' if s['mode'] == 'off' else 'restart'} the machine ({err}); "
                          "the projects are parked. Run sudo systemctl reboot yourself.", relay=True)
        return "restarting" if not err else "failed"
    _save(s)
    return did


def line(now=None):
    """The board's line while one is under way: how many are parked, by when."""
    s = state()
    if not s:
        return ""
    if s.get("failed"):
        return f"Colony couldn't {'shut down' if s['mode'] == 'off' else 'restart'} the machine: {s['failed']}"
    what = "Shutting down" if s["mode"] == "off" else "Restarting"
    done = sum(r in s["parked"] for r in s["queue"])
    return (f"{what} the machine: {done} of {len(s['queue'])} projects parked · by {clock(s['by'])} at the latest")
