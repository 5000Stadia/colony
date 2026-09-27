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
import subprocess
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import console, pins, providers

MILESTONE = re.compile(r"^##\s+(M\d+)\s*[—–-]+\s*(.+?)\s*$")
ITEM = re.compile(r"^\s*-\s*\[( |x|X|~|\?)\]\s*(R\d+)\s+(.+?)(?:\s*\(after\s+([R\d,\s]+)\))?\s*$")
STATE = {" ": "todo", "~": "doing", "?": "verify", "x": "done", "X": "done"}
LABEL = {"todo": "todo", "doing": "doing", "verify": "to verify", "done": "done"}

PROTOCOL = """
## This project is part of a colony

The colony is the person's set of projects, each with its own agent (you are this project's). The
person follows and steers them all from one board, and the projects can write to each other.

- The plan is `ROADMAP.md`: milestones as `## M1 — name`, items as `- [ ] R1 text` (`[~]` in progress,
  `[?]` built but waiting to be checked or accepted, `[x]` done). Keep it current as you work, and commit
  each finished piece with a clear message.
- The person's notes reach you by themselves, when they are relevant: notes on past work on your next
  turn, notes on a roadmap item once you mark it in progress. Act on each, then
  `colony noted ID "what you did"`. `colony notes` lists any still open.
- When something needs the person (a decision costly to undo, an act that leaves their hands), run
  `colony gate "the question" --item R4 --why "what depends on it"` and do not proceed on that point
  until it is answered; the answer reaches you as a note. If the person settles it with you in
  conversation instead, record it: `colony gate --answered ID "what they decided"`.
- Pin what the person will keep wanting to open (the running app's URL, a deliverable, a finished
  chapter, a shared document) with `colony pin PATH-or-URL --title "..." --why "..."`; `colony pins`
  lists what's pinned. Their pins, edits and comments reach you as notes.
- A turn that ends asking the person something waits for them on the board until they answer. If they
  ask you something first, answer it and end by asking your question again, so it keeps waiting.
- The other projects in the colony are a message away: `colony projects` lists them with their goals.
  When your work depends on one (a format it exports, a behaviour you rely on), ask its agent with
  `colony send NAME --ask "..."` rather than guessing; read its code yourself only when that is clearly
  quicker. Mail from the colony arrives by itself; answer a question with `colony reply ID "..."`.
"""

# Left once, as the person, for a project that joins with work of its own: the notes deliver it on the agent's
# next turn (the watcher starts or wakes the session), and its reply shows on the board.
JOIN = """I've just added this project to my colony: the set of projects I follow and steer from one board, where \
I leave notes and answer your gates, and where the projects can message each other. The new section of \
CLAUDE.md, "This project is part of a colony", says how it works.

Please bring the roadmap on board. Read how this project already plans its work (its plan and spec \
documents, notes, open work and recent history) and write ROADMAP.md in the colony format: milestones as \
`## M1 — name`, items as `- [ ] R1 text`, with `[x]` for done, `[~]` for in progress and `[?]` for built but \
waiting to be checked or accepted. Include what's \
done, what's under way, and features we've discussed but not built, as unchecked items under a later \
milestone. Point each item at the document its detail lives in rather than copying it; the project's own \
documents stay where they are. Open the file with one line saying what this project is: the board shows it \
as the project's goal. Then show me the milestones before treating them as settled."""

SKELETON = """# Roadmap

What we're making, in the person's words.

## M1 — first milestone: what it looks like

- [ ] R1 the smallest end-to-end step
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
    reg["settings"] = dict(DEFAULT_SETTINGS, **reg.get("settings", {}))
    return reg


# The person's global options, with what each means; the board's Settings page and `colony settings` show them.
DEFAULT_SETTINGS = {"provider": "claude", "remote": True, "monitor": True, "lan": True, "messaging": True, "model": "", "effort": "",
                    "permissions": "ask"}
# PROVIDER: the keys are the person's provider-neutral choices; the values are Claude Code's permission modes.
# Another provider maps the same keys to its own approval flags in its command(); move this map into
# ClaudeCode then, and keep only the keys here.
PERMISSIONS = {"ask": None, "edits": "acceptEdits", "all": "bypassPermissions", "plan": "plan"}
SETTING_HELP = {
    "provider": "which CLI runs new projects' agents (colony knows: claude)",
    # PROVIDER: Remote Control is Claude Code's. Another provider maps "remote" to its own way of reaching a
    # session from elsewhere in its command(), or ignores it; say which in this help and in the forms.
    "remote": "new consoles start with Remote Control, reachable from the Claude app",
    "lan": "the board answers other devices on your network, not only this machine",
    "messaging": "project agents can message each other (colony send, colony reply)",
    "permissions": "what new sessions may do unasked: ask, edits, all, or plan",
    "monitor": "the monitor session runs with the board",
    "model": "model for new project sessions (blank: the provider's default)",
    "effort": "effort for new project sessions (blank: the provider's default)",
}


def set_setting(key, value):
    reg = registry()
    if key == "new-folder":
        reg["new_root"] = str(Path(value).expanduser())
        if reg["new_root"] not in reg["roots"]:
            reg["roots"].append(reg["new_root"])
    elif key in ("remote", "monitor", "lan", "messaging"):
        reg["settings"][key] = str(value).lower() in ("on", "true", "yes", "1")
    elif key in ("model", "effort"):
        reg["settings"][key] = str(value).strip()
    elif key == "permissions":
        if value not in PERMISSIONS:
            raise KeyError(key)
        reg["settings"][key] = value
    elif key == "provider":
        from .providers import PROVIDERS
        if value not in PROVIDERS:
            raise KeyError(key)
        reg["settings"][key] = value
    else:
        raise KeyError(key)
    save_registry(reg)
    return reg


PROJECT_KEYS = ("provider", "model", "effort", "permissions", "remote")


def project_settings(root, changes=None):
    """A project's own choices, each falling back to the global setting when not made."""
    path = Path(root) / ".board" / "settings.json"
    own = json.loads(path.read_text()) if path.exists() else {}
    if changes:
        for k, v in changes.items():
            if k not in PROJECT_KEYS:
                raise KeyError(k)
            if v in ("", None, "default"):
                own.pop(k, None)
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
            if sub not in out:
                if not (sub / ".board").exists():
                    track(sub, register=False)
                out.append(sub)
    return out


