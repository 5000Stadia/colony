"""The hand-over (R127): stilling colony, or one project, before anything under it moves.

`colony stop --still`: every project lands its work as a pause does (R134), helpers included; once each is still, the
Codex app hosts stop and the sessions end, and only then is the clean-stop mark written, so a copy of the folder taken
after it carries no half-written minute. An ordinary start resumes what it paused; a restored copy waits for the
person to check it.

`colony move NAME`: one project, the same way: what outside it names its folder is shown first, then it lands its
work, its console and Codex host stop, the folder moves, everything colony keeps about it is re-pointed
(repoint.repoint, its worktrees repaired), and its console resumes its conversation in the new folder. A failure
after the folder moved puts it back.
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import board, console

WAIT = 60 * 60          # the longest a hand-over waits for work to land, as a restart does (machine.py)
LOOK = 5                # seconds between looks
COMING_UP = 15          # how long a moved console is watched as it comes up, for a question it puts to the person
RESTORE_NOTICE = "This colony was restored from a copy; nothing runs by itself until you check it."


def still(root):
    """Nothing of the project's is running: its screen not mid-turn, and no helper or background command behind it."""
    if not console.running(console.session_name(root)):
        return True
    return board.seen(console.snapshot(root, lines=4))["state"] != "working"


def settle(roots, by, wait=WAIT, say=print, clock=time.monotonic, nap=time.sleep):
    """Each project lands its work (paused, as R134 does, marked `by`); returns those still busy when the wait ran
    out (empty: all still)."""
    for root in roots:
        board.pause_project(root, by=by)
    deadline, busy, told = clock() + wait, list(roots), 0
    while busy and clock() < deadline:
        busy = [r for r in busy if not still(r)]
        if busy and clock() - told >= 60:
            say(f"waiting for {', '.join(board.name_of(r) for r in busy)} to land its work…")
            told = clock()
        if busy:
            nap(LOOK)
    return busy


def _codex_stop(root):
    from . import codex_remote
    home = codex_remote.home_for(root)
    if codex_remote.alive(home):
        codex_remote.drain(home) or codex_remote.stop_host(root)
    return not codex_remote.alive(home)


def mark_path():
    return board.home() / "stopped.json"


def _mark():
    try:
        path = board.colony_folder() / "COLONY.json" if board.colony_folder() else mark_path()
        mark = json.loads(path.read_text())
        return mark if isinstance(mark, dict) else {}
    except (OSError, ValueError):
        return {}


def _generation(value):
    return value if type(value) is int and value >= 0 else None


def _machine_generation():
    try:
        return _generation(json.loads((board.machine_state() / "generation").read_text()))
    except (OSError, ValueError):
        return None


def hold_restore(why):
    """Keep the reason and first time a copy was found, including across starts while it waits."""
    if not board.restore_held():
        board.save_json(board.home() / "restore-hold.json", {"why": why, "since": board.now()})


def stop_still(stop_sessions, wait=WAIT, say=print):
    """colony stop --still. Returns 0 once still and stopped, 1 if work hadn't landed in time (nothing stopped)."""
    roots = [p for p in board.projects(board.registry()) if p.exists() and not board.paused(p)]
    busy = settle(roots, "still", wait, say)
    if busy:
        for root in roots:
            board.resume_project(root)
        say(f"not stopped: {', '.join(board.name_of(r) for r in busy)} hadn't landed its work within "
            f"{wait // 60} minutes; everything carries on as it was")
        return 1
    held = [board.name_of(p) for p in board.projects(board.registry()) if p.exists() and not _codex_stop(p)]
    if held:
        say(f"not stopped: the Codex app host of {', '.join(held)} didn't stop; everything stays paused, so try again")
        return 1
    stop_sessions()
    generation = (_generation(_mark().get("generation")) or 0) + 1
    _save_mark({"at": board.now(), "still": True, "generation": generation})
    board.save_json(board.machine_state() / "generation", generation)
    say("colony is still: every project landed its work, and nothing of colony's is running. `~/colony/start` brings "
        "it back, each project resuming where it stopped.")
    return 0


def _save_mark(mark):
    board.save_json(mark_path(), mark)
    if folder := board.colony_folder():
        current = json.loads((folder / "COLONY.json").read_text())
        board.save_json(folder / "COLONY.json", dict(current, **mark))


def started():
    """Before launch, tell a restored copy from a crash. Return whether it must wait for the person."""
    from . import layout
    if layout.journal_path().exists() and layout.into_colony() != 0:
        raise RuntimeError("The layout change-over must finish before colony can start")
    with board.board_lock("restore"):
        if board.restore_held():
            return True
        mark = _mark()
        generation, machine = _generation(mark.get("generation")), _machine_generation()
        if generation is not None and (machine is None or generation < machine):
            hold_restore("This home has a generation but no record on this machine." if machine is None else
                         f"This home's generation ({generation}) is behind this machine's ({machine}).")
            return True
        # An old board or a fresh install starts its records here. A home ahead of the machine can be a stop
        # interrupted between the two writes, so it brings the machine forward without a restore hold.
        generation = generation if generation is not None else (machine or 0)
        board.save_json(board.machine_state() / "generation", generation)
        _resume_still(dict(mark, generation=generation))
        return False


def resume_restored():
    """The person's check accepts this copy. Match the machine before letting the watcher run again."""
    with board.board_lock("restore"):
        if not board.restore_held():
            return False
        mark = _mark()
        generation = _generation(mark.get("generation")) or 0
        board.save_json(board.machine_state() / "generation", generation)
        _resume_still(dict(mark, generation=generation))
        (board.home() / "restore-hold.json").unlink()
        return True


