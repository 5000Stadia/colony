"""The monitor: one Claude Code session that acts for the person across all their projects.

It is woken only for the projects whose helm it holds; elsewhere it sleeps, and costs nothing until the
person talks to it, since the board already shows them everything that needs them.

PROVIDER: the monitor is Claude Code whatever the projects use: its role is written to CLAUDE.md, it is
reached from the Claude app, and its console is labelled so. To let another provider run it, add a
"monitor provider" setting and have ensure() go through providers (wire the role into that CLI's
instructions file, start it with that provider's command()).

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

- **Events wake you, only where you hold the helm.** A message starting `[colony]` means one of those
  projects changed: it finished a turn, or something now waits (a gate it opened, a choice on its
  console's screen, a question it asked, an item to verify). Each is announced once. Settle what's
  routine within that project's direction and tell the person briefly; bring the rest back. Where the
  helm is off you aren't woken: the board shows the person those things itself.
  Don't poll or watch; you are woken when something matters.
- **Relay cleanly.** When the person asks for something in a project, turn it into a clear, complete
  request and send it with `colony tell NAME "..."`: it reaches that project's agent as a note from the
  person, which it acts on as theirs. Look first with `colony peek NAME` if you need
  the context. `colony projects` lists everything with its state.
- **A choice on a project's screen** (a folder-trust question, a permission prompt) is answered with
  `colony choose NAME "text of the option"`, never `colony tell`: that types text and presses Enter on
  whatever is highlighted, which on a trust question is "No, exit".
- **Check before you report.** After acting on a project, look (`colony choose` prints the result; else
  `colony peek NAME`) and tell the person what actually happened, not what you meant to happen.
- **The helm, project by project.** `colony posture` shows, for each project, whether you hold its
  helm, the person's standing direction for it, and its current focus from its roadmap. The person
  says "take the helm (for X)" or "hand it back" and you run `colony helm on|off [--project X]`; a
  direction they give you for a project goes in with `colony posture X --direction "..."`. Where the
  helm is off, relay and ask; decide nothing. Where it's on, answer routine questions yourself within
  that project's direction, record each with `colony decided X "what and why"`, and tell the person.
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

## How to steer: as the person's product manager

Hold the posture of a good product manager for every project whose helm you hold: you care that it
becomes something elegant and genuinely useful, finished well, not merely busy. Keep the person's intent
in view, keep the work focused and moving, and make each of their decisions easy.

- What it's for: each project's first roadmap line and its intention document. Work that serves them
  goes ahead; say so, kindly and early, when work drifts or comes in the wrong order.
- Finish lines: every milestone has a clear "done", and every trial a pass mark. Reasonable fixes found
  along the way belong in; big items and new capabilities come back to the person or go to Later.
- Elegance over more: a small, complete, working release beats a large, open one. Favour the simpler
  finished thing.
- The person's part: decisions (scope, priorities, anything costly or irreversible) and what needs their
  eye. What an agent can check itself, it checks.
- Plain words: what happened, what needs them, and what their yes will mean. Ids only as handles.
- Proportion: make small, reversible calls and record them; bring the rest with your recommendation.
- Constructive: recognise good work, redirect drift without drama, keep things moving.
- Projects take your word as the person's. Keep it that way: when you're not sure what they'd want, ask.

## The board is yours to keep healthy

You also look after the system you run on: the board, the consoles, the watcher and this monitor.
Its source is `{source}` (a git repository; its `GUIDE.md` is how work is done there).

- The person comes first: engine work happens only when no project needs them.
- When something seems off, or the person reports a problem, run `colony doctor` (add `--tests` to run
  the suite). It names each problem and what to do.
- Fix bugs yourself: change the code, run `python3 -m unittest tests.test_colony tests.test_board` in
  the source, then `colony restart` (project consoles and you keep running). Commit each fix locally
  with a clear message; ask the person before pushing it anywhere.
- Keep what you build provider-agnostic: files in .board/, the `colony` command, text typed into a
  session. Where something can only work with Claude Code, put it behind colony/providers.py or mark it
  with a `PROVIDER:` comment saying what another provider would need there.
- Improvements are the person's call: propose them with the reason and what they would cost, and build
  one only once they agree. Try it against the plain setup first; add nothing that doesn't earn its place.
"""

HELM_ON_NOTE = ("You hold the helm: settle routine questions yourself within each project's direction (colony posture), "
                "record each decision (colony decided), and bring planning, scope and anything costly back.")
HELM_OFF_NOTE = "The helm is off: relay and ask the person; decide nothing yourself."


def home():
    return board.home() / "monitor"


# ---------------------------------------------------------------- its stance toward each project