def save_registry(reg):
    home().mkdir(parents=True, exist_ok=True)
    (home() / "board.json").write_text(json.dumps(reg, indent=2))


# ---------------------------------------------------------------- a project's files

def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True).stdout


def root_of(path="."):
    """The project a command runs in: the nearest folder upward that is on the board (it has a .board).
    Not git's top level: a project can live inside another repository, like colony's own projects/."""
    here = Path(path).resolve()
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


def roadmap(root, text=None):
    """The roadmap as the agent keeps it: the goal line, milestones and their items."""
    if text is None:
        path = Path(root) / "ROADMAP.md"
        text = path.read_text() if path.exists() else ""
    goal = next((l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")), "")
    milestones, current, last, prev = [], None, None, None
    for line in text.splitlines():
        if m := MILESTONE.match(line):
            current = {"id": m.group(1), "title": m.group(2), "items": []}
            milestones.append(current)
            last = None
        elif (m := ITEM.match(line)) and current is not None:
            # An item builds on the one before it unless it says otherwise: `(after R2, R3)` branches.
            after = re.findall(r"R\d+", m.group(4) or "") or ([prev] if prev else [])
            last = {"id": m.group(2), "state": STATE[m.group(1)], "text": m.group(3), "after": after,
                    "desc": "", "milestone": current["id"]}
            current["items"].append(last)
            prev = last["id"]
        elif last is not None and line.startswith(("  ", "\t")) and line.strip():
            last["desc"] = (last["desc"] + " " + line.strip()).strip()     # indented lines describe the item
        elif not line.strip():
            continue
        else:
            last = None
    return {"goal": goal, "milestones": milestones}


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


def record_ask(root, key, text):
    """One entry per turn: the turn's whole text. A newer question replaces an unanswered older one."""
    if not text or not asks_question(text) or any(e.get("key") == key for e in read(root, "asks.jsonl") if key):
        return None
    answer_asks(root, "superseded by a later turn")
    ask = {"type": "ask", "id": "a" + secrets.token_hex(3), "at": now(), "key": key, "text": text.strip()}
    append(root, "asks.jsonl", ask)
    return ask


def answer_asks(root, how):
    for a in asks(root):
        append(root, "asks.jsonl", {"type": "answered", "of": a["id"], "at": now(), "how": how})


def gates(root):
    out = {}
    for e in read(root, "gates.jsonl"):
        if e["type"] == "gate":
            out[e["id"]] = dict(e, answer=None, answered_at=None)
        elif e["type"] == "answer" and e["of"] in out:
            out[e["of"]].update(answer=e["text"], answered_at=e["at"])
    return list(out.values())


def notes(root):
    out = {}
    for e in read(root, "notes.jsonl"):
        if e["type"] == "note":
            out[e["id"]] = dict(e, reply=None, addressed_at=None, delivered_at=None)
        elif e["type"] == "addressed" and e["of"] in out:
            out[e["of"]].update(reply=e["text"], addressed_at=e["at"])
        elif e["type"] == "delivered" and e["of"] in out:
            out[e["of"]]["delivered_at"] = e["at"]
    return list(out.values())


def deliver(root, session=False):
    """What the agent should hear now: notes whose moment has come and that it has not been handed,
    marked as handed; at the start of a session also the ones handed but not yet acted on, so nothing
    sits unanswered however the agent works."""
    fresh = [n for n in open_notes(root) if not n["delivered_at"]]
    for n in fresh:
        append(root, "notes.jsonl", {"type": "delivered", "of": n["id"], "at": now()})
    still = [n for n in open_notes(root) if n["delivered_at"]] if session else []
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


def render_notes(ns, heading):
    if not ns:
        return ""
    lines = [heading]
    for n in ns:
        lines.append(f"- [{n['id']}] {where(n)}: {n['text']}")
    lines.append('When you have acted on one: colony noted ID "what you did".')
    return "\n".join(lines)


def track(path, register=True):
    """Put a project on the board: its roadmap, its .board folder, and its provider's wiring (for Claude Code,
    the colony protocol in CLAUDE.md and the delivery hooks) — added to whatever the project already has."""
    root = Path(path).expanduser().resolve()      # the folder chosen is the root, whatever repository holds it
    ours = {".git", ".board", ".claude", "CLAUDE.md", "ROADMAP.md"}
    # Work of its own: any file colony didn't put there, or commits in its own repository (not an enclosing one).
    joining = any(p.name not in ours for p in root.iterdir()) or \
        ((root / ".git").exists() and bool(git(root, "rev-parse", "--verify", "-q", "HEAD").strip()))
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".board").mkdir(exist_ok=True)
    if not joining and not (root / "ROADMAP.md").exists():
        (root / "ROADMAP.md").write_text(SKELETON)
    has_plan = (root / "ROADMAP.md").exists() and "\n## M" in (root / "ROADMAP.md").read_text()
    if joining and not has_plan and not any(n["text"] == JOIN for n in notes(root)):
        add_note(root, None, JOIN)
    from . import providers
    providers.of(root).wire(root, PROTOCOL)
    reg = registry()
    inside_a_root = any(Path(r) == root.parent for r in reg["roots"])
    if register and not inside_a_root and str(root) not in reg["projects"]:
        reg["projects"].append(str(root))
        save_registry(reg)
    return root


def add_note(root, anchor, text, author="person", quiet=False):
    """A note for the agent. A quiet one reaches it on its next turn like any other, but does not wake it."""
    note = {"type": "note", "id": "n" + secrets.token_hex(3), "at": now(), "author": author,
            "anchor": anchor, "text": text.strip(), **({"quiet": True} if quiet else {})}
    append(root, "notes.jsonl", note)
    return note


def answer_gate(root, gate_id, text, tell=True):
    """The person's answer to a gate. From the board it reaches the agent the way every other word from the
    person does, as a note; settled in conversation, the agent records it and already knows (tell=False)."""
    gate = next(g for g in gates(root) if g["id"] == gate_id)
    append(root, "gates.jsonl", {"type": "answer", "of": gate_id, "at": now(), "text": text.strip()})
    if tell:
        add_note(root, {"gate": gate_id, "item": gate.get("item")}, f"On \"{gate['question']}\": {text.strip()}")


def open_notes(root, item=None):
    """Notes the agent has not yet acted on. Without an item: everything except notes on roadmap items
    not yet started, which wait until the agent reaches them."""
    road_items = items(roadmap(root))
    waiting = [n for n in notes(root) if not n["addressed_at"]]
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
        out += (f"<div class='note'><span class='who'>you · {e(n['at'][:10])}</span><div>{e(n['text'])}</div>"
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
        waiting = len(waiting_items(p))
        new = len(since(p, reg["seen"].get(str(p)))["commits"]) if p.exists() else 0
        badge = f"<span class='badge gate' id='badge-{i}' title='waiting on you'{'' if waiting else ' hidden'}>{waiting}</span>" + \
                (f"<span class='badge new'>{new} new</span>" if new else "")
        snap = console.snapshot(p, lines=1) if p.exists() else {"state": "off", "lines": []}
        last = snap["lines"][-1] if snap["lines"] else ""
        side.append(f"<a class='proj{' on' if i == pid else ''}' href='/?p={i}'><span class='pname'>"
                    f"<span class='sdot {snap['state'].replace(' ', '-')}' id='dot-{i}'></span>{e(p.name)}{badge}</span>"
                    f"<span class='sline' id='sline-{i}'>{e(snap['state'] if snap['state'] != 'off' else '')}"
                    f"{' · ' + e(last) if last else ''}</span></a>")
    side.append("<div class='navfoot'><a href='/add' title='Add project'><span class='long'>+ Add project</span><span class='short'>+</span></a>"
                "<a href='/settings' title='Settings'><span class='long'>Settings</span><span class='short'>⚙</span></a></div>")
    return "".join(side)


def shell(reg, pid, body, wide=False):
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<title>Projects — board</title><style>{CSS}</style></head><body><nav>{sidebar(reg, pid)}</nav>"
            f"<main{' class=wide' if wide else ''}>{body}</main><script>{POLL}</script></body></html>")


# Every few seconds: each project's status dot, line and waiting count in the sidebar.
POLL = """
async function poll() {
  try {
    const r = await fetch('/status', {cache: 'no-store'}); const st = await r.json();
    const md = document.getElementById('dot-m'); if (md) md.className = 'sdot ' + st.monitor.state.replace(' ', '-');
    st.projects.forEach((s, i) => {
      const dot = document.getElementById('dot-' + i), line = document.getElementById('sline-' + i);
      if (dot) dot.className = 'sdot ' + s.state.replace(' ', '-');
      if (line) line.textContent = (s.state === 'off' ? '' : s.state) + (s.lines.length ? ' · ' + s.lines[s.lines.length - 1] : '');
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


def render(reg, pid, view="overview"):
    plist = projects(reg)
    out = []
    if not plist:
        return shell(reg, pid, "")
    if view == "console":
        root = plist[pid]
        others = "".join(f"<option value='{i}'>{e(p.name)}</option>" for i, p in enumerate(plist) if i != pid)
        message = (f"<details class='msgbox'><summary>Message another project in the colony</summary>"
                   f"<form method='post' action='/message'><input type='hidden' name='p' value='{pid}'>"
                   f"<label>To <select name='to'>{others}</select></label>"
                   f"<label class='grow'>What should this agent message them about?"
                   f"<textarea name='text' placeholder='{e(root.name)}’s agent writes the message itself, with its own context'></textarea></label>"
                   f"<button>Have {e(root.name)} send it</button></form></details>") if others else ""
        return shell(reg, pid, f"<header class='slim'><h1>{e(root.name)}</h1>{tabs(pid, view)}{message}</header>"
                     + console.PAGE.format(label=e(providers.of(root).label), path=e(root), name=e(console.session_name(root)), pid=pid, token=console.token(), focus='true', scrolled=e(providers.of(root).scrolled_marker)),
                     wide=True)
    root = plist[pid]
    road, gs = roadmap(root), gates(root)
    all_notes = notes(root)
    by = lambda key, val: [n for n in all_notes if (n["anchor"] or {}).get(key) == val and not (n["anchor"] or {}).get("gate")]
    done = sum(1 for m in road["milestones"] for i in m["items"] if i["state"] == "done")
    total = sum(len(m["items"]) for m in road["milestones"])
    merged, own = project_settings(root)
    # the project's settings tuck into a link on the title's line, opening as a panel, to keep phones' space
    settings = (f"<details class='psettings'><summary>Settings</summary><div class='panel'>{project_settings_form(pid, own)}"
                f"<p class='muted'>Applies when its console next starts.</p></div></details>")
    out.append(f"<header class='project'><div class='titlerow'><h1>{e(root.name)}</h1>{settings}</div>{tabs(pid, view)}"
               f"<p>{e(road['goal'])}</p><p class='muted'>{done} of {total} roadmap items done</p></header>")
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
            for m in road["milestones"]:
                done_m = sum(1 for i in m["items"] if i["state"] == "done")
                out.append(f"<details class='card ms' open><summary><h3>{e(m['id'])} — {e(m['title'])}</h3>"
                           f"<span class='muted'>{done_m} of {len(m['items'])} done</span></summary>")
                for it in m["items"]:
                    i = order.index(it["id"])
                    default = [order[i - 1]] if i else []
                    ns = by("item", it["id"])
                    waiting = sum(1 for g in gs if g.get("item") == it["id"] and not g["answer"])
                    unlocks = [x for x, y in its.items() if it["id"] in y["after"]]
                    out.append(
                        f"<details class='item {it['state']}'><summary><span class='st {it['state']}'>{LABEL[it['state']]}</span> "
                        f"<b>{e(it['id'])}</b> {e(it['text'])}"
                        + (f" <span class='muted'>after {e(', '.join(it['after']))}</span>" if it["after"] != default else "")
                        + (f" <span class='badge gate'>{waiting} waiting</span>" if waiting else "")
                        + (f" <span class='badge'>{len(ns)} notes</span>" if ns else "")
                        + f"</summary><div class='body'><p>{e(it['desc'] or 'No description yet.')}</p>"
                        + (f"<p class='muted'>Unlocks: {e(', '.join(unlocks))}</p>" if unlocks else "")
                        + thread(ns, its)
                        + note_box(pid, "item", it["id"], "A note the agent reads when it works on this item", back=f"/?p={pid}&view=roadmap")
                        + f"<p><a href='/item?p={pid}&id={e(it['id'])}'>Open {e(it['id'])}: its work, gates and full thread →</a></p></div></details>")
                out.append("</details>")
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
        out.append(pinned_section(pid, root))
        # waiting on you: the same as this project's part of Needs you
        waiting = waiting_on(pid, root, f"/?p={pid}", label=False)
        out.append(f"<h2>Waiting on you ({len(waiting)})</h2><div class='card'>"
                   + ("".join(waiting) if waiting else "<p class='muted'>Nothing is waiting on you.</p>") + "</div>")
        # since you were last here: one timeline, newest first; "I'm caught up" rides down the list as you read,
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
                     f"<input type='hidden' name='head' value='{e(s['head'])}'><button>I'm caught up</button></form>")
        out.append(caught_up + f"<ul>{''.join(lines)}</ul>" if lines else "<p class='muted'>Nothing has changed.</p>")
        out.append("</div>")
        out.append("<h2>About the whole project</h2><div class='card'>" + thread([n for n in all_notes if not n["anchor"]], items(road))
                   + note_box(pid, "project", "", "Anything for the agent about the project as a whole") + "</div>")
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
        n_gates = sum(1 for g in gs if g.get("item") == iid and not g["answer"])
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
    body.append(f"<div class='card'><p>{e(it['desc'] or 'No description yet: direct it below and the agent will pick it up.')}</p>"
                f"<p class='muted'>Builds on: {', '.join(link(a) for a in it['after']) or 'nothing'}"
                f"<br>Unlocks: {', '.join(link(u) for u in unlocks) or 'nothing yet'}</p></div>")
    gs = [g for g in gates(root) if g.get("item") == iid]
    if gs:
        body.append("<h2>Gates</h2>")
        for g in gs:
            body.append(f"<div class='card{' gate' if not g['answer'] else ''}'><b>{e(g['question'])}</b><p>{e(g.get('why') or '')}</p>"
                        + (f"<p>Your answer: {e(g['answer'])}</p>" if g["answer"] else
                           f"<form class='add' method='post' action='/answer'><input type='hidden' name='p' value='{pid}'>"
                           f"<input type='hidden' name='gate' value='{e(g['id'])}'><input type='hidden' name='back' value='/item?p={pid}&id={e(iid)}'>"
                           f"<textarea name='text' placeholder='Your answer'></textarea><button>Answer</button></form>") + "</div>")
    work = [l.split("\x1f") for l in git(root, "log", "--format=%h\x1f%aI\x1f%s", f"--grep={iid}\\b", "-E").splitlines() if l]
    body.append("<h2>Work done on it</h2><div class='card'>" + ("".join(
        f"<div class='muted'><code>{e(h)}</code> {e(t_[:10])} {e(s)}</div>" for h, t_, s in work)
        or "<p class='muted'>No commits mention it yet.</p>") + "</div>")
    ns = [n for n in notes(root) if (n["anchor"] or {}).get("item") == iid]
    body.append("<h2>Direction and notes</h2><div class='card'>" + thread(ns, its)
                + note_box(pid, "item", iid, "What should the agent cover or keep in mind for this item?", back=f"/item?p={pid}&id={iid}")
                + "</div>")
    return shell(reg, pid, "".join(body))


def suggestions(name, values):
    """A datalist: plain values, or (value, label) pairs."""
    pair = lambda v: v if isinstance(v, tuple) else (v, "")
    return f"<datalist id='{name}'>" + "".join(
        f"<option value='{e(pair(v)[0])}'>{e(pair(v)[1])}</option>" for v in values) + "</datalist>"


def default_label(value, fallback="Claude Code picks"):
    return f"Default ({value})" if value else f"Default ({fallback})"


def provider_fields(cur, model, effort, blank):
    """Provider, model and effort: the provider from those colony knows, the model and effort free to type,
    with the provider's own suggestions. A project's form (blank="global") starts filled with what the project
    will use; the global form leaves them blank to mean the provider's own, and says what that is."""
    # PROVIDER: the model and effort suggestions are those of the provider shown first, fixed when the page is
    # drawn. With two providers, swap the datalists when the select changes (a few lines of script), and let
    # permissions and Remote Control say when the chosen provider has no equivalent.
    from .providers import PROVIDERS, get
    g = registry()["settings"]
    p = get(cur or g["provider"])
    own = p.own_defaults()
    in_project = blank == "global"
    if in_project:              # a project's form starts filled with what it will actually use
        cur = cur or g["provider"]
        model = model or g["model"] or own["model"] or ""
        effort = effort or g["effort"] or own["effort"] or ""
        dm, de = "Claude Code picks", "Claude Code picks"
    else:                       # the global form: blank leaves it to the provider, and says what that is
        dm = default_label(p.model_name(own["model"]) if own["model"] else None)
        de = default_label(own["effort"])
    return (f"<label>Provider <select name='provider'>"
            + "".join(f"<option value='{k}'{' selected' if cur == k else ''}>{e(v.label)}</option>" for k, v in PROVIDERS.items())
            + "</select></label>"
            f"<label>Model <input name='model' list='models' value='{e(model)}' placeholder='{e(dm)}'></label>"
            f"<label>Effort <input name='effort' list='efforts' value='{e(effort)}' placeholder='{e(de)}'></label>"
            + suggestions("models", p.models) + suggestions("efforts", p.efforts))


def project_settings_form(pid, own, action="/project-settings"):
    """The choices a project can make for itself; blank keeps the global one."""
    opt = lambda name, choices, cur: (f"<select name='{name}'>" + "".join(
        f"<option value='{v}'{' selected' if str(cur) == v else ''}>{label}</option>" for v, label in choices) + "</select>")
    g = registry()["settings"]
    remote = "on" if own.get("remote", g["remote"]) else "off"
    names = {"ask": "ask each time", "edits": "accept edits", "all": "allow everything", "plan": "plan only"}
    return (f"<form method='post' action='{action}' class='options'><input type='hidden' name='p' value='{pid}'>"
            + provider_fields(own.get("provider", ""), own.get("model", ""), own.get("effort", ""), "global") +
            f"<label>Permissions {opt('permissions', [(k, v) for k, v in names.items()], own.get('permissions') or g['permissions'])}</label>"
            # PROVIDER: Remote Control is Claude Code's; see SETTING_HELP["remote"].
            f"<label>Remote Control {opt('remote', [('on', 'on'), ('off', 'off')], remote)}</label>"
            f"<button>Save</button></form>")


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
                     f"<div class='muted'>The folder becomes the project's root; its agent brings its plan over to the roadmap.</div>"
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
           for g in gates(p) if not g["answer"]]
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
    out += [{"kind": "verify", "key": "verify:" + it["id"], "item": it, "summary": f"has {it['id']} to verify: {it['text']}"}
            for it in items(roadmap(p)).values() if it["state"] == "verify"]
    cleared = dismissed(p)
    gone = cleared - {w["key"] for w in out}
    for key in gone:                     # its item has gone: if the same thing comes back later, it shows again
        append(p, "dismissed.jsonl", {"type": "restored", "key": key, "at": now()})
    return [w for w in out if w["key"] not in cleared]


