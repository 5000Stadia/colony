"""The map: what exists in the work, located and summarised, whatever the work is made of.

Headings for prose, symbols for code, the first line for anything else. One-line summaries come from
the work itself (docstrings, the sentence under a heading), so building the map costs no tokens.
Cached by content hash; only changed files are re-read.
"""
import ast
import hashlib
import json
import re
import subprocess

CODE = re.compile(r"^\s*(?:export\s+)?(?:pub\s+)?(?:async\s+)?(function|def|class|fn|func|interface|struct|enum|type)\s+([A-Za-z_]\w*)")
HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
SKIP_DIRS = (".colony/", ".git/", "node_modules/", "__pycache__/")
WORD = re.compile(r"[a-z][a-z0-9_]{2,}")


def _files(project):
    out = []
    for args in (["ls-files"], ["ls-files", "--others", "--exclude-standard"]):
        done = subprocess.run(["git", "-C", str(project.root), *args], capture_output=True, text=True)
        out += done.stdout.splitlines()
    return sorted({f for f in out if not f.startswith(SKIP_DIRS)})


def _first_sentence(lines, start):
    for line in lines[start:start + 6]:
        text = line.strip()
        if text and not text.startswith(("#", "|", "```", "---")):
            return re.split(r"(?<=[.!?])\s", text)[0][:140]
    return ""


def _entries(path, text):
    lines = text.splitlines()
    entries = []
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        if tree is not None:
            def visit(nodes, prefix=""):
                for node in nodes:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if node.name.startswith("_") and not node.name.startswith("__"):
                            continue
                        doc = (ast.get_docstring(node) or "").strip().split("\n")[0][:140]
                        kind = "class" if isinstance(node, ast.ClassDef) else "def"
                        entries.append({"line": node.lineno, "kind": kind, "name": prefix + node.name, "summary": doc})
                        if isinstance(node, ast.ClassDef):
                            visit(node.body, prefix=node.name + ".")
            visit(tree.body)
            module_doc = (ast.get_docstring(tree) or "").strip().split("\n")[0][:140]
            entries.insert(0, {"line": 1, "kind": "file", "name": path, "summary": module_doc})
            return entries
    if path.endswith((".md", ".markdown", ".txt", ".rst")):
        for i, line in enumerate(lines):
            m = HEADING.match(line)
            if m:
                entries.append({"line": i + 1, "kind": "h" + str(len(m.group(1))), "name": m.group(2),
                                "summary": _first_sentence(lines, i + 1)})
    else:
        for i, line in enumerate(lines):
            m = CODE.match(line)
            if m:
                entries.append({"line": i + 1, "kind": m.group(1), "name": m.group(2), "summary": ""})
    first = next((l.strip() for l in lines if l.strip()), "")[:140]
    entries.insert(0, {"line": 1, "kind": "file", "name": path, "summary": first})
    return entries


def build(project):
    """Refresh the cache for changed files, write .colony/map.md, and return the entries. The map is a
    projection of the work, regenerated on demand, so it is never committed."""
    cache_path = project.state / "map.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    fresh, changed = {}, []
    for path in _files(project):
        full = project.root / path
        try:
            data = full.read_bytes()
        except OSError:
            continue
        if len(data) > 1_000_000 or b"\0" in data[:4096]:
            continue
        digest = hashlib.sha256(data).hexdigest()
        if cache.get(path, {}).get("hash") == digest:
            fresh[path] = cache[path]
            continue
        fresh[path] = {"hash": digest, "entries": _entries(path, data.decode("utf-8", errors="replace"))}
        changed.append(path)
    project.state.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(fresh, ensure_ascii=False))
    lines = ["# Map", "", "Generated from the work by `colony map`. Do not edit; query it with `colony map QUERY`.", ""]
    for path, item in fresh.items():
        lines.append(f"## {path}")
        for e in item["entries"]:
            if e["kind"] == "file":
                if e["summary"]:
                    lines.append(f"- {e['summary']}")
                continue
            lines.append(f"- `{e['name']}` ({e['kind']}, line {e['line']})" + (f" — {e['summary']}" if e["summary"] else ""))
        lines.append("")
    (project.state / "map.md").write_text("\n".join(lines))
    return fresh, changed


def query(project, text, k=40):
    """The entries that bear on `text`, best first: by shared words, and every entry of a named file."""
    cache_path = project.state / "map.json"
    if not cache_path.exists():
        build(project)
    cache = json.loads(cache_path.read_text())
    words = set(WORD.findall(text.lower()))
    scored = []
    for path, item in cache.items():
        named = path in text
        for e in item["entries"]:
            hay = set(WORD.findall(f"{path} {e['name']} {e['summary']}".lower().replace("_", " "))) | \
                  set(WORD.findall(f"{e['name']}".lower()))
            score = len(words & hay) + (5 if named else 0)
            if score:
                scored.append((score, path, e))
    scored.sort(key=lambda s: (-s[0], s[1], s[2]["line"]))
    return [dict(e, file=path) for _, path, e in scored[:k]]


def render(entries):
    return "\n".join(f"- {e['file']}:{e['line']} `{e['name']}` ({e['kind']})"
                     + (f" — {e['summary']}" if e["summary"] else "") for e in entries) or "- (nothing in the work yet)"
