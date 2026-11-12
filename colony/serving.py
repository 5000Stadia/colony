"""HTTPS for the board and for the projects' own servers, shared through Tailscale Serve (tailscale.py): one name, this
machine's on the person's tailnet, with a real certificate; the board at https://<name>/, and a project that serves
(Bookflow's host) at https://<name>:<its port>/, proxied to its localhost port. A project asks with colony serve PORT
and binds 127.0.0.1 only (Tailscale's advice: what it serves carries the person's identity headers, which a direct
caller could otherwise forge), taking its external origin from the URL colony prints. Each local port a project serves
has an address of its own (one project served three story boards side by side, and the one address it had moved to each in
turn, so the person's link showed another board), and a port another project serves is refused, naming it (one
project's host came up on a port colony had served for another's, which crash-looped). Kept across restarts by
tailscaled itself; looked at again as the board starts and every few minutes while it runs, so certificates turned on
later are taken up with no restart; a project's goes when it leaves the board. An address once given is its project's
for good (past): links to it are kept in showcase entries, mail and the person's own bookmarks, and one given up was
handed to the next project that asked, so a project's saved link opened another project's site. Given up, it answers
nothing until the same project serves the same local port again.

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


def _past_path():
    return board.home() / "served-past.json"


def past():
    """{https port: [project folder, the local port it was given for]}: every address ever given, kept after its
    serving ends, so no other project is given it. What is served now is in it whether or not it was written down."""
    try:
        out = {int(h): list(v) for h, v in json.loads(_past_path().read_text()).items()}
    except (OSError, ValueError, TypeError):
        out = {}
    for k, rs in records().items():
        for r in rs:
            out[r["https"]] = [k, r["port"]]
    return out


def _remember(held):
    board.home().mkdir(parents=True, exist_ok=True)
    _past_path().write_text(json.dumps({str(h): v for h, v in sorted(held.items())}, indent=1))


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
    """Serve one of a project's local ports over HTTPS, at an address of its own (the same one again for a port it
    serves already). Its URL; ValueError with what stands in the way, a port another project serves among it."""
    why = ready()
    if why:
        raise ValueError(why)
    rows, key, port = records(), str(Path(root).resolve()), int(port)
    for k, rs in rows.items():
        for r in rs:
            if k != key and r["port"] == port:
                raise ValueError(f"port {port} is served for {board.name_of(Path(k))} at {tailscale.url(r['https'])}: "
                                 "listen on a free port, and serve that")
    now, held = tailscale.served(), past()
    mine = next((r["https"] for r in rows.get(key, []) if r["port"] == port), None)
    if not mine:                          # served before and ended: the address it had, unless something else has it now
        mine = next((h for h, (k, p) in sorted(held.items())
                     if k == key and p == port and (now.get(h) == port or (h not in now and not _taken(h)))), None)
    used = {r["https"] for rs in rows.values() for r in rs if r["https"] != mine} | {h for h in now if h != mine}
    free = [h for h in range(tailscale.FIRST, tailscale.FIRST + 200) if h not in used and h != mine]
    # one never given to anyone; with all 200 given, one whose serving ended (the old way, and far off)
    https = mine or next((h for h in free if h not in held and not _taken(h)), None) or next(h for h in free if not _taken(h))
    err = tailscale.serve(https, port)
    if err:
        raise ValueError(f"tailscale serve: {err}")
    rows[key] = [r for r in rows.get(key, []) if r["port"] != port] + [{"port": port, "https": https}]
    _save(rows)
    held[https] = [key, port]
    _remember(held)
    return tailscale.url(https)


def remove(root, port=None):
    """A project's HTTPS ended for one local port (colony serve --off PORT), or for all it serves (--off, or the project
    leaving the board). Whether anything was served."""
    rows, key = records(), str(Path(root).resolve())
    had = [r for r in rows.get(key, []) if port is None or r["port"] == int(port)]
    if had:
        _remember(past())                 # whose it was outlasts the serving (one served before addresses were kept, too)
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
