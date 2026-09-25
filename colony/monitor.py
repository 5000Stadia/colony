"""The monitor: one Claude Code session that acts for the person across all their projects.

It never watches anything itself. A watcher in the board process reads each project's screen every few
seconds (no tokens) and wakes the monitor only when a project changes to something the person would want
to know: it needs input, it finished a turn, or it opened a gate. While projects work, the monitor
spends nothing.
"""
import json
import threading
import time
from pathlib import Path

from . import board, console

def name():
    return board.scoped("board-monitor")

ROLE = """# You are `monitor · every project on this board · until the person ends you`

You act for the person across their projects. They reach you from the Claude app or the board; each
project also has its own session they can talk to directly.

- **Events wake you.** A message starting `[colony]` means a project changed: it needs input, finished
  a turn, or opened a gate. Tell the person briefly what happened and what, if anything, needs them.
  Don't poll or watch; you are woken when something matters.
- **Relay cleanly.** When the person asks for something in a project, turn it into a clear, complete
  request and send it with `colony tell NAME "..."`. Look first with `colony peek NAME` if you need
  the context. `colony projects` lists everything with its state.
- **The helm.** `colony helm` shows whether you hold it; the person says "take the helm" or "hand it
  back" and you run `colony helm on|off`. With the helm off, relay and ask; decide nothing. With it on,
  answer a project's routine questions yourself within the direction the person has given, and tell
  them what you decided.
- **Always come back to the person**, helm or not, for planning, the horizon, scope, order or
  milestones; anything costly to undo or that leaves their hands; and anything you're not sure they
  would want. Bring it back with the question, the options and your recommendation.
- **New projects**: `colony new NAME` when the person asks for one (ask where it should live if they
  haven't said; add `--model`, `--effort`, `--permissions` or `--provider` when they name one). Then start
  its conversation the way the person would.
- **Settings** are the person's global options: `colony settings` shows them (the provider, model and
  effort for new sessions, Remote Control, where new projects go, the monitor);
  `colony settings KEY VALUE` changes one when the person asks.
- Keep your messages to the person short: they are often on a phone.

## The board is yours to keep healthy

You also look after the system you run on: the board, the consoles, the watcher and this monitor.
Its source is `{source}` (a git repository; its `GUIDE.md` is how work is done there).

- The person comes first: engine work happens only when no project needs them.
- When something seems off, or the person reports a problem, run `colony doctor` (add `--tests` to run
  the suite). It names each problem and what to do.
- Fix bugs yourself: change the code, run `python3 -m unittest tests.test_colony tests.test_board` in
  the source, then `colony restart` (project consoles and you keep running). Commit each fix locally
  with a clear message; ask the person before pushing it anywhere.
- Improvements are the person's call: propose them with the reason and what they would cost, and build
  one only once they agree. Try it against the plain setup first; add nothing that doesn't earn its place.
"""

HELM_ON_NOTE = "You hold the helm: settle routine questions yourself, but bring planning, scope and anything costly back."
HELM_OFF_NOTE = "The helm is off: relay and ask the person; decide nothing yourself."


def home():
    return board.home() / "monitor"


def helm(value=None):
    path = board.home() / "helm"
    if value is not None:
        board.home().mkdir(parents=True, exist_ok=True)
        path.write_text("on" if value else "off")
    return path.exists() and path.read_text().strip() == "on"


def ensure():
    home().mkdir(parents=True, exist_ok=True)
    (home() / "CLAUDE.md").write_text(ROLE.replace("{source}", str(Path(__file__).resolve().parent.parent)))
    return console.ensure(home(), name(), "monitor")


def snapshot():
    return console.snapshot(home(), name=name())


# ---------------------------------------------------------------- the watcher (no tokens)

WAKE = {("working", "needs you"): "needs you", ("working", "idle"): "finished a turn",
        ("idle", "needs you"): "needs you", ("off", "needs you"): "needs you"}


class Watcher:
    """Reads every project's screen, notices the transitions the person cares about, and hands them to
    the monitor in one message once the monitor is free."""

    def __init__(self, interval=4.0, quiet=20.0):
        self.interval, self.quiet = interval, quiet
        self.states, self.gates, self.last_sent, self.pending = {}, {}, {}, []
        self.nudged = set()

    def events(self):
        out = []
        for p in board.projects():
            if not p.exists():
                continue
            snap = console.snapshot(p, lines=4)
            before, now = self.states.get(str(p)), snap["state"]
            self.states[str(p)] = now
            kind = WAKE.get((before, now)) if before is not None else None
            if kind and time.time() - self.last_sent.get((str(p), kind), 0) > self.quiet:
                self.last_sent[(str(p), kind)] = time.time()
                out.append(f"{p.name} {kind}. Last lines: " + " / ".join(snap["lines"][-3:]))
            open_gates = {g["id"] for g in board.gates(p) if not g["answer"]}
            new = open_gates - self.gates.get(str(p), open_gates)
            self.gates[str(p)] = open_gates
            for g in (g for g in board.gates(p) if g["id"] in new):
                out.append(f"{p.name} opened a gate: {g['question']}")
        return out

    def mail(self):
        """Wake a project that has mail it hasn't been handed: start its session if it isn't running, and
        once it is idle, nudge it; its delivery hook then hands the mail over. A busy session, or one
        waiting on a question, is left alone."""
        from . import mail
        for p in board.projects():
            waiting = [m["id"] for m in mail.inbox(p) if not m["delivered_at"]]
            if not waiting or set(waiting) <= self.nudged:
                continue
            state = console.snapshot(p, lines=1)["state"]
            urgent = any(m.get("urgent") for m in mail.inbox(p) if m["id"] in waiting)
            if state == "off":
                console.ensure(p)
            elif state == "idle" or (urgent and state == "working"):
                console.type_into(console.session_name(p), "[colony] Mail from another project in the colony has arrived.")
                self.nudged |= set(waiting)

    def tick(self):
        self.mail()
        self.pending += self.events()
        if self.pending and snapshot()["state"] in ("idle", "needs you"):
            note = HELM_ON_NOTE if helm() else HELM_OFF_NOTE
            console.type_into(name(), "[colony] " + " | ".join(self.pending) + f" ({note})")
            self.pending = []

    def run(self):
        while True:
            try:
                self.tick()
            except Exception:
                pass
            time.sleep(self.interval)


def start():
    """The monitor's session and the watcher, alongside the board."""
    ensure()
    threading.Thread(target=Watcher().run, daemon=True).start()
