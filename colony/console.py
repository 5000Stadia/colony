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
import struct
import subprocess
import termios
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


def command(label, root=None):
    """How the project's provider starts its agent, with the project's settings (falling back to the global ones)."""
    if COMMAND:
        return COMMAND.format(name=shlex.quote(label))
    from . import board, providers
    s = board.project_settings(root)[0] if root else board.registry()["settings"]
    return providers.of(root).command(label, s)


def session_name(root):
    root = Path(root)
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", root.name)[:24]
    return f"board-{slug}-{hashlib.sha1(str(root).encode()).hexdigest()[:4]}"


def live(root):
    return subprocess.run(["tmux", "has-session", "-t", session_name(root)], capture_output=True).returncode == 0


def ensure(root, name=None, label=None):
    """Start the session if it is not running: the provider's CLI, in the project, as its settings say."""
    name = name or session_name(root)
    if subprocess.run(["tmux", "has-session", "-t", name], capture_output=True).returncode != 0:
        subprocess.run(["tmux", "new-session", "-d", "-s", name, "-c", str(root), "-x", "200", "-y", "50",
                        command(label or Path(root).name, None if label else root)],
                       check=True)
        subprocess.run(["tmux", "set-option", "-t", name, "status", "off"], capture_output=True)
    return name


def type_into(name, text):
    """Type a message into a session and send it, as if the person had."""
    # PROVIDER: this is how the board and the monitor reach an agent, and it relies on the CLI taking typed
    # text plus Enter as a message, and on Claude Code queuing it when it arrives mid-turn (urgent mail does
    # that). A provider that drops or garbles input while busy needs the watcher to wait for "idle" instead.
    subprocess.run(["tmux", "send-keys", "-t", name, "-l", text], check=True)
    subprocess.run(["tmux", "send-keys", "-t", name, "Enter"], check=True)


def press(name, keys):
    """Press keys in a session (tmux key names: Up, Down, Enter, Escape...)."""
    for k in keys:
        subprocess.run(["tmux", "send-keys", "-t", name, k], check=True)


def screen(name):
    return subprocess.run(["tmux", "capture-pane", "-p", "-t", name], capture_output=True, text=True).stdout


def stop(root):
    subprocess.run(["tmux", "kill-session", "-t", session_name(root)], capture_output=True)


# ---------------------------------------------------------------- a glance at a session without opening it

BORDER = re.compile(r"^[\s│|╭╮╰╯─━┃┏┓┗┛]+|[\s│|╭╮╰╯─━┃┏┓┗┛]+$")


def snapshot(root, lines=6, name=None):
    name = name or session_name(root)
    if subprocess.run(["tmux", "has-session", "-t", name], capture_output=True).returncode != 0:
        return {"state": "off", "lines": []}
    screen = subprocess.run(["tmux", "capture-pane", "-p", "-t", name], capture_output=True, text=True).stdout
    shown = [BORDER.sub("", l) for l in screen.splitlines()]
    shown = [l for l in shown if l.strip()]
    from . import providers
    return {"state": providers.of(root).classify(screen), "lines": shown[-lines:]}


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
 <button class='exit' id='exit'>Exit</button><button data-k='esc'>Esc</button><button data-k='tab'>Tab</button>
 <button data-k='up'>↑</button><button data-k='down'>↓</button><button data-k='left'>←</button><button data-k='right'>→</button>
 <button class='copy' id='history'>Select text</button><button class='selectall' id='selectall'>Select all</button></div>
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
const refit = () => {{ fit.fit(); send({{r: [term.cols, term.rows]}}); }};
function connect() {{
  ws = new WebSocket(`ws://${{location.host}}/console/ws?p={pid}&t={token}`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => {{ tries = 0; term.reset(); send({{r: [term.cols, term.rows]}}); term.focus(); }};
  ws.onmessage = (ev) => term.write(new Uint8Array(ev.data));
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
term.onData((d) => {{ send({{i: d}}); scrolled(-above); }});   // typing brings Claude Code back to the bottom
// A phone keyboard has no arrows or Esc, which every on-screen choice needs: these send the same bytes.
const KEYS = {{esc: '\\x1b', tab: '\\t', up: '\\x1b[A', down: '\\x1b[B', left: '\\x1b[D', right: '\\x1b[C'}};
document.querySelectorAll('.keys button[data-k]').forEach((b) => b.addEventListener('click', (ev) => {{
  ev.preventDefault(); send({{i: KEYS[b.dataset.k]}});
}}));
// On a phone the console takes the whole screen: the terminal, the keys, a way out.
const touch = matchMedia('(pointer: coarse)').matches;
const focus = (on) => {{ document.body.classList.toggle('focus', on); setTimeout(refit, 50); }};
if (touch && {focus}) focus(true);
document.getElementById('fullscreen').addEventListener('click', () => focus(true));
document.getElementById('exit').addEventListener('click', () => {{
  if (document.body.classList.contains('copying')) return showText(false);
  if ('{exit}') location.href = '{exit}'; else focus(false);
}});
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
let above = 0;
const scrolled = (n) => {{ above = Math.max(0, above + n); jump.hidden = above === 0; }};
jump.addEventListener('click', (ev) => {{
  ev.preventDefault(); cancelAnimationFrame(glide); acc = 0;
  send({{i: wheel(false).repeat(Math.min(400, above * 3 + 10))}}); scrolled(-above);
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
async function showText(on) {{
  if (!on) {{ hist.hidden = true; document.body.classList.remove('copying'); return term.focus(); }}
  const r = await fetch('/console/text?p={pid}', {{cache: 'no-store'}});
  hist.textContent = r.ok ? await r.text() : '(could not read the session)';
  hist.hidden = false; document.body.classList.add('copying'); hist.scrollTop = hist.scrollHeight;
}}
histBtn.addEventListener('click', () => showText(true));
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
