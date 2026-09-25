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

from . import console

MILESTONE = re.compile(r"^##\s+(M\d+)\s*[—–-]+\s*(.+?)\s*$")
ITEM = re.compile(r"^\s*-\s*\[( |x|X|~)\]\s*(R\d+)\s+(.+?)(?:\s*\(after\s+([R\d,\s]+)\))?\s*$")
STATE = {" ": "todo", "~": "doing", "x": "done", "X": "done"}

PROTOCOL = """
## The board (how the person follows and steers this project)

- The plan is `ROADMAP.md`: milestones as `## M1 — name`, items as `- [ ] R1 text` (`[~]` in progress,
  `[x]` done). Keep it current as you work, and commit each finished piece with a clear message.
- The person's notes reach you by themselves, when they are relevant: notes on past work on your next
  turn, notes on a roadmap item once you mark it in progress. Act on each, then
  `colony noted ID "what you did"`. `colony notes` lists any still open.
- When something needs the person (a decision costly to undo, an act that leaves their hands), run
  `colony gate "the question" --item R4 --why "what depends on it"` and do not proceed on that point
  until it is answered; the answer reaches you as a note.
"""

# Claude Code runs these and puts what they print in the agent's context: delivery needs no memory.
HOOKS = {"SessionStart": "colony notes --deliver --session", "UserPromptSubmit": "colony notes --deliver"}

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


def registry():
    path = home() / "board.json"
    return json.loads(path.read_text()) if path.exists() else {"projects": [], "seen": {}}


def save_registry(reg):
    home().mkdir(parents=True, exist_ok=True)
    (home() / "board.json").write_text(json.dumps(reg, indent=2))


# ---------------------------------------------------------------- a project's files

def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True).stdout


def root_of(path="."):
    top = git(path, "rev-parse", "--show-toplevel").strip()
    return Path(top) if top else Path(path).resolve()


def read(root, name):
    path = Path(root) / ".board" / name
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def append(root, name, record):
    path = Path(root) / ".board" / name
    path.parent.mkdir(exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


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


def render_notes(ns, heading):
    if not ns:
        return ""
    lines = [heading]
    for n in ns:
        a = n["anchor"] or {}
        where = (f"on gate {a['gate']}" if a.get("gate") else f"on roadmap item {a['item']}" if a.get("item")
                 else f"on commit {a['commit']}" if a.get("commit") else "on the whole project")
        lines.append(f"- [{n['id']}] {where}: {n['text']}")
    lines.append('When you have acted on one: colony noted ID "what you did".')
    return "\n".join(lines)


def track(path):
    """Put a project on the board: its roadmap, its .board folder, the agent's three habits in CLAUDE.md,
    and the delivery hooks in .claude/settings.json — added to whatever the project already has."""
    root = root_of(path)
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".board").mkdir(exist_ok=True)
    if not (root / "ROADMAP.md").exists():
        (root / "ROADMAP.md").write_text(SKELETON)
    claude_md = root / "CLAUDE.md"
    have = claude_md.read_text() if claude_md.exists() else ""
    if "## The board" not in have:
        claude_md.write_text(have + ("\n" if have and not have.endswith("\n") else "") + PROTOCOL.lstrip("\n"))
    settings = root / ".claude" / "settings.json"
    settings.parent.mkdir(exist_ok=True)
    cfg = json.loads(settings.read_text()) if settings.exists() else {}
    for event, command in HOOKS.items():
        entries = cfg.setdefault("hooks", {}).setdefault(event, [])
        if not any(h.get("command") == command for e in entries for h in e.get("hooks", [])):
            entries.append({"hooks": [{"type": "command", "command": command}]})
    settings.write_text(json.dumps(cfg, indent=2) + "\n")
    reg = registry()
    if str(root) not in reg["projects"]:
        reg["projects"].append(str(root))
        save_registry(reg)
    return root


def add_note(root, anchor, text, author="person"):
    note = {"type": "note", "id": "n" + secrets.token_hex(3), "at": now(), "author": author,
            "anchor": anchor, "text": text.strip()}
    append(root, "notes.jsonl", note)
    return note


