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


def shown(name):
    """A sender's name as people and agents read it: the steward's messages are stored as from "monitor", and are
    from the person's name for it, the role beside it, once it has one."""
    if name != "monitor":
        return name
    named = board.steward_named()
    return f"{named} (the person's steward)" if named else "steward"


def sender():
    """Who is writing: the project whose folder this runs in, the steward, or the person."""
    here = board.root_of()
    if here in names():
        return here, address(here)
    from . import monitor
    if Path.cwd().resolve() == monitor.home().resolve():
        return None, "monitor"
    return None, "the person"


def blocker(root, snap=None):
    """Known local reasons for delay. Cached authentication is explicitly a last check, not a fresh probe."""
    from . import providers, usage
    paused = usage.paused().get(str(root))
    if paused:
        window = paused.get('window', 'usage')
        reset = usage.when(paused.get('resets_at'))
        return {'code': 'usage-pause', 'reason': f"recipient is paused at its {window} usage limit; resets {reset}"}
    if snap and snap.get('delivery_blocker'):
        known = snap['delivery_blocker']
        return {'code': known.get('code', ''), 'reason': known['reason']}
    provider = providers.of(root)
    signed_out = board.signed_out().get(providers.key(provider))
    if signed_out:
        return {'code': 'signed-out',
                'reason': f"{provider.label} was signed out at the last check ({signed_out}); current authentication is unconfirmed"}
    if snap and snap.get('state') == 'off':
        return {'code': 'console-off', 'reason': 'recipient console is not running; no underlying cause is known'}
    return None


def send(to, text, ask=False, re=None, urgent=False):
    if not board.registry()["settings"]["messaging"]:
        raise PermissionError("messaging between projects is off (colony settings messaging on)")
    dest = resolve(to)
    src_root, src = sender()
    msg = {"type": "message", "id": "m" + secrets.token_hex(4), "at": board.now(), "from": src, "to": to,
           "text": text.strip(), "ask": ask, "re": re, "urgent": urgent,
           "delivery": {'status': 'queued', 'reason': '', 'code': '', 'at': board.now()}}
    if src_root and Path(src_root) in names():
        msg['source_root'] = str(src_root)
    else:
        src_root = None
    # Keep the sender's record first: a source failure must not look like a successful send.
    if src_root and src_root != dest:
        try:
            board.append(src_root, FILE, msg)
        except OSError as err:
            raise OSError(f"{msg['id']}: could not record sender copy; recipient was not queued: {err}") from err
    try:
        board.append(dest, FILE, msg)
    except OSError as err:
        reason = f"recipient inbox write was not confirmed (a partial write may have queued it): {err}"
        if src_root and src_root != dest:
            try:
                board.append(src_root, FILE, {'type': 'delivery', 'of': msg['id'], 'at': board.now(),
                                            'status': 'failed', 'reason': reason, 'code': 'file-write'})
            except OSError as source_err:
                reason += f"; could not record failure in sender log: {source_err}"
        raise OSError(f"{msg['id']}: {reason}") from err
    known = blocker(dest)
    if known:
        # The command returns this reason directly, so a second note would only repeat it.
        try:
            return report(dest, [msg], 'delayed', **known, notify=False)[0]
        except OSError as err:
            raise OSError(f"{msg['id']}: message is queued, but its delivery status could not be recorded: {err}") from err
    return msg


def messages(root):
    """Every message this project holds, folded with whether it was delivered and answered."""
    out = {}
    for e in board.read(root, FILE):
        if e["type"] == "message":
            out.setdefault(e["id"], dict(e, delivered_at=None, answer=None))
            out[e['id']].setdefault('delivery', {'status': 'queued', 'reason': '', 'code': '', 'at': e['at']})
        elif e["type"] == "delivered" and e["of"] in out:
            out[e["of"]]["delivered_at"] = e["at"]
            out[e['of']]['delivery'] = {'status': 'handed', 'reason': '', 'code': '', 'at': e['at']}
        elif e['type'] == 'delivery' and e['of'] in out:
            out[e['of']]['delivery'] = {k: v for k, v in e.items() if k not in ('type', 'of')}
            if e['status'] == 'handed' and not out[e['of']]['delivered_at']:
                out[e['of']]['delivered_at'] = e['at']
    for m in out.values():
        if m["re"] in out:
            out[m["re"]]["answer"] = m
    return sorted(out.values(), key=lambda m: m["at"])


