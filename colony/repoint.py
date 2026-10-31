"""Re-pointing a moved project (R127): everything colony keeps about a project at one folder, made to say another.

The project's identity (.board/id) already carries its console's name and its conversations (board.ident, console.py).
The rest of colony's records name the folder, so a move rewrites them all in one pass: every JSON and JSONL file in
colony's home and in each project's .board/, keys and values alike, a path equal to the old folder or under it. The
folders and keys colony names by a hash of the folder are renamed, and Claude Code's history for the folder moves
with a link left at its old name, so a console still running keeps writing into it. What can't be re-pointed safely
yet (a project in a lead group, a Codex host still running) is refused with why, before anything changes.
"""
import hashlib
import json
import os
from pathlib import Path

from . import board

# Top-level folders of colony's home that hold no record of a project's folder worth rewriting, or that are handled
# apart: program homes, caches, worktrees, the trash.
SKIP = {"codex-remote", "repair", "voice", "context-cache", "line-scratch", "trash", "backups", "items", "tools", "bench",
        "audits", "locks", "shared"}


def swap(value, pairs):
    """A string equal to an old prefix, or under it ("/"), says the new one; anything else as it is."""
    if isinstance(value, str):
        for old, new in pairs:
            if value == old:
                return new
            if value.startswith(old + "/"):
                return new + value[len(old):]
        return value
    if isinstance(value, dict):
        return {swap(k, pairs): swap(v, pairs) for k, v in value.items()}
    if isinstance(value, list):
        return [swap(v, pairs) for v in value]
    return value


def _write(path, text):
    tmp = path.with_name(path.name + ".repoint")
    tmp.write_text(text)
    os.chmod(tmp, path.stat().st_mode & 0o7777)
    tmp.replace(path)


def rewrite(path, pairs):
    """One record file re-pointed in place; True when it changed. A file that doesn't mention an old folder isn't
    parsed, and a line that isn't JSON is kept as it is."""
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return False
    if not any(old in text for old, _ in pairs):
        return False
    if path.suffix == ".jsonl":
        out = []
        for line in text.splitlines(keepends=True):
            try:
                out.append(json.dumps(swap(json.loads(line), pairs), ensure_ascii=False) + ("\n" if line.endswith("\n") else ""))
            except ValueError:
                out.append(line)
        new = "".join(out)
    else:
        try:
            new = json.dumps(swap(json.loads(text), pairs), indent=1, ensure_ascii=False)
        except ValueError:
            return False
    if new == text:
        return False
    _write(path, new)
    return True


def records(projects=()):
    """Every record file colony keeps that could name a project's folder: its home's, and each project's .board/."""
    home = board.home()
    for path in sorted(home.rglob("*.json*")):
        rel = path.relative_to(home).parts
        if rel[0] in SKIP or path.suffix not in (".json", ".jsonl") or path.is_symlink():
            continue
        yield path
    for root in projects:
        folder = Path(root) / ".board"
        if folder.is_dir():
            yield from (p for p in sorted(folder.rglob("*.json*")) if p.suffix in (".json", ".jsonl") and not p.is_symlink())


def _h(text, algo="sha256", n=16):
    return hashlib.new(algo, text.encode()).hexdigest()[:n]


def refusal(old, new):
    """Why this move can't be re-pointed yet, or None."""
    from . import codex_remote
    old, new = str(old), str(new)
    if not Path(new).is_dir():
        return f"{new} isn't a folder"
    if board.ident(new, make=False) is None:
        return f"{new} has no colony identity (.board/id)"
    try:
        leads = (board.home() / "leads.json").read_text()
    except OSError:
        leads = ""
    if f'"{old}"' in leads or f'"{old}/' in leads:
        return "it's in a lead group, whose worktrees move with the hand-over (colony move), not by themselves"
    if codex_remote.alive(codex_remote.home_for(old)):
        return "its Codex host is still running; it moves once that host is stopped between turns"
    return None


