"""Each agent program's usage limits (its 5-hour and weekly windows), read without spending a token, and the
safe pause colony calls when one runs low, so no work is cut off mid-task by the limit itself: at the threshold (98% by default, the person's call; per project too) each
project on that program is told on its next turn, not woken, to wind down and tell the person where things stand
and their options; once the window resets, it is woken to carry on.

PROVIDER: where each program keeps its limits. Codex writes them into its session files as it works; Claude Code
hands them to its status line, which colony wires to record them (colony statusline).
"""
import json
import re
import time
from pathlib import Path

from . import board

WINDOWS = {300: "5-hour", 10080: "weekly"}
NAMES = {"five_hour": "5-hour", "seven_day": "weekly"}
RESUME_MARGIN = 2                         # percentage points; ignore rounding near the pause threshold


def folder():
    return board.home() / "usage"


def record_claude(payload):
    """Claude Code's limits, from what it hands its status line: kept for the board and the watcher."""
    limits = payload.get("rate_limits") or {}
    windows = {NAMES[k]: {"used": v["used_percentage"], "resets_at": v["resets_at"]}
               for k, v in limits.items() if k in NAMES and v and v.get("used_percentage") is not None}
    if windows:
        folder().mkdir(parents=True, exist_ok=True)
        (folder() / "claude.json").write_text(json.dumps({"at": time.time(), "windows": windows, "raw": limits}))


def codex_live():
    """Codex's limits asked of a running Codex server (no tokens), when one is up: fresh even while every console idles.
    Asked at most every half minute: every page shows them, and asking takes a moment."""
    key, t = str(board.home()), time.time()
    if key in _live and t - _live[key][0] < 30:
        return _live[key][1]
    got = _codex_live()
    _live[key] = (t, got)
    return got


_live = {}


def _codex_live():
    from . import codex_remote
    from .codex_rpc import Client, RPCError
    for home in sorted((h for h in board.codex_homes().glob("*") if h.is_dir()),
                       key=lambda h: h.stat().st_mtime, reverse=True):
        if not codex_remote.alive(home):
            continue
        try:
            with Client(codex_remote.socket_for(home), timeout=2) as client:
                limits = client.call("account/rateLimits/read", {}).get("rateLimits") or {}
        except (OSError, RPCError, ValueError):
            continue
        windows = {}
        for w in (limits.get("primary"), limits.get("secondary")):
            if w and WINDOWS.get(w.get("windowDurationMins")) and w.get("usedPercent") is not None:
                windows[WINDOWS[w["windowDurationMins"]]] = {"used": w["usedPercent"], "resets_at": w.get("resetsAt")}
        if windows:
            return {"at": time.time(), "windows": windows}
    return None


def codex(home=None):
    """Codex's limits, from the newest session file that reports them, across every Codex home colony runs (each
    project's own, and the person's): its rate limits are the account's, whichever home wrote them. Only each file's
    end is read, since sessions grow large."""
    from .providers import get
    homes = [Path(home)] if home else [Path(get("codex").config_home()),
                                        *(h for h in board.codex_homes().glob("*") if h.is_dir())]
    files = [f for h in homes if (h / "sessions").exists() for f in (h / "sessions").glob("*/*/*/*.jsonl")]
    for f in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:8]:
        try:
            with f.open("rb") as handle:
                handle.seek(max(0, f.stat().st_size - TAIL))
                lines = handle.read().decode(errors="replace").splitlines()
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


TAIL = 1 << 20                              # bytes read from a session's end: its latest reports are there


def _find(x, key):
    if isinstance(x, dict):
        if key in x:
            return x[key]
        for v in x.values():
            got = _find(v, key)
            if got is not None:
                return got
    return None


def reading(key):
    """A program's latest report, as it was made: {at, windows}, or None."""
    if key == "claude":
        try:
            return json.loads((folder() / "claude.json").read_text())
        except (OSError, ValueError):
            return None
    return (codex_live() or codex()) if key == "codex" else None


