"""Migrate and go: everything colony keeps, carried in one archive to a fresh install under the same username, and back
in place there as if colony had just restarted (R127). The person, 2026-10-28: "I am wanting to make sure we have a data
folders-like migrate and go structure so I can drag and drop to a fresh deployment and it functions similarly as if I
just restarted", and "I might just wipe the drive - install the best linux distro for this and redeploy colony with
migration".

    colony migrate --list            what a pack takes, part by part, with sizes, and what it leaves out; writes nothing
    colony migrate --pack DIR        one archive (.tar.zst; .tar.gz where zstd is missing) and its manifest into DIR,
                                     with the program to unpack it with (colony.pyz); the archive is then read back whole
    colony migrate --unpack ARCHIVE  on the new machine: everything back at its own path, then what belongs to the
                                     machine made again, and what to sign in to

A pack takes colony's home, every project folder on the board and on its Archived shelf (they come back from there),
each one's conversations (Claude Code's folders of them, the steward's too; Codex's are in colony's home), and the
colony program's own checkout with its git history. It leaves out what the new machine makes again (a Python
environment, node_modules, caches, the voice's model) and every sign-in and key, which the person enters again there.
An archive and not loose files: the disk it goes on may keep neither permissions nor links (NTFS). Paths are kept
whole, so the same username unpacks everything in place; another home folder is refused for now (re-pointing comes
with the one-folder layout).
"""
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from . import board

FORMAT = 1
MANIFEST = "COLONY-MIGRATE.json"     # the archive's first member: what it holds, read before anything is unpacked
CACHE_TAG = b"Signature: 8a477f597d28d172789f06886806bc55"   # a folder that says it is a cache (bford.info/cachedir)
# Files a project keeps that look like keys: packed as they are (the project needs them to run), and named, so the
# person knows the archive holds them. Seen: Kernos's .env, credentials.json and secrets/*.key.
KEYLIKE = re.compile(r"^(\.env(\.(?!example$|sample$|template$).+)?|.+\.(pem|key|p12|pfx)|credentials.*\.json"
                     r"|id_(rsa|ed25519|ecdsa)|\.netrc|\.pgpass)$")
CODEX_CACHES = ("cache", "tmp", "shell_snapshots")   # in each of colony's Codex homes: Codex fetches or makes them again
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"     # how a zstd stream begins, whatever its file is called
PROGRESS = 30                        # seconds between the lines a long pack says how far it has got


def program():
    """The colony program's own checkout (with its git history), or None where colony was installed another way."""
    here = Path(__file__).resolve().parent.parent
    return here if (here / ".git").exists() and (here / "pyproject.toml").is_file() else None


def claude_key(folder):
    """The name Claude Code gives a folder's conversations (PROVIDER: as providers.ClaudeCode reads them)."""
    return re.sub(r"[^A-Za-z0-9-]", "-", str(folder))


def size(n):
    for unit, at in (("GB", 2 ** 30), ("MB", 2 ** 20), ("KB", 2 ** 10)):
        if n >= at:
            return f"{n / at:.1f} {unit}"
    return f"{n} B"


def _under(path, folder):
    return str(path).startswith(str(folder).rstrip("/") + "/")


# ---------------------------------------------------------------- what a pack takes

def _projects(reg):
    """Every project on the board, then those on its Archived shelf, then the folder each works in where that is another
    (a second agent's): [(kind, name, folder)]. Read only: a folder new in a projects folder isn't put on the board by
    looking."""
    on = board.projects(reg, adopt=False)
    shelf = [Path(k) for k in board.archived_off(on)]
    out = [(kind, root.name, root) for kind, roots in (("project", on), ("archived", shelf)) for root in roots]
    return out + [(kind, f"{root.name}'s folder", board.workdir(root)) for kind, _, root in out
                  if board.workdir(root) != root]


def _units():
    """The user services projects set up for themselves on this machine (colony's own is made again by autostart)."""
    from . import autostart
    folder = autostart.path().parent
    return [f for f in sorted([*folder.glob("*.service"), *folder.glob("*.timer")])
            if f.name != autostart.unit_name()] if folder.is_dir() else []


def _recorded(roots):
    """The conversations colony recorded for these folders, each provider's: (folder, transcript)."""
    try:
        rows = json.loads((board.home() / "conversations.json").read_text())
    except (OSError, ValueError):
        rows = {}
    for root in roots:
        row = rows.get(str(root)) or {}
        for saved in [row, *(row.get("by_provider") or {}).values()]:
            if saved.get("transcript"):
                yield root, Path(saved["transcript"])
        try:
            yield root, Path(json.loads((Path(root) / ".board" / "context-session.json").read_text())["path"])
        except (OSError, ValueError, KeyError, TypeError):
            pass