def repoint(old, new):
    """Everything colony keeps about the project at `old` now says `new`. Returns what changed, as lines; raises
    ValueError, changing nothing, when it can't yet (refusal)."""
    from . import codex_remote, connections, measures, migrate, providers
    old, new = str(old), str(new)
    why = refusal(old, new)
    if why:
        raise ValueError(f"Can't re-point {old} to {new} yet: {why}.")
    said, pairs = [], [(old, new)]
    # Claude Code's history for the folder (PROVIDER: it keys a folder's conversations by its path): moved, with a
    # link left at the old name so a console that is still running keeps appending where it can be found
    claude = Path(providers.get("claude").config_home()) / "projects"
    was, now = claude / migrate.claude_key(old), claude / migrate.claude_key(new)
    if was.is_dir() and not was.is_symlink() and not now.exists():
        was.rename(now)
        was.symlink_to(now, target_is_directory=True)
        pairs.append((str(was), str(now)))
        said.append(f"Claude Code's conversations moved to {now.name}")
    # the folders and keys colony names by a hash of the folder
    for before, after, what in ((codex_remote.home_for(old), codex_remote.home_for(new), "its Codex home"),
                                (measures.folder(old), measures.folder(new), "its measures")):
        if before.exists() and not after.exists():
            before.rename(after)
            said.append(f"{what} renamed")
    for folder in (connections.keys_dir().iterdir() if connections.keys_dir().is_dir() else ()):
        before = folder / (_h(str(Path(old).resolve())) + ".env")
        after = folder / (_h(str(Path(new).resolve())) + ".env")
        if folder.is_dir() and before.exists() and not after.exists():
            before.rename(after)
            said.append(f"its {folder.name} keys renamed")
    choices = board.home() / "model-selection.json"
    if choices.exists():
        text = choices.read_text()
        hashed = "@" + _h(str(Path(old).resolve()))
        if hashed in text:
            _write(choices, text.replace(hashed, "@" + _h(str(Path(new).resolve()))))
            said.append("its model choices re-keyed")
    # every record that names the folder
    plist = [p for p in board.projects(board.registry())]
    changed = [p for p in records(plist + [Path(new)]) if rewrite(p, pairs)]
    if changed:
        said.append(f"{len(changed)} record file(s) re-pointed")
    return said


def _places(old):
    """Where a project last seen at `old` may be now: the same path under this home (another username or home path),
    ~/Colony/projects, the board's project folders, and beside where it was."""
    old = Path(old)
    parts = old.parts
    if len(parts) > 3 and parts[1] in ("home", "Users") and Path.home() != Path(*parts[:3]):
        yield Path.home().joinpath(*parts[3:])
    roots = [Path.home() / "Colony" / "projects", old.parent] + [Path(r) for r in board.registry()["roots"]]
    for root in roots:
        if root.is_dir():
            yield from (d for d in sorted(root.iterdir()) if d.is_dir())


def refind():
    """Projects whose folder has gone, found again by their identity and followed (repoint), each said once to the
    steward for the person. One that can't be followed yet is said once with why, and stays where colony last knew
    it. Returns [(old, new or None, what was said)]."""
    from . import monitor
    reg = board.registry()
    out, said = [], _said()
    for value, old in sorted((reg.get("ids") or {}).items()):
        if Path(old).exists() or not reg_has(reg, old):
            continue
        new = next((d for d in _places(old) if board.ident(d, make=False) == value and str(d) != old), None)
        if new is None:
            continue
        name = board.name_of(Path(old))
        try:
            repoint(old, new)
            line = f"{name} was moved to {new}; colony followed it there (its records, conversations and console)."
        except ValueError as err:
            line = f"{name} was moved to {new}, but colony can't follow it by itself yet: {err}"
            if said.get(old) == line:
                continue
        said[old] = line
        monitor.queue(f"[colony] {line} Tell the person once.", relay=True)
        out.append((old, new, line))
    _save_said(said)
    return out


def reg_has(reg, path):
    return path in reg["projects"] or any(Path(r) == Path(path).parent for r in reg["roots"])


def _said():
    try:
        return json.loads((board.home() / "moves-said.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_said(said):
    if said:
        board.save_json(board.home() / "moves-said.json", said)
