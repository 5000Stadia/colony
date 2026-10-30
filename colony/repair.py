"""Installation repair access. Reads never enroll, pair, or submit work.

The explicitly selected provider owns its native host. It lives outside project
hosts so removing projects, restarting the board, and steward settings do not
remove the owner's way back into this installation.
"""
import fcntl
import json
import re
import threading
import time

from . import board, providers

_INTERVAL = 60
_guard = threading.Lock()
_worker = None
_last = 0.0
_MESSAGES = {
    'off': 'Repair access is off.',
    'starting': 'Starting the repair connection.',
    'connecting': 'Connecting the repair host. Colony will retry automatically.',
    'unpaired': 'The repair host is connected. Pair it with your ChatGPT app.',
    'ready': 'The repair connection is ready in your paired app.',
    'offline': 'The repair host could not connect. Check Codex sign-in and connectivity; Colony will retry.',
    'needs-signin': 'Sign in with Codex before enabling repair access.',
    'unavailable': 'This provider or installed version cannot provide repair access.',
    'stopping': 'Repair access is turning off after active work finishes.',
}


def _home(key):
    return board.home() / 'repair' / key


def _cache():
    return board.home() / 'repair' / 'state.json'


def _read():
    try:
        value = json.loads(_cache().read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def available():
    """Installed, enabled providers that supply a native repair host; no native probes."""
    return [key for key, p in providers.PROVIDERS.items()
            if providers.supplies(p, 'repair_start') and providers.usable(p)]


def _chosen():
    return board.registry()['settings'].get('repair_provider') or ''


def _server(value):
    return re.sub(r'[\x00-\x1f\x7f]', '', value)[:120] if isinstance(value, str) else ''


def _record(key, status, enabled=True):
    name = status.get('state', 'offline')
    if name not in _MESSAGES:
        name = 'offline'
    value = dict(enabled=enabled, provider=key, state=name, message=_MESSAGES[name],
                 paired=name == 'ready' and status.get('paired') is True,
                 server=_server(status.get('server')), checked_at=time.time())
    board.save_json(_cache(), value)
    return value


def state():
    """Safe cached status only: never contact a native process or generate a pairing code."""
    key, cached = _chosen(), _read()
    if cached.get('provider') != key or bool(cached.get('enabled')) != bool(key):
        name = 'starting' if key else 'off'
        return dict(enabled=bool(key), provider=key, state=name, message=_MESSAGES[name],
                    paired=False, server='', checked_at=None)
    name = cached.get('state', 'offline')
    if name not in _MESSAGES:
        name = 'offline'
    return dict(enabled=bool(key), provider=key, state=name, message=_MESSAGES[name],
                paired=name == 'ready' and cached.get('paired') is True,
                server=_server(cached.get('server')),
                checked_at=cached.get('checked_at') if isinstance(cached.get('checked_at'), (int, float)) else None)


def enable(key):
    if key not in available():
        raise ValueError('Choose an installed, enabled provider that supports repair access.')
    board.set_setting('repair_provider', key)
    tick(force=True)
    return state()


def disable():
    board.set_setting('repair_provider', '')
    tick(force=True)
    return state()


def keeps():
    """Owned live process IDs, including hosts finishing work after access was disabled."""
    result = set()
    for key, p in providers.PROVIDERS.items():
        if providers.supplies(p, 'repair_start'):
            result.update(p.repair_keeps(_home(key)))
    return result


def _refresh(force=False):
    folder = _cache().parent
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'host.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        old, key = _read(), _chosen()
        # The disk timestamp also bounds retries across watcher/CLI processes.
        if not force and time.time() - (old.get('checked_at') or 0) < _INTERVAL:
            return
        waiting = False
        for other, p in providers.PROVIDERS.items():
            if other != key and providers.supplies(p, 'repair_start') and p.repair_keeps(_home(other)):
                try:
                    waiting = not p.repair_stop(_home(other)) or waiting
                except Exception:
                    waiting = True
        if not key:
            _record('', {'state': 'stopping' if waiting else 'off'}, enabled=False)
            return
        if key not in available():
            _record(key, {'state': 'unavailable'})
            return
        try:
            status = providers.get(key).repair_start(_home(key), board.registry()['settings'])
        except Exception:
            # Provider errors may contain response bodies, private paths or credentials.
            status = {'state': 'offline'}
        # Settings may have changed while a bounded enrollment request was running.
        if _chosen() == key:
            _record(key, status)


def tick(force=False):
    """Schedule bounded, deduplicated maintenance; a disabled installation has no native probes."""
    global _worker, _last
    with _guard:
        if _worker is not None and _worker.is_alive():
            return
        if not force and time.monotonic() - _last < _INTERVAL:
            return
        if not _chosen() and not keeps():
            return
        _last = time.monotonic()
        _worker = threading.Thread(target=_refresh, kwargs={'force': force}, name='colony-repair', daemon=True)
        _worker.start()


def pair(manual_code=None):
    """An explicit pairing action against the existing host. Codes never enter the status cache."""
    key = _chosen()
    if key not in providers.PROVIDERS or not providers.supplies(providers.get(key), 'repair_start'):
        raise ValueError('Enable repair access before pairing it.')
    try:
        result = providers.get(key).repair_pair(_home(key), manual_code)
    except Exception:
        raise RuntimeError('Could not reach repair pairing. Check Codex sign-in and its connection, then try again.') from None
    if manual_code is not None:
        tick(force=True)
        return {'claimed': result.get('claimed') is True}
    return {k: result[k] for k in ('manualPairingCode', 'expiresAt')}
