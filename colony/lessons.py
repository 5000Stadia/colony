"""Lessons: what one project's mistake taught, shown to every agent just as it takes that action again.

A lesson is one short sentence keyed to a colony action (asking a gate, showing a deliverable, adding a
connection), proposed from evidence (the person declined something, corrected an answer, rated work low, had an
item redone) by the steward or an agent, and adopted only by the person's tap. Once adopted it is printed in that
command's output, every time an agent runs it, in the project it is for or in all of them: at the moment it
applies, not in a file read once. At most a few per action, so they are read; the steward merges or retires before
adding more.

A lesson is retired on evidence, not age: when the mistake recurs despite it, or the person overrides it, it is
marked suspect, and the person keeps or retires it. Each showing is counted, so a lesson shown and ignored can be
told from one never shown.
"""
import json
import secrets
from pathlib import Path

from . import board

# The colony commands a lesson can ride on: the actions agents take toward the person and each other.
ACTIONS = ("gate", "show", "ready", "connect", "consult", "vision", "send", "reply", "pin", "research", "needs-you",
           "progress", "nest", "noted")
MOST = 3                                    # shown per action; more and they stop being read
LONGEST = 240


def path():
    return board.home() / "lessons.json"


def load():
    try:
        return json.loads(path().read_text())
    except (OSError, ValueError):
        return []


def _save(rows):
    path().parent.mkdir(parents=True, exist_ok=True)
    tmp = path().with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1))
    tmp.replace(path())


def _scope(project):
    return "colony" if not project else str(Path(project).resolve())


def propose(action, text, evidence, by, project=None):
    """A lesson for the person to adopt or decline: for one project, or (no project) every one."""
    action, text = action.strip(), " ".join(text.split())
    if action not in ACTIONS:
        raise ValueError(f"A lesson rides on one of colony's actions: {', '.join(ACTIONS)}.")
    if not text or len(text) > LONGEST:
        raise ValueError(f"A lesson is one sentence, at most {LONGEST} characters (this is {len(text)}).")
    if not evidence.strip():
        raise ValueError("Say what happened that taught it (--evidence): the person adopts it on that.")
    rows = load()
    row = {"id": "l" + secrets.token_hex(4), "action": action, "text": text, "evidence": evidence.strip(),
           "scope": _scope(project), "by": Path(by).name if by else "steward", "state": "proposed",
           "at": board.now(), "shown": 0}
    rows.append(row)
    _save(rows)
    return row


def standing(action, root=None, states=("adopted",)):
    """The lessons for an action in this project: its own, then the colony's."""
    mine = str(Path(root).resolve()) if root else None
    return [r for r in load() if r["action"] == action and r["state"] in states and r["scope"] in ("colony", mine)]


def decide(lid, verdict, words=""):
    """The person's tap: adopt, decline, keep (a suspect one) or retire."""
    moves = {"proposed": {"adopt": "adopted", "decline": "declined"},
             "adopted": {"retire": "retired"}, "suspect": {"keep": "adopted", "retire": "retired"}}
    rows = load()
    row = next(r for r in rows if r["id"] == lid)
    to = moves.get(row["state"], {}).get(verdict)
    if not to:
        raise ValueError(f"{lid} is {row['state']}: it can't be {verdict}ed.")
    row.update(state=to, decided=board.now(), **({"words": words.strip()} if words.strip() else {}))
    _save(rows)
    return row


def suspect(lid, why):
    """Evidence against an adopted lesson (the mistake came back despite it, or the person overrode it)."""
    rows = load()
    row = next(r for r in rows if r["id"] == lid)
    if row["state"] != "adopted":
        raise ValueError(f"{lid} is {row['state']}, not adopted.")
    row.update(state="suspect", why=why.strip(), suspected=board.now())
    _save(rows)
    return row


def remind(action, root):
    """What an agent sees after it runs an action: the adopted lessons for it, each counted as shown."""
    found = standing(action, root)[:MOST]
    if not found:
        return ""
    rows, ids = load(), {r["id"] for r in found}
    for r in rows:
        if r["id"] in ids:
            r["shown"] = r.get("shown", 0) + 1
            r["last_shown"] = board.now()
    _save(rows)
    return (f"Lessons the person adopted for `colony {action}` (if one shows this was off, correct it now):\n"
            + "".join(f"- {r['text']}\n" for r in found))


def waiting():
    """What waits on the person: proposals, and lessons the evidence turned against."""
    return [r for r in load() if r["state"] in ("proposed", "suspect")]
