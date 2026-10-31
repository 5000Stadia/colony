"""The console: each project's own agent session (its provider's CLI, Claude Code today), live in the board.

Each project's session runs in tmux (`board-<name>`), so it keeps running when the browser closes or the
board restarts, and the person can attach from a terminal too (`tmux attach -t board-<name>`). The board
bridges a browser terminal (xterm.js) to it over a WebSocket that answers only the board's own page and
only with the token the board made when it started.
"""
import base64
import fcntl
import hashlib
import json
import os
import pty
import re
import secrets
import select
import shlex
import shutil
import struct
import subprocess
import termios
import time
from contextlib import contextmanager
from pathlib import Path

def token():
    """The console's key: made once and kept in the board's folder, so a page open in the browser still
    reattaches after the board restarts."""
    from . import board
    path = board.home() / "console-token"
    if not path.exists():
        board.home().mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(24))
        path.chmod(0o600)
    return path.read_text().strip()
# How a session starts is its provider's (colony/providers.py): with Claude Code, Remote Control named for the
# project, so the person can reach it from the Claude app anywhere; an idle session costs nothing.
COMMAND = os.environ.get("COLONY_CONSOLE_CMD")      # set only to replace the provider's CLI (tests, demos)


def command(label, root=None, folder=None):
    """How the project's provider starts its agent, with the project's settings (falling back to the global ones),
    back in the conversation it was last in, if it had one. folder: a console that isn't a project's (the
    steward's) resumes the conversation last active there."""
    if COMMAND:
        return COMMAND.format(name=shlex.quote(label))
    from . import board, providers, selection
    selection.migrate(root)
    s = board.project_settings(root)[0] if root else board.registry()["settings"]
    s = {k: v for k, v in s.items() if k not in board.TURBO}        # what turbo means for it isn't how it starts
    if label != "monitor" or root:
        s = dict(s, **selection.pair(selection.main(root)))
    # PROVIDER: resuming needs the provider to say which conversation its hooks ran in (conversation()) and to
    # take resume= in command(); one that doesn't simply starts fresh after a restart.
    resume = last_conversation(root) if root else None
    if label == "monitor" and not root:
        from . import monitor, context
        model, effort, _ = monitor.choice()
        s = dict(s, model=model, effort=effort, autocompact=monitor.CONTEXT_CAP)
        known = context.session(monitor.home())
        if known and known['provider'] == providers.key(providers.of(None)) and Path(known['path']).exists():
            resume = known['id']
    if not resume and not root and folder and providers.supplies(providers.of(None), "latest_conversation"):
        resume = providers.of(None).latest_conversation(folder)
    # PROVIDER: the label is the session's title in its program's app, where it has one (Claude's Remote Control):
    # the steward goes by the name the person gave it, a change reloading it there once it sits idle.
    title = board.steward_named() or label if label == "monitor" and not root else label
    return providers.of(root).command(title, s, root=root, **({"resume": resume} if resume else {}))


def _conversations():
    from . import board
    return board.home() / "conversations.json"     # this machine's, so kept with the board, not in the project


def transcript_provider(transcript, fallback=None):
    """The file's provider, including old bookmarks whose provider was never recorded or was mislabelled."""
    try:
        with Path(transcript).open() as handle:
            first = json.loads(handle.readline(65536))
        kind = first.get('type')
        if kind in ('session_meta', 'response_item', 'event_msg'):
            return 'codex'
        if kind in ('file-history-snapshot', 'queue-operation') or (kind in ('user', 'assistant') and 'message' in first):
            return 'claude'
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if '.claude' in Path(transcript).parts:
        return 'claude'
    return fallback


def saved_conversation(root, provider=None):
    """Resume only this provider's own bookmark; a switch never passes another program's session ID."""
    from . import providers
    provider = provider or providers.key(providers.of(root))
    path = _conversations()
    row = (json.loads(path.read_text()) if path.exists() else {}).get(str(Path(root))) or {}
    for saved in ((row.get('by_provider') or {}).get(provider), row):
        if saved and saved.get('transcript') and Path(saved['transcript']).is_file() \
                and transcript_provider(saved['transcript'], saved.get('provider') or provider) == provider:
            return {**saved, 'provider': provider}
    return None


def remember(root, conversation, transcript):
    """Keep each provider's conversation, so switching back resumes its own history."""
    if conversation and transcript:
        from . import board, providers
        path = _conversations()
        with board.board_lock('conversations'):
            rows = json.loads(path.read_text()) if path.exists() else {}
            old = rows.get(str(Path(root))) or {}
            history = dict(old.get('by_provider') or {})
            if old.get('transcript'):
                family = transcript_provider(old['transcript'], old.get('provider'))
                if family:
                    history[family] = {k: old[k] for k in ('conversation', 'transcript')}
                    history[family]['provider'] = family
            family = transcript_provider(transcript, providers.key(providers.of(root)))
            saved = dict(conversation=conversation, transcript=transcript, provider=family)
            history[family] = saved
            rows[str(Path(root))] = dict(saved, by_provider=history)
            board.save_json(path, rows)


def last_conversation(root):
    saved = saved_conversation(root)
    return saved['conversation'] if saved else None


def lingers(cmd):
    """The command, its shell staying half a second after it ends: a program that prints its error and leaves at once
    can be gone before tmux has read what it printed, and then the error is lost with it. How it ended is also left on
    its pane (@exit): tmux can mark a pane dead and never learn its status (1 start in 15, empty after 3.5 s)."""
    return f"{cmd}\ns=$?; tmux set-option -p @exit $s 2>/dev/null; sleep 0.5; exit $s"


UNIT = "colony-"            # a console's scope: colony-<its session>-<when it started>.scope


def contained(cmd, name=None):
    """Run a console in its own systemd scope that carries on when the kernel kills one of its processes for
    memory: by default systemd stops the whole unit, which once took a project's session down with the one
    browser a test had started. Named for its session, so what it leaves running once it ends is known as its own
    (lingering.left). Where systemd isn't there, the command runs as it is."""
    global _SCOPE
    if _SCOPE is None:
        _SCOPE = subprocess.run(["systemd-run", "--user", "--scope", "--quiet", "true"], capture_output=True).returncode == 0 \
            if shutil.which("systemd-run") else False
    if not _SCOPE:
        return cmd
    # Asked again where the console starts: a long-lived tmux server can lose the right to make scopes ("Access
    # denied") while the board still has it, and a console that can't get one starts as it is rather than dying.
    unit = f"--unit={UNIT}{name}-{int(time.time())} " if name else ""
    return (f"if systemd-run --user --scope --quiet true 2>/dev/null; then exec systemd-run --user --scope --quiet "
            f"{unit}-p OOMPolicy=continue -- sh -c {shlex.quote(cmd)}; else exec sh -c {shlex.quote(cmd)}; fi")


_SCOPE = None


def session_name(root):
    root = Path(root)
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", root.name)[:24]
    return f"board-{slug}-{hashlib.sha1(str(root).encode()).hexdigest()[:4]}"


def live(root):
    return running(session_name(root))


