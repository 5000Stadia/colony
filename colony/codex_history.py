"""PROVIDER: Codex history paths, measured with 0.166.0 in the R127 sandbox.

Only stopped, colony-owned homes are changed. Conversation prose is historical;
workspace settings and the indexes used to find it follow the moved folder.
"""
from bisect import bisect_right
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import shutil
import tempfile

from . import codex_remote, codex_transfer


@contextmanager
def stopped(home):
    if codex_remote.alive(home):
        raise ValueError('its Codex host is still running; stop it before moving history')
    state = home / 'state_5.sqlite'
    ids = []
    if state.exists():
        with closing(sqlite3.connect(state.as_uri() + '?mode=ro', uri=True)) as db:
            ids = [row[0] for row in db.execute('SELECT id FROM threads')]
    with codex_transfer.writers(home, ids):
        yield


def _columns(db, table):
    return {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}


def _rollout(path, pairs):
    """Change runtime settings, retaining the original bytes of every other line.

    Paginated history stores byte offsets into this file; return their translation
    along with its new contents so an edit cannot silently shift a saved turn.
    """
    from .repoint import swap
    before, after, ends, shifts, out = 0, 0, [0], [0], []
    for line in path.read_bytes().splitlines(keepends=True):
        row = json.loads(line)
        new = line
        kind, payload = row.get('type'), row.get('payload', {})
        if kind in ('session_meta', 'turn_context', 'world_state') or (
                kind == 'event_msg' and payload.get('type') == 'thread_settings_applied'):
            changed = swap(payload, pairs)
            if changed != payload:
                row['payload'] = changed
                new = json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode() + (b'\n' if line.endswith(b'\n') else b'')
        before += len(line)
        after += len(new)
        ends.append(before)
        shifts.append(after - before)
        out.append(new)
    def offset(value):
        return value + shifts[bisect_right(ends, value) - 1] if value is not None else None
    return b''.join(out), offset


def _offsets(home, changed):
    path = home / 'thread_history_1.sqlite'
    if not changed or not path.exists():
        return
    with closing(sqlite3.connect(path)) as db, db:
        for table, names in (('thread_turns', ('rollout_byte_offset', 'rollout_end_byte_offset')),
                             ('thread_history_projection_state', ('next_rollout_byte_offset',))):
            columns = [name for name in names if name in _columns(db, table)]
            if not columns:
                continue
            for tid, offset in changed.items():
                for row in db.execute(f'SELECT rowid,{",".join(columns)} FROM {table} WHERE thread_id=?', (tid,)).fetchall():
                    values = [offset(value) for value in row[1:]]
                    if values != list(row[1:]):
                        db.execute(f'UPDATE {table} SET ' + ','.join(f'{c}=?' for c in columns) + ' WHERE rowid=?',
                                   [*values, row[0]])


@contextmanager
def restore_on_error(home, paths):
    """Keep a stopped home's originals until all history edits and its rename succeed."""
    paths = list(dict.fromkeys(path.resolve() for path in paths if path.exists()))
    with tempfile.TemporaryDirectory(prefix='.history-backup-', dir=home.parent) as folder:
        copies = []
        for index, path in enumerate(paths):
            if path.suffix == '.sqlite':
                with closing(sqlite3.connect(path)) as db:
                    if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                        raise ValueError('Codex history database is still busy')
            backup = Path(folder) / str(index)
            shutil.copy2(path, backup)
            copies.append((path, backup))
        try:
            yield
        except Exception:
            for path, backup in copies:
                if path.suffix == '.sqlite':
                    for suffix in ('-wal', '-shm'):
                        Path(str(path) + suffix).unlink(missing_ok=True)
                temp = path.with_name(path.name + '.restore')
                shutil.copy2(backup, temp)
                temp.replace(path)
            raise


def _rewrite(home, pairs, backup=None, then=None):
    """Every path recorded in one stopped home under an old prefix says the new one; True when any did.

    `then` runs last under the same restore, so its failure (repoint's rename) puts every record back.
    With `backup`, the first version of each record that may change is also kept there, as a change-over keeps
    everything it rewrites.
    """
    from .repoint import swap, rewrite, _write, backup_file
    changed = False
    rollouts = []
    offsets = {}
    for folder in ('sessions', 'archived_sessions'):
        for path in sorted((home / folder).rglob('*.jsonl')):
            data, offset = _rollout(path, pairs)
            if data != path.read_bytes():
                first = json.loads(data.splitlines()[0])
                tid = first.get('payload', {}).get('id')
                if tid:
                    offsets[tid] = offset
                rollouts.append((path, data))
    config = home / 'config.toml'
    config_value = codex_remote.user_config(home) if config.exists() else None
    records = [path for path in home.glob('colony-*.json') if not path.is_symlink()]
    originals = [path for path, _ in rollouts] + records + [config, home / 'state_5.sqlite', home / 'thread_history_1.sqlite']
    with restore_on_error(home, originals):
        for path in originals:
            backup_file(path, backup)
        state = home / 'state_5.sqlite'
        if state.exists():
            with closing(sqlite3.connect(state)) as db, db:
                # Do not rewrite titles, messages, timestamps, IDs, or unrelated tables.
                for table, names in (('threads', ('cwd', 'rollout_path', 'sandbox_policy')),
                                     ('project_roots', ('path',)),
                                     ('rollout_migration_skipped_rollouts', ('rollout_path',))):
                    columns = [name for name in names if name in _columns(db, table)]
                    if not columns:
                        continue
                    for row in db.execute(f'SELECT rowid,{",".join(columns)} FROM {table}').fetchall():
                        values = []
                        for column, value in zip(columns, row[1:]):
                            if column == 'sandbox_policy':
                                policy = json.loads(value)
                                moved = swap(policy, pairs)
                                values.append(json.dumps(moved) if moved != policy else value)
                            else:
                                values.append(swap(value, pairs))
                        if values != list(row[1:]):
                            db.execute(f'UPDATE {table} SET ' + ','.join(f'{c}=?' for c in columns) + ' WHERE rowid=?',
                                       [*values, row[0]])
                            changed = True
        for path, data in rollouts:
            _write(path.resolve(), data.decode())  # preserve a gathered history's symlink
        _offsets(home, offsets)
        changed = changed or bool(rollouts)
        if config.exists():
            moved = swap(config_value, pairs)
            if moved != config_value:
                _write(config, '\n'.join(json.dumps(k) + '=' + codex_remote.toml(v) for k, v in moved.items()) + '\n')
                changed = True
        for path in records:
            changed = rewrite(path, pairs) or changed
        if then is not None:
            changed = then() or changed
    return changed