def inbox(root):
    me = address(root)
    return [m for m in messages(root) if m["to"] == me]


def unanswered(root):
    return [m for m in inbox(root) if m["ask"] and not m["answer"]]


def deliver(root, session=False, mark=True):
    """Mail the agent has not been handed, marked as handed (mark=False: the caller marks it once shown); at a
    session's start instead the questions it has not answered yet, so nothing waits unseen. New mail waits for a
    prompt, as new notes do (board.deliver): a session's start starts no turn."""
    if session:
        return [], [m for m in unanswered(root) if m["delivered_at"]]
    fresh = [m for m in inbox(root) if not m["delivered_at"]]
    still = []
    if mark:
        delivered(root, fresh)
    return fresh, still


def _source(message):
    """Only registered project folders are receipt destinations, including legacy address-only messages."""
    projects = names()
    stored = message.get('source_root')
    if stored:
        candidate = Path(stored)
        return candidate if candidate in projects else None
    try:
        return resolve(message['from'])
    except KeyError:
        return None


def report(root, messages, status, reason='', code='', *, notify=True):
    """Record delivery reality in both copies; only meaningful delays and seen-delay recovery wake a sender.

    Handed means a hook handed the message to the session, never proof that it was read or answered.
    Reload the log for deduplication: watcher restarts and stale caller dictionaries cannot repeat a notice.
    """
    import fcntl
    path = Path(root) / '.board' / 'mail.lock'
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        return _report(root, messages, status, reason, code, notify=notify)


def _report(root, originals, status, reason, code, *, notify):
    if status not in ('delayed', 'reply-delayed', 'handed'):
        raise ValueError(f'unknown mail delivery status: {status}')
    current = {m['id']: m for m in messages(root)}
    updated = []
    for original in originals:
        m = current.get(original['id'])
        if not m or m['to'] != address(root):
            continue
        previous = m['delivery']
        if status == 'delayed' and m['delivered_at']:
            updated.append(m)
            continue
        if status == 'reply-delayed' and (not m['delivered_at'] or not m['ask'] or m['answer']):
            updated.append(m)
            continue
        if (previous.get('status'), previous.get('reason', ''), previous.get('code', '')) == (status, reason, code):
            updated.append(m)
            continue
        source = _source(m)
        seen = previous.get('reported', False)
        old_note = None
        if source and previous.get('note_id'):
            old_note = next((n for n in board.notes(source) if n['id'] == previous['note_id']), None)
            if old_note:
                seen = seen or bool(old_note['delivered_at'] or old_note['addressed_at'])
                if not old_note['addressed_at']:
                    board.append(source, 'notes.jsonl', {'type': 'addressed', 'of': old_note['id'], 'at': board.now(),
                                                        'text': 'Superseded by the current mail delivery status.'})
        event = {'type': 'delivery', 'of': m['id'], 'at': board.now(), 'status': status,
                 'reason': reason, 'code': code}
        if status in ('delayed', 'reply-delayed'):
            event['reported'] = bool(seen or not notify)
            if source and notify:
                phase = 'reply delayed (the message was already handed to its session)' if status == 'reply-delayed' else 'delivery delayed; message remains queued'
                note = board.add_note(source, None, f"Mail {m['id']} to {m['to']}: {phase}: {reason}",
                                      author='colony', kind='mail-delivery')
                event['note_id'] = note['id']
        elif source and seen and previous['status'] in ('delayed', 'reply-delayed'):
            phase = ('the recipient can resume its reply; the message was already handed to its session'
                     if previous['status'] == 'reply-delayed' else 'handed to the recipient session')
            board.add_note(source, None, f"Mail {m['id']} to {m['to']}: {phase}. This confirms no read or answer.",
                           author='colony', kind='mail-delivery')
        board.append(root, FILE, event)
        if source and source != Path(root):
            board.append(source, FILE, event)
        m['delivery'] = {k: v for k, v in event.items() if k not in ('type', 'of')}
        if status == 'handed' and not m['delivered_at']:
            m['delivered_at'] = event['at']
        updated.append(m)
    return updated


def delivered(root, ms):
    report(root, ms, 'handed')


def render(ms, heading):
    if not ms:
        return ""
    lines = [heading]
    for m in ms:
        kind = "asks" if m["ask"] else "writes"
        lines.append(f"- [{m['id']}] {shown(m['from'])} {kind}: {m['text']}")
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
