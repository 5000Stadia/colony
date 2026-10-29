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
from contextlib import contextmanager
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
        return {'code': 'usage-pause', 'reason': f"recipient is in its configured {window} safe-pause window; resets {reset}"}
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
            if not out[e['of']]['delivered_at']:
                out[e['of']]['delivered_at'] = e.get('handed_at') or (e['at'] if e['status'] == 'handed' else None)
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


@contextmanager
def _lock(root):
    import fcntl
    path = Path(root) / '.board' / 'mail.lock'
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def report(root, originals, status, reason='', code='', *, notify=True):
    """Record the recipient's reality first, then finish sender feedback, retrying after partial writes.

    Handed means a hook handed the message to the session, never proof that it was read or answered.
    """
    with _lock(root):
        updated = _report(root, originals, status, reason, code, notify=notify)
        current = _repair(root)
        return [current[m['id']] for m in updated]


def _record(root, message, status, reason='', code='', *, notify=True):
    previous = message['delivery']
    event = {'type': 'delivery', 'of': message['id'], 'at': board.now(), 'status': status,
             'reason': reason, 'code': code}
    if message['delivered_at'] or status == 'handed':
        event['handed_at'] = message['delivered_at'] or event['at']
    if status in ('delayed', 'reply-delayed'):
        event['reported'] = bool(previous.get('reported') or not notify)
        if notify:
            event['note_id'] = 'n' + secrets.token_hex(4)
    elif previous['status'] in ('delayed', 'reply-delayed'):
        event['note_id'] = 'n' + secrets.token_hex(4)
    # The stable note identity must exist here before any sender log or note can be changed.
    board.append(root, FILE, event)
    message['delivery'] = {k: v for k, v in event.items() if k not in ('type', 'of')}
    if status == 'handed' and not message['delivered_at']:
        message['delivered_at'] = event['at']
    return event


def _report(root, originals, status, reason, code, *, notify):
    if status not in ('delayed', 'reply-delayed', 'handed'):
        raise ValueError(f'unknown mail delivery status: {status}')
    current = {m['id']: m for m in inbox(root)}
    updated = []
    for original in originals:
        m = current.get(original['id'])
        if not m:
            continue
        previous = m['delivery']
        if status == 'delayed' and m['delivered_at']:
            updated.append(m)
            continue
        if status == 'reply-delayed' and (not m['delivered_at'] or not m['ask'] or m['answer']):
            updated.append(m)
            continue
        if status == 'handed' and previous['status'] == 'handed':
            updated.append(m)
            continue
        if (previous.get('status'), previous.get('reason', ''), previous.get('code', '')) == (status, reason, code):
            updated.append(m)
            continue
        _record(root, m, status, reason, code, notify=notify)
        updated.append(m)
    return updated


def repair(root):
    """Finish pending sender feedback, even for handed FYI or answered asks, without delivering mail again."""
    # An idle watcher must not create bookkeeping before a project's first message. Once a log has work,
    # _repair reads it under the lock; a message arriving after this look waits for the next tick.
    try:
        if not (Path(root) / '.board' / FILE).stat().st_size:
            return
    except FileNotFoundError:
        return
    with _lock(root):
        _repair(root)


def _repair(root):
    current = {m['id']: m for m in inbox(root)}
    history = {ident: [] for ident in current}
    originals = {}
    for event in board.read(root, FILE):
        if event['type'] == 'message' and event['id'] in current:
            originals.setdefault(event['id'], event)
        elif event['type'] == 'delivery' and event['of'] in history:
            history[event['of']].append(event)
    projects, sources = names(), {}
    for ident, m in current.items():
        if m['delivery']['status'] == 'reply-delayed' and m['answer']:
            answer = m['answer']
            state = ('was handed to the sender session' if answer['delivered_at'] else
                     'was recorded; its delivery is unconfirmed' if answer['delivery']['status'] == 'failed' else
                     'is queued for the sender')
            history[ident].append(_record(root, m, 'handed', f"answer {answer['id']} {state}"))
        if history[ident]:
            try:
                _feedback(root, m, history[ident], originals[ident], projects, sources)
            except OSError:
                # Sender storage cannot revoke an already flushed handoff. The watcher retries this log.
                # A failed flush may still have appended: reload before another message uses the cache.
                sources.clear()
    return current


def _feedback(root, message, history, original, projects, sources):
    stored = message.get('source_root')
    source = Path(stored) if stored else next((p for p, name in projects.items() if name == message['from']), None)
    if source not in projects:
        return
    if source not in sources:
        copies = {m['id']: m['delivery'] for m in messages(source)} if source != Path(root) else {}
        sources[source] = copies, {n['id']: n for n in board.notes(source)}
    copies, notes = sources[source]
    event = history[-1]
    if source != Path(root):
        if message['id'] not in copies:
            board.append(source, FILE, original)
            copies[message['id']] = None
        if copies[message['id']] != message['delivery']:
            board.append(source, FILE, event)
            copies[message['id']] = message['delivery']
    superseded = 'Superseded by the current mail delivery status.'
    seen = False
    for earlier in reversed(history[:-1]):
        if earlier['status'] not in ('delayed', 'reply-delayed'):
            break
        note = notes.get(earlier.get('note_id'), {})
        seen = seen or earlier.get('reported') or note.get('delivered_at') or (
            note.get('addressed_at') and note.get('reply') != superseded)
    for earlier in history[:-1]:
        note = notes.get(earlier.get('note_id'))
        if note and not note['addressed_at']:
            stamp = board.now()
            board.append(source, 'notes.jsonl', {'type': 'addressed', 'of': note['id'],
                                                'at': stamp, 'text': superseded})
            note.update(addressed_at=stamp, reply=superseded)
    note_id = event.get('note_id')
    delayed = event['status'] in ('delayed', 'reply-delayed')
    if not note_id or note_id in notes or (not delayed and not seen):
        return
    if delayed:
        phase = ('reply delayed (the message was already handed to its session)' if event['status'] == 'reply-delayed'
                 else 'delivery delayed; message remains queued')
        text = f"{phase}: {event['reason']}"
    else:
        phase = (event['reason'] or
                 ('the previously reported block is no longer observed; the message was already handed to its session'
                  if history[-2]['status'] == 'reply-delayed' else 'handed to the recipient session'))
        text = phase + ('.' if message['answer'] else '. This confirms no read or answer.')
    note = {'type': 'note', 'id': note_id, 'at': event['at'], 'author': 'colony',
            'anchor': None, 'kind': 'mail-delivery',
            'text': f"Mail {message['id']} to {message['to']}: {text}"}
    board.append(source, 'notes.jsonl', note)
    notes[note_id] = dict(note, reply=None, addressed_at=None, delivered_at=None)


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
            f"`colony send {dest} --ask \"...\"`; otherwise `colony send {dest} \"...\"`. "
            f"Then tell the person the returned queue or delivery status and any known reason.")


def read_text(value):
    return sys.stdin.read() if value == "-" else value