def tell_pinned(root, pin, comment=""):
    """The agent hears of the person's pin: with a comment, as a message; without one, quietly."""
    text = f"The person pinned {pins.describe(pin)} on the board."
    add_note(root, {"pin": pin["id"]}, text + (f" Their comment: {comment}" if comment else ""), quiet=not comment)


def dismissed(root):
    out = set()
    for e in read(root, "dismissed.jsonl"):
        (out.add if e["type"] == "dismissed" else out.discard)(e["key"])
    return out


def clear_waiting(root, key):
    """The person cleared something from what waits on them. A gate or a question is closed; an item to
    verify or a choice on screen is set aside until it goes. The agent hears of it quietly, on its next turn,
    without being woken: it may already be handled."""
    w = next((w for w in waiting_items(root) if w["key"] == key), None)
    if not w:
        return
    if w["kind"] == "gate":
        g = w["gate"]
        answer_gate(root, g["id"], "(cleared by the person without an answer)", tell=False)
        add_note(root, {"gate": g["id"], "item": g.get("item")}, f"The person cleared your gate \"{g['question']}\" from "
                 "their list without answering. Treat it as handled; if it still blocks you, open it again with the reason.", quiet=True)
    elif w["kind"] == "ask":
        answer_asks(root, "cleared by the person")
        asked = [s.strip() for s in re.split(r"(?<=[.!?])\s+", w["ask"]["text"]) if s.strip().endswith("?")]
        add_note(root, None, f"The person cleared your question from their list: \"{(asked or [w['ask']['text'][-200:]])[-1]}\" "
                 "Treat it as handled; ask again only if it still blocks you.", quiet=True)
    else:
        append(root, "dismissed.jsonl", {"type": "dismissed", "key": key, "at": now()})
        if w["kind"] == "verify":
            it = w["item"]
            add_note(root, {"item": it["id"]}, f"The person cleared {it['id']} from their list of things to verify: they "
                     "consider it handled. Update the roadmap if you agree.", quiet=True)


