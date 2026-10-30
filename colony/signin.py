"""Cached native authentication checks and transient, browser-assisted native logins.

Only provider adapters interpret native output. Credentials remain with the native
program; the board persists safe status metadata and hashes of observed failures.
"""
import atexit
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit

from . import board, providers

CHECK_SECONDS = 300
SESSION_SECONDS = 600
OUTPUT_LIMIT = 32768
MARKER_LIMIT = 128
_ACTIVE = {'starting', 'waiting', 'verifying'}
_REASONS = {
    'unknown': 'Sign-in status is unknown.',
    'signed-in': 'Native sign-in is available.',
    'signed-out': 'The native program is signed out.',
    'authentication': 'The provider rejected authentication. Reconnect to continue.',
}
_MESSAGES = {
    'starting': 'Starting native sign-in…',
    'waiting': 'Finish signing in with the provider.',
    'verifying': 'Checking the saved native sign-in…',
    'done': 'Connected. Idle consoles will reload their sign-in; project pauses are unchanged.',
    'failed': 'Sign-in did not finish. You can try again.',
    'cancelled': 'Sign-in cancelled.',
    'expired': 'Sign-in expired. Start again for a new link.',
}
_lock = threading.RLock()
_sessions = {}
_checker = None
_revision = {}

# A separate supervisor avoids preexec_fn in the threaded board. Its Linux parent
# death signal kills the entire login process group even if the board is killed.
_SUPERVISOR = r'''
import ctypes, os, signal, subprocess, sys
parent = int(sys.argv[1])
def stop(*_):
    os.killpg(os.getpgrp(), signal.SIGKILL)
signal.signal(signal.SIGTERM, stop)
if sys.platform.startswith('linux'):
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0):
        sys.exit(125)
    if os.getppid() != parent:
        stop()
child = subprocess.Popen(sys.argv[2:], stdin=sys.stdin.buffer, stdout=sys.stdout.buffer,
                         stderr=sys.stderr.buffer, shell=False)
try:
    result = child.wait()
except BaseException:
    stop()
sys.exit(result if result >= 0 else 128 - result)
'''


