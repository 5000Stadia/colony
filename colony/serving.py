"""HTTPS for the board and for the projects' own servers, shared through Tailscale Serve (tailscale.py): one name, this
machine's on the person's tailnet, with a real certificate; the board at https://<name>/, and a project that serves
(Bookflow's host) at https://<name>:<its port>/, proxied to its localhost port. A project asks with colony serve PORT
and binds 127.0.0.1 only (Tailscale's advice: what it serves carries the person's identity headers, which a direct
caller could otherwise forge), taking its external origin from the URL colony prints. Kept across restarts by
tailscaled itself; looked at again as the board starts and every few minutes while it runs, so certificates turned on
later are taken up with no restart; a project's goes when it leaves the board.

R119. The person, via Bookflow (2026-10-28): "Yes: turn it on, Colony builds shared https serving", and "We want to do
this without any friction whatsoever on install, it should just work out of the box."
"""
import json
import socket
import time
from pathlib import Path

from . import board, tailscale

EVERY = 300                  # seconds between looks while the board runs: a 10 ms question to tailscaled
trouble = ""                 # why Tailscale refused to serve the board, last it was asked (doctor says it)


def _path():
    return board.home() / "served.json"


def records():
    """{project folder: {"port": its local port, "https": its https port}}."""
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


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


def add(root, port):
    """Serve a project's local port over HTTPS, on a port of its own (the same one again if it had one). Its URL;
    ValueError with what stands in the way."""
    why = ready()
    if why:
        raise ValueError(why)
    rows, key = records(), str(Path(root).resolve())
    now = tailscale.served()
    mine = rows.get(key, {}).get("https")
    used = {r["https"] for k, r in rows.items() if k != key} | {h for h, local in now.items() if h != mine}
    https = mine if mine and mine not in used else next(
        h for h in range(tailscale.FIRST, tailscale.FIRST + 200) if h not in used and (h == mine or not _taken(h)))
    err = tailscale.serve(https, int(port))
    if err:
        raise ValueError(f"tailscale serve: {err}")
    rows[key] = {"port": int(port), "https": https}
    _save(rows)
    return tailscale.url(https)


def remove(root):
    """A project's HTTPS ended (colony serve --off, or the project leaving the board). Whether it had one."""
    rows, key = records(), str(Path(root).resolve())
    had = rows.pop(key, None)
    if had:
        tailscale.unserve(had["https"])
        _save(rows)
    return bool(had)


def of(root):
    """A project's HTTPS address, or None."""
    had = records().get(str(Path(root).resolve()))
    return tailscale.url(had["https"]) if had else None


def restore(port):
    """When the board starts: the board, and each project still on it, served as recorded; one gone, let go."""
    if ready():
        return
    ensure_board(port)
    on = {str(Path(p).resolve()) for p in board.projects()}
    now = tailscale.served()
    for key, r in list(records().items()):
        if key not in on:
            remove(key)
        elif now.get(r["https"]) != r["port"]:
            tailscale.serve(r["https"], r["port"])


def keep(port):
    """While the board runs (its own thread): served as recorded, then looked at again every EVERY seconds, so the
    tailnet's certificates turned on (or Tailscale connected) after the board started are taken up with no restart, and
    a serve tailscaled lost is put back. Nothing is asked of Tailscale while remote access is off and nothing is served."""
    while True:
        try:
            if board.registry()["settings"].get("tailscale") or records():
                restore(port)
        except Exception:
            pass                        # https is extra: its trouble stops nothing else
        time.sleep(EVERY)