def read(key, raw=False):
    """A program's windows now: {name: {used, resets_at}}; a window past its reset reads as unused (raw: as
    last reported)."""
    got = reading(key)
    if not got:
        return {}
    if raw:
        return got["windows"]
    now = time.time()
    return {w: dict(v, used=0 if v.get("resets_at") and v["resets_at"] <= now else v["used"])
            for w, v in got["windows"].items()}


def session_threshold():
    """The share of a 5-hour session window at which projects pause: colony's safe_pause_session (99 by default, the
    person's call); None is off, leaving the window to the safe pause itself."""
    try:
        t = float(board.registry()["settings"].get("safe_pause_session") or 0)
    except (TypeError, ValueError):
        return None
    return t if 0 < t <= 100 else None


def limit(window, t):
    """The threshold a window pauses at: the safe pause's (the project's own, else colony's), a 5-hour window never
    later than the session's. Safe pause off is off for both: the person chose to keep going past the limit."""
    s = session_threshold() if window == "5-hour" else None
    return min(t, s) if t and s else t


def threshold(root=None):
    """The percentage of a window at which projects pause: the project's own, else colony's; None is off."""
    s = board.project_settings(root)[0] if root else board.registry()["settings"]
    try:
        t = float(s.get("safe_pause") or 0)
    except (TypeError, ValueError):
        return None
    return t if 0 < t <= 100 else None


def week_limits(root):
    """The week limits that hold for a project (R168): {program: percent} for each program it could build on, the
    share of that program's week past which it starts nothing new there. Its own for a program where it has one (off:
    none), else colony's. A project's agent can run another program's builds, so every usable program is counted."""
    from . import providers

    def kept(value):
        try:
            return board.week_shares(value or {})
        except ValueError:
            return {}
    own, everyone = kept(board.project_settings(root)[1].get("week_pause")), kept(board.registry()["settings"].get("week_pause"))
    out = {}
    for k, x in providers.PROVIDERS.items():
        n = own.get(k, own.get("*"))
        n = everyone.get(k, everyone.get("*")) if n is None else n
        if n and providers.usable(x):
            out[k] = n
    return out


def weeks_past(limits, clock):
    """The weekly windows at their limit or past it, of the programs in limits ({program: percent}), named by
    program: {"codex weekly": {used, resets_at, program, limit}}."""
    out = {}
    for k, pct in limits.items():
        v = read(k, raw=True).get("weekly") or {}
        if v.get("used") is not None and v["used"] >= pct and not (v.get("resets_at") and v["resets_at"] <= clock):
            out[f"{k} weekly"] = dict(v, program=k, limit=pct)
    return out