def running(name):
    """Whether its program is there. One that has exited stays on its screen (remain-on-exit) until looked at here:
    its last words are kept, then the session closed, so a console that dies as it starts can say why."""
    # list-panes, not display-message: that answers for the client it runs in when the session is gone
    r = subprocess.run(["tmux", "list-panes", "-t", name, "-F", "#{pane_dead} #{pane_dead_status} #{@exit}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False
    dead, status, told = ((r.stdout.splitlines() or [""])[0] + "  ").split(" ")[:3]
    if dead != "1":
        return True
    status = status or told                         # what tmux didn't learn, the program's own wrapper left on its pane
    # tmux can say a pane is dead a moment before it knows how it ended: asked again for that, an error is never
    # taken for a clean exit (the suite's flaky test, its record read: status 0 beside the error's own words)
    for _ in range(10):
        if status.strip():
            break
        time.sleep(0.1)
        again = subprocess.run(["tmux", "list-panes", "-t", name, "-F", "#{pane_dead_status} #{pane_dead_signal}"],
                               capture_output=True, text=True).stdout.split()
        if again and again[0].isdigit():
            status = again[0]
        elif len(again) > 1 and again[1].isdigit():
            status = str(128 + int(again[1]))          # ended by a signal, as a shell would say it
    if not status.strip():
        # still unknown (another look at it closed the session first): its record, written by that look, stands
        began = (json.loads(_started_path().read_text()).get(name) or {}).get("at", 0) if _started_path().exists() else 0
        try:
            if json.loads(_died_path(name).read_text()).get("at", 0) >= began:
                return False
        except (OSError, ValueError):
            pass
    # from its history too: as the pane dies, tmux moves what was on the screen up into it
    shown = subprocess.run(["tmux", "capture-pane", "-p", "-S", "-200", "-t", name], capture_output=True, text=True).stdout
    words = [l.rstrip() for l in shown.splitlines() if l.strip() and not l.startswith("Pane is dead")]  # tmux's own
    path = _died_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": time.time(), "status": int(status or 0), "lines": words[-12:]}))
    subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)
    return False


def _died_path(name):
    from . import board
    return board.home() / "consoles" / f"{name}.died.json"


def died(name):
    """How its program last ended, where it ended in an error since it was last started: {"at", "status", "lines"},
    or None. A record from before that start is an earlier program's, kept rather than deleted: two starts at once
    (a message, a note delivered) would otherwise wipe the one that just died's record, and with it why."""
    try:
        d = json.loads(_died_path(name).read_text())
    except (OSError, ValueError):
        return None
    try:
        began = (json.loads(_started_path().read_text()).get(name) or {}).get("at") or 0
    except (OSError, ValueError):
        began = 0
    return d if d.get("status") and d.get("at", 0) >= began else None


def ensure(root, name=None, label=None, settle=0):
    """Start the session if it is not running: the provider's CLI, in the project, as its settings say. settle: when
    it had to be started, seconds to watch that it stays (a message for it waits that long rather than be lost)."""
    name = name or session_name(root)
    if not running(name):
        from . import board, providers
        if not COMMAND and not providers.installed(providers.of(None if label else root)):
            return name                             # its program isn't here: no console that would only die
        # what an earlier one died of no longer counts from here (died() reads only what came after this start)
        began = time.time()
        # COLONY_CONSOLE marks the session as the board's, so its hooks record its conversation and no other.
        # COLONY_PROJECT says which project it is, where two share a folder; it works in that project's folder.
        subprocess.run(["tmux", "new-session", "-d", "-s", name, "-c", str(board.workdir(root)), "-x", "200", "-y", "50",
                        "-e", f"COLONY_CONSOLE={name}", "-e", f"COLONY_PROJECT={Path(root)}",
                        contained(lingers(command(label or Path(root).name, None if label else root, folder=root if label else None)), name),
                        # in the same breath, before the program can end: if it does, its screen stays to be read
                        ";", "set-option", "-w", "-t", name, "remain-on-exit", "on"],
                       check=True)
        subprocess.run(["tmux", "set-option", "-t", name, "status", "off"], capture_output=True)
        _started(name, fingerprint(root, label), providers.key(providers_of(root, label)), at=began)
        scrollable(name, providers_of(root, label))
        if not label:
            try:
                catch_up(root)
            except Exception:
                pass                                # a note it would have had: never a console that fails to start
        until = time.time() + settle
        while time.time() < until and running(name):
            time.sleep(0.1)
        if settle and not running(name):
            until = time.time() + 2             # ended: what it died of is written a moment after it, later under load
            while time.time() < until and not died(name):
                time.sleep(0.1)
    return name


def providers_of(root, label=None):
    from . import providers
    return providers.of(None if label else root)


def scrollable(name, prov):
    """A CLI drawing inline with no mouse of its own (Codex) leaves its history to tmux: its session takes the mouse,
    so a swipe or wheel up scrolls tmux's copy of it, back down returns to the live screen, and a program that takes
    the mouse itself (Claude Code) still gets it."""
    if prov.scrolls_in_tmux:
        subprocess.run(["tmux", "set-option", "-t", name, "mouse", "on"], capture_output=True)


def unscroll(name):
    """Back to the live screen if the session is scrolled up in tmux's history: typed keys would go to the scrolling
    instead of the program."""
    mode = subprocess.run(["tmux", "display", "-p", "-t", name, "#{pane_in_mode}"], capture_output=True, text=True)
    if (getattr(mode, "stdout", "") or "").strip() == "1":
        subprocess.run(["tmux", "send-keys", "-t", name, "-X", "cancel"], capture_output=True)


# ---------------------------------------------------------------- staying current: reloading a stale console

def fingerprint(root, label=None):
    """What a console was started with: its launch command (less the conversation it resumes) and the files
    its program reads only at start (see the provider's startup_files). A change means a reload would differ."""
    import hashlib
    from . import board, providers
    cmd = command(label or Path(root).name, None if label else root, folder=root if label else None)
    cmd = re.sub(r" (--)?resume \S+", "", cmd)
    p = providers.of(None if label else root)
    files = [f for f in (p.startup_files(board.workdir(root)) if not label else [])
             if f.exists()]
    h = hashlib.sha256(cmd.encode())
    for f in sorted(files):
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _started_path():
    from . import board
    return board.home() / "consoles.json"


def _record(change):
    """The consoles' start records, changed under a lock and written whole: the board and the watcher both write."""
    path = _started_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            have = json.loads(path.read_text())
        except (OSError, ValueError):
            have = {}
        change(have)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(have))
        tmp.replace(path)


def _started(name, fp, program=None, at=None):
    _record(lambda have: have.__setitem__(name, {"fingerprint": fp, "at": at or time.time(), **({"program": program} if program else {})}))


def running_program(name):
    """Which program a console runs (its provider's key): as colony started it, else from its process's path (Claude
    Code keeps its versions in versions/, Codex in releases/), else None."""
    try:
        was = json.loads(_started_path().read_text()).get(name) or {}
    except (OSError, ValueError):
        was = {}
    if was.get("program"):
        return was["program"]
    m = _running_exe(name)
    return ("claude" if m.group(1) else "codex") if m else None