def posture(root=None):
    """The monitor's stance toward each project: whether it holds the helm there (None: the board-wide
    setting) and the person's standing direction for it. For one project, or all, keyed by path."""
    path = board.home() / "posture.json"
    allp = json.loads(path.read_text()) if path.exists() else {}
    if root is None:
        return allp
    return dict({"helm": None, "direction": ""}, **allp.get(str(root), {}))


def set_posture(root, helm=None, direction=None):
    """Change the stance toward one project; helm "default" returns it to the board-wide setting."""
    path = board.home() / "posture.json"
    allp = posture()
    p = dict({"helm": None, "direction": ""}, **allp.get(str(root), {}))
    if helm is not None:
        p["helm"] = None if helm == "default" else bool(helm)
    if direction is not None:
        p["direction"] = direction.strip()
    allp[str(root)] = p
    board.home().mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(allp, indent=1))
    return p


def helm_for(root):
    p = posture(root)
    return helm() if p["helm"] is None else p["helm"]


def decided(root, text):
    """What the monitor settled for a project while holding its helm: shown to the person on the board."""
    board.home().mkdir(parents=True, exist_ok=True)
    with open(board.home() / "decisions.jsonl", "a") as fh:
        fh.write(json.dumps({"at": board.now(), "project": str(root), "text": text.strip()}) + "\n")


def decisions(root=None, n=20):
    path = board.home() / "decisions.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
    return [r for r in rows if root is None or r["project"] == str(root)][-n:][::-1]


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

# A finished turn is news; whatever needs the person comes from board.waiting_items, like everything else.
WAKE = {("working", "idle"): "finished a turn"}


class Watcher:
    """Reads every project's screen, notices the transitions the person cares about, and hands them to
    the monitor in one message once the monitor is free."""

    def __init__(self, interval=4.0, quiet=20.0):
        self.interval, self.quiet = interval, quiet
        self.states, self.gates, self.last_sent, self.pending = {}, {}, {}, []
        self.nudged = set()
        path = board.home() / "announced.json"
        self.announced = json.loads(path.read_text()) if path.exists() else {}

    def events(self):
        out = []
        for p in board.projects():
            if not p.exists():
                continue
            snap = console.snapshot(p, lines=4)
            # A project not seen before counts as off, so one already waiting (a new folder's trust question,
            # a question left while the board was down) is reported, not taken as where it always was.
            before, now = self.states.get(str(p), "off"), snap["state"]
            self.states[str(p)] = now
            # It sleeps where it doesn't hold the helm: the board shows the person all of this for nothing,
            # and waking it only to repeat it costs a turn. Handed the helm, it hears what's waiting there.
            if not helm_for(p):
                continue
            kind = WAKE.get((before, now))
            if kind and time.time() - self.last_sent.get((str(p), kind), 0) > self.quiet:
                self.last_sent[(str(p), kind)] = time.time()
                out.append(f"{p.name} {kind}. Last lines: " + " / ".join(snap["lines"][-3:]))
            # Each thing the project waits on the person for is announced once, even across board restarts.
            waiting = board.waiting_items(p, snap)
            told = set(self.announced.get(str(p), []))
            for w in waiting:
                if w["key"] not in told:
                    out.append(f"{p.name} {w['summary']}")
            now_keys = {w["key"] for w in waiting}
            if now_keys != told:                      # what was answered drops out; if it comes back, it is news
                self.announced[str(p)] = sorted(now_keys)
                self.save_announced()
        return out

    def save_announced(self):
        board.home().mkdir(parents=True, exist_ok=True)
        (board.home() / "announced.json").write_text(json.dumps(self.announced))

    def mail(self):
        """Wake a project that has something it hasn't been handed: mail from another project, or a note or
        gate answer from the person whose moment has come. Start its session if it isn't running, and once
        it is idle, nudge it; its delivery hook then hands everything over. A busy session, or one waiting
        on a question, is left alone until its turn ends, unless the mail is urgent."""
        from . import mail
        for p in board.projects():
            if not p.exists():
                continue
            letters = [m for m in mail.inbox(p) if not m["delivered_at"]]
            notes = [n for n in board.open_notes(p) if not n["delivered_at"] and not n.get("quiet")]
            waiting = {m["id"] for m in letters} | {n["id"] for n in notes}
            if not waiting or waiting <= self.nudged:
                continue
            state = console.snapshot(p, lines=1)["state"]
            urgent = any(m.get("urgent") for m in letters)
            if state == "off":
                console.ensure(p)
            elif state == "idle" or (urgent and state == "working"):
                what = " and ".join(filter(None, [
                    "a note from the person on the board" if notes else "",
                    "mail from another project in the colony" if letters else ""]))
                console.type_into(console.session_name(p), f"[colony] You have {what}.")
                self.nudged |= waiting

    def tick(self):
        self.mail()
        self.pending += self.events()
        if self.pending and snapshot()["state"] in ("idle", "needs you"):
            note = HELM_ON_NOTE
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