def parts(reg=None):
    """What a pack takes, as [{kind, name, path, inside}]: colony's home, the program, each project folder on the board and
    on its shelf, the Claude Code conversations of each (and of colony's home, where the steward works), and a recorded
    conversation kept anywhere else. One inside another (the projects in the program's folder) is packed with it, and
    keeps its own name and size; inside names the outermost."""
    from . import providers
    reg = reg or board.registry()
    out, seen = [], set()

    def add(kind, name, path):
        path = Path(path)
        if str(path) not in seen and path.exists():
            seen.add(str(path))
            out.append({"kind": kind, "name": name, "path": path, "inside": None})

    add("home", "colony's home", board.home())
    if program():
        add("program", "the colony program", program())
    folders = _projects(reg)
    for kind, name, path in folders:
        add(kind, name, path)
    # Claude Code keeps a folder's conversations under its own name for it, and those of each folder inside it (a
    # helper's worktree, a run in a subfolder) under a longer one that begins the same; each is named for the folder
    # it belongs to most nearly
    keys = {}
    for name, place in [("colony's home", board.home()), *([("the colony program", program())] if program() else []),
                        *[(name, p) for _, name, p in folders]]:
        keys.setdefault(claude_key(place), name)
    talks = Path(providers.get("claude").config_home()) / "projects"
    for d in sorted(talks.iterdir()) if talks.is_dir() else []:
        near = max((k for k in keys if d.name == k or d.name.startswith(k + "-")), key=len, default=None)
        if near and d.is_dir() and not d.is_symlink():
            rest = d.name[len(near):].lstrip("-")
            add("claude", "the steward" if (keys[near], rest) == ("colony's home", "monitor") else
                f"{keys[near]} ({rest})" if rest else keys[near], d)
    for root, transcript in _recorded([p for _, _, p in folders]):
        if transcript.is_file() and not any(_under(transcript, p["path"]) for p in out):
            add("conversation", f"{Path(root).name}'s conversation", transcript)
    for unit in _units():                            # back in place unpacked, never started by it
        add("service", unit.name, unit)
    out.sort(key=lambda p: str(p["path"]))
    for p in out:                                    # sorted, an outer one comes first and is settled first
        p["inside"] = next((str(q["path"]) for q in out if q["inside"] is None and q is not p
                            and _under(p["path"], q["path"])), None)
    return out


def _never():
    """Sign-ins and keys, found where the code keeps them: {path: (what it is, how it comes back)}. None is ever packed."""
    from . import bench, connections, providers
    claude, codex = providers.get("claude"), providers.get("codex")
    return {Path(claude.config_home()) / ".credentials.json": ("Claude Code's sign-in", "claude, then /login"),
            Path(codex.config_home()) / "auth.json": ("Codex's sign-in", "codex login"),
            claude.token_path(): ("colony's long-lived Claude sign-in", "claude setup-token, pasted in Settings"),
            connections.keys_dir(): ("the connections' keys", "entered again on the Connections page"),
            bench.key_path(): ("the Artificial Analysis key", "colony bench key"),
            board.home() / "console-token": ("the board's key for its consoles", "made again by colony"),
            board.home() / "page-secret": ("the board's key for its pages", "made again by colony")}


def _remade():
    """What colony's home holds that the new machine makes again: {path: why}."""
    from . import voice
    out = {voice.folder() / "env": "the voice's own Python (colony voice --setup)",
           voice.folder() / "cache": "the voice's clips, made as it reads aloud",
           voice.folder() / "samples": "the voice's samples, made as the board starts",
           board.home() / "context-cache": "colony's reading of transcripts, made again as it reads",
           board.home() / "line-scratch": "colony's scratch, made again as it reads"}
    out.update({voice.folder() / f: "the voice's model (colony voice --setup)" for f in voice.FILES})
    return out


def _tagged(folder, names):
    if "CACHEDIR.TAG" not in names:
        return False
    try:
        with open(os.path.join(folder, "CACHEDIR.TAG"), "rb") as f:
            return f.read(len(CACHE_TAG)) == CACHE_TAG
    except OSError:
        return False


def _lsize(path):
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0


