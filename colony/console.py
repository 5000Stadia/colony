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
    monitor's) resumes the conversation last active there."""
    if COMMAND:
        return COMMAND.format(name=shlex.quote(label))
    from . import board, providers
    s = board.project_settings(root)[0] if root else board.registry()["settings"]
    # PROVIDER: resuming needs the provider to say which conversation its hooks ran in (conversation()) and to
    # take resume= in command(); one that doesn't simply starts fresh after a restart.
    resume = last_conversation(root) if root else None
    if label == "monitor" and not root:
        from . import monitor
        model, effort, _ = monitor.choice()
        s = dict(s, **({"model": model} if model else {}), **({"effort": effort} if effort else {}),
                 autocompact=monitor.CONTEXT_CAP)
        if monitor.fresh_flag().exists():
            folder = None                               # a fresh start: no conversation to resume
    if not root and folder and hasattr(providers.of(None), "latest_conversation"):
        resume = providers.of(None).latest_conversation(folder)
    return providers.of(root).command(label, s, root=root, **({"resume": resume} if resume else {}))


def _conversations():
    from . import board
    return board.home() / "conversations.json"     # this machine's, so kept with the board, not in the project


def remember(root, conversation, transcript):
    """The conversation a console is in, recorded by its hooks, so a console that died comes back to it."""
    if conversation and transcript:
        path = _conversations()
        rows = json.loads(path.read_text()) if path.exists() else {}
        rows[str(Path(root))] = {"conversation": conversation, "transcript": transcript}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=1))


def last_conversation(root):
    path = _conversations()
    c = (json.loads(path.read_text()) if path.exists() else {}).get(str(Path(root)))
    return c["conversation"] if c and Path(c["transcript"]).exists() else None


def contained(cmd):
    """Run a console in its own systemd scope that carries on when the kernel kills one of its processes for
    memory: by default systemd stops the whole unit, which once took a project's session down with the one
    browser a test had started. Where systemd isn't there, the command runs as it is."""
    global _SCOPE
    if _SCOPE is None:
        _SCOPE = subprocess.run(["systemd-run", "--user", "--scope", "--quiet", "true"], capture_output=True).returncode == 0 \
            if shutil.which("systemd-run") else False
    return f"systemd-run --user --scope --quiet -p OOMPolicy=continue -- sh -c {shlex.quote(cmd)}" if _SCOPE else cmd


_SCOPE = None


def session_name(root):
    root = Path(root)
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", root.name)[:24]
    return f"board-{slug}-{hashlib.sha1(str(root).encode()).hexdigest()[:4]}"


def live(root):
    return running(session_name(root))


def running(name):
    return subprocess.run(["tmux", "has-session", "-t", name], capture_output=True).returncode == 0


def ensure(root, name=None, label=None):
    """Start the session if it is not running: the provider's CLI, in the project, as its settings say."""
    name = name or session_name(root)
    if not running(name):
        from . import board, providers
        if not COMMAND and not providers.installed(providers.of(None if label else root)):
            return name                             # its program isn't here: no console that would only die
        # COLONY_CONSOLE marks the session as the board's, so its hooks record its conversation and no other.
        # COLONY_PROJECT says which project it is, where two share a folder; it works in that project's folder.
        subprocess.run(["tmux", "new-session", "-d", "-s", name, "-c", str(board.workdir(root)), "-x", "200", "-y", "50",
                        "-e", f"COLONY_CONSOLE={name}", "-e", f"COLONY_PROJECT={Path(root)}",
                        contained(command(label or Path(root).name, None if label else root, folder=root if label else None))],
                       check=True)
        subprocess.run(["tmux", "set-option", "-t", name, "status", "off"], capture_output=True)
        _started(name, fingerprint(root, label))
    return name


# ---------------------------------------------------------------- staying current: reloading a stale console

def fingerprint(root, label=None):
    """What a console was started with: its launch command (less the conversation it resumes) and the files
    its program reads only at start (see the provider's startup_files). A change means a reload would differ."""
    import hashlib
    from . import board, providers
    cmd = command(label or Path(root).name, None if label else root, folder=root if label else None)
    cmd = re.sub(r" (--)?resume \S+", "", cmd)
    p = providers.of(None if label else root)
    files = [f for f in (p.startup_files(board.workdir(root)) if hasattr(p, "startup_files") and not label else [])
             if f.exists()]
    h = hashlib.sha256(cmd.encode())
    for f in sorted(files):
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _started_path():
    from . import board
    return board.home() / "consoles.json"


def _started(name, fp):
    try:
        have = json.loads(_started_path().read_text())
    except (OSError, ValueError):
        have = {}
    have[name] = {"fingerprint": fp, "at": time.time()}
    _started_path().parent.mkdir(parents=True, exist_ok=True)
    _started_path().write_text(json.dumps(have))


def running_version(name):
    """The version of the program a console runs, from its process: each program keeps its versions side by side
    (Claude Code's versions/2.1.284, Codex's releases/0.154.0-...), so the path says which one is running."""
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
            return m.group(1) or m.group(2)
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
    try:
        was = json.loads(_started_path().read_text()).get(name)
    except (OSError, ValueError):
        was = None
    if not was:
        return "started before colony kept a record of how it starts"
    if was["fingerprint"] != fingerprint(root, label):
        return "its settings or helper tiers changed"
    return None


def attached(name):
    """Whether anyone has the console open: the board's terminal or a terminal of the person's."""
    r = subprocess.run(["tmux", "display", "-p", "-t", name, "#{session_attached}"], capture_output=True, text=True)
    return r.stdout.strip() not in ("", "0")