def switching(root, name=None, label=None):
    """A console set to another program than the one it runs (a switch asked for, not yet made): (set, running), as
    provider keys, or None."""
    from . import providers
    name = name or session_name(root)
    if not running(name):
        return None
    want, ran = providers.key(providers_of(root, label)), running_program(name)
    return (want, ran) if ran and ran != want else None


def busy(root, name=None, label=None):
    """What still runs for a console that its screen can show as idle (a command or helper it left at work in the
    background, a turn its program runs elsewhere), or None. PROVIDER: busy(); what can't be read counts as busy."""
    from . import providers
    name = name or session_name(root)
    p = providers.PROVIDERS.get(running_program(name)) or providers_of(root, label)
    try:
        return p.busy(root, name)
    except Exception:
        return "its program couldn't say whether anything still runs"


def catch_up(root):
    """Back on a program after another ran the project, a note of what changed meanwhile: neither program sees the
    other's conversation, so the returning agent would otherwise know only what it happens to look up. Once a switch."""
    from . import board, hours, providers
    want = providers.key(providers.of(root))
    path = _conversations()
    by = ((json.loads(path.read_text()) if path.exists() else {}).get(str(Path(root))) or {}).get("by_provider") or {}

    def last(saved):
        try:
            return Path(saved["transcript"]).stat().st_mtime
        except (OSError, KeyError, TypeError):
            return 0
    since = last(by.get(want))
    others = sorted((last(v), k) for k, v in by.items() if k != want and last(v) > since + 60)
    if not since or not others:
        return None
    other_at, other = others[-1]
    if any(n.get("kind") == "catch-up" and board.epoch(n["at"]) >= int(other_at) for n in board.read(root, "notes.jsonl")):
        return None
    where = board.workdir(root)
    git = lambda *a: subprocess.run(["git", "-C", str(where), *a], capture_output=True, text=True).stdout.strip()
    commits = git("log", f"--since=@{int(since)}", "--format=%h %s").splitlines()
    ahead = git("rev-list", "--count", "@{u}..HEAD")
    loose = [l for l in git("status", "--porcelain").splitlines() if l[3:] not in ("CLAUDE.md", "AGENTS.md")]
    acted = [r for r in board.read(root, "notes.jsonl") if r.get("type") == "addressed" and board.epoch(r["at"]) > since]
    words = [r for r in board.read(root, "said.jsonl") if r.get("text") and board.epoch(r["at"]) > since]
    cut = lambda t, n: t if len(t) <= n else t[:n - 1] + "…"
    parts = [f"Back on {providers.PROVIDERS[want].label}: {providers.PROVIDERS[other].label} ran this project after your "
             f"conversation's last turn ({hours.at_day(since)}), "
             "and you don't see its conversation. What changed meanwhile, to check rather than assume:"]
    if commits:
        parts.append(f"{len(commits)} commit{'s' if len(commits) != 1 else ''}: "
                     + "; ".join(cut(c, 90) for c in commits[:10]) + (" …" if len(commits) > 10 else ""))
    if ahead.isdigit() and int(ahead):
        parts.append(f"{ahead} commit{'s' if ahead != '1' else ''} not pushed")
    if loose:
        parts.append(f"{len(loose)} file{'s' if len(loose) != 1 else ''} changed and not committed: "
                     + ", ".join(l[3:] for l in loose[:8]) + (" …" if len(loose) > 8 else ""))
    if words:
        parts.append("the person said: " + " | ".join(f"\"{cut(r['text'], 160)}\"" for r in words[-5:]))
    if acted:
        parts.append("notes it acted on: " + " | ".join(f"[{r['of']}] {cut(r.get('text', ''), 160)}" for r in acted[-6:]))
    if len(parts) == 1:
        parts.append("nothing (no commits, notes or words from the person)")
    return board.add_note(root, "project", parts[0] + " " + "; ".join(parts[1:]) + ".", author="colony", quiet=True, kind="catch-up")


def running_version(name):
    """The version of the program a console runs, from its process: each program keeps its versions side by side
    (Claude Code's versions/2.1.284, Codex's releases/0.154.0-...), so the path says which one is running."""
    m = _running_exe(name)
    return (m.group(1) or m.group(2)) if m else None


def _running_exe(name):
    """The program's own path among a console's processes, matched on its versions/ or releases/ folder, or None."""
    r = subprocess.run(["tmux", "display", "-p", "-t", name, "#{pane_pid}"], capture_output=True, text=True)
    todo, seen = r.stdout.split(), set()
    while todo:
        pid = todo.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            exe = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            exe = ""
        m = re.search(r"/versions/(\d[\w.]*)$|/releases/(\d[\d.]*)-", exe)
        if m:
            return m
        todo += subprocess.run(["pgrep", "-P", pid], capture_output=True, text=True).stdout.split()
    return None


def stale(root, name=None, label=None):
    """Why a running console is out of date, or None: its program was updated since it started, or what it
    would be started with has changed (a setting, a helper tier)."""
    from . import providers
    name = name or session_name(root)
    if not running(name):
        return None
    p = providers.of(None if label else root)
    now = re.search(r"\d+(\.\d+)+", p.version() or "")
    ran = running_version(name)
    if now and ran and now.group(0) != ran:
        return f"{p.label} {ran} → {now.group(0)}"
    why = not label and p.stale(root)       # what only its program knows (Codex's app access)
    if why:
        return why
    try:
        was = json.loads(_started_path().read_text()).get(name)
    except (OSError, ValueError):
        was = None
    if not was:
        return "started before colony kept a record of how it starts"
    if fingerprint(root, label) != was.get("ran", was["fingerprint"]):    # ran: what its process started with, in older records
        return "its settings or helper tiers changed"
    return None


ROOM = 2 << 30              # bytes free before a console is reloaded: one reading back a long conversation takes GBs


def room():
    """Whether there is memory for a console to start: one resuming a long conversation reads it all back, and on a
    machine already short the kernel kills what is largest (2026-10-28: the board, as ten consoles reloaded at once).
    Linux's MemAvailable; elsewhere, yes."""
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024 >= ROOM
    except (OSError, ValueError, IndexError):
        pass
    return True


def attached(name):
    """Whether anyone has the console open: the board's terminal or a terminal of the person's."""
    r = subprocess.run(["tmux", "display", "-p", "-t", name, "#{session_attached}"], capture_output=True, text=True)
    return r.stdout.strip() not in ("", "0")


def reload(root, name=None, label=None):
    """Restart a console on what it would be started with now, back in the same conversation."""
    name = name or session_name(root)
    pane = subprocess.run(["tmux", "display", "-p", "-t", name, "#{pane_pid}"], capture_output=True, text=True).stdout.strip()
    held = _descendants(int(pane)) if pane.isdigit() else []
    subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)
    # The old program lets go of its conversation only as it exits: started before then, the new one finds it held
    # (Codex: "thread already has an active writer") and the app can't reach it.
    deadline = time.time() + 15
    while time.time() < deadline and any(Path(f"/proc/{pid}").exists() for pid in held):
        time.sleep(0.2)
    return ensure(root, name, label)