def answer_gate(root, gate_id, text):
    gate = next(g for g in gates(root) if g["id"] == gate_id)
    append(root, "gates.jsonl", {"type": "answer", "of": gate_id, "at": now(), "text": text.strip()})
    # The answer reaches the agent the way every other word from the person does: as a note.
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
    return {"head": head, "commits": commits, "moved": moved, "opened": opened, "replies": replies,
            "first": not seen}


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
    projects = [Path(p) for p in reg["projects"]]
    ms = monitor.snapshot()
    helm = "at the helm" if monitor.helm() else "helm with you"
    side = [f"<a class='proj monitor{' on' if pid == -1 else ''}' href='/monitor'><span class='pname'>"
            f"<span class='sdot {ms['state'].replace(' ', '-')}' id='dot-m'></span>monitor</span>"
            f"<span class='sline'>{e(ms['state'] if ms['state'] != 'off' else 'not running')} · {helm}</span></a>"]
    for i, p in enumerate(projects):
        waiting = sum(1 for g in gates(p) if not g["answer"]) if p.exists() else 0
        new = len(since(p, reg["seen"].get(str(p)))["commits"]) if p.exists() else 0
        badge = (f"<span class='badge gate'>{waiting}</span>" if waiting else "") + \
                (f"<span class='badge new'>{new} new</span>" if new else "")
        snap = console.snapshot(p, lines=1) if p.exists() else {"state": "off", "lines": []}
        last = snap["lines"][-1] if snap["lines"] else ""
        side.append(f"<a class='proj{' on' if i == pid else ''}' href='/?p={i}'><span class='pname'>"
                    f"<span class='sdot {snap['state'].replace(' ', '-')}' id='dot-{i}'></span>{e(p.name)}{badge}</span>"
                    f"<span class='sline' id='sline-{i}'>{e(snap['state'] if snap['state'] != 'off' else '')}"
                    f"{' · ' + e(last) if last else ''}</span></a>")
    return "".join(side) or "<p class=muted>No projects yet: run <code>colony track</code> in one.</p>"


def shell(reg, pid, body, wide=False):
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Projects — board</title><style>{CSS}</style></head><body><nav>{sidebar(reg, pid)}</nav>"
            f"<main{' class=wide' if wide else ''}>{body}</main><script>{POLL}</script></body></html>")


# Every few seconds: each project's status in the sidebar, and the peek strip if this page has one.
POLL = """
async function poll() {
  try {
    const r = await fetch('/status', {cache: 'no-store'}); const st = await r.json();
    const md = document.getElementById('dot-m'); if (md) md.className = 'sdot ' + st.monitor.state.replace(' ', '-');
    st.projects.forEach((s, i) => {
      const dot = document.getElementById('dot-' + i), line = document.getElementById('sline-' + i);
      if (dot) dot.className = 'sdot ' + s.state.replace(' ', '-');
      if (line) line.textContent = (s.state === 'off' ? '' : s.state) + (s.lines.length ? ' · ' + s.lines[s.lines.length - 1] : '');
      const peek = document.getElementById('peek-' + i);
      if (peek) { peek.hidden = s.state === 'off';
        document.getElementById('peek-state-' + i).textContent = s.state;
        document.getElementById('peek-lines-' + i).textContent = s.lines.join('\\n'); }
    });
  } catch (e) {}
}
setInterval(poll, 2500);
"""


def tabs(pid, view):
    return (f"<div class='tabs'><a class='{'on' if view != 'console' else ''}' href='/?p={pid}'>Overview</a>"
            f"<a class='{'on' if view == 'console' else ''}' href='/?p={pid}&view=console'>Console</a></div>")