def _bytes(folder):
    total, stack = 0, [str(folder)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


class Plan:
    """What a pack takes, looked over once for the sizes (survey): each part's files and bytes, and what's left out and
    why. Packing walks it again by the same rules, taking what is there by then."""

    def __init__(self, parts):
        self.parts = parts
        self.never, self.remade = _never(), _remade()
        self.codex = board.home() / "codex-remote"
        self.left = []           # [{path, why, bytes}]: what the new machine makes again
        self.kept_out = []       # [{path, what, how}]: sign-ins and keys met inside what's packed
        self.keylike, self.unreadable, self.special = [], [], 0
        for p in parts:
            p.update(files=0, bytes=0)

    def _why(self, path, is_dir, is_link):
        """Why a pack leaves this out, ("never", what, how) or ("remade", why), or None."""
        if path in self.never:
            return ("never",) + self.never[path]
        if path in self.remade:
            return ("remade", self.remade[path])
        if path.parent.parent == self.codex:
            if is_dir and path.name in CODEX_CACHES:
                return ("remade", "Codex's own caches, made again as it runs")
            if path.name == "auth.json" and not is_link:            # its home links the person's; a copy is a sign-in
                return ("never", "a copy of Codex's sign-in", "codex login")
        if is_dir and path.name in ("node_modules", "__pycache__"):
            return ("remade", path.name)
        return None

    def _leave(self, path, why, is_dir):
        if why[0] == "never":
            self.kept_out.append({"path": str(path), "what": why[1], "how": why[2]})
        else:
            self.left.append({"path": str(path), "why": why[1], "bytes": _bytes(path) if is_dir else _lsize(path)})

    def walk(self, part, record=False):
        """Every file, folder and link of an outermost part that a pack takes, parents first. Recording, each file's size
        goes to every part holding it, and what's left out goes on the lists."""
        inner = {str(p["path"]): p for p in self.parts if p["inside"] == str(part["path"])}
        top = part["path"]
        if not top.is_dir() or top.is_symlink():                    # a conversation kept by itself
            if record:
                part["files"], part["bytes"] = 1, _lsize(top)
            yield top
            return
        stack = [(top, (part,))]
        while stack:
            folder, chain = stack.pop()
            try:
                with os.scandir(folder) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError:
                if record:
                    self.unreadable.append(str(folder))
                continue
            names = {e.name for e in entries}
            if chain[-1]["path"] != folder:                          # a part is taken whole, whatever it holds
                why = (("remade", "a Python environment") if "pyvenv.cfg" in names else
                       ("remade", "a cache (its CACHEDIR.TAG says so)") if _tagged(folder, names) else None)
                if why:
                    if record:
                        self._leave(folder, why, True)
                    continue
            yield folder
            below = []
            for e in entries:
                path = Path(e.path)
                try:
                    is_dir, is_link = e.is_dir(follow_symlinks=False), e.is_symlink()
                except OSError:
                    continue
                why = self._why(path, is_dir, is_link)
                if why:
                    if record:
                        self._leave(path, why, is_dir)
                    continue
                if is_dir:
                    below.append((path, chain + (inner[str(path)],) if str(path) in inner else chain))
                elif is_link or e.is_file(follow_symlinks=False):
                    if record:
                        try:
                            n = e.stat(follow_symlinks=False).st_size
                        except OSError:
                            continue                         # gone since its folder was read
                        for p in chain:
                            p["files"] += 1
                            p["bytes"] += n
                        if not is_link and KEYLIKE.match(e.name):
                            self.keylike.append(str(path))
                    yield path
                elif record:
                    self.special += 1                        # a socket or a pipe: nothing to carry
            stack.extend(reversed(below))

    def survey(self):
        for p in self.parts:
            if p["inside"] is None:
                for _ in self.walk(p, record=True):
                    pass
        return self

    @property
    def files(self):
        return sum(p["files"] for p in self.parts if p["inside"] is None)

    @property
    def bytes(self):
        return sum(p["bytes"] for p in self.parts if p["inside"] is None)


def _crontab():
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def machine(reg=None):
    """What belongs to this machine and isn't carried as files, recorded so the new one can be made the same or the
    person told: the voice, starting with the machine, remote access, the programs and sign-ins in use, which of the
    projects' own services were enabled, and the crontab's lines (not a file of the person's: kept here whole)."""
    from . import autostart, bench, connections, providers, voice
    reg = reg or board.registry()
    used = {providers.key(providers.of(p)) for p in board.projects(reg, adopt=False) if p.exists()}
    used.add(providers.key(providers.of(None)))                       # the steward's
    held = connections.keys_dir()
    keys = sorted({f.stem if f.parent == held else f.parent.name for f in held.rglob("*.env")}) if held.is_dir() else []
    mine = {u.name for u in _units()}
    enabled = sorted({w.name for w in autostart.path().parent.glob("*.wants/*") if w.name in mine})
    on = autostart.available() and autostart.on()
    return {"voice": voice.ready(), "autostart": on, "lingers": bool(on and autostart.lingers()),
            "remote": reg["settings"].get("tailscale") or "", "https": bool(reg["settings"].get("https_board")),
            "providers": sorted(used), "claude_token": bool(providers.get("claude").token()),
            "connection_keys": keys, "aa_key": bench.key_path().exists(), "crontab": _crontab(),
            "services_enabled": enabled}


def _outside(plan):
    """The sign-ins and keys outside what's packed: named, so the manifest says each stays behind."""
    met = {r["path"] for r in plan.kept_out}
    return [{"path": str(p), "what": what, "how": how} for p, (what, how) in plan.never.items()
            if str(p) not in met and (p.exists() or p.is_symlink())]


def manifest(plan, reg=None):
    """What the archive holds and leaves out, for the person and for the unpack: of a key, only its name."""
    return {"colony_migrate": FORMAT, "made": board.now(), "machine_name": socket.gethostname(),
            "user": os.environ.get("USER", ""), "home": str(Path.home()), "colony_home": str(board.home()),
            "program": str(program()) if program() else None,
            "parts": [{"kind": p["kind"], "name": p["name"], "path": str(p["path"]), "inside": p["inside"],
                       "files": p["files"], "bytes": p["bytes"]} for p in plan.parts],
            "files": plan.files, "bytes": plan.bytes,
            "left_out": {"remade": plan.left, "sign_ins_and_keys": plan.kept_out + _outside(plan),
                         "unreadable": plan.unreadable, "sockets_and_pipes": plan.special},
            "keylike_packed": plan.keylike, "machine": machine(reg)}


def report(plan, say=print):
    """The plan in plain lines: each part with its size, the biggest, the total, and what's left out and why."""
    home = str(Path.home())
    short = lambda p: "~" + str(p)[len(home):] if _under(p, home) else str(p)
    by = lambda *kinds: [p for p in plan.parts if p["kind"] in kinds]
    say("What colony migrate --pack takes, before compression:")
    for title, kinds in (("colony", ("home", "program")), ("Projects on the board", ("project",)),
                         ("Archived projects, off the board (they come back from its Archived shelf)", ("archived",)),
                         ("Conversations, Claude Code's (Codex's are in colony's home, codex-remote)", ("claude",)),
                         ("Conversations recorded elsewhere", ("conversation",)),
                         ("Services projects set up for themselves (back in place unpacked, not started)", ("service",))):
        ps = by(*kinds)
        if ps:
            say(f"  {title}:")
            for p in ps:
                say(f"    {p['name'][:40]:40} {size(p['bytes']):>9}  {short(p['path'])}"
                    + (f"  (inside {short(p['inside'])})" if p["inside"] else ""))
    biggest = sorted(by("home", "program", "project", "archived"), key=lambda p: -p["bytes"])[:6]
    talks = sum(p["bytes"] for p in by("claude"))
    say("  Biggest: " + ", ".join(f"{p['name']} {size(p['bytes'])}" for p in biggest)
        + (f"; Claude Code's conversations {size(talks)} all told" if talks else ""))
    say(f"Total: {size(plan.bytes)} in {plan.files:,} files, less once compressed")
    groups = {}
    for r in plan.left:
        g = groups.setdefault(r["why"], [0, 0])
        g[0], g[1] = g[0] + 1, g[1] + r["bytes"]
    if groups:
        say(f"Left out, made again on the new machine ({size(sum(g[1] for g in groups.values()))}):")
        for why, (n, b) in sorted(groups.items(), key=lambda g: -g[1][1]):
            say(f"    {why}: {n} ({size(b)})")
    never = plan.kept_out + _outside(plan)
    if never:
        say("Never packed, entered again on the new machine:")
        for r in never:
            say(f"    {short(r['path'])}: {r['what']} ({r['how']})")
    if plan.keylike:
        say(f"Packed as they are, so keep the archive private: {len(plan.keylike)} project file(s) that look like keys ("
            + ", ".join(short(p) for p in plan.keylike[:4]) + (", …" if len(plan.keylike) > 4 else "") + ")")
    if plan.unreadable:
        say(f"Couldn't be read, so not packed: {len(plan.unreadable)} folder(s) ({', '.join(plan.unreadable[:3])})")


def _cron(m):
    """The crontab's own lines, without its comments."""
    return [l.strip() for l in m["crontab"].splitlines() if l.strip() and not l.lstrip().startswith("#")]


def listing(say=print):
    """colony migrate --list."""
    plan = Plan(parts()).survey()
    report(plan, say)
    lines = _cron(machine())
    if lines:
        say(f"The crontab ({len(lines)} line{'s' * (len(lines) != 1)}) isn't a file a pack can carry: its manifest keeps "
            "the lines, and the unpack shows them to add again.")
    return plan


# ---------------------------------------------------------------- packing

class _Exactly:
    """A file read for exactly the size it had when opened: one that grows is cut there, one that shrinks is padded, so
    the archive stays whole while a transcript is still being written."""

    def __init__(self, f, n):
        self.f, self.left, self.short = f, n, False

    def read(self, n=-1):
        n = self.left if n < 0 else min(n, self.left)
        data = self.f.read(n)
        if len(data) < n:
            self.short = True
            data += b"\0" * (n - len(data))
        self.left -= len(data)
        return data


def _at_work(plan):
    """Projects mid-work now: as the steward's watch saw them, or with a conversation written in the last two minutes."""
    busy = set(board.at_work_now())
    for root, transcript in _recorded([p["path"] for p in plan.parts if p["kind"] in ("project", "archived")]):
        try:
            if time.time() - transcript.stat().st_mtime < 120:
                busy.add(str(Path(root).resolve()))
        except OSError:
            pass
    return sorted(Path(b).name for b in busy)


def _zstd():
    return shutil.which("zstd")


def _write(path, plan, head, say):
    """The archive at path: the manifest first, then every outermost part. (members, bytes, files cut) as packed."""
    info = tarfile.TarInfo(MANIFEST)
    info.size, info.mtime, info.mode = len(head), time.time(), 0o600
    proc = None
    if _zstd():
        proc = subprocess.Popen([_zstd(), "-q", "-T0", "--check", "-f", "-o", str(path)], stdin=subprocess.PIPE)
        tar = tarfile.open(fileobj=proc.stdin, mode="w|", format=tarfile.PAX_FORMAT, bufsize=1 << 20)
    else:
        tar = tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT, compresslevel=6)
    tar.copybufsize = 1 << 16
    members = done = 0
    cut, last = [], time.time()
    try:
        tar.addfile(info, io.BytesIO(head))
        for part in plan.parts:
            if part["inside"] is not None:
                continue
            for p in plan.walk(part):
                name = str(p).lstrip("/")
                try:
                    if stat.S_ISREG(os.lstat(p).st_mode):
                        with open(p, "rb") as f:
                            member = tar.gettarinfo(arcname=name, fileobj=f)
                            reader = _Exactly(f, member.size) if member.isreg() else None
                            tar.addfile(member, reader)
                        if reader and reader.short:
                            cut.append(str(p))
                        done += member.size if member.isreg() else 0
                    else:
                        member = tar.gettarinfo(str(p), arcname=name)
                        if member is None:
                            continue
                        tar.addfile(member)
                except FileNotFoundError:
                    continue                                 # gone since it was listed
                except PermissionError:
                    plan.unreadable.append(str(p))
                    continue
                members += 1
                if time.time() - last > PROGRESS:
                    last = time.time()
                    say(f"  packed {size(done)} of {size(plan.bytes)}…")
        tar.close()
    finally:
        if proc:
            proc.stdin.close()
            if proc.wait():
                raise OSError(f"zstd stopped (exit {proc.returncode}) writing {path}")
    with open(path, "rb") as f:
        os.fsync(f.fileno())
    return members, done, cut