def _descendants(pid):
    """A process and everything it started, by their parent links in /proc (Linux; elsewhere, just itself)."""
    children = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(stat.parent.name))
        except (OSError, ValueError, IndexError):
            continue
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        todo += children.get(p, [])
    return out


def provider(name):
    """The provider running a session, by its name."""
    from . import board, providers
    return providers.of(next((p for p in board.projects() if session_name(p) == name), None))


def draft(name):
    """What is half-typed in the session's box, or "" (None: no box on screen)."""
    p = provider(name)
    styled = subprocess.run(["tmux", "capture-pane", "-p", "-e", "-t", name], capture_output=True, text=True).stdout
    return p.draft(styled)


def drafting(name):
    """Whether someone has something half-typed in the session: anything typed now would land on it and send it."""
    return bool(draft(name))


def asking(name):
    """Whether a choice is on the session's screen (a question with options, a permission): it takes the box's place,
    so whatever is typed there lands in it, and its Enter picks an option (the person's message, 2026-10-28, ticked
    one and was lost)."""
    return running(name) and provider(name).classify(screen(name)) == "needs you"


def dismissed(name, wait=4.0):
    """Put away the choice on screen (PROVIDER: dismiss_keys), so the person's message goes as their answer instead.
    True once its box is back."""
    keys = provider(name).dismiss_keys
    if not keys:
        return False
    press(name, keys)
    end = time.time() + wait
    while time.time() < end:
        time.sleep(0.25)
        if not asking(name) and draft(name) is not None:
            return True
    return False


def _sent(name, text, tries=3):
    """Confirm an empty input box; retry Enter only for our own pending input.

    A missing box may be a redraw. A different draft, a choice or a dead console
    cannot acknowledge delivery and must never receive a retry intended for us.
    """
    owned = _pending_input(name, live=False)
    def same_dispatch():
        current = _input_json(_input_paths(name)[0])
        return (owned.get('incarnation') and owned['incarnation'] == _input_identity(name)
                and isinstance(current, dict) and current.get('id') == owned['id'])
    for attempt in range(tries + 1):
        if attempt:
            time.sleep(0.5 * attempt)
        if (owned and not same_dispatch()) or not running(name) or asking(name):
            return False
        left = draft(name)
        if owned and not same_dispatch():
            return False
        if left == "":
            _accept_input(name, text)
            return True
        if left is None:
            continue
        record = _pending_input(name)
        if record and record.get('state') == 'accepted' and _input_normal(record['text']) == _input_normal(text):
            return False                                  # an accepted queued prompt must never receive another Enter
        owned_suffix = bool(record and record.get('state') == 'unsent' and
                            _input_normal(record['text']) == _input_normal(text) and _input_matches(record, left))
        if not provider(name).pending_input(text, left) and not owned_suffix:
            return False
        if owned and not same_dispatch():
            return False
        subprocess.run(["tmux", "send-keys", "-t", name, "Enter"], check=True)
    # Give the final Enter one bounded redraw interval before reading its result.
    time.sleep(0.1)
    accepted = (not owned or same_dispatch()) and running(name) and not asking(name) and draft(name) == ""
    accepted = accepted and (not owned or same_dispatch())
    if accepted:
        _accept_input(name, text)
    return accepted


STARTING = 60                   # seconds after a console starts in which a screen without its box is still coming up


def _start_up(name):
    """Whether its screen shows a start-up question (its folder's trust, its permission mode, Remote Control, hooks):
    the watcher answers those as the console was set up, or the person does. Never put away for a message: Esc on a
    folder's trust question exits the program. PROVIDER: providers.starting reads them."""
    from . import providers
    return bool(providers.starting(provider(name), screen(name), fresh=True))


def coming_up(name):
    """Whether a console without its box is still coming up: started a moment ago, with nothing on screen to answer
    yet (its program still drawing) or a start-up question the watcher answers by itself (Settings → trust)."""
    from . import board
    if age(name) >= STARTING:
        return False
    if provider(name).choice(screen(name)) is None:
        return True
    return board.registry()["settings"]["trust"] and _start_up(name)


def ready(name, wait=0):
    """What is in the session's box once it is on screen ("" when it is empty); None while it isn't, looked for up to
    `wait` seconds while the console is still coming up. Typed into a console still starting, whose blank screen
    reads as idle, colony's line was counted as sent, though it sat in the box unsent once the program came up: the
    steward's first-time setup waited on it for minutes."""
    until = time.time() + wait
    while True:
        left = draft(name)
        if left is not None or time.time() >= until or not running(name) or not coming_up(name):
            return left
        time.sleep(0.25)


@contextmanager
def _typing(name, wait=10):
    """One message typed into a session at a time, across the board's threads and colony's own commands: two typed
    at once (the watcher's line and the person's message) would land in one box together. Yields False if another
    is still typing there after `wait` seconds."""
    from . import board
    path = board.home() / "consoles" / f"{name}.typing"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        until = time.time() + wait
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= until:
                    yield False
                    return
                time.sleep(0.05)
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


INPUT_TTL = 180
INPUT_BYTES = 64 * 1024


def _input_paths(name):
    from . import board
    base = board.home() / 'consoles' / ('input-' + hashlib.sha256(name.encode()).hexdigest()[:20])
    return base.with_suffix('.json'), base.with_suffix('.receipt'), base.with_suffix('.events')


def _input_json(path):
    try:
        return json.loads(path.read_text()) if path.stat().st_size <= INPUT_BYTES * 8 else {}
    except (OSError, ValueError):
        return {}


def _private_input(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + secrets.token_hex(6))
    try:
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as handle:
            json.dump(value, handle)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _input_normal(text):
    import unicodedata
    return ''.join(unicodedata.normalize('NFC', text).split())


def _input_identity(name):
    """A portable tmux incarnation, including its server start time, never just a recyclable PID."""
    result = subprocess.run(['tmux', 'display', '-p', '-t', name,
                             '#{pid}|#{start_time}|#{session_id}|#{pane_id}|#{session_created}'], capture_output=True, text=True)
    parts = (result.stdout or '').strip().split('|')
    if result.returncode or len(parts) != 5 or not all(parts) or not all(parts[i].isdigit() for i in (0, 1, 4)):
        return None
    began = (_input_json(_started_path()).get(name) or {}).get('at')
    return hashlib.sha256(json.dumps([parts, began]).encode()).hexdigest()


def _input_root(name):
    from . import board, monitor
    return next((str(p) for p in board.projects() if session_name(p) == name),
                str(monitor.home()) if monitor.name() == name else None)


def _input_event(name, record, event):
    path = _input_paths(name)[2]
    rows = _input_json(path)
    rows = rows if isinstance(rows, list) else []
    rows.append(dict(at=time.time(), event=event, id=record.get('id'), hash=record.get('hash'),
                     purpose=record.get('purpose'), carry=record.get('carry')))
    _private_input(path, rows[-32:])          # metadata only: never copy prompts into a log


