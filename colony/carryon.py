"""Carry on: an agent sitting idle with roadmap work it can start is told so, by rule, with no model.

Once a project's console has sat idle a while, with nothing typed into it and no one looking at it, no question of
its own waiting on the person, the person not talking with it lately, no version under way (that carries on by
itself), its program not at a usage limit, and roadmap items it owns that can start (not done, not under Later,
nothing they wait on unfinished, no decision about them open), colony types one short wake into its console: those
items, and to carry on with the smartest next one or record what holds it where this rule reads it (a gate on the
item, or "on hold" in its text), since a reply is read by no one. Outside the person's active hours it adds
that they're away, so it takes what doesn't need them. Once per stretch: it is woken again only after something
has changed (a commit, the plan, the person's words); that memory is the project's own carryon.jsonl, so a restart of
the board forgets nothing. An item the person parked (its text says so) is not work to start. A project can turn it
off (its carry_on setting).
"""
import re
import subprocess
import time
from pathlib import Path

from . import board, console

IDLE = 10 * 60               # seconds idle before a wake
TALKING = 15 * 60            # the person spoke to it this recently: they're in a conversation, leave it to them
# The roadmap has no mark for "the person parked this", only their words in the item, so those are read; reading one
# wrongly only leaves out a nudge, which costs less than waking an agent for work it was told to leave.
PARKED = re.compile(r"\b(?:parked|on hold)\b", re.I)
_idle = {}


def startable(root):
    """Items this project owns that can start now, in the plan's order: [id]."""
    from . import lead
    road = board.roadmap(root)
    its = board.items(road)
    open_gates = {g.get("item") for g in board.gates(root) if not g.get("answer")}
    shared = lead.group(root)
    out = []
    for m in road["milestones"]:
        if "later" in (m["id"] + " " + m["title"]).lower():
            continue
        for it in m["items"]:
            if it["state"] not in ("todo", "doing") or it["id"] in open_gates:
                continue
            if PARKED.search(it["text"] + " " + it["desc"]):
                continue
            if shared and Path(lead.owner(root, it["id"])).resolve() != Path(root).resolve():
                continue
            if all(its.get(a, {}).get("state") in (None, "done", "verify") for a in it["after"]):
                out.append(it["id"])
    return out


def signature(root):
    """What has to change before an agent is woken again."""
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    from . import lead
    plan = lead.plan_path(root)
    said = board.read(root, "said.jsonl")
    return (head, plan.stat().st_mtime if plan.exists() else 0, len(said))


def woke(root):
    """The signature the last wake was sent under, from the log (a list, as it was written), or None."""
    sent = [r for r in board.read(root, "carryon.jsonl") if r.get("sig")]
    return sent[-1]["sig"] if sent else None


def wanted(root):
    """Whether this project may be woken to carry on now, by everything but time."""
    from . import progress, providers, usage
    if board.project_settings(root)[1].get("carry_on") == "off" or board.paused(root):
        return False
    if progress.current(root):                       # a version under way carries itself on
        return False
    if usage.paused().get(providers.key(providers.of(root))):
        return False
    if [a for a in board.asks(root) if not a.get("held")] or board.waiting_items(root) and any(
            w["kind"] in ("ask", "choice", "screen") for w in board.waiting_items(root)):
        return False
    said = [r for r in board.read(root, "said.jsonl") if r.get("text")]
    if said and time.time() - board.epoch(said[-1]["at"]) < TALKING:
        return False
    return True


def tick(now=None):
    """Called by the watcher: wake each project that has sat idle long enough with work it can start."""
    from . import hours
    now = time.time() if now is None else now
    for root in board.projects():
        if not root.exists():
            continue
        name = console.session_name(root)
        key = str(root)
        try:
            running = console.running(name)
            state = console.snapshot(root, lines=1)["state"] if running else "off"
        except Exception:
            continue
        if state != "idle" or console.drafting(name) or console.attached(name):
            _idle.pop(key, None)
            continue
        first = _idle.setdefault(key, now)
        if now - first < IDLE:
            continue
        try:
            sig = signature(root)
            if woke(root) == list(sig) or not wanted(root):
                continue
            items = startable(root)
        except Exception:
            continue
        if not items:
            continue
        until = hours.held_until()
        away = "" if hours.active() else (f" The person is away{' until ' + hours.at_day(board.epoch(until)) if until else ''}: "
                                          "take what doesn't need them.")
        text = (f"[colony] Idle, with roadmap items that can start: {', '.join(items[:6])}"
                f"{' and more' if len(items) > 6 else ''}. Carry on with the smartest next one toward the vision. "
                # a hold said only in the reply is read by no one: the next commit would wake it for the same item
                f"If something holds one, record it where it lasts: a decision for the person as a gate on it "
                f"(colony gate ... --item {items[0]}), anything else as \"on hold: why\" in its text.{away}")
        if console.type_into(name, text):
            _idle.pop(key, None)
            board.append(root, "carryon.jsonl", {"at": board.now(), "items": items[:6], "sig": list(sig)})