def limits_line(root):
    """What a project's agent hears each turn while another program than its own is past its week limit: nothing
    new starts there, and its own program carries on. (Its own program past its limit pauses the project: check().)"""
    from . import providers
    mine = providers.key(providers.of(root))
    past = weeks_past({k: n for k, n in week_limits(root).items() if k != mine}, time.time())
    return " ".join(
        f"Week limit: {providers.PROVIDERS[v['program']].label} is past {v['limit']:g}% of its week, the limit the person "
        f"set (at {v['used']:g}%; it resets {when(v.get('resets_at'))}). Start nothing new on it until then: what is "
        "already running there finishes, and the rest of your work carries on here. Changing the limit is theirs."
        for v in past.values())


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
    """Pause at the threshold; resume after reset or a clear drop in every blocking window. Returns
    what changed, as (project, "paused"|"resumed")."""
    from . import providers
    was, now, changed = paused(), {}, []
    for p in board.projects():
        if not p.exists():
            continue
        prov = providers.of(p)
        key, t = providers.key(prov), threshold(p)
        previous = was.get(str(p))
        held = (previous.get('windows') or {previous['window']: {'resets_at': previous.get('resets_at')}}
                if previous and previous['provider'] == key else {})
        raw, clock = read(key, raw=True), time.time()

        def reset(value):
            return bool(value.get('resets_at') and value['resets_at'] <= clock)

        blocking = {w: v for w, v in raw.items() if limit(w, t) and not reset(v) and v['used'] >= limit(w, t)}
        week = week_limits(p).get(key)                     # its own program's week limit pauses it; another's doesn't
        blocking.update(weeks_past({key: week}, clock) if week else {})
        for window, saved in held.items():
            if saved.get('program'):                       # a program's week (week_pause): held until it resets
                latest = read(saved['program'], raw=True).get('weekly') or {}
                if saved['program'] == key and week and not reset(saved) and not reset(latest) and not (
                        latest.get('used') is not None and latest['used'] <= max(0, week - RESUME_MARGIN)):
                    blocking.setdefault(window, dict(saved, **latest))
                continue
            if not limit(window, t):
                continue
            latest = raw.get(window, {})
            # Missing telemetry or a missing reset timestamp is not evidence of a reset.
            if reset(saved) or reset(latest):
                continue
            if latest.get('used') is not None and latest['used'] <= max(0, limit(window, t) - RESUME_MARGIN):
                continue
            retained = dict(saved, **latest)
            retained['resets_at'] = latest.get('resets_at') or saved.get('resets_at')
            blocking.setdefault(window, retained)
        if blocking:
            window, v = max(blocking.items(), key=lambda x: x[1].get('resets_at') or 0)
            now[str(p)] = {"provider": key, "window": window, "resets_at": v.get("resets_at"),
                           "windows": blocking, "active": previous.get("active", True) if previous else True}
            if not previous or previous['provider'] != key:
                # Only a project at work is told to wind down, and later to carry on: one idle when the limit came is
                # held all the same (nothing starts it), but told nothing, so it isn't woken to say again that nothing
                # was running (the person, of EconomyHumboldt: "It just prompts it to repeat itself. Can we reserve
                # that feature only for active projects?")
                from . import console
                active = console.snapshot(p).get("state") == "working"
                now[str(p)]["active"] = active
                past = {w: x for w, x in blocking.items() if x.get('program')}
                if past and not any(w for w in blocking if w not in past):
                    # the person asked to be told: idle, it is woken to tell them where things stand
                    board.add_note(p, None, week_down(p, week, past), author="colony", quiet=active)
                elif active:
                    others = [(k, x) for k, x in providers.PROVIDERS.items() if k != key and providers.usable(x)]
                    board.add_note(p, None, wind_down(p, prov, window, v, others), author="colony", quiet=True)
                changed.append((p, "paused"))
        elif previous:
            turned = held and all(reset(v) or reset(raw.get(w, {})) for w, v in held.items())
            weekly = any(v.get('program') for v in held.values())
            why = (("this project no longer pauses at a share of the week" if not week
                    else "the week it paused at has reset") if weekly
                   else "this project no longer pauses at a usage limit" if not t
                   else f"this project now uses {prov.label}" if previous['provider'] != key
                   else f"{prov.label}'s usage limit has reset" if turned
                   else f"{prov.label}'s blocking usage windows have reset or fallen below their pause thresholds")
            calm = any(n.get("kind") == "turbo-end" and not n["delivered_at"] for n in board.open_notes(p))
            if previous.get("active", True):              # it was at work when the pause came: it carries on
                board.add_note(p, None, f"Safe pause over: {why}. Carry on where you stopped" + (
                    ", at this project's usual posture: turbo is over (its note says how)." if calm else "."),
                    author="colony", kind="resume")
            changed.append((p, "resumed"))
    if now != was:
        folder().mkdir(parents=True, exist_ok=True)
        (folder() / "paused.json").write_text(json.dumps(now))
    return changed


# What a program prints when its limit stops a turn, and when it says the limit lifts. PROVIDER: Claude Code ("You've
# hit your session limit · resets 3:40pm (America/Los_Angeles)", or "… resets Aug 28, 4pm (…)"); Codex ("You've hit
# your usage limit. … try again at Sep 13th, 2026 9:15 AM.").
# The program wraps its own message at the window's width, its further lines indented ("… resets Nov\n     20, 3pm
# (America/Los_Angeles)", Nov 16): those lines are the message's too, read as one.
# the message on a line of its own, after its program's mark (⎿, ■): a copy quoted in a log or in code an agent shows
# is no limit
CUT = re.compile(r"^[ \t]*(?:[^\w\s\"'][ \t]*)?(?P<msg>You've hit your [A-Za-z0-9 .-]*limit\b[^\n]*(?:\n[ \t]{3,}(?![⎿❯●✻])[^\n]+)*)", re.M)
# A limit named by a window or kind is the program's; any other name is a model's own (Claude Code: "You've hit your
# Fable limit · resets Nov 20, 3pm (…)", Nov 16): that model is spent until it lifts, and the program's others still run.
KINDS = {"session", "usage", "weekly", "daily", "monthly", "monthly spend", "rate", "5-hour", "five-hour", "hourly",
         "spend", "credit", "token", "context", "request"}
