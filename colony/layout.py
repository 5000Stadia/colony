"""The still colony's one-time change to a portable folder, resumed from a machine-local journal."""
import errno
import fcntl
import filecmp
import getpass
import json
import os
import shutil
import socket
from contextlib import contextmanager
from pathlib import Path

from . import board


def journal_path():
    return board.machine_state() / "into-colony.json"


def _save(path, value):
    board.save_json(path, value)
    with open(path, "rb") as stream:
        os.fsync(stream.fileno())
    _sync_dir(path.parent)


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _exists(path):
    return path.exists() or path.is_symlink()


def _move(old, new):
    """A journaled move can be retried after either rename; an existing destination is never overwritten."""
    if not _exists(old):
        if not _exists(new):
            raise FileNotFoundError(f"Neither {old} nor {new} exists")
        return
    if _exists(new):
        raise FileExistsError(f"Both {old} and {new} exist; keep the intended copy before retrying")
    new.parent.mkdir(parents=True, exist_ok=True)
    try:
        old.rename(new)
        _sync_dir(new.parent)
        _sync_dir(old.parent)
    except OSError as err:
        if err.errno != errno.EXDEV:
            raise
        # Move each child separately across disks. A retry sees either the old child or its finished copy.
        if old.is_dir() and not old.is_symlink():
            new.mkdir(mode=old.stat().st_mode & 0o7777)
            for child in sorted(old.iterdir()):
                _move(child, new / child.name)
            shutil.copystat(old, new)
            old.rmdir()
            _sync_dir(new)
            _sync_dir(old.parent)
        else:
            stage = new.with_name(new.name + ".colony-moving")
            if _exists(stage):
                stage.unlink()
            if old.is_symlink():
                stage.symlink_to(os.readlink(old))
            else:
                shutil.copy2(old, stage)
                if not filecmp.cmp(old, stage, shallow=False):
                    raise OSError(f"The copy of {old} did not match")
                with open(stage, "rb") as stream:
                    os.fsync(stream.fileno())
            stage.rename(new)
            _sync_dir(new.parent)
            old.unlink()
            _sync_dir(old.parent)


def _transfer(old, new):
    """Merge only directories named by the journal, including a cross-disk transfer interrupted inside one."""
    if old.is_dir() and not old.is_symlink() and new.is_dir() and not new.is_symlink():
        for child in sorted(old.iterdir()):
            _transfer(child, new / child.name)
        shutil.copystat(old, new)
        old.rmdir()
        _sync_dir(new)
        _sync_dir(old.parent)
    elif _exists(old) and _exists(new) and not old.is_symlink() and not new.is_symlink() \
            and old.is_file() and new.is_file() and filecmp.cmp(old, new, shallow=False):
        old.unlink()                         # the copy finished before the source was removed
        _sync_dir(old.parent)
    elif old.is_symlink() and new.is_symlink() and os.readlink(old) == os.readlink(new):
        old.unlink()
        _sync_dir(old.parent)
    else:
        _move(old, new)


@contextmanager
def _home(path, secrets):
    saved = {key: os.environ.get(key) for key in ("COLONY_BOARD_HOME", "COLONY_SECRETS_HOME")}
    os.environ.update(COLONY_BOARD_HOME=str(path), COLONY_SECRETS_HOME=str(secrets))
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _plan(old, folder, state, secrets, mark):
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "colony"
    new = folder / "home"
    special = {old / name: secrets / name for name in board.SECRET_PATHS}
    special.update({old / name: cache / name for name in ("voice", "context-cache", "line-scratch")})
    special.update({old / "codex-remote": state / "codex", old / "backups": state / "backups" / "legacy",
                    old / "agents": folder / "projects" / ".agents", old / "items": folder / "projects" / ".items",
                    old / "project-keys": secrets / "project-keys"})
    from . import providers
    for auth in providers.get("codex").copied_repair_sign_ins(old):
        special[auth] = secrets / auth.relative_to(old)
    moves = []

    def add(source, dest):
        if source in special:
            dest = special[source]
        if source == dest:
            return
        if source.is_dir() and not source.is_symlink() and any(source in p.parents for p in special):
            for child in sorted(source.iterdir()):
                add(child, dest / child.name)
        else:
            if _exists(dest):
                raise FileExistsError(f"{dest} already exists; nothing changed")
            moves.append([str(source), str(dest)])

    for child in sorted(old.iterdir()):
        add(child, new / child.name)
    projects = list(board.projects(board.registry(), adopt=False))
    moved = []
    for base in (old / "agents", old / "items"):
        if not base.exists():
            continue
        roots = set(p for p in projects if p == base or base in p.parents)
        roots.update(p.parent for p in base.rglob(".git"))
        roots.update(p.parent.parent for p in base.rglob(".board/id"))
        for root in sorted(roots):
            board.ident(root)
            target = folder / "projects" / (".agents" if base.name == "agents" else ".items") / root.relative_to(base)
            moved.append([str(root), str(target)])
    pairs = [[str(source), str(dest)] for source, dest in special.items()]
    pairs.append([str(old), str(new)])
    # Each Codex home records its own place inside its files; after the move it is told where it is now.
    homes = old / "codex-remote"
    rehome = [[str(home), str(special[homes] / home.name)] for home in sorted(homes.iterdir())
              if home.is_dir() and not home.is_symlink()] if homes.is_dir() else []
    return {"format": 1, "old": str(old), "folder": str(folder), "secrets": str(secrets), "moves": moves,
            "projects": [str(p) for p in projects], "repoint": moved, "pairs": pairs, "rehome": rehome, "done": [],
            "marker": {"format": 1, "written_by": {"machine": socket.gethostname(), "user": getpass.getuser()},
                       "generation": mark["generation"], "still": True, "at": mark.get("at")}}


