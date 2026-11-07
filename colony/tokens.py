"""Tokens spent, from the programs' own records: every session's transcript (Claude Code's, Codex's, those of the Codex sessions colony runs remotely), read once and
then only as it grows, summed by the hour for each program and the folder the session ran in. Colony's Overview shows
the last 4 hours, 24 hours and week, by program and by project, and apart from the projects' own work what colony
itself spent: the steward, and the one-off asks it makes (consultants, judges of measures, connection audits, model
checks) and the steward's scouts, which keep no transcript and note their own here.

Colony's notes and instructions inside a project's session are part of that session, so they count as the project's.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

from . import board

KEEP = 8 * 24 * 3600                    # a week, and a day's margin
BUDGET = 20                             # seconds one look may spend reading; the rest waits for the next look
WINDOWS = (("4 h", 4 * 3600), ("24 h", 24 * 3600), ("7 d", 7 * 24 * 3600))
TMP = re.compile(r"^/tmp/colony-([a-z]+)-")
# what colony's own asks are, by the folder each runs in
ASKS = {"consult": "Consultants, judges and audits", "models": "Model checks", "token": "Model checks", "scout": "Steward's scouts",
        "research": "Research passes (Q Branch)", "screen": "Research screens", "qbranch": "Guide upkeep (Q Branch)"}
_lock = threading.Lock()


def path():
    return board.home() / "usage" / "tokens.json"


def load():
    try:
        return json.loads(path().read_text())
    except (OSError, ValueError):
        return {"files": {}, "hours": {}}


def _save(state):
    path().parent.mkdir(parents=True, exist_ok=True)
    tmp = path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path())


def _hour(ts):
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()) // 3600 * 3600
    except ValueError:
        return None


def _add(state, hour, prog, cwd, fresh, read, write, out):
    if hour is None or hour < time.time() - KEEP:
        return
    k = f"{hour}|{prog}|{cwd}"
    v = state["hours"].setdefault(k, [0, 0, 0, 0])
    for i, n in enumerate((fresh, read, write, out)):
        v[i] += int(n or 0)


def _where(cwd):
    """A session's folder as kept: colony's one-off asks by their kind (each runs in a fresh temporary folder)."""
    m = TMP.match(cwd or "")
    return f"colony:{m[1]}" if m else cwd or ""


def _home_repo(cwd):
    """The repository a git worktree belongs to: a helper building in a copy of its project elsewhere (/tmp/…) works
    for that project. None for any other folder."""
    p = Path(cwd)
    for folder in (p, *list(p.parents)[:3]):
        git = folder / ".git"
        if git.is_dir():
            return None
        if git.is_file():
            try:
                m = re.match(r"gitdir:\s*(.+?)/\.git/worktrees/[^/\n]+\s*$", git.read_text())
            except OSError:
                return None
            return m[1] if m else None
    return None


def _project_env(proc):
    """COLONY_PROJECT of a process or the nearest of its parents: what a project's console starts carries it."""
    for _ in range(8):
        try:
            for kv in (proc / "environ").read_bytes().split(b"\0"):
                if kv.startswith(b"COLONY_PROJECT="):
                    return kv[15:].decode(errors="replace") or None
            ppid = (proc / "stat").read_text().rsplit(")", 1)[1].split()[1]
        except (OSError, IndexError):
            return None
        if ppid in ("0", "1"):
            return None
        proc = Path("/proc") / ppid
    return None


def _holders():
    """{transcript: the project whose console started its writer}, from the running processes holding one open."""
    out = {}
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            open_ = [os.readlink(fd) for fd in (proc / "fd").iterdir()]
        except OSError:
            continue
        open_ = [x for x in open_ if x.endswith(".jsonl")]
        if open_ and (project := _project_env(proc)):
            out.update(dict.fromkeys(open_, project))
    return out


class _Places:
    """Where each transcript's tokens are kept: its folder, unless that folder is no project's and colony's (a
    temporary folder): then a git worktree's repository, else the project whose console started the session, read from
    the running process. Looked up once per transcript and folder."""

    def __init__(self, roots, steward):
        self.roots, self.steward, self.held = roots, steward, None

    def outside(self, cwd):
        return owner(cwd, self.roots, self.steward)[0] == "outside"

    def __call__(self, st, f):
        cwd = st.get("cwd") or ""
        if st.get("where_of") != cwd or "where" not in st:
            where = _where(cwd)
            if where and self.outside(where):
                where = _home_repo(cwd) or self._started_by(f) or where
            st["where_of"], st["where"] = cwd, where
        return st["where"]

    def _started_by(self, f):
        if self.held is None:
            self.held = _holders()
        return self.held.get(str(f))