NAMED = re.compile(r"You've hit your (?P<name>[A-Za-z0-9 .-]+?) limit\b")
SPENT_UNSAID = 86400        # a model's limit that names no time is tried again after a day
RESETS = re.compile(r"resets (?:(?P<day>[A-Z][a-z]{2} \d{1,2}), )?(?P<time>\d{1,2}(?::\d{2})?\s?[ap]m) \((?P<tz>[A-Za-z_/+-]+)\)")
AGAIN = re.compile(r"try again at (?P<when>[A-Z][a-z]{2} \d{1,2}(?:st|nd|rd|th)?, \d{4} \d{1,2}:\d{2} [AP]M)")


def time_zone(name):
    """The zone a limit's message names, also where its program printed it without the underscore (Claude Code shows
    America/Los_Angeles as America/LosAngeles, the underscore taken as emphasis); None where none matches."""
    from zoneinfo import ZoneInfo, available_timezones
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError):
        pass
    bare = name.replace("_", "").lower()
    match = [z for z in available_timezones() if z.replace("_", "").lower() == bare]
    return ZoneInfo(match[0]) if len(match) == 1 else None


def lifts(text, seen):
    """When a limit's message says it lifts (a timestamp), or None if it doesn't say (a monthly spend limit)."""
    from datetime import datetime, timedelta
    if m := AGAIN.search(text):
        when = re.sub(r"(\d)(st|nd|rd|th),", r"\1,", m.group("when"))
        return datetime.strptime(when, "%b %d, %Y %I:%M %p").timestamp()
    if not (m := RESETS.search(text)):
        return None
    if not (zone := time_zone(m.group("tz"))):
        return None
    clock = m.group("time").replace(" ", "")
    hour = datetime.strptime(clock, "%I:%M%p" if ":" in clock else "%I%p").time()
    base = datetime.fromtimestamp(seen, zone)
    if m.group("day"):
        day = datetime.strptime(f"{m.group('day')} {base.year}", "%b %d %Y")
        at = datetime.combine(day.date(), hour, zone)
        if at.timestamp() < seen - 180 * 86400:
            at = at.replace(year=base.year + 1)
        return at.timestamp()
    at = datetime.combine(base.date(), hour, zone)
    return (at + timedelta(days=1) if at.timestamp() < seen - 60 else at).timestamp()


def limit_name(line):
    """The model a limit's message names ("Fable"), or None where it names a window or kind of limit."""
    m = NAMED.search(line or "")
    name = m.group("name").strip() if m else ""
    return name if name and name.lower() not in KINDS and not name.lower().endswith(tuple(" " + k for k in KINDS)) else None


