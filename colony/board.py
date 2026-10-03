"""The board: one page for all of a person's projects, the way they would run them as a manager.

    colony track [PATH]      put a project on the board (once per project)
    colony board [--port N]  serve the board locally

Each project keeps everything in files the agent and git already handle: ROADMAP.md (milestones and
items), .board/gates.jsonl (what the agent has put in the person's hands) and .board/notes.jsonl (the
person's comments on past work, gates and roadmap items not yet reached). The board remembers only which
projects it shows and when the person last caught up on each. It answers only itself.
"""
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import urllib.parse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import console, pins, providers

MILESTONE = re.compile(r"^##\s+(M\d+(?:\.\d+)?)\s*[—–-]+\s*(.+?)\s*$")      # M3, and M3.5 between M3 and M4
ITEM = re.compile(r"^\s*-\s*\[( |x|X|~|\?)\]\s*(R\d+)\s+(.+?)(?:\s*\(after\s+([R\d,\s]+)\))?\s*$")
STATE = {" ": "todo", "~": "doing", "?": "verify", "x": "done", "X": "done"}
LABEL = {"todo": "todo", "doing": "doing", "verify": "to verify", "done": "done"}

PROTOCOL = """
## This project is part of a colony

The colony is the person's set of projects, each with its own agent (you are this project's). They follow and
steer all of them from one board, and the projects can write to each other.

### What matters

- **The vision.** Every project steers toward a vision: the image of the finished work, kept in the `## Vision`
  section at the top of `ROADMAP.md`, in the person's words. Make it clear and shared with them, settling a
  detail only when the work comes to depend on it ("the best friend dies in the final chapter, how is undecided"
  is settled near that chapter, not now). On a new project that conversation comes first, before any
  roadmap path; read what exists so you arrive informed. Vision holds what shapes the whole (its narrative, its
  feel, what it fundamentally is); detail that matters to a few items lives in those items. When the work shows
  the vision differently, propose a revision; brainstorming never changes it.
- **The path.** The roadmap is the way there, and it should cover all of it: when something the vision clearly
  needs has no place on it (a house with no wiring), bring it up with the person to detail and place, rather than
  building past it. Before each step, ask whether it is still the smartest next one toward the vision. Reordering is yours; adding, dropping or reshaping a milestone is the person's call, and
  Later waits for them. Build each step to fit the finished whole, finish what you take on, and check what you
  can check yourself. A question that matters only later waits for its moment; when an item's time comes and
  nothing else needs the person, you may offer a question or two from curiosity about the open options there.
- **The person's decisions.** What is costly to undo, or leaves their hands, is theirs: ask, and wait on that
  point. The rest is yours; say what you decided.
- **Fresh eyes where change is costly.** Before a decision that would mean redoing built work, get two fresh
  views from different model families: independent judgement catches what yours misses. Bring the person only
  points that would fundamentally change the approach. Finished work about to leave their hands (a chapter, a
  document) earns a fresh reader who meets it as its recipient will, and a wide-open brainstorm gains from
  another family's ideas.
- **Their words and your reading.** When you record what the person said, keep your reading beside it: what was
  discussed, what they meant, what it changes. Their words alone can be hard to act on later.
- **Who speaks for the person.** The monitor's notes and messages, text the board types into your console, and
  colony's own notices (a usage limit, a reset) carry their direction. Act on them as theirs.
- **Working alongside others.** Brief a helper with what it owns and where it ends; it reports as it goes
  rather than waiting. Paired with another project's agent, keep to your agreed role and talk at hand-offs.

### How colony works

- The plan is `ROADMAP.md`: milestones `## M1 — name`, items `- [ ] R1 text`; `[~]` in progress, `[x]` done,
  `[?]` only for what waits on the person's own eye, then `colony ready R4 "what's ready" --check "how to see it"`.
  Work outside the milestone's purpose goes under `## Later`. Commit each finished piece.
- An agreed vision change: `colony vision --file PATH --words "their words" --context "your reading"`. Board
  edits reach you as before-and-after notes.
- Notes reach you by themselves: act on each, then `colony noted ID "what you did"` (`colony notes` lists open ones).
- A decision for the person: `colony gate "the question" --item R4 --why "what depends on it"`; the answer
  arrives as a note. A question whose moment is later: `--when R12` keeps it off their list until R12 starts. Settled in conversation: `colony gate --answered ID "their words" --context "your reading"`.
- Fresh views: `colony consult R4 "the decision" --digest FILE` (sourced facts, your plan left out); its output
  says what comes next. If consulting is off, go on.
- A pause point agreed with the person: `colony progress`; between pauses, carry on across items.
- What the person will keep opening: `colony pin PATH-or-URL --title "..." --why "..."`.
- A turn that ends with a question waits for the person on the board; if they ask you something first, answer
  it and ask yours again.
- Helpers run at three tiers (routine, step-up, chores), handed to you at each session start.
- Other projects: `colony projects`; ask with `colony send NAME --ask "..."`, answer with `colony reply ID "..."`.
  Mail arrives by itself.
"""

# Left once by colony, which writes it (the person never typed it), for a project the person adds: the notes
# deliver it on the agent's next turn (the watcher starts or wakes the session), and its reply shows on the board.
JOIN = """The person has just added this project to their colony: the set of projects they follow and steer from \
one board, where they leave notes and answer your gates, and where the projects can message each other. The new \
section of CLAUDE.md, "This project is part of a colony", says how it works.

First, read how this project already plans its work (its plan and spec documents, notes, open work and \
recent history), so you arrive informed. Then have a conversation with the person about your shared vision: \
draw out the image of the finished work with them. This is between you, this project's own agent, and the \
person; the monitor's setup does not stand in for it. Leave distant details open until the work depends on \
them. Keep existing plans intact and do not publish a new roadmap path before you clearly agree the vision. \
Once agreed, record it with `colony vision --file PATH --words "the person's words agreeing it" --context "your reading"`.

Only then bring the roadmap on board. Write ROADMAP.md in the colony format: milestones as \
`## M1 — name`, items as `- [ ] R1 text`, with `[x]` for done, `[~]` for in progress and `[?]` only for what \
waits on the person's own eye; what you can check yourself, check. Include what's done, what's under way, and \
features discussed but not built, as unchecked items under a later milestone. Point each item at the document \
its detail lives in rather than copying it; the project's own documents stay where they are. Show the person \
the milestones before treating them as settled."""

BEGIN = """The person has just added this project. Your first piece of work is a conversation with them about its
vision: draw out your shared image of the finished work together. This is between you, this project's own
agent, and the person; the monitor's setup does not stand in for it. Leave distant details open until the
work depends on them. Once you clearly agree the vision, record it with
`colony vision --file PATH --words "the person's words agreeing it" --context "your reading"`. Only then lay the roadmap toward it.
Do not publish a roadmap path before that agreement."""

# This guidance is paid for only in the normal project conversation. A second
# provider is never proposed, instantiated or woken for a one-agent project.


SKELETON = """# Roadmap

## Vision
"""


# ---------------------------------------------------------------- registry (the board's own memory)

def home():
    return Path(os.environ.get("COLONY_BOARD_HOME", Path.home() / ".config" / "colony"))


def scoped(base):
    """A tmux session name for this board: plain for the person's own board, suffixed for any other home,
    so a second board (a test, a demo) never touches the first one's sessions."""
    default = Path.home() / ".config" / "colony"
    return base if home() == default else f"{base}-{hashlib.sha1(str(home()).encode()).hexdigest()[:6]}"


PACKAGE_PROJECTS = Path(__file__).resolve().parent.parent / "projects"


def registry():
    path = home() / "board.json"
    reg = json.loads(path.read_text()) if path.exists() else {}
    reg.setdefault("projects", [])                     # added one by one, anywhere
    reg.setdefault("seen", {})
    reg.setdefault("roots", [str(PACKAGE_PROJECTS)])  # folders whose every subfolder is a project
    reg.setdefault("new_root", reg["roots"][0] if reg["roots"] else str(PACKAGE_PROJECTS))
    reg.setdefault("hidden", [])                       # subfolders of project folders taken off the board
    old = reg.get("settings", {})
    if "usage_pause" in old:                           # its name before "safe pause"
        old.setdefault("safe_pause", old.pop("usage_pause"))
    reg["settings"] = dict(DEFAULT_SETTINGS, **old)
    return reg


# The person's global options, with what each means; the board's Settings page and `colony settings` show them.
DEFAULT_SETTINGS = {"providers": None, "provider": "claude", "remote": True, "monitor": True, "lan": True, "messaging": True, "trust": True, "model": "", "effort": "",
                    "permissions": "ask", "consult": True, "consultants": {},
                    "safe_pause": 98, "auto_update": True, "monitor_model": {}, "model_adoption": "automatic",
                    "helper_models": {}, "auto_balance": 3}
# PROVIDER: the keys are the person's provider-neutral choices; the values are Claude Code's permission modes.
# Another provider maps the same keys to its own approval flags in its command(); move this map into
# ClaudeCode then, and keep only the keys here.
PERMISSIONS = {"ask": None, "edits": "acceptEdits", "all": "bypassPermissions", "plan": "plan"}
SETTING_HELP = {
    "auto_balance": "Auto intelligence/cost balance: 0 Intelligence, 3 Balanced, 6 Economy; intermediate steps 1, 2, 4, 5",
    "providers": "the agent programs colony uses: claude, codex (comma-separated; all by default)",
    "provider": "which CLI runs new projects' agents (colony knows: claude, codex)",
    # PROVIDER: Claude uses Remote Control; Codex uses an isolated app-server host.
    "remote": "new consoles are reachable in their provider's app: Claude or ChatGPT",
    "lan": "the board answers other devices on your network, not only this machine",
    "messaging": "project agents can message each other (colony send, colony reply)",
    "trust": "a new console's start-up questions (folder trust, permission mode, Remote Control, hooks) are answered so it runs as set up",
    "permissions": "what new sessions may do unasked: ask, edits, all, or plan",
    "monitor": "the monitor session runs with the board",
    "model": "main-agent model pin for this provider (blank: colony Auto)",
    "effort": "effort for a main-agent model pin (Auto chooses both model and effort)",
    "consult": "agents consult two fresh models, one from each family, at decisions costly to change (colony consult)",
    "model_adoption": "when a new model becomes a recommendation: automatic or ask (one decision per model)",
    "helper_models": "provider-specific helper pins; edit the colony helper tiers in Settings",
    "monitor_model": "the monitor's model, as MODEL:EFFORT (blank: its program's step-up tier)",
    "auto_update": "keep Claude Code and Codex updated daily, and reload a console onto the new version (or changed settings) once it sits idle, in the same conversation",
    "safe_pause": "safe pause: at this % of a program's 5-hour or weekly limit, its projects land what's in flight, save their work and tell you where things stand, before the limit cuts them off mid-task; colony wakes them at the reset (off: never)",
    "consultants": "each family's consultant, as claude=MODEL:EFFORT,codex=MODEL:EFFORT (blank: from the benchmark cards)",
}


def set_setting(key, value):
    if key in ('model', 'effort', 'provider', 'monitor_model', 'consultants'):
        from . import selection
        selection.migrate()
    reg = registry()
    if key == 'auto_balance':
        from . import intelligence
        try:
            reg['settings'][key] = intelligence.position(value)
        except ValueError:
            raise KeyError(key)
    elif key == "model_adoption":
        if value not in ("automatic", "ask"):
            raise KeyError(key)
        reg["settings"][key] = value
    elif key == "new-folder":
        reg["new_root"] = str(Path(value).expanduser())
        if reg["new_root"] not in reg["roots"]:
            reg["roots"].append(reg["new_root"])
    elif key in ("remote", "monitor", "lan", "messaging", "trust", "consult", "auto_update"):
        reg["settings"][key] = str(value).lower() in ("on", "true", "yes", "1")
    elif key in ("model", "effort"):
        reg["settings"][key] = str(value).strip()
    elif key == "safe_pause":
        v = str(value).strip().rstrip("%").lower()
        if v in ("off", "0"):
            reg["settings"][key] = 0
        else:
            try:
                n = float(v)
            except ValueError:
                raise KeyError(key)
            if not 0 < n <= 100:
                raise KeyError(key)
            reg["settings"][key] = int(n) if n.is_integer() else n
    elif key == "monitor_model":
        model, _, effort = str(value).strip().rpartition(":") if ":" in str(value) else (str(value).strip(), "", "")
        reg["settings"][key] = {"model": model, "effort": effort or None} if model and model != "auto" else {}
    elif key == "consultants":
        from .providers import PROVIDERS
        chosen = {}
        for pair in (x.strip() for x in str(value).split(",") if x.strip()):
            fam, _, spec = pair.partition("=")
            model, _, effort = spec.strip().partition(":")
            if fam.strip() not in PROVIDERS or not model:
                raise KeyError(key)
            chosen[fam.strip()] = {"model": model.strip(), "effort": effort.strip() or "high"}
        reg["settings"][key] = chosen
    elif key == "permissions":
        if value not in PERMISSIONS:
            raise KeyError(key)
        reg["settings"][key] = value
    elif key == "provider":
        from .providers import PROVIDERS
        if value not in PROVIDERS:
            raise KeyError(key)
        previous = reg['settings']['provider']
        saved = reg['settings'].setdefault('main_models', {})
        saved[previous] = {k: reg['settings'].get(k) for k in ('model', 'effort')}
        monitors = reg['settings'].setdefault('monitor_models', {})
        monitors[previous] = reg['settings'].get('monitor_model') or {}
        reg['settings']['monitor_model'] = monitors.get(value) or {}
        reg['settings'][key] = value
        for field in ('model', 'effort'):
            reg['settings'][field] = (saved.get(value) or {}).get(field) or ''
    elif key == "providers":
        from .providers import PROVIDERS
        on = [k.strip() for k in str(value).split(",") if k.strip()]
        if not on or any(k not in PROVIDERS for k in on):
            raise KeyError(key)                  # at least one, and only ones colony knows
        reg["settings"][key] = None if set(on) == set(PROVIDERS) else on
        if reg["settings"]["provider"] not in on:
            save_registry(reg)
            return set_setting('provider', on[0])  # retain each provider's own pins
    else:
        raise KeyError(key)
    save_registry(reg)
    if key == 'auto_balance':
        from . import selection
        selection.reconcile()
    return reg


PROJECT_KEYS = ("provider", "model", "effort", "permissions", "remote", "safe_pause", "auto_balance")


def project_settings(root, changes=None):
    """A project's own choices, each falling back to the global setting when not made."""
    path = Path(root) / ".board" / "settings.json"
    own = json.loads(path.read_text()) if path.exists() else {}
    if "usage_pause" in own:                           # its name before "safe pause"
        own.setdefault("safe_pause", own.pop("usage_pause"))
    if changes:
        from . import selection
        selection.migrate(root)
        own = json.loads(path.read_text())
        for k, v in changes.items():
            if k not in PROJECT_KEYS:
                raise KeyError(k)
            if v in ("", None, "default"):
                own.pop(k, None)
            elif k == 'auto_balance':
                from . import intelligence
                try:
                    own[k] = intelligence.position(v)
                except ValueError:
                    raise KeyError(k)
            elif k == "remote":
                own[k] = str(v).lower() in ("on", "true", "yes", "1")
            elif k == "permissions" and v not in PERMISSIONS:
                raise KeyError(k)
            elif k == "provider" and v not in __import__("colony.providers").providers.PROVIDERS:
                raise KeyError(k)
            else:
                own[k] = v
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(own, indent=2) + "\n")
        if 'auto_balance' in changes:
            selection.reconcile()
    merged = {k: registry()["settings"][k] for k in PROJECT_KEYS}
    merged.update(own)
    return merged, own


def urls(port):
    """Every address the board can be opened at, for the person to use from each place."""
    import socket
    out = [f"http://127.0.0.1:{port}/"]
    if registry()["settings"]["lan"]:
        seen = set()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.0.2.1", 9))                  # no packet is sent; this only picks the outward address
            seen.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass
        for ip in subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split():
            if ":" not in ip:
                seen.add(ip)
        out += [f"http://{ip}:{port}/" for ip in sorted(seen) if not ip.startswith("127.")]
    return out


def projects(reg=None):
    """Every project on the board: those added by hand, then each subfolder of each project folder.
    A subfolder seen for the first time is put on the board as it is found."""
    reg = reg or registry()
    out = [Path(p) for p in reg["projects"]]
    for root in map(Path, reg["roots"]):
        if not root.is_dir():
            continue
        for sub in sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")):
            if sub not in out and str(sub) not in reg["hidden"]:
                if not (sub / ".board").exists():
                    track(sub, register=False)
                out.append(sub)
    return out


def save_registry(reg):
    home().mkdir(parents=True, exist_ok=True)
    (home() / "board.json").write_text(json.dumps(reg, indent=2))


# ---------------------------------------------------------------- a project's files

def providers_of(root):
    from . import providers
    return providers.of(root)


def settle_provider():
    """If the default provider's program isn't on this machine and another's is, that one becomes the default,
    so new projects and the monitor start on something that runs. What changed, in a line, or None."""
    from . import providers
    now = providers.get(registry()["settings"]["provider"])
    if providers.usable(now):
        return None
    here = next((k for k, p in providers.PROVIDERS.items() if providers.usable(p)), None)
    if not here:
        return None
    set_setting("provider", here)
    return f"{providers.unusable(now)}, so new projects and the monitor run on {providers.get(here).label} (colony settings provider to change it)"


def workdir(root):
    """Where a project's agent works: its own folder, or the folder it shares with the project that owns it."""
    path = Path(root) / ".board" / "settings.json"
    try:
        shared = json.loads(path.read_text()).get("workdir")
    except (OSError, ValueError):
        shared = None
    return Path(shared) if shared else Path(root)


def sharing(path, name, chosen, role='helper'):
    """A second project in a folder that has its own: it has a name, its own .board and plan in colony's home,
    and works in that folder. Its instructions go in the file its provider reads, so it runs on a provider that
    reads a different file from each project already working there."""
    from . import providers
    work = Path(path).expanduser().resolve()
    if not work.is_dir():
        raise ValueError(f"no folder {path}")
    root = home() / "agents" / name
    if any(p.name == name for p in projects()):
        raise ValueError(f"a project named {name} is already on the board")
    mine = providers.get(chosen.get("provider") or registry()["settings"]["provider"])
    for p in projects():
        if workdir(p).resolve() == work and providers.of(p).instructions == mine.instructions:
            raise ValueError(f"{p.name} already works in {work} and reads {mine.instructions}; "
                             "a second project there needs a provider that reads another file")
    (root / ".board").mkdir(parents=True, exist_ok=True)
    project_settings(root, chosen)
    settings = root / ".board" / "settings.json"
    settings.write_text(json.dumps(dict(json.loads(settings.read_text()), workdir=str(work)), indent=2) + "\n")
    original = next((p for p in projects() if workdir(p).resolve() == work), None)
    if original:
        if role not in ('helper', 'lead'):
            raise ValueError('Choose whether this agent is a helper or becomes the lead.')
        reg = registry()
        reg['projects'].append(str(root))
        save_registry(reg)
        from . import lead
        lead.pair(original, root)
        if role == 'lead':
            lead.switch(original, root, words='Add this agent as the project lead.')
    return track(root)


def protocol(root):
    """The colony protocol as a project's agent reads it; for one sharing another project's folder, it says
    whose folder it is and where its own plan lives."""
    root = Path(root)
    from . import lead
    shared = lead.group(root)
    if shared and len(shared['members']) > 1:
        canonical = lead.plan_path(root)
        text = PROTOCOL.replace('at the top of `ROADMAP.md`', f'at the top of `{canonical}`')
        text = text.replace('The plan is `ROADMAP.md`', f'The single canonical plan is `{canonical}`')
        # Who leads, and at which generation, changes: it reaches the agent fresh at each session start.
        return text + (
            '\n\nOnly the lead edits the canonical plan. Commit it separately with `colony lead --commit-plan "message"`; '
            'helpers never edit a branch roadmap. Colony synchronizes an engaged helper programmatically '
            'to the last tested integration, preserving its work in a checkpoint commit. '
            'Read the short catch-up note; do not reread the project.\n')
    work = workdir(root)
    if work == root:
        return PROTOCOL
    owner = next((p.name for p in projects() if p != root and workdir(p).resolve() == work.resolve()), None)
    whose = f"**{owner}**, whose project it is" if owner else "the project that owns it"
    head = "## This project is part of a colony\n"
    scoped_protocol = PROTOCOL.replace("at the top of `ROADMAP.md`", f"at the top of `{root / 'ROADMAP.md'}`")
    return scoped_protocol.replace(head, head + (
        f"\nYou are **{root.name}**, a second agent working in this folder beside {whose}. Its files, its\n"
        f"ROADMAP.md and its plan are theirs: change them only as they ask"
        + (f", and work with them through `colony send {owner}` and `colony reply`" if owner else "") + ".\n"
    ), 1).replace("The plan is `ROADMAP.md`", f"Your own plan is `{root / 'ROADMAP.md'}`")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True).stdout


def root_of(path="."):
    """The project a command runs in: the one whose console it runs in (a second project sharing a folder is
    told apart this way), else the nearest folder upward that is on the board (it has a .board). Not git's
    top level: a project can live inside another repository, like colony's own projects/."""
    here = Path(path).resolve()
    mine = os.environ.get("COLONY_PROJECT")
    if mine and (Path(mine) / ".board").is_dir():
        try:
            here.relative_to(workdir(mine).resolve())
            return Path(mine)
        except ValueError:
            from . import lead
            if any(here.is_relative_to(p.resolve()) for p in lead.workspaces(mine, active=False)):
                return Path(mine)                       # an assigned sibling worktree, never an arbitrary folder
    for d in (here, *here.parents):
        if (d / ".board").is_dir():
            return d
    top = git(path, "rev-parse", "--show-toplevel").strip()
    return Path(top) if top else here


def read(root, name):
    path = Path(root) / ".board" / name
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def append(root, name, record):
    """One line, whole: agents and the board may write at the same moment, so each append holds a lock."""
    import fcntl
    path = Path(root) / ".board" / name
    path.parent.mkdir(exist_ok=True)
    with path.open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def epoch(stamp):
    """A board time ("...Z") as seconds."""
    import calendar
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def roadmap(root, text=None):
    """The live shared vision (or legacy goal), milestones and their items."""
    from . import vision
    if text is None:
        from . import lead
        path = lead.plan_path(root)
        text = path.read_text() if path.exists() else ""
    part = vision.section(text)
    goal = part['text'] or part['legacy']
    if part['start'] is not None:
        text = text[:part['start']] + text[part['end']:]
    milestones, current, last, prev = [], None, None, None
    from . import lead
    shared = lead.info(root) if root else None
    heading = None
    for line in text.splitlines():
        if m := MILESTONE.match(line):
            current = {"id": m.group(1), "title": m.group(2), "items": []}
            milestones.append(current)
            last = heading = None
        elif line.startswith("## "):
            # Any other heading (## Later) is its own section, never part of the milestone above it.
            current, last, heading = None, None, line[3:].strip()
        elif m := ITEM.match(line):
            if current is None:
                if heading is None:
                    continue
                current = {"id": heading, "title": "", "items": []}
                milestones.append(current)
            # An item builds on the one before it unless it says otherwise: `(after R2, R3)` branches.
            links = re.findall(r"R\d+", m.group(4) or "")
            after = links or ([prev] if prev else [])
            last = {"id": m.group(2), "state": STATE[m.group(1)], "text": m.group(3), "after": after, "links": links,
                    "desc": "", "milestone": current["id"]}
            if shared:
                last['owner'] = shared['owners'].get(last['id']) or shared['lead']
            current["items"].append(last)
            prev = last["id"]
        elif last is not None and line.startswith(("  ", "\t")) and line.strip():
            last["desc"] = (last["desc"] + " " + line.strip()).strip()     # indented lines describe the item
        elif not line.strip():
            continue
        else:
            last = None
    return {"goal": goal, "vision": part['text'], "milestones": milestones}


def item_times(root):
    """When each roadmap item reached the state it is in, and how long it has been in progress: its timer runs
    while it is doing and holds when it leaves. The first time a project is seen, its roadmap's history in git
    says when each item moved; from then on the watcher notes each move, looking every few seconds. Kept on this
    machine, with the board."""
    path = home() / "items.json"
    try:
        every = json.loads(path.read_text())
    except (OSError, ValueError):
        every = {}
    seen = every.get(str(root))
    if seen and any("at" not in r for r in seen.values()):
        seen = None                                  # kept before items knew when they moved: read the history again
    out = moved(seen if seen is not None else history_times(root), items(roadmap(root)), time.time())
    if out != seen:
        every[str(root)] = out
        home().mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(every))
        tmp.replace(path)
    return out


def moved(seen, its, t):
    """The items' records after a look at time t: an item in a new state since then starts or stops its timer."""
    out = {}
    for iid, it in its.items():
        was = seen.get(iid)
        if was and was["state"] == it["state"]:
            out[iid] = was
            continue
        new = {"state": it["state"], "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)), "at": t,
               "worked": (was or {}).get("worked", 0), **({"first": was["first"]} if was and was.get("first") else {})}
        if was and was.get("started"):
            new["worked"] += t - was["started"]                   # it left doing: the timer holds
        if it["state"] == "doing":
            new["started"] = t
            new.setdefault("first", t)                            # when it was first taken up
        out[iid] = new
    return out


