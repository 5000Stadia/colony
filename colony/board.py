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
import threading
import urllib.parse
from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import console, hours, pins, providers, showcase

MILESTONE = re.compile(r"^##\s+(M\d+(?:\.\d+)?)\s*[—–-]+\s*(.+?)\s*$")      # M3, and M3.5 between M3 and M4
ITEM = re.compile(r"^\s*-\s*\[( |x|X|~|\?)\]\s*(R\d+)\s+(.+?)(?:\s*\(after\s+([R\d,\s]+)\))?\s*$")
STATE = {" ": "todo", "~": "doing", "?": "verify", "x": "done", "X": "done"}
# A milestone may say what kind of part it is, after its title (`## M4 — Concepts to develop · menu`): its items in
# order (a sequence, unsaid), options in any order (menu), roads side by side (lanes, each item's `(lane: hair)`),
# questions answered by experiment (findings), or pieces of one whole (sections). Only a sequence implies an order.
KINDS = ("sequence", "menu", "lanes", "findings", "sections")
KIND = re.compile(r"^(.*?)\s*·\s*(" + "|".join(KINDS) + r")\s*$", re.I)
LANE = re.compile(r"\s*\(lane:\s*([^)]+?)\s*\)")
LABEL = {"todo": "todo", "doing": "doing", "verify": "to verify", "done": "done"}

PROTOCOL = """
## This project is part of a colony

The colony is the person's set of projects, each with its own agent (you are this project's). They follow and
steer all of them from one board, and the projects can write to each other.

### What matters

- **The vision.** Every project steers toward a vision: the image of the finished work, kept in the `## Vision`
  section at the top of `ROADMAP.md`, in the person's words. It directs the work, and the person is its guide: their
  interest lives in it, so a request that drifts from it is named, and one that sharpens it updates it. Listen past
  the literal request for why it matters in the person's life and work, so the work serves that root and not only
  its surface. Its depth fits the project, so the conversation first asks openly whether this is a one-off or part of something
  bigger (a guess here invites a yes). A quick job
  with a conclusion (one benchmark, one report) needs what it is, what done looks like, what's ruled out and one light
  question on what it's for or what hinges on it; often the answer is simply that they want it, and that is enough. A mid-size
  one adds acceptance criteria buttoned up against a real example, and a brief gut check on the deeper questions
  below: raise those that would change the work, offer the rest in a line in case the person wants to discuss them,
  and leave them out where bringing them up would be silly. A long venture holds them all: beyond what the work
  fundamentally is, why the person wants it (what getting there gives them), where it lands (who uses it, how many,
  how they find it, on what terms), and the edge they bring (their insight, taste and experience), which the work
  builds on rather than replacing with a generic version; what using it should feel like, what finished and excellent
  mean (ideally against a real example the person admires), and where it narrows or widens what they asked for. For a venture, draw its arc with the person, in this order: its
  end (how long it is meant to live, and what would make it stop: done, outgrown, not worth it); its peak, which the
  person paints themselves, in their own words, as a scene at the height of its success (offer no draft of it: their
  picture is what the work is judged against, though you may be creative with them about what would make it
  incredible); then the climb from here to there and the foothold to begin from (the cheapest proof, the quickest
  return, what is ready now), which you draft from their picture for them to correct. Read the picture for what it
  means for the work (what success is, its scale, the risk they'll take, the pace, what must stay true), and say back
  in plain words what you take for the work and what you leave with them: what they share of their own life explains
  why it matters, never becomes tasks of yours, and stays in this project. Make
  it clear and shared with them, settling a detail only when the work comes to depend on it ("the best friend dies in
  the final chapter, how is undecided" is settled near that chapter, not now); detail that matters to a few items
  lives in those items. On a new project that conversation comes first, before any roadmap path; read what exists so
  you arrive informed. At every scale it holds acceptance criteria that test what the work is for, not only what it
  is: how you and the person will know it served them (for a benchmark the team distrusts, that the skeptics rerun it
  and get the same answer). Each item's check and the roadmap as a whole trace to them, and a criterion no item serves
  is a gap in the path. When a quick job grows into more, say so, and deepen the vision when it is next revised. A
  running project takes these up when its vision is next revised. When the person's reaction to the work reveals a
  standard, or the work shows the vision differently, propose a revision; brainstorming never changes it.
- **The path.** The roadmap is the way there, and it should cover all of it: when something the vision clearly
  needs has no place on it (a house with no wiring), bring it up with the person to detail and place, rather than
  building past it. Before each step, ask whether it is still the smartest next one toward the vision. Reordering is yours; adding, dropping or reshaping a milestone is the person's call, and
  Later waits for them. The person is the compass: they shape the vision and the path, and can say where quality or
  direction seems off and steer it. How hands-on they are is theirs to set, and can change from one stretch to the
  next, so routine deliverables need no sign-off unless they ask for one; decisions that are theirs and judgements
  only they can make still wait on that point. When a deliverable reaches a stage where it should
  be good, show it in the showcase: what it is and how to see or try it, only when seeing it would tell them
  something about quality or direction, never for each small piece of a spec. Build each step to fit the finished whole, finish what you take on, and check what you
  can check yourself: before building an item, say how it will be checked, and before calling it done, reopen
  what you produced and check it against that, since work judged from memory of writing it passes what it
  shouldn't. Where only the person can judge, ask for their verdict in a form later work can be checked against
  (an approved image, a graded set). A question that matters only later waits for its moment; when an item's time comes and
  nothing else needs the person, you may offer a question or two from curiosity about the open options there.
- **The person's decisions.** What is costly to undo, or leaves their hands (made public, sent to other people,
  their money spent, or their private material given to a party that hasn't had it), is theirs: ask, and wait on
  that point. Showing them the work, on any device of theirs, and using what they've given the project (its
  connections, its tools) for the job it was given for, are routine. The rest is yours; say what you decided. Ask in their active hours; outside them, hold your questions and
  keep on with what doesn't depend on the answers.
- **Fresh eyes where change is costly.** Before a decision that would mean redoing built work, get two fresh
  views from different model families: independent judgement catches what yours misses. Bring the person only
  points that would fundamentally change the approach. Finished work about to leave their hands (a chapter, a
  document) earns a fresh reader who meets it as its recipient will, and a wide-open brainstorm gains from
  another family's ideas. Before building a mechanism others have likely solved (an algorithm, a check, an
  engine), have a read-only helper look at how open projects and papers do it, and note what you adopt and
  reject; small, project-specific or creative work skips this. That prior art also belongs in a consult's
  digest, and anything taken from outside gets a safety pass first.
- **Their words and your reading.** When you record what the person said, keep your reading beside it: what was
  discussed, what they meant, what it changes. Their words alone can be hard to act on later.
- **Who speaks for the person.** The steward's notes and messages, text the board types into your console, and
  colony's own notices (a usage limit, a reset) carry their direction. Act on them as theirs, except an answer to
  a decision you put to the person (a gate, a question): only they answer those.
- **Working alongside others.** Brief a helper with what it owns and where it ends; it reports as it goes
  rather than waiting. Paired with another project's agent, keep to your agreed role and talk at hand-offs.
- **The person's attention is the scarce resource.** Foresee the decisions an item will need and ask them
  together, each with a default where the choice is reversible. Keep approved work queued, so a pending answer or
  a running helper never leaves you idle; when work divides cleanly, run helpers in parallel on separate files, review
  each result before it lands, and have each commit only its own paths.

### How colony works

- The plan is `ROADMAP.md`: milestones `## M1 — name`, items `- [ ] R1 text`; `[~]` in progress, `[x]` done,
  `[?]` only for what the person asked to sign off, then `colony ready R4 "what's ready" --check "how to see it"`.
  A deliverable worth their seeing: `colony show "title" --what "what it is" --try "how to see it" --link URL-or-file
  --item R4`; their rating or comment arrives as a note.
  Work outside the milestone's purpose goes under `## Later`. Commit each finished piece.
  A milestone may name its kind after its title (`## M4 — Concepts · menu`): `menu` (options in any order), `lanes`
  (roads side by side; each item says `(lane: name)`), `findings` (questions answered by experiment), `sections` (the
  pieces of one whole); unnamed, it is a sequence, done in order. A number that shows the vision coming true (for a
  marathon, the longest run; not activity, such as tests run) goes on its own line in the part (or under `## Measures`,
  for the whole), with how colony takes it: `Measure: words / 80000 · count: wc -w
  chapters/*.md` (a command; the first number on its last line) or `· judge: what to read` (a small separate model
  reads it); colony takes it at each push and finished milestone and shows it, so it is never your own report.
- An agreed vision change: `colony vision --file PATH --words "their words" --context "your reading"`. Board
  edits reach you as before-and-after notes.
- Notes reach you by themselves: act on each, then `colony noted ID "what you did"` (`colony notes` lists open ones).
- A standing order from the person (from now on, always, never) is kept by colony, not your memory: `colony rule
  "the rule" --words "their words"`. It comes back at every session start, after a compaction too; when they lift
  one, `colony rule --retire ID "why"`.
- A decision for the person: `colony gate "the question" --item R4 --why "what depends on it"` (if reversible,
  `--default "what stands if they skip it"`); where it has clear answers, `--option "..."` for each and `--recommended "..."` for
  yours: they answer with a tap. The answer arrives as a note. A question whose moment is later: `--when R12` keeps it off their list until R12 starts. Settled in conversation: `colony gate --answered ID "their words" --context "your reading"`; no longer applying (superseded, moot): `colony gate --withdraw ID "why"`. An open gate stays on their list.
- Fresh views: `colony consult R4 "the decision" --digest FILE` (sourced facts, your plan left out); its output
  says what comes next. If consulting is off, go on.
- A pause point agreed with the person: `colony progress`; between pauses, carry on across items.
- The Library holds what you and the person will keep referring to (a world's places and people, a policy set,
  research you build on, a page you work on together): `colony pin PATH-or-URL --title "..." --why "..." --group
  "heading"`. It starts empty; when one's part of the work is done, `colony unpin ID --why "..."` folds it into the archive.
  Organize it as best serves the work (`colony regroup ID "heading"`, `colony regroup --rename OLD NEW`); when the
  person reorganizes it, that is their preference showing: follow it, and settle the shape with them.
- Research others could reuse (prior art, a package vetted, prices) is kept in colony's research library, shared
  by every project: before researching, `colony research "a few words"`; after, `colony research --add FILE --title
  "…" --question "the question it answers"` (`--recheck DAYS` if it ages faster than 90 days).
- In the person's Chat, a picture in the project shows in your reply written `![what it shows](path)`, and a linked file
  there (a PDF) opens; one made elsewhere is copied into the project first, and the copy linked.
- Outside services (an API, a mailbox) reach you as connections, set by the person per project. Whatever needs an
  account or a key, or sends, posts or spends, goes through one, even for this project alone (it can have its own
  account there), never wired into the project with its keys in a file; public data needing neither, your own code
  may fetch. One the work needs and you lack: `colony connect --add NAME --command "…" --key KEY`, an existing MCP
  server with its version pinned (no `--command` for keys your own script uses); they enter its keys on the board,
  never in a chat.
- A turn that asks the person something runs `colony needs-you` before its last words, which hold the question:
  then it waits for them on the board. If they ask you something first, answer it and ask yours again.
- Helpers run at four tiers (routine, step-up, chores, rote), handed to you at each session start.
- How long work goes well, from colony's measured runs: `GUIDE_PATH`. Read it at a project's start, and again
  when weighing structure (helpers, review, tests, memory).
- Projects that share a purpose share a nest (`colony nest` shows them, `colony nest NAME` puts this one in one):
  its shared vision, in the person's words, is the why your own vision serves. Agreed with them: `colony nest NAME
  --vision --file PATH --words "their words" --context "your reading"`.
- Other projects: `colony projects`; ask with `colony send NAME --ask "..."`, answer with `colony reply ID "..."`.
  Mail arrives by itself.
- A delete through a variable is written `rm -rf "${OUT:?}"/*`: if the variable were ever empty, it stops
  instead of deleting from `/`, so no safety check has to stop you.
"""

# Left once by colony, which writes it (the person never typed it), for a project the person adds: the notes
# deliver it on the agent's next turn (the watcher starts or wakes the session), and its reply shows on the board.
JOIN = """The person has just added this project to their colony: the set of projects they follow and steer from \
one board, where they leave notes and answer your gates, and where the projects can message each other. The new \
section of CLAUDE.md, "This project is part of a colony", says how it works.

First, read how this project already plans its work (its plan and spec documents, notes, open work and \
recent history), so you arrive informed. Then have a conversation with the person about your shared vision: \
draw out the image of the finished work with them. This is between you, this project's own agent, and the \
person; the steward's setup does not stand in for it. Leave distant details open until the work depends on \
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
agent, and the person; the steward's setup does not stand in for it. Leave distant details open until the
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
                    "permissions": "all", "consult": True, "consultants": {},
                    "safe_pause": 98, "auto_update": True, "monitor_model": {}, "model_adoption": "automatic",
                    "helper_models": {}, "auto_balance": 3, "remote_by": {}, "safe_pause_session": 99, "turbo_by": {}, "active_hours": hours.DEFAULT, "updates": 60,
                    "tailscale": "", "tailscale_lan_before": None}
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
    "remote_by": "each program's default for new projects, as claude=on,codex=on (a project can turn its own off)",
    "safe_pause_session": "% of a 5-hour session window at which projects wind down until it resets; never later than safe_pause (off: as safe_pause)",
    "turbo_by": "turbo for each program, as claude=on,codex=on (on unless turned off): when its weekly use runs behind pace, its projects with queued work are turned up as each one ticks",
    "lan": "the board answers other devices on your network, not only this machine",
    "messaging": "project agents can message each other (colony send, colony reply)",
    "trust": "a new console's start-up questions (folder trust, permission mode, Remote Control, hooks) are answered so it runs as set up",
    "permissions": "what new sessions may do unasked: ask, edits, all, or plan",
    "monitor": "the steward session runs with the board",
    "model": "main-agent model pin for this provider (blank: colony Auto)",
    "effort": "effort for a main-agent model pin (Auto chooses both model and effort)",
    "consult": "agents consult two fresh models, one from each family, at decisions costly to change (colony consult)",
    "model_adoption": "when a new model becomes a recommendation: automatic or ask (one decision per model)",
    "helper_models": "provider-specific helper pins; edit the colony helper tiers in Settings",
    "monitor_model": "the steward's model, as MODEL:EFFORT (blank: its program's step-up tier)",
    "auto_update": "keep Claude Code and Codex updated daily, and reload a console onto the new version (or changed settings) once it sits idle, in the same conversation",
    "safe_pause": "safe pause: at this % of a program's 5-hour or weekly limit, its projects land what's in flight, save their work and tell you where things stand, before the limit cuts them off mid-task; colony wakes them at the reset (off: never)",
    "consultants": "each family's consultant, as claude=MODEL:EFFORT,codex=MODEL:EFFORT (blank: from the benchmark cards)",
    "updates": "minutes between the steward's updates to you, helm or not: what the projects finished, then what was already waiting on you, then new asks (off: none); never while it is muted or outside your active hours",
    "active_hours": "when you're around, as HH:MM-HH:MM on this machine's clock (off: always): outside them agents keep working but hold their questions, which reach you together when your hours begin",
}


# The steward was once called the monitor: its settings are stored (and may be typed) under the old keys, and
# shown and typed under the new names.
SETTING_ALIASES = {"steward": "monitor", "steward_model": "monitor_model"}
SETTING_NAMES = {v: k for k, v in SETTING_ALIASES.items()}


def set_setting(key, value):
    key = SETTING_ALIASES.get(key, key)
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
    elif key in ("model", "effort", "tailscale"):
        reg["settings"][key] = str(value).strip()
    elif key in ("safe_pause", "safe_pause_session"):
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
    elif key == "updates":
        v = str(value).strip().lower().removesuffix("m").removesuffix("min").strip()
        try:
            n = 0 if v == "off" else int(float(v))
        except ValueError:
            raise KeyError(key)
        if n < 0:
            raise KeyError(key)
        reg["settings"][key] = n
    elif key == "active_hours":
        reg["settings"][key] = hours.normal(value)          # ValueError, saying what reads, for anything else
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
    elif key in ("remote_by", "turbo_by"):
        # Each program's own: its default for new projects' remote reach (one not named follows `remote`), or
        # whether turbo turns its projects up (one not named does).
        from .providers import PROVIDERS
        chosen = {}
        for pair in (x.strip() for x in str(value).split(",") if x.strip()):
            prog, _, state = pair.partition("=")
            if prog not in PROVIDERS:
                raise KeyError(key)
            chosen[prog] = state.lower() in ("on", "true", "yes", "1")
        reg["settings"][key] = chosen
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
    if key == "active_hours":
        restamp_held()                                       # what waits for the hours, waits for the new ones
    return reg


# What turbo means for a project, as it ticks it (colony/turbo.py): stronger models, going deeper on its queued
# work, research on a topic it names. Never part of how its console starts.
TURBO = {"turbo_models": True, "turbo_deeper": True, "turbo_research": False, "turbo_topic": ""}
PROJECT_KEYS = ("provider", "model", "effort", "permissions", "remote", "safe_pause", "auto_balance", "color", "carry_on", *TURBO)
# A project's own colour, the person's to choose: its accent in light and dark, which every tint on its page comes
# from (the bars, the chosen chip, buttons), and a light wash on its chip in the menu. Green is the board's own.
PALETTE = {"green": ("#2f5d50", "#7fb5a2"), "forest": ("#2e5a2e", "#8fc48f"), "sage": ("#566f50", "#aac4a2"),
           "mint": ("#1f6b57", "#7dcbb2"), "teal": ("#1f6670", "#7cc3c9"), "sky": ("#2a6a94", "#8cc4e6"),
           "blue": ("#2f5487", "#93b4e0"), "navy": ("#273f6b", "#8ea7d6"), "indigo": ("#47478f", "#a9a8e6"),
           "violet": ("#5a3d8f", "#b49ae6"), "lavender": ("#675a8f", "#c2b5e3"), "plum": ("#6b3f78", "#c79ad6"),
           "magenta": ("#8a2f78", "#e08fd0"), "rose": ("#8f3b57", "#e39ab3"), "crimson": ("#8f2a33", "#e68c93"),
           "coral": ("#9a4434", "#ef9e8c"), "clay": ("#8f4a2c", "#e0a07f"), "rust": ("#8a3d1c", "#e39570"),
           "amber": ("#7d5a10", "#dcb563"), "gold": ("#776410", "#e0c75e"), "olive": ("#55611f", "#b5c27a"),
           "sand": ("#76674a", "#cdbb94"), "brown": ("#5e4430", "#c2a183"), "slate": ("#46525f", "#a9b4c0"),
           "charcoal": ("#3c3f44", "#a8adb4")}


def colour(root):
    """The project's chosen colour, or None for the board's own."""
    try:
        c = json.loads((Path(root) / ".board" / "settings.json").read_text()).get("color")
    except (OSError, ValueError):
        return None
    return c if c in PALETTE else None


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
            elif k == "turbo_topic":
                own[k] = " ".join(str(v).split())[:300]
            elif k in TURBO:
                if str(v).lower() not in ("on", "off", "true", "false", "yes", "no", "1", "0"):
                    raise KeyError(k)
                own[k] = str(v).lower() in ("on", "true", "yes", "1")
                if own[k] == TURBO[k]:
                    own.pop(k)                         # as colony starts it: nothing of the project's own
            elif k == "remote":
                own[k] = str(v).lower() in ("on", "true", "yes", "1")
            elif k == "carry_on":                          # on as colony starts it: only off is kept
                if str(v).lower() in ("off", "false", "no", "0"):
                    own[k] = "off"
                else:
                    own.pop(k, None)
            elif k == "permissions" and v not in PERMISSIONS:
                raise KeyError(k)
            elif k == "provider" and v not in __import__("colony.providers").providers.PROVIDERS:
                raise KeyError(k)
            elif k == "color" and v not in PALETTE:
                raise KeyError(k)
            else:
                own[k] = v
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(own, indent=2) + "\n")
        if 'auto_balance' in changes:
            selection.reconcile()
    g = registry()["settings"]
    merged = {k: g[k] for k in PROJECT_KEYS if k in g}
    merged["remote"] = g["remote_by"].get(own.get("provider") or g["provider"], g["remote"])
    merged.update(TURBO)
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
            if (sub / ".git").is_file():          # a git worktree a project made for a branch: part of it, not another
                continue
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
    so new projects and the steward start on something that runs. What changed, in a line, or None."""
    from . import providers
    now = providers.get(registry()["settings"]["provider"])
    if providers.usable(now):
        return None
    here = next((k for k, p in providers.PROVIDERS.items() if providers.usable(p)), None)
    if not here:
        return None
    set_setting("provider", here)
    return f"{providers.unusable(now)}, so new projects and the steward run on {providers.get(here).label} (colony settings provider to change it)"


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
    return _protocol(root).replace("GUIDE_PATH", str(Path(__file__).resolve().parent.parent / "GUIDE.md"))


def _protocol(root):
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
            title, kind = (k.group(1), k.group(2).lower()) if (k := KIND.match(m.group(2))) else (m.group(2), "sequence")
            current = {"id": m.group(1), "title": title, "kind": kind, "items": []}
            milestones.append(current)
            last = heading = None
            if kind != "sequence":
                prev = None                     # a menu or lanes leaves no last step for the next part to follow
        elif line.startswith("## "):
            # Any other heading (## Later) is its own section, never part of the milestone above it.
            current, last, heading = None, None, line[3:].strip()
        elif m := ITEM.match(line):
            if current is None:
                if heading is None:
                    continue
                current = {"id": heading, "title": "", "kind": "sequence", "items": []}
                milestones.append(current)
            # In a sequence an item builds on the one before it unless it says otherwise: `(after R2, R3)` branches.
            # In a menu, lanes, findings or sections nothing waits on anything it doesn't name, so they may run at once.
            links = re.findall(r"R\d+", m.group(4) or "")
            seq = current.get("kind", "sequence") == "sequence"
            after = links or ([prev] if prev and seq else [])
            lane = LANE.search(m.group(3))
            last = {"id": m.group(2), "state": STATE[m.group(1)], "text": LANE.sub("", m.group(3)).strip(), "after": after,
                    "links": links, "desc": "", "milestone": current["id"], "lane": lane.group(1) if lane else None}
            if shared:
                last['owner'] = shared['owners'].get(last['id']) or shared['lead']
            current["items"].append(last)
            prev = last["id"] if seq else None
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
        with board_lock("items"):              # read afresh: another page or the watcher may have written meanwhile
            try:
                every = json.loads(path.read_text())
            except (OSError, ValueError):
                every = {}
            every[str(root)] = out
            save_json(path, every)
    return out


def save_json(path, value):
    """A file of the board's written whole, so no reader sees half of it, through a temporary file of this writer's
    own: the board serves pages in threads, and two writing at once never take each other's."""
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(value))
    os.replace(tmp, path)


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
    """The turn ends asking the person something: a sentence ending in "?", outside code, in its last paragraph (or
    its last list). A question earlier in it is talk, or one the agent put to itself (what it is checking)."""
    prose = re.sub(r"```.*?```|`[^`]*`|https?://\S+", " ", text, flags=re.S)
    blocks = [b for b in re.split(r"\n\s*\n", prose) if b.strip()]
    return bool(blocks) and bool(re.search(r"\?(?=[\s)*_\"'»]|$)", blocks[-1]))


def asks(root):
    """The turns that asked the person something and haven't had an answer: at most the latest one. One asked outside
    the person's active hours keeps `held` (its `held_until`) until those begin, like a gate."""
    out, clock = {}, now()
    for e in read(root, "asks.jsonl"):
        if e["type"] == "ask":
            out[e["id"]] = dict(e)
        elif e["type"] == "answered":
            out.pop(e["of"], None)
        elif e["type"] == "held" and e["of"] in out:            # the person's hours changed: released at the new ones
            out[e["of"]]["held_until"] = e["until"]
    for a in out.values():
        a["held"] = a["held_until"] if a.get("held_until", "") > clock else None
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


def mark_needs_you(root):
    """`colony needs-you`: the turn now running asks the person something."""
    path = Path(root) / ".board" / "needs-you"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(now())


def take_needs_you(root):
    """Whether the turn just ended said it asks the person; said once, for that turn."""
    path = Path(root) / ".board" / "needs-you"
    if not path.exists():
        return False
    path.unlink(missing_ok=True)
    return True


def record_ask(root, key, text, explicit=False):
    """One entry per turn: the turn's whole text. A newer question replaces an unanswered older one. A turn whose
    only questions are open gates restated asks nothing new: the gates already wait on the person."""
    if not text or (not explicit and not asks_question(text)) or any(e.get("key") == key for e in read(root, "asks.jsonl") if key):
        return None
    if not explicit and all(restates_gate(q, root) for q in questions(text) or [text]):
        return None
    answer_asks(root, "superseded by a later turn")
    ask = {"type": "ask", "id": "a" + secrets.token_hex(3), "at": now(), "key": key, "text": text.strip()}
    until = hours.held_until(epoch(ask["at"]))       # asked at night: the person's when their hours begin
    if until:
        ask["held_until"] = until
    append(root, "asks.jsonl", ask)
    return ask


def answer_asks(root, how):
    for a in asks(root):
        append(root, "asks.jsonl", {"type": "answered", "of": a["id"], "at": now(), "how": how})


def answers_ask(n):
    """A note that answers the agent's open question: the person's own words, or their steward's, on the project."""
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
    it is no one's question yet. An item that isn't on the roadmap doesn't hold a question back. One opened outside
    the person's active hours keeps `held` (its `held_until`) until those begin, and then reaches them with the rest.
    `answered_by` is colony where a default stood, the person otherwise."""
    out, later, clock = {}, None, now()
    for e in read(root, "gates.jsonl"):
        if e["type"] == "gate":
            out[e["id"]] = dict(e, answer=None, answered_at=None, point_decisions=None, note_id=None, comment="",
                                cleared=False, answered_by=None)
        elif e["type"] == "answer" and e["of"] in out:
            out[e["of"]].update(answer=e["text"], answered_at=e["at"],
                                point_decisions=e.get("point_decisions"), note_id=e.get("note_id"),
                                comment=e.get("comment", ""), cleared=e.get("cleared", False),
                                answered_by=e.get("author", "person"))
        elif e["type"] == "held" and e["of"] in out:            # the person's hours changed: released at the new ones
            out[e["of"]]["held_until"] = e["until"]
    # one the person replied on is the agent's to take up: off their list until it settles it, asks it again, or
    # finishes with their reply and leaves it as it was (then it is theirs again)
    replied = {(n.get("anchor") or {}).get("gate") for n in notes(root)
               if n.get("author") == "person" and not n.get("addressed_at") and not n.get("reply")}
    for g in out.values():
        g["awaiting"] = g["id"] in replied and not g["answer"]
        if g.get("when"):
            if later is None:
                later = {iid for iid, it in items(roadmap(root)).items() if it["state"] == "todo"}
            g["waits_for"] = g["when"] if g["when"] in later else None
        g["held"] = g["held_until"] if g.get("held_until", "") > clock else None
    return list(out.values())


def due(g):
    """An unanswered gate whose moment has come: the person's question now (not one waiting for its item, nor one
    held for their active hours)."""
    return not g["answer"] and not g.get("waits_for") and not g.get("held") and not g.get("awaiting")


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
            else f"from page {a['page']}" if a.get("page")
            else f"on commit {a['commit']}" if a.get("commit") else "on the whole project")


# A support the steward proved elsewhere is still only a guess about this project's work: the agent doing the
# work knows it best, so it arrives as a suggestion to check, never as the person's word.
SUGGESTION = (" (a suggestion from the steward, not an instruction from the person, and not a new task: it serves the"
              " work you are doing and widens nothing. Check it against what you know of that work; if it fits, ask the"
              " person to install it with colony gate; if not, say why with colony noted)")
# The steward's own idea for the work (colony suggest), a research question say: weighed, never obeyed.
IDEA = (" (the steward's own suggestion, not an instruction from the person and not a new task: weigh it against your"
        " work and what the person asked; take it up only if it is worth doing, within the agreed scope, and answer"
        " with colony noted either way)")
# Research advice colony asked of a fresh model while turbo is on (colony/turbo.py): weighed, never obeyed.
ADVICE = (" (research advice colony asked of a fresh, read-only model apart from you, while your program has spare"
          " capacity: not an instruction from the person and not a new task. Read the report and weigh it against your"
          " work and what the person asked; take up only what is worth doing, within the agreed scope, and answer with"
          " colony noted either way)")


def render_notes(ns, heading):
    if not ns:
        return ""
    lines = [heading]
    for n in ns:
        by = {"monitor": " (from the person's steward, acting for them; never an answer to a decision you put to the person)",
              "suggestion": IDEA if n.get("kind") == "idea" else ADVICE if n.get("kind") == "advice" else SUGGESTION,
              "colony": " (from colony, the harness the person trusts: act on it as theirs)",
              "page": " (sent by a page in the project as the person used it: what they did, not words they wrote)"}.get(n.get("author"), "")
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
    if register and str(root) in reg["hidden"]:      # taken off (closed, removed) inside a projects folder: back on
        show_project(root)
    if register:
        _closed(root, False)                          # reopened: continuation may take it up again
        was = _archived(root, drop=True)
        if was:
            said = f' The person\'s reason then: "{was["why"]}".' if was.get("why") else ""
            add_note(root, None, f"This project was archived on {was['at'][:10]} and is open again.{said} ARCHIVE.md, "
                     "if you wrote one, says where it stood; read it before going on.", author="colony")
    from . import monitor
    monitor.rebrief()                                 # colony's own source joining the board changes the steward's part
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


CLOSE_NOTE = ("The person is closing this project. Land your work now: let what is running finish, helpers included "
              "(don't cancel them), and take in what they hand back; commit what works (a clean point: an unfinished item "
              "stays in progress, so the roadmap says where it stopped); start nothing new. Then end your turn with a short "
              "word on where things stand (done, half-done, next). Once your turn ends with nothing running, colony takes "
              "the project off the board; its files and this conversation stay, and putting the folder back on the board "
              "(colony track) reopens it here.")


def _closing_path():
    return home() / "closing.json"


def closing():
    """The projects being closed, each with the note that told it to land its work."""
    try:
        return json.loads(_closing_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_closing(rows):
    home().mkdir(parents=True, exist_ok=True)
    tmp = _closing_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(rows))
    tmp.replace(_closing_path())


def _closed_path():
    return home() / "closed.json"


def fenced(root):
    """Whether a project is being closed or was closed and not reopened: continuation leaves it alone, so nothing
    sets it going again behind the person's close."""
    try:
        closed = json.loads(_closed_path().read_text())
    except (OSError, ValueError):
        closed = []
    return str(Path(root)) in closing() or str(Path(root)) in closed


def _closed(root, value):
    try:
        closed = set(json.loads(_closed_path().read_text()))
    except (OSError, ValueError):
        closed = set()
    (closed.add if value else closed.discard)(str(Path(root)))
    home().mkdir(parents=True, exist_ok=True)
    _closed_path().write_text(json.dumps(sorted(closed)))


def finish_closing(root):
    """A project that landed its work comes off the board (its close done, not cancelled): files and conversation
    kept, and fenced until it is put back on the board."""
    rows = closing()
    rows.pop(str(Path(root)), None)
    _save_closing(rows)
    _closed(root, True)
    remove_project(root)


def archive_note(why):
    said = f' Their reason, in their words: "{why.strip()}".' if why and why.strip() else ""
    return ("The person is archiving this project." + said + " " + CLOSE_NOTE.replace("The person is closing this project. ", "")
            .replace("putting the folder back on the board (colony track)", "bringing it back from the Archived shelf"))


def _archived(root=None, value=None, drop=False):
    """Projects archived and not yet reopened, with when and the person's reason; reopening tells the agent."""
    path = home() / "archived.json"
    try:
        rows = json.loads(path.read_text())
    except (OSError, ValueError):
        rows = {}
    if root is None:
        return rows
    key = str(Path(root))
    if value is None and not drop:
        return rows.get(key)
    row = rows.pop(key, None) if drop else rows.setdefault(key, value)
    home().mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return row


def close_project(root, cancel=False, why="", archive=False, together=()):
    """Close a project safely: its agent is told to land its work, and once that turn has ended with nothing running,
    the project comes off the board (remove_project), its files and conversation kept. One whose console isn't
    running has nothing in flight, so it comes off at once. Returns "closing", "closed" or "kept" (cancelled)."""
    root, rows = Path(root), closing()
    if cancel:
        _archived(root, drop=True)
        row = rows.pop(str(root), None)
        _save_closing(rows)
        note = row and next((n for n in notes(root) if n["id"] == row["note"]), None)
        if note and not note["delivered_at"]:       # not yet heard: withdrawn, so it never arrives
            append(root, "notes.jsonl", {"type": "addressed", "of": note["id"], "at": now(), "text": "closing cancelled"})
        elif note:
            add_note(root, None, "The person kept this project open after all: closing is cancelled, so carry on as "
                     "before.", author="colony")
        return "kept"
    from . import lead
    shared = lead.group(root)
    if shared and len(shared["members"]) > 1 and not (archive and {str(Path(m).resolve()) for m in shared["members"]} <= set(together)):
        # Its seats share one roadmap and lead record: one alone coming off leaves the others' work and assignments
        # pointing at it. Closing them together is a step of its own, not built.
        raise ValueError(f"{root.name} shares its roadmap with " + ", ".join(Path(m).name for m in shared["members"]
                         if Path(m) != root.resolve()) + "; colony close doesn't close one seat of a shared project")
    if archive:
        _archived(root, {"at": now(), "why": (why or "").strip()})
    # off, or (archiving) sitting idle: nothing in flight, so it comes off at once, asked for nothing; at work, it
    # lands that turn first
    if console.snapshot(root, lines=1)["state"] in (("off", "idle") if archive else ("off",)) and at_rest(root):
        finish_closing(root)
        return "closed"
    row = rows.get(str(root))
    if not row:
        note = add_note(root, None, archive_note(why) if archive else CLOSE_NOTE, author="colony", kind="close")
        rows[str(root)] = {"at": now(), "note": note["id"], "archive": bool(archive)}
        _save_closing(rows)
    return "closing"


CLOSE_SETTLE = 2                    # looks in a row a closing console must read idle, its note handed over, to close


def close_ready(root, state, idle_looks):
    """Whether a closing project has landed its work: its note was handed over and the turn it started has ended,
    nothing running (a console waiting on background helpers reads as working)."""
    row = closing().get(str(root))
    if not row:
        return False
    note = next((n for n in notes(root) if n["id"] == row["note"]), None)
    if not at_rest(root):
        return False                                    # its program still runs something the screen doesn't show
    if note is None or state == "off":
        return True                                     # nothing left to hand over, or nothing running
    return bool(note["delivered_at"]) and state == "idle" and idle_looks >= CLOSE_SETTLE


def at_rest(root):
    """Whether the project's program runs nothing beyond what its screen shows. PROVIDER: at_rest()."""
    from . import providers
    prov = providers.of(root)
    return prov.at_rest(root) if hasattr(prov, "at_rest") else True


def archive_together(roots, why=""):
    """Archive projects at once (a whole group): a shared project's seats go with each other, since they keep one plan.
    Each idle one comes off the board and the steward's watch at once, one at work once it has landed that turn; files
    and conversation kept, until brought back. Returns {name: "closing" | "closed" | the reason it can't}."""
    from . import lead
    on = {str(p.resolve()): p for p in projects()}
    keep = {str(Path(r).resolve()): on[str(Path(r).resolve())] for r in roots if str(Path(r).resolve()) in on}
    for r in list(keep.values()):
        for m in (lead.group(r) or {}).get("members", []):
            if str(Path(m).resolve()) in on:
                keep.setdefault(str(Path(m).resolve()), on[str(Path(m).resolve())])
    out = {}
    for r in keep.values():
        try:
            out[r.name] = close_project(r, why=why, archive=True, together=set(keep))
        except ValueError as err:
            out[r.name] = str(err)
    return out


def archived_off():
    """Projects now off the board that can come back, each with when and why: archived, and those closed or taken off
    before there was an Archived shelf. {path: row}"""
    on = {str(p.resolve()) for p in projects()}
    try:
        closed = json.loads(_closed_path().read_text())
    except (OSError, ValueError):
        closed = []
    out = {k: {"at": "", "why": ""} for k in [*registry()["hidden"], *closed] if Path(k).is_dir()}
    out.update(_archived())
    return {k: v for k, v in out.items() if str(Path(k).resolve()) not in on}


def bring_back(roots):
    """Archived projects back on the board, each where it was (its group, its conversation): a shared project's
    archived seats come back with it. Its agent hears it is open again, and why it was archived."""
    from . import lead
    off = {str(Path(k).resolve()): k for k in archived_off()}
    back = [off[str(Path(r).resolve())] for r in roots if str(Path(r).resolve()) in off]
    for k in list(back):
        back += [off[str(Path(m).resolve())] for m in (lead.group(k) or {}).get("members", [])
                 if str(Path(m).resolve()) in off and off[str(Path(m).resolve())] not in back]
    for k in back:
        if Path(k).is_dir():
            track(Path(k))
    return [Path(k).name for k in back]


def archived_shelf(link=False):
    """The Archived shelf: each group archived as one row, each project archived alone as its own, a tap bringing it
    back. With link, only a line saying what's there, pointing to All projects."""
    from . import nest
    groups = {}
    for k, v in archived_off().items():
        groups.setdefault(nest.nest_of(k) or "", []).append((k, v))
    if not groups:
        return ""
    if link:
        what = [f"{g} ({len(x)})" for g, x in groups.items() if g] + [Path(k).name for k, _ in groups.get("", [])]
        return f"<p class='archline'><a href='/projects#archived'>Archived: {e(', '.join(what))} →</a></p>"
    rows = []
    for name, items in sorted(groups.items(), key=lambda g: (g[0] == "", g[0].lower())):
        singles = [[x] for x in items] if not name else [items]
        for it in singles:
            when = on_day(max(v["at"] for _, v in it)) or "earlier"
            why = next((v["why"] for _, v in it if v.get("why")), "")
            what = (f"<input type='hidden' name='nest' value='{e(name)}'>" if name else
                    f"<input type='hidden' name='path' value='{e(it[0][0])}'>")
            rows.append(f"<div class='archrow'><div><b>{e(name or Path(it[0][0]).name)}</b> <span class='muted'>"
                        + (f"group · {len(it)} project{'s' * (len(it) != 1)} · " if name else "") + f"archived {e(when)}</span>"
                        + (f"<div class='who'>{e(', '.join(Path(k).name for k, _ in it))}</div>" if name else "")
                        + (f"<div class='who'>“{e(why)}”</div>" if why else "")
                        + f"</div><form method='post' action='/bring-back'>{what}<button class='quiet'>Bring back</button></form></div>")
    return (f"<h2 id='archived'>Archived</h2><div class='card'>{''.join(rows)}<p class='muted'>Off the board and the steward's "
            "watch, nothing waking them. Brought back, each is where it was, in the same conversation: ask it anything.</p></div>")


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
    the steward can catch up on direction given without it."""
    append(root, "said.jsonl", {"at": now(), "text": text.strip()[:1000]})


def said_reply(root, text):
    """The agent's answer to the person's last direct words: its closing lines (up to 600 characters), where it
    says what it did, or why it pushed back. Context the steward needs beside their words: an agent may have
    shown a request cut against the project's own principles, and the person changed course."""
    rows = read(root, "said.jsonl")
    if not text.strip() or not rows or "reply" in rows[-1]:
        return                                      # they didn't speak directly, or this turn's answer is kept
    tail = text.strip()
    append(root, "said.jsonl", {"at": now(), "reply": ("…" + tail[-600:]) if len(tail) > 600 else tail})


def add_note(root, anchor, text, *, author, quiet=False, kind=None):
    """A note for the agent. A quiet one reaches it on its next turn like any other, but does not wake it."""
    note = {"type": "note", "id": "n" + secrets.token_hex(3), "at": now(), "author": author,
            "anchor": anchor, "text": text.strip(), **({"quiet": True} if quiet else {}), **({"kind": kind} if kind else {})}
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


def add_gate(root, question, item=None, why="", *, consultation=None, points=None, when=None, default=None, options=(),
             recommended=None, reframes=None, told="", hold=None):
    with gate_lock(root):
        return _add_gate(root, question, item, why, consultation=consultation, points=points, when=when, default=default,
                         options=options, recommended=recommended, reframes=reframes, told=told, hold=hold)


def _add_gate(root, question, item, why, *, consultation, points, when=None, default=None, options=(), recommended=None,
              reframes=None, told="", hold=None):
    """A decision put in the person's hands. A reversible one may carry the default that stands if they skip it
    (stand_defaults); one with consultant points never does, since each point needs their decision."""
    default = (default or "").strip()
    gate = dict(type="gate", id="g" + secrets.token_hex(3), at=now(), question=question.strip(), item=item or when,
                why=why.strip(), **({"when": when} if when else {}), **({"default": default} if default else {}))
    if not gate["question"]:
        raise ValueError("A gate needs its question.")
    if default and (consultation or points is not None):
        raise ValueError("A gate with consultant points has no default: the person decides each point.")
    # the answers the person can tap, the agent's recommended one among them
    options, recommended = [o.strip() for o in options or () if o.strip()], (recommended or "").strip()
    if (options or recommended) and (consultation or points is not None):
        raise ValueError("A gate with consultant points is decided point by point, not by options.")
    if recommended and options and recommended not in options:
        raise ValueError("The recommended answer is one of the options.")
    if options:
        gate["options"] = options
    if reframes:                                     # asked again whole: the round before, and what the agent told them
        gate["reframes"] = reframes
        if (told or "").strip():
            gate["told"] = told.strip()
    if recommended:
        gate["recommended"] = recommended
    if hold:                                         # a connection's call that runs only on their tap (connections)
        gate["hold"] = hold
    until = hours.held_until(epoch(gate["at"]))     # outside the person's active hours: theirs when those begin
    if until:
        gate["held_until"] = until
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
        before = next((g for g in gates(root) if g["id"] == gate_id), None)
        # a held call's outcome is told once, by colony, as it runs or not (connections.answered)
        answer = _answer_gate(root, gate_id, text, tell=tell and not (before or {}).get("hold"), decisions=decisions)
    if tell and before and before.get("hold") and not before["answer"]:
        from . import connections                    # theirs, from the board, the first answer: a held call runs on yes
        connections.answered(root, before, text)
    return answer


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


def withdraw_gate(root, gate_id, why):
    """A gate the agent closes because it no longer applies (superseded, moot): off the person's list, kept on record
    as the agent's, never as their answer."""
    why = why.strip()
    if not why:
        raise ValueError("Say why the gate no longer applies.")
    with gate_lock(root):
        gate = next(g for g in gates(root) if g["id"] == gate_id)
        if gate["answer"]:
            raise ValueError(f"Gate {gate_id} is already answered: {gate['answer']}")
        append(root, "gates.jsonl", dict(type="answer", of=gate_id, at=now(), text=f"Withdrawn by the agent: {why}",
                                         author="agent"))


def open_gates_line(root, cap=160):
    """For the agent when the person speaks to it directly: the gates still open on their list here, so one their
    words settle, or that no longer applies, leaves it rather than being raised to them again."""
    open_ = [g for g in gates(root) if due(g)]
    if not open_:
        return ""
    short = lambda q: (lambda t: t if len(t) <= cap else t[:cap - 1].rstrip() + "…")(" ".join(q.split()))
    return ("Decisions still open on the person's list here; the board and their steward keep raising them until "
            "closed:\n" + "\n".join(f"- {g['id']}: {short(g['question'])}" for g in open_)
            + "\nIf what they say now settles one: colony gate --answered ID \"their words\" --context \"your reading\". "
            "If one no longer applies: colony gate --withdraw ID \"why\".")


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


# ---------------------------------------------------------------- the day's decisions: by number, with defaults

ANSWERING = 3600         # seconds an unsent answer on the page holds back the defaults of the decisions it answers


def due_since(root, g, times=None):
    """When a gate became the person's question: when it was opened, when its hold for their hours ended, or when the
    item it waited for started, whichever came last."""
    t = epoch(g["at"])
    if g.get("held_until"):
        t = max(t, epoch(g["held_until"]))
    if g.get("when"):
        rec = (item_times(root) if times is None else times).get(g["when"]) or {}
        t = max(t, rec.get("first") or rec.get("at") or 0)
    return t


def stands_at(root, g, times=None):
    """When a skipped default stands: as the person's day begins after the first whole day the decision was theirs."""
    return hours.next_day(hours.next_day(due_since(root, g, times) - 1))


def stand_defaults(root, gs=None):
    """A reversible decision the person skipped: a gate with a default, still unanswered as their next day begins
    after a whole day on their page, is answered with it by colony, and its note reaches the agent as their answer.
    Never a gate without a default or with consultant points, nor one they have started answering on the page. The
    gates whose defaults stood."""
    gs = gates(root) if gs is None else gs
    times = item_times(root) if any(g.get("when") and g.get("default") for g in gs) else {}
    clock = epoch(now())
    ripe = {g["id"] for g in gs if g.get("default") and not g.get("points") and not g.get("consult") and due(g)
            and stands_at(root, g, times) <= clock}
    ripe -= answering(root) if ripe else set()
    if ripe:                                         # one the person replied on waits for the agent to settle it
        ripe -= {(n.get("anchor") or {}).get("gate") for n in notes(root) if n.get("author") == "person"}
    stood = []
    if ripe:
        with gate_lock(root):
            for g in gates(root):
                if g["id"] in ripe and not g["answer"]:
                    answer = dict(type="answer", of=g["id"], at=now(), author="colony",
                                  text=f"Default stood (skipped): {g['default']}", note_id="n" + secrets.token_hex(3))
                    append(root, "gates.jsonl", answer)
                    _gate_note(root, g, answer["note_id"], answer["text"], answer["at"], author="colony")
                    stood.append(g)
    return stood


def decisions(root, gs=None):
    """The day's decisions as the person's page numbers them: every gate due now, and every one settled since their
    day began, in the order each became theirs. A settled one keeps its number, so a number means one decision all
    day. [(number, gate)], the numbers as text."""
    gs = gates(root) if gs is None else gs
    if stand_defaults(root, gs):
        gs = gates(root)
    times = item_times(root) if any(g.get("when") for g in gs) else {}
    start, out = hours.day_start(epoch(now())), []
    for g in gs:
        if g.get("waits_for"):
            continue
        since_ = due_since(root, g, times)
        if due(g) or (g["answer"] and start <= epoch(g["answered_at"]) and since_ <= epoch(g["answered_at"])):
            out.append((since_, g))
    # a stable sort: those that became theirs together keep the order they were opened in
    return [(str(k), g) for k, (_, g) in enumerate(sorted(out, key=lambda x: x[0]), 1)]


def draft(root):
    """The person's unsent answers by number on a project's page, kept as they type: {text, numbers, at}, or None."""
    try:
        d = json.loads((Path(root) / ".board" / "answering.json").read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and str(d.get("text") or "").strip() else None


def save_draft(root, text, numbers):
    """Keep what the person has typed so far (numbered as the page they typed on); nothing typed, nothing kept."""
    path = Path(root) / ".board" / "answering.json"
    if not str(text or "").strip():
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"text": text, "numbers": numbers, "at": now()}, ensure_ascii=False))
    tmp.replace(path)


def answering(root):
    """The gates the person has started answering: those their unsent answer speaks to, while typed within the hour."""
    d = draft(root)
    if not d or epoch(now()) - epoch(d["at"]) >= ANSWERING:
        return set()
    numbers = d.get("numbers") or {}
    return {numbers[n] for n in by_number(d["text"], numbers, lenient=True)}


def numbering(value):
    """The numbers a page showed, as its form sends them back ("1:g1a2b3c,2:g4d5e6f"): {number: gate id}."""
    pairs = (p.strip().partition(":") for p in str(value or "").split(","))
    return {n: gid for n, _, gid in pairs if n.isdigit() and re.fullmatch(r"g[0-9a-f]+", gid)}


ANSWER = re.compile(r"\s*(\d{1,3})(?:\s*[.):–—-]+\s*|\s+|$)(.*)", re.S)


def by_number(text, numbers, lenient=False):
    """The person's answers by number, {number: their words}. An answer starts with its decision's number at the start
    of the text, of a line, or after a semicolon ("1 yes; 2 email, not SMS"); with one decision open, words alone
    answer it. ValueError, saying what didn't read; lenient (an unsent draft), whatever reads so far."""
    out, current, lead = {}, None, ""
    pieces = re.split(r"(\n|;)", text or "")
    for k in range(0, len(pieces), 2):
        sep, seg = pieces[k - 1] if k else "", pieces[k]
        m = ANSWER.fullmatch(seg)
        if m and m.group(1) in numbers and m.group(1) not in out:
            current = m.group(1)
            out[current] = m.group(2)
        elif m and not lenient:
            raise ValueError(f"Decision {m.group(1)} is answered twice." if m.group(1) in out else
                             f"{m.group(1)} isn't an open decision here: start a line with a decision's number only.")
        elif current:
            out[current] += sep + seg
        else:
            lead += sep + seg
    if lead.strip():
        if len(numbers) == 1 and not out:
            out[next(iter(numbers))] = lead
        elif not lenient:
            raise ValueError("Start each answer with its decision's number: 1 yes; 2 email, not SMS.")
    out = {n: w.strip() for n, w in out.items()}
    if not lenient:
        for n, w in out.items():
            if not w:
                raise ValueError(f"Decision {n} has its number but no answer.")
            for m in re.findall(r"(?<!\S)(\d{1,3})(?=[\s.):–—-]|$)", w):
                if m in numbers and m not in out:          # "1 yes 2 no" on one line: two answers run together?
                    raise ValueError(f"Your answer to {n} has {m} in it, another decision's number: put each answer "
                                     "on its own line or after a semicolon (1 yes; 2 no).")
    return out


def answer_by_number(root, text, numbers):
    """The person's answers by number, each recorded as its gate's answer (reaching the agent as a note, as from any
    gate); "default" takes a decision's default now. All or none: a ValueError, saying what didn't read, records
    nothing. A default that stood is answered over. Sent, the unsent answer is cleared. [(number, gate)]"""
    words = by_number(text, numbers)
    if not words:
        raise ValueError("Nothing to send: answer by number, as 1 yes; 2 email.")
    current, todo = {g["id"]: g for g in gates(root)}, []
    for n, w in words.items():
        g = current.get(numbers[n])
        if not g:
            raise ValueError(f"Decision {n} isn't in this project.")
        if g.get("points"):
            raise ValueError(f"Decision {n} has consultant points: choose on each point instead.")
        if re.fullmatch(r"(the\s+)?default[.!]?", w, re.I):
            if not g.get("default"):
                raise ValueError(f"Decision {n} has no default: say what you'd like.")
            if g["answered_by"] == "colony":
                continue                                    # it stood already
            w = f"Default accepted: {g['default']}"
        if g["answer"] and g["answered_by"] != "colony":
            if g["answer"] == w:
                continue                                    # sent twice
            raise ValueError(f"Decision {n} was answered meanwhile: {g['answer']}")
        todo.append((n, g, w))
    for n, g, w in todo:
        answer_gate(root, g["id"], w)
    save_draft(root, "", {})
    return [(n, g) for n, g, _ in todo]


def restamp_held():
    """The person changed their hours: what is held for them is released as the new hours next begin (at once, if
    those hold now or are off)."""
    until = hours.held_until(epoch(now())) or now()
    for root in projects():
        if not (root / ".board").is_dir():
            continue
        with gate_lock(root):
            for g in gates(root):
                if g.get("held") and not g["answer"] and g["held_until"] != until:
                    append(root, "gates.jsonl", {"type": "held", "of": g["id"], "at": now(), "until": until})
        for a in asks(root):
            if a.get("held") and a["held_until"] != until:
                append(root, "asks.jsonl", {"type": "held", "of": a["id"], "at": now(), "until": until})


def open_notes(root, item=None):
    """Notes the agent has not yet acted on. Without an item: everything except notes on roadmap items
    not yet started, which wait until the agent reaches them. A default whose moment has come stands first, so its
    answer is among them: the watcher looks here for every project every few seconds."""
    stand_defaults(root)
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
    """Commits (the newest 40, and how many in all; a merge is not news of its own), roadmap moves, gates and replies
    since the person last looked."""
    head = git(root, "rev-parse", "HEAD").strip()
    base, at = (seen or {}).get("head"), (seen or {}).get("at", "")
    rng = f"{base}..HEAD" if base else "HEAD"
    commits = [l.split("\x1f") for l in git(root, "log", "-n", "40", "--no-merges", "--format=%h\x1f%aI\x1f%s", rng).splitlines() if l]
    count = git(root, "rev-list", "--count", "--no-merges", rng).strip()
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
    return {"head": head, "commits": commits, "count": int(count) if count.isdigit() else len(commits), "moved": moved,
            "moved_at": moved_at, "opened": opened, "replies": replies, "pinned": pinned, "first": not seen}


def page_base(root, mark=True):
    """Where a project's page for the day starts what landed (and its Overview, what changed): the person's last look at
    it on an earlier day (when, and the commit it stood at), or on a first look, the start of the day before. Each look
    (at either page) is kept on this machine, with the board: the pages read the same all day, and turn over with the
    person's day."""
    path = home() / "today.json"
    with board_lock("today") if mark else nullcontext():
        try:
            every = json.loads(path.read_text())
        except (OSError, ValueError):
            every = {}
        rec, day = every.get(str(root)) or {}, hours.day_start(epoch(now()))
        board_time = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        if rec.get("day") != board_time(day):
            before = hours.day_start(day - 1)
            rec = {"day": board_time(day), "from": rec.get("last") or {
                "at": board_time(before), "head": git(root, "rev-list", "-1", f"--before=@{int(before)}", "HEAD").strip()}}
        if mark:
            rec["last"] = {"at": now(), "head": git(root, "rev-parse", "HEAD").strip()}
            every[str(root)] = rec
            save_json(path, every)
    return rec["from"]


@contextmanager
def board_lock(name):
    """One writer at a time to one of the board's own files: read, change and write it as one step, so two pages
    served at once never lose each other's change."""
    import fcntl
    home().mkdir(parents=True, exist_ok=True)
    with open(home() / f"{name}.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def stamp(iso):
    """A git date (with its own offset) on the board's clock (UTC, "...Z"), so events from both sort together."""
    from datetime import datetime, timezone
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return iso


def on_day(at):
    """A board time as a list on the page dates it: the clock time if it was today, else the day (Oct 17)."""
    try:
        t = epoch(at)
    except (TypeError, ValueError):
        return ""
    then, today = time.localtime(t), time.localtime(epoch(now()))
    if then[:3] == today[:3]:
        return hours.at_clock(t)
    return time.strftime("%b %-d" if then.tm_year == today.tm_year else "%b %-d %Y", then)


# ---------------------------------------------------------------- the page

def e(text):
    return html.escape(str(text), quote=True)


STEER = {"next": "Do this next.", "later": "Leave this for later."}


def steer_row(pid, it):
    """Steering from the plan, one tap: do this item next, or leave it for later. Each reaches the agent as the
    person's note on the item, and waits among their notes not yet acted on until the agent has."""
    if it["state"] not in ("todo", "doing"):
        return ""
    back = f"/?p={pid}&view=roadmap"
    form = lambda text, label: (f"<form method='post' action='/note'><input type='hidden' name='p' value='{pid}'>"
                                f"<input type='hidden' name='back' value='{e(back)}'><input type='hidden' name='kind' value='item'>"
                                f"<input type='hidden' name='ref' value='{e(it['id'])}'><input type='hidden' name='text' value='{e(text)}'>"
                                f"<button class='quiet'>{label}</button></form>")
    return ("<div class='steer'>" + (form(STEER["next"], "Do this next") if it["state"] == "todo" else "")
            + form(STEER["later"], "Leave for later") + "</div>")


def note_box(pid, kind, ref, hint, back=None):
    back = back or f"/?p={pid}"
    return (f"<form class='add' method='post' action='/note'><input type='hidden' name='p' value='{pid}'>"
            f"<input type='hidden' name='back' value='{e(back)}'>"
            f"<input type='hidden' name='kind' value='{e(kind)}'><input type='hidden' name='ref' value='{e(ref)}'>"
            f"<textarea name='text' placeholder='{e(hint)}'></textarea><button>Leave note</button></form>")


def thread(ns, road_items=None):
    out = ""
    for n in ns:
        who = {'monitor': 'the steward, for you',
               'suggestion': 'research advice' if n.get('kind') == 'advice' else 'the steward suggests',
               'observation': 'file change observed', 'colony': 'colony', 'page': 'a page you used'}.get(n.get('author'), 'you')
        out += (f"<div class='note'><span class='who'>{who} · {e(n['at'][:10])}</span><div>{e(n['text'])}</div>"
                + (f"<div class='reply'><span class='who'>agent · {e(n['addressed_at'][:10])}</span>"
                   f"<div>{e(n['reply'])}</div></div>" if n["addressed_at"]
                   else f"<div class='who'>{e(status(n, road_items or {}))}</div>")
                + "</div>")
    return out


PINNED = "\x01pinned"                   # the group of the projects the person pinned, above every other


def pinned_projects():
    return registry()["settings"].get("pinned_projects") or []


def arranged(plist, theirs=True):
    """(index, project, group) in the board's order: the projects the person pinned first (group PINNED), then each
    nest's, then the rest; groups as the person moved them, else by activity, and within each, the project that last
    went from idle to working or took up a roadmap item first. The group is "" for a project in no nest, or None
    when nothing is pinned or nested. Without theirs (the top menu), groups follow activity alone, the pinned still first.
    By activity, those at work now come first (a nest whole, for any one of its projects), each where it came in. In
    the top menu a project in no nest moves alone (its Other projects heading is the lists' alone): as one block, any
    of them stirring took all of them ahead of a nest, which then fell last."""
    from . import nest
    homes = {p: n for n, v in nest.nests().items() for p in v.get("members", [])}
    pins = set(pinned_projects())
    rec, busy = stirred(), at_work_now()
    grp = lambda p: PINNED if str(p.resolve()) in pins else homes.get(str(p.resolve()), "")
    # what moves together: a nest, the pinned, and (in the lists) Other projects; in the top menu, each of those alone
    moves = lambda p: grp(p) or ("" if theirs else "\0" + str(p.resolve()))
    best, hot = {}, set()
    for p in plist:
        best[moves(p)] = max(best.get(moves(p), ""), rec.get(str(p.resolve()), ""))
        if str(p.resolve()) in busy:
            hot.add(moves(p))
    # the person's own order of the groups, where they've moved them ("" is Other projects); the rest after, by activity
    mine = [PINNED] + ((registry()["settings"].get("nest_order") or []) if theirs else [])
    place = lambda g: -mine.index(g) if g in mine else -len(mine)
    grouped = bool(homes or any(grp(p) == PINNED for p in plist))
    return [(i, p, grp(p) if grouped else None) for i, p in sorted(enumerate(plist), reverse=True,
            key=lambda ip: (place(grp(ip[1])), moves(ip[1]) in hot, best[moves(ip[1])], moves(ip[1]),
                            str(ip[1].resolve()) in busy, rec.get(str(ip[1].resolve()), ""), -ip[0]))]


def nest_form(pid, root):
    """Put the project in a nest, or take it out: the nests there are, listed to tap, and a box for a new one."""
    from . import nest
    hidden, here, every = f"<input type='hidden' name='p' value='{pid}'>", nest.nest_of(root), nest.nests()
    others = [n for n in every if n != here]
    pick = (f"<form method='post' action='/nest/join' class='nestpick'>{hidden}"
            + "".join(f"<button class='quiet' name='name' value='{e(n)}'>{e(n)} <span class='muted'>"
                      f"{len(every[n].get('members', []))}</span></button>" for n in others) + "</form>") if others else ""
    if here:
        return (f"<form method='post' action='/nest/leave'>{hidden}In the <a href='/nest?n={urllib.parse.quote(here)}'>"
                f"{e(here)} nest</a> <button class='quiet'>Take it out</button></form>"
                + (f"<p class='muted'>Move it to another nest:</p>{pick}" if pick else ""))
    return ((f"<p class='muted'>Join a nest, with projects that share its purpose:</p>{pick}" if pick else "")
            + f"<form method='post' action='/nest/join'>{hidden}<input name='name' "
            f"placeholder='{'Or a new nest' if pick else 'A nest: projects that share a purpose'}'>"
            "<button class='quiet'>Start a new nest</button></form>")


def project_row(i, p, sub=""):
    """One project in a list (the steward's, All projects, a nest's), one design everywhere, the whole row a link to
    it: its state, its name, what it is at, what waits on the person; and under it, given one, what it is for. The
    board's poll keeps the state, the line and the count current."""
    snap = console.snapshot(p, lines=0) if p.exists() else {"state": "off"}
    waiting = len(moments(p)) if p.exists() else 0
    st = snap["state"]
    from . import monitor
    out = p.exists() and not monitor.involved(p)
    pinned = str(p.resolve()) in pinned_projects()
    return (f"<a class='prow{' out' if out else ''}' href='/?p={i}'{' title=' + chr(39) + 'the steward leaves it out' + chr(39) if out else ''}><span class='prowline'>"
            f"<span class='sdot msdot {st.replace(' ', '-')}' data-p='{i}' title='{e(st)}'></span><b>{e(p.name)}</b>"
            f"<span class='pwork' data-p='{i}'>{e(at_work(p) if p.exists() else '')}</span>"
            f"<span class='badge gate pbadge' data-p='{i}' title='waiting on you'{'' if waiting else ' hidden'}>{waiting}</span>"
            f"<span class='pinbtn{' on' if pinned else ''}' role='button' onclick='return pinProject(event, {i}, {0 if pinned else 1})' "
            f"title='{'Unpin' if pinned else 'Pin to the top'}' aria-label='{'Unpin' if pinned else 'Pin to the top'}'>📌</span></span>"
            + (f"<span class='psub'>{e(sub)}</span>" if sub else "") + "</a>")


def project_list(plist, sub=None, fold="list"):
    """Projects in the board's order, each nest's under its name, folding open or shut (remembered on this device,
    open at first) when there are nests."""
    out, heading = [], None
    for i, p, here in arranged(plist):
        if here != heading and here is not None:
            if heading is not None:
                out.append("</div></div>")
            # the whole heading folds the group; the arrow just after its name opens the nest's page; the grip at the
            # far right moves it (the pinned stay on top)
            nested = here and here != PINNED
            page = (f"<a class='nestgo' href='/nest?n={urllib.parse.quote(here)}' onclick='event.stopPropagation()' "
                    f"aria-label='The {e(here)} nest page'>→</a>" if nested else "")
            name = "Pinned" if here == PINNED else here + " nest" if here else "Other projects"
            grip = ("" if here == PINNED else
                    "<span class='grip' title='Drag to move this group' aria-label='Drag to move this group'>⠿</span>")
            attrs = "class='nestgroup pinned'" if here == PINNED else f"class='nestgroup' data-nest='{e(here)}'"
            out.append(f"<div {attrs} data-fold='{e(fold)}:{e('*pinned' if here == PINNED else here or '-')}' data-open='1'>"
                       f"<h3 class='listhead' onclick='toggleFold(this.closest(\".nestgroup\"))'>"
                       f"<span class='foldbtn' aria-hidden='true'></span><span class='nestname'>{e(name)}</span>{page}"
                       f"<span class='headgap'></span>{grip}</h3><div class='members'>")
        heading = here
        out.append(project_row(i, p, sub(p) if sub and p.exists() else ""))
    if heading is not None:
        out.append("</div></div>")
    return "".join(out)


def home_list(reg):
    """Every project, those waiting on the person first: each its line (state, what it is at), its measures, and
    under it what waits on the person, answered right there; then the quiet ones, a line each. And colony's own
    asks of them (model choices, the steward's supports) above all."""
    plist = projects(reg)
    choices = model_changes(pending_only=True)
    waiting, quiet = [], []
    for i, p, _ in arranged(plist):
        if not p.exists():
            continue
        decided, box, others = waiting_parts(i, p, "/", label=False, day=True)
        needs = "".join(decided) + box + "".join(others)
        if needs:
            waiting.append(f"<section class='hproj'>{project_row(i, p)}{measures_html(p)}<div class='hneeds'>{needs}</div></section>")
        else:
            quiet.append(f"<section class='hproj quiet'>{project_row(i, p)}{measures_html(p)}</section>")
    top = "".join([choices] if choices else []) + "".join(support_rows())
    return (top + "".join(waiting)
            + (f"<h2>Nothing waiting on you</h2>" if waiting and quiet else "") + "".join(quiet)
            + ("" if plist else "<p class='muted'>No projects yet.</p>"))


def home_page(reg):
    """The board's front page: every project and what each needs of the person, answered where it shows."""
    return shell(reg, -2, "<h1>Projects</h1><div id='home'>" + home_list(reg) + "</div>"
                 f"<script>{KEEP_CURRENT}keepCurrent(document.getElementById('home'), '/home/feed');</script>")


def all_projects_page(reg):
    """Every project at a glance, under its nest: what it's doing, what waits on the person, what it's for."""
    plist = projects(reg)
    goal = lambda p: first_sentence_of(roadmap(p)["vision"] or roadmap(p)["goal"])
    return shell(reg, -2, "<h1>All projects</h1>" + (f"<div class='card plist'>{project_list(plist, goal)}</div>" if plist
                                                      else "<p class='muted'>No projects yet.</p>") + archived_shelf())


def woke(root):
    """A project just went from idle to working: it comes to the front of the board's list."""
    path = home() / "woke.json"
    try:
        rows = json.loads(path.read_text())
    except (OSError, ValueError):
        rows = {}
    rows[str(Path(root).resolve())] = now()
    save_json(path, rows)


def woken():
    try:
        return json.loads((home() / "woke.json").read_text())
    except (OSError, ValueError):
        return {}


def working_now(roots):
    """The projects at work, as the steward's watch saw them just now: they lead the board's lists. Written only when
    it changes, and once a minute besides, so a watch that has stopped is not believed for long."""
    path, rows = home() / "working.json", sorted(str(Path(r).resolve()) for r in roots)
    try:
        was = json.loads(path.read_text())
    except (OSError, ValueError):
        was = {}
    if was.get("roots") != rows or time.time() - was.get("at", 0) > 60:
        save_json(path, {"at": time.time(), "roots": rows})


def at_work_now():
    try:
        was = json.loads((home() / "working.json").read_text())
    except (OSError, ValueError):
        return set()
    return set(was.get("roots") or []) if time.time() - was.get("at", 0) < 180 else set()


def stirred():
    """When each project last stirred: went from idle to working, or took up a roadmap item, whichever came later."""
    out = dict(woken())
    try:
        every = json.loads((home() / "items.json").read_text())
    except (OSError, ValueError):
        every = {}
    for root, recs in every.items():
        took_up = max((r["started"] for r in recs.values() if isinstance(r, dict) and r.get("started")), default=0)
        if took_up:
            at, key = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(took_up)), str(Path(root).resolve())
            out[key] = max(out.get(key, ""), at)
    return out


def at_work(root, n=70):
    """What a project is at, in a few words from its roadmap: the item in progress (and how many more are), else the
    next one to come outside Later. Sharing a plan, an agent's own items in progress; a helper with none, nothing."""
    from . import lead
    road = roadmap(root)
    its = [i for m in road["milestones"] for i in m["items"]]
    doing = [i for i in its if i["state"] == "doing"]
    g, mine = lead.group(root), str(Path(root).resolve())
    if g and len(g["members"]) > 1:
        own = [i for i in doing if i.get("owner") == mine]
        if own or mine != g["lead"]:
            doing = own
            if not own:
                return ""
    if doing:
        return f"{doing[0]['id']} {short(plain(doing[0]['text']), n)}" + (f" +{len(doing) - 1}" if len(doing) > 1 else "")
    later = lambda m: re.match(r"later\b", (m["title"] or m["id"]).strip(), re.I)
    nxt = next((i for m in road["milestones"] if not later(m) for i in m["items"] if i["state"] == "todo"), None)
    return f"next {nxt['id']} {short(plain(nxt['text']), n)}" if nxt else ""


def nav_groups(plist):
    """The top menu's order, [(group, [(index, project)])]: it follows activity, a nest coming forward whole, led by
    the project that moved it."""
    groups = []
    for i, p, here in arranged(plist, theirs=False):
        if here and groups and groups[-1][0] == here:
            groups[-1][1].append((i, p))
        else:
            groups.append((here, [(i, p)]))
    return groups


def nav_order(plist):
    """nav_groups as the poll sends it, so an open page moves its chips when a project stirs: ["p", index] for a
    chip of its own, ["n", nest, [index, ...]] for a nest's."""
    out = []
    for here, members in nav_groups(plist):
        if not here or here == PINNED:
            out += [["p", i] for i, _ in members]
        else:
            out.append(["n", here, [i for i, _ in members]])
    return out


def sidebar(reg, pid):
    from . import monitor
    plist = projects(reg)
    ms = monitor.snapshot()
    helm = "at the helm" if monitor.helm() else "helm with you"
    side, total = [], 0
    for here, members in nav_groups(plist):
        chips, states, waits = [], [], 0
        for i, p in members:
            # its screen read once: its dot, and what waits on the person
            snap = console.snapshot(p, lines=4) if p.exists() else {"state": "off", "lines": []}
            waiting = len(moments(p, snap)) if p.exists() else 0
            total, waits = total + (waiting if p.exists() and monitor.involved(p) else 0), waits + waiting
            badge = f"<span class='badge gate' id='badge-{i}' title='waiting on you'{'' if waiting else ' hidden'}>{waiting}</span>"
            states.append(snap["state"])
            # its line says what it is at, from its roadmap: never its console's footer or spinner words
            work = at_work(p) if p.exists() else ""
            tint = PALETTE.get(colour(p)) if p.exists() else None
            tint = f" style='--pc:{tint[0]};--pcd:{tint[1]}'" if tint else ""
            chips.append(f"<a class='proj{' on' if i == pid else ''}'{tint} href='/?p={i}'><span class='pname'>"
                         f"<span class='sdot {snap['state'].replace(' ', '-')}' id='dot-{i}'></span>{e(p.name)}{badge}</span>"
                         f"<span class='sline' id='sline-{i}'>{e(snap['state'] if snap['state'] != 'off' else '')}"
                         f"{' · ' + e(work) if work and snap['state'] != 'off' else e(work)}</span></a>")
        if not here or here == PINNED:
            side += [c.replace("class='proj", "class='proj pinchip", 1) for c in chips] if here else chips
            continue
        # a nest: one chip that opens to its projects (remembered on this device); open while one of them is shown
        state = next((s for s in ("needs you", "working", "idle") if s in states), "off")
        mine = any(i == pid for i, _ in members)
        q = urllib.parse.quote(here)
        side.append(f"<span class='nestgroup' data-fold='nav:{e(here)}' data-open='{1 if mine else 0}'{' data-pin=1' if mine else ''}>"
                    f"<a class='proj nestchip' href='/nest?n={q}' onclick='return toggleFold(this.parentNode)'>"
                    f"<span class='pname'><span class='sdot {state.replace(' ', '-')}'></span>{e(here)} <span class='nestcount'>"
                    f"{len(members)}</span><span class='badge gate'{'' if waits else ' hidden'}>{waits}</span></span>"
                    f"<span class='sline'>nest <span class='nestopen' onclick=\"event.stopPropagation(); colonyNav.go('/nest?n={q}')\">"
                    f"→</span></span></a><span class='members'>{''.join(chips)}<a class='proj nestset' href='/nest?n={q}' "
                    f"title='{e(here)}: its page' aria-label='{e(here)}: its page'>⚙&#xFE0E;</a></span></span>")
    # the steward first, with what waits on the person in every project: on a phone it stays in reach at the left
    side.insert(0, f"<a class='proj monitor{' on' if pid == -1 else ''}' href='/steward'><span class='pname'>"
                   f"<span class='sdot {ms['state'].replace(' ', '-')}' id='dot-m'></span>steward"
                   f"<span id='badge-all' class='badge gate' title='waiting on you, in the projects the steward follows'{'' if total else ' hidden'}>{total}</span></span>"
                   f"<span class='sline'>{e(ms['state'] if ms['state'] != 'off' else 'not running')} · {helm}</span></a>")
    from . import providers as pv, usage
    for k, prov in pv.PROVIDERS.items():
        ws = usage.read(k) if pv.usable(prov) else {}
        if ws:
            side.append(f"<div class='sline long' style='padding:2px 10px'>{e(prov.label)}: "
                        + " · ".join(f"{e(w)} {v['used']:g}%" for w, v in sorted(ws.items(), key=lambda x: x[0] != "weekly")) + "</div>")
    side.append("<div class='navfoot'><a href='/projects' title='All projects'><span class='long'>All projects</span><span class='short'>☰</span></a>"
                "<a href='/add' title='Add project'><span class='long'>+ Add project</span><span class='short'>+</span></a>"
                "<a href='/connections' title='Connections'><span class='long'>Connections</span><span class='short'>⇄</span></a>"
                "<a href='/models' title='Models'><span class='long'>Models</span><span class='short'>ⓘ</span></a>"
                "<a href='/settings' title='Settings'><span class='long'>Settings</span><span class='short'>⚙&#xFE0E;</span></a></div>")
    # on a phone the steward is a bar of its own above the projects' row, which scrolls alone
    # scrolled past the pinned chips, a 📌 floats at the row's left: a tap brings them back
    return (side[0] + "<div class='navrow'>" + "".join(side[1:]) + "</div>"
            + "<button type='button' class='pinjump' hidden aria-label='Back to the pinned'>📌</button>")


def own_colour(reg, pid):
    """A project's page in its own colour, light and dark."""
    plist = projects(reg)
    c = PALETTE.get(colour(plist[pid])) if 0 <= pid < len(plist) else None
    return (f":root {{ --accent:{c[0]} }} @media (prefers-color-scheme: dark) {{ :root {{ --accent:{c[1]} }} }}" if c else "")


def css_version():
    """The board's styling, as one file a phone keeps: fetched once per version, never again with every page."""
    import hashlib
    return hashlib.sha1(CSS.encode()).hexdigest()[:10]


def shell(reg, pid, body, wide=False):
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<title>Projects — board</title><link rel='stylesheet' href='/static/board.css?v={css_version()}'>"
            f"<style>{own_colour(reg, pid)}</style></head><body{proj_attr(reg, pid)}><script>{PLAYER.replace('@ICONS@', json.dumps(ICONS))}</script><nav{'' if pid >= 0 else ' class=noproj'}>{sidebar(reg, pid)}</nav>"
            f"<main{' class=wide' if wide else ''}>{body}</main><script>{FOLDS}{GRIPS}{POLL}{CHIPS}{DRAFTS}{ANSWERS}{LASTPAGE}{BAR}</script></body></html>")


# Closed and opened again, the board comes back to the page the person was last on. A fresh open lands on the front
# page (the steward's); a page opened in the same tab, or by a link to it, stays as it is.
def proj_attr(reg, pid):
    """The project a page belongs to, by name, for the page's own scripts (its place in the list can change)."""
    plist = projects(reg)
    return f" data-project='{e(Path(plist[pid]).name)}'" if 0 <= pid < len(plist) else ""


# The board opened afresh goes back to the page last open on this device; and going to a project (its chip, a link to
# it) goes back to the page last open in that project, unless you came from inside it ("← plants" is its Overview).
LASTPAGE = """
(() => {
  try {
    const here = location.pathname + location.search, fresh = !sessionStorage.getItem('open');
    sessionStorage.setItem('open', '1');
    const was = localStorage.getItem('lastpage');
    if (fresh && was && was !== here && ['/', '/steward'].includes(here)) return location.replace(was);
    const proj = document.body.dataset.project, q = new URLSearchParams(location.search);
    if (proj && location.pathname === '/' && [...q.keys()].join() === 'p') {
      const last = localStorage.getItem('page:' + proj);
      let from = null; try { const r = window.colonyFrom || document.referrer; from = r ? new URL(r) : null; } catch (e) {}
      const inside = from && from.origin === location.origin && from.searchParams.get('p') === q.get('p');
      if (last && !inside) { const to = new URL(last, location.href); to.searchParams.set('p', q.get('p'));
        if (to.pathname + to.search !== here) return window.colonyNav ? colonyNav.replace(to.pathname + to.search) : location.replace(to.pathname + to.search); }
    }
    if (proj) localStorage.setItem('page:' + proj, here);
    localStorage.setItem('lastpage', here);
  } catch (e) {}
})();
"""


# On a phone the tab bar sits on the bottom edge. An iPhone can go on placing it as though the keyboard were still up
# after it has gone, and the bar then floats a keyboard's height up the screen with the page showing under it (the
# person's screenshot, Oct 22). So wherever the visible part of the page ends, the bar is moved to it; and when a box
# loses the keyboard, the page is nudged a pixel and back, which makes the phone place fixed things afresh. A gap it
# had to close goes to the board's log, a few a page, so the phone's own numbers say whether this holds.
BAR = """
(() => {
  const vv = window.visualViewport, bar = document.querySelector('.tabs');
  if (!vv || !bar) return;
  let shift = 0, told = 0, queued = 0;
  const seat = () => { queued = 0;
    if (getComputedStyle(bar).position !== 'fixed' || document.body.classList.contains('typing') || vv.scale > 1.01) {
      if (shift) { shift = 0; bar.style.transform = ''; } return; }
    // only a keyboard's worth down: the few pixels a toolbar folding away leaves are the phone's to place
    const base = bar.getBoundingClientRect().bottom - shift, gap = Math.round(vv.offsetTop + vv.height - base);
    // never more than a screen: mid-change a phone reports a page-long gap (41515px), which would send the bar off it
    const want = gap > 60 && gap < innerHeight ? gap : 0;
    if (Math.abs(want - shift) < 2) return;
    shift = want; bar.style.transform = shift ? 'translateY(' + shift + 'px)' : '';
    if (shift && told++ < 3) fetch('/chat/measure', {method: 'POST', body: new URLSearchParams({m: JSON.stringify({when: 'bar',
      shift, inner: innerHeight, client: document.documentElement.clientHeight, vvh: vv.height, vvtop: vv.offsetTop,
      scroll: scrollY, page: document.body.scrollHeight, path: location.pathname + location.search})})}).catch(() => {});
  };
  const soon = () => { if (!queued) queued = requestAnimationFrame(seat); };
  vv.addEventListener('resize', soon); vv.addEventListener('scroll', soon);
  window.addEventListener('scroll', soon, {passive: true}); window.addEventListener('pageshow', soon);
  document.addEventListener('focusout', (ev) => { if (!ev.target.matches('input, textarea, select')) return;
    setTimeout(() => { const y = scrollY; scrollTo(scrollX, y > 0 ? y - 1 : y + 1); scrollTo(scrollX, y); soon(); }, 350); });
  soon();
})();
"""

# The board's player (R108): reading aloud lives in the page's frame, not in a chat, with a bar over every page; and while
# something is being read, a link or a form changes the page in place, so the voice goes on. At any other time pages
# load as they always have. Before the page's own scripts, which find it there.
PLAYER = """
(() => {
  if (window.colonyNav) return;
  // A page's own listeners on the window and the document, and its timers, are noted as it adds them, so when the page
  // changes in place the old one's go with it; the player's are added past this and stay.
  const O = {add: EventTarget.prototype.addEventListener, rm: EventTarget.prototype.removeEventListener,
    si: window.setInterval, st: window.setTimeout, ci: window.clearInterval, ct: window.clearTimeout};
  const fresh = () => ({ls: [], is: new Set(), ts: new Set()});
  let scope = fresh();
  const noted = (x) => x === window || x === document || x === window.visualViewport;
  EventTarget.prototype.addEventListener = function (t, f, o) { if (noted(this)) scope.ls.push([this, t, f, o]); return O.add.call(this, t, f, o); };
  window.setInterval = function (f, ms, ...a) { const id = O.si.call(window, f, ms, ...a); scope.is.add(id); return id; };
  window.setTimeout = function (f, ms, ...a) { const s = scope; let id = 0;
    id = O.st.call(window, typeof f === 'function' ? (...b) => { s.ts.delete(id); return f.apply(window, b); } : f, ms, ...a);
    s.ts.add(id); return id; };
  const leave = () => { const s = scope; scope = fresh();
    s.ls.forEach(([x, t, f, o]) => O.rm.call(x, t, f, o)); s.is.forEach((id) => O.ci.call(window, id)); s.ts.forEach((id) => O.ct.call(window, id)); };
  const busy = () => !!(window.colonyPlayer && window.colonyPlayer.busy());
  // The page changes in place: the new one's body comes in, the player stays, and its scripts run as they would have.
  let going = 0;
  const go = async (url, how = {}) => {
    const mine = ++going, method = (how.method || 'GET').toUpperCase(), root = document.documentElement;
    root.classList.add('going');                   // on its way: the page dims, so a tap is seen to have landed
    let r = null;
    try { r = await fetch(url, {method, body: how.body, credentials: 'same-origin'}); } catch (e) {}
    if (mine !== going) return;
    root.classList.remove('going');
    if (!r) { if (method === 'GET') location.href = url; return; }
    if (!(r.headers.get('content-type') || '').includes('text/html')) { if (method === 'GET') location.href = r.url; return; }
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    if (mine !== going) return;
    if (!doc.querySelector('body > main')) { if (method === 'GET') location.href = r.url; return; }
    const from = location.href, to = method !== 'GET' && !r.redirected ? location.href : r.url;
    leave(); if (window.colonyPlayer) window.colonyPlayer.leave();
    try {
      if (how.push !== false) history.replaceState(Object.assign({}, history.state, {colony: 1, y: scrollY}), '');
      if (how.replace) history.replaceState({colony: 1}, '', to); else if (how.push !== false) history.pushState({colony: 1}, '', to);
    } catch (e) {}
    document.title = doc.title;
    const st = document.head.querySelector('style'), nst = doc.head.querySelector('style');
    if (st && nst) st.textContent = nst.textContent;
    const css = document.head.querySelector('link[rel=stylesheet]'), ncss = doc.head.querySelector('link[rel=stylesheet]');
    if (css && ncss && css.getAttribute('href') !== ncss.getAttribute('href')) css.setAttribute('href', ncss.getAttribute('href'));
    [...document.body.attributes].forEach((a) => document.body.removeAttribute(a.name));
    [...doc.body.attributes].forEach((a) => document.body.setAttribute(a.name, a.value));
    const bar = document.getElementById('cplayer');
    document.body.replaceChildren(...[...doc.body.childNodes].map((n) => document.adoptNode(n)));
    if (bar) document.body.appendChild(bar);
    window.colonyFrom = from;
    scrollTo(0, how.y || 0);
    // each script runs in a block of its own: a name it declares can be declared again by the next page
    document.body.querySelectorAll('script').forEach((old) => { const s = document.createElement('script');
      s.textContent = '{\\n' + old.textContent + '\\n}'; old.replaceWith(s); });
    if (window.colonyPlayer) window.colonyPlayer.placed();
  };
  O.add.call(window, 'popstate', (ev) => { if (ev.state && ev.state.colony) go(location.href, {push: false, y: ev.state.y}); });
  // While something is being read, a link or a form on the board changes the page in place, so the voice goes on
  O.add.call(window, 'click', (ev) => {
    if (ev.defaultPrevented || ev.button || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || !busy()) return;
    const a = ev.target.closest && ev.target.closest('a[href]');
    if (!a || (a.target && a.target !== '_self') || a.hasAttribute('download')) return;
    const u = new URL(a.href, location.href);
    if (u.origin !== location.origin || /\\.[a-z0-9]{1,5}$/i.test(u.pathname) || /^\\/(static|voice|chat\\/file|pins\\/file|file)\\b/.test(u.pathname)) return;
    if (u.pathname + u.search === location.pathname + location.search && u.hash) return;
    ev.preventDefault(); go(u.href);
  });
  O.add.call(window, 'submit', (ev) => {
    if (ev.defaultPrevented || !busy()) return;
    const f = ev.target, b = ev.submitter;
    const target = (b && b.getAttribute('formtarget')) || f.getAttribute('target');
    if (target && target !== '_self') return;
    const action = new URL((b && b.getAttribute('formaction')) || f.getAttribute('action') || location.href, location.href);
    if (action.origin !== location.origin) return;
    const method = ((b && b.getAttribute('formmethod')) || f.getAttribute('method') || 'get').toUpperCase();
    const multi = ((b && b.getAttribute('formenctype')) || f.enctype) === 'multipart/form-data';
    let d; try { d = b ? new FormData(f, b) : new FormData(f); } catch (e) { d = new FormData(f); if (b && b.name) d.append(b.name, b.value); }
    ev.preventDefault();
    if (method === 'GET') { action.search = new URLSearchParams(d).toString(); go(action.href); }
    else go(action.href, {method, body: multi ? d : new URLSearchParams(d)});
  });
  window.colonyNav = {keep: O, go: (u) => busy() ? go(new URL(u, location.href).href) : (location.href = u),
    replace: (u) => busy() ? go(new URL(u, location.href).href, {replace: true}) : location.replace(u),
    reload: () => busy() ? go(location.href, {replace: true}) : location.reload()};
})();
(() => {
  if (window.colonyPlayer || !window.colonyNav) return;
  const O = colonyNav.keep, on = (x, t, f, o) => O.add.call(x, t, f, o), after = (f, ms) => O.st.call(window, f, ms);
  const get = (k) => { try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; } };
  const put = (k, v) => { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
  const talk = window.speechSynthesis, player = new Audio(), ms = navigator.mediaSession;
  let voice = null;
  const pick = () => {
    const vs = talk ? talk.getVoices().filter((v) => /^en/i.test(v.lang)) : [];
    const rank = (v) => (/premium/i.test(v.name) ? 4 : /enhanced|neural|natural/i.test(v.name) ? 3 : 0) + (v.localService ? 1 : 0) + (v.default ? .5 : 0);
    voice = vs.sort((a, b) => rank(b) - rank(a))[0] || null;
  };
  if (talk) { pick(); talk.addEventListener && on(talk, 'voiceschanged', pick); }
  // A fast start that grows into paragraphs: the first clip is the first two sentences, so the voice starts in
  // moments and plays long enough for the second to be made (one alone left a hiccup after it), and each next one a third longer than the last, which the machine makes in less time than the one before
  // it takes to play (all that has played gives it its time), so there is no wait; soon a clip is a whole paragraph. A clip never
  // runs across a paragraph's end, and breaks only between sentences: a pause there reads as a breath, at a comma
  // it reads as a stall (the person's ear).
  const chunks = (text) => {
    // a clip takes about 0.75 of its playing time to make here (measured: 0.74 to 0.80 at the person's speed), so
    // the next may be as long as all that has played lets it be made in time, with a margin: over 0.85
    const out = []; let want = 80, heard = 0, made = 0, lead = '', said = 0;
    const push = (c) => { if (out.length) made += c.length; out.push(c); heard += c.length;
      want = Math.min(600, Math.max(40, heard / 0.85 - made)); };
    text.split(/\\n\\s*\\n/).map((p) => p.replace(/\\s+/g, ' ').trim()).filter(Boolean).forEach((p) => {
      // a sentence ends at a stop followed by a space, so $1.50, 3.14 and board.py stay whole, as do Dr. and e.g.
      const sents = (p.match(/.+?(?:(?<!\\b(?:e\\.g|i\\.e|vs|Dr|Mr|Mrs|Ms|St|[A-Z]))[.!?]+(?=\\s|$)|$)/g) || [p]).map((x) => x.trim()).filter(Boolean);
      let cur = '';
      sents.forEach((x) => {
        // the first two sentences for a quick start; under four words ("Done. Yes.") they take the next with them
        if (!out.length) { lead = lead ? lead + ' ' + x : x; said += 1;
          if (said >= 2 && lead.split(' ').length >= 4) push(lead); return; }
        if (cur && (cur + ' ' + x).length > want) { push(cur); cur = ''; }
        cur = cur ? cur + ' ' + x : x;                                  // a sentence is never broken, however long
      });
      if (cur) push(cur);
    });
    if (!out.length && lead) push(lead);
    return out;
  };
  // The reading: one project's, its sections (each part of a reply as it was written, with its time) in order, the one
  // being heard and the piece of it reached. Kept on this device as it goes, so a reading left (another chat took the
  // voice, the page was closed) comes back paused where it stopped, what it hadn't reached still unheard.
  let R = null, playing = false, paused = false, loading = false, via = '', view = null, now = null;
  const rate = () => { let r = 1; try { r = +localStorage.getItem('pace') || 1; } catch (e) {} return [1, 1.25, 1.5, 1.75].includes(r) ? r : 1; };
  const pace = () => rate() + 0.25;               // the voices read slowly: each speed plays a quarter faster than it says
  const aloud = () => { try { return localStorage.getItem('aloud') === '1'; } catch (e) { return false; } };
  const left = (r) => !!r && r.cur < r.list.length;
  const save = () => { if (R) put('reading:' + R.name, Object.assign({}, R, {saved: Date.now()})); };
  const forget = (name) => put('reading:' + name, null);
  const load = (name) => { const r = name && get('reading:' + name);
    if (r && Array.isArray(r.list) && left(r) && Date.now() - (r.saved || 0) < 12 * 3600e3) return r;
    if (r) put('reading:' + name, null);
    return null; };
  const blank = (v) => ({name: v.name, key: v.key, href: v.href, kvoice: v.kvoice, kokoro: v.kokoro, list: [], cur: 0, piece: 0});
  const abs = (u) => new URL(u, location.href).href;
  const clip = (p) => '/voice?v=' + encodeURIComponent(R.kvoice || 'am_echo') + via + '&t=' + encodeURIComponent(p.trim());
  // the clips made already: going on to one of them is no wait, so the button stays as it is
  const ready = new Set();
  // A clip made ahead is downloaded whole and kept here as a file of the page's own (a phone's player would fetch
  // it again over the network otherwise), so going on to it needs nothing from the network: no wait between clips.
  const kept = new Map();                         // the clip's address -> the page's own copy
  const make = (p) => { const u = abs(clip(p));
    if (kept.has(u)) return Promise.resolve();
    return fetch(u).then((r) => r.ok ? r.blob() : null).then((b) => { if (!b) return;
      kept.set(u, URL.createObjectURL(b)); ready.add(u);
      if (kept.size > 24) { const [old, o] = kept.entries().next().value; URL.revokeObjectURL(o); kept.delete(old); } }).catch(() => {}); };
  // The clips after the one first heard are asked for one after another, each as soon as the one before has come, so
  // the machine makes them back to back with no idle time, never more than three ahead of what is being heard.
  let line = 0;
  const pipe = (i, parts, from) => { const mine = ++line; let k = from;
    const step = () => { if (mine !== line || !playing || !R || R.cur !== i) return;
      if (k >= parts.length) { const n = R.list[i + 1]; if (n) make(chunks(n.text)[0] || ''); return; }   // the next section's start
      if (k - (now && now.i === i ? now.at : from) > 3) { after(step, 400); return; }
      make(parts[k++]).then(step); };
    step(); };
  player.addEventListener('playing', () => { if (!player.loop) ready.add(player.src); if (loading && playing && !player.loop) { loading = false; changed(); } });
  // The phone stopped the sound itself (locked, a call, another app's audio): shown as paused, ▷ takes it up again,
  // and coming back to the page, what is shown is checked against what is heard.
  // Back on the page, a reading the phone stopped (dictation in another app, a call) goes on by itself, and one shown
  // as playing whose sound has not moved is set going again; a pause the person chose stays.
  let ours = 0, byPhone = false;
  player.addEventListener('pause', () => { if (Date.now() - ours < 600 || player.ended || player.loop || !playing || paused) return;
    paused = true; byPhone = true; loading = false; changed(); });
  on(document, 'visibilitychange', () => {
    if (document.hidden) return;
    if (resting && player.paused) { player.play().catch(() => {}); return; }
    if (!playing) return;
    if (!R.kokoro) { if (talk && !talk.speaking && !talk.pending) { playing = false; changed(); } return; }
    if (paused) { if (byPhone) { byPhone = false; hold(false); } return; }
    const at = player.currentTime;
    after(() => { if (playing && !paused && !player.ended && player.currentTime === at) player.play().catch(() => {}); }, 700);
  });
  player.addEventListener('waiting', () => { if (playing && !player.loop && !loading && !player.src.startsWith('blob:') && !ready.has(player.src)) {
    loading = true; changed(); } });
  // keep: on from the silence between replies to the next, the player is not stopped first (locked, a phone would
  // not let it start again)
  const hush = (keep) => { ours = Date.now(); if (talk) talk.cancel(); if (!keep) player.pause(); player.onended = player.onerror = null; paused = false; loading = false;
    player.loop = false; resting = 0; line++; };
  // Locked, a phone runs the page only while it plays something: between replies it plays silence, so the next one
  // is heard as it comes, as a music app goes on to the next song; after twenty quiet minutes it lets go.
  let resting = 0;
  const rest = () => { if (!R || !R.kokoro || !aloud()) return;
    player.onended = player.onerror = null; player.loop = true; player.src = SILENT; resting = Date.now();
    player.play().catch(() => { resting = 0; }); };
  O.si.call(window, () => { if (resting && Date.now() - resting > 20 * 60e3) { player.pause(); player.loop = false; resting = 0; } }, 30e3);
  // on the lock screen as a song is: what is being read, with pause and skip
  const tell = (text) => { try { if (ms && window.MediaMetadata) ms.metadata = new MediaMetadata({title: text.slice(0, 90),
    artist: R.name, album: 'colony'}); } catch (e) {} };
  // A phone plays a page's audio only after a tap on it: the first tap anywhere (Send included) plays a moment of
  // silence, and from then on new replies may be read by themselves.
  const SILENT = 'data:audio/wav;base64,UklGRqQMAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YYAMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==';
  let unlocked = false;
  const unlock = () => {
    if (talk && !unlocked) talk.speak(new SpeechSynthesisUtterance(''));
    if (unlocked || playing) return;
    unlocked = true; player.src = SILENT; player.play().catch(() => { unlocked = false; });
  };
  on(document, 'pointerdown', () => { if (aloud()) unlock(); }, true);
  const sayK = (i, parts, from) => {
    let j = from;
    const next = () => {
      if (!playing || !R || R.cur !== i) return;
      if (j >= parts.length) return finished(i);
      now = {i, parts, at: j}; R.piece = j; save(); changed();
      const u = abs(clip(parts[j]));
      player.src = kept.get(u) || u; player.playbackRate = pace(); player.defaultPlaybackRate = pace();
      // the first clip heard comes over the network as it is made; the rest are lined up as soon as it has come
      if (j === from) player.addEventListener('loadeddata', () => pipe(i, parts, from + 1), {once: true});
      j += 1;
      // refused: the phone's voice; cut short by a stop, a skip or the next clip is no refusal, and nothing is read again.
      // Refused for want of a tap on this page (opened by itself), the phone's voice is refused too: ▷ again, which a
      // tap plays, rather than ■ over silence.
      player.play().catch((err) => { if (err && err.name === 'AbortError') return;
        if (err && err.name === 'NotAllowedError' && playing && R && R.cur === i) { hush(); playing = false; unlocked = false; changed(); return; }
        if (playing && R && R.cur === i && now && now.at === j - 1) { hush(); sayPhone(i, parts, j - 1); } });
    };
    player.onended = next;
    player.onerror = () => { const err = player.error;                  // the phone's voice then, for a clip that failed
      if (!err || err.code === 1 || !playing || !R || R.cur !== i) return;   // 1: stopped on purpose, nothing failed
      hush(); sayPhone(i, parts, Math.max(0, j - 1)); };
    next();
  };
  const sayPhone = (i, parts, from) => {
    if (!talk) { playing = false; changed(); return; }
    playing = true; changed();
    // a phone that won't speak before a tap on the page says nothing and starts nothing: ▷ again after a moment
    let began = false;
    after(() => { if (playing && R && R.cur === i && !began && !talk.speaking) { talk.cancel(); playing = false; loading = false; changed(); } }, 2500);
    parts.slice(from).forEach((p, n) => { const x = from + n, u = new SpeechSynthesisUtterance(p.trim()); if (voice) u.voice = voice; u.rate = pace();
      u.onstart = () => { began = true; now = {i, parts, at: x}; if (R) { R.piece = x; save(); } loading = false; changed(); };
      if (x === parts.length - 1) u.onend = () => { if (playing && R && R.cur === i) finished(i); };
      talk.speak(u); });
  };
  // a section heard to its end: on to the next; the last, and the reading is done
  const finished = (i) => { if (!R || R.cur !== i) return;
    R.list[i].heard = true;
    if (i + 1 < R.list.length) return begin(i + 1, 0, true);
    playing = false; R.cur = R.list.length; R.piece = 0; forget(R.name); changed(); rest(); };
  // From a section: those before it are counted heard, whether read or skipped past
  function begin(i, from = 0, flow = false) {
    const s = R && R.list[i]; if (!s) return;
    const parts = chunks(s.text).filter((p) => p.trim());
    from = Math.min(from, Math.max(0, parts.length - 1));
    hush(!!resting); R.cur = i; R.piece = from; playing = true; now = null;
    R.list.forEach((x, n) => { if (n < i) x.heard = true; });
    loading = !(R.kokoro && parts[from] && ready.has(abs(clip(parts[from])))); save(); changed(true);
    if (!parts.length) return finished(i);
    tell(parts[from]);
    return R.kokoro ? sayK(i, parts, from) : sayPhone(i, parts, from);
  }
  // Another project's reading, playing or paused, gives way: kept where it stopped, its unheard sections waiting
  const yieldTo = (r) => { if (R && R !== r) { if (playing) hush(); playing = false; paused = false; if (left(R)) save(); R = null; } };
  // ⏸ holds the reading where it is, ▷ takes it up from there; paused in the silence between replies, it lets go
  function hold(want) {
    if (!playing) { if (want && resting) { player.pause(); player.loop = false; resting = 0; } else if (!want && left(R)) begin(R.cur, R.piece); return; }
    paused = want; ours = Date.now(); byPhone = false;
    if (R.kokoro && !(talk && talk.speaking)) { if (paused) player.pause(); else player.play().catch(() => { paused = true; changed(); }); }
    else if (talk) { if (paused) talk.pause(); else talk.resume(); }
    changed();
  }
  // ⏭: on to the next section, or, at the last, to the next piece of it
  function skip() {
    if (!R || !playing) return;
    if (R.cur + 1 < R.list.length) return begin(R.cur + 1);
    if (!now || now.i !== R.cur) return;
    const {i, parts, at} = now;
    hush(); playing = true;
    if (at + 1 >= parts.length) return finished(i);
    changed(); if (R.kokoro) sayK(i, parts, at + 1); else sayPhone(i, parts, at + 1);
  }
  function stop() { hush(); playing = false; now = null; if (R) forget(R.name); R = null; changed(); }
  // A new voice or speed takes effect at once: Kokoro's speed on the clip playing; otherwise from the piece being heard.
  const again = (voiceChanged) => {
    if (!playing || !now || !R || now.i !== R.cur) return;
    if (R.kokoro && !voiceChanged) { player.playbackRate = pace(); player.defaultPlaybackRate = pace(); return; }
    const {i, parts, at} = now;
    hush(); playing = true; changed();
    if (R.kokoro) sayK(i, parts, at); else sayPhone(i, parts, at);
  };
  if (ms) { [['pause', () => hold(true)], ['play', () => hold(false)], ['nexttrack', skip], ['stop', stop]].forEach(([a, h]) => {
    try { ms.setActionHandler(a, h); } catch (e) {} }); }
  // The player: over every page, above the box and the tabs, while there is something to read or take up again
  const PICON = @ICONS@;
  const bar = document.createElement('div'); bar.id = 'cplayer'; bar.className = 'cplayer'; bar.hidden = true;
  // three panels open above it, one at a time: the sections (☰), whose reading it is (its name), and its settings (⚙)
  bar.innerHTML = "<div class='cppanel cpsecs' hidden><div class='cphead'><span class='cpcount'></span>"
    + "<label class='cpfollow'><input type='checkbox'> Follow in chat</label></div><ol class='cprows'></ol></div>"
    + "<div class='cppanel cpwho' hidden><div class='cphead'><span>Read from</span><a class='cpopen'>Open its chat ›</a></div><ol class='cprows cpprojs'></ol></div>"
    + "<div class='cppanel cpset' hidden><div class='cpsets'><label class='cpline'><input type='checkbox' class='cpaloud'> Auto-speak new replies</label>"
    + "<label class='cpline'>Speed <select class='cprate'>" + [1, 1.25, 1.5, 1.75].map((r) => "<option value='" + r + "'>" + r + "×</option>").join('') + "</select></label>"
    + "<div class='cpvoice' hidden><label class='cpline'>Voice <select class='cpkvoice'></select></label>"
    + "<label class='cpline'><input type='checkbox' class='cpvpin'> Pin voice to this project</label></div></div></div>"
    + "<div class='cpbar'><button type='button' class='cplist' aria-label='Sections' aria-expanded='false'>" + PICON.list + "</button>"
    + "<button type='button' class='cpwhat' aria-label='Read from another project' aria-expanded='false'><b class='cpname'></b><span class='cpprev'></span></button>"
    + "<button type='button' class='cphold'></button><button type='button' class='cpskip' aria-label='Skip ahead'>" + PICON.skip + "</button>"
    + "<button type='button' class='cpstop' aria-label='Stop'>" + PICON.stop + "</button>"
    + "<button type='button' class='cpcog' aria-label='Auto-speak, speed and voice' aria-expanded='false'>⚙&#xFE0E;</button></div>";
  const $ = (s) => bar.querySelector(s), panel = $('.cpsecs'), rows = $('.cprows'), follow = $('.cpfollow input');
  const following = () => { try { return localStorage.getItem('follow') !== '0'; } catch (e) { return true; } };
  follow.checked = following();
  on(follow, 'change', () => { try { localStorage.setItem('follow', follow.checked ? '1' : '0'); } catch (e) {} if (view) view.mark(); });
  const words = (t, n) => { const w = t.replace(/\\s+/g, ' ').trim().split(' '); return w.slice(0, n).join(' ') + (w.length > n ? '…' : ''); };
  const when = (at) => { const d = new Date(at); if (isNaN(d)) return '';
    const clock = d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
    return d.toDateString() === new Date().toDateString() ? clock : d.toLocaleDateString([], {month: 'short', day: 'numeric'}) + ', ' + clock; };
  let drawn = '';
  const draw = () => {
    const show = left(R);
    if (bar.hidden === show) { bar.hidden = !show; document.body.classList.toggle('cpon', show); if (!show) shut(); }
    document.body.classList.toggle('cpon', show);
    if (!show) return;
    const s = R.list[R.cur];
    $('.cpname').textContent = R.name; $('.cpprev').textContent = words(s.text, 12); $('.cpopen').setAttribute('href', R.href || '/');
    const st = playing && !paused ? (loading ? 'load' : 'pause') : 'play';
    const hb = $('.cphold'); if (hb.dataset.on !== st) { hb.dataset.on = st; hb.innerHTML = PICON[st]; hb.setAttribute('aria-label', st === 'play' ? 'Play' : 'Pause'); }
    $('.cpskip').disabled = !playing;
    $('.cpcount').textContent = 'Section ' + (R.cur + 1) + ' of ' + R.list.length;
    const key = R.name + '|' + R.cur + '|' + R.list.map((x) => x.k + (x.heard ? '+' : '')).join(',');
    if (key !== drawn) { drawn = key;
      rows.replaceChildren(...R.list.map((x, n) => { const li = document.createElement('li'); li.dataset.i = n;
        li.className = n === R.cur ? 'now' : x.heard ? 'heard' : 'wait';
        const a = document.createElement('span'); a.className = 'cpat'; a.textContent = when(x.at);
        const w = document.createElement('span'); w.className = 'cpwords'; w.textContent = words(x.text, 10);
        li.append(a, w); return li; }));
      if (!panel.hidden) { const cur = rows.querySelector('.now'); if (cur) cur.scrollIntoView({block: 'nearest'}); } }
    placed();
  };
  // sits above what is fixed at the foot of the page: the box in Chat, a phone's tabs
  const placed = () => { if (bar.hidden) return;
    let edge = innerHeight;
    document.querySelectorAll('.composer, .tabs').forEach((x) => { const p = getComputedStyle(x).position;
      if (p !== 'fixed' && p !== 'sticky') return;
      const r = x.getBoundingClientRect(); if (r.height && r.top > innerHeight * 0.55 && r.top < edge) edge = r.top; });
    bar.style.bottom = Math.max(0, Math.round(innerHeight - edge)) + 'px';
    document.documentElement.style.setProperty('--cph', bar.offsetHeight + 'px'); };
  let placing = 0;
  const soon = () => { if (!placing) placing = requestAnimationFrame(() => { placing = 0; placed(); }); };
  on(window, 'scroll', soon, {passive: true}); on(window, 'resize', soon);
  if (window.visualViewport) { on(window.visualViewport, 'resize', soon); }
  // what is shown changed: the player, and the chat on screen (where it is in that chat, and, as a section starts, there)
  function changed(started) { draw(); if (view) { view.mark(); if (started && R && R.name === view.name && R.list[R.cur]) view.focus(R.list[R.cur].k, true); } }
  on($('.cphold'), 'click', () => hold(playing ? !paused : false));
  on($('.cpskip'), 'click', skip);
  on($('.cpstop'), 'click', stop);
  const panels = [[$('.cplist'), panel, () => { drawn = ''; draw(); }], [$('.cpwhat'), $('.cpwho'), () => who()], [$('.cpcog'), $('.cpset'), () => sets()]];
  function shut() { panels.forEach(([b, p]) => { p.hidden = true; b.setAttribute('aria-expanded', 'false'); }); }
  panels.forEach(([b, p, fill]) => on(b, 'click', () => { const was = p.hidden; shut(); if (was) { p.hidden = false; b.setAttribute('aria-expanded', 'true'); fill(); } }));
  on(rows, 'click', (ev) => { const li = ev.target.closest('li'); if (!li || !R) return; shut(); begin(+li.dataset.i); });
  on(document, 'click', (ev) => { if (!ev.target.closest('#cplayer')) shut(); });
  // Whose reading it is: the project of the page open first, then the one reading, those with a reading kept paused,
  // then the rest, as the board lists them. A tap reads from it: a kept reading from where it stopped; otherwise what
  // came since the person last wrote there and hasn't been read, or, all read, its latest reply.
  const listed = () => { const out = new Map();
    if (document.querySelector('nav a.proj.monitor')) out.set('steward', {key: 'steward', name: 'steward', href: '/steward?view=chat'});
    document.querySelectorAll('nav a.proj[href^="/?p="]').forEach((a) => { const k = new URL(a.href).searchParams.get('p'), n = a.querySelector('.pname');
      if (out.has(k) || !n) return;
      const name = [...n.childNodes].filter((x) => x.nodeType === 3).map((x) => x.textContent).join('').trim();
      out.set(k, {key: k, name, href: '/?p=' + k + '&view=chat'}); });
    return [...out.values()]; };
  const here = () => location.pathname === '/steward' ? 'steward' : location.pathname === '/' ? new URLSearchParams(location.search).get('p') : null;
  const who = () => { const all = listed(), mine = here(), rank = (p) => p.key === mine ? 0 : R && p.name === R.name ? 1 : load(p.name) ? 2 : 3;
    $('.cpprojs').replaceChildren(...all.map((p, n) => [rank(p), n, p]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map(([, , p]) => {
      const li = document.createElement('li'), b = document.createElement('b'), st = document.createElement('span'), kept = load(p.name);
      li.dataset.key = p.key; b.className = 'cpwords'; b.textContent = p.name; st.className = 'cpstate';
      st.textContent = [p.key === mine ? 'this page' : '', R && R.name === p.name ? (playing ? 'reading' : 'paused') : kept ? 'paused · ' + (kept.list.length - kept.cur) + ' left' : '']
        .filter(Boolean).join(' · ');
      if (R && R.name === p.name) li.className = 'now';
      li.append(b, st); return li; })); };
  on($('.cpprojs'), 'click', (ev) => { const li = ev.target.closest('li'); if (!li) return; shut();
    const p = listed().find((x) => x.key === li.dataset.key); if (p) switchTo(p); });
  on($('.cpopen'), 'click', () => shut());
  const fetched = (u, as) => fetch(u, {cache: 'no-store'}).then((r) => r.ok ? r[as]() : null).catch(() => null);
  async function switchTo(p) {
    if (R && R.name === p.name) { if (!playing) begin(R.cur, R.piece); return; }
    const kept = load(p.name);
    if (kept) { yieldTo(kept); R = kept; via = ''; begin(R.cur, R.piece); return; }
    $('.cpprev').textContent = 'Opening ' + p.name + '…';
    const [feed, vo] = await Promise.all([fetched('/chat/feed?p=' + encodeURIComponent(p.key), 'text'), fetched('/chat/voice?p=' + encodeURIComponent(p.key), 'json')]);
    // read off the page's own drawing, as the chat does, so its paragraphs stay paragraphs
    const box = document.createElement('div'); box.className = 'chat'; box.style.cssText = 'position:absolute;left:-9999px;top:0;width:600px';
    box.innerHTML = feed || ''; document.body.appendChild(box);
    const says = [...box.querySelectorAll('.say')], you = [...box.querySelectorAll('.msg.you')].pop();
    const since = says.filter((x) => !you || (you.compareDocumentPosition(x) & Node.DOCUMENT_POSITION_FOLLOWING));
    const heard = new Set(get('heard:' + p.name) || []);
    let pick = since.filter((x) => !heard.has(x.dataset.k)).slice(-10);
    if (!pick.length) { const last = says.length && says[says.length - 1].dataset.t; pick = since.length ? since : says.filter((x) => x.dataset.t === last); }
    const secs = pick.map((x) => { x.querySelectorAll('pre').forEach((e) => e.remove());
      return {t: x.dataset.t, k: x.dataset.k, at: x.dataset.at || '', text: x.innerText.replace(/[*_`#>|]/g, '').trim()}; }).filter((x) => x.text);
    box.remove();
    if (!secs.length) { draw(); $('.cpprev').textContent = 'Nothing to read in ' + p.name + ' yet'; return; }
    put('heard:' + p.name, [...new Set([...heard, ...pick.map((x) => x.dataset.k)])].slice(-300));
    yieldTo(null);
    R = {name: p.name, key: p.key, href: p.href, kvoice: (vo && vo.voice) || 'am_echo', kokoro: vo ? !!vo.ready : false, list: secs, cur: 0, piece: 0};
    via = ''; begin(0);
  }
  // ⚙: Auto-speak, speed and voice, the same as the chat's own (each changed here shows there too)
  async function sets() {
    $('.cpaloud').checked = aloud(); $('.cprate').value = String(rate());
    if (!R) return;
    const vo = await fetched('/chat/voice?p=' + encodeURIComponent(R.key), 'json');
    if (!vo || !vo.ready) { $('.cpvoice').hidden = true; return; }
    $('.cpkvoice').innerHTML = vo.options; $('.cpkvoice').value = vo.voice; $('.cpvpin').checked = vo.pinned; $('.cpvoice').hidden = false;
  }
  on($('.cpaloud'), 'change', () => { const a = $('.cpaloud').checked;
    try { localStorage.setItem('aloud', a ? '1' : '0'); } catch (e) {}
    if (a) unlock();
    const box = document.getElementById('aloud'); if (box) box.checked = a; if (view) view.mark(); });
  on($('.cprate'), 'change', () => { P.setRate(+$('.cprate').value || 1); const r = document.getElementById('rate'); if (r) r.value = $('.cprate').value; });
  const keepVoice = async (body) => { if (!R) return; const name = R.name;
    const r = await fetch('/chat/voice', {method: 'POST', body: new URLSearchParams(Object.assign({p: R.key}, body))}).catch(() => null);
    if (!r || !r.ok) return;
    const got = await r.json();
    $('.cpkvoice').value = got.voice; $('.cpvpin').checked = got.pinned; P.setVoice(name, got.voice);
    document.dispatchEvent(new CustomEvent('colony-voice', {detail: {name, voice: got.voice, pinned: got.pinned}})); };
  on($('.cpkvoice'), 'change', () => keepVoice({voice: $('.cpkvoice').value}));
  on($('.cpvpin'), 'change', () => keepVoice({pin: $('.cpvpin').checked ? '1' : '0'}));
  const P = window.colonyPlayer = {
    busy: () => left(R), placed, rate, following,
    setRate(r) { try { localStorage.setItem('pace', r); } catch (e) {} again(false); },
    setVoice(name, v) { if (R && R.name === name && R.kvoice !== v) { R.kvoice = v; save(); again(true); }
      else { const r = load(name); if (r) { r.kvoice = v; put('reading:' + name, r); } } },
    attach(v) { view = v; changed(); },
    leave() { view = null; },
    unlock,
    // where a project's reading is: the one in play, or one kept paused for it
    of(name) {
      const r = R && R.name === name ? R : load(name); if (!left(r)) return null;
      const s = r.list[r.cur], active = r === R;
      return {active, playing: active && playing, paused: active && paused, loading: active && loading, t: s.t, k: s.k,
        into: r.piece >= 1 || (r.cur > 0 && r.list[r.cur - 1].t === s.t)};
    },
    now: () => ({playing, paused}),
    hold, skip, stop,
    // sections from a chat: tapped (read these now) or new ones arriving there (auto). New ones join a reading of
    // their project's already going, or wait after one kept paused; otherwise they take the voice from any other.
    read(v, secs, how = {}) {
      if (!secs.length) return;
      const clean = secs.map((x) => ({t: x.t, k: x.k, at: x.at, text: x.text}));
      if (how.now) { yieldTo(null); R = blank(v); R.list = clean; via = ''; begin(0); return; }
      const mine = R && R.name === v.name ? R : load(v.name);
      const add = (r) => { const have = new Set(r.list.map((x) => x.k)); clean.forEach((x) => { if (!have.has(x.k)) r.list.push(x); });
        while (r.list.length > 60 && r.cur > 0) { r.list.shift(); r.cur -= 1; } };
      if (mine && mine !== R) { add(mine); put('reading:' + v.name, Object.assign(mine, {saved: Date.now()})); changed(); return; }
      if (mine) { const was = left(R); add(R); save(); via = '&auto=1'; if (!was) begin(R.cur); else changed(); return; }
      yieldTo(null); R = blank(v); add(R); via = '&auto=1'; begin(0);
    },
    // ▷ on a reply kept paused: from its start, what waited after it still waiting
    again(v, secs) { const r = R && R.name === v.name ? R : load(v.name), ks = new Set(secs.map((x) => x.k));
      const rest = left(r) ? r.list.slice(r.cur).filter((x) => !ks.has(x.k)) : [];
      yieldTo(null); R = blank(v); R.list = secs.map((x) => ({t: x.t, k: x.k, at: x.at, text: x.text})).concat(rest); via = ''; begin(0); },
    // Continue: from the piece it stopped at
    resume(name) { const r = R && R.name === name ? R : load(name); if (!left(r)) return; yieldTo(r); R = r; via = ''; begin(R.cur, R.piece); },
    // coming back with Auto-speak on: what came since is read at once where the browser lets a page speak by itself; a
    // phone lets it only after a tap, so there it starts with the first touch anywhere on the page
    whenAllowed(go) { if (unlocked || playing) return go();
      player.src = SILENT; player.play().then(() => { unlocked = true; go(); }).catch(() => on(document, 'pointerdown', () => { unlocked = true; go(); }, {once: true, capture: true})); },
  };
  document.body.appendChild(bar);
  // the reading last left on this device: here again, paused
  try { R = Object.keys(localStorage).filter((k) => k.startsWith('reading:')).map((k) => load(k.slice(8))).filter(Boolean)
    .sort((a, b) => b.saved - a.saved)[0] || null; } catch (e) {}
  draw();
})();
"""

# On a phone the projects are one row of chips: a page opens with its own chip in view, beside the steward's, which
# stays at the left. Only the row scrolls, never the page; the order is the board's own.
CHIPS = """
(() => { const nav = document.querySelector('nav'), at = () => {     // what a jump to a place on the page lands below
  const sticky = nav && getComputedStyle(nav).position === 'sticky';
  document.documentElement.style.setProperty('--navh', (sticky ? nav.getBoundingClientRect().height : 0) + 'px'); };
  at(); addEventListener('resize', at); })();
(() => { const n = document.querySelector('nav .navrow'), jump = document.querySelector('nav .pinjump');
  const pins = n ? [...n.querySelectorAll('.proj.pinchip')] : [];
  if (n && jump && pins.length) {
    const past = () => { const last = pins[pins.length - 1];
      jump.hidden = n.scrollLeft < last.offsetLeft - n.offsetLeft + last.offsetWidth - 12; };
    n.addEventListener('scroll', past, {passive: true}); past();
    jump.addEventListener('click', () => n.scrollTo({left: 0, behavior: 'smooth'}));
  } })();
(() => { const n = document.querySelector('nav .navrow'), on = n && n.querySelector('.proj.on');
  if (!on || n.scrollWidth <= n.clientWidth) return;
  n.scrollLeft = Math.max(0, on.offsetLeft - n.offsetLeft - (n.clientWidth - on.offsetWidth) / 2); })();
"""


# A nest folds open or shut where it's listed, and stays as the person left it on this device; one pinned open (the nest
# of the project being shown, in the menu) stays open, whatever was stored.
FOLDS = """
function toggleFold(g) {
  g.dataset.open = g.dataset.open === '1' ? '0' : '1';
  delete g.dataset.pin;
  try { localStorage.setItem('fold:' + g.dataset.fold, g.dataset.open); } catch (e) {}
  return false;
}
document.querySelectorAll('[data-fold]').forEach((g) => {
  let v = null;
  try { v = localStorage.getItem('fold:' + g.dataset.fold); } catch (e) {}
  if (v !== null && !g.dataset.pin) g.dataset.open = v;
});
"""


# The groups in a list move by their grip (finger or mouse); where they're dropped is the person's order, kept by the board.
GRIPS = """
document.querySelectorAll('.nestgroup .grip').forEach((h) => {
  h.addEventListener('click', (ev) => ev.stopPropagation());
  h.addEventListener('pointerdown', (ev) => {
    const g = h.closest('.nestgroup'), list = g.parentNode;
    ev.preventDefault(); ev.stopPropagation();
    g.classList.add('dragging');
    const move = (e) => {
      const others = [...list.children].filter((x) => x.classList.contains('nestgroup') && !x.classList.contains('pinned') && x !== g);
      // a group goes before the first whose heading is below the finger: a long group needn't be dragged past whole
      const before = others.find((x) => { const r = x.querySelector('.listhead').getBoundingClientRect(); return e.clientY < r.top + r.height / 2; });
      list.insertBefore(g, before || null);
    };
    const up = () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up);
      g.classList.remove('dragging');
      const order = [...list.children].filter((x) => x.classList.contains('nestgroup') && !x.classList.contains('pinned')).map((x) => ['n', x.dataset.nest]);
      fetch('/nests/order', {method: 'POST', body: new URLSearchParams(order)}).catch(() => {});
    };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up);
  });
});
function pinProject(ev, p, on) {
  ev.preventDefault(); ev.stopPropagation();
  fetch('/project/pin', {method: 'POST', body: new URLSearchParams({p, on})}).then(() => window.colonyNav ? colonyNav.reload() : location.reload()).catch(() => {});
  return false;
}
"""


# What's typed in a box for answering by number is kept as it's typed (answer_box): a reload, or a list redrawn
# meanwhile, loses nothing, and the defaults it speaks to wait while it's written. Sending it leaves nothing behind.
DRAFTS = """
let drafting;
document.addEventListener('input', (ev) => {
  const f = ev.target.closest && ev.target.closest('form.bynumber'); if (!f) return;
  clearTimeout(drafting);
  drafting = setTimeout(() => fetch('/answers/draft', {method: 'POST', body: new URLSearchParams(new FormData(f))}).catch(() => {}), 700);
});
document.addEventListener('submit', (ev) => { if (ev.target.matches('form.bynumber')) clearTimeout(drafting); }, true);
"""


# A tap on one of a decision's answers takes effect at once, on any page: the question leaves the page as the finger
# lifts and the answer goes behind it, with no page to wait for. It stays gone while the page redraws (a rule keyed to its
# gate), and comes back saying so only if the answer never reached the board. The board's reply to an answer is a
# redirect to a page, which isn't fetched: an answer once taken is sent, however slow that page would have been to make.
ANSWERS = """
document.addEventListener('submit', async (ev) => {
  const f = ev.target, b = ev.submitter;
  if (!f.matches || !f.matches('form[action="/answer"]') || !(b && b.matches('button.choice'))) return;
  ev.preventDefault(); ev.stopPropagation();
  const gate = (f.querySelector('input[name=gate]') || {}).value || '';
  const rule = document.createElement('style');
  rule.textContent = `.need:has(input[name=gate][value="${CSS.escape(gate)}"]), .card.gate:has(input[name=gate][value="${CSS.escape(gate)}"]),
    details.need[data-k="g${CSS.escape(gate)}"] { display:none !important }`;
  document.head.appendChild(rule);
  const r = await fetch('/answer', {method: 'POST', body: new URLSearchParams(new FormData(f)), redirect: 'manual'}).catch(() => null);
  if (r && (r.ok || r.type === 'opaqueredirect')) return;
  rule.remove();
  const p = document.createElement('p'); p.className = 'muted'; p.textContent = 'Not sent: the board could not be reached. Tap again.';
  f.closest('.gatechoices') ? f.closest('.gatechoices').after(p) : f.after(p);
}, true);
"""


# Every few seconds: each project's status dot, line and waiting count in the sidebar.
POLL = """
async function poll() {
  try {
    const r = await fetch('/status', {cache: 'no-store'}); const st = await r.json();
    const md = document.getElementById('dot-m'); if (md) md.className = 'sdot ' + st.monitor.state.replace(' ', '-');
    const all = document.getElementById('badge-all');
    if (all) { const n = st.projects.reduce((k, s) => k + (s.followed ? s.waiting || 0 : 0), 0); all.hidden = !n; all.textContent = n; }
    st.projects.forEach((s, i) => {
      const dot = document.getElementById('dot-' + i), line = document.getElementById('sline-' + i);
      if (dot) dot.className = 'sdot ' + s.state.replace(' ', '-');
      document.querySelectorAll(`.msdot[data-p="${i}"]`).forEach((d) => { d.className = 'sdot msdot ' + s.state.replace(' ', '-'); });
      document.querySelectorAll(`.pwork[data-p="${i}"]`).forEach((x) => { x.textContent = s.work || ''; });
      document.querySelectorAll(`.pbadge[data-p="${i}"]`).forEach((x) => { x.hidden = !s.waiting; x.textContent = s.waiting; });
      if (line) line.textContent = (s.state === 'off' ? '' : s.state) + (s.work ? (s.state === 'off' ? '' : ' · ') + s.work : '');
      const status = document.getElementById('status-' + i);
      if (status) {
        const act = s.activity || {}, st = s.state.replace(' ', '-');
        status.querySelector('.sdot').className = 'sdot ' + st;
        status.querySelector('b').textContent = s.state;
        status.querySelector('.statusline .muted').textContent = s.work ? ' · ' + s.work : '';
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
    reorder(st.nav);
  } catch (e) {}
}
setInterval(poll, 2500);
// A project that stirs comes forward in the top menu then and there, as a reload would put it; where the menu has
// changed shape (a project added, a nest formed), it waits for the next page.
function reorder(nav) {
  const row = document.querySelector('nav .navrow'); if (!row || !nav) return;
  if (!row.dataset.watched) {           // never under a finger: a chip doesn't move while the row is touched or scrolled
    row.dataset.watched = 1;
    ['pointerdown', 'scroll', 'touchstart'].forEach((t) => row.addEventListener(t, () => { row.dataset.touched = Date.now(); }, {passive: true}));
  }
  if (Date.now() - (+row.dataset.touched || 0) < 5000) return;
  const chip = (i) => { const d = document.getElementById('dot-' + i); return d && d.closest('a.proj'); };
  const nests = [...row.querySelectorAll(':scope > .nestgroup')];
  const els = nav.map(([k, v]) => k === 'p' ? chip(v) : nests.find((g) => g.dataset.fold === 'nav:' + v));
  if (els.some((x) => !x || x.parentNode !== row)) return;
  nav.forEach(([k, v, members], n) => {
    if (k !== 'n') return;
    const box = els[n].querySelector('.members'), set = box && box.querySelector('.nestset');
    const mine = (members || []).map(chip);
    if (!box || mine.some((c) => !c || c.parentNode !== box)) return;
    const now = [...box.children].filter((c) => mine.includes(c));
    if (now.some((c, j) => c !== mine[j])) mine.forEach((c) => box.insertBefore(c, set));
  });
  const now = [...row.children].filter((x) => els.includes(x));
  if (now.every((x, j) => x === els[j])) return;
  const end = row.querySelector(':scope > div.sline, :scope > .navfoot');
  els.forEach((x) => row.insertBefore(x, end));
}
"""


def tabs(pid, view, root=None):
    """A project's tabs. Showcase only once the project has shown something (or when it is the page open): an empty
    tab is one more place to look that says nothing."""
    tab = lambda v, label, href: f"<a class='{'on' if view == v else ''}' href='{href}'>{label}</a>"
    shown = root is None or view == "showcase" or showcase.entries(root)
    return ("<div class='tabs'>" + tab("overview", "Overview", f"/?p={pid}&view=overview")
            + tab("roadmap", "Roadmap", f"/?p={pid}&view=roadmap")
            + tab("library", "Library", f"/?p={pid}&view=library")
            + (tab("showcase", "Showcase", f"/?p={pid}&view=showcase") if shown else "")
            + tab("chat", "Chat", f"/?p={pid}&view=chat")
            + tab("console", "Console", f"/?p={pid}&view=console") + "</div>")


SETTLE = 2                   # seconds a console started for a message is watched first: one that dies at once says why


def stopped(root):
    """Why nothing could be sent, where the project's agent isn't running: in its last words, where it ended in an error."""
    end = console.died(console.session_name(root))
    return ("Not sent: its agent isn't running" + (f"; it stopped with an error (exit {end['status']}), its last words: "
            + " / ".join(end["lines"][-3:]) if end else ".") + " Tell colony's agent, or open its Console to see.")


def held(who, back, root=None):
    """What the person sees when what they sent was not typed: someone's draft was in the way, or its agent isn't there."""
    why = (e(stopped(root)) if root and not console.live(root) else
           f"Not sent: {e(who)} has something half-typed in its console, and your message would have landed on it "
           f"and sent it. Send or clear it there, then send this again.")
    return (f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<style>body{{font:15px system-ui;margin:24px;max-width:36em;line-height:1.5}}</style>"
            f"<p>{why}</p>"
            f"<p><a href='javascript:history.back()'>Back to what you wrote</a> · <a href='{e(back)}'>Back to the board</a></p>")


IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".svg")


def chat_file(key, root, target):
    """The address a file in the project is shown at in the chat, or None: nothing outside its folder is served."""
    from . import pins
    path = pins.inside(root, html.unescape(target)) if root else None
    if not path or not path.is_file():
        return None
    return f"/chat/file?p={urllib.parse.quote(str(key))}&f={urllib.parse.quote(str(path))}"


def chat_prose(text, key=None, root=None):
    """An agent's prose in the chat: markdown as the page shows it, code blocks kept as they are, links live; a
    picture in the project shown as itself (![what](path)), and a link to any other file there (a PDF) opens it."""
    out = []
    for k, part in enumerate(re.split(r"^```[^\n]*\n", text, flags=re.M)):
        if k % 2:                                            # inside a fence, up to its closing one
            code, _, rest = part.partition("```")
            out.append(f"<pre>{e(code.rstrip())}</pre>" + prose(rest))
        else:
            out.append(prose(part))
    page = "".join(out)

    def picture(m):
        url = chat_file(key, root, m.group(2))
        return (f"<a class='shot' href='{url}' target='_blank' rel='noopener'><img src='{url}' alt='{m.group(1)}' loading='lazy'></a>"
                if url and m.group(2).lower().endswith(IMAGES) else m.group(0))

    def link(m):
        url = chat_file(key, root, m.group(2)) if not m.group(2).startswith("http") else None
        if url:
            return f"<a class='file' href='{url}' target='_blank' rel='noopener'>📄 {m.group(1)}</a>"
        return f"<a href='{m.group(2)}' target='_blank' rel='noopener'>{m.group(1)}</a>"
    page = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", picture, page)
    return re.sub(r"(?<!!)\[([^\]]+)\]\(((?:https?://|/)[^)\s]+)\)", link, page)


NOTE_FROM = {"monitor": "The person's steward", "suggestion": "The steward suggests", "colony": "Colony",
             "observation": "Observed file change", "page": "From a page you used"}


def mail_by_turn(root, turns):
    """What each of colony's notices ("[colony] You have mail", "... a note from the person") brought: the letters and
    notes handed over from just before it until the next turn began, as they were delivered."""
    from . import mail
    def at(t):
        try:
            return epoch(t["at"][:19] + "Z")
        except (ValueError, TypeError):
            return None
    notices = []                                         # (turn, began, the next turn's start)
    for i, t in enumerate(turns):
        start = at(t) if t["from"] == "colony" else None
        if start is not None:
            notices.append((i, start, next((x for x in map(at, turns[i + 1:]) if x is not None), float("inf"))))
    byturn = {}
    said = [(m["delivered_at"], f"{mail.shown(m['from'])} {'asks' if m['ask'] else 'writes'}:\n{m['text']}")
            for m in mail.inbox(root) if m.get("delivered_at")]
    said += [(n["delivered_at"], f"{NOTE_FROM.get(n.get('author'), 'The person')}:\n{n['text']}")
             for n in notes(root) if n.get("delivered_at")]
    for delivered, m in sorted(said):                    # each under one notice: the last begun by its delivery
        when = epoch(delivered[:19] + "Z")
        fits = [(i, start) for i, start, end in notices if start - 30 <= when < end]
        before = [x for x in fits if x[1] <= when]
        if fits:
            byturn.setdefault((max(before, key=lambda x: x[1]) if before else min(fits, key=lambda x: x[1]))[0], []).append(m)
    return {i: "\n\n".join(ms) for i, ms in byturn.items()}


def said_at(at, now=None):
    """A message's time in a few characters, in this machine's time: "8:41 PM" today, "Oct 24, 8:41 PM" before."""
    from datetime import datetime
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return ""
    now = (now or datetime.now().astimezone())
    clock = when.strftime("%I:%M %p").lstrip("0")
    return f"<span class='stamp' title='{when:%Y-%m-%d %H:%M}'>{clock if when.date() == now.date() else f'{when:%b} {when.day}, {clock}'}</span>"


def chat_feed(key, root, shown=40):
    """The conversation, as the app shows one: the person's messages, the agent's prose in full, each run of work
    between folded to a line (Ran 6 commands), colony's own notices small; then what it is doing now."""
    from . import chat, context, monitor
    s = context.session(root)
    turns = chat.turns(chat.events(s["provider"], s["path"])) if s else []
    turns = turns[-shown:]
    letters = mail_by_turn(root, turns)
    out = []
    for i, t in enumerate(turns):
        k = f"{e(t['at'])}"
        if t["from"] == "you":                          # with the pictures it sent, as pictures
            shots = "".join(f"<a class='shot' href='{u}' target='_blank' rel='noopener'><img src='{u}' alt='' loading='lazy'></a>"
                            for a in re.findall(r"^Attached: (.+)$", t["text"], flags=re.M)
                            if a.strip().lower().endswith(IMAGES) and (u := chat_file(key, root, e(a.strip()))))
            out.append(f"<div class='msg you'>{e(t['text'])}{shots}{said_at(t['at'])}</div>")
        elif t["from"] == "queued":                      # sent mid-turn, not yet taken up
            out.append(f"<div class='msg you pending queued' title='queued: it reaches the agent within this turn'>{e(t['text'])}</div>")
        elif t["from"] == "colony" and t["text"].lstrip().startswith("[colony] Context carry-over ("):
            # what it wrote down before a context refresh: one line, opened to read; never read aloud
            said = "\n\n".join(x for kind, x in t["items"] if kind == "say")
            m = re.search(r"<colony-carry[^>]*>([\s\S]*?)</colony-carry>", said)
            out.append(f"<details class='msg colony' data-k='c{k}'><summary>Context refreshed: what it carried over</summary>"
                       f"<div>{chat_prose((m[1] if m else said).strip(), key, root)}</div></details>")
            continue
        elif t["from"] == "colony":
            head, _, rest = t["text"].removeprefix("[colony]").strip().partition("\n")
            body = "\n\n".join(filter(None, [rest.strip(), letters.get(i, "")]))
            out.append(f"<details class='msg colony' data-k='c{k}'><summary>{e(short(head, 90))}</summary>"
                       f"<div>{e(body)}</div></details>")
        elif t["from"] == "compact":
            out.append("<div class='msg colony'>The conversation was compacted here</div>")
        run = []
        for j, (kind, text) in enumerate(t["items"] + [("end", "")]):
            if kind == "step":
                run.append(text)
                continue
            if run:
                out.append(f"<details class='ran' data-k='r{k}-{j}'><summary>{e(chat.worked(run))}</summary>"
                           f"<ul>{''.join(f'<li>{e(x)}</li>' for x in run)}</ul></details>")
                run = []
            if kind == "say":
                when = (t.get("times") or [])[j] if j < len(t.get("times") or []) else t.get("last") or t["at"]
                out.append(f"<div class='say' data-t='{k}' data-k='s{k}-{j}' data-at='{e(when)}'>{chat_prose(text, key, root)}</div>")
        if any(kind == "say" for kind, _ in t["items"]):
            out.append(f"<div class='turnacts'><button class='copy' data-t='{k}' aria-label='Copy this reply'>{ICONS['copy']}</button>"
                       "<button class='vset' aria-label='Voice: auto-speak, speed, voice'>Voice</button>"
                       f"<button class='listen' data-t='{k}' aria-label='Read this reply aloud'>{ICONS['play']}</button>"
                       f"<button class='cont' data-t='{k}' hidden aria-label='Continue where it stopped'>Continue</button>"
                       f"<button class='hold' data-t='{k}' hidden aria-label='Pause'>{ICONS['pause']}</button>"
                       f"<button class='skip' data-t='{k}' hidden aria-label='Skip ahead'>{ICONS['skip']}</button>"
                       f"{said_at(t.get('last') or t['at'])}</div>")
    if not turns:
        out.append("<p class='muted'>No conversation yet: what you send here starts one.</p>")
    # what it is doing now: at work, or a choice in its console, answered here
    name = monitor.name() if key == "steward" else console.session_name(root)
    snap = monitor.snapshot() if key == "steward" else console.snapshot(root, lines=4)
    # and what it waits on in the background, its turn over or not (a test run, a helper): a tap opens the tasks
    behind = chat.running(snap.get("lines") or [])
    tasks = chat.background(s["path"]) if s and s["provider"] == "claude" else []
    if not behind:                                       # nothing runs, whatever the transcript last said
        tasks = [dict(t, state="ended") if t["state"] == "running" else t for t in tasks]
    line = ""
    if snap["state"] == "working":
        act = (snap.get("activity") or {}).get("line") or "Working"
        # two short lines that never wrap: what it's doing, then its parenthesis (time, tokens) and what runs behind
        head, sep, inner = act.rpartition(" (") if act.endswith(")") else ("", "", "")
        head, inner = (head, inner[:-1]) if sep else (act, "")
        inner = " · ".join(x for x in re.split(r"\s+[·•]\s+", inner) if x and "esc to" not in x)   # Stop is on the page
        second = " · ".join(filter(None, [e(inner), f"<span class='behind'>{e(behind)}</span>" if behind else ""]))
        line = (f"<span class='sdot working'></span><span class='act'><span>{e(head)}</span>"
                + (f"<span class='more'>{second}</span>" if second else "") + "</span>")
    elif behind:
        line = f"<span class='sdot working'></span>{e(behind)}"
    busy = " busy" if snap["state"] == "working" else ""
    # what the agent needs of the person, at the end of the conversation: folded to a line, each one opening to its
    # own answer, so none is missed and however many there are take no room until opened
    asks = [g for g in gates(root) if due(g)] if key != "steward" else []
    # and the rest the project waits on them for, as its Overview shows it (a sign-off, a question asked in its console)
    rest = waiting_parts(key, root, f"/?p={key}&view=chat", label=False, with_gates=False)[2] if key != "steward" else []
    if asks or rest:
        out.append(f"<details class='needs' data-k='needs'><summary><span class='sdot needs-you'></span>Needs you · {len(asks) + len(rest)}</summary>"
                   + "".join(f"<details class='need' open data-k='g{e(g['id'])}'><summary><span class='q'>{e(short(g['question'], 110))}</span>"
                             f"<span class='fold muted'>Fold ▴</span></summary>"
                             f"{gate_body(root, key, g, '/')}</details>" for g in asks) + "".join(rest) + "</details>")
    if line and tasks:
        out.append(f"<details class='chatlive tasks{busy}' data-k='tasks'><summary>{line}</summary>{tasks_html(tasks)}</details>")
    elif line:
        out.append(f"<div class='chatlive{busy}'>{line}</div>")
    elif snap["state"] == "needs you" and key != "steward":
        found = providers.of(root).choice(console.screen(name))
        if found:
            question, options, _ = found
            out.append("<div class='chatask'>" + "".join(f"<div>{e(q)}</div>" for q in question) + "<div class='choices'>"
                       + "".join(f"<button class='quiet' data-option='{e(o)}'>{e(o)}</button>" for o in options) + "</div></div>")
        else:
            out.append(f"<div class='chatask'>It needs you in its console. <a href='/?p={key}&view=console'>Open it →</a></div>")
    return "".join(out)


def tasks_html(tasks, shown=15):
    """The background tasks, as the app lists them: running first, with how long; then the finished, newest first."""
    def since(a, b=None):
        try:
            t0 = time.mktime(time.strptime(a[:19], "%Y-%m-%dT%H:%M:%S"))
            t1 = time.mktime(time.strptime(b[:19], "%Y-%m-%dT%H:%M:%S")) if b else time.mktime(time.gmtime())
            return took(max(0, t1 - t0))
        except ValueError:
            return ""
    row = lambda t, tail: (f"<li><span class='tk'>{'›_' if t['kind'] == 'Shell' else '◆'}</span><span class='tw'>"
                           f"<span class='tc'>{e(short(' '.join(t['what'].split()), 140))}</span>"
                           f"<span class='tm'>{e(t['kind'])} · {tail}</span></span></li>")
    running = [t for t in tasks if t["state"] == "running"]
    done = [t for t in tasks if t["state"] != "running"][::-1][:shown]
    word = lambda t: ("Completed" if t["state"] == "completed" and not t["exit"] else
                      f"Failed (exit {t['exit']})" if t["exit"] else t["state"].capitalize())
    return ("<div class='tasklist'>"
            + (f"<div class='th'>Running</div><ul>{''.join(row(t, e(since(t['at']))) for t in running)}</ul>" if running else "")
            + (f"<div class='th'>Finished {len(done)}</div><ul>{''.join(row(t, e(word(t))) for t in done)}</ul>" if done else "")
            + "</div>")


# The reply's actions as drawn icons, one line weight, in the text's colour: a symbol font or an emoji (⏭ on a phone)
# would not match.
_SVG = "<svg viewBox='0 0 24 24' width='20' height='20' fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>{}</svg>"
ICONS = {"copy": _SVG.format("<rect x='8' y='8' width='12' height='12' rx='2.5'/><path d='M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2'/>"),
         "play": _SVG.format("<path d='M7 4.5v15l12.5-7.5z'/>"),
         "stop": _SVG.format("<rect x='6' y='6' width='12' height='12' rx='2' fill='currentColor'/>"),
         "load": _SVG.format("<path class='spin' d='M12 3.5a8.5 8.5 0 1 0 8.5 8.5'/>"),
         "pause": _SVG.format("<rect x='6.5' y='5' width='3.5' height='14' rx='1' fill='currentColor'/><rect x='14' y='5' width='3.5' height='14' rx='1' fill='currentColor'/>"),
         "skip": _SVG.format("<path d='M4.5 5.5v13l8-6.5z'/><path d='M12.5 5.5v13l8-6.5z'/>"),
         "check": _SVG.format("<path d='M5 12.5l4.5 4.5L19 7.5'/>"),
         "list": _SVG.format("<path d='M4 7h16M4 12h16M4 17h10'/>")}


# The voice Chat reads in is the person's, on every device: one voice of theirs that every project uses, changed from
# any project not pinned (so a new project starts in the last voice they chose), and a pin that keeps a project on a
# voice of its own, changed only from there.
def _voices():
    path = home() / "voice.json"
    try:
        v = json.loads(path.read_text())
    except (OSError, ValueError):
        v = {}
    from . import voice
    return {"voice": v.get("voice") or voice.DEFAULT, "pins": v.get("pins") or {}}


def voice_of(key, root):
    """(the voice this chat reads in, whether it is pinned to it)"""
    v, me = _voices(), "steward" if key == "steward" else str(Path(root).resolve())
    return (v["pins"][me], True) if me in v["pins"] else (v["voice"], False)


def choose_voice(key, root, name=None, pin=None):
    """A voice chosen in a chat (the person's own, or this project's where it is pinned); pin=True keeps the voice it
    reads in now for this project, pin=False lets it follow the person's voice again."""
    from . import voice
    v, me = _voices(), "steward" if key == "steward" else str(Path(root).resolve())
    if name is not None and name not in dict(voice.VOICES):
        raise ValueError(name)
    now, pinned = voice_of(key, root)
    if pin is True:
        v["pins"][me] = name or now
    elif pin is False:
        v["pins"].pop(me, None)
    elif name and pinned:
        v["pins"][me] = name
    elif name:
        v["voice"] = name
    (home() / "voice.json").write_text(json.dumps(v, indent=1))
    return voice_of(key, root)


def queue_keys(key, root):
    """PROVIDER: whether this chat's program has a queue the board can work (queue_keys)."""
    from . import monitor
    return getattr(monitor.provider() if key == "steward" else providers.of(root), "queue_keys", None)


def chat_page(key, root):
    """Chat: the conversation, kept current, and a box to send into it, as Message does into its console."""
    from . import voice
    if voice.ready():
        threading.Thread(target=voice.warm, daemon=True).start()     # loaded before the first ▷
    return (f"<div class='chat' id='chat' data-key='{e(key)}' data-name='{e(Path(root).name if key != 'steward' else 'steward')}' "
            f"data-voice='{1 if voice.ready() else 0}' data-queue='{1 if queue_keys(key, root) else 0}'>{chat_feed(key, root)}</div>"
            f"<form class='composer' id='composer'><input type='hidden' name='p' value='{e(key)}'>"
            "<div class='attached' hidden></div>"
            f"<textarea name='text' rows='1' placeholder='Message {e(root.name if key != 'steward' else 'the steward')}'></textarea>"
            # under the words, as the app has it: attach, reading aloud (its speed, its voice), send; it goes where the box goes
            "<div class='composerbar'><label class='attach' aria-label='Attach photos or files'>+<input type='file' name='file' multiple hidden></label>"
            "<span class='gap'></span><button class='send' aria-label='Send'>↑</button></div></form>"
            # reading aloud's settings, opened from ⋯ beside a reply's ▷
            "<div class='voicesheet' id='voicesheet' hidden><label class='aloud' title='Speak each new reply as it arrives'>"
            "<input type='checkbox' id='aloud'> Auto-speak</label>"
            "<select class='rate' id='rate' aria-label='Reading speed'>"
            + "".join(f"<option value='{r}'>{r:g}×</option>" for r in (1, 1.25, 1.5, 1.75)) + "</select>"
            + (f"<select id='kvoice' aria-label='Voice'>{voice.options(voice_of(key, root)[0])}</select>"
               f"<label class='vpin' title='Keep this voice for this project; unpinned, it uses your voice'>"
               f"<input type='checkbox' id='vpin'{' checked' if voice_of(key, root)[1] else ''}> Pin voice to project</label>" if voice.ready() else "")
            + "<button type='button' class='shut' aria-label='Close'>✕</button></div>"
            "<button class='toend' id='toend' hidden aria-label='Back to the latest'>↓</button><div class='chatheld muted' id='chatheld' hidden></div>"
            f"<script>const ICON = {json.dumps(ICONS)};{CHAT}</script>")


CHAT = """
(() => {
  const box = document.getElementById('chat'), key = box.dataset.key, form = document.getElementById('composer');
  const ta = form.querySelector('textarea'), held = document.getElementById('chatheld');
  // At the end: what is really visible reaches it. With the keyboard up the page's own height still counts the part
  // behind it, so the visible part is read off the visual viewport.
  // An iPhone counts the keyboard's pan in its scrollY and other browsers don't; the visual viewport's pageTop is the
  // same on both, so the visible part is read off it alone.
  const shownTo = () => { const v = window.visualViewport; return v ? v.pageTop + v.height : window.scrollY + window.innerHeight; };
  const atEnd = () => { const seen = shownTo();
    return typingNow() ? seen - form.offsetHeight >= lastBottom() - 40 : seen >= document.body.scrollHeight - 80; };
  // It follows new messages only while the person is at the end; scrolled up, it stays where they are.
  let stick = true;
  window.addEventListener('scroll', () => { stick = atEnd(); }, {passive: true});
  // The latest is the last message. Typing, the page has room below it (so it never ends above the keyboard, where a
  // phone measures the visible part short), and the latest is brought just above the box.
  const typingNow = () => document.body.classList.contains('typing');
  const lastBottom = () => { const el = box.lastElementChild; return el ? el.getBoundingClientRect().bottom + window.scrollY : 0; };
  const toEnd = () => { if (!typingNow()) return window.scrollTo(0, document.body.scrollHeight);
    const by = lastBottom() + form.offsetHeight + 24 - shownTo();
    // a scroll the phone makes while its keyboard is up shifts the whole page for a moment (the bar at the top slips
    // away and back): only a real move is made, by what it needs from where the eye is (counting the keyboard's pan
    // twice swung it back and forth at each look, the bar with it)
    if (Math.abs(by) > 4) window.scrollBy(0, Math.max(by, -window.scrollY)); };
  toEnd();
  let last = box.innerHTML, pending = [];
  // what was just sent shows at once, faded, until the conversation has it (a busy agent takes it up mid-turn)
  // counted against what was there when it was sent, so the same words sent again still show
  const times = (match) => [...box.querySelectorAll('.msg.you')].filter((m) => !m.classList.contains('mine') && m.textContent.includes(match)).length;
  const seen = (p) => times(p.match) > p.before;
  const keep = () => { pending = pending.filter((p) => !seen(p));
    pending.forEach((p) => { const d = document.createElement('div'); d.className = 'msg you pending mine'; d.textContent = p.show; box.appendChild(d); }); };
  const file = form.querySelector('input[type=file]'), chip = form.querySelector('.attached');
  const chosen = () => { const fs = [...file.files]; chip.hidden = !fs.length;
    chip.innerHTML = ''; if (!fs.length) return;
    const name = document.createElement('span'); name.textContent = '📎 ' + fs.map((f) => f.name).join(', '); chip.appendChild(name);
    const x = document.createElement('button'); x.type = 'button'; x.textContent = '×'; x.setAttribute('aria-label', 'Remove the files');
    x.addEventListener('click', () => { file.value = ''; chosen(); }); chip.appendChild(x); };
  file.addEventListener('change', chosen);
  // While a finger is on the page, or it still glides from a flick, nothing is redrawn or moved: a phone stops a
  // gliding page at any change. The next look catches up.
  let moving = 0;
  ['touchstart', 'touchmove', 'scroll', 'wheel'].forEach((t) => window.addEventListener(t, () => { moving = Date.now(); }, {passive: true}));
  async function refresh() {
    if (!box.isConnected || Date.now() - moving < 1200) return;
    // an answer being written in the conversation (to what it needs of the person) is never redrawn away
    if ([...box.querySelectorAll('textarea')].some((t) => t.value.trim()) || box.contains(document.activeElement) &&
        document.activeElement.matches('textarea, select, input')) return;
    try {
      const r = await fetch('/chat/feed?p=' + key, {cache: 'no-store'}); if (!r.ok) return;
      const html = await r.text(); if (html === last) return;
      const open = [...box.querySelectorAll('details[open]')].map((d) => d.dataset.k), end = stick, was = lastBottom();
      const shut = [...box.querySelectorAll('details:not([open])')].map((d) => d.dataset.k);   // what came open, folded by them
      // what the reader is looking at stays where it is, though the oldest turns above drop away as new ones come
      const seen = [...box.querySelectorAll('[data-k]')].find((x) => x.getBoundingClientRect().bottom > 0);
      const mark0 = seen && [seen.dataset.k, seen.getBoundingClientRect().top];
      // only what changed is redrawn: a part already there stays as it is (a picture is not loaded again, so nothing
      // blinks or shifts), the rest comes in new
      const t = document.createElement('template'); t.innerHTML = last = html;
      box.querySelectorAll('.say.reading').forEach((x) => x.classList.remove('reading'));
      const have = new Map();
      [...box.children].forEach((c) => { if (!c.classList.contains('mine')) { const k = c.outerHTML; have.set(k, (have.get(k) || []).concat([c])); } });
      box.replaceChildren(...[...t.content.children].map((n) => { const same = have.get(n.outerHTML); return same && same.length ? same.shift() : n; }));
      keep();
      open.forEach((k) => { const d = box.querySelector(`details[data-k="${CSS.escape(k)}"]`); if (d) d.open = true; });
      shut.forEach((k) => { const d = k && box.querySelector(`details[data-k="${CSS.escape(k)}"]`); if (d) d.open = false; });
      const again = mark0 && !end && box.querySelector(`[data-k="${CSS.escape(mark0[0])}"]`);
      const moved = again ? again.getBoundingClientRect().top - mark0[1] : 0;
      if (Math.abs(moved) >= 1) window.scrollBy(0, moved);
      if (end && (!typingNow() || lastBottom() !== was)) toEnd();       // typing, it moves only when the conversation grew
    } catch (e) {}
  }
  let tick = 0;                                  // every 3 s; reading aloud, twice as often, so the voice starts sooner
  setInterval(() => { if (aloud.checked || ++tick % 2 === 0) refresh(); }, 1500);
  const sendBtn = form.querySelector('button.send');
  const busy = () => { const b = !!box.querySelector('.chatlive.busy'), empty = !ta.value.trim() && !file.files.length;
    sendBtn.textContent = b && empty ? '■' : '↑'; sendBtn.setAttribute('aria-label', b && empty ? 'Stop' : 'Send');
    ta.placeholder = b ? 'Queue for after this turn…' : ta.dataset.idle; };
  ta.dataset.idle = ta.placeholder; busy();
  new MutationObserver(busy).observe(box, {childList: true}); ta.addEventListener('input', busy); file.addEventListener('change', busy);
  const down = document.getElementById('toend');
  const where = () => { down.hidden = atEnd(); };
  window.addEventListener('scroll', where, {passive: true});
  down.addEventListener('click', () => window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'}));
  // Grown by a line while typing, the box is placed again at once and the cursor re-seated: a phone otherwise draws
  // the cursor where the box was before it grew (never while dictated or composed words are still being formed)
  let composing = false;
  ta.addEventListener('compositionstart', () => { composing = true; });
  ta.addEventListener('compositionend', () => { composing = false; });
  const grow = () => { const was = ta.style.height; ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
    if (ta.style.height === was || composing || !document.body.classList.contains('typing')) return;
    onKeys(); requestAnimationFrame(() => { if (composing) return; const a = ta.selectionStart, z = ta.selectionEnd; ta.setSelectionRange(a, z); }); };
  // Typing on a phone: the box sits on the keyboard, the tabs step aside, and the latest stays just above it.
  // The keyboard covers the bottom of the page without resizing it, so its top is read off the visible part.
  const vv = window.visualViewport;
  // placed by its own height rather than shifted up by it: a phone draws the cursor where a shifted box would have been
  const onKeys = () => { if (!vv || !document.body.classList.contains('typing')) return;
    document.documentElement.style.setProperty('--kbtop', (vv.offsetTop + vv.height) + 'px');
    document.documentElement.style.setProperty('--boxh', form.offsetHeight + 'px'); };
  // The keyboard slides up, and the phone reports its size only now and then: while typing, the box is placed again on
  // every frame, so it rides the keyboard up and lands on it.
  let follow = 0;
  const ride = () => { if (!document.body.classList.contains('typing')) return; onKeys(); follow = requestAnimationFrame(ride); };
  ta.addEventListener('focus', () => { if (!matchMedia('(max-width: 700px)').matches) return;
    document.body.classList.add('typing'); cancelAnimationFrame(follow); ride(); setTimeout(toEnd, 350);
    if (!window.__kbSent) { window.__kbSent = true; setTimeout(() => measure('focus'), 1200); }
  });
  // Where the keyboard and the box are, for the board's log, so a gap is fixed from the phone's own numbers: once after
  // the keyboard comes up, and when a scroll settles while typing (a few times a page).
  let measured = 0, settle = 0;
  function measure(when) {
    if (measured++ > 4) return;
    const r = form.getBoundingClientRect(), v = window.visualViewport;
    const m = {when, inner: innerHeight, vvh: v && v.height, vvtop: v && v.offsetTop, vvpage: v && v.pageTop, scroll: scrollY, page: document.body.scrollHeight,
      boxTop: Math.round(r.top), boxBottom: Math.round(r.bottom), kbtop: getComputedStyle(document.documentElement).getPropertyValue('--kbtop'),
      typing: document.body.classList.contains('typing')};
    fetch('/chat/measure', {method: 'POST', body: new URLSearchParams({m: JSON.stringify(m)})}).catch(() => {});
  }
  window.addEventListener('scroll', () => { if (!document.body.classList.contains('typing')) return;
    clearTimeout(settle); settle = setTimeout(() => measure('scroll'), 500); }, {passive: true});
  // a tap into the text while typing: where it landed and which letter the phone put the cursor at, with the page's
  // pan (the person saw the cursor land away from their tap); for the board's log, a few a page, until it is fixed
  let taps = 0;
  ta.addEventListener('touchend', (ev) => { if (!document.body.classList.contains('typing') || taps++ > 5) return;
    const t = ev.changedTouches[0], r = ta.getBoundingClientRect(), v = window.visualViewport;
    setTimeout(() => fetch('/chat/measure', {method: 'POST', body: new URLSearchParams({m: JSON.stringify({when: 'tap',
      x: Math.round(t.clientX - r.left), y: Math.round(t.clientY - r.top), w: Math.round(r.width), at: ta.selectionStart, of: ta.value.length,
      near: ta.value.slice(Math.max(0, ta.selectionStart - 12), ta.selectionStart + 12), vvtop: v && v.offsetTop, vvh: v && v.height,
      boxTop: Math.round(r.top), scroll: scrollY})})}).catch(() => {}), 120); }, {passive: true});
  // Back on the page with the keyboard still up (after a lock), the box is placed again and the cursor re-seated
  // once things settle, since a phone can draw both from before; and its numbers go to the board's log.
  const settleBox = () => { if (!document.body.classList.contains('typing')) return;
    onKeys(); setTimeout(() => { onKeys(); const a = ta.selectionStart, b = ta.selectionEnd; ta.setSelectionRange(a, b);
      measured = Math.min(measured, 3); measure('back'); }, 400); };
  // Leaving the page, the box lets go of the keyboard (its draft kept): a phone could bring the page back with the box
  // still holding focus and no keyboard, measuring as though one were up, and the box hung where the keyboard's top
  // had been, the tabs hidden (the person's screenshot, Oct 24). Back, a tap on the box brings the keyboard.
  const away = () => { if (document.activeElement === ta) ta.blur(); };
  document.addEventListener('visibilitychange', () => { if (document.hidden) away(); else settleBox(); });
  window.addEventListener('pagehide', away);
  window.addEventListener('pageshow', settleBox);
  ta.addEventListener('blur', () => { document.body.classList.remove('typing'); document.documentElement.style.removeProperty('--kbtop'); });
  if (vv) { vv.addEventListener('resize', onKeys); vv.addEventListener('scroll', onKeys); }
  // ↑ keeps the box in focus until it sends: the box doesn't move under the tap (then the keyboard goes down)
  form.querySelector('button.send').addEventListener('pointerdown', (ev) => ev.preventDefault());
  // A phone still takes the focus away as the finger lifts: the keyboard drops, the box moves down, and the tap lands
  // where the button was, sending nothing. It sends on the lift instead, before anything moves.
  sendBtn.addEventListener('touchend', (ev) => { ev.preventDefault(); form.requestSubmit ? form.requestSubmit() : sendBtn.click(); });
  ta.addEventListener('input', grow);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const text = ta.value.trim(), fs = [...file.files];
    if (!text && !fs.length) {
      if (box.querySelector('.chatlive.busy')) { await fetch('/chat/stop', {method: 'POST', body: new URLSearchParams({p: key})}).catch(() => {});
        setTimeout(refresh, 800); }
      return;
    }
    // it goes out at once: the box clears and the message shows while the board hands it over, however long that takes
    const body = new FormData(form), mine = {show: (fs.length ? '📎 ' + fs.map((x) => x.name).join(', ') + (text ? '\\n' : '') : '') + text,
      match: text || fs[0].name.replace(/\\.[^.]*$/, '')};
    mine.before = times(mine.match);
    ta.value = ''; grow(); file.value = ''; chosen(); busy(); held.hidden = true;
    ta.blur();                                       // sent: the keyboard goes down, the conversation in view
    pending.push(mine); box.querySelectorAll('.mine').forEach((m) => m.remove()); keep(); toEnd();
    const r = await fetch('/chat/send', {method: 'POST', body}).catch(() => null);
    if (r && r.ok) return setTimeout(refresh, 600);
    // not sent: it comes back to the box, ahead of anything typed since, its files with it
    pending = pending.filter((p) => p !== mine); box.querySelectorAll('.mine').forEach((m) => m.remove()); keep();
    if (text) ta.value = text + (ta.value.trim() ? '\\n\\n' + ta.value : '');
    if (fs.length) { try { const dt = new DataTransfer(); [...fs, ...file.files].forEach((x) => dt.items.add(x)); file.files = dt.files; } catch (e) {} }
    grow(); chosen(); busy();
    held.hidden = false;
    held.textContent = r && r.status === 409 ? 'Not sent: something is half-typed in its console. Send or clear it there, then send this again.'
      : r && r.status === 503 ? await r.text() : r ? 'Not sent: the board could not hand it over (' + r.status + ').' : 'Not sent: the board could not be reached.';
  });
  // Read aloud: the board's player, over every page, reads; this chat hands it the replies and shows where it is
  const P = window.colonyPlayer, aloud = document.getElementById('aloud'), sheet = document.getElementById('voicesheet');
  const plain = (els) => els.map((el) => { const c = el.cloneNode(true); c.querySelectorAll('pre').forEach((p) => p.remove());
    return c.innerText; }).join('\\n\\n').replace(/[*_`#>|]/g, '').trim();
  const KOKORO = box.dataset.voice === '1', picker = document.getElementById('kvoice'), pin = document.getElementById('vpin');
  let kvoice = picker ? picker.value : 'am_echo';
  const view = {name: box.dataset.name, key, href: location.pathname + location.search, kokoro: KOKORO, get kvoice() { return kvoice; },
    mark: () => mark(), focus: (k, go) => focus(k, go)};
  // a section: one part of a reply as it was written, with its time
  const sec = (x) => ({t: x.dataset.t, k: x.dataset.k, at: x.dataset.at || '', text: plain([x])});
  // ▷ reads all the agent said since the person's own message before it, across colony's notices in between
  const turn = (t) => {
    const all = [...box.querySelectorAll('.say')], end = all.map((x) => x.dataset.t).lastIndexOf(t);
    const you = [...box.querySelectorAll('.msg.you:not(.mine)')].filter((m) => m.compareDocumentPosition(all[end]) & Node.DOCUMENT_POSITION_FOLLOWING).pop();
    return all.slice(0, end + 1).filter((x) => !you || (you.compareDocumentPosition(x) & Node.DOCUMENT_POSITION_FOLLOWING)).map(sec);
  };
  // ▷ or ■ on each reply; the newest reply's ⋯ says Auto while new replies are read by themselves
  // ▷, then a spinner while the voice is being made, then ■ once it is heard; pause and skip come with the sound
  const mark = () => { if (!box.isConnected) return;
    const s = P.of(view.name), on = s && s.playing ? s.t : null;
    box.querySelectorAll('button.listen').forEach((b) => {
      const st = b.dataset.t !== on ? 'play' : s.loading ? 'load' : 'stop';
      if (b.dataset.on !== st) { b.dataset.on = st; b.innerHTML = ICON[st]; } });
    box.querySelectorAll('button.skip, button.hold').forEach((b) => { b.hidden = b.dataset.t !== on || s.loading; });
    box.querySelectorAll('button.cont').forEach((b) => { b.hidden = !s || s.playing || b.dataset.t !== s.t || !s.into; });
    box.querySelectorAll('button.hold').forEach((b) => { const p = !!(s && s.paused); if (b.dataset.p !== String(p)) { b.dataset.p = p;
      b.innerHTML = p ? ICON.play : ICON.pause; b.setAttribute('aria-label', p ? 'Resume' : 'Pause'); } });
    box.querySelectorAll('button.vset').forEach((b) => { b.classList.toggle('on', !!(aloud && aloud.checked)); });
    focus(s && s.active ? s.k : null, false); };
  // Follow in chat (the player's list): the section being heard is marked, and as each starts it is brought into view
  // below the bar, unless a finger moved the page just now or the box is being typed in
  let fingered = 0;
  ['touchstart', 'touchmove', 'wheel'].forEach((t) => window.addEventListener(t, () => { fingered = Date.now(); }, {passive: true}));
  const focus = (k, go) => { if (!box.isConnected) return;
    const follow = P.following();
    box.querySelectorAll('.say.reading').forEach((x) => { if (!follow || x.dataset.k !== k) x.classList.remove('reading'); });
    const el = follow && k && box.querySelector(`.say[data-k="${CSS.escape(k)}"]`); if (!el) return;
    el.classList.add('reading');
    if (!go || typingNow() || Date.now() - fingered < 4000) return;
    const nav = document.querySelector('nav'), under = nav && getComputedStyle(nav).position === 'sticky' ? nav.getBoundingClientRect().bottom : 0;
    stick = false; window.scrollBy({top: el.getBoundingClientRect().top - under - 12, behavior: 'smooth'}); };
  sheet.querySelector('.shut').addEventListener('click', () => { sheet.hidden = true; });
  document.addEventListener('click', (ev) => { if (!sheet.hidden && !ev.target.closest('#voicesheet, button.vset')) sheet.hidden = true; });
  // its speed, remembered on this device
  const rateBtn = document.getElementById('rate');
  rateBtn.value = String(P.rate());
  rateBtn.addEventListener('change', () => P.setRate(+rateBtn.value || 1));
  // the voice is the board's to keep: the person's own, or this project's where it is pinned
  const keepVoice = async (body) => {
    const r = await fetch('/chat/voice', {method: 'POST', body: new URLSearchParams(Object.assign({p: key}, body))}).catch(() => null);
    if (!r || !r.ok) return;
    const got = await r.json(), changed = got.voice !== kvoice;
    kvoice = picker.value = got.voice; pin.checked = got.pinned;
    if (changed) P.setVoice(view.name, kvoice);
  };
  document.addEventListener('colony-voice', (ev) => { const d = ev.detail; if (!picker) return;
    if (d.name === view.name) { kvoice = picker.value = d.voice; pin.checked = d.pinned; }
    else if (!d.pinned && !pin.checked) kvoice = picker.value = d.voice; });
  if (picker) { picker.addEventListener('change', () => keepVoice({voice: picker.value}));
    pin.addEventListener('change', () => keepVoice({pin: pin.checked ? '1' : '0'}));
    // a voice once kept on this device for this project stays with it, as its pin
    try { const old = localStorage.getItem('kvoice:' + box.dataset.name);
      if (old) { localStorage.removeItem('kvoice:' + box.dataset.name); if (!pin.checked && old !== kvoice) keepVoice({voice: old, pin: '1'}); } } catch (e) {} }
  // What has been read is remembered on this device, for each project: coming back (a reload, another project and
  // back) with Auto-speak on, it reads what came since, up to the last ten parts after the person's own last message.
  const HKEY = 'heard:' + box.dataset.name, BACK = 10;
  let before = null; try { before = JSON.parse(localStorage.getItem(HKEY) || 'null'); } catch (e) {}
  const says = [...box.querySelectorAll('.say')], mineLast = [...box.querySelectorAll('.msg.you')].pop();
  const since = says.filter((x) => !mineLast || (mineLast.compareDocumentPosition(x) & Node.DOCUMENT_POSITION_FOLLOWING)).slice(-BACK);
  const heard = new Set(says.filter((x) => !since.includes(x) || (before && before.includes(x.dataset.k))).map((x) => x.dataset.k));
  const remember = () => { try { localStorage.setItem(HKEY, JSON.stringify([...heard].slice(-300))); } catch (e) {} };
  try { aloud.checked = localStorage.getItem('aloud') === '1'; } catch (e) {}
  aloud.addEventListener('change', () => { mark();
    try { localStorage.setItem('aloud', aloud.checked ? '1' : '0'); } catch (e) {}
    if (aloud.checked) P.unlock();                 // a tap lets the phone speak later
    else { const s = P.of(view.name); if (s && s.active) P.stop(); }
  });
  // Each part of a reply is read as it lands (the transcript holds a part only once it is written whole), after any
  // still being read: waiting for the turn to end would hold the voice back for as long as the work takes.
  const fresh = () => { if (!box.isConnected) return;
    const news = [...box.querySelectorAll('.say')].filter((x) => !heard.has(x.dataset.k));
    news.forEach((x) => heard.add(x.dataset.k)); if (news.length) remember();
    if (!aloud.checked || !news.length) return;    // what came while it was off isn't read later
    P.read(view, news.map(sec), {auto: true});
  };
  new MutationObserver(() => { mark(); fresh(); }).observe(box, {childList: true});
  // What waits in its queue, sent mid-turn: Send now, or Edit and reorder, taken back out of the queue meanwhile so
  // none of it goes; Done puts it back in the new order. PROVIDER: queue_keys (where its program has none, no bar).
  const qpost = (body) => fetch('/chat/queue', {method: 'POST', body: new URLSearchParams(Object.assign({p: key}, body))}).catch(() => null);
  const qbar = document.createElement('div'); qbar.className = 'qbar';
  qbar.innerHTML = "<button type='button' class='qnow'>Send now</button><button type='button' class='qedit'>Edit or reorder</button>";
  let editor = null;
  const placeBar = () => { const q = [...box.querySelectorAll('.msg.queued')];
    if (box.dataset.queue !== '1' || editor || !q.length) { if (qbar.isConnected) qbar.remove(); return; }
    if (qbar.previousElementSibling !== q[q.length - 1]) q[q.length - 1].after(qbar); };
  new MutationObserver(placeBar).observe(box, {childList: true}); placeBar();
  qbar.querySelector('.qnow').addEventListener('click', async () => { qbar.remove(); await qpost({action: 'now'}); setTimeout(refresh, 800); });
  qbar.querySelector('.qedit').addEventListener('click', async () => {
    const was = [...box.querySelectorAll('.msg.queued')].map((m) => m.textContent);
    const r = await qpost({action: 'back'});
    if (!r || !r.ok) { held.hidden = false; held.textContent = 'Not taken back: something is half-typed in its console.'; return; }
    box.classList.add('editingq'); editor = document.createElement('div'); editor.className = 'qeditor'; box.after(editor);
    let items = was.slice();
    const draw = () => { editor.innerHTML = "<p class='muted'>Out of the queue while you edit: nothing goes until Done.</p>";
      items.forEach((text, i) => { const row = document.createElement('div'); row.className = 'qrow';
        row.innerHTML = "<textarea rows='2'></textarea><span class='qmoves'><button type='button' data-m='-1' aria-label='Earlier'>↑</button>"
          + "<button type='button' data-m='1' aria-label='Later'>↓</button><button type='button' data-m='x' aria-label='Remove'>✕</button></span>";
        const ta2 = row.querySelector('textarea'); ta2.value = text; ta2.addEventListener('input', () => { items[i] = ta2.value; });
        row.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
          if (b.dataset.m === 'x') items.splice(i, 1);
          else { const j = i + +b.dataset.m; if (j < 0 || j >= items.length) return; [items[i], items[j]] = [items[j], items[i]]; }
          draw(); }));
        editor.appendChild(row); });
      const foot = document.createElement('div'); foot.className = 'qfoot';
      foot.innerHTML = "<button type='button' class='qcancel quiet'>Cancel</button><button type='button' class='qdone'>Done</button>";
      foot.querySelector('.qdone').addEventListener('click', () => put(items));
      foot.querySelector('.qcancel').addEventListener('click', () => put(was));
      editor.appendChild(foot); };
    const put = async (list) => { const r2 = await qpost({action: 'put', msgs: JSON.stringify(list.filter((x) => x.trim()))});
      if (!r2 || !r2.ok) { held.hidden = false; held.textContent = "Not put back: it's in its console's box, as you left it."; }
      editor.remove(); editor = null; box.classList.remove('editingq'); setTimeout(refresh, 600); placeBar(); };
    draw();
  });
  // opening something (a decision, a run of work) keeps the reader where they are: it no longer follows the end
  box.addEventListener('toggle', () => { stick = atEnd(); }, true);
  // Needs you opened by a tap: its top comes to the top of what is visible, below the bar, to be read from the start
  box.addEventListener('click', (ev) => {
    const s = ev.target.closest('details.needs > summary'); if (!s || s.parentElement.open) return;
    requestAnimationFrame(() => { const d = s.parentElement, bar = document.querySelector('nav');
      const under = bar && getComputedStyle(bar).position === 'sticky' ? bar.getBoundingClientRect().bottom : 0;
      const at = d.querySelector('.round.now') || d;            // asked again: the latest round, where it stands now
      window.scrollBy(0, at.getBoundingClientRect().top - under - 12); stick = false; });
  });
  // Dictating into the box: the voice would be heard by the phone too, so reading pauses as soon as dictated words
  // arrive (as underlined words, or a run of them with no key pressed), and goes on once they have stopped coming
  // for a couple of seconds, or the message is sent.
  let keyed = 0, was = ta.value.length, byVoice = false, quiet = 0;
  ta.addEventListener('keydown', () => { keyed = Date.now(); });
  const goOn = () => { clearTimeout(quiet); if (byVoice && P.now().paused) P.hold(false); byVoice = false; };
  const dictating = () => { const n = P.now(); if (n.playing && !n.paused) { P.hold(true); byVoice = true; }
    if (byVoice) { clearTimeout(quiet); quiet = setTimeout(goOn, 2200); } };
  ta.addEventListener('compositionstart', dictating);
  ta.addEventListener('compositionupdate', () => { if (byVoice) dictating(); });
  ta.addEventListener('input', (ev) => { const grew = ta.value.length - was; was = ta.value.length;
    if (ev.inputType !== 'insertFromPaste' && (grew > 12 || byVoice && grew > 0) && Date.now() - keyed > 400) dictating(); });
  form.addEventListener('submit', goOn);
  // On coming back, what came since is read at once where the browser lets a page speak by itself; a phone lets it only
  // after a tap, so there it starts with the first touch anywhere on the page.
  P.attach(view);
  if (aloud.checked && says.some((x) => !heard.has(x.dataset.k))) P.whenAllowed(fresh);
  // an answer to what it needs goes from here, the page staying where it is
  box.addEventListener('submit', async (ev) => {
    const f = ev.target.closest('form[action="/answer"]'); if (!f) return;
    ev.preventDefault();
    const d = new FormData(f); if (ev.submitter && ev.submitter.name) d.append(ev.submitter.name, ev.submitter.value);
    const r = await fetch('/answer', {method: 'POST', body: f.enctype === 'multipart/form-data' ? d : new URLSearchParams(d), redirect: 'manual'}).catch(() => null);
    if (!r || !(r.ok || r.type === 'opaqueredirect')) { const p = document.createElement('p'); p.className = 'muted'; p.textContent = 'Not sent: try again.'; f.appendChild(p); return; }
    f.querySelectorAll('textarea, input[type=file]').forEach((t) => { t.value = ''; }); document.activeElement && document.activeElement.blur();
    refresh();
  });
  box.addEventListener('click', async (ev) => {
    if (ev.target.closest('button.vset')) { sheet.hidden = !sheet.hidden; return; }
    const cp = ev.target.closest('button.copy');
    if (cp) {                            // the reply as text, code and all; a plain-http page has no clipboard API
      const text = [...box.querySelectorAll(`.say[data-t="${CSS.escape(cp.dataset.t)}"]`)].map((x) => x.innerText).join('\\n\\n');
      let ok = false;
      try { const t = document.createElement('textarea'); t.value = text; t.setAttribute('readonly', ''); t.style.position = 'fixed';
        t.style.opacity = '0'; document.body.appendChild(t); t.select(); t.setSelectionRange(0, text.length);
        ok = document.execCommand('copy'); t.remove(); } catch (e) {}
      if (!ok && navigator.clipboard) { try { await navigator.clipboard.writeText(text); ok = true; } catch (e) {} }
      cp.innerHTML = ok ? ICON.check : '✗'; setTimeout(() => { cp.innerHTML = ICON.copy; }, 1500); return;
    }
    // ⏸ holds the reading where it is, ▷ takes it up from there
    if (ev.target.closest('button.hold')) return P.hold(!P.now().paused);
    if (ev.target.closest('button.skip')) return P.skip();
    if (ev.target.closest('button.cont')) return P.resume(view.name);           // from the piece it stopped at
    const l = ev.target.closest('button.listen');
    if (l) { const s = P.of(view.name), t = l.dataset.t;
      if (s && s.playing && s.t === t) P.stop();
      else if (s && !s.playing && s.t === t) P.again(view, turn(t));               // from the start, what waited after it
      else P.read(view, turn(t), {now: true});
      return; }
    const b = ev.target.closest('button[data-option]'); if (!b) return;
    await fetch('/choose', {method: 'POST', body: new URLSearchParams({p: key, option: b.dataset.option})}).catch(() => {});
    setTimeout(refresh, 600);
  });
})();
"""


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
    # pinning lives here too: with nothing pinned yet, the page has no Pinned section to start from
    pin = f"<p class='pinlink'><a href='/pins/add?p={pid}'>+ Pin a file, link or upload, for you both to keep at hand</a></p>"
    return (f"<details class='{cls}'><summary>Message</summary>"
            + (f"<div class='panel'>{form}{pin}</div>" if cls == "psettings" else form) + "</details>")


def rules_box(root, pid):
    """The person's standing rules for this project, folded under its vision, each retirable with a tap."""
    from . import rules
    found = rules.rules(root)
    if not found:
        return ""
    items = "".join(f"<li>{e(r['text'])} <span class='muted'>(your words: {e(r['words'])})</span>"
                    f"<form class='inline' method='post' action='/rule'><input type='hidden' name='p' value='{pid}'>"
                    f"<input type='hidden' name='id' value='{e(r['id'])}'><button class='quiet'>Retire</button></form></li>"
                    for r in found)
    return (f"<details class='vision'><summary><b>Standing rules</b> · {len(found)}, kept for its agent across "
            f"compactions</summary><ul>{items}</ul></details>")


def vision_box(root, pid):
    from . import vision
    road = roadmap(root)
    changes = [r for r in vision.history(root) if r['how'] != 'baseline']
    current = (prose(road['vision']) if road['vision'] else
               f"<p class='muted'>{e(road['goal']) or 'Shape the vision with your project agent.'}</p>")
    rows = ''.join(f"<li><span class='who'>{e(r['at'])} · {e(r['how'])}</span>"
                   f"<div>{prose(r['text']) or '(vision cleared)'}</div>"
                   + (f"<p>{e(r['words'])}</p>" if r.get('words') else '')
                   + (f"<p class='muted'>Reading: {e(r['context'])}</p>" if r.get('context') else '') + '</li>' for r in reversed(changes))
    summary = first_sentence_of(road['vision'] or road['goal'])
    return (f"<details class='vision'><summary><b>Vision</b>{' · ' + e(summary) if summary else ''}</summary>{current}"
            f"<details><summary>Edit vision</summary><form class='options' method='post' action='/vision'>"
            f"<input type='hidden' name='p' value='{pid}'>"
            f"<textarea name='before' hidden>{e(road['vision'])}</textarea>"
            f"<label>The finished work on the horizon<textarea name='text' rows='6'>{e(road['vision'] or road['goal'])}</textarea></label>"
            "<label>What changed or became clearer? (optional)<input name='words'></label>"
            "<button>Save vision</button><p class='muted'>Saving updates the vision and tells your project agent "
            "to consider its effect on the work at hand.</p></form></details>"
            + (f"<details><summary>Vision history ({len(changes)})</summary><ul>{rows}</ul></details>" if changes else '')
            + '</details>')


def _inline(text):
    """Escaped text with **bold** and *emphasis* and `code` shown as such."""
    text = e(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", text)


def prose(text):
    """Markdown written in a file, as a page shows it: paragraphs flow (a line wrapped in the file isn't a break on
    the page), lists stay lists, headings stay headings, tables are tables (scrolling sideways on a phone if wide)
    and a quote is set off."""
    out, para, items, rows, quote = [], [], [], [], []

    def cells(line):
        return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]

    def flush():
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(' '.join(i))}</li>" for i in items) + "</ul>")
            items.clear()
        if rows:
            head = len(rows) > 1 and all(re.fullmatch(r":?-{3,}:?", c) for c in cells(rows[1]))
            body = rows[2:] if head else rows
            out.append("<div class='mdtable'><table>"
                       + (f"<tr>{''.join(f'<th>{_inline(c)}</th>' for c in cells(rows[0]))}</tr>" if head else "")
                       + "".join(f"<tr>{''.join(f'<td>{_inline(c)}</td>' for c in cells(r))}</tr>" for r in body)
                       + "</table></div>")
            rows.clear()
        if quote:
            out.append(f"<blockquote>{prose(chr(10).join(quote))}</blockquote>")
            quote.clear()
    for raw in (text or "").splitlines():
        line = raw.strip()
        bullet = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)", line)
        if line.startswith("|"):
            if not rows:
                flush()
            rows.append(line)
            continue
        if line.startswith(">"):
            if not quote:
                flush()
            quote.append(line[1:].lstrip())
            continue
        if rows or quote:
            flush()
        if not line:
            flush()
        elif line.startswith("#"):
            flush()
            out.append(f"<h3>{_inline(line.lstrip('#').strip())}</h3>")
        elif bullet:
            if para:
                flush()
            items.append([bullet.group(1)])
        elif items and raw[:1] in (" ", "\t"):
            items[-1].append(line)                       # a list item wrapped onto the next line
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return "".join(out)


def first_sentence_of(text):
    """The vision's opening sentence, plain, for its collapsed heading."""
    for block in re.split(r"\n\s*\n", text or ""):
        plain = re.sub(r"[*_`#>]", "", " ".join(block.split())).strip(" -")
        if plain:
            m = re.match(r"(.+?[.!?])(\s|$)", plain)
            s = (m.group(1) if m else plain)
            return s if len(s) <= 180 else s[:177].rsplit(" ", 1)[0] + "…"
    return ""


def lead_is_shared(root):
    from . import lead
    g = lead.group(root)
    return bool(g and len(g['members']) > 1)


def lead_form(root, pid):
    """Which of a shared project's agents leads it (edits the plan): one of its settings."""
    from . import lead
    g = lead.group(root)
    if not g or len(g['members']) < 2:
        return ''
    options = ''.join(f"<option value='{e(member)}'{' selected' if member == g['lead'] else ''}>{e(Path(member).name)}</option>" for member in g['members'])
    return (f"<hr><form method='post' action='/lead/switch'><input type='hidden' name='p' value='{pid}'>"
            f"<label>Project lead <select name='member'>{options}</select></label><button class='quiet'>Switch lead</button></form>"
            + ("<p>Handoff is landing; the incoming lead waits for the current turn and committed work.</p>" if g.get('handoff') else ''))


def version_hold(root):
    """What holds a shared plan's version now, read live (the record of the last hold outlasts it once no version
    runs): (reason, [(member, gate)]: the open decisions that hold it, each where it waits), or None."""
    from . import lead, progress
    g = lead.group(root)
    c = progress.current(root) if g else None
    if not c or c['state'] not in ('active', 'corrections'):
        return None
    reason = progress.hold(root, root)
    if not reason:
        return None
    blocking = [(Path(m), gate) for m in g['members'] for gate in gates(Path(m))
                if due(gate) and (not gate.get('item') or gate['item'] in c['items'])] if reason == 'blocking decision' else []
    return reason, blocking


def where_waiting(member):
    """A link to the project a decision waits in, on its Overview."""
    at = next((i for i, p in enumerate(projects()) if p.resolve() == Path(member).resolve()), None)
    return f"<a href='/?p={at}'>{e(Path(member).name)}</a>" if at is not None else e(Path(member).name)


def progress_panel(root, pid):
    """A shared plan's versions, on its Roadmap tab, only while there is one to run, review or start: the version under
    way, what holds it now and the decisions holding it, Pause and Continue; or the planned ones, to start one."""
    from . import lead, progress, continuation
    g = lead.group(root)
    if not g or not g['checkpoints']:
        return ''
    hidden = f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='back' value='/?p={pid}&view=roadmap'>"
    parts, c = [], progress.current(root)
    if c:
        summary = f"{c['outcome']} · {c['state']}"
        parts.append(f"<p><b>{e(c['outcome'])}</b> · {e(c['state'])}</p><p>{e(c['definition'])}</p>")
        held = version_hold(root)
        if held:
            parts.append(f"<p class='muted'>Held: {e(held[0])}</p>" + "".join(
                f"<p>Waiting on you in {where_waiting(m)}: {e(gate['question'])}</p>" for m, gate in held[1]))
        error = continuation.status(root).get('error')
        if error:
            parts.append(f"<p class='muted'>{e(error)}</p>")
        parts.append(f"<form method='post' action='/progress/control'>{hidden}"
                     "<button class='quiet' name='action' value='pause'>Pause work</button>"
                     "<button class='quiet' name='action' value='resume'>Continue current version</button></form>")
        if c['state'] == 'review' and progress.waiting(root)[0]['key'] in dismissed(root):
            # Visible even after Clear; hiding a card never grants approval.
            parts.append("<p>Version review is hidden from Needs you; it remains unapproved.</p>"
                         f"<form method='post' action='/progress/control'>{hidden}<button class='quiet' name='action' value='show'>Show version review</button></form>")
    else:
        planned = [v for v in g['checkpoints'] if v['state'] == 'planned']
        if not planned:
            return ''                                   # every version accepted: nothing to run, review or start
        summary = f"{len(planned)} planned"
        opts = ''.join(f"<option value='{e(v['id'])}'>{e(v['outcome'])}</option>" for v in planned)
        parts.append(f"<form method='post' action='/progress/control' class='startver'>{hidden}<select name='checkpoint'>{opts}</select>"
                     "<button name='action' value='start'>Start selected version</button></form>")
    return (f"<details class='card versions'{' open' if c else ''}><summary><b>Versions</b> · {e(summary)}</summary>"
            + "".join(parts) + "</details>")


def switch_line(root, name=None, label=None):
    """While a console runs another program than the one now set for it, said where the person looks: a switch still
    waiting for its safe moment doesn't read as one that didn't take."""
    s = console.switching(root, name, label)
    if not s:
        return ""
    want, ran = (providers.PROVIDERS[k].label for k in s)
    return (f"<p class='muted switching'>Set to {e(want)} · still running {e(ran)}: it switches as soon as nothing is "
            f"under way there (no turn or background work, nothing typed, no one in its terminal).</p>")


def render(reg, pid, view="overview", show_all=""):
    """A project's page: its Overview, Today, Roadmap, Showcase or Console. show_all names a record on the Roadmap tab
    (history, messages, mail) to show in full."""
    plist = projects(reg)
    out = []
    if not plist:
        return shell(reg, pid, "")
    view = view if view in ("roadmap", "library", "showcase", "console", "chat", "connections") else "overview"  # Today is Overview now
    if view == "connections":                # reached from its Settings: no tab of its own
        root = plist[pid]
        return shell(reg, pid, f"<header class='slim'><h1>{e(root.name)} · Connections</h1>{tabs(pid, view, root)}</header>"
                     + project_connections(pid, root))
    if view == "chat":
        root = plist[pid]
        return shell(reg, pid, f"<header class='slim'><h1>{e(root.name)}</h1>{tabs(pid, view, root)}{switch_line(root)}</header>"
                     + chat_page(str(pid), root))
    if view == "console":
        root = plist[pid]
        message = message_form(pid, plist, "msgbox")
        if not providers.installed(providers.of(root)) and not console.live(root):
            return shell(reg, pid, f"<header class='slim'><h1>{e(root.name)}</h1>{tabs(pid, view, root)}</header>{vision_box(root, pid)}"
                         f"<div class='card'><p>{e(providers.missing(providers.of(root)))}, so its console can't start.</p>"
                         f"<p class='muted'>Install it, or choose another provider in this project's Settings.</p></div>")
        return shell(reg, pid, f"<header class='slim'><h1>{e(root.name)}</h1>{tabs(pid, view, root)}{message}{switch_line(root)}</header>"
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
                f"<p class='muted'>Provider, model, effort and Remote Control apply when its console next starts; helper tiers at once.</p>"
                f"{remote_line(root)}<p><a href='/?p={pid}&view=connections'>Connections →</a></p>{lead_form(root, pid)}<hr>{nest_form(pid, root)}<hr><div class='dangers'>"
                f"<p class='muted'>Archive takes it off the board and the steward's watch (at work, it finishes that turn "
                f"first); All projects → Archived brings it back as it was, the same conversation.</p>"
                f"<form method='post' action='/project/archive'><input type='hidden' name='p' value='{pid}'>"
                f"<input name='why' placeholder='Why (optional; its agent reads it when it comes back)'>"
                f"<button class='quiet'>Archive</button></form>"
                f"<form method='post' action='/project/delete' onsubmit=\"return confirm('Delete {e(root.name)}? Its session stops and its folder moves to colony\\'s trash ({e(home() / 'trash')}), where you can restore it.')\">"
                f"<input type='hidden' name='p' value='{pid}'><button class='danger'>Delete project</button></form></div></div></details>")
    # Message sits on the title's line too, where a phone reaches it (its console is full screen)
    message = message_form(pid, plist, "psettings")
    # pinned or not, it says so beside Message, and a tap changes it
    pinned = str(root.resolve()) in pinned_projects()
    pin = (f"<a class='ppin{' on' if pinned else ''}' href='#' onclick='return pinProject(event, {pid}, {0 if pinned else 1})'>"
           f"<span class='ppinmark'>📌</span>{'Unpin' if pinned else 'Pin'}</a>")
    # time its agent has spent at work, all told: turns only, never the time it sat waiting
    secs = getattr(providers.of(root), "active_seconds", lambda r: None)(root)
    active = f" · Active: {span(secs)}" if secs and secs >= 60 else ""
    # the day's page opens on the day itself: the vision and the project's state are a tab away. The status says what
    # the project is at, from its roadmap, and whether anything waits on the person.
    waiting = len(moments(root)) if view == "overview" else 0
    lead_status = status_card(pid, console.snapshot(root), at_work(root), waiting) if view == "overview" else ""
    out.append(f"<header class='project'><div class='titlerow'><h1>{e(root.name)}</h1>{pin}{message}{settings}</div>{tabs(pid, view, root)}"
               + switch_line(root) + lead_status + f"{vision_box(root, pid)}{rules_box(root, pid) if view == 'overview' else ''}"
               f"<p class='muted'>Roadmap: {done}/{total}{active}</p>" + "</header>")
    if view == "showcase":
        out.append(showcase_html(pid, root))
    elif view == "library":                 # what the project knows and has made, found where it is
        out.append(library_html(pid, root, show_all))
    elif view == "roadmap":                 # the plan and its record: the roadmap, notes, history, mail
        out.append(progress_panel(root, pid))       # a shared plan's versions, while one runs or waits to start
        # the person's own notes the agent has not acted on yet, wherever they were left
        its_now = items(road)
        mine = [n for n in all_notes if not n["addressed_at"] and not (n["anchor"] or {}).get("gate")]
        if mine:
            out.append(f"<h2>Your notes, not yet acted on ({len(mine)})</h2><div class='card'>"
                       + "".join(f"<div class='note'><span class='who'>{e(where(n))} · {e(n['at'][:10])}</span>"
                                 f"<div>{e(n['text'])}</div><div class='who'>{e(status(n, its_now))}</div></div>" for n in mine)
                       + "</div>")
        out.append(measures_html(root))
        # roadmap: the list reads best; the map shows the shape, open by default only when the plan branches
        out.append("<h2 id='roadmap'>Roadmap</h2>")
        from . import hints
        every, kept = hints.cached(root), hints.settled(root)
        found = hints.open_hints(root, every)
        done = [(k, t) for k, _, t in every if k in kept]
        if found or done:               # where the plan may have drifted from its work, read by rule: the agent's to judge
            out.append(f"<details class='card fold hints'><summary><h3>Shape hints</h3><span class='muted'>{len(found)}</span>"
                       f"</summary><ul>{''.join(f'<li>{e(t)}</li>' for _, _, t in found)}</ul>"
                       + (f"<p class='muted small'>Settled by the agent under the present vision:</p><ul class='muted small'>"
                          + "".join(f"<li>{e(t)} <i>{e(kept[k]['why'])}</i></li>" for k, t in done) + "</ul>" if done else "")
                       + "<p class='muted small'>Read from the plan and its history, by rule; the agent hears the open ones "
                         "when a milestone finishes.</p></details>")
        if road["milestones"]:
            its = items(road)
            order = list(its)
            branched = any(it["links"] for it in its.values())          # only what the plan says waits on what
            out.append(journey_html(road, item_times(root)))
            if branched:                   # the map only where the plan itself says what waits on what
                out.append(f"<details class='mapbox'><summary>Map of the roadmap"
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
                out.append(f"<details id='ms-{e(m['id'])}' class='{'ms' if finished(m) else 'card ms'}'><summary>{dot}<h3>{e(m['id'])}{' — ' + e(m['title']) if m['title'] else ''}</h3>"
                           f"<span class='muted'>{done_m}/{len(m['items'])} {elapsed([times.get(i['id'], {}) for i in m['items']], finished(m))}</span></summary>")
                doing = sorted((i for i in m["items"] if i["state"] == "doing"), key=when, reverse=True)
                done_i = sorted((i for i in m["items"] if i["state"] == "done"), key=when, reverse=True)
                drawn = {}
                for it in doing + [i for i in m["items"] if i["state"] not in ("doing", "done")] + done_i:
                    ns = by("item", it["id"])
                    waiting = sum(1 for g in gs if g.get("item") == it["id"] and due(g))
                    unlocks = [x for x, y in its.items() if it["id"] in y["after"]]
                    drawn[it["id"]] = (
                        f"<details class='item {it['state']}'><summary><i class='jd {it['state']}' title='{LABEL[it['state']]}'></i> "
                        f"<span class='ititle'>{e(lead_clause(it['text'], 90))}</span> <span class='muted iid'>{e(it['id'])}</span>"
                        + (f" <span class='muted'>primary: {e(Path(it['owner']).name)}</span>" if lead_is_shared(root) else '')
                        + (f" <span class='muted'>after {e(', '.join(it['links']))}</span>" if it["links"] else "")
                        + (f" <span class='badge gate'>{waiting} waiting</span>" if waiting else "")
                        + (f" <span class='badge'>{len(ns)} notes</span>" if ns else "")
                        + (f" {timer(times[it['id']])}" if it["id"] in times else "")
                        + f"</summary><div class='body'>{steer_row(pid, it)}"
                        + (f"<p>{e(it['text'])}</p>" if lead_clause(it['text'], 90) != it['text'] else "")
                        + f"<p>{e(it['desc'] or ('' if lead_clause(it['text'], 90) != it['text'] else 'No description yet.'))}</p>"
                        + (f"<p class='muted'>Unlocks: {e(', '.join(unlocks))}</p>" if unlocks else "")
                        + thread(ns, its)
                        + note_box(pid, "item", it["id"], "A note the agent reads when it works on this item", back=f"/?p={pid}&view=roadmap")
                        + f"<p><a href='/item?p={pid}&id={e(it['id'])}'>Open {e(it['id'])}: its work, gates and full thread →</a></p></div></details>")
                out.append(part_body(m, drawn) + "</details>")
            if done_ms:
                out.append("</details>")
            out.append(f"<script>{TICK}</script>")
        else:
            out.append("<div class='card muted'>No roadmap yet: the agent keeps it in ROADMAP.md.</div>")
        gone = sorted({(n["anchor"] or {}).get("item") for n in all_notes} - set(items(road)) - {None})
        if gone:                        # an item renamed or dropped keeps its conversation
            out.append(f"<details class='card'><summary>Notes on items no longer on the roadmap ({', '.join(map(e, gone))})</summary>"
                       + "".join(f"<h3>{e(g)}</h3>" + thread(by("item", g), items(road)) for g in gone) + "</details>")
        # The record: what was built, what the person said to the project as a whole (with Message at the top), and
        # its mail. Each folds behind its count and latest date; opened, its newest few, the rest a click further.
        hist_n = int(git(root, "rev-list", "--count", "HEAD").strip() or 0)
        hist = []
        for line in git(root, "log", "-n", str(RECORD_ALL if show_all == "history" else FOLD_SHOWN),
                        "--format=%h\x1f%aI\x1f%s").splitlines():
            h, t, subj = line.split("\x1f")
            hist.append((stamp(t), f"<details class='item'><summary><code>{e(h)}</code> {e(t[:10])} {e(subj)}"
                         + (f" <span class='badge new'>{len(by('commit', h))} notes</span>" if by("commit", h) else "")
                         + f"</summary>{thread(by('commit', h), items(road))}" + note_box(pid, "commit", h, "A note on this work; it reaches the agent on its next turn", back=f"/?p={pid}&view=roadmap") + "</details>"))
        out.append(record_fold(pid, "history", "History", hist, hist_n, show_all, rest="the rest are in its git log"))
        whole = [(n["at"], thread([n], items(road))) for n in all_notes if not n["anchor"]]
        whole += [(m["at"], f"<div class='note'><span class='who'>you · {e(m['at'][:10])} · "
                            f"{'into its console' if m['to'] == 'console' else 'to ' + e(m['to']) + ', written by its agent'}</span>"
                            f"<div>{e(m['text'])}</div></div>") for m in read(root, "messages.jsonl")]
        whole = [(at, h) for at, _, h in sorted(((at, i, h) for i, (at, h) in enumerate(whole)), reverse=True)]
        out.append(record_fold(pid, "messages", "Messages", whole, len(whole), show_all))
        from . import mail
        ms = mail.messages(root)
        me, rows = mail.address(root), []
        for m in reversed(ms):
            way = f"from <b>{e(mail.shown(m['from']))}</b>" if m["to"] == me else f"to <b>{e(m['to'])}</b>"
            state = ("answered" if m["answer"] else "waiting for an answer") if m["ask"] else ""
            if m["to"] == me and not m["delivered_at"]:
                state = "reaches the agent on its next turn"
            rows.append((m["at"], f"<div class='note'><span class='who'>{e(m['at'][:16].replace('T', ' '))} · {way}"
                                  f"{' · ' + state if state else ''}</span><div>{e(m['text'])}</div></div>"))
        out.append(record_fold(pid, "mail", "Mail with other projects", rows, len(rows), show_all))
    else:                                 # what needs the person now, and what changed (its status leads the page)
        # waiting on you, first: the same as this project's part of Needs you, kept current like it (what the agent
        # settles meanwhile drops out, except while being typed in). With nothing waiting, the section is out of the
        # way, and comes back by itself when something does.
        out.append(measures_html(root))           # the numbers it steers by, as colony took them, before all else
        rows = waiting_html(pid, root)
        out.append(f"<section id='waitingbox'{'' if rows else ' hidden'}><h2>Waiting on you (<span id='wcount'>{waiting}</span>)</h2>"
                   f"<div class='card' id='waiting'>{rows}</div></section>"
                   f"<script>{KEEP_CURRENT}keepCurrent(document.getElementById('waiting'), '/needs?p={pid}', (w) => {{"
                   " const n = w.querySelectorAll('.need:not(.held)').length; document.getElementById('wcount').textContent = n;"
                   " document.getElementById('waitingbox').hidden = !w.children.length;"
                   f" const q = document.querySelector('#status-{pid} .waits'); if (q) q.hidden = !!n; }});</script>")
        clock = epoch(now())
        if not hours.active(clock):
            out.append(f"<p class='muted'>Outside your hours until {e(hours.at_clock(hours.next_start(clock)))}: "
                       "what's asked now waits for them.</p>")
        if Path(root).resolve() == Path(__file__).resolve().parent.parent:
            out.append(tokens_html())             # colony's own page: what the whole colony spends, and colony itself
        out.append(pinned_section(pid, root))
        # what changed since the person's last look on an earlier day: the newest few, dated, and how much in all
        out.append(since_html(pid, root, page_base(root)))
        out.append(queue_html(pid, root))
    return shell(reg, pid, "".join(out))


def tokens_html():
    """Tokens spent over the last 4 hours, day and week: by program, by project, and colony's own overhead apart."""
    import threading
    from . import providers as pv, tokens
    threading.Thread(target=tokens.update, daemon=True).start()      # current by the next look
    rows, totals, reads = tokens.windows()
    looked = tokens.load().get("looked")
    if not looked:
        return ("<h2>Tokens</h2><div class='card'><p class='muted'>Counting: the first look reads the week's transcripts, "
                "in the background. Back in a minute.</p></div>")
    ws = [w for w, _ in tokens.WINDOWS]
    line = lambda label, v, cls="": (f"<tr{f' class={cls}' if cls else ''}><td>{label}</td>"
                                     + "".join(f"<td>{tokens.short(v.get(w, 0)) if v.get(w) else '–'}</td>" for w in ws) + "</tr>")
    of = lambda kind: sorted(((n, v) for (k, n), v in rows.items() if k == kind), key=lambda x: (-x[1]["7 d"], x[0]))
    mine = of("colony")
    over = {w: sum(v[w] for _, v in mine) for w in ws}
    share = {w: f"{over[w] / totals[w]:.1%}" if totals[w] else "–" for w in ws}
    body = ("<table class='tokens'><tr><th></th>" + "".join(f"<th>{w}</th>" for w in ws) + "</tr>"
            + line("<b>All</b>", totals, "all")
            + "".join(line(e(pv.PROVIDERS[k].label if k in pv.PROVIDERS else k), v, "sub") for k, v in of("program"))
            + "<tr class='head'><td colspan='4'>By project</td></tr>"
            + "".join(line(e(n), v, "sub") for n, v in of("project"))
            + "".join(line(e(n), v, "sub") for n, v in of("outside"))
            + "<tr class='head'><td colspan='4'>Colony itself</td></tr>"
            + "".join(line(e(n), v, "sub") for n, v in mine)
            + f"<tr class='all'><td>Overhead, of all</td>" + "".join(f"<td>{share[w]}</td>" for w in ws) + "</tr></table>")
    fresh = {w: totals[w] - reads[w] for w in ws}
    return ("<h2>Tokens</h2><div class='card'>" + body
            + f"<p class='muted'>Mostly cache reads, the conversation read again at each turn, which cost a tenth and count "
              f"lightly against limits: {reads['24 h'] / totals['24 h'] if totals['24 h'] else 0:.0%} of the last day. "
              "Without them: " + " · ".join(f"{w} {tokens.short(fresh[w])}" for w in ws) + ". Colony itself is the steward "
              "and the asks it makes (consultants, measure judges, connection audits, model checks, scouts); its notes "
              "inside a project's own session count as the project's. As of "
              f"{e(time.strftime('%-I:%M %p', time.localtime(looked)))}, from the programs' own transcripts.</p></div>")


FOLD_SHOWN = 10          # entries of a folded record shown when it is opened, before "Show all"
RECORD_ALL = 200         # entries "Show all" lists: a phone page stays a page (History's rest is in its git log)
SINCE_SHOWN = 8          # things listed under "Since you were last here"; the rest are counted


def record_fold(pid, key, title, rows, total, show_all, rest=""):
    """A record on the Roadmap tab (History, Messages, Mail), folded behind its count and latest date: opened, its
    newest few, the rest a click further (the newest RECORD_ALL). rows: [(board time, html)], newest first. Nothing at
    all, no section."""
    if not total:
        return ""
    everything = show_all == key
    shown = rows[:RECORD_ALL] if everything else rows[:FOLD_SHOWN]
    latest = on_day(rows[0][0]) if rows else ""
    more = (f"<p><a class='showall' href='/?p={pid}&view=roadmap&all={key}#{key}'>Show "
            f"{f'all {total}' if total <= RECORD_ALL else f'the newest {RECORD_ALL} of {total}'} →</a></p>"
            if not everything and total > len(shown) else
            f"<p class='muted'>The newest {len(shown)}{'; ' + e(rest) if rest else ''}.</p>" if everything and total > len(shown) else "")
    return (f"<details class='card fold' id='{key}'{' open' if everything else ''}><summary><h3>{e(title)}</h3>"
            f"<span class='muted'>{total}{' · ' + e(latest) if latest else ''}</span></summary>"
            + "".join(h for _, h in shown) + more + "</details>")


def since_html(pid, root, base):
    """What changed since the person's last look on an earlier day (as the day's page counts it; no clearing needed):
    the newest few things, one line each with its date, and when there is more, how much in all, each count pointing
    to where it lives. Items moved, gates opened, the person's and the steward's notes answered, and the agent's pins
    come first; commits fill what room is left. Nothing changed, no section."""
    s = since(root, base)
    times = item_times(root) if s["moved"] else {}
    road = f"/?p={pid}&view=roadmap"
    told = [n for n in s["replies"] if n.get("author") in ("person", "monitor") and not n.get("quiet")]
    events = [(times.get(iid, {}).get("since") or s["moved_at"], False,
               f"<b>{e(was)} → {e(state)}</b> <a href='/item?p={pid}&id={e(iid)}'>{e(iid)}</a> {e(short(plain(text), 90))}")
              for iid, was, state, text in s["moved"]]
    events += [(g["at"], False, f"gate opened: {e(short(g['question'], 100))}") for g in s["opened"]]
    events += [(n["addressed_at"], False, f"{'your' if n.get('author') == 'person' else 'the steward' + chr(8217) + 's'} note "
                f"“{e(short(n['text'], 40))}” answered: {e(short(n['reply'] or '', 70))}") for n in told]
    events += [(p["at"], False, f"the agent pinned <a href='/pin/open?p={pid}&id={e(p['id'])}'>{e(short(p['title'], 70))}</a>")
               for p in s["pinned"]]
    events += [(g["answered_at"], False, f"decided: {e(short(g['question'], 70))} <b>{e(short(g['answer'], 70))}</b>")
               for g in gates(root) if g["answered_at"] and g["answered_at"] > (base.get("at") or "")]
    events.sort(key=lambda ev: ev[0] or "", reverse=True)
    commits = [(stamp(t), True, f"<code>{e(h)}</code> {e(short(subj, 100))}") for h, t, subj in s["commits"] if not MERGE.match(subj)]
    shown = events[:SINCE_SHOWN]
    shown = sorted(shown + commits[:SINCE_SHOWN - len(shown)], key=lambda ev: ev[0] or "", reverse=True)
    if not shown:
        return ""
    lines = "".join(f"<li{' class=muted' if muted else ''}><span class='when'>{e(on_day(at))}</span> {body}</li>"
                    for at, muted, body in shown)
    more = ""
    if len(events) + s["count"] > len(shown):
        counts = [(len(s["moved"]), "item moved", "items moved", f"{road}#roadmap"), (len(s["opened"]), "gate opened", "gates opened", ""),
                  (len(told), "note answered", "notes answered", f"{road}&all=messages#messages"),
                  (len(s["pinned"]), "pin", "pins", "#pinned"), (s["count"], "commit", "commits", f"{road}#history")]
        said = [(f"<a href='{href}'>{n} {one if n == 1 else many}</a>" if href else f"{n} {one if n == 1 else many}")
                for n, one, many, href in counts if n]
        more = f"<p class='more muted'>In all: {' · '.join(said)}</p>"
    return (f"<h2>Since you were last here · {e(on_day(base['at']))}</h2>"
            f"<div class='card since'><ul class='events'>{lines}</ul>{more}</div>")


MERGE = re.compile(r"Merge (branch|remote-tracking branch|pull request|commit)\b")


def short(text, n=140):
    """A roadmap line or subject cut to read at a glance."""
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def queue_html(pid, root):
    """The queue: open items outside Later, by milestone, folded behind its count (Later waits for the person)."""
    road = roadmap(root)
    its = items(road)
    link = lambda iid: f"<a href='/item?p={pid}&id={e(iid)}'>{e(iid)}</a>" if iid in its else e(iid)
    parts, count = [], 0
    for m in road["milestones"]:
        left = [i for i in m["items"] if i["state"] != "done"]
        if left and not re.match(r"later\b", (m["title"] or m["id"]).strip(), re.I):
            count += len(left)
            parts.append(f"<h3>{e(m['id'])}{' — ' + e(m['title']) if m['title'] else ''}</h3><ul class='queue'>" + "".join(
                f"<li><span class='st {i['state']}'>{LABEL[i['state']]}</span> {link(i['id'])} {e(short(plain(i['text'])))}</li>"
                for i in left) + "</ul>")
    return (f"<details class='card fold' id='queue'><summary><h3>Queue</h3><span class='muted'>{count} open</span></summary>"
            + "".join(parts) + "</details>") if parts else ""


W, H, GX, GY = 172, 56, 38, 16


def library_html(pid, root, q=""):
    """The Library tab: what the person and the agent keep to refer to, under their headings, each opening as it is
    (a text one to edit together, a page to run). It starts empty. Finding a file in the project is only a way to
    add one: a search, its results each with Add."""
    from . import library
    hidden = f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='back' value='/?p={pid}&view=library'>"
    groups = {}
    for x in sorted(pins.pins(root), key=lambda x: x["at"]):
        groups.setdefault(x.get("group") or "", []).append(x)
    heads = [g for g in groups if g] + [""] * ("" in groups)
    known = "".join(f"<option value='{e(g)}'>" for g in heads if g)
    out = [f"<div class='pinhead'><h2>Library</h2><a href='/pins/add?p={pid}'>+ Add</a></div>"]
    for g in heads:
        rows = []
        for x in reversed(groups[g]):
            row = pin_row(pid, x)
            move = (f"<form class='add regroup' method='post' action='/pin/group'>{hidden}<input type='hidden' name='id' value='{e(x['id'])}'>"
                    f"<input name='group' list='libheads-{pid}' value='{e(x.get('group') or '')}' placeholder='Heading'>"
                    f"<button class='quiet'>Move</button></form>")
            rows.append(row.replace("<div class='pinmenu'>", "<div class='pinmenu'>" + move, 1))
        rename = (f"<details class='renamehead'><summary>Rename</summary><form class='add regroup' method='post' action='/pin/rename'>"
                  f"{hidden}<input type='hidden' name='old' value='{e(g)}'><input name='new' value='{e(g)}' placeholder='Heading'>"
                  f"<button class='quiet'>Rename</button></form></details>")
        out.append(f"<details class='libgroup d1' open><summary>{e(g or 'Not under a heading')} <span class='muted'>{len(rows)}</span>"
                   f"</summary>{rename}{''.join(rows)}</details>")
    if not groups:
        out.append("<div class='card muted'>Empty, and ready for what you'll both keep returning to: a world's places and people, "
                   "a set of policies, research you build on, a page you work on together. Add one, or the agent will when "
                   "something earns a place. Only what keeps its value stays: unpin what no longer does.</div>")
    out.append(f"<datalist id='libheads-{pid}'>{known}</datalist>")
    gone = pins.archived(root)
    if gone:                            # folded, and never read to the agent: what's done costs nothing to keep
        out.append(f"<details class='libgroup d1 archive'><summary>Archive <span class='muted'>{len(gone)}</span></summary>" + "".join(
            f"<div class='pin'><div class='pinline'><a class='pintitle' href='/pin/open?p={pid}&id={e(x['id'])}' target='_blank' "
            f"rel='noopener'>{e(x['title'])}</a><form method='post' action='/pin/restore'>{hidden}<input type='hidden' name='id' "
            f"value='{e(x['id'])}'><button class='quiet'>Restore</button></form></div><div class='who'>"
            f"archived {e(x['archived']['at'][:10])}{' by the agent' if x['archived'].get('by') == 'agent' else ''}"
            + (f" · {e(x['archived']['why'])}" if x["archived"].get("why") else "") + "</div></div>" for x in gone) + "</details>")
    # adding a file from the project: found by a search, never listed by itself
    q = (q or "").strip().lower()
    hits = []
    if q:
        kept = {x["target"] for x in pins.pins(root)}
        for rel in library.files(root):
            title, line, kind, changed = library.about(root, rel)
            if q in f"{title} {line} {rel}".lower() and rel not in kept:
                hits.append(f"<div class='libitem'><a href='{chat_file(pid, root, rel)}' target='_blank' rel='noopener'>{e(title)}</a>"
                            f"<div class='muted small'>{e(line) + ' · ' if line else ''}<code>{e(rel)}</code></div>"
                            f"<form class='add' method='post' action='/pin'>{hidden}<input type='hidden' name='target' value='{e(rel)}'>"
                            f"<input type='hidden' name='title' value='{e(title)}'><input name='group' list='libheads-{pid}' "
                            f"placeholder='Heading (optional)'><button class='quiet'>Add to the Library</button></form></div>")
                if len(hits) >= 40:
                    break
    out.append(f"<details class='libgroup d1'{' open' if q else ''}><summary>Add a file from the project</summary>"
               f"<form class='libsearch' method='get' action='/'><input type='hidden' name='p' value='{pid}'>"
               f"<input type='hidden' name='view' value='library'><input type='search' name='all' value='{e(q)}' "
               f"placeholder='Find a file by its name or title'></form>"
               + ("".join(hits) or ("<p class='muted'>No file matches.</p>" if q else "")) + "</details>")
    return "".join(out)


def page_sig(root, path):
    """The signature on one page's way back: only the board, which made it, can give a page its address."""
    import hmac
    secret = home() / "page-secret"
    if not secret.exists():
        secret.write_text(secrets.token_hex(32))
        secret.chmod(0o600)
    return hmac.new(secret.read_bytes(), f"{Path(root).resolve()}|{Path(path).resolve()}".encode(), "sha256").hexdigest()[:32]


def page_with_send(key, root, path, data):
    """A page as the board serves it: with colony.send(what) ready, which brings what the person did on the page back to
    the project's agent as a note from the page (a string, or anything JSON can hold)."""
    url = f"/page/send?p={urllib.parse.quote(str(key))}&f={urllib.parse.quote(str(path))}&sig={page_sig(root, path)}"
    script = ("<script>window.colony={send:function(d){return fetch(" + json.dumps(url) + ",{method:'POST',"
              "headers:{'Content-Type':'text/plain'},body:typeof d==='string'?d:JSON.stringify(d)}).then(function(r){"
              "return r.ok}).catch(function(){return false})}};</script>").encode()
    m = re.search(rb"<head[^>]*>", data, re.I)
    return data[:m.end()] + script + data[m.end():] if m else script + data


def measures_html(root):
    """The plan's measures, as colony took them: each its latest value against its target, its trend, when and at
    which commit, or that it couldn't be taken. Nothing when the plan has none."""
    from . import measures
    rows = []
    for m in measures.summary(root):
        last = m["last"]
        target = f" / {measures.fmt(m['target'])}" if m["target"] is not None else ""
        how = {"count": "counted", "run": "run", "judge": "judged"}[m["how"]]
        if not last:
            value = "<span class='muted'>not taken yet: at the next push</span>"
        elif last["value"] is None:
            value = f"<span class='flag'>couldn't measure</span> <span class='muted'>{e(short(last['error'] or '', 90))}</span>"
        else:
            reached = m["target"] is not None and m["values"] and len(m["values"]) and (
                m["values"][-1] >= m["target"] if (m["values"][0] <= m["target"]) else m["values"][-1] <= m["target"])
            value = (f"<b>{measures.fmt(last['value'])}</b>{target}{' <span class=reached>✓ reached</span>' if reached else ''} "
                     f"<span class='spark'>{measures.spark(m['values'])}</span>"
                     + (" <span class='muted'>(method changed)</span>" if m["changed"] else ""))
        when = (f" · {how} at {e(last['commit'][:7])}, {e(last['at'][:10])}" if last else "")
        why = f"<div class='muted small'>{e(last['reason'])}</div>" if last and last.get("reason") else ""
        rows.append(f"<div class='measure'><span class='mname'>{e(m['name'])}</span> {value}"
                    f"<div class='muted small' title='{e(m['what'])}'>{e(m['part'] if m['part'] not in (None, 'Vision', 'Measures') else 'the whole plan')}{when}</div>{why}</div>")
    return f"<div class='card measures'>{''.join(rows)}</div>" if rows else ""


KIND_MARK = {"menu": "☰ menu", "lanes": "‖ lanes", "findings": "? findings", "sections": "▦ sections"}


def lead_clause(text, n=70):
    """An item's opening clause, its title: items run long, with the person's words and decisions after it."""
    head = re.split(r"(?<=[a-z0-9)])[.:;](?:\s|$)| — | \(", text, maxsplit=1)[0].strip()
    return short(head, n)


def journey_html(road, times=None):
    """The roadmap as a journey down the page: what's finished in one line, each open milestone a stop (a ring of its
    progress, its kind, a dot per item: filled when done, glowing while in progress, hollow to come; lanes as rows
    of their own), what is being worked on now, and at the end the vision it all heads for. A stop opens its list."""
    ms = road["milestones"]
    done = lambda m: m["items"] and all(i["state"] == "done" for i in m["items"])
    finished, open_ = [m for m in ms if done(m)], [m for m in ms if not done(m) and m["items"]]
    dot = lambda i: f"<i class='jd {i['state']}' title='{e(i['id'])} · {e(lead_clause(i['text'], 80))}'></i>"
    out = []
    if finished:
        # what's been done, as medals: each milestone finished, its items, and when it was won; a tap opens it
        times = times or {}
        when = lambda m: max((times.get(i["id"], {}).get("since") or "" for i in m["items"]), default="")
        medals = sorted(finished, key=when)
        n = sum(len(m["items"]) for m in finished)
        out.append(f"<div class='jmedals-head'><span>Done</span><span class='muted'>{len(finished)} milestone"
                   f"{'s' * (len(finished) != 1)} · {n} items</span></div><div class='jmedals'>" + "".join(
                       f"<a class='jmedal' href='#ms-{e(m['id'])}' onclick=\"var d=document.getElementById('ms-{e(m['id'])}');"
                       f" if (d) {{ d.open=true; var g=d.closest('details.done-group'); if (g) g.open=true }}\">"
                       f"<span class='jseal'>✓</span><b>{e(m['id'])}</b><span class='jmname'>{e(short(m['title'], 48))}</span>"
                       f"<span class='muted'>{len(m['items'])} items{' · ' + e(on_day(when(m)) or when(m)[:10]) if when(m) else ''}</span></a>"
                       for m in medals) + "</div>")
    out.append("<div class='journey'>")
    for m in open_:
        its = m["items"]
        d = sum(i["state"] == "done" for i in its)
        doing = [i for i in its if i["state"] == "doing"]
        kind = m.get("kind", "sequence")
        if kind == "lanes":
            lanes = {}
            for i in its:
                lanes.setdefault(i.get("lane") or "other", []).append(i)
            dots = "".join(f"<span class='jlane'><span class='jlname'>{e(k)}</span>{''.join(dot(i) for i in v)}</span>" for k, v in lanes.items())
        else:
            dots = f"<span class='jdots'>{''.join(dot(i) for i in its)}</span>"
        later = "later" in (m["id"] + " " + m["title"]).lower()
        now = (f"<span class='jnow'>Now: {e(lead_clause(doing[0]['text']))}" + (f" <span class='muted'>+{len(doing) - 1}</span>" if len(doing) > 1 else "")
               + "</span>") if doing else ""
        out.append(f"<a class='jstop{' live' if doing else ''}{' later' if later else ''}' href='#ms-{e(m['id'])}' "
                   f"onclick=\"var d=document.getElementById('ms-{e(m['id'])}'); if (d) d.open=true\">"
                   f"<span class='jring' style='--p:{round(100 * d / len(its))}'><b>{d}</b><small>/{len(its)}</small></span>"
                   f"<span class='jbody'><span class='jtitle'><b>{e(m['id'])}</b> {e(short(m['title'], 70))}"
                   f"<span class='jkind' title='{e(kind)}'>{KIND_MARK.get(kind, '')}</span></span>{dots}{now}</span></a>")
    vision = first_sentence_of(road.get("vision") or road.get("goal") or "")
    if vision:
        out.append(f"<div class='jdest'><span class='jflag'>⚑</span><span>{e(vision)}</span></div>")
    return "".join(out) + "</div>"


def part_body(m, drawn):
    """A milestone's items drawn as its kind: a sequence or menu as a list (in progress, then to come in the agent's
    order, then done); lanes side by side, each with its progress and what it is on; findings as what runs, what is
    still open, and what was established (with what was found); sections as the pieces of the whole, each its state.
    drawn: each item's own row, in that list's order."""
    its = {i["id"]: i for i in m["items"]}
    rows = lambda ids: "".join(drawn[i] for i in ids)
    order = list(drawn)
    kind = m.get("kind", "sequence")
    if kind == "menu":
        rest = [i for i in order if its[i]["state"] not in ("doing", "done")]
        return (rows(i for i in order if its[i]["state"] == "doing")
                + (f"<p class='kindnote muted'>To come, in any order · {len(rest)}</p>" + rows(rest) if rest else "")
                + rows(i for i in order if its[i]["state"] == "done"))
    if kind == "lanes":
        lanes = {}
        for i in order:
            lanes.setdefault(its[i]["lane"] or "Other", []).append(i)
        out = "<div class='lanes'>"
        for name, ids in lanes.items():
            done = sum(its[i]["state"] == "done" for i in ids)
            on = next((its[i]["text"] for i in ids if its[i]["state"] == "doing"), None)
            out += (f"<div class='lane'><b>{e(name)}</b> <span class='muted'>{done}/{len(ids)}</span>"
                    f"<div class='lanebar'><i style='width:{round(100 * done / len(ids))}%'></i></div>"
                    + (f"<p class='muted'>On: {e(short(on, 70))}</p>" if on else "") + rows(ids) + "</div>")
        return out + "</div>"
    if kind == "findings":
        groups = (("Running", [i for i in order if its[i]["state"] == "doing"]),
                  ("Still open", [i for i in order if its[i]["state"] in ("todo", "verify")]),
                  ("Established", [i for i in order if its[i]["state"] == "done"]))
        return "".join(f"<p class='kindnote muted'>{name} · {len(ids)}</p>" + rows(ids) for name, ids in groups if ids)
    if kind == "sections":
        cells = "".join(f"<span class='piece {its[i]['state']}' title='{e(its[i]['text'])}'>{e(short(its[i]['text'], 28))}</span>"
                        for i in its)
        return f"<div class='pieces'>{cells}</div>" + rows(order)
    # a sequence: what is being done (latest started first), what is to come (in order), what is done (latest first,
    # folded once it runs long), each a line by its title
    doing = [i for i in order if its[i]["state"] in ("doing", "verify")]
    todo = [i for i in order if its[i]["state"] == "todo"]
    done = [i for i in order if its[i]["state"] == "done"]
    head = lambda name, ids: f"<p class='kindnote muted'>{name} · {len(ids)}</p>"
    return ((head("Doing", doing) + rows(doing) if doing else "") + (head("To come", todo) + rows(todo) if todo else "")
            + ((f"<details class='donefold'><summary class='kindnote muted'>Done · {len(done)}</summary>{rows(done)}</details>"
                if len(done) > 6 else head("Done", done) + rows(done)) if done else ""))


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
            until = (f"<p class='muted'>Held until {e(hours.at_clock(epoch(g['held'])))}: opened outside your active hours, it "
                     "reaches your list when they begin. You can answer it now.</p>" if g.get("held") and not g["answer"] else "")
            body.append(f"<div class='card{' gate' if due(g) else ''}'>" + until
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


def gate_choices(pid, gate, back):
    """The answers a decision offers, each a tap: the agent's recommended one first and marked (or, with no options,
    the default that would stand), then the rest; anything else goes in the box below."""
    if gate.get("points") or gate["answer"]:
        return ""
    rec = gate.get("recommended") or gate.get("default") or ""
    opts = [rec] * bool(rec) + [o for o in gate.get("options") or [] if o != rec]
    one = lambda o, label=None: (f"<form method='post' action='/answer'><input type='hidden' name='p' value='{e(pid)}'>"
                                 f"<input type='hidden' name='gate' value='{e(gate['id'])}'><input type='hidden' name='back' value='{e(back)}'>"
                                 f"<input type='hidden' name='text' value='{e(o)}'><button class='choice{' rec' if o == rec else ' yours' if label else ''}'>"
                                 + ("<span class='tag'>Recommended</span>" if o == rec else "") + f"{e(label or o)}</button></form>")
    if gate.get("hold"):                             # a connection's call: theirs to release, never handed back
        return "<div class='gatechoices'>" + "".join(one(o) for o in opts) + "</div>"
    # handed back in a tap: this once (where no answer is recommended, which would be the same tap), or this and every
    # question like it, the agent told where the line is
    return ("<div class='gatechoices'>" + "".join(one(o) for o in opts) + ("" if rec else one(CHOOSE, "Choose for me"))
            + one(OBVIOUS, "Choose for me, now and for questions like this") + "</div>")


CHOOSE = "Choose for me: take what best serves the vision, mission and roadmap, and say what you chose."
OBVIOUS = ("Choose for me, now and for questions like this: take what best serves the vision, mission and roadmap, say "
           "what you chose, and settle choices like this yourself from here on.")


def gate_rounds(root, gate):
    """A decision asked again after the person's questions: (the first question, [(what they asked, what they were
    told)]), earliest first; the first question is None for one asked once."""
    every = {g["id"]: g for g in gates(root)} if gate.get("reframes") else {}
    asked = {}
    if every:
        for n in notes(root):
            a = n.get("anchor") or {}
            if a.get("asked") and n.get("author") == "person":
                asked[a["gate"]] = a["asked"]              # the last they asked on each
    rounds, g, first = [], gate, None
    while g.get("reframes") in every:
        before = every[g["reframes"]]
        rounds.insert(0, (asked.get(before["id"], ""), g.get("told", "")))
        first, g = before["question"], before
    return first, rounds


def gate_body(root, pid, gate, back, number=None, fold=False, tail=""):
    """The same point decisions in Needs you, Waiting on you and the item's record. With its number on the day's page;
    folded, its own answer box tucks away beside the page's box for answering by number. A tail (clearing it unanswered)
    goes with its own answer, folded with it."""
    first, rounds = gate_rounds(root, gate)
    # the way here, folded, and the latest exchange open: what is open is what matters now
    shots = lambda a: "".join(f"<a class='shot' href='{u}' target='_blank' rel='noopener'><img src='{u}' alt='' loading='lazy'></a>"
                              for x in re.findall(r"^Attached: (.+)$", a or "", flags=re.M)
                              if x.strip().lower().endswith(IMAGES) and (u := chat_file(pid, root, e(x.strip()))))
    said = lambda a: re.sub(r"^Attached: .+\n*", "", a or "", flags=re.M).strip()
    exchange = lambda a, t: ((f"<p><span class='muted'>You asked:</span> {e(said(a))}</p>{shots(a)}" if a else "")
                             + (f"<p><span class='muted'>Answer:</span> {e(t)}</p>" if t else ""))
    body = ((f"<details class='round'><summary>First asked: {e(short(first, 90))}</summary><p>{e(first)}</p></details>"
             + "".join(f"<details class='round'><summary>You asked: {e(short(a or '…', 90))}</summary>{exchange(a, t)}</details>"
                       for a, t in rounds[:-1])
             + f"<div class='round now'>{exchange(*rounds[-1])}</div>") if first else "")
    body += (f"<span class='num'>{e(number)}</span> " if number else "") + f"<b>{e(gate['question'])}</b>"
    if gate.get("why"):
        body += f"<p class='muted'>{e(gate['why'])}</p>"
    if gate.get("default") and not gate["answer"]:
        when = "" if gate.get("waits_for") else stands_at(root, gate)
        when = when and (f" · stands {hours.at_day(when)}" if when > epoch(now()) else " · waits while you answer it")
        body += f"<p class='default'>If you skip it: {e(gate['default'])}<span class='muted'>{e(when)}</span></p>"
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
        hint = ("Optional comment" if points else "Something else, or a question…" if gate.get("options") or gate.get("default")
                else "Your answer, or a question…")
        css = "add consultant-gate" if points else "add"
        button = "Save decisions" if points else "Send"
        return (f"<form class='{css}' method='post' action='/answer'{'' if points else " enctype='multipart/form-data'"}>"
                f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='gate' value='{e(gate['id'])}'>"
                f"<input type='hidden' name='back' value='{e(back)}'>" + content
                + ("<p class='muted'>Accepting a change allows one checking round. Choose for every point.</p>" if points else "")
                + ("" if points else "<input type='hidden' name='reply' value='1'>")
                + f"<textarea name='text' placeholder='{hint}'>{e(comment)}</textarea>"
                + ("" if points else "<label class='attach' aria-label='Attach photos or files'>+<input type='file' name='file' multiple hidden></label>")
                + f"<button>{button}</button></form>")
    if gate["answer"]:
        answer_text = ("Cleared without an answer." if gate.get("cleared") else e(gate["answer"])
                       if gate.get("answered_by") in ("colony", "agent") else f"Your answer: {e(gate['answer'])}")
        body += "".join(rows) + f"<div class='pre'>{answer_text}</div>"
        if points:
            body += ("<p class='muted'>The checking round is already recorded; this decision has used both rounds.</p>"
                     if rec and rec.get("checking_round") else
                     "<p class='muted'>A checking round is allowed for the accepted changes.</p>"
                     if "accept" in choices.values() else
                     "<p class='muted'>No change accepted; no checking round.</p>")
            body += ("<details><summary>Revise your choices</summary>"
                     + form("".join(editable), gate.get("comment", "")) + "</details>")
    elif fold and not points:
        body += (f"<details class='one' data-k='one-{e(gate['id'])}'><summary>Answer just this one</summary>"
                 f"{gate_choices(pid, gate, back)}{form('')}{tail}</details>")
    else:
        body += gate_choices(pid, gate, back) + form("".join(rows)) + tail
    if rec:
        body += ("<details><summary>Full consultant answers</summary>"
                 + consultation(dict(rec, adopted=None, point_decisions=[])) + "</details>")
    return body


def answer_box(pid, root, numbered, back, hint=False, day=False):
    """One box for a project's open decisions, answered by number. What's typed is kept as it's typed, so a reload or
    a redrawn list loses nothing, and the defaults it speaks to wait while it's written. Typed against numbers the
    page no longer shows, it is set beside the box to retype, never sent to the wrong decision."""
    numbers = {n: g["id"] for n, g in numbered if due(g)}
    if not any(not g.get("points") for n, g in numbered if due(g)):
        return ""
    text, aside, d = "", "", draft(root)
    if d:
        theirs = d.get("numbers") or {}
        if all(numbers.get(n) == theirs.get(n) for n in by_number(d["text"], theirs, lenient=True)):
            text = d["text"]
        else:
            aside = f"<p class='muted'>Unsent, from when the numbers were different:</p><div class='pre muted'>{e(d['text'])}</div>"
    first = list(numbers)
    hint_ = f"Your answer to {first[0]}" if len(first) == 1 else f"By number: {first[0]} yes; {first[1]} …"
    return (f"{aside}<form class='add bynumber' method='post' action='/answers'><input type='hidden' name='p' value='{pid}'>"
            f"<input type='hidden' name='back' value='{e(back)}'>"
            f"<input type='hidden' name='n' value='{e(','.join(f'{n}:{gid}' for n, gid in numbers.items()))}'>"
            f"<textarea name='text' placeholder='{e(hint_)}'>{e(text)}</textarea>"
            + (f"<a class='dayline' href='/?p={pid}'>{e(Path(root).name)} →</a>" if day else "")
            + "<button>Send answers</button></form>"
            + ("<p class='muted'>One answer a line, or after a semicolon; “2 default” takes a default now. Skip one, and "
               "its default stands as your day begins after a whole day here.</p>" if hint else ""))


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
    remote = "on" if own.get("remote", g["remote_by"].get(own.get("provider") or g["provider"], g["remote"])) else "off"
    names = {"ask": "ask each time", "edits": "accept edits", "all": "allow everything", "plan": "plan only"}
    return (f"<form method='post' action='{action}' class='options'><input type='hidden' name='p' value='{pid}'>"
            + provider_fields(own.get("provider", ""), own.get("model", ""), own.get("effort", ""), "global") +
            f"<label>Permissions {opt('permissions', [(k, v) for k, v in names.items()], own.get('permissions') or g['permissions'])}</label>"
            f"<label>Remote Control {opt('remote', [('on', 'on'), ('off', 'off')], remote)}</label>"
            + "<div class='swatches'><span>Colour</span>" + "".join(
                f"<label title='{k}' style='--sw:{light};--swd:{dark}'><input type='radio' name='color' value='{'' if k == 'green' else k}'"
                f"{' checked' if (own.get('color') or 'green') == k else ''}><span></span></label>" for k, (light, dark) in PALETTE.items())
            + "</div>" + (COLOUR_NOW.replace("PID", str(pid)) if root else "") +
            f"<label>Safe pause at <input name='safe_pause' value='{e(str(own.get('safe_pause', '')))}' size='4' "
            f"placeholder='{e(str(g['safe_pause'] or 'off'))}'> % of a usage limit <span class='muted'>(blank: colony's; off: never)</span></label>"
            + (f"<label><input type='checkbox' name='carry_on' value='on'{'' if own.get('carry_on') == 'off' else ' checked'}>"
               "<input type='hidden' name='carry_on' value='off'> Carry on by itself: idle with roadmap work it can start, "
               "it is told so <span class='muted'>(once, until something changes)</span></label>" if root else "")
            + (auto_balance_fields(root) + tier_fields(root) + turbo_fields(root) if root else "") + f"<button>Save</button></form>")


# A colour tapped on a project's page is its colour at once: the page takes it, and the board keeps it, no Save.
COLOUR_NOW = """<script>document.currentScript.parentNode.querySelectorAll('.swatches input[name=color]').forEach((r) =>
  r.addEventListener('change', () => { const l = r.closest('label'), dark = matchMedia('(prefers-color-scheme: dark)').matches;
    const light = l.style.getPropertyValue('--sw'), deep = l.style.getPropertyValue('--swd');
    document.documentElement.style.setProperty('--accent', dark ? deep : light);
    const chip = document.querySelector('nav .proj.on'); if (chip) { chip.style.setProperty('--pc', light); chip.style.setProperty('--pcd', deep); }
    fetch('/project/color', {method: 'POST', body: new URLSearchParams({p: 'PID', color: r.value})}).catch(() => {}); }));</script>"""


def turbo_fields(root):
    """What turbo means for this project, ticked: an unticked box still says so (its hidden twin sends off)."""
    from . import turbo
    o = turbo.options(root)
    box = lambda k, label: (f"<label><input type='checkbox' name='{k}' value='on'{' checked' if o[k] else ''}>"
                            f"<input type='hidden' name='{k}' value='off'> {label}</label>")
    return ("<div class='turbo'><p><b>Turbo</b> <span class='muted'>when its program's week runs behind pace, and only "
            "if it has queued work or a research topic: alongside its work, never instead of it</span></p>"
            + box("turbo_models", "Stronger models <span class='muted'>(two positions toward Intelligence, from its next idle reload)</span>")
            + box("turbo_deeper", "Go deeper on queued work <span class='muted'>(preparation, tests, reviews, prior art, parallel helpers)</span>")
            + box("turbo_research", "Research")
            + f"<label>Research topic <input name='turbo_topic' value='{e(o['turbo_topic'])}' placeholder='what it would research'></label>"
            + f"<p class='muted'>{e(turbo.project_line(root))}</p></div>")


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
                rows.append(f"<tr><th>{e(family)} · {e('steward' if role == 'monitor' else role)}</th><td>{e(text)}<div class='muted'>Accepted: {e(describe(held))}</div></td></tr>")
        panels.append(f"<div data-balance='{level}'{' hidden' if level != selected else ''}><p><strong>{e(label)}</strong></p><table class='bench'>{''.join(rows)}</table></div>")
    toggle = ("<label><input type='checkbox'" + (' checked' if inherited else '') +
              " onchange=\"const r=this.closest('.auto-balance').querySelector('input[type=range]');r.disabled=this.checked;"
              f"if(this.checked){{r.value='{settings.get('auto_balance', 3)}';r.dispatchEvent(new Event('input'))}}\"> Use colony setting</label>") if root else ''
    return ("<div class='auto-balance'><label>Auto: Intelligence → Balanced → Economy "
            f"<input type='range' name='auto_balance' min='0' max='6' step='1' value='{selected}'{' disabled' if inherited else ''} "
            "oninput=\"for(const p of this.closest('.auto-balance').querySelectorAll('[data-balance]')){p.hidden=p.dataset.balance!==this.value;if(!p.hidden)this.closest('.auto-balance').querySelector('output').textContent=p.querySelector('strong').textContent}\">"
            f"<output>{e(intelligence.POSITIONS[selected])}</output></label>"
            + toggle + "<p class='muted'>Higher intelligence keeps goals near the shared ceiling. Economy lowers the goals so cheaper pairs qualify. "
            "Usage never moves this slider; turbo, while a program's week runs behind pace, picks two positions toward "
            "Intelligence for projects that tick stronger models. Rote keeps its value pick at every position. Explicit model pins stay in effect.</p>"
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
               .split("<input type='hidden' name='p' value='0'>", 1)[1].rsplit("<button>Save</button></form>", 1)[0]
               + connection_ticks())
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


def folder_name(text):
    """A folder's name from what the person typed: letters, digits and a few marks kept, the rest (an emoji) a dash,
    and none at either end, so "🌈 SnackStand" is SnackStand: a name opening on a dash reads as an option to its program."""
    return re.sub(r"[^A-Za-z0-9_. -]", "-", text or "").strip("-. ")


def clone(url, within, name=""):
    """Clone a repository into a new folder under `within`; its path, or a ValueError saying what went wrong."""
    url = url.strip()
    if not re.match(r"^(https://|git@|ssh://|file://)[^\s]+$", url):
        raise ValueError("That doesn't look like a repository link (https://github.com/owner/repo).")
    name = folder_name(name) or re.sub(r"\.git$", "", re.split(r"[/:]", url.rstrip("/"))[-1])
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
            pick = lambda st, n=5: [f"{i['id']} {short(i['text'], 70)}" for i in open_ if i["state"] == st][:n]
            return {"milestone": f"{m['id']} — {m['title']}" if m['title'] else m['id'], "doing": pick("doing"), "verify": pick("verify"), "next": pick("todo", 3)}
    return {"milestone": "", "doing": [], "verify": [], "next": []}


def monitor_page(reg, view="overview"):
    """The steward, like a project: Overview (what needs the person, every project's status, what the
    steward decided), Helm (its stance toward each project), Console."""
    from . import monitor
    if MONITOR:                          # a board started without its steward doesn't start one by being looked at
        monitor.ensure()
    tab = lambda v, label: f"<a class='{'on' if view == v else ''}' href='/steward?view={v}'>{label}</a>"
    on = monitor.helm()
    targets = "".join(f"<option value='{i}'>{e(p.name)}</option>" for i, p in enumerate(projects(reg)))
    # the steward relays for the person, so what it sends a project is taken as the person's word
    message = (f"<details class='psettings'><summary>Message a project</summary><div class='panel'>"
               f"<form class='options' method='post' action='/message'><input type='hidden' name='p' value='monitor'>"
               f"<label>To <select name='to'>{targets}</select></label>"
               f"<label class='stack'>What should the steward tell them for you?"
               f"<textarea name='text' placeholder='It relays it as yours, in full, and checks it landed'></textarea></label>"
               f"<button>Have the steward send it</button></form></div></details>") if targets else ""
    # its state in a word beside its name; what that means is on the Helm tab (and under a pointer, in the word's title)
    stance, means = (("At the helm", "It holds the helm of every project included in it, and settles routine questions "
                      "within each one's direction.") if on else
                     ("Muted", "Nothing wakes it and it spends nothing; it answers only when you talk to it.") if monitor.muted() else
                     ("Asleep", "Nothing wakes it but you and its scouting, and the board shows you what needs you."))
    head = (f"<header class='project'><div class='titlerow'><h1>steward</h1><span class='badge stance' title='{e(means)}'>{stance}</span>"
            f"{message}<form method='post' action='/helm' class='helmform'>"
            f"<input type='hidden' name='state' value='{'off' if on else 'on'}'><button class='{'quiet' if on else ''}'>"
            f"{'Take the helm back' if on else 'Give it the helm'}</button></form>"
            + ("" if on else f"<form method='post' action='/mute' class='helmform'><input type='hidden' name='state' "
               f"value='{'off' if monitor.muted() else 'on'}'><button class='quiet'>{'Unmute' if monitor.muted() else 'Mute'}</button></form>")
            + "</div>" + switch_line(monitor.home(), monitor.name(), "monitor")
            + f"<div class='tabs'>{tab('overview', 'Overview')}{tab('helm', 'Helm')}{tab('chat', 'Chat')}{tab('console', 'Console')}</div></header>")
    plist = projects(reg)
    if view == "chat":
        return shell(reg, -1, head + chat_page("steward", monitor.home()))
    if view == "console":
        body = head + console.PAGE.format(label=e(monitor.provider().label), path=e(monitor.home()), name=monitor.name(), pid=-1,
                                          token=console.token(), focus='true', scrolled=e(monitor.provider().scrolled_marker))
        return shell(reg, -1, body, wide=True)
    if view == "direction":
        mine = (home() / "direction.md").exists()
        body = head + (f"<div class='card'><h3>Standing direction, for every project</h3>"
                       f"<p class='muted'>How the steward steers all your projects: what it settles, what always comes back to you, "
                       f"how it holds a project to its vision. Each project's own direction, on the Helm tab, adds to this. "
                       f"{'This is your version.' if mine else 'This is colony' + chr(39) + 's default.'}</p>"
                       f"<form class='options' method='post' action='/direction'><textarea name='text' class='direction'>{e(monitor.direction())}</textarea>"
                       f"<div class='choices'><button>Save</button>"
                       + ("<button class='quiet' name='reset' value='1'>Back to colony's default</button>" if mine else "")
                       + "</div></form><p><a href='/steward?view=helm'>← Helm</a></p></div>")
        return shell(reg, -1, body)
    if view == "helm":
        cards = [f"<div class='card'><div class='titlerow'><h3>Every project</h3>"
                 f"<a href='/steward?view=direction'>Standing direction →</a></div>"
                 f"<p><b>{stance}.</b> {e(means)}</p>"
                 f"<p class='muted'>What the steward settles, what always comes back to you, and how it steers, for all projects"
                 f"{' (your version)' if (home() / 'direction.md').exists() else ''}. Each project below adds its own, and "
                 f"whether it is included in the steward's helm.</p></div>"]
        for pid, p in enumerate(plist):
            if not p.exists():
                continue
            pos, f, held = monitor.posture(p), focus(p), monitor.helm_for(p)
            opt = lambda v, label, cur: f"<option value='{v}'{' selected' if cur == v else ''}>{label}</option>"
            lines = "".join(f"<li><span class='kind'>{k}</span> {e(x)}</li>" for k in ("doing", "verify", "next") for x in f[k])
            made = "".join(f"<div class='note'><span class='who'>{e(d['at'][:16].replace('T', ' '))}</span><div>{e(d['text'])}</div></div>"
                           for d in monitor.decisions(p, 8))
            cards.append(f"<div class='card helmcard'><div class='titlerow'><h3>{e(p.name)}</h3>"
                         f"<span class='badge {'gate' if held else ''}'>{'steward holds the helm' if held else 'helm with you'}</span></div>"
                         f"<form class='options' method='post' action='/posture'><input type='hidden' name='p' value='{pid}'>"
                         f"<label><input type='checkbox' name='helm' value='on'{'' if pos['helm'] is False else ' checked'}> Include in the steward's helm "
                         f"<span class='muted'>({'held now' if held else 'the helm is with you' if not on else 'left out'})</span></label>"
                         f"<label class='stack'>Direction for the steward<textarea name='direction' placeholder='Empty: the standing direction alone. Add what is particular here: this version is… and it is done when… · always bring back… · you may settle… · the yardstick…'>{e(pos['direction'])}</textarea></label>"
                         f"<label><input type='checkbox' name='scouting' value='on'{' checked' if pos['scouting'] else ''}> Scout for it every "
                         f"<input name='scout' type='number' min='1' step='1' value='{pos['scout'] or 24}' class='hours'> hours "
                         f"<span class='muted'>(if worked on since)</span></label>"
                         f"<label class='stack'>What scouting should favour here<textarea name='scout_note' class='short' placeholder='e.g. faster test runs; papers on forecasting; nothing that changes the stack'>{e(pos['scout_note'])}</textarea></label>"
                         f"<button>Save</button></form>"
                         f"<div class='focus'><b>Now: {e(f['milestone'] or 'no roadmap yet')}</b><ul>{lines}</ul></div>"
                         + (f"<details><summary>What it decided here</summary>{made}</details>" if made else "") + "</div>")
        body = head + ("".join(cards) if len(cards) > 1 else cards[0] + "<p class='muted'>No projects yet.</p>")
        return shell(reg, -1, body)
    # Every project at a glance first, one line each (what it is at, what waits on the person), then what waits on them,
    # project by project, then, folded, what the steward decided.
    made = monitor.decisions(None, 10)
    recent = "".join(f"<div class='note'><span class='who'>{e(Path(d['project']).name)} · {e(d['at'][:16].replace('T', ' '))}</span>"
                     f"<div>{e(d['text'])}</div></div>" for d in made)
    last = monitor.said_last()
    said = (f"<a class='saidlast' href='/steward?view=console'><span class='who'>The steward said · {e(on_day(last['at']))}"
            f"</span><div class='said'>{e(' '.join(re.sub(r'[*_`#>]', '', last['text']).split()))}</div></a>" if last else "")
    follows = "".join(f"<label><input type='checkbox' name='p' value='{i}'{' checked' if monitor.involved(p) else ''}> {e(p.name)}</label>"
                      for i, p, _ in arranged(plist) if p.exists())
    kept = sum(monitor.involved(p) for p in plist if p.exists())
    follow = (f"<details class='card fold' id='follows'><summary><h3>Projects it follows</h3><span class='muted'>{kept} of "
              f"{sum(1 for p in plist if p.exists())}</span></summary><form class='options' method='post' action='/steward/involved'>"
              f"<p class='muted'>Unchecked, the steward leaves a project out: it doesn't wake for it, report on it or scout for it, "
              f"and hears of it only when you bring it up.</p>{follows}<button>Save</button></form></details>") if plist else ""
    body = (head + said + "<div class='listtop'><h2>Projects</h2><a href='/projects'>All projects →</a></div>"
            + (f"<div class='card plist'>{project_list(plist, fold='steward')}</div>" if plist else "<p class='muted'>No projects yet.</p>")
            + archived_shelf(link=True) + follow
            + f"<h2>Needs you</h2><div class='card' id='needs-box'><div id='needs'>{needs_you(reg)}</div></div>"
            f"<script>{KEEP_CURRENT}keepCurrent(document.getElementById('needs'), '/needs');</script>"
            + (f"<details class='card fold'><summary><h3>What the steward decided</h3><span class='muted'>{len(made)}"
               f"{' · ' + e(on_day(made[0]['at'])) if on_day(made[0]['at']) else ''}</span></summary>{recent}</details>" if made else ""))
    return shell(reg, -1, body)


def status_card(pid, snap, work="", waiting=None):
    """The project's status: working / needs you / idle / off, as its console shows it; what it is at, from its
    roadmap (never its console's spinner words); its background agents; and, given the count, when nothing waits on
    the person, that it doesn't (what does is listed just below)."""
    act = snap.get("activity") or {}
    agents = "".join(f"<div class='agent{' current' if a['current'] else ''}'><span>{'●' if a['current'] else '○'} {e(a['name'])}</span>"
                     f"<span class='muted'>{e(a['detail'])}</span></div>" for a in act.get("agents", []))
    return (f"<a class='status' id='status-{pid}' href='/?p={pid}&view=console'><div class='statusline'>"
            f"<span class='sdot {snap['state'].replace(' ', '-')}'></span><b>{e(snap['state'])}</b>"
            f"<span class='muted'>{(' · ' + e(work)) if work else ''}</span></div>"
            f"<div class='agents'>{agents}</div>"
            + ("" if waiting is None else f"<div class='waits muted'{' hidden' if waiting else ''}>Nothing waits on you.</div>")
            + "</a>")


def showcase_html(pid, root):
    """What the project made that's worth seeing, newest first: what it is, how to see or try it, and the person's
    rating and comments, none of them required. The rest folds into Earlier, the project's history of what it showed."""
    rows = showcase.entries(root)
    if not rows:
        return ("<h2>Showcase</h2><p class='muted'>Nothing shown yet. When a deliverable reaches a stage where it should be "
                "good, the agent puts it here: what it is and how to see it. Rate or comment if you like; nothing waits on you.</p>")
    back = f"/?p={pid}&view=showcase"

    def card(x):
        hidden = (f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='id' value='{e(x['id'])}'>"
                  f"<input type='hidden' name='back' value='{e(back)}'>")
        link = ""
        if x.get("link"):
            new_tab = " target='_blank' rel='noopener'"
            href = f"/showcase/open?p={pid}&id={e(x['id'])}"
            image = not showcase.is_url(x) and showcase.mime(x).startswith("image/")
            link = (f"<a href='{href}'{new_tab}><img class='shot' src='{href}' alt='{e(x['title'])}'></a>" if image
                    else f"<a class='open' href='{href}'{new_tab}>Open {e(x['link'] if not showcase.is_url(x) else 'it')} →</a>")
        stars = "".join(f"<button name='stars' value='{n}' class='star{' on' if (x['stars'] or 0) >= n else ''}' "
                        f"title='{n} of 5'>★</button>" for n in range(1, 6))
        said = "".join(f"<div class='note'><span class='who'>you · {e(c['at'][:10])}</span><div>{e(c['text'])}</div></div>"
                       for c in x["comments"])
        return (f"<div class='card show'><div class='showhead'><b>{e(x['title'])}</b>"
                f"<span class='who'>{e(x.get('item') or '')} {e(x['at'][:10])}</span></div>"
                f"<div>{e(x['what'])}</div>" + (f"<div class='try'><b>Try:</b> {e(x['try'])}</div>" if x.get("try") else "")
                + link + f"<form class='rate' method='post' action='/showcase/rate'>{hidden}{stars}</form>{said}"
                f"<details><summary class='muted'>Comment</summary><form class='add' method='post' action='/showcase/comment'>"
                f"{hidden}<textarea name='text' placeholder='What you think of it: it reaches the agent'></textarea>"
                f"<button>Send</button></form></details></div>")
    fresh, earlier = rows[:showcase.FRESH], rows[showcase.FRESH:]
    return ("<h2>Showcase</h2>" + "".join(card(x) for x in fresh)
            + (f"<details class='card'><summary><h3>Earlier ({len(earlier)})</h3></summary>"
               + "".join(card(x) for x in earlier) + "</details>" if earlier else ""))


def nest_page(reg, name):
    """A nest's own page: the shared vision its projects build toward, its projects, and every member's links and
    demos gathered in one place, each marked with the project it came from (open, rate and comment there)."""
    from . import nest
    plist = projects(reg)
    n = nest.nests().get(name)
    if n is None:
        return shell(reg, -2, f"<h1>No nest named {e(name)}</h1><p class='muted'>colony nest NAME PROJECT... makes one.</p>")
    at = {str(p.resolve()): i for i, p in enumerate(plist)}
    members = [(at[m], plist[at[m]]) for m in n.get("members", []) if m in at]
    vision = n.get("vision", "").strip()
    out = [f"<h1>{e(name)} nest</h1>",
           "<h2>Shared vision</h2><div class='card'>" + (prose(vision) if vision else "<p class='muted'>No shared vision yet: the why these projects build toward together. Agree it "
           "with any member's agent; it records your words and every member hears it.</p>") + "</div>",
           *([f"<h2>Standards</h2><div class='card'>" + "".join(
               f"<div class='note'>“{e(r['words'])}”<span class='who'>{' · said in ' + e(r['from']) if r.get('from') else ''}"
               f" · {e(r['at'][:10])}</span></div>" for r in n["standards"]) + "</div>"] if n.get("standards") else []),
           "<h2>Projects</h2><div class='card plist'>"]
    for i, p in members:
        out.append(project_row(i, p, first_sentence_of(roadmap(p)["vision"] or roadmap(p)["goal"]) if p.exists() else ""))
    back = f"<input type='hidden' name='back' value='/nest?n={e(urllib.parse.quote(name))}'>"
    others = [(i, p) for i, p in enumerate(plist) if str(p.resolve()) not in n.get("members", [])]
    out.append("<details class='more'><summary>Add or take out projects</summary><ul class='folders'>"
               + "".join(f"<li><b>{e(p.name)}</b><div class='folderacts'><form method='post' action='/nest/leave'>{back}"
                         f"<input type='hidden' name='p' value='{i}'><button class='quiet'>Take out</button></form></div></li>"
                         for i, p in members)
               + "</ul>" + (f"<form method='post' action='/nest/join' class='nestadd'>{back}<input type='hidden' name='name' "
                            f"value='{e(name)}'><select name='p'>" + "".join(
                                f"<option value='{i}'>{e(p.name)}{' (in ' + e(nest.nest_of(p)) + ')' if nest.nest_of(p) else ''}</option>"
                                for i, p in others) + "</select><button>Add to this group</button></form>" if others else "")
               + "<p class='muted'>A project is in one group at a time: adding one from another group moves it here. A group "
                 "with no projects left keeps its shared vision, if it has one.</p></details>")
    away = [k for k in archived_off() if nest.nest_of(k) == name]
    if away:
        out.append(f"<div class='archrow'><div class='muted'>Archived: {e(', '.join(Path(k).name for k in away))}</div>"
                   f"<form method='post' action='/bring-back'><input type='hidden' name='nest' value='{e(name)}'>"
                   f"<button class='quiet'>Bring back</button></form></div>")
    if members:
        out.append(f"<details class='more'><summary>Archive the group</summary><form method='post' action='/nest/archive' "
                   f"class='nestadd'><input type='hidden' name='name' value='{e(name)}'><input name='why' placeholder="
                   f"'Why (optional; its agents read it when it comes back)'><button class='quiet'>Archive the group</button></form>"
                   "<p class='muted'>Off the board and the steward's watch at once (an agent at work finishes that turn "
                   "first), nothing waking it. All projects → Archived brings it back, each in the same conversation.</p></details>")
    out.append("</div>")
    links = [(i, p, x) for i, p in members if p.exists() for x in pins.pins(p)]
    new_tab, icons = " target='_blank' rel='noopener'", {"url": "🔗", "upload": "📎"}
    out.append(f"<h2>Links ({len(links)})</h2><div class='card'>" + ("".join(
        f"<div class='pin'><a class='pintitle' href='/pin/open?p={i}&id={e(x['id'])}'{'' if pins.is_text(x) else new_tab}>"
        f"{icons.get(x['kind'], '📄')} {e(x['title'])}</a> <span class='who'>{e(p.name)}</span></div>"
        for i, p, x in links) or "<p class='muted'>Nothing pinned in these projects yet.</p>") + "</div>")
    demos = sorted(((i, p, x) for i, p in members if p.exists() for x in showcase.entries(p)),
                   key=lambda r: r[2]["at"], reverse=True)
    out.append(f"<h2>Demos ({len(demos)})</h2>")
    for i, p, x in demos:
        open_it = (f"<a class='open' href='/showcase/open?p={i}&id={e(x['id'])}' target='_blank' rel='noopener'>Open →</a>"
                   if x.get("link") else "")
        stars = f" · {'★' * x['stars']}" if x.get("stars") else ""
        out.append(f"<div class='card show'><div class='showhead'><b>{e(x['title'])}</b><span class='who'>{e(p.name)} · "
                   f"{e(x['at'][:10])}{stars}</span></div><div>{e(x['what'])}</div>"
                   + (f"<div class='try'><b>Try:</b> {e(x['try'])}</div>" if x.get("try") else "") + open_it
                   + f" <a class='muted' href='/?p={i}&view=showcase'>rate or comment in {e(p.name)}</a></div>")
    if not demos:
        out.append("<p class='muted'>Nothing shown yet in these projects.</p>")
    return shell(reg, -2, "".join(out))


def pin_row(pid, p):
    """One thing in the Library: tap to open (a text file opens to edit), comment to the agent about it, or unpin it."""
    icon = {"url": "🔗", "upload": "📎"}.get(p["kind"], "📄")
    new_tab = "" if pins.is_text(p) else " target='_blank' rel='noopener'"
    hidden = f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='id' value='{e(p['id'])}'>"
    return (f"<div class='pin'><div class='pinline'><a class='pintitle' href='/pin/open?p={pid}&id={e(p['id'])}'{new_tab}>"
            f"{icon} {e(p['title'])}</a><details class='pinmore'><summary>⋯</summary><div class='pinmenu'>"
            f"<form class='add' method='post' action='/pin/comment'>{hidden}"
            f"<textarea name='text' placeholder='Comment to the agent about this'></textarea><button>Send</button></form>"
            f"<form class='add regroup' method='post' action='/unpin'>{hidden}<input name='why' placeholder='Why it is done (optional)'>"
            f"<button class='quiet'>Archive</button></form></div></details></div>"
            f"<div class='who'>{'by the agent · ' if p['by'] == 'agent' else ''}"
            + (f"{e(p['why'])} · " if p.get("why") else "")
            + f"{e(p['target'] if p['kind'] != 'url' else p['target'][:60])}</div></div>")


def pinned_section(pid, root):
    """The Library's newest few, at the top of the Overview, the rest a tap away."""
    rows = [pin_row(pid, p) for p in pins.pins(root)]
    if not rows:
        return ""                          # nothing kept, no section: adding is also under Message, at the top
    return (f"<div class='pinned' id='pinned'><div class='pinhead'><h2>Library</h2><a href='/?p={pid}&view=library'>"
            f"{'All ' + str(len(rows)) + ' →' if len(rows) > PINS_SHOWN else 'Open →'}</a></div>"
            + "".join(rows[:PINS_SHOWN]) + "</div>")


PINS_SHOWN = 5           # the newest pins shown; the rest fold


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
    Waiting on you, the sidebar's count, `colony projects` and the steward's wake-ups all read this.
      gate    a decision the agent put to the person (`colony gate`), once due: not before its item, nor their hours;
              a skipped default that has come to stand is answered first
      choice  an on-screen choice in its console, such as a permission or trust question
      screen  its console needs the person but the choice can't be read (answer it in the console)
      ask     a turn that asked the person something in the console, once their hours hold
      verify  a roadmap item built but waiting to be checked or accepted
    Each has a key that stays the same while it waits, so it is announced once."""
    if not p.exists():
        return []
    gs = gates(p)
    if stand_defaults(p, gs):
        gs = gates(p)
    out = [{"kind": "gate", "key": "gate:" + g["id"], "gate": g, "summary": f"opened a gate: {g['question']}"}
           for g in gs if due(g)]
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
    out += [{"kind": "ask", "key": "ask:" + a["id"], "ask": a, "summary": "asked you: " + a["text"][-300:]}
            for a in asks(p) if not a["held"]]
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
    if key.startswith("ask:"):                       # held for the person's hours or not
        w = next(({"kind": "ask", "key": key, "ask": a} for a in asks(root) if "ask:" + a["id"] == key), None)
    else:
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


def clear_form(hidden, keys, label="Clear"):
    """Clearing something off the person's list (the agent hears quietly, on its next turn). Never the first thing
    under a thumb: it sits at the foot of what it clears, and for a decision, inside its own answer."""
    return (f"<form class='clear' method='post' action='/clear'>{hidden}<input type='hidden' name='key' value='{e(keys)}'>"
            f"<button class='quiet' title='Clear it: the agent hears quietly, on its next turn'>{e(label)}</button></form>")


def question_lines(text):
    """The sentences in a turn that ask something, as written (its code and links kept, list marks off); a question
    that follows a clause ending in a colon keeps it."""
    out = []
    for block in re.split(r"```.*?```", text or "", flags=re.S):
        parts = [s.strip() for s in re.split(r"(?<=[.!?:])\s+|\n", block)]
        for k, s in enumerate(parts):
            if re.search(r"\?[)*_\"'»]*$", re.sub(r"`[^`]*`|https?://\S+", " ", s).strip()):
                lead_in = parts[k - 1] if k and parts[k - 1].endswith(":") and len(parts[k - 1]) < 90 else ""
                out.append(re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", f"{lead_in} {s}".strip()))
    return out


def reply_form(pid, hidden, ask_id, keys):
    """A reply typed into the project's console, its Send on one row with Clear: Send at the right, under the thumb,
    and Clear at the far left."""
    fid = f"reply-{pid}-{e(ask_id)}"
    return (f"<form class='add' id='{fid}' method='post' action='/reply'>{hidden}"
            f"<textarea name='text' placeholder='Your reply goes straight to its console'></textarea></form>"
            f"<div class='needfoot'>{clear_form(hidden, keys)}<button form='{fid}'>Send</button></div>")


ASK_WHOLE = 320          # characters: a turn this short shows whole, its question already in sight


def ask_html(text):
    """A turn that asked the person something: its questions first, as written, and the whole turn beneath them,
    open (a question can lean on what came before it; Fold puts it away), as a page shows prose. With no sentence that reads as a question, its last paragraph leads (where an agent
    leaves what it asks of the person). A short turn shows whole."""
    text = (text or "").strip()
    asked, blocks = question_lines(text), [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    # a link written in it, to a board page or https, is one tap, as in Chat
    links = lambda h: re.sub(r"(?<!!)\[([^\]]+)\]\(((?:/(?!/)|https://)[^)\s]+)\)", r"<a href='\2'>\1</a>", h)
    if len(text) <= ASK_WHOLE or (not asked and len(blocks) < 2):
        return f"<div class='asktext'>{links(prose(text))}</div>"
    lead = "".join(f"<p><b>{_inline(q)}</b></p>" for q in asked) if asked else prose(blocks[-1])
    return (f"<div class='asked'>{links(lead)}</div>"
            f"<details class='full' open><summary>Full message</summary><div class='asktext'>{links(prose(text))}</div></details>")


def waiting_on(pid, p, back, label=True, with_gates=True):
    """The project's moments as the person answers them, in one list: the decisions, their box, then the rest."""
    decided, box, others = waiting_parts(pid, p, back, label, with_gates)
    return decided + ([box] if box else []) + others


def waiting_parts(pid, p, back, label=True, with_gates=True, day=None):
    """The project's moments as the person answers them, where they stand: (its decisions, the box answering them by
    number, the rest). Its gates are the day's decisions, by number, answered together in one box (each reaches the
    agent as a note); a choice gets a button per option; a question (with what it left to verify) gets a reply typed
    into the console; items to verify get one note about them. Without its gates, the rest. Labelled, each says its
    project, and the box links to its day's page (or, given day, as day says). Clearing one sits at its foot, or for a
    decision inside its own answer: never first under a thumb."""
    decided, others = [], []
    hidden = (f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='back' value='{e(back)}'>")
    groups = moments(p)
    ours = [g for g in groups if g[0]["kind"] == "gate"] if with_gates else []
    numbered = decisions(p) if ours else []
    number = {g["id"]: n for n, g in numbered}
    ours.sort(key=lambda g: int(number.get(g[0]["gate"]["id"], 0)))
    rest = [g for g in groups if g[0]["kind"] != "gate"]
    box = answer_box(pid, p, numbered, back, day=label if day is None else day) if ours else ""
    for group in ours + rest:
        rows = decided if group[0]["kind"] == "gate" else others
        w = group[0]
        keys = ",".join(x["key"] for x in group)
        foot = f"<div class='needfoot'>{clear_form(hidden, keys)}</div>"
        who = lambda what: f"<div class='who'><span class='kind'>{e(p.name) + ' · ' if label else ''}{what}</span></div>"
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
                        f"<button class='quiet' name='action' value='changes'>Request corrections</button></form>{foot}</div>")
        elif w["kind"] == "gate":
            g = w["gate"]
            folded = not g.get("points")                 # its own answer folds away, and clearing it waits inside
            rows.append(f"<div class='need'>{who('gate' + (' on ' + e(g['item']) if g.get('item') else ''))}"
                        + gate_body(p, pid, g, back, number=number.get(g["id"]), fold=True,
                                    tail=f"<div class='needfoot'>{clear_form(hidden, keys, 'Clear without an answer')}</div>" if folded else "")
                        + ("" if folded else foot) + "</div>")
        elif w["kind"] == "choice":
            rows.append(f"<div class='need'>{who('asking in its console')}"
                        + "".join(f"<div>{e(q)}</div>" for q in w["question"])
                        + "<div class='choices'>" + "".join(
                            f"<form method='post' action='/choose'>{hidden}<input type='hidden' name='option' value='{e(o)}'>"
                            f"<button class='quiet'>{e(o)}</button></form>" for o in w["options"]) + f"</div>{foot}</div>")
        elif w["kind"] == "screen":
            rows.append(f"<div class='need'>{who('needs you in its console')}<pre>{e(chr(10).join(w['lines']))}</pre>"
                        f"<a class='go' href='/?p={pid}&view=console'>Open its console →</a>{foot}</div>")
        elif w["kind"] == "ask":
            a = w["ask"]
            rows.append(f"<div class='need'>{who('asked in its console · ' + e(a['at'][:16].replace('T', ' ')))}"
                        f"{ask_html(a['text'])}{to_verify}{reply_form(pid, hidden, a['id'], keys)}</div>")
        else:                                         # items ready for the person's OK, with no question beside them
            rows.append(f"<div class='need'>{who('ready for your OK' if len(verify) == 1 else f'{len(verify)} ready for your OK')}{to_verify}{foot}</div>")
    return decided, box, others


def waiting_html(pid, root):
    """A project's Waiting on you: its moments; a decision in another of its agents that holds the version it shares,
    pointed to (it is answered there); and what is held for the person's hours. Nothing, nothing at all."""
    held = version_hold(root)
    elsewhere = [(m, g) for m, g in (held[1] if held else []) if m.resolve() != Path(root).resolve()]
    return ("".join(waiting_on(pid, root, f"/?p={pid}", label=False))
            + "".join(f"<div class='need held'><div class='who'><span class='kind'>holds this version</span></div>"
                      f"Waiting on you in {where_waiting(m)}: <b>{e(g['question'])}</b></div>" for m, g in elsewhere)
            + held_html(pid, root))


def held_html(pid, root, back=None):
    """Gates opened and questions asked outside the person's active hours, below what waits on them and not counted
    with it: they reach their list when the hours begin, and can be answered here before then."""
    later = [g for g in gates(root) if not g["answer"] and g.get("held") and not g.get("waits_for")]
    asked = [a for a in asks(root) if a["held"]]
    if not later and not asked:
        return ""
    back = back or f"/?p={pid}"
    hidden = f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='back' value='{e(back)}'>"
    foot = lambda key, label="Clear": f"<div class='needfoot'>{clear_form(hidden, key, label)}</div>"
    until = lambda x: e(hours.at_clock(epoch(x["held"])))
    return ("<p class='muted'>From outside your active hours: these reach your list when the hours begin, and you can "
            "answer them now.</p>" + "".join(
                f"<div class='need held'><div class='who'><span class='kind'>gate"
                f"{' on ' + e(g['item']) if g.get('item') else ''} · held until {until(g)}</span></div>"
                f"{gate_body(root, pid, g, back)}{foot('gate:' + g['id'], 'Clear without an answer')}</div>" for g in later) + "".join(
                f"<div class='need held'><div class='who'><span class='kind'>asked in its console · "
                f"held until {until(a)}</span></div>{ask_html(a['text'])}{reply_form(pid, hidden, a['id'], 'ask:' + a['id'])}</div>"
                for a in asked))


def support_rows():
    """Supports the steward has brought the person: its question, and Test it / Approve suggesting it / Drop,
    or talk it over in its console. The answer goes back to the steward."""
    from . import supports
    rows = []
    for r in supports.asking():
        a = r["asking"]
        approve = "<button name='verdict' value='approve'>Approve suggesting it</button>" if supports.suggestible(r) else ""
        rows.append(f"<div class='need'><div class='who'><span class='kind'>{e(Path(a['project']).name)} · a find the steward brought</span></div>"
                    f"<b>{e(r['name'])}</b> <span class='muted'>({e(r['status'])}{', reference' if r.get('kind') == 'reference' else ''})</span>"
                    f"<div class='asktext'>{prose(a['text'])}</div><p class='muted'>{e(supports.NOTICE)}</p>"
                    f"<form class='verdict' method='post' action='/support'><input type='hidden' name='id' value='{e(r['id'])}'>"
                    f"{approve}<button name='verdict' value='test' class='{'quiet' if approve else ''}'>Test it</button>"
                    f"<input name='text' placeholder='anything to tell the steward'>"
                    f"<button class='quiet' name='verdict' value='drop'>Drop</button></form>"
                    f"<a class='go' href='/steward?view=console'>Talk it over with the steward →</a></div>")
    return rows


def lesson_rows():
    """Lessons waiting on the person: a proposal (Adopt / Not this) or one the evidence turned against (Keep /
    Retire), each with what taught it and where it would show."""
    from . import lessons
    rows = []
    for r in lessons.waiting():
        where = "every project" if r["scope"] == "colony" else Path(r["scope"]).name
        sus = r["state"] == "suspect"
        buttons = ("<button name='verdict' value='keep'>Keep it</button><button class='quiet' name='verdict' value='retire'>Retire it</button>"
                   if sus else "<button name='verdict' value='adopt'>Adopt</button><button class='quiet' name='verdict' value='decline'>Not this</button>")
        rows.append(f"<div class='need'><div class='who'><span class='kind'>{'a lesson in question' if sus else 'a lesson'}, "
                    f"from {e(r['by'])} · shown to {e(where)} at <code>colony {e(r['action'])}</code></span></div>"
                    f"<b>{e(r['text'])}</b><div class='asktext muted'>{'Against it: ' + e(r.get('why', '')) if sus else 'What taught it: ' + e(r['evidence'])}</div>"
                    f"<form class='verdict' method='post' action='/lesson'><input type='hidden' name='id' value='{e(r['id'])}'>"
                    f"{buttons}</form></div>")
    return rows


def lessons_kept():
    """The lessons the person adopted, folded at the foot of Needs you, each retirable with a tap."""
    from . import lessons
    kept = [r for r in lessons.load() if r["state"] == "adopted"]
    if not kept:
        return ""
    items = "".join(f"<li><code>colony {e(r['action'])}</code> · {e('every project' if r['scope'] == 'colony' else Path(r['scope']).name)}"
                    f" · shown {r.get('shown', 0)}: {e(r['text'])}<form class='inline' method='post' action='/lesson'>"
                    f"<input type='hidden' name='id' value='{e(r['id'])}'><button class='quiet' name='verdict' value='retire'>Retire</button></form></li>"
                    for r in kept)
    return f"<details class='more'><summary>Lessons you adopted ({len(kept)})</summary><ul>{items}</ul></details>"


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
    const opened = new Set([...el.querySelectorAll('details[open][data-k]')].map((d) => d.dataset.k));   // what they unfolded stays so
    el.innerHTML = last = html;
    el.querySelectorAll('details[data-k]').forEach((d) => { if (opened.has(d.dataset.k)) d.open = true; });
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
    """Colony decisions are deliberately outside project questions and steward wake-ups."""
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
    if not pending_only and state['history']:
        out.append(f"<details class='more'><summary>Recent choices ({min(len(state['history']), 30)})</summary>")
        for event in reversed(state['history'][-30:]):
            out.append('<ul>' + row(selection.role_label(event['role']), event['before'], event['after'], event['prices']) + '</ul>'
                       + f"<p class='muted'>{e(event['reason'])} · {e(event['at'])}</p>")
            if selection.returnable(state, event):
                out.append("<form method='post' action='/model-decision'><input type='hidden' name='action' value='rollback'>"
                           f"<input type='hidden' name='event' value='{e(event['id'])}'>"
                           "<button>Return to previous choice</button></form>")
        out.append("</details>")
    out.append("<p class='muted'>Benchmark task costs and token rates are separate evidence; actual project spend varies. "
               "Changes take effect when a console reloads while idle.</p></section>")
    return ''.join(out)


NEEDS_SHOWN = 3          # a project's decisions shown in Needs you; the rest fold, its box for answering by number below


def needs_you(reg):
    """Needs you: what every project is waiting on the person for, in one place, project by project in the board's
    order, each numbered as its own page numbers it: a project's first few decisions, the rest folded, its box for
    answering them by number, then the rest. And the supports the steward has brought them."""
    choices = model_changes(pending_only=True)
    from . import monitor
    rows = ([choices] if choices else []) + support_rows() + lesson_rows()
    aside = []                                         # the projects the person left out of the steward's: named, not listed
    for pid, p, _ in arranged(projects(reg)):
        if p.exists() and not monitor.involved(p):
            n = len(moments(p))
            aside += [f"<a href='/?p={pid}'>{e(p.name)}</a> ({n})"] if n else []
            continue
        decided, box, others = waiting_parts(pid, p, "/steward", label=False, day=True) if p.exists() else ([], "", [])
        if not decided and not others:
            continue
        more = decided[NEEDS_SHOWN:]
        fold = (f"<details class='more' data-k='more-{pid}'><summary>{len(more)} more decision{'s' * (len(more) != 1)} "
                f"from {e(p.name)}</summary>{''.join(more)}</details>") if more else ""
        rows.append(f"<section class='ngroup'><h3 class='nhead'><a href='/?p={pid}'>{e(p.name)}</a>"
                    f"<span class='badge gate' title='waiting on you'>{len(decided) + len(others)}</span></h3>"
                    f"{''.join(decided[:NEEDS_SHOWN])}{fold}{box}{''.join(others)}</section>")
    out = "".join(rows) if rows else "<p class='muted'>Nothing is waiting on you.</p>"
    return out + (f"<p class='muted'>Also waiting, in projects the steward doesn't follow: {', '.join(aside)}.</p>" if aside else "") + lessons_kept()


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
            rows.append(f"<tr><th>{e(family)} · {e('steward' if role == 'monitor' else role)}</th><td>{e(desc)}<div class='muted'>Accepted: {e(accepted)}</div></td></tr>")
    cards = ''.join(model_card(bench.card(mid, entries)) for _, mid, _, _ in bench.lineup())
    title = f'{ceiling:.2f}' if ceiling is not None else 'unavailable'
    problems, notes, _ = intelligence.health(entries)
    health = (f"<p><strong style='color:var(--flag)'>Model data:</strong> {e('; '.join(problems + notes))}.</p>"
              if problems or notes else '')
    from . import turbo
    health += ("<p class='muted'>Turbo: " + e("; ".join(turbo.line(k) for k in families)) + ". A project that ticks "
               "stronger models runs two positions toward Intelligence while its program's turbo is on.</p>") if families else ''
    return shell(reg, -2, model_changes() + "<header><h1>Models</h1><p>Artificial Analysis Intelligence Index and cost per benchmark task. "
                 f"Shared runnable intelligence ceiling: <strong>{title}</strong>. "
                 "Scores within one point count as equal; the cheaper pair wins. Estimates are labelled and can participate. "
                 "Cost per task is the token volume a page last measured at today's price. "
                 "Usage never lowers an Auto goal. <a href='/settings'>Change the Auto slider in Settings</a>.</p>" + health + "</header>"
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
        kind = 'Estimated' if point['estimated'] else 'Carried' if point.get('carried') else 'Measured'
        sources = []
        for metric, evidence in point['evidence'].items():
            if not evidence:
                continue
            link = f"<a href='{e(evidence.get('url') or '')}' rel='noopener' target='_blank'>Artificial Analysis</a>"
            if evidence.get('estimated'):
                sources.append(f"<li>{e(metric)}: {e(json.dumps(evidence['derivation'], ensure_ascii=False))}</li>")
            elif evidence.get('carried'):
                d = evidence['derivation']
                sources.append(f"<li>{e(metric)}: token volume {link} measured {e(evidence['date'])}, at today's "
                               f"${d['price']:g} per 1M tokens (API, {e(d['price_date'])})</li>")
            else:
                warning = (evidence.get('derivation') or {}).get('warning')
                sources.append(f"<li>{e(metric)}: {link}" + (' API' if evidence.get('source', '').endswith('(API)') else '')
                               + f", {e(evidence['date'])}" + (f"; {e(warning)}" if warning else '') + "</li>")
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
        if codex_remote.paired(root):
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
    offered = any(codex_remote.pairing_choice(root) == 'offered' for root in roots)
    return (f"<details class='more' id='codex-pairing'{' open' if offered else ''}><summary>Codex in ChatGPT "
            f"<span class='muted'>({len(roots)} project{'s' * (len(roots) != 1)})</span></summary><p>Pair each project once with your ChatGPT app. "
            "Sign in to the same account as Codex. When you are ready, get a fresh code and open Codex → Add manually in the app. "
            "You can pair another device here later.</p><ul class='folders'>" + ''.join(rows) + '</ul></details>')


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


PERMISSION_LABELS = [("ask", "ask each time"), ("edits", "accept edits"), ("all", "allow everything"), ("plan", "plan only")]

# Settings opens where the person left it: the sections they had open, and the one a link names
SETTINGS_FOLDS = """<script>
(() => {
  const all = [...document.querySelectorAll('details.setsec')];
  let open = []; try { open = JSON.parse(localStorage.getItem('setsec') || '[]'); } catch (e) {}
  for (const d of all) if (open.includes(d.id)) d.open = true;
  // a link to a place inside (a section, a fold in it) opens its way there
  for (let d = location.hash && document.getElementById(location.hash.slice(1)); d; d = d.parentElement)
    if (d.tagName === 'DETAILS') d.open = true;
  for (const d of all) d.addEventListener('toggle', () => {
    try { localStorage.setItem('setsec', JSON.stringify(all.filter(x => x.open).map(x => x.id))); } catch (e) {}
  });
})();
</script>"""


def intelligence_label(n):
    from . import intelligence
    try:
        return intelligence.POSITIONS[int(n)]
    except (ValueError, IndexError, TypeError):
        return str(n)


def connections_summary():
    from . import connections
    connections.refresh_personal()                   # their Claude connectors, looked up in the background
    svcs = connections.services()
    lacking = sum(bool(connections.missing(k, c)) for k, c in connections.load()["connections"].items())
    return f"{len(svcs)} service{'s' * (len(svcs) != 1)}" + (f" · {lacking} waiting for keys" if lacking else "")


VIA = {"colony": "via colony", "claude": "via Claude", "chatgpt": "via ChatGPT", "off": "off", None: "off"}


def _conn_form(do, fields, back, button, **hidden):
    return (f"<form method='post' action='/connections' class='options'><input type='hidden' name='do' value='{do}'>"
            f"<input type='hidden' name='back' value='{e(back)}'>"
            + "".join(f"<input type='hidden' name='{k}' value='{e(str(v))}'>" for k, v in hidden.items() if v is not None)
            + f"{fields}<div class='dangers'><button>{button}</button></div></form>")


def _paths(s, root, inherit=False):
    """The ways a service can be reached, as radio choices."""
    from . import connections
    now = connections.path_for_choice(s, root)
    ways = [("inherit", f"As colony-wide ({VIA[connections.path_for(s['id'], None)]})")] if inherit else []
    ways += [(w, VIA[w]) for w in ("colony", "claude", "chatgpt") if s.get(w)] + [("off", "Off")]
    return "".join(f"<label class='tick'><input type='radio' name='how' value='{w}'{' checked' if w == now else ''}> {label}</label>"
                   for w, label in ways)


def _keys_fields(name, c, root):
    from . import connections
    have = connections.keys(name, root)
    return "".join(f"<label>{e(k)} <input type='password' name='key_{e(k)}' autocomplete='off' "
                   f"placeholder='{'Kept; paste to replace' if have.get(k) else 'Paste it'}'></label>" for k in c.get("keys") or ())


def _holds_fields(c, held):
    """Which tools wait for the tap: by the server's own marks, or chosen."""
    seen = c.get("seen") or {}
    auto = held is None
    # what it has listed, and before that what it offers and holds, so saving never lets go of a hold unseen
    tools = sorted(set(seen) | set(c.get("tools") or ()) | set(held or ()))
    ticks = "".join(f"<label class='tick'><input type='checkbox' name='tool' value='{e(t)}'"
                    f"{' checked' if (not seen.get(t) if auto else t in held) else ''}> {e(t)}{' (reads)' if seen.get(t) else ''}</label>"
                    for t in tools)
    return (f"<label class='tick'><input type='radio' name='mode' value='auto'{' checked' if auto else ''}> Everything but what it "
            f"marks read-only</label><label class='tick'><input type='radio' name='mode' value='chosen'{'' if auto else ' checked'}> "
            f"Only these:</label><div>{ticks or '<span class=muted>its tools show here once a project has used it</span>'}</div>")


def _audit_card(name, c):
    """The steward's audit on an approval card, and the buttons it allows: none while it runs, Remove alone once it refused."""
    from . import audit
    a = audit.current(c) or {}
    asked = f" (asked for by {e(Path(c['by']).name)})" if c.get("by") else ""
    remove = _conn_form("remove", "", "/connections", "Remove", name=name)
    if not a or a.get("state") == "running":
        return ("<p><b>The steward is auditing it</b>: a fresh model reads its code, without running it, and weighs it "
                f"against the work of the project that asked for it{asked}. Approve shows here once that's done.</p>" + remove)
    if a.get("state") in ("unread", "unreviewed"):
        return (f"<p><b>Not audited:</b> {e(a.get('why', ''))}. Approve it only if you trust where it comes from.</p>"
                + _conn_form("approve", "", "/connections", "Approve", name=name) + remove)
    word = {"yes": "yes", "no": "<b class='warn'>no</b>", "unsure": "<b class='warn'>unsure</b>"}
    facts = [f"from {e(a['file'])}" if a.get("file") else "",
             f"names these hosts: {e(', '.join(a['hosts']))}" if a.get("hosts") else "",
             f"depends on (not read): {e(', '.join(a['depends']))}" if a.get("depends") else ""]
    body = (f"<p><b>{'Refused by the steward' if audit.refused(a) else 'The steward'}'s audit</b>{asked}: safe {word[a['safe']]} · "
            f"fits the work {word[a['fits']]}. {e(a.get('summary', ''))}</p>"
            + ("<ul>" + "".join(f"<li>{e(x)}</li>" for x in a["concerns"]) + "</ul>" if a.get("concerns") else "")
            + f"<details><summary class='muted'>How it was checked</summary><p class='muted'>Read by {e(a.get('by', ''))}, "
              f"{e(a.get('at', ''))}; {'; '.join(f for f in facts if f) or 'nothing to read but its purpose'}.</p>"
            + ("<p class='muted'>Plain marks (where to look, not verdicts): " + e("; ".join(a["marks"])) + "</p>" if a.get("marks") else "")
            + (f"<pre class='report'>{e(a['report'])}</pre>" if a.get("report") else "") + "</details>")
    if audit.refused(a):
        return body + "<p class='muted'>It can't be approved; the agent that asked was told why.</p>" + remove
    if audit.misfit(a):
        return (body + "<p class='muted'>The steward doesn't see the work calling for it; what the work needs is yours to "
                "say, and the agent was told.</p>" + _conn_form("approve", "", "/connections", "Approve anyway", name=name) + remove)
    return body + _conn_form("approve", "", "/connections", "Approve", name=name) + remove


def connections_page(reg):
    """Connections: each service once, whichever sources reach it (colony's own, the person's Claude connector), with
    its colony-wide settings: how projects reach it, its keys, what waits for the tap, and which projects differ."""
    from . import audit, connections
    connections.refresh_personal()
    audit.kick()                                     # any connection waiting on its audit, started apart from the page
    data, every = connections.load(), [p for p in projects(reg) if p.exists()]
    out = ["<header><h1>Connections</h1></header><p class='muted'>Outside services your projects reach. Every project has "
           "each one unless you switch it off there (on its own Connections page, or as you add it). Through colony, keys "
           "stay in a file only you can read and what sends or spends waits for your tap; through your Claude or ChatGPT "
           "sign-in (Claude projects use the one, Codex projects the other), it goes straight out.</p>"]
    for svc, s in connections.services().items():
        s["id"] = svc
        way = connections.path_for(svc, None)
        differ = [p for p in every if connections.path_for(svc, p) != way or connections.has_own(
            data["connections"].get(s.get("colony"), {}), p)]
        sources = " or ".join(VIA[w] for w in ("colony", "claude", "chatgpt") if s.get(w))
        c = data["connections"].get(s.get("colony")) or {}
        lacking = s.get("colony") and connections.missing(s["colony"], c)
        review = s.get("colony") and not c.get("approved", True)
        body = ([f"<div class='card gate'><p><b>Added by an agent; {'it runs nowhere' if c.get('command') else 'no project gets it'} "
                 f"until you approve it.</b> "
                 + (f"It starts: <code>{e(' '.join(c['command']))}</code>" if c.get("command") else
                    f"It is only keys ({e(', '.join(c.get('keys') or []))}), for projects' own scripts.") + "</p>"
                 + _audit_card(s["colony"], c) + "</div>"] if review else [])
        if c.get("changed"):
            body.append(f"<div class='card gate'><p><b>Its tools changed since colony first saw them:</b> "
                        f"{e(', '.join(sorted(c['changed'])))}. What a tool says it does changed (or a new one appeared), "
                        "which is how a server turned against its approval looks. Until you accept the change, each of "
                        "them waits for your tap.</p>" + _conn_form("accept", "", "/connections", "Accept the change",
                                                                     name=s["colony"]) + "</div>")
        body += [f"<p class='muted'>Reachable {sources}.</p>" + (f"<p>{e(c['about'])}</p>" if c.get("about") else ""),
                "<h4>Projects reach it</h4>" + _conn_form("path", _paths(s, None), "/connections", "Save", svc=svc)]
        if s.get("colony"):
            fields = _keys_fields(s["colony"], c, None)
            if fields:
                body.append("<h4>Keys</h4>" + _conn_form("keys", fields, "/connections", "Save keys", name=s["colony"]))
            body.append("<h4>Waits for your tap</h4>" + (_conn_form("holds", _holds_fields(c, c.get("hold")), "/connections",
                                                                   "Save", name=s["colony"]) if c.get("command") else
                                                        f"<p class='muted'>Nothing: these keys are for projects' own scripts, "
                                                        f"which use them without asking.</p>"))
        else:
            body.append(f"<p class='muted'>Its sends go out without asking. For a tap first, or a separate account per "
                        f"project, ask any project's agent to add {e(s['title'])} through colony.</p>")
        if differ:
            body.append("<h4>Projects set their own</h4><p>" + ", ".join(
                f"<a href='/?p={projects(reg).index(p)}&view=connections'>{e(p.name)}</a> ({VIA[connections.path_for(svc, p)]}"
                + (", own settings" if connections.has_own(c, p) else "") + ")" for p in differ) + "</p>")
        out.append(f"<details class='conn card'><summary><b>{e(s['title'])}</b> · {VIA[way]}"
                   + (f" · {len(differ)} project{'s' * (len(differ) != 1)} differ" if differ else "")
                   + (" · <b class='warn'>its tools changed</b>" if c.get("changed") else "")
                   + ((" · <b class='warn'>refused by the audit</b>" if audit.refused(audit.current(c)) else
                       " · being audited" if not audit.current(c) or audit.current(c).get("state") == "running" else
                       " · <b class='warn'>to approve: not seen to fit the work</b>" if audit.misfit(audit.current(c)) else
                       " · <b class='warn'>to approve</b>") if review else " · <b class='warn'>keys missing</b>" if lacking else "")
                   + "</summary>" + "".join(body) + "</details>")
    if len(out) == 1:
        out.append("<div class='card'><p class='muted'>None yet.</p></div>")
    out.append("<p class='muted'>To add a service, ask any project's agent (\"connect our eBay account\"): it sets it up "
               "and it shows here for your keys.</p>")
    return shell(reg, -2, "".join(out))


def project_connections(pid, root):
    """A project's Connections: each service as colony-wide, through another source, off, or with its own account
    and holds."""
    from . import connections
    data, back = connections.load(), f"/?p={pid}&view=connections"
    out = [f"<p class='muted'>Set colony-wide on <a href='/connections'>Connections</a>; here this project can differ.</p>"]
    for svc, s in connections.services().items():
        s["id"] = svc
        c = data["connections"].get(s.get("colony")) or {}
        way, own = connections.path_for(svc, root), connections.has_own(c, root)
        here = bool(s.get("colony")) and connections.is_on(c, root)     # on here, if not yet usable (keys, approval)
        body = [_conn_form("path", _paths(s, root, inherit=True), back, "Save", svc=svc, p=pid)]
        if here:
            body.append(_conn_form("own", f"<label class='tick'><input type='checkbox' name='own' value='1'{' checked' if own else ''}> "
                                   f"Its own account{' and holds' if c.get('command') else ''}</label>", back, "Save", name=s["colony"], p=pid))
            if own:
                fields = _keys_fields(s["colony"], c, root)
                if fields:
                    body.append("<h4>Its keys</h4>" + _conn_form("keys", fields, back, "Save keys", name=s["colony"], p=pid))
                if c.get("command"):
                    body.append("<h4>Waits for your tap</h4>" + _conn_form(
                        "holds", _holds_fields(c, connections.holds(c, root)), back, "Save", name=s["colony"], p=pid))
        lacking = here and connections.missing(s["colony"], c, root)
        out.append(f"<details class='conn card'><summary><b>{e(s['title'])}</b> · {VIA[way]}{' · own settings' if own else ''}"
                   + (" · <b class='warn'>keys missing</b>" if lacking else "") + "</summary>" + "".join(body) + "</details>")
    if len(out) == 1:
        out.append("<div class='card'><p class='muted'>No services yet.</p></div>")
    return "".join(out)


def connection_ticks():
    """For a project being added: each service it would have, ticked; untick to leave it out."""
    from . import connections
    svcs = [(k, s) for k, s in connections.services().items() if connections.path_for(k, None)]
    if not svcs:
        return ""
    return ("<div class='muted'>Connections it has (untick to leave one out):</div><div>"
            + "".join(f"<input type='hidden' name='svc_listed' value='{e(k)}'><label class='tick'><input type='checkbox' "
                      f"name='svc' value='{e(k)}' checked> {e(s['title'])}</label>" for k, s in svcs) + "</div>")


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
        from . import turbo, usage
        used = usage.line(k) if pv.installed(p) else ""
        used = f"Usage: {used}" if used else ""
        out = signed_out().get(k)
        used = (f"signed out since {out}: sign in again in a terminal" + (f" · {used}" if used else "")) if out else used
        pace = f"<p class='muted' style='margin:0 0 8px 26px'>Pace: {e(turbo.line(k, named=False))}</p>" if pv.installed(p) else ""
        from . import catalog_freshness
        freshness = catalog_freshness.info(p)
        fresh = " · ".join(f"{e(label)}: {e(value)}" for label, value in freshness['fields'])
        fresh += (". <strong>" + e('; '.join(freshness['warnings'])) + "</strong>" if freshness['warnings'] else "")
        fresh = "<p class='muted'>Model list: " + fresh.lstrip('. ') + ". Re-read at the program's next session start.</p>"
        remote_info = ''
        if k == 'codex':                         # PROVIDER: each Codex project owns its app-server process
            from . import codex_remote
            hosts = codex_remote.statuses()
            count = sum(h['running'] for h in hosts)
            remote_info = f"<p class='muted'>{count} project daemon{'s' if count != 1 else ''} running"
            if hosts:
                remote_info += ': ' + '; '.join(e(Path(h.get('project', h['home'])).name) + ' — '
                                               + e(h.get('mode', 'not started'))
                                               + (': ' + e(h['reason']) if h.get('reason') else '') for h in hosts)
            remote_info += '. ChatGPT shows the machine name as host; each conversation carries its project name.</p>'
        return (f"<label><input type='checkbox' name='on' value='{k}'{' checked' if pv.enabled(p) else ''}> {e(p.label)} "
                f"<span class='muted'>({state}{also})</span></label>"
                + (f"<p class='muted' style='margin:0 0 8px 26px'>{e(used)}</p>" if used else "") + pace
                + f"<details class='more' style='margin:0 0 8px 26px'><summary class='muted'>Details{' · <b>model list stale</b>' if freshness['warnings'] else ''}</summary>"
                  f"{fresh}{remote_info}</details>")
    programs = (f"<form method='post' action='/providers' class='options'>"
                + "".join(program(k, p) for k, p in pv.PROVIDERS.items())
                + "<p class='muted'>Colony offers the ones ticked for new projects and its default. Projects already on one "
                  "you untick keep running. At least one stays on.</p><button>Save</button></form>")
    # one form, its fields placed in the sections they belong to (form='opts'): a save in any section keeps the rest
    own = lambda html: "<div class='optset'>" + re.sub(r"<(input|select|textarea)\b", r"<\1 form='opts'", html) + "</div>"
    save = "<button form='opts'>Save</button>"
    hours_fields = own(
        f"<label>Active hours <input name='active_hours' value='{e(s['active_hours'])}' size='11'></label>"
        f"<p class='muted'>When you're around, as 07:30-23:45 on this machine's clock, or off. Outside them agents keep "
        f"working, take what doesn't need you, and hold their questions, which reach you together when your hours begin.</p>"
        f"<label>Safe pause at <input name='safe_pause' value='{e(str(s['safe_pause'] or 'off'))}' size='4'> %</label>"
        f"<p class='muted'>Of a program's 5-hour or weekly limit: its projects land what's in flight, save their work and "
        f"tell you where things stand, instead of being cut off mid-task; colony wakes them at the reset.</p>") + save
    session_fields = own(
        provider_fields(s["provider"], s["model"], s["effort"], "the provider's default")
        + f"<label>Permissions <select name='permissions'>"
        + "".join(f"<option value='{k}'{' selected' if s['permissions'] == k else ''}>{label}</option>" for k, label in PERMISSION_LABELS)
        + "</select></label>"
        + f"<label><input type='checkbox' name='trust' value='on'{check('trust')}> Answer a new console's start-up questions "
          f"<span class='muted'>(folder trust, permission mode, Remote Control, hooks: so it runs as set up)</span></label>"
        + "".join(f"<label><input type='checkbox' name='remote_{k}' value='on'"
                  f"{' checked' if s['remote_by'].get(k, s['remote']) else ''}> "
                  f"New {e(p.label)} projects are reachable from {'ChatGPT' if k == 'codex' else 'the Claude app'} "
                  "<span class='muted'>(each project can turn it off)</span></label>"
                  for k, p in pv.PROVIDERS.items() if pv.usable(p))
        + "".join(f"<label><input type='checkbox' name='turbo_{k}' value='on'"
                  f"{' checked' if s['turbo_by'].get(k, True) else ''}> Turbo for {e(p.label)} "
                  "<span class='muted'>(when its weekly use runs behind pace, its projects with queued work are "
                  "turned up as each ticks in its own Settings)</span></label>"
                  for k, p in pv.PROVIDERS.items() if pv.usable(p))
        + f"<label>New projects go in <input name='new_root' value='{e(reg['new_root'])}'></label>") + save + (
        "<p class='muted'>These apply to new projects' sessions and to consoles started from now on.</p>")
    board_fields = own(
        f"<label><input type='checkbox' name='lan' value='on'{check('lan')}> Open from other devices on your network "
        f"<span class='muted'>(after colony restart)</span></label>"
        f"<label><input type='checkbox' name='messaging' value='on'{check('messaging')}> Projects can message each other "
        f"<span class='muted'>(one inbox per project)</span></label>"
        f"<label><input type='checkbox' name='auto_update' value='on'{check('auto_update')}> Keep Claude Code and Codex updated "
        f"<span class='muted'>(daily; a console moves to the new version once it sits idle, in the same conversation)</span></label>") + save
    steward_on = own(f"<label><input type='checkbox' name='monitor' value='on'{check('monitor')}> Run the steward with the board</label>") + save
    balance = own(auto_balance_fields()) + save
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
        signin = (f"<h3>Claude Code sign-in</h3><p>{'Long-lived sign-in connected: Claude Code consoles use it, so they are not signed out every week or so.' if tok else 'Using the regular sign-in, which expires every week or so. A long-lived one keeps every console signed in.'}</p>"
                  + (f"<p class='muted'>That token didn't work: {e(failed)}</p>" if failed else "")
                  + f"<ol class='steps'>{steps_}</ol><form method='post' action='/claude-token' class='options'>"
                  f"<label>Token <input type='password' name='token' autocomplete='off' placeholder='{'Paste a new token to replace it' if tok else 'Paste the token'}'></label>"
                  f"<div class='dangers'><button name='do' value='save'>Save and check</button>"
                  + ("<button name='do' value='remove' class='quiet'>Remove</button>" if tok else "") + "</div></form>")
    from . import selection
    pending = model_changes(pending_only=True)
    on = [p.label for k, p in pv.PROVIDERS.items() if pv.usable(p) and pv.enabled(p)]
    out = list(signed_out())
    hidden = "".join(f"<li><code class='path'>{e(h)}</code><div class='folderacts'><form method='post' action='/roots'>"
                     f"<input type='hidden' name='show' value='{e(h)}'><button class='quiet'>Show again</button></form></div></li>"
                     for h in reg['hidden'])
    pairing = codex_pairing_options(reg)
    card = lambda *parts: "".join(f"<div class='card'>{x}</div>" for x in parts if x)

    def section(key, title, summary, inner):
        """A part of Settings, folded to its name and where it stands; it opens where it was left open."""
        return (f"<details class='setsec' id='set-{key}'><summary><span class='sttl'>{title}</span>"
                f"<span class='ssum'>{summary}</span></summary>{inner}</details>")
    sections = [
        section("hours", "Your hours", e(f"{s['active_hours'] or 'off'} · safe pause "
                                           f"{str(s['safe_pause']) + '%' if s['safe_pause'] else 'off'}"), card(hours_fields)),
        section("programs", "Agent programs", e(", ".join(on) or "none on") + (
            " · <b class='warn'>ChatGPT pairing waits</b>" if "id='codex-pairing' open" in pairing else "") + (
            f" · <b class='warn'>{e(', '.join(pv.get(k).label for k in out))} signed out</b>" if out else ""),
            card(programs + pairing, signin)),
        section("sessions", "New sessions", e(f"{pv.get(s['provider']).label} · {dict(PERMISSION_LABELS).get(s['permissions'], s['permissions'])}"),
                card(session_fields)),
        section("models", "Models", e(f"{'New models switch in automatically' if s.get('model_adoption', 'automatic') == 'automatic' else 'New models: ask me'} "
                                     f"· {intelligence_label(s.get('auto_balance', 3))}"),
                card(balance) + global_role_fields() + model_changes()),
        section("consulting", "Consulting", "On: two fresh views at costly decisions" if s["consult"] else "Off", card(consulting)),
        section("steward", "Steward", ("Runs with the board" if s["monitor"] else "Off") + e(f" · {mm or 'its default'}"),
                card(steward_on, monitor_card)),
        section("connections", "Connections", e(connections_summary()),
                card("<p>Outside services your projects reach, and how: <a href='/connections'>Connections →</a></p>")),
        section("board", "This board", e(f"{len(urls(port))} address{'es' * (len(urls(port)) != 1)} · "
                                         f"{'open on your network' if s['lan'] else 'this machine only'}"),
                card(f"<ul class='folders'>{where}</ul><p class='muted'>Each project, and the steward, is also in the Claude "
                     f"app when Remote Control is on.</p>", board_fields)),
        section("projects", "Project folders", e(f"{len(reg['roots'])} folder{'s' * (len(reg['roots']) != 1)} · "
                                                 f"{len(reg['projects'])} added one by one"
                                                 + (f" · {len(reg['hidden'])} hidden" if reg['hidden'] else "")),
                card(f"<p class='muted'>Every subfolder of these is a project on the board.</p><ul class='folders'>"
                     f"{''.join(rows) or '<li class=muted>none</li>'}</ul><p><a href='/add?for=root'>+ Add a folder of projects</a></p>",
                     f"<h3>Added one by one</h3><ul class='folders'>{single or '<li class=muted>none</li>'}</ul>"
                     + ("<p class='muted'><a href='/projects#archived'>Archived and hidden projects: All projects → Archived</a></p>" if hidden else "")
                     + "<p><a href='/add'>+ Add a project folder</a></p><p class='muted'>Removing leaves every file where "
                       "it is; the project just leaves the board.</p>")),
        section("bench", "Benchmark data", "Artificial Analysis: " + ("connected" if st["connected"] else "not connected"), card(keybox)),
    ]
    body = (f"<header><h1>Settings</h1></header><form id='opts' method='post' action='/options'></form>{pending}"
            + "".join(sections) + SETTINGS_FOLDS)
    return shell(reg, -2, body)


# A project's own file opened from the board (a pin, a showcase entry) runs in a sandbox of its own: its scripts may run,
# so an interactive page works, but as no origin at all, so anything it posts back to the board is refused (Origin: null).
SANDBOX = "sandbox allow-scripts"


def inline(name):
    """A Content-Disposition for any file name: headers are Latin-1, so an ASCII fallback and the name in UTF-8."""
    plain = re.sub(r'[^ -~]|["\\]', "_", name)
    return f"inline; filename=\"{plain}\"; filename*=UTF-8''{urllib.parse.quote(name, safe='')}"


_names = {"at": 0, "names": set()}


def own_host(header):
    """Whether a request was addressed to this machine: by an address, or by one of its own names (localhost, its
    hostname, its name on Tailscale). A page elsewhere can point a name of its own at this machine (DNS rebinding) and
    read the board through it; that name is none of these, so it is refused. A request with no Host is a local tool's."""
    import ipaddress, socket
    from . import tailscale
    name = header.strip().lower()
    name = name[1:name.index("]")] if name.startswith("[") and "]" in name else name.rsplit(":", 1)[0] if name.count(":") == 1 else name
    if not name:
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if time.time() - _names["at"] > 60:                  # looked up again at most once a minute
        me = socket.gethostname().lower()
        ts = tailscale.status().get("name", "").lower() if shutil.which("tailscale") else ""
        _names.update(at=time.time(), names={"localhost", me, me + ".local", me.split(".")[0], ts, ts.split(".")[0]} - {""})
    return name.rstrip(".") in _names["names"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def parse_request(self):
        """With remote access on (colony remote), only this machine and the person's own devices on Tailscale are
        answered; the open home network and everything else are refused before anything is read."""
        if not super().parse_request():
            return False
        from . import tailscale
        if registry()["settings"].get("tailscale") and not tailscale.own_device(self.client_address[0]):
            self.send_error(403, "This board answers only this machine and your own devices on Tailscale")
            return False
        if not own_host(self.headers.get("Host", "")):
            self.send_error(403, "This board answers only at this machine's own addresses and names")
            return False
        return True

    def _page_send(self):
        """A page in a project sends back what the person did on it (a pick, a number). Pages run sandboxed, with no
        origin of their own, so the address the board gave that one page, signed for it, is its only way back; what
        it sends reaches the agent as a note from that page, never as the person's own words."""
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        if not getattr(self.server, "lan", False) and host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            return self._send(403, b"not from this board")
        if self.headers.get("Origin") not in (None, "null", f"http://{host}"):
            return self._send(403, b"not from a page of this board")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        key, rel, sig = (q.get("p") or [""])[0], (q.get("f") or [""])[0], (q.get("sig") or [""])[0]
        try:
            root = projects(registry())[int(key)]
        except (ValueError, IndexError):
            return self._send(404, b"No such project")
        path = pins.inside(root, rel)
        if not path or not secrets.compare_digest(sig, page_sig(root, path)):
            return self._send(403, b"not this page's address")
        text = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 16 * 1024)).decode("utf-8", "replace").strip()
        if not text:
            return self._send(400, b"nothing sent")
        name = str(path.relative_to(Path(root).resolve())) if path.is_relative_to(Path(root).resolve()) else path.name
        add_note(root, {"page": name}, f"From your page {name}, what the person did on it:\n{text}", author="page")
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

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
        self._uploads = []                               # every file, where a form sends several
        for part in msg.iter_parts() if msg.is_multipart() else []:
            name = part.get_param("name", header="content-disposition")
            if part.get_filename():
                upload = (part.get_filename(), part.get_payload(decode=True) or b"")
                if upload[1]:
                    self._uploads.append(upload)
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
        if self.headers.get("Sec-Fetch-Mode") == "navigate" and self.headers.get("Sec-Fetch-Dest") == "document":
            hours.seen()                               # the person opened a page (a page keeping itself current isn't them)
        url = urllib.parse.urlparse(self.path)
        if url.path == "/monitor":                     # the steward's page was once /monitor: old links and bookmarks still work
            url = url._replace(path="/steward")
        q = urllib.parse.parse_qs(url.query)
        reg = registry()
        plist = projects(reg)
        try:                                           # the steward's chat asks as p=steward: its routes read it themselves
            pid = min(max(int((q.get("p") or ["0"])[0]), 0), max(len(plist) - 1, 0))
        except ValueError:
            pid = 0
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
        if url.path == "/" and "p" not in q:          # the front page: every project and what each needs of the person
            return self._send(200, home_page(reg).encode())
        if url.path == "/":
            view = (q.get("view") or ["overview"])[0]           # switching to a project starts on its Overview
            return self._send(200, render(reg, pid, view, (q.get("all") or [""])[0]).encode())
        if url.path == "/add":
            if (q.get("for") or ["project"])[0] == "project":
                return self._send(200, add_project_page(reg, (q.get("tab") or ["new"])[0], (q.get("error") or [""])[0]).encode())
            return self._send(200, folder_browser(reg, (q.get("dir") or [""])[0], (q.get("for") or ["project"])[0]).encode())
        if url.path == "/add/browse":
            return self._send(200, machine_folders((q.get("dir") or [""])[0]).encode())
        if url.path == "/models":
            return self._send(200, models_page(reg).encode())
        if url.path == "/connections":
            return self._send(200, connections_page(reg).encode())
        if url.path == "/settings":
            settings_page.port = self.server.server_address[1]
            return self._send(200, settings_page(reg).encode())
        if url.path == "/pins/add":
            return self._send(200, pin_add_page(reg, pid).encode())
        if url.path == "/pins/browse":
            return self._send(200, pin_browse(plist[pid], (q.get("dir") or [""])[0]).encode())
        if url.path == "/showcase/open":
            x = showcase.get(plist[pid], (q.get("id") or [""])[0])
            if not x or not x.get("link"):
                return self._send(404, b"no such showcase entry")
            if showcase.is_url(x):
                target = x["link"]
                host = self.headers.get("Host", "").split(":")[0]
                parts = urllib.parse.urlsplit(target)
                if parts.hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1") and host not in ("", "localhost", "127.0.0.1"):
                    target = urllib.parse.urlunsplit(parts._replace(netloc=host + (f":{parts.port}" if parts.port else "")))
                self.send_response(303)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            path = pins.inside(plist[pid], x["link"])
            if not path or not path.is_file():
                return self._send(404, b"the file is gone")
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", showcase.mime(x))
            self.send_header("Content-Disposition", inline(path.name))
            self.send_header("Content-Security-Policy", SANDBOX)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
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
            self.send_header("Content-Disposition", inline(path.name))
            self.send_header("Content-Security-Policy", SANDBOX)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if url.path == "/voice":                      # a few sentences in Kokoro's voice, made here (or kept)
            from . import voice
            clip = voice.speak((q.get("t") or [""])[0], (q.get("v") or ["bm_fable"])[0])
            ask = self.headers.get("Range", "")
            voice.heard(ask + (" auto" if q.get("auto") else ""), self.headers.get("User-Agent", ""), bool(clip))
            if not clip:
                return self._send(404, b"no voice here")
            body = clip.read_bytes()
            # Safari plays audio only as byte ranges, each answered with just the part it asked for
            got = re.match(r"bytes=(\d*)-(\d*)$", ask.strip())
            start, end = 0, len(body) - 1
            if got and (got.group(1) or got.group(2)):
                if got.group(1):
                    start, end = int(got.group(1)), min(int(got.group(2) or end), end)
                else:
                    start = max(0, len(body) - int(got.group(2)))
            self.send_response(206 if got else 200)
            self.send_header("Content-Type", "audio/mpeg" if clip.suffix == ".mp3" else "audio/wav")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "private, max-age=86400")
            if got:
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            return self.wfile.write(body[start:end + 1])
        if url.path == "/chat/file":                  # a picture or a file the chat shows, from inside the project only
            from . import monitor
            import mimetypes
            key = (q.get("p") or ["0"])[0]
            try:
                root = monitor.home() if key == "steward" else plist[int(key)]
            except (ValueError, IndexError):
                return self._send(404, b"No such console")
            path = pins.inside(root, (q.get("f") or [""])[0])
            if not path or not path.is_file():
                return self._send(404, b"the file is gone")
            data = path.read_bytes()
            if key != "steward" and path.suffix.lower() in (".html", ".htm"):
                data = page_with_send(key, root, path, data)
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Disposition", inline(path.name))
            if path.suffix.lower() != ".pdf":         # a phone's PDF viewer is a plugin, which a sandbox refuses: a blank page
                self.send_header("Content-Security-Policy", SANDBOX)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "private, max-age=300")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if url.path == "/chat/voice":                 # the voice a chat reads in, for the player: picked, pinned, the choices
            from . import monitor, voice
            key = (q.get("p") or ["0"])[0]
            try:
                root = monitor.home() if key == "steward" else plist[int(key)]
            except (ValueError, IndexError):
                return self._send(404, b"No such console")
            name, pinned = voice_of(key, root)
            ready = voice.ready()
            return self._send(200, json.dumps({"voice": name, "pinned": pinned, "ready": ready,
                                               "options": voice.options(name) if ready else ""}).encode())
        if url.path == "/chat/feed":                  # the chat's conversation, as it stands now
            from . import monitor
            key = (q.get("p") or ["0"])[0]
            try:
                root = monitor.home() if key == "steward" else plist[int(key)]
            except (ValueError, IndexError):
                return self._send(404, b"No such console")
            body = chat_feed(key, root).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
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
        if url.path == "/projects":
            return self._send(200, all_projects_page(reg).encode())
        if url.path == "/nest":
            return self._send(200, nest_page(reg, (q.get("n") or [""])[0]).encode())
        if url.path == "/static/board.css":
            data = CSS.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")   # its address changes with it
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if url.path == "/home/feed":
            return self._send(200, home_list(reg).encode())
        if url.path == "/needs":
            if "p" in q:                                   # one project's Waiting on you
                return self._send(200, waiting_html(pid, plist[pid]).encode())
            return self._send(200, needs_you(reg).encode())
        if url.path == "/steward":
            return self._send(200, monitor_page(reg, (q.get("view") or ["overview"])[0]).encode())
        if url.path == "/status":
            from . import monitor
            body = json.dumps({"board": str(home()), "projects": [
                dict(console.snapshot(p), waiting=len(moments(p)), work=at_work(p), followed=monitor.involved(p)) if p.exists()
                else {"state": "off", "lines": [], "waiting": 0, "work": "", "followed": False} for p in plist], "monitor": monitor.snapshot(),
                "nav": nav_order(plist)}).encode()
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
        if urllib.parse.urlparse(self.path).path == "/page/send":
            return self._page_send()
        if not self._from_this_page():
            return self._send(403, b"not from this page")
        if urllib.parse.urlparse(self.path).path not in ("/codex-pair/status",):
            hours.seen()                               # the person tapped, typed or sent something
        if int(self.headers.get("Content-Length") or 0) > pins.UPLOAD_LIMIT + 64 * 1024:
            return self._send(413, b"that file is too large (25 MB at most)")
        if urllib.parse.urlparse(self.path).path == "/pin/upload":
            return self._upload()
        body_text = ""
        self._uploads = []
        if self.headers.get("Content-Type", "").startswith("multipart/form-data"):
            form, upload = self._multipart()       # Message, with a file or a long paste
            values = {k: [v] for k, v in form.items()}
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
        if path == "/connections":
            from . import connections
            name, do = form.get("name", ""), form.get("do")
            try:
                root = projects(reg)[int(form["p"])] if form.get("p") else None
            except (ValueError, IndexError):
                return self._send(404, b"No such project.")
            try:
                if do == "path":
                    if form.get("how") in ("colony", "claude", "chatgpt", "off") or (root and form.get("how") == "inherit"):
                        connections.choose_path(form.get("svc", ""), root, form["how"])
                elif do == "own" and root:
                    connections.configure(name, root, own=bool(form.get("own")))
                elif do == "keys":
                    c = connections.load()["connections"][name]
                    whose = root if root and connections.has_own(c, root) else None
                    for k in c.get("keys") or ():
                        if form.get(f"key_{k}", "").strip():
                            connections.set_key(name, k, form[f"key_{k}"], whose)
                elif do == "approve":
                    try:
                        connections.approve(name)
                    except ValueError as err:
                        return self._send(409, shell(reg, -2, f"<h1>Connections</h1><p>{e(str(err))}</p>"))
                elif do == "remove":
                    connections.remove(name)
                elif do == "accept":
                    connections.accept_tools(name)
                elif do == "holds":
                    connections.configure(name, root, hold=None if form.get("mode") != "chosen" else sorted(values.get("tool", [])))
            except KeyError:
                return self._send(404, b"No such connection.")
            back = form.get("back", "/connections")
            self.send_response(303)
            self.send_header("Location", back if back.startswith("/") and not back.startswith("//") else "/connections")
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
            if form.get("active_hours", "").strip():
                try:
                    hours.parse(form["active_hours"])           # checked before anything is saved
                except ValueError as err:
                    return self._send(400, str(err).encode())
            if 'auto_balance' in form:
                try:
                    set_setting('auto_balance', form['auto_balance'])
                except KeyError:
                    return self._send(400, b'Auto balance must be from 0 to 6')
            from . import providers as pv
            set_setting("remote_by", ",".join(f"{k}={form.get('remote_' + k, 'off')}" for k, p in pv.PROVIDERS.items() if pv.usable(p)))
            turbo = dict(reg["settings"]["turbo_by"], **{k: form.get("turbo_" + k) == "on" for k, p in pv.PROVIDERS.items() if pv.usable(p)})
            set_setting("turbo_by", ",".join(f"{k}={'on' if v else 'off'}" for k, v in turbo.items()))   # one not shown keeps its own
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
            if form.get("active_hours", "").strip():
                set_setting("active_hours", form["active_hours"])
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
                    return self._send(409, held("the steward", "/steward").encode())
                where = "/steward?view=console"
            else:
                from . import mail
                src = projects(reg)[int(form.get("p", "0"))]
                dst = src if form.get("to", "self") == "self" else projects(reg)[int(form["to"])]
                files = [str(f) for f in [pins.inside(src, form["path"]) if form.get("path") else None] if f and f.is_file()]
                if upload and upload[1]:                   # kept where the project it's for can read it
                    files.append(str(dst / pins.save_upload(dst, *upload)))
                if files:
                    text = "\n".join(f"Attached: {f}" for f in files) + (f"\n\n{text}" if text else "")
                sent = not text or (console.paste_into(console.ensure(src, settle=SETTLE), text) if dst == src
                                    else console.type_into(console.ensure(src, settle=SETTLE), mail.instruction(mail.address(dst), text)))
                if not sent:
                    return self._send(409, held(src.name, f"/?p={form.get('p', '0')}", src).encode())
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
        from . import connections
        if path == "/clone":
            chosen = {k: form.get(k, "") for k in PROJECT_KEYS if form.get(k)}
            try:
                dest = clone(form.get("url", ""), form.get("within") or reg["new_root"], form.get("name", ""))
            except (ValueError, subprocess.TimeoutExpired) as err:
                target = "/add?tab=github&error=" + urllib.parse.quote(str(err))
            else:
                if chosen:
                    project_settings(dest, chosen)
                connections.pick(dest, values.get("svc", []), values.get("svc_listed", []))
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
                    connections.pick(folder, values.get("svc", []), values.get("svc_listed", []))
                    track(folder)
                    target = f"/?p={projects().index(folder)}"
            elif path == "/new" and folder_name(form.get("name", "")):
                new = Path(form.get("within") or reg["new_root"]).expanduser() / folder_name(form["name"])
                new.mkdir(parents=True, exist_ok=True)
                if chosen:
                    project_settings(new, chosen)
                connections.pick(new, values.get("svc", []), values.get("svc_listed", []))
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
        if path == "/rule":
            from . import rules
            try:
                root = projects(reg)[int(form.get("p", ""))]
                rules.retire(root, form.get("id", ""), "the person retired it on the board", by="person")
                add_note(root, None, f"The person retired a standing rule on the board ({form.get('id', '')}); it no longer holds.",
                         author="colony", quiet=True)
            except (ValueError, IndexError, KeyError):
                pass
            back = f"/?p={form.get('p', '')}&view=overview"
            self.send_response(303)
            self.send_header("Location", back)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/lesson":
            from . import lessons
            try:
                lessons.decide(form.get("id", ""), form.get("verdict", ""))
            except (StopIteration, ValueError):
                pass                                   # decided already, or not a choice it offers
            self.send_response(303)
            self.send_header("Location", "/steward")
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
            self.send_header("Location", "/steward")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/direction":
            from . import monitor
            monitor.set_direction(None if form.get("reset") else form.get("text", ""))
            self.send_response(303)
            self.send_header("Location", "/steward?view=direction")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/posture":
            from . import monitor
            root = projects(reg)[int(form.get("p", "0"))]
            monitor.set_posture(root, helm=form.get("helm") == "on", direction=form.get("direction", ""), scout=form.get("scout"),
                                scout_note=form.get("scout_note", ""), scouting=form.get("scouting") == "on")
            self.send_response(303)
            self.send_header("Location", "/steward?view=helm")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/chat/voice":                        # a voice chosen, or pinned to this project, in Chat
            from . import monitor
            key = values.get("p", ["0"])[0]
            try:
                root = monitor.home() if key == "steward" else projects(registry())[int(key)]
                pin = {"1": True, "0": False}.get(values.get("pin", [""])[0])
                name, pinned = choose_voice(key, root, values.get("voice", [None])[0] or None, pin)
            except (ValueError, IndexError):
                return self._send(400, b"no such voice or project")
            return self._send(200, json.dumps({"voice": name, "pinned": pinned}).encode())
        if path == "/chat/measure":                      # where the box landed on the person's phone, typing
            line = f"{now()} {values.get('m', [''])[0][:400]} {self.headers.get('User-Agent', '')[:60]}\n"
            with open(home() / "chat-measure.log", "a") as fh:
                fh.write(line)
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/chat/queue":                        # what waits in its queue: sent now, taken back to edit, put back
            from . import monitor
            key = values.get("p", ["0"])[0]
            try:
                name = monitor.name() if key == "steward" else console.session_name(projects(registry())[int(key)])
                msgs = json.loads(values.get("msgs", ["[]"])[0]) if values.get("action", [""])[0] == "put" else []
            except (ValueError, IndexError):
                return self._send(400, b"no such console")
            ok = console.queue(name, values.get("action", [""])[0], [str(m) for m in msgs])
            self.send_response(204 if ok else 409)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/chat/stop":                         # at work, ■ in Chat: Esc in its console, as the person would press it
            from . import monitor
            key = values.get("p", ["0"])[0]
            console.press(monitor.name() if key == "steward" else console.session_name(projects(registry())[int(key)]), ["Escape"])
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/chat/send":                         # what the person sends in Chat: into the console, as Message does
            from . import monitor
            key, text = values.get("p", ["0"])[0], values.get("text", [""])[0].strip()
            dst = monitor.home() if key == "steward" else projects(registry())[int(key)]
            if self._uploads:                              # kept where the one it's for can read them, their paths in the message
                try:
                    kept = [f"Attached: {Path(dst) / pins.save_upload(dst, *u)}" for u in self._uploads]
                except ValueError:
                    return self._send(413, b"that file is too large (25 MB at most)")
                text = "\n".join(kept) + (f"\n\n{text}" if text else "")
            if key == "steward":
                sent = not text or console.paste_into(monitor.ensure(), text)
            else:
                src = projects(registry())[int(key)]
                sent = not text or console.paste_into(console.ensure(src, settle=SETTLE), text)
                if sent and text:
                    answer_asks(src, "in the console")
                    append(src, "messages.jsonl", {"type": "message", "at": now(), "text": text, "to": "console"})
                if not sent and not console.live(src):        # not a draft in the way: its agent isn't there
                    return self._send(503, stopped(src).encode())
            self.send_response(204 if sent else 409)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in ("/nest/archive", "/bring-back"):
            from . import nest
            name = form.get("name") or form.get("nest") or ""
            members = [m for m in nest.nests().get(name, {}).get("members", [])] if name else []
            if path == "/nest/archive":
                archive_together(members, why=form.get("why", ""))
            else:
                bring_back(members if name else [form.get("path", "")])
            self.send_response(303)
            self.send_header("Location", f"/nest?n={urllib.parse.quote(name)}" if name else "/projects")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/nests/order":                       # the person moved the groups: their order, everywhere
            reg = registry()
            reg["settings"]["nest_order"] = list(dict.fromkeys(values.get("n", [])))
            save_registry(reg)
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/project/color":                    # a colour tapped: the project's at once
            try:
                project_settings(projects(registry())[int(values.get("p", ["0"])[0])], {"color": values.get("color", [""])[0]})
            except (KeyError, IndexError, ValueError):
                return self._send(400, b"no such colour or project")
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/project/pin":                      # pinned above every group, or back to its own
            reg = registry()
            root = str(projects(reg)[int(values.get("p", ["0"])[0])].resolve())
            kept = [r for r in reg["settings"].get("pinned_projects") or [] if r != root]
            reg["settings"]["pinned_projects"] = kept + ([root] if values.get("on", ["1"])[0] == "1" else [])
            save_registry(reg)
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/steward/involved":                 # which projects the steward follows: the checked ones
            from . import monitor
            chosen = set(values.get("p", []))
            for i, p in enumerate(projects(reg)):
                if p.exists() and monitor.involved(p) != (str(i) in chosen):
                    monitor.set_posture(p, steward=str(i) in chosen)
            self.send_response(303)
            self.send_header("Location", "/steward")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/mute":
            from . import monitor
            try:
                monitor.muted(form.get("state") == "on")
            except ValueError as err:
                return self._send(409, shell(reg, -1, f"<p>{e(str(err))}</p><p><a href='/steward'>Back</a></p>").encode())
            self.send_response(303)
            self.send_header("Location", "/steward")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/helm":
            from . import monitor
            monitor.helm(form.get("state") == "on")
            self.send_response(303)
            self.send_header("Location", "/steward")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        pid = int(form.get("p", "0"))
        if pid == -1 and path == "/console/stop":
            from . import monitor
            subprocess.run(["tmux", "kill-session", "-t", monitor.name()], capture_output=True)
            self.send_response(303)
            self.send_header("Location", "/steward")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        root = projects(reg)[pid]
        text = form.get("text", "").strip()
        path = urllib.parse.urlparse(self.path).path
        back = form.get("back", "")
        back = back if back.startswith("/") and not back.startswith("//") else f"/?p={pid}"
        if path == "/answers/draft":                     # what's typed in the box for answering by number, as typed
            save_draft(root, form.get("text", "").replace("\r\n", "\n"), numbering(form.get("n")))
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/answers":
            typed, numbers = form.get("text", "").replace("\r\n", "\n"), numbering(form.get("n"))
            try:
                answer_by_number(root, typed, numbers)
            except ValueError as err:
                save_draft(root, typed, numbers)          # kept in the box, to put right
                return self._send(400, shell(reg, pid, f"<h1>Answers not sent</h1><p>{e(err)}</p><p class='muted'>Nothing "
                                             f"was recorded; what you wrote is still in the box.</p>"
                                             f"<p><a href='{e(back)}'>Back to it</a></p>").encode())
        elif path in ('/lead/switch', '/item/owner', '/progress/decision', '/progress/control'):
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
        elif path == "/answer" and form.get("reply") and (text or self._uploads):
            # typed in a decision's own box: an answer or a question, the agent judges which; it stays open meanwhile
            g = next((g for g in gates(root) if g["id"] == form.get("gate")), None)
            if not g:
                return self._send(404, b"No such gate in this project.")
            if self._uploads:                              # what they attached, kept in the project, its paths in the reply
                try:
                    text = "\n".join(f"Attached: {Path(root) / pins.save_upload(root, *u)}" for u in self._uploads) + (f"\n\n{text}" if text else "")
                except ValueError:
                    return self._send(413, b"that file is too large (25 MB at most)")
            add_note(root, {"gate": g["id"], "item": g.get("item"), "asked": text},
                     f"The person replied on \"{g['question']}\" (still open): {text}\n"
                     f"If this answers it, record it: colony gate --answered {g['id']} \"their words\" --context \"your reading\". "
                     f"If it asks something or leaves it unclear, answer them, then ask the decision again with what it was "
                     f"missing: colony gate \"the question\" --reframes {g['id']} --told \"your answer to them\" (with --why, "
                     f"--option, --recommended, --default as fit).", author="person")
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
        elif path in ("/project/archive", "/project/close", "/nest/join", "/nest/leave"):
            from . import nest
            if path == "/project/close":
                try:
                    close_project(root)
                except ValueError:
                    pass
            elif path == "/project/archive":
                archive_together([root], why=form.get("why", ""))
            elif path == "/nest/join" and form.get("name", "").strip():
                nest.join(root, form["name"])
            elif path == "/nest/leave":
                nest.leave(root)
            back = form.get("back", "")
            if back.startswith("/nest?n=") and urllib.parse.unquote(back[8:]) not in nest.nests():
                back = "/"                                   # its last project taken out, and nothing else kept it
            self.send_response(303)
            self.send_header("Location", back if back.startswith("/nest?n=") else f"/?p={pid}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif path in ("/project/remove", "/project/delete"):
            (remove_project if path == "/project/remove" else delete_project)(root)
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif path == "/pin/group" and form.get("id"):
            pin = pins.get(root, form["id"])
            if pin and (pin.get("group") or "") != form.get("group", "").strip():
                pins.regroup(root, pin["id"], form.get("group", ""))
                add_note(root, {"pin": pin["id"]}, f"The person moved {pins.describe(pin)} in the Library from "
                         f"'{pin.get('group') or 'no heading'}' to '{form.get('group', '').strip() or 'no heading'}'.",
                         author='person', quiet=True)
        elif path == "/pin/rename" and form.get("old") is not None and form.get("new", "").strip():
            moved = [x for x in pins.pins(root) if (x.get("group") or "") == form["old"]]
            for x in moved:
                pins.regroup(root, x["id"], form["new"])
            if moved:
                add_note(root, None, f"The person renamed the Library heading '{form['old'] or 'no heading'}' to "
                         f"'{form['new'].strip()}'.", author='person', quiet=True)
        elif path == "/pin" and form.get("target", "").strip():
            try:
                pin = pins.add(root, form["target"].strip(), form.get("title", ""), kind=form.get("kind"), group=form.get("group", ""))
                tell_pinned(root, pin, form.get("comment", "").strip())
            except FileNotFoundError:
                pass
        elif path == "/unpin" and form.get("id"):
            pin = pins.get(root, form["id"])
            if pin:
                pins.archive(root, pin["id"], form.get("why", ""))
                add_note(root, {"pin": pin["id"]}, f"The person archived {pins.describe(pin)} from the Library"
                         + (f": {form['why'].strip()}" if form.get("why", "").strip() else "") + ".", author='colony', quiet=True)
        elif path == "/pin/restore" and form.get("id"):
            pin = pins.get(root, form["id"])
            if pin:
                pins.restore(root, pin["id"])
                add_note(root, {"pin": pin["id"]}, f"The person brought {pins.describe(pin)} back into the Library.",
                         author='colony', quiet=True)
        elif path == "/showcase/rate" and form.get("id") and form.get("stars", "").isdigit():
            showcase.rate(root, form["id"], int(form["stars"]))
        elif path == "/showcase/comment" and text and form.get("id"):
            showcase.comment(root, form["id"], text)
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
        self.send_response(303)
        self.send_header("Location", back)
        self.send_header("Content-Length", "0")
        self.end_headers()


MONITOR = True


def serve(port, lan=False, monitor=True):
    global MONITOR
    MONITOR = monitor
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
nav .pinjump { display:none }
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
/* one project in a list, the whole row a link: state, name, what it is at, what waits; under it, what it is for */
.card.plist { padding:8px } a.prow, a.prow:visited { display:block; margin:4px 0 0; padding:9px 10px; background:var(--sunk);
  border-radius:7px; color:var(--ink); text-decoration:none } .plist > .prow:first-child { margin-top:0 }
.prowline { display:flex; align-items:center; gap:8px; min-width:0 } .prowline b { flex:none; max-width:62%; color:var(--accent);
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap } .prowline .sdot { flex:none }
span.nestgroup, span.nestgroup > .members { display:contents }
/* a nest in the menu is one tinted capsule: its chip, and, open, its projects inside on slimmer chips */
nav span.nestgroup { display:block; margin:3px 0; padding:3px; border-radius:10px; background:color-mix(in srgb, var(--accent) 15%, var(--card));
  border:1px solid color-mix(in srgb, var(--accent) 28%, var(--card)) } nav span.nestgroup .members .proj { padding-top:3px; padding-bottom:3px }
nav span.nestgroup .members .proj:not(.on) { background:var(--card) } .nestgroup[data-open='0'] > .members { display:none }
.listhead { display:flex; align-items:center; gap:8px; cursor:pointer; user-select:none; min-height:44px; margin:6px 0 2px }
.listhead .nestname { flex:none; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap } .listhead .headgap { flex:1 }
.listhead .nestgo { margin-left:-6px; line-height:1; padding-bottom:3px } .grip { display:inline-flex; align-items:center; justify-content:center; width:32px; height:32px;
  touch-action:none; cursor:grab; color:var(--muted); font-size:20px; flex:none } .nestgroup.dragging { opacity:.6 } .foldbtn { display:inline-flex; align-items:center; justify-content:center; width:28px; height:28px;
  border-radius:6px; background:var(--sunk); color:var(--ink); font-size:16px; flex:none }
.foldbtn::before { content:'▾' } .nestgroup[data-open='0'] .foldbtn::before { content:'▸' }
.nestgo { display:inline-flex; align-items:center; justify-content:center; min-width:44px; min-height:44px; font-size:20px;
  text-decoration:none; color:var(--accent) }
/* a group in a list is one tinted panel, its heading at the top and its projects inside on lighter, slimmer rows */
div.nestgroup { margin:6px 0 10px; padding:2px 6px 6px; border-radius:10px; background:color-mix(in srgb, var(--accent) 15%, var(--card));
  border:1px solid color-mix(in srgb, var(--accent) 28%, var(--card)) } div.nestgroup:first-child { margin-top:0 }
div.nestgroup[data-open='0'] { padding-bottom:2px } div.nestgroup > .listhead { margin:0; color:var(--ink) }
div.nestgroup > .listhead .foldbtn { background:var(--card) }
div.nestgroup .members a.prow { display:flex; flex-direction:column; justify-content:center; min-height:36px; padding:4px 10px;
  background:var(--card); margin-top:4px } div.nestgroup .members .psub { margin-top:1px }
.nestpick { display:flex; flex-wrap:wrap; gap:6px; margin:4px 0 10px }
/* Chat, as the app shows a conversation: the person's messages in bubbles at the right, the agent's prose in full, each
   run of work folded to a line, colony's notices small; a rounded box at the bottom to send */
.chat { display:flex; flex-direction:column; gap:12px; max-width:760px; margin:8px auto 0; padding-bottom:12px; font-size:16px; line-height:1.55 }
.chat .msg.you { align-self:flex-end; max-width:86%; padding:10px 14px; border-radius:20px; background:var(--sunk);
  border:1px solid var(--line); white-space:pre-wrap; overflow-wrap:anywhere }
.chat .msg.colony { align-self:center; max-width:92%; padding:3px 12px; border-radius:999px; background:var(--sunk);
  color:var(--muted); font-size:12.5px } .chat details.msg.colony[open] { border-radius:12px }
.chat details.msg.colony > div { white-space:pre-wrap; padding:4px 0 6px; overflow-wrap:anywhere }
.swatches { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin:6px 0 } .swatches > span { margin-right:4px }
.swatches label { display:inline-flex; margin:0; cursor:pointer } .swatches input { position:absolute; opacity:0; pointer-events:none }
.swatches label span { width:30px; height:30px; border-radius:50%; background:var(--sw); border:2px solid var(--card); box-shadow:0 0 0 1px var(--line) }
.swatches input:checked + span { box-shadow:0 0 0 2px var(--ink) }
@media (prefers-color-scheme: dark) { .swatches label span { background:var(--swd) } }
nav .proj[style]:not(.on) { background:color-mix(in srgb, var(--pc) 13%, var(--sunk)) }
/* off any project (the steward, the front page), no project's chip is tinted: none reads as the one you're in */
nav.noproj .proj[style]:not(.on) { background:var(--sunk) }
@media (prefers-color-scheme: dark) { nav .proj[style]:not(.on) { background:color-mix(in srgb, var(--pcd) 16%, var(--sunk)) } }
nav .proj[style].on { background:var(--pc) } @media (prefers-color-scheme: dark) { nav .proj[style].on { background:var(--pcd) } }
.renamehead { margin:0 0 4px } .renamehead > summary { font-size:12px; color:var(--muted); cursor:pointer }
.pinmenu .regroup input { flex:1; min-width:0 } .pinmenu .regroup { display:flex; gap:6px }
.libsearch input { width:100%; font-size:16px; padding:8px 12px; border:1px solid var(--line); border-radius:12px; background:var(--card); color:var(--ink); margin:6px 0 10px }
.libgroup { border-bottom:1px solid var(--line); padding:4px 0 } .libgroup > summary { cursor:pointer; padding:8px 0; font-weight:600 }
.libgroup.d2 { margin-left:12px } .libgroup.d3 { margin-left:24px } .libitem { padding:6px 0 6px 4px }
.libitem code { font-size:12px; word-break:break-all }
/* what's been done, as medals: a row that scrolls sideways on a phone */
.jmedals-head { display:flex; gap:10px; align-items:baseline; margin:4px 0 6px; font-size:13px; font-weight:600;
  text-transform:uppercase; letter-spacing:.05em; color:var(--muted) }
.jmedals { display:flex; gap:10px; overflow-x:auto; padding:4px 2px 12px; scroll-snap-type:x proximity }
a.jmedal { flex:none; width:118px; scroll-snap-align:start; display:flex; flex-direction:column; align-items:center; gap:3px;
  padding:12px 8px 10px; border-radius:14px; text-decoration:none; color:var(--ink); text-align:center; font-size:12px;
  background:linear-gradient(160deg, color-mix(in srgb, var(--accent) 16%, var(--card)), var(--card));
  border:1px solid color-mix(in srgb, var(--accent) 35%, var(--line)) }
.jseal { width:40px; height:40px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:18px;
  font-weight:700; color:var(--card); background:radial-gradient(circle at 35% 30%, color-mix(in srgb, var(--accent) 70%, #fff), var(--accent));
  box-shadow:0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent); margin-bottom:3px }
.jmname { line-height:1.25; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; min-height:2.5em }
/* the roadmap as a journey: stops down a line, each a ring of its progress and a dot per item */
.journey { position:relative; margin:6px 0 18px; padding-left:4px }
.journey::before { content:''; position:absolute; left:27px; top:18px; bottom:18px; width:2px; background:var(--line) }
.jdone, .jdest { position:relative; display:flex; gap:12px; align-items:center; padding:8px 0; font-size:14px }
.jtick, .jflag { flex:none; width:48px; height:32px; display:flex; align-items:center; justify-content:center; z-index:1 }
.jtick { color:var(--accent); font-weight:700 } .jflag { font-size:20px; color:var(--accent) }
.jtick::before, .jflag::before { content:''; position:absolute; left:14px; width:28px; height:28px; border-radius:50%; background:var(--bg); z-index:-1 }
.jdest span:last-child { font-weight:600 }
a.jstop { position:relative; display:flex; gap:12px; align-items:flex-start; padding:10px 8px 10px 0; color:var(--ink); text-decoration:none;
  border-radius:12px }
a.jstop:hover { background:var(--sunk) } a.jstop.later { opacity:.65 }
.jring { flex:none; z-index:1; width:48px; height:48px; border-radius:50%; display:flex; align-items:center; justify-content:center;
  background:conic-gradient(var(--accent) calc(var(--p) * 1%), var(--line) 0); position:relative; font-size:13px }
.jring::after { content:''; position:absolute; inset:5px; border-radius:50%; background:var(--bg) }
.jring b, .jring small { position:relative; z-index:1 } .jring small { color:var(--muted); font-size:10px }
a.jstop.live .jring { box-shadow:0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent) }
.jbody { display:flex; flex-direction:column; gap:5px; min-width:0; padding-top:2px }
.jtitle { font-size:15px; line-height:1.3 } .jkind { margin-left:6px; color:var(--muted); font-size:12px }
.jdots, .jlane { display:flex; flex-wrap:wrap; gap:4px; align-items:center }
.jlname { font-size:11px; color:var(--muted); min-width:56px; margin-right:4px }
.jd { width:9px; height:9px; border-radius:50%; border:1.5px solid var(--muted); display:inline-block }
.jd.done { background:var(--accent); border-color:var(--accent) } .jd.verify { border-color:var(--flag); background:var(--flag-bg) }
.jd.doing { border-color:var(--accent); background:color-mix(in srgb, var(--accent) 35%, transparent); animation:beat 1.6s ease-in-out infinite }
.jnow { font-size:13px; color:var(--muted) }
.hproj { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:6px 12px; margin:10px 0 }
.hproj.quiet { padding:2px 12px; margin:6px 0 } .hproj .measures { border:0; margin:0; padding:0 0 4px }
.hneeds { border-top:1px solid var(--line); margin-top:4px } .hneeds .need:first-child { border-top:0 }
.steer { display:flex; gap:8px; margin:4px 0 8px } .steer form { margin:0 }
.measures .measure { padding:6px 0; border-bottom:1px solid var(--line) } .measures .measure:last-child { border-bottom:0 }
.measures .mname { margin-right:6px } .measures .reached { color:var(--accent); font-weight:600 } .measures .spark { letter-spacing:1px; color:var(--accent) } .flag { color:var(--flag) }
.small { font-size:13px }
.lanes { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:10px; margin:8px 0 }
.lane { border:1px solid var(--line); border-radius:12px; padding:8px 10px } .lane p { margin:4px 0 }
.lanebar { height:5px; border-radius:3px; background:var(--line); margin:5px 0; overflow:hidden } .lanebar i { display:block; height:100%; background:var(--accent) }
.kindnote { margin:12px 0 2px; font-size:12px; text-transform:uppercase; letter-spacing:.05em }
details[id^='ms-'] { scroll-margin-top:calc(var(--navh, 0px) + 12px) }
details.item > summary .jd { vertical-align:middle; margin-right:4px } .ititle { font-weight:500 } .iid { font-size:12px }
.donefold > summary { cursor:pointer } .pieces { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0 }
.piece { font-size:12px; border:1px solid var(--line); border-radius:6px; padding:2px 6px } .piece.done { background:var(--line) }
.piece.doing { border-color:var(--accent) }
.mdtable { overflow-x:auto; margin:10px 0 } .mdtable table { border-collapse:collapse; font-size:14px; line-height:1.4 }
.mdtable th, .mdtable td { border-bottom:1px solid var(--line); padding:6px 10px 6px 0; text-align:left; vertical-align:top }
.mdtable th { font-weight:600; border-bottom-width:2px } .mdtable td code { white-space:nowrap }
blockquote { margin:10px 0; padding:2px 0 2px 12px; border-left:3px solid var(--line); color:var(--muted) }
.chat details.needs { border:1px solid var(--flag); border-radius:14px; background:var(--flag-bg); padding:8px 12px }
.chat details.needs > summary { display:flex; align-items:center; gap:8px; cursor:pointer; font-weight:600; color:var(--flag); list-style:none }
.chat details.needs > summary::-webkit-details-marker { display:none }
.chat details.need { margin:8px 0 0; padding:8px 10px; border-radius:10px; background:var(--card) }
.chat details.need > summary { cursor:pointer; list-style:none } .chat details.need > summary::-webkit-details-marker { display:none } .chat details.need .fold, .chat details.need[open] .q { display:none }
.chat details.need[open] .fold { display:inline }
.chat details.need textarea { width:100%; min-height:64px; margin:6px 0; font:inherit; padding:8px; border-radius:10px;
  border:1px solid var(--line); background:var(--bg); color:var(--ink) } .chat details.need form { margin:6px 0 0 }
.chat details.need button { min-height:36px; padding:4px 14px; border-radius:999px; border:1px solid var(--line); background:var(--sunk); color:var(--ink) }
.chat .qbar { align-self:flex-end; display:flex; gap:6px; margin-top:-6px } .chat .qbar button { min-height:30px; padding:2px 12px;
  border-radius:999px; border:1px solid var(--line); background:var(--card); color:var(--accent); font-size:13px }
.chat.editingq .msg.queued { display:none }
.qeditor { max-width:760px; margin:8px auto; padding:10px; border:1px solid var(--accent); border-radius:14px; background:var(--card) }
.qeditor .qrow { display:flex; flex-direction:column; gap:4px; margin:8px 0 } .qeditor textarea { flex:1; font:inherit; padding:8px;
  border-radius:10px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.qeditor .qmoves { display:flex; gap:6px; align-self:flex-end } .qeditor .qmoves button { width:34px; height:30px; padding:0;
  border-radius:8px; border:1px solid var(--line); background:var(--sunk); color:var(--ink) }
.qeditor .qfoot { display:flex; justify-content:flex-end; gap:8px; margin-top:8px } .qeditor .qfoot button { min-height:36px; padding:4px 16px; border-radius:999px }
.benchbtns { display:flex; flex-wrap:wrap; gap:8px; margin:12px 0 } .benchbtns button { min-height:40px; padding:6px 14px; border-radius:999px }
table.benchres { width:100%; border-collapse:collapse; font-size:14px } table.benchres td, table.benchres th { padding:6px 4px; text-align:left;
  border-bottom:1px solid var(--line) }
.round { margin:0 0 8px; padding:6px 10px; border-left:3px solid var(--line); color:var(--muted) } .round p { margin:4px 0 }
.round > summary { cursor:pointer } .round.now { border-left-color:var(--accent); color:var(--ink) }
.chat details.need form .attach { display:inline-flex; align-items:center; justify-content:center; width:36px; height:36px; margin-right:6px;
  border-radius:50%; background:var(--sunk); cursor:pointer; font-size:20px }
.gatechoices { display:flex; flex-direction:column; gap:6px; margin:8px 0 } .gatechoices form { margin:0 }
.gatechoices button.choice, .chat details.need .gatechoices button.choice { width:100%; min-height:40px; text-align:left; white-space:normal; padding:8px 12px; border-radius:12px;
  border:1px solid var(--line); background:var(--sunk); color:var(--ink); font:inherit; cursor:pointer }
.gatechoices button.choice.rec, .chat details.need .gatechoices button.choice.rec { border:1.5px solid var(--accent); background:var(--card) }
.gatechoices button.choice.yours { color:var(--muted); font-style:italic }
.gatechoices .tag { display:block; font-size:12px; font-weight:600; color:var(--accent); letter-spacing:.02em }
.chat a.shot { display:block; margin-top:8px } .chat a.shot img { display:block; max-width:100%; max-height:420px; border-radius:12px;
  border:1px solid var(--line) } .chat a.file { display:inline-block; padding:2px 10px; border-radius:999px; background:var(--sunk) }
.chat .say { overflow-wrap:anywhere } .chat .say p { margin:0 0 10px } .chat .say p:last-child { margin-bottom:0 }
.chat .say pre { overflow-x:auto; padding:10px; border-radius:8px; background:var(--sunk); font-size:13px }
.chat .say h3 { font-size:16px; margin:12px 0 6px }
.chat details.ran > summary { list-style:none; cursor:pointer; color:var(--muted); font-size:15px; padding:4px 0 }
.chat details.ran > summary::before { content:'☰'; margin-right:10px; opacity:.7 } .chat details.ran > summary::after { content:' ›' }
.chat details.ran[open] > summary::after { content:' ⌄' } .chat details.ran > summary::-webkit-details-marker { display:none }
.chat details.ran ul { margin:2px 0 4px; padding-left:22px; font:12.5px/1.5 ui-monospace,monospace; color:var(--muted) }
.chat details.ran li { overflow-wrap:anywhere }
.chat .msg.you.pending { opacity:.6 } .chat .msg.you.pending::after { content:' · queued'; font-size:12px; color:var(--muted) }
.chat .turnacts { display:flex; gap:4px; margin-top:-4px } .chat .turnacts button { display:inline-flex; align-items:center; justify-content:center } .chat .turnacts button[hidden] { display:none } .chat .stamp { font-size:10.5px; color:var(--muted); opacity:.8; white-space:nowrap }
.chat .turnacts .stamp { margin-left:auto; align-self:center; padding-right:4px } .chat .msg.you .stamp { display:block; text-align:right; margin-top:2px } .chat .turnacts .vset, .chat .turnacts .cont { width:auto; min-width:36px; font-size:13px; padding:0 10px } .chat .turnacts .cont { color:var(--accent) }
.chat .turnacts .vset.on { color:var(--accent); font-weight:600 }
/* The player: over every page while something is being read or waits to be taken up, above the box and the tabs */
.cplayer { position:fixed; left:0; right:0; bottom:0; z-index:45; padding:0 10px 8px; pointer-events:none }
.cplayer[hidden], .cppanel[hidden] { display:none }
/* typing on a phone, the box rides the keyboard up: the player rides just above it */
body.typing .cplayer { top:calc(var(--kbtop, 100vh) - var(--boxh, 0px) - var(--cph, 56px) - 8px); bottom:auto !important }
body.cpon.typing .toend { top:calc(var(--kbtop, 100vh) - 124px - var(--cph, 56px)) } body.cpon.typing .chat { padding-bottom:calc(140px + var(--cph, 56px)) }
html.going main { opacity:.5; transition:opacity .2s .15s }
body.cpon:not(.typing) .toend { bottom:calc(190px + var(--cph, 56px) + env(safe-area-inset-bottom)) }
.cplayer > * { pointer-events:auto; max-width:740px; margin:0 auto }
.cpbar { display:flex; align-items:center; gap:2px; padding:3px 4px; background:var(--card); border:1px solid var(--line);
  border-radius:16px; box-shadow:0 4px 18px rgba(0,0,0,.16) }
.cpbar button { width:40px; height:40px; min-height:40px; padding:0; border:0; border-radius:10px; background:transparent; color:var(--ink);
  display:inline-flex; align-items:center; justify-content:center; flex:none; cursor:pointer } .cpbar button:hover { background:var(--sunk) }
.cpbar button:disabled { opacity:.35 } .cpbar .cphold { color:var(--accent) } .cpbar .cplist[aria-expanded=true] { color:var(--accent); background:var(--sunk) }
.cpbar .cpwhat { flex:1; min-width:0; width:auto; height:auto; display:flex; flex-direction:column; align-items:stretch; justify-content:center;
  padding:2px 6px; line-height:1.25; text-align:left; color:inherit; font:inherit }
.cpwhat b { font-size:12px; color:var(--accent); font-weight:600 } .cpwhat b::after { content:' ▾'; opacity:.7 }
.cpbar .cpcog { font-size:19px; color:var(--muted) } .cpbar .cpcog[aria-expanded=true], .cpbar .cpwhat[aria-expanded=true] { background:var(--sunk) }
.cphead a.cpopen { color:var(--accent); text-decoration:none; font-size:13px }
.cpprojs li { align-items:center } .cpprojs .cpstate { margin-left:auto; font-size:12px; color:var(--muted); white-space:nowrap }
.cpsets { display:flex; flex-direction:column; gap:12px; padding:12px 14px }
.cpline { display:flex; align-items:center; gap:10px; margin:0; font-size:14px } .cpline select { flex:1; min-width:0 } .cpprev { font-size:13.5px; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
.cppanel { display:flex; flex-direction:column; max-height:min(52vh, 420px); margin-bottom:6px; background:var(--card); border:1px solid var(--line);
  border-radius:16px; box-shadow:0 4px 18px rgba(0,0,0,.16); overflow:hidden }
.cphead { display:flex; justify-content:space-between; align-items:center; gap:10px; padding:8px 12px; font-size:13px; color:var(--muted); border-bottom:1px solid var(--line) }
.cpfollow { display:flex; align-items:center; gap:6px; margin:0; color:var(--ink) }
.cprows { list-style:none; margin:0; padding:4px; overflow-y:auto }
.cprows li { display:flex; gap:10px; align-items:baseline; padding:9px 8px; border-radius:10px; cursor:pointer; font-size:14px; line-height:1.35 }
.cprows li:hover { background:var(--sunk) } .cprows li.now { background:var(--sunk); font-weight:600 } .cprows li.now .cpat { color:var(--accent) }
.cprows li.heard { color:var(--muted) } .cprows li.heard .cpwords::before { content:'✓ ' }
.cpat { flex:none; min-width:62px; font-size:11.5px; color:var(--muted); white-space:nowrap }
.cpwords { flex:1; min-width:0 }
table.tokens { width:100%; border-collapse:collapse; font-size:14px; font-variant-numeric:tabular-nums }
table.tokens th { text-align:right; font-weight:600; color:var(--muted); font-size:12.5px; padding:2px 6px } table.tokens th:first-child { text-align:left }
table.tokens td { text-align:right; padding:3px 6px; white-space:nowrap } table.tokens td:first-child { text-align:left; white-space:normal; overflow-wrap:anywhere }
table.tokens tr.sub td:first-child { padding-left:16px; color:var(--muted) } table.tokens tr.all td { font-weight:600; border-top:1px solid var(--line) }
table.tokens tr.head td { padding-top:12px; font-size:12.5px; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; text-align:left }
body.cpon .chat { padding-bottom:calc(12px + var(--cph, 56px)) } body.cpon main:not(:has(.composer))::after { content:''; display:block; height:calc(var(--cph, 56px) + 8px) }
.chat .say.reading { background:color-mix(in srgb, var(--accent) 9%, transparent); box-shadow:0 0 0 8px color-mix(in srgb, var(--accent) 9%, transparent);
  border-radius:4px; transition:background .3s, box-shadow .3s }
.chat .turnacts .spin { transform-origin:12px 12px; animation:spin .9s linear infinite } @keyframes spin { to { transform:rotate(360deg) } }
.voicesheet { position:fixed; left:50%; transform:translateX(-50%); width:min(760px, calc(100% - 24px)); bottom:120px; z-index:36;
  display:flex; flex-wrap:wrap; align-items:center; gap:8px 10px; margin:0; padding:8px 10px 8px 14px; border:1px solid var(--line); border-radius:16px; background:var(--card);
  box-shadow:0 2px 12px rgba(0,0,0,.12); font-size:14px } .voicesheet[hidden] { display:none } .voicesheet .gap { flex:1 }
.voicesheet .aloud { display:flex; align-items:center; gap:6px; color:var(--ink); white-space:nowrap } .voicesheet .aloud input { width:18px; height:18px; margin:0 } .voicesheet .vpin { display:flex; align-items:center; gap:5px; color:var(--ink); white-space:nowrap; font-size:14px } .voicesheet .vpin input { width:18px; height:18px; margin:0 }
.voicesheet .rate, .voicesheet select { height:32px; min-height:32px; padding:0 10px; border-radius:999px; background:var(--sunk); color:var(--ink);
  border:1px solid var(--line); font-size:14px; line-height:1 } .voicesheet .rate { display:inline-flex; align-items:center; justify-content:center;
  min-width:52px; flex:none } .voicesheet .shut { width:32px; height:32px; min-height:32px; padding:0; border-radius:50%; background:transparent;
  color:var(--muted); font-size:16px }
.chat .turnacts button { width:36px; height:36px; min-height:36px; padding:0; border:0; border-radius:8px; background:transparent;
  color:var(--muted); font-size:18px; cursor:pointer } .chat .turnacts button:hover { color:var(--accent); background:var(--sunk) }
.chatlive { display:flex; align-items:center; gap:8px; color:var(--muted); font-size:13px }
.chatlive .act { display:flex; flex-direction:column; min-width:0; flex:1; line-height:1.3 }
.chatlive .act > span { white-space:nowrap; overflow:hidden; text-overflow:ellipsis } .chatlive .act .more { font-size:11.5px }
details.chatlive { display:block } details.chatlive > summary { display:flex; align-items:center; gap:8px; cursor:pointer; list-style:none }
details.chatlive > summary::-webkit-details-marker { display:none } details.chatlive .behind { color:var(--accent) }
.tasklist { margin:8px 0 0; padding:8px 10px; border:1px solid var(--line); border-radius:14px; background:var(--card) }
.tasklist .th { margin:4px 2px 4px; font-size:13px; color:var(--muted) } .tasklist ul { list-style:none; margin:0 0 6px; padding:0 }
.tasklist li { display:flex; gap:10px; padding:8px; margin:4px 0; border-radius:10px; background:var(--sunk); color:var(--ink) }
.tasklist .tk { flex:none; font-family:ui-monospace,monospace; color:var(--muted) } .tasklist .tw { min-width:0; display:flex; flex-direction:column }
.tasklist .tc { font:13px/1.4 ui-monospace,monospace; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
  overflow-wrap:anywhere } .tasklist .tm { font-size:12.5px; color:var(--muted) }
.chatask { padding:10px 12px; border:1px solid var(--flag); border-radius:12px; background:var(--flag-bg) }
.chatask .choices { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px }
.composer { position:sticky; bottom:12px; z-index:30; display:flex; align-items:flex-end; gap:8px; max-width:760px; margin:14px auto 0;
  padding:8px 8px 8px 14px; border:1px solid var(--line); border-radius:24px; background:var(--card); box-shadow:0 2px 12px rgba(0,0,0,.08) }
.composer { flex-wrap:wrap; padding:8px 8px 6px 14px; border-radius:22px } .composer textarea { flex:1 1 100% }
.composerbar { flex:1 1 100%; display:flex; align-items:center; gap:6px; margin-left:-6px }
.composerbar .gap { flex:1 } .composerbar .aloud { display:flex; align-items:center; gap:5px; margin:0; color:var(--muted); font-size:13px;
  white-space:nowrap } .composerbar .aloud input { width:18px; height:18px; margin:0 }
.composerbar .rate, .composerbar select { height:30px; min-height:30px; padding:0 10px; border-radius:999px; background:var(--sunk);
  color:var(--ink); border:1px solid var(--line); font-size:13px; line-height:1 }
.composerbar .rate { display:inline-flex; align-items:center; justify-content:center; min-width:48px; width:auto; flex:none }
.composer .attached { flex:1 1 100%; display:flex; align-items:center; gap:6px; font-size:14px;
  color:var(--muted); padding:2px 0 } .composer .attached[hidden] { display:none } .composer .attached button { width:28px; height:28px;
  min-height:28px; font-size:16px; background:var(--sunk); color:var(--ink) }
.toend { position:fixed; left:50%; transform:translateX(-50%); bottom:calc(190px + env(safe-area-inset-bottom)); z-index:35; width:44px; height:44px;
  min-height:44px; padding:0; border-radius:50%; font-size:20px; background:var(--card); color:var(--ink); border:1px solid var(--line);
  box-shadow:0 2px 10px rgba(0,0,0,.18) } .toend[hidden] { display:none }
body.typing .toend { top:calc(var(--kbtop, 100vh) - 124px); bottom:auto; transform:translate(-50%, -100%) }
.composer .attach { flex:none; display:inline-flex; align-items:center; justify-content:center; width:40px; height:40px; border-radius:50%;
  background:var(--sunk); color:var(--ink); font-size:24px; cursor:pointer; margin-left:-6px }
.composer textarea { flex:1; min-height:28px; max-height:200px; resize:none; border:0; outline:0; background:transparent; color:var(--ink);
  font-size:16px; line-height:1.45; font-family:inherit; padding:6px 0 } .composer button { flex:none; width:40px; height:40px; min-height:40px; border-radius:50%;
  padding:0; font-size:20px; font-weight:700 } .chatheld { max-width:760px; margin:6px auto 0 }
.pinbtn { flex:none; display:inline-flex; align-items:center; justify-content:center; width:36px; height:32px; margin:-6px -6px -6px 0;
  font-size:15px; opacity:.25; filter:grayscale(1); cursor:pointer } .pinbtn.on { opacity:1; filter:none }
a.proj.pinchip .pname::before { content:'📌'; font-size:11px; margin-right:2px }
.archrow { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:8px 0; border-bottom:1px solid var(--line) }
.archrow > div { min-width:0; overflow-wrap:anywhere } .archrow form { flex:none } .archline { margin:6px 0 14px } .nestset { display:none } form.nestadd { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin:8px 0 }
.nestchip .nestcount { color:var(--muted); font-weight:400 } .nestopen { cursor:pointer; padding:0 6px; font-size:16px; color:var(--accent) }
.saidlast { display:block; margin:12px 0; padding:10px 12px; border:1px solid var(--line); border-radius:10px; background:var(--card);
  color:var(--ink); text-decoration:none } .saidlast .said { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
  overflow:hidden } a.prow.out { opacity:.55 }
.pwork { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:13px; color:var(--muted) }
.psub { display:block; margin:3px 0 0 18px; color:var(--muted); font-size:13.5px }
.listhead { margin:12px 4px 6px; font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em }
.listhead:first-child { margin-top:4px } .listtop { display:flex; align-items:baseline; gap:12px }
.listtop a { margin-left:auto; font-size:14px; text-decoration:none }
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
:is(form.options, div.optset) { display:flex; flex-direction:column; gap:10px }
:is(form.options, div.optset) label { display:flex; gap:6px 10px; align-items:center; flex-wrap:wrap }
.provider-fields { display:contents } div.optset p.muted { margin:-6px 0 0 }
:is(form.options, div.optset) input[type=text], :is(form.options, div.optset) input:not([type]) { font:inherit; padding:5px 8px; border-radius:7px;
  border:1px solid var(--line); background:var(--bg); color:var(--ink); min-width:0; flex:1 1 220px; max-width:100% }
:is(form.options, div.optset) button { align-self:flex-start }
/* Settings: each part folded to its name and where it stands */
details.conn { border-top:1px solid var(--line); padding:10px 0 } details.conn > summary { cursor:pointer }
details.conn label.tick { display:inline-flex; align-items:center; gap:6px; margin:4px 14px 4px 0 }
details.setsec { margin:0 0 10px; border:1px solid var(--line); border-radius:12px; background:var(--card); max-width:760px }
details.setsec > summary { display:flex; flex-direction:column; gap:3px; padding:14px 40px 14px 16px; cursor:pointer;
  list-style:none; position:relative } details.setsec > summary::-webkit-details-marker { display:none }
details.setsec > summary::after { content:'›'; position:absolute; right:16px; top:50%; transform:translateY(-50%);
  font-size:22px; color:var(--muted); transition:transform .15s } details.setsec[open] > summary::after { transform:translateY(-50%) rotate(90deg) }
.sttl { font-weight:600; font-size:16px } .ssum { color:var(--muted); font-size:13px; overflow-wrap:anywhere } .ssum .warn { color:var(--flag) }
details.setsec > .card, details.setsec > .card + h2 + .card { border:0; border-top:1px solid var(--line); border-radius:0; margin:0;
  background:transparent } details.setsec > h2 { margin:0; padding:14px 16px 0; border-top:1px solid var(--line) }
details.setsec > section.card h2 { margin-top:0 }
form.options textarea.short { min-height:44px }
.vision { margin:18px 0; max-width:900px } .vision details { margin:12px 0 }
.vision > summary { cursor:pointer; overflow:hidden; text-overflow:ellipsis } .vision[open] > summary { margin-bottom:8px }
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
.titlerow .ppin { margin-left:auto; text-decoration:none; white-space:nowrap } .titlerow .ppin + .psettings { margin-left:0 }
.ppinmark { font-size:13px; margin-right:3px; opacity:.3; filter:grayscale(1) } .ppin.on .ppinmark { opacity:1; filter:none }
.exitlink { font-size:14px; text-decoration:none; white-space:nowrap }
.segs { display:flex; gap:4px; margin:6px 0 12px; padding:3px; border-radius:10px; background:var(--sunk); width:fit-content; max-width:100% }
.seg { background:transparent; color:var(--muted); padding:6px 12px; border-radius:8px } .seg.on { background:var(--card); color:var(--ink); font-weight:600 }
.picker { display:flex; flex-direction:column; gap:6px }
.chosen { max-width:100% } .chosen input, form.options .chosen input:not([type]) { min-width:0; flex:1 1 auto; width:auto } .chosen button { flex:none }
.psettings .panel { position:absolute; right:0; z-index:10; width:min(440px, calc(100vw - 32px)); padding:14px 16px;
  border-radius:10px; background:var(--card); border:1px solid var(--line); box-shadow:0 8px 24px rgba(0,0,0,.18) } hr { border:0; border-top:1px solid var(--line); margin:14px 0 }
.sdot { width:10px; height:10px; border-radius:50%; flex:none; background:transparent; border:2px solid var(--line); box-sizing:border-box }
.sdot.off { opacity:.55 }
/* in the bar, a project's dot is in its own colour, whichever project's page is open */
nav .proj[style] .sdot { --accent:var(--pc) } @media (prefers-color-scheme: dark) { nav .proj[style] .sdot { --accent:var(--pcd) } }
.sdot.idle { border-color:var(--accent) }
.sdot.working { border-color:var(--accent); border-top-color:transparent; animation:spin .8s linear infinite }
.sdot.needs-you { background:var(--flag); border-color:var(--flag); animation:beat 1.1s ease-in-out infinite }
@keyframes spin { to { transform:rotate(360deg) } }
@keyframes beat { 50% { transform:scale(1.35); box-shadow:0 0 0 4px color-mix(in srgb, var(--flag) 25%, transparent) } }
a.status, a.status:visited { display:block; margin:10px 0 4px; padding:10px 14px; border-radius:10px; background:var(--card); border:1px solid var(--line);
  color:var(--ink); text-decoration:none } .statusline { display:flex; align-items:center; gap:8px; min-width:0 }
.statusline .muted { overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
.agents { font:12.5px/1.6 ui-monospace,Menlo,monospace; margin-top:4px } .agents:empty { display:none }
.agent { display:flex; justify-content:space-between; gap:12px; color:var(--muted) } .agent.current { color:var(--ink); font-weight:600 }
.agent > span:first-child { flex:none } .agent .muted { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
@keyframes pulse { 50% { opacity:.35 } }
.console-bar { display:flex; align-items:center; gap:12px; justify-content:space-between; margin-bottom:8px; font-size:13px }
.console-bar form { margin:0 } button.quiet { background:var(--sunk); color:var(--ink) }
.needs summary { cursor:pointer } .need { border-top:1px solid var(--line); padding:10px 0 }
.helmform { margin-left:auto } .titlerow .psettings + .helmform { margin-left:0 } .helmcard .titlerow .badge { margin-left:auto } .helmcard label.stack { flex-direction:column; align-items:stretch }
.helmcard textarea { width:100%; min-height:56px; font:inherit; padding:7px 9px; border-radius:7px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.focus ul { margin:6px 0 0; padding-left:0; list-style:none } .focus li .kind { display:inline-block; min-width:52px; font-size:12px; color:var(--muted) }
/* Needs you, project by project: its name and count, its first decisions, the rest folded, its box, then the rest */
.ngroup + .ngroup { margin-top:16px } .nhead { display:flex; align-items:center; gap:8px; margin:4px 0 2px; font-size:15px }
.nhead a { text-decoration:none } .nhead .badge { margin-left:0 } .titlerow .badge.stance { margin-left:0; align-self:center }
details.more > summary, details.morepins > summary { cursor:pointer; color:var(--accent); font-size:13px; padding:8px 0;
  border-top:1px solid var(--line) }
/* a long record folded behind its count and latest date */
details.fold > summary, details.versions > summary { cursor:pointer } details.fold > summary h3 { display:inline; margin:0 8px 0 0 }
details.fold[open] > summary, details.versions[open] > summary { margin-bottom:8px }
.startver { display:flex; gap:8px; flex-wrap:wrap; align-items:center } .pinlink { margin:12px 0 0; font-size:14px }
.status .waits { font-size:13px; margin-top:2px }
.pinned { margin:10px 0 6px } .pinhead { display:flex; align-items:baseline; gap:12px } .pinhead h2 { margin:10px 0 6px }
.show .showhead { display:flex; gap:8px; align-items:baseline } .show .showhead .who { margin-left:auto; white-space:nowrap }
.show { min-width:0 } .show .showhead { flex-wrap:wrap } .show > div { overflow-wrap:anywhere }
.show .try { margin-top:6px } .show .open { display:inline-block; max-width:100%; margin-top:6px; overflow-wrap:anywhere } .show .shot { display:block; max-width:100%; margin-top:8px; border-radius:6px }
.rate { display:flex; gap:2px; margin-top:6px } .star { background:none; border:none; padding:2px 4px; font-size:20px; color:var(--line); cursor:pointer }
.star.on { color:var(--accent) }
.pinhead a { margin-left:auto; font-size:14px; text-decoration:none } .pin { padding:7px 0; border-top:1px solid var(--line) }
.pinline { display:flex; align-items:center; gap:8px } .pintitle { text-decoration:none; font-weight:600; flex:1; min-width:0;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap } .pin .who { white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
.pin > a.pintitle { display:block }
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
/* what changed since the last look: one dated line each, two at most on a phone; what more there is, counted */
ul.events { list-style:none; padding:0; margin:0 } ul.events li { margin:5px 0 } ul.events .when { color:var(--muted); font-size:12.5px;
  font-variant-numeric:tabular-nums; margin-right:4px } .since .more { margin:10px 0 0 }
.need .who { display:flex; align-items:center; gap:8px } .need .who .kind { margin-left:auto; text-align:right }
form.clear { margin:0; flex:none } form.clear button { padding:2px 10px; font-size:12px }
/* clearing sits at the foot, at the far left: never first under a thumb, never beside the answer's own button */
.needfoot { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-top:6px }
.ready { padding:6px 0; border-top:1px dashed var(--line) } .ready:first-child { border-top:0 } form.verdict { display:flex; gap:8px; flex-wrap:wrap; margin-top:6px } form.verdict input { flex:1 1 140px; min-width:0; font:inherit; padding:5px 8px; border-radius:7px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.num { display:inline-block; min-width:1.7em; padding:0 6px; margin-right:2px; border-radius:999px; text-align:center;
  font-weight:700; font-size:13px; background:var(--flag-bg); color:var(--flag) }
.need.settled { color:var(--muted) } .need.settled .num { background:var(--sunk); color:var(--muted) }
.default { margin:4px 0 } details.one summary { cursor:pointer; color:var(--accent); font-size:13px; margin-top:4px }
form.bynumber { margin:12px 0 14px; align-items:center } form.bynumber textarea { min-height:56px }
form.bynumber button { margin-left:auto } .dayline { font-size:13px }
ul.landed, ul.queue { padding-left:0; list-style:none } ul.landed li, ul.queue li { overflow-wrap:anywhere; margin:5px 0 }
.need form.add button { margin-left:auto } .need pre { margin:6px 0; font:12px/1.45 ui-monospace,Menlo,monospace; white-space:pre-wrap; color:var(--muted) }
/* a question asked in a console: its questions first, the whole turn folded beneath, as prose */
.asked p { margin:6px 0 } .asktext { margin:6px 0 } .asktext p, .asktext ul { margin:6px 0 } .asktext h3 { margin:10px 0 4px; font-size:14px }
details.full > summary { cursor:pointer; color:var(--accent); font-size:13px }
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
/* what people and agents write can hold a long unbroken word (a flag, a path, a URL): it breaks rather than widen a
   phone's page (anywhere, in what they write; at the edge, everywhere else, which never squeezes a row's label to a
   letter a line); a control's own label never breaks mid-word, and a choice never outgrows its box */
main { overflow-wrap:break-word }
.note, .need, .ready, .reply, header p, .item .body p, ul.events li, .vision, .card > p, .show > div, .psub { overflow-wrap:anywhere }
button, .badge, .num, .st, .timer, select { overflow-wrap:normal } .badge, .num { white-space:nowrap } select { max-width:100% }
.statusline .sdot, .statusline b { flex:none } .statusline .muted { min-width:0 }
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
.navfoot .short { display:none } nav .navrow { display:contents }
/* A phone: the sidebar becomes one row of chips (dot, name, waiting count), then + and ⚙; it scrolls sideways. */
@media (max-width: 700px) { body { display:block }
  nav { width:auto; border-right:0; border-bottom:1px solid var(--line); padding:0; white-space:nowrap }
  nav .navrow { display:flex; align-items:center; gap:6px; padding:8px 10px; overflow-x:auto }
  nav .pinjump:not([hidden]) { display:flex; position:absolute; left:6px; bottom:9px; z-index:2; width:34px; height:34px;
    align-items:center; justify-content:center; border-radius:50%; border:1px solid var(--line); background:var(--card);
    box-shadow:0 2px 8px rgba(0,0,0,.18); font-size:15px; padding:0; cursor:pointer }
  nav span.nestgroup { display:inline-flex; flex:none; align-items:center; gap:4px; margin:0; padding:3px; border-radius:999px }
  nav span.nestgroup .nestchip { background:transparent; font-weight:600 }
  nav span.nestgroup .members .proj.nestset { display:inline-flex; background:transparent; padding:2px 10px; font-size:17px; color:var(--accent) }
  nav span.nestgroup .members .proj { min-height:32px; padding:2px 10px; font-size:14px }
  nav .proj { flex:none; flex-direction:row; align-items:center; margin:0; padding:5px 11px; border:0;
    border-radius:999px; background:var(--sunk) }
  nav .proj.on { background:var(--accent); color:var(--card) } nav .proj.on .sdot:not(.needs-you) { border-color:var(--card) } nav .proj.on .sdot.working { border-top-color:transparent }
  nav .sline, nav .badge.new { display:none }
  /* the steward is no project: a slim bar of its own across the top, its state, the helm and what waits in every project */
  nav .proj.monitor { display:flex; gap:10px; margin:0; padding:6px 14px; min-height:38px; border:0; border-radius:0;
    background:color-mix(in srgb, var(--accent) 14%, var(--card)); border-bottom:1px solid var(--line) }
  nav .proj.monitor .pname { font-weight:700; letter-spacing:.02em } nav .proj.monitor .sline { display:block; flex:1; min-width:0;
    overflow:hidden; text-overflow:ellipsis; color:var(--muted); font-size:13px }
  nav .proj.monitor.on { background:var(--accent); color:var(--card) } nav .proj.monitor.on .sline { color:var(--card); opacity:.85 }
  .note { margin-left:0 } .reply { padding-left:8px } form.add { margin-left:0 }
  ul.events li { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden }
  /* nothing is wider than the phone: a table's words wrap, and what still can't fit scrolls in its own box */
  table.bench { display:block; max-width:100%; overflow-x:auto } table.bench td { white-space:normal; overflow-wrap:anywhere }
  .console-bar { flex-wrap:wrap } .console-bar > span { flex:1 1 220px; min-width:0; overflow-wrap:anywhere }
  /* All projects, Add, Models and Settings ride at the right of the steward's bar, always in reach */
  .navfoot { position:absolute; top:0; right:4px; flex-direction:row; align-items:center; margin:0; padding:0; border:0;
    gap:0; font-size:17px; z-index:3 }
  /* its five links are 44px each to a finger: the steward's line ends before them, never under them (the person's screenshot, Oct 26) */
  nav .proj.monitor { padding-right:232px }
  @media (max-width: 440px) { nav .proj.monitor .sline { display:none } }   /* no room for its line there: its dot says it */
  .navfoot a { padding:0 8px; min-height:38px; display:inline-flex; align-items:center; justify-content:center }
  .navfoot .long { display:none } .navfoot .short { display:inline }
  nav .proj.monitor.on ~ .navrow .navfoot a { color:var(--card) }   /* on the steward's own page its bar is the accent */
  nav { position:sticky; top:0; z-index:40 }
  /* iPhone zooms in on a box typed in below 16px, and zoomed in, the bottom bar sits mid-screen: no box is below 16px */
  input, textarea, select { font-size:16px }
  .composer { bottom:calc(64px + env(safe-area-inset-bottom)) }
  body.typing .tabs { display:none }
  body.typing .composer { position:fixed; left:8px; right:8px; top:calc(var(--kbtop, 100vh) - var(--boxh, 0px)); bottom:auto; transform:none;
    margin:0; box-shadow:0 0 0 8px var(--bg) }
  /* below it to the keyboard, the page's own ground: where the phone's measure and its keyboard differ, nothing shows through */
  body.typing .composer::after { content:''; position:absolute; left:-8px; right:-8px; top:calc(100% + 8px); height:420px;
    background:var(--bg); pointer-events:none }
  body.typing .chat { padding-bottom:140px } body.typing main { padding-bottom:70vh }
  .voicesheet { bottom:calc(64px + 118px + env(safe-area-inset-bottom)) }
  body.typing .voicesheet { top:calc(var(--kbtop, 100vh) - 118px); bottom:auto; transform:translate(-50%, -100%) }
  .tabs { position:fixed; left:0; right:0; bottom:0; z-index:40; margin:0; gap:0; background:var(--card);
    border-top:1px solid var(--line); padding:4px 6px calc(4px + env(safe-area-inset-bottom)) }
  /* six tabs fit a 360px phone whole: each as wide as its word, a little air between (at an even split and 3.7vw,
     Overview, Roadmap and Showcase were cut short and ran together) */
  .tabs a { flex:1 1 auto; min-width:0; text-align:center; padding:10px 3px 9px; border-radius:0; color:var(--muted);
    font-size:clamp(11px, 3.3vw, 15px); white-space:nowrap; overflow:hidden; text-overflow:ellipsis } main { padding-bottom:78px }
  /* the tab bar reads as navigation, not as more keys: its own tinted ground, the current tab in the accent */
  .tabs { background:color-mix(in srgb, var(--accent) 12%, var(--bg)); border-top:0; box-shadow:0 -1px 0 var(--line) }
  .tabs a.on { background:transparent; color:var(--accent); font-weight:700; box-shadow:inset 0 3px 0 var(--accent) } }
/* A finger, not a pointer: whatever is tapped in a list is at least 44px tall. */
@media (pointer: coarse) {
  button, select, nav .proj, .tabs a, a.prow { min-height:44px }
  .navfoot a { min-height:44px; min-width:44px; display:inline-flex; align-items:center; justify-content:center }
  details > summary, details.more > summary, details.morepins > summary { padding-top:12px; padding-bottom:12px }
  .pinmore > summary { min-width:44px; min-height:44px; padding:0; display:flex; align-items:center; justify-content:center }
  .pintitle { line-height:44px } a.go, a.dayline, a.showall, .pinlink a, .listtop a, .pinhead a, .nhead a, .titlerow > a {
    display:inline-block; padding-top:12px; padding-bottom:12px } }
"""
