"""Backup (R165): a nightly copy of colony, kept where the person said, made by colony itself.

The copy is the archive `colony migrate --pack` makes (every project, colony's records, the agents' conversations,
read back whole before it is kept); `migrate --unpack` puts it back. The folder is the `backup` setting: empty, no
copy is kept, and colony chooses none for the person. A folder on another drive is known by that drive's own id and
found wherever it is mounted (a copy made by hand skipped two nights running because its drive sat at another path);
with that drive away nothing is written anywhere else, and the person is told once, not every night."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import board

KEEP = 4                 # archives kept, the newest
HOUR = (3, 30)           # the night's copy is due from this time, on this machine's clock
CUT_SHORT = 12 * 3600    # a .part older than this is a copy a crash cut short: cleared
RETELL = 7 * 86400       # a copy still not made for the same reason is told again after this long


def chosen():
    """The folder the person chose, or ''."""
    return str(board.registry()["settings"].get("backup") or "")


def _path():
    return board.home() / "backup.json"


def state():
    """What the backup keeps of itself: the chosen folder's drive, the last copy made, the last one that wasn't."""
    try:
        got = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _keep(**changes):
    with board.board_lock("backup"):
        s = dict(state(), **changes)
        board.home().mkdir(parents=True, exist_ok=True)
        temp = _path().with_suffix(".tmp")
        temp.write_text(json.dumps({k: v for k, v in s.items() if v is not None}, indent=1))
        temp.replace(_path())


def _findmnt(*args):
    """One value from findmnt (Linux), or '' where it isn't here or has nothing to say."""
    if not shutil.which("findmnt"):
        return ""
    try:
        out = subprocess.run(["findmnt", "-rno", *args], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    line = out.splitlines()[0].strip() if out.strip() else ""
    return line.encode().decode("unicode_escape") if "\\x" in line else line     # a space in a path comes as \x20


def _same_drive(folder):
    """Whether a folder is on the filesystem colony's own records are on."""
    return os.stat(folder).st_dev == os.stat(board.home()).st_dev


def _drive(folder):
    """A chosen folder's drive, as it can be found again: whether it is another filesystem than colony's records'
    and, where this machine can say, that filesystem's id and the folder's place under its mount point."""
    other = not _same_drive(folder)
    uuid, under = "", ""
    if other:
        uuid, target = _findmnt("UUID", "-T", str(folder)), _findmnt("TARGET", "-T", str(folder))
        try:
            under = str(Path(folder).relative_to(target)) if uuid and target else ""
        except ValueError:
            uuid = ""
    return {"other": other, "uuid": uuid, "under": under}


def choose(value):
    """The `backup` setting as it is kept: the folder's whole path, or '' for none. Raises ValueError, saying why,
    for a folder the copy can't go in."""
    value = str(value or "").strip()
    if value.lower() in ("", "off", "none"):
        _keep(drive=None, failed=None)
        return ""
    folder = Path(value).expanduser()
    if not folder.is_absolute():
        raise ValueError(f"backup: give the folder's whole path (it read as {value})")
    folder = folder.resolve()
    from . import migrate
    within = next((p for p in migrate.parts(board.registry()) if folder == p["path"] or migrate._under(folder, p["path"])), None)
    if within:
        raise ValueError(f"backup: {folder} is inside {within['path']}, which the copy itself takes: choose a folder "
                         "outside your projects and colony's own records (another drive, ideally)")
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        raise ValueError(f"backup: {folder} can't be made ({err.strerror or err})")
    if not os.access(folder, os.W_OK | os.X_OK):
        raise ValueError(f"backup: {folder} can't be written to by {os.environ.get('USER') or 'this user'}")
    _keep(drive=_drive(folder), failed=None)
    return str(folder)


AWAY = "its drive isn't attached, or isn't mounted"


def where():
    """(the folder tonight's copy goes in, '') or (None, why not). A folder chosen on another drive is found on that
    drive wherever it is mounted now (attached and not mounted, it is mounted as the signed-in person would, never
    with sudo); with the drive away there is no folder: its path on colony's own drive is never used in its place."""
    folder = chosen()
    if not folder:
        return None, "no folder is chosen"
    drive = state().get("drive") or {}
    if drive.get("uuid"):
        target = _findmnt("TARGET", "-S", f"UUID={drive['uuid']}")
        if not target and shutil.which("udisksctl"):
            try:
                subprocess.run(["udisksctl", "mount", "-b", f"/dev/disk/by-uuid/{drive['uuid']}"], capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError):
                pass
            target = _findmnt("TARGET", "-S", f"UUID={drive['uuid']}")
        return (Path(target) / drive.get("under", ""), "") if target else (None, AWAY)
    path = Path(folder)
    if drive.get("other") and (not path.is_dir() or _same_drive(path)):
        return None, AWAY
    return path, ""


def archives(dest):
    """The copies in a folder, newest first (each is named by when it was made)."""
    return sorted((p for kind in ("zst", "gz") for p in Path(dest).glob(f"colony-*.tar.{kind}")),
                  key=lambda p: p.name, reverse=True)


def _drop(archive):
    archive.unlink(missing_ok=True)
    (archive.parent / (archive.name.split(".tar.")[0] + ".manifest.json")).unlink(missing_ok=True)


def due(now=None):
    """Whether the night's copy is still to be made: a folder is chosen and none has been tried since the last HOUR
    to pass. A machine that was off then makes it when it is next on; a folder just chosen gets its first at once."""
    if not chosen():
        return False
    now = time.time() if now is None else now
    t = time.localtime(now)
    night = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, HOUR[0], HOUR[1], 0, 0, 0, -1))
    if now < night:
        night -= 86400
    return (state().get("tried") or 0) < night