def waiting_on(pid, p, back, label=True):
    """The project's waiting items as the person answers them, where they stand: a gate's answer reaches the
    agent as a note, a choice gets a button per option, a question gets a reply typed into the console, an
    item to verify gets a note on the item."""
    rows = []
    hidden = (f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='back' value='{e(back)}'>")
    for w in waiting_items(p):
        clear = (f"<form class='clear' method='post' action='/clear'>{hidden}<input type='hidden' name='key' value='{e(w['key'])}'>"
                 f"<button class='quiet' title='Clear it: the agent hears quietly, on its next turn'>Clear</button></form>")
        who = lambda what: f"<div class='who'>{clear}<span class='kind'>{e(p.name) + ' · ' if label else ''}{what}</span></div>"
        if w["kind"] == "gate":
            g = w["gate"]
            rows.append(f"<div class='need'>{who('gate' + (' on ' + e(g['item']) if g.get('item') else ''))}"
                        f"<b>{e(g['question'])}</b>" + (f"<p class='muted'>{e(g.get('why') or '')}</p>" if g.get("why") else "")
                        + f"<form class='add' method='post' action='/answer'>{hidden}<input type='hidden' name='gate' value='{e(g['id'])}'>"
                        f"<textarea name='text' placeholder='Your answer'></textarea><button>Send</button></form></div>")
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
                        f"<div class='asktext'>{e(a['text'])}</div>"
                        f"<form class='add' method='post' action='/reply'>{hidden}"
                        f"<textarea name='text' placeholder='Your reply goes straight to its console'></textarea><button>Send</button></form></div>")
        elif w["kind"] == "verify":
            it = w["item"]
            rows.append(f"<div class='need'>{who('to verify')}<b>{e(it['id'])}</b> {e(it['text'])}"
                        f"<form class='add' method='post' action='/note'>{hidden}<input type='hidden' name='kind' value='item'>"
                        f"<input type='hidden' name='ref' value='{e(it['id'])}'>"
                        f"<textarea name='text' placeholder='Checked it? Say so, or say what is wrong'></textarea><button>Send</button></form></div>")
    return rows