def render(reg, pid, view="overview"):
    projects = [Path(p) for p in reg["projects"]]
    out = []
    if not projects:
        return shell(reg, pid, "")
    if view == "console":
        root = projects[pid]
        return shell(reg, pid, f"<header class='slim'><h1>{e(root.name)}</h1>{tabs(pid, view)}</header>"
                     + console.PAGE.format(path=e(root), name=e(console.session_name(root)), pid=pid, token=console.TOKEN),
                     wide=True)
    root = projects[pid]
    road, gs = roadmap(root), gates(root)
    all_notes = notes(root)
    by = lambda key, val: [n for n in all_notes if (n["anchor"] or {}).get(key) == val and not (n["anchor"] or {}).get("gate")]
    done = sum(1 for m in road["milestones"] for i in m["items"] if i["state"] == "done")
    total = sum(len(m["items"]) for m in road["milestones"])
    out.append(f"<header><h1>{e(root.name)}</h1>{tabs(pid, view)}<p>{e(road['goal'])}</p><p class='muted'>{done} of {total} roadmap items done</p></header>")
    snap = console.snapshot(root)
    out.append(f"<a class='peek' id='peek-{pid}' href='/?p={pid}&view=console'{' hidden' if snap['state'] == 'off' else ''}>"
               f"<span class='peek-head'>Console · <b id='peek-state-{pid}'>{e(snap['state'])}</b> · open →</span>"
               f"<pre id='peek-lines-{pid}'>{e(chr(10).join(snap['lines']))}</pre></a>")
    # since you were last here
    s = since(root, reg["seen"].get(str(root)))
    out.append("<h2>Since you were last here</h2><div class='card'>")
    if s["first"]:
        out.append("<p class='muted'>First visit: everything below is the current state.</p>")
    lines = [f"<li><b>{e(a)} → {e(b)}</b> {e(i)} {e(t)}</li>" for i, a, b, t in s["moved"]]
    lines += [f"<li>gate opened: {e(g['question'])}</li>" for g in s["opened"]]
    lines += [f"<li>the agent acted on your note “{e(n['text'][:80])}”: {e(n['reply'])}</li>" for n in s["replies"]]
    lines += [f"<li class='muted'><code>{e(h)}</code> {e(t[:10])} {e(subj)}</li>" for h, t, subj in s["commits"][:15]]
    out.append(f"<ul>{''.join(lines)}</ul>" if lines else "<p class='muted'>Nothing has changed.</p>")
    out.append(f"<form method='post' action='/seen'><input type='hidden' name='p' value='{pid}'>"
               f"<input type='hidden' name='head' value='{e(s['head'])}'><button>I'm caught up</button></form></div>")
    # waiting on you
    open_gates = [g for g in gs if not g["answer"]]
    out.append(f"<h2>Waiting on you ({len(open_gates)})</h2>")
    for g in open_gates:
        item = f" · {e(g['item'])}" if g.get("item") else ""
        out.append(f"<div class='card gate'><b>{e(g['question'])}</b><div class='who'>{e(g['at'][:10])}{item}</div>"
                   f"<p>{e(g.get('why') or '')}</p><form class='add' method='post' action='/answer'>"
                   f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='gate' value='{e(g['id'])}'>"
                   f"<textarea name='text' placeholder='Your answer reaches the agent next time it works'></textarea>"
                   f"<button>Answer</button></form></div>")
    if not open_gates:
        out.append("<div class='card muted'>Nothing is waiting on you.</div>")
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
                    f"<details class='item {it['state']}'><summary><span class='st {it['state']}'>{it['state']}</span> "
                    f"<b>{e(it['id'])}</b> {e(it['text'])}"
                    + (f" <span class='muted'>after {e(', '.join(it['after']))}</span>" if it["after"] != default else "")
                    + (f" <span class='badge gate'>{waiting} waiting</span>" if waiting else "")
                    + (f" <span class='badge'>{len(ns)} notes</span>" if ns else "")
                    + f"</summary><div class='body'><p>{e(it['desc'] or 'No description yet.')}</p>"
                    + (f"<p class='muted'>Unlocks: {e(', '.join(unlocks))}</p>" if unlocks else "")
                    + thread(ns, its)
                    + note_box(pid, "item", it["id"], "A note the agent reads when it works on this item")
                    + f"<p><a href='/item?p={pid}&id={e(it['id'])}'>Open {e(it['id'])}: its work, gates and full thread →</a></p></div></details>")
            out.append("</details>")
    else:
        out.append("<div class='card muted'>No roadmap yet: the agent keeps it in ROADMAP.md.</div>")
    # history
    out.append("<h2>History</h2><div class='card'>")
    for line in git(root, "log", "-n", "20", "--format=%h\x1f%aI\x1f%s").splitlines():
        h, t, subj = line.split("\x1f")
        out.append(f"<details class='item'><summary><code>{e(h)}</code> {e(t[:10])} {e(subj)}"
                   + (f" <span class='badge new'>{len(by('commit', h))} notes</span>" if by("commit", h) else "")
                   + f"</summary>{thread(by('commit', h), items(road))}" + note_box(pid, "commit", h, "A note on this work; it reaches the agent on its next turn") + "</details>")
    out.append("</div><h2>About the whole project</h2><div class='card'>" + thread([n for n in all_notes if not n["anchor"]], items(road))
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
            f"<span class='pop'><b>{e(iid)} — {e(it['text'])}</b><span class='st {it['state']}'>{it['state']}</span>"
            f"<span>{e(it['desc'] or 'No description yet.')}</span>{flags}</span></a>")
    legend = " · ".join(f"<b>{e(m['id'])}</b> {e(m['title'])}" for m in road["milestones"])
    return (f"<div class='card mapcard'><div class='legend'>{legend}</div><div class='mapwrap'><div class='map' "
            f"style='width:{width}px;height:{height}px'><svg width='{width}' height='{height}'>{''.join(edges)}</svg>"
            f"{''.join(nodes)}</div></div></div>")