def matches(name, model, label=""):
    """Whether a model a limit named is this model: its words, in order, in the model's id or name ("Fable" is
    claude-fable-5-2 and any later Fable; "Opus 5.5" is claude-opus-5-5), whatever the models to come."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    if not words:
        return False
    # a name that ends on a version ("Opus 5") is that version, not a later one ("Opus 5.5")
    pattern = (r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(map(re.escape, words))
               + (r"(?![a-z0-9]|[^a-z0-9][0-9])" if words[-1].isdigit() else r"(?![a-z])"))
    return any(re.search(pattern, t.lower()) for t in (model, label) if t)


def spent_store():
    try:
        return json.loads((folder() / "spent.json").read_text())
    except (OSError, ValueError):
        return {}


def spent(key, clock=None):
    """A program's models whose own limit is spent now: {model id: when it lifts}. Auto passes over them and picks
    the best of the rest; they come back as their limit lifts."""
    from . import providers
    clock = time.time() if clock is None else clock
    names = {n: v for n, v in (spent_store().get(key) or {}).items() if clock < v["until"]}
    if not names or key not in providers.PROVIDERS:
        return {}
    return {m: max(v["until"] for n, v in names.items() if matches(n, m, label))
            for m, label in providers.available(providers.PROVIDERS[key])
            if any(matches(n, m, label) for n in names)}


def mark_spent(key, name, until, clock):
    """Keep a model's own limit as spent, until it lifts; True where that changed what is spent."""
    store = spent_store()
    mine = {n: v for n, v in (store.get(key) or {}).items() if clock < v["until"]}
    before = {n: v["until"] for n, v in mine.items()}
    known = mine.get(name.lower(), {}).get("until", 0)     # seen again without a time it can read: not sooner
    mine[name.lower()] = {"until": until or max(known, clock + SPENT_UNSAID), "at": clock}
    store[key] = mine
    folder().mkdir(parents=True, exist_ok=True)
    (folder() / "spent.json").write_text(json.dumps(store))
    return {n: v["until"] for n, v in mine.items()} != before


def lapsed(clock):
    """Drop model limits that have lifted; True where any did (Auto picks again, and the model comes back)."""
    store = spent_store()
    kept = {k: {n: v for n, v in m.items() if clock < v["until"]} for k, m in store.items()}
    if kept == store:
        return False
    (folder() / "spent.json").write_text(json.dumps(kept))
    return True


def repick(roots=()):
    """Auto picks again, as what is spent changed, and each project's helpers are written at their new tiers: a
    console then runs stale, and reloads into its conversation when idle (the watcher's reload)."""
    from . import bench, selection
    selection.reconcile()
    for p in roots or board.projects():
        if p.exists():
            bench.write_helpers(p)


def moved(root, key, clock):
    """Whether a project's agent now starts on a model that is not spent (Auto moved it, or the person's own
    choice is another); False where it would start on a spent one still."""
    from . import selection
    try:
        chosen = selection.main(root)
    except Exception:
        return False
    return bool(chosen) and chosen["model"] not in spent(key, clock)


def cut_off(screens=None, clock=None):
    """Agents a limit stopped mid-turn, outside any safe pause (it was off for them, or the limit came first): seen by
    the limit's message on their console, and woken once it lifts to carry on. Returns the projects woken."""
    from . import console
    clock = clock or time.time()
    path = folder() / "cut.json"
    try:
        was = json.loads(path.read_text())
    except (OSError, ValueError):
        was = {}
    from . import providers
    now, woken, pausing, changed = {}, [], paused(), set()
    if lapsed(clock):
        changed.add("*")
    for p in board.projects():
        if not p.exists():
            continue
        k, mine = str(p), was.get(str(p)) or {}
        name = console.session_name(p)
        # its whole scrollback, wrapped lines joined: each limit message is one more hit, however alike their words
        text = (screens or {}).get(k, "") if screens is not None else (console.history(name) if console.running(name) else "")
        hits = [" ".join(m.group("msg").split()) for m in CUT.finditer(text or "")]
        if k not in was or len(hits) < mine.get("n", 0):
            mine = {"n": len(hits)}                     # first seen, or a new console: what's there already is past
        if len(hits) > mine.get("n", 0):
            mine = {"n": len(hits)}
            name = limit_name(hits[-1])
            if name:                                    # a model's own: the program's others still run
                key = providers.key(providers.of(p))
                if mark_spent(key, name, lifts(hits[-1], clock), clock):
                    changed.add(key)
                mine.update(line=hits[-1], at=clock, model=name)
            elif k not in pausing:                      # a safe pause wakes its own projects
                mine.update(line=hits[-1], at=clock, until=lifts(hits[-1], clock))
        if mine.get("model"):
            mine = moving(p, mine, clock, changed) or {"n": mine["n"]}
        if mine.get("until") and clock >= mine["until"] + 60:
            board.add_note(p, None, f"A usage limit stopped you mid-work (it said: \"{mine['line']}\"). It has "
                           "lifted: carry on where you stopped.", author="colony", kind="resume")
            woken.append(p)
            mine = {"n": mine["n"]}
        if mine:
            now[k] = mine
    if changed:
        repick()                                        # a model's limit lifted, or one no project here met yet
    if now != was:
        folder().mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(now))
    return woken


