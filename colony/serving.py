"""HTTPS for the board and for the projects' own servers, shared through Tailscale Serve (tailscale.py): one name, this
machine's on the person's tailnet, with a real certificate; the board at https://<name>/, and a project that serves
(Bookflow's host) at https://<name>:<its port>/, proxied to its localhost port. A project asks with colony serve PORT
and binds 127.0.0.1 only (Tailscale's advice: what it serves carries the person's identity headers, which a direct
caller could otherwise forge), taking its external origin from the URL colony prints. Each local port a project serves
has an address of its own (one project served three story boards side by side, and the one address it had moved to each in
turn, so the person's link showed another board), and a port another project serves is refused, naming it (one
project's host came up on a port colony had served for another's, which crash-looped). Kept across restarts by
tailscaled itself; looked at again as the board starts and every few minutes while it runs, so certificates turned on
later are taken up with no restart; a project's goes when it leaves the board.

A link is looked at when it is tapped, and only then (tapped; the person's design, 2026-11-12): the board draws every
link to an address of this kind through its opener, carrying the project the link sits in. Its site answering, the
person is sent on. Not answering, colony starts it with the command the project gave (colony serve PORT --start), waits
while it comes up, and sends them on: no agent has a part in it. An address that is not that project's now (it moved
to another port, or another project has the address since) goes to the project's own site. What cannot be opened is
said to the person on a page. So nothing is kept about addresses given up: a link kept to one opened the next
project's site, once.

R119. The person, via Bookflow (2026-10-28): "Yes: turn it on, Colony builds shared https serving", and "We want to do
this without any friction whatsoever on install, it should just work out of the box."
"""
import json
import shutil
import socket
import subprocess
import time
import urllib.parse
from pathlib import Path

from . import board, tailscale

EVERY = 300                  # seconds between looks while the board runs: a 10 ms question to tailscaled
WAIT = 60                    # seconds a site started on a tap has to answer (one took 16)
trouble = ""                 # why Tailscale refused to serve the board, last it was asked (doctor says it)


def _path():
    return board.home() / "served.json"


def records():
    """{project folder: [{"port": a local port it serves, "https": that one's https port}, ...]} (a record from before a
    project could serve several held one row, not a list)."""
    try:
        rows = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return {k: [v] if isinstance(v, dict) else list(v) for k, v in rows.items()}


def _save(rows):
    board.home().mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(rows, indent=1))


def ready():
    """Why HTTPS can't be served here yet, or None."""
    st = tailscale.status()
    if not st["installed"]:
        return "Tailscale isn't installed"
    if not st["running"]:
        return "Tailscale isn't connected"
    if not tailscale.certs():
        return "your tailnet's HTTPS certificates are off (Tailscale admin › DNS › HTTPS Certificates)"
    return None


def _taken(port):
    """Whether something on this machine already listens on the port (it would collide with Serve's)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True


def ensure_board(port):
    """The board at https://<name>/ while remote access is on and the tailnet issues certificates. Its URL, or None.
    Port 443 is taken only when it is free or the board's already (at the port it had last): something of the person's
    own served there stays, and the board keeps its http address."""
    global trouble
    if not board.registry()["settings"].get("tailscale") or ready():
        return None
    now, reg = tailscale.served().get(443), board.registry()
    if now not in (None, port, reg["settings"].get("https_board")):
        trouble = f"https://{tailscale.status()['name']}/ already serves this machine's port {now}; colony leaves it"
        return None
    if now != port:
        trouble = tailscale.serve(443, port) or ""
        if trouble:
            return None
    if reg["settings"].get("https_board") != port:
        reg["settings"]["https_board"] = port
        board.save_registry(reg)
    trouble = ""
    return tailscale.url(443)


def add(root, port, start=None):
    """Serve one of a project's local ports over HTTPS, at an address of its own (the same one again for a port it
    serves already). start: the command that starts what listens there, run from the project's folder when a link to
    it is tapped and nothing answers (kept when not said again; "" forgets it). Its URL; ValueError with what stands
    in the way, a port another project serves among it."""
    why = ready()
    if why:
        raise ValueError(why)
    rows, key, port = records(), str(Path(root).resolve()), int(port)
    for k, rs in rows.items():
        for r in rs:
            if k != key and r["port"] == port:
                raise ValueError(f"port {port} is served for {board.name_of(Path(k))} at {tailscale.url(r['https'])}: "
                                 "listen on a free port, and serve that")
    now = tailscale.served()
    mine = next((r["https"] for r in rows.get(key, []) if r["port"] == port), None)
    used = {r["https"] for rs in rows.values() for r in rs if r["https"] != mine} | {h for h in now if h != mine}
    https = mine if mine else next(h for h in range(tailscale.FIRST, tailscale.FIRST + 200) if h not in used and not _taken(h))
    err = tailscale.serve(https, port)
    if err:
        raise ValueError(f"tailscale serve: {err}")
    was = next((r for r in rows.get(key, []) if r["port"] == port), {})
    cmd = was.get("start", "") if start is None else start.strip()
    rows[key] = [r for r in rows.get(key, []) if r["port"] != port] + [{"port": port, "https": https, **({"start": cmd} if cmd else {})}]
    _save(rows)
    return tailscale.url(https)


def remove(root, port=None):
    """A project's HTTPS ended for one local port (colony serve --off PORT), or for all it serves (--off, or the project
    leaving the board). Whether anything was served."""
    rows, key = records(), str(Path(root).resolve())
    had = [r for r in rows.get(key, []) if port is None or r["port"] == int(port)]
    for r in had:
        tailscale.unserve(r["https"])
    if had:
        left = [r for r in rows.get(key, []) if r not in had]
        if left:
            rows[key] = left
        else:
            rows.pop(key, None)
        _save(rows)
    return bool(had)


def served(root):
    """What a project serves: [(its https address, the local port), ...]."""
    return [(tailscale.url(r["https"]), r["port"]) for r in records().get(str(Path(root).resolve()), [])]


def of(root):
    """A project's HTTPS address (its first, where it serves several), or None."""
    return next((url for url, _ in served(root)), None)