def _step(journal, note, name, action):
    if name not in note["done"]:
        action()
        note["done"].append(name)
        _save(journal, note)


def _finish(journal, note):
    from . import migrate, repoint
    folder = Path(note["folder"])
    backups = journal.parent / "backups" / "into-colony"
    _step(journal, note, "marker", lambda: _save(folder / "COLONY.json", note["marker"]))
    with _home(note["old"], note["secrets"]):
        for i, (old, _) in enumerate(note["repoint"]):
            _step(journal, note, f"keys:{i}", lambda old=old: migrate.keep_project_keys(old, say=lambda s: None))
    for i, (old, new) in enumerate(note["moves"]):
        _step(journal, note, f"move:{i}", lambda old=old, new=new: _transfer(Path(old), Path(new)))
    from . import providers
    for i, (old, new) in enumerate(note.get("rehome", [])):
        _step(journal, note, f"rehome:{i}",
              lambda old=old, new=new: providers.get("codex").rehome_history(Path(new), Path(old), backups))
    with _home(folder / "home", note["secrets"]):
        def records():
            roots = [Path(repoint.swap(p, note["pairs"])) for p in note["projects"]]
            roots += [Path(new) for _, new in note["repoint"]]
            for path in set(repoint.records(roots)):
                repoint.rewrite(path, note["pairs"], backup=backups)
            path = board.home() / "board.json"
            repoint.backup_file(path, backups)
            reg = board.registry()
            reg["new_root"] = str(folder / "projects")
            if reg["new_root"] not in reg["roots"]:
                reg["roots"].append(reg["new_root"])
            _save(path, reg)
        _step(journal, note, "records", records)
        for i, (old, new) in enumerate(note["repoint"]):
            _step(journal, note, f"repoint:{i}", lambda old=old, new=new: repoint.repoint(old, new, backup=backups))
        (folder / "projects").mkdir(parents=True, exist_ok=True)
    # Empty old directories can be left by the split around secrets. Never recreate one on a retry.
    old = Path(note["old"])
    if old.exists():
        for path in sorted(old.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir() and not path.is_symlink():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            old.rmdir()
        except OSError:
            pass
    journal.unlink()
    _sync_dir(journal.parent)


def into_colony(say=print):
    """Convert only a cleanly stilled colony; a saved journal is authoritative on every retry."""
    from . import handover
    if "COLONY_BOARD_HOME" in os.environ and not board.colony_folder():
        say("Unset COLONY_BOARD_HOME before changing over the default colony; an overridden board keeps its own home.")
        return 1
    journal = journal_path()
    journal.parent.mkdir(parents=True, exist_ok=True)
    with open(journal.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if journal.exists():
            note = json.loads(journal.read_text())
        else:
            folder, old = Path.home() / "Colony", board.home()
            if (folder / "COLONY.json").exists():
                say(f"Colony already uses {folder}")
                return 0
            mark = handover._mark()
            if not mark.get("still") or handover._generation(mark.get("generation")) is None:
                say("Colony must be still first: run `colony stop --still`, then `colony migrate --into-colony`.")
                return 1
            try:
                note = _plan(old, folder, journal.parent, board.secrets(create=False), mark)
            except (OSError, ValueError) as err:
                say(f"Not changed over: {err}")
                return 1
            _save(journal, note)
        try:
            _finish(journal, note)
        except (OSError, ValueError) as err:
            say(f"Change-over paused: {err}. Run `colony migrate --into-colony` again to resume its journal.")
            return 1
    say(f"Colony is in {note['folder']}; projects outside it stayed in place. Start colony to resume.")
    return 0
