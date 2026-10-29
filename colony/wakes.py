"""Timed wakes: an agent asks to be woken later, to check back on something (a build, a reply, a price), instead of
a sleeping process or a cron job no one can see. Each is kept in its project's .board/wakes.jsonl, one row as it is
made, one each time it fires, one if it is cancelled, so it outlives a board restart or a reboot and its use can be
counted (KERNOS built follow-ups and its live data showed none was ever used). The steward's live under its home.

Waiting costs nothing: the watcher's pass reads what is due. A due wake becomes a note from colony, handed over with
a prompt like any other, once its console is idle (or off, which the watcher then starts) and inside the person's
active hours; a wake that came due while the board was down fires once. One that repeats is due again `every` after
it fires.

    colony wake [NAME] --at 14:30 | --in 2h [--every 1d] "why"     colony wake --list     colony wake --cancel ID
"""
import re
import secrets
import time
from datetime import datetime
from pathlib import Path

from . import board, hours

FLOOR = 5 * 60                  # nothing sooner or more often than this: a wake is for checking back, not polling
UNITS = {"m": 60, "h": 3600, "d": 86400}
DURATION = re.compile(r"(?:(\d+)\s*([mhd])\s*)+")


def duration(text):
    """30m, 2h, 1d (or 1h30m) as seconds; ValueError for anything else, or under five minutes."""
    text = str(text or "").strip().lower()
    if not DURATION.fullmatch(text):
        raise ValueError(f"{text or 'nothing'} isn't a duration: write it as 30m, 2h or 1d.")
    secs = sum(int(n) * UNITS[u] for n, u in re.findall(r"(\d+)\s*([mhd])", text))
    if secs < FLOOR:
        raise ValueError("A wake is five minutes away at the soonest, so nothing polls.")
    return secs


def span(secs):
    """Seconds as a duration is written: 1d, 2h, 1h30m."""
    out, secs = "", int(secs)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            out, secs = out + f"{secs // size}{unit}", secs % size
    return out or "0m"


def moment(text, now=None):
    """When --at means, as a timestamp on this machine's clock: HH:MM is today, or tomorrow once that has passed; an
    ISO date-time (2026-10-29T09:00, or with a space) is local time."""
    now = hours.clock() if now is None else now
    text = str(text or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if m:
        h, mi = map(int, m.groups())
        if h > 23 or mi > 59:
            raise ValueError(f"{text} isn't a time of day: write it as HH:MM, on a 24-hour clock.")
        t = time.localtime(now)
        at = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, mi, 0, 0, 0, -1))
        if at <= now:                              # mktime carries a day past the month's end
            at = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + 1, h, mi, 0, 0, 0, -1))
    else:
        try:
            when = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{text or 'nothing'} isn't a time: write HH:MM, or a date and time like 2026-10-29T09:00.")
        if when.tzinfo is None:
            at = time.mktime(when.timetuple())
        else:
            at = when.timestamp()
    if at - now < FLOOR:
        raise ValueError("That is past, or under five minutes away: a wake is five minutes away at the soonest.")
    return at


def _stamp(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def make(root, why, at=None, within=None, every=None, by="agent", now=None):
    """A wake for `root`, recorded as made. at: an --at text; within and every: durations as text. Without either
    at or within, the first is `every` from now. ValueError says what was wrong."""
    now = hours.clock() if now is None else now
    why = " ".join(str(why or "").split())
    if not why:
        raise ValueError("A wake says why: what to check when it comes.")
    if at and within:
        raise ValueError("Either --at or --in, not both.")
    step = duration(every) if every else None
    if at:
        due = moment(at, now)
    elif within:
        due = now + duration(within)
    elif step:
        due = now + step
    else:
        raise ValueError("Say when: --at TIME or --in DURATION (and --every DURATION to repeat).")
    row = {"type": "made", "id": "w" + secrets.token_hex(3), "at": _stamp(now), "due": _stamp(due), "why": why,
           "by": by, **({"every": step} if step else {})}
    (Path(root) / ".board").mkdir(parents=True, exist_ok=True)     # the steward's home may not have one yet
    board.append(root, "wakes.jsonl", row)
    return row


def waiting(root):
    """The wakes still to come, soonest first: each made row with its next "due" as the latest firing left it."""
    live = {}
    for r in board.read(root, "wakes.jsonl"):
        if r.get("type") == "made":
            live[r["id"]] = dict(r)
        elif r.get("type") == "fired" and r.get("id") in live:
            if r.get("next"):
                live[r["id"]]["due"] = r["next"]
            else:
                live.pop(r["id"])
        elif r.get("type") == "cancelled":
            live.pop(r.get("id"), None)
    return sorted(live.values(), key=lambda w: w["due"])


def cancel(root, ident, by="agent", why=""):
    """Cancel a waiting wake; KeyError if there is none by that id."""
    if not any(w["id"] == ident for w in waiting(root)):
        raise KeyError(ident)
    board.append(root, "wakes.jsonl", {"type": "cancelled", "id": ident, "at": board.now(), "by": by,
                                       **({"why": why} if why else {})})


def when(w):
    """A wake's next time as the person reads a clock, locally: Thu 9:00 AM."""
    return hours.at_day(board.epoch(w["due"]))


def text(w):
    """The note it becomes: why, and when and by whom it was set."""
    made = time.strftime("%Y-%m-%d %H:%M", time.localtime(board.epoch(w["at"])))
    who = {"agent": "you", "steward": "the steward", "person": "the person"}.get(w.get("by"), w.get("by") or "you")
    return f"[colony] Wake: {w['why']} (set {made} by {who})"


def fire(root, w, now=None, steward=False):
    """Hand a due wake over and record it fired, with its next time if it repeats (from this firing, so missed ones
    while the board was down make one, not one each)."""
    now = hours.clock() if now is None else now
    if steward:
        from . import monitor
        monitor.queue(text(w))
    else:
        board.add_note(root, None, text(w), author="colony", kind="wake")
    board.append(root, "wakes.jsonl", {"type": "fired", "id": w["id"], "at": _stamp(now),
                                       **({"next": _stamp(now + w["every"])} if w.get("every") else {})})


def tick(now=None):
    """The watcher's pass: each project's due wakes fire once its console is idle (or off) inside the person's hours;
    one working, or asking something, is left until its turn ends."""
    from . import console, monitor
    now = hours.clock() if now is None else now
    if not hours.active(now):
        return
    stamp = _stamp(now)
    places = [(p, False) for p in board.projects() if p.exists()] + [(monitor.home(), True)]
    for root, steward in places:
        due = [w for w in waiting(root) if w["due"] <= stamp]
        if not due:
            continue
        state = (monitor.snapshot() if steward else console.snapshot(root, lines=1))["state"]
        if state not in ("idle", "off"):
            continue
        for w in due:
            fire(root, w, now, steward)
