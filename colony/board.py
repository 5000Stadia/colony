"""The board: one page for all of a person's projects, the way they would run them as a manager.

    colony track [PATH]      put a project on the board (once per project)
    colony board [--port N]  serve the board locally

Each project keeps everything in files the agent and git already handle: ROADMAP.md (milestones and
items), .board/gates.jsonl (what the agent has put in the person's hands) and .board/notes.jsonl (the
person's comments on past work, gates and roadmap items not yet reached). The board remembers only which
projects it shows and when the person last caught up on each. It answers only itself.
"""
import contextvars
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
  --item R4`; their comment arrives as a note.
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
- To check back on something later, `colony wake --in 2h "what to check"` (or `--at HH:MM`, `--every 1d`), never a
  sleeping process or cron: it comes as a note when you're idle in the person's hours (`--list`, `--cancel ID`).
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
    if path.exists() and "lan" not in old:
        # a board from before a new one answered this machine only kept the default it had then, the home network,
        # pinned once so nothing about how it is reached changes (every board saves its settings in full after this)
        old["lan"] = True
        raw = json.loads(path.read_text())
        raw.setdefault("settings", {})["lan"] = True
        save_registry(raw)
    reg["settings"] = dict(DEFAULT_SETTINGS, **old)
    return reg


# The person's global options, with what each means; the board's Settings page and `colony settings` show them.
DEFAULT_SETTINGS = {"providers": None, "provider": "claude", "remote": True, "monitor": True, "lan": False, "messaging": True, "trust": True, "model": "", "effort": "",
                    "permissions": "all", "consult": True, "consultants": {},
                    "safe_pause": 98, "auto_update": True, "monitor_model": {}, "model_adoption": "automatic",
                    "helper_models": {}, "auto_balance": 3, "remote_by": {}, "safe_pause_session": 99, "turbo_by": {}, "active_hours": hours.DEFAULT, "updates": 60,
                    "tailscale": "", "tailscale_lan_before": None, "steward_name": ""}
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
    "lan": "the board answers every device on your home network, not only this machine (off: your phone reaches it through Tailscale or the Claude app)",
    "messaging": "project agents can message each other (colony send, colony reply)",
    "trust": "a new console's start-up questions (folder trust, permission mode, Remote Control, hooks) are answered so it runs as set up",
    "permissions": "what new sessions may do unasked: ask, edits, all, or plan",
    "monitor": "the steward session runs with the board",
    "steward_name": "what you call the steward, as Jarvis (blank: steward); it answers to it, and the board shows it",
    "model": "main-agent model pin for this provider (blank: colony Auto)",
    "effort": "effort for a main-agent model pin (Auto chooses both model and effort)",
    "consult": "agents consult two fresh models, one from each family, at decisions costly to change (colony consult)",
    "model_adoption": "when a new model becomes a recommendation: automatic or ask (one decision per model)",
    "helper_models": "provider-specific helper pins; edit the colony helper tiers in Settings",
    "monitor_model": "the steward's model, as MODEL:EFFORT (blank: its program's step-up tier)",
    "autostart": "colony starts with the machine (a systemd user service): the board, then each console that was running, back in its own conversation, and those that were mid-work told to carry on",
    "auto_update": "keep Claude Code and Codex updated daily, and reload a console onto the new version (or changed settings) once it sits idle, in the same conversation",
    "safe_pause": "safe pause: at this % of a program's 5-hour or weekly limit, its projects land what's in flight, save their work and tell you where things stand, before the limit cuts them off mid-task; colony wakes them at the reset (off: never)",
    "consultants": "each family's consultant, as claude=MODEL:EFFORT,codex=MODEL:EFFORT (blank: from the benchmark cards)",
    "updates": "minutes between the steward's updates to you, helm or not: what the projects finished, then what was already waiting on you, then new asks, then what each project's agent has been saying, digested by a small model, with the steward's commentary (off: none); never while it is muted or outside your active hours",
    "active_hours": "when you're around, as HH:MM-HH:MM on this machine's clock (off: always): outside them agents keep working but hold their questions, which reach you together when your hours begin",
}


# The steward was once called the monitor: its settings are stored (and may be typed) under the old keys, and
# shown and typed under the new names.
SETTING_ALIASES = {"steward": "monitor", "steward_model": "monitor_model"}
SETTING_NAMES = {v: k for k, v in SETTING_ALIASES.items()}


# The person can name the steward ("rename the steward to something like Jarvis or whatever I want", Oct 28): the
# board and the steward's own brief then use their name, as they typed it, and what reaches a project keeps the
# role beside it, so its agent knows who that is. Unnamed, every line reads as it always did.
STEWARD_NAME_MAX = 30


def steward_name_of(value):
    """A name as the person typed it, trimmed; '' for none (or "steward" itself). One too long, or with what can't
    be shown plainly everywhere it goes (a page, a console title, the steward's brief), is refused with ValueError."""
    v = " ".join(str(value or "").split())
    if v.lower() in ("", "steward", "the steward"):
        return ""
    if len(v) > STEWARD_NAME_MAX:
        raise ValueError(f"the steward's name is at most {STEWARD_NAME_MAX} characters")
    if not re.fullmatch(r"[^\W_][\w .'-]*", v):
        raise ValueError("the steward's name takes letters, digits, spaces and . ' - only, starting with a letter or digit")
    return v


def steward_named():
    """The name the person gave the steward, or '' while it has none."""
    try:
        return steward_name_of(registry()["settings"].get("steward_name"))
    except ValueError:                                   # a hand-edited record: shown as the default, never raw
        return ""


def steward_name():
    """What the board calls the steward: the person's name for it, else "steward"."""
    return steward_named() or "steward"


def the_steward(start=False, possessive=False):
    """The steward in a sentence: "the steward" ("The steward" at its start, "the steward's"), or its name."""
    n = steward_named()
    return (n or ("The steward" if start else "the steward")) + ("'s" if possessive else "")


def setting_help(key):
    """A setting's line, the steward in it called by the person's name for it once it has one."""
    text, named = SETTING_HELP.get(key, ""), steward_named()
    if not named or key == "steward_name":
        return text
    return (text.replace("the steward session", f"{named}'s session").replace("the steward's", f"{named}'s")
            .replace("the steward", named))


def steward_role():
    """The steward as a project's agent reads it: the role, with the person's name for it in front when it has one."""
    n = steward_named()
    return f"{n}, the person's steward" if n else "the person's steward"


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
        was = reg["settings"].get(key)
        reg["settings"][key] = str(value).lower() in ("on", "true", "yes", "1")
        if key == "lan" and reg["settings"][key] != was:
            save_reach(reg["settings"][key], reg)     # a restart starts the board as it was last started
    elif key == "autostart":                   # kept by systemd, not here: what it says is what holds
        from . import autostart
        try:
            autostart.turn(str(value).lower() in ("on", "true", "yes", "1"))
        except RuntimeError as err:
            raise ValueError(str(err))
        return
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
    elif key == "steward_name":
        before = steward_named()
        reg["settings"][key] = steward_name_of(value)       # ValueError, saying what reads, for anything else
        renamed = (before, reg["settings"][key]) if reg["settings"][key] != before else None
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
    if key == "steward_name" and renamed:
        from . import monitor
        monitor.renamed(*renamed)                            # its brief says the new name, and it is told once
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


def reach(saved=False):
    """Whether the board answers other devices on the network as it runs now: the running board's own record
    (listening.json, written as it starts to serve). saved: as it will after its next restart, which repeats the start
    it saved (server.json). Either way the lan setting is only the default for a start without --lan or --local: read
    in their place, the steward sent the person to a home-network address of a board started with --local, which
    answered this machine alone."""
    if not saved:
        try:
            rec = json.loads((home() / "listening.json").read_text())
            os.kill(rec["pid"], 0)                      # a board that has stopped has no reach of its own
            return bool(rec["lan"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        args = json.loads((home() / "server.json").read_text())
    except (OSError, ValueError):
        args = []
    return "--lan" in args if "--lan" in args or "--local" in args else registry()["settings"]["lan"]


def save_reach(lan, reg=None):
    """The saved start answers the network or only this machine, so the next restart does: a change of the lan
    setting takes effect there. Not while remote access holds the board open to the person's devices
    (`colony remote --off` gives it back the reach it had)."""
    saved = home() / "server.json"
    if (reg or registry())["settings"].get("tailscale") or not saved.exists():
        return
    try:
        args = [x for x in json.loads(saved.read_text()) if x not in ("--lan", "--local")]
    except ValueError:
        return
    saved.write_text(json.dumps(args + ["--lan" if lan else "--local"]))


def urls(port, lan=None):
    """Every address the board can be opened at, for the person to use from each place: beyond this machine only
    where it answers there (reach(), unless `lan` says)."""
    import socket
    from . import serving, tailscale
    out = [f"http://127.0.0.1:{port}/"]
    if registry()["settings"].get("tailscale") and _https_served(port):
        out.insert(0, tailscale.url(443))
    if reach() if lan is None else lan:
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


_HTTPS = {"at": 0.0, "on": False}


def _https_served(port):
    """Whether Tailscale serves the board over https now (looked up at most once a minute)."""
    from . import tailscale
    if time.time() - _HTTPS["at"] > 60:
        _HTTPS.update(at=time.time(), on=shutil.which("tailscale") is not None and tailscale.served().get(443) == port)
    return _HTTPS["on"]


def projects(reg=None, adopt=True):
    """Every project on the board: those added by hand, then each subfolder of each project folder.
    A subfolder seen for the first time is put on the board as it is found (unless adopt is off: a look that only
    reads, as colony migrate --list's)."""
    reg = reg or registry()
    out = [Path(p) for p in reg["projects"]]
    for root in map(Path, reg["roots"]):
        if not root.is_dir():
            continue
        for sub in sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")):
            if (sub / ".git").is_file():          # a git worktree a project made for a branch: part of it, not another
                continue
            if sub not in out and str(sub) not in reg["hidden"]:
                if adopt and not (sub / ".board").exists():
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
    return f"{providers.unusable(now)}, so new projects and {the_steward()} run on {providers.get(here).label} (colony settings provider to change it)"


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
    text = _protocol(root).replace("GUIDE_PATH", str(Path(__file__).resolve().parent.parent / "GUIDE.md"))
    return text + PRIVATE if is_private(root) else text


# A private project's agent is told why its plan and notes never show in git status, and not to force them in.
PRIVATE = ("\nThis project is private: its repository is public or shared, so nothing of colony's enters its history. "
           "Git here ignores colony's files (these instructions, colony's settings and helpers, `.board/`, and "
           "`ROADMAP.md` unless the project already tracked it): never force-add or commit them, and keep the person's "
           "words, notes and roadmap out of commit messages.\n")


def is_private(root):
    """Whether nothing of colony's may enter the project's git history (colony track --private): kept on its record,
    so every later wiring honours it. A second agent in a private project's folder is private with it."""
    def marked(r):
        try:
            return bool(json.loads((Path(r) / ".board" / "settings.json").read_text()).get("private"))
        except (OSError, ValueError):
            return False
    return marked(root) or marked(workdir(root))


def wire(root):
    """The provider's wiring in the project's folder; private, in files its repository is told to ignore. Returns what
    the person should know about it (a plan already tracked, no repository)."""
    if not is_private(root):                         # as it always was: a program's wire() needn't know of privacy
        providers_of(root).wire(workdir(root), protocol(root))
        return []
    providers_of(root).wire(workdir(root), protocol(root), private=True)
    return exclude(root)


def exclude(root):
    """Colony's files in the repository's own exclude list (.git/info/exclude, which is local and never committed),
    each line added only if missing; ROADMAP.md only while the project doesn't track it, since untracking a plan it
    already keeps is the person's call. Removing the project leaves the lines: they are harmless."""
    work = workdir(root)
    found = subprocess.run(["git", "-C", str(work), "rev-parse", "--path-format=absolute", "--git-path", "info/exclude",
                            "--show-prefix"], capture_output=True, text=True)
    if found.returncode:
        return [f"{work} is not in a git repository, so there is nothing to exclude colony's files from: they stay local."]
    path, prefix = (found.stdout.splitlines() + ["", ""])[:2]
    files = list(providers_of(root).private_files) + ([".board/"] if work == Path(root) else [])
    said = []
    if git(work, "ls-files", "--", "ROADMAP.md").strip():
        said.append("ROADMAP.md is already tracked in this repository, so it stays in its history; whether to untrack "
                    "it (git rm --cached ROADMAP.md) is yours.")
    elif work == Path(root):                          # a second agent's plan lives in colony's home, not here
        files.append("ROADMAP.md")
    path = Path(path)
    have = path.read_text() if path.exists() else ""
    lines = set(have.splitlines())
    missing = [f"/{prefix}{f}" for f in files if f"/{prefix}{f}" not in lines]
    if missing:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(have + ("\n" if have and not have.endswith("\n") else "")
                        + "# colony: this project is private, so its files stay out of git\n" * ("# colony:" not in have)
                        + "".join(m + "\n" for m in missing))
    return said


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
    """The project a command runs in: the one whose console it runs in, wherever that console has changed folder to
    (a second project sharing a folder is told apart this way too), else the nearest folder upward that is on the
    board (it has a .board). Not git's top level: a project can live inside another repository, like colony's own
    projects/. Taken by the folder, a console's `cd ../Bookflow && colony send` went as Bookflow and filed its gates
    there, and one in /tmp grew a .board of its own: agentpost's silent misattribution, which it fixed the same way."""
    here = Path(path).resolve()
    mine = os.environ.get("COLONY_PROJECT")
    if mine and (Path(mine) / ".board").is_dir():
        return Path(mine)
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


_ROADMAPS = {}                # a plan as read, kept until its text changes: every page reads every project's


def roadmap(root, text=None):
    """The live shared vision (or legacy goal), milestones and their items."""
    import copy
    if text is None:
        from . import lead
        path = lead.plan_path(root)
        text = path.read_text() if path.exists() else ""
    from . import lead
    shared = lead.info(root) if root else None
    key = (text, json.dumps(shared, sort_keys=True, default=str))   # who owns which item reads into it too
    got = _ROADMAPS.get(key)
    if got is None:
        got = _ROADMAPS[key] = _roadmap(text, shared)
        while len(_ROADMAPS) > 200:
            _ROADMAPS.pop(next(iter(_ROADMAPS)))
    return copy.deepcopy(got)                    # a caller's changes never reach the next reader


def _roadmap(text, shared):
    from . import vision
    part = vision.section(text)
    goal = part['text'] or part['legacy']
    if part['start'] is not None:
        text = text[:part['start']] + text[part['end']:]
    milestones, current, last, prev = [], None, None, None
    from . import lead
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


def hold_needs_you(root):
    """A turn said it asks the person, but its words couldn't be read as it ended: the word is kept, so the next turn
    whose words can be read waits on the person with them, rather than spent on nothing."""
    path = Path(root) / ".board" / "needs-you"
    if path.exists():
        path.write_text("held " + now())


def drop_held_needs_you(root):
    """The person answered (in its console, or a note): a word kept from a turn that ended asking is answered too.
    One said in the turn now running stays."""
    path = Path(root) / ".board" / "needs-you"
    try:
        if path.read_text().startswith("held "):
            path.unlink(missing_ok=True)
    except OSError:
        pass


def record_ask(root, key, text, explicit=False):
    """One entry per turn: the turn's whole text. A newer question replaces an unanswered older one. A turn whose
    only questions are open gates restated asks nothing new: the gates already wait on the person."""
    if not text or (not explicit and not asks_question(text)) or any(e.get("key") == key for e in read(root, "asks.jsonl") if key):
        return None
    if not explicit and all(restates_gate(q, root) for q in questions(text) or [text]):
        return None
    answer_asks(root, "superseded by a later turn")
    ask = {"type": "ask", "id": "a" + secrets.token_hex(4), "at": now(), "key": key, "text": text.strip()}
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
    marked as handed; at the start of a session instead the ones handed but not yet acted on, so nothing
    sits unanswered however the agent works. What is new waits for a prompt: handed over as a session starts,
    which starts no turn, a new project's first note sat in its agent's context while nothing woke it (it
    counted as handed over), so the agent never spoke first. Left for the prompt, the watcher types one."""
    if session:
        return [], [n for n in open_notes(root) if n["delivered_at"]]
    fresh = [n for n in open_notes(root) if not n["delivered_at"]]
    still = []
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
            else f"from page {a['page']}" if a.get("page") else f"on file {a['file']}" if a.get("file")
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
    lines, named = [heading], steward_named()
    # the person's name for the steward, with its role, so the agent knows who that is
    suggestion = SUGGESTION.replace("the steward", steward_role()) if named else SUGGESTION
    idea = IDEA.replace("the steward's own suggestion", f"{named}'s own suggestion, as the person's steward") if named else IDEA
    for n in ns:
        by = {"monitor": f" (from {steward_role()}, acting for them; never an answer to a decision you put to the person)",
              "suggestion": idea if n.get("kind") == "idea" else ADVICE if n.get("kind") == "advice" else suggestion,
              "colony": " (from colony, the harness the person trusts: act on it as theirs)",
              "page": " (sent by a page in the project as the person used it: what they did, not words they wrote)"}.get(n.get("author"), "")
        lines.append(f"- [{n['id']}] {where(n)}{by}: {n['text']}")
    lines.append('When you have acted on one: colony noted ID "what you did".')
    return "\n".join(lines)


def track(path, register=True, private=False):
    """Put a project on the board: its roadmap, its .board folder, and its provider's wiring (for Claude Code,
    the colony protocol in CLAUDE.md and the delivery hooks) — added to whatever the project already has. Private,
    none of it enters the project's git history (wire()); a project once private stays so."""
    root = Path(path).expanduser().resolve()      # the folder chosen is the root, whatever repository holds it
    shared = workdir(root) != root                # a second project in another's folder: no repository of its own
    ours = {".git", ".board", ".claude", "CLAUDE.md", "ROADMAP.md"}
    # Work of its own: any file colony didn't put there, or commits in its own repository (not an enclosing one).
    joining = not shared and ((root / 'ROADMAP.md').exists() or any(p.name not in ours for p in root.iterdir()) or
                              ((root / ".git").exists() and bool(git(root, "rev-parse", "--verify", "-q", "HEAD").strip())))
    if not shared and not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    if private:
        if providers_of(root).private_files is None:
            raise ValueError(f"{providers_of(root).label} reads its instructions only from a file the project may "
                             "track, so colony can't keep this project private on it")
        settings = root / ".board" / "settings.json"
        settings.parent.mkdir(exist_ok=True)
        own = json.loads(settings.read_text()) if settings.exists() else {}
        settings.write_text(json.dumps(dict(own, private=True), indent=2) + "\n")
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
        append(root, 'notes.jsonl', dict(type='note', id='n' + secrets.token_hex(4), at=now(),
               author='colony', anchor=None, text=join if joining else BEGIN, onboarding='vision'))
    wire(root)
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


def deploy(root):
    """What colony installs in a project for its program (Provider.wire, write_helpers), brought up to date in place:
    its instructions and hooks, its commit stamp, its helpers at their tiers. A file already current is left as it is;
    a running console takes the rest up at its next idle reload, so no work is interrupted."""
    from . import bench, lead
    wire(root)
    lead.install_stamp(root)
    bench.write_helpers(root)


def rewire_projects():
    """deploy() in every project: as the board starts (colony updated) and daily, after its programs update."""
    for root in projects():
        if not root.exists():
            continue
        try:
            deploy(root)
        except (OSError, ValueError):
            continue  # The doctor continues to report a project that could not be wired.


def remove_project(root):
    """Take a project off the board: its session stops, and whatever its consoles left running with it, its files
    staying where they are."""
    root = Path(root)
    console.stop(root)
    try:
        from . import lingering, serving
        lingering.stop_all(root)                       # no one is left to stop it, or to watch it
        serving.remove(root)                           # nor to serve it: its https port goes too
    except Exception:
        pass
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
    return prov.at_rest(root)


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


def archived_off(on=None):
    """Projects now off the board that can come back, each with when and why: archived, and those closed or taken off
    before there was an Archived shelf. {path: row}. on: the projects on the board, where the caller has them."""
    on = {str(p.resolve()) for p in (projects() if on is None else on)}
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


def _bring_back_buttons(roots):
    return "<div class='folderacts'>" + "".join(
        f"<form method='post' action='/bring-back'><input type='hidden' name='path' value='{e(k)}'>"
        f"<button class='quiet'>Bring back {e(Path(k).name)}</button></form>" for k in roots) + "</div>"


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
                        + (_bring_back_buttons(k for k, _ in it) if name else "")
                        + f"</div><form method='post' action='/bring-back'>{what}<button class='quiet'>"
                        + ("Bring back group" if name else "Bring back") + "</button></form></div>")
    return (f"<h2 id='archived'>Archived</h2><div class='card'>{''.join(rows)}<p class='muted'>Off the board and {e(the_steward(possessive=True))} "
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
    note = {"type": "note", "id": "n" + secrets.token_hex(4), "at": now(), "author": author,
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
    gate = dict(type="gate", id="g" + secrets.token_hex(4), at=now(), question=question.strip(), item=item or when,
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
    answer["note_id"] = "n" + secrets.token_hex(4) if tell else None
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
                          text="(cleared by the person without an answer)", note_id="n" + secrets.token_hex(4))
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
                                  text=f"Default stood (skipped): {g['default']}", note_id="n" + secrets.token_hex(4))
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
    shown = [x for x in showcase.entries(root) if x["at"] > at]       # each showing, a new version of one included
    moved_at = stamp(git(root, "log", "-1", "--format=%aI", "--", "ROADMAP.md").strip()) if moved else ""
    return {"head": head, "commits": commits, "count": int(count) if count.isdigit() else len(commits), "moved": moved,
            "moved_at": moved_at, "opened": opened, "replies": replies, "pinned": pinned, "shown": shown, "first": not seen}


# Whether the page being made is the person's own look. Colony's audit, reading every page from this machine to check
# them (R126), is not: it leaves their hours and their last look at each project as they were (Handler._the_persons).
PERSONS_LOOK = contextvars.ContextVar("persons_look", default=True)


def page_base(root, mark=True):
    """Where a project's page for the day starts what landed (and its Overview, what changed): the person's last look at
    it on an earlier day (when, and the commit it stood at), or on a first look, the start of the day before, marked
    first: they were never here, so there is no "since you were last here" to tell. Each look (at either page) is kept
    on this machine, with the board: the pages read the same all day, and turn over with the person's day."""
    mark = mark and PERSONS_LOOK.get()
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
                "at": board_time(before), "head": git(root, "rev-list", "-1", f"--before=@{int(before)}", "HEAD").strip(),
                "first": True}}
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


def local_day(at, clock=False):
    """A board or git time as the person reads it, on this machine's clock: 2026-10-28 (with clock, 2026-10-28 16:00).
    Cut from the stored text it was UTC, and an evening's question read as tomorrow's 23:00 beside Chat's 4:00 PM."""
    from datetime import datetime
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return str(at)[:16 if clock else 10].replace("T", " ")
    return when.strftime("%Y-%m-%d %H:%M" if clock else "%Y-%m-%d")


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


def note_by(n):
    """Who left a note, as a page names them (HTML): the person is "you", everyone else is named as themselves."""
    return {'monitor': e(the_steward()) + ', for you',
            'suggestion': 'research advice' if n.get('kind') == 'advice' else e(the_steward()) + ' suggests',
            'observation': 'file change observed', 'colony': 'colony', 'page': 'a page you used'}.get(n.get('author'), 'you')


def thread(ns, road_items=None):
    out = ""
    for n in ns:
        out += (f"<div class='note'><span class='who'>{note_by(n)} · {e(local_day(n['at']))}</span><div>{e(n['text'])}</div>"
                + (f"<div class='reply'><span class='who'>agent · {e(local_day(n['addressed_at']))}</span>"
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
    return (f"<a class='prow{' out' if out else ''}' href='/?p={i}'{' title=' + chr(39) + e(the_steward(True)) + ' leaves it out' + chr(39) if out else ''}><span class='prowline'>"
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


def machine_card():
    """Restarting or shutting down the machine, the projects parked first (machine.py), or the one under way."""
    from . import autostart, machine
    if machine.state():
        return (f"<form method='post' action='/machine' class='options'><p>{e(machine.line())}</p>"
                "<button name='do' value='cancel' class='quiet'>Cancel</button></form>")
    return ("<form method='post' action='/machine' class='options'><p class='muted'>Each project at work is asked in turn "
            "to park (finish or stop at a clean point, commit) and says when it's safe; once all have, the machine goes. "
            "At the hour it goes regardless.</p><button name='do' value='restart'>Park, then restart</button> "
            "<button name='do' value='off' class='quiet'>Park, then shut down</button></form>"
            + ("" if autostart.on() or not autostart.available() else
               "<p class='muted'>Colony won't start by itself afterwards: tick Start with the machine, under This board.</p>"))


def machine_bar():
    """On the front page while a restart is under way: how far the parking is, with Cancel."""
    from . import machine
    said = machine.line()
    return (f"<div class='card machinebar'><form method='post' action='/machine'><span>{e(said)}</span> "
            "<button name='do' value='cancel' class='quiet'>Cancel</button></form></div>") if said else ""


def home_page(reg):
    """The board's front page: every project and what each needs of the person, answered where it shows."""
    return shell(reg, -2, "<h1>Projects</h1>" + machine_bar() + "<div id='home'>" + home_list(reg) + "</div>"
                 f"<script>{KEEP_CURRENT}keepCurrent(document.getElementById('home'), '/home/feed');</script>")


def all_projects_page(reg):
    """Every project at a glance, under its nest: what it's doing, what waits on the person, what it's for."""
    plist = projects(reg)
    goal = lambda p: first_sentence_of(roadmap(p)["vision"] or roadmap(p)["goal"])
    return shell(reg, -2, "<h1>All projects</h1>" + (tokens_html(brief=True) if plist else "")
                 + (f"<div class='card plist'>{project_list(plist, goal)}</div>" if plist else "<p class='muted'>No projects yet.</p>")
                 + archived_shelf() + left_shelf())


def left_shelf():
    """What consoles that have ended left running, of no project on the board (one taken off it, a trial): the
    person's to stop, as they chose (2026-10-28). A project's own are named to its agent instead (lingering.left)."""
    from . import lingering
    rows, at = [], time.time()
    for g in lingering.unowned():
        more = f" and {g['more']} more" if g["more"] else ""
        facts = [took(at - g["since"]), f"{g['rss'] // 2**20} MB"] + ([", ".join(g["ports"])] if g["ports"] else []) \
            + ([g["cwd"]] if g["cwd"] else [])
        rows.append(f"<div class='archrow'><div><code>{e(short(' '.join(g['what'].split()), 90))}</code>{e(more)}"
                    f"<div class='who'>{e(' · '.join(facts))}</div></div><form method='post' action='/left/stop'>"
                    f"<input type='hidden' name='key' value='{e(g['key'])}'><button class='quiet'>Stop</button></form></div>")
    if not rows:
        return ""
    return (f"<h2 id='left'>Left running</h2><div class='card'>{''.join(rows)}<p class='muted'>Still running from consoles "
            "that have ended, of no project on the board. Stop what is done; what you leave is listed until it ends.</p></div>")


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


NEEDS_PAGE = -3          # the page of everything waiting on the person: no project's, nor the steward's


def steward_status(state=None):
    """The steward's state in one vocabulary wherever it shows (the menu, its page, the Helm tab): what it is doing
    (working, idle, needs you, not running), then who decides: "you decide" with the helm off (muted too, if so), or
    "steward decides" (by its name, once it has one) with the helm on. Before, at one moment its page said "Asleep",
    the menu "idle · helm with you", and "helm with you" read as the helm being on. Returns (line, who decides, what
    that means)."""
    from . import monitor
    if state is None:
        state = monitor.snapshot()["state"]
    if monitor.helm():
        who, means = (f"{steward_name()} decides", f"{the_steward(start=True)} settles routine questions in every project "
                      "included in its helm, within each one's direction; what is yours still comes to you.")
    elif monitor.muted():
        who, means = ("you decide · muted", "Every decision comes to you. Nothing wakes "
                      f"{the_steward()} and it spends nothing; it answers only when you talk to it.")
    else:
        who, means = ("you decide", f"Every decision comes to you. Nothing wakes {the_steward()} but you and its "
                      "scouting, and the board shows you what needs you.")
    return f"{state if state != 'off' else 'not running'} · {who}", who, means


def sidebar(reg, pid):
    from . import monitor
    plist = projects(reg)
    ms = monitor.snapshot()
    side, total = [], 0
    for here, members in nav_groups(plist):
        chips, states, waits = [], [], 0
        for i, p in members:
            # its screen read once: its dot, and what waits on the person
            snap = seen(console.snapshot(p, lines=4)) if p.exists() else {"state": "off", "lines": []}
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
                         f"<span class='sline' id='sline-{i}'>{e(snap.get('behind') or snap['state'] if snap['state'] != 'off' else '')}"
                         f"{' · ' + e(work) if work and snap['state'] != 'off' else e(work)}</span></a>")
        if not here or here == PINNED or len(members) == 1:     # a nest down to one live project: the project itself
            side += [c.replace("class='proj", "class='proj pinchip", 1) for c in chips] if here == PINNED else chips
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
    # the steward first: on a phone it stays in reach at the left
    side.insert(0, f"<a class='proj monitor{' on' if pid == -1 else ''}' href='/steward'><span class='pname'>"
                   f"<span class='sdot {ms['state'].replace(' ', '-')}' id='dot-m'></span>{e(steward_name())}</span>"
                   f"<span class='sline'>{e(steward_status(ms['state'])[0])}</span></a>")
    from . import providers as pv, usage
    for k, prov in pv.PROVIDERS.items():
        ws = usage.read(k) if pv.usable(prov) else {}
        if ws:
            side.append(f"<div class='sline long' style='padding:2px 10px'>{e(prov.label)}: "
                        + " · ".join(f"{e(w)} {v['used']:g}%" for w, v in sorted(ws.items(), key=lambda x: x[0] != "weekly")) + "</div>")
    # first of the links, the first icon right of the steward on a phone: what waits on the person in every project
    # the steward follows, its count glowing, shown while anything does
    side.append(f"<div class='navfoot'><a class='needsbtn{' on' if pid == NEEDS_PAGE else ''}' id='needs-chip' href='/needs-you' "
                f"title='Needs you'{'' if total or pid == NEEDS_PAGE else ' hidden'}><span class='long'>Needs you </span>"
                f"<span id='badge-all' class='badge gate' title='waiting on you, in the projects {e(the_steward())} follows'>{total}</span></a>"
                "<a href='/projects' title='All projects'><span class='long'>All projects</span><span class='short'>☰</span></a>"
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


def player_js():
    from . import voice
    return PLAYER.replace("@ICONS@", json.dumps(ICONS)).replace("@SAMPLE@", json.dumps(voice.SAMPLE))


def trouble_bar():
    """On every page while colony's own watcher fails pass after pass (monitor.trouble): what it no longer does, since
    when, and the error, so a board that looks well while doing nothing is never silent."""
    from . import monitor
    t = monitor.trouble()
    if not t:
        return ""
    return (f"<div class='card troublebar'><b>Colony's watcher is failing</b> (every pass since {e(t['since'][11:16])} UTC, "
            f"{t['count']} in a row): mail between projects, recovery and nudges aren't running. "
            f"<span class='muted'>{e(t['error'])}</span> Its agent can see why in watcher-errors.log.</div>")


def shell(reg, pid, body, wide=False):
    from . import media
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<title>Colony</title><meta name='application-name' content='Colony'>"
            f"<meta name='apple-mobile-web-app-title' content='Colony'><link rel='stylesheet' href='/static/board.css?v={css_version()}'>"
            f"<link rel='stylesheet' href='/static/media.css?v={media.version()}'><script type='module' src='/static/media.js?v={media.version()}'></script>"
            f"<style>{own_colour(reg, pid)}</style></head><body{proj_attr(reg, pid)}><script>{player_js()}</script><nav{'' if pid >= 0 else ' class=noproj'}>{sidebar(reg, pid)}</nav>"
            f"<main{' class=wide' if wide else ''}>{trouble_bar()}{body}</main><script>{FOLDS}{GRIPS}{POLL}{CHIPS}{DRAFTS}{ANSWERS}{LAZY}{LASTPAGE}{BAR}</script></body></html>")


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
  const keyed = () => { const t = document.activeElement;
    return vv.scale > 1.01 || !!(t && t.matches('textarea, input:not([type]), input[type=text], input[type=search], [contenteditable]')); };
  const seat = () => { queued = 0;
    if (getComputedStyle(bar).position !== 'fixed' || document.body.classList.contains('typing') || vv.scale > 1.01) {
      if (shift) { shift = 0; bar.style.transform = ''; } return; }
    // only a keyboard's worth down: the few pixels a toolbar folding away leaves are the phone's to place
    // a bounce past the end (or the top) parts the visible part from the page by just the overshoot (the phone's log,
    // Oct 27: 52px off, 52px past the end): not a keyboard, and the bar sent down by it left the screen for a frame
    const over = Math.max(0, scrollY + innerHeight - document.body.scrollHeight) + Math.min(0, scrollY);
    const base = bar.getBoundingClientRect().bottom - shift, gap = Math.round(vv.offsetTop - over + vv.height - base);
    // never more than a screen: mid-change a phone reports a page-long gap (41515px), which would send the bar off it;
    // and no keyboard without a box to type in: a phone can go on reporting the keyboard's room after it went down
    const want = gap > 60 && gap < innerHeight && keyed() ? gap : 0;
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
  // A page the player will show next is fetched ahead and kept half a minute, so going there is at once.
  let going = 0;
  const ahead = new Map();
  const prefetch = async (url) => { url = new URL(url, location.href).href;
    const was = ahead.get(url); if (was && Date.now() - was.at < 30000) return;
    try { const r = await fetch(url, {credentials: 'same-origin'});
      if (r.ok && (r.headers.get('content-type') || '').includes('text/html')) ahead.set(url, {at: Date.now(), url: r.url, html: await r.text()});
    } catch (e) {}
    while (ahead.size > 6) ahead.delete(ahead.keys().next().value); };
  const go = async (url, how = {}) => {
    if (document.documentElement.classList.contains('media-open')) return;
    const mine = ++going, method = (how.method || 'GET').toUpperCase(), root = document.documentElement;
    root.classList.add('going');                   // on its way: the page dims, so a tap is seen to have landed
    let r = null, html = '', got = url, redirected = false;
    const hit = method === 'GET' && ahead.get(url);
    if (hit && Date.now() - hit.at < 30000) { ahead.delete(url); html = hit.html; got = hit.url; }    // fetched ahead: at once
    else {
      try { r = await fetch(url, {method, body: how.body, credentials: 'same-origin'}); } catch (e) {}
      if (document.documentElement.classList.contains('media-open')) { root.classList.remove('going'); return; }
      if (mine !== going) return;
      root.classList.remove('going');
      if (!r) { if (method === 'GET') location.href = url; return; }
      if (!(r.headers.get('content-type') || '').includes('text/html')) { if (method === 'GET') location.href = r.url; return; }
      html = await r.text(); got = r.url; redirected = r.redirected;
      if (document.documentElement.classList.contains('media-open')) { root.classList.remove('going'); return; }
    }
    root.classList.remove('going');
    const doc = new DOMParser().parseFromString(html, 'text/html');
    if (mine !== going) return;
    if (!doc.querySelector('body > main')) { if (method === 'GET') location.href = got; return; }
    const from = location.href, to = method !== 'GET' && !redirected ? location.href : got;
    if (document.documentElement.classList.contains('media-open')) return;
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
    const kept = ['cplayer', 'gc'].map((x) => document.getElementById(x)).filter(Boolean);    // the player, the message box
    document.body.replaceChildren(...[...doc.body.childNodes].map((n) => document.adoptNode(n)));
    kept.forEach((x) => document.body.appendChild(x));
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
  window.colonyNav = {keep: O, prefetch, go: (u) => document.documentElement.classList.contains('media-open') ? Promise.resolve() : busy() ? go(new URL(u, location.href).href) : (location.href = u),
    replace: (u) => document.documentElement.classList.contains('media-open') ? Promise.resolve() : busy() ? go(new URL(u, location.href).href, {replace: true}) : location.replace(u),
    reload: () => document.documentElement.classList.contains('media-open') ? Promise.resolve() : busy() ? go(location.href, {replace: true}) : location.reload()};
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
  // the sentences of a clip, split as chunks() splits them: what the page turns to and marks as each is heard
  const sentences = (p) => (p.match(/.+?(?:(?<!\\b(?:e\\.g|i\\.e|vs|Dr|Mr|Mrs|Ms|St|[A-Z]))[.!?]+(?=\\s|$)|$)/g) || [p]).map((x) => x.trim()).filter(Boolean);
  // The line (R123): every project's replies not yet heard, as the board keeps them for every device and view (keys,
  // counts and first words; a reply's words are fetched as it is about to be read). Replies you haven't heard wait in
  // it; Play goes through it, and Auto reads what lands while it is on (the person's pick, B).
  let L = {line: [], projects: {}, total: 0, since: '', ready: false}, tag = '', off = 0;
  // parts this page marked, by project and key (two projects' turns can begin in the same millisecond): out of its line
  // at once, before the board's next answer; and parts another device's Auto will read, as the board said: not seen here
  const marked = new Set(), held = new Map(), mk = (p, k) => p + ' ' + k;
  const DEV = (() => { let d = ''; try { d = localStorage.getItem('dev') || ''; if (!d) { d = Math.random().toString(36).slice(2, 8);
    localStorage.setItem('dev', d); } } catch (e) {} return d || 'nodev'; })();
  const clock = () => Date.now() + off;   // the board's clock, which a part's own time is set by
  const id = (x) => x.p + '|' + x.t;
  // the line as this page has it: the board's, less what this page marked since
  const visible = () => L.line.map((x) => Object.assign({}, x, {b: x.b.filter(([k]) => !marked.has(mk(x.p, k)))})).filter((x) => x.b.length);
  // What is being read is one reply, known by its project and turn, never by its place in a list that shifts (a reading
  // kept by place stood still as replies landed: the person's phone, 68 times, 2026-10-28). N: that reply, its parts'
  // words and how far it got; S: the session it is in, what Play or a row asked for and what Auto took up as it landed;
  // back: the line's reading a ▷ under a reply stepped in front of, taken up again after it.
  let N = null, S = null, back = null, playing = false, paused = false, loading = false, via = '', view = null, now = null;
  let startingSound = null;                      // a prepared part, including one whose page or voice is still loading
  let resumeAfterWriting = null;                 // an explicit Play while the box still holds the voice
  let why = null, stopAt = 0, letGo = null, unlocked = false, said = null, saidAt = 0, sent = -1, hidAt = 0;
  const rate = () => { let r = 1; try { r = +localStorage.getItem('pace') || 1; } catch (e) {} return [1, 1.25, 1.5, 1.75].includes(r) ? r : 1; };
  const pace = () => rate() + 0.25;               // the voices read slowly: each speed plays a quarter faster than it says
  const aloud = () => { try { return localStorage.getItem('aloud') === '1'; } catch (e) { return false; } };
  // How it reads, set behind the line's settings row: both (aloud, the page following what is read, into each project's
  // Chat), audio (aloud alone, the page left as it is), text (no sound: each reply shown in its chat, ⏭ goes on). A reply
  // tapped is always heard.
  const mode = () => { try { return localStorage.getItem('automode') || 'both'; } catch (e) { return 'both'; } };
  const following = () => mode() !== 'audio';
  // Auto reads what lands while it is on: a part is new when its own time is after Auto was armed (each part's, not its
  // turn's, as the board judges it). Armed from now when it is turned on, and on a tap after a stop of two hours or
  // more, so a night's replies wait for Play while the answer that lands a little after the phone is locked is read
  const LONG = 2 * 3600e3;
  let armed = +(get('armed') || 0);
  const arm = () => { armed = clock(); put('armed', armed); };
  // able: Auto on and the phone letting this page play (a sound played since it opened, and none refused or let go)
  const able = () => aloud() && unlocked && !letGo;
  const listening = () => able() && !paused && !writing();
  // The pauses and takings-up, each with its cause and how it went, and each let-go with how long the page had been
  // hidden, for the player-state report: the live trial says from the phone's own record what still cuts it off
  const story = [];
  let logged = 0, told = 0;
  const hidden = () => hidAt ? Math.round((Date.now() - hidAt) / 1000) + 's hidden' : 'shown';
  const log = (what, cause, ok) => { story.push([new Date().toISOString().slice(11, 19), what, cause, ok === undefined ? '' : ok ? 'ok' : 'refused', hidden()]);
    logged += 1; if (story.length > 14) story.shift(); };
  const measure = (m) => fetch('/chat/measure', {method: 'POST', body: new URLSearchParams({m: JSON.stringify(m)})}).catch(() => {});
  // Marks: the board's, and during the trial this device's own as well, so going back to the player before it
  // replays nothing it heard here
  const owed = [];
  const send = async (how, byP, seat) => {
    const r = await fetch('/chat/heard', {method: 'POST', body: new URLSearchParams({how, keys: JSON.stringify(byP), dev: DEV, view: seat || 'line'})}).catch(() => null);
    if (!r || !r.ok) { if (how !== 'seen') owed.push([how, byP, seat]); return null; }
    return r.json().catch(() => null); };
  const legacy = (name, ks, ats) => { if (!name || !ks.length) return;
    put('heard:' + name, [...new Set((get('heard:' + name) || []).concat(ks))].slice(-300));
    const top = ats.filter(Boolean).sort().pop(); if (top && top > (get('heardto:' + name) || '')) put('heardto:' + name, top); };
  // parts of a reply heard or skipped: only those still in the line, so a reply heard before plays again writing nothing
  function markParts(x, ks, how) {
    const waiting = new Map(); L.line.forEach((y) => { if (y.p === String(x.p)) y.b.forEach(([k, at]) => waiting.set(k, at)); });
    const mine = ks.filter((k) => waiting.has(k) && !marked.has(mk(x.p, k)));
    if (!mine.length) return;
    mine.forEach((k) => marked.add(mk(x.p, k))); legacy(x.name, mine, mine.map((k) => waiting.get(k)));
    send(how, {[x.p]: mine}, x.seat);
  }
  // Every page looks at the line: every few seconds while Auto is on or a reply is read, now and then otherwise; as it
  // looks, a device whose Auto is on and able to play says when it was armed, and the board holds what lands for it
  let looking = false, more = false, nextLook = 0, poked = 0;
  async function look() {
    if (looking) { more = true; return; }
    looking = true; O.ct.call(window, nextLook);
    try {
      for (const [how, byP, seat] of owed.splice(0)) await send(how, byP, seat);
      const u = '/chat/line?dev=' + encodeURIComponent(DEV) + (listening() && armed ? '&arm=' + encodeURIComponent(new Date(armed).toISOString()) : '');
      const r = await fetch(u, {cache: 'no-store', headers: tag ? {'If-None-Match': tag} : {}}).catch(() => null);
      if (r) { const d = Date.parse(r.headers.get('Date') || ''); if (d) off = d + 500 - Date.now();
        if (r.status === 200) { const got = await r.json().catch(() => null); if (got && Array.isArray(got.line)) { L = got; tag = r.headers.get('ETag') || ''; } } }
      // what the board no longer lists is out for good: this page needn't hold it out any more
      const listed = new Set(); L.line.forEach((x) => x.b.forEach(([k]) => listed.add(mk(x.p, k))));
      [...marked].forEach((k) => { if (!listed.has(k)) marked.delete(k); });
      if (able()) put('alive', Date.now());
      if (N && !N.tapped && !playing && !N.blocks.slice(N.j).some((b) => listed.has(mk(N.p, b.k)))) { N = null; save(); }   // heard elsewhere meanwhile
      arrivals(); changed();
    } finally { looking = false; schedule(); if (more) { more = false; look(); } }
  }
  const schedule = () => { O.ct.call(window, nextLook); nextLook = after(look, aloud() || N ? 4000 : document.hidden ? 60000 : 10000); };
  // a chat whose conversation grew asks for a look at once: a reply read as it lands, not at the next look
  const poke = () => { if (Date.now() - poked < 1000) return; poked = Date.now(); look(); };
  // What lands while Auto is on joins the session, read after what is being read (the backlog waits for Play); held by a
  // pause or a let-go, it waits there quietly until the tap that resumes
  const session = () => ({want: new Set(), play: new Set(), auto: new Map(), begun: new Set()});
  let opening = 0, opened = '', replaying = null;
  function arrivals() {
    if (!aloud() || !armed) return;
    const fresh = visible().filter((x) => x.b.some(([, at]) => Date.parse(at) >= armed));
    if (!fresh.length) return;
    S = S || session();
    fresh.forEach((x) => { if (!S.want.has(id(x))) { S.want.add(id(x)); S.auto.set(id(x), Date.now()); } });
    if (!N && !opened && able() && !writing()) { const x = nextUp(); if (x) start(x); }
  }
  // the reply to read next: the first in the line the session asked for (Play, a row, Auto as it landed), by its keys;
  // the one being read comes again only for parts that landed in it since it began
  const nextUp = () => { if (!S) return null;
    return visible().find((x) => S.want.has(id(x)) && !(N && id(x) === N.id && x.b.every(([k]) => N.blocks.some((b) => b.k === k)))) || null; };
  const nextAfter = () => (S && visible().find((x) => S.want.has(id(x)) && !(N && id(x) === N.id))) || null;
  // a reply's words, fetched as it is about to be read (and the next one's, ahead)
  const words = new Map();
  const fetched = (u, as) => fetch(u, {cache: 'no-store'}).then((r) => r.ok ? r[as]() : null).catch(() => null);
  async function texts(p, i, ks) {
    const have = words.get(i), find = (k) => have.find((b) => b.k === k);
    if (have && ks.every(find)) return ks.map(find);
    const got = await fetched('/chat/text?p=' + encodeURIComponent(p) + '&k=' + encodeURIComponent(ks.join(',')), 'json');
    const bs = (got && got.blocks) || [];
    if (bs.length) { words.set(i, (have || []).filter((b) => !bs.some((c) => c.k === b.k)).concat(bs));
      while (words.size > 8) words.delete(words.keys().next().value); }
    return ks.map((k) => bs.find((b) => b.k === k)).filter(Boolean);
  }
  // Each reply in its own project's voice, its project named first as the voice comes to it (from another project, or
  // after a quiet spell; not before a ▷), as a clip of its own in its own voice or one chosen for names
  const NAME = '@@';
  const herald = () => { try { return localStorage.getItem('announcer') || ''; } catch (e) { return ''; } };
  const named = (name, tapped) => !tapped && (said !== name || Date.now() - saidAt > 120e3);
  const abs = (u) => new URL(u, location.href).href;
  const clip = (p, v) => '/voice?v=' + encodeURIComponent(p.startsWith(NAME) && herald() || v || 'am_echo') + via
    + '&t=' + encodeURIComponent(p.replace(NAME, '').trim());
  const piecesOf = (r, j) => (r.named && !j ? [NAME + r.name + '.'] : []).concat(chunks(r.blocks[j].text));
  // the clips made already: going on to one of them is no wait, so the button stays as it is
  const ready = new Set();
  // A clip made ahead is downloaded whole and kept here as a file of the page's own (a phone's player would fetch
  // it again over the network otherwise), so going on to it needs nothing from the network: no wait between clips.
  const kept = new Map(), failed = new Set();      // the clip's address -> the page's own copy; those not made
  const keyOf = (u) => u.replace('&auto=1', '');  // a clip is the same clip, read by itself or on a tap
  const make = (p, v) => { const u = abs(clip(p, v)), key = keyOf(u);
    if (kept.has(key)) return Promise.resolve();
    return fetch(u).then((r) => r.ok ? r.blob() : null).then((b) => { if (!b) { failed.add(key); return; }
      kept.set(key, URL.createObjectURL(b)); ready.add(key);
      if (kept.size > 24) { const [old, o] = kept.entries().next().value; URL.revokeObjectURL(o); kept.delete(old); } })
      .catch(() => { failed.add(key); }); };
  // Ahead of what is heard, the next AHEAD clips are made, through this reply and into the next once its words are
  // here (the machine makes them back to back with no idle time), topped up as each clip starts. While the clip about to
  // be heard is still being made over the network it waits, so that one comes first. A clip that failed isn't asked
  // for again (the phone's voice reads it).
  const AHEAD = 5;
  let warming = false;
  const upcoming = () => {
    const out = []; if (!N) return out;
    let k = playing && now && now.s === N.blocks[N.j] ? now.at + 1 : N.piece;
    for (let j = N.j; j < N.blocks.length && out.length < AHEAD; j++, k = 0) {
      const ps = piecesOf(N, j).filter((p) => p.trim());
      for (; k < ps.length && out.length < AHEAD; k++) out.push([ps[k], N.kvoice]); }
    const x = out.length < AHEAD && nextAfter(), info = x ? L.projects[x.p] || {} : {}, bs = x && words.get(id(x));
    if (bs) { const r = {name: info.name, named: named(info.name, false), blocks: bs};
      bs.forEach((b, j) => piecesOf(r, j).filter((p) => p.trim()).forEach((p) => { if (out.length < AHEAD) out.push([p, info.voice]); })); }
    return out; };
  function warm() {
    if (warming || (playing && loading)) return;
    const next = upcoming().find(([p, v]) => { const key = keyOf(abs(clip(p, v))); return !kept.has(key) && !failed.has(key); });
    if (!next) return;
    warming = true;
    make(next[0], next[1]).then(() => { warming = false; warm(); });
  }
  player.addEventListener('playing', () => { if (!player.loop) ready.add(keyOf(player.src)); if (loading && playing && !player.loop) { loading = false; changed(); } setState(); });
  // Locked, a phone runs a page only while it plays something, and lets it start a sound again only from one going: so
  // the element never stops between clips or replies. Each clip is set going as the last ends, and any wait between (a
  // reply's words fetched, its name made, its chat opened) is bridged with silence; between replies Auto plays silence,
  // as a music app goes on to the next song, so the next reply is heard as it lands (the person, 2026-10-28: "when I
  // lock my phone, the audio keeps playing which is great ... anything you can do to support that kind of usage")
  let ours = 0, resting = 0;
  const hush = (keep) => { if (talk) talk.cancel(); if (!keep) { ours = Date.now(); player.pause(); player.loop = false; resting = 0; }
    player.onended = player.onerror = null; paused = false; loading = false; };
  const SILENT = 'data:audio/wav;base64,UklGRqQMAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YYAMAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==';
  const silent = () => player.loop && player.src === SILENT;
  const bridge = () => { if (writing() || !unlocked || (silent() && !player.paused)) return;
    player.onended = player.onerror = null; player.loop = true; player.src = SILENT; player.play().catch(() => {}); };
  // the lock screen and Android's notification as a song is: what is being read, with pause and skip; between replies
  // "Waiting for replies"; and playing, held or nothing, as the pill says
  const tell = (text) => { try { if (ms && window.MediaMetadata) ms.metadata = new MediaMetadata(N && text
    ? {title: text.slice(0, 90), artist: N.name || 'colony', album: 'colony'} : {title: 'Waiting for replies', artist: 'colony', album: 'colony'}); } catch (e) {} };
  const setState = () => { try { if (ms) ms.playbackState = N ? (playing && !paused ? 'playing' : 'paused')
    : resting && !letGo ? 'playing' : aloud() && letGo ? 'paused' : 'none'; } catch (e) {} };
  let lastStop = null, stops = 0;
  const restStop = (why) => { lastStop = {why, at: new Date().toISOString().slice(11, 19), hidden: hidden()}; log('let go', why);
    if (stops++ < 4) measure({when: 'rest-stop', why, hidden: hidden(), aloud: aloud(), path: location.pathname + location.search}); };
  const rest = (cause, then) => {
    if (writing() || (letGo && letGo.why === 'paused') || !aloud() || N || !L.ready) { if (then) then(false); return; }
    const stopped = letGo, current = () => resting && !N && letGo === stopped;
    player.onended = player.onerror = null; player.loop = true;
    if (player.src !== SILENT) player.src = SILENT;
    resting = resting || Date.now(); tell(); setState();
    player.play().then(() => { if (!current()) return;
      unlocked = true; if (letGo) { log('silence', cause, true); letGo = null; } if (then) then(true); changed(); })
      .catch((err) => { if (!current() || (err && err.name === 'AbortError')) return;
        resting = 0; player.loop = false; letGo = {why: 'refused', at: Date.now()}; stopAt = stopAt || Date.now();
        restStop('play() refused: ' + cause); if (then) then(false); changed(); }); };
  // After two quiet hours it lets go (the window a tap resumes in): long enough for a locked phone to keep Auto through a
  // long piece of work. A pause, or Auto turned off, lets go at once.
  O.si.call(window, () => { if (resting && Date.now() - resting > LONG) { ours = Date.now(); player.pause(); player.loop = false; resting = 0;
    letGo = {why: 'two quiet hours', at: Date.now()}; stopAt = Date.now(); restStop('two quiet hours'); changed(); } }, 30e3);
  // a tap lets the phone play: a moment of silence, played within it, and from then on this page may sound by itself
  const prime = () => { if (writing()) return;
    if (talk && !unlocked) talk.speak(new SpeechSynthesisUtterance(''));
    if ((N && playing) || resting) return;
    player.loop = false; player.src = SILENT;
    player.play().then(() => { unlocked = true; if (letGo && letGo.why !== 'paused') letGo = null; changed(); }).catch(() => {}); };
  const reading = (s) => playing && !paused && N && N.blocks[N.j] === s;
  // a play() the phone refused: held where it is, the pill or the bar saying tap to resume, never a silent pause
  const refused = (what, cause) => { if (!N) return; paused = true; why = 'phone'; letGo = {why: 'refused', at: Date.now()}; stopAt = stopAt || Date.now();
    unlocked = false; log(what, cause, false); save(); changed(); };
  const sayK = (j, parts, from) => {
    let n = from;
    const s = N.blocks[j], mine = () => reading(s);
    const next = () => {
      if (!mine()) return;
      if (n >= parts.length) return done(N.j);
      now = {s, parts, at: n}; N.piece = n; sent = -1; save(); changed();
      const u = abs(clip(parts[n], N.kvoice));
      player.loop = false; player.src = kept.get(keyOf(u)) || u; player.playbackRate = pace(); player.defaultPlaybackRate = pace();
      n += 1;
      // refused for want of a tap on this page: held there, tap to resume; cut short by a pause, a skip or the next clip
      // is no refusal, and nothing is read again; a clip that failed is read by the phone's own voice
      player.play().then(() => { unlocked = true; }).catch((err) => { if (err && err.name === 'AbortError') return;
        if (err && err.name === 'NotAllowedError' && mine()) { N.piece = n - 1; return refused('play', 'a clip'); }
        if (mine() && now && now.at === n - 1) { hush(); sayPhone(N.j, parts, n - 1); } });
    };
    player.onended = next;
    player.onerror = () => { const err = player.error;                  // the phone's voice then, for a clip that failed
      if (!err || err.code === 1 || !mine()) return;   // 1: stopped on purpose, nothing failed
      hush(); sayPhone(N.j, parts, Math.max(0, n - 1)); };
    next();
  };
  const sayPhone = (j, parts, from) => {
    if (!talk) { playing = false; changed(); return; }
    playing = true; changed();
    // a phone that won't speak before a tap on the page says nothing and starts nothing: tap to resume
    let began = false;
    const s = N.blocks[j], mine = () => reading(s);
    after(() => { if (mine() && !began && !talk.speaking) { talk.cancel(); refused('speak', "the phone's voice"); } }, 2500);
    parts.slice(from).forEach((p, n) => { const x = from + n, u = new SpeechSynthesisUtterance(p.replace(NAME, '').trim()); if (voice) u.voice = voice; u.rate = pace();
      u.onstart = () => { began = true; if (!mine()) return; now = {s, parts, at: x}; N.piece = x; save(); loading = false; changed();
        if (!p.startsWith(NAME)) { sent = 0; follow(s.k, sentences(p)[0]); } };
      u.onboundary = (ev) => { if (!mine() || p.startsWith(NAME)) return; const ss = sentences(p); let had = 0, i = 0;
        ss.forEach((y, m) => { if (ev.charIndex >= had) i = m; had += y.length + 1; }); if (i !== sent) { sent = i; follow(s.k, ss[i]); } };
      if (x === parts.length - 1) u.onend = () => { if (mine()) done(N.j); };
      talk.speak(u); });
  };
  // Audio + text: as each sentence is about to be heard, its chat shows it (a page turn where it runs off what shows)
  // and marks it softly (the person, 2026-10-28: "the specific word that is being read by the audio is visible on the
  // screen"). Kokoro gives no word times: after the clip's fifth of a second of quiet, each sentence starts at its share
  // of the clip's letters of what is left, read off the element's own time, so the speed doesn't matter.
  const LEAD = 0.2;
  const follow = (k, text) => { if (view && view.follow) view.follow(k, text); };
  const heedTime = () => {
    if (!N || !playing || paused || !now || player.loop || N.silent || !N.kokoro) return;
    const p = now.parts[now.at]; if (!p || p.startsWith(NAME)) return;
    const ss = sentences(p), d = player.duration, c = player.currentTime + 0.25;   // a moment ahead: about to be heard
    let i = 0;
    if (isFinite(d) && d > LEAD && ss.length > 1) { const all = ss.reduce((m, x) => m + x.length, 0); let had = 0;
      ss.forEach((x, m) => { if (c >= LEAD + had / all * (d - LEAD)) i = m; had += x.length; }); }
    if (i === sent) return;
    sent = i; follow(now.s.k, ss[i]); };
  player.addEventListener('timeupdate', heedTime); player.addEventListener('playing', heedTime);
  // a part heard to its end: marked heard, on to the next part; the last, on to the next reply
  const done = (j) => { if (!N || N.j !== j) return;
    markParts(N, [N.blocks[j].k], 'heard');
    if (j + 1 < N.blocks.length) return begin(j + 1, 0);
    replyDone(true); };
  // a reply done (heard, skipped, cleared): back to the line's reading a ▷ stepped in front of, or on to the next the
  // session asked for; none, and the bar folds back into the pill, Auto waiting in silence for the next to land
  function replyDone(finished = false) {
    // Finishing the old reply is not a new choice: a replay whose words are loading still comes next. Skip and Clear cancel it.
    const waiting = finished && replaying && replaying.opening === opening;
    if (!waiting) { opening += 1; opened = ''; replaying = null; }
    startingSound = null; resumeAfterWriting = null; byVoice = false;
    follow(null); bridge(); playing = false; paused = false; why = null; now = null; N = null;
    if (waiting) { save(); return changed(); }
    if (back) { const b = back; back = null; N = b.N; if (b.sounding) return begin(N.j, N.piece); hush(); save(); return changed(); }
    const x = nextUp();
    if (x) return start(x);
    S = null; save(); changed();
    if (aloud()) rest('between replies'); else hush();
  }
  async function start(x) {
    if (writing()) { quietSoon(); return; }         // it stays in S until the box and its grace period let go
    const mine = ++opening, i = id(x), info = L.projects[x.p] || {};
    opened = i; replaying = null;
    // what Auto took up as it landed is read from what landed; what Play or a row asked for, all of it that waits
    const fresh = S && S.auto.has(i) && !S.play.has(i) ? x.b.filter(([, at]) => Date.parse(at) >= armed) : [];
    const ks = (fresh.length ? fresh : x.b).map(([k]) => k);
    bridge(); loading = true; changed();
    const blocks = await texts(x.p, i, ks);
    if (mine !== opening) return;
    opened = ''; loading = false;
    if (!blocks.length) { if (S) S.want.delete(i); return replyDone(); }      // its words couldn't be had: on to the next
    N = {id: i, p: x.p, t: x.t, name: info.name || '', href: info.href || '', kvoice: info.voice || 'am_echo', kokoro: !!L.ready,
      blocks, j: 0, piece: 0, seat: S && S.auto.has(i) && !S.play.has(i) ? 'auto' : 'line'};
    if (S) S.begun.add(i);
    if (letGo && letGo.why === 'paused') { playing = false; paused = true; why = 'person'; save(); return changed(); }
    begin(0, 0);
  }
  // A part begins: shown in its chat as it is heard (Audio + text: the page goes there, 3 s at most to wait, and the
  // next reply's page and words are fetched meanwhile, so going on is at once), its project named first where the voice
  // comes to it: the name waits until the first clip of the message is made too, so the two run on with no gap (the
  // person: "a delay before first message ... no audio name reading start until message 1 is ready"); 20 s at most
  function begin(j, from = 0) {
    const s = N && N.blocks[j]; if (!s) return replyDone();
    startingSound = null;
    if (writing()) {
      N.j = j; N.piece = from; playing = false; paused = true; loading = false; why = 'dictation'; byVoice = true;
      save(); changed(); quietSoon(); return;
    }
    // Text only holds for whatever Auto reads; a reply the person tapped ▷ on is read aloud
    N.silent = !N.tapped && aloud() && mode() === 'text';
    if (!from && !j) N.named = !!N.say || named(N.name, N.tapped);
    const parts = piecesOf(N, j).filter((p) => p.trim());
    from = Math.min(from, Math.max(0, parts.length - 1));
    hush(true); N.j = j; N.piece = from; playing = true; paused = false; why = null; now = null; sent = -1; said = N.name; saidAt = Date.now();
    loading = !N.silent && !(N.kokoro && parts[from] && ready.has(keyOf(abs(clip(parts[from], N.kvoice))))); save(); changed();
    if (!parts.length) return done(j);
    const shown = showHere(), x = j + 1 >= N.blocks.length && nextAfter();
    if (x) { const h = (L.projects[x.p] || {}).href; if (h && following() && x.p !== N.p) colonyNav.prefetch(h);
      texts(x.p, id(x), x.b.map(([k]) => k)); }
    if (N.silent) { hush(); return changed(); }
    tell(parts[from].replace(NAME, ''));
    const pending = startingSound = {reply: N, ready: false, go: () => {
      if (startingSound !== pending || !reading(s)) return;
      if (writing()) { dictating(); return; }
      startingSound = null;
      return N.kokoro ? sayK(j, parts, from) : sayPhone(j, parts, from);
    }};
    const sound = () => { pending.ready = true; return pending.go(); };
    const titled = N.kokoro && parts[from] && parts[from].startsWith(NAME) && parts[from + 1];
    const both = titled ? Promise.race([Promise.all([make(parts[from], N.kvoice), make(parts[from + 1], N.kvoice)]), new Promise((ok) => after(ok, 20000))]) : null;
    const waits = [shown && Promise.race([shown, new Promise((ok) => after(ok, 3000))]), both].filter(Boolean);
    if (waits.length) { bridge(); return Promise.all(waits).then(sound); }
    return sound();
  }
  // ❚❚ holds the reading where it is, ▶ takes it up from there; held in the silence between replies, Auto lets go at once
  function hold(want, cause = 'person') {
    if (replaying && replaying.opening === opening && cause !== 'dictation') replaying.pause = want ? cause : null;
    if (want && cause !== 'dictation') resumeAfterWriting = null;
    if (!want && writing()) { resumeAfterWriting = {reply: N, cause}; quietSoon(); return; }
    if (!N) {
      if (!want) return resume(cause);
      if (aloud()) { ours = Date.now(); player.pause(); player.loop = false; resting = 0; letGo = {why: 'paused', at: Date.now()}; stopAt = Date.now(); log('pause', cause); }
      return changed(); }
    if (!playing) { if (!want) { byVoice = false; takeUp(); prime(); begin(N.j, N.piece); }
      else if (cause !== 'dictation') { why = cause; byVoice = false; save(); changed(); } return; }
    if (want) { if (paused) { if (cause !== 'dictation') { why = cause; byVoice = false; save(); changed(); } return; }
      paused = true; why = cause; stopAt = Date.now(); follow(null); log('pause', cause); save();
      if (!N.silent) { if (N.kokoro && !(talk && talk.speaking)) { ours = Date.now(); player.pause(); } else if (talk) talk.pause(); }
      return changed(); }
    if (!paused) return;
    takeUp(); paused = false; why = null; byVoice = false; sent = -1; save();
    if (N.silent) return changed();
    if (startingSound && startingSound.reply === N) { if (startingSound.ready) startingSound.go(); return changed(); }
    if (N.kokoro && !(talk && talk.speaking)) player.play().then(() => { unlocked = true; log('resume', cause, true); changed(); })
      .catch((err) => { if (!err || err.name !== 'AbortError') refused('resume', cause); });
    else if (talk && talk.paused) talk.resume(); else begin(N.j, N.piece);
    changed();
  }
  // the tap that resumes: the reading it held goes on, then what landed meanwhile, if the stop began under two hours ago;
  // past that Auto is armed from now, and what came waits in the line for Play
  function takeUp() {
    if (stopAt && Date.now() - stopAt >= LONG) {
      if (S) [...S.auto].forEach(([i, t]) => { if (t >= stopAt && !S.play.has(i) && !(N && N.id === i)) { S.want.delete(i); S.auto.delete(i); } });
      if (aloud()) arm();
      log('armed again', Math.round((Date.now() - stopAt) / 60e3) + ' min stopped');
    }
    stopAt = 0; letGo = null;
  }
  function resume(cause) {
    if (writing()) { resumeAfterWriting = {reply: N, cause}; quietSoon(); return; }
    takeUp();
    if (aloud() && L.ready) rest(cause, (ok) => { log('resume', cause, ok); if (ok) look(); }); else prime();
    changed(); }
  // ⏭: on to the next reply, the rest of this one counted skipped (the same move in the bar and on the lock screen)
  function skip() { if (!N) return; markParts(N, N.blocks.slice(N.j).map((b) => b.k), 'skipped'); log('skip', N.name); replyDone(); }
  // The phone stopped the sound itself. While a box has the keyboard, dictation took it (iOS gives the sound to the
  // microphone): the dictation hold, taken up after the box lets go. Otherwise a call, the lock screen,
  // another app's audio: held, and taken up again on coming back to the page where the phone lets it.
  player.addEventListener('pause', () => {
    if (Date.now() - ours < 600 || player.ended) return;
    if (player.loop && resting) { if (aloud()) { resting = 0; player.loop = false; letGo = {why: 'phone', at: Date.now()}; stopAt = Date.now();
      restStop('the phone stopped the silence'); changed(); } return; }
    if (!playing || paused || !N) return;
    paused = true; loading = false; stopAt = Date.now(); follow(null);
    if (boxed()) { why = 'dictation'; byVoice = true; quietSoon(); log('pause', 'the phone, a box being written in'); }
    else { why = 'phone'; letGo = {why: 'phone', at: Date.now()}; log('pause', 'the phone'); }
    save(); changed(); });
  // Writing is a hold even with no reply playing yet. Focus covers an iOS microphone waiting for words; composition
  // and every input cover short updates without key events. Only after all of them end, plus 2.2 seconds, may a new
  // part or reply begin. Send's capture handler runs before the form clears and blurs, so it cannot release this hold.
  let byVoice = false, quiet = 0, writingUntil = 0;
  const composing = new Set();
  const isBox = (t) => !!(t && t.matches && t.matches('textarea, input[type=text], input:not([type])'));
  const boxed = () => isBox(document.activeElement);
  const writing = () => boxed() || composing.size > 0 || Date.now() < writingUntil;
  const goOn = (cause) => {
    O.ct.call(window, quiet);
    if (writing()) return quietSoon();
    const resume = resumeAfterWriting; resumeAfterWriting = null;
    if (resume && resume.reply === N) return hold(false, resume.cause);
    if (byVoice) { byVoice = false; if (N && paused && why === 'dictation') hold(false, cause); }
    if (!N && !opened && S && (able() || S.play.size) && !(letGo && letGo.why === 'paused')) {
      const x = nextUp(); if (x) start(x);
    }
  };
  const quietSoon = () => { O.ct.call(window, quiet); quiet = after(() => goOn('the box let go'), Math.max(1, writingUntil - Date.now(), 2200)); };
  const dictating = () => {
    if (N && playing && !paused) { hold(true, 'dictation'); byVoice = true; }
    if (!N && resting) { ours = Date.now(); player.pause(); player.loop = false; resting = 0; changed(); }
    quietSoon();
  };
  const writingChanged = (ev) => {
    if (!isBox(ev.target)) return;
    if (ev.type === 'compositionstart' || ev.type === 'compositionupdate' || ev.isComposing) composing.add(ev.target);
    if (ev.type === 'compositionend' || ev.type === 'focusout') composing.delete(ev.target);
    writingUntil = Date.now() + 2200; dictating();
  };
  ['focusin', 'focusout', 'keydown', 'beforeinput', 'input', 'compositionstart', 'compositionupdate', 'compositionend']
    .forEach((t) => on(document, t, writingChanged, true));
  // A reading shown as playing whose sound has stood still for a few seconds (stopped by the phone, a clip that ended
  // without saying so) is set going again, on to the next clip where this one ended; each time goes to the board's log
  // with what the phone said, so the cause is read from the phone's own numbers.
  let stood = 0, stalls = 0;
  O.si.call(window, () => {
    if (!playing || paused || loading || !N || N.silent || !N.kokoro || silent() || document.hidden || !(player.paused || player.ended)) { stood = 0; return; }
    if (!stood) { stood = Date.now(); return; }
    if (Date.now() - stood < 4000) return;
    stood = 0;
    if (stalls++ < 6) measure({when: 'stall', ended: player.ended, paused: player.paused, state: player.readyState, net: player.networkState,
      error: player.error && player.error.code, t: player.currentTime, d: player.duration, reply: N.id, part: N.j, of: N.blocks.length,
      piece: now && now.at, blob: player.src.startsWith('blob:')});
    if (player.ended && player.onended) player.onended();
    else player.play().catch(() => { if (playing && N && now) begin(N.j, now.at); });
  }, 2000);
  // Back on the page: what the phone stopped is taken up again where it lets it (a pause the person chose stays), and a
  // try that fails says tap to resume, never a ring
  on(document, 'visibilitychange', () => {
    if (document.hidden) { hidAt = Date.now(); report(); return; }
    hidAt = 0; look();
    if (N && playing && paused && why === 'phone') return hold(false, 'back on the page');
    if (!N && aloud() && letGo && letGo.why !== 'paused') return rest('back on the page', (ok) => { log('resume', 'back on the page', ok); if (ok) { takeUp(); look(); } });
    if (N && playing && !paused && N.kokoro && !N.silent) { const at = player.currentTime;
      after(() => { if (N && playing && !paused && !player.ended && player.currentTime === at)
        player.play().catch((err) => { if (!err || err.name !== 'AbortError') refused('play', 'stood still on coming back'); }); }, 700); }
  });
  // A phone plays a page's audio only after a tap on it: a tap anywhere (Send included) plays a moment of silence, and
  // from then on new replies may be read by themselves; where Auto was let go, it is the tap that resumes
  on(document, 'pointerdown', (ev) => { if (!aloud() || (ev.target.closest && ev.target.closest('#cplayer'))) return;
    if (writing()) return;                        // a Send tap belongs to the box, never a request to resume
    if (letGo && !isBox(ev.target)) { if (!N) return resume('a tap'); if (playing && paused && why !== 'person') return hold(false, 'a tap'); }
    prime(); }, true);
  if (ms) { [['pause', () => hold(true, 'lock screen')], ['play', () => hold(false, 'lock screen')], ['nexttrack', () => skip()],
    ['stop', () => hold(true, 'lock screen')], ['previoustrack', null]].forEach(([a, h]) => { try { ms.setActionHandler(a, h); } catch (e) {} }); }
  // Audio + text, Text only: as a part starts the page shows it, in its project's Chat (changed in place, the voice going
  // on), unless the person is typing or touched the page just now (it looks again once they have let the page be, while
  // that part still plays: the person, Oct 27)
  let touched = 0, looking2 = 0;
  on(document, 'pointerdown', (ev) => { if (!ev.target.closest('#cplayer')) touched = Date.now(); }, true);
  const later = (s, wait) => { clearTimeout(looking2);
    looking2 = setTimeout(() => { if (reading(s)) showHere(); }, wait); };
  function showHere() {
    if (!N || !following()) return null;
    const s = N.blocks[N.j]; if (!s) return null;
    if (view && view.name === N.name) { view.focus(s.k, true); return null; }
    // never away from something being written: a box with words in it, or one being typed in, keeps the page
    const box = document.body.classList.contains('gcon') ? null : document.querySelector('#composer textarea'), at = document.activeElement;
    if (!N.href || document.body.classList.contains('typing') || (box && box.value.trim()) || (at && at.matches && at.matches('input, textarea, select')))
      return null;
    if (Date.now() - touched < 8000) { later(s, 8200 - (Date.now() - touched)); return null; }
    return colonyNav.go(N.href);
  }
  // A new voice or speed takes effect at once: Kokoro's speed on the clip playing; otherwise from the piece being heard.
  const anew = (voiceChanged) => {
    if (!playing || !now || !N || now.s !== N.blocks[N.j]) return;
    if (N.kokoro && !voiceChanged) { player.playbackRate = pace(); player.defaultPlaybackRate = pace(); return; }
    const {parts, at} = now;
    if (paused) { const reason = why; hush(); playing = false; paused = true; why = reason; N.piece = at; save(); changed(); return; }   // ▶ takes it up there, in the new voice
    hush(true); playing = true; changed();
    if (N.kokoro) sayK(N.j, parts, at); else sayPhone(N.j, parts, at);
  };
  // Text only, or sound again, chosen while Auto reads: at once, on the part being read (the person: picking Text only,
  // it went on with audio and text)
  function heed() { if (!N || !playing || paused || N.tapped || !aloud() || !!N.silent === (mode() === 'text')) return;
    if (!N.silent) hush(); begin(N.j, N.piece); }
  // A voice chosen is heard at once, saying its name and a sentence that ranges across the sounds of speech (a
  // question, an exclamation, numbers, a time), so voices are compared by ear. What was being read pauses, and stays
  // paused: ▶ takes it up again.
  const sample = new Audio();
  const SAMPLE = @SAMPLE@;                        // colony/voice.py's: each voice's is made ahead and kept
  function preview(v, label) {
    if (!v || writing()) return;
    if (playing && !paused) hold(true, 'a voice tried');
    if (resting) { ours = Date.now(); player.pause(); player.loop = false; resting = 0; }
    sample.src = '/voice?v=' + encodeURIComponent(v) + '&t=' + encodeURIComponent((label || '') + '. ' + SAMPLE);
    sample.playbackRate = sample.defaultPlaybackRate = pace();
    sample.play().catch(() => {});
  }
  // The player, over every page above the box and the tabs (R123, the person's pick: "Replies you haven't heard wait in
  // a line. Play goes through the line, and Auto keeps it going as more arrive"). At rest a headphones pill with how
  // many replies wait: a ring and "Auto · 5" while Auto is on and able to play, "Auto · tap to resume" where the phone
  // let the sound go. A tap opens the line. Reading, a bar: whose reply and where in the line, its first words, ❚❚ and ⏭.
  const PICON = @ICONS@;
  const HEAD = "<svg viewBox='0 0 24 24' width='18' height='18' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' aria-hidden='true'>"
    + "<path d='M4 14v-2a8 8 0 0 1 16 0v2'/><rect x='3' y='14' width='5' height='6' rx='2'/><rect x='16' y='14' width='5' height='6' rx='2'/></svg>";
  const bar = document.createElement('div'); bar.id = 'cplayer'; bar.className = 'cplayer cpidle'; bar.hidden = true;
  bar.innerHTML = "<div class='cpminirow'><button type='button' class='cppill' aria-haspopup='dialog'>" + HEAD + "<span class='cpsay'></span></button></div>"
    + "<div class='cpbar'><button type='button' class='cpwhat' aria-label='The line'><b class='cpname'></b><span class='cpprev'></span></button>"
    + "<button type='button' class='cphold'></button><button type='button' class='cpnext' aria-label='Skip to the next reply'>" + PICON.skip + "</button></div>"
    + "<div class='cpshade' hidden></div><div class='cpsheet' role='dialog' aria-label='The unheard line' hidden><button type='button' class='cpgrab' aria-label='Close'></button>"
    + "<div class='cpmain'><div class='cpchips'></div><ol class='cprows'></ol><p class='cpempty' hidden></p>"
    + "<div class='cpacts'><button type='button' class='cpplay'></button><button type='button' class='cpclear'>Clear</button></div>"
    + "<div class='cpundo' hidden><span></span><button type='button' class='cpundobtn'>Undo</button></div>"
    + "<label class='cpsw'><span class='cptxt'><b>Auto</b><span>Reads new replies as they land, from every project</span></span>"
    + "<input type='checkbox' class='cpauto' role='switch'><span class='cptoggle' aria-hidden='true'></span></label>"
    + "<button type='button' class='cplink cpsetopen'>Voice, speed, mode<span>›</span></button>"
    + "<button type='button' class='cplink cpheardopen'>Heard<span>›</span></button></div>"
    + "<div class='cpset' hidden><button type='button' class='cpback'>‹ The line</button><div class='cplbl'>Reads as</div>"
    + "<div class='cpseg'><button type='button' data-m='both'>Audio + text</button><button type='button' data-m='audio'>Audio only</button>"
    + "<button type='button' data-m='text'>Text only</button></div><p class='cpmodesay'></p>"
    + "<label class='cpline'>Speed <select class='cprate'>" + [1, 1.25, 1.5, 1.75].map((r) => "<option value='" + r + "'>" + r + "×</option>").join('') + "</select></label>"
    + "<div class='cpvoice' hidden><label class='cpline'>Voice of <select class='cpwhom'></select></label>"
    + "<label class='cpline'>Voice <select class='cpkvoice'></select></label>"
    + "<label class='cpline'>Names said in <select class='cpherald'></select></label></div></div>"
    + "<div class='cpheard' hidden><button type='button' class='cpback'>‹ The line</button><ol class='cprows cpheardrows'></ol></div></div>";
  const $ = (s) => bar.querySelector(s), pill = $('.cppill'), sheet = $('.cpsheet'), shade = $('.cpshade'), rows = $('.cpmain .cprows');
  const MODESAY = {both: "Read aloud, and the page goes with it, into each project's chat", audio: 'Read aloud; the page stays where you are',
    text: 'No sound: each reply shown in its chat, and ⏭ goes on'};
  const first = (t, n) => { const w = String(t || '').replace(/\\s+/g, ' ').trim().split(' '); return w.slice(0, n).join(' ') + (w.length > n ? '…' : ''); };
  const when = (at) => { const d = new Date(at); if (isNaN(d)) return '';
    const clock = d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
    return d.toDateString() === new Date().toDateString() ? clock : d.toLocaleDateString([], {month: 'short', day: 'numeric'}) + ', ' + clock; };
  // the chips filter what the line shows and what Play plays, never what Auto reads: Auto is every project's
  let chipName = get('linechip') || '';
  const chipOf = (xs) => { const p = Object.keys(L.projects).find((k) => L.projects[k].name === chipName); return p && xs.some((x) => x.p === p) ? p : ''; };
  const shownLine = () => { const xs = visible(), p = chipOf(xs); return p ? xs.filter((x) => x.p === p) : xs; };
  // the line starts empty when it goes live: said plainly, in its first days
  const begun = () => { const t = Date.parse(L.since); return t && Date.now() - t < 3 * 86400e3 ? ' The line began ' + when(L.since) + ': replies from before then count as heard.' : ''; };
  const row = (x, info) => { const li = document.createElement('li'); li.dataset.id = id(x);
    const a = document.createElement('span'); a.className = 'cpat'; a.textContent = when(x.at);
    const w = document.createElement('span'); w.className = 'cpwords'; const b = document.createElement('b'); b.textContent = (info && info.name) || '';
    w.append(b, document.createTextNode(' ' + x.w)); li.append(a, w); return li; };
  let drawnSheet = '';
  function drawSheet() {
    if (sheet.hidden) return;
    const xs = visible(), p = chipOf(xs), shown = p ? xs.filter((x) => x.p === p) : xs, counts = {};
    xs.forEach((x) => { counts[x.p] = (counts[x.p] || 0) + 1; });
    const key = [p, N && N.id, xs.map((x) => id(x) + ':' + x.b.length).join(','), L.since].join('|');
    if (key !== drawnSheet) { drawnSheet = key;
      const chip = (k, label) => { const b = document.createElement('button'); b.type = 'button'; b.dataset.p = k; b.textContent = label;
        if (k === p) b.className = 'on'; return b; };
      $('.cpchips').replaceChildren(chip('', 'All · ' + xs.length), ...Object.keys(counts).map((k) => chip(k, ((L.projects[k] || {}).name || k) + ' · ' + counts[k])));
      rows.replaceChildren(...shown.map((x) => { const li = row(x, L.projects[x.p]); if (N && N.id === id(x)) li.className = 'now'; return li; }));
      $('.cpempty').hidden = !!xs.length; rows.hidden = $('.cpacts').hidden = $('.cpchips').hidden = !xs.length;
      $('.cpempty').textContent = xs.length ? '' : 'Nothing waiting.' + begun();
      const label = document.createElement('span'); label.textContent = p ? 'Play ' + ((L.projects[p] || {}).name || '') + ' · ' + shown.length : 'Play all ' + shown.length;
      $('.cpplay').innerHTML = PICON.play; $('.cpplay').appendChild(label);
    }
    $('.cpauto').checked = aloud();
    const u = get('lineundo'), live = !!(u && Date.now() - u.at < 600e3);
    $('.cpundo').hidden = !live; if (live) $('.cpundo span').textContent = 'Cleared ' + u.n + (u.n === 1 ? ' reply' : ' replies');
  }
  const place = () => { if (!S || !N || N.tapped || !S.begun.has(N.id)) return '';
    const left = visible().filter((x) => S.want.has(id(x)) && !S.begun.has(id(x))).length;
    return ' · ' + S.begun.size + ' of ' + (S.begun.size + left); };
  const draw = () => {
    bar.hidden = false;
    const n = visible().length, live = aloud() && able(), held2 = aloud() && !able();
    document.documentElement.classList.toggle('autoon', live);              // Auto on and able: its ring
    document.documentElement.classList.toggle('cpgo', !!N && playing && !paused);      // sounding now
    bar.classList.toggle('cpidle', !N); document.body.classList.toggle('cpon', !!N);
    pill.classList.toggle('on', live); pill.classList.toggle('held', held2); pill.classList.toggle('none', !aloud() && !n);
    const say = aloud() ? (held2 ? 'Auto · tap to resume' : 'Auto · ' + n) : String(n);
    if ($('.cpsay').textContent !== say) $('.cpsay').textContent = say;
    pill.setAttribute('aria-label', held2 ? 'Auto is on, but the phone let the sound go: tap to resume'
      : n + (n === 1 ? ' reply' : ' replies') + ' waiting' + (live ? ', Auto on' : '') + ': open the line');
    if (N) {
      const s = N.blocks[N.j] || N.blocks[0] || {text: ''};
      $('.cpname').textContent = (N.name || '') + place() + (!playing || paused ? (letGo ? ' · tap to resume' : ' · paused') : '');
      bar.classList.toggle('cppaused', !playing || paused); bar.classList.toggle('cpheld', !!letGo);
      $('.cpprev').textContent = first(s.text, 12);
      const st = playing && !paused ? (loading ? 'load' : 'pause') : 'play';
      const hb = $('.cphold'); if (hb.dataset.on !== st) { hb.dataset.on = st; hb.innerHTML = PICON[st]; hb.setAttribute('aria-label', st === 'play' ? 'Play' : 'Pause'); }
    }
    setState(); drawSheet(); placed(); };
  const openSheet = () => { sheet.hidden = shade.hidden = false; $('.cpmain').hidden = false; $('.cpset').hidden = $('.cpheard').hidden = true;
    drawnSheet = ''; draw(); look(); };
  const shutSheet = () => { sheet.hidden = shade.hidden = true; };
  on(pill, 'click', () => { if (aloud() && !able()) return resume('the pill'); openSheet(); });
  on($('.cpwhat'), 'click', openSheet);
  on(shade, 'click', shutSheet); on($('.cpgrab'), 'click', shutSheet);
  on(document, 'keydown', (ev) => { if (ev.key === 'Escape' && !sheet.hidden) shutSheet(); });
  on($('.cphold'), 'click', () => hold(playing ? !paused : false));
  on($('.cpnext'), 'click', skip);
  on($('.cpchips'), 'click', (ev) => { const b = ev.target.closest('button'); if (!b) return;
    chipName = b.dataset.p ? (L.projects[b.dataset.p] || {}).name || '' : ''; put('linechip', chipName || null); drawnSheet = ''; draw(); });
  on(rows, 'click', (ev) => { const li = ev.target.closest('li'); if (!li) return;
    const xs = shownLine(), i = xs.findIndex((x) => id(x) === li.dataset.id); if (i >= 0) playFrom(xs, i); });
  on($('.cpplay'), 'click', () => playFrom(shownLine(), 0));
  on($('.cpclear'), 'click', () => clearShown());
  on($('.cpundobtn'), 'click', () => undoClear());
  on($('.cpauto'), 'change', () => setAloud($('.cpauto').checked));
  on($('.cpsetopen'), 'click', () => { $('.cpmain').hidden = true; $('.cpset').hidden = false; sets(); });
  on($('.cpheardopen'), 'click', () => { $('.cpmain').hidden = true; $('.cpheard').hidden = false; heardList(); });
  bar.querySelectorAll('.cpback').forEach((b) => on(b, 'click', () => { $('.cpset').hidden = $('.cpheard').hidden = true; $('.cpmain').hidden = false;
    drawnSheet = ''; draw(); }));
  on($('.cpseg'), 'click', (ev) => { const b = ev.target.closest('button'); if (b) { P.setMode(b.dataset.m); sets(false); } });
  // Play: the line as the chips show it, from its first reply or the row tapped, and on down it
  function playFrom(xs, i) {
    if (!xs[i]) return;
    opening += 1; opened = ''; replaying = null; startingSound = null; resumeAfterWriting = null; byVoice = false;
    S = session(); xs.slice(i).forEach((x) => { S.want.add(id(x)); S.play.add(id(x)); });
    back = null; said = null; shutSheet();
    if (N) { follow(null); hush(true); playing = false; paused = false; N = null; }
    why = null; stopAt = 0; letGo = null; prime(); start(xs[i]);
  }
  // Clear: what the line shows, out of it, with Undo for ten minutes (only that Clear's own rows go)
  async function clearShown() {
    const xs = shownLine(); if (!xs.length) return;
    const byP = {}, all = [];
    xs.forEach((x) => { const ks = x.b.map(([k]) => k); (byP[x.p] = byP[x.p] || []).push(...ks); ks.forEach((k) => { all.push(mk(x.p, k)); marked.add(mk(x.p, k)); });
      legacy((L.projects[x.p] || {}).name, ks, x.b.map(([, at]) => at)); });
    if (xs.some((x) => (N && id(x) === N.id) || id(x) === opened)) replyDone(); else changed();
    const got = await send('cleared', byP, 'line');
    if (got && got.clear) put('lineundo', {clear: got.clear, n: xs.length, keys: all, at: Date.now()});
    else all.forEach((k) => marked.delete(k));                 // not taken: back in the line
    drawnSheet = ''; changed();
  }
  async function undoClear() {
    const u = get('lineundo'); put('lineundo', null);
    if (!u || Date.now() - u.at > 600e3) return changed();
    const r = await fetch('/chat/unclear', {method: 'POST', body: new URLSearchParams({clear: u.clear, dev: DEV, view: 'line'})}).catch(() => null);
    if (r && r.ok) u.keys.forEach((k) => marked.delete(k));
    tag = ''; drawnSheet = ''; look();
  }
  // Heard: the last twenty heard (skipped and seen too), to play one again; playing it writes nothing
  async function heardList() {
    const got = await fetched('/chat/line?heard=20', 'json'), list = $('.cpheardrows');
    const items = (got && got.heard) || [];
    list.replaceChildren(...items.map((x) => { const info = got.projects[x.p] || {}, li = row(x, info); on(li, 'click', () => again(x, info)); return li; }));
    if (!items.length) { const li = document.createElement('li'); li.className = 'cpnone'; li.textContent = 'Nothing heard yet.'; list.append(li); }
  }
  // a ▷ under a reply, or one played again, steps in front of the line's reading, which goes on after it
  const stepIn = () => { opening += 1; opened = ''; replaying = null; startingSound = null; resumeAfterWriting = null;
    if (N && !N.tapped) back = {N, sounding: playing && !paused}; follow(null);
    if (N) { hush(true); playing = false; paused = false; } };
  async function again(x, info) {
    shutSheet(); takeUp(); prime();
    const pending = replaying = {opening: ++opening, pause: null}; opened = id(x);
    const got = await fetched('/chat/text?p=' + encodeURIComponent(x.p) + '&t=' + encodeURIComponent(x.t), 'json');
    if (pending.opening !== opening) return;
    opened = ''; replaying = null;
    const bs = (got && got.blocks) || []; if (!bs.length) { if (!N) replyDone(); return; }
    stepIn();
    N = {id: id(x), p: x.p, t: x.t, name: info.name || '', href: info.href || '', kvoice: info.voice || 'am_echo', kokoro: !!L.ready,
      blocks: bs, j: 0, piece: 0, tapped: true, say: true, seat: 'heard'};
    via = '';
    if (pending.pause) { playing = false; paused = true; why = pending.pause; save(); return changed(); }
    begin(0, 0);
  }
  // Auto: one switch, every project's; on, what lands from now is read as it lands, and the phone is kept able to
  function setAloud(yes) { try { localStorage.setItem('aloud', yes ? '1' : '0'); } catch (e) {}
    if (yes) { arm(); letGo = null; stopAt = 0; if (L.ready && !N) rest('Auto on'); else prime(); log('Auto on', ''); }
    else { if (S) { [...S.auto].forEach(([i]) => { if (!S.play.has(i) && !(N && N.id === i)) S.want.delete(i); }); S.auto.clear(); }
      if (!N) { ours = Date.now(); player.pause(); player.loop = false; resting = 0; } letGo = null; log('Auto off', ''); }
    changed(); heed(); look(); }
  // sits above what is fixed at the foot of the page: the box in Chat, a phone's tabs
  // Placed from the foot of the part of the page that is visible (an iPhone measures that part apart from the page,
  // and left the box off it when the two parted: the person's screenshot, Oct 27): the message box on the tabs, the
  // player on the box, or on the chat's own box where the message box isn't shown.
  const probe = document.createElement('div'); probe.style.cssText = 'position:fixed;top:0;left:0;width:0;height:0;visibility:hidden';
  document.body.appendChild(probe);
  // As the page is (no keyboard, not zoomed), each is held at a set height from the foot (bottom:), steady while a phone's
  // toolbar comes and goes: a frame's odd measure (the person: "Periodic flash for one frame brings floating message
  // box to bottom") moves nothing. Only while the visible part is apart from the page is each placed from its foot.
  let ndh = 0;
  const FIELD = "input:not([type=hidden]), textarea, select, button, [contenteditable='true'], [contenteditable='']";
  const fieldUnder = (el) => { const r = el.getBoundingClientRect(); if (!r.width || !r.height) return false;
    return [...document.querySelectorAll(FIELD)].some((f) => { if (bar.contains(f) || f.closest('#gc')) return false;
      const q = f.getBoundingClientRect(); return q.width && q.height && q.left < r.right && q.right > r.left && q.top < r.bottom && q.bottom > r.top; }); };
  const placed = () => { gcDraw();
    const v = window.visualViewport, foot = v ? v.offsetTop + v.height : innerHeight;
    // a bounce past the end or the top parts the two by just the overshoot: as the page is, still (it dropped what floats)
    const over = Math.max(0, scrollY + innerHeight - document.body.scrollHeight) + Math.min(0, scrollY);
    // and with no box to type in there is no keyboard, whatever the phone reports: it can go on giving the keyboard's room
    // after it went down, which floated the box and the player up the screen (the person's screenshot, Oct 27)
    const t = document.activeElement, keyed = (v && v.scale > 1.01) || !!(t && t.matches('textarea, input:not([type]), input[type=text], input[type=search], [contenteditable]'));
    const plain = !v || !keyed || (Math.abs(v.offsetTop - over) < 2 && Math.abs(innerHeight - v.height) < 2);
    const shift = -probe.getBoundingClientRect().top;          // what a box's own measure is off the page's by
    let lift = 0;                                              // from the foot to what they sit on
    const tabs = document.querySelector('.tabs');
    if (tabs && getComputedStyle(tabs).position === 'fixed' && tabs.offsetHeight) lift = tabs.offsetHeight;
    const own = document.querySelector('.composer');
    if (own && gc.hidden && ['fixed', 'sticky'].includes(getComputedStyle(own).position)) {
      const r = own.getBoundingClientRect(), top = r.top + shift;
      if (r.height && r.top > (v ? v.height : innerHeight) * 0.55 && foot - top > lift) lift = Math.round(foot - top); }
    const at = (el, up) => { if (plain) { el.style.top = 'auto'; el.style.bottom = Math.round(up) + 'px'; }
      else { el.style.bottom = 'auto'; el.style.top = Math.round(foot - up - el.offsetHeight) + 'px'; } };
    if (!gc.hidden && !gcTyping) { at(gc, lift); lift += gc.offsetHeight; }
    document.documentElement.style.setProperty('--gch', (gc.hidden ? 0 : gc.offsetHeight) + 'px');
    at(bar, lift);
    document.documentElement.style.setProperty('--cph', bar.offsetHeight + 'px');
    // a chat's Needs you floats on the player: placed from the page's foot, so shown only as the page is
    const nd = document.querySelector('.chat details.needs'), ns = nd && nd.querySelector('summary');
    // on top of the stack: on the player, on the box where the player is at rest, beside the pill where only that shows
    const mini = bar.classList.contains('cpidle') && !bar.classList.contains('open');
    document.documentElement.classList.toggle('apart', !plain); document.documentElement.classList.toggle('cpsmall', mini && !bar.hidden);
    document.documentElement.style.setProperty('--stk', Math.round(lift + (mini ? 8 : bar.offsetHeight)) + 'px');
    document.documentElement.style.setProperty('--pillw', (mini ? pill.offsetWidth : 0) + 'px');
    // never over a box, a field or a button (a fresh install's test: the ♪ sat on a reply's Send at a desk, and on the Add
    // project form on a phone): with one under it, the pill steps aside until the page has moved on
    bar.classList.toggle('cpaside', mini && !bar.hidden && fieldUnder(pill));
    // room for it at the chat's end stays put while it hides a moment (a phone bouncing at the end moves its view):
    // a page that shrank and grew back with each bounce jumped up and down under the finger
    ndh = !ns ? 0 : ns.offsetHeight ? ns.offsetHeight + 28 : ndh;
    // an answer being typed in it: the keyboard shrinks the visible part, which hid it, and the box lost the typing;
    // it stays, at the top of what is visible, over the rest
    const answering = !!(nd && nd.contains(document.activeElement) && document.activeElement.matches('textarea, input'));
    document.documentElement.classList.toggle('answering', answering);
    if (nd) { nd.style.top = answering ? Math.round((v ? v.offsetTop : 0) + 8) + 'px' : '';
      nd.style.bottom = answering ? 'auto' : ''; nd.style.maxHeight = answering ? Math.round((v ? v.height : innerHeight) - 16) + 'px' : ''; }
    document.documentElement.style.setProperty('--ndh', ndh + 'px');
    // the end of a page clears all that floats over it: the last reply's time and buttons are never under it
    document.documentElement.style.setProperty('--room', ((gc.hidden || gcTyping ? 0 : gc.offsetHeight) + (bar.hidden ? 0 : bar.offsetHeight)) + 'px'); };
  let placing = 0;
  const soon = () => { if (!placing) placing = requestAnimationFrame(() => { placing = 0; placed(); }); };
  on(window, 'scroll', soon, {passive: true}); on(window, 'resize', soon);
  on(document, 'focusin', soon); on(document, 'focusout', soon);
  on(document, 'DOMContentLoaded', placed); on(window, 'load', placed);   // it is made before the tabs below it are there
  if (window.visualViewport) { on(window.visualViewport, 'resize', soon); on(window.visualViewport, 'scroll', soon); }
  // Once the keyboard has come up and what floats has moved with it, the cursor of the box being typed in is re-seated:
  // a phone draws it where the box was as the keyboard rose, a line off, until the first letter (the person, Oct 27)
  let seated = 0, forming = false;
  on(document, 'compositionstart', () => { forming = true; }); on(document, 'compositionend', () => { forming = false; });
  const reseat = () => { clearTimeout(seated); seated = setTimeout(() => { const t = document.activeElement;
    if (forming || !t || !t.matches('textarea, input[type=text], input:not([type])')) return;
    const a = t.selectionStart, z = t.selectionEnd; if (a !== null) t.setSelectionRange(a, z); }, 180); };
  on(document, 'focusin', () => { reseat(); setTimeout(reseat, 450); setTimeout(reseat, 900); });   // the page pans late
  if (window.visualViewport) on(window.visualViewport, 'resize', reseat);
  // what is shown changed: the player, and the chat on screen (where it is in that chat, and, as a part starts, there)
  function changed() { if (N && N.kokoro && !N.silent) warm(); draw(); if (view) view.mark(); }
  // Whose voice: the project of the page open, else the one being heard, else every project's (the colony's); its name
  // is a picker, so several projects' voices can be set while there. The projects are read off the page's own menu.
  const listed = () => { const out = new Map();
    if (document.querySelector('nav a.proj.monitor')) out.set('steward', {key: 'steward', name: 'steward', href: '/steward?view=chat'});
    document.querySelectorAll('nav a.proj[href^="/?p="]').forEach((a) => { const k = new URL(a.href).searchParams.get('p'), n = a.querySelector('.pname');
      if (out.has(k) || !n) return;
      const name = [...n.childNodes].filter((x) => x.nodeType === 3).map((x) => x.textContent).join('').trim();
      out.set(k, {key: k, name, href: '/?p=' + k + '&view=chat'}); });
    return [...out.values()]; };
  const here = () => location.pathname === '/steward' ? 'steward' : location.pathname === '/' ? new URLSearchParams(location.search).get('p') : null;
  const voiced = () => { const mine = here(), p = listed().find((x) => x.key === mine);
    if (p) return {key: p.key, name: p.name};
    return N && L.projects[N.p] ? {key: N.p, name: N.name} : {key: 'default', name: 'Every project (default)'}; };
  let whom = null, asking = 0;                     // the project whose voice the settings show, while they are open
  const whose = () => whom || voiced();
  async function sets(fresh = true) {
    $('.cprate').value = String(rate());
    bar.querySelectorAll('.cpseg button').forEach((b) => b.classList.toggle('on', b.dataset.m === mode()));
    $('.cpmodesay').textContent = MODESAY[mode()] || '';
    if (!fresh) return;
    whom = voiced();
    $('.cpwhom').innerHTML = "<option value='default'>Every project (default)</option>" + listed().map((p) =>
      `<option value="${p.key}">${p.name.replace(/[&<>"]/g, '')}</option>`).join('');
    $('.cpwhom').value = whom.key;
    voices();
  }
  async function voices() {
    const who = whose(), ask = ++asking;
    const vo = await fetched('/chat/voice?p=' + encodeURIComponent(who.key), 'json');
    if (ask !== asking) return;                    // another project chosen meanwhile: its answer is the one shown
    if (!vo || !vo.ready) { $('.cpvoice').hidden = true; return; }
    $('.cpkvoice').innerHTML = vo.options; $('.cpkvoice').value = vo.default ? vo.voice : vo.own ? vo.voice : ''; $('.cpvoice').hidden = false;
    $('.cpkvoice').dataset.voice = vo.voice;
    $('.cpherald').innerHTML = "<option value=''>Each project's own voice</option>" + vo.voices; $('.cpherald').value = herald();
  }
  on($('.cpwhom'), 'change', () => { const k = $('.cpwhom').value, p = listed().find((x) => x.key === k);
    whom = p ? {key: p.key, name: p.name} : {key: 'default', name: 'Every project (default)'}; voices(); });
  on($('.cprate'), 'change', () => { P.setRate(+$('.cprate').value || 1); const r = document.getElementById('rate'); if (r) r.value = $('.cprate').value; });
  const keepVoice = async (body) => { const {key, name} = whose(); if (!key) return;
    const r = await fetch('/chat/voice', {method: 'POST', body: new URLSearchParams(Object.assign({p: key}, body))}).catch(() => null);
    if (!r || !r.ok) return;
    const got = await r.json();
    $('.cpkvoice').dataset.voice = got.voice; P.setVoice(name, got.voice);
    document.dispatchEvent(new CustomEvent('colony-voice', {detail: {name, voice: got.voice, own: got.own, colony: key === 'default'}})); };
  const label = (sel) => (sel.selectedOptions[0] || {}).textContent || '';
  on($('.cpkvoice'), 'change', () => { const v = $('.cpkvoice').value;       // Default: the colony's voice, said as such
    preview(v || $('.cpkvoice').dataset.voice, label($('.cpkvoice'))); keepVoice({voice: v}); });
  on($('.cpherald'), 'change', () => { try { localStorage.setItem('announcer', $('.cpherald').value); } catch (e) {}
    preview($('.cpherald').value, label($('.cpherald'))); });
  // coming back with Auto on: what came since is read at once where the browser lets a page speak by itself; a phone
  // lets it only after a tap, so there it starts with the first touch anywhere on the page
  let allowing = null;
  const whenAllowed = (go) => { if (writing()) { after(() => whenAllowed(go), 2200); return; }
    if (unlocked || playing) return go();
    // one moment of silence asks for them all: two asking at once would each cut the other's sound short
    if (!allowing) allowing = new Promise((ok) => { player.src = SILENT;
      player.play().then(() => { unlocked = true; ok(); }).catch(() => on(document, 'pointerdown', () => { unlocked = true; ok(); }, {once: true, capture: true})); });
    allowing.then(() => { allowing = null; go(); }); };
  const save = () => { put('linenow', N ? {N, S: S && {want: [...S.want], play: [...S.play], auto: [...S.auto], begun: [...S.begun]},
    sounding: playing && !paused, saved: Date.now()} : null); };
  const P = window.colonyPlayer = {
    busy: () => !!N || resting > 0, placed, rate, following, mode, auto: aloud, unlock: prime, poke, hold, skip, preview,
    setRate(r) { try { localStorage.setItem('pace', r); } catch (e) {} anew(false); },
    setVoice(name, v) { Object.values(L.projects).forEach((x) => { if (x.name === name) x.voice = v; });
      if (N && N.name === name && N.kvoice !== v) { N.kvoice = v; save(); anew(true); } },
    attach(v) { view = v; changed();
      const s = N && N.blocks[N.j];                     // arrived at the chat being read: there, at once
      if (s && playing && following() && N.name === v.name) v.focus(s.k, true); },
    setMode(m) { try { localStorage.setItem('automode', m); } catch (e) {} placed(); if (view) view.mark(); heed(); },
    leave() { follow(null); view = null; },
    // where a chat's project is being read: the reply and part, playing or held
    of(name) { if (!N || N.name !== name) return null; const s = N.blocks[N.j] || {};
      return {active: true, playing, paused, loading, t: N.t, k: s.k}; },
    now: () => ({playing, paused}),
    // ▷ under a reply in Chat: that reply alone, from the page's own words (the person: "sometimes the last message is
    // all I want to hear"); a line's reading it steps in front of goes on after it, and one heard before writes nothing
    read(v, secs) { if (!secs.length) return;
      prime(); stepIn();
      N = {id: v.key + '|' + secs[0].t, p: String(v.key), t: secs[0].t, name: v.name, href: v.href, kvoice: v.kvoice, kokoro: v.kokoro,
        blocks: secs.map((x) => ({k: x.k, at: x.at, text: x.text})), j: 0, piece: 0, tapped: true, seat: 'chat'};
      via = ''; begin(0, 0); },
    // a chat's seen: its project's parts still in the line, whether anything will read one, and marking them seen
    unheard(p) { const m = new Map(); visible().forEach((x) => { if (x.p === String(p)) x.b.forEach(([k, at]) => m.set(k, {t: x.t, at})); }); return m; },
    quiet: () => !(aloud() && able() && !paused),
    willRead(p, k, at, t) { p = String(p);
      if (N && N.p === p && N.blocks.some((b) => b.k === k)) return true;               // being read
      if (S && S.want.has(p + '|' + t)) return true;                                      // waiting to be, in this session
      if (aloud() && armed && Date.parse(at) >= armed && !(stopAt && Date.now() - stopAt >= LONG)) return true;   // Auto's, as it landed
      return (held.get(mk(p, k)) || 0) > Date.now(); },                                            // another device's Auto's
    async seen(p, ks) { p = String(p); const name = (L.projects[p] || {}).name, ats = P.unheard(p);
      ks.forEach((k) => marked.add(mk(p, k))); changed();
      const got = await send('seen', {[p]: ks}, 'chat'), took = new Set(((got && got.marked) || {})[p] || []);
      ks.forEach((k) => { if (!took.has(k)) { marked.delete(mk(p, k)); held.set(mk(p, k), Date.now() + 10000); } });
      legacy(name, [...took], [...took].map((k) => (ats.get(k) || {}).at));
      changed(); },
    // what the player holds, for a look from outside (the board's own checks)
    state: () => ({reading: N && {id: N.id, j: N.j, piece: N.piece, k: (N.blocks[N.j] || {}).k}, playing, paused, why, letGo: letGo && letGo.why,
      able: able(), resting: !!resting, sent, clip: now && now.parts[now.at], waiting: visible().length, story: story.slice()}),
  };
  document.body.appendChild(bar);
  // The message box over every page while Auto reads with text (Audio + text, Text only; R112): a line on it names
  // the project it sends to. Empty, it follows the project on screen (on a page of none, the one being read); with
  // words in it, it keeps the project they were begun for until sent, the page changing under it as the voice moves
  // on. A tap on its line lists every project, the active page's first. Unsent words are kept on this device. On a
  // Chat page it stands in for the page's own box; Auto off, or reading aloud alone, the page's box is as it was.
  const gc = document.createElement('form'); gc.id = 'gc'; gc.className = 'gc'; gc.hidden = true;
  gc.innerHTML = "<ol class='gclist' hidden></ol><div class='gcbox'><div class='gchead'><button type='button' class='gcto'></button><span class='gcsaid'></span></div>"
    + "<div class='gcfiles' hidden></div><div class='gcrow'><label class='gcattach' aria-label='Attach photos or files'>+<input type='file' multiple hidden></label>"
    + "<textarea rows='1' placeholder='Message'></textarea><button class='gcsend' aria-label='Send'>↑</button></div></div>";
  document.body.appendChild(gc);
  const gta = gc.querySelector('textarea'), glist = gc.querySelector('.gclist'), gto = gc.querySelector('.gcto'), gsaid = gc.querySelector('.gcsaid');
  const gfile = gc.querySelector('input[type=file]'), gfiles = gc.querySelector('.gcfiles');
  const chosen = () => { const fs = [...gfile.files]; gfiles.hidden = !fs.length; gfiles.innerHTML = '';
    if (fs.length) { const n = document.createElement('span'); n.textContent = '📎 ' + fs.map((f) => f.name).join(', ');
      // a small × lets them go, as on the chat's own box
      const x = document.createElement('button'); x.type = 'button'; x.textContent = '×'; x.setAttribute('aria-label', 'Remove the files');
      x.addEventListener('click', () => { gfile.value = ''; chosen(); }); gfiles.append(n, x); }
    if (fs.length && !anchor) anchor = target();             // a file ties it to where it is going, as a word does
    placed(); };
  on(gfile, 'change', chosen);
  const esc = (x) => String(x).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'})[c]);
  let anchor = null, gcTyping = false, ridef = 0;          // anchor: {key, name, chosen} the words are for
  try { const was = JSON.parse(sessionStorage.getItem('gc') || 'null'); if (was) { anchor = was.to; gta.value = was.text || ''; } } catch (e) {}
  const keepDraft = () => { try { sessionStorage.setItem('gc', JSON.stringify({to: anchor, text: gta.value})); } catch (e) {} };
  const onPage = () => { const k = here(); return listed().find((x) => x.key === k) || null; };
  const target = () => { if (anchor) return anchor; const p = onPage(); if (p) return p;
    return N && L.projects[N.p] ? {key: N.p, name: N.name} : null; };
  function gcDraw() {
    const show = aloud() && mode() !== 'audio';
    if (gc.hidden === show) { gc.hidden = !show; if (!show) glist.hidden = true; else requestAnimationFrame(() => grow()); }
    document.body.classList.toggle('gcon', show);
    if (!show) return;
    const t = target(), says = 'To: ' + (t ? t.name : 'choose a project') + ' ▾';
    if (gto.textContent !== says) gto.textContent = says;
    gc.classList.toggle('held', !!anchor);
  }
  // sized only while shown: measured hidden (a fresh load) it read nothing, and the box shut on its "Message", which fell
  // below it (the person's screenshot, Oct 27); it is sized again as it appears
  const grow = () => { if (gc.hidden) return; gta.style.height = 'auto'; gta.style.height = Math.min(gta.scrollHeight, 140) + 'px'; };
  on(gta, 'input', () => { const words = !!gta.value.trim();
    if (words && !anchor) anchor = target();                 // the first word ties them to where they are going
    if (!words && anchor && !anchor.chosen) anchor = null;
    grow(); keepDraft(); placed(); });
  on(gto, 'click', () => { if (!glist.hidden) { glist.hidden = true; return; }
    const page = onPage();
    glist.innerHTML = (page ? `<li data-k="${esc(page.key)}"><b>Active page:</b> ${esc(page.name)}</li>` : '')
      + listed().map((p) => `<li data-k="${esc(p.key)}">${esc(p.name)}</li>`).join('');
    glist.hidden = false; });
  on(glist, 'click', (ev) => { const li = ev.target.closest('li'); if (!li) return;
    const p = listed().find((x) => x.key === li.dataset.k); if (p) anchor = {key: p.key, name: p.name, chosen: true};
    glist.hidden = true; keepDraft(); placed(); });
  on(document, 'click', (ev) => { if (!glist.hidden && !ev.target.closest('#gc')) glist.hidden = true; });
  // typing on a phone, the box rides the keyboard: placed where the visible part of the page ends, the player out of the way
  const ride = () => { if (!gcTyping) return; const v = window.visualViewport;
    if (v) { const h = gc.offsetHeight, low = v.offsetTop, high = v.offsetTop + Math.max(0, v.height - h);
      gc.style.bottom = 'auto'; gc.style.top = Math.round(Math.min(high, Math.max(low, v.offsetTop + v.height - h))) + 'px'; }
    ridef = requestAnimationFrame(ride); };
  let gtold = 0;
  const gmeasure = (when) => { if (gtold++ > 5) return; const v = window.visualViewport, r = gc.getBoundingClientRect(),
    tabs = document.querySelector('.tabs'), tr = tabs && tabs.getBoundingClientRect();
    fetch('/chat/measure', {method: 'POST', body: new URLSearchParams({m: JSON.stringify({when: 'gc-' + when, top: Math.round(r.top),
      bottom: Math.round(r.bottom), h: Math.round(r.height), inner: innerHeight, vvh: v && Math.round(v.height), vvtop: v && Math.round(v.offsetTop),
      tabs: tr && Math.round(tr.top), style: gc.style.cssText, path: location.pathname + location.search,
      nav: ((performance.getEntriesByType && performance.getEntriesByType('navigation')[0]) || {}).type || ''})})}).catch(() => {}); };
  on(gta, 'focus', () => { gcTyping = true; glist.hidden = true; document.body.classList.add('gctyping'); cancelAnimationFrame(ridef); ride();
    after(() => gmeasure('typing'), 1200); });
  on(gta, 'blur', () => { gcTyping = false; document.body.classList.remove('gctyping'); cancelAnimationFrame(ridef); placed(); });
  on(gc.querySelector('.gcsend'), 'pointerdown', (ev) => ev.preventDefault());      // the box keeps its focus until it sends
  // a phone still drops the keyboard as the finger lifts, the box moves, and the tap lands where ↑ was: it sends on the
  // lift instead, as Chat's own box does (the person, 2026-10-28: "a singular tap retracts the keyboard and does not send")
  on(gc.querySelector('.gcsend'), 'touchend', (ev) => { ev.preventDefault(); gc.requestSubmit ? gc.requestSubmit() : gc.querySelector('.gcsend').click(); });
  // at a desk, with a keyboard and a mouse, Enter sends and Shift+Enter starts a new line, as chat apps there do; a
  // phone keeps Enter as a new line (its keyboard has no Shift+Enter), and words still being composed are left alone
  const desk = window.matchMedia('(min-width: 701px) and (hover: hover) and (pointer: fine)');
  on(gta, 'keydown', (ev) => { if (ev.key !== 'Enter' || ev.shiftKey || ev.altKey || ev.ctrlKey || ev.metaKey || ev.isComposing || !desk.matches) return;
    ev.preventDefault(); gc.requestSubmit ? gc.requestSubmit() : gc.querySelector('.gcsend').click(); });
  on(gc, 'submit', async (ev) => { ev.preventDefault();
    const t = target(), text = gta.value.trim(), fs = [...gfile.files];
    if (!text && !fs.length) return;
    if (!t) { gto.click(); return; }                          // nowhere to send yet: the list, to choose
    const body = new FormData(); body.append('p', t.key); body.append('text', text); fs.forEach((f) => body.append('file', f));
    gta.value = ''; gfile.value = ''; chosen(); anchor = null; keepDraft(); grow(); gta.blur(); gsaid.textContent = 'Sending to ' + t.name + '…'; placed();
    const r = await fetch('/chat/send', {method: 'POST', body: fs.length ? body : new URLSearchParams({p: t.key, text})}).catch(() => null);
    if (r && r.ok) { gsaid.textContent = 'Sent to ' + t.name; after(() => { if (gsaid.textContent === 'Sent to ' + t.name) gsaid.textContent = ''; }, 4000); return; }
    // not sent: the words come back, still for that project
    gta.value = text + (gta.value.trim() ? '\\n\\n' + gta.value : ''); anchor = {key: t.key, name: t.name, chosen: true}; keepDraft(); grow(); placed();
    if (fs.length) { try { const dt = new DataTransfer(); fs.forEach((f) => dt.items.add(f)); gfile.files = dt.files; chosen(); } catch (e) {} }
    const left = r && r.status === 409 ? await r.text() : '';
    gsaid.textContent = r && r.status === 409 ? (left.startsWith('Not sent') ? left : 'Not sent: ' + (left ? '“' + left + '” is' : 'something is') + ' half-typed in its console')
      : r && r.status === 503 ? (await r.text()).slice(0, 140) : 'Not sent: the board could not be reached';
  });
  grow();
  after(() => { if (!gc.hidden) gmeasure('resting'); }, 2500);
  // the player as it stands, once a page and again as the page is put away after it held, took up or let go, for the
  // board's log: Auto, the line, whether it can play and what last stopped it, and the pauses with their causes
  const state = (when) => ({when, aloud: aloud(), mode: mode(), line: L.total, waiting: visible().length, able: able(), letGo: letGo && letGo.why,
    stop: lastStop, armed: armed ? new Date(armed).toISOString().slice(0, 19) : '', reading: N && N.name, playing, paused, why, resting: !!resting,
    story: story.slice(-10), path: location.pathname + location.search});
  after(() => { told = logged; measure(state('player-state')); }, 6000);
  function report() { if (logged <= told) return; told = logged;
    try { navigator.sendBeacon('/chat/measure', new URLSearchParams({m: JSON.stringify(state('player-story'))})); } catch (e) {} }
  // the reading last left on this device: here again, held
  try { const was = get('linenow');
    if (was && was.N && Array.isArray(was.N.blocks) && Date.now() - (was.saved || 0) < 12 * 3600e3) { N = was.N;
      if (was.S) S = {want: new Set(was.S.want), play: new Set(was.S.play), auto: new Map(was.S.auto), begun: new Set(was.S.begun)};
      // sounding a moment ago with Auto on (the page refreshed mid-reading): it goes on from the piece it was at, at the
      // first touch where a phone lets a page sound only after one
      if (was.sounding && aloud() && Date.now() - was.saved < 90e3) { const restored = N, mine = opening;
        whenAllowed(() => { if (mine === opening && N === restored && !playing && !paused && !why) begin(N.j, N.piece); }); }
    } else put('linenow', null); } catch (e) {}
  // Auto on as the page opens: armed again where this device was last able to play two hours ago or more, so a night's
  // replies wait for Play; its silence started where the phone lets it, and where it doesn't the pill says tap to resume
  if (aloud() && (!armed || Date.now() - (+(get('alive') || 0)) >= LONG)) arm();
  draw();
  Promise.resolve(look()).then(() => { if (aloud() && !N && !resting) rest('the page opened'); });
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


# A panel made as it is opened (a project's Settings): fetched the first time, its scripts run as they would have been.
LAZY = """
document.addEventListener('toggle', async (ev) => {
  const d = ev.target; if (!d.matches || !d.matches('details[data-lazy]') || !d.open || d.dataset.made) return;
  d.dataset.made = '1';
  const r = await fetch(d.dataset.lazy, {cache: 'no-store'}).catch(() => null);
  const panel = d.querySelector('.panel'); if (!panel) return;
  if (!r || !r.ok) { panel.innerHTML = "<p class='muted'>Couldn't open it: try again.</p>"; delete d.dataset.made; return; }
  panel.innerHTML = await r.text();
  panel.querySelectorAll('script').forEach((old) => { const s = document.createElement('script'); s.textContent = '{\\n' + old.textContent + '\\n}'; old.replaceWith(s); });
}, true);
"""


# Every few seconds: each project's status dot, line and waiting count in the sidebar.
POLL = """
async function poll() {
  try {
    const r = await fetch('/status', {cache: 'no-store'}); const st = await r.json();
    const md = document.getElementById('dot-m'); if (md) md.className = 'sdot ' + st.monitor.state.replace(' ', '-');
    const all = document.getElementById('badge-all');
    if (all) { const n = st.projects.reduce((k, s) => k + (s.followed ? s.waiting || 0 : 0), 0); all.textContent = n;
      const chip = document.getElementById('needs-chip'); if (chip && !chip.classList.contains('on')) chip.hidden = !n; }
    st.projects.forEach((s, i) => {
      const dot = document.getElementById('dot-' + i), line = document.getElementById('sline-' + i);
      if (dot) dot.className = 'sdot ' + s.state.replace(' ', '-');
      document.querySelectorAll(`.msdot[data-p="${i}"]`).forEach((d) => { d.className = 'sdot msdot ' + s.state.replace(' ', '-'); });
      document.querySelectorAll(`.pwork[data-p="${i}"]`).forEach((x) => { x.textContent = s.work || ''; });
      document.querySelectorAll(`.pbadge[data-p="${i}"]`).forEach((x) => { x.hidden = !s.waiting; x.textContent = s.waiting; });
      if (line) line.textContent = (s.state === 'off' ? '' : s.behind || s.state) + (s.work ? (s.state === 'off' ? '' : ' · ') + s.work : '');
      const status = document.getElementById('status-' + i);
      if (status) {
        const act = s.activity || {}, st = s.state.replace(' ', '-');
        status.querySelector('.sdot').className = 'sdot ' + st;
        status.querySelector('b').textContent = s.behind || s.state;
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
CHAT_WAIT = 20               # seconds the person's message waits for a console still starting to show its box
NOT_READY = "Not sent: its console is still starting, so there is nowhere to type it yet. Send this again in a moment."


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
    """The address a file in the project is shown at in the chat: the opener's, as every file link on the board opens
    (R126); or None: nothing outside its folder, nor what isn't there."""
    from . import opener
    got = opener.link(key, root, html.unescape(target)) if root else None
    return got[0] if got else None


def chat_attachments(key, root, text):
    from . import media
    out = []
    for target in re.findall(r'^Attached: (.+)$', text, flags=re.M):
        target = target.strip()
        url = chat_file(key, root, target)
        if url and target.lower().endswith(IMAGES):
            out.append(media.picture(url, Path(target).name))
        elif url and target.lower().endswith('.pdf'):
            out.append(media.pdf(url, Path(target).name))
    return ''.join(out)


def chat_prose(text, key=None, root=None):
    """An agent's prose in the chat: markdown as the page shows it, code blocks kept as they are, links live; a
    picture in the project shown as itself (![what](path)), and a link to any other file there (a PDF) opens it."""
    from . import media
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
        return (media.picture(url, html.unescape(m.group(1)))
                if url and m.group(2).lower().endswith(IMAGES) else m.group(0))

    def link(m):
        url = chat_file(key, root, m.group(2)) if not m.group(2).startswith("http") else None
        if url:
            if m.group(2).lower().endswith('.pdf'):
                return media.pdf(url, html.unescape(m.group(1)))
            if m.group(2).lower().endswith(IMAGES):
                return media.picture(url, html.unescape(m.group(1)))
            return f"<a class='file' href='{url}' target='_blank' rel='noopener'>📄 {m.group(1)}</a>"
        return f"<a href='{m.group(2)}' target='_blank' rel='noopener'>{m.group(1)}</a>"
    page = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", picture, page)
    page = re.sub(r"(?<!!)\[([^\]]+)\]\(((?:https?://|/)[^)\s]+|[^)\s]+\.(?:pdf|png|jpe?g|gif|webp|heic|svg))\)", link, page, flags=re.I)
    return re.sub(r"<div class='mdtable'><table>.*?</table></div>", lambda m: media.table(m[0]), page, flags=re.S)


NOTE_FROM = {"monitor": "The person's steward", "suggestion": "The steward suggests", "colony": "Colony",
             "observation": "Observed file change", "page": "From a page you used"}


def note_from(author):
    """Who left a note, as a chat shows it: the steward by the person's name for it, once it has one."""
    named = steward_named()
    if named and author in ("monitor", "suggestion"):
        return named if author == "monitor" else f"{named} suggests"
    return NOTE_FROM.get(author, "The person")


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
    said += [(n["delivered_at"], f"{note_from(n.get('author'))}:\n{n['text']}")
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


def spoken_text(md):
    """A reply's words as the voice reads them: no code, no pictures, a link as its words, no markdown's marks."""
    md = re.sub(r"```.*?```", "", md, flags=re.S)
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)
    return re.sub(r"[*_`#>|]", "", md).strip()


def iso_epoch(stamp):
    """A transcript's time (ISO, as the programs write it) in epoch seconds; 0 where it has none."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


WIDE = 300                   # seconds: colony starting a turn in many projects within one span is one notice to them all


def colony_wide(started):
    """The spans (WIDE seconds each, numbered) in which colony started a turn in three projects or more, from {project:
    the spans its colony-started turns began in}: a notice to them all (a usage limit, a safe pause, colony updated),
    so their replies are the same reply many times over, and none of them is read aloud among the projects or
    digested for the steward."""
    from collections import Counter
    seen = Counter(b for spans in started.values() for b in set(spans))
    return {b for b, n in seen.items() if n >= 3}


# ---------------------------------------------------------------- the unheard line (R123)
# Replies the person hasn't heard wait in a line, kept on the board, so every device and view reads the same one: heard
# in All, a reply is heard in its project's chat too, and the other way round (the person, 2026-10-28: "if the user hears
# or reads something in all, it should be eliminated from the valid queue of the individual project ... Vice versa").
# Each project keeps its own copy of what its agent said, a row the first time a part is read from its transcript
# (.board/line.jsonl), so a reply stays in the line past the person's next message, its tenth part and a new session;
# and what of it was heard, skipped, seen or cleared (.board/heard.jsonl). The line is what the first has and the second
# doesn't. A reply is one turn's prose (one ▷ in Chat): its parts are marked one by one, and it is heard once all are.
LINE_EVERY = 5               # seconds between the feeder's looks at every project's transcript
UNDO = 600                   # seconds a Clear can be undone
CLAIM = 60                   # seconds a device reading by itself holds what lands for it, from its last look
MARKS = ("heard", "skipped", "seen", "cleared")


def line_since():
    """When the line began (R123 going live): what was said before it counts as heard; no past listening is rebuilt."""
    path = home() / "line.json"
    for _ in range(2):
        try:
            return json.loads(path.read_text())["since"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        with board_lock("line"):
            if not path.exists():
                save_json(path, {"since": now()})
    return now()


_REC, _RECLOCK = {}, threading.RLock()       # each record as read so far, and one reader at a time


def _record(path, fresh, add):
    """One of the line's records (append-only), read once and then only what was added since, as a chat's transcript is;
    `add` folds each row into what is kept of it, with its place in the file. Replaced or cut short, it is read afresh."""
    with _RECLOCK:
        c = _REC.get(str(path))
        try:
            st = os.stat(path)
        except OSError:
            _REC.pop(str(path), None)
            return fresh()
        if not c or c["ino"] != st.st_ino or st.st_size < c["off"]:
            c = _REC[str(path)] = {"ino": st.st_ino, "off": 0, "kept": fresh()}
        if st.st_size > c["off"]:
            with open(path, "rb") as fh:
                fh.seek(c["off"])
                data = fh.read(st.st_size - c["off"])
            at = c["off"]
            for row in data[:data.rfind(b"\n") + 1].splitlines(keepends=True):   # a row half-written waits
                try:
                    add(c["kept"], json.loads(row), at)
                except (ValueError, TypeError, KeyError, AttributeError):
                    pass
                at += len(row)
            c["off"] = at
        return c["kept"]


def first_words(text, n=12):
    w = text.split()
    return " ".join(w[:n]) + ("…" if len(w) > n else "")


def _said(kept, row, at):
    k = row.get("k")
    if k and k not in kept["rows"]:
        kept["rows"][k] = {"t": row["t"], "at": row["at"], "e": iso_epoch(row["at"]), "w": first_words(row["text"]),
                           "pos": at, **({"colony": row["colony"]} if "colony" in row else {})}
        kept["order"].append(k)
        kept["turns"].setdefault(row["t"], []).append(k)
        if "colony" in row:
            kept["spans"].add(row["colony"])


def line_rows(root):
    """What the project's agent said since the line began, part by part: {rows: key -> its turn, time and first words,
    order, turns: turn -> its parts, spans: the WIDE spans of colony's notices it replied to}."""
    return _record(Path(root) / ".board" / "line.jsonl",
                   lambda: {"rows": {}, "order": [], "turns": {}, "spans": set()}, _said)


def _marked(kept, row, at):
    how, k = row.get("how"), row.get("k")
    if how == "undone":
        kept["undone"].add(row.get("clear"))
    elif how == "cleared" and k:
        kept["cleared"].setdefault(k, []).append(row.get("clear"))
        kept["clears"].setdefault(row.get("clear"), iso_epoch(row.get("at", "")))
    elif how in MARKS and k:
        kept["marks"].setdefault(k, how)
        kept["recent"].append((iso_epoch(row.get("at", "")), k, how))
        if len(kept["recent"]) > 800:
            del kept["recent"][:-400]


def heard_state(root):
    """What of the project's line was heard, skipped or seen (the first mark of each part), cleared (by which Clear), and
    which Clears were undone."""
    return _record(Path(root) / ".board" / "heard.jsonl",
                   lambda: {"marks": {}, "cleared": {}, "undone": set(), "clears": {}, "recent": []}, _marked)


def _gone(hs, k):
    """Out of the line: heard, skipped or seen, or cleared by a Clear not undone."""
    return k in hs["marks"] or any(c not in hs["undone"] for c in hs["cleared"].get(k, ()))


def append_rows(root, name, rows):
    """Several lines, whole, under one lock (as append)."""
    import fcntl
    path = Path(root) / ".board" / name
    path.parent.mkdir(exist_ok=True)
    with path.open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        fh.flush()


_FEED, _FEEDING = {}, {}     # each project's place in its transcript; one look at a project at a time


def _feed_state(root):
    st = _FEED.get(str(root))
    if st is None:
        try:
            st = json.loads((Path(root) / ".board" / "line-feed.json").read_text())
        except (OSError, ValueError):
            st = {}
        _FEED[str(root)] = st = st if isinstance(st, dict) else {}
    return st


def line_feed(root, wait=True):
    """What the agent said since the last look, into its project's line, read from where that look stopped. A session
    changed, the one before is read to its end first, so no reply is lost to it. It waits on another look at the same
    project only when asked to (the feeder does; a page's look takes the line as it stands)."""
    lock = _FEEDING.setdefault(str(root), threading.Lock())
    if not lock.acquire(blocking=wait):
        return
    try:
        _line_feed(Path(root))
    finally:
        lock.release()


def _line_feed(root):
    from . import context
    s = context.session(root)
    if not s or not s.get("path") or not s.get("provider"):
        return
    st, since = _feed_state(root), iso_epoch(line_since())
    was = json.dumps(st, sort_keys=True)
    st = json.loads(was)                    # a failed later batch must not move the cached turn past its offset
    if st.get("session") != s["path"]:
        if st.get("session"):
            _feed_file(root, st, since)                        # the session before, to its end
        st.clear()
        st.update(session=s["path"], provider=s["provider"])
    _feed_file(root, st, since)
    if json.dumps(st, sort_keys=True) != was:
        save_json(root / ".board" / "line-feed.json", st)
        _FEED[str(root)] = st


def _feed_file(root, st, since):
    """The parts one transcript gained since st's place in it, as the line's rows; st moves on past them. Only what was
    added is read (unchanged, the look is a stat), its lines read by its own program as Chat reads them."""
    from . import chat
    from .jsonl import read_line
    path = Path(st["session"])
    try:
        stat = path.stat()
    except OSError:
        return
    if (st.get("offset") == stat.st_size and st.get("identity") == [stat.st_dev, stat.st_ino]
            and st.get("stamp") == [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]):
        return
    scratch = home() / "line-scratch" / (hashlib.sha1(str(root).encode()).hexdigest()[:12] + ".jsonl")
    with open(path, "rb") as fh:
        stat = os.fstat(fh.fileno())
        size, identity = stat.st_size, [stat.st_dev, stat.st_ino]
        stamp = [size, stat.st_mtime_ns, stat.st_ctime_ns]
        off, cut = st.get("offset"), st.get("discard", False)

        def marker(end):
            # Only hashes survive in the checkpoint, never fragments of the person's transcript.
            position = fh.tell()
            fh.seek(0)
            digest = hashlib.sha256(fh.read(min(512, end)))
            fh.seek(max(0, end - 64))
            digest.update(fh.read(min(64, end)))
            fh.seek(position)
            return digest.hexdigest()

        def checkpoint(end, discard):
            current = os.fstat(fh.fileno())
            st.update(offset=end, discard=discard, identity=identity, mark=marker(end),
                      stamp=[current.st_size, current.st_mtime_ns, current.st_ctime_ns])

        historical = False
        if off is None and size:
            first = fh.readline(64_001)
            try:
                meta = json.loads(first)
                born = iso_epoch(meta.get("timestamp") or (meta.get("snapshot") or {}).get("timestamp", ""))
            except (ValueError, AttributeError, TypeError):
                born = 0
            historical = stat.st_mtime < since or (born and born < since)
        changed = st.get("stamp") != stamp
        replaced = off is not None and (size < off or st.get("identity", identity) != identity or (
            changed and st.get("mark") and marker(off) != st["mark"]))
        if off is None or replaced:         # a first look, or written anew: from where Chat begins reading it
            off, st["turn"] = max(0, size - chat.TAIL), None
            cut = off > 0
        if size == off:
            if changed or "mark" not in st:
                checkpoint(off, cut)
            return
        scratch.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(scratch, "wb") as out:
                fh.seek(off)
                if historical:
                    # Adopt at this exact EOF, but keep Chat's bounded last-turn context for a later
                    # assistant-only continuation. The native reader applies its usual entry limits.
                    if cut:
                        read_line(fh, chat.BIG)
                    while fh.tell() < size:
                        chunk = fh.read(min(64_000, size - fh.tell()))
                        if not chunk:
                            break
                        out.write(chunk)
                    out.flush()
                    _parts_of(chat.events(st["provider"], scratch), st, float("inf"), (), [])
                    fh.seek(size - 1)
                    checkpoint(size, fh.read(1) != b"\n")
                    return
                end, count, written = off, 0, 0
                known = set(line_rows(root)["rows"])
                budget = min(1_000_000, max(1, chat.TAIL // 2))

                def flush(out):
                    nonlocal count, written
                    if not count:
                        return
                    out.flush()
                    try:
                        # Each batch fits the native Chat reader's tail and entry limits. Its turn continues in st.
                        evs = chat.events(st["provider"], scratch)
                        rows = _parts_of(evs, st, since, known, [])
                        if rows:
                            append_rows(root, "line.jsonl", rows)
                            known.update(r["k"] for r in rows)
                    finally:
                        chat.forget(scratch)
                    out.seek(0)
                    out.truncate()
                    count, written = 0, 0

                while True:
                    line, complete = read_line(fh, chat.BIG)
                    if line == b"":
                        break
                    discard = cut or line is None
                    if not complete and not discard:
                        break                    # a small unfinished row waits at its original offset
                    end = fh.tell()
                    cut = not complete
                    if discard:
                        if not complete:
                            break                # resume discarding an oversized or initially cut row next time
                        continue
                    out.write(line)
                    count += 1
                    written += len(line)
                    if count >= 1000 or written >= budget:
                        flush(out)
                flush(out)
                checkpoint(end, cut)
        finally:
            chat.forget(scratch)
            scratch.unlink(missing_ok=True)


def _parts_of(evs, st, since, known, fresh):
    """A transcript's new events, as the line's rows: its prose part by part with the keys and times Chat gives them (a
    turn begun by the person's message, colony's or a compaction, its items counted in order), going on from the turn
    the last look ended in. What colony's notices carried over a refresh is never read aloud."""
    out, have, t = [], {r["k"] for r in fresh}, st.get("turn")
    for kind, text, at in evs:
        if kind == "queued":
            continue                         # sent mid-turn, not taken up yet: listed after all, never a turn's start
        if kind in ("you", "colony", "compact"):
            t = {"at": at, "from": kind, "n": 0, "last": "",
                 "carry": kind == "colony" and text.lstrip().startswith("[colony] Context carry-over (")}
            continue
        if t is None:
            t = {"at": at, "from": None, "n": 0, "last": ""}
        j, when = t["n"], at or t["last"] or t["at"]
        t["n"] += 1
        t["last"] = at or t["last"]
        k = f"s{t['at']}-{j}"
        if kind != "say" or t.get("carry") or not t["at"] or k in known or k in have or iso_epoch(when) < since:
            continue
        if words := spoken_text(text):
            out.append({"k": k, "t": t["at"], "at": when, "text": words,
                        **({"colony": int(iso_epoch(t["at"]) // WIDE)} if t["from"] == "colony" else {})})
            have.add(k)
    st["turn"] = t
    return out


_TROUBLE = {"said": "", "at": 0.0, "n": 0}


def line_trouble(said):
    """A look at the line that failed: a line in the board's line-errors.log, never silence; the same failure again
    within ten minutes is counted, and the count written with the next line."""
    t = time.time()
    if said == _TROUBLE["said"] and t - _TROUBLE["at"] < 600:
        _TROUBLE["n"] += 1
        return
    home().mkdir(parents=True, exist_ok=True)
    with open(home() / "line-errors.log", "a") as fh:
        if _TROUBLE["n"]:
            fh.write(f"{now()} (the one before, {_TROUBLE['n']} more times)\n")
        fh.write(f"{now()} {said[:600]}\n")
    _TROUBLE.update(said=said, at=t, n=0)


def feed_all(plist, wait=True, only=None):
    failed = []
    for root in plist:
        if only is not None and home() != only:
            return                              # the board it fed is gone: not another's projects
        try:
            if Path(root).exists():
                line_feed(root, wait)
        except Exception as x:                  # one project's unreadable transcript never stops the rest
            failed.append(f"{Path(root).name}: {x.__class__.__name__}: {x}")
    if failed:
        line_trouble("feeding the line failed: " + "; ".join(failed))


def line_keep(every=LINE_EVERY):
    """The feeder: every few seconds each project's transcript is looked at, so what an agent says reaches the line
    whether or not a page is open, and a session changing twice while none is leaves no reply out. It feeds the board it
    was started for and no other: should the board's home change under it, it stops. (A test's board, started in the
    process that runs the tests, ended and its home went, and the feeder went on with the person's real one.)"""
    mine = home()
    while home() == mine:
        try:
            reg = registry()
            if home() != mine:
                break
            feed_all(projects(reg), only=mine)
        except Exception as x:
            line_trouble(f"a pass of the feeder failed: {x.__class__.__name__}: {x}")
        time.sleep(every)


_LISTEN = {}                 # a device reading by itself, able to -> (when its Auto was armed, its last look)
_OPEN = {}                   # each project's parts not gone as of the last look, kept to look on from


def _waiting(root, rec, hs):
    """The project's parts still in the line, in order: from those waiting at the last look and those added since; an
    Undo, which brings parts back, has them all looked at again."""
    c = _OPEN.get(str(root))
    if not c or c["rec"] is not rec or c["hs"] is not hs or c["undone"] != len(hs["undone"]):
        c = {"rec": rec, "hs": hs, "undone": len(hs["undone"]), "n": 0, "keys": []}     # (a record read afresh too)
    keys = c["keys"] + rec["order"][c["n"]:]
    c["keys"], c["n"] = [k for k in keys if not _gone(hs, k)], len(rec["order"])
    _OPEN[str(root)] = c
    return c["keys"]


def line(plist, dev="", arm=""):
    """The line: every project's replies not heard since it began, oldest first, each with its parts' keys and times
    and its first words (its words are fetched when it is about to be read), and how many wait in each project. A
    device whose Auto is on and able to play says so as it looks (arm: when it was armed), and holds what lands."""
    from . import voice
    if dev:
        if arm and iso_epoch(arm):
            _LISTEN[dev[:16]] = (iso_epoch(arm), time.time())
        else:
            _LISTEN.pop(dev[:16], None)
    feed_all(plist, wait=False)
    since, items, info = iso_epoch(line_since()), [], {}
    with _RECLOCK:
        got = [(str(i), root, line_rows(root), heard_state(root)) for i, root in enumerate(plist) if Path(root).exists()]
        # a notice colony gave them all is answered by each: none of those replies is read among the projects
        wide = colony_wide({p: rec["spans"] for p, _, rec, _ in got})
        for p, root, rec, hs in got:
            mine = {}
            for k in _waiting(root, rec, hs):
                r = rec["rows"][k]
                if r["e"] < since or r.get("colony") in wide:
                    continue
                it = mine.get(r["t"]) or mine.setdefault(r["t"], {"p": p, "t": r["t"], "b": [], "at": r["at"], "w": r["w"], "e": r["e"]})
                it["b"].append([k, r["at"]])
            if mine:
                info[p] = {"name": root.name, "href": f"/?p={p}&view=chat", "voice": voice_of(p, root)[0], "n": len(mine)}
                items += mine.values()
    items.sort(key=lambda x: x["e"])
    return {"since": line_since(), "ready": voice.ready(), "total": len(items), "projects": info,
            "line": [{k: v for k, v in x.items() if k != "e"} for x in items]}


def mark_line(plist, how, keys, dev="", view=""):
    """Parts of the line marked heard, skipped, seen or cleared ({project: [keys]}), in their projects' records; a
    Clear's rows carry its id, for Undo. Seen never takes what a device reading by itself will read: a part landed
    after its Auto was armed, while it still looks in. A part already out of the line isn't marked again, though one
    a Clear took is, so undoing the Clear leaves it heard."""
    if how not in MARKS:
        raise ValueError(how)
    clear, t, at = (secrets.token_hex(4) if how == "cleared" else None), time.time(), now()
    held = [a for a, looked in _LISTEN.values() if t - looked <= CLAIM]
    out = {"marked": {}, "refused": {}, **({"clear": clear} if clear else {})}
    for p, ks in (keys or {}).items():
        try:
            root = plist[int(p)]
        except (ValueError, IndexError, TypeError):
            continue
        rows = []
        with _RECLOCK:
            rec, hs = line_rows(root), heard_state(root)
            for k in dict.fromkeys(ks if isinstance(ks, list) else []):
                r = rec["rows"].get(k)
                if not r or (_gone(hs, k) if clear else k in hs["marks"]):
                    continue                     # not a part of the line (before it began, a question's), or out already
                if how == "seen" and any(r["e"] >= a for a in held):
                    out["refused"].setdefault(p, []).append(k)
                    continue
                rows.append({"k": k, "how": how, "at": at, "dev": str(dev)[:16], "view": str(view)[:24],
                             **({"clear": clear} if clear else {})})
        if rows:
            append_rows(root, "heard.jsonl", rows)
            out["marked"][p] = [r["k"] for r in rows]
    return out


def unclear(plist, clear, dev="", view=""):
    """A Clear undone, within ten minutes of it: only its own rows are let go, so a part heard since stays heard. How
    many parts came back."""
    n, t = 0, time.time()
    for root in plist:
        if not Path(root).exists():
            continue
        with _RECLOCK:
            hs = heard_state(root)
            due = clear and clear in hs["clears"] and clear not in hs["undone"] and t - hs["clears"][clear] <= UNDO
            # those it took that nothing else took out: heard since, or cleared by another Clear, they stay out
            back = sum(1 for k, ids in hs["cleared"].items() if clear in ids and k not in hs["marks"]
                       and not any(c != clear and c not in hs["undone"] for c in ids)) if due else 0
        if due:
            n += back
            append(root, "heard.jsonl", {"how": "undone", "clear": clear, "at": now(), "dev": str(dev)[:16], "view": str(view)[:24]})
    return n


def heard_list(plist, most=20):
    """The replies heard last (skipped and seen too), newest first, to be played again; playing them writes nothing."""
    got, info = [], {}
    for i, root in enumerate(plist):
        if not Path(root).exists():
            continue
        with _RECLOCK:
            rec, hs = line_rows(root), heard_state(root)
            latest = {}
            for n, (when, k, how) in enumerate(hs["recent"]):       # its place in the record decides a tie of seconds
                if k in rec["rows"]:
                    latest[rec["rows"][k]["t"]] = (when, n, how)
            for turn, (when, n, how) in latest.items():
                ks = rec["turns"][turn]
                got.append({"p": str(i), "t": turn, "b": [[k, rec["rows"][k]["at"]] for k in ks], "at": rec["rows"][ks[0]]["at"],
                            "w": rec["rows"][ks[0]]["w"], "h": when, "n": n, "how": how})
        if any(x["p"] == str(i) for x in got):
            info[str(i)] = {"name": root.name, "href": f"/?p={i}&view=chat", "voice": voice_of(str(i), root)[0]}
    got.sort(key=lambda x: (-x["h"], -x["n"]))
    got = [{k: v for k, v in x.items() if k != "n"} for x in got[:most]]
    return {"heard": got, "projects": {p: v for p, v in info.items() if any(x["p"] == p for x in got)}}


def line_text(root, keys=(), turn=""):
    """A reply's words as the line keeps them: the parts asked for, or all of one turn's, in order."""
    with _RECLOCK:
        rec = line_rows(root)
        ks = list(rec["turns"].get(turn, [])) if turn else [k for k in keys if k in rec["rows"]]
        where = [(k, rec["rows"][k]["pos"], rec["rows"][k]["at"]) for k in ks]
    out = []
    try:
        with open(Path(root) / ".board" / "line.jsonl", "rb") as fh:
            for k, pos, at in where:
                fh.seek(pos)
                out.append({"k": k, "at": at, "text": json.loads(fh.readline()).get("text", "")})
    except (OSError, ValueError):
        pass
    return out
# A message sent as a reply to one of the agent's (Chat's ↩) begins with that reply's first words, so the agent knows
# which it answers (the person, 2026-10-28: "a small and simple reference to at least the first few words of that
# message block with a specification that it is a reply to that")
REPLY = re.compile(r'\(Replying to: "(.*?)"\)\n')


# Chat holds the latest turns, earlier ones a tap away: forty of a long day's, sent whole every few seconds, made a page
# 119,000 pixels tall that the person's phone kept reloading (2026-10-28)
SHOWN, MORE, MOST = 20, 40, 400


def chat_feed(key, root, shown=SHOWN):
    """The conversation, as the app shows one: the person's messages, the agent's prose in full, each run of work
    between folded to a line (Ran 6 commands), colony's own notices small; then what it is doing now. The latest
    `shown` turns, with a button for earlier ones above them."""
    from . import chat, context, monitor
    s = context.session(root)
    turns = chat.turns(chat.events(s["provider"], s["path"])) if s else []
    out = []
    if len(turns) > shown:
        out.append(f"<div class='earlier' data-k='earlier'><button type='button' class='quiet more' "
                   f"data-n='{min(MOST, shown + MORE)}'>Show earlier</button></div>")
    turns = turns[-shown:]
    letters = mail_by_turn(root, turns)
    for i, t in enumerate(turns):
        k = f"{e(t['at'])}"
        if t["from"] == "you":                          # with the pictures it sent, as pictures
            shots = chat_attachments(key, root, t['text'])
            to = REPLY.match(t["text"])
            said = t["text"][to.end():] if to else t["text"]
            out.append(f"<div class='msg you'>" + (f"<div class='re'>↩ {e(to.group(1))}</div>" if to else "")
                       + f"{e(said)}{shots}{said_at(t['at'])}</div>")
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
                       f"<button class='reply' data-t='{k}' aria-label='Reply to this'>{ICONS['reply']}</button>"
                       # ▷ alone: it holds the reply as it is read and goes on where it stopped; Auto is the line's (R123)
                       f"<button class='listen' data-t='{k}' aria-label='Read this reply aloud'>{ICONS['play']}</button>"
                       f"{said_at(t.get('last') or t['at'])}</div>")
    if not turns:
        out.append("<p class='muted'>No conversation yet: what you send here starts one.</p>")
    # what it is doing now: at work, or a choice in its console, answered here
    name = monitor.name() if key == "steward" else console.session_name(root)
    snap = monitor.snapshot() if key == "steward" else console.snapshot(root, lines=4)
    # and what it waits on in the background, its turn over or not (a test run, a helper): a tap opens the tasks
    helping, behind = chat.helpers(snap), chat.behind(snap)
    # PROVIDER: background(path), each program's reading of its own transcript
    tasks = providers.PROVIDERS[s["provider"]].background(s["path"]) if s and s.get("provider") in providers.PROVIDERS else []
    # only as many commands run as its footer counts, the newest: one stopped by hand sends no notice
    shells = re.search(r"\b(\d+) (?:shells?|background tasks?|running tasks?|background terminals?)\b", behind)
    keep = {t["id"] for t in [t for t in tasks if t["kind"] == "Shell" and t["state"] == "running"][::-1][:int(shells.group(1)) if shells else 0]}
    tasks = [dict(t, state="ended") if t["state"] == "running" and t["kind"] == "Shell" and t["id"] not in keep else t for t in tasks]
    if not behind:                                       # nothing runs, whatever the transcript last said
        tasks = [dict(t, state="ended") if t["state"] == "running" else t for t in tasks]
    # a helper is at work while its program lists it, whatever its notices said (one comes each time it pauses)
    live = {h["what"]: h for h in helping}
    tasks = [dict(t, state="running", stats=live[t["what"]]["stats"]) if t["kind"] == "Helper" and t["what"] in live
             else dict(t, state="completed") if t["kind"] == "Helper" and t["state"] == "running" and behind else t for t in tasks]
    known = {t["what"] for t in tasks if t["kind"] == "Helper"}
    tasks += [{"kind": "Helper", "what": h["what"] or h["name"], "at": "", "state": "running", "stats": h["stats"], "exit": None}
              for h in helping if h["what"] not in known]
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
    # A choice on its console (a question with options, a permission) holds the agent until it is answered: Needs you
    # comes open with it first, stays open as the page is tapped, and a new choice opens it again. The person, 2026-10-28:
    # "it should be ever present on the project page until it is answered"
    held_up = snap["state"] == "needs you" and key != "steward"
    if asks or rest:
        decided = "".join(f"<details class='need' open data-k='g{e(g['id'])}'><summary>"
                          f"<button type='button' class='needplay' data-t='g{e(g['id'])}' data-say='{e(gate_spoken(g))}' "
                          f"aria-label='Read this question aloud'>{ICONS['play']}</button><span class='q'>{e(short(g['question'], 110))}</span>"
                          f"<span class='fold muted'>Fold ▴</span></summary>"
                          f"{gate_body(root, key, g, '/')}</details>" for g in asks)
        if held_up:
            found = providers.of(root).choice(console.screen(name))
            k = "needs-" + hashlib.sha1(repr(found).encode()).hexdigest()[:8]
            out.append(f"<details class='needs blocking' data-k='{k}' open><summary><span class='sdot needs-you'></span>"
                       f"Needs your answer · {len(asks) + len(rest)}</summary>" + "".join(rest) + decided + "</details>")
        else:
            out.append(f"<details class='needs' data-k='needs'><summary><span class='sdot needs-you'></span>Needs you · "
                       f"{len(asks) + len(rest)}</summary>" + decided + "".join(rest) + "</details>")
    if line and tasks:
        out.append(f"<details class='chatlive tasks{busy}' data-k='tasks'><summary>{line}</summary>{tasks_html(tasks)}</details>")
    elif line:
        out.append(f"<div class='chatlive{busy}'>{line}</div>")
    elif held_up and not (asks or rest):
        found = providers.of(root).choice(console.screen(name))
        if found:
            question, options, _ = found
            out.append("<div class='chatask'>" + "".join(f"<div>{e(q)}</div>" for q in question) + "<div class='choices'>"
                       + "".join(f"<button class='quiet' data-option='{e(o)}'>{e(o)}</button>" for o in options) + "</div></div>")
        else:
            out.append(f"<div class='chatask'>It needs you in its console. <a href='/?p={key}&view=console'>Open it →</a></div>")
    return "".join(out)


def gate_spoken(gate):
    """A decision as the player reads it, by its ▷ in a chat's Needs you: the question, why, what stands if it is
    skipped, and the choices, the recommended one said so."""
    rec = gate.get("recommended") or ""
    opts = [o + (", recommended" if o == rec else "") for o in gate.get("options") or []]
    said = [gate["question"], gate.get("why") or "",
            f"If you skip it: {gate['default']}." if gate.get("default") and not opts else "",
            ("The choices: " + "; ".join(opts) + ".") if opts else ""]
    said = " ".join(x.strip() for x in said if x.strip())
    # an address is not spelled out: a web one is "the link", a long path its last name
    said = re.sub(r"https?://\S+?(?=[.,;:)]?(\s|$))", "the link", said)
    return re.sub(r"\b[\w.-]+(?:/[\w.-]+){2,}/?", lambda m: m.group().rstrip("/").rsplit("/", 1)[-1], said)


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
            + (f"<div class='th'>Running</div><ul>{''.join(row(t, e(t.get('stats') or since(t['at']))) for t in running)}</ul>" if running else "")
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
         "next": _SVG.format("<path d='M4.5 12h15M13 5.5l6.5 6.5-6.5 6.5'/>"),
         "list": _SVG.format("<path d='M4 7h16M4 12h16M4 17h10'/>"),
         "reply": _SVG.format("<path d='M9.5 6.5L4.5 11.5l5 5'/><path d='M4.5 11.5H14a5.5 5.5 0 0 1 5.5 5.5v1.5'/>")}


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


def choose_voice(key, root, name=""):
    """A project's voice: one chosen is its own, kept for it alone; "" (Default) has it follow the colony's voice again.
    Choosing a voice never changes another project's (the person: "selecting a voice and having it pin or auto
    change everywhere is weird")."""
    from . import voice
    v, me = _voices(), "steward" if key == "steward" else str(Path(root).resolve())
    if name and name not in dict(voice.VOICES):
        raise ValueError(name)
    if name:
        v["pins"][me] = name
    else:
        v["pins"].pop(me, None)
    home().mkdir(parents=True, exist_ok=True)
    (home() / "voice.json").write_text(json.dumps(v, indent=1))
    return voice_of(key, root)


def default_voice(name=None):
    """The colony's voice, set in Settings: every project reads in it unless it has a voice of its own."""
    from . import voice
    v = _voices()
    if name is not None:
        if name not in dict(voice.VOICES):
            raise ValueError(name)
        v["voice"] = name
        home().mkdir(parents=True, exist_ok=True)
        (home() / "voice.json").write_text(json.dumps(v, indent=1))
    return v["voice"]


def voice_choices(own=""):
    """A project's voice picker: Default (the colony's voice, named) first, then every voice; its own selected."""
    from . import voice
    d = default_voice()
    return (f"<option value=''{'' if own else ' selected'}>Default ({e(dict(voice.VOICES).get(d, d))})</option>"
            + voice.options(own or None))


def queue_keys(key, root):
    """PROVIDER: whether this chat's program has a queue the board can work (queue_keys)."""
    from . import monitor
    return (monitor.provider() if key == "steward" else providers.of(root)).queue_keys


def chat_page(key, root):
    """Chat: the conversation, kept current, and a box to send into it, as Message does into its console."""
    from . import voice
    if voice.ready():
        threading.Thread(target=voice.warm, daemon=True).start()     # loaded before the first ▷
    return (f"<div class='chat' id='chat' data-key='{e(key)}' data-name='{e(Path(root).name if key != 'steward' else 'steward')}' "
            f"data-voice='{1 if voice.ready() else 0}' data-queue='{1 if queue_keys(key, root) else 0}'>{chat_feed(key, root)}</div>"
            f"<form class='composer' id='composer'><input type='hidden' name='p' value='{e(key)}'>"
            "<div class='replying' hidden><span></span><button type='button' class='unreply' aria-label='Not a reply'>✕</button></div>"
            "<div class='attached' hidden></div>"
            f"<textarea name='text' rows='1' placeholder='Message {e(root.name if key != 'steward' else the_steward())}'></textarea>"
            # under the words, as the app has it: attach, reading aloud (its speed, its voice), send; it goes where the box goes
            "<div class='composerbar'><label class='attach' aria-label='Attach photos or files'>+<input type='file' name='file' multiple hidden></label>"
            "<span class='gap'></span><button class='send' aria-label='Send'>↑</button></div></form>"
            # this chat's voice and speed, kept for the player (Auto is the line's, R123)
            "<div class='voicesheet' id='voicesheet' hidden>"
            "<select class='rate' id='rate' aria-label='Reading speed'>"
            + "".join(f"<option value='{r}'>{r:g}×</option>" for r in (1, 1.25, 1.5, 1.75)) + "</select>"
            + (f"<select id='kvoice' aria-label='Voice' data-voice='{e(voice_of(key, root)[0])}'>"
               f"{voice_choices(voice_of(key, root)[0] if voice_of(key, root)[1] else '')}</select>" if voice.ready() else "")
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
  // at the end, a pull further down goes nowhere, with no bounce (it moved what floats and jumped the page about);
  // at the top the phone's pull to refresh stays
  const ended = () => document.documentElement.classList.toggle('atend', window.scrollY > 40 && atEnd());
  window.addEventListener('scroll', () => { stick = atEnd(); ended(); }, {passive: true});
  // The latest is the last message. Typing, the page has room below it (so it never ends above the keyboard, where a
  // phone measures the visible part short), and the latest is brought just above the box.
  const typingNow = () => document.body.classList.contains('typing') || document.documentElement.classList.contains('media-open');
  const lastBottom = () => { const el = box.lastElementChild; return el ? el.getBoundingClientRect().bottom + window.scrollY : 0; };
  const toEnd = () => { if (!typingNow()) return window.scrollTo(0, document.body.scrollHeight);
    if (document.documentElement.classList.contains('media-open')) return;
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
  // Handed over, yet long not in the conversation while its agent isn't at work: it never reached the agent. It comes
  // back to the box, saying so, rather than showing queued until a reload drops it (the person's first message, Oct 28)
  const LOST = 90000;
  const lost = () => { const gone = pending.filter((p) => p.sent && Date.now() - p.sent > LOST && !seen(p));
    if (!gone.length || box.querySelector('.chatlive.busy')) return;
    pending = pending.filter((p) => !gone.includes(p)); box.querySelectorAll('.mine').forEach((m) => m.remove()); keep();
    const back = gone.map((p) => p.text).filter(Boolean).join('\\n\\n');
    if (back) { ta.value = back + (ta.value.trim() ? '\\n\\n' + ta.value : ''); grow(); busy(); }
    held.hidden = false; held.textContent = 'Not delivered: it never reached the conversation. It is back in the box: send it again.'; };
  const file = form.querySelector('input[type=file]'), chip = form.querySelector('.attached');
  const chosen = () => { const fs = [...file.files]; chip.hidden = !fs.length;
    chip.innerHTML = ''; if (!fs.length) return;
    const name = document.createElement('span'); name.textContent = '📎 ' + fs.map((f) => f.name).join(', '); chip.appendChild(name);
    const x = document.createElement('button'); x.type = 'button'; x.textContent = '×'; x.setAttribute('aria-label', 'Remove the files');
    x.addEventListener('click', () => { file.value = ''; chosen(); }); chip.appendChild(x); };
  file.addEventListener('change', chosen);
  // While a finger is on the page, or it still glides from a flick, nothing is redrawn or moved: a phone stops a
  // gliding page at any change. The next look catches up.
  let moving = 0, shown = 0, tag = '';             // shown: the earlier turns asked for; tag: the feed as last fetched
  // caught on the way down: a scroll inside a part (the floating Needs you scrolls in itself) never reaches the window
  ['touchstart', 'touchmove', 'scroll', 'wheel'].forEach((t) => window.addEventListener(t, () => { moving = Date.now(); }, {passive: true, capture: true}));
  async function refresh() {
    if (!box.isConnected || document.documentElement.classList.contains('media-open') || Date.now() - moving < 1200) return;
    // an answer being written in the conversation (to what it needs of the person) is never redrawn away
    if ([...box.querySelectorAll('textarea')].some((t) => t.value.trim()) || box.contains(document.activeElement) &&
        document.activeElement.matches('textarea, select, input')) return;
    try {
      const r = await fetch('/chat/feed?p=' + key + (shown ? '&n=' + shown : ''), {cache: 'no-store',
        headers: tag ? {'If-None-Match': tag} : {}});
      if (r.status === 304 || !r.ok) return;
      const html = await r.text(); if (document.documentElement.classList.contains('media-open')) return;
      tag = r.headers.get('ETag') || ''; if (html === last) return;
      // a part being read stays in view as replies land under it: the page keeps its place rather than following them
      const open = [...box.querySelectorAll('details[open]')].map((d) => d.dataset.k), end = stick && !box.querySelector('.say.reading'), was = lastBottom();
      const shut = [...box.querySelectorAll('details:not([open])')].map((d) => d.dataset.k);   // what came open, folded by them
      // what the reader is looking at stays where it is, though the oldest turns above drop away as new ones come
      const seen = [...box.querySelectorAll('[data-k]')].find((x) => x.getBoundingClientRect().bottom > 0);
      const mark0 = seen && [seen.dataset.k, seen.getBoundingClientRect().top];
      // and where they were in a part that scrolls in itself (Needs you), should it come in new
      const inner = [...box.querySelectorAll('details[data-k]')].filter((d) => d.scrollTop).map((d) => [d.dataset.k, d.scrollTop]);
      // only what changed is redrawn: a part already there stays as it is (a picture is not loaded again, so nothing
      // blinks or shifts), the rest comes in new
      const t = document.createElement('template'); t.innerHTML = last = html;
      // what the page itself changes on a part (the one being read, opened or folded, placed where it floats) doesn't
      // make it new: an open Needs you was swapped for a fresh one at each update, its place in it lost, a tap on it too
      // (and every reply's ▷, marked as resting, with its row)
      const have = new Map(), as = (h) => h.replace('class="say reading"', 'class="say"').replace(/ open=""/g, '')
        .replace(/<span class="media-pdf-preview"[^>]*>[\\s\\S]*?<\\/span>/g, '<span class="media-pdf-preview"></span>')
        .replace(/^(<details[^>]*?) style="[^"]*"/, '$1').replace(/ data-on="play"| data-p="false"/g, '');
      [...box.children].forEach((c) => { if (!c.classList.contains('mine')) {
        const k = as(c.outerHTML); have.set(k, (have.get(k) || []).concat([c])); } });
      box.replaceChildren(...[...t.content.children].map((n) => { const same = have.get(as(n.outerHTML)); return same && same.length ? same.shift() : n; }));
      keep(); if (window.colonyPlayer) window.colonyPlayer.placed();
      open.forEach((k) => { const d = box.querySelector(`details[data-k="${CSS.escape(k)}"]`); if (d) d.open = true; });
      shut.forEach((k) => { const d = k && box.querySelector(`details[data-k="${CSS.escape(k)}"]`); if (d) d.open = false; });
      inner.forEach(([k, top]) => { const d = box.querySelector(`details[data-k="${CSS.escape(k)}"]`); if (d && !d.scrollTop) d.scrollTop = top; });
      const again = mark0 && !end && box.querySelector(`[data-k="${CSS.escape(mark0[0])}"]`);
      const moved = again ? again.getBoundingClientRect().top - mark0[1] : 0;
      if (Math.abs(moved) >= 1) window.scrollBy(0, moved);
      if (end && (!typingNow() || lastBottom() !== was)) toEnd();       // typing, it moves only when the conversation grew
    } catch (e) {}
  }
  let tick = 0;                                  // every 3 s; reading aloud, twice as often, so the voice starts sooner
  setInterval(() => { if (window.colonyPlayer.auto() || ++tick % 2 === 0) refresh(); lost(); }, 1500);
  const sendBtn = form.querySelector('button.send');
  let armed = 0;                                  // ■ tapped once: a second tap within 3 s stops the agent
  const busy = () => { const b = !!box.querySelector('.chatlive.busy'), empty = !ta.value.trim() && !file.files.length;
    const asking = b && empty && Date.now() - armed < 3000;
    sendBtn.textContent = asking ? 'Stop?' : b && empty ? '■' : '↑'; sendBtn.classList.toggle('arm', asking);
    sendBtn.setAttribute('aria-label', asking ? 'Tap again to stop it' : b && empty ? 'Stop' : 'Send');
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
  // ↩ under a reply: what is sent next answers it. Its first words go with the message, so the agent knows which;
  // above the box, the first word or two, until it is sent or ✕
  const replying = form.querySelector('.replying');
  let replyTo = '';
  const firstWords = (s, n) => { const w = s.split(/\\s+/).filter(Boolean); return w.slice(0, n).join(' ') + (w.length > n ? '…' : ''); };
  const answering = (text) => { replyTo = text.trim() ? firstWords(text, 8) : ''; replying.hidden = !replyTo;
    replying.querySelector('span').textContent = replyTo ? 'Replying to “' + firstWords(text, 2) + '”' : ''; };
  replying.querySelector('.unreply').addEventListener('click', () => { answering(''); ta.focus(); });
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const text = ta.value.trim(), fs = [...file.files];
    if (!text && !fs.length) {
      if (!box.querySelector('.chatlive.busy')) return;
      // a second tap stops it: one alone could be a finger the page moved onto ■ as it shifted (the person, twice)
      if (Date.now() - armed > 3000) { armed = Date.now(); busy(); setTimeout(busy, 3100); return; }
      armed = 0; busy();
      await fetch('/chat/stop', {method: 'POST', body: new URLSearchParams({p: key})}).catch(() => {});
      setTimeout(refresh, 800);
      return;
    }
    // it goes out at once: the box clears and the message shows while the board hands it over, however long that takes
    const body = new FormData(form), mine = {show: (fs.length ? '📎 ' + fs.map((x) => x.name).join(', ') + (text ? '\\n' : '') : '') + text,
      match: text || fs[0].name.replace(/\\.[^.]*$/, ''), text};
    if (replyTo && text) body.set('text', '(Replying to: "' + replyTo + '")\\n' + text);
    mine.before = times(mine.match);
    ta.value = ''; grow(); file.value = ''; chosen(); busy(); held.hidden = true;
    ta.blur();                                       // sent: the keyboard goes down, the conversation in view
    pending.push(mine); box.querySelectorAll('.mine').forEach((m) => m.remove()); keep(); toEnd();
    const r = await fetch('/chat/send', {method: 'POST', body}).catch(() => null);
    if (r && r.ok) { answering(''); mine.sent = Date.now(); return setTimeout(refresh, 600); }
    // not sent: it comes back to the box, ahead of anything typed since, its files with it
    pending = pending.filter((p) => p !== mine); box.querySelectorAll('.mine').forEach((m) => m.remove()); keep();
    if (text) ta.value = text + (ta.value.trim() ? '\\n\\n' + ta.value : '');
    if (fs.length) { try { const dt = new DataTransfer(); [...fs, ...file.files].forEach((x) => dt.items.add(x)); file.files = dt.files; } catch (e) {} }
    grow(); chosen(); busy();
    held.hidden = false;
    const left = r && r.status === 409 ? await r.text() : '';
    held.textContent = r && r.status === 409 ? (left.startsWith('Not sent') ? left : 'Not sent: ' + (left ? '“' + left + '” is' : 'something is') + ' half-typed in its console. Send or clear it there, then send this again.')
      : r && r.status === 503 ? await r.text() : r ? 'Not sent: the board could not hand it over (' + r.status + ').' : 'Not sent: the board could not be reached.';
  });
  // Read aloud: the board's player, over every page, reads; this chat hands it a reply on its ▷ and shows where the
  // reading is: the part being heard marked, and in Audio + text the page going with it, the sentence about to be heard
  // in view and softly marked
  const P = window.colonyPlayer, sheet = document.getElementById('voicesheet');
  // a part's words for the voice, code left out: a copy apart from the page has no layout, so its innerText ran each
  // paragraph into the next ("twice.A command"), and its sentences could not be told apart; each paragraph, item and
  // heading now ends with a blank line, so a clip is a sentence or two as the voice is meant to start
  const plain = (els) => els.map((el) => { const c = el.cloneNode(true); c.querySelectorAll('pre, .media-expand, .media-pdf-preview').forEach((p) => p.remove());
    c.querySelectorAll('p, li, h1, h2, h3, h4, h5, h6, blockquote, tr, br').forEach((b) => b.after('\\n\\n'));
    return c.textContent; }).join('\\n\\n').replace(/[*_`#>|]/g, '').trim();
  const KOKORO = box.dataset.voice === '1', picker = document.getElementById('kvoice');
  let kvoice = picker ? picker.dataset.voice : 'am_echo';         // what it reads in: its own, or the colony's
  const view = {name: box.dataset.name, key, href: location.pathname + location.search, kokoro: KOKORO, get kvoice() { return kvoice; },
    mark: () => mark(), focus: (k, go) => focus(k, go), follow: (k, said) => trace(k, said), followed: () => followed};
  // a section: one part of a reply as it was written, with its time
  const sec = (x) => ({t: x.dataset.t, k: x.dataset.k, at: x.dataset.at || '', text: plain([x])});
  // ▷ reads the reply it is under, and only that one: read back to the person's last message, the newest reply came
  // after three long ones with no way to hear it alone (the person, 2026-10-28)
  const turn = (t) => [...box.querySelectorAll(`.say[data-t="${CSS.escape(t)}"]`)].map(sec);
  // ▷ under each reply: a spinner while its voice is being made, ❚❚ while it is heard (a tap holds it), ▷ while it is
  // held (a tap goes on from where it stopped); no ■, and the bar has the rest
  const mark = () => { if (!box.isConnected) return;
    const s = P.of(view.name), on = s && s.playing ? s.t : null;
    box.querySelectorAll('button.listen, button.needplay').forEach((b) => {
      const st = b.dataset.t !== on || s.paused ? 'play' : s.loading ? 'load' : 'pause';
      if (b.dataset.on !== st) { b.dataset.on = st; b.innerHTML = ICON[st]; } });
    focus(s && s.active ? s.k : null, false); };
  // Follow in chat: the part being heard is marked, and as each starts it is brought into view below the bar, unless a
  // finger moved the page just now or the box is being typed in
  let fingered = 0;
  // (a tap on Next or the rest of the player isn't one: tapping on, the page goes with what is read)
  ['touchstart', 'touchmove', 'wheel'].forEach((t) => window.addEventListener(t, (ev) => {
    if (!(ev.target.closest && ev.target.closest('#cplayer, #gc'))) fingered = Date.now(); }, {passive: true}));
  let refocus = 0;
  const focus = (k, go) => { if (!box.isConnected) return;
    const follow = P.following();
    clearTimeout(refocus);
    box.querySelectorAll('.say.reading').forEach((x) => { if (!follow || x.dataset.k !== k) x.classList.remove('reading'); });
    const el = follow && k && box.querySelector(`.say[data-k="${CSS.escape(k)}"]`); if (!el) return;
    el.classList.add('reading');
    if (!go || typingNow()) return;
    // a finger on the page just now: it looks again once they have let it be, while that part is still the one read
    if (Date.now() - fingered < 4000) { refocus = setTimeout(() => { if (el.classList.contains('reading')) focus(k, go); },
      4200 - (Date.now() - fingered)); return; }
    const nav = document.querySelector('nav'), under = nav && getComputedStyle(nav).position === 'sticky' ? nav.getBoundingClientRect().bottom : 0;
    stick = false; window.scrollBy({top: el.getBoundingClientRect().top - under - 12, behavior: 'smooth'}); };
  // The sentence about to be heard, in Audio + text (the person, 2026-10-28: "if the sentence that is about to be read,
  // continues off the visible page then the page should adjust such that the first visible sentence in the viewer window
  // becomes that sentence"): where any of it is below what shows (above the box and the player), the page turns to put
  // its top just under the bar at the top, never back up; not within 4 s of a finger on the page, nor while the box is
  // typed in (it looks again once they are let be). In Audio only the page stays put. It is marked softly with the
  // browser's own highlight, which changes nothing on the page, so no redraw of the chat undoes it. It is found by its
  // letters and digits in the part as the page draws it (code left out, as plain() reads it), on from where the last
  // was found; not found, the part's start is what the page turns to.
  const lit = window.CSS && CSS.highlights && window.Highlight ? CSS.highlights : null;
  // One mark, kept: its range is swapped, and the chat's area is repainted as it moves. Safari on the iPhone left the
  // sentence before still marked as the next one was (the person, 2026-10-28: "Sometimes the text highlight doesnt
  // un-highlight when the next sentence starts highlighting"): WebKit doesn't always repaint what a highlight leaves
  // (its bugs 266250, 307491), so a transparent outline on the box changes, which repaints it and changes nothing seen
  // (in three steps: two moves in one frame, flipped back and forth, would leave it as it was and nothing repainted).
  const shone = lit ? new Highlight() : null;
  let shook = 0;
  const shine = (r) => { if (!lit) return;
    const was = shone.size ? [...shone][0] : null;
    if (was && r && ['startContainer', 'startOffset', 'endContainer', 'endOffset'].every((x) => was[x] === r[x])) return;  // as it was
    if (!was && !r) return;
    shone.clear(); if (r) shone.add(r);
    if (r && lit.get('cpread') !== shone) lit.set('cpread', shone);
    shook = (shook + 1) % 3; box.style.outline = shook ? `${shook}px solid transparent` : ''; };
  const letters = new WeakMap();
  const lettersOf = (el) => { let x = letters.get(el); if (x && x.len === el.textContent.length) return x;
    const map = [], keep = [], walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT,
      {acceptNode: (n) => n.parentElement && n.parentElement.closest('pre') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
    for (let n = walk.nextNode(); n; n = walk.nextNode()) { const t = n.nodeValue;
      for (let i = 0; i < t.length; i++) if (/[\\p{L}\\p{N}]/u.test(t[i])) { keep.push(t[i].toLowerCase()); map.push([n, i]); } }
    x = {text: keep.join(''), map, len: el.textContent.length}; letters.set(el, x); return x; };
  const squash = (s) => String(s).toLowerCase().replace(/[^\\p{L}\\p{N}]/gu, '');
  let foundIn = '', foundAt = 0, foundFrom = 0, turning = 0, followed = null;
  const find = (k, said, same) => {         // the part, and a Range of the sentence in it (null: not found)
    const el = box.querySelector(`.say[data-k="${CSS.escape(k)}"]`); if (!el) return [null, null];
    const x = lettersOf(el), want = squash(said);
    if (foundIn !== k) { foundIn = k; foundAt = foundFrom = 0; }
    let a = want ? x.text.indexOf(want.slice(0, 40), same ? foundFrom : foundAt) : -1;
    if (a < 0 && want) a = x.text.indexOf(want.slice(0, 40));         // drawn again, or read again from its start
    if (a < 0 || !x.map.length) return [el, null];
    const tail = want.slice(-24), e = x.text.indexOf(tail, a + Math.max(0, want.length - tail.length - 8));
    const z = Math.min(x.map.length - 1, e >= 0 ? e + tail.length - 1 : a + want.length - 1);
    foundFrom = a; foundAt = a + 1;
    const [en, eo] = x.map[z], tail2 = en.nodeValue.slice(eo + 1).match(/^[.!?…:;,"'”’)\\]]*/)[0].length;   // its stop marked too
    const r = document.createRange(); r.setStart(x.map[a][0], x.map[a][1]); r.setEnd(en, eo + 1 + tail2);
    return [el, r]; };
  const viewTop = () => { const nav = document.querySelector('nav'); return nav && getComputedStyle(nav).position === 'sticky' ? nav.getBoundingClientRect().bottom : 0; };
  const viewBottom = () => { const v = window.visualViewport; let b = v ? v.height : innerHeight;
    [form, document.getElementById('gc'), document.querySelector('#cplayer:not(.cpidle) .cpbar')].forEach((el) => {
      if (el && !el.hidden && el.offsetHeight) { const r = el.getBoundingClientRect(); if (r.top > 0 && r.top < b) b = r.top; } });
    return b; };
  const trace = (k, said) => { clearTimeout(turning);
    followed = k && said && P.following() ? {k, said} : null;
    if (!followed || !box.isConnected) { shine(null); return; }
    const [el, r] = find(k, said, false); shine(r); if (!el) return;
    turnTo(k, said); };
  const turnTo = (k, said) => {
    const again = () => { if (followed && followed.k === k && followed.said === said) turnTo(k, said); };
    if (typingNow()) { turning = setTimeout(again, 1500); return; }
    if (Date.now() - fingered < 4000) { turning = setTimeout(again, 4200 - (Date.now() - fingered)); return; }
    const [el, r] = find(k, said, true), what = r || el; if (!what) return;
    if (r) shine(r);              // found afresh: the chat may have drawn it again
    const rs = [...what.getClientRects()]; if (!rs.length) return;
    if (Math.max(...rs.map((q) => q.bottom)) <= viewBottom()) return;  // all of it shows
    const by = Math.min(...rs.map((q) => q.top)) - viewTop() - 12;
    if (by > 0) { stick = false; window.scrollBy({top: by, behavior: 'smooth'}); } };
  const relight = () => { if (!followed || !lit) return; const [, r] = find(followed.k, followed.said, true); if (r) shine(r); };
  sheet.querySelector('.shut').addEventListener('click', () => { sheet.hidden = true; });
  document.addEventListener('click', (ev) => { if (!sheet.hidden && !ev.target.closest('#voicesheet, button.vset')) sheet.hidden = true; });
  // its speed, remembered on this device
  const rateBtn = document.getElementById('rate');
  rateBtn.value = String(P.rate());
  rateBtn.addEventListener('change', () => P.setRate(+rateBtn.value || 1));
  // the voice is the board's to keep: this project's own where one was chosen, else the colony's
  const keepVoice = async (body) => {
    const r = await fetch('/chat/voice', {method: 'POST', body: new URLSearchParams(Object.assign({p: key}, body))}).catch(() => null);
    if (!r || !r.ok) return;
    const got = await r.json(), changed = got.voice !== kvoice;
    kvoice = got.voice; picker.value = got.own ? got.voice : '';
    if (changed) P.setVoice(view.name, kvoice);
  };
  document.addEventListener('colony-voice', (ev) => { const d = ev.detail; if (!picker) return;
    if (d.name === view.name) { kvoice = d.voice; picker.value = d.own ? d.voice : ''; }
    else if (d.colony && !picker.value) kvoice = d.voice; });         // the colony's changed, and this one follows it
  if (picker) picker.addEventListener('change', () => { P.preview(picker.value || picker.dataset.voice, (picker.selectedOptions[0] || {}).textContent);
    keepVoice({voice: picker.value}); });
  // Seen (R123): a reply shown here for three seconds, to its last part, counts as heard everywhere; but only where
  // nothing will read it, Auto off or held, and the reply neither being read nor waiting to be (in Audio + text a part
  // shown ahead of the voice is never seen). One another device's Auto will read, the board holds for that one. This
  // replaces the rule that counted a chat's new replies heard as it showed them with Auto off.
  const SEEN = 3000, shownSince = new Map();
  setInterval(() => {
    if (!box.isConnected) return;
    // (the line's sheet open over the chat hides it: what is behind the shade isn't being read)
    const covered = document.documentElement.classList.contains('media-open') || !!document.querySelector('#cplayer .cpsheet:not([hidden])');
    const waiting = document.visibilityState === 'visible' && P.quiet() && !covered ? P.unheard(key) : new Map();
    if (!waiting.size) { shownSince.clear(); return; }
    const top = viewTop(), bottom = viewBottom(), t0 = Date.now(), turns = new Map(), last = new Map();
    waiting.forEach((w, k) => { if (!turns.has(w.t)) turns.set(w.t, []); turns.get(w.t).push(k); });
    box.querySelectorAll('.say[data-k]').forEach((el) => { if (!turns.has(el.dataset.t)) return; last.set(el.dataset.t, el);
      const r = el.getBoundingClientRect(), shows = Math.min(r.bottom, bottom) - Math.max(r.top, top) >= Math.min(r.height, 60);
      if (!shows) shownSince.delete(el.dataset.k); else if (!shownSince.has(el.dataset.k)) shownSince.set(el.dataset.k, t0); });
    turns.forEach((ks, t) => { const el = last.get(t), from = el && shownSince.get(el.dataset.k);
      if (!from || t0 - from < SEEN || ks.some((k) => P.willRead(key, k, waiting.get(k).at, t))) return;
      shownSince.delete(el.dataset.k); P.seen(key, ks); });
  }, 1000);
  // what lands here asks the player to look at the line at once, so a reply is read as it lands
  new MutationObserver(() => { mark(); relight(); P.poke(); }).observe(box, {childList: true});
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
  box.addEventListener('toggle', () => { stick = atEnd(); P.placed(); }, true);
  // typing an answer in the floating Needs you: once the keyboard is up, the box scrolls in itself to the text
  box.addEventListener('focusin', (ev) => { const d = ev.target.closest('details.needs'); if (!d || !ev.target.matches('textarea, input')) return;
    setTimeout(() => { const by = ev.target.getBoundingClientRect().bottom + 12 - d.getBoundingClientRect().bottom; if (by > 0) d.scrollTop += by; }, 400); });
  // Needs you opened by a tap: read from the start (asked again, from the latest round). Floating on the player, it
  // opens upward and scrolls in itself; a tap outside it folds it again, unless an answer is being written there.
  document.addEventListener('click', (ev) => { const d = box.querySelector('details.needs');
    if (d && d.open && !d.classList.contains('blocking') && !d.contains(ev.target) && !ev.target.closest('#cplayer, #gc') && getComputedStyle(d).position === 'fixed'
        && ![...d.querySelectorAll('textarea')].some((t) => t.value.trim())) d.open = false; });
  box.addEventListener('click', (ev) => {
    const s = ev.target.closest('details.needs > summary'); if (!s || s.parentElement.open) return;
    requestAnimationFrame(() => { const d = s.parentElement, bar = document.querySelector('nav');
      if (getComputedStyle(d).position === 'fixed') { const now = d.querySelector('.round.now'); d.scrollTop = now ? now.offsetTop - 8 : 0; return; }
      const under = bar && getComputedStyle(bar).position === 'sticky' ? bar.getBoundingClientRect().bottom : 0;
      const at = d.querySelector('.round.now') || d;            // asked again: the latest round, where it stands now
      window.scrollBy(0, at.getBoundingClientRect().top - under - 12); stick = false; });
  });
  // dictating into the box holds the reading, and Send takes it up within its own tap: the player's, for every box
  P.attach(view);
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
    const more = ev.target.closest('button.more');       // earlier turns, above where the reader is, who stays put
    if (more) { shown = +more.dataset.n; tag = ''; moving = 0; more.textContent = 'Loading…'; refresh(); return; }
    if (ev.target.closest('button.vset')) { sheet.hidden = !sheet.hidden; return; }
    const rp = ev.target.closest('button.reply');
    if (rp) { const s = box.querySelector(`.say[data-t="${CSS.escape(rp.dataset.t)}"]`); answering(s ? s.innerText : ''); ta.focus(); return; }
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
    // ▷ by a question in Needs you, or under a reply: that one read, alone. On the one being read, ❚❚ holds it and ▷
    // goes on from where it stopped (with Continue gone, never from its start); on any other, that one starts
    const np = ev.target.closest('button.needplay');
    const l = np || ev.target.closest('button.listen');
    if (l) { if (np) ev.preventDefault(); const s = P.of(view.name), t = l.dataset.t;
      if (s && s.t === t) return P.hold(s.playing && !s.paused);
      return P.read(view, np ? [{t, k: t, at: '', text: np.dataset.say}] : turn(t)); }
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
    items = "".join(f"<li>{e(r['text'])} <span class='muted'>(your words: {e(r['words'])})"
                    f"{(' · why: ' + e(r['why'])) if r.get('why') else ''}</span>"
                    f"<form class='inline' method='post' action='/rule'><input type='hidden' name='p' value='{pid}'>"
                    f"<input type='hidden' name='id' value='{e(r['id'])}'><button class='quiet'>Retire</button></form></li>"
                    for r in found)
    return (f"<details class='vision'><summary><b>Standing rules</b> · {len(found)}, kept for its agent across "
            f"compactions</summary><ul>{items}</ul></details>")


def wakes_box(root, pid):
    """The timed wakes the project's agent set, each with its next time and why, cancellable with a tap."""
    from . import wakes
    found = wakes.waiting(root)
    if not found:
        return ""
    items = "".join(f"<li>{e(wakes.when(w))}{(' · every ' + e(wakes.span(w['every']))) if w.get('every') else ''} · "
                    f"{e(w['why'])} <form class='inline' method='post' action='/wake'><input type='hidden' name='p' value='{pid}'>"
                    f"<input type='hidden' name='id' value='{e(w['id'])}'><button class='quiet'>Cancel</button></form></li>"
                    for w in found)
    return (f"<details class='vision'><summary><b>Timed wakes</b> · {len(found)}, next {e(wakes.when(found[0]))}"
            f"</summary><ul>{items}</ul></details>")


def vision_box(root, pid):
    from . import vision
    road = roadmap(root)
    changes = [r for r in vision.history(root) if r['how'] != 'baseline']
    current = (prose(road['vision']) if road['vision'] else
               f"<p class='muted'>{e(road['goal']) or 'Shape the vision with your project agent.'}</p>")
    rows = ''.join(f"<li><span class='who'>{e(local_day(r['at'], clock=True))} · {e(r['how'])}</span>"
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


def prose(text, inline=None, doc=False, ids=None):
    """Markdown written in a file, as a page shows it: paragraphs flow (a line wrapped in the file isn't a break on
    the page), lists stay lists, headings stay headings, tables are tables (scrolling sideways on a phone if wide)
    and a quote is set off. As a document (doc: the opener's reading page), as Markdown says: headings at their own
    levels, each an anchor its links can land on; numbered lists numbered and a task's box shown; a rule drawn; code
    fenced as written; a line ending in two spaces breaks; front matter left out. inline: how a line's words are drawn
    (the reading page finds their links from the file's own folder)."""
    inline = inline or _inline
    ids = set() if ids is None else ids              # a document's heading anchors, each once
    out, para, items, rows, quote = [], [], [], [], []
    lines = (text or "").splitlines()
    if doc and lines and lines[0].strip() == "---":   # front matter: what the file says of itself, not its text
        end = next((k for k, l in enumerate(lines[1:60], 1) if l.strip() in ("---", "...")), None)
        lines = lines[end + 1:] if end else lines

    def cells(line):
        return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]

    def flush():
        if para:
            out.append(f"<p>{inline(' '.join(para)).replace(chr(2), '<br>')}</p>")
            para.clear()
        if items:
            # an item indented under another is a list within it, as deep as it goes (they read flat before)
            html_, stack = [], []                   # stack: each list open, outermost first (its indent, ul or ol)
            for indent, number, *words in items:
                while len(stack) > 1 and indent < stack[-1][0]:
                    html_.append(f"</li></{stack.pop()[1]}>")
                if not stack or indent > stack[-1][0]:
                    stack.append((indent, "ol" if number is not None else "ul"))
                    html_.append(f"<ol start='{number}'>" if number not in (None, 1) else f"<{stack[-1][1]}>")
                else:
                    html_.append("</li>")
                html_.append(f"<li>{inline(' '.join(words))}")
            out.append("".join(html_) + "".join(f"</li></{tag}>" for _, tag in reversed(stack)))
            items.clear()
        if rows:
            head = len(rows) > 1 and all(re.fullmatch(r":?-{3,}:?", c) for c in cells(rows[1]))
            body = rows[2:] if head else rows
            out.append("<div class='mdtable'><table>"
                       + (f"<tr>{''.join(f'<th>{inline(c)}</th>' for c in cells(rows[0]))}</tr>" if head else "")
                       + "".join(f"<tr>{''.join(f'<td>{inline(c)}</td>' for c in cells(r))}</tr>" for r in body)
                       + "</table></div>")
            rows.clear()
        if quote:
            out.append(f"<blockquote>{prose(chr(10).join(quote), inline, doc, ids)}</blockquote>")
            quote.clear()
    fence = None                                     # a document's fenced code: its opening mark, and its lines
    for raw in lines:
        line = raw.strip()
        if doc and fence:
            if line.startswith(fence[0]):
                out.append(f"<pre>{e(chr(10).join(fence[1]))}</pre>")
                fence = None
            else:
                fence[1].append(raw)
            continue
        if doc and line.startswith(("```", "~~~")):
            flush()
            fence = (line[:3], [])
            continue
        bullet = re.match(r"^(?:[-*+]|(\d+)[.)])\s+(.*)", line)
        rule = doc and re.fullmatch(r"(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|=+", line)
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
        elif rule and para and not items:            # a line underlined: a heading, as Markdown makes one
            words = " ".join(para)
            para.clear()
            level = 1 if line.startswith("=") else 2
            out.append(f"<h{level} id='{heading_id(words, ids)}'>{inline(words)}</h{level}>")
        elif rule and not line.startswith("="):
            flush()
            out.append("<hr>")
        elif line.startswith("#") and (not doc or re.match(r"#{1,6}(\s|$)", line)):
            flush()
            words = line.lstrip("#").strip()
            if doc:
                words, level = re.sub(r"\s+#+$", "", words), min(len(line) - len(line.lstrip("#")), 6)
                out.append(f"<h{level} id='{heading_id(words, ids)}'>{inline(words)}</h{level}>")
            else:
                out.append(f"<h3>{inline(words)}</h3>")
        elif bullet:
            if para:
                flush()
            words = bullet.group(2)
            if doc:                                  # a task's box, ticked or not
                words = re.sub(r"^\[([ xX])\]\s+", lambda m: "☐ " if m.group(1) == " " else "☑ ", words)
            items.append([len(raw.expandtabs(4)) - len(raw.expandtabs(4).lstrip()),
                          int(bullet.group(1)) if doc and bullet.group(1) else None, words])
        elif items and raw[:1] in (" ", "\t"):
            items[-1].append(line)                       # a list item wrapped onto the next line
        else:
            if items:
                flush()
            # a line ending in two spaces (or a backslash) breaks there, in a document; elsewhere lines simply flow
            para.append(line.rstrip("\\").rstrip() + "\x02" if doc and (raw.endswith("  ") or line.endswith("\\")) else line)
    if fence:
        out.append(f"<pre>{e(chr(10).join(fence[1]))}</pre>")
    flush()
    return "".join(out).replace("\x02", "")


def heading_id(words, ids):
    """A heading's anchor in a document: as GitHub makes it, so a link to a section lands; a second the same, numbered."""
    base = re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", re.sub(r"[*_`]", "", words).strip().lower())) or "section"
    found, n = base, 1
    while found in ids:
        found, n = f"{base}-{n}", n + 1
    ids.add(found)
    return e(found)


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
    # the project's settings tuck into a link on the title's line, opening as a panel, to keep phones' space; what
    # is in it is made as it opens (it took most of a second, every page, folded away)
    settings = (f"<details class='psettings' data-lazy='/project/settings?p={pid}'><summary>Settings</summary>"
                "<div class='panel'><p class='muted'>Opening…</p></div></details>")
    # Message sits on the title's line too, where a phone reaches it (its console is full screen)
    message = message_form(pid, plist, "psettings")
    # pinned or not, it says so beside Message, and a tap changes it
    pinned = str(root.resolve()) in pinned_projects()
    pin = (f"<a class='ppin{' on' if pinned else ''}' href='#' onclick='return pinProject(event, {pid}, {0 if pinned else 1})'>"
           f"<span class='ppinmark'>📌</span>{'Unpin' if pinned else 'Pin'}</a>")
    # time its agent has spent at work, all told: turns only, never the time it sat waiting
    secs = providers.of(root).active_seconds(root) if view == "overview" else 0
    active = f" · Active: {span(secs)}" if secs and secs >= 60 else ""
    # the day's page opens on the day itself: the vision and the project's state are a tab away. The status says what
    # the project is at, from its roadmap, and whether anything waits on the person.
    waiting = len(moments(root)) if view == "overview" else 0
    lead_status = status_card(pid, seen(console.snapshot(root)), at_work(root), waiting) if view == "overview" else ""
    # the vision, the standing rules and the plan's count are the Overview's: repeated on every tab, they took 39 to 54%
    # of a phone's first screen before the tab's own content (the sections review, 2026-10-28)
    front = view == "overview"
    out.append(f"<header class='project'><div class='titlerow'><h1>{e(root.name)}</h1>{pin}{message}{settings}</div>{tabs(pid, view, root)}"
               + switch_line(root) + lead_status
               + (f"{vision_box(root, pid)}{rules_box(root, pid)}{wakes_box(root, pid)}<p class='muted'>Roadmap: {done}/{total}{active}</p>"
                  + project_usage(root) if front else "") + "</header>")
    if view == "showcase":
        out.append(showcase_html(pid, root))
    elif view == "library":                 # what the project knows and has made, found where it is
        out.append(library_html(pid, root, show_all))
    elif view == "roadmap":                 # the plan and its record: the roadmap, notes, history, mail
        out.append(progress_panel(root, pid))       # a shared plan's versions, while one runs or waits to start
        # the person's own notes the agent has not acted on yet, wherever they were left; what colony, the steward or
        # a page left for it follows, folded and named as theirs (colony's brief for a new project, in its command
        # syntax, sat among "your notes" on a newcomer's first look)
        its_now = items(road)
        unread = [n for n in all_notes if not n["addressed_at"] and not (n["anchor"] or {}).get("gate")]
        mine = [n for n in unread if n.get("author", "person") == "person"]
        others = [n for n in unread if n.get("author", "person") != "person" and not n.get("quiet")]
        row = lambda n, by="": (f"<div class='note'><span class='who'>{by}{e(where(n))} · {e(local_day(n['at']))}</span>"
                                f"<div>{e(n['text'])}</div><div class='who'>{e(status(n, its_now))}</div></div>")
        if mine:
            out.append(f"<h2>Your notes, not yet acted on ({len(mine)})</h2><div class='card'>"
                       + "".join(row(n) for n in mine) + "</div>")
        if others:
            out.append(f"<details class='card fold'><summary><h3>Left for the agent by others</h3>"
                       f"<span class='muted'>{len(others)}</span></summary>"
                       + "".join(row(n, f"{note_by(n)} · ") for n in others) + "</details>")
        out.append(measures_html(root))
        # roadmap: the list reads best; the map shows the shape, open by default only when the plan branches
        out.append("<h2 id='roadmap'>Roadmap</h2>")
        from . import hints
        every, kept = hints.cached(root), hints.settled(root)
        found = hints.open_hints(root, every)
        done = [(k, t) for k, _, t in every if k in kept]
        if found:                       # where the plan may have drifted from its work, read by rule: the agent's to judge
            # (none open, none shown: "Shape hints 0" was a fold with nothing for the person in it)
            out.append(f"<details class='card fold hints'><summary><h3>Shape hints</h3><span class='muted'>{len(found)}</span>"
                       f"</summary><ul>{''.join(f'<li>{e(t)}</li>' for _, _, t in found)}</ul>"
                       + (f"<p class='muted small'>Settled by the agent under the present vision:</p><ul class='muted small'>"
                          + "".join(f"<li>{e(t)} <i>{e(kept[k]['why'])}</i></li>" for k, t in done) + "</ul>" if done else "")
                       + "<p class='muted small'>Read from the plan and its history, by rule; the agent hears the open ones "
                         "when a milestone finishes.</p></details>")
        if road["milestones"]:
            its = items(road)
            branched = any(it["links"] for it in its.values())          # only what the plan says waits on what
            if branched:                   # the map only where the plan itself says what waits on what
                out.append(f"<details class='mapbox'><summary>Map of the roadmap"
                       f"{' (it branches)' if branched else ''}</summary>{roadmap_map(road, pid, all_notes, gs)}</details>")
            # One list in the plan's order (journey_html), every milestone loading folded. Opened, a milestone's items
            # as its card drew them: what is in progress (newest first), what's to come, what's done (newest first),
            # each with its steering, notes, decisions waiting and time.
            times = item_times(root)
            when = lambda it: times.get(it["id"], {}).get("since", "")
            live = console.snapshot(root, lines=1)["state"].replace(" ", "-")

            def inside(m):
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
                return part_body(m, drawn)
            out.append(journey_html(road, times, inside, live, pid))
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
            hist.append((stamp(t), f"<details class='item'><summary><code>{e(h)}</code> {e(local_day(t))} {e(subj)}"
                         + (f" <span class='badge new'>{len(by('commit', h))} notes</span>" if by("commit", h) else "")
                         + f"</summary>{thread(by('commit', h), items(road))}" + note_box(pid, "commit", h, "A note on this work; it reaches the agent on its next turn", back=f"/?p={pid}&view=roadmap") + "</details>"))
        out.append(record_fold(pid, "history", "History", hist, hist_n, show_all, rest="the rest are in its git log"))
        whole = [(n["at"], thread([n], items(road))) for n in all_notes if not n["anchor"]]
        whole += [(m["at"], f"<div class='note'><span class='who'>you · {e(local_day(m['at']))} · "
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
            rows.append((m["at"], f"<div class='note'><span class='who'>{e(local_day(m['at'], clock=True))} · {way}"
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
        out.append(pinned_section(pid, root))
        # what changed since the person's last look on an earlier day: the newest few, dated, and how much in all
        out.append(since_html(pid, root, page_base(root)))
        out.append(queue_html(pid, root))
    return shell(reg, pid, "".join(out))


def tokens_html(brief=False):
    """Tokens spent, a section for each program: its limit windows as bars, the part used split by who used it (each
    project in its own colour, colony itself, outside colony), and under them what each spent over 4 hours, a day and
    a week, colony's own overhead apart. Brief (All projects): the bars alone, and the way to the rest (Usage)."""
    import threading
    from . import providers as pv, tokens, usage
    threading.Thread(target=tokens.update, daemon=True).start()      # current by the next look
    looked = tokens.load().get("looked")
    if not looked:
        return ("<h2>Tokens</h2><div class='card'><p class='muted'>Counting: the first look reads the week's transcripts, "
                "in the background. Back in a minute.</p></div>")
    ws = [w for w, _ in tokens.WINDOWS]
    tint = {Path(p).name: PALETTE.get(colour(p)) for p in projects(registry())}
    spare = iter([("#8a7f6a", "#c2b79f"), ("#6f8a9a", "#a7c2d2"), ("#9a6f8a", "#d2a7c2"), ("#7f9a6f", "#b7d2a7")] * 9)
    hue = {("colony", "Colony itself"): ("#77736c", "#a29d95"), ("outside", "Outside colony"): ("#c9c4ba", "#5c5952")}
    paint = lambda who: hue.get(who) or tint.get(who[1]) or hue.setdefault(who, next(spare))
    dot = lambda c: f"--c:{c[0]};--cd:{c[1]}"
    rank = {"project": 0, "colony": 1, "outside": 2}
    num = lambda n: tokens.short(n) if n else "–"
    line = lambda label, v, cls="": (f"<tr{f' class={cls}' if cls else ''}><td>{label}</td>"
                                     + "".join(f"<td>{num(v.get(w, 0))}</td>" for w in ws) + "</tr>")
    clock = lambda ts: time.strftime("%-I:%M %p" if time.localtime(ts)[:3] == time.localtime()[:3] else "%a %-I:%M %p", time.localtime(ts))
    out, (_, every, cached) = [], tokens.windows()
    for key, prov in pv.PROVIDERS.items():
        limits = usage.read(key)
        rows, totals, reads = tokens.windows(prog=key)
        if not limits and not any(totals.values()):
            continue
        bars = []
        for name, v in sorted(limits.items(), key=lambda x: x[0] != "5-hour"):      # the session's window first
            span, used = tokens.SPANS.get(name), float(v.get("used") or 0)
            if not span or not v.get("resets_at"):
                continue
            got = sorted(tokens.spent(key, v["resets_at"] - span).items(), key=lambda x: (rank[x[0][0]], -x[1]))
            whole = sum(x for _, x in got)
            parts = [(who, used * x / whole, x / whole) for who, x in got] if whole else []
            seg = "".join(f"<span style='width:{p:.2f}%;{dot(paint(who))}' title='{e(who[1])}: {p:.1f}% of the limit'></span>"
                          for who, p, _ in parts)
            shown = [x for x in parts if x[1] >= 0.5] or parts[:3]
            rest = sum(p for who, p, _ in parts if (who, p, _) not in shown)
            legend = ("".join(f"<span class='uk'><i style='{dot(paint(who))}'></i>{e(who[1])} <b>{p:.0f}%</b>"
                              f"<span class='muted'> · {sh:.0%} of the used</span></span>" for who, p, sh in shown)
                      + (f"<span class='uk muted'>others {rest:.1f}%</span>" if rest >= 0.05 else "")
                      if parts else "<span class='muted'>Nothing from this machine in this window: the use came from elsewhere "
                                    "(its app, another device).</span>")
            bars.append(f"<div class='uwin'><div class='uhead'><b>{e(name.capitalize())} window</b><span class='muted'>"
                        f"{used:g}% used · resets {e(clock(v['resets_at']))}</span></div>"
                        f"<div class='ubar' role='img' aria-label='{used:g}% of the {e(name)} limit used'>{seg}</div>"
                        f"<div class='ulegend'>{legend}</div></div>")
        of = lambda kind: sorted(((n, v) for (k, n), v in rows.items() if k == kind), key=lambda x: (-x[1]["7 d"], x[0]))
        mine = of("colony")
        over = {w: sum(v[w] for _, v in mine) for w in ws}
        table = ("<table class='tokens'><tr><th>Tokens</th>" + "".join(f"<th>{w}</th>" for w in ws) + "</tr>"
                 + line("<b>All</b>", totals, "all")
                 + "".join(line(f"<i class='key' style='{dot(paint(('project', n)))}'></i>{e(n)}", v, "sub") for n, v in of("project"))
                 + "".join(line(e(n), v, "sub") for n, v in of("outside"))
                 + ("<tr class='head'><td colspan='4'>Colony itself</td></tr>" + "".join(line(e(n), v, "sub") for n, v in mine)
                    + "<tr class='all'><td>Overhead, of all</td>"
                    + "".join(f"<td>{over[w] / totals[w]:.1%}</td>" if totals[w] else "<td>–</td>" for w in ws) + "</tr>" if mine else "")
                 + "</table>")
        out.append(f"<h3 class='uprog'>{e(prov.label)}</h3>" + "".join(bars) + ("" if brief else table))
    if brief:
        return ("<h2>Usage</h2><div class='card'>" + "".join(out)
                + "<p><a href='/usage'>Tokens by project, and colony's own →</a></p></div>")
    weights = " ".join(f"{pv.PROVIDERS[k].label} output {w[3]}x." for k, w in tokens.WEIGHTS.items() if k in pv.PROVIDERS)
    return ("<h2>Tokens</h2><div class='card'>" + "".join(out)
            + "<p class='muted'>Each window's used part is split by what this machine's sessions spent in it, weighed as the "
              f"program's prices weigh tokens: a cache read a tenth of new input; {e(weights)} Use from elsewhere (its app, "
              "another device) isn't seen here, so it falls to those shown. The tables count tokens as they are, mostly cache "
              f"reads (the conversation read again at each turn): {cached['24 h'] / every['24 h'] if every['24 h'] else 0:.0%} of the last day. "
              f"Colony itself is {e(the_steward())} and the asks it makes (consultants, measure judges, connection audits, model "
              "checks, scouts); its notes inside a project's own session count as the project's. As of "
              f"{e(time.strftime('%-I:%M %p', time.localtime(looked)))}.</p></div>")


def usage_page(reg):
    """Usage: what the programs' limits hold and who spent it, every project and colony itself, for anyone's colony."""
    return shell(reg, -2, "<h1>Usage</h1>" + tokens_html())


def project_usage(root):
    """A project's tokens in a line on its Overview: over 4 hours, a day and a week, and its share of each limit."""
    from . import providers as pv, tokens, usage
    if not tokens.load().get("looked"):
        return ""
    me = ("project", Path(root).name)
    rows = tokens.windows()[0].get(me)
    if not rows or not rows["7 d"]:
        return ""
    shares = []
    for key, prov in pv.PROVIDERS.items():
        for name, v in sorted(usage.read(key).items(), key=lambda x: x[0] != "5-hour"):
            span = tokens.SPANS.get(name)
            if span and v.get("resets_at") and v.get("used"):
                got = tokens.spent(key, v["resets_at"] - span)
                whole, mine = sum(got.values()), got.get(me, 0)
                if whole and mine:
                    shares.append(f"{v['used'] * mine / whole:.0f}% of {prov.label}'s {name} limit")
    return ("<p class='muted'><a href='/usage'>Tokens</a>: " + " · ".join(f"{w} {tokens.short(rows[w])}" for w, _ in tokens.WINDOWS
                                                                     if rows[w]) + "".join(f" · {x}" for x in shares) + "</p>")


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
    come first; commits fill what room is left. Nothing changed, or never here before, no section (a project made
    minutes earlier read "Since you were last here · Oct 27" on its first day)."""
    if base.get("first"):
        return ""
    from . import opener
    s = since(root, base)
    times = item_times(root) if s["moved"] else {}
    road = f"/?p={pid}&view=roadmap"
    told = [n for n in s["replies"] if n.get("author") in ("person", "monitor") and not n.get("quiet")]
    events = [(times.get(iid, {}).get("since") or s["moved_at"], False,
               f"<b>{e(was)} → {e(state)}</b> <a href='/item?p={pid}&id={e(iid)}'>{e(iid)}</a> {e(short(plain(text), 90))}")
              for iid, was, state, text in s["moved"]]
    events += [(g["at"], False, f"gate opened: {e(short(g['question'], 100))}") for g in s["opened"]]
    events += [(n["addressed_at"], False, f"{'your' if n.get('author') == 'person' else e(the_steward(possessive=True).replace(chr(39), chr(8217)))} note "
                f"“{e(short(n['text'], 40))}” answered: {e(short(n['reply'] or '', 70))}") for n in told]
    # each through the opener, as the Library and the Showcase open them; one whose file is gone, named only
    linked = lambda got, title: (f"<a href='{e(got[0])}'{opener.NEW_TAB if got[1] else ''}>{e(short(title, 70))}</a>"
                                 if got else e(short(title, 70)))
    events += [(p["at"], False, f"the agent pinned {linked(opener.pin_link(pid, root, p), p['title'])}") for p in s["pinned"]]
    # what is new to see: a line while it is newer than the person's last look, never a block standing for weeks (P2)
    events += [(x["at"], False, f"new to see: {linked(opener.entry_link(pid, root, x), x['title'])}") for x in s["shown"]]
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
                  (len(s["pinned"]), "pin", "pins", "#pinned"),
                  (len(s["shown"]), "new to see", "new to see", f"/?p={pid}&view=showcase"), (s["count"], "commit", "commits", f"{road}#history")]
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
    """The Library tab: what the person and the agent keep to refer to, under their headings, each opening through
    the opener (a document to read, with Edit at its foot; a page to run). It starts empty. Finding a file in the
    project is only a way to add one: a search, its results each with Add. A heading has no Rename of its own: in 11
    projects none was ever renamed or moved, and `colony regroup --rename` still renames one."""
    from . import library, opener
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
            row = pin_row(pid, root, x)
            move = (f"<form class='add regroup' method='post' action='/pin/group'>{hidden}<input type='hidden' name='id' value='{e(x['id'])}'>"
                    f"<input name='group' list='libheads-{pid}' value='{e(x.get('group') or '')}' placeholder='Heading'>"
                    f"<button class='quiet'>Move</button></form>")
            rows.append(row.replace("<div class='pinmenu'>", "<div class='pinmenu'>" + move, 1))
        out.append(f"<details class='libgroup d1' open><summary>{e(g or 'Not under a heading')} <span class='muted'>{len(rows)}</span>"
                   f"</summary>{''.join(rows)}</details>")
    if not groups:
        out.append("<div class='card muted'>Empty, and ready for what you'll both keep returning to: a world's places and people, "
                   "a set of policies, research you build on, a page you work on together. Add one, or the agent will when "
                   "something earns a place. Only what keeps its value stays: unpin what no longer does.</div>")
    out.append(f"<datalist id='libheads-{pid}'>{known}</datalist>")
    gone = pins.archived(root)

    def reach(x):                       # an archived one opens as the others do; one whose file left, named only
        got = opener.pin_link(pid, root, x)
        return (f"<a class='pintitle' href='{e(got[0])}'{opener.NEW_TAB if got[1] else ''}>{e(x['title'])}</a>" if got
                else f"<span class='pintitle gone'>{e(x['title'])}</span>")
    if gone:                            # folded, and never read to the agent: what's done costs nothing to keep
        out.append(f"<details class='libgroup d1 archive'><summary>Archive <span class='muted'>{len(gone)}</span></summary>" + "".join(
            f"<div class='pin'><div class='pinline'>{reach(x)}<form method='post' action='/pin/restore'>{hidden}<input type='hidden' name='id' "
            f"value='{e(x['id'])}'><button class='quiet'>Restore</button></form></div><div class='who'>"
            f"archived {e(local_day(x['archived']['at']))}{' by the agent' if x['archived'].get('by') == 'agent' else ''}"
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
        when = (f" · {how} at {e(last['commit'][:7])}, {e(local_day(last['at']))}" if last else "")
        why = f"<div class='muted small'>{e(last['reason'])}</div>" if last and last.get("reason") else ""
        rows.append(f"<div class='measure'><span class='mname'>{e(m['name'])}</span> {value}"
                    f"<div class='muted small' title='{e(m['what'])}'>{e(m['part'] if m['part'] not in (None, 'Vision', 'Measures') else 'the whole plan')}{when}</div>{why}</div>")
    return f"<div class='card measures'>{''.join(rows)}</div>" if rows else ""


KIND_MARK = {"menu": "☰ menu", "lanes": "‖ lanes", "findings": "? findings", "sections": "▦ sections"}


def lead_clause(text, n=70):
    """An item's opening clause, its title: items run long, with the person's words and decisions after it."""
    head = re.split(r"(?<=[a-z0-9)])[.:;](?:\s|$)| — | \(", text, maxsplit=1)[0].strip()
    return short(head, n)


def journey_html(road, times=None, inside=None, live="", pid=0):
    """The roadmap as one list down the page, in the plan's order: the path to the vision (P3; it replaces R32's cards,
    latest started first, which listed every open milestone a second time). What's finished shows as medals; each
    open milestone, one with no items yet included, is a stop (a ring of its progress, its kind, a dot per item: filled
    when done, glowing while in progress, hollow to come; lanes as rows of their own; what is being worked on now, its
    agent's dot and its time) that opens in place to its items, inside(m). At the end the vision it all heads for,
    then the finished milestones folded together into Completed, which a medal opens."""
    ms = road["milestones"]
    times, inside = times or {}, inside or (lambda m: "")
    done = lambda m: m["items"] and all(i["state"] == "done" for i in m["items"])
    finished, open_ = [m for m in ms if done(m)], [m for m in ms if not done(m)]
    dot = lambda i: f"<i class='jd {i['state']}' title='{e(i['id'])} · {e(lead_clause(i['text'], 80))}'></i>"
    out = []
    if finished:
        # what's been done, as medals: each milestone finished, its items, and when it was won; a tap opens it
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
        # while one of its items is under way: whether its agent is at work right now, kept current by the page
        agent = (f"<span class='sdot msdot {live}' data-p='{pid}' title='In progress; its agent is {e(live.replace('-', ' '))}'></span>"
                 if doing else "")
        took_ = elapsed([times.get(i["id"], {}) for i in its], False)
        out.append(f"<details id='ms-{e(m['id'])}' class='jms{' live' if doing else ''}{' later' if later else ''}'>"
                   f"<summary class='jstop'><span class='jring' style='--p:{round(100 * d / len(its)) if its else 0}'><b>{d}</b>"
                   f"<small>/{len(its)}</small></span><span class='jbody'><span class='jtitle'>{agent}<b>{e(m['id'])}</b> "
                   f"{e(short(m['title'], 70))}<span class='jkind' title='{e(kind)}'>{KIND_MARK.get(kind, '')}</span></span>"
                   f"{dots if its else ''}{now}" + (f"<span class='jtook muted'>{took_}</span>" if took_ else "")
                   + f"</span></summary><div class='jitems'>"
                   + (inside(m) if its else "<p class='muted'>No items yet.</p>") + "</div></details>")
    vision = first_sentence_of(road.get("vision") or road.get("goal") or "")
    if vision:
        out.append(f"<div class='jdest'><span class='jflag'>⚑</span><span>{e(vision)}</span></div>")
    out.append("</div>")
    if finished:                       # the finished, folded together: their medals above open them here
        out.append(f"<details class='card ms done-group'><summary><h3>Completed</h3><span class='muted'>"
                   f"{len(finished)} milestone{'s' * (len(finished) > 1)} · {sum(len(m['items']) for m in finished)} items "
                   f"{elapsed([times.get(i['id'], {}) for m in finished for i in m['items']], True)}</span></summary>"
                   + "".join(f"<details id='ms-{e(m['id'])}' class='ms'><summary><h3>{e(m['id'])}{' — ' + e(m['title']) if m['title'] else ''}</h3>"
                             f"<span class='muted'>{len(m['items'])}/{len(m['items'])} {elapsed([times.get(i['id'], {}) for i in m['items']], True)}"
                             f"</span></summary>{inside(m)}</details>" for m in finished) + "</details>")
    return "".join(out)


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
        f"<div class='muted'><code>{e(h)}</code> {e(local_day(t_))} {e(s)}</div>" for h, t_, s in work)
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
            f"<span class='muted'>{e(local_day(c['at']))} · ${c['cost']:.2f}{note}</span>"
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


def settings_panel(pid, root):
    """A project's Settings panel, made as it is opened: its models and effort, its program, its lead, its nest,
    and archiving or deleting it."""
    merged, own = project_settings(root)
    return (f"{project_settings_form(pid, own, root=root)}"
                f"<p class='muted'>Provider, model, effort and remote access apply when its console next starts; helper tiers at once.</p>"
                f"{remote_line(root)}<p><a href='/?p={pid}&view=connections'>Connections →</a></p>{lead_form(root, pid)}<hr>{nest_form(pid, root)}<hr><div class='dangers'>"
                f"<p class='muted'>Archive takes it off the board and {e(the_steward(possessive=True))} watch (at work, it finishes that turn "
                f"first); All projects → Archived brings it back as it was, the same conversation.</p>"
                f"<form method='post' action='/project/archive'><input type='hidden' name='p' value='{pid}'>"
                f"<input name='why' placeholder='Why (optional; its agent reads it when it comes back)'>"
                f"<button class='quiet'>Archive</button></form>"
                f"<form method='post' action='/project/delete' onsubmit=\"return confirm('Delete {e(root.name)}? Its session stops and its folder moves to colony\\'s trash ({e(home() / 'trash')}), where you can restore it.')\">"
                f"<input type='hidden' name='p' value='{pid}'><button class='danger'>Delete project</button></form></div>")


def app_reach():
    """How each program in use is reached from its own app, besides this board. PROVIDER: Claude Code's Remote Control
    in the Claude app; Codex's pairing with the ChatGPT app."""
    from . import providers as pv
    used = {pv.key(pv.of(p)) for p in projects(registry()) if p.exists()}
    ways = [w for k, w in (("claude", "Claude Code projects are also in the Claude app when Remote Control is on"),
                           ("codex", "Codex projects are in the ChatGPT app once each is paired")) if k in used]
    return "; ".join(ways) + "." if ways else ""


def project_settings_form(pid, own, action="/project-settings", root=None):
    """The choices a project can make for itself; blank keeps the global one."""
    opt = lambda name, choices, cur: (f"<select name='{name}'>" + "".join(
        f"<option value='{v}'{' selected' if str(cur) == v else ''}>{label}</option>" for v, label in choices) + "</select>")
    g = registry()["settings"]
    remote = "on" if own.get("remote", g["remote_by"].get(own.get("provider") or g["provider"], g["remote"])) else "off"
    prog = providers.get(own.get("provider") or g["provider"])
    names = {"ask": "ask each time", "edits": "accept edits", "all": "allow everything", "plan": "plan only"}
    return (f"<form method='post' action='{action}' class='options'><input type='hidden' name='p' value='{pid}'>"
            + provider_fields(own.get("provider", ""), own.get("model", ""), own.get("effort", ""), "global") +
            f"<label>Permissions {opt('permissions', [(k, v) for k, v in names.items()], own.get('permissions') or g['permissions'])}</label>"
            # each program's own way in from its app (Provider.reach): Claude Code's Remote Control, Codex's ChatGPT pairing
            + (f"<label>{e(prog.reach)} {opt('remote', [('on', 'on'), ('off', 'off')], remote)}</label>" if prog.reach else "")
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
                rows.append(f"<tr><th>{e(family)} · {e(steward_name() if role == 'monitor' else role)}</th><td>{e(text)}<div class='muted'>Accepted: {e(describe(held))}</div></td></tr>")
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
                   f"{PRIVATE_TICK}<button>Add this folder as a project</button></form><hr>"
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


# Where the board adds an existing folder: a public or shared repository keeps colony's files out of its history.
PRIVATE_TICK = ("<label><input type='checkbox' name='private' value='on'> Private: keep colony's files (its instructions, "
                "your notes and roadmap) out of this repository's git history, for one that is public or shared</label>")


def add_project_page(reg, tab="new", error=""):
    """Add a project three ways, one tab each: a new one, an existing folder, or a clone of a GitHub
    repository. Folders are chosen with Browse…, which starts closed and opens in place."""
    from . import voice
    choices = (project_settings_form(0, {}, action="")
               .split("<input type='hidden' name='p' value='0'>", 1)[1].rsplit("<button>Save</button></form>", 1)[0]
               + (f"<label>Voice, reading its replies aloud <select name='voice'>{voice_choices()}</select></label>" if voice.ready() else "")
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
                     f"{picker('path', '', 'No folder chosen')}{choices}{PRIVATE_TICK}<button>Add project</button></form>"),
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
    """The steward, like a project: Overview (what it said, the projects it follows, what it decided, and how much
    waits on the person), Helm (its stance toward each project), Chat, Console."""
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
               f"<label class='stack'>What should {e(the_steward())} tell them for you?"
               f"<textarea name='text' placeholder='It relays it as yours, in full, and checks it landed'></textarea></label>"
               f"<button>Have {e(the_steward())} send it</button></form></div></details>") if targets else ""
    # its state beside its name, in the menu's words; what that means is on the Helm tab (and under a pointer, in its title)
    line, stance, means = steward_status()
    head = (f"<header class='project'><div class='titlerow'><h1>{e(steward_name())}</h1><span class='badge stance' title='{e(means)}'>{e(line)}</span>"
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
                       f"<p class='muted'>How {e(the_steward())} steers all your projects: what it settles, what always comes back to you, "
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
                 f"<p><b>{e(stance[:1].upper() + stance[1:])}.</b> {e(means)}</p>"
                 f"<p class='muted'>What {e(the_steward())} settles, what always comes back to you, and how it steers, for all projects"
                 f"{' (your version)' if (home() / 'direction.md').exists() else ''}. Each project below adds its own, and "
                 f"whether it is included in {e(the_steward(possessive=True))} helm.</p></div>"]
        for pid, p in enumerate(plist):
            if not p.exists():
                continue
            pos, f, held = monitor.posture(p), focus(p), monitor.helm_for(p)
            opt = lambda v, label, cur: f"<option value='{v}'{' selected' if cur == v else ''}>{label}</option>"
            lines = "".join(f"<li><span class='kind'>{k}</span> {e(x)}</li>" for k in ("doing", "verify", "next") for x in f[k])
            made = "".join(f"<div class='note'><span class='who'>{e(local_day(d['at'], clock=True))}</span><div>{e(d['text'])}</div></div>"
                           for d in monitor.decisions(p, 8))
            cards.append(f"<div class='card helmcard'><div class='titlerow'><h3>{e(p.name)}</h3>"
                         f"<span class='badge {'gate' if held else ''}'>{e(steward_name()) + ' decides' if held else 'you decide'}</span></div>"
                         f"<form class='options' method='post' action='/posture'><input type='hidden' name='p' value='{pid}'>"
                         f"<label><input type='checkbox' name='helm' value='on'{'' if pos['helm'] is False else ' checked'}> Include in {e(the_steward(possessive=True))} helm "
                         f"<span class='muted'>({'it decides here now' if held else 'the helm is off: you decide' if not on else 'left out: you decide here'})</span></label>"
                         f"<label class='stack'>Direction for {e(the_steward())}<textarea name='direction' placeholder='Empty: the standing direction alone. Add what is particular here: this version is… and it is done when… · always bring back… · you may settle… · the yardstick…'>{e(pos['direction'])}</textarea></label>"
                         f"<label><input type='checkbox' name='scouting' value='on'{' checked' if pos['scouting'] else ''}> Scout for it every "
                         f"<input name='scout' type='number' min='1' step='1' value='{pos['scout'] or 24}' class='hours'> hours "
                         f"<span class='muted'>(if worked on since)</span></label>"
                         f"<label class='stack'>What scouting should favour here<textarea name='scout_note' class='short' placeholder='e.g. faster test runs; papers on forecasting; nothing that changes the stack'>{e(pos['scout_note'])}</textarea></label>"
                         f"<button>Save</button></form>"
                         f"<div class='focus'><b>Now: {e(f['milestone'] or 'no roadmap yet')}</b><ul>{lines}</ul></div>"
                         + (f"<details><summary>What it decided here</summary>{made}</details>" if made else "") + "</div>")
        body = head + ("".join(cards) if len(cards) > 1 else cards[0] + "<p class='muted'>No projects yet.</p>")
        return shell(reg, -1, body)
    # What it last said first, then how much waits on the person, the projects it follows, and, folded, what it decided.
    made = monitor.decisions(None, 10)
    recent = "".join(f"<div class='note'><span class='who'>{e(Path(d['project']).name)} · {e(local_day(d['at'], clock=True))}</span>"
                     f"<div>{e(d['text'])}</div></div>" for d in made)
    last = monitor.said_last()
    said = (f"<a class='saidlast' href='/steward?view=console'><span class='who'>{e(the_steward(True))} said · {e(on_day(last['at']))}"
            f"</span><div class='said'>{e(' '.join(re.sub(r'[*_`#>]', '', last['text']).split()))}</div></a>" if last else "")
    follows = "".join(f"<label><input type='checkbox' name='p' value='{i}'{' checked' if monitor.involved(p) else ''}> {e(p.name)}</label>"
                      for i, p, _ in arranged(plist) if p.exists())
    kept = sum(monitor.involved(p) for p in plist if p.exists())
    follow = (f"<details class='card fold' id='follows'><summary><h3>Projects it follows</h3><span class='muted'>{kept} of "
              f"{sum(1 for p in plist if p.exists())}</span></summary><form class='options' method='post' action='/steward/involved'>"
              f"<p class='muted'>Unchecked, {e(the_steward())} leaves a project out: it doesn't wake for it, report on it or scout for it, "
              f"and hears of it only when you bring it up.</p>{follows}<button>Save</button></form></details>") if plist else ""
    # Only its own parts, and how much waits on the person a tap from Needs you: its list of projects repeated the chip
    # row and the sidebar, and its Needs you (5,178 px on a phone) the front page's and the Needs you page's (the
    # sections review, 2026-10-28). The count is the Needs you chip's: what waits in the projects it follows.
    waiting = sum(len(moments(p)) for p in plist if p.exists() and monitor.involved(p))
    wait = (f"<p class='waitline'><a href='/needs-you'>{waiting} waiting →</a></p>" if waiting
            else "<p class='waitline muted'>Nothing waits on you.</p>")
    body = (head + said + wait + follow
            + (f"<details class='card fold'><summary><h3>What {e(the_steward())} decided</h3><span class='muted'>{len(made)}"
               f"{' · ' + e(on_day(made[0]['at'])) if on_day(made[0]['at']) else ''}</span></summary>{recent}</details>" if made else ""))
    return shell(reg, -1, body)


def seen(snap):
    """A console as the board's dots and lines show it: idle on screen while its helpers or background commands are at
    work is working, saying what runs ("2 helpers running"). What decides when to write to it keeps the screen's own."""
    from . import chat
    behind = chat.behind(snap) if snap.get("state") == "idle" else ""
    return dict(snap, state="working", behind=behind) if behind else snap


def status_card(pid, snap, work="", waiting=None):
    """The project's status: working / needs you / idle / off, as its console shows it; what it is at, from its
    roadmap (never its console's spinner words); its background agents; and, given the count, when nothing waits on
    the person, that it doesn't (what does is listed just below)."""
    act = snap.get("activity") or {}
    agents = "".join(f"<div class='agent{' current' if a['current'] else ''}'><span>{'●' if a['current'] else '○'} {e(a['name'])}</span>"
                     f"<span class='muted'>{e(a['detail'])}</span></div>" for a in act.get("agents", []))
    return (f"<a class='status' id='status-{pid}' href='/?p={pid}&view=console'><div class='statusline'>"
            f"<span class='sdot {snap['state'].replace(' ', '-')}'></span><b>{e(snap.get('behind') or snap['state'])}</b>"
            f"<span class='muted'>{(' · ' + e(work)) if work else ''}</span></div>"
            f"<div class='agents'>{agents}</div>"
            + ("" if waiting is None else f"<div class='waits muted'{' hidden' if waiting else ''}>Nothing waits on you.</div>")
            + "</a>")


PATHLIKE = re.compile(r"(?<![\w/~.:@-])((?:[\w-][\w.-]*/)+[\w.-]*|[\w-][\w.-]*\.[A-Za-z][A-Za-z0-9]{0,5})(?![\w/-])")
ADDRESS = re.compile(r"https?://[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'\"]")


def try_html(text, pid, root, x):
    """How to see or try an entry, its paths and addresses as links through the opener (7 of 22 Try texts named a
    folder, a file or an address the person couldn't tap): a path only where it is in the project, so none leads
    nowhere, found from the project's folder or the entry's own; each carrying the entry, as its card does."""
    from . import opener
    here = [Path(root)] + ([(Path(root) / x["link"]).parent] if x.get("link") and not showcase.is_url(x) else [])

    def path(m):
        word = m.group(1).rstrip(".")
        got = next((g for g in (opener.link(pid, root, word, entry=x["id"], here=h) for h in here) if g), None)
        rest = m.group(1)[len(word):]
        return (f"<a href='{e(got[0])}'{opener.NEW_TAB if got[1] else ''}>{e(word)}</a>{e(rest)}" if got else e(m.group(1)))

    out, at = [], 0
    for m in ADDRESS.finditer(text):
        out.append(try_paths(text[at:m.start()], path))
        out.append(f"<a href='{e(opener.address(pid, m.group(0)))}'{opener.NEW_TAB}>{e(m.group(0))}</a>")
        at = m.end()
    out.append(try_paths(text[at:], path))
    return "".join(out)


def try_paths(text, path):
    """Plain text with each path-like word put through path(match), the rest escaped."""
    out, at = [], 0
    for m in PATHLIKE.finditer(text):
        out.append(e(text[at:m.start()]) + path(m))
        at = m.end()
    return "".join(out) + e(text[at:])


def showcase_html(pid, root):
    """What the project made that's worth seeing, newest first: what it is (three lines, the rest a tap away), how to see
    or try it (its paths and addresses links), one button saying what it opens, and the person's comments, none
    required. A deliverable shown again is one card, its earlier showings folded under it with their dates, stories
    and comments (P4). Past the newest few, the rest fold into Earlier, the project's history of what it showed. All
    of it opens through the opener, each carrying its entry, so a comment where it opens is on that showing."""
    from . import opener
    rows = showcase.versions(root)
    if not rows:
        return ("<h2>Showcase</h2><p class='muted'>Nothing shown yet. When a deliverable reaches a stage where it should be "
                "good, the agent puts it here: what it is and how to see it. Comment if you like; nothing waits on you.</p>")
    back = f"/?p={pid}&view=showcase"

    def opens(x, button=True):
        """The picture, fitted to the screen and a tap to open it whole; or one button that says what it opens."""
        if not x.get("link"):
            return ""
        got = opener.entry_link(pid, root, x)
        if not got:
            return f"<p class='muted'>{e(x['link'])} is no longer in the project.</p>"
        tab = opener.NEW_TAB if got[1] else ""
        if button and not showcase.is_url(x) and showcase.mime(x).startswith("image/"):
            return f"<a class='shotlink' href='{e(got[0])}'{tab}><img class='shot' src='{e(got[0])}' alt='{e(x['title'])}' loading='lazy'></a>"
        return f"<a class='{'open' if button else 'opensmall'}' href='{e(got[0])}'{tab}>{e(opener.open_label(x))}</a>"

    def said(x):
        return "".join(f"<div class='note'><span class='who'>you · {e(local_day(c['at']))}</span><div>{e(c['text'])}</div></div>"
                       for c in x["comments"])

    def story(x, clamp=True):
        # "What" ran 133 to 677 characters, a card 442 to 895 px on a phone: three lines, then More
        what = (f"<div class='what clamp'>{e(x['what'])}</div><button type='button' class='quiet more' hidden "
                "onclick=\"this.previousElementSibling.classList.remove('clamp'); this.remove()\">More</button>"
                if clamp else f"<div class='what'>{e(x['what'])}</div>")
        return what + (f"<div class='try'><b>Try:</b> {try_html(x['try'], pid, root, x)}</div>" if x.get("try") else "")

    def card(x):
        hidden = (f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='id' value='{e(x['id'])}'>"
                  f"<input type='hidden' name='back' value='{e(back)}'>")
        before = "".join(f"<div class='showing'><div class='showhead'><b>{e(y['title'])}</b><span class='who'>{e(y.get('item') or '')} "
                         f"{e(local_day(y['at']))}</span></div>{story(y, clamp=False)}{opens(y, button=False)}{said(y)}</div>"
                         for y in x["earlier"])
        return (f"<div class='card show' id='s-{e(x['id'])}'><div class='showhead'><b>{e(x['title'])}</b>"
                f"<span class='who'>{e(x.get('item') or '')} {e(local_day(x['at']))}</span></div>"
                + story(x) + opens(x) + said(x)
                + f"<details><summary class='muted'>Comment</summary><form class='add' method='post' action='/showcase/comment'>"
                f"{hidden}<textarea name='text' placeholder='What you think of it: it reaches the agent'></textarea>"
                f"<button>Send</button></form></details>"
                + (f"<details class='showings'><summary class='muted'>Shown before · {len(x['earlier'])}</summary>{before}</details>"
                   if x["earlier"] else "") + "</div>")
    fresh, earlier = rows[:showcase.FRESH], rows[showcase.FRESH:]
    # "More" only where the three lines hide something: measured once laid out, and again as a fold opens (inside a
    # closed one nothing has a height yet)
    more = ("<script>(() => { const measure = () => document.querySelectorAll('.show .what.clamp').forEach((w) => {"
            " const m = w.nextElementSibling; if (m && m.classList.contains('more')) m.hidden = w.scrollHeight <= w.clientHeight + 2; });"
            " measure(); document.addEventListener('toggle', measure, true); })();</script>")
    return ("<h2>Showcase</h2>" + "".join(card(x) for x in fresh)
            + (f"<details class='card'><summary><h3>Earlier ({len(earlier)})</h3></summary>"
               + "".join(card(x) for x in earlier) + "</details>" if earlier else "") + more)


def nest_page(reg, name):
    """A nest's own page: the shared vision its projects build toward, its projects, and every member's links and
    demos gathered in one place, each marked with the project it came from (open and comment there)."""
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
               f" · {e(local_day(r['at']))}</span></div>" for r in n["standards"]) + "</div>"] if n.get("standards") else []),
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
        out.append(f"<div class='archrow'><div><div class='muted'>Archived: {e(', '.join(Path(k).name for k in away))}</div>"
                   + _bring_back_buttons(away) + "</div>"
                   f"<form method='post' action='/bring-back'><input type='hidden' name='nest' value='{e(name)}'>"
                   f"<button class='quiet'>Bring back group</button></form></div>")
    if members:
        out.append(f"<details class='more'><summary>Archive the group</summary><form method='post' action='/nest/archive' "
                   f"class='nestadd'><input type='hidden' name='name' value='{e(name)}'><input name='why' placeholder="
                   f"'Why (optional; its agents read it when it comes back)'><button class='quiet'>Archive the group</button></form>"
                   f"<p class='muted'>Off the board and {e(the_steward(possessive=True))} watch at once (an agent at work finishes that turn "
                   "first), nothing waking it. All projects → Archived brings it back, each in the same conversation.</p></details>")
    out.append("</div>")
    from . import opener
    links = [(i, p, x) for i, p in members if p.exists() for x in pins.pins(p)]
    icons = {"url": "🔗", "upload": "📎"}
    tap = lambda got, words, cls: (f"<a class='{cls}' href='{e(got[0])}'{opener.NEW_TAB if got[1] else ''}>{words}</a>" if got
                                   else f"<span class='{cls} gone'>{words}</span>")
    named = lambda x: icons.get(x["kind"], "📄") + " " + e(x["title"])
    out.append(f"<h2>Links ({len(links)})</h2><div class='card'>" + ("".join(
        f"<div class='pin'>{tap(opener.pin_link(i, p, x), named(x), 'pintitle')} <span class='who'>{e(p.name)}</span></div>"
        for i, p, x in links) or "<p class='muted'>Nothing pinned in these projects yet.</p>") + "</div>")
    demos = sorted(((i, p, x) for i, p in members if p.exists() for x in showcase.entries(p)),
                   key=lambda r: r[2]["at"], reverse=True)
    out.append(f"<h2>Demos ({len(demos)})</h2>")
    for i, p, x in demos:
        open_it = tap(opener.entry_link(i, p, x), e(opener.open_label(x)), "open") if x.get("link") else ""
        out.append(f"<div class='card show'><div class='showhead'><b>{e(x['title'])}</b><span class='who'>{e(p.name)} · "
                   f"{e(local_day(x['at']))}</span></div><div>{e(x['what'])}</div>"
                   + (f"<div class='try'><b>Try:</b> {e(x['try'])}</div>" if x.get("try") else "") + open_it
                   + f" <a class='muted' href='/?p={i}&view=showcase'>comment in {e(p.name)}</a></div>")
    if not demos:
        out.append("<p class='muted'>Nothing shown yet in these projects.</p>")
    return shell(reg, -2, "".join(out))


def pin_row(pid, root, p):
    """One thing in the Library: its title over two lines, then why it is kept; a tap opens it through the opener (a
    document to read, Edit at its foot), and its ⋯ holds a comment to the agent about it and Archive. Who pinned it
    and its path are left off: "by the agent" led 56 of the 57 pins ever made, and the path, last, was always cut off
    (the sections review). One whose file has left the project says so, with nothing to tap that leads nowhere."""
    from . import opener
    icon = {"url": "🔗", "upload": "📎"}.get(p["kind"], "📄")
    got = opener.pin_link(pid, root, p)
    hidden = f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='id' value='{e(p['id'])}'>"
    title = (f"<a class='pintitle' href='{e(got[0])}'{opener.NEW_TAB if got[1] else ''}><span class='pt'>{icon} {e(p['title'])}</span></a>"
             if got else f"<span class='pintitle gone'><span class='pt'>{icon} {e(p['title'])}</span></span>")
    why = p.get("why", "") if got else "no longer in the project"
    return (f"<div class='pin'><div class='pinline'>{title}<details class='pinmore'><summary>⋯</summary><div class='pinmenu'>"
            f"<form class='add' method='post' action='/pin/comment'>{hidden}"
            f"<textarea name='text' placeholder='Comment to the agent about this'></textarea><button>Send</button></form>"
            f"<form class='add regroup' method='post' action='/unpin'>{hidden}<input name='why' placeholder='Why it is done (optional)'>"
            f"<button class='quiet'>Archive</button></form></div></details></div>"
            + (f"<div class='who'>{e(why)}</div>" if why else "") + "</div>")


def pinned_section(pid, root):
    """The Library's newest few, at the top of the Overview, the rest a tap away: a direct list, as it was, not its
    headings (P2: they would mostly read "Not under a heading · 19", as quality-lab's would)."""
    rows = [pin_row(pid, root, p) for p in pins.pins(root)]
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
    """A pinned text file, to edit in place (Edit at the foot of its reading page); saving tells the agent quietly. The
    form carries what the file was as it opened (its time and size), so a save over a change made meanwhile (an agent
    writing it) overwrites nothing (P4)."""
    from . import opener
    root = projects(reg)[pid]
    path = pins.inside(root, pin["target"])
    st = path.stat() if path and path.exists() else None
    text = path.read_text(errors="replace") if st else ""
    reader = opener.ref(pid, pin["target"], pin=pin["id"])
    body = (f"<form class='editor' method='post' action='/pin/save'><input type='hidden' name='p' value='{pid}'>"
            f"<input type='hidden' name='id' value='{e(pin['id'])}'><input type='hidden' name='back' value='{e(reader)}'>"
            + (f"<input type='hidden' name='mtime' value='{st.st_mtime_ns}'><input type='hidden' name='size' value='{st.st_size}'>" if st else "")
            + f"<div class='editbar'><a href='{e(reader)}' onclick='if (history.length > 1) {{ history.back(); return false; }}'>"
            f"← {e(pin['title'])}</a><button>Save</button></div><textarea name='text' spellcheck='true'>{e(text)}</textarea></form>")
    return shell(reg, pid, body, wide=True)


def kept_draft(root, path, text):
    """The person's draft of a file that changed while they edited it, kept beside the board's own records (named for
    the file and the time; never in git) so nothing they wrote is lost: its path in the project."""
    folder = Path(root) / ".board" / "drafts"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".gitignore").write_text("*\n")
    rel = str(Path(path).resolve().relative_to(Path(root).resolve()))
    name = re.sub(r"[^A-Za-z0-9._-]", "-", str(Path(rel).with_suffix(""))).strip("-") or "draft"
    draft = folder / f"{name}-{time.strftime('%Y-%m-%d-%H%M%S')}{Path(rel).suffix}"
    draft.write_text(text)
    return str(draft.relative_to(Path(root)))


def clash_page(reg, pid, pin, draft, kept):
    """A save that would have overwritten a change made while the person edited: nothing was overwritten. Their draft,
    kept, and the file as it is now, side by side, with the way to edit it as it now stands."""
    from . import opener
    root = projects(reg)[pid]
    path = pins.inside(root, pin["target"])
    now_text = path.read_text(errors="replace") if path and path.exists() else ""
    reader = opener.ref(pid, pin["target"], pin=pin["id"])
    body = (f"<div class='readtop'><a class='back' href='{e(reader)}'>‹ {e(Path(pin['target']).name)}</a></div>"
            f"<h1>Not saved: {e(pin['title'])} changed while you edited it</h1>"
            f"<p>The file was written to (by the agent, most likely) after you opened it, so your save would have overwritten "
            f"that change. Nothing was overwritten: your draft is kept at <a href='{e(opener.ref(pid, kept))}'><code>{e(kept)}</code></a>, "
            f"and the agent has been told where.</p><p><a class='open' href='/pin/edit?p={pid}&id={e(pin['id'])}'>Edit it as it is now</a></p>"
            f"<div class='clash'><div><h3>Yours</h3><textarea readonly>{e(draft)}</textarea></div>"
            f"<div><h3>Now in the file</h3><textarea readonly>{e(now_text)}</textarea></div></div>")
    return shell(reg, pid, body)


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
            # its own way to write an answer, or to talk it over, is the message box: a message sent now answers it
            shown = [o for o in w["options"] if not re.match(r"(?:[☐☑] )?(?:type something|chat about this)", o, re.I)]
            rows.append(f"<div class='need'>{who('asking in its console')}"
                        + "".join(f"<div>{e(q)}</div>" for q in w["question"])
                        + "<div class='choices'>" + "".join(
                            f"<form method='post' action='/choose'>{hidden}<input type='hidden' name='option' value='{e(o)}'>"
                            f"<button class='quiet'>{e(o)}</button></form>" for o in shown) + "</div>"
                        + ("<div class='muted'>Or answer in a message: sending one closes this, and it goes as your reply.</div>"
                           if providers.of(p).dismiss_keys else "") + f"{foot}</div>")
        elif w["kind"] == "screen":
            rows.append(f"<div class='need'>{who('needs you in its console')}<pre>{e(chr(10).join(w['lines']))}</pre>"
                        f"<a class='go' href='/?p={pid}&view=console'>Open its console →</a>{foot}</div>")
        elif w["kind"] == "ask":
            a = w["ask"]
            rows.append(f"<div class='need'>{who('asked in its console · ' + said_at(a['at']))}"
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
        rows.append(f"<div class='need'><div class='who'><span class='kind'>{e(Path(a['project']).name)} · a find {e(the_steward())} brought</span></div>"
                    f"<b>{e(r['name'])}</b> <span class='muted'>({e(r['status'])}{', reference' if r.get('kind') == 'reference' else ''})</span>"
                    f"<div class='asktext'>{prose(a['text'])}</div><p class='muted'>{e(supports.NOTICE)}</p>"
                    f"<form class='verdict' method='post' action='/support'><input type='hidden' name='id' value='{e(r['id'])}'>"
                    f"{approve}<button name='verdict' value='test' class='{'quiet' if approve else ''}'>Test it</button>"
                    f"<input name='text' placeholder='anything to tell {e(the_steward())}'>"
                    f"<button class='quiet' name='verdict' value='drop'>Drop</button></form>"
                    f"<a class='go' href='/steward?view=console'>Talk it over with {e(the_steward())} →</a></div>")
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
                       + f"<p class='muted'>{e(event['reason'])} · {e(local_day(event['at'], clock=True))}</p>")
            if selection.returnable(state, event):
                out.append("<form method='post' action='/model-decision'><input type='hidden' name='action' value='rollback'>"
                           f"<input type='hidden' name='event' value='{e(event['id'])}'>"
                           "<button>Return to previous choice</button></form>")
        out.append("</details>")
    out.append("<p class='muted'>Benchmark task costs and token rates are separate evidence; actual project spend varies. "
               "Changes take effect when a console reloads while idle.</p></section>")
    return ''.join(out)


def needs_page(reg):
    """Needs you on a page of its own, from its chip beside the steward's: kept current as things are answered."""
    return shell(reg, NEEDS_PAGE, f"<h1>Needs you</h1><div class='card' id='needs-box'><div id='needs'>{needs_you(reg)}</div></div>"
                 f"<script>{KEEP_CURRENT}keepCurrent(document.getElementById('needs'), '/needs');</script>")


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
    return out + (f"<p class='muted'>Also waiting, in projects {e(the_steward())} doesn't follow: {', '.join(aside)}.</p>" if aside else "") + lessons_kept()


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
            rows.append(f"<tr><th>{e(family)} · {e(steward_name() if role == 'monitor' else role)}</th><td>{e(desc)}<div class='muted'>Accepted: {e(accepted)}</div></td></tr>")
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
    return f"<p class='muted'>{e(providers.of(root).reach)}: {'on' if on else 'off'}</p>" if providers.of(root).reach else ""


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
        return (f"<p><b>{e(the_steward(True))} is auditing it</b>: a fresh model reads its code, without running it, and weighs it "
                f"against the work of the project that asked for it{asked}. Approve shows here once that's done.</p>" + remove)
    if a.get("state") in ("unread", "unreviewed"):
        return (f"<p><b>Not audited:</b> {e(a.get('why', ''))}. Approve it only if you trust where it comes from.</p>"
                + _conn_form("approve", "", "/connections", "Approve", name=name) + remove)
    word = {"yes": "yes", "no": "<b class='warn'>no</b>", "unsure": "<b class='warn'>unsure</b>"}
    facts = [f"from {e(a['file'])}" if a.get("file") else "",
             f"names these hosts: {e(', '.join(a['hosts']))}" if a.get("hosts") else "",
             f"depends on (not read): {e(', '.join(a['depends']))}" if a.get("depends") else ""]
    body = (f"<p><b>{'Refused by ' + e(the_steward()) if audit.refused(a) else e(the_steward(True))}'s audit</b>{asked}: safe {word[a['safe']]} · "
            f"fits the work {word[a['fits']]}. {e(a.get('summary', ''))}</p>"
            + ("<ul>" + "".join(f"<li>{e(x)}</li>" for x in a["concerns"]) + "</ul>" if a.get("concerns") else "")
            + f"<details><summary class='muted'>How it was checked</summary><p class='muted'>Read by {e(a.get('by', ''))}, "
              f"{e(a.get('at', ''))}; {'; '.join(f for f in facts if f) or 'nothing to read but its purpose'}.</p>"
            + ("<p class='muted'>Plain marks (where to look, not verdicts): " + e("; ".join(a["marks"])) + "</p>" if a.get("marks") else "")
            + (f"<pre class='report'>{e(a['report'])}</pre>" if a.get("report") else "") + "</details>")
    if audit.refused(a):
        return body + "<p class='muted'>It can't be approved; the agent that asked was told why.</p>" + remove
    if audit.misfit(a):
        return (body + f"<p class='muted'>{e(the_steward(True))} doesn't see the work calling for it; what the work needs is yours to "
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
    from . import autostart, machine, selection, voice
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
                  f"New {e(p.label)} projects are reachable from {e(p.app)} "
                  "<span class='muted'>(each project can turn it off)</span></label>"
                  for k, p in pv.PROVIDERS.items() if pv.usable(p) and p.app)
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
        f"<span class='muted'>(daily; a console moves to the new version once it sits idle, in the same conversation)</span></label>"
        + (f"<label><input type='checkbox' name='autostart' value='on'{' checked' if autostart.on() else ''}> Start with the machine "
           f"<span class='muted'>(after a restart, the board and each console that was running come back, in the same "
           f"conversation; those mid-work are told to carry on)</span></label>" if autostart.available() else "")) + save
    steward_on = own(f"<label><input type='checkbox' name='monitor' value='on'{check('monitor')}> Run {e(the_steward())} with the board</label>"
                     f"<label>Its name <input name='steward_name' value='{e(steward_named())}' placeholder='steward' "
                     f"maxlength='{STEWARD_NAME_MAX}' size='16'></label><p class='muted'>What you call it, as Jarvis: the board "
                     f"shows it, and it answers to it. Blank: steward.</p>") + save
    balance = own(auto_balance_fields()) + save
    port = getattr(settings_page, "port", 8790)
    where = "".join(f"<li><code>{e(u)}</code></li>" for u in urls(port))
    from . import bench
    st = bench.status()
    steps = "".join(f"<li>{e(t)}" + (f" <a href='{e(url)}' target='_blank' rel='noopener'>{e(url)}</a>" if url else "") + "</li>"
                    for t, url in bench.AA_STEPS)
    keybox = (f"<p>{'Connected' if st['connected'] else 'Not connected'}"
              + (f"; data last fetched {e(local_day(st['at'], clock=True))} ({st.get('models', 0)} models)" if st.get("at") else "")
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
    fams = [k for k, p in pv.PROVIDERS.items() if pv.usable(p) and pv.supplies(p, "consult")][:2]

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
        section("steward", e(steward_named() or "Steward"), ("Runs with the board" if s["monitor"] else "Off") + e(f" · {mm or 'its default'}"),
                card(steward_on, monitor_card)),
        section("voice", "Reading aloud", e(f"Default voice: {dict(voice.VOICES).get(default_voice(), default_voice())}") if voice.ready()
                else "Not set up", card(f"<form method='post' action='/voice/default' class='options'><label>Default voice, for every "
                                        f"project without a voice of its own <select name='voice'>{voice.options(default_voice())}</select></label>"
                                        "<button>Save</button></form><p class='muted'>A project's own voice is chosen in its Chat's "
                                        "player (⚙), or when it is made; choosing Default there has it follow this one again.</p>"
                                        if voice.ready() else "<p class='muted'>Reading replies aloud needs its voice set up: "
                                        "<code>colony voice --setup</code>.</p>")),
        section("connections", "Connections", e(connections_summary()),
                card("<p>Outside services your projects reach, and how: <a href='/connections'>Connections →</a></p>")),
        section("board", "This board", e(f"{len(urls(port))} address{'es' * (len(urls(port)) != 1)} · "
                                         f"{'open on your network' if reach() else 'this machine only'}"),
                card(f"<ul class='folders'>{where}</ul><p class='muted'>{e(app_reach())}</p>", board_fields)),
        section("machine", "This machine", e(machine.line() or ("starts with the machine" if autostart.on() else
                                                                "doesn't start with the machine")), card(machine_card())),
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
        if self.headers.get("Origin") not in (None, "null", f"http://{host}", f"https://{host}"):
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
        return origin is None or origin in (f"http://{host}", f"https://{host}")   # https: through Tailscale Serve

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

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _file(self, key, root, path, download=False):
        """A project's file as the browser's own viewer shows it: a page sandboxed (with its colony.send, never the
        steward's), a PDF not (a phone's PDF viewer is a plugin, which a sandbox refuses: a blank page)."""
        data = path.read_bytes()
        if key != "steward" and path.suffix.lower() in (".html", ".htm"):
            data = page_with_send(key, root, path, data)
        self.send_response(200)
        self.send_header("Content-Type", pins.content_type(path.name))
        self.send_header("Content-Disposition", inline(path.name).replace('inline;', 'attachment;', 1) if download else inline(path.name))
        if path.suffix.lower() != ".pdf":
            self.send_header("Content-Security-Policy", SANDBOX)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=300")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        return self.wfile.write(data)

    def _open(self, reg, plist, q):
        """The opener (opener.py): a file, a folder or an address of a project or the steward's, as the board shows
        it. A path outside its folder, or one not there, is not found."""
        from . import monitor, opener
        one = lambda k: (q.get(k) or [""])[0]
        if one("u"):                                   # an address on this machine, as the device asking reaches it
            return (self._redirect(opener.reach(one("u"), self.headers.get("Host", ""))) if re.match(r"https?://", one("u"), re.I)
                    else self._send(404, b"not an address"))
        key = one("p")                                 # the steward's own files as p=steward, never read as project 0
        root = monitor.home() if key == "steward" else plist[int(key)] if key.isdigit() and int(key) < len(plist) else None
        path = pins.inside(root, one("f")) if root else None
        if not path or not path.exists():
            return self._send(404, b"the file is gone")
        if opener.kind(path) == "raw":
            return self._file(key, root, path, download=one('download') == '1')
        return self._send(200, opener.page(reg, key, root, path, pin_id=one("pin"), entry_id=one("s"), whole=bool(one("whole")),
                                           referer=self.headers.get("Referer", ""), sent=bool(one("sent"))).encode())

    def _the_persons(self):
        """Whether this request is the person's own look, kept as theirs (their active hours, their last look at each
        project). Only colony's audit says otherwise (R126: reading every page and file to check them), and only straight
        from this machine: through Tailscale Serve every request reaches the board from here too, the person's identity
        on it, so one carrying that is always theirs."""
        if self.headers.get("X-Colony-Audit") != "read-only" or self.client_address[0] not in ("127.0.0.1", "::1"):
            return True
        return any(self.headers.get(h) for h in ("Tailscale-User-Login", "X-Forwarded-For", "Forwarded"))

    def do_GET(self):
        token = PERSONS_LOOK.set(self._the_persons())
        try:
            return self._get()
        finally:
            PERSONS_LOOK.reset(token)

    def _get(self):
        if not self._from_this_page():
            return self._send(403, b"not from this page")
        if PERSONS_LOOK.get() and self.headers.get("Sec-Fetch-Mode") == "navigate" and self.headers.get("Sec-Fetch-Dest") == "document":
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
            artifact = Path(candidate['artifact'])
            body = artifact.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', pins.content_type(artifact.name))
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
        if url.path == "/open":                       # every file link the person taps on the board (opener.py)
            return self._open(reg, plist, q)
        if url.path == "/showcase/open":              # an entry's older address: the opener takes it, the entry kept
            from . import opener
            x = showcase.get(plist[pid], (q.get("id") or [""])[0]) if plist else None
            if not x or not x.get("link"):
                return self._send(404, b"no such showcase entry")
            if showcase.is_url(x):                    # a link to this machine's localhost, at its own address for a phone
                return self._redirect(opener.reach(x["link"], self.headers.get("Host", "")))
            return self._redirect(opener.ref(pid, x["link"], entry=x["id"]))
        if url.path == "/pin/open":                   # a pin's older address: the opener takes it, the pin kept
            from . import opener
            pin = pins.get(plist[pid], (q.get("id") or [""])[0]) if plist else None
            if not pin:
                return self._send(404, b"no such pin")
            if pin["kind"] == "url":
                return self._redirect(opener.reach(pin["target"], self.headers.get("Host", "")))
            return self._redirect(opener.ref(pid, pin["target"], pin=pin["id"]))
        if url.path == "/pin/edit":                   # Edit, at the foot of a pinned text file's reading page
            pin = pins.get(plist[pid], (q.get("id") or [""])[0]) if plist else None
            path = pins.inside(plist[pid], pin["target"]) if pin and pins.is_text(pin) else None
            if not path or not path.is_file():
                return self._send(404, b"no such pinned text")
            return self._send(200, pin_editor(reg, pid, pin).encode())
        if url.path == "/sweep":                      # every pin and entry on one page, for the person's iPhone check (R126)
            from . import opener
            return self._send(200, opener.sweep(reg).encode())
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
            key = (q.get("p") or ["0"])[0]
            try:
                root = monitor.home() if key == "steward" else plist[int(key)]
            except (ValueError, IndexError):
                return self._send(404, b"No such console")
            path = pins.inside(root, (q.get("f") or [""])[0])
            if not path or not path.is_file():
                return self._send(404, b"the file is gone")
            return self._file(key, root, path)          # as the opener serves it (Chat's links are the opener's now)
        if url.path == "/chat/line":                  # the unheard line, every project's; or the replies heard last
            if q.get("heard"):
                n = q["heard"][0]
                return self._send(200, json.dumps(heard_list(plist, min(50, int(n)) if n.isdigit() else 20)).encode())
            body = json.dumps(line(plist, (q.get("dev") or [""])[0], (q.get("arm") or [""])[0])).encode()
            # unchanged since the page last looked: said in a line, so the phone neither fetches nor compares it again
            tag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
            if self.headers.get("If-None-Match") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("ETag", tag)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if url.path == "/chat/text":                  # a reply's words, fetched as it is about to be read
            try:
                root = plist[int((q.get("p") or [""])[0])]
            except (ValueError, IndexError):
                return self._send(404, b"no such project")
            keys = [k for k in (q.get("k") or [""])[0].split(",") if k]
            return self._send(200, json.dumps({"name": root.name, "voice": voice_of((q.get("p") or [""])[0], root)[0],
                                               "blocks": line_text(root, keys, (q.get("t") or [""])[0])}).encode())
        if url.path == "/chat/voice":                 # the voice a chat reads in, for the player: picked, pinned, the choices
            from . import monitor, voice
            key, ready = (q.get("p") or ["0"])[0], voice.ready()
            if key == "default":                     # the colony's own, for every project without one of its own
                d = default_voice()
                return self._send(200, json.dumps({"voice": d, "own": False, "ready": ready, "default": True,
                                                   "options": voice.options(d) if ready else "",
                                                   "voices": voice.options() if ready else ""}).encode())
            try:
                root = monitor.home() if key == "steward" else plist[int(key)]
            except (ValueError, IndexError):
                return self._send(404, b"No such console")
            name, own = voice_of(key, root)
            return self._send(200, json.dumps({"voice": name, "own": own, "ready": ready,
                                               "options": voice_choices(name if own else "") if ready else "",
                                               "voices": voice.options() if ready else ""}).encode())
        if url.path == "/chat/feed":                  # the chat's conversation, as it stands now
            from . import monitor
            key = (q.get("p") or ["0"])[0]
            try:
                root = monitor.home() if key == "steward" else plist[int(key)]
            except (ValueError, IndexError):
                return self._send(404, b"No such console")
            try:
                shown = min(MOST, max(SHOWN, int((q.get("n") or [SHOWN])[0])))
            except ValueError:
                shown = SHOWN
            body = chat_feed(key, root, shown).encode()
            # unchanged since the page last looked: said so in a line, so the phone neither fetches nor compares it again
            tag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
            if self.headers.get("If-None-Match") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("ETag", tag)
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
        if url.path == "/usage":
            return self._send(200, usage_page(reg).encode())
        if url.path == "/project/settings":           # a project's Settings panel, made as it opens
            try:
                return self._send(200, settings_panel(pid, plist[pid]).encode())
            except IndexError:
                return self._send(404, b"no such project")
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
        if url.path.startswith('/static/'):
            from . import media
            asset = media.asset(url.path.removeprefix('/static/'))
            if asset is None:
                return self._send(404, b'not here')
            data, kind = asset
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if url.path == "/home/feed":
            return self._send(200, home_list(reg).encode())
        if url.path == "/needs":
            if "p" in q:                                   # one project's Waiting on you
                return self._send(200, waiting_html(pid, plist[pid]).encode())
            return self._send(200, needs_you(reg).encode())
        if url.path == "/needs-you":
            return self._send(200, needs_page(reg).encode())
        if url.path == "/steward":
            return self._send(200, monitor_page(reg, (q.get("view") or ["overview"])[0]).encode())
        if url.path == "/status":
            from . import monitor
            body = json.dumps({"board": str(home()), "projects": [
                dict(seen(console.snapshot(p)), waiting=len(moments(p)), work=at_work(p), followed=monitor.involved(p)) if p.exists()
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
        if urllib.parse.urlparse(self.path).path not in ("/codex-pair/status", "/chat/measure", "/chat/heard"):
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
            # a Clear names every part it takes: a long day's line runs past what a form holds
            most = (1 << 20) if urllib.parse.urlparse(self.path).path == "/chat/heard" else 64 * 1024
            length = min(int(self.headers.get("Content-Length") or 0), most)
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
            if "steward_name" in form:
                try:
                    steward_name_of(form["steward_name"])        # checked before anything is saved
                except ValueError as err:
                    return self._send(400, str(err).encode())
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
            if "steward_name" in form:
                set_setting("steward_name", form["steward_name"])
            set_setting("lan", form.get("lan", "off"))
            set_setting("messaging", form.get("messaging", "off"))
            set_setting("trust", form.get("trust", "off"))
            set_setting("auto_update", form.get("auto_update", "off"))
            from . import autostart
            if autostart.available() and autostart.on() != (form.get("autostart") == "on"):
                set_setting("autostart", form.get("autostart", "off"))
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
                    return self._send(409, held(the_steward(), "/steward").encode())
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
                sent = not text or (console.paste_into(console.ensure(src, settle=SETTLE), text, answer=True) if dst == src
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
                if form.get("voice"):
                    choose_voice("0", dest, form["voice"])
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
                    if form.get("voice"):
                        choose_voice("0", folder, form["voice"])
                    try:
                        track(folder, private=form.get("private") == "on")
                    except ValueError as err:
                        return self._send(409, str(err).encode())
                    target = f"/?p={projects().index(folder)}"
            elif path == "/new" and folder_name(form.get("name", "")):
                new = Path(form.get("within") or reg["new_root"]).expanduser() / folder_name(form["name"])
                new.mkdir(parents=True, exist_ok=True)
                if chosen:
                    project_settings(new, chosen)
                connections.pick(new, values.get("svc", []), values.get("svc_listed", []))
                if form.get("voice"):
                    choose_voice("0", new, form["voice"])
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
        if path == "/wake":
            from . import wakes
            try:
                root = projects(reg)[int(form.get("p", ""))]
                wakes.cancel(root, form.get("id", ""), by="person")
                add_note(root, None, f"The person cancelled your timed wake {form.get('id', '')} on the board; it won't come.",
                         author="colony", quiet=True)
            except (ValueError, IndexError, KeyError):
                pass
            self.send_response(303)
            self.send_header("Location", f"/?p={form.get('p', '')}&view=overview")
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
        if path == "/chat/voice":                        # a project's voice chosen (or Default), or the colony's own
            from . import monitor
            key, chosen = values.get("p", ["0"])[0], values.get("voice", [""])[0]
            try:
                if key == "default":
                    return self._send(200, json.dumps({"voice": default_voice(chosen), "own": False}).encode())
                root = monitor.home() if key == "steward" else projects(registry())[int(key)]
                name, own = choose_voice(key, root, chosen)
            except (ValueError, IndexError):
                return self._send(400, b"no such voice or project")
            return self._send(200, json.dumps({"voice": name, "own": own}).encode())
        if path == "/voice/default":                     # Settings: the colony's voice
            try:
                default_voice(form.get("voice", ""))
            except ValueError:
                return self._send(400, b"no such voice")
            self.send_response(303)
            self.send_header("Location", "/settings#set-voice")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/chat/heard":                        # parts of the line heard, skipped, seen or cleared
            try:
                keys = json.loads(values.get("keys", ["{}"])[0])
                how = values.get("how", [""])[0]
                if how in ("skipped", "cleared"):
                    hours.seen()                         # an explicit action; heard/seen can arrive automatically
                got = mark_line(projects(registry()), how, keys if isinstance(keys, dict) else {},
                                values.get("dev", [""])[0], values.get("view", [""])[0])
            except ValueError:
                return self._send(400, b"heard, skipped, seen or cleared, and the parts as {project: [keys]}")
            return self._send(200, json.dumps(got).encode())
        if path == "/chat/unclear":                      # Undo, on a Clear of the last ten minutes
            n = unclear(projects(registry()), values.get("clear", [""])[0], values.get("dev", [""])[0], values.get("view", [""])[0])
            return self._send(200 if n else 410, json.dumps({"back": n}).encode())
        if path == "/chat/measure":                      # where the box landed on the person's phone, typing
            # the player's report carries its last pauses and let-goes, with their causes: room for them
            line = f"{now()} {values.get('m', [''])[0][:1500]} {self.headers.get('User-Agent', '')[:60]}\n"
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
            # a console still coming up is waited for: 204 only once the message has left its box
            if key == "steward":
                sent = not text or console.paste_into(monitor.ensure(), text, answer=True, wait=CHAT_WAIT)
            else:
                src = projects(registry())[int(key)]
                sent = not text or console.paste_into(console.ensure(src, settle=SETTLE), text, answer=True, wait=CHAT_WAIT)
                if sent and text:
                    answer_asks(src, "in the console")
                    append(src, "messages.jsonl", {"type": "message", "at": now(), "text": text, "to": "console"})
                if not sent and not console.live(src):        # not a draft in the way: its agent isn't there
                    return self._send(503, stopped(src).encode())
            if not sent:                                  # what is in the way, so a leftover (a command left typed) shows
                where = monitor.name() if key == "steward" else console.session_name(src)
                if console.asking(where):                 # a choice its program can't put away: answered there first
                    return self._send(409, "Not sent: it is asking you something in its console. Answer it above, then send this again.".encode())
                left = console.draft(where)
                if left is None:                          # no box to type into yet: its program is still starting
                    return self._send(503, NOT_READY.encode())
                return self._send(409, short(" ".join(left.split()), 80).encode())
            self.send_response(204 if sent else 409)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/machine":                         # the person restarting the machine, the projects parked first
            from . import machine
            do = form.get("do", "")
            if do == "cancel":
                machine.cancel()
            elif do in ("restart", "off"):
                machine.start(off=do == "off")
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/left/stop":                       # something a console left running, stopped by the person
            from . import lingering
            lingering.stop(form.get("key", ""))
            self.send_response(303)
            self.send_header("Location", "/projects#left")
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
        if path == "/open/comment":                       # a comment on a file opened in the reader, the file named
            from . import monitor
            key, text = form.get("p", ""), form.get("text", "").strip()
            back = form.get("back", "")
            try:
                root = monitor.home() if key == "steward" else projects(reg)[int(key)] if key.isdigit() else None
            except IndexError:
                root = None
            found = pins.inside(root, form.get("f", "")) if root else None
            if not found or not found.exists():
                return self._send(404, b"No such file")
            rel = str(found.relative_to(Path(root).resolve()))
            if text and key == "steward":
                monitor.queue(f"The person, on {rel} in your folder (opened on the board): {text}")
            elif text:
                add_note(root, {"file": rel}, f"On {rel}, opened on the board: {text}", author="person")
            self.send_response(303)
            self.send_header("Location", back if back.startswith("/") and not back.startswith("//") else "/")
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
                draft = form.get("text", "").replace("\r\n", "\n")
                st = target.stat() if target.exists() else None
                opened = (form.get("mtime"), form.get("size"))
                if opened != (None, None) and opened != ((str(st.st_mtime_ns), str(st.st_size)) if st else (None, None)):
                    # changed since the editor opened it: the person's draft is kept, the file stands, nothing is lost
                    kept = kept_draft(root, target, draft)
                    add_note(root, {"pin": pin["id"]}, f"The person's edit of the pinned {pins.describe(pin)} on the board came "
                             f"after a change to it since they opened it, so nothing was overwritten: their draft is kept at "
                             f"{kept}.", author='colony', quiet=True)
                    return self._send(409, clash_page(reg, pid, pin, draft, kept).encode())
                target.write_text(draft)
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
    from . import serving, voice
    threading.Thread(target=voice.make_samples, daemon=True).start()   # every voice's sample, made once and kept
    threading.Thread(target=serving.keep, args=(port,), daemon=True).start()   # https as it was, and once it can be
    threading.Thread(target=line_keep, daemon=True).start()            # the unheard line, fed with no page open
    mon.start(enabled=monitor and registry()["settings"]["monitor"])
    httpd = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), Handler)
    httpd.lan = lan
    try:                                             # how it answers, for colony urls, colony doctor and Settings (reach)
        home().mkdir(parents=True, exist_ok=True)
        (home() / "listening.json").write_text(json.dumps({"pid": os.getpid(), "port": int(httpd.server_address[1]),
                                                           "lan": bool(lan)}))
    except (OSError, TypeError, ValueError):
        pass                                         # a record to read, never a reason the board doesn't start
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
/* where the menu is a column (a desktop, a phone turned sideways), the player and the message box keep to the page beside it */
@media (min-width: 701px) { body .cplayer, body .gc { left:220px } }
/* and it stays in place as the page scrolls, scrolling itself when it is the longer: in a long chat it went up with the
   page and left an empty column */
@media (min-width: 701px) { nav { position:sticky; top:0; align-self:flex-start; height:100vh; overflow-y:auto } }
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
/* one list in the plan's order: a stop is a fold's line, opening in place to its items (P3) */
.jstop { position:relative; display:flex; gap:12px; align-items:flex-start; padding:10px 8px 10px 0; color:var(--ink); text-decoration:none;
  border-radius:12px; list-style:none; cursor:pointer } .jstop::-webkit-details-marker { display:none }
.jstop:hover { background:var(--sunk) } .jms.later > .jstop { opacity:.65 }
.jms > .jstop .jtitle::after { content:' ▸'; color:var(--muted) } .jms[open] > .jstop .jtitle::after { content:' ▾' }
.jstop .msdot { display:inline-block; vertical-align:middle; margin-right:6px } .jtook .timer { font-size:12px }
.jitems { position:relative; padding:0 0 10px 60px } .jitems > .lanes, .jitems > .pieces { margin-top:4px }
.jring { flex:none; z-index:1; width:48px; height:48px; border-radius:50%; display:flex; align-items:center; justify-content:center;
  background:conic-gradient(var(--accent) calc(var(--p) * 1%), var(--line) 0); position:relative; font-size:13px }
.jring::after { content:''; position:absolute; inset:5px; border-radius:50%; background:var(--bg) }
.jring b, .jring small { position:relative; z-index:1 } .jring small { color:var(--muted); font-size:10px }
.jms.live > .jstop .jring { box-shadow:0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent) }
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
.chat details.needs.blocking { border-width:2px }
.chat .earlier { text-align:center; margin:4px 0 10px }
.troublebar { border:2px solid var(--flag); background:var(--flag-bg); margin-bottom:12px }
.chat details.needs > summary { display:flex; align-items:center; gap:8px; cursor:pointer; font-weight:600; color:var(--flag); list-style:none }
.chat details.needs > summary::-webkit-details-marker { display:none }
/* a chat's Needs you floats on the player (on the message box, where the player isn't shown), its own to that chat:
   folded to a line, it opens upward over the conversation, the latest of what it waits on in view */
.chat details.needs { position:fixed; left:10px; right:10px; bottom:calc(var(--stk, 64px) + 4px); z-index:44; max-width:718px;
  margin:0 auto; max-height:calc(100dvh - var(--stk, 64px) - 120px); overflow:auto; overscroll-behavior:contain;
  box-shadow:0 4px 18px rgba(0,0,0,.16) }
.chat:has(details.needs)::after { content:''; display:block; flex:none; height:var(--ndh, 0px) }
.chat details.needs:not([open]) { padding:2px 12px } html.cpsmall .chat details.needs { right:calc(var(--pillw, 54px) + 18px) }
html.atend, html.atend body { overscroll-behavior-y:none }
html.apart .chat details.needs, body.gctyping .chat details.needs, body.typing .chat details.needs { display:none }
html.answering .chat details.needs { display:block; z-index:60 }
@media (min-width: 701px) { .chat details.needs { left:230px } }
.chat details.need { margin:8px 0 0; padding:8px 10px; border-radius:10px; background:var(--card) }
.chat details.need > summary { cursor:pointer; list-style:none } .chat details.need > summary::-webkit-details-marker { display:none } .chat details.need .fold, .chat details.need[open] .q { display:none }
.chat details.need[open] .fold { display:inline }
.chat details.need > summary { display:flex; align-items:center; gap:8px }
.chat details.need .needplay { flex:none; width:30px; height:30px; min-height:30px; padding:0; border:0; border-radius:50%;
  background:transparent; color:var(--flag); display:inline-flex; align-items:center; justify-content:center }
.chat details.need .needplay svg { width:17px; height:17px }
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
.chat .turnacts .stamp { margin-left:auto; align-self:center; padding-right:4px } .chat .msg.you .stamp { display:block; text-align:right; margin-top:2px }
/* The player: over every page while something is being read or waits to be taken up, above the box and the tabs */
.cplayer { position:fixed; left:0; right:0; bottom:0; z-index:45; padding:0 10px 8px; pointer-events:none }
.cplayer[hidden], .cppanel[hidden] { display:none }
/* typing on a phone, the box rides the keyboard up: the player rides just above it */
.composer button.send.arm { width:auto; min-width:72px; padding:0 14px; border-radius:22px; font-size:15px; font-weight:600; background:var(--flag) }
/* the message box over every page (R112), on the tabs, the player on it */
.gc { position:fixed; left:0; right:0; z-index:44; padding:0 10px 6px; pointer-events:none } .gc[hidden], .gclist[hidden] { display:none }
.gc > * { pointer-events:auto; max-width:740px; margin-left:auto; margin-right:auto }
/* its line is a tab on the box's top edge, on the box's own ground, so the page under it never shows through */
.gchead { display:flex; align-items:baseline; gap:8px; width:max-content; max-width:calc(100% - 28px); margin:0 0 -1px 14px;
  padding:3px 10px 2px; font-size:12.5px; background:color-mix(in srgb, var(--accent) 8%, var(--card));
  border:1px solid color-mix(in srgb, var(--accent) 35%, var(--line)); border-bottom:0; border-radius:10px 10px 0 0;
  position:relative; z-index:1; white-space:nowrap; overflow:hidden }
.gcsaid { color:var(--muted); overflow:hidden; text-overflow:ellipsis }
.gc .gcto { border:0; background:transparent; color:var(--accent); font-weight:600; padding:2px 2px; min-height:0; width:auto; font-size:12.5px; cursor:pointer }
.gc.held .gcto::after { content:' · kept while you write'; color:var(--muted); font-weight:400 }
.gcrow { display:flex; align-items:flex-end; gap:6px; background:color-mix(in srgb, var(--accent) 8%, var(--card));
  border:1px solid color-mix(in srgb, var(--accent) 35%, var(--line)); border-radius:20px; padding:5px 5px 5px 8px;
  box-shadow:0 4px 18px rgba(0,0,0,.16) }
.gcrow textarea { flex:1; min-width:0; border:0; background:transparent; resize:none; font:inherit; font-size:16px; line-height:1.4; color:var(--ink);
  padding:7px 0; max-height:140px; outline:none; box-shadow:none }
.gcattach { display:inline-flex; align-items:center; justify-content:center; width:34px; height:34px; flex:none; margin:1px 0;
  border-radius:50%; color:var(--accent); font-size:22px; cursor:pointer; background:var(--sunk) }
.gcfiles { margin:0 0 4px 14px; padding:2px 4px 2px 10px; width:max-content; max-width:calc(100% - 28px); font-size:12.5px; border-radius:8px;
  background:var(--card); border:1px solid var(--line); display:flex; align-items:center; gap:4px; white-space:nowrap }
.gcfiles[hidden] { display:none } .gcfiles span { overflow:hidden; text-overflow:ellipsis }
.gc .gcfiles button { width:26px; height:26px; min-height:26px; padding:0; flex:none; border:0; border-radius:50%; background:transparent;
  color:var(--muted); font-size:17px; line-height:1; cursor:pointer }
.gc .gcsend { width:36px; height:36px; min-height:36px; padding:0; border-radius:50%; border:0; background:var(--accent); color:var(--card); font-size:18px; flex:none }
.gclist { list-style:none; margin:0 0 6px; padding:4px; background:var(--card); border:1px solid var(--line); border-radius:12px; max-height:42vh;
  overflow-y:auto; box-shadow:0 6px 22px rgba(0,0,0,.22) } .gclist li { padding:10px; border-radius:8px; cursor:pointer; font-size:14.5px }
@media (hover: hover) { .gclist li:hover { background:var(--sunk) } }
body.gcon #composer { display:none } body.gctyping .cplayer { display:none }
/* typing in it, as the chat's own box: the tabs step aside, the page has room below, and between the box and the keyboard
   the page's own ground, where the phone's measure and its keyboard differ, so nothing shows through */
body.gctyping .tabs { display:none } body.gctyping main { padding-bottom:70vh }
body.gctyping .gc::after { content:''; position:absolute; left:0; right:0; top:100%; height:420px; background:var(--bg); pointer-events:none }
/* the end of a page clears what floats over its foot (the message box, the player or its ♪), as measured where they
   are placed: a status line or Send now is never left under them */
body .chat { padding-bottom:calc(12px + var(--room, 0px)) }
main:not(:has(.composer))::after { content:''; display:block; height:calc(var(--room, 0px) + 8px) }
/* At rest, the pill (R123): headphones and how many replies wait; Auto on and able to play, a ring and "Auto · 5"; Auto
   on but the sound let go, "Auto · tap to resume" in the colour of what waits on the person */
.cpminirow { display:none; justify-content:flex-end; pointer-events:none } .cpminirow .cppill { pointer-events:auto } .cplayer.cpidle .cpminirow { display:flex }
.cplayer.cpidle .cpbar { display:none }
.cpminirow { transition:opacity .15s } .cplayer.cpaside .cpminirow { opacity:0 } .cplayer.cpaside .cppill { pointer-events:none }
.cppill { display:inline-flex; align-items:center; gap:8px; width:auto; height:40px; min-height:40px; padding:0 14px; border-radius:999px;
  border:1px solid var(--line); background:var(--card); color:var(--ink); font-size:14px; font-weight:600; white-space:nowrap;
  box-shadow:0 3px 12px rgba(0,0,0,.16); cursor:pointer } .cppill svg { flex:none; color:var(--accent) }
.cppill.none, .cppill.none svg { color:var(--muted) }
.cppill.on { border-color:var(--accent); color:var(--accent); box-shadow:0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent), 0 3px 12px rgba(0,0,0,.16) }
.cppill.held, .cppill.held svg { color:var(--flag) } .cppill.held { border-color:var(--flag); background:var(--flag-bg) }
body.typing .cplayer { top:calc(var(--kbtop, 100vh) - var(--boxh, 0px) - var(--cph, 56px) - 8px); bottom:auto !important }
body.cpon.typing .toend { top:calc(var(--kbtop, 100vh) - 124px - var(--cph, 56px)) } body.cpon.typing .chat { padding-bottom:calc(140px + var(--cph, 56px)) }
html.going main { opacity:.5; transition:opacity .2s .15s }
body.cpon:not(.typing) .toend { bottom:calc(190px + var(--cph, 56px) + var(--ndh, 0px) + env(safe-area-inset-bottom)) }
.cplayer > * { pointer-events:auto; max-width:740px; margin:0 auto }
.cpbar { display:flex; align-items:center; gap:2px; padding:3px 4px; background:color-mix(in srgb, var(--accent) 8%, var(--card));
  border:1px solid color-mix(in srgb, var(--accent) 35%, var(--line));
  border-radius:16px; box-shadow:0 4px 18px rgba(0,0,0,.16) }
.cpbar button { width:40px; height:40px; min-height:40px; padding:0; border:0; border-radius:10px; background:transparent; color:var(--ink);
  display:inline-flex; align-items:center; justify-content:center; flex:none; cursor:pointer } @media (hover: hover) {.cpbar button:hover { background:var(--sunk) }}
.cpbar button:disabled { opacity:.35 }
/* ❚❚, the reading's own button, filled and largest (a pause, where Next was once the big one: the fresh look's finding);
   ⏭ beside it, the lock screen's glyph, Auto's ring circling it while Auto is on */
.cpbar .cphold { width:48px; height:48px; min-height:48px; border-radius:50%; background:var(--accent); color:var(--card) }
@media (hover: hover) {.cpbar .cphold:hover { background:var(--accent); filter:brightness(1.1) }}
.cpbar .cpnext { border-radius:50% } .cpbar .cpnext svg { stroke-width:2.2 }
.cpbar .cpwhat { flex:1; min-width:0; width:auto; height:auto; display:flex; flex-direction:column; align-items:stretch; justify-content:center;
  padding:2px 6px; line-height:1.25; text-align:left; color:inherit; font:inherit }
.cpwhat b { font-size:12px; color:var(--accent); font-weight:600 } .cpwhat b::after { content:' ▾'; opacity:.7 }
.cppaused .cpprev { opacity:.55 } .cplayer.cpheld .cpwhat b { color:var(--flag) }
.cpline { display:flex; align-items:center; gap:10px; margin:0; font-size:14px } .cpline select { flex:1; min-width:0 } .cpprev { font-size:13.5px; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
/* The line (R123): a sheet from the foot of the page over a shade. Chips filter it (never Auto), a row starts there and
   goes on down, Play goes through what shows, Clear takes it out (Undo for ten minutes), Auto is one switch, and the
   voice, speed and mode are one row away */
.cplayer > .cpshade { position:fixed; inset:0; z-index:58; max-width:none; margin:0; background:rgba(0,0,0,.35) }
.cpshade[hidden], .cpsheet[hidden], .cpsheet [hidden] { display:none }
.cplayer > .cpsheet { position:fixed; left:0; right:0; bottom:0; z-index:59; max-height:82dvh; overflow-y:auto; overscroll-behavior:contain;
  padding:4px 18px calc(18px + env(safe-area-inset-bottom)); background:var(--card); border:1px solid var(--line); border-bottom:0;
  border-radius:20px 20px 0 0; box-shadow:0 -10px 30px rgba(0,0,0,.18) }
@media (min-width: 701px) { .cplayer > .cpsheet { left:220px } }
.cpgrab { display:block; width:64px; height:22px; min-height:22px; margin:0 auto 4px; padding:0; border:0; background:transparent; cursor:pointer }
.cpgrab::before { content:''; display:block; width:40px; height:5px; margin:0 auto; border-radius:3px; background:var(--line) }
.cpchips { display:flex; gap:6px; overflow-x:auto; scrollbar-width:none; margin:2px -18px 8px; padding:0 18px } .cpchips::-webkit-scrollbar { display:none }
.cpchips button { flex:none; width:auto; height:32px; min-height:32px; padding:0 12px; border:0; border-radius:999px; background:var(--sunk);
  color:var(--muted); font-size:14px; white-space:nowrap; cursor:pointer } .cpchips button.on { background:var(--accent); color:var(--card); font-weight:600 }
.cprows { list-style:none; margin:0; padding:0; overflow-y:auto } .cpsheet .cprows { max-height:38dvh; margin:0 -6px }
.cprows li { display:flex; gap:10px; align-items:baseline; padding:10px 6px; border-bottom:1px solid var(--line); cursor:pointer; font-size:14px; line-height:1.35 }
.cprows li:last-child { border-bottom:0 } .cprows li.cpnone { color:var(--muted); cursor:default }
@media (hover: hover) { .cprows li:hover { background:var(--sunk) } } .cprows li.now { background:var(--sunk); border-radius:8px } .cprows li.now .cpat { color:var(--accent) }
.cpat { flex:none; min-width:62px; font-size:12px; color:var(--muted); white-space:nowrap }
.cpwords { flex:1; min-width:0 } .cpwords b { color:var(--accent); font-weight:600 }
.cpempty { margin:8px 0 4px; font-size:14.5px; color:var(--muted) }
.cpacts { display:flex; gap:8px; margin-top:12px }
.cpacts .cpplay { flex:1; display:flex; align-items:center; justify-content:center; gap:8px; min-height:48px; border:0; border-radius:14px;
  background:var(--accent); color:var(--card); font-size:15px; font-weight:700; cursor:pointer } .cpacts .cpplay svg { width:18px; height:18px; fill:currentColor }
.cpacts .cpclear { flex:none; width:auto; min-height:48px; padding:0 18px; border:1px solid var(--line); border-radius:14px; background:transparent;
  color:var(--ink); font-size:15px; cursor:pointer }
.cpundo { display:flex; align-items:center; gap:10px; margin-top:8px; font-size:14px; color:var(--muted) }
.cpundo button { width:auto; height:32px; min-height:32px; padding:0 14px; border:0; border-radius:999px; background:var(--sunk); color:var(--accent); font-weight:600; cursor:pointer }
.cpsw { position:relative; display:flex; align-items:center; gap:12px; margin:12px 0 0; padding:12px 0; border-top:1px solid var(--line);
  border-bottom:1px solid var(--line); cursor:pointer }
.cptxt { flex:1; display:flex; flex-direction:column } .cptxt b { font-size:16px } .cptxt span { font-size:13px; color:var(--muted) }
.cpsw input { position:absolute; opacity:0; width:1px; height:1px; pointer-events:none }
.cptoggle { flex:none; position:relative; width:52px; height:32px; border-radius:16px; background:var(--line); transition:background .15s }
.cptoggle::after { content:''; position:absolute; top:3px; left:3px; width:26px; height:26px; border-radius:50%; background:var(--card);
  box-shadow:0 1px 2px rgba(0,0,0,.3); transition:left .15s }
.cpsw input:checked + .cptoggle { background:var(--accent) } .cpsw input:checked + .cptoggle::after { left:23px }
.cpsw input:focus-visible + .cptoggle { outline:2px solid var(--accent); outline-offset:2px }
.cplink { display:flex; justify-content:space-between; align-items:center; width:100%; min-height:46px; padding:0; border:0;
  border-bottom:1px solid var(--line); border-radius:0; background:transparent; color:var(--ink); font-size:15px; text-align:left; cursor:pointer }
.cplink span { color:var(--muted); font-size:18px }
.cpback { width:auto; min-height:36px; padding:0 2px; border:0; background:transparent; color:var(--accent); font-size:15px; cursor:pointer }
.cplbl { margin:12px 0 6px; font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted) }
.cpseg { display:flex; padding:3px; border-radius:12px; background:var(--sunk) }
.cpseg button { flex:1; min-height:38px; padding:0 4px; border:0; border-radius:9px; background:transparent; color:var(--muted); font-size:14px; cursor:pointer }
.cpseg button.on { background:var(--card); color:var(--ink); font-weight:600; box-shadow:0 1px 2px rgba(0,0,0,.15) }
.cpmodesay { margin:6px 2px 10px; font-size:13px; color:var(--muted) } .cpset .cpline { margin:10px 0 }
.cpheard .cprows { max-height:60dvh }
/* the sentence being heard, in Audio + text: a soft mark of the browser's own, in the page's colour, light or dark */
::highlight(cpread) { background-color:color-mix(in srgb, var(--accent) 24%, transparent) }
@media (prefers-color-scheme: dark) { ::highlight(cpread) { background-color:color-mix(in srgb, var(--accent) 34%, transparent) } }
table.tokens { width:100%; border-collapse:collapse; font-size:14px; font-variant-numeric:tabular-nums }
table.tokens th { text-align:right; font-weight:600; color:var(--muted); font-size:12.5px; padding:2px 6px } table.tokens th:first-child { text-align:left }
table.tokens td { text-align:right; padding:3px 6px; white-space:nowrap } table.tokens td:first-child { text-align:left; white-space:normal; overflow-wrap:anywhere }
table.tokens tr.sub td:first-child { padding-left:16px; color:var(--muted) } table.tokens tr.all td { font-weight:600; border-top:1px solid var(--line) }
.uprog { margin:4px 0 8px; font-size:15px } .uprog:not(:first-child) { margin-top:22px; padding-top:14px; border-top:1px solid var(--line) }
.uwin { margin:0 0 14px } .uhead { display:flex; flex-wrap:wrap; justify-content:space-between; align-items:baseline; gap:2px 8px; font-size:14px; margin-bottom:5px } .uhead b { white-space:nowrap }
.ubar { display:flex; height:14px; border-radius:7px; background:var(--sunk); box-shadow:inset 0 0 0 1px var(--line); overflow:hidden }
.ubar span { display:block; height:100%; background:var(--c) } .ubar span + span { box-shadow:inset 1px 0 0 var(--card) }
.ulegend { display:flex; flex-wrap:wrap; gap:3px 12px; margin-top:6px; font-size:12.5px }
.ulegend i, table.tokens i.key { display:inline-block; width:9px; height:9px; border-radius:2px; background:var(--c); margin-right:5px; vertical-align:0 }
@media (prefers-color-scheme: dark) { .ubar span, .ulegend i, table.tokens i.key { background:var(--cd) } }
table.tokens tr.sub td:has(> i.key) { position:relative; padding-left:30px } table.tokens i.key { position:absolute; left:14px; top:.62em }
table.tokens tr.head td { padding-top:12px; font-size:12.5px; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; text-align:left }

.chat .say.reading { background:color-mix(in srgb, var(--accent) 9%, transparent); box-shadow:0 0 0 8px color-mix(in srgb, var(--accent) 9%, transparent);
  border-radius:4px }
.chat .turnacts .spin { transform-origin:12px 12px; animation:spin .9s linear infinite } @keyframes spin { to { transform:rotate(360deg) } }
.voicesheet { position:fixed; left:50%; transform:translateX(-50%); width:min(760px, calc(100% - 24px)); bottom:120px; z-index:36;
  display:flex; flex-wrap:wrap; align-items:center; gap:8px 10px; margin:0; padding:8px 10px 8px 14px; border:1px solid var(--line); border-radius:16px; background:var(--card);
  box-shadow:0 2px 12px rgba(0,0,0,.12); font-size:14px } .voicesheet[hidden] { display:none } .voicesheet .gap { flex:1 }
.voicesheet .aloud { display:flex; align-items:center; gap:6px; color:var(--ink); white-space:nowrap } .voicesheet .aloud input { width:18px; height:18px; margin:0 }
.voicesheet .rate, .voicesheet select { height:32px; min-height:32px; padding:0 10px; border-radius:999px; background:var(--sunk); color:var(--ink);
  border:1px solid var(--line); font-size:14px; line-height:1 } .voicesheet .rate { display:inline-flex; align-items:center; justify-content:center;
  min-width:52px; flex:none } .voicesheet .shut { width:32px; height:32px; min-height:32px; padding:0; border-radius:50%; background:transparent;
  color:var(--muted); font-size:16px }
/* Auto on: a small light circles the player's Next, the way Auto goes on by itself, its tail in the rainbow, glowing; in Chat the
   part being read, while it sounds, has a calm border and a soft glow (the person: "for text highlight ... a gentle
   border solid maybe light glow but no stark animation too visually busy for readability") */
.cpbar .cpnext, .chat .say.reading { position:relative }
html.autoon .cpbar .cpnext::before {
  content:''; position:absolute; inset:1px; border-radius:50%; padding:2px; pointer-events:none;
  background:conic-gradient(transparent 0deg 190deg, #a66bff 250deg, #2fb8ff 290deg, #47e891 320deg, #ffc371 342deg, #ff5f6d 358deg, #fff 360deg);
  -webkit-mask:linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite:xor; mask-composite:exclude;
  filter:drop-shadow(0 0 2px #ff9ad5); animation:autoring 1.8s linear infinite }
@keyframes autoring { to { transform:rotate(360deg) } }
html.autoon.cpgo .chat .say.reading::before { content:''; position:absolute; inset:-8px; border-radius:8px; pointer-events:none;
  border:1.5px solid color-mix(in srgb, var(--accent) 55%, transparent); box-shadow:0 0 10px color-mix(in srgb, var(--accent) 25%, transparent) }
@media (prefers-reduced-motion: reduce) { html.autoon .cpbar .cpnext::before { animation:none } }
.chat .turnacts button { width:36px; height:36px; min-height:36px; padding:0; border:0; border-radius:8px; background:transparent;
  color:var(--muted); font-size:18px; cursor:pointer } @media (hover: hover) {.chat .turnacts button:hover { color:var(--accent); background:var(--sunk) }}
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
.composer .replying { flex:1 1 100%; display:flex; align-items:center; gap:6px; font-size:13px; color:var(--muted);
  padding:2px 0 } .composer .replying[hidden] { display:none } .composer .replying button { width:26px; height:26px;
  min-height:26px; padding:0; border:0; font-size:14px; background:transparent; color:var(--muted) }
.chat .msg.you .re { font-size:12px; color:var(--muted); margin-bottom:3px }
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
.navfoot a.needsbtn { color:var(--flag); font-weight:600 } .navfoot a.needsbtn .badge { margin-left:0; font-weight:700;
  background:var(--flag); color:var(--card); animation:glow 1.6s ease-in-out infinite }
@keyframes glow { 50% { box-shadow:0 0 6px 2px color-mix(in srgb, var(--flag) 70%, transparent) } }
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
/* once the showcase's stars; the reading-speed controls (.voicesheet, .composerbar) are laid out with it too */
.rate { display:flex; gap:2px; margin-top:6px }
.show { min-width:0 } .show .showhead { flex-wrap:wrap } .show > div { overflow-wrap:anywhere }
/* a card: three lines of what it is, then More; one button saying what it opens; a picture fitted to the screen */
.show .what.clamp { display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden }
.show button.more { padding:4px 0; border:0; background:none; color:var(--accent); font-size:13px; cursor:pointer }
.show .try { margin-top:6px; overflow-wrap:anywhere }
.show a.open { display:block; max-width:100%; margin:10px 0 4px; padding:9px 12px; border-radius:10px; text-align:center;
  background:var(--accent); color:var(--card); font-weight:600; text-decoration:none; overflow-wrap:anywhere }
.show a.opensmall { display:inline-block; margin-top:4px; font-size:14px; overflow-wrap:anywhere }
.show .shotlink { display:block; margin-top:8px } .show .shot { display:block; max-width:100%; max-height:60vh; width:auto; height:auto; border-radius:6px }
.showings > summary { cursor:pointer; padding:6px 0 } .showing { border-top:1px solid var(--line); padding:8px 0 }
.pinhead a { margin-left:auto; font-size:14px; text-decoration:none } .pin { padding:7px 0; border-top:1px solid var(--line) }
/* a row: its title over two lines (21 of 43 were cut to one on a phone), then why it is kept, on one */
.pinline { display:flex; align-items:center; gap:8px } .pintitle { display:block; text-decoration:none; font-weight:600; flex:1; min-width:0 }
.pintitle .pt { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; overflow-wrap:anywhere }
.pin .who { white-space:nowrap; overflow:hidden; text-overflow:ellipsis } .pintitle.gone { color:var(--muted); font-weight:400 }
.pin > a.pintitle { display:block }
/* the reading page (opener.py): the document at a readable width, light and dark, its actions at its foot */
.readtop, .readabout, .reader, .readfoot, .sent { max-width:42em; margin-left:auto; margin-right:auto }
.readtop .back { display:inline-block; padding:8px 0; font-size:15px; text-decoration:none } .readabout { margin-top:0; margin-bottom:12px }
.reader { font-size:16.5px; line-height:1.6; overflow-wrap:anywhere }
.reader h1, .reader h2, .reader h3, .reader h4, .reader h5, .reader h6 { color:var(--ink); text-transform:none; letter-spacing:0; line-height:1.3 }
.reader h1 { font-size:24px; margin:6px 0 12px } .reader h2 { font-size:19.5px; margin:26px 0 8px } .reader h3 { font-size:17px; margin:22px 0 6px }
.reader h4, .reader h5, .reader h6 { font-size:15.5px; margin:18px 0 4px } .reader p { margin:0 0 12px }
.reader ul, .reader ol { padding-left:22px; margin:0 0 12px } .reader li { margin:3px 0 }
.reader img { display:block; max-width:100%; height:auto; border-radius:8px } .reader a.pic { display:block; margin:8px 0 }
.reader pre { overflow-x:auto; padding:10px; border-radius:8px; background:var(--sunk); font-size:13px; line-height:1.45 }
.reader code { background:var(--sunk); padding:1px 4px; border-radius:4px } .reader pre code { background:none; padding:0 }
.reader pre.plain { white-space:pre-wrap; overflow-wrap:anywhere; background:transparent; padding:0; font-size:13.5px; line-height:1.5 }
.reader hr { border:0; border-top:1px solid var(--line); margin:22px 0 } .reader .cut { margin-top:18px; color:var(--muted) }
.readfoot { display:flex; gap:10px; align-items:flex-start; margin-top:20px; padding-top:12px; border-top:1px solid var(--line) }
.readcomment { flex:1; min-width:0 } .readcomment > summary { display:inline-block; list-style:none; cursor:pointer }
.readcomment > summary::-webkit-details-marker { display:none }
.readcomment > summary, a.readedit { padding:11px 18px; border:1px solid var(--line); border-radius:10px; background:var(--card); text-decoration:none }
.readcomment[open] > summary { margin-bottom:8px } .sent { color:var(--accent) }
.folder a.frow { display:block; padding:10px 0; border-top:1px solid var(--line); text-decoration:none; overflow-wrap:anywhere }
.fpics { display:grid; gap:14px; margin-top:12px } .fpic { margin:0 } .fpic img { display:block; max-width:100%; max-height:70vh; border-radius:8px }
.clash { display:grid; gap:12px } .clash textarea { width:100%; min-height:40vh; font:13px/1.45 ui-monospace,Menlo,monospace }
@media (min-width:900px) { .clash { grid-template-columns:1fr 1fr } }
.sweep > div { padding:9px 0; border-top:1px solid var(--line) } .sweep > div:first-child { border-top:0 }
.sweep a { text-decoration:none } .sweep a:visited { color:var(--muted) } .waitline { margin:12px 0 } .waitline a { font-weight:600; text-decoration:none }
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
  nav:has(#needs-chip:not([hidden])) .proj.monitor { padding-right:276px }   /* and Needs you's, while shown */
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
  .pintitle { padding:11px 0; line-height:1.3 } a.go, a.dayline, a.showall, .pinlink a, .listtop a, .pinhead a, .nhead a, .titlerow > a {
    display:inline-block; padding-top:12px; padding-bottom:12px } }
"""