def _pending_input(name, live=True):
    path, receipt, _ = _input_paths(name)
    record = _input_json(path)
    if not isinstance(record, dict) or not isinstance(record.get('text'), str):
        return None
    proof = _input_json(receipt)
    if isinstance(proof, dict) and proof.get('id') == record.get('id') and record.get('state') == 'accepted':
        path.unlink(missing_ok=True); receipt.unlink(missing_ok=True)
        _input_event(name, record, 'confirmed')
        return None
    if live and (not record.get('incarnation') or record['incarnation'] != _input_identity(name)):
        return None
    return record


def _record_input(name, text, purpose='system'):
    """Called under _typing, before the first byte is sent. Only one pending payload is retained."""
    if len(text.encode()) > INPUT_BYTES:
        raise ValueError('Input exceeds the bounded ownership record')
    incarnation = _input_identity(name)
    if not incarnation:
        raise ValueError('The console incarnation could not be established')
    normal = _input_normal(text)
    carry = re.search(r'\[colony\] Context carry-over \(([a-f0-9]{16})\)', text)
    record = dict(id=secrets.token_hex(12), name=name, incarnation=incarnation, text=text,
                  hash=hashlib.sha256(normal.encode()).hexdigest(), at=time.time(), purpose=purpose,
                  carry=carry[1] if carry and purpose == 'system' else None, root=_input_root(name), state='unsent')
    _private_input(_input_paths(name)[0], record)
    _input_event(name, record, 'typed')
    return record