def _iso(seconds=None):
    return datetime.fromtimestamp(time.time() if seconds is None else seconds,
                                  timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _epoch(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, TypeError, ValueError):
        return 0


def _provider(key):
    if key not in providers.PROVIDERS:
        raise ValueError('Unknown provider.')
    return providers.PROVIDERS[key]


def _read(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _load():
    rows = _read(board.home() / 'signin-state.json')
    legacy = _read(board.home() / 'signed-out.json')
    for key in providers.PROVIDERS:
        if key not in rows and key in legacy:
            rows[key] = dict(state='reconnect', checked_at=None, since=legacy[key],
                             reason=_REASONS['signed-out'], authoritative=False, markers=[])
    return rows


def _empty():
    return dict(state='unknown', checked_at=None, connected_at=None, since=None,
                reason=_REASONS['unknown'], authoritative=False, markers=[])


def _save(rows, before):
    if rows != before:
        board.save_json(board.home() / 'signin-state.json', rows)
    path = board.home() / 'signed-out.json'
    was = _read(path)
    out = dict(was)
    for key, row in rows.items():
        if key not in providers.PROVIDERS:
            continue
        if row.get('state') == 'reconnect':
            out[key] = row.get('since') or _iso()
        else:
            out.pop(key, None)
    if out != was:
        board.save_json(path, out)


def _change(key, mutate):
    with _lock, board.board_lock('signin'):
        rows = _load()
        before = json.loads(json.dumps(rows))
        row = rows.setdefault(key, _empty())
        mutate(row)
        _save(rows, before)


def states():
    """Read safe cached status only; never invoke a native command on page rendering."""
    with _lock:
        rows = _load()
        return {key: {field: rows.get(key, _empty()).get(field)
                      for field in ('state', 'checked_at', 'connected_at', 'since', 'reason')}
                for key, provider in providers.PROVIDERS.items() if _eligible(key, provider)}


def _slot(key):
    return _sessions.get((str(board.home()), key))


def _public(session):
    if session is None:
        return None
    result = {field: session[field] for field in ('id', 'state', 'expires_at')}
    result['message'] = _MESSAGES[session['state']]
    if session['state'] in _ACTIVE:
        result.update(session.get('prompt', {}))
    return result


def state(key):
    _provider(key)
    with _lock:
        result = states().get(key, {field: _empty()[field]
                                  for field in ('state', 'checked_at', 'connected_at', 'since', 'reason')})
        result['session'] = _public(_slot(key))
        return result


def _eligible(key, provider):
    policy = getattr(providers, 'signin_eligible', None)
    return policy(key, provider) if policy else providers.usable(provider)


def _due(row):
    return time.time() - _epoch(row.get('checked_at')) >= CHECK_SECONDS


def check(key=None, force=False):
    """Run token-free native status checks. Runtime authentication failures stay sticky."""
    keys = [key] if key is not None else list(providers.PROVIDERS)
    for item in keys:
        provider = _provider(item)
        with _lock:
            if not _eligible(item, provider) or (not force and not _due(states()[item])):
                continue
            scope = (str(board.home()), item)
            revision = _revision.get(scope, 0)
        try:
            signed_in = provider.signed_in()
        except Exception:
            signed_in = None
        def update(row):
            row['checked_at'] = _iso()
            if signed_in is False:
                row.update(state='reconnect', since=row.get('since') or _iso())
                if not row.get('authoritative'):
                    row['reason'] = _REASONS['signed-out']
            elif signed_in is True and not row.get('authoritative'):
                row.update(state='signed-in', since=None, reason=_REASONS['signed-in'])
            elif signed_in is None and row.get('state') != 'reconnect':
                row.update(state='unknown', since=None, reason=_REASONS['unknown'])
        with _lock:
            if revision == _revision.get(scope, 0):
                _change(item, update)
    return state(key) if key is not None else states()


def request_check(key=None, force=True):
    """Deduplicate asynchronous explicit rechecks and periodic cached checks."""
    global _checker
    if key is not None:
        _provider(key)
    with _lock:
        if _checker is not None and _checker.is_alive():
            return
        rows = states()
        keys = [key] if key is not None else list(rows)
        if not any(item in rows and (force or _due(rows[item])) for item in keys):
            return
        _checker = threading.Thread(target=check, kwargs=dict(key=key, force=force),
                                    name='colony-signin-check', daemon=True)
        _checker.start()


def tick():
    """Fixture consoles never cause native probes as a side effect of a tick."""
    if not os.environ.get('COLONY_CONSOLE_CMD'):
        request_check(force=False)


def observe(key, problem, marker):
    """Remember a classified runtime authentication error, without its raw output."""
    _provider(key)
    code = problem.get('code') if isinstance(problem, dict) else getattr(problem, 'code', None)
    if code != 'authentication':
        return state(key)
    # Markers identify a screen/transcript generation; only hashes survive checks
    # and successful login, so an old unchanged screen cannot relatch the banner.
    digest = hashlib.sha256((key + '\0' + str(marker)).encode()).hexdigest()
    def update(row):
        markers = row.setdefault('markers', [])
        if digest in markers:
            return
        row['markers'] = (markers + [digest])[-MARKER_LIMIT:]
        row.update(state='reconnect', since=row.get('since') or _iso(),
                   reason=_REASONS['authentication'], authoritative=True)
        scope = (str(board.home()), key)
        _revision[scope] = _revision.get(scope, 0) + 1
    _change(key, update)
    return state(key)


def _safe_prompt(provider, output):
    # Keep ANSI fragments until complete: parse the cumulative bounded output,
    # then strip whole CSI/OSC sequences (including hyperlinks), never line-read.
    clean = re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)', '', output)
    clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', clean)
    parsed = provider.signin_prompt(clean)
    if not isinstance(parsed, dict):
        return {}
    prompt = {}
    url = parsed.get('url')
    if isinstance(url, str) and len(url) <= 8192 and not re.search(r'[\x00-\x20\x7f]', url):
        try:
            parts = urlsplit(url)
            if parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password:
                prompt['url'] = url
        except ValueError:
            pass
    code = parsed.get('code')
    if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9-]{1,128}', code):
        prompt['code'] = code
    if parsed.get('accept_code') is True:
        prompt['accept_code'] = True
    return prompt


def _stop_process(session):
    process = session.get('process')
    if process is None:
        return
    # Always target the isolated process group, including a login's descendants.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass


def _finish(session, result):
    session.update(state=result, prompt={})