def _claude(state, f, st, place=lambda st, f: _where(st.get("cwd"))):
    with open(f, "rb") as fh:
        fh.seek(st["at"])
        for raw in fh:
            if not raw.endswith(b"\n"):
                break                                   # a line still being written: read it whole next time
            st["at"] += len(raw)
            if b'"usage"' not in raw or b'"assistant"' not in raw:
                if not st.get("cwd") and b'"cwd"' in raw:
                    try:
                        st["cwd"] = json.loads(raw).get("cwd") or ""
                    except ValueError:
                        pass
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            m = d.get("message") or {}
            u, mid = m.get("usage"), m.get("id")
            st["cwd"] = d.get("cwd") or st.get("cwd") or ""
            # one reply is written as a line per part, each carrying the reply's whole usage: counted once
            if not u or not mid or mid in st.setdefault("seen", []) or m.get("model") == "<synthetic>":
                continue
            st["seen"] = (st["seen"] + [mid])[-20:]
            _add(state, _hour(d.get("timestamp")), "claude", place(st, f), u.get("input_tokens"),
                 u.get("cache_read_input_tokens"), u.get("cache_creation_input_tokens"), u.get("output_tokens"))


def _codex(state, f, st, place=lambda st, f: _where(st.get("cwd"))):
    with open(f, "rb") as fh:
        fh.seek(st["at"])
        for raw in fh:
            if not raw.endswith(b"\n"):
                break
            st["at"] += len(raw)
            if b'"session_meta"' in raw and not st.get("cwd"):
                try:
                    st["cwd"] = (json.loads(raw).get("payload") or {}).get("cwd") or ""
                except ValueError:
                    pass
                continue
            if b'"token_count"' not in raw:
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            u = ((d.get("payload") or {}).get("info") or {}).get("last_token_usage")
            if u:
                cached = u.get("cached_input_tokens") or 0
                _add(state, _hour(d.get("timestamp")), "codex", place(st, f), (u.get("input_tokens") or 0) - cached,
                     cached, u.get("cache_write_input_tokens"), u.get("output_tokens"))


def sources(keep=KEEP):
    """Every transcript written to in the kept week (or `keep` seconds): Claude Code's (subagents' too) and Codex's rollouts."""
    since = time.time() - keep
    found = []
    from . import providers as pv
    bases = [(Path(pv.get("claude").config_home()) / "projects", "claude"), (Path(pv.get("codex").config_home()) / "sessions", "codex")]
    bases += [(d / "sessions", "codex") for d in board.codex_homes().glob("*")]   # the remote sessions colony keeps
    for base, prog in bases:
        if base.exists():
            for f in base.rglob("*.jsonl"):
                try:
                    if (m := f.stat().st_mtime) >= since:
                        found.append((m, f, prog))
                except OSError:
                    pass
    return [(f, prog) for _, f, prog in sorted(found, key=lambda x: x[0], reverse=True)]    # the newest first


def update(budget=BUDGET):
    """Read what the transcripts gained since the last look, within the budget; drop hours older than the week."""
    if not _lock.acquire(blocking=False):
        return False
    held = _file_lock()
    if held is None:                                    # another process is writing the record: the next look reads
        _lock.release()
        return False
    try:
        from . import monitor
        state, end = load(), time.time() + budget
        files, done = state["files"], True
        place = _Places([Path(p) for p in board.projects(board.registry())], monitor.home())
        if state.get("v", 0) < 2:                       # hours kept before worktrees were followed: theirs now
            hours = {}
            for k, v in state["hours"].items():
                hour, prog, cwd = k.split("|", 2)
                if place.outside(cwd) and (repo := _home_repo(cwd)):
                    k = f"{hour}|{prog}|{repo}"
                    v = [a + b for a, b in zip(hours.get(k, [0, 0, 0, 0]), v)]
                hours[k] = v
            state["hours"], state["v"] = hours, 2
        for f, prog in sources():
            if time.time() > end:
                done = False
                break
            st = files.setdefault(str(f), {"at": 0})
            try:
                size = f.stat().st_size
                if size < st["at"]:
                    st.update(at=0, seen=[])                # rewritten from the start
                if size > st["at"]:
                    (_claude if prog == "claude" else _codex)(state, f, st, place)
            except OSError:
                continue
        old = time.time() - KEEP
        state["hours"] = {k: v for k, v in state["hours"].items() if int(k.split("|", 1)[0]) >= old}
        state["files"] = {k: v for k, v in files.items() if os.path.exists(k)}
        state["looked"] = time.time()
        _save(state)
        return done
    finally:
        held.close()
        _lock.release()


