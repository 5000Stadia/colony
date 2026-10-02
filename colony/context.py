"""Rolling context, within the same provider conversation. No transcript rewrites."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time

from . import board, console, providers
from .codex_transfer import atomic_json

SOFT = .60
TAIL_EXCHANGES = 4
TAIL_BUDGET = 5_000  # UTF-8 bytes are a conservative token upper bound for text
CARRY_BUDGET = 3_000
OUTPUT_BUDGET = 9_000
DAILY = 20 * 3600
TIMEOUT = 15 * 60
COOLDOWN = 3600
LIVE = ('due', 'requested', 'ready', 'compacting', 'prepared', 'emitted', 'unknown')
_cache = {}


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def file(root, name='context.json'):
    return Path(root) / '.board' / name


@contextmanager
def locked(root):
    file(root).parent.mkdir(parents=True, exist_ok=True)
    with file(root, 'context.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def is_monitor(root):
    from . import monitor
    return Path(root).resolve() == monitor.home().resolve()


def program(root):
    return providers.of(None if is_monitor(root) else root)


def seat(root):
    from . import monitor
    return monitor.name() if is_monitor(root) else console.session_name(root)


def scoped(root, key, payload):
    if os.environ.get('COLONY_CONSOLE') != seat(root) or providers.PROVIDERS.get(key) is not program(root):
        return False
    if payload.get('subagent') or payload.get('agent_id') or '/subagents/' in payload.get('transcript_path', ''):
        return False
    sid = payload.get('session_id')
    if not sid or not payload.get('transcript_path'):
        return False
    if key == 'codex' and not is_monitor(root):
        from . import codex_remote
        expected = codex_remote.read_json(codex_remote.home_for(root) / 'colony-thread.json').get('thread')
        if expected and sid != expected:
            return False
    return True


def register(root, key, payload):
    if not scoped(root, key, payload):
        return False
    value = dict(provider=key, id=payload['session_id'], path=payload['transcript_path'])
    with locked(root):
        old = read(file(root, 'context-session.json'))
        # Only a SessionStart can replace an established session identity.
        if old and old['id'] != value['id'] and payload.get('hook_event_name') != 'SessionStart':
            return False
        atomic_json(file(root, 'context-session.json'), value)
    return True


def session(root):
    value = read(file(root, 'context-session.json'))
    if value:
        return value
    # Existing project hooks already recorded this exact logical seat before R68.
    prior = read(console._conversations()).get(str(Path(root)))
    if prior:
        return dict(provider=providers.key(program(root)), id=prior['conversation'], path=prior['transcript'])
    return None


def _text(content):
    if isinstance(content, str):
        return content
    return '\n'.join(c.get('text', '') for c in content or [] if isinstance(c, dict) and c.get('type') in ('text', 'input_text', 'output_text'))


def transcript(s):
    """PROVIDER: incrementally read exact prose and last-request usage, not lifetime spend."""
    cache_file = board.home() / 'context-cache' / (hashlib.sha256((s['provider'] + s['path']).encode()).hexdigest() + '.json')
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with cache_file.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _transcript(s, cache_file)


def _transcript(s, cache_file):
    path = Path(s['path'])
    stat = path.stat()
    key = (str(path), s['provider'])
    cached = read(cache_file) or _cache.get(key)
    if cached:
        cached['markers'] = set(cached['markers'])
        cached['summary_carries'] = set(cached.get('summary_carries', []))
        cached['current'] = cached['exchanges'][-1] if cached['current'] and cached['exchanges'] else None
    if not cached or cached['inode'] != stat.st_ino or cached['offset'] > stat.st_size:
        cached = dict(inode=stat.st_ino, offset=0, exchanges=[], current=None, tokens=None,
                      window=None, usage_at=0, markers=set(), summary_carries=set(), compact=0)
        _cache[key] = cached
    if cached['offset'] == stat.st_size:
        return cached
    with path.open() as stream:
        stream.seek(cached['offset'])
        while True:
            line = stream.readline()
            if not line or not line.endswith('\n'):
                break
            cached['offset'] = stream.tell()
            try:
                event = json.loads(line)
            except ValueError:
                continue
            role, text = None, ''
            if s['provider'] == 'claude':
                if event.get('type') == 'attachment' and (event.get('attachment') or {}).get('type') == 'hook_success':
                    cached['markers'].update(re.findall(r'colony-restored-([a-f0-9]{16})', line))
                if event.get('type') == 'system' and event.get('subtype') == 'compact_boundary':
                    cached.update(tokens=None, usage_at=0, compact=cached['offset'])
                    cached['summary_carries'].clear()
                if event.get('isCompactSummary') or (event.get('type') == 'system' and event.get('subtype') == 'compact_boundary'):
                    cached['summary_carries'].update(re.findall(r'<colony-carry id=\\?["\']([a-f0-9]{16})', line))
                message = event.get('message') or {}
                if event.get('type') == 'assistant' and (usage := message.get('usage')):
                    cached['tokens'] = sum(usage.get(k, 0) or 0 for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens'))
                    cached['usage_at'] = cached['offset']
                if event.get('type') in ('user', 'assistant') and not event.get('isMeta') and not event.get('isCompactSummary'):
                    role, text = event['type'], _text(message.get('content'))
            elif s['provider'] == 'codex':
                payload = event.get('payload') or {}
                if event.get('type') == 'response_item' and payload.get('type') == 'message':
                    role, text = payload.get('role'), _text(payload.get('content'))
                    if role == 'assistant':
                        role, text = None, ''  # Compaction's own model reply is also a response item.
                    if role == 'developer':
                        cached['markers'].update(re.findall(r'colony-restored-([a-f0-9]{16})', text))
                if event.get('type') == 'compacted':
                    cached.update(tokens=None, usage_at=0, compact=cached['offset'])
                    cached['summary_carries'] = set(re.findall(r'<colony-carry id=\\?["\']([a-f0-9]{16})', line))
                if event.get('type') == 'event_msg':
                    kind = payload.get('type')
                    if kind == 'task_started':
                        cached['current'] = None
                    if kind == 'item_completed' and (payload.get('item') or {}).get('type') == 'AgentMessage':
                        role = 'assistant'
                        text = '\n'.join(c.get('text', '') for c in payload['item'].get('content', []) if c.get('type') in ('Text', 'text'))
                    if kind == 'token_count' and (info := payload.get('info')):
                        usage = info.get('last_token_usage') or {}
                        cached['tokens'] = usage.get('total_tokens')
                        cached['window'] = info.get('model_context_window')
                        cached['usage_at'] = cached['offset']
            if not text or not text.strip():
                continue
            if role == 'user':
                if text.lstrip().startswith(('[colony]', '<local-command', '<command-name>', '<system-reminder>',
                                            '<environment_context>', '# AGENTS.md instructions', '<permissions instructions>')):
                    cached['current'] = None
                    continue
                exchange = [dict(role='user', text=text)]
                cached['exchanges'].append(exchange)
                cached['exchanges'] = cached['exchanges'][-TAIL_EXCHANGES:]
                cached['current'] = exchange
            elif role == 'assistant' and cached['current'] is not None:
                cached['current'].append(dict(role='assistant', text=text))
    atomic_json(cache_file, dict(cached, markers=list(cached['markers']), summary_carries=list(cached['summary_carries'])))
    return cached


def tail(snapshot, budget=TAIL_BUDGET, skip_pending=False):
    chosen, size = [], 0
    for exchange in reversed(snapshot['exchanges']):
        if not any(m['role'] == 'assistant' for m in exchange):
            if skip_pending:
                continue  # Native compaction retains its current input; keep earlier complete exchanges.
            raise ValueError('The latest personal exchange has not completed.')
        cost = sum(len(m['text'].encode()) + 64 for m in exchange)
        if size + cost > budget:
            if not chosen:
                raise ValueError('The latest whole exchange exceeds the restoration budget; it was not truncated.')
            break
        chosen.insert(0, exchange)
        size += cost
    return [m for exchange in chosen for m in exchange]


def usage(root, payload):
    """PROVIDER: statusline telemetry is scoped to a registered Claude session."""
    s = session(root)
    if not s or s['provider'] != 'claude' or payload.get('session_id') != s['id']:
        return
    data = payload.get('context_window') or {}
    current = data.get('current_usage')
    if current and data.get('context_window_size'):
        atomic_json(file(root, 'context-usage.json'), dict(id=s['id'], at=time.time(),
                    tokens=sum(current.get(k, 0) or 0 for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens')),
                    window=data['context_window_size']))


def occupancy(root, s, snapshot):
    window = snapshot['window']
    if s['provider'] == 'claude':
        data = read(file(root, 'context-usage.json'))
        if data.get('id') == s['id']:
            window = data.get('window')
        if is_monitor(root):
            window = min(window or 150_000, 150_000)
    return snapshot['tokens'], window


def log(root, job, status, reason='', after=None):
    board.append(root, 'context-history.jsonl', dict(at=board.now(), id=job.get('id'), session=job.get('session'),
                 status=status, reason=reason, before=job.get('before'), exchanges=len([m for m in job.get('tail', []) if m['role'] == 'user']),
                 carry_bytes=len(job.get('carry', '').encode()), after=after))


def carry_reply(root, key, payload):
    if not register(root, key, payload):
        return False
    with locked(root):
        st = read(file(root))
        job = st.get('job') or {}
        if job.get('phase') == 'emitted' and job.get('session') == payload['session_id']:
            restored(root, st)
        if job.get('phase') != 'requested' or job.get('session') != payload['session_id']:
            return False
        _, answer = program(root).turn_text(payload)
        match = re.search(r'<colony-carry id="' + re.escape(job['id']) + r'">([\s\S]*?)</colony-carry>', answer)
        if not match:
            return False
        carry = match[1].strip()
        if not carry or len(carry.encode()) > CARRY_BUDGET:
            job.update(phase='deferred', reason='Carry-over was empty or too large.')
            st['retry_after'] = time.time() + COOLDOWN
        else:
            job.update(phase='ready', carry=carry, written=board.now())
            st['carry'] = dict(text=carry, at=job['written'])
            file(root, 'context-carried.md').write_text(carry + '\n')
        atomic_json(file(root), st)
    return True


def before_compact(root, key, payload):
    if not register(root, key, payload):
        return
    s = session(root)
    snap = transcript(s)
    with locked(root):
        st = read(file(root))
        job = st.get('job') or {}
        if job.get('session') != s['id'] or job.get('phase') not in ('ready', 'compacting', 'unknown', 'prepared'):
            job = dict(id=secrets.token_hex(8), session=s['id'], carry=(st.get('carry') or {}).get('text', ''),
                       written=(st.get('carry') or {}).get('at'), automatic=True)
        try:
            recent = tail(snap, skip_pending=job.get('automatic', False))
        except ValueError as err:
            # Never interrupt a native emergency compaction. Keep the source recoverable.
            recent = []
            job['tail_deferred'] = str(err)
        job.update(phase='prepared', tail=recent, before=snap['tokens'], source=s['path'], boundary=snap['offset'])
        st['job'] = job
        atomic_json(file(root), st)


def historical(root, job, *, omit_carry=False):
    marker = 'colony-restored-' + job['id']
    text = (f'[{marker}] Historical context restored after compaction, not a new request. '
            'Do not redo actions from these exchanges. Current user instructions take precedence. '
            'The carry-over may predate the native summary; use that summary for newer progress. '
            'Read the current Vision, roadmap, notes and gates for durable project state.\n'
            + ('The native summary already retains this carry-over; its text is not repeated here.\n\n' if omit_carry else
               f"Carry-over recorded {job.get('written') or 'before this refresh'}:\n{job.get('carry') or '(none)'}\n\n"))
    text += '\n\n'.join(m['role'] + ' (verbatim historical text):\n' + m['text'] for m in job.get('tail', []))
    if job.get('tail_deferred'):
        text += '\n\nThe complete recent exchange is retained in ' + job['source'] + '; ' + job['tail_deferred']
    return text


def restored(root, st):
    st['job']['phase'] = 'restored'
    st['last'] = time.time()
    snap = transcript(session(root))
    st['await_usage'] = max(snap['compact'], st['job'].get('boundary', 0))
    st['retry_after'] = time.time() + COOLDOWN
    log(root, st['job'], 'restored', st['job'].get('tail_deferred', ''))
    atomic_json(file(root), st)


def on_prompt(root, key, payload):
    if not register(root, key, payload):
        return ''
    event = payload.get('hook_event_name')
    compact_start = payload.get('source') == 'compact'
    if not compact_start and event != 'UserPromptSubmit':
        return ''
    with locked(root):
        st = read(file(root))
        job = st.get('job') or {}
        if job.get('session') != payload['session_id'] or job.get('phase') not in ('prepared', 'emitted'):
            return ''
        snap = transcript(session(root))
        if job['id'] in snap['markers']:
            restored(root, st)
            return ''
        if not compact_start and snap['compact'] <= job.get('boundary', snap['offset']):
            return ''  # PreCompact is not evidence that compaction succeeded.
        text = historical(root, job, omit_carry=job['id'] in snap.get('summary_carries', set()))
        if len(text.encode()) > OUTPUT_BUDGET:
            raise ValueError('Restoration exceeds the provider output budget; retained for recovery.')
        return text


def delivered(root, payload):
    """Called only after the hook has flushed its context to the provider."""
    with locked(root):
        st = read(file(root))
        job = st.get('job') or {}
        if job.get('phase') == 'prepared' and job.get('session') == payload.get('session_id'):
            job['phase'] = 'emitted'
            atomic_json(file(root), st)


def safe(root):
    from . import mail, monitor, usage as limits
    name = seat(root)
    if not console.running(name) or console.attached(name) or console.drafting(name):
        return False
    if console.snapshot(root, name=name, lines=1)['state'] != 'idle' or str(root) in limits.paused():
        return False
    if is_monitor(root):
        queue = board.home() / 'to_monitor.jsonl'
        return not queue.exists() or not queue.read_text().strip()
    return not any(not n['delivered_at'] and not n.get('quiet') for n in board.open_notes(root)) and not any(not m['delivered_at'] for m in mail.inbox(root))


def refreshing(root):
    """Only a live refresh of the current conversation holds continuation; a dropped or finished one never does."""
    s, job = session(root), read(file(root)).get('job') or {}
    return bool(s and job.get('session') == s['id'] and job.get('phase') in LIVE)


def due(root, st, tokens, window, now):
    """The one test that both holds continuation for a new refresh and starts it."""
    if now < st.get('retry_after', 0) or st.get('await_usage') is not None or st.get('ineffective'):
        return None
    if is_monitor(root) and now - st.get('last', now) >= DAILY:
        return 'daily'
    return 'context threshold' if window and tokens is not None and tokens >= window * SOFT else None


def tick(root):
    s = session(root)
    if not s:
        return
    snap = transcript(s)
    tokens, window = occupancy(root, s, snap)
    now = time.time()
    with locked(root):
        st = read(file(root)) or dict(last=now)
        job = st.get('job') or {}
        if job and job.get('session') != s['id']:
            st = dict(last=now)  # A deliberately new conversation has its own cursor and transient state.
            job = {}
        phase = job.get('phase')
        if phase == 'emitted' and job['id'] in snap['markers']:
            restored(root, st)
            phase = 'restored'
        if st.get('await_usage') is not None and snap['usage_at'] > st['await_usage']:
            log(root, job, 'usage after restoration', after=tokens)
            st.pop('await_usage', None)
            if window and tokens is not None and tokens >= window * SOFT:
                st['ineffective'] = 'Restored context remains above the soft threshold; further scheduled refresh waits for reduction.'
                log(root, job, 'deferred', st['ineffective'], after=tokens)
        if window and tokens is not None and tokens < window * SOFT:
            st.pop('ineffective', None)
        reason = due(root, st, tokens, window, now)
        if phase == 'requested' and now - job['asked'] > TIMEOUT:
            job.update(phase='deferred', reason='No fresh carry-over arrived; conversation left intact.')
            st['retry_after'] = now + COOLDOWN
            log(root, job, 'deferred', job['reason'])
        elif phase == 'due' and not reason:
            st.pop('job')
            job = {}
        elif phase not in LIVE and reason:
            # Recorded before anything is sent, so continuation stays held while the input becomes idle.
            job = st['job'] = dict(id=secrets.token_hex(8), session=s['id'], phase='due', before=tokens, reason=reason)
        atomic_json(file(root), st)
    if job.get('phase') not in ('due', 'ready'):
        return
    from . import continuation
    if not continuation.hold(root, reason='context refresh'):
        defer(root, job, 'Native work did not stop; refresh postponed.')
        return
    if not safe(root):
        return
    action, ident = None, job['id']
    with locked(root):
        st = read(file(root))
        job = st.get('job') or {}
        if job.get('id') != ident or job.get('phase') not in ('due', 'ready'):
            return
        try:
            recent = tail(snap, min(TAIL_BUDGET, int((window or 200_000) * .08)))
        except ValueError as err:
            job.update(phase='deferred', reason=str(err))
            st['retry_after'] = now + COOLDOWN
            log(root, job, 'deferred', str(err))
        else:
            action = 'carry' if job['phase'] == 'due' else 'compact'
            job.update(tail=recent, **(dict(phase='requested', asked=now) if action == 'carry' else dict(phase='compacting')))
        atomic_json(file(root), st)
    if action and not safe(root):
        defer(root, job, 'Input became busy before dispatch; refresh postponed.')
        return
    if action == 'carry':
        prompt = (f'[colony] Context carry-over ({job["id"]}). Write a short carry-over of what the current Vision, '
                  'roadmap, notes and gates do not hold: unfinished intent, constraints/preferences, what remains '
                  'authorized, actions already completed or with uncertain outcomes, and rejected approaches with '
                  'their reasons. Do not redo work. Under 350 words and 3000 UTF-8 bytes. Reply only inside '
                  f'<colony-carry id="{job["id"]}">...</colony-carry>. Colony records it before refreshing context.')
        if not console.type_into(seat(root), prompt):
            defer(root, job, 'Carry-over request was not sent; refresh postponed.')
    elif action == 'compact':
        dispatched = False
        try:
            if s['provider'] == 'codex':
                from . import codex_remote
                from .codex_rpc import Client
                socket = codex_remote.socket_for(codex_remote.home_for(root))
                if socket.exists():
                    with Client(socket) as client:
                        thread = client.call('thread/read', dict(threadId=s['id'], includeTurns=False))['thread']
                        if thread['status']['type'] != 'idle':
                            raise ValueError('App thread is busy; refresh postponed.')
                        dispatched = True
                        client.call('thread/compact/start', dict(threadId=s['id']))
                    return
            dispatched = True
            if not console.type_into(seat(root), '/compact'):
                dispatched = False
                raise ValueError('The input is now busy; refresh postponed.')
        except Exception as err:
            defer(root, job, str(err), unknown=dispatched)


def defer(root, job, reason, unknown=False):
    with locked(root):
        st = read(file(root))
        current = st.get('job') or {}
        if current.get('id') == job['id'] and current.get('phase') in ('due', 'ready', 'compacting', 'requested'):
            current.update(phase='unknown' if unknown else 'deferred', reason=reason)
            st['retry_after'] = time.time() + COOLDOWN
            log(root, current, current['phase'], reason)
            atomic_json(file(root), st)


def tick_all():
    from . import monitor
    for root in [*board.projects(), monitor.home()]:
        try:
            tick(root)
        except (OSError, ValueError, KeyError):
            continue
