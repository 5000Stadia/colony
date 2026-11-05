"""Idle sessions hibernate (R142): a project's console idle a day, with nothing set to wake it, closes until the next
word for it, then resumes in its own conversation.

The person, 2026-11-01: "Can we have sessions that are inactive or 24 hours and have no timers to wake up go into like
a hibernate state where the session actually closes until the next time the user sends a message or something triggers
it to wake up?"

The watcher looks every CHECK seconds, with no model and no tokens (screens, /proc, file times). A console sleeps when
it has taken no turn for HIBERNATE seconds and nothing keeps it awake (awake() says what does: a wake of its own set, a
helper, a background command or a turn its program runs elsewhere, something typed in its box, a turn mid-way or a
choice on its screen, someone at its terminal, something waiting to be handed to it, a safe pause or a usage limit that
wakes it at the reset, a close or a lead change under way). The steward never sleeps here: its mute is its own.

Asleep, its program stops (PROVIDER: Provider.hibernate, Codex's app host drained with it; the installation's repair
host is another thing and stays up) and its tmux session closes, as `colony stop` closes one; hibernating.json in the
board's home records when, and the conversation it resumes. Anything that would reach it starts it first
(console.ensure, which resumes the conversation colony remembers for it, and clears the record), then delivers as
before: console.type_into and paste_into do so for every path that types into a console, and the watcher's delivery of
notes and mail starts an off console itself. Being looked at on the board doesn't wake it; a key pressed in its
console page does. While it sleeps the Claude and ChatGPT apps can't reach it (their link is the session); the board
always can, and its message wakes it. Any message in colony wakes it (the person, 2026-11-01: "Sending any message in
colony should wake"): a note, quiet ones too (the watcher wakes it and says so), mail, a suggestion, colony tell; a
project the person paused stays as it is.

From cold (colony started after the machine restarted, or after `colony stop`), colony starts with most projects asleep:
only those that took a turn in the day before colony last ran, or that something keeps awake, come back running
(cold(), recovery.rebooted); the rest are recorded asleep with their conversation, never started (sleep_cold()).
"""
import json
import time
from pathlib import Path

from . import board, console

HIBERNATE = 24 * 3600        # seconds without a turn before a console may sleep
CHECK = 5 * 60               # how often the watcher looks
WAKE_WAIT = 60               # seconds a word for a sleeping console waits for its box once it is started again
_looked = [0.0]
_woke = {}                   # consoles this process woke: {session: when}

SAYS = ("Its next message wakes it, in its own conversation; while it sleeps, the Claude and ChatGPT apps can't "
        "reach it.")


def _path():
    return board.home() / "hibernating.json"


def rows():
    """The projects asleep: {path: {at, since, conversation, provider, session}}."""
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def asleep(root):
    """The record of this project's sleep, or None."""
    return rows().get(str(Path(root))) if root is not None else None


def root_of(name):
    """The sleeping project whose console is `name`, or None."""
    for path in rows():
        if console.session_name(Path(path)) == name:
            return Path(path)
    return None


def _write(change):
    with board.board_lock("hibernating"):
        now = rows()
        out = change(dict(now))
        if out != now:
            board.home().mkdir(parents=True, exist_ok=True)
            board.save_json(_path(), out)


def clear(root):
    """Awake again (console.ensure calls this whenever it starts a project's console)."""
    if asleep(root):
        _write(lambda r: (r.pop(str(Path(root)), None), r)[1])
        _woke[console.session_name(root)] = time.time()


def woke_recently(name):
    """Whether this process woke that console within WAKE_WAIT seconds: a message for it waits longer for its box."""
    return time.time() - _woke.get(name, 0) < WAKE_WAIT


def since(row):
    from . import hours
    return hours.at_day(row.get("since") or time.time())


def line(row):
    """What the board says of a sleeping console."""
    if row.get("cold"):
        return f"Asleep since colony started ({since(row)}): it took no turn in the day before colony last ran. {SAYS}"
    return f"Asleep since {since(row)}, after a day with nothing to do. {SAYS}"


def last_turn(root, name):
    """When it last took a turn, as near as colony can tell with no model: its conversation's file last written, or
    its console's start, whichever is later."""
    from . import context
    seen = 0.0
    try:
        s = context.session(root)
        if s and s.get("path"):
            seen = Path(s["path"]).stat().st_mtime
    except (OSError, TypeError, ValueError):
        pass
    age = console.age(name)
    return max(seen, time.time() - age if age != float("inf") else 0.0)


def _cut(root):
    """A usage limit stopped it mid-work, and colony wakes it once that lifts (usage.cut_off reads its screen)."""
    from . import usage
    try:
        return bool((json.loads((usage.folder() / "cut.json").read_text()).get(str(root)) or {}).get("until"))
    except (OSError, ValueError, AttributeError):
        return False