def _run(key, provider, session):
    selector = selectors.DefaultSelector()
    output = ''
    try:
        with _lock:
            if session['state'] not in _ACTIVE:
                return
            command = provider.signin_command()
            if not command:
                _finish(session, 'failed')
                return
            session['process'] = subprocess.Popen(
                [sys.executable, '-I', '-c', _SUPERVISOR, str(os.getpid()), *command],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=board.home(), env=provider.signin_environment(), shell=False,
                start_new_session=True, bufsize=0)
            process = session['process']
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            with _lock:
                if session['state'] not in _ACTIVE:
                    return
                if time.monotonic() >= session['deadline']:
                    _finish(session, 'expired')
                    return
            for selected, _ in selector.select(.1):
                chunk = os.read(selected.fileobj.fileno(), 4096)
                if chunk:
                    output = (output + chunk.decode('utf-8', errors='replace'))[-OUTPUT_LIMIT:]
                    prompt = _safe_prompt(provider, output)
                    with _lock:
                        if session['state'] in _ACTIVE and prompt:
                            session['prompt'].update(prompt)
                            if not session.get('submitted'):
                                session['state'] = 'waiting'
                else:
                    selector.unregister(selected.fileobj)
            result = process.poll()
            if result is not None:
                break
        with _lock:
            if session['state'] not in _ACTIVE:
                return
            session.update(state='verifying', prompt={})
        verified = result == 0 and provider.signin_verified() is True
        with _lock:
            if session['state'] not in _ACTIVE:
                return
            if verified:
                provider.signin_complete()
                def success(row):
                    row.update(state='signed-in', since=None, checked_at=_iso(), connected_at=_iso(),
                               reason=_REASONS['signed-in'], authoritative=False)
                _change(key, success)
                scope = (str(board.home()), key)
                _revision[scope] = _revision.get(scope, 0) + 1
                _finish(session, 'done')
            else:
                _finish(session, 'failed')
    except Exception:
        # Native exceptions may contain credentials or identity data. Keep no text.
        with _lock:
            if session['state'] in _ACTIVE:
                _finish(session, 'failed')
    finally:
        output = ''
        selector.close()
        _stop_process(session)
        session.pop('process', None)


def start(key):
    """Reuse one active native login per provider; return before it waits for input."""
    provider = _provider(key)
    with _lock:
        current = _slot(key)
        if current is not None and current['state'] in _ACTIVE:
            return state(key)
        if not _eligible(key, provider):
            raise ValueError('Provider is not available for sign-in.')
        board.home().mkdir(parents=True, exist_ok=True)
        session = dict(id=uuid.uuid4().hex, state='starting', prompt={},
                       expires_at=_iso(time.time() + SESSION_SECONDS),
                       deadline=time.monotonic() + SESSION_SECONDS)
        _sessions[(str(board.home()), key)] = session
        worker = threading.Thread(target=_run, args=(key, provider, session),
                                  name='colony-signin-' + key, daemon=True)
        session['worker'] = worker
        worker.start()
        return state(key)


def _matching(key, session_id):
    _provider(key)
    session = _slot(key)
    if not session_id or session is None or session['id'] != session_id or session['state'] not in _ACTIVE:
        raise ValueError('Sign-in session is no longer active.')
    return session


def submit(key, session_id, code):
    with _lock:
        session = _matching(key, session_id)
        if (session.get('submitted') or not session['prompt'].get('accept_code')
                or session.get('process') is None):
            raise ValueError('This sign-in is not waiting for a code.')
        try:
            value = _provider(key).signin_input(code)
        except ValueError:
            raise ValueError('Enter a valid authorization code.') from None
        if not isinstance(value, str) or not value.endswith('\n') or '\n' in value[:-1] or '\r' in value:
            raise ValueError('Enter a single authorization code.')
        try:
            session['process'].stdin.write(value.encode())
            session['process'].stdin.flush()
        except OSError:
            _finish(session, 'failed')
            raise ValueError('Sign-in session is no longer active.') from None
        session.update(submitted=True, state='verifying', prompt={})
        return state(key)


def cancel(key, session_id):
    with _lock:
        session = _matching(key, session_id)
        _finish(session, 'cancelled')
    _stop_process(session)
    worker = session.get('worker')
    if worker is not None and worker is not threading.current_thread():
        worker.join(timeout=3)
    return state(key)


def _shutdown():
    with _lock:
        sessions = list(_sessions.values())
        for session in sessions:
            if session['state'] in _ACTIVE:
                _finish(session, 'cancelled')
    for session in sessions:
        _stop_process(session)


atexit.register(_shutdown)