def _resume_still(mark):
    """Resume only projects stilled for copying; keep pauses the person chose."""
    for root, row in list(board.paused_projects().items()):
        if row.get("by") == "still" and Path(root).exists():
            board.resume_project(root)
    _save_mark(dict(mark, still=False, started=board.now()))


def outside(root):
    """What outside the project names its folder, which a move leaves pointing at the old place: the person's own
    services, crontab lines, and a Python environment inside it (its scripts name the folder)."""
    from . import migrate
    root, found = str(root), []
    for unit in migrate._units():
        try:
            if root in unit.read_text():
                found.append(f"the service {unit.name}")
        except OSError:
            pass
    found += [f"the crontab line: {line.strip()}" for line in migrate._crontab().splitlines()
              if root in line and not line.lstrip().startswith("#")]
    found += [f"the Python environment in {cfg.parent.relative_to(root)} (remake it after the move)"
              for cfg in Path(root).glob("*/pyvenv.cfg")]
    return found


def _files(folder):
    return sorted((str(p.relative_to(folder)), p.lstat().st_size) for p in Path(folder).rglob("*")
                  if p.is_file() or p.is_symlink())


def _carry(old, new):
    """The folder to its new place: renamed on the same disk; across disks copied, checked file by file, and the old
    one put in colony's trash."""
    new.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(old, new)
        return
    except OSError as err:
        if err.errno != 18:                    # EXDEV: another disk
            raise
    shutil.copytree(old, new, symlinks=True)
    if _files(old) != _files(new):
        shutil.rmtree(new)
        raise OSError(f"the copy to {new} didn't match the original; nothing moved")
    trash = board.home() / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old), str(trash / f"{old.name}-moved-{time.strftime('%Y%m%d-%H%M%S')}"))


def asks(name, window=None):
    """Whether a console just started is asking the person something (its folder's trust, its permission mode): a
    choice on screen before its box comes up. Looked at until its box is there, it stops, or `window` seconds pass.
    The question is left on screen for the person, or for the watcher where they let it answer start-up questions."""
    until = time.time() + (COMING_UP if window is None else window)
    while console.running(name):
        if console.asking(name):
            return True
        if console.draft(name) is not None or time.time() >= until:
            return False
        time.sleep(0.25)
    return False


def move(root, dest, sure=False, wait=WAIT, say=print):
    """colony move. Returns 0 when moved and its console started there (resumed, or asking the person something first,
    and it says which); 1 when it stopped short (and says why), nothing lost."""
    from . import codex_remote, migrate, providers, repoint
    root, dest = Path(root), Path(dest).expanduser()
    if dest.exists():
        say(f"{dest} already exists; pick a new place")
        return 1
    if board.ident(root, make=False) is None:
        board.keep_ids([root])
    named = outside(root)
    if named and not sure:
        say(f"Outside {board.name_of(root)}, these still name its folder and will point at the old place after the move: "
            + "; ".join(named) + ". Fix them after, or keep the project where it is (the archive still carries it). "
            "Run again with --yes to move it.")
        return 1
    was_paused = bool(board.paused(root))
    busy = settle([root], "move", wait, say)
    if busy:
        if not was_paused:
            board.resume_project(root)
        say(f"not moved: {board.name_of(root)} hadn't landed its work within {wait // 60} minutes; it carries on as it was")
        return 1
    console.stop(root)
    if codex_remote.alive(codex_remote.home_for(root)) and not _codex_stop(root):
        if not was_paused:
            board.resume_project(root)
        say(f"not moved: {board.name_of(root)}'s Codex app host didn't stop")
        return 1
    try:
        if dest.resolve().is_relative_to((Path.home() / "Colony" / "projects").resolve()):
            migrate.keep_project_keys(root, say)
        _carry(root, dest)
        said = repoint.repoint(root, dest, backup=board.machine_state() / "backups" / "moves")
    except Exception as err:
        if dest.exists() and not root.exists():
            _carry(dest, root)                      # put back as it was
        if not was_paused:
            board.resume_project(root)
        say(f"not moved: re-pointing failed ({err}); the folder is back where it was")
        return 1
    provider = providers.of(dest)
    recorded = console.saved_conversation(dest, providers.key(provider)) is not None
    try:
        found = recorded and provider.verify_history(dest)
    except (OSError, ValueError):
        found = False
    if recorded and not found:
        console.forget_conversation(dest, providers.key(provider))
        say(f"{provider.label} can't find the recorded conversation at {dest}. The move completed; "
            "its console will start a fresh conversation. Existing history files are kept.")
    if not was_paused:
        board.resume_project(dest)
    console.ensure(dest)
    if asks(console.session_name(dest)):
        # not resumed yet: until the question is answered, nothing typed in Chat reaches its agent
        then = (" Its console is asking you something in its Console tab; answer it once"
                + (", and it carries on with its conversation." if found else
                   ", and it starts a fresh conversation." if recorded else "."))
    else:
        then = (" Its console resumed its conversation there." if found else
                " Its console started a fresh conversation." if recorded else " Its console started there.")
    say(f"{board.name_of(dest)} moved to {dest}" + (f" ({'; '.join(said)})" if said else "") + "." + then
        + (f" Still to fix: {'; '.join(named)}." if named else ""))
    return 0