def held(root):
    """What keeps a project awake whether its console runs or not, in a few words, or None: a wake of its own, a close,
    a safe pause or a usage limit waiting to lift, something waiting to be handed to it, a lead change, a context
    refresh. awake() reads these for a running console; cold() for one colony would bring back from cold."""
    from . import context, lead, mail, usage, wakes
    if wakes.waiting(root):
        return "a wake of its own is set"
    if str(root) in board.closing():
        return "it is being closed"
    if str(root) in usage.paused():
        return "the safe pause holds it until the reset"
    if _cut(root):
        return "a usage limit stopped it mid-work; colony wakes it as it lifts"
    # The person's pause holds these until resume; they aren't work the console can take now.
    if not board.paused(root) and (
            any(not n["delivered_at"] and not n.get("quiet") for n in board.open_notes(root))
            or any(not m["delivered_at"] for m in mail.inbox(root))):
        return "something waits to be handed to it"
    try:
        if lead.info(root).get("handoff"):
            return "its lead is changing"
    except Exception:
        return "its lead record can't be read"
    if context.refreshing(root):
        return "its context is being refreshed"
    return None


def awake(root, now=None):
    """What keeps this project's console awake, in a few words, or None when it may sleep. Read cheapest first: most
    consoles took a turn within the day."""
    from . import lingering, monitor
    from . import chat
    now = time.time() if now is None else now
    root = Path(root)
    if root.resolve() == monitor.home().resolve():
        return "the steward"
    name = console.session_name(root)
    if not console.running(name):
        return "not running"
    turn = last_turn(root, name)
    if now - turn < HIBERNATE:
        return "took a turn within the day"
    why = held(root)
    if why:
        return why
    snap = console.snapshot(root, lines=4, name=name)
    if snap["state"] != "idle":
        return snap["state"]                                   # mid-turn, or a choice on its screen
    if console.drafting(name):
        return "something is typed in its box"
    if console.attached(name):
        return "someone is at its terminal"
    why = console.busy(root, name)                             # PROVIDER: a turn or command its screen doesn't show
    if why:
        return why
    if lingering.commands(root, name):
        return "a background command still runs"
    if chat.helpers(snap):
        return "a helper still runs"
    return None


def hibernate(root, now=None):
    """Put its console to sleep: its program's own host first (PROVIDER: hibernate(), which may refuse), then the
    record, then its session. Whether it sleeps now."""
    from . import providers
    now = time.time() if now is None else now
    root, name = Path(root), console.session_name(root)
    with console._typing(name, wait=0) as mine:                 # nothing is being typed into it as it closes
        if not mine or console.drafting(name) or console.snapshot(root, lines=1, name=name)["state"] != "idle":
            return False
        prov = providers.of(root)
        try:
            if not prov.hibernate(root):
                return False
        except Exception:
            return False
        saved = console.saved_conversation(root) or {}
        row = {"at": board.now(), "since": now, "conversation": saved.get("conversation"),
               "provider": providers.key(prov), "session": name}
        _write(lambda r: (r.__setitem__(str(root), row), r)[1])
        console.stop(root)
    return True


def cold(root, ran):
    """At a cold start (recovery.rebooted: the machine restarted, or colony started again after it was stopped), what
    brings this project's console back, in a few words, or None: it starts asleep. `ran` is when colony last ran (the
    watcher's last record of what runs, boot.json's `at`), not now, so a machine off a week doesn't put every project
    to sleep. The person, 2026-11-01: "Colony could start in first load full hybernate except for projects last active
    in the 24 hours colony was last running"."""
    from . import monitor
    root = Path(root)
    if root.resolve() == monitor.home().resolve():
        return "the steward"
    if not ran:
        return "when colony last ran isn't known"
    if ran - last_turn(root, console.session_name(root)) < HIBERNATE:
        return "took a turn within the day before colony last ran"
    return held(root)


def sleep_cold(root, now=None):
    """Record a project asleep from a cold start, its console never started: the conversation it resumes, as
    hibernate() records it. PROVIDER: hibernate() lets go of a host its program left running from before a stop, and
    may refuse; then it isn't recorded and comes back as before. Whether it sleeps."""
    from . import providers
    now = time.time() if now is None else now
    root, name = Path(root), console.session_name(root)
    if console.running(name):
        return False
    prov = providers.of(root)
    try:
        if not prov.hibernate(root):
            return False
    except Exception:
        return False
    saved = console.saved_conversation(root) or {}
    row = {"at": board.now(), "since": now, "conversation": saved.get("conversation"),
           "provider": providers.key(prov), "session": name, "cold": True}
    _write(lambda r: (r.__setitem__(str(root), row), r)[1])
    return True


def wake(root, settle=0):
    """Start a sleeping project's console again in its own conversation (console.ensure clears the record). Its
    console's name, or None when it wasn't asleep."""
    if not asleep(root):
        return None
    return console.ensure(Path(root), settle=settle)


def tick(now=None):
    """The watcher's look, every CHECK seconds: a record whose console is running again (started some other way) or
    whose project left the board is dropped; each console that may sleep, sleeps."""
    if board.restore_held():
        return []
    now = time.time() if now is None else now
    if now - _looked[0] < CHECK:
        return []
    _looked[0] = now
    on = {str(p): p for p in board.projects() if p.exists()}
    stale = [path for path in rows() if path not in on or console.running(console.session_name(Path(path)))]
    if stale:
        _write(lambda r: {k: v for k, v in r.items() if k not in stale})
    slept, sleeping = [], rows()
    for path, root in on.items():
        if path in sleeping:
            continue
        try:
            if awake(root, now) is None and hibernate(root, now):
                slept.append(root)
        except Exception:
            continue                                           # one project's trouble stops no other's
    return slept