class _Reading:
    """An archive read as a stream, through zstd where it is one (or Python's own, from 3.14). whole: the reader took it
    to its end, so zstd's own check of it counts; stopped early, zstd is let go."""

    def __init__(self, path):
        self.path, self.proc, self.whole = Path(path), None, False

    def __enter__(self):
        with open(self.path, "rb") as f:
            zst = f.read(4) == ZSTD_MAGIC
        try:
            if zst and _zstd():
                self.proc = subprocess.Popen([_zstd(), "-d", "-q", "-c", str(self.path)], stdout=subprocess.PIPE)
                self.tar = tarfile.open(fileobj=self.proc.stdout, mode="r|", bufsize=1 << 20)
            else:
                self.tar = tarfile.open(self.path, "r|*", bufsize=1 << 20)
        except (tarfile.ReadError, tarfile.CompressionError):
            if self.proc:
                self.proc.kill()
                self.proc.wait()
            raise SystemExit(f"colony: {self.path} can't be opened as an archive here"
                             + (": it needs zstd (sudo apt install zstd)" if zst and not _zstd() else ""))
        self.tar.copybufsize = 1 << 20
        return self

    def members(self):
        """Each member after the manifest, the reader keeping no list of them (a stream is read once)."""
        for m in self.tar:
            if m.name != MANIFEST:
                yield m
            if len(self.tar.members) > 4096:
                del self.tar.members[:-1]

    def __exit__(self, *exc):
        if self.proc:
            if exc[0] or not self.whole:
                self.proc.kill()
            else:
                while self.proc.stdout.read(1 << 20):       # tar's closing blocks
                    pass
            self.proc.stdout.close()
            if self.proc.wait() and self.whole and not exc[0]:
                raise OSError(f"zstd found {self.path} damaged (exit {self.proc.returncode})")
        self.tar.close()
        return False


