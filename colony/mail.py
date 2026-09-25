"""Mail between projects: one inbox per project, created with the project, nothing before the first.

A project's address is its name on the board. A message is kept in the recipient's `.board/mail.jsonl`
and copied to the sender's, so each project holds its own conversation. It reaches the recipient's
agent through the board's hooks on its next turn; if that session is idle or not running, the watcher
wakes it. `--ask` marks a message that expects an answer; `colony reply` answers it. `--urgent` is typed
in even while the recipient is mid-turn (Claude Code queues it); otherwise mail waits for the turn to end.
Mail itself is provider-agnostic: files and the `colony` command. PROVIDER: only the mid-turn typing assumes
Claude Code (see console.type_into).
"""
import secrets
import sys
from pathlib import Path

from . import board

FILE = "mail.jsonl"


def names():
    """Each project's address: its folder name, made unique on the board if two share one."""
    out, seen = {}, {}
    for p in board.projects():
        base = p.name
        seen[base] = seen.get(base, 0) + 1
        out[p] = base if seen[base] == 1 else f"{base}-{seen[base]}"
    return out


def address(root):
    return names().get(Path(root), Path(root).name)


def resolve(name):
    for p, n in names().items():
        if n == name:
            return p
    raise KeyError(name)


def sender():
    """Who is writing: the project whose folder this runs in, the monitor, or the person."""
    here = board.root_of()
    if here in names():
        return here, address(here)
    from . import monitor
    if Path.cwd().resolve() == monitor.home().resolve():
        return None, "monitor"
    return None, "the person"


def send(to, text, ask=False, re=None, urgent=False):
    if not board.registry()["settings"]["messaging"]:
        raise PermissionError("messaging between projects is off (colony settings messaging on)")
    dest = resolve(to)
    src_root, src = sender()
    msg = {"type": "message", "id": "m" + secrets.token_hex(3), "at": board.now(), "from": src, "to": to,
           "text": text.strip(), "ask": ask, "re": re, "urgent": urgent}
    board.append(dest, FILE, msg)
    if src_root and src_root != dest:
        board.append(src_root, FILE, msg)
    return msg


def messages(root):
    """Every message this project holds, folded with whether it was delivered and answered."""
    out = {}
    for e in board.read(root, FILE):
        if e["type"] == "message":
            out.setdefault(e["id"], dict(e, delivered_at=None, answer=None))
        elif e["type"] == "delivered" and e["of"] in out:
            out[e["of"]]["delivered_at"] = e["at"]
    for m in out.values():
        if m["re"] in out:
            out[m["re"]]["answer"] = m
    return sorted(out.values(), key=lambda m: m["at"])


def inbox(root):
    me = address(root)
    return [m for m in messages(root) if m["to"] == me]


def unanswered(root):
    return [m for m in inbox(root) if m["ask"] and not m["answer"]]


def deliver(root, session=False):
    """Mail the agent has not been handed, marked as handed; at a session's start also the questions it
    has not answered yet, so nothing waits unseen."""
    fresh = [m for m in inbox(root) if not m["delivered_at"]]
    for m in fresh:
        board.append(root, FILE, {"type": "delivered", "of": m["id"], "at": board.now()})
    still = [m for m in unanswered(root) if m["delivered_at"]] if session else []
    return fresh, still


def render(ms, heading):
    if not ms:
        return ""
    lines = [heading]
    for m in ms:
        kind = "asks" if m["ask"] else "writes"
        lines.append(f"- [{m['id']}] {m['from']} {kind}: {m['text']}")
    if any(m["ask"] for m in ms):
        lines.append('Answer a question with: colony reply ID "your answer"')
    return "\n".join(lines)


def reply(msg_id, text):
    here, _ = sender()
    roots = [here] if here else list(names())
    for root in roots:
        original = next((m for m in messages(root) if m["id"] == msg_id), None)
        if original:
            return send(original["from"], text, ask=False, re=msg_id)
    raise KeyError(msg_id)


def instruction(dest, text):
    """What the board types into the sending project's console when the person asks it to write to another
    project: the person's intent; the agent writes the message with its own context."""
    return (f"[colony] The person asks you to message the {dest} project in the colony. What it should be about: {text} — "
            f"Write it so their agent can act on it without our context. If you need an answer, "
            f"`colony send {dest} --ask \"...\"`; otherwise `colony send {dest} \"...\"`. Then tell the person it is sent.")


def read_text(value):
    return sys.stdin.read() if value == "-" else value