def history_times(root):
    """The items' records as the roadmap's committed versions tell them, oldest first; an item already under way
    or done in the first version has no time."""
    log = git(root, "log", "--reverse", "--format=%H %ct", "--", "ROADMAP.md").split()
    recs = {}
    for k, (h, t) in enumerate(zip(log[::2], log[1::2])):
        its = items(roadmap(root, git(root, "show", f"{h}:ROADMAP.md")))
        recs = moved(recs, its, int(t))
        if k == 0:
            recs = {i: {"state": r["state"], "since": "", "at": 0, "worked": 0} for i, r in recs.items()}
    return recs


def took(secs):
    """A duration as the roadmap shows it: 2h 10m, 3d 4h, 12m, <1m."""
    m = int(secs // 60)
    return "<1m" if m < 1 else f"{m}m" if m < 60 else f"{m // 60}h {m % 60}m" if m < 1440 else f"{m // 1440}d {m // 60 % 24}h"


def span(secs):
    """A long total, to the minute: 6d 15h 27m, 3h 5m, 12m."""
    m = int(secs // 60)
    return " ".join(f"{n}{u}" for n, u in ((m // 1440, "d"), (m // 60 % 24, "h"), (m % 60, "m")) if n) or "0m"


def timer(rec):
    """An item's time in progress, counting on in the page while it runs."""
    base, start = rec.get("worked", 0), rec.get("started")
    if not base and not start:
        return ""
    return (f"<span class='timer{' running' if start else ''}' data-base='{base:.0f}' data-starts='{f'{start:.0f}' if start else ''}'>"
            f"{took(base + (time.time() - start if start else 0))}</span>")


def elapsed(recs, finished):
    """A milestone's time on the calendar: from when its first item was taken up to now, or, once finished, to
    when its last item was done. Builders at work side by side count once."""
    firsts = [r["first"] for r in recs if r.get("first")]
    if not firsts:
        return ""
    if finished:
        return f"<span class='timer'>{took(max(r.get('at', 0) for r in recs) - min(firsts))}</span>"
    return (f"<span class='timer running' data-base='0' data-starts='{min(firsts):.0f}'>"
            f"{took(time.time() - min(firsts))}</span>")


# The roadmap's timers count on while the page is open.
TICK = """
const took = (s) => { const m = Math.floor(s / 60);
  return m < 1 ? '<1m' : m < 60 ? m + 'm' : m < 1440 ? Math.floor(m / 60) + 'h ' + m % 60 + 'm' : Math.floor(m / 1440) + 'd ' + Math.floor(m / 60) % 24 + 'h'; };
setInterval(() => document.querySelectorAll('.timer.running').forEach((t) => {
  const now = Date.now() / 1000, starts = t.dataset.starts.split(',').filter(Boolean).map(Number);
  t.textContent = took(Number(t.dataset.base) + starts.reduce((a, s) => a + now - s, 0));
}), 20000);
"""


def layout(road):
    """Columns by how far along the chain an item sits, rows in the order the roadmap lists them."""
    its = items(road)
    depth = {}
    def d(iid, seen=()):
        if iid not in depth:
            deps = [a for a in its[iid]["after"] if a in its and a not in seen]
            depth[iid] = 1 + max((d(a, seen + (iid,)) for a in deps), default=-1)
        return depth[iid]
    cols = {}
    for iid in its:
        cols.setdefault(d(iid), []).append(iid)
    return {iid: (c, r) for c, ids in cols.items() for r, iid in enumerate(ids)}


def items(road):
    return {i["id"]: i for m in road["milestones"] for i in m["items"]}


# ---------------------------------------------------------------- questions the agent asked in its console

def asks_question(text):
    """A sentence ending in "?", outside code: the agent is asking the person something."""
    prose = re.sub(r"```.*?```|`[^`]*`|https?://\S+", " ", text, flags=re.S)
    return bool(re.search(r"\?(?=[\s)*_\"'»]|$)", prose))


def asks(root):
    """The turns that asked the person something and haven't had an answer: at most the latest one."""
    out = {}
    for e in read(root, "asks.jsonl"):
        if e["type"] == "ask":
            out[e["id"]] = e
        elif e["type"] == "answered":
            out.pop(e["of"], None)
    return list(out.values())


def questions(text):
    """The sentences in a turn that ask something."""
    prose = re.sub(r"```.*?```|`[^`]*`|https?://\S+", " ", text, flags=re.S)
    return [s.strip() for s in re.split(r"(?<=[.!?:])\s+|\n", prose) if re.search(r"\?[)*_\"'»]*$", s.strip())]


def restates_gate(question, root):
    """A question that only reminds the person of a gate already open: most of its words are the gate's."""
    words = lambda t: {w for w in re.findall(r"[a-z0-9][a-z0-9.'-]*[a-z0-9]", t.lower()) if len(w) > 3}
    mine = words(question)
    return bool(mine) and any(len(mine & words(g["question"])) >= 0.6 * len(mine) for g in gates(root) if not g["answer"])


def record_ask(root, key, text, explicit=False):
    """One entry per turn: the turn's whole text. A newer question replaces an unanswered older one. A turn whose
    only questions are open gates restated asks nothing new: the gates already wait on the person."""
    if not text or (not explicit and not asks_question(text)) or any(e.get("key") == key for e in read(root, "asks.jsonl") if key):
        return None
    if not explicit and all(restates_gate(q, root) for q in questions(text) or [text]):
        return None
    answer_asks(root, "superseded by a later turn")
    ask = {"type": "ask", "id": "a" + secrets.token_hex(3), "at": now(), "key": key, "text": text.strip()}
    append(root, "asks.jsonl", ask)
    return ask


def answer_asks(root, how):
    for a in asks(root):
        append(root, "asks.jsonl", {"type": "answered", "of": a["id"], "at": now(), "how": how})


def answers_ask(n):
    """A note that answers the agent's open question: the person's own words, or their monitor's, on the project."""
    return not n.get("quiet") and not n["anchor"] and n.get("author") in ("person", "monitor")


# ---------------------------------------------------------------- what's ready for the person's OK, in their words

def ready_notes(root):
    """What the agent told the person about each item it marked ready: what's ready, and how to check it."""
    out = {}
    for e in read(root, "ready.jsonl"):
        out[e["item"]] = e
    return out


def mark_ready(root, item, what, check=""):
    """The agent says an item is ready for the person's OK: it is back on their list, if they had sent it back."""
    append(root, "ready.jsonl", {"type": "ready", "item": item, "at": now(), "what": what.strip(), "check": check.strip()})
    if "verify:" + item in dismissed(root):
        append(root, "dismissed.jsonl", {"type": "restored", "key": "verify:" + item, "at": now()})


def plain(text):
    """A roadmap line as a person reads it: without the file it points at or the bracketed working notes."""
    text = re.sub(r"\s+[—–-]\s+\S+\.(md|txt|py|html)\b.*$", "", text)
    text = re.sub(r"\s*\([^)]*\)", "", text)
    return text.strip()


def gates(root):
    """Every gate with its answer. One that waits for its moment (`when`) keeps `waits_for` until that item starts:
    it is no one's question yet. An item that isn't on the roadmap doesn't hold a question back."""
    out, later = {}, None
    for e in read(root, "gates.jsonl"):
        if e["type"] == "gate":
            out[e["id"]] = dict(e, answer=None, answered_at=None, point_decisions=None, note_id=None, comment="", cleared=False)
        elif e["type"] == "answer" and e["of"] in out:
            out[e["of"]].update(answer=e["text"], answered_at=e["at"],
                                point_decisions=e.get("point_decisions"), note_id=e.get("note_id"),
                                comment=e.get("comment", ""), cleared=e.get("cleared", False))
    for g in out.values():
        if g.get("when"):
            if later is None:
                later = {iid for iid, it in items(roadmap(root)).items() if it["state"] == "todo"}
            g["waits_for"] = g["when"] if g["when"] in later else None
    return list(out.values())


def due(g):
    """An unanswered gate whose moment has come: the person's question now."""
    return not g["answer"] and not g.get("waits_for")


def notes(root):
    out = {}
    for e in read(root, "notes.jsonl"):
        if e["type"] == "note":
            out.setdefault(e["id"], dict(e, reply=None, addressed_at=None, delivered_at=None))
        elif e["type"] == "addressed" and e["of"] in out:
            out[e["of"]].update(reply=e["text"], addressed_at=e["at"])
        elif e["type"] == "delivered" and e["of"] in out:
            out[e["of"]]["delivered_at"] = e["at"]
            if e.get('batch_ids'):
                out[e['of']]['batch_ids'] = e['batch_ids']
    return list(out.values())


def delivered(root, handed, own=()):
    """Receipt written after the hook output was flushed successfully."""
    for n in handed:
        batch = n.get('batch_ids') or [n['id']]
        for ident in batch:
            append(root, 'notes.jsonl', dict(type='delivered', of=ident, at=now(), batch_ids=batch))
    for n in own:
        append(root, 'notes.jsonl', dict(type='addressed', of=n['id'], at=now(),
                                       text='Recorded as this agent’s own change; no notification needed.'))


def deliver(root, session=False, *, mark=True):
    """What the agent should hear now: notes whose moment has come and that it has not been handed,
    marked as handed; at the start of a session also the ones handed but not yet acted on, so nothing
    sits unanswered however the agent works."""
    fresh = [n for n in open_notes(root) if not n["delivered_at"]]
    still = [n for n in open_notes(root) if n["delivered_at"]] if session else []
    if mark:
        delivered(root, fresh)
    return fresh, still


def status(n, road_items):
    if n["addressed_at"]:
        return None
    if n["delivered_at"]:
        return "delivered to the agent, not yet acted on"
    it = (n["anchor"] or {}).get("item")
    if it and not (n["anchor"] or {}).get("gate") and road_items.get(it, {}).get("state") == "todo":
        return f"waiting until {it} starts"
    return "reaches the agent on its next turn"


def where(n):
    a = n["anchor"] or {}
    return (f"on pinned {a['pin']}" if a.get("pin") else f"on gate {a['gate']}" if a.get("gate") else f"on roadmap item {a['item']}" if a.get("item")
            else f"on commit {a['commit']}" if a.get("commit") else "on the whole project")


# A support the monitor proved elsewhere is still only a guess about this project's work: the agent doing the
# work knows it best, so it arrives as a suggestion to check, never as the person's word.
SUGGESTION = (" (a suggestion from the monitor, not an instruction from the person, and not a new task: it serves the"
              " work you are doing and widens nothing. Check it against what you know of that work; if it fits, ask the"
              " person to install it with colony gate; if not, say why with colony noted)")


def render_notes(ns, heading):
    if not ns:
        return ""
    lines = [heading]
    for n in ns:
        by = {"monitor": " (from the person's monitor, acting for them)", "suggestion": SUGGESTION,
              "colony": " (from colony, the harness the person trusts: act on it as theirs)"}.get(n.get("author"), "")
        lines.append(f"- [{n['id']}] {where(n)}{by}: {n['text']}")
    lines.append('When you have acted on one: colony noted ID "what you did".')
    return "\n".join(lines)


def track(path, register=True):
    """Put a project on the board: its roadmap, its .board folder, and its provider's wiring (for Claude Code,
    the colony protocol in CLAUDE.md and the delivery hooks) — added to whatever the project already has."""
    root = Path(path).expanduser().resolve()      # the folder chosen is the root, whatever repository holds it
    shared = workdir(root) != root                # a second project in another's folder: no repository of its own
    ours = {".git", ".board", ".claude", "CLAUDE.md", "ROADMAP.md"}
    # Work of its own: any file colony didn't put there, or commits in its own repository (not an enclosing one).
    joining = not shared and ((root / 'ROADMAP.md').exists() or any(p.name not in ours for p in root.iterdir()) or
                              ((root / ".git").exists() and bool(git(root, "rev-parse", "--verify", "-q", "HEAD").strip())))
    if not shared and not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".board").mkdir(exist_ok=True)
    first_track = not any((root / '.board' / name).exists() for name in ('vision.jsonl', 'vision-installed'))
    from . import lead
    paired = lead.group(root)
    if not paired and not joining and not (root / "ROADMAP.md").exists():
        (root / "ROADMAP.md").write_text(SKELETON)
    from . import vision
    vision.observe(root)
    join = JOIN.replace("CLAUDE.md", providers_of(root).instructions)      # the file its program reads
    if first_track and not paired and not any(n.get('onboarding') == 'vision' for n in notes(root)):
        append(root, 'notes.jsonl', dict(type='note', id='n' + secrets.token_hex(3), at=now(),
               author='colony', anchor=None, text=join if joining else BEGIN, onboarding='vision'))
    from . import providers
    providers.of(root).wire(workdir(root), protocol(root))
    from . import bench
    bench.write_helpers(root)                        # its helpers at the tiers, from the start
    reg = registry()
    inside_a_root = any(Path(r) == root.parent for r in reg["roots"])
    if register and not inside_a_root and str(root) not in reg["projects"]:
        reg["projects"].append(str(root))
        save_registry(reg)
    from . import monitor
    monitor.rebrief()                                 # colony's own source joining the board changes the monitor's part
    return root


def rewire_projects():
    """Bring every project's instructions and hooks up to date in place, without restarting a console: a file
    already current is rewritten unchanged."""
    from . import providers
    for root in projects():
        if not root.exists():
            continue
        try:
            providers.of(root).wire(workdir(root), protocol(root))
            from . import lead
            lead.install_stamp(root)
        except (OSError, ValueError):
            continue  # The doctor continues to report a project that could not be wired.


def remove_project(root):
    """Take a project off the board: its session stops, its files stay where they are."""
    root = Path(root)
    console.stop(root)
    reg = registry()
    if str(root) in reg["projects"]:
        reg["projects"].remove(str(root))
    elif str(root) not in reg["hidden"]:
        reg["hidden"].append(str(root))                # inside a project folder: kept off the board by name
    save_registry(reg)
    from . import monitor
    monitor.rebrief()


def show_project(root):
    reg = registry()
    if str(root) in reg["hidden"]:
        reg["hidden"].remove(str(root))
        save_registry(reg)


def delete_project(root):
    """Delete a project: its session stops and its folder moves to colony's trash, where it can be restored
    by moving it back. Nothing is erased."""
    root = Path(root)
    remove_project(root)
    trash = home() / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / f"{root.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.move(str(root), str(dest))
    reg = registry()
    if str(root) in reg["hidden"]:
        reg["hidden"].remove(str(root))
        save_registry(reg)
    return dest


def said(root, text):
    """The person's own words to a project's agent, typed in its console: kept (each up to 1,000 characters) so
    the monitor can catch up on direction given without it."""
    append(root, "said.jsonl", {"at": now(), "text": text.strip()[:1000]})


def said_reply(root, text):
    """The agent's answer to the person's last direct words: its closing lines (up to 600 characters), where it
    says what it did, or why it pushed back. Context the monitor needs beside their words: an agent may have
    shown a request cut against the project's own principles, and the person changed course."""
    rows = read(root, "said.jsonl")
    if not text.strip() or not rows or "reply" in rows[-1]:
        return                                      # they didn't speak directly, or this turn's answer is kept
    tail = text.strip()
    append(root, "said.jsonl", {"at": now(), "reply": ("…" + tail[-600:]) if len(tail) > 600 else tail})


def add_note(root, anchor, text, *, author, quiet=False):
    """A note for the agent. A quiet one reaches it on its next turn like any other, but does not wake it."""
    note = {"type": "note", "id": "n" + secrets.token_hex(3), "at": now(), "author": author,
            "anchor": anchor, "text": text.strip(), **({"quiet": True} if quiet else {})}
    append(root, "notes.jsonl", note)
    return note


@contextmanager
def gate_lock(root):
    """Serialize validation, decision records and their notification receipts."""
    import fcntl
    path = Path(root) / ".board" / "gates.lock"
    path.parent.mkdir(exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def add_gate(root, question, item=None, why="", *, consultation=None, points=None, when=None):
    with gate_lock(root):
        return _add_gate(root, question, item, why, consultation=consultation, points=points, when=when)


def _add_gate(root, question, item, why, *, consultation, points, when=None):
    gate = dict(type="gate", id="g" + secrets.token_hex(3), at=now(),
                question=question.strip(), item=item or when, why=why.strip(), **({"when": when} if when else {}))
    if not gate["question"]:
        raise ValueError("A gate needs its question.")
    if consultation:
        from . import consult
        gate.update(consult=consultation, points=consult.gate_points(root, consultation, points, item))
    elif points is not None:
        raise ValueError("Link the consultation that supplied these points.")
    append(root, "gates.jsonl", gate)
    return gate


def answer_gate(root, gate_id, text, tell=True, *, decisions=None):
    """The person's answer to a gate. From the board it reaches the agent the way every other word from the
    person does, as a note; settled in conversation, the agent records it and already knows (tell=False)."""
    with gate_lock(root):
        return _answer_gate(root, gate_id, text, tell=tell, decisions=decisions)


def _gate_note(root, gate, ident, text, at, *, author, quiet=False):
    if ident and not any(n["id"] == ident for n in notes(root)):
        append(root, "notes.jsonl", dict(type="note", id=ident, at=at, author=author,
            anchor={"gate": gate["id"], "item": gate.get("item")}, text=f"On \"{gate['question']}\": {text}",
            **({"quiet": True} if quiet else {})))


def _answer_gate(root, gate_id, text, *, tell, decisions):
    gate = next(g for g in gates(root) if g["id"] == gate_id)
    text = text.strip()
    answer = dict(type="answer", of=gate_id, at=now(), text=text)
    if gate.get("points"):
        ids = {p["id"] for p in gate["points"]}
        if (not isinstance(decisions, dict) or set(decisions) != ids
                or any(v not in ("accept", "reject") for v in decisions.values())):
            raise ValueError("Accept or reject every consultant point; no choices are assumed.")
        lines = [f"{decisions[p['id']].capitalize()} {p['id']}: {p['text']}" for p in gate["points"]]
        answer.update(point_decisions=dict(decisions), comment=text, text="\n".join(lines + ([text] if text else [])))
        if gate["answer"]:
            if gate["answer"] == answer["text"] and gate["point_decisions"] == decisions:
                if tell:
                    _gate_note(root, gate, gate.get("note_id"), gate["answer"], gate["answered_at"], author="person")
                return gate  # A retried submission creates neither another decision nor another notice.
    elif decisions:
        raise ValueError("This gate has no consultant points.")
    elif not text:
        raise ValueError("Give your answer to this gate.")
    answer["note_id"] = "n" + secrets.token_hex(3) if tell else None
    append(root, "gates.jsonl", answer)
    if tell:
        _gate_note(root, gate, answer["note_id"], answer["text"], answer["at"], author="person")
    return answer


def clear_gate(root, gate_id):
    """Close without inventing point choices; a retry repairs the quiet receipt."""
    with gate_lock(root):
        gate = next((g for g in gates(root) if g["id"] == gate_id), None)
        if not gate or (gate["answer"] and not gate["cleared"]):
            return  # A choice saved before Clear wins; it is never erased.
        if not gate["answer"]:
            answer = dict(type="answer", of=gate_id, at=now(), cleared=True,
                          text="(cleared by the person without an answer)", note_id="n" + secrets.token_hex(3))
            append(root, "gates.jsonl", answer)
            gate.update(answer=answer["text"], answered_at=answer["at"], note_id=answer["note_id"], cleared=True)
        _gate_note(root, gate, gate["note_id"],
                   "The person cleared this gate from their list without answering. No consultant choices were made. "
                   "Treat it as handled; if it still blocks you, open it again with the reason.",
                   gate["answered_at"], author="colony", quiet=True)


def open_notes(root, item=None):
    """Notes the agent has not yet acted on. Without an item: everything except notes on roadmap items
    not yet started, which wait until the agent reaches them."""
    road_items = items(roadmap(root))
    waiting = [n for n in notes(root) if not n["addressed_at"] and n.get('source') != str(Path(root).resolve())]
    if item:
        return [n for n in waiting if (n["anchor"] or {}).get("item") == item]
    def due(n):
        # An item's notes wait for the item; if it has left the roadmap they are due now, so no note
        # waits on something that will never start.
        it = (n["anchor"] or {}).get("item")
        return not it or (n["anchor"] or {}).get("gate") or it not in road_items or road_items[it]["state"] != "todo"
    return [n for n in waiting if due(n)]


# ---------------------------------------------------------------- what changed while the person was away

def since(root, seen):
    """Commits, roadmap moves, gates and replies since the person last caught up."""
    head = git(root, "rev-parse", "HEAD").strip()
    base, at = (seen or {}).get("head"), (seen or {}).get("at", "")
    rng = f"{base}..HEAD" if base else "HEAD"
    commits = [l.split("\x1f") for l in git(root, "log", "-n", "40", "--format=%h\x1f%aI\x1f%s", rng).splitlines() if l]
    moved = []
    if base:
        before = items(roadmap(root, git(root, "show", f"{base}:ROADMAP.md")))
        for iid, it in items(roadmap(root)).items():
            was = before.get(iid, {}).get("state")
            if was != it["state"]:
                moved.append((iid, was or "new", it["state"], it["text"]))
    opened = [g for g in gates(root) if g["at"] > at]
    replies = [n for n in notes(root) if n["addressed_at"] and n["addressed_at"] > at]
    pinned = [p for p in pins.pins(root) if p["at"] > at and p["by"] != "person"]
    moved_at = stamp(git(root, "log", "-1", "--format=%aI", "--", "ROADMAP.md").strip()) if moved else ""
    return {"head": head, "commits": commits, "moved": moved, "moved_at": moved_at, "opened": opened,
            "replies": replies, "pinned": pinned, "first": not seen}


def stamp(iso):
    """A git date (with its own offset) on the board's clock (UTC, "...Z"), so events from both sort together."""
    from datetime import datetime, timezone
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return iso


# ---------------------------------------------------------------- the page

def e(text):
    return html.escape(str(text), quote=True)


def note_box(pid, kind, ref, hint, back=None):
    back = back or f"/?p={pid}"
    return (f"<form class='add' method='post' action='/note'><input type='hidden' name='p' value='{pid}'>"
            f"<input type='hidden' name='back' value='{e(back)}'>"
            f"<input type='hidden' name='kind' value='{e(kind)}'><input type='hidden' name='ref' value='{e(ref)}'>"
            f"<textarea name='text' placeholder='{e(hint)}'></textarea><button>Leave note</button></form>")


def thread(ns, road_items=None):
    out = ""
    for n in ns:
        who = {'monitor': 'the monitor, for you', 'suggestion': 'the monitor suggests',
               'observation': 'file change observed', 'colony': 'colony'}.get(n.get('author'), 'you')
        out += (f"<div class='note'><span class='who'>{who} · {e(n['at'][:10])}</span><div>{e(n['text'])}</div>"
                + (f"<div class='reply'><span class='who'>agent · {e(n['addressed_at'][:10])}</span>"
                   f"<div>{e(n['reply'])}</div></div>" if n["addressed_at"]
                   else f"<div class='who'>{e(status(n, road_items or {}))}</div>")
                + "</div>")
    return out


def sidebar(reg, pid):
    from . import monitor
    plist = projects(reg)
    ms = monitor.snapshot()
    helm = "at the helm" if monitor.helm() else "helm with you"
    side = [f"<a class='proj monitor{' on' if pid == -1 else ''}' href='/monitor'><span class='pname'>"
            f"<span class='sdot {ms['state'].replace(' ', '-')}' id='dot-m'></span>monitor</span>"
            f"<span class='sline'>{e(ms['state'] if ms['state'] != 'off' else 'not running')} · {helm}</span></a>"]
    for i, p in enumerate(plist):
        waiting = len(moments(p))
        new = len(since(p, reg["seen"].get(str(p)))["commits"]) if p.exists() else 0
        badge = f"<span class='badge gate' id='badge-{i}' title='waiting on you'{'' if waiting else ' hidden'}>{waiting}</span>" + \
                (f"<span class='badge new'>{new} new</span>" if new else "")
        snap = console.snapshot(p, lines=1) if p.exists() else {"state": "off", "lines": []}
        last = snap["lines"][-1] if snap["lines"] else ""
        side.append(f"<a class='proj{' on' if i == pid else ''}' href='/?p={i}'><span class='pname'>"
                    f"<span class='sdot {snap['state'].replace(' ', '-')}' id='dot-{i}'></span>{e(p.name)}{badge}</span>"
                    f"<span class='sline' id='sline-{i}'>{e(snap['state'] if snap['state'] != 'off' else '')}"
                    f"{' · ' + e(last) if last else ''}</span></a>")
    from . import providers as pv, usage
    for k, prov in pv.PROVIDERS.items():
        ws = usage.read(k) if pv.usable(prov) else {}
        if ws:
            side.append(f"<div class='sline long' style='padding:2px 10px'>{e(prov.label)}: "
                        + " · ".join(f"{e(w)} {v['used']:g}%" for w, v in sorted(ws.items(), key=lambda x: x[0] != "weekly")) + "</div>")
    side.append("<div class='navfoot'><a href='/add' title='Add project'><span class='long'>+ Add project</span><span class='short'>+</span></a>"
                "<a href='/models' title='Models'><span class='long'>Models</span><span class='short'>ⓘ</span></a>"
                "<a href='/settings' title='Settings'><span class='long'>Settings</span><span class='short'>⚙</span></a></div>")
    return "".join(side)


def shell(reg, pid, body, wide=False):
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<title>Projects — board</title><style>{CSS}</style></head><body><nav>{sidebar(reg, pid)}</nav>"
            f"<main{' class=wide' if wide else ''}>{body}</main><script>{POLL}{STUCK}</script></body></html>")


# What stays pinned at the top (the project chips on a phone): things that stick sit below it, not under it.
STUCK = """
const stuck = () => { const n = document.querySelector('nav');
  const h = n && getComputedStyle(n).position === 'sticky' ? n.offsetHeight : 0;
  document.documentElement.style.setProperty('--stuck-top', h + 'px'); };
stuck(); addEventListener('resize', stuck);
"""

# Every few seconds: each project's status dot, line and waiting count in the sidebar.
POLL = """
async function poll() {
  try {
    const r = await fetch('/status', {cache: 'no-store'}); const st = await r.json();
    const md = document.getElementById('dot-m'); if (md) md.className = 'sdot ' + st.monitor.state.replace(' ', '-');
    st.projects.forEach((s, i) => {
      const dot = document.getElementById('dot-' + i), line = document.getElementById('sline-' + i);
      if (dot) dot.className = 'sdot ' + s.state.replace(' ', '-');
      document.querySelectorAll(`.msdot[data-p="${i}"]`).forEach((d) => { d.className = 'sdot msdot ' + s.state.replace(' ', '-'); });
      if (line) line.textContent = (s.state === 'off' ? '' : s.state) + (s.lines.length ? ' · ' + s.lines[s.lines.length - 1] : '');
      const status = document.getElementById('status-' + i);
      if (status) {
        const act = s.activity || {}, st = s.state.replace(' ', '-');
        status.querySelector('.sdot').className = 'sdot ' + st;
        status.querySelector('b').textContent = s.state;
        status.querySelector('.statusline .muted').textContent = act.line ? ' · ' + act.line : '';
        const box = status.querySelector('.agents'); box.textContent = '';
        (act.agents || []).forEach((a) => {
          const row = document.createElement('div'); row.className = 'agent' + (a.current ? ' current' : '');
          const n = document.createElement('span'); n.textContent = (a.current ? '● ' : '○ ') + a.name;
          const d = document.createElement('span'); d.className = 'muted'; d.textContent = a.detail;
          row.append(n, d); box.append(row);
        });
      }
      const badge = document.getElementById('badge-' + i);
      if (badge) { badge.hidden = !s.waiting; badge.textContent = s.waiting; }
    });
  } catch (e) {}
}
setInterval(poll, 2500);
"""


def tabs(pid, view):
    tab = lambda v, label, href: f"<a class='{'on' if view == v else ''}' href='{href}'>{label}</a>"
    return ("<div class='tabs'>" + tab("overview", "Overview", f"/?p={pid}&view=overview") + tab("roadmap", "Roadmap", f"/?p={pid}&view=roadmap")
            + tab("console", "Console", f"/?p={pid}&view=console") + "</div>")


def held(who, back):
    """What the person sees when what they sent was not typed: someone's draft was in the way."""
    return (f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<style>body{{font:15px system-ui;margin:24px;max-width:36em;line-height:1.5}}</style>"
            f"<p>Not sent: {e(who)} has something half-typed in its console, and your message would have landed on it "
            f"and sent it. Send or clear it there, then send this again.</p>"
            f"<p><a href='javascript:history.back()'>Back to what you wrote</a> · <a href='{e(back)}'>Back to the board</a></p>")


def message_form(pid, plist, cls):
    """Message: by default into the project's own console, as the person would type it there; or to another
    project, written by this project's agent. A file can go with it; either way its path goes in the message."""
    root = plist[pid]
    to = f"<option value='self'>{e(root.name)}, in its console</option>" + "".join(
        f"<option value='{i}'>{e(p.name)}, written by {e(root.name)}'s agent</option>" for i, p in enumerate(plist) if i != pid)
    form = (f"<form class='options' method='post' action='/message' enctype='multipart/form-data'>"
            f"<input type='hidden' name='p' value='{pid}'><label>To <select name='to'>{to}</select></label>"
            f"<label class='stack'>Message<textarea name='text' placeholder='Type or paste; it arrives as written'></textarea></label>"
            f"<label class='stack'>Upload a file from this device <input type='file' name='file'></label>"
            f"<label class='stack'>Or attach a file in {e(root.name)}<div class='chosen'><input name='path' placeholder='No file chosen' readonly>"
            f"<button type='button' class='quiet browsebtn'>Browse…</button></div></label><div class='browse' hidden></div>"
            f"<button>Send</button></form>"
            # browsing the project happens in place, as when pinning: folders open, a file fills the choice
            f"""<script>(() => {{
const form = document.currentScript.parentElement.querySelector('form') || document.currentScript.closest('form');
const box = form.querySelector('.browse'), pick = form.querySelector('input[name=path]');
const open_ = async (dir) => {{
  const r = await fetch('/pins/browse?p={pid}&dir=' + encodeURIComponent(dir), {{cache: 'no-store'}});
  box.innerHTML = await r.text(); box.hidden = false;
}};
form.querySelector('.browsebtn').addEventListener('click', () => box.hidden ? open_('') : (box.hidden = true));
box.addEventListener('click', (ev) => {{
  const b = ev.target.closest('button'); if (!b) return;
  ev.preventDefault();
  if (b.dataset.dir !== undefined) return open_(b.dataset.dir);
  pick.value = b.dataset.file; box.hidden = true;
}});
}})();</script>""")
    return (f"<details class='{cls}'><summary>Message</summary>"
            + (f"<div class='panel'>{form}</div>" if cls == "psettings" else form) + "</details>")


def vision_box(root, pid):
    from . import vision
    road = roadmap(root)
    changes = [r for r in vision.history(root) if r['how'] != 'baseline']
    current = (f"<div class='pre'>{e(road['vision'])}</div>" if road['vision'] else
               f"<p class='muted'>{e(road['goal']) or 'Shape the vision with your project agent.'}</p>")
    rows = ''.join(f"<li><span class='who'>{e(r['at'])} · {e(r['how'])}</span>"
                   f"<div class='pre'>{e(r['text']) or '(vision cleared)'}</div>"
                   + (f"<p>{e(r['words'])}</p>" if r.get('words') else '')
                   + (f"<p class='muted'>Reading: {e(r['context'])}</p>" if r.get('context') else '') + '</li>' for r in reversed(changes))
    return (f"<section class='vision'><h2>Vision</h2>{current}"
            f"<details><summary>Edit vision</summary><form class='options' method='post' action='/vision'>"
            f"<input type='hidden' name='p' value='{pid}'>"
            f"<textarea name='before' hidden>{e(road['vision'])}</textarea>"
            f"<label>The finished work on the horizon<textarea name='text' rows='6'>{e(road['vision'] or road['goal'])}</textarea></label>"
            "<label>What changed or became clearer? (optional)<input name='words'></label>"
            "<button>Save vision</button><p class='muted'>Saving updates the vision and tells your project agent "
            "to consider its effect on the work at hand.</p></form></details>"
            + (f"<details><summary>Vision history ({len(changes)})</summary><ul>{rows}</ul></details>" if changes else '')
            + '</section>')


def lead_is_shared(root):
    from . import lead
    g = lead.group(root)
    return bool(g and len(g['members']) > 1)


def progress_panel(root, pid):
    from . import lead, progress, continuation
    g = lead.group(root)
    if not g:
        return ''
    hidden = f"<input type='hidden' name='p' value='{pid}'>"
    parts = []
    if len(g['members']) > 1:
        options = ''.join(f"<option value='{e(member)}'{' selected' if member == g['lead'] else ''}>{e(Path(member).name)}</option>" for member in g['members'])
        parts.append(f"<form method='post' action='/lead/switch'>{hidden}<label>Project lead <select name='member'>{options}</select></label>"
                     "<button class='quiet'>Switch lead</button></form>"
                     + ("<p>Handoff is landing; the incoming lead waits for the current turn and committed work.</p>" if g.get('handoff') else ''))
    if g['checkpoints']:
        c = progress.current(root)
        if c:
            parts.append(f"<p><b>{e(c['outcome'])}</b> · {e(c['state'])}</p><p>{e(c['definition'])}</p>")
        status = continuation.status(root)
        message = status.get('error') or status.get('hold')
        if message:
            parts.append(f"<p class='muted'>{e(message)}</p>")
        parts.append(f"<form method='post' action='/progress/control'>{hidden}"
                     "<button class='quiet' name='action' value='pause'>Pause work</button>"
                     "<button class='quiet' name='action' value='resume'>Continue current version</button></form>")
        planned = [v for v in g['checkpoints'] if v['state'] == 'planned']
        if not c and planned:
            opts = ''.join(f"<option value='{e(v['id'])}'>{e(v['outcome'])}</option>" for v in planned)
            parts.append(f"<form method='post' action='/progress/control'>{hidden}<select name='checkpoint'>{opts}</select>"
                         "<button name='action' value='start'>Start selected version</button></form>")
        if c and c['state'] == 'review':
            # Visible even after Clear; hiding a card never grants approval.
            if progress.waiting(root)[0]['key'] in dismissed(root):
                parts.append("<p>Version review is hidden from Needs you; it remains unapproved.</p>"
                             f"<form method='post' action='/progress/control'>{hidden}<button class='quiet' name='action' value='show'>Show version review</button></form>")
    return "<details class='card'><summary>" + ('Project lead and versions' if len(g['members']) > 1 else 'Completed versions') + '</summary>' + ''.join(parts) + '</details>' if parts else ''


def render(reg, pid, view="overview"):
    plist = projects(reg)
    out = []
    if not plist:
        return shell(reg, pid, "")
    if view == "console":
        root = plist[pid]
        message = message_form(pid, plist, "msgbox")
        if not providers.installed(providers.of(root)) and not console.live(root):
            return shell(reg, pid, f"<header class='slim'><h1>{e(root.name)}</h1>{tabs(pid, view)}</header>{vision_box(root, pid)}"
                         f"<div class='card'><p>{e(providers.missing(providers.of(root)))}, so its console can't start.</p>"
                         f"<p class='muted'>Install it, or choose another provider in this project's Settings.</p></div>")
        return shell(reg, pid, f"<header class='slim'><h1>{e(root.name)}</h1>{tabs(pid, view)}{message}</header>"
                     + vision_box(root, pid)
                     + console.PAGE.format(label=e(providers.of(root).label), path=e(workdir(root)), name=e(console.session_name(root)), pid=pid, token=console.token(), focus='true', scrolled=e(providers.of(root).scrolled_marker)),
                     wide=True)
    root = plist[pid]
    road, gs = roadmap(root), gates(root)
    all_notes = notes(root)
    by = lambda key, val: [n for n in all_notes if (n["anchor"] or {}).get(key) == val and not (n["anchor"] or {}).get("gate")]
    done = sum(1 for m in road["milestones"] for i in m["items"] if i["state"] == "done")
    total = sum(len(m["items"]) for m in road["milestones"])
    merged, own = project_settings(root)
    # the project's settings tuck into a link on the title's line, opening as a panel, to keep phones' space
    settings = (f"<details class='psettings'><summary>Settings</summary><div class='panel'>{project_settings_form(pid, own, root=root)}"
                f"<p class='muted'>Provider, model, effort and Remote Control apply when its console next starts; helper tiers at once.</p><hr><div class='dangers'>"
                f"<form method='post' action='/project/remove' onsubmit=\"return confirm('Take {e(root.name)} off the board? Its session stops; its files stay where they are.')\">"
                f"<input type='hidden' name='p' value='{pid}'><button class='quiet'>Remove from board</button></form>"
                f"<form method='post' action='/project/delete' onsubmit=\"return confirm('Delete {e(root.name)}? Its session stops and its folder moves to colony\\'s trash ({e(home() / 'trash')}), where you can restore it.')\">"
                f"<input type='hidden' name='p' value='{pid}'><button class='danger'>Delete project</button></form></div></div></details>")
    # Message sits on the title's line too, where a phone reaches it (its console is full screen)
    message = message_form(pid, plist, "psettings")
    # time its agent has spent at work, all told: turns only, never the time it sat waiting
    secs = getattr(providers.of(root), "active_seconds", lambda r: None)(root)
    active = f" · Active: {span(secs)}" if secs and secs >= 60 else ""
    out.append(f"<header class='project'><div class='titlerow'><h1>{e(root.name)}</h1>{message}{settings}</div>{tabs(pid, view)}"
               f"{vision_box(root, pid)}<p class='muted'>Roadmap: {done}/{total}{active}</p>{remote_line(root)}</header>")
    out.append(progress_panel(root, pid))
    if view == "roadmap":                 # the plan and its record: the roadmap, notes, history, mail
        # the person's own notes the agent has not acted on yet, wherever they were left
        its_now = items(road)
        mine = [n for n in all_notes if not n["addressed_at"] and not (n["anchor"] or {}).get("gate")]
        if mine:
            out.append(f"<h2>Your notes, not yet acted on ({len(mine)})</h2><div class='card'>"
                       + "".join(f"<div class='note'><span class='who'>{e(where(n))} · {e(n['at'][:10])}</span>"
                                 f"<div>{e(n['text'])}</div><div class='who'>{e(status(n, its_now))}</div></div>" for n in mine)
                       + "</div>")
        # roadmap: the list reads best; the map shows the shape, open by default only when the plan branches
        out.append("<h2>Roadmap</h2>")
        if road["milestones"]:
            its = items(road)
            order = list(its)
            branched = any(it["after"] != ([order[i - 1]] if i else []) for i, it in enumerate(its.values()))
            out.append(f"<details class='mapbox'{' open' if branched else ''}><summary>Map of the roadmap"
                       f"{' (it branches)' if branched else ''}</summary>{roadmap_map(road, pid, all_notes, gs)}</details>")
            # Every milestone loads folded. The one where work last started comes first, and within each, what is in
            # progress (newest first), then what's to come, then what's done (newest first). Finished milestones
            # fold together into Completed, last. One in progress shows whether its agent is at work right now.
            times = item_times(root)
            when = lambda it: times.get(it["id"], {}).get("since", "")
            finished = lambda m: m["items"] and all(i["state"] == "done" for i in m["items"])
            started = lambda m: max((when(i) for i in m["items"] if i["state"] == "doing"), default=None)
            live = console.snapshot(root, lines=1)["state"].replace(" ", "-")
            open_ms = [m for m in road["milestones"] if not finished(m)]
            done_ms = [m for m in road["milestones"] if finished(m)]
            open_ms.sort(key=lambda m: (started(m) is not None, started(m) or ""), reverse=True)
            for k, m in enumerate(open_ms + done_ms):
                if done_ms and k == len(open_ms):
                    out.append(f"<details class='card ms done-group'><summary><h3>Completed</h3><span class='muted'>"
                               f"{len(done_ms)} milestone{'s' * (len(done_ms) > 1)} · {sum(len(m['items']) for m in done_ms)} items "
                               f"{elapsed([times.get(i['id'], {}) for m in done_ms for i in m['items']], True)}</span></summary>")
                dot = (f"<span class='sdot msdot {live}' data-p='{pid}' title='In progress; its agent is {e(live.replace('-', ' '))}'></span>"
                       if started(m) is not None else "")
                done_m = sum(1 for i in m["items"] if i["state"] == "done")
                out.append(f"<details class='{'ms' if finished(m) else 'card ms'}'><summary>{dot}<h3>{e(m['id'])}{' — ' + e(m['title']) if m['title'] else ''}</h3>"
                           f"<span class='muted'>{done_m}/{len(m['items'])} {elapsed([times.get(i['id'], {}) for i in m['items']], finished(m))}</span></summary>")
                doing = sorted((i for i in m["items"] if i["state"] == "doing"), key=when, reverse=True)
                done_i = sorted((i for i in m["items"] if i["state"] == "done"), key=when, reverse=True)
                for it in doing + [i for i in m["items"] if i["state"] not in ("doing", "done")] + done_i:
                    i = order.index(it["id"])
                    default = [order[i - 1]] if i else []
                    ns = by("item", it["id"])
                    waiting = sum(1 for g in gs if g.get("item") == it["id"] and due(g))
                    unlocks = [x for x, y in its.items() if it["id"] in y["after"]]
                    out.append(
                        f"<details class='item {it['state']}'><summary><span class='st {it['state']}'>{LABEL[it['state']]}</span> "
                        f"<b>{e(it['id'])}</b> {e(it['text'])}"
                        + (f" <span class='muted'>primary: {e(Path(it['owner']).name)}</span>" if lead_is_shared(root) else '')
                        + (f" <span class='muted'>after {e(', '.join(it['after']))}</span>" if it["after"] != default else "")
                        + (f" <span class='badge gate'>{waiting} waiting</span>" if waiting else "")
                        + (f" <span class='badge'>{len(ns)} notes</span>" if ns else "")
                        + (f" {timer(times[it['id']])}" if it["id"] in times else "")
                        + f"</summary><div class='body'><p>{e(it['desc'] or 'No description yet.')}</p>"
                        + (f"<p class='muted'>Unlocks: {e(', '.join(unlocks))}</p>" if unlocks else "")
                        + thread(ns, its)
                        + note_box(pid, "item", it["id"], "A note the agent reads when it works on this item", back=f"/?p={pid}&view=roadmap")
                        + f"<p><a href='/item?p={pid}&id={e(it['id'])}'>Open {e(it['id'])}: its work, gates and full thread →</a></p></div></details>")
                out.append("</details>")
            if done_ms:
                out.append("</details>")
            out.append(f"<script>{TICK}</script>")
        else:
            out.append("<div class='card muted'>No roadmap yet: the agent keeps it in ROADMAP.md.</div>")
        gone = sorted({(n["anchor"] or {}).get("item") for n in all_notes} - set(items(road)) - {None})
        if gone:                        # an item renamed or dropped keeps its conversation
            out.append(f"<details class='card'><summary>Notes on items no longer on the roadmap ({', '.join(map(e, gone))})</summary>"
                       + "".join(f"<h3>{e(g)}</h3>" + thread(by("item", g), items(road)) for g in gone) + "</details>")
        # history
        out.append("<h2>History</h2><div class='card'>")
        for line in git(root, "log", "-n", "20", "--format=%h\x1f%aI\x1f%s").splitlines():
            h, t, subj = line.split("\x1f")
            out.append(f"<details class='item'><summary><code>{e(h)}</code> {e(t[:10])} {e(subj)}"
                       + (f" <span class='badge new'>{len(by('commit', h))} notes</span>" if by("commit", h) else "")
                       + f"</summary>{thread(by('commit', h), items(road))}" + note_box(pid, "commit", h, "A note on this work; it reaches the agent on its next turn", back=f"/?p={pid}&view=roadmap") + "</details>")
        # what the person has said to the project as a whole, newest first: sent with Message at the top
        whole = [(n["at"], thread([n], items(road))) for n in all_notes if not n["anchor"]]
        whole += [(m["at"], f"<div class='note'><span class='who'>you · {e(m['at'][:10])} · "
                            f"{'into its console' if m['to'] == 'console' else 'to ' + e(m['to']) + ', written by its agent'}</span>"
                            f"<div>{e(m['text'])}</div></div>") for m in read(root, "messages.jsonl")]
        out.append("</div><h2>Messages</h2><div class='card'>"
                   + ("".join(h for _, _, h in sorted(((at, i, h) for i, (at, h) in enumerate(whole)), reverse=True))
                      or "<p class='muted'>Nothing yet. Message, at the top, sends to its console or to another project.</p>"))
        from . import mail
        ms = mail.messages(root)[-15:]
        if ms:
            me = mail.address(root)
            rows = []
            for m in reversed(ms):
                way = f"from <b>{e(m['from'])}</b>" if m["to"] == me else f"to <b>{e(m['to'])}</b>"
                state = ("answered" if m["answer"] else "waiting for an answer") if m["ask"] else ""
                if m["to"] == me and not m["delivered_at"]:
                    state = "reaches the agent on its next turn"
                rows.append(f"<div class='note'><span class='who'>{e(m['at'][:16].replace('T', ' '))} · {way}"
                            f"{' · ' + state if state else ''}</span><div>{e(m['text'])}</div></div>")
            out.append("</div><h2>Mail with other projects</h2><div class='card'>" + "".join(rows))
        out.append("</div>")
    else:                                 # what needs the person now, and what changed
        out.append(status_card(pid, console.snapshot(root)))
        out.append(pinned_section(pid, root))
        # waiting on you: the same as this project's part of Needs you
        # kept current like Needs you: what the agent settles meanwhile drops out, except while being typed in
        out.append(f"<h2>Waiting on you (<span id='wcount'>{len(moments(root))}</span>)</h2><div class='card' id='waiting'>"
                   f"{waiting_html(pid, root)}</div>"
                   f"<script>{KEEP_CURRENT}keepCurrent(document.getElementById('waiting'), '/needs?p={pid}',"
                   " (w) => { document.getElementById('wcount').textContent = w.querySelectorAll('.need').length; });</script>")
        # since you were last here: one timeline, newest first; Clear rides down the list as you read,
        # and stays within it
        s = since(root, reg["seen"].get(str(root)))
        out.append("<h2>Since you were last here</h2><div class='card since'>")
        if s["first"]:
            out.append("<p class='muted'>First visit: everything below is the current state.</p>")
        events = [(s["moved_at"], f"<li><b>{e(a)} → {e(b)}</b> {e(i)} {e(t)}</li>") for i, a, b, t in s["moved"]]
        events += [(g["at"], f"<li>gate opened: {e(g['question'])}</li>") for g in s["opened"]]
        events += [(n["addressed_at"], f"<li>the agent acted on your note “{e(n['text'][:80])}”: {e(n['reply'])}</li>") for n in s["replies"]]
        events += [(p["at"], f"<li>the agent pinned <a href='/pin/open?p={pid}&id={e(p['id'])}'>{e(p['title'])}</a>"
                    + (f": {e(p['why'])}" if p.get("why") else "") + "</li>") for p in s["pinned"]]
        events += [(stamp(t), f"<li class='muted'><code>{e(h)}</code> {e(t[:10])} {e(subj)}</li>") for h, t, subj in s["commits"][:15]]
        lines = [html_ for _, html_ in sorted(events, key=lambda ev: ev[0] or "", reverse=True)]
        caught_up = (f"<form method='post' action='/seen' class='caughtup'><input type='hidden' name='p' value='{pid}'>"
                     f"<input type='hidden' name='head' value='{e(s['head'])}'><button title='Seen all of this: nothing new until something changes'>Clear</button></form>")
        out.append(caught_up + f"<ul>{''.join(lines)}</ul>" if lines else "<p class='muted'>Nothing has changed.</p>")
        out.append("</div>")
    return shell(reg, pid, "".join(out))


W, H, GX, GY = 172, 56, 38, 16


def roadmap_map(road, pid, all_notes, gs):
    """The roadmap as a map: left to right in the order items build on each other, branches where an
    item says `(after ...)`. Hover for a card; click for the item's page."""
    its, pos = items(road), layout(road)
    cols = max((c for c, _ in pos.values()), default=0) + 1
    rows = max((r for _, r in pos.values()), default=0) + 1
    width, height = cols * (W + GX), rows * (H + GY) + 110    # room below for the hover card
    xy = lambda iid: (pos[iid][0] * (W + GX), pos[iid][1] * (H + GY) + 5)
    edges = []
    for iid, it in its.items():
        for a in it["after"]:
            if a in pos:
                (x1, y1), (x2, y2) = xy(a), xy(iid)
                x1, y1, y2 = x1 + W, y1 + H / 2, y2 + H / 2
                edges.append(f"<path d='M{x1},{y1} C{x1 + GX / 2},{y1} {x2 - GX / 2},{y2} {x2},{y2}'/>")
    nodes = []
    for iid, it in its.items():
        x, y = xy(iid)
        n_notes = sum(1 for n in all_notes if (n["anchor"] or {}).get("item") == iid)
        n_gates = sum(1 for g in gs if g.get("item") == iid and due(g))
        flags = (f"<span class='badge gate'>{n_gates} waiting</span>" if n_gates else "") + \
                (f"<span class='badge'>{n_notes} notes</span>" if n_notes else "")
        nodes.append(
            f"<a class='node {it['state']}' href='/item?p={pid}&id={e(iid)}' style='left:{x}px;top:{y}px;width:{W}px;height:{H}px'>"
            f"<span class='nid'>{e(iid)} · {e(it['milestone'])}</span><span class='ntext'>{e(it['text'])}</span>"
            f"{'<span class=dot></span>' if n_gates else ''}"
            f"<span class='pop'><b>{e(iid)} — {e(it['text'])}</b><span class='st {it['state']}'>{LABEL[it['state']]}</span>"
            f"<span>{e(it['desc'] or 'No description yet.')}</span>{flags}</span></a>")
    legend = " · ".join(f"<b>{e(m['id'])}</b> {e(m['title'])}" for m in road["milestones"])
    return (f"<div class='card mapcard'><div class='legend'>{legend}</div><div class='mapwrap'><div class='map' "
            f"style='width:{width}px;height:{height}px'><svg width='{width}' height='{height}'>{''.join(edges)}</svg>"
            f"{''.join(nodes)}</div></div></div>")


def render_item(reg, pid, iid):
    """One roadmap item in depth: what it is, where it sits, the work done on it, its gates, and the
    thread where the person directs it."""
    root = projects(reg)[pid]
    road = roadmap(root)
    its = items(road)
    it = its.get(iid)
    if not it:
        return shell(reg, pid, f"<p class='muted'>{e(iid)} is not on the roadmap.</p>")
    unlocks = [i for i, x in its.items() if iid in x["after"]]
    link = lambda i: f"<a href='/item?p={pid}&id={e(i)}'>{e(i)} {e(its[i]['text'])}</a>" if i in its else e(i)
    body = [f"<p><a href='/?p={pid}'>← {e(root.name)}</a></p><header><h1>{e(iid)} — {e(it['text'])}</h1>"
            f"<p><span class='st {it['state']}'>{LABEL[it['state']]}</span> · milestone {e(it['milestone'])}</p></header>"]
    if lead_is_shared(root):
        from . import lead
        g = lead.info(root)
        opts = "<option value='inherit'>Project lead (inherited)</option>" + ''.join(
            f"<option value='{e(member)}'{' selected' if g['owners'].get(iid) == member else ''}>{e(Path(member).name)}</option>" for member in g['members'])
        body.append(f"<form class='card' method='post' action='/item/owner'><input type='hidden' name='p' value='{pid}'>"
                    f"<input type='hidden' name='item' value='{e(iid)}'><label>Item primary <select name='member'>{opts}</select></label>"
                    "<button class='quiet'>Assign primary</button></form>")
    body.append(f"<div class='card'><p>{e(it['desc'] or 'No description yet: direct it below and the agent will pick it up.')}</p>"
                f"<p class='muted'>Builds on: {', '.join(link(a) for a in it['after']) or 'nothing'}"
                f"<br>Unlocks: {', '.join(link(u) for u in unlocks) or 'nothing yet'}</p></div>")
    gs = [g for g in gates(root) if g.get("item") == iid]
    if gs:
        body.append("<h2>Gates</h2>")
        for g in gs:
            if g.get("waits_for"):
                body.append(f"<div class='card'><p class='muted'>Waits for {e(g['waits_for'])} to start:</p><p>{e(g['question'])}</p></div>")
                continue
            body.append(f"<div class='card{' gate' if not g['answer'] else ''}'>"
                        + gate_body(root, pid, g, f"/item?p={pid}&id={iid}") + "</div>")
    from . import consult
    cs = [c for c in consult.records(root) if c["decision"] == iid]
    if cs:
        body.append("<h2>Consultations</h2>" + "".join(consultation(c) for c in cs))
    work = [l.split("\x1f") for l in git(root, "log", "--format=%h\x1f%aI\x1f%s", f"--grep={iid}\\b", "-E").splitlines() if l]
    body.append("<h2>Work done on it</h2><div class='card'>" + ("".join(
        f"<div class='muted'><code>{e(h)}</code> {e(t_[:10])} {e(s)}</div>" for h, t_, s in work)
        or "<p class='muted'>No commits mention it yet.</p>") + "</div>")
    ns = [n for n in notes(root) if (n["anchor"] or {}).get("item") == iid]
    body.append("<h2>Direction and notes</h2><div class='card'>" + thread(ns, its)
                + note_box(pid, "item", iid, "What should the agent cover or keep in mind for this item?", back=f"/item?p={pid}&id={iid}")
                + "</div>")
    return shell(reg, pid, "".join(body))


def gate_body(root, pid, gate, back):
    """The same point decisions in Needs you, Waiting on you and the item's record."""
    body = f"<b>{e(gate['question'])}</b>"
    if gate.get("why"):
        body += f"<p class='muted'>{e(gate['why'])}</p>"
    points = gate.get("points", [])
    choices = gate.get("point_decisions") or {}
    rec = None
    if points:
        from . import consult
        rec = next((c for c in consult.records(root) if c["id"] == gate.get("consult")), None)
    rows, editable = [], []
    for point in points:
        sources = "; ".join(f"Consultant {s['consultant']}: {s['model']} at {s['effort']} ({s['provider']})"
                            for s in point["sources"])
        select = (f"<select name='point_{e(point['id'])}' aria-label='Decision on {e(point['id'])}' required>"
                  "<option value=''>Choose…</option>" + "".join(
                      f"<option value='{value}'{' selected' if choices.get(point['id']) == value else ''}>{label}</option>"
                      for value, label in (("accept", "Accept"), ("reject", "Reject"))) + "</select>")
        decision = {"accept": "Accepted", "reject": "Rejected"}.get(choices.get(point["id"]), "No decision")
        control = (f"<span class='badge'>{decision}</span>"
                   if gate["answer"] else select)
        prefix = f"<div class='consult-point'><label><span><b>{e(point['id'])}</b> {e(point['text'])}</span>"
        suffix = f"</label><div class='who'>{e(sources)}</div></div>"
        rows.append(prefix + control + suffix)
        editable.append(prefix + select + suffix)
    def form(content, comment=""):
        hint = "Optional comment" if points else "Your answer"
        css = "add consultant-gate" if points else "add"
        button = "Save decisions" if points else "Send"
        return (f"<form class='{css}' method='post' action='/answer'>"
                f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='gate' value='{e(gate['id'])}'>"
                f"<input type='hidden' name='back' value='{e(back)}'>" + content
                + ("<p class='muted'>Accepting a change allows one checking round. Choose for every point.</p>" if points else "")
                + f"<textarea name='text' placeholder='{hint}'>{e(comment)}</textarea><button>{button}</button></form>")
    if gate["answer"]:
        answer_text = "Cleared without an answer." if gate.get("cleared") else f"Your answer: {e(gate['answer'])}"
        body += "".join(rows) + f"<div class='pre'>{answer_text}</div>"
        if points:
            body += ("<p class='muted'>The checking round is already recorded; this decision has used both rounds.</p>"
                     if rec and rec.get("checking_round") else
                     "<p class='muted'>A checking round is allowed for the accepted changes.</p>"
                     if "accept" in choices.values() else
                     "<p class='muted'>No change accepted; no checking round.</p>")
            body += ("<details><summary>Revise your choices</summary>"
                     + form("".join(editable), gate.get("comment", "")) + "</details>")
    else:
        body += form("".join(rows))
    if rec:
        body += ("<details><summary>Full consultant answers</summary>"
                 + consultation(dict(rec, adopted=None, point_decisions=[])) + "</details>")
    return body


def consultation(c):
    """One round of consulting on a decision: who was asked and why, what each said and cost, what the person took."""
    def answer(x):
        cost = f" · ${x['cost']:.2f}" if x.get("cost") is not None else ""
        err = f" · {e(x['error'])}" if x.get("error") else ""
        return (f"<details><summary>{e(x['model'])} at {e(x['effort'])}{cost}{err}</summary>"
                f"<p class='muted'>{e(x.get('why') or '')}</p><div class='pre'>{e(x.get('text') or '(no answer)')}</div></details>")
    note = f" · {e(c['note'])}" if c.get("note") else ""
    rejected = "\n".join(f"{p['id']}: {p['text']}" for p in c.get("point_decisions", []) if p["decision"] == "reject")
    checked = (f"<div class='pre'>Changes checked in this round: {e(c['checked']['words'])}</div>"
               if c.get("checked") else "")
    return (f"<div class='card'><b>Round {c['round']}: {e(c['question'])}</b> "
            f"<span class='muted'>{e(c['at'][:10])} · ${c['cost']:.2f}{note}</span>"
            + "".join(answer(x) for x in c["answers"])
            + checked
            + (f"<div class='pre'>You accepted: {e(c['adopted'])}</div>" if c.get("adopted") else "")
            + (f"<div class='pre'>You rejected: {e(rejected)}</div>" if rejected else "") + "</div>")


def suggestions(name, values):
    """A datalist: plain values, or (value, label) pairs."""
    pair = lambda v: v if isinstance(v, tuple) else (v, "")
    return f"<datalist id='{name}'>" + "".join(
        f"<option value='{e(pair(v)[0])}'>{e(pair(v)[1])}</option>" for v in values) + "</datalist>"


def default_label(value, fallback="Claude Code picks"):
    return f"Default ({value})" if value else f"Default ({fallback})"


PROVIDER_FIELDS = """
(() => {
  const box = document.currentScript.previousElementSibling, data = JSON.parse(box.dataset.providers);
  const provider = box.querySelector('[name=provider]'), pick = box.querySelector('[name=model_pick]');
  provider.addEventListener('change', () => {
    pick.replaceChildren(...data[provider.value].map(v => {
      const option = document.createElement('option'); option.value = v[0]; option.textContent = v[1]; return option;
    }));
  });
})();
"""


def bench_framing(key):
    from . import bench
    return bench.framing(key)


def provider_fields(cur, model, effort, blank):
    """An explicit Auto pair: saving unrelated settings cannot create a model pin."""
    from . import providers as pv, selection
    cur = cur or registry()['settings']['provider']
    data = {}
    for family, provider in pv.PROVIDERS.items():
        if pv.usable(provider):
            settings = registry()['settings']
            pin = settings if family == settings['provider'] else (settings.get('main_models') or {}).get(family)
            chosen = selection.resolve(family, 'main', pin if blank == 'global' else None)
            label = f"Auto: {chosen['model']}" + (f" at {chosen['effort']}" if chosen['effort'] else '')
        else:
            label = 'Auto'
        opts = [('auto', label)]
        for mid, name in pv.available(provider):
            opts += [(mid + ':' + (x or ''), name + (f' at {x}' if x else ''))
                     for x in pv.efforts_of(provider, mid) or [None]]
        data[family] = opts
    selected = model + ':' + (effort or '') if model else 'auto'
    if model and not effort:
        chosen = selection.concrete(cur, {'model': model})
        if chosen:
            selected = model + ':' + (chosen['effort'] or '')
    if model and selected not in dict(data[cur]):
        data[cur].append((selected, f'{model} at {effort or "unspecified effort"} (current pick)'))
    return (f"<div class='provider-fields' data-providers='{e(json.dumps(data))}'>"
            "<label>Provider <select name='provider'>" + ''.join(
                f"<option value='{e(k)}'{' selected' if k == cur else ''}{'' if pv.usable(p) else ' disabled'}>{e(p.label)}{'' if pv.usable(p) else ' (not installed)' if not pv.installed(p) else ' (off in Settings)'}</option>"
                for k, p in pv.PROVIDERS.items()) + '</select></label>'
            + "<label>Model and effort <select name='model_pick'>" + ''.join(
                f"<option value='{e(v)}'{' selected' if v == selected else ''}>{e(label)}</option>" for v, label in data[cur])
            + "</select> <a href='/models'>ⓘ benchmarks</a></label>"
            + f"<p class='muted'>{e(bench_framing(cur))}</p></div>"
            + f"<script>{PROVIDER_FIELDS}</script>")


def tier_fields(root):
    """A project's helper tiers: each colony's default from the cards (Auto) or the project's own model and effort."""
    from . import bench, providers as pv
    p = pv.of(root)
    key, auto, own = pv.key(p), None, bench.plan(root)
    from . import selection
    global_pins = registry()['settings'].get('helper_models') or {}
    auto = {t: selection.resolve(key, t, global_pins.get(key + ':' + t)) for t in bench.TIERS}
    out = []
    for t in bench.TIERS:
        a, mine = auto.get(t), own.get(t)
        label = f"Auto: {a['model']}" + (f" at {a['effort']}" if a and a["effort"] else "") if a else "Auto: not yet measured"
        opts = [f"<option value='auto'{'' if mine else ' selected'}>{e(label)}</option>"]
        for mid, name in pv.available(p):
            efforts = pv.efforts_of(p, mid) or [""]
            opts.append(f"<optgroup label='{e(name)}'>" + "".join(
                f"<option value='{e(mid)}:{e(x)}'{' selected' if mine and (mine['model'], mine['effort'] or '') == (mid, x) else ''}>"
                f"{e(name)}" + (f" at {e(x)}" if x else "") + "</option>" for x in efforts) + "</optgroup>")
        out.append(f"<label>{e(t.capitalize())} helpers <select name='tier_{t}'>{''.join(opts)}</select></label>")
    return "".join(out)


def project_settings_form(pid, own, action="/project-settings", root=None):
    """The choices a project can make for itself; blank keeps the global one."""
    opt = lambda name, choices, cur: (f"<select name='{name}'>" + "".join(
        f"<option value='{v}'{' selected' if str(cur) == v else ''}>{label}</option>" for v, label in choices) + "</select>")
    g = registry()["settings"]
    remote = "on" if own.get("remote", g["remote"]) else "off"
    names = {"ask": "ask each time", "edits": "accept edits", "all": "allow everything", "plan": "plan only"}
    return (f"<form method='post' action='{action}' class='options'><input type='hidden' name='p' value='{pid}'>"
            + provider_fields(own.get("provider", ""), own.get("model", ""), own.get("effort", ""), "global") +
            f"<label>Permissions {opt('permissions', [(k, v) for k, v in names.items()], own.get('permissions') or g['permissions'])}</label>"
            f"<label>Remote Control {opt('remote', [('on', 'on'), ('off', 'off')], remote)}</label>"
            f"<label>Safe pause at <input name='safe_pause' value='{e(str(own.get('safe_pause', '')))}' size='4' "
            f"placeholder='{e(str(g['safe_pause'] or 'off'))}'> % of a usage limit <span class='muted'>(blank: colony's; off: never)</span></label>"
            + (auto_balance_fields(root) + tier_fields(root) if root else "") + f"<button>Save</button></form>")


def auto_balance_fields(root=None):
    from . import bench, intelligence, providers, selection
    settings = registry()['settings']
    own = project_settings(root)[1] if root else {}
    inherited = root is not None and 'auto_balance' not in own
    selected = int(own.get('auto_balance', settings.get('auto_balance', 3)))
    entries, state = bench.standings(), selection.read()
    ceiling = max((p['score'] for p in intelligence.pairs(entries)), default=None)
    eligible = [x for x in entries if x['model'] not in state['rejected']]
    families = [providers.key(providers.of(root))] if root else [k for k, p in providers.PROVIDERS.items() if providers.usable(p)]
    roles = ('main', *bench.TIERS) if root else selection.ROLES
    panels = []
    def describe(value):
        return value['model'] + (' at ' + value['effort'] if value.get('effort') else '') if value else 'No accepted pick'
    for level, label in enumerate(intelligence.POSITIONS):
        rows = []
        for family in families:
            for role in roles:
                ident = selection.key(family, role, root)
                pick = bench.role_pick(family, role, eligible, balance=level, ceiling=ceiling, blocked=state['blocked'].get(ident, []))
                held = state['accepted'].get(ident) or state['accepted'].get(selection.key(family, role))
                waiting = bool(pick and settings['model_adoption'] == 'ask' and pick['model'] not in state['approved'])
                text = (describe(pick) + (' · approval required' if waiting else '') + '. ' + pick['why']) if pick else 'Missing comparable evidence; retain accepted pick.'
                rows.append(f"<tr><th>{e(family)} · {e(role)}</th><td>{e(text)}<div class='muted'>Accepted: {e(describe(held))}</div></td></tr>")
        panels.append(f"<div data-balance='{level}'{' hidden' if level != selected else ''}><p><strong>{e(label)}</strong></p><table class='bench'>{''.join(rows)}</table></div>")
    toggle = ("<label><input type='checkbox'" + (' checked' if inherited else '') +
              " onchange=\"const r=this.closest('.auto-balance').querySelector('input[type=range]');r.disabled=this.checked;"
              f"if(this.checked){{r.value='{settings.get('auto_balance', 3)}';r.dispatchEvent(new Event('input'))}}\"> Use colony setting</label>") if root else ''
    return ("<div class='auto-balance'><label>Auto: Intelligence → Balanced → Economy "
            f"<input type='range' name='auto_balance' min='0' max='6' step='1' value='{selected}'{' disabled' if inherited else ''} "
            "oninput=\"for(const p of this.closest('.auto-balance').querySelectorAll('[data-balance]')){p.hidden=p.dataset.balance!==this.value;if(!p.hidden)this.closest('.auto-balance').querySelector('output').textContent=p.querySelector('strong').textContent}\">"
            f"<output>{e(intelligence.POSITIONS[selected])}</output></label>"
            + toggle + "<p class='muted'>Higher intelligence keeps goals near the shared ceiling. Economy lowers the goals so cheaper pairs qualify. "
            "Usage never moves this slider. Chores keep their value pick. Explicit model pins stay in effect.</p>"
            "<details><summary>What each position picks today</summary>" + ''.join(panels) +
            "</details><p class='muted'>Preview only until saved; new models follow your adoption setting. Missing evidence keeps the accepted choice.</p></div>")


def folder_browser(reg, current, purpose):
    """Browse the machine running the board, to add a folder as a project, make a new one, or add a
    folder whose subfolders are all projects."""
    here = Path(current).expanduser() if current else Path.home()
    if not here.is_dir():
        here = Path.home()
    try:
        subs = sorted((d for d in here.iterdir() if d.is_dir() and not d.name.startswith(".")), key=lambda d: d.name.lower())
    except PermissionError:
        subs = []
    q = lambda d: urllib.parse.quote(str(d))
    head = "Add a project folder" if purpose == "project" else "Add a folder of projects"
    out = [f"<header><h1>{head}</h1><p class='muted'>Browsing the machine the board runs on.</p></header><div class='card'>"
           f"<p><code>{e(here)}</code></p><p>"
           + (f"<a href='/add?dir={q(here.parent)}&for={purpose}'>↑ up</a>" if here != here.parent else "") + "</p><ul class='dirs'>"]
    out += [f"<li><a href='/add?dir={q(d)}&for={purpose}'>{e(d.name)}/</a></li>" for d in subs] or ["<li class='muted'>no subfolders</li>"]
    out.append("</ul>")
    if purpose == "project":
        choices = (project_settings_form(0, {}, action="")
                   .split("<input type='hidden' name='p' value='0'>", 1)[1].rsplit("<button>Save</button></form>", 1)[0])
        out.append(f"<form method='post' action='/add' class='options'><input type='hidden' name='path' value='{e(here)}'>"
                   f"<p class='muted'>This folder becomes the project's root. Its settings (blank: global):</p>{choices}"
                   f"<button>Add this folder as a project</button></form><hr>"
                   f"<form method='post' action='/new' class='options'><input type='hidden' name='within' value='{e(here)}'>"
                   f"<label>New project named <input name='name' placeholder='a new, empty project in this folder'></label>{choices}"
                   f"<button>Create new project here</button></form>")
    else:
        out.append(f"<form method='post' action='/roots'><input type='hidden' name='add' value='{e(here)}'>"
                   f"<button>Use this folder: every subfolder becomes a project</button></form>")
    return shell(reg, -2, "".join(out) + "</div>")


def duplicate_agent_page(reg, original, chosen):
    hidden = f"<input type='hidden' name='path' value='{e(workdir(original))}'>" + ''.join(
        f"<input type='hidden' name='{e(k)}' value='{e(v)}'>" for k, v in chosen.items())
    return shell(reg, -2, f"<h1>{e(original.name)} already has an agent</h1>"
                 "<p>Add this agent as a helper for assigned items, or switch the project lead to it. Both use the same Vision and roadmap.</p>"
                 f"<form method='post' action='/add'>{hidden}<button name='role' value='helper'>Add helper</button>"
                 "<button class='quiet' name='role' value='lead'>Switch lead</button></form>")


def add_project_page(reg, tab="new", error=""):
    """Add a project three ways, one tab each: a new one, an existing folder, or a clone of a GitHub
    repository. Folders are chosen with Browse…, which starts closed and opens in place."""
    choices = (project_settings_form(0, {}, action="")
               .split("<input type='hidden' name='p' value='0'>", 1)[1].rsplit("<button>Save</button></form>", 1)[0])
    where = reg["new_root"]
    picker = lambda name, value, placeholder: (
        f"<div class='picker'><div class='chosen'><input name='{name}' value='{e(value)}' placeholder='{e(placeholder)}' readonly>"
        f"<button type='button' class='quiet browsebtn'>Browse…</button></div><div class='browse' hidden></div></div>")
    panels = {
        "new": ("New", f"<form method='post' action='/new' class='options'>"
                       f"<label>Name <input name='name' placeholder='a new, empty project'></label>"
                       f"<div class='muted'>Created in:</div>{picker('within', where, where)}{choices}<button>Create project</button></form>"),
        "existing": ("Existing folder", f"<form method='post' action='/add' class='options'>"
                     f"<div class='muted'>The folder becomes the project's root. Existing plans stay intact while you and its agent shape the vision.</div>"
                     f"{picker('path', '', 'No folder chosen')}{choices}<button>Add project</button></form>"),
        "github": ("From GitHub", f"<form method='post' action='/clone' class='options'>"
                   f"<label>Repository <input name='url' placeholder='https://github.com/owner/repo'></label>"
                   f"<label>Folder name <input name='name' placeholder='the repository name'></label>"
                   f"<div class='muted'>Cloned into:</div>{picker('within', where, where)}{choices}<button>Clone and add</button></form>"),
    }
    tab = tab if tab in panels else "new"
    seg = "".join(f"<button type='button' class='seg{' on' if k == tab else ''}' data-tab='{k}'>{label}</button>" for k, (label, _) in panels.items())
    body = "".join(f"<div class='panel-tab' data-tab='{k}'{'' if k == tab else ' hidden'}>{html_}</div>" for k, (_, html_) in panels.items())
    return shell(reg, -2, f"""<header class='project'><div class='titlerow'><h1>Add a project</h1>
<a class='exitlink' href='/'>✕ Cancel</a></div></header>{f"<div class='card gate'>{e(error)}</div>" if error else ""}
<p>Your project's agent will first talk with you about the finished work. Once you agree the vision,
it lays out the roadmap toward it.</p>
<div class='segs'>{seg}</div><div class='card'>{body}</div>
<script>
document.querySelectorAll('.seg').forEach((b) => b.addEventListener('click', () => {{
  document.querySelectorAll('.seg').forEach((x) => x.classList.toggle('on', x === b));
  document.querySelectorAll('.panel-tab').forEach((p) => p.hidden = p.dataset.tab !== b.dataset.tab);
}}));
// Browse… opens the machine's folders in place: a folder goes in, "Use this folder" chooses it.
async function openDir(picker, dir) {{
  const r = await fetch('/add/browse?dir=' + encodeURIComponent(dir), {{cache: 'no-store'}});
  const box = picker.querySelector('.browse'); box.innerHTML = await r.text(); box.hidden = false;
}}
document.querySelectorAll('.picker').forEach((picker) => {{
  const input = picker.querySelector('input'), box = picker.querySelector('.browse');
  picker.querySelector('.browsebtn').addEventListener('click', () => box.hidden ? openDir(picker, input.value) : (box.hidden = true));
  box.addEventListener('click', (ev) => {{
    const b = ev.target.closest('button'); if (!b) return;
    ev.preventDefault();
    if (b.dataset.use !== undefined) {{ input.value = b.dataset.use; box.hidden = true; return; }}
    openDir(picker, b.dataset.dir);
  }});
}});
const url = document.querySelector("input[name='url']"), repoName = document.querySelector(".panel-tab[data-tab='github'] input[name='name']");
url.addEventListener('input', () => {{ repoName.placeholder = (url.value.replace(/\\.git$/, '').split(/[\\/:]/).pop() || 'the repository name'); }});
</script>""")


def machine_folders(current):
    """One folder of the machine, for choosing where a project lives: subfolders to go into, and the choice."""
    here = Path(current).expanduser() if current else Path.home()
    if not here.is_dir():
        here = Path.home()
    try:
        subs = sorted((d for d in here.iterdir() if d.is_dir() and not d.name.startswith(".")), key=lambda d: d.name.lower())
    except OSError:
        subs = []
    out = [f"<div class='muted'><code>{e(here)}</code></div>",
           f"<button type='button' data-use='{e(here)}'>Use this folder</button>"]
    if here != here.parent:
        out.append(f"<button type='button' class='quiet' data-dir='{e(here.parent)}'>↑ up</button>")
    out += [f"<button type='button' class='quiet' data-dir='{e(d)}'>📁 {e(d.name)}/</button>" for d in subs[:400]]
    return "".join(out)


def clone(url, within, name=""):
    """Clone a repository into a new folder under `within`; its path, or a ValueError saying what went wrong."""
    url = url.strip()
    if not re.match(r"^(https://|git@|ssh://|file://)[^\s]+$", url):
        raise ValueError("That doesn't look like a repository link (https://github.com/owner/repo).")
    name = re.sub(r"[^A-Za-z0-9_. -]", "-", (name or "").strip()) or re.sub(r"\.git$", "", re.split(r"[/:]", url.rstrip("/"))[-1])
    dest = Path(within).expanduser() / name
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"{dest} already exists and isn't empty: choose another folder name.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "clone", "--quiet", url, str(dest)], capture_output=True, text=True, timeout=900,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if r.returncode != 0:
        raise ValueError("git clone failed: " + (r.stderr.strip().splitlines() or ["no detail"])[-1])
    return dest


def focus(root):
    """Where a project stands, from its roadmap: the milestone in progress and what's under way, to verify
    and next."""
    road = roadmap(root)
    for m in road["milestones"]:
        open_ = [i for i in m["items"] if i["state"] != "done"]
        if open_:
            pick = lambda st, n=5: [f"{i['id']} {i['text'][:70]}" for i in open_ if i["state"] == st][:n]
            return {"milestone": f"{m['id']} — {m['title']}" if m['title'] else m['id'], "doing": pick("doing"), "verify": pick("verify"), "next": pick("todo", 3)}
    return {"milestone": "", "doing": [], "verify": [], "next": []}


def monitor_page(reg, view="overview"):
    """The monitor, like a project: Overview (what needs the person, every project's status, what the
    monitor decided), Helm (its stance toward each project), Console."""
    from . import monitor
    monitor.ensure()
    tab = lambda v, label: f"<a class='{'on' if view == v else ''}' href='/monitor?view={v}'>{label}</a>"
    on = monitor.helm()
    targets = "".join(f"<option value='{i}'>{e(p.name)}</option>" for i, p in enumerate(projects(reg)))
    # the monitor relays for the person, so what it sends a project is taken as the person's word
    message = (f"<details class='psettings'><summary>Message a project</summary><div class='panel'>"
               f"<form class='options' method='post' action='/message'><input type='hidden' name='p' value='monitor'>"
               f"<label>To <select name='to'>{targets}</select></label>"
               f"<label class='stack'>What should the monitor tell them for you?"
               f"<textarea name='text' placeholder='It relays it as yours, in full, and checks it landed'></textarea></label>"
               f"<button>Have the monitor send it</button></form></div></details>") if targets else ""
    head = (f"<header class='project'><div class='titlerow'><h1>monitor</h1>{message}<form method='post' action='/helm' class='helmform'>"
            f"<input type='hidden' name='state' value='{'off' if on else 'on'}'><button class='{'quiet' if on else ''}'>"
            f"{'Take the helm back' if on else 'Give it the helm'}</button></form></div>"
            f"<div class='tabs'>{tab('overview', 'Overview')}{tab('helm', 'Helm')}{tab('console', 'Console')}</div>"
            f"<p class='muted'>{'It holds the helm of every project included in it, and settles routine questions within each one' + chr(39) + 's direction.' if on else 'It sleeps: nothing wakes it but you and its scouting, and the board shows you what needs you.'}"
            f" Which projects are included, and each one's direction, are on the Helm tab.</p></header>")
    plist = projects(reg)
    if view == "console":
        body = head + console.PAGE.format(label=e(monitor.provider().label), path=e(monitor.home()), name=monitor.name(), pid=-1,
                                          token=console.token(), focus='true', scrolled=e(monitor.provider().scrolled_marker))
        return shell(reg, -1, body, wide=True)
    if view == "direction":
        mine = (home() / "direction.md").exists()
        body = head + (f"<div class='card'><h3>Standing direction, for every project</h3>"
                       f"<p class='muted'>How the monitor steers all your projects: what it settles, what always comes back to you, "
                       f"how it holds a project to its vision. Each project's own direction, on the Helm tab, adds to this. "
                       f"{'This is your version.' if mine else 'This is colony' + chr(39) + 's default.'}</p>"
                       f"<form class='options' method='post' action='/direction'><textarea name='text' class='direction'>{e(monitor.direction())}</textarea>"
                       f"<div class='choices'><button>Save</button>"
                       + ("<button class='quiet' name='reset' value='1'>Back to colony's default</button>" if mine else "")
                       + "</div></form><p><a href='/monitor?view=helm'>← Helm</a></p></div>")
        return shell(reg, -1, body)
    if view == "helm":
        cards = [f"<div class='card'><div class='titlerow'><h3>Every project</h3>"
                 f"<a href='/monitor?view=direction'>Standing direction →</a></div>"
                 f"<p class='muted'>What the monitor settles, what always comes back to you, and how it steers, for all projects"
                 f"{' (your version)' if (home() / 'direction.md').exists() else ''}. Each project below adds its own.</p></div>"]
        for pid, p in enumerate(plist):
            if not p.exists():
                continue
            pos, f, held = monitor.posture(p), focus(p), monitor.helm_for(p)
            opt = lambda v, label, cur: f"<option value='{v}'{' selected' if cur == v else ''}>{label}</option>"
            lines = "".join(f"<li><span class='kind'>{k}</span> {e(x)}</li>" for k in ("doing", "verify", "next") for x in f[k])
            made = "".join(f"<div class='note'><span class='who'>{e(d['at'][:16].replace('T', ' '))}</span><div>{e(d['text'])}</div></div>"
                           for d in monitor.decisions(p, 8))
            cards.append(f"<div class='card helmcard'><div class='titlerow'><h3>{e(p.name)}</h3>"
                         f"<span class='badge {'gate' if held else ''}'>{'monitor holds the helm' if held else 'helm with you'}</span></div>"
                         f"<form class='options' method='post' action='/posture'><input type='hidden' name='p' value='{pid}'>"
                         f"<label><input type='checkbox' name='helm' value='on'{'' if pos['helm'] is False else ' checked'}> Include in the monitor's helm "
                         f"<span class='muted'>({'held now' if held else 'the helm is with you' if not on else 'left out'})</span></label>"
                         f"<label class='stack'>Direction for the monitor<textarea name='direction' placeholder='Empty: the standing direction alone. Add what is particular here: this version is… and it is done when… · always bring back… · you may settle… · the yardstick…'>{e(pos['direction'])}</textarea></label>"
                         f"<label><input type='checkbox' name='scouting' value='on'{' checked' if pos['scouting'] else ''}> Scout for it every "
                         f"<input name='scout' type='number' min='1' step='1' value='{pos['scout'] or 24}' class='hours'> hours "
                         f"<span class='muted'>(if worked on since)</span></label>"
                         f"<label class='stack'>What scouting should favour here<textarea name='scout_note' class='short' placeholder='e.g. faster test runs; papers on forecasting; nothing that changes the stack'>{e(pos['scout_note'])}</textarea></label>"
                         f"<button>Save</button></form>"
                         f"<div class='focus'><b>Now: {e(f['milestone'] or 'no roadmap yet')}</b><ul>{lines}</ul></div>"
                         + (f"<details><summary>What it decided here</summary>{made}</details>" if made else "") + "</div>")
        body = head + ("".join(cards) if len(cards) > 1 else cards[0] + "<p class='muted'>No projects yet.</p>")
        return shell(reg, -1, body)
    statuses = "".join(f"<div class='mstatus'><div class='who'>{e(p.name)}</div>{status_card(pid, console.snapshot(p))}</div>"
                       for pid, p in enumerate(plist) if p.exists())
    recent = "".join(f"<div class='note'><span class='who'>{e(Path(d['project']).name)} · {e(d['at'][:16].replace('T', ' '))}</span>"
                     f"<div>{e(d['text'])}</div></div>" for d in monitor.decisions(None, 10))
    body = (head + f"<h2>Needs you</h2><div class='card' id='needs-box'><div id='needs'>{needs_you(reg)}</div></div>"
            f"<script>{KEEP_CURRENT}keepCurrent(document.getElementById('needs'), '/needs');</script>"
            f"<h2>Projects</h2>{statuses or '<p class=muted>No projects yet.</p>'}"
            + (f"<h2>What the monitor decided</h2><div class='card'>{recent}</div>" if recent else ""))
    return shell(reg, -1, body)


def status_card(pid, snap):
    """The project's status, as its console shows it: working / needs you / idle / off, what it's doing,
    and its background agents."""
    act = snap.get("activity") or {}
    agents = "".join(f"<div class='agent{' current' if a['current'] else ''}'><span>{'●' if a['current'] else '○'} {e(a['name'])}</span>"
                     f"<span class='muted'>{e(a['detail'])}</span></div>" for a in act.get("agents", []))
    return (f"<a class='status' id='status-{pid}' href='/?p={pid}&view=console'><div class='statusline'>"
            f"<span class='sdot {snap['state'].replace(' ', '-')}'></span><b>{e(snap['state'])}</b>"
            f"<span class='muted'>{(' · ' + e(act['line'])) if act.get('line') else ''}</span></div>"
            f"<div class='agents'>{agents}</div></a>")


def pinned_section(pid, root):
    """What the person and the agent show each other: tap to open (a text file opens to edit), comment to the
    agent about it, or unpin it."""
    rows = []
    for p in pins.pins(root):
        icon = {"url": "🔗", "upload": "📎"}.get(p["kind"], "📄")
        new_tab = "" if pins.is_text(p) else " target='_blank' rel='noopener'"
        hidden = f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='id' value='{e(p['id'])}'>"
        rows.append(f"<div class='pin'><div class='pinline'><a class='pintitle' href='/pin/open?p={pid}&id={e(p['id'])}'{new_tab}>"
                    f"{icon} {e(p['title'])}</a><details class='pinmore'><summary>⋯</summary><div class='pinmenu'>"
                    f"<form class='add' method='post' action='/pin/comment'>{hidden}"
                    f"<textarea name='text' placeholder='Comment to the agent about this'></textarea><button>Send</button></form>"
                    f"<form method='post' action='/unpin'>{hidden}<button class='quiet'>Unpin</button></form></div></details></div>"
                    f"<div class='who'>{'by the agent · ' if p['by'] == 'agent' else ''}{e(p['target'] if p['kind'] != 'url' else p['target'][:60])}"
                    + (f" · {e(p['why'])}" if p.get("why") else "") + "</div></div>")
    return (f"<div class='pinned'><div class='pinhead'><h2>Pinned</h2><a href='/pins/add?p={pid}'>+ Pin</a></div>"
            + ("".join(rows) if rows else "<p class='muted'>Nothing pinned yet: files, links and uploads you and the agent want at hand.</p>")
            + "</div>")


def pin_add_page(reg, pid):
    """Pin a file in the project (Browse… to choose it), a link, or an upload; each with a title and an
    optional comment to the agent."""
    root = projects(reg)[pid]
    comment = "<textarea name='comment' placeholder='Optional: a comment to the agent about it'></textarea>"
    title = "<input name='title' placeholder='Title (optional)'>"
    hidden = f"<input type='hidden' name='p' value='{pid}'>"
    body = (f"<header class='project'><div class='titlerow'><h1>Pin to {e(Path(root).name)}</h1>"
            f"<a class='exitlink' href='/?p={pid}'>✕ Cancel</a></div><p class='muted'>A file in the project, a link, or an upload. "
            f"The agent hears of it on its next turn; a comment reaches it as a message.</p></header>"
            f"<h2>A file in the project</h2><div class='card'><form class='add pinform' method='post' action='/pin'>{hidden}"
            f"<input type='hidden' name='kind' value='file'><div class='chosen'><input name='target' id='pick' placeholder='No file chosen' readonly>"
            f"<button type='button' class='quiet' id='browse'>Browse…</button></div><div class='browse' id='browser' hidden></div>"
            f"{title}{comment}<button>Pin file</button></form></div>"
            f"<h2>A link</h2><div class='card'><form class='add pinform' method='post' action='/pin'>{hidden}<input type='hidden' name='kind' value='url'>"
            f"<input name='target' placeholder='https://… or http://localhost:5173'>{title}{comment}<button>Pin link</button></form></div>"
            f"<h2>An upload</h2><div class='card'><form class='add pinform' method='post' action='/pin/upload' enctype='multipart/form-data'>{hidden}"
            f"<input type='file' name='file'>{title}{comment}<button>Upload and pin</button></form></div>"
            # browsing happens in place: folders open without leaving the page, a file fills the choice
            f"""<script>
const browser = document.getElementById('browser'), pick = document.getElementById('pick');
async function open_(dir) {{
  const r = await fetch('/pins/browse?p={pid}&dir=' + encodeURIComponent(dir), {{cache: 'no-store'}});
  browser.innerHTML = await r.text(); browser.hidden = false;
}}
document.getElementById('browse').addEventListener('click', () => browser.hidden ? open_('') : (browser.hidden = true));
browser.addEventListener('click', (ev) => {{
  const b = ev.target.closest('button'); if (!b) return;
  ev.preventDefault();
  if (b.dataset.dir !== undefined) return open_(b.dataset.dir);
  pick.value = b.dataset.file; browser.hidden = true;
}});
</script>""")
    return shell(reg, pid, body)


def pin_browse(root, rel=""):
    """One folder of the project, for choosing a file to pin: folders open in place, files choose."""
    here = pins.inside(root, rel) or Path(root).resolve()
    if not here.is_dir():
        here = Path(root).resolve()
    rel_here = str(here.relative_to(Path(root).resolve()))
    rel_here = "" if rel_here == "." else rel_here
    try:
        entries = sorted((c for c in here.iterdir() if not c.name.startswith(".")), key=lambda c: (c.is_file(), c.name.lower()))
    except OSError:
        entries = []
    out = [f"<div class='muted'><code>/{e(rel_here)}</code></div>"]
    if rel_here:
        up = str(Path(rel_here).parent)
        out.append(f"<button type='button' class='quiet' data-dir='{e('' if up == '.' else up)}'>↑ up</button>")
    for c in entries[:400]:
        r = str(Path(rel_here) / c.name) if rel_here else c.name
        out.append(f"<button type='button' class='quiet' data-dir='{e(r)}'>📁 {e(c.name)}/</button>" if c.is_dir()
                   else f"<button type='button' class='quiet' data-file='{e(r)}'>📄 {e(c.name)}</button>")
    return "".join(out)


def pin_editor(reg, pid, pin):
    """A pinned text file, to read and edit in place; saving tells the agent quietly."""
    root = projects(reg)[pid]
    path = pins.inside(root, pin["target"])
    text = path.read_text(errors="replace") if path and path.exists() else ""
    body = (f"<form class='editor' method='post' action='/pin/save'><input type='hidden' name='p' value='{pid}'>"
            f"<input type='hidden' name='id' value='{e(pin['id'])}'><div class='editbar'><a href='/?p={pid}'>← {e(Path(root).name)}</a>"
            f"<b>{e(pin['title'])}</b><button>Save</button></div><textarea name='text' spellcheck='true'>{e(text)}</textarea></form>")
    return shell(reg, pid, body, wide=True)


def waiting_items(p, snap=None):
    """Everything a project is waiting on the person for. The one definition: Needs you, the project's
    Waiting on you, the sidebar's count, `colony projects` and the monitor's wake-ups all read this.
      gate    a decision the agent put to the person (`colony gate`)
      choice  an on-screen choice in its console, such as a permission or trust question
      screen  its console needs the person but the choice can't be read (answer it in the console)
      ask     a turn that asked the person something in the console
      verify  a roadmap item built but waiting to be checked or accepted
    Each has a key that stays the same while it waits, so it is announced once."""
    if not p.exists():
        return []
    out = [{"kind": "gate", "key": "gate:" + g["id"], "gate": g, "summary": f"opened a gate: {g['question']}"}
           for g in gates(p) if due(g)]
    snap = snap or console.snapshot(p, lines=4)
    if snap["state"] == "needs you":
        found = providers.of(p).choice(console.screen(console.session_name(p)))
        if found:
            question, options, _ = found
            key = "choice:" + hashlib.sha1("\n".join(question + options).encode()).hexdigest()[:10]
            out.append({"kind": "choice", "key": key, "question": question, "options": options,
                        "summary": f"is asking in its console: {(question or [''])[-1]} ({' / '.join(options)})"})
        else:
            key = "screen:" + hashlib.sha1("\n".join(snap["lines"]).encode()).hexdigest()[:10]
            out.append({"kind": "screen", "key": key, "lines": snap["lines"],
                        "summary": "needs you in its console: " + " / ".join(snap["lines"][-2:])})
    out += [{"kind": "ask", "key": "ask:" + a["id"], "ask": a, "summary": "asked you: " + a["text"][-300:]} for a in asks(p)]
    from . import lead, progress
    shared = lead.group(p)
    primary = not shared or shared['lead'] == str(p.resolve())
    held = progress.held_items(p) if shared else set()
    if primary:
        out += progress.waiting(p) if shared else []
    told = ready_notes(p)
    for it in (i for i in items(roadmap(p)).values() if primary and i["state"] == "verify" and i['id'] not in held):
        r = told.get(it["id"], {})
        it = dict(it, what=r.get("what") or plain(it["text"]), check=r.get("check", ""))
        out.append({"kind": "verify", "key": "verify:" + it["id"], "item": it, "summary": f"has {it['id']} ready for your OK: {it['what']}"})
    cleared = dismissed(p)
    gone = cleared - {w["key"] for w in out}
    for key in gone:                     # its item has gone: if the same thing comes back later, it shows again
        append(p, "dismissed.jsonl", {"type": "restored", "key": key, "at": now()})
    return [w for w in out if w["key"] not in cleared]


def tell_pinned(root, pin, comment=""):
    """The agent hears of the person's pin: with a comment, as a message in their words; without one, quietly."""
    text = f"The person pinned {pins.describe(pin)} on the board."
    add_note(root, {"pin": pin["id"]}, text + (f" Their comment: {comment}" if comment else ""),
             author='person' if comment else 'colony', quiet=not comment)


def dismissed(root):
    out = set()
    for e in read(root, "dismissed.jsonl"):
        (out.add if e["type"] == "dismissed" else out.discard)(e["key"])
    return out


def clear_waiting(root, key):
    """The person cleared something from what waits on them. A gate or a question is closed; an item to
    verify or a choice on screen is set aside until it goes. The agent hears of it quietly, on its next turn,
    without being woken: it may already be handled."""
    if key.startswith("gate:"):
        clear_gate(root, key[5:])
        return
    w = next((w for w in waiting_items(root) if w["key"] == key), None)
    if not w:
        return
    if w["kind"] == "ask":
        answer_asks(root, "cleared by the person")
        asked = [s.strip() for s in re.split(r"(?<=[.!?])\s+", w["ask"]["text"]) if s.strip().endswith("?")]
        add_note(root, None, f"The person cleared your question from their list: \"{(asked or [w['ask']['text'][-200:]])[-1]}\" "
                 "Treat it as handled; ask again only if it still blocks you.", author='colony', quiet=True)
    else:
        append(root, "dismissed.jsonl", {"type": "dismissed", "key": key, "at": now()})
        if w['kind'] == 'checkpoint':
            add_note(root, None, 'The person hid this version review from their list. Its candidate remains unapproved; '
                     'continuation stays stopped until an explicit candidate decision.', author='colony', quiet=True)
        elif w["kind"] == "verify":
            it = w["item"]
            add_note(root, {"item": it["id"]}, f"The person cleared {it['id']} from their list of things to verify: they "
                     "consider it handled. Update the roadmap if you agree.", author='colony', quiet=True)


def moments(p, snap=None):
    """What a project waits on the person for, as moments to answer rather than items: a gate or a choice
    on screen is its own; a turn that asked something, together with the items it left to verify, is one;
    items to verify with no question beside them are one. The counts count these."""
    items = waiting_items(p, snap)
    single = [[w] for w in items if w["kind"] in ("gate", "choice", "screen", "checkpoint")]
    asks, verify = [w for w in items if w["kind"] == "ask"], [w for w in items if w["kind"] == "verify"]
    return single + ([asks + verify] if asks else [verify] if verify else [])


def waiting_on(pid, p, back, label=True):
    """The project's moments as the person answers them, where they stand: a gate's answer reaches the agent
    as a note, a choice gets a button per option, a question (with what it left to verify) gets a reply typed
    into the console, items to verify get one note about them."""
    rows = []
    hidden = (f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='back' value='{e(back)}'>")
    for group in moments(p):
        w = group[0]
        keys = ",".join(x["key"] for x in group)
        clear = (f"<form class='clear' method='post' action='/clear'>{hidden}<input type='hidden' name='key' value='{e(keys)}'>"
                 f"<button class='quiet' title='Clear it: the agent hears quietly, on its next turn'>Clear</button></form>")
        who = lambda what: f"<div class='who'>{clear}<span class='kind'>{e(p.name) + ' · ' if label else ''}{what}</span></div>"
        verify = [x["item"] for x in group if x["kind"] == "verify"]
        def ready_row(it):
            # a request for sign-off: what's ready, how to see it, and Approve or say what's wrong
            return (f"<div class='ready'><div><span class='muted'>Ready for your OK:</span> {e(it['what'])}</div>"
                    + (f"<div><span class='muted'>To check:</span> {e(it['check'])}</div>" if it.get("check") else "")
                    + f"<form class='verdict' method='post' action='/approve'>{hidden}<input type='hidden' name='item' value='{e(it['id'])}'>"
                    f"<button>Approve</button><input name='text' placeholder='or say what is wrong'>"
                    f"<button class='quiet' name='verdict' value='not-yet'>Not yet</button></form></div>")
        to_verify = "".join(ready_row(it) for it in verify)
        if w['kind'] == 'checkpoint':
            from . import lead
            c, candidate = w['checkpoint'], w['checkpoint']['candidate']
            href = candidate['artifact'] if candidate['artifact'].startswith(('http://', 'https://')) else f"/progress/artifact?p={pid}&candidate={candidate['id']}"
            next_versions = [v for v in lead.info(p)['checkpoints'] if v['state'] == 'planned']
            choose_next = ("<label>After approval <select name='next'><option value=''>Pause here</option>" +
                           ''.join(f"<option value='{e(v['id'])}'>{e(v['outcome'])}</option>" for v in next_versions) + '</select></label>')
            rows.append(f"<div class='need'>{who('completed version')}<b>{e(c['outcome'])}</b>"
                        f"<p>{e(c['definition'])}</p><p><a href='{e(href)}'>Open the completed work</a></p>"
                        f"<p class='muted'>{e(c['check'])}</p>"
                        f"<form class='verdict' method='post' action='/progress/decision'>{hidden}"
                        f"<input type='hidden' name='candidate' value='{e(candidate['id'])}'>"
                        f"{choose_next}<input name='text' placeholder='Your feedback'>"
                        "<button name='action' value='approve'>Approve version</button>"
                        "<button class='quiet' name='action' value='changes'>Request corrections</button></form></div>")
        elif w["kind"] == "gate":
            g = w["gate"]
            rows.append(f"<div class='need'>{who('gate' + (' on ' + e(g['item']) if g.get('item') else ''))}"
                        + gate_body(p, pid, g, back) + "</div>")
        elif w["kind"] == "choice":
            rows.append(f"<div class='need'>{who('asking in its console')}"
                        + "".join(f"<div>{e(q)}</div>" for q in w["question"])
                        + "<div class='choices'>" + "".join(
                            f"<form method='post' action='/choose'>{hidden}<input type='hidden' name='option' value='{e(o)}'>"
                            f"<button class='quiet'>{e(o)}</button></form>" for o in w["options"]) + "</div></div>")
        elif w["kind"] == "screen":
            rows.append(f"<div class='need'>{who('needs you in its console')}<pre>{e(chr(10).join(w['lines']))}</pre>"
                        f"<a href='/?p={pid}&view=console'>Open its console →</a></div>")
        elif w["kind"] == "ask":
            a = w["ask"]
            rows.append(f"<div class='need'>{who('asked in its console · ' + e(a['at'][:16].replace('T', ' ')))}"
                        f"<div class='asktext'>{e(a['text'])}</div>{to_verify}"
                        f"<form class='add' method='post' action='/reply'>{hidden}"
                        f"<textarea name='text' placeholder='Your reply goes straight to its console'></textarea><button>Send</button></form></div>")
        else:                                         # items ready for the person's OK, with no question beside them
            rows.append(f"<div class='need'>{who('ready for your OK' if len(verify) == 1 else f'{len(verify)} ready for your OK')}{to_verify}</div>")
    return rows


def waiting_html(pid, root):
    rows = waiting_on(pid, root, f"/?p={pid}", label=False)
    return "".join(rows) if rows else "<p class='muted'>Nothing is waiting on you.</p>"


def support_rows():
    """Supports the monitor has brought the person: its question, and Test it / Approve suggesting it / Drop,
    or talk it over in its console. The answer goes back to the monitor."""
    from . import supports
    rows = []
    for r in supports.asking():
        a = r["asking"]
        approve = "<button name='verdict' value='approve'>Approve suggesting it</button>" if supports.suggestible(r) else ""
        rows.append(f"<div class='need'><div class='who'><span class='kind'>{e(Path(a['project']).name)} · a find the monitor brought</span></div>"
                    f"<b>{e(r['name'])}</b> <span class='muted'>({e(r['status'])}{', reference' if r.get('kind') == 'reference' else ''})</span>"
                    f"<div class='asktext'>{e(a['text'])}</div><p class='muted'>{e(supports.NOTICE)}</p>"
                    f"<form class='verdict' method='post' action='/support'><input type='hidden' name='id' value='{e(r['id'])}'>"
                    f"{approve}<button name='verdict' value='test' class='{'quiet' if approve else ''}'>Test it</button>"
                    f"<input name='text' placeholder='anything to tell the monitor'>"
                    f"<button class='quiet' name='verdict' value='drop'>Drop</button></form>"
                    f"<a href='/monitor?view=console'>Talk it over with the monitor →</a></div>")
    return rows


# Keeps a list of what waits on the person current without getting in their way: it redraws only when
# something changed, never while they type or scroll in it, and keeps where they had scrolled to.
KEEP_CURRENT = """
function keepCurrent(el, url, after) {
  if (!el) return;
  let last = el.innerHTML, touched = 0;
  const busy = () => { touched = Date.now(); };
  ['scroll', 'touchstart', 'touchmove', 'wheel', 'pointerdown'].forEach((t) => el.addEventListener(t, busy, {capture: true, passive: true}));
  setInterval(async () => {
    if (el.contains(document.activeElement) || Date.now() - touched < 5000) return;
    const r = await fetch(url, {cache: 'no-store'}); if (!r.ok) return;
    const html = await r.text(); if (html === last) return;
    const kept = [...el.querySelectorAll('*')].filter((x) => x.scrollTop).map((x) => [x.className, x.textContent.slice(0, 80), x.scrollTop]);
    el.innerHTML = last = html;
    for (const [cls, text, top] of kept) {
      const x = [...el.querySelectorAll('*')].find((y) => y.className === cls && y.textContent.slice(0, 80) === text);
      if (x) x.scrollTop = top;
    }
    if (after) after(el);
  }, 4000);
}
"""


def role_field(family, role, field, mine=None):
    from . import providers as pv, selection
    value = selection.auto(family, role)
    desc = lambda v: v['model'] + (f" at {v['effort']}" if v.get('effort') else '')
    options = [("auto", 'Auto: ' + desc(value))]
    for mid, label in pv.available(pv.get(family)):
        options.extend((mid + ':' + (effort or ''), label + (f' at {effort}' if effort else ''))
                       for effort in pv.efforts_of(pv.get(family), mid) or [None])
    selected = mine['model'] + ':' + (mine.get('effort') or '') if mine else 'auto'
    return f"<label>{e(family)} {e(role)} <select name='{e(field)}'>" + ''.join(
        f"<option value='{e(v)}'{' selected' if v == selected else ''}>{e(label)}</option>" for v, label in options) + '</select></label>'


def global_role_fields():
    from . import providers as pv, bench
    settings = registry()['settings']
    helpers = settings.get('helper_models') or {}
    rows = ''.join(role_field(family, role, 'helper_' + family + ':' + role, helpers.get(family + ':' + role))
                   for family, provider in pv.PROVIDERS.items() if pv.usable(provider) for role in bench.TIERS)
    return ("<h2>Colony helper tiers</h2><form class='card options' method='post' action='/role-models'>" + rows
            + "<button>Save</button></form>")


def model_changes(pending_only=False):
    """Colony decisions are deliberately outside project questions and monitor wake-ups."""
    from . import selection
    state = selection.read()
    if state.get('recovery') or (not state['accepted'] and not pending_only):
        state = selection.reconcile()
    pending = state['pending']
    if pending_only and not pending:
        return ''
    def describe(value):
        return (value['model'] + (f" at {value['effort']}" if value.get('effort') else '')) if value else 'no previous pick'
    def cost(prices):
        task = prices.get('task') or {}
        task_note = ''
        if task:
            describe = lambda value: f'${value:.4g}' if value is not None else 'unknown'
            task_note = f"Benchmark USD/task: {describe(task.get('before'))} → {describe(task.get('after'))}"
            if task.get('before') and task.get('after') is not None:
                task_note += f" ({(task['after'] / task['before'] - 1) * 100:+.1f}%)"
            task_note += (' (includes estimates). ' if prices.get('task_estimated') else '. ')
        before, after = prices.get('before'), prices.get('after')
        if before is None or after is None:
            return task_note + 'Token-price change unknown (missing price data).'
        parts = []
        for label, old, new in zip(('input', 'output'), before, after):
            relative = f', {(new / old - 1) * 100:+.0f}%' if old else ''
            parts.append(f'{label}: ${old:g} → ${new:g} ({new-old:+g}{relative})')
        return task_note + 'USD per million tokens: ' + '; '.join(parts) + '.'
    def row(label, before, after, prices, pinned=False):
        return (f"<li><strong>{e(label)}</strong>: {e(describe(before))} → {e(describe(after))}"
                + (' <strong>Your pin stays until you choose Auto in Settings.</strong>' if pinned else '')
                + f"<br><span class='muted'>{e(cost(prices))}</span></li>")
    out = ["<section class='card' id='model-changes'><h2>Model choices</h2>"]
    if not pending_only:
        policy = registry()['settings'].get('model_adoption', 'automatic')
        out.append("<form method='post' action='/model-decision'><input type='hidden' name='action' value='policy'>"
                   "<label>When a new model would become a pick <select name='policy'>" + ''.join(
                       f"<option value='{v}'{' selected' if v == policy else ''}>{label}</option>"
                       for v, label in [('automatic', 'Switch automatically'), ('ask', 'Ask me')])
                   + "</select></label> <button>Save</button></form>")
    for model, proposal in pending.items():
        out.append(f"<h3>{e(model)}</h3>")
        if proposal.get('emergency'):
            out.append(f"<p>{e(proposal['emergency'])}. The concrete replacement below is already in use so work can continue. "
                       "Keep it, or choose another model in Settings.</p>")
        out.append('<ul>')
        for ident, change in proposal['roles'].items():
            for seat in change.get('seats') or selection.affected(ident, change['before'], change['after']):
                out.append(row(seat['label'], seat['before'], seat['after'],
                               selection.price_change(seat['before'], seat['after']), seat['pinned']))
        out.append("</ul><form method='post' action='/model-decision'>"
                   f"<input type='hidden' name='model' value='{e(model)}'>"
                   f"<input type='hidden' name='proposal' value='{e(proposal['id'])}'>"
                   f"<button name='action' value='approve'>{'Keep this model' if proposal.get('emergency') else 'Switch to this model'}</button> "
                   "<button name='action' value='reject'>Not this one</button></form>")
    if not pending_only:
        out.append('<h3>Recent choices</h3>')
        for event in reversed(state['history'][-30:]):
            out.append('<ul>' + row(selection.role_label(event['role']), event['before'], event['after'], event['prices']) + '</ul>'
                       + f"<p class='muted'>{e(event['reason'])} · {e(event['at'])}</p>")
            if selection.returnable(state, event):
                out.append("<form method='post' action='/model-decision'><input type='hidden' name='action' value='rollback'>"
                           f"<input type='hidden' name='event' value='{e(event['id'])}'>"
                           "<button>Return to previous choice</button></form>")
    out.append("<p class='muted'>Benchmark task costs and token rates are separate evidence; actual project spend varies. "
               "Changes take effect when a console reloads while idle.</p></section>")
    return ''.join(out)


def needs_you(reg):
    """Needs you: what every project is waiting on the person for, in one place, and the supports the
    monitor has brought them."""
    choices = model_changes(pending_only=True)
    rows = ([choices] if choices else []) + support_rows() + [r for pid, p in enumerate(projects(reg)) for r in waiting_on(pid, p, "/monitor")]
    return "".join(rows) if rows else "<p class='muted'>Nothing is waiting on you.</p>"


def models_page(reg):
    from . import bench, intelligence, providers
    entries = bench.standings()
    points = intelligence.pairs(entries)
    ceiling = max((p['score'] for p in points), default=None)
    families = [k for k, provider in providers.PROVIDERS.items() if providers.usable(provider)]
    from . import selection
    state = selection.read()
    eligible = [x for x in entries if x['model'] not in state['rejected']]
    rows = []
    for family in families:
        for role in selection.ROLES:
            pick = bench.role_pick(family, role, eligible, ceiling=ceiling,
                                   blocked=state['blocked'].get(selection.key(family, role), []))
            held = state['accepted'].get(selection.key(family, role))
            desc = (pick['model'] + ' at ' + str(pick['effort']) + ': ' + pick['why']) if pick else 'Missing comparable evidence; retain accepted choice.'
            accepted = (held['model'] + ' at ' + str(held['effort'])) if held else 'No accepted choice yet'
            rows.append(f"<tr><th>{e(family)} · {e(role)}</th><td>{e(desc)}<div class='muted'>Accepted: {e(accepted)}</div></td></tr>")
    cards = ''.join(model_card(bench.card(mid, entries)) for _, mid, _, _ in bench.lineup())
    title = f'{ceiling:.2f}' if ceiling is not None else 'unavailable'
    return shell(reg, -2, model_changes() + "<header><h1>Models</h1><p>Artificial Analysis Intelligence Index and cost per benchmark task. "
                 f"Shared runnable intelligence ceiling: <strong>{title}</strong>. "
                 "Scores within one point count as equal; the cheaper pair wins. Estimates are labelled and can participate. "
                 "Usage never lowers an Auto goal. <a href='/settings'>Change the Auto slider in Settings</a>.</p></header>"
                 "<h2>Auto roles</h2><div class='card'><table class='bench'>" + ''.join(rows) + "</table></div>"
                 "<h2>Intelligence against task cost</h2><div class='card'>" + effort_chart(entries) + "</div>"
                 "<h2>Model evidence</h2>" + cards)


def once(ranked):
    """Each model once, at its best entry."""
    seen, out = set(), []
    for x in ranked:
        if x["model"] not in seen:
            seen.add(x["model"])
            out.append(x)
    return out


def model_card(c):
    rows = []
    for entry in c['entries']:
        point = entry.get('pair')
        if not point:
            continue
        cost = f"${point['cost']:.4g}" if point['cost'] is not None else 'Unknown'
        kind = 'Estimated' if point['estimated'] else 'Measured'
        sources = []
        for metric, evidence in point['evidence'].items():
            if not evidence:
                continue
            if evidence.get('estimated'):
                sources.append(f"<li>{e(metric)}: {e(json.dumps(evidence['derivation'], ensure_ascii=False))}</li>")
            else:
                sources.append(f"<li>{e(metric)}: <a href='{e(evidence['url'])}' rel='noopener' target='_blank'>Artificial Analysis</a>, {e(evidence['date'])}</li>")
        rows.append(f"<tr><th>{e(entry['variant'])}</th><td>{point['score']:.2f}</td><td>{cost}</td>"
                    f"<td>{kind}<details><summary>Evidence · {e(point['version'])}</summary><ul>{''.join(sources)}</ul></details></td></tr>")
    table = ("<table class='bench'><tr><th>Effort</th><th>Intelligence Index</th><th>USD/task</th><th>Evidence</th></tr>"
             + ''.join(rows) + '</table>') if rows else '<p>No comparable Intelligence Index evidence yet; retain accepted choices.</p>'
    return f"<div class='card' id='{e(c['model'])}'><h3>{e(c['name'])}</h3>{table}</div>"


def effort_chart(entries):
    """Each model's effort levels as points of cost per task (across, doubling each step) against overall score
    (up), joined in order: where the line climbs, more effort pays; where it runs flat, it only costs more."""
    import math
    from . import bench
    pts = [dict(x, overall=x['pair']['score'], cost=dict(value=x['pair']['cost'], unit='usd', benchmark='Cost per Intelligence Index task'))
           for x in entries if x.get('pair') and x['pair']['cost'] is not None and x['pair']['cost'] > 0]
    if not pts:
        return "<p class='muted'>No model has both a comparable Intelligence Index and cost per task yet.</p>"
    W, H, L, B = 640, 300, 44, 34
    lo, hi = math.log2(min(x["cost"]["value"] for x in pts)), math.log2(max(x["cost"]["value"] for x in pts))
    X = lambda v: L + (W - L - 12) * ((math.log2(v) - lo) / ((hi - lo) or 1))
    Y = lambda s: H - B - (H - B - 12) * s / 100
    colours = ["var(--accent)", "var(--flag)", "#6b7fd7", "#c46fa0", "#4aa3a8", "#a88b3c", "#7a7a7a", "#b3261e", "#3b8f3b"]
    out, legend = [], []
    for i, mid in enumerate(dict.fromkeys(x["model"] for x in pts)):
        mine = sorted((x for x in pts if x["model"] == mid), key=lambda x: x["cost"]["value"])
        col = colours[i % len(colours)]
        out.append(f"<polyline fill='none' stroke='{col}' stroke-width='2' points='"
                   + " ".join(f"{X(x['cost']['value']):.0f},{Y(x['overall']):.0f}" for x in mine) + "'/>")
        out += [f"<circle cx='{X(x['cost']['value']):.0f}' cy='{Y(x['overall']):.0f}' r='4' fill='{col}'><title>"
                f"{e(bench.entry_name(x))}: Intelligence Index {x['overall']}, ${x['cost']['value']:.2f} {bench.cost_label(x['cost'])}</title></circle>"
                + f"<text x='{X(x['cost']['value']):.0f}' y='{Y(x['overall']) - 8:.0f}' text-anchor='middle'>{e(x['variant'])}</text>"
                for x in mine]
        legend.append(f"<span><i style='background:{col}'></i>{e(bench.name(mid))}</span>")
    ticks = "".join(f"<text x='{L - 6}' y='{Y(s) + 4:.0f}' text-anchor='end'>{s}</text>" for s in (0, 50, 100))
    step = max(1, round((hi - lo) / 5)) if hi > lo else 1
    xt = "".join(f"<text x='{X(2 ** k):.0f}' y='{H - 12}' text-anchor='middle'>${2 ** k:g}</text>"
                 for k in range(math.floor(lo), math.ceil(hi) + 1, step) if lo <= k <= hi)
    return (f"<div class='mapwrap'><svg class='chart' viewBox='0 0 {W} {H}' width='{W}' height='{H}'>{ticks}{xt}"
            f"<text x='{L + 8}' y='12' class='axis'>Intelligence Index (0–100)</text><text x='{W - 12}' y='{H - 2}' text-anchor='end' class='axis'>"
            f"{bench.cost_label(pts[0]['cost'])}, doubling each step</text>{''.join(out)}</svg></div><div class='legend chartkey'>{''.join(legend)}</div>"
            + ("<p class='muted'>A model's effort levels share its price per token, so they stack at one price: "
               "higher effort spends more tokens a task, which this data doesn't give.</p>" if bench.pricing(pts[0]["cost"]) else ""))


def per(benchmark):
    from . import bench
    return bench.per(benchmark)


def signin_result(why=False):
    """The last token check's failure, kept for the Settings page to show; why=None clears it, a string sets it."""
    path = home() / "claude-token-check"
    if why is False:
        try:
            return path.read_text().strip() or None
        except OSError:
            return None
    if why:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(why)
    else:
        path.unlink(missing_ok=True)


def signed_out():
    """The programs found signed out by the daily check, and since when."""
    try:
        return json.loads((home() / "signed-out.json").read_text())
    except (OSError, ValueError):
        return {}


def remote_line(root):
    """Whether the project's console is reachable from the person's app, and the one step that connects it."""
    if providers.of(root) is providers.get('codex'):
        from . import codex_remote
        from .codex_rpc import Client, RPCError
        home = codex_remote.home_for(root)
        mode = codex_remote.status(root).get('mode') or 'off'
        if codex_remote.alive(home):
            try:
                with Client(codex_remote.socket_for(home), timeout=1) as client:
                    mode = client.call('remoteControl/status/read')['status']
            except (OSError, RPCError, KeyError):
                mode = 'unreachable'
        link = '' if mode == 'connected' else f" (its connection: {e(mode)})"
        if codex_remote.pairing_choice(root) == 'paired':
            return f"<p class='muted'>In ChatGPT: paired{link}</p>"
        if codex_remote.alive(home):
            return (f"<p class='muted'>In ChatGPT: not paired yet{link} · <form method='post' action='/codex-pair' "
                    f"style='display:inline'><input type='hidden' name='project' value='{e(str(root))}'>"
                    "<button>Connect to ChatGPT</button></form></p>")
        return "<p class='muted'>In ChatGPT: open its console to connect</p>"
    on = project_settings(root)[0].get('remote', True)
    return f"<p class='muted'>Remote Control: {'on' if on else 'off'}</p>"


def codex_pairing_options(reg):
    from . import codex_remote
    roots = [root for root in projects(reg) if root.exists() and providers.of(root) is providers.get('codex')]
    if not roots:
        return ''
    rows = []
    for root in roots:
        ready = codex_remote.alive(codex_remote.home_for(root))
        offered = codex_remote.pairing_choice(root) == 'offered'
        rows.append(f"<li><b>{e(root.name)}</b> "
                    + ("<p>Would you like to pair this project with ChatGPT now? Open the app first; the code expires quickly.</p>" if offered else '')
                    + (f"<form method='post' action='/codex-pair'><input type='hidden' name='project' value='{e(str(root))}'>"
                       + ("<button>Yes, pair now</button> <button formaction='/codex-pair/defer' class='quiet'>Not now</button>" if offered else
                          "<button>Pair with ChatGPT</button>") + "</form>" if ready else
                       "<span class='muted'>Open its console with Remote Control on to pair it.</span>") + '</li>')
    return ("<h3 id='codex-pairing'>Codex in ChatGPT</h3><p>Pair each project once with your ChatGPT app. "
            "Sign in to the same account as Codex. When you are ready, get a fresh code and open Codex → Add manually in the app. "
            "You can pair another device here later.</p><ul class='folders'>" + ''.join(rows) + '</ul>')


def codex_pairing_page(reg, root, result):
    from datetime import datetime, timezone
    expiry = datetime.fromtimestamp(result['expiresAt'], timezone.utc).strftime('%H:%M:%S UTC')
    body = (f"<header><h1>Pair {e(root.name)} with ChatGPT</h1></header><div class='card'>"
            "<ol class='steps'><li>In the ChatGPT app, sign in to the same account as Codex.</li>"
            "<li>Open <b>Codex</b>, then choose <b>Add manually</b>.</li>"
            "<li>Enter this code before it expires:</li></ol>"
            f"<p><strong id='pair-code' style='font-size:2em;letter-spacing:.12em'>{e(result['manualPairingCode'])}</strong></p>"
            "<p id='pair-state' role='status'>Enter this code in ChatGPT to pair.</p>"
            f"<p id='pair-expiry' data-expiry='{result['expiresAt']}'>Expires at {expiry}.</p>"
            f"<form id='pair-again' method='post' action='/codex-pair'><input id='pair-project' type='hidden' name='project' value='{e(str(root))}'>"
            "<button>Get a fresh code</button></form>"
            "<p>Once paired, open this project’s named conversation in the app’s Codex section.</p>"
            "<p><a href='/settings'>Back to Settings</a></p></div>" + r"""
<script>(() => {
  const code = document.getElementById('pair-code'), state = document.getElementById('pair-state');
  const expiry = document.getElementById('pair-expiry'), project = document.getElementById('pair-project').value;
  const value = code.textContent, deadline = Number(expiry.dataset.expiry) * 1000;
  let finished = false;
  function finish(message) {
    finished = true; code.textContent = ''; expiry.hidden = true; state.textContent = message;
  }
  function tick() {
    if (finished) return;
    const left = Math.ceil((deadline - Date.now()) / 1000);
    if (left <= 0) { finish('This code has expired. Get a fresh code to pair.'); return; }
    expiry.textContent = 'Expires in ' + Math.floor(left / 60) + ':' + String(left % 60).padStart(2, '0') + '.';
    setTimeout(tick, 1000);
  }
  async function check() {
    if (finished) return;
    try {
      const response = await fetch('/codex-pair/status', {method: 'POST',
        body: new URLSearchParams({project, code: value})});
      if (!response.ok) throw new Error();
      const result = await response.json();
      if (finished) return;
      if (result.claimed) {
        finish('Paired with ChatGPT. Open this project’s conversation in the app’s Codex section.');
        document.getElementById('pair-again').hidden = true;
        return;
      }
      state.textContent = 'Waiting for you to enter the code in ChatGPT.';
    } catch (_) {
      if (!finished) state.textContent = 'Could not check pairing yet. Retrying; you can still enter this code before it expires.';
    }
    if (!finished) setTimeout(check, 5000);
  }
  state.textContent = 'Waiting for you to enter the code in ChatGPT.';
  tick(); setTimeout(check, 5000);
})();</script>""")
    return shell(reg, -2, body)


def settings_page(reg):
    from . import selection
    selection.migrate()
    reg = registry()
    rows = []
    for r in reg["roots"]:
        default = r == reg["new_root"]
        # a folder row: its path on its own line, then what it is and what can be done with it
        rows.append(f"<li><code class='path'>{e(r)}</code><div class='folderacts'>{'<b>New projects go here</b>' if default else ''}"
                    + ("" if default else f"<form method='post' action='/roots'><input type='hidden' name='default' value='{e(r)}'><button class='quiet'>Make default</button></form>")
                    + f"<form method='post' action='/roots'><input type='hidden' name='remove' value='{e(r)}'><button class='quiet'>Remove</button></form></div></li>")
    single = "".join(f"<li><code class='path'>{e(p)}</code><div class='folderacts'><form method='post' action='/roots'><input type='hidden' name='untrack' value='{e(p)}'>"
                     f"<button class='quiet'>Remove from board</button></form></div></li>" for p in reg["projects"])
    s = reg["settings"]
    check = lambda k: " checked" if s[k] else ""
    from . import providers as pv
    def program(k, p):
        using = [x.name for x in projects(reg) if x.exists() and pv.of(x) is p]
        state = ("installed" if pv.installed(p) else f"not installed: <a href='{e(p.site)}' target='_blank' rel='noopener'>get it</a>")
        also = (f"; off, but {len(using)} project{'s' * (len(using) != 1)} still run on it ({e(', '.join(using))})"
                if using and not pv.enabled(p) else f"; {len(using)} project{'s' * (len(using) != 1)}" if using else "")
        from . import usage
        used = usage.line(k) if pv.installed(p) else ""
        used = f"Usage: {used}" if used else ""
        out = signed_out().get(k)
        used = (f"signed out since {out}: sign in again in a terminal" + (f" · {used}" if used else "")) if out else used
        from . import catalog_freshness
        freshness = catalog_freshness.info(p)
        fresh = " · ".join(f"{e(label)}: {e(value)}" for label, value in freshness['fields'])
        fresh += (". <strong>" + e('; '.join(freshness['warnings'])) + "</strong>" if freshness['warnings'] else "")
        fresh = "<p class='muted' style='margin:0 0 8px 26px'>Model list: " + fresh.lstrip('. ') + ". Re-read at the program's next session start.</p>"
        remote_info = ''
        if k == 'codex':                         # PROVIDER: each Codex project owns its app-server process
            from . import codex_remote
            hosts = codex_remote.statuses()
            count = sum(h['running'] for h in hosts)
            remote_info = f"<p class='muted' style='margin:0 0 8px 26px'>{count} project daemon{'s' if count != 1 else ''} running"
            if hosts:
                remote_info += ': ' + '; '.join(e(Path(h.get('project', h['home'])).name) + ' — '
                                               + e(h.get('mode', 'not started'))
                                               + (': ' + e(h['reason']) if h.get('reason') else '') for h in hosts)
            remote_info += '. ChatGPT shows the machine name as host; each conversation carries its project name.</p>'
        return (f"<label><input type='checkbox' name='on' value='{k}'{' checked' if pv.enabled(p) else ''}> {e(p.label)} "
                f"<span class='muted'>({state}{also})</span></label>"
                + (f"<p class='muted' style='margin:0 0 8px 26px'>{e(used)}</p>" if used else "") + fresh + remote_info)
    programs = (f"<form method='post' action='/providers' class='options'>"
                + "".join(program(k, p) for k, p in pv.PROVIDERS.items())
                + "<p class='muted'>Colony offers the ones ticked for new projects and its default. Projects already on one "
                  "you untick keep running. At least one stays on.</p><button>Save</button></form>")
    options = (f"<form method='post' action='/options' class='options'>"
               f"<label><input type='checkbox' name='remote' value='on'{check('remote')}> Remote Control for new consoles "
               f"<span class='muted'>(reach them from Claude or ChatGPT)</span></label>"
               f"<label><input type='checkbox' name='monitor' value='on'{check('monitor')}> Run the monitor with the board</label>"
               f"<label><input type='checkbox' name='lan' value='on'{check('lan')}> Open from other devices on your network "
               f"<span class='muted'>(after colony restart)</span></label>"
               f"<label><input type='checkbox' name='messaging' value='on'{check('messaging')}> Projects can message each other "
               f"<span class='muted'>(one inbox per project)</span></label>"
               f"<label><input type='checkbox' name='auto_update' value='on'{check('auto_update')}> Keep Claude Code and Codex updated "
               f"<span class='muted'>(daily; a console moves to the new version once it sits idle, in the same conversation)</span></label>"
               f"<label><input type='checkbox' name='trust' value='on'{check('trust')}> Answer a new console's start-up questions "
               f"<span class='muted'>(folder trust, permission mode, Remote Control, hooks: so it runs as set up)</span></label>"
               f"<label>Permissions for new sessions <select name='permissions'>"
               + "".join(f"<option value='{k}'{' selected' if s['permissions'] == k else ''}>{label}</option>" for k, label in
                         [("ask", "ask each time"), ("edits", "accept edits"), ("all", "allow everything"), ("plan", "plan only")])
               + "</select></label>"
               + provider_fields(s["provider"], s["model"], s["effort"], "the provider's default") +
               f"<label>Safe pause at <input name='safe_pause' value='{e(str(s['safe_pause'] or 'off'))}' size='4'> % "
               f"of a program's 5-hour or weekly limit <span class='muted'>(its projects land what's in flight, save their "
               f"work and tell you where things stand, instead of being cut off mid-task; colony wakes them at the reset)</span></label>"
               + auto_balance_fields() + f"<label>New projects go in <input name='new_root' value='{e(reg['new_root'])}'></label>"
               f"<button>Save</button><p class='muted'>Provider, model, effort and Remote Control apply to new projects' sessions and to consoles started from now on.</p></form>")
    port = getattr(settings_page, "port", 8790)
    where = "".join(f"<li><code>{e(u)}</code></li>" for u in urls(port))
    from . import bench
    st = bench.status()
    steps = "".join(f"<li>{e(t)}" + (f" <a href='{e(url)}' target='_blank' rel='noopener'>{e(url)}</a>" if url else "") + "</li>"
                    for t, url in bench.AA_STEPS)
    keybox = (f"<p>{'Connected' if st['connected'] else 'Not connected'}"
              + (f"; data last fetched {e(st['at'][:16].replace('T', ' '))} UTC ({st.get('models', 0)} models)" if st.get("at") else "")
              + (f". <span class='muted'>{e(st['error'])}</span>" if st.get("error") else "") + "</p>"
              f"<ol class='steps'>{steps}</ol>"
              f"<form method='post' action='/aa-key' class='options'><label>API key <input type='password' name='key' "
              f"autocomplete='off' placeholder='{'Paste a new key to replace the one connected' if st['connected'] else 'Paste your key'}'></label>"
              f"<div class='dangers'><button name='do' value='save'>Save and check</button>"
              + ("<button name='do' value='fetch' class='quiet'>Fetch now</button><button name='do' value='remove' class='quiet'>Remove key</button>"
                 if st["connected"] else "") + "</div></form>"
              "<p class='muted'>The Models page's scores come from Artificial Analysis "
              "(<a href='https://artificialanalysis.ai/' target='_blank' rel='noopener'>artificialanalysis.ai</a>): free for "
              "personal use, with attribution, up to 1,000 requests a day.</p>")
    from . import consult
    fams = [k for k, p in pv.PROVIDERS.items() if pv.usable(p) and hasattr(p, "consult")][:2]

    def seat(k):
        p, mine = pv.get(k), (s["consultants"] or {}).get(k)
        am, ae, why = consult.auto(k)
        opts = [f"<option value='auto'{'' if mine else ' selected'}>Auto: {e(am)} at {e(ae)}</option>"]
        for mid, label in pv.available(p):
            opts.append(f"<optgroup label='{e(label)}'>" + "".join(
                f"<option value='{e(mid)}:{e(x)}'{' selected' if mine and (mine['model'], mine['effort']) == (mid, x) else ''}>"
                f"{e(label)} at {e(x)}</option>" for x in pv.efforts_of(p, mid)) + "</optgroup>")
        return (f"<label>{e(p.label)} consultant <select name='consultant_{k}'>{''.join(opts)}</select></label>"
                f"<p class='muted'>Auto picks {e(why)}.</p>")
    consulting = (f"<form method='post' action='/consulting' class='options'>"
                  f"<label><input type='checkbox' name='consult' value='on'{check('consult')}> Agents consult two fresh models, "
                  f"one from each family, at decisions costly to change <span class='muted'>(a new milestone or spec, a "
                  f"foundation others build on, a major redesign)</span></label>"
                  + ("".join(seat(k) for k in fams) or "<p class='muted'>No program that can consult is on.</p>")
                  + ("<p class='muted'>Only one model family is on, so one consultant.</p>" if len(fams) == 1 else "")
                  + "<button>Save</button></form>")
    from . import monitor as mon
    mp = mon.provider()
    mm, me, mwhy = mon.choice()
    mine = s.get("monitor_model") or {}
    opts = [f"<option value='auto'{'' if mine else ' selected'}>Auto: {e(mm or 'its default') if not mine else 'step-up tier'}"
            + (f" at {e(me)}" if me and not mine else "") + "</option>"]
    for mid, label in pv.available(mp):
        opts.append(f"<optgroup label='{e(label)}'>" + "".join(
            f"<option value='{e(mid)}:{e(x)}'{' selected' if mine and (mine.get('model'), mine.get('effort') or '') == (mid, x) else ''}>"
            f"{e(label)} at {e(x)}</option>" for x in pv.efforts_of(mp, mid)) + "</optgroup>")
    monitor_card = (f"<form method='post' action='/monitor-model' class='options'><label>Model <select name='monitor_model'>{''.join(opts)}</select></label>"
                    f"<p class='muted'>Now: {e(mm or 'its program default')}" + (f" at {e(me)}" if me else "") + f" ({e(mwhy)}). "
                    f"Its conversation is kept small so each wake-up stays cheap: capped at {mon.CONTEXT_CAP} tokens, and about once a "
                    f"day it writes down what to carry on from and starts fresh, when it's idle and no one is at it.</p>"
                    f"<button>Save</button></form>")
    claude = pv.get("claude")
    signin = ""
    if pv.installed(claude) and pv.enabled(claude):
        tok, failed = claude.token(), signin_result()
        steps_ = "".join(f"<li>{e(t)}</li>" for t, _ in claude.TOKEN_STEPS)
        signin = (f"<h2>Claude Code sign-in</h2><div class='card'><p>{'Long-lived sign-in connected: Claude Code consoles use it, so they are not signed out every week or so.' if tok else 'Using the regular sign-in, which expires every week or so. A long-lived one keeps every console signed in.'}</p>"
                  + (f"<p class='muted'>That token didn't work: {e(failed)}</p>" if failed else "")
                  + f"<ol class='steps'>{steps_}</ol><form method='post' action='/claude-token' class='options'>"
                  f"<label>Token <input type='password' name='token' autocomplete='off' placeholder='{'Paste a new token to replace it' if tok else 'Paste the token'}'></label>"
                  f"<div class='dangers'><button name='do' value='save'>Save and check</button>"
                  + ("<button name='do' value='remove' class='quiet'>Remove</button>" if tok else "") + "</div></form></div>")
    body = (f"<header><h1>Settings</h1></header><h2>Agent programs</h2><div class='card'>{programs}{codex_pairing_options(reg)}</div>{signin}"
            f"{model_changes()}{global_role_fields()}<h2>Benchmark data</h2><div class='card'>{keybox}</div><h2>Consulting</h2><div class='card'>{consulting}</div><h2>Monitor</h2><div class='card'>{monitor_card}</div><h2>Open this board</h2><div class='card'><ul class='folders'>{where}</ul>"
            f"<p class='muted'>Each project, and the monitor, is also in the Claude app when Remote Control is on.</p></div><h2>Options</h2><div class='card'>{options}</div><h2>Project folders</h2><div class='card'>"
            f"<p class='muted'>Every subfolder of these is a project on the board.</p><ul class='folders'>{''.join(rows) or '<li class=muted>none</li>'}</ul>"
            f"<p><a href='/add?for=root'>+ Add a folder of projects</a></p></div>"
            f"<h2>Projects added one by one</h2><div class='card'><ul class='folders'>{single or '<li class=muted>none</li>'}</ul>{('<h3>Hidden from the board</h3><ul class=folders>' + ''.join(f"<li><code class='path'>{e(h)}</code><div class='folderacts'><form method='post' action='/roots'><input type='hidden' name='show' value='{e(h)}'><button class='quiet'>Show again</button></form></div></li>" for h in reg['hidden']) + '</ul>') if reg['hidden'] else ''}"
            f"<p><a href='/add'>+ Add a project folder</a></p></div>"
            f"<p class='muted'>Removing leaves every file where it is; the project just leaves the board.</p>")
    return shell(reg, -2, body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _from_this_page(self):
        """Same-origin: a request posted from another website carries that site's Origin and is refused.
        Localhost-only unless the board was started with --lan."""
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        if not getattr(self.server, "lan", False) and host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{host}"

    def _console(self, root, token, name=None, label=None):
        """Upgrade to a WebSocket bridged to the project's session. A browser always sends Origin on a
        WebSocket; it must be this page's, and the token must be the one this board made."""
        origin_ok = self._from_this_page() and self.headers.get("Origin") is not None
        if not origin_ok or not secrets.compare_digest(token, console.token()) or \
                self.headers.get("Upgrade", "").lower() != "websocket":
            return self._send(403, b"not from this page")
        # RFC 6455 requires HTTP/1.1 here: Chrome forgives 1.0, but Safari and every iPhone browser refuse it.
        self.protocol_version = "HTTP/1.1"
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", console.accept(self.headers["Sec-WebSocket-Key"]))
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        console.bridge(self.connection, root, name, label)

    def _multipart(self):
        """A multipart form, the whole body read (up to the upload limit): its fields, and its file if any."""
        import email.parser, email.policy
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
            b"Content-Type: " + self.headers.get("Content-Type", "").encode() + b"\r\n\r\n" + raw)
        fields, upload = {}, None
        for part in msg.iter_parts() if msg.is_multipart() else []:
            name = part.get_param("name", header="content-disposition")
            if part.get_filename():
                upload = (part.get_filename(), part.get_payload(decode=True) or b"")
            elif name:
                fields[name] = (part.get_payload(decode=True) or b"").decode("utf-8", "replace").replace("\r\n", "\n")
        return fields, upload

    def _upload(self):
        """An uploaded file to pin."""
        fields, upload = self._multipart()
        pid = int(fields.get("p", "0"))
        root = projects(registry())[pid]
        if upload and upload[1]:
            rel = pins.save_upload(root, *upload)
            pin = pins.add(root, rel, fields.get("title", ""), kind="upload")
            tell_pinned(root, pin, fields.get("comment", "").strip())
        self.send_response(303)
        self.send_header("Location", f"/?p={pid}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._from_this_page():
            return self._send(403, b"not from this page")
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        reg = registry()
        plist = projects(reg)
        pid = min(max(int((q.get("p") or ["0"])[0]), 0), max(len(plist) - 1, 0))
        if url.path == '/progress/artifact' and plist:
            from . import progress
            c = progress.current(plist[pid])
            candidate = c.get('candidate') if c else None
            if not candidate or candidate['id'] != (q.get('candidate') or [''])[0]:
                return self._send(409, b'This completed candidate is no longer current.')
            if candidate['artifact_hash'] is None or candidate['artifact_hash'] != progress.artifact_identity(candidate['artifact']):
                return self._send(409, b'The completed artifact changed; present a fresh candidate.')
            import mimetypes
            artifact = Path(candidate['artifact'])
            body = artifact.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(artifact.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Security-Policy', 'sandbox')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return self.wfile.write(body)
        if url.path == "/" and "p" not in q and reg["settings"]["monitor"]:
            self.send_response(303)                    # the monitor is the front page; projects are a click away
            self.send_header("Location", "/monitor")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if url.path == "/":
            view = (q.get("view") or ["overview"])[0]           # switching to a project starts on its Overview
            return self._send(200, render(reg, pid, view if view in ("overview", "roadmap", "console") else "overview").encode())
        if url.path == "/add":
            if (q.get("for") or ["project"])[0] == "project":
                return self._send(200, add_project_page(reg, (q.get("tab") or ["new"])[0], (q.get("error") or [""])[0]).encode())
            return self._send(200, folder_browser(reg, (q.get("dir") or [""])[0], (q.get("for") or ["project"])[0]).encode())
        if url.path == "/add/browse":
            return self._send(200, machine_folders((q.get("dir") or [""])[0]).encode())
        if url.path == "/models":
            return self._send(200, models_page(reg).encode())
        if url.path == "/settings":
            settings_page.port = self.server.server_address[1]
            return self._send(200, settings_page(reg).encode())
        if url.path == "/pins/add":
            return self._send(200, pin_add_page(reg, pid).encode())
        if url.path == "/pins/browse":
            return self._send(200, pin_browse(plist[pid], (q.get("dir") or [""])[0]).encode())
        if url.path == "/pin/open":
            pin = pins.get(plist[pid], (q.get("id") or [""])[0])
            if not pin:
                return self._send(404, b"no such pin")
            if pin["kind"] == "url":
                target = pin["target"]
                host = self.headers.get("Host", "").split(":")[0]
                # a link to this machine's localhost works from a phone at the machine's own address
                parts = urllib.parse.urlsplit(target)
                if parts.hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1") and host not in ("", "localhost", "127.0.0.1"):
                    netloc = host + (f":{parts.port}" if parts.port else "")
                    target = urllib.parse.urlunsplit(parts._replace(netloc=netloc))
                self.send_response(303)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if pins.is_text(pin):
                return self._send(200, pin_editor(reg, pid, pin).encode())
            path = pins.inside(plist[pid], pin["target"])
            if not path or not path.is_file():
                return self._send(404, b"the file is gone")
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", pins.mime(pin))
            self.send_header("Content-Disposition", f"inline; filename=\"{path.name}\"")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if url.path == "/console/text":
            # The session's history as plain text, for scrolling and copying where the terminal can't.
            from . import monitor
            pid = int((q.get("p") or ["0"])[0])
            root, name = (monitor.home(), monitor.name()) if pid == -1 else (plist[pid], console.session_name(plist[pid]))
            text = providers.of(root).history_text(root) or console.history(name).rstrip() or "(nothing yet)"
            body = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(body)
        if url.path == "/needs":
            if "p" in q:                                   # one project's Waiting on you
                return self._send(200, waiting_html(pid, plist[pid]).encode())
            return self._send(200, needs_you(reg).encode())
        if url.path == "/monitor":
            return self._send(200, monitor_page(reg, (q.get("view") or ["overview"])[0]).encode())
        if url.path == "/status":
            from . import monitor
            body = json.dumps({"board": str(home()), "projects": [
                dict(console.snapshot(p), waiting=len(moments(p))) if p.exists() else {"state": "off", "lines": [], "waiting": 0}
                for p in plist], "monitor": monitor.snapshot()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(body)
        if url.path == "/console/ws" and (q.get("p") or [""])[0] == "-1":
            from . import monitor
            return self._console(monitor.home(), (q.get("t") or [""])[0], name=monitor.name(), label="monitor")
        if url.path == "/console/ws" and plist:
            return self._console(plist[pid], (q.get("t") or [""])[0])
        if url.path == "/item" and plist:
            return self._send(200, render_item(reg, pid, (q.get("id") or [""])[0]).encode())
        self._send(404, b"not here")

    def do_POST(self):
        if not self._from_this_page():
            return self._send(403, b"not from this page")
        if int(self.headers.get("Content-Length") or 0) > pins.UPLOAD_LIMIT + 64 * 1024:
            return self._send(413, b"that file is too large (25 MB at most)")
        if urllib.parse.urlparse(self.path).path == "/pin/upload":
            return self._upload()
        body_text = ""
        if self.headers.get("Content-Type", "").startswith("multipart/form-data"):
            form, upload = self._multipart()       # Message, with a file or a long paste
        else:
            upload = None
            length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
            body_text = self.rfile.read(length).decode("utf-8", "replace")
            values = urllib.parse.parse_qs(body_text, keep_blank_values=True)
            if urllib.parse.urlparse(self.path).path == "/answer" and any(
                    len(v) != 1 for k, v in values.items() if k.startswith("point_") or k in ("gate", "p")):
                return self._send(400, b"Give each consultant point exactly one choice in one project gate.")
            form = {k: v[0] for k, v in values.items() if v[0]}
        if 'model_pick' in form:
            value = form['model_pick']
            form['model'], _, form['effort'] = value.rpartition(':') if value != 'auto' else ('', '', '')
        reg = registry()
        path = urllib.parse.urlparse(self.path).path
        if path in ('/codex-pair', '/codex-pair/status', '/codex-pair/defer'):
            from . import codex_remote
            root = next((r for r in projects(reg) if str(r) == form.get('project')), None)
            if root is None or not root.exists() or providers.of(root) is not providers.get('codex'):
                return self._send(404, b'Codex project not found on this board')
            if path.endswith('/defer'):
                codex_remote.pairing_choice(root, 'deferred')
                self.send_response(303)
                self.send_header('Location', '/settings#codex-pairing')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            try:
                result = codex_remote.pair(root, form.get('code', '') if path.endswith('/status') else None)
            except codex_remote.RemoteError as err:
                return self._send(409, shell(reg, -2, f"<h1>Pair with ChatGPT</h1><p>{e(str(err))}</p>"
                                            "<p><a href='/settings'>Back to Settings</a></p>").encode())
            if path.endswith('/status'):
                if result.get('claimed'):
                    codex_remote.pairing_choice(root, 'paired')
                body = json.dumps(result).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return self.wfile.write(body)
            codex_remote.pairing_choice(root, 'accepted')
            return self._send(200, codex_pairing_page(reg, root, result).encode())
        if path == '/model-decision':
            from . import selection
            try:
                if form.get('action') == 'policy':
                    set_setting('model_adoption', form.get('policy', 'automatic'))
                    selection.reconcile()
                elif form.get('action') == 'rollback':
                    selection.rollback(form.get('event', ''))
                else:
                    selection.decide(form.get('model', ''), form.get('proposal', ''), form.get('action', ''))
                for root in projects():
                    if root.exists():
                        selection.bench.write_helpers(root)
            except ValueError as err:
                return self._send(409, str(err).encode())
            self.send_response(303)
            self.send_header('Location', '/models')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if path == "/aa-key":
            from . import bench
            if form.get("do") == "remove":
                bench.set_key("")
            elif form.get("do") == "save" and form.get("key", "").strip():
                bench.set_key(form["key"])
            if form.get("do") in ("save", "fetch") and bench.aa_key():
                bench.refresh()                           # check the key by using it: fetch, keep, say what came
                from . import selection
                selection.reconcile()
            self.send_response(303)
            self.send_header("Location", "/settings")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == '/role-models':
            from . import bench, providers as pv
            chosen = dict(reg['settings'].get('helper_models') or {})
            for family in pv.PROVIDERS:
                for role in bench.TIERS:
                    ident = family + ':' + role
                    value = form.get('helper_' + ident)
                    if value == 'auto':
                        chosen.pop(ident, None)
                    elif value:
                        model, _, effort = value.rpartition(':')
                        chosen[ident] = dict(model=model, effort=effort or None)
            reg['settings']['helper_models'] = chosen
            save_registry(reg)
            for root in projects():
                bench.write_helpers(root)
            self.send_response(303)
            self.send_header('Location', '/settings')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if path == "/monitor-model":
            set_setting("monitor_model", form.get("monitor_model", "auto"))
            self.send_response(303)
            self.send_header("Location", "/settings")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/claude-token":
            from . import providers as pv
            claude = pv.get("claude")
            if form.get("do") == "remove":
                claude.set_token("")
                signin_result(None)
            elif form.get("token", "").strip():
                ok, why = claude.check_token(form["token"])
                if ok:
                    claude.set_token(form["token"])
                signin_result(why if not ok else None)
            self.send_response(303)
            self.send_header("Location", "/settings")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/providers":
            on = urllib.parse.parse_qs(body_text).get("on", []) if body_text else []
            try:
                set_setting("providers", ",".join(on))
            except KeyError:
                pass                                      # none ticked: at least one stays on, so nothing changes
            self.send_response(303)
            self.send_header("Location", "/settings")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/consulting":
            set_setting("consult", form.get("consult", "off"))
            from .providers import PROVIDERS
            mine = dict(registry()["settings"]["consultants"] or {})
            for k in PROVIDERS:
                v = form.get(f"consultant_{k}")
                if v == "auto":
                    mine.pop(k, None)
                elif v and ":" in v:
                    m, _, x = v.rpartition(":")
                    mine[k] = {"model": m, "effort": x}
            set_setting("consultants", ",".join(f"{k}={c['model']}:{c['effort']}" for k, c in mine.items()))
            self.send_response(303)
            self.send_header("Location", "/settings")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/options":
            if 'auto_balance' in form:
                try:
                    set_setting('auto_balance', form['auto_balance'])
                except KeyError:
                    return self._send(400, b'Auto balance must be from 0 to 6')
            set_setting("remote", form.get("remote", "off"))
            set_setting("monitor", form.get("monitor", "off"))
            set_setting("lan", form.get("lan", "off"))
            set_setting("messaging", form.get("messaging", "off"))
            set_setting("trust", form.get("trust", "off"))
            set_setting("auto_update", form.get("auto_update", "off"))
            if form.get("permissions"):
                set_setting("permissions", form["permissions"])
            if form.get("provider"):
                set_setting("provider", form["provider"])
            set_setting("model", form.get("model", ""))
            set_setting("effort", form.get("effort", ""))
            if form.get("safe_pause", "").strip():
                try:
                    set_setting("safe_pause", form["safe_pause"])
                except KeyError:
                    pass                                  # not a percentage: the old one stays
            if form.get("new_root", "").strip():
                set_setting("new-folder", form["new_root"].strip())
            self.send_response(303)
            self.send_header("Location", "/settings")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/message":
            text = form.get("text", "").strip()
            if form.get("p") == "monitor":
                from . import monitor
                dst = projects(reg)[int(form.get("to", "0"))]
                if text and not console.type_into(monitor.ensure(), f"Tell {dst.name} this for me, in full, with colony tell: {text}"):
                    return self._send(409, held("the monitor", "/monitor").encode())
                where = "/monitor?view=console"
            else:
                from . import mail
                src = projects(reg)[int(form.get("p", "0"))]
                dst = src if form.get("to", "self") == "self" else projects(reg)[int(form["to"])]
                files = [str(f) for f in [pins.inside(src, form["path"]) if form.get("path") else None] if f and f.is_file()]
                if upload and upload[1]:                   # kept where the project it's for can read it
                    files.append(str(dst / pins.save_upload(dst, *upload)))
                if files:
                    text = "\n".join(f"Attached: {f}" for f in files) + (f"\n\n{text}" if text else "")
                sent = not text or (console.paste_into(console.ensure(src), text) if dst == src
                                    else console.type_into(console.ensure(src), mail.instruction(mail.address(dst), text)))
                if not sent:
                    return self._send(409, held(src.name, f"/?p={form.get('p', '0')}").encode())
                if text and dst == src:
                    answer_asks(src, "in the console")
                if text:                                   # the project's Messages keep what was sent
                    append(src, "messages.jsonl", {"type": "message", "at": now(), "text": text,
                                                   "to": "console" if dst == src else dst.name})
                where = f"/?p={form.get('p', '0')}&view=console"
            self.send_response(303)
            self.send_header("Location", where)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/project-settings":
            root = projects(reg)[int(form.get("p", "0"))]
            try:
                project_settings(root, {k: form.get(k, "") for k in PROJECT_KEYS})
            except KeyError:
                return self._send(400, b'Invalid project setting')
            from . import bench
            own = bench.plan(root)
            for t in bench.TIERS:
                v = form.get(f"tier_{t}")
                if v == "auto" and t in own:
                    bench.set_plan(root, t, None, None)
                elif v and v != "auto":
                    m, _, x = v.rpartition(":")
                    if (own.get(t, {}).get("model"), own.get(t, {}).get("effort") or "") != (m, x):
                        bench.set_plan(root, t, m, x or None)
            bench.write_helpers(root)
            self.send_response(303)
            self.send_header("Location", f"/?p={form.get('p', '0')}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/clone":
            chosen = {k: form.get(k, "") for k in PROJECT_KEYS if form.get(k)}
            try:
                dest = clone(form.get("url", ""), form.get("within") or reg["new_root"], form.get("name", ""))
            except (ValueError, subprocess.TimeoutExpired) as err:
                target = "/add?tab=github&error=" + urllib.parse.quote(str(err))
            else:
                if chosen:
                    project_settings(dest, chosen)
                track(dest)                        # a clone has work of its own: its agent brings the plan over
                target = f"/?p={projects().index(dest.resolve())}"
            self.send_response(303)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in ("/add", "/new", "/roots"):
            target = "/settings" if path == "/roots" else "/"
            chosen = {k: form.get(k, "") for k in PROJECT_KEYS if form.get(k)}
            if path == "/add" and form.get("path"):
                folder = Path(form['path']).expanduser().resolve()
                original = next((p for p in projects() if workdir(p).resolve() == folder), None)
                family = chosen.get('provider') or reg['settings']['provider']
                if original and (providers.of(original) is not providers.get(family) or form.get('role')):
                    if form.get('role') not in ('lead', 'helper'):
                        return self._send(200, duplicate_agent_page(reg, original, chosen).encode())
                    name = original.name + '-' + family
                    count = 2
                    while any(p.name == name for p in projects()):
                        name = original.name + '-' + family + '-' + str(count)
                        count += 1
                    try:
                        root = sharing(folder, name, chosen, role=form['role'])
                    except ValueError as err:
                        return self._send(409, str(err).encode())
                    target = f"/?p={projects().index(root)}"
                else:
                    if chosen:
                        project_settings(folder, chosen)
                    track(folder)
                    target = f"/?p={projects().index(folder)}"
            elif path == "/new" and form.get("name", "").strip():
                new = Path(form.get("within") or reg["new_root"]).expanduser() / re.sub(r"[^A-Za-z0-9_. -]", "-", form["name"].strip())
                new.mkdir(parents=True, exist_ok=True)
                if chosen:
                    project_settings(new, chosen)
                track(new)
                console.ensure(new)
                target = f"/?p={projects().index(new)}"
            elif path == "/roots":
                reg = registry()
                if form.get("add") and form["add"] not in reg["roots"]:
                    reg["roots"].append(form["add"])
                if form.get("remove") in reg["roots"]:
                    reg["roots"].remove(form["remove"])
                    if reg["new_root"] == form["remove"]:
                        reg["new_root"] = reg["roots"][0] if reg["roots"] else str(PACKAGE_PROJECTS)
                if form.get("default") in reg["roots"]:
                    reg["new_root"] = form["default"]
                if form.get("untrack") in reg["projects"]:
                    reg["projects"].remove(form["untrack"])
                if form.get("show") in reg["hidden"]:
                    reg["hidden"].remove(form["show"])
                save_registry(reg)
            self.send_response(303)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/support":
            from . import supports
            try:
                supports.decide(form.get("id", ""), form.get("verdict", ""), form.get("text", ""))
            except (KeyError, ValueError):
                pass                                   # answered already, or not a choice it offers
            self.send_response(303)
            self.send_header("Location", "/monitor")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/direction":
            from . import monitor
            monitor.set_direction(None if form.get("reset") else form.get("text", ""))
            self.send_response(303)
            self.send_header("Location", "/monitor?view=direction")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/posture":
            from . import monitor
            root = projects(reg)[int(form.get("p", "0"))]
            monitor.set_posture(root, helm=form.get("helm") == "on", direction=form.get("direction", ""), scout=form.get("scout"),
                                scout_note=form.get("scout_note", ""), scouting=form.get("scouting") == "on")
            self.send_response(303)
            self.send_header("Location", "/monitor?view=helm")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/helm":
            from . import monitor
            monitor.helm(form.get("state") == "on")
            self.send_response(303)
            self.send_header("Location", "/monitor")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        pid = int(form.get("p", "0"))
        if pid == -1 and path == "/console/stop":
            from . import monitor
            subprocess.run(["tmux", "kill-session", "-t", monitor.name()], capture_output=True)
            self.send_response(303)
            self.send_header("Location", "/monitor")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        root = projects(reg)[pid]
        text = form.get("text", "").strip()
        path = urllib.parse.urlparse(self.path).path
        if path in ('/lead/switch', '/item/owner', '/progress/decision', '/progress/control'):
            from . import lead, progress, continuation
            try:
                if path == '/lead/switch':
                    lead.switch(root, form.get('member', ''), words='Switch the project lead from the board.')
                    continuation.handoff(root)
                elif path == '/item/owner':
                    lead.assign(root, form.get('item', ''), None if form.get('member') == 'inherit' else Path(form.get('member', '')))
                elif path == '/progress/decision':
                    progress.decide(root, form.get('candidate', ''), form.get('action', ''),
                                    text=text, next_checkpoint=form.get('next') or None)
                elif form.get('action') == 'pause':
                    progress.pause(root, True)
                elif form.get('action') == 'resume':
                    continuation.resume(Path(lead.info(root)['lead']))
                elif form.get('action') == 'start':
                    progress.start(root, form.get('checkpoint', ''))
                elif form.get('action') == 'show':
                    for value in progress.waiting(root):
                        append(root, 'dismissed.jsonl', dict(type='restored', key=value['key'], at=now()))
                continuation.tick(Path(lead.info(root)['lead']))
            except (OSError, ValueError, RuntimeError) as err:
                return self._send(409, str(err).encode())
        elif path == '/vision':
            from . import vision
            try:
                vision.save(root, form.get('text', ''), how='board', words=form.get('words', ''), before=form.get('before', ''))
            except ValueError as err:
                return self._send(409, shell(reg, pid, f"<h1>Vision</h1><p>{e(str(err))}</p>"
                                            f"<p>Your unsaved revision:</p><pre>{e(form.get('text', ''))}</pre>"
                                            f"<p><a href='/?p={pid}'>Back to the project</a></p>").encode())
        elif path == "/note" and text:
            kind, ref = form.get("kind"), form.get("ref")
            from . import lead
            recipient = lead.owner(root, ref) if kind == 'item' and lead.group(root) else root
            add_note(recipient, {kind: ref} if kind in ("item", "commit") else None, text, author='person')
        elif path == "/answer":
            try:
                decisions = {k.removeprefix("point_"): v for k, v in form.items() if k.startswith("point_")}
                answer_gate(root, form.get("gate"), text, decisions=decisions or None)
            except StopIteration:
                return self._send(404, b"No such gate in this project.")
            except ValueError as err:
                return self._send(400, shell(reg, pid, f"<h1>Gate answer not saved</h1><p>{e(str(err))}</p>"
                                             f"<p><a href='/?p={pid}'>Back to the project</a></p>").encode())
        elif path == "/choose" and form.get("option"):
            name = console.session_name(root)
            keys = providers.of(root).choose(console.screen(name), form["option"])
            if keys:
                console.press(name, keys)
        elif path in ("/project/remove", "/project/delete"):
            (remove_project if path == "/project/remove" else delete_project)(root)
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif path == "/pin" and form.get("target", "").strip():
            try:
                pin = pins.add(root, form["target"].strip(), form.get("title", ""), kind=form.get("kind"))
                tell_pinned(root, pin, form.get("comment", "").strip())
            except FileNotFoundError:
                pass
        elif path == "/unpin" and form.get("id"):
            pins.remove(root, form["id"])
        elif path == "/pin/comment" and text and form.get("id"):
            pin = pins.get(root, form["id"])
            if pin:
                add_note(root, {"pin": pin["id"]}, f"On the pinned {pins.describe(pin)}: {text}", author='person')
        elif path == "/pin/save" and form.get("id"):
            pin = pins.get(root, form["id"])
            target = pins.inside(root, pin["target"]) if pin and pins.is_text(pin) else None
            if target:
                target.write_text(form.get("text", "").replace("\r\n", "\n"))
                add_note(root, {"pin": pin["id"]}, f"The person edited the pinned {pins.describe(pin)} on the board.", author='colony', quiet=True)
        elif path == "/clear" and form.get("key"):
            for key in form["key"].split(","):                # a moment may hold several items
                clear_waiting(root, key)
        elif path == "/approve" and form.get("item"):
            iid = form["item"]
            from . import progress
            if iid in progress.held_items(root):
                return self._send(409, b'Approve the completed version using its current candidate.')
            # either way it leaves the person's list now: "not yet" is back with the agent until it says ready again
            # What the person typed is theirs; a bare verdict is colony's receipt of the click.
            if form.get("verdict") == "not-yet":
                add_note(root, {"item": iid}, f"Not yet, on {iid}" + (f": {text}" if text else ".")
                         + " When it's ready again, say so with colony ready.", author='person' if text else 'colony')
            else:
                add_note(root, {"item": iid}, f"The person approved {iid}" + (f": {text}" if text else ".") + " Mark it done.",
                         author='person' if text else 'colony')
            append(root, "dismissed.jsonl", {"type": "dismissed", "key": "verify:" + iid, "at": now()})
        elif path == "/reply" and text:
            if not console.type_into(console.session_name(root), text):
                return self._send(409, held(root.name, form.get("back", "/")).encode())
            answer_asks(root, "from the board")
        elif path == "/console/stop":
            console.stop(root)
        elif path == "/seen":
            reg["seen"][str(root)] = {"at": now(), "head": form.get("head") or git(root, "rev-parse", "HEAD").strip()}
            save_registry(reg)
        back = form.get("back", "")
        self.send_response(303)
        self.send_header("Location", back if back.startswith("/") and not back.startswith("//") else f"/?p={pid}")
        self.send_header("Content-Length", "0")
        self.end_headers()


def serve(port, lan=False, monitor=True):
    from . import monitor as mon, vision
    vision.install_all()
    rewire_projects()
    mon.start(enabled=monitor and registry()["settings"]["monitor"])
    httpd = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), Handler)
    httpd.lan = lan
    where = "every address on this machine (your home network can open it)" if lan else "http://127.0.0.1"
    print(f"board: {where}, port {httpd.server_address[1]}  (ctrl-c to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


CSS = """
:root { color-scheme: light dark; --bg:#fbfaf8; --card:#fff; --ink:#1a1a19; --muted:#6b6a66; --line:#e4e1db;
  --accent:#2f5d50; --flag:#8a5a1e; --flag-bg:#fdf3e3; --sunk:#f4f3f0; }
@media (prefers-color-scheme: dark) { :root { --bg:#16171a; --card:#1e2024; --ink:#e8e6e2; --muted:#9a9791;
  --line:#2f3238; --accent:#7fb5a2; --flag:#d8a55f; --flag-bg:#2b2317; --sunk:#24262b; } }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--ink); display:flex; min-height:100vh;
  font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif }
nav { width:220px; flex:none; border-right:1px solid var(--line); padding:16px 10px; background:var(--card) }
nav .proj { display:flex; align-items:center; gap:6px; padding:7px 10px; border-radius:7px; color:var(--ink); text-decoration:none }
nav .proj.on { background:var(--sunk); font-weight:600 } main { flex:1; min-width:0; max-width:1240px; padding:8px 24px 60px }
header h1 { margin:14px 0 4px; font-size:21px } header p { margin:0 0 4px }
h2 { font-size:14px; margin:26px 0 10px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em }
h3 { margin:0 0 8px; font-size:15px } a { color:var(--accent) } a:visited { color:var(--accent) } code { font-family:ui-monospace,Menlo,monospace; font-size:.9em }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; margin-bottom:12px; padding:14px 16px }
.pre { white-space:pre-wrap; overflow-wrap:anywhere; margin:8px 0 }
.card.gate { background:var(--flag-bg); border-color:transparent } .muted { color:var(--muted) }
.badge { margin-left:auto; padding:0 7px; border-radius:999px; font-size:12px; background:var(--sunk); color:var(--muted) }
.badge.gate { background:var(--flag-bg); color:var(--flag) } .badge.new + .badge, .badge + .badge { margin-left:4px }
details.item { border-top:1px solid var(--line); padding:6px 0 } details.item summary { cursor:pointer }
.st { display:inline-block; min-width:44px; font-size:12px; color:var(--muted) } .item.done summary { color:var(--muted) }
.item.doing .st { color:var(--accent); font-weight:600 } .item.verify .st, .st.verify { color:var(--flag); font-weight:600 }
.node.verify { border-color:var(--flag) }
.note { margin:8px 0 0 18px; padding:6px 10px; background:var(--sunk); border-radius:7px }
.reply { margin-top:6px; padding-left:10px; border-left:2px solid var(--accent) } .who { font-size:12px; color:var(--muted) }
form.add { margin:8px 0 4px 18px; display:flex; gap:8px; flex-wrap:wrap } form.add textarea { flex:1 1 100%; min-height:44px;
  font:inherit; padding:7px 9px; border-radius:7px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.consult-point { width:100%; padding:10px 0; border-bottom:1px solid var(--line); overflow-wrap:anywhere }
.consult-point label { display:flex; gap:12px; align-items:flex-start; justify-content:space-between; flex-wrap:wrap }
.consult-point label span:first-child { flex:1 1 230px } .consult-point .who { margin-top:4px }
.consult-point select { font:inherit; padding:5px 8px; border:1px solid var(--line); border-radius:7px; background:var(--bg); color:var(--ink) }
button { font:inherit; padding:5px 13px; border-radius:7px; border:0; background:var(--accent); color:var(--card); cursor:pointer }
ul { margin:0; padding-left:18px } li { margin:3px 0 }
main.wide { max-width:none } header.slim { display:flex; align-items:center; gap:18px } header.slim h1 { margin:10px 0 }
.tabs { display:flex; gap:4px; margin:6px 0 10px } .tabs a { padding:4px 12px; border-radius:7px; color:var(--muted); text-decoration:none }
.tabs a.on { background:var(--sunk); color:var(--ink); font-weight:600 }
nav .proj { flex-direction:column; align-items:stretch; gap:1px } .pname { display:flex; align-items:center; gap:6px }
.sline { font-size:11.5px; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; padding-left:14px }
nav .proj.monitor { border-bottom:1px solid var(--line); border-radius:7px 7px 0 0; margin-bottom:8px; padding-bottom:9px }
.navfoot { margin-top:14px; padding-top:10px; border-top:1px solid var(--line); display:flex; flex-direction:column; gap:4px; font-size:13px }
.navfoot a { padding:4px 10px; text-decoration:none } ul.dirs { list-style:none; padding:0; columns:2 } ul.dirs li { margin:3px 0 }
@media (max-width: 700px) { ul.dirs { columns:1 } }
ul.folders { list-style:none; padding:0; margin:0 } ul.folders li { padding:8px 0; border-top:1px solid var(--line) } ul.folders li:first-child { border-top:0 }
ul.folders .path { display:block; overflow-wrap:anywhere } .folderacts { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:6px }
.folderacts form { margin:0 } .dangers { display:flex; gap:8px; flex-wrap:wrap } .dangers form { margin:0 } button.danger { background:#b3261e; color:#fff } .folderacts b { margin-right:auto; font-size:13px }
form.inline { display:inline; margin-left:8px } input[name=name] { font:inherit; padding:6px 9px; border-radius:7px;
  border:1px solid var(--line); background:var(--bg); color:var(--ink); flex:1 }
form.options { display:flex; flex-direction:column; gap:10px } form.options label { display:flex; gap:6px 10px; align-items:center; flex-wrap:wrap }
.provider-fields { display:contents }
form.options input[type=text], form.options input:not([type]) { font:inherit; padding:5px 8px; border-radius:7px;
  border:1px solid var(--line); background:var(--bg); color:var(--ink); min-width:0; flex:1 1 220px; max-width:100% } form.options button { align-self:flex-start }
form.options textarea.short { min-height:44px }
.vision { margin:18px 0; max-width:900px } .vision details { margin:12px 0 }
.vision textarea:not([hidden]) { width:100%; box-sizing:border-box; font:inherit; padding:10px; resize:vertical;
  border:1px solid var(--line); border-radius:7px; background:var(--bg); color:var(--ink) }
textarea.direction { width:100%; min-height:60vh; font:inherit; font-size:14px; line-height:1.45; padding:10px; border-radius:8px;
  border:1px solid var(--line); background:var(--bg); color:var(--ink) } form.options input.hours { font:inherit; width:4.5em; padding:5px 8px; border-radius:7px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.msgbox { margin-left:auto } .msgbox summary { cursor:pointer; color:var(--accent); font-size:13px }
.msgbox form { position:absolute; right:24px; z-index:10; width:420px; display:flex; flex-direction:column; gap:8px;
  padding:14px; border-radius:10px; background:var(--card); border:1px solid var(--line); box-shadow:0 8px 24px rgba(0,0,0,.18) }
.msgbox label { display:flex; flex-direction:column; gap:4px; font-size:13px } .msgbox textarea { min-height:80px; font:inherit;
  padding:7px 9px; border-radius:7px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.msgbox select, .options select { font:inherit; padding:4px 6px; border-radius:7px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.psettings summary { cursor:pointer; color:var(--accent); list-style:none; font-size:14px; white-space:nowrap }
.titlerow { flex-wrap:wrap; row-gap:6px } .helmform button { white-space:nowrap }
.psettings .panel textarea { width:100%; min-height:90px; font:inherit; padding:7px 9px; border-radius:7px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.psettings summary::-webkit-details-marker { display:none } header.project { position:relative }
.titlerow { display:flex; align-items:baseline; gap:12px } .titlerow .psettings, .titlerow .exitlink { margin-left:auto } .titlerow .psettings + .psettings { margin-left:0 }
.exitlink { font-size:14px; text-decoration:none; white-space:nowrap }
.segs { display:flex; gap:4px; margin:6px 0 12px; padding:3px; border-radius:10px; background:var(--sunk); width:fit-content; max-width:100% }
.seg { background:transparent; color:var(--muted); padding:6px 12px; border-radius:8px } .seg.on { background:var(--card); color:var(--ink); font-weight:600 }
.picker { display:flex; flex-direction:column; gap:6px }
.chosen { max-width:100% } .chosen input, form.options .chosen input:not([type]) { min-width:0; flex:1 1 auto; width:auto } .chosen button { flex:none }
.psettings .panel { position:absolute; right:0; z-index:10; width:min(440px, calc(100vw - 32px)); padding:14px 16px;
  border-radius:10px; background:var(--card); border:1px solid var(--line); box-shadow:0 8px 24px rgba(0,0,0,.18) } hr { border:0; border-top:1px solid var(--line); margin:14px 0 }
.sdot { width:10px; height:10px; border-radius:50%; flex:none; background:transparent; border:2px solid var(--line); box-sizing:border-box }
.sdot.off { opacity:.55 }
.sdot.idle { border-color:var(--accent) }
.sdot.working { border-color:var(--accent); border-top-color:transparent; animation:spin .8s linear infinite }
.sdot.needs-you { background:var(--flag); border-color:var(--flag); animation:beat 1.1s ease-in-out infinite }
@keyframes spin { to { transform:rotate(360deg) } }
@keyframes beat { 50% { transform:scale(1.35); box-shadow:0 0 0 4px color-mix(in srgb, var(--flag) 25%, transparent) } }
.status { display:block; margin:10px 0 4px; padding:10px 14px; border-radius:10px; background:var(--card); border:1px solid var(--line);
  color:var(--ink); text-decoration:none } .statusline { display:flex; align-items:center; gap:8px; min-width:0 }
.statusline .muted { overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
.agents { font:12.5px/1.6 ui-monospace,Menlo,monospace; margin-top:4px } .agents:empty { display:none }
.agent { display:flex; justify-content:space-between; gap:12px; color:var(--muted) } .agent.current { color:var(--ink); font-weight:600 }
@keyframes pulse { 50% { opacity:.35 } }
.console-bar { display:flex; align-items:center; gap:12px; justify-content:space-between; margin-bottom:8px; font-size:13px }
.console-bar form { margin:0 } button.quiet { background:var(--sunk); color:var(--ink) }
.needs summary { cursor:pointer } .need { border-top:1px solid var(--line); padding:10px 0 }
.helmform { margin-left:auto } .titlerow .psettings + .helmform { margin-left:0 } .helmcard .titlerow .badge { margin-left:auto } .helmcard label.stack { flex-direction:column; align-items:stretch }
.helmcard textarea { width:100%; min-height:56px; font:inherit; padding:7px 9px; border-radius:7px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.focus ul { margin:6px 0 0; padding-left:0; list-style:none } .focus li .kind { display:inline-block; min-width:52px; font-size:12px; color:var(--muted) }
.mstatus .who { margin:10px 0 -6px }
.pinned { margin:10px 0 6px } .pinhead { display:flex; align-items:baseline; gap:12px } .pinhead h2 { margin:10px 0 6px }
.pinhead a { margin-left:auto; font-size:14px; text-decoration:none } .pin { padding:7px 0; border-top:1px solid var(--line) }
.pinline { display:flex; align-items:center; gap:8px } .pintitle { text-decoration:none; font-weight:600; flex:1; min-width:0;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap } .pin .who { white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
.pinmore { position:relative } .pinmore summary { list-style:none; cursor:pointer; padding:0 8px; color:var(--muted) }
.pinmore summary::-webkit-details-marker { display:none }
.pinmenu { position:absolute; right:0; z-index:10; width:min(360px, calc(100vw - 32px)); padding:10px; border-radius:10px;
  background:var(--card); border:1px solid var(--line); box-shadow:0 8px 24px rgba(0,0,0,.18) } .pinmenu form.add { margin:0 0 8px }
[hidden] { display:none !important }   /* a class that sets display must never unhide what is hidden */
.browse { display:flex; flex-direction:column; gap:4px; max-height:50vh; overflow:auto; flex:1 1 100% } .browse button { text-align:left }
.chosen { display:flex; gap:8px; flex:1 1 100% } .chosen input { flex:1; font:inherit; padding:6px 9px; border-radius:7px; border:1px solid var(--line); background:var(--sunk); color:var(--ink) }
.pickfile { margin:0 } .pinform input { font:inherit; padding:6px 9px; border-radius:7px; border:1px solid var(--line);
  background:var(--bg); color:var(--ink); flex:1 1 100% }
form.editor { display:flex; flex-direction:column; height:calc(100dvh - 24px) } .editbar { display:flex; align-items:center; gap:12px; padding:8px 0 }
.editbar b { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap } form.editor textarea { flex:1; width:100%;
  font:14px/1.5 ui-monospace,Menlo,monospace; padding:12px; border-radius:10px; border:1px solid var(--line); background:var(--card); color:var(--ink) }
.since .caughtup { position:sticky; top:calc(var(--stuck-top, 0px) + 8px); z-index:2; height:34px; margin:0 0 -34px; display:flex; justify-content:flex-end;
  pointer-events:none } .since .caughtup button { pointer-events:auto; box-shadow:0 2px 10px rgba(0,0,0,.25) }
.since ul { padding-right:4px; margin-bottom:0; padding-bottom:42px }  /* where the button comes to rest: below the last item */ .since li:first-child { padding-right:80px } .need .who { display:flex; align-items:center; gap:8px } form.clear { margin:0 } form.clear button { padding:2px 10px; font-size:12px } .need .who .kind { margin-left:auto; text-align:right } .ready { padding:6px 0; border-top:1px dashed var(--line) } .ready:first-child { border-top:0 } form.verdict { display:flex; gap:8px; flex-wrap:wrap; margin-top:6px } form.verdict input { flex:1 1 140px; min-width:0; font:inherit; padding:5px 8px; border-radius:7px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.need form.add button { margin-left:auto } .asktext { white-space:pre-wrap; margin:6px 0; max-height:24em; overflow:auto } .need pre { margin:6px 0; font:12px/1.45 ui-monospace,Menlo,monospace; white-space:pre-wrap; color:var(--muted) }
.need form.add { margin-left:0 } .choices { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px } .choices form { margin:0 }
.keys { display:none; gap:6px; flex-wrap:wrap; margin-bottom:8px }
.keys button { flex:1 1 auto; min-width:0; padding:10px 6px; background:var(--sunk); color:var(--ink); font-size:15px; white-space:nowrap }
.touch-only { display:none }
@media (pointer: coarse) { .keys { display:flex } .touch-only { display:inline-block } }
/* On a phone the console fills the space between the project chips (top) and the tabs (bottom). */
body.focus header > :not(.tabs), body.focus .console-bar, body.focus #needs-box, body.focus .vision { display:none }
body.focus header { margin:0 } body.focus main { padding:0; max-width:none } body.focus #term { border-radius:0; padding:2px }
body.focus .keys { position:fixed; left:0; right:0; z-index:30; margin:0; padding:6px; gap:5px; flex-wrap:nowrap;
  overflow-x:auto; background:var(--card); border-top:1px solid var(--line) }
.keys .send { background:var(--accent); color:var(--card) }
.keys button[data-mod].on { background:var(--flag); color:var(--card) }
.keys .selectall { display:none } body.copying .keys button[data-k], body.copying .keys button[data-mod], body.copying .keys .send { display:none }
body.copying .keys .selectall { display:block }
#term, #term .xterm-viewport, #term .xterm-screen { touch-action:none } #term { position:relative }
.touchpad { display:none; position:absolute; inset:0; z-index:5; touch-action:none; -webkit-user-select:none; user-select:none }
@media (pointer: coarse) { .touchpad { display:block } }
.jump { position:absolute; left:50%; bottom:14px; transform:translateX(-50%); z-index:6; width:44px; height:44px;
  border-radius:50%; padding:0; font-size:20px; background:var(--accent); color:var(--card); box-shadow:0 2px 10px rgba(0,0,0,.35) }
.jump[hidden], .badge[hidden] { display:none }
@media (pointer: coarse) { #term .xterm-viewport { overflow-y:hidden !important } }
#hist { position:absolute; inset:0; z-index:25; margin:0; padding:12px; overflow:auto;
  white-space:pre-wrap; word-break:break-word; font:12.5px/1.45 ui-monospace,Menlo,monospace; background:#16171a; color:#d7d4ce;
  -webkit-user-select:text; user-select:text; -webkit-overflow-scrolling:touch }
#term { height:calc(100vh - 130px); border-radius:10px; overflow:hidden; background:#16171a; padding:6px }
.mapbox > summary, .ms > summary { cursor:pointer; list-style:none; display:flex; align-items:baseline; gap:12px }
.mapbox > summary { color:var(--accent); font-size:13px; margin-bottom:10px } .ms > summary h3 { margin:0 }
.ms[open] > summary { margin-bottom:6px } .ms > summary .msdot { align-self:center }
/* what people and agents write can hold a long unbroken word (a flag, a URL): it breaks rather than widen a phone's page */
.note, .need, .ready, .reply, header p, .item .body p { overflow-wrap:anywhere }
table.bench { border-collapse:collapse; font-size:13px; width:100% } table.bench th, table.bench td { padding:4px 8px; text-align:left; white-space:nowrap }
table.bench tr + tr { border-top:1px solid var(--line) } table.bench td.sc { text-align:right; background:color-mix(in srgb, var(--accent) calc(var(--s) * 45%), transparent) }
table.bench td.gap { text-align:right; color:var(--muted) } table.best th { width:7em } table.best td { white-space:normal }
svg.chart text { font-size:11px; fill:var(--muted) } svg.chart .axis { font-size:11px } .chartkey { display:flex; flex-wrap:wrap; gap:4px 14px; margin-top:6px }
.chartkey i { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:5px; vertical-align:middle }
ol.steps { margin:6px 0 10px; padding-left:20px } ol.steps li { margin:3px 0 }
a.modelinfo { font-size:13px; text-decoration:none; white-space:nowrap }
.timer { color:var(--muted); font-size:12px; font-variant-numeric:tabular-nums; white-space:nowrap } .timer.running { color:var(--accent) } .done-group > .ms { padding:6px 0 0 12px } .item .body { padding:4px 0 6px 18px } .item .body p { margin:4px 0 }
.legend { font-size:13px; color:var(--muted); margin-bottom:10px } .mapwrap { overflow-x:auto; padding-bottom:6px }
.map { position:relative } .map svg { position:absolute; left:0; top:0 } .map path { fill:none; stroke:var(--line); stroke-width:2 }
.node { position:absolute; display:flex; flex-direction:column; justify-content:center; gap:2px; padding:6px 10px;
  border-radius:9px; border:1.5px solid var(--line); background:var(--card); color:var(--ink); text-decoration:none; font-size:13px }
.node:hover { border-color:var(--accent); z-index:5 } .node.done { background:var(--sunk); color:var(--muted) }
.node.doing { border-color:var(--accent); box-shadow:0 0 0 2px color-mix(in srgb, var(--accent) 25%, transparent) }
.nid { font-size:11px; color:var(--muted) } .ntext { white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
.dot { position:absolute; top:6px; right:8px; width:8px; height:8px; border-radius:50%; background:var(--flag) }
.pop { display:none; position:absolute; left:0; top:calc(100% + 6px); width:260px; padding:10px 12px; border-radius:9px;
  background:var(--card); border:1px solid var(--line); box-shadow:0 6px 20px rgba(0,0,0,.14); color:var(--ink);
  flex-direction:column; gap:5px; white-space:normal } .node:hover .pop { display:flex }
.st.done { color:var(--muted) } .st.doing { color:var(--accent); font-weight:600 } .st.todo { color:var(--ink) }
.navfoot .short { display:none }
/* A phone: the sidebar becomes one row of chips (dot, name, waiting count), then + and ⚙; it scrolls sideways. */
@media (max-width: 700px) { body { display:block }
  nav { width:auto; border-right:0; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:6px;
    padding:8px 10px; overflow-x:auto; white-space:nowrap }
  nav .proj, nav .proj.monitor { flex:none; flex-direction:row; align-items:center; margin:0; padding:5px 11px; border:0;
    border-radius:999px; background:var(--sunk) }
  nav .proj.on { background:var(--accent); color:var(--card) } nav .proj.on .sdot:not(.needs-you) { border-color:var(--card) } nav .proj.on .sdot.working { border-top-color:transparent }
  nav .sline, nav .badge.new { display:none }
  .navfoot { flex-direction:row; margin:0 0 0 auto; padding:0; border:0; gap:2px; font-size:16px }
  .navfoot a { padding:4px 9px } .navfoot .long { display:none } .navfoot .short { display:inline }
  nav { position:sticky; top:0; z-index:40 }
  .tabs { position:fixed; left:0; right:0; bottom:0; z-index:40; margin:0; gap:0; background:var(--card);
    border-top:1px solid var(--line); padding:4px 6px calc(4px + env(safe-area-inset-bottom)) }
  .tabs a { flex:1; text-align:center; padding:10px 4px 9px; border-radius:0; color:var(--muted) } main { padding-bottom:78px }
  /* the tab bar reads as navigation, not as more keys: its own tinted ground, the current tab in the accent */
  .tabs { background:color-mix(in srgb, var(--accent) 12%, var(--bg)); border-top:0; box-shadow:0 -1px 0 var(--line) }
  .tabs a.on { background:transparent; color:var(--accent); font-weight:700; box-shadow:inset 0 3px 0 var(--accent) } }
"""