def verify(path):
    """Read an archive back whole: (members, bytes) it holds, every byte decompressed and checked on the way."""
    members = done = 0
    with _Reading(path) as r:
        for m in r.members():
            members += 1
            if m.isreg():
                f = r.tar.extractfile(m)
                while f.read(1 << 20):
                    pass
                done += m.size
        r.whole = True
    return members, done


def _pyz(dest):
    """The program to unpack with on a machine without colony yet: this one, as a single file Python runs."""
    import zipfile
    package = Path(__file__).resolve().parent
    target, partial = dest / "colony.pyz", dest / "colony.pyz.part"
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("__main__.py", "import sys\nfrom colony.cli import main\nsys.exit(main())\n")
        for f in sorted(package.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, str(f.relative_to(package.parent)))
    partial.replace(target)
    return target


def pack(dest, say=print):
    """colony migrate --pack DIR: the archive, its manifest and the program to unpack it with, into DIR."""
    dest = Path(dest).expanduser().resolve()
    reg = board.registry()
    plan = Plan(parts(reg))
    within = next((p for p in plan.parts if dest == p["path"] or _under(dest, p["path"])), None)
    if within:
        raise SystemExit(f"colony: {dest} is inside {within['path']}, which the archive takes: choose a folder outside "
                         "what's packed (another disk, ideally)")
    plan.survey()
    report(plan, say)
    need = plan.bytes + 1536 * (plan.files + 1) + 2 ** 20         # each file's header, padding and PAX record
    dest.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(dest).free
    if free < need:
        raise SystemExit(f"colony: {dest} has {size(free)} free, and the archive can take up to {size(need)} (less once "
                         "compressed, but a pack is never started that might not finish): make room, or choose another disk")
    busy = _at_work(plan)
    if busy:
        say(f"Mid-work now: {', '.join(busy)}. Packing goes on: a conversation still being written is taken as it stands "
            "at that moment, whole up to there. For the cleanest copy, colony stop first (each console resumes its "
            "conversation after the move).")
    stamp = time.strftime("%Y-%m-%d-%H%M")
    name = f"colony-{stamp}.tar." + ("zst" if _zstd() else "gz")
    archive, partial = dest / name, dest / (name + ".part")
    note = manifest(plan, reg)
    say(f"Packing into {archive}" + ("" if _zstd() else " (zstd isn't here, so gzip: slower, and larger)") + "…")
    try:
        members, done, cut = _write(partial, plan, json.dumps(note, indent=1).encode(), say)
        say("Reading it back whole…")
        got = verify(partial)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    if got != (members, done):
        partial.unlink(missing_ok=True)
        raise SystemExit(f"colony: the archive didn't read back as written ({got[0]:,} entries and {size(got[1])}, of "
                         f"{members:,} and {size(done)}), so it wasn't kept: try again, or another disk")
    partial.replace(archive)
    note.update(packed={"entries": members, "bytes": done, "archive_bytes": archive.stat().st_size,
                        "compression": "zstd" if _zstd() else "gzip", "changed_while_packing": cut})
    note["left_out"]["unreadable"] = plan.unreadable
    side = dest / f"colony-{stamp}.manifest.json"
    side.write_text(json.dumps(note, indent=1) + "\n")
    tool = _pyz(dest)
    say(f"Packed, and read back whole: {members:,} entries, {size(done)}, in {size(archive.stat().st_size)}.")
    say(f"  archive   {archive}")
    say(f"  manifest  {side}  (what it holds, and what it leaves out)")
    say(f"  program   {tool}  (to unpack it with, on a machine without colony yet)")
    if cut:
        say(f"  {len(cut)} file(s) changed as they were packed, each taken as it stood (the manifest names them)")
    say(f"On the new machine, signed in as {os.environ.get('USER') or Path.home().name}, with this disk attached:")
    say(f"  python3 {tool} migrate --unpack {archive}")
    return archive