def moving(p, mine, clock, changed):
    """A project a model's own limit stopped: Auto picks again (once a minute at most, where what is spent changed),
    then its console reloads onto the model it now starts on, when that is safe, and its agent is told to carry on.
    Where nothing else is open to it (its own choice is that model, or no other fits), it is woken as the limit lifts.
    Returns what is kept for it, or None once it is done."""
    from . import console, providers
    key = providers.key(providers.of(p))
    if changed:
        repick()
        changed.clear()
    name, nm = console.session_name(p), mine["model"]
    if not moved(p, key, clock):
        until = max(spent(key, clock).values(), default=None)
        return {"n": mine["n"], "line": mine["line"], "at": mine["at"], "until": until or clock + SPENT_UNSAID}
    if console.running(name) and console.stale(p, name):
        snap = console.snapshot(p, lines=1, name=name)
        if (snap["state"] != "idle" or console.drafting(name) or console.attached(name) or not console.room()
                or console.busy(p, name)):
            return mine                                 # its next free minute
        console.reload(p, name)
    chosen = __import__("colony.selection", fromlist=["main"]).main(p)
    board.add_note(p, None, f"{nm}'s own usage limit stopped you mid-work (it said: \"{mine['line']}\"). Colony's Auto "
                   f"now starts you on {chosen['model']}" + (f" at {chosen['effort']}" if chosen.get("effort") else "")
                   + f", the best model still open, until {nm} comes back, and your helpers likewise: carry on where you "
                   "stopped.", author="colony", kind="resume")
    return None


def twin(root, key):
    """A project already on the board that runs this one's work on another program: the same folder, or the
    name this one's would get (colony-codex for colony)."""
    from . import providers
    for p in board.projects():
        if p != root and providers.key(providers.of(p)) == key and (
                board.workdir(p) == board.workdir(root) or p.name == f"{root.name}-{key}"):
            return p
    return None


def week_down(root, pct, past):
    """What an agent is told when its own program's week passes its week limit: wind down as for the safe pause,
    and tell the person where each program stands; carrying on is theirs."""
    from . import providers
    weeks = "; ".join(f"{providers.PROVIDERS[k].label} {line(k) or 'not read'}" for k, x in providers.PROVIDERS.items()
                      if providers.usable(x))
    names = " and ".join(providers.PROVIDERS[v["program"]].label for v in past.values())
    return (f"Week pause: {names} {'is' if len(past) == 1 else 'are'} past {pct:g}% of the week, the limit the person set "
            f"({weeks}). Wind down now: let work already running finish, helpers included (don't "
            "cancel them), and take in what they hand back; commit what works so far (a clean point; the item stays in "
            "progress). Start nothing new. (Idle when it came: nothing to land.) Then end your turn by telling the person, briefly, where things stand (done, "
            "half-done, next) and where each program's week is, and wait for them: carrying on before the week resets "
            f"is theirs (colony settings --project {root.name} week_pause off, or a higher limit).")


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
    options.append(f"keep going past the limit: colony settings --project {root.name} safe_pause off")
    return (f"Safe pause: {prov.label} is at {v['used']:g}% of its {window} usage limit, which resets "
            f"{when(v.get('resets_at'))}. Wind down now, so nothing is cut off mid-task: there is a little room left, "
            "enough to land what's in flight, not to start more. Let work "
            "already running finish, helpers included (don't cancel them), and take in what they hand back; then commit "
            "what works so far (a clean point, not the item finished: the item stays in progress). Start nothing new: no new helpers or consultations. Then "
            "end your turn by telling the person, briefly, where things stand (done, half-done, next) and their "
            "options:\n" + "\n".join(f"  {i}. {o[0].upper() + o[1:]}" for i, o in enumerate(options, 1)))