# --- a link, looked at when it is tapped

_name = None                 # this machine's tailnet name, asked of Tailscale once where the board's settings lack it
_started = {}                # (project, local port): when colony last started it


def given(url):
    """The https port of an address colony gives a project's server (this machine's tailnet name, a port of the
    projects' range), or None: any other address is left as it is."""
    global _name
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if not port or not tailscale.FIRST <= port < tailscale.FIRST + 200 or parts.scheme != "https":
        return None
    name = board.registry()["settings"].get("tailscale") or _name
    if not name:
        name = _name = tailscale.status().get("name") or "-"
    return port if (parts.hostname or "").lower() == name.lower() else None


def up(port):
    """Whether anything answers on this machine's local port."""
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def start(root, row):
    """Run the command a project gave for what listens on one of its ports, from its folder, apart from the board (a
    unit of its own where systemd runs the person's services, else a process of its own). Whether it was run."""
    if not row.get("start"):
        return False
    where, unit = str(board.workdir(Path(root))), f"colony-site-{Path(root).name}-{row['port']}"
    unit = "".join(c if c.isalnum() or c in "-_" else "-" for c in unit)
    try:
        if shutil.which("systemd-run") and subprocess.run(
                ["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}", f"--working-directory={where}",
                 "sh", "-c", row["start"]], capture_output=True, timeout=30).returncode == 0:
            return True
        logs = board.home() / "sites"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / f"{unit}.log", "ab") as log:
            subprocess.Popen(["sh", "-c", row["start"]], cwd=where, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def tapped(root, url, since=None, now=None):
    """A link in one of a project's pages, tapped. None: not an address colony gives, opened as it is. Else
    ("go", where to send the person), ("wait", what is happening, when the wait began) while its site comes up, or
    ("down", why it cannot be opened, said to the person alone: whether a site is up is colony's to see to, with no
    agent, since a link is used long after its project is parked; what is wrong inside one that loads is between
    the person and its project). since: when this tap began to wait."""
    https = given(url)
    if https is None:
        return None
    now = now or time.time()
    key, name = str(Path(root).resolve()), board.name_of(Path(root))
    ask = f" To have it looked at, ask {name}'s agent in its Chat."
    rows = records().get(key, [])
    row = next((r for r in rows if r["https"] == https), None)
    if row is None:                                   # not this project's address now: its own site, where it has one
        if len(rows) != 1:
            return "down", (f"This link is to an address {name} no longer has, and {name} "
                            + ("serves nothing now." if not rows else "serves several sites, so colony cannot tell "
                               "which one it meant.") + ask)
        row = rows[0]
        parts = urllib.parse.urlsplit(url)
        url = urllib.parse.urlunsplit(parts._replace(netloc=f"{parts.hostname}:{row['https']}"))
    if up(row["port"]):
        return "go", url
    at = (key, row["port"])
    began = since if since and now - 2 * WAIT <= since <= now else now     # an old page's wait, opened again, is a new tap
    if row.get("start") and now - began < WAIT:
        if now - _started.get(at, 0) >= WAIT and start(root, row):
            _started[at] = now
        if at in _started:
            return "wait", f"{name}'s site was not running. Colony is starting it, and this page opens it when it answers.", began
    return "down", (f"{name}'s site is not answering, and " + ("colony's start command did not bring it up."
                    if row.get("start") else "colony has no command to start it with.") + ask)


def restore(port):
    """When the board starts: the board, and each project still on it, served as recorded; one gone, let go."""
    if ready():
        return
    ensure_board(port)
    on = {str(Path(p).resolve()) for p in board.projects()}
    now = tailscale.served()
    for key, rs in list(records().items()):
        if key not in on:
            remove(key)
            continue
        for r in rs:
            if now.get(r["https"]) != r["port"]:
                tailscale.serve(r["https"], r["port"])


def keep(port):
    """While the board runs (its own thread): served as recorded, then looked at again every EVERY seconds, so the
    tailnet's certificates turned on (or Tailscale connected) after the board started are taken up with no restart, and
    a serve tailscaled lost is put back. Nothing is asked of Tailscale while remote access is off and nothing is served.
    It keeps the board it was started for and no other: should the board's home change under it, it stops. (A test's
    board, started in the process that runs the tests, ended and its home went, and this went on with the person's
    real settings and their real Tailscale Serve.)"""
    mine = board.home()
    while board.home() == mine:
        try:
            if board.registry()["settings"].get("tailscale") or records():
                if board.home() != mine:
                    break
                restore(port)
        except Exception:
            pass                        # https is extra: its trouble stops nothing else
        time.sleep(EVERY)
