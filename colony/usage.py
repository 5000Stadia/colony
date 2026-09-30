"""Each agent program's usage limits (its 5-hour and weekly windows), read without spending a token, and the
pause colony calls when one runs low: at the threshold (97% by default, the person's call; per project too) each
project on that program is told on its next turn, not woken, to wind down and tell the person where things stand
and their options; once the window resets, it is woken to carry on.

PROVIDER: where each program keeps its limits. Codex writes them into its session files as it works; Claude Code
hands them to its status line, which colony wires to record them (colony statusline).
"""
import json
import time
from pathlib import Path

from . import board

WINDOWS = {300: "5-hour", 10080: "weekly"}
NAMES = {"five_hour": "5-hour", "seven_day": "weekly"}


def folder():
    return board.home() / "usage"


def record_claude(payload):
    """Claude Code's limits, from what it hands its status line: kept for the board and the watcher."""
    limits = payload.get("rate_limits") or {}
    windows = {NAMES[k]: {"used": v["used_percentage"], "resets_at": v["resets_at"]}
               for k, v in limits.items() if k in NAMES and v and v.get("used_percentage") is not None}
    if windows:
        folder().mkdir(parents=True, exist_ok=True)
        (folder() / "claude.json").write_text(json.dumps({"at": time.time(), "windows": windows}))


def codex(home=None):
    """Codex's limits, from the newest session file that reports them."""
    from .providers import get
    sessions = Path(home or get("codex").config_home()) / "sessions"
    files = sorted(sessions.glob("*/*/*/*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:5] if sessions.exists() else []
    for f in files:
        try:
            lines = f.read_text().splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            found = _find(e, "rate_limits")
            if not isinstance(found, dict):
                continue
            windows = {}
            for w in (found.get("primary"), found.get("secondary")):
                if w and WINDOWS.get(w.get("window_minutes")) and w.get("used_percent") is not None:
                    windows[WINDOWS[w["window_minutes"]]] = {"used": w["used_percent"], "resets_at": w.get("resets_at")}
            if windows:
                return {"at": f.stat().st_mtime, "windows": windows}
    return None


def _find(x, key):
    if isinstance(x, dict):
        if key in x:
            return x[key]
        for v in x.values():
            got = _find(v, key)
            if got is not None:
                return got
    return None


def read(key, raw=False):
    """A program's windows now: {name: {used, resets_at}}; a window past its reset reads as unused (raw: as
    last reported)."""
    if key == "claude":
        try:
            got = json.loads((folder() / "claude.json").read_text())
        except (OSError, ValueError):
            got = None
    elif key == "codex":
        got = codex()
    else:
        got = None
    if not got:
        return {}
    if raw:
        return got["windows"]
    now = time.time()
    return {w: dict(v, used=0 if v.get("resets_at") and v["resets_at"] <= now else v["used"])
            for w, v in got["windows"].items()}


def threshold(root=None):
    """The percentage of a window at which projects pause: the project's own, else colony's; None is off."""
    s = board.project_settings(root)[0] if root else board.registry()["settings"]
    try:
        t = float(s.get("usage_pause") or 0)
    except (TypeError, ValueError):
        return None
    return t if 0 < t <= 100 else None


def over(key, pct):
    """The window of a program at or past pct, the one that resets last: (name, {used, resets_at}) or None."""
    hit = [(w, v) for w, v in read(key).items() if v["used"] >= pct]
    return max(hit, key=lambda x: x[1].get("resets_at") or 0) if hit else None


def when(ts):
    return time.strftime("%a %b %-d, %-I:%M %p", time.localtime(ts)) if ts else "when it resets"


def line(key):
    """A program's usage in a few words, for the board."""
    ws = read(key)
    return " · ".join(f"{w} {v['used']:g}%" + (f", resetting {when(v['resets_at'])}" if v["used"] and v.get("resets_at") else "")
                      for w, v in sorted(ws.items(), key=lambda x: x[0] != "weekly"))


def paused():
    try:
        return json.loads((folder() / "paused.json").read_text())
    except (OSError, ValueError):
        return {}


def check():
    """Pause each project whose program has passed its threshold, and resume it when the window resets. Returns
    what changed, as (project, "paused"|"resumed")."""
    from . import providers
    was, now, changed = paused(), {}, []
    for p in board.projects():
        if not p.exists():
            continue
        prov = providers.of(p)
        key, t = providers.key(prov), threshold(p)
        hit = over(key, t) if t else None
        if hit:
            window, v = hit
            now[str(p)] = {"provider": key, "window": window, "resets_at": v.get("resets_at")}
            if str(p) not in was:
                others = [(k, x) for k, x in providers.PROVIDERS.items() if k != key and providers.usable(x)]
                board.add_note(p, None, wind_down(p, prov, window, v, others), author="colony", quiet=True)
                changed.append((p, "paused"))
        elif str(p) in was:
            w = was[str(p)]
            latest = read(key, raw=True).get(w["window"]) or {}
            turned = min(w.get("resets_at") or 0, latest.get("resets_at") or w.get("resets_at") or 0) <= time.time()
            why = (f"{prov.label}'s {w['window']} usage limit has reset" if turned
                   else f"{prov.label} is back under this project's pause threshold ({t:g}%)" if t
                   else "this project no longer pauses at a usage limit")
            board.add_note(p, None, f"{why}. Carry on where you stopped.", author="colony")
            changed.append((p, "resumed"))
    if now != was:
        folder().mkdir(parents=True, exist_ok=True)
        (folder() / "paused.json").write_text(json.dumps(now))
    return changed


def twin(root, key):
    """A project already on the board that runs this one's work on another program: the same folder, or the
    name this one's would get (colony-codex for colony)."""
    from . import providers
    for p in board.projects():
        if p != root and providers.key(providers.of(p)) == key and (
                board.workdir(p) == board.workdir(root) or p.name == f"{root.name}-{key}"):
            return p
    return None


def wind_down(root, prov, window, v, others):
    """What an agent is told when its program's window passes the threshold."""
    options = [f"wait for the reset, {when(v.get('resets_at'))}, when colony wakes this project to carry on"]
    for k, x in others:
        u = line(k)
        room = f" ({x.label} is at {u})" if u else ""
        t = twin(root, k)
        options.append(f"hand the work to {t.name}, already on the board on {x.label}{room}: colony send {t.name} with "
                       "what to take over" if t else
                       f"hand this project to {x.label}{room}: colony track {board.workdir(root)} --provider {k} "
                       f"--name {root.name}-{k}")
    options.append(f"keep going past the limit: colony settings --project {root.name} usage_pause off")
    return (f"{prov.label} is at {v['used']:g}% of its {window} usage limit, which resets {when(v.get('resets_at'))}. "
            "Wind down: there is a little room left, enough to land what's in flight, not to start more. Let work "
            "already running finish, helpers included (don't cancel them), and take in what they hand back; then commit "
            "what works so far (a clean point, not the item finished: the item stays in progress). Start nothing new: no new helpers or consultations. Then "
            "end your turn by telling the person, briefly, where things stand (done, half-done, next) and their "
            "options:\n" + "\n".join(f"  {i}. {o[0].upper() + o[1:]}" for i, o in enumerate(options, 1)))