def repoint(old, new):
    was, now = codex_remote.home_for(old), codex_remote.home_for(new)
    pairs = [(str(old), str(new)), (str(was), str(now))]
    home = was if was.exists() else now
    if not home.exists():
        return [], []
    if was != now and was.exists() and now.exists():
        raise ValueError('the destination already has a Codex home')
    if codex_remote.alive(now):
        raise ValueError('its Codex host is still running; stop it before moving history')

    def rename():
        if home == was and was != now:
            now.parent.mkdir(parents=True, exist_ok=True)
            was.rename(now)
            return True
        return False
    with stopped(home):
        changed = _rewrite(home, pairs, then=rename)
    return [(str(was), str(now))], ["its Codex history re-pointed"] if changed else []


def retarget(home, pairs, backup=None):
    """Every path recorded in the stopped home at `home` under an old prefix of `pairs` says the new one: what a whole
    colony unpacked under another home folder needs, where the project folders and the home itself moved together."""
    home = Path(home)
    if not home.is_dir():
        return False
    with stopped(home):
        return _rewrite(home, [(str(a), str(b)) for a, b in pairs], backup)


def rehome(home, old_home, backup=None):
    """The stopped home now at `home`, formerly at `old_home`, finds its own files again.

    Only paths under the old home change: the project's folder and the conversation's prose stay as they are, so
    a home already done is left as it is. True when anything changed.
    """
    home, old_home = Path(home), Path(old_home)
    if home == old_home or not home.is_dir():
        return False
    with stopped(home):
        return _rewrite(home, [(str(old_home), str(home))], backup)


def gather(root, dest):
    """Gather an owned home's history; local conversations must be transferred first.

    The layout caller chooses dest and arranges transfer through codex_transfer.
    No credentials, configuration, logs or remote enrollment go into this folder.
    """
    home, dest = codex_remote.home_for(root), Path(dest).resolve()
    if not home.is_dir():
        raise ValueError('Codex history needs a colony-owned home before gathering')
    names = ['sessions', 'archived_sessions', *codex_transfer.SCHEMAS]
    with stopped(home):
        for name in names:
            source, target = home / name, dest / name
            if source.is_symlink() and source.resolve() == target:
                continue
            if target.exists() or target.is_symlink() or source.is_symlink():
                raise ValueError(f'Codex history already exists elsewhere: {name}')
        # Finish WAL writes before moving any database, and close every connection.
        for name in codex_transfer.SCHEMAS:
            source = home / name
            if source.is_file():
                with closing(sqlite3.connect(source)) as db:
                    if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                        raise ValueError('Codex history database is still busy')
        dest.mkdir(parents=True, exist_ok=True)
        for name in names:
            source, target = home / name, dest / name
            if source.is_symlink() or not source.exists():
                continue
            source.rename(target)
            source.symlink_to(target, target_is_directory=target.is_dir())
    return dest


def verify(root):
    """The bookmarked thread exists in Codex's index and its rollout at this cwd."""
    from . import board, console
    saved = console.saved_conversation(root, 'codex')
    if not saved:
        return False
    home = codex_remote.home_for(root)
    state = home / 'state_5.sqlite'
    if not state.is_file():
        return False
    try:
        with closing(sqlite3.connect(state.as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute('SELECT cwd,rollout_path FROM threads WHERE id=? AND archived=0', (saved['conversation'],)).fetchone()
        if not row or Path(row[0]) != board.workdir(root):
            return False
        with Path(row[1]).open() as file:
            meta = json.loads(file.readline())
        payload = meta.get('payload') if isinstance(meta, dict) else None
        return (isinstance(payload, dict) and meta.get('type') == 'session_meta'
                and payload.get('id') == saved['conversation'] and payload.get('cwd') == row[0]
                and Path(saved['transcript']).resolve() == Path(row[1]).resolve())
    except (OSError, ValueError, sqlite3.Error):
        return False