def reload(root, name=None, label=None):
    """Restart a console on what it would be started with now, back in the same conversation."""
    name = name or session_name(root)
    subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)
    return ensure(root, name, label)


def provider(name):
    """The provider running a session, by its name."""
    from . import board, providers
    return providers.of(next((p for p in board.projects() if session_name(p) == name), None))


def drafting(name):
    """Whether someone has something half-typed in the session: anything typed now would land on it and send it."""
    p = provider(name)
    styled = subprocess.run(["tmux", "capture-pane", "-p", "-e", "-t", name], capture_output=True, text=True).stdout
    return bool(p.draft(styled)) if hasattr(p, "draft") else False


def type_into(name, text):
    """Type a message into a session and send it, as if the person had; unless someone has a draft there, which
    is theirs to send: then nothing is typed, and False says so."""
    if not running(name) or drafting(name):
        return False
    # PROVIDER: this is how the board and the monitor reach an agent, and it relies on the CLI taking typed
    # text plus Enter as a message, and on Claude Code queuing it when it arrives mid-turn (urgent mail does
    # that). A provider that drops or garbles input while busy needs the watcher to wait for "idle" instead.
    subprocess.run(["tmux", "send-keys", "-t", name, "-l", text], check=True)
    time.sleep(getattr(provider(name), "enter_after", 0))   # one that takes fast typing for a paste needs a beat
    subprocess.run(["tmux", "send-keys", "-t", name, "Enter"], check=True)
    return True


def paste_into(name, text):
    """Send the person's own message, as if they had pasted it into the session and pressed Enter: lines and
    all, in one piece (a bracketed paste where the program asks for one, as Claude Code does)."""
    if "\n" not in text or not running(name) or drafting(name):
        return type_into(name, text)
    buf = f"colony-{secrets.token_hex(4)}"
    subprocess.run(["tmux", "load-buffer", "-b", buf, "-"], input=text, text=True, check=True)
    subprocess.run(["tmux", "paste-buffer", "-p", "-d", "-b", buf, "-t", name], check=True)
    time.sleep(max(0.3, getattr(provider(name), "enter_after", 0)))    # the paste lands before its Enter
    subprocess.run(["tmux", "send-keys", "-t", name, "Enter"], check=True)
    return True


def press(name, keys):
    """Press keys in a session (tmux key names: Up, Down, Enter, Escape...)."""
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
        return {"state": "off", "lines": [] if providers.installed(p) else [providers.missing(p)]}
    now = screen(name)
    shown = [l for l in (BORDER.sub("", l) for l in now.splitlines()) if l.strip()]
    from . import providers
    p = providers.of(root)
    if lines > len(shown):
        # A CLI drawing in the alternate screen (Claude Code does) leaves tmux no scrollback: then its own
        # record of the conversation is what lies above the screen.
        more = [l for l in (BORDER.sub("", l) for l in history(name).splitlines()) if l.strip()]
        if len(more) <= len(shown) and hasattr(p, "history_text"):
            more = [l for l in (p.history_text(root) or "").splitlines() if l.strip()] + shown
        shown = more
    marker = getattr(p, "scrolled_marker", "")
    return {"state": p.classify(now), "lines": shown[-lines:], "scrolled": bool(marker and marker in now),
            "activity": p.activity(now) if hasattr(p, "activity") else {"line": None, "agents": []}}


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
                elif "i" in msg:
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
<div id='term'></div>
<pre id='hist' hidden></pre>
<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.css'>
<script src='https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.js'></script>
<script src='https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.js'></script>
<script>
const term = new Terminal({{fontSize: 13, cursorBlink: true, theme: {{background: '#16171a'}}}});
const fit = new FitAddon.FitAddon();
term.loadAddon(fit);
term.open(document.getElementById('term'));
fit.fit();
// A phone drops the connection whenever the browser goes to the background, and the board may restart:
// reconnect by itself, and reload the page if that keeps failing. The session runs on regardless.
let ws, tries = 0;
const send = (m) => ws && ws.readyState === 1 && ws.send(JSON.stringify(m));
// Full screen on a phone: the terminal takes what the chips above and the keys and tabs below leave.
const refit = () => {{
  if (document.body.classList.contains('focus')) {{
    const tabs = document.querySelector('.tabs'), keys = document.querySelector('.keys');
    const tabsH = tabs && getComputedStyle(tabs).position === 'fixed' ? tabs.offsetHeight : 0;
    keys.style.bottom = tabsH + 'px';
    const h = (window.visualViewport ? visualViewport.height : innerHeight) - termEl.getBoundingClientRect().top - keys.offsetHeight - tabsH;
    termEl.style.height = Math.max(120, h) + 'px';
  }}
  fit.fit(); send({{r: [term.cols, term.rows]}});
}};
function connect() {{
  ws = new WebSocket(`ws://${{location.host}}/console/ws?p={pid}&t={token}`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {{ tries = 0; term.reset(); send({{r: [term.cols, term.rows]}}); term.focus(); }};
  ws.onmessage = (ev) => term.write(new Uint8Array(ev.data), () => {{ if (typeof showJump === 'function') showJump(); }});
  ws.onclose = () => {{
    if (++tries > 4) return location.reload();
    term.write('\\r\\n[reconnecting…]\\r\\n');
    setTimeout(connect, 800 * tries);
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
</script>
"""