def _lock():
    """The one-copy-at-a-time lock, held, or None while another copy holds it."""
    import fcntl
    board.home().mkdir(parents=True, exist_ok=True)
    fh = open(board.home() / "backup-running.lock", "w")       # its own file: backup.lock is _keep's, taken while this is held
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def running():
    fh = _lock()
    if fh:
        fh.close()
    return fh is None


def start():
    """A copy, begun in a process of its own (it takes minutes; the watcher and the board go on)."""
    log = board.home() / "backup.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists() and log.stat().st_size > 200_000:
        log.write_text(log.read_text(errors="replace")[-100_000:])
    _keep(tried=time.time())
    with open(log, "a") as out:
        # -P: nothing in the folder it starts from is imported in place of what it means to import
        subprocess.Popen([sys.executable, "-P", "-m", "colony", "backup", "--now"], stdin=subprocess.DEVNULL,
                         stdout=out, stderr=out, start_new_session=True, cwd=str(board.home()))


def tick():
    """The watcher's look, each pass: the night's copy started when it is due and none is being made."""
    if board.restore_held() or not due() or running():
        return False
    start()
    return True


def _failed(why, say):
    why = " ".join(str(why).split()).removeprefix("colony: ")
    was, now = state().get("failed") or {}, time.time()
    same = was.get("why") == why
    told = was.get("told", 0) if same else 0
    if now - told >= RETELL:                       # once for each reason, and again if it still stands a week on
        board.notice(f"Colony's nightly copy of itself wasn't made: {why}. Settings → Backup says where it goes.")
        told = now
    _keep(failed={"why": why, "at": now, "since": was.get("since", now) if same else now, "told": told})
    say(f"{board.now()} not made: {why}")
    return 1


def run(say=print):
    """colony backup --now: one copy into the chosen folder, then the newest KEEP kept. 0 when it was made."""
    lock = _lock()
    if not lock:
        say("A copy is already being made.")
        return 1
    try:
        began = time.time()
        _keep(tried=began)
        dest, why = where()
        if dest is None:
            return _failed(why, say)
        from . import migrate
        try:
            dest.mkdir(parents=True, exist_ok=True)
            for part in dest.glob("colony-*.part"):
                if began - part.stat().st_mtime > CUT_SHORT:
                    part.unlink(missing_ok=True)
            while True:
                try:
                    archive = migrate.pack(dest, say=say)
                    break
                except migrate.NoRoom:                 # the oldest make room for tonight's, never the only one
                    old = archives(dest)
                    if len(old) < 2:
                        raise
                    _drop(old[-1])
                    say(f"Made room: dropped {old[-1].name}")
        except SystemExit as err:
            return _failed(err.code if isinstance(err.code, str) else f"the pack stopped ({err.code})", say)
        except Exception as err:
            return _failed(f"{type(err).__name__}: {err}", say)
        for old in archives(dest)[KEEP:]:
            _drop(old)
        _keep(last={"at": time.time(), "archive": str(archive), "bytes": archive.stat().st_size,
                    "seconds": round(time.time() - began)}, failed=None)
        say(f"{board.now()} made in {round(time.time() - began)}s; {len(archives(dest))} kept")
        return 0
    finally:
        lock.close()


def line():
    """Where the backup stands, in a line: Settings' summary and `colony backup`."""
    folder = chosen()
    if not folder:
        return "Off: no copy of colony is kept"
    s = state()
    last, failed = s.get("last"), s.get("failed")
    from . import migrate
    made = (f"last copy {board.local_day(_iso(last['at']), clock=True)} ({migrate.size(last['bytes'])})" if last
            else "making the first copy" if running() else "no copy made yet")
    return f"Nightly, into {folder} · {made}" + (f" · not made since: {failed['why']}" if failed else "")


def _iso(at):
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(at))


def same_drive():
    """Whether the chosen folder shares a drive with colony's own records (so it does not survive that drive)."""
    return bool(chosen()) and not (state().get("drive") or {}).get("other")
