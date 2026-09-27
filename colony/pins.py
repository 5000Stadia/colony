"""Pinned: what the person and the agent show each other, kept at the top of the project's page.

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


def pins(root):
    """The pins in place, newest first."""
    out = {}
    for e in board.read(root, FILE):
        if e["type"] == "pin":
            out[e["id"]] = e
        elif e["type"] == "unpin":
            out.pop(e["of"], None)
    return sorted(out.values(), key=lambda p: p["at"], reverse=True)


def get(root, pin_id):
    return next((p for p in pins(root) if p["id"] == pin_id), None)


def inside(root, rel):
    """A path in the project, or None: nothing outside the project's folder is pinned, opened or written."""
    root = Path(root).resolve()
    try:
        path = (root / rel).resolve()
        path.relative_to(root)
    except (ValueError, OSError):
        return None
    return path


def add(root, target, title="", why="", by="person", kind=None):
    """Pin a file in the project (a path relative to it), a link, or an upload already saved."""
    kind = kind or ("url" if re.match(r"^[a-z][a-z0-9+.-]*://", target, re.I) else "file")
    if kind in ("file", "upload"):
        path = inside(root, target)
        if not path or not path.is_file():
            raise FileNotFoundError(target)
        target = str(path.relative_to(Path(root).resolve()))
    pin = {"type": "pin", "id": "p" + secrets.token_hex(3), "at": board.now(), "by": by, "kind": kind,
           "target": target, "title": (title or Path(target).name or target).strip(), "why": why.strip()}
    board.append(root, FILE, pin)
    return pin


def remove(root, pin_id):
    board.append(root, FILE, {"type": "unpin", "of": pin_id, "at": board.now()})


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
    return mimetypes.guess_type(pin["target"])[0] or "application/octet-stream"


def describe(pin):
    return f"\"{pin['title']}\" ({pin['target']})"
