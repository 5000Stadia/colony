"""PROVIDER: project-bound Codex app servers, shared by the console and ChatGPT.

Only launch() changes lifecycle. Rendering commands, fingerprints and settings
does not start a daemon. SIGHUP drains admitted turns and never forces shutdown.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
import time

from .codex_rpc import Client, RPCError
from .codex_transfer import TransferError, atomic_json, transfer


class RemoteError(RuntimeError):
    pass


_processes = {}                    # reap daemons started by this process after a graceful drain


def home_for(root):
    from . import board
    key = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:12]
    return board.home() / 'codex-remote' / key


def socket_for(home):
    return Path(home) / 'app-server-control' / 'app-server-control.sock'


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def process_start(pid):
    try:
        # Linux start time also protects against signalling a recycled PID.
        fields = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (OSError, ValueError, IndexError):
        return None


def alive(home):
    record = read_json(Path(home) / 'colony-daemon.json')
    process = _processes.get(record.get('pid'))
    if process is not None and process.poll() is not None:
        _processes.pop(record['pid'], None)
        return False
    return bool(record.get('pid') and record.get('start') and process_start(record['pid']) == record['start'])


def status(root):
    home = home_for(root)
    state = read_json(home / 'colony-status.json')
    return dict(state, running=alive(home), home=str(home))


def statuses():
    from . import board
    result = []
    for home in sorted((board.home() / 'codex-remote').glob('*')):
        if not home.is_dir():
            continue
        value = dict(read_json(home / 'colony-status.json'), running=alive(home), home=str(home))
        value.setdefault('project', read_json(home / 'colony-daemon.json').get('project', str(home)))
        if value['running'] and value.get('mode') != 'pending':
            try:
                with Client(socket_for(home), timeout=1) as client:
                    remote = client.call('remoteControl/status/read')
                value['mode'] = remote['status']
            except (OSError, RPCError):
                value['mode'] = 'unreachable'
        result.append(value)
    return result


def report(home, root, mode, reason='', **extra):
    atomic_json(Path(home) / 'colony-status.json', dict(project=str(root), mode=mode, reason=reason,
                                                      at=time.time(), **extra))


def environment(root, home, source):
    from . import board, console
    env = {k: v for k, v in os.environ.items() if not k.startswith('COLONY_')}
    env.update(CODEX_HOME=str(home), COLONY_CODEX_SOURCE_HOME=str(source),
               COLONY_PROJECT=str(Path(root).resolve()), COLONY_CONSOLE=console.session_name(root),
               COLONY_BOARD_HOME=str(board.home()))
    # Remote enrollment uses the person's ChatGPT sign-in, not an unrelated API key.
    env.pop('OPENAI_API_KEY', None)
    env.pop('CODEX_API_KEY', None)
    return env


def toml(value):
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ','.join(toml(v) for v in value) + ']'
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return json.dumps(value, ensure_ascii=False)


def user_config(source):
    import tomllib
    path = Path(source) / 'config.toml'
    return tomllib.loads(path.read_text()) if path.exists() else {}


def prepare_home(root, home, source):
    """Keep credentials shared; do not clone refresh tokens into multiple homes."""
    config = user_config(source)
    if config.get('cli_auth_credentials_store', 'file') not in ('file', 'auto') or not (source / 'auth.json').is_file():
        raise RemoteError('ChatGPT file sign-in is needed for a project remote host; sign in with Codex first')
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home, 0o700)
    for name in ('auth.json', 'skills', 'plugins', 'rules', 'agents', 'models_cache.json', 'AGENTS.md', 'AGENTS.override.md'):
        origin, target = source / name, home / name
        if origin.exists():
            if target.is_symlink():
                if target.resolve() != origin.resolve():
                    raise RemoteError('Owned Codex home has an unexpected shared-file target')
            elif target.exists():
                raise RemoteError('Owned Codex home contains an independent sign-in or shared configuration')
            else:
                target.symlink_to(origin, target_is_directory=origin.is_dir())
    config['cli_auth_credentials_store'] = 'file'
    config['sqlite_home'] = str(home)
    # Paths in a user config are normally relative to the original config directory.
    def absolute_paths(value):
        if isinstance(value, dict):
            return {k: str(source / v) if k in ('config_file', 'model_instructions_file', 'model_catalog_json')
                    and isinstance(v, str) and not Path(v).is_absolute() else absolute_paths(v) for k, v in value.items()}
        if isinstance(value, list):
            return [absolute_paths(v) for v in value]
        return value
    config = absolute_paths(config)
    prior = user_config(home)
    trust = prior.get('hooks', {}).get('state')
    if trust:
        config.setdefault('hooks', {}).setdefault('state', {}).update(trust)
    text = '\n'.join(json.dumps(k) + '=' + toml(v) for k, v in config.items()) + '\n'
    path = home / 'config.toml'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as file:
        file.write(text)


def overrides(root, home, settings):
    from . import board, bench, providers
    provider = providers.get('codex')
    config = {'features.hooks': True, 'check_for_update_on_startup': False, 'sqlite_home': str(home)}
    for key, setting in (('model', 'model'), ('model_reasoning_effort', 'effort')):
        if settings.get(setting):
            config[key] = settings[setting]
    permission = settings.get('permissions') or 'ask'
    config['approval_policy'] = 'never' if permission in ('all', 'edits') else 'on-request'
    config['sandbox_mode'] = {'all': 'danger-full-access', 'plan': 'read-only'}.get(permission, 'workspace-write')
    config['sandbox_workspace_write.writable_roots'] = [str(board.home())]
    for event, command in provider.hooks.items():
        config['hooks.' + event] = [{'hooks': [{'type': 'command', 'command': command}]}]
    hook = shlex.join([sys.executable, '-m', 'colony.codex_remote', 'hook'])
    for event in ('PreToolUse', 'PostToolUse'):
        config['hooks.' + event] = [{'matcher': '.*request_user_input.*', 'hooks': [{'type': 'command', 'command': hook}]}]
    tiers = bench.effective(root)
    # Immutable helper generations: preparing a pending launch cannot change the
    # overlays still used by an active app turn.
    pairs = {k: {f: v.get(f) for f in ('model', 'effort')} for k, v in tiers.items()}
    generation = hashlib.sha256(json.dumps(pairs, sort_keys=True).encode()).hexdigest()[:12]
    helper_root = home / 'colony-helpers' / generation
    provider.write_helpers(helper_root, tiers)
    for tier in tiers:
        name = provider.helper_name(tier)
        overlay = helper_root / '.codex' / 'agents' / (name + '.toml')
        config[f'agents.{name}.description'] = provider.HELPER_BRIEF[tier]
        config[f'agents.{name}.config_file'] = str(overlay)
    return config


def console_config(config):
    """What the console attached with --remote may be given: Codex 0.159.3 refuses workspace-root overrides there
    ("configure additional workspace roots on the server"), and the server already has them from the same config."""
    return {k: v for k, v in config.items() if not k.startswith('sandbox_workspace_write.')}


def flags(config):
    return [arg for k, v in config.items() for arg in ('-c', k + '=' + toml(v))]


def idle(client):
    cursor = None
    while True:
        result = client.call('thread/loaded/list', dict(cursor=cursor, limit=100))
        for tid in result['data']:
            thread = client.call('thread/read', {'threadId': tid, 'includeTurns': False})['thread']
            if thread['status']['type'] not in ('idle', 'notLoaded'):
                return False
        cursor = result.get('nextCursor')
        if not cursor:
            return True


def drain(home, timeout=15):
    """One graceful-only signal: a turn racing the idle check is allowed to finish."""
    if not alive(home):
        return True
    record = read_json(home / 'colony-daemon.json')
    with Client(socket_for(home)) as client:
        if not idle(client):
            return False
    if not alive(home):
        return True
    os.kill(record['pid'], signal.SIGHUP)
    deadline = time.monotonic() + timeout
    while alive(home) and time.monotonic() < deadline:
        time.sleep(.1)
    return not alive(home)


def start(root, home, source, config):
    from . import board
    command = ['codex', 'app-server', '--listen', 'unix://' + str(socket_for(home)), '--remote-control', *flags(config)]
    log = os.open(home / 'colony-daemon.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        process = subprocess.Popen(command, cwd=board.workdir(root), env=environment(root, home, source),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        _processes[process.pid] = process
    finally:
        os.close(log)
    atomic_json(home / 'colony-daemon.json', dict(pid=process.pid, start=process_start(process.pid),
                                                 fingerprint=fingerprint(config, source), project=str(root)))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RemoteError('Codex remote host did not start; see its project daemon log')
        if socket_for(home).exists():
            try:
                return Client(socket_for(home))
            except (RPCError, OSError):
                pass
        time.sleep(.1)
    raise RemoteError('Codex remote host is still starting; no second host was started')


def fingerprint(config, source):
    from . import providers
    content = json.dumps(config, sort_keys=True) + (providers.get('codex').version() or '')
    path = source / 'config.toml'
    if path.exists():
        content += path.read_text()
    return hashlib.sha256(content.encode()).hexdigest()


def trust_hooks(client, root, config):
    from . import board
    commands = {h['command'] for k, entries in config.items() if k.startswith('hooks.')
                for entry in entries for h in entry['hooks']}
    result = client.call('hooks/list', {'cwds': [str(board.workdir(root))]})
    hooks = [h for entry in result['data'] for h in entry['hooks'] if h.get('command') in commands]
    if len(hooks) != 5 or any(not h['enabled'] for h in hooks):
        raise RemoteError('Codex did not load all five project hooks')
    updates = {h['key']: {'trusted_hash': h['currentHash']} for h in hooks if h['trustStatus'] != 'trusted'}
    if updates:
        if not board.registry()['settings']['trust']:
            raise RemoteError('Review the project hooks in /hooks before using app access')
        client.call('config/batchWrite', {'edits': [{'keyPath': 'hooks.state', 'value': updates, 'mergeStrategy': 'upsert'}],
                                         'reloadUserConfig': True})


def attach(client, root, config, resume):
    from . import board, console
    params = dict(cwd=str(board.workdir(root)), model=config.get('model'),
                  approvalPolicy=config['approval_policy'], sandbox=config['sandbox_mode'],
                  config={'model_reasoning_effort': config.get('model_reasoning_effort')})
    if resume:
        params.update(threadId=resume, excludeTurns=True)
    result = client.call('thread/resume' if resume else 'thread/start', params)
    if (result['model'] != config.get('model') or result.get('reasoningEffort') != config.get('model_reasoning_effort')
            or result['approvalPolicy'] != config['approval_policy']
            or result['sandbox']['type'] != {'danger-full-access': 'dangerFullAccess', 'read-only': 'readOnly',
                                             'workspace-write': 'workspaceWrite'}[config['sandbox_mode']]):
        raise RemoteError('Codex returned different session settings; attachment stopped')
    thread = result['thread']
    atomic_json(home_for(root) / 'colony-thread.json', {'thread': thread['id']})
    client.call('thread/name/set', {'threadId': thread['id'], 'name': Path(root).name})
    if thread.get('path'):
        console.remember(root, thread['id'], thread['path'])
    return thread['id']


def launch(root, settings, resume=None):
    from . import providers
    provider = providers.get('codex')
    source = Path(os.environ.get('COLONY_CODEX_SOURCE_HOME') or provider.config_home()).resolve()
    home = home_for(root)
    owned = read_json(home / 'colony-transfer.json').get('state') == 'published' or (home / 'colony-owned').exists()
    remote = settings.get('remote', True)
    if not sys.platform.startswith('linux') or not re.search(r'\b0\.159\.3\b', provider.version() or ''):
        report(home, root, 'local', 'Remote integration requires Codex 0.159.3 on Linux')
        return local(root, settings, resume, home if owned else source)
    if not remote and not alive(home):
        report(home, root, 'off')
        return local(root, settings, resume, home if owned else source)
    try:
        if not remote:
            if not drain(home):
                raise RemoteError('Remote off is pending until the running app turn finishes')
            report(home, root, 'off')
            return local(root, settings, resume, home if owned else source)
        config = overrides(root, home, settings)
        if alive(home):
            previous = read_json(home / 'colony-daemon.json')
            if previous.get('fingerprint') != fingerprint(config, source):
                if not drain(home):
                    raise RemoteError('New settings are pending until the app turn finishes')
        if not alive(home):
            prepare_home(root, home, source)
            if resume and not owned:
                transfer(source, home, resume)
                owned = True
            with start(root, home, source, config) as client:
                trust_hooks(client, root, config)
                resume = attach(client, root, config, resume)
        else:
            with Client(socket_for(home)) as client:
                trust_hooks(client, root, config)
                resume = attach(client, root, config, resume)
        (home / 'colony-owned').touch(mode=0o600)
        owned = True
        with Client(socket_for(home)) as client:
            remote_state = client.call('remoteControl/status/read')
        if remote_state['status'] in ('disabled', 'errored'):
            raise RemoteError('ChatGPT remote enrollment is unavailable; the conversation remains local')
        mode = 'connected' if remote_state['status'] == 'connected' else 'connecting'
        report(home, root, mode, server=remote_state.get('serverName'), thread=resume)
        args = ['codex', '--remote', 'unix://' + str(socket_for(home)), '--no-alt-screen',
                *flags(console_config(config)), 'resume', resume]
        os.execvpe(args[0], args, environment(root, home, source))
    except (RemoteError, RPCError, TransferError, OSError, ValueError, KeyError, sqlite3.Error, ImportError) as error:
        reason = str(error)
        report(home, root, 'pending' if alive(home) else 'local', reason, thread=resume)
        print('Colony: ' + reason, file=sys.stderr)
        if alive(home):
            # A live authoritative daemon must not acquire a competing local writer.
            try:
                stopped = drain(home)
            except (RPCError, OSError):
                stopped = False
            if not stopped:
                print('The app conversation is still running. Reopen this console when it finishes.', file=sys.stderr)
                return 1
        owned = owned or read_json(home / 'colony-transfer.json').get('state') == 'published'
        return local(root, settings, resume, home if owned else source)


def local(root, settings, resume, home):
    from . import providers
    provider = providers.get('codex')
    args = shlex.split(provider.local_command(Path(root).name, settings, resume, root))
    source = Path(os.environ.get('COLONY_CODEX_SOURCE_HOME') or provider.config_home()).resolve()
    if home == home_for(root):
        # Same persistent hook/helper configuration in embedded mode after remote off.
        extra = flags(overrides(root, home, settings))
        index = args.index('resume') if 'resume' in args else len(args)
        args[index:index] = extra
    help_text = subprocess.run(['codex', '--help'], capture_output=True, text=True).stdout
    if '--no-daemon' in help_text:
        args.insert(1, '--no-daemon')
    env = environment(root, home, source)
    os.execvpe(args[0], args, env)


def hook():
    from . import board
    payload = json.load(sys.stdin)
    root = board.root_of()
    if not accepts_hook(root, payload):
        print('{}')
        return
    if 'request_user_input' in payload.get('tool_name', ''):
        args = payload.get('tool_input') or {}
        if isinstance(args, str):
            args = json.loads(args)
        text = '\n'.join(q.get('question') or q.get('title') or '' for q in args.get('questions', []))
        if payload.get('hook_event_name') == 'PreToolUse' and text:
            board.record_ask(root, payload.get('tool_use_id') or payload.get('turn_id'), text, explicit=True)
        elif payload.get('hook_event_name') == 'PostToolUse':
            response = payload.get('tool_response') or {}
            if isinstance(response, str):
                try:
                    response = json.loads(response)
                except ValueError:
                    response = {}
            if response.get('answers'):
                board.said(root, json.dumps(response['answers'], ensure_ascii=False))
                board.answer_asks(root, 'in the ChatGPT app or console')
    print('{}')


def accepts_hook(root, payload):
    """A helper's session must not replace the project's authoritative conversation."""
    from . import console
    if os.environ.get('COLONY_CONSOLE') != console.session_name(root):
        return False
    expected = read_json(home_for(root) / 'colony-thread.json').get('thread')
    return not expected or payload.get('session_id') == expected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['launch', 'hook'])
    parser.add_argument('--project')
    parser.add_argument('--settings')
    parser.add_argument('--resume')
    args = parser.parse_args()
    if args.action == 'hook':
        return hook()
    import fcntl
    home = home_for(args.project)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (home / 'colony-launch.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return launch(Path(args.project), json.loads(args.settings), args.resume)


if __name__ == '__main__':
    sys.exit(main())
