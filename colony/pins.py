"""The Library: what the person and the agent keep to refer to, on purpose, each with why it is kept and under a
heading either of them may set (a world bible's places and people, a policy set, research they build on, a page they
work on together). It starts empty and holds only what keeps its value: what is done goes to its archive, folded,
kept, and never read to the agent.

A pin is a file in the project, a link, or a file the person uploaded. Either side pins; the agent hears of
the person's pins, edits and comments as notes: quietly (on its next turn, without waking it) for a pin
or an edit, as a message for a comment. Pins live in .board/pins.jsonl; uploads in .board/uploads/, which
keeps itself out of git.
"""
import mimetypes
import re
import secrets
from pathlib import Path

from . import board

FILE = "pins.jsonl"
TEXT = {".md", ".txt", ".markdown", ".text"}             # opened into an editor on tap
UPLOAD_LIMIT = 25 * 1024 * 1024


def _all(root):
    out = {}
    for e in board.read(root, FILE):
        if e["type"] == "pin":
            out[e["id"]] = e
        elif e["type"] == "unpin":
            out.pop(e["of"], None)
        elif e["type"] == "group" and e["of"] in out:
            out[e["of"]] = dict(out[e["of"]], group=e["group"])
        elif e["type"] == "archive" and e["of"] in out:
            out[e["of"]] = dict(out[e["of"]], archived=e)
        elif e["type"] == "restore" and e["of"] in out:
            out[e["of"]] = {k: v for k, v in out[e["of"]].items() if k != "archived"}
    return sorted(out.values(), key=lambda p: p["at"], reverse=True)


def pins(root):
    """What the Library holds now, newest first."""
    return [p for p in _all(root) if "archived" not in p]


def archived(root):
    """What was put away when its part of the work was done: kept, folded, never read to the agent; newest first."""
    return sorted((p for p in _all(root) if "archived" in p), key=lambda p: p["archived"]["at"], reverse=True)


def archive(root, pin_id, why="", by="person"):
    board.append(root, FILE, {"type": "archive", "of": pin_id, "why": why.strip(), "by": by, "at": board.now()})


def restore(root, pin_id):
    board.append(root, FILE, {"type": "restore", "of": pin_id, "at": board.now()})


def get(root, pin_id):
    return next((p for p in _all(root) if p["id"] == pin_id), None)


def inside(root, rel):
    """A path in the project, or None: nothing outside the project's folder is pinned, opened or written."""
    root = Path(root).resolve()
    try:
        path = (root / rel).resolve()
        path.relative_to(root)
    except (ValueError, OSError):
        return None
    return path


def add(root, target, title="", why="", by="person", kind=None, group=""):
    """Pin a file in the project (a path relative to it), a link, or an upload already saved."""
    kind = kind or ("url" if re.match(r"^[a-z][a-z0-9+.-]*://", target, re.I) else "file")
    if kind in ("file", "upload"):
        path = inside(root, target)
        if not path or not path.is_file():
            raise FileNotFoundError(target)
        target = str(path.relative_to(Path(root).resolve()))
    pin = {"type": "pin", "id": "p" + secrets.token_hex(4), "at": board.now(), "by": by, "kind": kind,
           "target": target, "title": (title or Path(target).name or target).strip(), "why": why.strip(),
           **({"group": group.strip()} if group.strip() else {})}
    board.append(root, FILE, pin)
    return pin


def regroup(root, pin_id, group):
    """Under another heading (empty: none)."""
    board.append(root, FILE, {"type": "group", "of": pin_id, "group": group.strip(), "at": board.now()})


def save_upload(root, filename, data):
    """An uploaded file, kept in .board/uploads/ (which ignores itself in git); its path in the project."""
    if len(data) > UPLOAD_LIMIT:
        raise ValueError("upload too large")
    folder = Path(root) / ".board" / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".gitignore").write_text("*\n")
    name = re.sub(r"[^A-Za-z0-9._ -]", "-", Path(filename or "upload").name).strip() or "upload"
    path = folder / name
    n = 1
    while path.exists():
        path = folder / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    path.write_bytes(data)
    return str(path.relative_to(Path(root)))


def is_text(pin):
    return pin["kind"] != "url" and Path(pin["target"]).suffix.lower() in TEXT


def mime(pin):
    return content_type(pin["target"])


def content_type(name):
    """What a file is served as; text says it is UTF-8, as colony and its agents write it: unsaid, a phone read a
    showcase's .md as Latin-1, its dashes garbled ("$1,730â€"2,540", the sections review, 2026-10-28)."""
    kind = mimetypes.guess_type(str(name))[0] or "application/octet-stream"
    return kind + "; charset=utf-8" if kind.startswith("text/") or kind in ("application/json", "application/javascript") else kind


def describe(pin):
    return f"\"{pin['title']}\" ({pin['target']})"
