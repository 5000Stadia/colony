"""Finding a file in a project to add to its Library (colony/pins.py holds the Library itself): its tracked files
that are read rather than run, each by its own title (a document's first heading, a page's <title>) and first line.
"""
import json
import os
import re
import subprocess
from pathlib import Path

from . import board

KINDS = {".md": "doc", ".markdown": "doc", ".txt": "doc", ".pdf": "pdf", ".html": "page", ".htm": "page",
         ".png": "picture", ".jpg": "picture", ".jpeg": "picture", ".gif": "picture", ".webp": "picture", ".svg": "picture",
         ".csv": "data", ".tsv": "data", ".mp3": "sound", ".wav": "sound", ".m4a": "sound", ".mp4": "video", ".mov": "video",
         ".webm": "video"}
SKIP_DIRS = {"node_modules", "dist", "build", "vendor", "__pycache__", ".venv", "venv", "site-packages", ".git", ".board"}
PLAN = {"ROADMAP.md", "CLAUDE.md", "AGENTS.md", "LIBRARY.md", "GEMINI.md"}


def files(root):
    """The project's tracked files that are read rather than run, by path from its folder."""
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, text=True).stdout
    paths = [p for p in out.split("\0") if p]
    if not paths:                                 # not a git folder: what is there
        paths = [str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file()]
    return [p for p in paths if Path(p).suffix.lower() in KINDS and Path(p).name not in PLAN
            and not any(part in SKIP_DIRS or part.startswith(".") for part in Path(p).parts[:-1])]


_cache = {}


def about(root, rel):
    """(title, line, kind, changed): a document's own title and first sentence, read once while it is unchanged."""
    path = Path(root) / rel
    try:
        st = path.stat()
    except OSError:
        return Path(rel).name, "", KINDS.get(Path(rel).suffix.lower(), "file"), ""
    key = (str(path), st.st_mtime, st.st_size)
    if key in _cache:
        return _cache[key]
    kind = KINDS.get(path.suffix.lower(), "file")
    title, line = path.name, ""
    if kind in ("doc", "page"):
        try:
            head = path.read_text(errors="replace")[:4000]
        except OSError:
            head = ""
        if kind == "page":
            m = re.search(r"<title>(.*?)</title>", head, re.S | re.I)
            title = re.sub(r"\s+", " ", m.group(1)).strip() if m and m.group(1).strip() else title
        else:
            front = re.match(r"^---\n(.*?)\n---\n", head, re.S)
            body = head[front.end():] if front else head
            if front and (d := re.search(r"^description:\s*(.+)$", front.group(1), re.M)):
                line = d.group(1).strip().strip('"\'')
            if m := re.search(r"^#{1,3}\s+(.+)$", body, re.M):
                title = m.group(1).strip()
            if not line:
                for para in re.split(r"\n\s*\n", body):
                    p = " ".join(para.split())
                    if p and not p.startswith(("#", "|", "```", "<", "!", "---", "[!", "[![")):
                        m = re.match(r"(.+?[.!?])(\s|$)", p)
                        line = (m.group(1) if m else p)[:160]
                        break
    changed = __import__("datetime").datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
    _cache[key] = (title, re.sub(r"[*_`]", "", line), kind, changed)
    return _cache[key]