# ---------------------------------------------------------------- unpacking

def _guard(note):
    """The extraction filter: tar's own (no device files, nothing outside its destination, no setuid bits), and besides,
    each member only inside a part the manifest names, never written through a link the archive itself made.
    Permissions stay as they were packed (tar's own filter would take away group write)."""
    roots = [p["path"].lstrip("/") for p in note["parts"]]
    links = set()

    def keep(member, dest):
        name = member.name
        if not any(name == r or name.startswith(r + "/") for r in roots) or member.isdev() or member.isfifo():
            raise ValueError(f"/{name} is outside what the manifest says this archive holds")
        bits = name.split("/")
        if any("/".join(bits[:i]) in links for i in range(1, len(bits))):
            raise ValueError(f"/{name} would be written through a link in the archive")
        if member.issym():
            links.add(name)
        if hasattr(tarfile, "tar_filter"):
            member = tarfile.tar_filter(member, dest).replace(mode=member.mode & 0o777, deep=False)
        return member
    return keep


def _board_running():
    return subprocess.run(["tmux", "has-session", "-t", board.scoped("board-server")],
                          capture_output=True).returncode == 0


def unpack(archive, force=False, say=print):
    """colony migrate --unpack ARCHIVE: everything back at its own path, then what belongs to this machine made again."""
    archive = Path(archive).expanduser().resolve()
    if not archive.is_file():
        raise SystemExit(f"colony: no archive at {archive}")
    with _Reading(archive) as r:
        first = r.tar.next()
        if first is None or first.name != MANIFEST:
            raise SystemExit(f"colony: {archive} isn't an archive colony migrate --pack made")
        note = json.loads(r.tar.extractfile(first).read())
        if note.get("colony_migrate") != FORMAT:
            raise SystemExit(f"colony: {archive} was made by another version of colony migrate: unpack it with the "
                             "colony.pyz packed beside it")
        if note["home"] != str(Path.home()) or note["colony_home"] != str(board.home()):
            raise SystemExit(f"colony: this archive was made for the home folder {note['home']} (colony's in "
                             f"{note['colony_home']}), and here it is {Path.home()} (colony's in {board.home()}). Unpack "
                             "it signed in with the same username; moving colony to another home folder comes later.")
        if board.home().exists() and any(board.home().iterdir()) and not force:
            raise SystemExit(f"colony: {board.home()} already holds a colony, which unpacking would write over: --force "
                             "does, if that's what you want")
        if shutil.which("tmux") and _board_running():
            raise SystemExit("colony: colony is running here: colony stop first, then unpack")
        free = shutil.disk_usage(Path.home()).free
        if free < note["bytes"]:
            raise SystemExit(f"colony: the archive holds {size(note['bytes'])}, and {Path.home()} has {size(free)} free")
        try:
            made = time.strftime("%Y-%m-%d %H:%M", time.localtime(board.epoch(note["made"])))
        except (KeyError, ValueError):
            made = "at an unknown time"
        say(f"Unpacking colony from {note['machine_name']}, packed {made}: {note['files']:,} files, "
            f"{size(note['bytes'])}, each to its own path…")
        try:
            r.tar.extractall("/", members=r.members(), filter=_guard(note))
        except (OSError, ValueError, tarfile.TarError) as err:      # a member refused, or one that couldn't be written
            raise SystemExit(f"colony: unpacking stopped: {err}. What was unpacked before it stays; once that's put "
                             "right, --force unpacks it all again")
        r.whole = True
    kinds = [p["kind"] for p in note["parts"]]
    say(f"Unpacked: colony's home, {kinds.count('project')} project folder(s) on the board and {kinds.count('archived')} "
        f"archived, {kinds.count('claude')} folder(s) of Claude Code's conversations"
        + (", and the colony program" if "program" in kinds else "") + ".")
    for line in remake(note):
        say(line)
    return note


