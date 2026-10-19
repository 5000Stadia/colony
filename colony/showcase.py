"""The showcase: a project's deliverables worth seeing, lab-style, newest first.

An entry says what was made and how to see or try it: a link (a page the project serves, a URL) or a file in the
project. The agent adds one when a deliverable reaches a stage where it should be good and seeing it would tell the
person something about quality or direction; never for each small piece of a spec. The person may rate it and
comment, never must: nothing waits on them. What they say reaches the agent as a note: a rating quietly, on its next
turn; a comment as a message. Kept in .board/showcase.jsonl; older entries are the project's history of what was
shown (holo-emitter's lab page, generalised).
"""
import re
import secrets
from pathlib import Path

from . import board, pins

FILE = "showcase.jsonl"
FRESH = 6                    # entries shown in full before the rest fold into Earlier


def entries(root):
    """The entries, newest first, each with its latest rating (or None) and its comments, oldest first."""
    out = {}
    for e in board.read(root, FILE):
        if e["type"] == "entry":
            out[e["id"]] = dict(e, stars=None, comments=[])
        elif e["type"] == "rate" and e["of"] in out:
            out[e["of"]]["stars"] = e["stars"]
        elif e["type"] == "comment" and e["of"] in out:
            out[e["of"]]["comments"].append({"at": e["at"], "text": e["text"]})
    return list(out.values())[::-1]             # the log is in the order they were shown: two in one second keep theirs


def get(root, sid):
    return next((x for x in entries(root) if x["id"] == sid), None)


def add(root, title, what, try_="", link="", item=None):
    """A deliverable for the person to see. `link` is a URL or a file in the project (kept as its path in it)."""
    title, what, link = title.strip(), what.strip(), (link or "").strip()
    if not title or not what:
        raise ValueError("An entry needs its title and what it is.")
    if link and not re.match(r"^[a-z][a-z0-9+.-]*://", link, re.I):
        path = pins.inside(root, link)
        if not path or not path.is_file():
            raise FileNotFoundError(link)
        link = str(path.relative_to(Path(root).resolve()))
    entry = {"type": "entry", "id": "s" + secrets.token_hex(3), "at": board.now(), "title": title, "what": what,
             "try": try_.strip(), "link": link, **({"item": item} if item else {})}
    board.append(root, FILE, entry)
    return entry


def rate(root, sid, stars):
    """The person's rating, 1 to 5; it reaches the agent quietly, as direction."""
    entry = get(root, sid)
    if not entry or stars not in range(1, 6):
        return None
    board.append(root, FILE, {"type": "rate", "of": sid, "at": board.now(), "stars": stars})
    return board.add_note(root, {"showcase": sid}, f"The person rated \"{entry['title']}\" in the showcase {stars} of 5.",
                          author="person", quiet=True)


def comment(root, sid, text):
    """The person's comment; it reaches the agent as a message."""
    entry, text = get(root, sid), text.strip()
    if not entry or not text:
        return None
    board.append(root, FILE, {"type": "comment", "of": sid, "at": board.now(), "text": text})
    return board.add_note(root, {"showcase": sid}, f"On \"{entry['title']}\" in the showcase: {text}", author="person")


def is_url(entry):
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", entry.get("link") or "", re.I))


def mime(entry):
    return pins.mime({"target": entry["link"], "kind": "file"})