def needs_you(reg):
    """Needs you: what every project is waiting on the person for, in one place."""
    rows = [r for pid, p in enumerate(projects(reg)) for r in waiting_on(pid, p, "/monitor")]
    return "".join(rows) if rows else "<p class='muted'>Nothing is waiting on you.</p>"


def settings_page(reg):
    rows = []
    for r in reg["roots"]:
        default = r == reg["new_root"]
        rows.append(f"<li><code>{e(r)}</code>{' <b>new projects go here</b>' if default else ''}"
                    + ("" if default else f"<form class='inline' method='post' action='/roots'><input type='hidden' name='default' value='{e(r)}'><button class='quiet'>Make default</button></form>")
                    + f"<form class='inline' method='post' action='/roots'><input type='hidden' name='remove' value='{e(r)}'><button class='quiet'>Remove</button></form></li>")
    single = "".join(f"<li><code>{e(p)}</code><form class='inline' method='post' action='/roots'><input type='hidden' name='untrack' value='{e(p)}'>"
                     f"<button class='quiet'>Remove from board</button></form></li>" for p in reg["projects"])
    s = reg["settings"]
    check = lambda k: " checked" if s[k] else ""
    options = (f"<form method='post' action='/options' class='options'>"
               # PROVIDER: Remote Control and the Claude app are Claude Code's; see SETTING_HELP["remote"].
               f"<label><input type='checkbox' name='remote' value='on'{check('remote')}> Remote Control for new consoles "
               f"<span class='muted'>(reach them from the Claude app)</span></label>"
               f"<label><input type='checkbox' name='monitor' value='on'{check('monitor')}> Run the monitor with the board</label>"
               f"<label><input type='checkbox' name='lan' value='on'{check('lan')}> Open from other devices on your network "
               f"<span class='muted'>(after colony restart)</span></label>"
               f"<label><input type='checkbox' name='messaging' value='on'{check('messaging')}> Projects can message each other "
               f"<span class='muted'>(one inbox per project)</span></label>"
               f"<label>Permissions for new sessions <select name='permissions'>"
               + "".join(f"<option value='{k}'{' selected' if s['permissions'] == k else ''}>{label}</option>" for k, label in
                         [("ask", "ask each time"), ("edits", "accept edits"), ("all", "allow everything"), ("plan", "plan only")])
               + "</select></label>"
               + provider_fields(s["provider"], s["model"], s["effort"], "the provider's default") +
               f"<label>New projects go in <input name='new_root' value='{e(reg['new_root'])}'></label>"
               f"<button>Save</button><p class='muted'>Provider, model, effort and Remote Control apply to new projects' sessions and to consoles started from now on.</p></form>")
    port = getattr(settings_page, "port", 8790)
    where = "".join(f"<li><code>{e(u)}</code></li>" for u in urls(port))
    body = (f"<header><h1>Settings</h1></header><h2>Open this board</h2><div class='card'><ul class='dirs'>{where}</ul>"
            f"<p class='muted'>Each project, and the monitor, is also in the Claude app when Remote Control is on.</p></div><h2>Options</h2><div class='card'>{options}</div><h2>Project folders</h2><div class='card'>"
            f"<p class='muted'>Every subfolder of these is a project on the board.</p><ul class='dirs'>{''.join(rows) or '<li class=muted>none</li>'}</ul>"
            f"<p><a href='/add?for=root'>+ Add a folder of projects</a></p></div>"
            f"<h2>Projects added one by one</h2><div class='card'><ul class='dirs'>{single or '<li class=muted>none</li>'}</ul>"
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

    def _upload(self):
        """An uploaded file to pin: multipart form, the whole body read (up to the upload limit)."""
        import email.parser, email.policy
        length = int(self.headers.get("Content-Length") or 0)
        if length > pins.UPLOAD_LIMIT + 64 * 1024:
            return self._send(413, b"that file is too large to pin (25 MB at most)")
        raw = self.rfile.read(length)
        msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
            b"Content-Type: " + self.headers.get("Content-Type", "").encode() + b"\r\n\r\n" + raw)
        fields, upload = {}, None
        for part in msg.iter_parts() if msg.is_multipart() else []:
            name = part.get_param("name", header="content-disposition")
            if part.get_filename():
                upload = (part.get_filename(), part.get_payload(decode=True) or b"")
            elif name:
                fields[name] = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
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

    def _send(self, code, body, cookie=None):
        self.send_response(code)
        if cookie:
            self.send_header("Set-Cookie", cookie)
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
        if url.path == "/" and "p" not in q and reg["settings"]["monitor"]:
            self.send_response(303)                    # the monitor is the front page; projects are a click away
            self.send_header("Location", "/monitor")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if url.path == "/":
            # a project opens on the tab last used, so switching projects keeps the person where they were
            view = (q.get("view") or [""])[0]
            if view in ("overview", "roadmap", "console"):
                return self._send(200, render(reg, pid, view).encode(), cookie=f"colony_view={view}; Path=/; SameSite=Lax; Max-Age=31536000")
            last = re.search(r"colony_view=(overview|roadmap|console)", self.headers.get("Cookie", ""))
            return self._send(200, render(reg, pid, last.group(1) if last else "overview").encode())
        if url.path == "/add":
            if (q.get("for") or ["project"])[0] == "project":
                return self._send(200, add_project_page(reg, (q.get("tab") or ["new"])[0], (q.get("error") or [""])[0]).encode())
            return self._send(200, folder_browser(reg, (q.get("dir") or [""])[0], (q.get("for") or ["project"])[0]).encode())
        if url.path == "/add/browse":
            return self._send(200, machine_folders((q.get("dir") or [""])[0]).encode())
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
            text = providers.of(root).history_text(root) or subprocess.run(
                ["tmux", "capture-pane", "-p", "-J", "-S", "-3000", "-t", name], capture_output=True, text=True
            ).stdout.rstrip() or "(nothing yet)"
            body = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(body)
        if url.path == "/needs":
            return self._send(200, needs_you(reg).encode())
        if url.path == "/monitor":
            from . import monitor
            monitor.ensure()
            on = monitor.helm()
            body = (f"<details class='card needs' id='needs-box' open><summary><b>Needs you</b></summary><div id='needs'>{needs_you(reg)}</div></details>"
                    "<script>setInterval(async () => { const n = document.getElementById('needs');"
                    " if (!n || n.contains(document.activeElement)) return;"
                    " const r = await fetch('/needs', {cache: 'no-store'}); if (r.ok) n.innerHTML = await r.text(); }, 4000);</script>"
                    f"<header class='slim'><h1>monitor</h1><form method='post' action='/helm'>"
                    f"<input type='hidden' name='state' value='{'off' if on else 'on'}'>"
                    f"<button class='{'quiet' if on else ''}'>{'Take the helm back' if on else 'Give the monitor the helm'}</button>"
                    f"</form><span class='muted'>{'The monitor answers routine questions for you.' if on else 'The monitor relays and asks; you decide.'}"
                    f"</span></header>" + console.PAGE.format(label="Claude Code", path=e(monitor.home()), name=monitor.name(), pid=-1, token=console.token(), focus='false', scrolled=e(providers.get('claude').scrolled_marker)))
            return self._send(200, shell(reg, -1, body, wide=True).encode())
        if url.path == "/status":
            from . import monitor
            body = json.dumps({"board": str(home()), "projects": [
                dict(console.snapshot(p), waiting=len(waiting_items(p))) if p.exists() else {"state": "off", "lines": [], "waiting": 0}
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
        if urllib.parse.urlparse(self.path).path == "/pin/upload":
            return self._upload()
        length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace")).items()}
        reg = registry()
        path = urllib.parse.urlparse(self.path).path
        if path == "/options":
            set_setting("remote", form.get("remote", "off"))
            set_setting("monitor", form.get("monitor", "off"))
            set_setting("lan", form.get("lan", "off"))
            set_setting("messaging", form.get("messaging", "off"))
            if form.get("permissions"):
                set_setting("permissions", form["permissions"])
            if form.get("provider"):
                set_setting("provider", form["provider"])
            set_setting("model", form.get("model", ""))
            set_setting("effort", form.get("effort", ""))
            if form.get("new_root", "").strip():
                set_setting("new-folder", form["new_root"].strip())
            self.send_response(303)
            self.send_header("Location", "/settings")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/message":
            src, dst = projects(reg)[int(form.get("p", "0"))], projects(reg)[int(form.get("to", "0"))]
            text = form.get("text", "").strip()
            if text:
                from . import mail
                console.type_into(console.ensure(src), mail.instruction(mail.address(dst), text))
            self.send_response(303)
            self.send_header("Location", f"/?p={form.get('p', '0')}&view=console")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/project-settings":
            project_settings(projects(reg)[int(form.get("p", "0"))], {k: form.get(k, "") for k in PROJECT_KEYS})
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
                if chosen:
                    project_settings(Path(form["path"]).expanduser().resolve(), chosen)
                track(Path(form["path"]))
                target = f"/?p={projects().index(root_of(form['path']))}"
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
                save_registry(reg)
            self.send_response(303)
            self.send_header("Location", target)
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
        if path == "/note" and text:
            kind, ref = form.get("kind"), form.get("ref")
            add_note(root, {kind: ref} if kind in ("item", "commit") else None, text)
        elif path == "/answer" and text:
            answer_gate(root, form["gate"], text)
        elif path == "/choose" and form.get("option"):
            name = console.session_name(root)
            keys = providers.of(root).choose(console.screen(name), form["option"])
            if keys:
                console.press(name, keys)
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
                add_note(root, {"pin": pin["id"]}, f"On the pinned {pins.describe(pin)}: {text}")
        elif path == "/pin/save" and form.get("id"):
            pin = pins.get(root, form["id"])
            target = pins.inside(root, pin["target"]) if pin and pins.is_text(pin) else None
            if target:
                target.write_text(form.get("text", "").replace("\r\n", "\n"))
                add_note(root, {"pin": pin["id"]}, f"The person edited the pinned {pins.describe(pin)} on the board.", quiet=True)
        elif path == "/clear" and form.get("key"):
            clear_waiting(root, form["key"])
        elif path == "/reply" and text:
            console.type_into(console.session_name(root), text)
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
    if monitor and registry()["settings"]["monitor"]:
        from . import monitor as mon
        mon.start()
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
form.inline { display:inline; margin-left:8px } input[name=name] { font:inherit; padding:6px 9px; border-radius:7px;
  border:1px solid var(--line); background:var(--bg); color:var(--ink); flex:1 }
form.options { display:flex; flex-direction:column; gap:10px } form.options label { display:flex; gap:10px; align-items:center }
form.options input[type=text], form.options input:not([type]) { font:inherit; padding:5px 8px; border-radius:7px;
  border:1px solid var(--line); background:var(--bg); color:var(--ink); min-width:260px } form.options button { align-self:flex-start }
.msgbox { margin-left:auto } .msgbox summary { cursor:pointer; color:var(--accent); font-size:13px }
.msgbox form { position:absolute; right:24px; z-index:10; width:420px; display:flex; flex-direction:column; gap:8px;
  padding:14px; border-radius:10px; background:var(--card); border:1px solid var(--line); box-shadow:0 8px 24px rgba(0,0,0,.18) }
.msgbox label { display:flex; flex-direction:column; gap:4px; font-size:13px } .msgbox textarea { min-height:80px; font:inherit;
  padding:7px 9px; border-radius:7px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.msgbox select, .options select { font:inherit; padding:4px 6px; border-radius:7px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
.psettings summary { cursor:pointer; color:var(--accent); list-style:none; font-size:14px }
.psettings summary::-webkit-details-marker { display:none } header.project { position:relative }
.titlerow { display:flex; align-items:baseline; gap:12px } .titlerow .psettings, .titlerow .exitlink { margin-left:auto }
.exitlink { font-size:14px; text-decoration:none; white-space:nowrap }
.segs { display:flex; gap:4px; margin:6px 0 12px; padding:3px; border-radius:10px; background:var(--sunk); width:fit-content; max-width:100% }
.seg { background:transparent; color:var(--muted); padding:6px 12px; border-radius:8px } .seg.on { background:var(--card); color:var(--ink); font-weight:600 }
.picker { display:flex; flex-direction:column; gap:6px }
.psettings .panel { position:absolute; right:0; z-index:10; width:min(440px, calc(100vw - 32px)); padding:14px 16px;
  border-radius:10px; background:var(--card); border:1px solid var(--line); box-shadow:0 8px 24px rgba(0,0,0,.18) } hr { border:0; border-top:1px solid var(--line); margin:14px 0 }
.sdot { width:8px; height:8px; border-radius:50%; flex:none; background:transparent; border:1.5px solid var(--line) }
.sdot.working { background:var(--accent); border-color:var(--accent); animation:pulse 1.2s ease-in-out infinite }
.sdot.needs-you { background:var(--flag); border-color:var(--flag) } .sdot.idle { border-color:var(--accent) }
@keyframes pulse { 50% { opacity:.35 } }
.console-bar { display:flex; align-items:center; gap:12px; justify-content:space-between; margin-bottom:8px; font-size:13px }
.console-bar form { margin:0 } button.quiet { background:var(--sunk); color:var(--ink) }
.needs summary { cursor:pointer } .need { border-top:1px solid var(--line); padding:10px 0 }
.pinned { margin:10px 0 6px } .pinhead { display:flex; align-items:baseline; gap:12px } .pinhead h2 { margin:10px 0 6px }
.pinhead a { margin-left:auto; font-size:14px; text-decoration:none } .pin { padding:7px 0; border-top:1px solid var(--line) }
.pinline { display:flex; align-items:center; gap:8px } .pintitle { text-decoration:none; font-weight:600; flex:1; min-width:0;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap } .pin .who { white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
.pinmore { position:relative } .pinmore summary { list-style:none; cursor:pointer; padding:0 8px; color:var(--muted) }
.pinmore summary::-webkit-details-marker { display:none }
.pinmenu { position:absolute; right:0; z-index:10; width:min(360px, calc(100vw - 32px)); padding:10px; border-radius:10px;
  background:var(--card); border:1px solid var(--line); box-shadow:0 8px 24px rgba(0,0,0,.18) } .pinmenu form.add { margin:0 0 8px }
.browse { display:flex; flex-direction:column; gap:4px; max-height:50vh; overflow:auto; flex:1 1 100% } .browse button { text-align:left }
.chosen { display:flex; gap:8px; flex:1 1 100% } .chosen input { flex:1; font:inherit; padding:6px 9px; border-radius:7px; border:1px solid var(--line); background:var(--sunk); color:var(--ink) }
.pickfile { margin:0 } .pinform input { font:inherit; padding:6px 9px; border-radius:7px; border:1px solid var(--line);
  background:var(--bg); color:var(--ink); flex:1 1 100% }
form.editor { display:flex; flex-direction:column; height:calc(100dvh - 24px) } .editbar { display:flex; align-items:center; gap:12px; padding:8px 0 }
.editbar b { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap } form.editor textarea { flex:1; width:100%;
  font:14px/1.5 ui-monospace,Menlo,monospace; padding:12px; border-radius:10px; border:1px solid var(--line); background:var(--card); color:var(--ink) }
.since .caughtup { position:sticky; top:8px; z-index:2; height:34px; margin:0 0 -34px; display:flex; justify-content:flex-end;
  pointer-events:none } .since .caughtup button { pointer-events:auto; box-shadow:0 2px 10px rgba(0,0,0,.25) }
.since ul { padding-right:4px } .since li:first-child { padding-right:128px } .need .who { display:flex; align-items:center; gap:8px } form.clear { margin:0 } form.clear button { padding:2px 10px; font-size:12px } .need .who .kind { margin-left:auto; text-align:right }
.need form.add button { margin-left:auto } .asktext { white-space:pre-wrap; margin:6px 0; max-height:24em; overflow:auto } .need pre { margin:6px 0; font:12px/1.45 ui-monospace,Menlo,monospace; white-space:pre-wrap; color:var(--muted) }
.need form.add { margin-left:0 } .choices { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px } .choices form { margin:0 }
.keys { display:none; gap:6px; flex-wrap:wrap; margin-bottom:8px }
.keys button { flex:1 0 auto; min-width:40px; padding:10px 8px; background:var(--sunk); color:var(--ink); font-size:15px }
.touch-only { display:none }
@media (pointer: coarse) { .keys { display:flex } .touch-only { display:inline-block } }
/* On a phone the console fills the space between the project chips (top) and the tabs (bottom). */
body.focus header > :not(.tabs), body.focus .console-bar, body.focus #needs-box { display:none }
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
.ms[open] > summary { margin-bottom:6px } .item .body { padding:4px 0 6px 18px } .item .body p { margin:4px 0 }
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
  nav .proj.on { background:var(--accent); color:var(--card) } nav .proj.on .sdot { border-color:var(--card) }
  nav .sline, nav .badge.new { display:none }
  .navfoot { flex-direction:row; margin:0 0 0 auto; padding:0; border:0; gap:2px; font-size:16px }
  .navfoot a { padding:4px 9px } .navfoot .long { display:none } .navfoot .short { display:inline }
  nav { position:sticky; top:0; z-index:40 }
  .tabs { position:fixed; left:0; right:0; bottom:0; z-index:40; margin:0; gap:0; background:var(--card);
    border-top:1px solid var(--line); padding:4px 6px calc(4px + env(safe-area-inset-bottom)) }
  .tabs a { flex:1; text-align:center; padding:9px 4px } main { padding-bottom:78px } }
"""