def remake(note):
    """What belongs to this machine, made again as the old one had it, each step said; then what's the person's: programs
    to install, sign-ins and keys, and what projects set up for themselves outside their folders."""
    m = note["machine"]
    checkout = Path(note["program"]) if note.get("program") else None
    out = ["This machine:", "  The colony command: " + _command(checkout),
           "  The voice: " + (_voice() if m["voice"] else "it wasn't set up there (colony voice --setup, about 500 MB)"),
           "  Starting with the machine: " + (_autostart(checkout, m) if m["autostart"] else
                                              "it was off there (Settings › Start with the machine turns it on)"),
           "  Remote access: " + _remote(m)]
    needed = [("tmux", "tmux", "sudo apt install tmux"), ("git", "git", "sudo apt install git")]
    needed += [("Claude Code", "claude", "https://claude.com/claude-code")] if "claude" in m["providers"] else []
    needed += [("Codex", "codex", "https://developers.openai.com/codex")] if "codex" in m["providers"] else []
    missing = [f"{label} ({how})" for label, program_, how in needed if not shutil.which(program_)]
    if missing:
        out.append("To install here: " + "; ".join(missing))
    signs = (["Claude Code (claude, then /login)"] if "claude" in m["providers"] else []) \
        + (["Codex (codex login)"] if "codex" in m["providers"] else [])
    out.append("Sign in again, as sign-ins never travel in the archive: " + "; ".join(signs))
    later = (["colony's long-lived Claude sign-in (claude setup-token, pasted in Settings)"] if m["claude_token"] else []) \
        + ([f"the keys of {', '.join(m['connection_keys'])} (the Connections page)"] if m["connection_keys"] else []) \
        + (["the Artificial Analysis key (colony bench key)"] if m["aa_key"] else [])
    if later:
        out.append("Then enter again: " + "; ".join(later))
    envs = sorted({str(Path(r["path"]).parent) for r in note["left_out"]["remade"]
                   if r["why"] in ("a Python environment", "node_modules")})
    if envs:
        out.append("Left behind in these folders, to be made again when next needed (a Python environment, "
                   f"node_modules): {', '.join(envs[:8])}" + (", …" if len(envs) > 8 else ""))
    services = [p["name"] for p in note["parts"] if p["kind"] == "service"]
    if services:
        on = [s for s in m.get("services_enabled", []) if s in services]
        out.append(f"Back in place but not started, the services projects set up for themselves: {', '.join(services)}"
                   + (f". Enabled there: {', '.join(on)}; each is started again with systemctl --user enable --now NAME, "
                      "once its project runs here" if on else ""))
    if _cron(m):
        out.append("The old machine's crontab had these lines, set up by projects (crontab -e adds them again): "
                   + " | ".join(_cron(m)))
    out.append("Then start colony: colony board. Consoles that were running come back in their own conversations, as "
               "after a restart.")
    return out