def _file_lock():
    """The record's lock across processes (the board's reader, and the asks colony makes from its command line, which
    note what they spent), taken without waiting: None when another holds it."""
    import fcntl
    board.home().mkdir(parents=True, exist_ok=True)
    fh = open(board.home() / "tokens.lock", "a")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


def note(prog, usage, kind="scout"):
    """An ask that keeps no transcript (the steward's scouts) notes what it spent."""
    if not usage:
        return
    with _lock, board.board_lock("tokens"):           # the board's reader, in another process, writes it too
        state = load()
        hour = int(time.time()) // 3600 * 3600
        if prog == "claude":
            _add(state, hour, prog, f"colony:{kind}", usage.get("input_tokens"), usage.get("cache_read_input_tokens"),
                 usage.get("cache_creation_input_tokens"), usage.get("output_tokens"))
        else:
            _add(state, hour, prog, f"colony:{kind}", usage.get("input_tokens"), 0, 0, usage.get("output_tokens"))
        _save(state)


def _mangled(p):
    return re.sub(r"[^A-Za-z0-9]", "-", str(p))


_OWNERS = {}                 # a folder's owner, kept while the projects are the same


def owner(cwd, roots, steward):
    """Whose a session was: a project (its own folder, a worktree inside it, or a scratch folder its agent's helper ran
    in), colony itself (the steward, its one-off asks), or outside colony."""
    key = (cwd, tuple(str(r) for r in roots), str(steward), str(board.home()))
    if key not in _OWNERS:
        if len(_OWNERS) > 5000:
            _OWNERS.clear()
        _OWNERS[key] = _owner(cwd, roots, steward)
    return _OWNERS[key]


def _owner(cwd, roots, steward):
    if cwd.startswith("colony:"):
        return ("colony", ASKS.get(cwd[7:], "Other asks"))
    if cwd == str(steward) or cwd.startswith(str(steward) + "/"):
        return ("colony", "Steward")
    twin = re.match(re.escape(str(board.agents_home())) + r"/([^/]+?)(?:-codex)?(?:/|$)", cwd)
    if twin:                                            # a project's agent from the other program, kept in colony's folder
        mine = [r for r in roots if Path(r).name == twin[1]]
        return ("project", twin[1]) if mine else ("colony", "Other asks")
    best = None
    for r in roots:
        s = str(r)
        if (cwd == s or cwd.startswith(s + "/") or f"/{_mangled(s)}/" in cwd) and (not best or len(s) > len(str(best))):
            best = r
    return ("project", Path(best).name) if best else ("outside", "Outside colony")


# How a program's limits weigh its tokens, as its prices do: (new input, cache read, cache write, output). A cache read
# costs a tenth of new input; output several times it (Claude 5x, OpenAI's models 8x).
WEIGHTS = {"claude": (1, 0.1, 1.25, 5), "codex": (1, 0.1, 1, 8)}
# a limit window's length, by the name its program reports it under
SPANS = {"5-hour": 5 * 3600, "weekly": 7 * 24 * 3600}


def spent(prog, since, now=None):
    """What each owner (project, colony itself, outside) spent of a program since a time, weighed as its limits weigh
    tokens: {owner: weight}. A window's start falls within an hour: the hour it falls in counts whole."""
    from . import monitor
    roots = [Path(p) for p in board.projects(board.registry())]
    w, out = WEIGHTS.get(prog, (1, 0.1, 1, 5)), {}
    for k, v in load()["hours"].items():
        hour, p, cwd = k.split("|", 2)
        if p == prog and int(hour) + 3600 > since:
            who = owner(cwd, roots, monitor.home())
            who = ("colony", "Colony itself") if who[0] == "colony" else who
            out[who] = out.get(who, 0) + sum(a * b for a, b in zip(w, v))
    return out


def windows(now=None, prog=None):
    """{program: {window: total}, ("project", name): {...}, ("colony", what): {...}, ...} and the share that is cache
    reads; for one program, prog."""
    from . import monitor
    now = now or time.time()
    roots = [Path(p) for p in board.projects(board.registry())]
    steward = monitor.home()
    rows, reads, totals = {}, {w: 0 for w, _ in WINDOWS}, {w: 0 for w, _ in WINDOWS}
    for k, v in load()["hours"].items():
        hour, prog_, cwd = k.split("|", 2)
        if prog and prog_ != prog:
            continue
        prog_key, age, n = prog_, now - int(hour), sum(v)
        who = owner(cwd, roots, steward)
        for w, span in WINDOWS:
            if age <= span:
                for key in (("program", prog_key), who):
                    rows.setdefault(key, {x: 0 for x, _ in WINDOWS})[w] += n
                totals[w] += n
                reads[w] += v[1]
    return rows, totals, reads


def short(n):
    return (f"{n / 1e9:.1f}B" if n >= 1e9 else f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(n))