def _input_matches(record, left):
    if not left:
        return False
    wanted, visible = _input_normal(record['text']), _input_normal(left)
    return bool(visible and (visible == wanted or
                len(visible) >= max(64, (len(wanted) * 7 + 9) // 10) and wanted.endswith(visible)))


def _accept_input(name, text):
    record = _pending_input(name, live=False)
    if record and record.get('state') == 'unsent' and _input_normal(record['text']) == _input_normal(text):
        record['state'] = 'accepted'
        try:
            _private_input(_input_paths(name)[0], record)
        except OSError:
            # An acknowledged native write must never become a retry because bookkeeping failed. Without a
            # persistent acknowledgement, discard its ownership proof so it cannot authorize a later resubmit.
            try:
                _input_paths(name)[0].unlink(missing_ok=True)
            except OSError:
                pass
        try:
            _input_event(name, record, 'accepted')
        except OSError:
            pass


def confirm_input(name, prompt):
    """A scoped UserPromptSubmit confirms accepted input without waiting for the typing lock.

    Receipts name a record, not just its text: an old identical prompt cannot confirm a newer unsent write.
    A hook racing the initial CLI acknowledgement leaves that record conservatively accepted.
    """
    record = _input_json(_input_paths(name)[0])
    if not isinstance(record, dict) or record.get('state') != 'accepted' or not record.get('incarnation') or \
            record['incarnation'] != _input_identity(name) or _input_normal(prompt) != _input_normal(record.get('text', '')):
        return False
    _private_input(_input_paths(name)[1], {'id': record['id']})
    with _typing(name, wait=0) as mine:
        if mine:
            _pending_input(name)
    return True


def _carry_input(record, defer=False):
    if not record.get('carry') or not record.get('root'):
        return False
    from . import context
    root = Path(record['root'])
    job = context.read(context.file(root)).get('job') or {}
    if job.get('id') != record['carry']:
        return False
    if defer:
        context.defer(root, job, 'Unsent carry-over input expired; refresh postponed.')
    return job.get('phase') in ('deferred', 'unknown')


def _retract_input(name, record):
    """Clear only a proven unsent system write, verifying each deletion and the final empty box."""
    keys = provider(name).queue_keys
    left = draft(name)
    if record.get('purpose') != 'system' or record.get('state') != 'unsent' or not keys or not _input_matches(record, left):
        return False
    wanted, bound = _input_normal(record['text']), len(_input_normal(record['text']))
    for _ in range(128):
        if asking(name) or _input_identity(name) != record['incarnation'] or draft(name) != left:
            return False
        subprocess.run(['tmux', 'send-keys', '-t', name, 'C-e', keys['clear']], check=True)
        time.sleep(.03)
        after = draft(name)
        if after == '':
            if _input_identity(name) != record['incarnation']:
                return False
            _input_paths(name)[0].unlink(missing_ok=True)
            _input_event(name, record, 'retracted')
            _carry_input(record, defer=True)
            return True
        if not after or after == left:
            return False
        # Ownership was established before this deletion. Each resulting native suffix must end earlier in that
        # same known payload; an unrelated edit stops this transaction rather than licensing another clear.
        visible = _input_normal(after)
        start = wanted.rfind(visible, 0, bound - 1)
        if start < 0:
            return False
        bound, left = start + len(visible), after
    return False


def expire_inputs(now=None):
    """Cheap watcher upkeep: only expired unsent system writes; never busy, attached or unmatched input."""
    from . import board
    now = time.time() if now is None else now
    for path in (board.home() / 'consoles').glob('input-*.json'):
        record = _input_json(path)
        if not isinstance(record, dict):
            continue
        if record.get('state') == 'accepted' and record.get('name') and _input_paths(record['name'])[1].exists():
            with _typing(record['name'], wait=0) as mine:
                if mine:
                    _pending_input(record['name'], live=False)
            continue
        if record.get('purpose') != 'system' or record.get('state') != 'unsent':
            continue
        if now - record.get('at', now) < INPUT_TTL:
            continue
        name, root = record.get('name'), record.get('root')
        if not name or not running(name) or attached(name) or provider(name).classify(screen(name)) != 'idle':
            continue
        if root and busy(Path(root), name):
            continue
        with _typing(name, wait=0) as mine:
            current = _pending_input(name) if mine else None
            if current and current['id'] == record.get('id'):
                try:
                    _retract_input(name, current)
                except (OSError, subprocess.CalledProcessError):
                    continue


def _make_way(name):
    """The session's box cleared for a message: "" once it is on screen and empty; colony's own line if one was left
    there unsent (an Enter swallowed) and has now been sent; None if nothing may be typed: no box on screen, the
    person's own draft in it (theirs to send), or colony's leftover that won't go."""
    left = draft(name)
    if not left:
        return left
    record = _pending_input(name)
    if not record or record.get('purpose') != 'system' or record.get('state') != 'unsent' or not _input_matches(record, left):
        return None
    if time.time() - record['at'] >= INPUT_TTL or _carry_input(record):
        return '' if _retract_input(name, record) else None
    unscroll(name)
    return record['text'] if _sent(name, record['text']) else None


def type_into(name, text, wait=0, *, _purpose='system'):
    """Type a message into a session and send it, as if the person had; unless someone has a draft there, which
    is theirs to send, or its box isn't on screen (looked for up to `wait` seconds while it comes up): then nothing
    is typed, and False says so. Colony's own unsent line left there earlier is sent first, rather than mistaken for
    the person's draft, and then the message: sending only the leftover lost the person's first message to the
    steward. True only once the message has left the box. Colony's own words never go to a project the person paused
    (R134); theirs do."""
    if _purpose == 'system' and held(name):
        return False
    if not running(name) or ready(name, wait) is None or asking(name):   # a choice on screen is the person's: colony's word waits
        return False
    with _typing(name) as mine:
        before = _make_way(name) if mine else None
        if before is None:
            return False
        if before and _purpose == 'system' and " ".join(before.split()) == " ".join(text.split()):
            return True                                  # it was this very line, left unsent earlier: now it is sent
        unscroll(name)
        # PROVIDER: this is how the board and the steward reach an agent, and it relies on the CLI taking typed
        # text plus Enter as a message, and on Claude Code queuing it when it arrives mid-turn (urgent mail does
        # that). A provider that drops or garbles input while busy needs the watcher to wait for "idle" instead.
        try:
            _record_input(name, text, _purpose)
            subprocess.run(["tmux", "send-keys", "-t", name, "-l", text], check=True)
            time.sleep(provider(name).enter_after)   # typing that arrives at once can read as a paste
            return _sent(name, text)
        except (subprocess.CalledProcessError, OSError, ValueError):
            return False


def held(name):
    """Whether this session is the console of a project the person paused: colony types nothing of its own there."""
    from . import board
    return any(session_name(root) == name for root in board.paused_projects())


def paste_into(name, text, answer=False, wait=0):
    """Send the person's own message, as if they had pasted it into the session and pressed Enter: lines and
    all, in one piece (a bracketed paste where the program asks for one, as Claude Code does). A console still
    coming up is waited for, up to `wait` seconds. With a choice on screen, nothing goes into it: as `answer` (the
    person writing in Chat while it asks) the choice is put away and the message goes as their answer, a start-up
    question excepted; otherwise, or where it can't be put away, False. As type_into, True only once the message
    has left the box."""
    if not running(name):
        return False
    ready(name, wait)
    if asking(name) and not (answer and not _start_up(name) and dismissed(name)):
        return False
    if "\n" not in text:
        return type_into(name, text, _purpose='person')
    if ready(name) is None:
        return False
    with _typing(name) as mine:
        if (_make_way(name) if mine else None) is None:
            return False
        unscroll(name)
        buf = f"colony-{secrets.token_hex(4)}"
        try:
            _record_input(name, text, 'person')
            subprocess.run(["tmux", "load-buffer", "-b", buf, "-"], input=text, text=True, check=True)
            subprocess.run(["tmux", "paste-buffer", "-p", "-d", "-b", buf, "-t", name], check=True)
            time.sleep(max(0.3, provider(name).enter_after))    # the paste lands before its Enter
            return _sent(name, text)
        except (subprocess.CalledProcessError, OSError, ValueError):
            return False


def queue(name, action, msgs=()):
    """PROVIDER: queue_keys. Messages sent mid-turn wait in the program's own queue: "now" sends them at once, "back"
    takes them back into its box (none is sent meanwhile; refused over the person's own draft), "put" clears the box
    and queues msgs again in this order. False where it can't, the box left as it was."""
    keys = provider(name).queue_keys
    if not keys or not running(name):
        return False
    if action == "now":
        press(name, keys["now"])
        return True
    if action == "back":
        if drafting(name):
            return False
        press(name, keys["back"])
        return True
    if action == "put":
        for _ in range(120):                       # a line at a time, till the box is empty; never Esc (it interrupts)
            if not drafting(name):
                break
            subprocess.run(["tmux", "send-keys", "-t", name, "C-e", keys["clear"]], check=True)
            time.sleep(0.03)
        if drafting(name):
            return False
        return all(paste_into(name, m) for m in msgs if m.strip())
    return False


def press(name, keys):
    """Press keys in a session (tmux key names: Up, Down, Enter, Escape...)."""
    unscroll(name)
    for k in keys:
        subprocess.run(["tmux", "send-keys", "-t", name, k], check=True)


def screen(name):
    return subprocess.run(["tmux", "capture-pane", "-p", "-t", name], capture_output=True, text=True).stdout


def history(name):
    """The session's screen with the scrollback above it, wrapped lines joined: what a narrow window (a phone
    attached) has broken across rows reads as one line again."""
    return subprocess.run(["tmux", "capture-pane", "-p", "-J", "-S", "-", "-t", name], capture_output=True, text=True).stdout


def age(name):
    """Seconds since a session started (a large number if it isn't running)."""
    r = subprocess.run(["tmux", "display", "-p", "-t", name, "#{session_created}"], capture_output=True, text=True)
    try:
        return time.time() - int(r.stdout.strip())
    except ValueError:
        return float("inf")


def stop(root):
    subprocess.run(["tmux", "kill-session", "-t", session_name(root)], capture_output=True)


# ---------------------------------------------------------------- a glance at a session without opening it

BORDER = re.compile(r"^[\s│|╭╮╰╯─━┃┏┓┗┛]+|[\s│|╭╮╰╯─━┃┏┓┗┛]+$")


def snapshot(root, lines=6, name=None):
    """Its state, read off the screen as it is now, and its last lines: from the scrollback too when more are
    asked for than the screen holds."""
    name = name or session_name(root)
    if not running(name):
        from . import providers
        p = providers.of(root)
        if not providers.installed(p):
            return {"state": "off", "lines": [providers.missing(p)]}
        end = died(name)
        return {"state": "off", "lines": [f"Its agent stopped with an error (exit {end['status']}). Its last words:"]
                + end["lines"][-max(lines - 1, 3):] if end else []}
    now = screen(name)
    shown = [l for l in (BORDER.sub("", l) for l in now.splitlines()) if l.strip()]
    from . import providers
    p = providers.of(root)
    if lines > len(shown):
        # A CLI drawing in the alternate screen (Claude Code does) leaves tmux no scrollback: then its own
        # record of the conversation is what lies above the screen.
        more = [l for l in (BORDER.sub("", l) for l in history(name).splitlines()) if l.strip()]
        if len(more) <= len(shown):
            more = [l for l in (p.history_text(root) or "").splitlines() if l.strip()] + shown
        shown = more
    marker = p.scrolled_marker
    out = {"state": p.classify(now), "lines": shown[-lines:], "scrolled": bool(marker and marker in now),
           "activity": p.activity(now)}
    helping = p.helpers(root)                          # a program that lists no helpers on screen says them otherwise
    if helping is not None:
        out["helpers"] = helping
    return out


# ---------------------------------------------------------------- a minimal WebSocket (RFC 6455)

def accept(key):
    return base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()


def send_frame(sock, data, opcode=2):
    head = bytes([0x80 | opcode])
    n = len(data)
    head += bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n)
    sock.sendall(head + data)


def read_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def recv_frame(sock):
    b1, b2 = read_exact(sock, 2)
    opcode, masked, n = b1 & 0x0F, b2 & 0x80, b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", read_exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", read_exact(sock, 8))[0]
    mask = read_exact(sock, 4) if masked else b"\0\0\0\0"
    data = bytes(c ^ mask[i % 4] for i, c in enumerate(read_exact(sock, n)))
    return opcode, data


def bridge(sock, root, name=None, label=None):
    """Attach a terminal to the session and pass bytes both ways until either side leaves. Leaving
    detaches; the session keeps running."""
    name = ensure(root, name, label)
    inline, up = providers_of(root, label).scrolls_in_tmux, False
    scrollable(name, providers_of(root, label))                 # a session started before it took the mouse too
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.execvp("tmux", ["tmux", "attach-session", "-t", name])
    try:
        while True:
            ready, _, _ = select.select([sock, fd], [], [])
            if fd in ready:
                try:
                    out = os.read(fd, 65536)
                except OSError:
                    break
                if not out:
                    break
                send_frame(sock, out)
            if sock in ready:
                opcode, data = recv_frame(sock)
                if opcode == 8:
                    break
                if opcode == 9:
                    send_frame(sock, data, opcode=10)
                    continue
                msg = json.loads(data.decode("utf-8", "replace"))
                if "r" in msg:
                    cols, rows = msg["r"]
                    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", int(rows), int(cols), 0, 0))
                elif "live" in msg:                         # the ↓: back to the latest, however far up
                    unscroll(name)
                    up = False
                elif "i" in msg:
                    if inline and msg["i"].startswith("\x1b[<"):
                        up = True                           # a swipe: tmux scrolls its history
                    elif inline and up:
                        unscroll(name)                          # a key: to the program, on the live screen
                        up = False
                    os.write(fd, msg["i"].encode())
    except (ConnectionError, OSError, ValueError):
        pass
    finally:
        try:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


PAGE = """
<div class='console-bar'><span class='muted'>{label} in <code>{path}</code> · session <code>{name}</code>
 (also reachable with <code>tmux attach -t {name}</code>)</span>
 <button class='quiet touch-only' id='fullscreen'>Full screen</button>
 <form method='post' action='/console/stop' onsubmit="return confirm('End this project\\'s session?')">
 <input type='hidden' name='p' value='{pid}'><input type='hidden' name='back' value='/?p={pid}&view=console'>
 <button class='quiet'>End session</button></form></div>
<div class='keys' aria-label='Keys a phone keyboard lacks'>
 <button data-k='esc'>Esc</button><button data-k='tab'>Tab</button>
 <button data-k='up'>↑</button><button data-k='down'>↓</button><button data-mod='ctrl'>Ctrl</button><button data-mod='alt'>Alt</button>
 <button class='copy' id='history'>Select</button><button class='selectall' id='selectall'>Select all</button>
 <button class='send' data-k='enter'>Send</button></div>
<div id='term'><p id='console-state' role='status'>Opening Console…</p></div>
<pre id='hist' hidden></pre>
<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.css'>
<script src='https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.js'></script>
<script src='https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.js'></script>
<script>
(() => {{
const status = document.getElementById('console-state');
if (typeof Terminal === 'undefined' || typeof FitAddon === 'undefined') {{
  status.textContent = 'Console could not load. Check your connection, then ';
  const retry = document.createElement('a'); retry.href = location.href; retry.textContent = 'reload Console';
  status.append(retry); return;
}}
status.hidden = true;
const term = new Terminal({{fontSize: 13, cursorBlink: true, theme: {{background: '#16171a'}}}});
const fit = new FitAddon.FitAddon();
term.loadAddon(fit);
term.open(document.getElementById('term'));
fit.fit();
// A phone drops the connection whenever the browser goes to the background, and the board may restart:
// reconnect by itself, and reload the page if that keeps failing. The session runs on regardless.
let ws, tries = 0, retry = null, left = false;
const send = (m) => ws && ws.readyState === 1 && ws.send(JSON.stringify(m));
// Full screen on a phone: the terminal takes what the chips above and the keys and tabs below leave.
const refit = () => {{
  if (document.body.classList.contains('focus')) {{
    const keys = document.querySelector('.keys'), top = termEl.getBoundingClientRect().top;
    let foot = window.visualViewport ? visualViewport.offsetTop + visualViewport.height : innerHeight;
    // Auto's message box and player stay usable, with the terminal's keys just above them.
    document.querySelectorAll('.tabs, #gc, #cplayer').forEach((el) => {{
      const r = el.getBoundingClientRect();
      if (!el.hidden && r.height && getComputedStyle(el).position === 'fixed') foot = Math.min(foot, r.top);
    }});
    keys.style.bottom = Math.max(0, innerHeight - foot) + 'px';
    const h = foot - top - keys.offsetHeight;
    termEl.style.height = Math.max(120, h) + 'px';
  }}
  fit.fit(); send({{r: [term.cols, term.rows]}});
}};
function connect() {{
  clearTimeout(retry); retry = null;
  if (left || (ws && ws.readyState < 2)) return;
  const socket = ws = new WebSocket(`${{location.protocol === 'https:' ? 'wss' : 'ws'}}://${{location.host}}/console/ws?p={pid}&t={token}`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {{ tries = 0; term.reset(); send({{r: [term.cols, term.rows]}}); term.focus(); }};
  ws.onmessage = (ev) => term.write(new Uint8Array(ev.data), () => {{ if (typeof showJump === 'function') showJump(); }});
  ws.onclose = () => {{
    if (left || ws !== socket) return;
    if (++tries > 4) return location.reload();
    term.write('\\r\\n[reconnecting…]\\r\\n');
    retry = setTimeout(connect, 800 * tries);
  }};
}}
connect();
document.addEventListener('visibilitychange', () => {{
  if (!document.hidden && (!ws || ws.readyState > 1)) {{ tries = 0; connect(); }}
}});
// Ctrl and Alt are sticky: tap one, then a key on the phone's keyboard; tap it again to let go.
let mod = null;
// The key row acts on touch without taking focus, so the phone's keyboard stays as it was.
const tap = (b, fn) => {{
  b.addEventListener('touchstart', (ev) => {{ ev.preventDefault(); fn(); }}, {{passive: false}});
  b.addEventListener('click', (ev) => {{ ev.preventDefault(); fn(); }});
}};
const modBtns = document.querySelectorAll('.keys button[data-mod]');
const arm = (m) => {{ mod = m; modBtns.forEach((b) => b.classList.toggle('on', b.dataset.mod === m)); }};
modBtns.forEach((b) => tap(b, () => {{
  const on = mod !== b.dataset.mod;
  arm(on ? b.dataset.mod : null);
  if (on) term.focus();                             // the next key comes from the keyboard: open it if closed
}}));
const withMod = (d) => {{
  if (!mod || d.length !== 1) return d;
  const out = mod === 'alt' ? '\\x1b' + d : String.fromCharCode(d.toUpperCase().charCodeAt(0) & 0x1f);
  arm(null);
  return out;
}};
term.onData((d) => send({{i: withMod(d)}}));
// A phone keyboard has no arrows or Esc, which every on-screen choice needs: these send the same bytes.
const KEYS = {{esc: '\\x1b', tab: '\\t', up: '\\x1b[A', down: '\\x1b[B', enter: '\\r'}};
document.querySelectorAll('.keys button[data-k]').forEach((b) => tap(b, () => send({{i: KEYS[b.dataset.k]}})));
// On a phone the console takes the whole screen: the terminal, the keys, a way out.
const touch = matchMedia('(pointer: coarse)').matches;
const focus = (on) => {{ document.body.classList.toggle('focus', on); setTimeout(refit, 50); }};
if (touch && {focus}) focus(true);
document.getElementById('fullscreen').addEventListener('click', () => focus(true));
// A finger swipe scrolls: each stretch of movement is sent as a mouse-wheel event, as a desktop wheel would
// be, and tmux hands it to the program in the session (Claude Code scrolls its own history). A flick glides
// on; a tap still opens the keyboard. The touches land on a still layer over the terminal: a touch belongs
// to the element it started on, and the terminal replaces its rows as it redraws, which ends the drag.
const termEl = document.getElementById('term'), STEP = 14;
// Auto may open, fold or resize after the terminal has fitted; its fixed controls then leave a different space.
const overlays = [...document.querySelectorAll('#gc, #cplayer')];
const layout = new ResizeObserver(refit);
const placed = new MutationObserver(refit);
overlays.forEach((el) => {{ layout.observe(el); placed.observe(el, {{attributes: true, attributeFilter: ['style', 'class', 'hidden']}}); }});
const pad = document.createElement('div');
pad.className = 'touchpad';
termEl.appendChild(pad);
// Scrolled up, a ↓ at the bottom brings the latest back: the count of steps up says how far up we are.
const jump = document.createElement('button');
jump.className = 'jump'; jump.textContent = '↓'; jump.hidden = true; jump.setAttribute('aria-label', 'Jump to the latest');
termEl.appendChild(jump);
// The ↓ follows the program's own sign that its view is scrolled up (Claude Code prints "Jump to bottom"),
// so typing or scrolling any other way keeps it true; without such a sign it counts the swipes.
const MARKER = '{scrolled}';
let above = 0;
const markerShown = () => {{
  const b = term.buffer.active;
  for (let i = b.length - 1; i >= Math.max(0, b.length - term.rows); i--) {{
    const line = b.getLine(i); if (line && line.translateToString(true).includes(MARKER)) return true;
  }}
  return false;
}};
// It floats just above the typing box, which Claude Code draws between two rules of ─, never over it.
const lift = () => {{
  const b = term.buffer.active, top = b.viewportY, rules = [];
  for (let r = term.rows - 1; r >= 0 && rules.length < 2; r--) {{
    const t = (b.getLine(top + r) || {{translateToString: () => ''}}).translateToString(true).trim();
    if (t.length > 8 && /^[─━-]+$/.test(t.slice(0, 8))) rules.push(r);
  }}
  const screen = termEl.querySelector('.xterm-screen');
  const rowH = screen ? screen.clientHeight / term.rows : 17;
  const boxTop = rules.length === 2 ? rules[1] : term.rows;
  jump.style.bottom = Math.max(14, (term.rows - boxTop) * rowH + 10) + 'px';
}};
const showJump = () => {{ jump.hidden = MARKER ? !markerShown() : above === 0; if (!jump.hidden) lift(); }};
const scrolled = (n) => {{ above = Math.max(0, above + n); if (!MARKER) showJump(); }};
jump.addEventListener('click', (ev) => {{
  ev.preventDefault(); cancelAnimationFrame(glide); acc = 0;
  send({{live: 1}});                                  // scrolled in tmux's history (Codex), straight back to live
  let tries = 0;
  const down = () => {{ send({{i: wheel(false).repeat(40)}}); above = 0;
    setTimeout(() => {{ showJump(); if (!jump.hidden && ++tries < 8) down(); }}, 250); }};
  down();
}});
const wheel = (up) => `\\x1b[<${{up ? 64 : 65}};${{Math.ceil(term.cols / 2)}};${{Math.ceil(term.rows / 2)}}M`;
let y0 = null, lastY = 0, lastT = 0, acc = 0, vel = 0, moved = false, glide = null, frame = null;
// Steps go out once per screen refresh, together, so the terminal redraws in time with the finger.
const drain = () => {{ if (!frame) frame = requestAnimationFrame(() => {{
  frame = null; let out = '';
  while (Math.abs(acc) >= STEP) {{ out += wheel(acc > 0); scrolled(acc > 0 ? 1 : -1); acc -= Math.sign(acc) * STEP; }}
  if (out) send({{i: out}});
}}); }};
pad.addEventListener('touchstart', (ev) => {{
  if (ev.touches.length !== 1) return;
  cancelAnimationFrame(glide); y0 = lastY = ev.touches[0].clientY; lastT = performance.now(); acc = 0; vel = 0; moved = false;
}}, {{passive: true}});
pad.addEventListener('touchmove', (ev) => {{
  if (y0 === null) return;
  const y = ev.touches[0].clientY, t = performance.now();
  vel = (y - lastY) / Math.max(1, t - lastT); acc += y - lastY; lastY = y; lastT = t;
  ev.preventDefault(); ev.stopPropagation();        // from the first movement, or the phone takes the drag over
  if (!moved && Math.abs(y - y0) > 8) moved = true;
  if (moved) drain();
}}, {{passive: false, capture: true}});
const release = () => {{
  if (!moved && y0 !== null) term.focus();          // a tap: the keyboard, as on the terminal itself
  if (moved && Math.abs(vel) > 0.25) {{
    let v = vel * 16;
    const step = () => {{ acc += v; drain(); v *= 0.94; if (Math.abs(v) >= 0.8) glide = requestAnimationFrame(step); }};
    glide = requestAnimationFrame(step);
  }}
  y0 = null;
}};
pad.addEventListener('touchend', release, {{passive: true}});
pad.addEventListener('touchcancel', release, {{passive: true}});
// Selecting text in the terminal is poor on a phone: what is on screen, as plain text, selects natively.
const hist = document.getElementById('hist'), histBtn = document.getElementById('history');
termEl.appendChild(hist);                           // over the terminal, between the chips and the keys
async function showText(on) {{
  histBtn.textContent = on ? 'Live' : 'Select';
  if (!on) {{ hist.hidden = true; document.body.classList.remove('copying'); return term.focus(); }}
  const r = await fetch('/console/text?p={pid}', {{cache: 'no-store'}});
  hist.textContent = r.ok ? await r.text() : '(could not read the session)';
  hist.hidden = false; document.body.classList.add('copying'); hist.scrollTop = hist.scrollHeight;
}}
histBtn.addEventListener('click', () => showText(hist.hidden));
// Select all, and copy it where the browser allows (a plain-http page may not): else it stays selected.
const allBtn = document.getElementById('selectall');
allBtn.addEventListener('click', () => {{
  const range = document.createRange(); range.selectNodeContents(hist);
  const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
  let copied = false; try {{ copied = document.execCommand('copy'); }} catch (e) {{}}
  allBtn.textContent = copied ? 'Copied' : 'Selected: tap Copy';
  setTimeout(() => {{ allBtn.textContent = 'Select all'; }}, 2500);
}});
window.addEventListener('resize', refit);
if (window.visualViewport) visualViewport.addEventListener('resize', refit);
const leave = (ev) => {{
  if (ev.persisted) return;
  left = true; clearTimeout(retry);
  layout.disconnect(); placed.disconnect();
  cancelAnimationFrame(glide); cancelAnimationFrame(frame);
  if (ws) ws.close(); term.dispose();
}};
document.addEventListener('colony-leave', leave);
window.addEventListener('pagehide', leave);
}})();
</script>
"""
