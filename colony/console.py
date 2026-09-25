"""The console: each project's own Claude Code session, live in the board.

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
import struct
import subprocess
import termios
from pathlib import Path

TOKEN = secrets.token_urlsafe(24)          # made fresh each time the board starts
COMMAND = os.environ.get("COLONY_CONSOLE_CMD", "claude")


def session_name(root):
    root = Path(root)
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", root.name)[:24]
    return f"board-{slug}-{hashlib.sha1(str(root).encode()).hexdigest()[:4]}"


def live(root):
    return subprocess.run(["tmux", "has-session", "-t", session_name(root)], capture_output=True).returncode == 0


def ensure(root):
    """Start the project's session if it is not running: the person's own `claude`, in the project."""
    name = session_name(root)
    if not live(root):
        subprocess.run(["tmux", "new-session", "-d", "-s", name, "-c", str(root), "-x", "200", "-y", "50", COMMAND],
                       check=True)
        subprocess.run(["tmux", "set-option", "-t", name, "status", "off"], capture_output=True)
    return name


def stop(root):
    subprocess.run(["tmux", "kill-session", "-t", session_name(root)], capture_output=True)


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


def bridge(sock, root):
    """Attach a terminal to the project's session and pass bytes both ways until either side leaves.
    Leaving detaches; the session keeps running."""
    name = ensure(root)
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
<div class='console-bar'><span class='muted'>Claude Code in <code>{path}</code> · session <code>{name}</code>
 (also reachable with <code>tmux attach -t {name}</code>)</span>
 <form method='post' action='/console/stop' onsubmit="return confirm('End this project\\'s session?')">
 <input type='hidden' name='p' value='{pid}'><input type='hidden' name='back' value='/?p={pid}&view=console'>
 <button class='quiet'>End session</button></form></div>
<div id='term'></div>
<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.css'>
<script src='https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.js'></script>
<script src='https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.js'></script>
<script>
const term = new Terminal({{fontSize: 13, cursorBlink: true, theme: {{background: '#16171a'}}}});
const fit = new FitAddon.FitAddon();
term.loadAddon(fit);
term.open(document.getElementById('term'));
fit.fit();
const ws = new WebSocket(`ws://${{location.host}}/console/ws?p={pid}&t={token}`);
ws.binaryType = 'arraybuffer';
const send = (m) => ws.readyState === 1 && ws.send(JSON.stringify(m));
ws.onopen = () => {{ send({{r: [term.cols, term.rows]}}); term.focus(); }};
ws.onmessage = (ev) => term.write(new Uint8Array(ev.data));
ws.onclose = () => term.write('\\r\\n[disconnected: reload to reattach; the session keeps running]\\r\\n');
term.onData((d) => send({{i: d}}));
window.addEventListener('resize', () => {{ fit.fit(); send({{r: [term.cols, term.rows]}}); }});
</script>
"""
