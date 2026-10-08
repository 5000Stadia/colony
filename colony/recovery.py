"""Work a network drop stopped, set going again once the network is back, with no tokens.

An agent program retries a failed request for a while and then gives up (Claude Code after about six minutes): each
helper it had running is reported failed, and the agent's own turn ends on the same error, as does each turn those
reports start while the network is still down. Then nothing is left to start a turn, and an outage of two hours costs
the night (holo-emitter, 2026-10-07: four helpers lost between 1:13 and 1:17 AM, the network back by 3:05, nothing
moved until the person typed "Continue" at 7:59 and the agent resumed each helper itself). So once a console sits idle
on a turn a network error ended, and the network answers again, the watcher types one nudge, as the person did. Each
program reads its own transcript (network_stop in providers.py); this decides when.

Never while the person has something typed there or a question waits on them, nor while the safe pause holds the
project, the person has paused it, its lead is changing or its context is being refreshed. At any hour: this is work,
not a question. Once per loss; a nudge whose turn the network stops again waits longer before the next.
"""
import json
import socket
import threading
import time
from pathlib import Path

from . import board, console, context, hours, lead, providers, usage

GRACE = 120              # seconds a stop stands before colony steps in: a person at the console has the first move
RETRY = 300              # the wait before nudging again when the last nudge's turn was stopped too; it doubles...
LONGEST = 2 * 3600       # ...up to this
PROBE = 60               # seconds between checks that the network answers, while something waits on it
_scans, _probes = {}, {}


def records():
    try:
        return json.loads((board.home() / "recovery.json").read_text())
    except (OSError, ValueError):
        return {}


def reachable(host):
    """Whether `host` took a connection when last tried, in the last two minutes. A check that is due runs apart,
    so the watcher never waits on a network that is down; until one answers, the network counts as down. Nothing
    is sent: a connection to its port 443, opened and closed."""
    probe = _probes.setdefault(host, {"at": 0.0, "ok": False, "busy": False})
    if not probe["busy"] and time.time() - probe["at"] >= PROBE:
        probe["busy"] = True
        threading.Thread(target=_probe, args=(host, probe), daemon=True).start()
    return probe["ok"] and time.time() - probe["at"] < 2 * PROBE


def _probe(host, probe):
    try:
        socket.create_connection((host, 443), timeout=10).close()
        ok = True
    except OSError:
        ok = False
    probe.update(ok=ok, at=time.time(), busy=False)


def stop(root):
    """What a network drop stopped in the project's console, as its program reports it, or None; read afresh only
    once its transcript has changed."""
    s, program = context.session(root), providers.of(root)
    if not s or s.get("provider") != providers.key(program) or not hasattr(program, "network_stop"):
        return None
    try:
        st = Path(s["path"]).stat()
    except (OSError, KeyError, TypeError):
        return None
    seen = (st.st_size, st.st_mtime_ns)
    if _scans.get(s["path"], (None,))[0] != seen:
        _scans[s["path"]] = (seen, program.network_stop(s["path"]))
    return _scans[s["path"]][1]


def nudge(found, helpers):
    """The words typed into the console: the helpers the drop stopped that no nudge has named, else the turn."""
    if helpers:
        one = len(helpers) == 1
        names = "; ".join(f"{h['name']} ({h['id']})" for h in helpers)
        return (f"[colony] {'A helper' if one else f'{len(helpers)} helpers'} stopped on a network error at "
                f"{hours.at_clock(helpers[0]['at'])}: {names}. The network is back: resume or relaunch "
                f"{'it' if one else 'them'} and carry on.")
    return (f"[colony] Your turn stopped on a network error at {hours.at_clock(found['at'])} ({found['error']}). "
            "The network is back: carry on where you were.")


def tick(root):
    """Nudge the project's console once about what a network drop stopped there, when that is safe: the words
    typed, or None."""
    found = stop(root)
    if not found:
        return None
    rows = records()
    mine = rows.get(str(root), {})
    done, now = set(mine.get("nudged", [])), time.time()
    if found["key"] in done or now - found["at"] < GRACE:
        return None
    g = lead.info(root)
    if (str(root) in usage.paused() or g.get("paused") or g.get("handoff") or context.refreshing(root)
            or board.asks(root)):
        return None
    name = console.session_name(root)
    if console.snapshot(root, lines=1)["state"] != "idle" or console.drafting(name):
        return None
    # A nudge whose turn the network stopped again, with no reply since, waits before the next: longer each time.
    streak = mine.get("streak", 0) if found["ok_at"] <= mine.get("at", 0) else 0
    if streak and now - mine["at"] < min(LONGEST, RETRY * 2 ** (streak - 1)):
        return None
    if not reachable(providers.of(root).api_host):
        return None
    helpers = [h for h in found["helpers"] if h["key"] not in done]
    text = nudge(found, helpers)
    if not console.type_into(name, text):
        return None                                 # someone is typing there: tried again next time
    keys = mine.get("nudged", []) + [found["key"]] + [h["key"] for h in helpers]
    rows[str(root)] = {"nudged": keys[-100:], "at": now, "streak": streak + 1}
    board.home().mkdir(parents=True, exist_ok=True)
    (board.home() / "recovery.json").write_text(json.dumps(rows, indent=1))
    return text


def tick_all():
    """Every project's console. One's trouble stops no other's, nor the rest of the watcher's round."""
    for p in board.projects():
        if p.exists():
            try:
                tick(p)
            except Exception:
                pass
