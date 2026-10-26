"""Digests for the steward: what each project's agent told the person since the steward's last update, in a few
lines from a small model (the project's own chores helper), so the steward reads digests rather than transcripts and
goes into a project's own words only where a digest flags something (a decision, a stall, a drift from what the
person asked). Its own summary and commentary are what the person hears from it, in Chat and in the player.

One digest for each project at each update, and none for a project that said nothing since: a model's call per
project per update at most, never one per reply. What was said in a few words is its own digest.
"""
from . import board

SHORT = 60                  # words: said in fewer, it is its own digest
CAP = 12000                 # characters of what was said that the small model reads at most, the latest


def said_since(root, since):
    """What the agent said to the person since a time (epoch seconds), as the voice would read it: its prose only."""
    from . import chat, context
    s = context.session(root)
    turns = chat.turns(chat.events(s["provider"], s["path"])) if s else []
    out = []
    for t in turns:
        times = t.get("times") or []
        for j, (kind, text) in enumerate(t["items"]):
            when = times[j] if j < len(times) else t.get("last") or t["at"]
            if kind == "say" and _when(when) > since and (words := board.spoken_text(text)):
                out.append(words)
    return out


def _when(stamp):
    """A transcript's time (ISO, as the programs write it) in epoch seconds; 0 where it has none."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def digest(root, since, ask=None):
    """A few lines on what a project's agent said since a time; None where it said nothing."""
    said = "\n\n".join(said_since(root, since))[-CAP:]
    if not said.strip():
        return None
    if len(said.split()) < SHORT:
        return " ".join(said.split())
    brief = ("Below is what a project's agent told the person since the last update. In at most three short lines, say "
             "what it did or found, and anything it needs from the person, is stuck on, or that may be drifting from what "
             "they asked. Plain words, no preamble, no file names.\n\n" + said)
    return (ask or _ask)(root, brief)


def _ask(root, brief):
    """PROVIDER: the project's chores helper, as a consultant (an empty folder, read-only), answers."""
    from . import providers, selection
    pick = selection.helper(root, "chores")
    said = providers.of(root).consult(brief, pick["model"], pick.get("effort") or "low", root)
    return None if said.get("error") else (said.get("text") or "").strip() or None


def all_since(since):
    """{project name: digest} for every project the steward follows that said something since a time."""
    from . import monitor
    out = {}
    for p in board.projects():
        if not p.exists() or p == monitor.home() or not monitor.involved(p):
            continue
        try:
            d = digest(p, since)
        except Exception:
            d = None                                 # one project's unreadable transcript or failed call: left out
        if d:
            out[p.name] = d
    return out
