"""The Library: what a project knows and has made, in one place the person and its agent both read.

It is found, not filed: the project's own documents and pages (its tracked files that are read rather than run), what
the person uploaded, what is pinned and what was shown to them. Its headings come from LIBRARY.md, a map of content the
agent writes first from the project's shape and either of them may change:

  ## Findings
  - [Fidelity over chapters](research/fidelity.md) — what holds, and where it slips
  ### Older
  - [First probe](research/probe.md)

Whatever the map leaves out shows under its folder. A document's title is its first heading (a page's, its <title>),
its line the map's or else its own first sentence.
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
MAP = "LIBRARY.md"
LINK = re.compile(r"^\s*[-*]\s+\[([^\]]+)\]\(([^)\s]+)\)\s*(?:[—–-]+\s*(.*))?$")


def files(root):
    """The project's tracked files that are read rather than run, by path from its folder."""
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, text=True).stdout
    paths = [p for p in out.split("\0") if p]
    if not paths:                                 # not a git folder: what is there
        paths = [str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file()]
    return [p for p in paths if Path(p).suffix.lower() in KINDS and Path(p).name not in PLAN
            and not any(part in SKIP_DIRS or part.startswith(".") for part in Path(p).parts[:-1])]


def uploads(root):
    up = Path(root) / ".board" / "uploads"
    return sorted((str(p.relative_to(root)) for p in up.iterdir() if p.is_file()), reverse=True) if up.is_dir() else []


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


def read_map(root):
    """LIBRARY.md as sections: [{"title", "depth", "entries": [(path, title, line)]}], in its order."""
    path = Path(root) / MAP
    if not path.exists():
        return []
    sections, cur = [], None
    for raw in path.read_text(errors="replace").splitlines():
        if m := re.match(r"^(#{2,4})\s+(.+?)\s*$", raw):
            cur = {"title": m.group(2), "depth": len(m.group(1)) - 1, "entries": []}
            sections.append(cur)
        elif (m := LINK.match(raw)):
            if cur is None:
                cur = {"title": "Library", "depth": 1, "entries": []}
                sections.append(cur)
            target = m.group(2).split("#")[0]
            cur["entries"].append((target, m.group(1).strip(), (m.group(3) or "").strip()))
    return sections


def shape(root):
    """The Library as the page draws it: (the map's sections, the rest by folder, uploads)."""
    sections = read_map(root)
    placed = {os.path.normpath(p) for s in sections for p, _, _ in s["entries"]}
    rest = {}
    for rel in files(root):
        if os.path.normpath(rel) not in placed:
            top = Path(rel).parts[0] if len(Path(rel).parts) > 1 else ""
            rest.setdefault(top, []).append(rel)
    return sections, rest, uploads(root)