def render_item(reg, pid, iid):
    """One roadmap item in depth: what it is, where it sits, the work done on it, its gates, and the
    thread where the person directs it."""
    root = Path(reg["projects"][pid])
    road = roadmap(root)
    its = items(road)
    it = its.get(iid)
    if not it:
        return shell(reg, pid, f"<p class='muted'>{e(iid)} is not on the roadmap.</p>")
    unlocks = [i for i, x in its.items() if iid in x["after"]]
    link = lambda i: f"<a href='/item?p={pid}&id={e(i)}'>{e(i)} {e(its[i]['text'])}</a>" if i in its else e(i)
    body = [f"<p><a href='/?p={pid}'>← {e(root.name)}</a></p><header><h1>{e(iid)} — {e(it['text'])}</h1>"
            f"<p><span class='st {it['state']}'>{it['state']}</span> · milestone {e(it['milestone'])}</p></header>"]
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
        if not origin_ok or not secrets.compare_digest(token, console.TOKEN) or \
                self.headers.get("Upgrade", "").lower() != "websocket":
            return self._send(403, b"not from this page")
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", console.accept(self.headers["Sec-WebSocket-Key"]))
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True
        console.bridge(self.connection, root, name, label)

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
        pid = min(max(int((q.get("p") or ["0"])[0]), 0), max(len(reg["projects"]) - 1, 0))
        if url.path == "/":
            return self._send(200, render(reg, pid, (q.get("view") or ["overview"])[0]).encode())
        if url.path == "/monitor":
            from . import monitor
            monitor.ensure()
            on = monitor.helm()
            body = (f"<header class='slim'><h1>monitor</h1><form method='post' action='/helm'>"
                    f"<input type='hidden' name='state' value='{'off' if on else 'on'}'>"
                    f"<button class='{'quiet' if on else ''}'>{'Take the helm back' if on else 'Give the monitor the helm'}</button>"
                    f"</form><span class='muted'>{'The monitor answers routine questions for you.' if on else 'The monitor relays and asks; you decide.'}"
                    f"</span></header>" + console.PAGE.format(path=e(monitor.home()), name=monitor.name(), pid=-1, token=console.TOKEN))
            return self._send(200, shell(reg, -1, body, wide=True).encode())
        if url.path == "/status":
            from . import monitor
            body = json.dumps({"projects": [console.snapshot(Path(p)) if Path(p).exists() else {"state": "off", "lines": []}
                                            for p in reg["projects"]], "monitor": monitor.snapshot()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(body)
        if url.path == "/console/ws" and (q.get("p") or [""])[0] == "-1":
            from . import monitor
            return self._console(monitor.home(), (q.get("t") or [""])[0], name=monitor.name(), label="monitor")
        if url.path == "/console/ws" and reg["projects"]:
            return self._console(Path(reg["projects"][pid]), (q.get("t") or [""])[0])
        if url.path == "/item" and reg["projects"]:
            return self._send(200, render_item(reg, pid, (q.get("id") or [""])[0]).encode())
        self._send(404, b"not here")

    def do_POST(self):
        if not self._from_this_page():
            return self._send(403, b"not from this page")
        length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace")).items()}
        reg = registry()
        path = urllib.parse.urlparse(self.path).path
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
        root = Path(reg["projects"][pid])
        text = form.get("text", "").strip()
        path = urllib.parse.urlparse(self.path).path
        if path == "/note" and text:
            kind, ref = form.get("kind"), form.get("ref")
            add_note(root, {kind: ref} if kind in ("item", "commit") else None, text)
        elif path == "/answer" and text:
            answer_gate(root, form["gate"], text)
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
    if monitor:
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
.item.doing .st { color:var(--accent); font-weight:600 }
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
.sdot { width:8px; height:8px; border-radius:50%; flex:none; background:transparent; border:1.5px solid var(--line) }
.sdot.working { background:var(--accent); border-color:var(--accent); animation:pulse 1.2s ease-in-out infinite }
.sdot.needs-you { background:var(--flag); border-color:var(--flag) } .sdot.idle { border-color:var(--accent) }
@keyframes pulse { 50% { opacity:.35 } }
.peek { display:block; margin:0 0 16px; padding:10px 14px; border-radius:10px; background:#16171a; color:#d7d4ce; text-decoration:none }
.peek[hidden] { display:none } .peek-head { font-size:12px; color:#9a9791 } .peek-head b { color:#7fb5a2 }
.peek pre { margin:6px 0 0; font:12px/1.45 ui-monospace,Menlo,monospace; white-space:pre-wrap; max-height:9em; overflow:hidden }
.console-bar { display:flex; align-items:center; gap:12px; justify-content:space-between; margin-bottom:8px; font-size:13px }
.console-bar form { margin:0 } button.quiet { background:var(--sunk); color:var(--ink) }
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
@media (max-width: 700px) { body { display:block } nav { width:auto; border-right:0; border-bottom:1px solid var(--line) } }
"""