def _command(checkout):
    """The colony command, installed as the start script installs it: this checkout, editable, for this user."""
    if shutil.which("colony"):
        return "here"
    if not checkout:
        return "not here: install colony again (its start script)"
    if not _has("pip"):
        return f"not installed, as pip isn't here (sudo apt install python3-pip, then {checkout / 'start'})"
    base = [sys.executable, "-m", "pip", "install", "--user", "-q", "-e", str(checkout)]
    r = subprocess.run(base, capture_output=True, text=True)
    if r.returncode:
        r = subprocess.run(base + ["--break-system-packages"], capture_output=True, text=True)
    return ("installed (~/.local/bin/colony)" if r.returncode == 0 else
            f"not installed ({(r.stderr or r.stdout).strip()[-200:]}): {checkout / 'start'} tries again")


def _has(module):
    import importlib.util
    return importlib.util.find_spec(module) is not None


def _voice():
    from . import voice
    if not _has("ensurepip"):
        return "not made yet: Python can't make an environment here (sudo apt install python3-venv, then colony voice --setup)"
    try:
        voice.setup(say=lambda line: None)
        return "made again (Kokoro, in colony's home)"
    except Exception as err:                       # the voice is extra: Chat reads in the phone's own until it's back
        return (f"not made ({err.__class__.__name__}: {str(err)[:160]}); Chat reads in the phone's own voice until "
                "colony voice --setup succeeds")


def _autostart(checkout, m):
    """On again, written by the checkout's own code, so the service starts that checkout (not the program unpacking)."""
    from . import autostart
    if not autostart.available():
        return "not here: this machine doesn't run systemd"
    if not checkout:
        return "on once colony is installed again (Settings › Start with the machine)"
    r = subprocess.run([sys.executable, "-c", "from colony import autostart; print(autostart.turn(True))"],
                       cwd=str(checkout), capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(checkout)))
    said = (r.stdout.strip().splitlines() or [""])[-1] if r.returncode == 0 else ""
    if not said:
        return (f"not turned on ({(r.stderr or r.stdout).strip()[-200:]}): Settings › Start with the machine, once "
                "colony runs")
    return said + (" (it started at boot there too: sudo loginctl enable-linger $USER does that here)"
                   if m.get("lingers") and not autostart.lingers() else "")


def _remote(m):
    """Remote access as it was: Tailscale is the person's to install and log in to; colony takes it up again."""
    from . import serving, tailscale
    if not m["remote"]:
        return "it was off there (colony remote sets it up, through Tailscale)"
    st = tailscale.status()
    if not st["installed"]:
        return (f"it was on there ({m['remote']}), and Tailscale isn't installed here: curl -fsSL "
                "https://tailscale.com/install.sh | sh, then sudo tailscale up --operator=$USER and log in with the "
                "account your phone uses, then colony remote")
    if not st["running"]:
        return (f"it was on there ({m['remote']}), and Tailscale isn't connected here: sudo tailscale up "
                "--operator=$USER and log in with the account your phone uses, then colony remote")
    why = serving.ready()
    return (f"Tailscale is connected as {st['name']}"
            + (f", where the old machine was {m['remote']}: colony remote takes up the new name (or remove the old "
               "machine in Tailscale's admin page first, and this one keeps its name)" if st["name"] != m["remote"] else "")
            + (f"; HTTPS once the board starts" if m["https"] and not why else f"; HTTPS: {why}" if m["https"] else "")
            + " (colony remote --check says where it stands)")
