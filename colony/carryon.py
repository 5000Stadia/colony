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
            # all three places an item's words live: its line, its indented lines in the plan, its details file.
            # item_full gives the file where there is one, so a hold written under the line went unread (Accord, Nov 14)
            if PARKED.search(" ".join((it["text"], it.get("desc", ""), board.item_details(root, it["id"])))):
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
    if board.restore_held():
        return
    from . import hibernate, hours
    now = time.time() if now is None else now
    for root in board.projects():
        if not root.exists():
            continue
        name = console.session_name(root)
        key = str(root)
        try:
            running = console.running(name)
            # asleep (R142) counts as idle: its nudge wakes it (console.type_into), so startable work doesn't wait on it
            state = console.snapshot(root, lines=1)["state"] if running else "idle" if hibernate.asleep(root) else "off"
        except Exception:
            continue
        if state != "idle" or console.drafting(name) or console.attached(name):
            _idle.pop(key, None)
            continue
        first = _idle.setdefault(key, now)
        if now - first < IDLE:
            continue
        try:
            if running:
                from . import chat, lingering
                # its helpers or background work tell it when they end: a nudge meanwhile wakes it for nothing (Bookflow's
                # lead, two weeks to 2026-11-01: 80 nudges, 69 ending within three steps while its helpers still ran)
                if chat.helpers(console.snapshot(root, lines=4, name=name)) or lingering.commands(root, name):
                    continue
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


# --- an item taken up that the person didn't just ask for: its shape, said at the mark (R154) ----------------------

SHAPE = 10 * 60              # seconds after an item is first taken up in which its reminder may still be sent


def shape_due(root, now=None):
    """Items this project took up for the first time in the last few minutes, while the person wasn't in conversation
    with it, and not yet reminded of: [(id, its words)]. Across 33 starts on every project, the shape was told in
    full at 5, and the guide that asks for it was read before the moment and not acted on at it; the one start that
    followed colony's own line gave all five lines. Half the starts were things the person had asked for minutes
    before: those are left alone (their answer, gate g794c00db), by the only sign colony has, that they had just
    spoken to it. Whether they asked for the item longer ago, or a rule of theirs covers it, the agent knows and
    colony doesn't, so the line says so."""
    from . import lead
    now = time.time() if now is None else now
    recs, its = board.item_times(root), board.items(board.roadmap(root))
    told = {i for r in board.read(root, "carryon.jsonl") for i in r.get("shape") or ()}
    said = [r for r in board.read(root, "said.jsonl") if r.get("text")]
    spoke = board.epoch(said[-1]["at"]) if said else 0
    shared = lead.group(root)
    due, quiet = [], []
    for iid, r in recs.items():
        first = r.get("first")
        if r.get("state") != "doing" or not first or first != r.get("started") or now - first > SHAPE or iid in told:
            continue                                  # not under way, taken up again, long since, or already said
        if shared and Path(lead.owner(root, iid)).resolve() != Path(root).resolve():
            continue
        (quiet if first - spoke < TALKING else due).append((iid, (its.get(iid) or {}).get("text", "")))
    if quiet:
        board.append(root, "carryon.jsonl", {"at": board.now(), "shape": [i for i, _ in quiet], "left": "in conversation"})
    return due


def shape(now=None):
    """Called by the watcher: tell each project's agent, once, at the moment it takes up an item the person didn't
    just ask for, to give them the item's shape."""
    from . import providers, usage
    for root in board.projects():
        try:
            if not root.exists() or board.paused(root) or usage.paused().get(providers.key(providers.of(root))):
                continue
            due = shape_due(root, now)
            if not due:
                continue
            names = ", ".join(f"{i} ({t[:60].rsplit(' ', 1)[0] if len(t) > 60 else t})" if t else i for i, t in due[:3])
            text = (f"[colony] You took up {names}. If the person didn't ask for {'it' if len(due) == 1 else 'these'} or "
                    f"agree to {'it' if len(due) == 1 else 'them'}, and no rule of theirs covers it, tell them "
                    f"{'its' if len(due) == 1 else 'each'} shape now if you haven't, in five short lines: what it is, why "
                    f"now, what it rests on, what done looks like, what it will ask of them. It is not a question: carry on. "
                    f"If they did, nothing is needed.")
            if console.type_into(console.session_name(root), text):
                board.append(root, "carryon.jsonl", {"at": board.now(), "shape": [i for i, _ in due]})
        except Exception:
            continue
