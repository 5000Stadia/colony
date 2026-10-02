"""One declared project, one canonical plan and one switchable lead.

Agent conversations/settings remain private to their logical roots. Shared execution
records live beside the board registry, never in a branch's .board directory.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path
import secrets
import shlex
import subprocess

from . import board
from .codex_transfer import atomic_json


def location():
    return board.home() / 'leads.json'


def records():
    try:
        return json.loads(location().read_text())
    except FileNotFoundError:
        return {}


@contextmanager
def locked():
    location().parent.mkdir(parents=True, exist_ok=True)
    with location().with_suffix('.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        data = records()
        yield data
        atomic_json(location(), data)


def identity(root):
    return hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16]


def group(root):
    root = str(Path(root).resolve())
    return next((deepcopy(g) for g in records().values() if root in g['members']), None)


def info(root):
    root = str(Path(root).resolve())
    return group(root) or dict(id=identity(root), canonical=root, members=[root], lead=root,
                              generation=0, owners={}, assignments={}, checkpoints=[], goals={}, paused=False)


def plan_root(root):
    return Path(info(root)['canonical'])


def plan_path(root):
    return plan_root(root) / 'ROADMAP.md'


def workspaces(root, *, active=True):
    g = info(root)
    mine = str(Path(root).resolve())
    return [Path(a['workspace']) for a in g['assignments'].values()
            if a['owner'] == mine and (not active or a['state'] in ('working', 'handback', 'testing', 'deploying'))]


@contextmanager
def operation_lock(root, kind):
    """No model or wake-up is needed while another item holds integration."""
    g = info(root)
    if len(g['members']) == 1:
        yield
        return
    path = board.home() / 'locks' / (g['id'] + '-' + kind + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def commit_plan(root, message, *, actor=None, source=None, before_revision=None, expected_revision=None):
    """Commit the canonical plan by itself, even when the lead codes elsewhere."""
    g = require_lead(root, actor)
    canonical = Path(g['canonical'])
    git(canonical, 'add', '--', 'ROADMAP.md')
    git(canonical, 'commit', '--only', '-m', message,
        '--trailer', 'Colony-Agent: ' + Path(g['lead']).name, '--', 'ROADMAP.md')
    commit = git(canonical, 'rev-parse', 'HEAD')
    # A stamped commit alone is not proof the agent knew the previous content:
    # an outside edit may already have been present before its own mutation.
    if (source is not None and str(Path(source).resolve()) == g['lead'] and before_revision
            and expected_revision == revision(root)
            and git(canonical, 'hash-object', 'ROADMAP.md') == git(canonical, 'rev-parse', commit + ':ROADMAP.md')):
        def credit(g):
            values = [g.get('engagements', {}).get(g['lead'], {})]
            values += [a for a in g['assignments'].values() if a['owner'] == g['lead']]
            for value in values:
                if value.get('looked_revision') == before_revision:
                    value['looked_revision'] = expected_revision
        update(root, credit, generation=g['generation'])
    return commit


def revision(root):
    path = plan_path(root)
    return hashlib.sha256(path.read_bytes() if path.exists() else b'').hexdigest()


def update(root, change, *, generation=None):
    with locked() as data:
        value = info_from(data, root)
        if generation is not None and value['generation'] != generation:
            raise ValueError('The project lead changed; read the current assignment before acting.')
        change(value)
        data[value['id']] = value
        return deepcopy(value)


def info_from(data, root):
    root = str(Path(root).resolve())
    value = next((g for g in data.values() if root in g['members']), None)
    return value if value is not None else dict(id=identity(root), canonical=root, members=[root], lead=root,
        generation=0, owners={}, assignments={}, checkpoints=[], goals={}, paused=False)


def require_lead(root, actor):
    g = info(root)
    if actor is not None and str(Path(actor).resolve()) != g['lead']:
        raise ValueError(f"Only {Path(g['lead']).name}, the lead, changes this project's plan or assignments. Propose the change to them.")
    if g.get('handoff'):
        raise ValueError('The lead handoff is still landing; assignments wait until it finishes.')
    return g


def notify(g, text, *, quiet=True, exclude=()):
    for member in g['members']:
        if member not in exclude and Path(member).exists():
            board.add_note(Path(member), None, text, author='colony', quiet=quiet)


def pair(root, member, *, actor=None):
    root, member = Path(root).resolve(), Path(member).resolve()
    require_lead(root, actor)
    if member == root:
        return info(root)
    registered = {p.resolve() for p in board.projects()}
    if root not in registered or member not in registered:
        raise ValueError('Add both agent consoles to this board before pairing them.')
    other = group(member)
    if other and root.as_posix() not in other['members']:
        raise ValueError('That agent already belongs to another declared project.')
    current = info(root)
    source = Path(current.get('integration_root') or board.workdir(Path(current['canonical'])))
    fallback = current.get('integrated') or git(source, 'rev-parse', 'HEAD', check=False)
    completed = [i['id'] for i in board.items(board.roadmap(root)).values() if i['state'] in ('done', 'verify')]
    baselines = {m: dict(looked_at=git(board.workdir(Path(m)), 'rev-parse', 'HEAD', check=False) or fallback,
                         looked_items=completed, looked_revision=revision(root))
                 for m in set(current['members'] + [str(member)])}
    def add(g):
        if str(member) not in g['members']:
            g['members'].append(str(member))
        g.setdefault('plan_revision', revision(root))
        g.setdefault('integration_root', str(board.workdir(root)))
        for seat, baseline in baselines.items():
            g.setdefault('engagements', {}).setdefault(seat, baseline)
    g = update(root, add)
    notify(g, f"One shared project: {Path(g['lead']).name} is the lead. Read the single canonical Vision and roadmap at "
              f"{plan_path(root)}; do not copy it or use the roadmap in your branch. Other agents work only on assigned items.")
    return g


def owner(root, item):
    g = info(root)
    return Path(g['owners'].get(item) or g['lead'])


def assign(root, item, member=None, *, actor=None):
    g = require_lead(root, actor)
    if item not in board.items(board.roadmap(root)):
        raise ValueError(f'No roadmap item {item}.')
    if member and str(Path(member).resolve()) not in g['members']:
        raise ValueError('An item owner must be a member of this shared project.')
    current = g['assignments'].get(item, {})
    if current.get('state') in ('working', 'handback', 'testing', 'deploying'):
        raise ValueError('Land or integrate the existing assignment before changing its owner.')
    def change(g):
        if member:
            g['owners'][item] = str(Path(member).resolve())
        else:
            g['owners'].pop(item, None)
    value = update(root, change, generation=g['generation'])
    notify(value, f"{item} primary: {owner(root, item).name}. Read {plan_path(root)} at item start; "
                  'sync the item workspace before starting and before handback.')
    return value


def switch(root, member, *, words=''):
    member = str(Path(member).resolve())
    def change(g):
        if member not in g['members']:
            raise ValueError('Choose a declared member as the new lead.')
        if g.get('handoff'):
            raise ValueError('Finish the current lead handoff before requesting another.')
        if member == g['lead']:
            return
        g['generation'] += 1
        g['handoff'] = dict(outgoing=g['lead'], incoming=member, generation=g['generation'],
                            at=board.now(), words=words, state='landing')
    g = update(root, change)
    if g.get('handoff'):
        notify(g, f"The person switches the lead from {Path(g['lead']).name} to {Path(member).name}. "
                  'Outgoing lead: finish and commit work in flight, and start no new item. '
                  'The incoming lead waits for that turn to land. The canonical plan and checkpoints stay at '
                  f"{plan_path(root)}. The agent completing an item integrates, tests and delivers it under the shared lock.", quiet=False)
    return g


def finish_handoff(root, generation):
    """Called only after continuation verifies pause, idle and clean tracked work."""
    def change(g):
        handoff = g.get('handoff')
        if not handoff or handoff['generation'] != generation:
            raise ValueError('This handoff is no longer current.')
        g['lead'] = handoff['incoming']
        for item, value in g['assignments'].items():
            if (item not in g['owners'] and value['owner'] == handoff['outgoing']
                    and value['state'] in ('working', 'handback', 'testing', 'deploying')):
                value.update(owner=handoff['incoming'], generation=g['generation'],
                             previous_owner=handoff['outgoing'])
                seen = g.get('engagements', {}).get(handoff['incoming'], {})
                value.update(looked_at=seen.get('looked_at') or value['base'],
                             looked_items=seen.get('looked_items', []),
                             looked_revision=seen.get('looked_revision', value['plan_revision']))
                value.pop('pending_look', None)
        g['last_handoff'] = dict(handoff, state='landed', landed_at=board.now())
        g.pop('handoff')
    g = update(root, change, generation=generation)
    notify(g, f"{Path(g['lead']).name} is now the project's only lead. Read the current Vision, roadmap, "
              f"notes, gates and unfinished work at {plan_path(root)}. Continue only the current agreed checkpoint; "
              'keep explicit item owners. The completing agent integrates, tests and delivers its item.', quiet=False)
    return g


def linked(root, first, second):
    items = board.items(board.roadmap(root))
    def ancestors(item, seen=None):
        seen = set() if seen is None else seen
        for previous in items.get(item, {}).get('after', []):
            if previous not in seen:
                seen.add(previous)
                ancestors(previous, seen)
        return seen
    return first == second or first in ancestors(second) or second in ancestors(first)


def git(root, *args, check=True):
    result = subprocess.run(['git', '-C', str(root), *args], text=True, capture_output=True)
    if check and result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or 'Git could not complete this operation.')
    return result.stdout.strip()


def clean(root):
    return not git(root, 'status', '--porcelain', '--untracked-files=no', check=False)


def tested(root, commit, checks, *, actor=None):
    g = require_lead(root, actor)
    source = Path(g.get('integration_root') or board.workdir(Path(g['canonical'])))
    commit = git(source, 'rev-parse', commit + '^{commit}')
    if commit != git(source, 'rev-parse', 'HEAD'):
        raise ValueError('Record the current completed integration commit, not another branch or revision.')
    if not checks.strip() or not clean(source):
        raise ValueError('Record the checks and land tracked work before recording the tested integration.')
    def change(g):
        g['integrated'] = commit
        g['integrated_checks'] = checks.strip()
        g['integrated_at'] = board.now()
        g['completed_items'] = [i['id'] for i in board.items(board.roadmap(root)).values() if i['state'] in ('done', 'verify')]
    return update(root, change, generation=g['generation'])


def start_item(root, item, *, actor=None):
    g = info(root)
    if g.get('handoff') or g.get('paused'):
        raise ValueError('This project is paused or handing over its lead.')
    responsible = str(owner(root, item))
    if actor is not None and str(Path(actor).resolve()) not in (responsible, g['lead']):
        raise ValueError('Start only your assigned item.')
    items = board.items(board.roadmap(root))
    if item not in items or items[item]['state'] == 'done':
        raise ValueError('Choose an unfinished roadmap item.')
    old = g['assignments'].get(item)
    if old and old.get('state') == 'working':
        return sync_item(root, item, actor=actor)
    for iid, active in g['assignments'].items():
        if active['state'] in ('working', 'handback', 'testing', 'deploying') and (active['owner'] == responsible or linked(root, item, iid)):
            raise ValueError(f'{iid} must land first; one item per helper and linked items run in sequence.')
    for previous in items[item]['after']:
        if items.get(previous, {}).get('state') not in (None, 'done', 'verify'):
            raise ValueError(f'{previous} must be built before {item} starts.')
    source = Path(g.get('integration_root') or board.workdir(Path(g['canonical'])))
    head = git(source, 'rev-parse', 'HEAD', check=False)
    base = g.get('integrated', '')
    if head and responsible != g['lead'] and not base:
        raise ValueError('Record the last completed and tested integration before engaging a helper.')
    base = base or head
    workspace = board.workdir(Path(responsible))
    branch = None
    if responsible != g['lead'] and base:
        workspace = board.home() / 'items' / g['id'] / (item + '-' + str(g['generation']))
        branch = 'colony-item/' + g['id'] + '/' + item + '-' + str(g['generation'])
        if workspace.exists():
            if not old or old['state'] != 'integrated' or not clean(workspace):
                raise ValueError('The previous item workspace still exists; inspect and land it before reusing this item.')
            base = old['base']
        else:
            workspace.parent.mkdir(parents=True, exist_ok=True)
            git(source, 'worktree', 'add', '-b', branch, str(workspace), base)
            # Helpers have no working-file copy of the roadmap or its Vision.
            git(workspace, 'sparse-checkout', 'set', '--no-cone', '/*', '!/ROADMAP.md')
    elif responsible != g['lead']:
        workspace = source
        if any(a['state'] in ('working', 'handback') for a in g['assignments'].values()):
            raise ValueError('A shared non-git workspace runs one item at a time.')
    assignment = dict(item=item, owner=responsible, workspace=str(workspace), branch=branch, base=base,
                      plan_revision=revision(root), generation=g['generation'], state='working', at=board.now())
    seen = g.get('engagements', {}).get(responsible, {})
    assignment.update(looked_at=seen.get('looked_at') or base, looked_items=seen.get('looked_items', []),
                      looked_revision=seen.get('looked_revision', revision(root)))
    def register(g):
        for iid, active in g['assignments'].items():
            if active['state'] in ('working', 'handback', 'testing', 'deploying') and (active['owner'] == responsible or linked(root, item, iid)):
                raise ValueError('Another linked assignment started; land it before this one.')
        g['assignments'][item] = assignment
        g.setdefault('development', []).append(dict(item=item, owner=responsible, at=board.now(),
                                                   generation=g['generation']))
    update(root, register, generation=g['generation'])
    # Instructions/hooks stay at the logical agent's root; generated provider
    # configuration must never become an item change in the integration branch.
    board.add_note(Path(responsible), {'item': item}, f"You own only {item}, in {workspace}. Read the canonical plan at "
                   f"{plan_path(root)}. Colony syncs when you are engaged, to the last tested integration. "
                   f"Propose plan changes to {Path(g['lead']).name}.", author='colony')
    return sync_item(root, item, actor=actor)


def assignment(root, item, actor=None):
    g = info(root)
    value = g['assignments'].get(item)
    if not value:
        raise ValueError('Start this assigned item first.')
    if actor is not None and str(Path(actor).resolve()) not in (value['owner'], g['lead']):
        raise ValueError('This is another agent’s item.')
    return g, value


def conflict_recipient(g, path, item):
    # File provenance wins; then the latest current/previous item developer.
    # Ordered durable records avoid timestamps or provider/model judgements.
    for event in reversed(g.get('integrations', [])):
        if path in event.get('files', []) and event['owner'] in g['members']:
            return event['owner'], event['item']
    for event in reversed(g.get('development', [])):
        if event['item'] == item and event['owner'] in g['members']:
            return event['owner'], item
    return g['owners'].get(item) or g['lead'], item


def record_conflict(root, item, workspace, before, target, generation, *, member=None):
    g = info(root)
    ancestor = git(workspace, 'merge-base', before, target, check=False)
    stages = {}
    for entry in git(workspace, 'ls-files', '-u', '-z', check=False).split('\0'):
        if not entry:
            continue
        meta, path = entry.split('\t', 1)
        _, blob, stage = meta.split()
        stages.setdefault(path, {})[int(stage)] = blob
    files = []
    for path, blobs in sorted(stages.items()):
        recipient, affected_item = conflict_recipient(g, path, item)
        base, ours, theirs = (blobs.get(i) for i in (1, 2, 3))
        files.append(dict(path=path, base=base, ours=ours, theirs=theirs,
                          both_changed=base != ours and base != theirs,
                          recipient=recipient, item=affected_item))
    ident = hashlib.sha256(((item or member) + before + target).encode()).hexdigest()[:16]
    evidence = dict(id=ident, item=item, workspace=str(workspace), checkpoint=before,
                    target=target, ancestor=ancestor, files=files, at=board.now())
    error = ('Sync conflict; checkpoint ' + before + ' is preserved. ' +
             '; '.join(f"{f['path']} → {Path(f['recipient']).name}" for f in files))
    previous = g['assignments'][item] if item else g.get('engagements', {}).get(member, {})
    already = previous.get('conflict', {}).get('id') == ident
    def change(g):
        value = g['assignments'][item] if item else g.setdefault('engagements', {}).setdefault(member, {})
        value.update(sync_error=error, conflict=evidence)
    update(root, change, generation=generation)
    if not already:
        recipients = {f['recipient'] for f in files} or {g['lead']}
        for recipient in recipients:
            relevant = [f for f in files if f['recipient'] == recipient]
            detail = '\n'.join(f"{f['path']} ({f['item']}): base {f['base'] or 'absent'}, "
                               f"workspace {f['ours'] or 'absent'}, integrated {f['theirs'] or 'absent'}; "
                               f"both changed: {f['both_changed']}" for f in relevant)
            board.add_note(Path(recipient), {'item': item},
                           f"Resolve synchronization conflict {ident} for {item} in {workspace}. "
                           f"Shared ancestor {ancestor}; checkpoint {before}; tested target {target}.\n{detail}\n"
                           "The automatic merge was aborted. Resolve against the tested target and commit, then "
                           + ("run colony item " + item + " --sync" if item else "engage the helper again through its delivery hook")
                           + ". The helper checkpoint is retained.", author='colony')
    return error


def sync_item(root, item, *, actor=None):
    with operation_lock(root, 'item-' + item):
        g, value = assignment(root, item, actor)
        if value['state'] not in ('working', 'handback', 'testing'):
            raise ValueError('This item is already integrated.')
        workspace = Path(value['workspace'])
        target = g.get('integrated') or value['base']
        if target and target != value['base'] and git(workspace, 'rev-parse', 'HEAD', check=False):
            if git(workspace, 'ls-files', '-u', check=False):
                raise ValueError('Finish the merge already in progress before Colony synchronizes this workspace.')
            # Preserve unfinished helper work before engagement, including new files.
            # The canonical plan and harness bookkeeping are never helper changes.
            checkpoint(workspace, f'Checkpoint {item} before Colony engagement sync', value['owner'])
            before = git(workspace, 'rev-parse', 'HEAD')
            prior = value.get('conflict') or {}
            if prior.get('checkpoint') == before and prior.get('target') == target:
                raise ValueError(value['sync_error'])
            result = subprocess.run(['git', '-C', str(workspace), 'merge', '--no-edit', target], text=True, capture_output=True)
            if result.returncode:
                if git(workspace, 'ls-files', '-u', check=False):
                    error = record_conflict(root, item, workspace, before, target, g['generation'])
                    git(workspace, 'merge', '--abort')
                    if git(workspace, 'rev-parse', 'HEAD') != before:
                        raise ValueError('The engagement merge did not restore its checkpoint; inspect the workspace.')
                    raise ValueError(error)
                git(workspace, 'merge', '--abort', check=False)
                raise ValueError(result.stderr.strip() or result.stdout.strip())
        value.update(base=target, plan_revision=revision(root), synced_at=board.now())
        value.pop('sync_error', None)
        if value.get('conflict'):
            value['conflict']['resolved_at'] = board.now()
        def change(g):
            # Preserve independent receipt fields added while the Git lock was held.
            g['assignments'][item].update(value)
            g['assignments'][item].pop('sync_error', None)
        update(root, change, generation=g['generation'])
        return value

def checkpoint(workspace, message, member):
    def names(*args):
        result = subprocess.run(['git', '-C', str(workspace), *args], text=True, capture_output=True, check=True)
        return [p for p in result.stdout.split('\0') if p]
    blocked = {'.board', 'ROADMAP.md', '.codex', '.claude', 'AGENTS.md', 'CLAUDE.md'}
    changed = names('ls-files', '--modified', '--deleted', '--others', '--exclude-standard', '-z')
    changed += names('diff', '--cached', '--name-only', '-z')
    eligible = sorted({p for p in changed if Path(p).parts[0] not in blocked})
    if not eligible:
        return
    # Force only this explicit list: ignored new files never enter it unless
    # someone already staged them deliberately; harness files are filtered out.
    git(workspace, 'add', '--all', '-f', '--', *eligible)
    eligible = names('diff', '--cached', '--name-only', '-z', '--', *eligible)
    if eligible:
        # --only also protects excluded files that were already staged.
        git(workspace, 'commit', '--only', '-m', message,
            '--trailer', 'Colony-Agent: ' + Path(member).name, '--', *eligible)


def catch_up_subjects(workspace, previous, target, member):
    if not previous or not target or previous == target:
        return []
    raw = git(workspace, 'log', '--format=%s%x1f%(trailers:key=Colony-Agent,valueonly)%x00',
              previous + '..' + target, check=False)
    subjects = []
    for entry in raw.split('\0'):
        if '\x1f' not in entry:
            continue
        subject, source = entry.strip('\n').split('\x1f', 1)
        if source.strip() not in (str(member), Path(member).name):
            subjects.append(subject)
    return subjects


def _looked(g, member, target, items, revision, *, mark, item=None):
    seat = g.setdefault('engagements', {}).setdefault(member, {})
    value = g['assignments'][item] if item else seat
    receipt = dict(looked_at=target, looked_items=items, looked_revision=revision)
    if mark:
        value.update(receipt)
        value.pop('pending_look', None)
        seat.update(receipt)
        seat.pop('pending_look', None)
    else:
        pending = dict(receipt, token=secrets.token_hex(16), generation=g['generation'], item=item,
                       assignment_at=value.get('at') if item else None,
                       member_before_revision=seat.get('looked_revision'),
                       item_before_revision=value.get('looked_revision'))
        value['pending_look'] = pending
        seat['pending_look'] = pending


def acknowledge_engagement(root, token=None):
    """Advance only the catch-up actually flushed by the engagement hook."""
    if not token or not group(root):
        return
    mine = str(Path(root).resolve())
    def acknowledge(g):
        seat = g.get('engagements', {}).get(mine, {})
        pending = seat.get('pending_look') or {}
        if pending.get('token') != token or pending.get('generation') != g['generation']:
            return
        def apply(value, before_revision):
            # An explicitly known own plan edit made after preparation stays
            # known; acknowledging an older snapshot cannot move it backward.
            seen = {key: pending[key] for key in ('looked_at', 'looked_items', 'looked_revision')}
            if value.get('looked_revision') != before_revision:
                seen.pop('looked_revision')
            value.update(seen)
            value.pop('pending_look', None)
        item = pending.get('item')
        value = g['assignments'].get(item, {})
        if (item and value.get('owner') == mine and value.get('at') == pending.get('assignment_at')
                and (value.get('pending_look') or {}).get('token') == token):
            apply(value, pending.get('item_before_revision'))
        apply(seat, pending.get('member_before_revision'))
    update(root, acknowledge)


def engage(root, *, mark=True, with_receipt=False):
    """Catch up on engagement; deferred output can return its exact receipt token."""
    text, token = _engage(root, mark=mark)
    return (text, token) if with_receipt else text


def _engage(root, *, mark):
    """Called by a scoped delivery hook, never by a model or a periodic wake."""
    g = group(root)
    mine = str(Path(root).resolve())
    if not g:
        return '', None
    jobs = [a for a in g['assignments'].values() if a['owner'] == mine and a['state'] == 'working']
    if not jobs:
        return _engage_member(root, mark=mark)
    job = jobs[0]
    target = g.get('integrated') or job['base']
    previous = job.get('looked_at') or job['base']
    seen_plan = revision(root)
    completed = list(g.get('completed_items', []))
    plan_changed = job.get('looked_revision', job['plan_revision']) != seen_plan
    if not job.get('looked_at') or not job.get('looked_revision'):
        # Keep the baseline even when sync succeeds but hook output fails.
        update(root, lambda g: g['assignments'][job['item']].update(
            looked_at=previous, looked_revision=job.get('looked_revision', job['plan_revision'])), generation=g['generation'])
    try:
        synced = sync_item(root, job['item'], actor=root)
    except ValueError as error:
        return str(error), None
    done = [iid for iid in completed if iid not in job.get('looked_items', [])
            and g['assignments'].get(iid, {}).get('owner') != mine]
    subjects = catch_up_subjects(Path(job['workspace']), previous, target, mine)
    def save_look(g):
        g['assignments'][job['item']] = synced
        _looked(g, mine, target, completed, seen_plan, mark=mark, item=job['item'])
    updated = update(root, save_look, generation=g['generation'])
    token = None if mark else updated['engagements'][mine]['pending_look']['token']
    if not subjects and not done and not plan_changed:
        return '', token
    return ('Colony synchronized your item workspace to the last completed and tested integration ' + target
            + '. Since you last looked: ' + ('items ' + ', '.join(done) + '; ' if done else '')
            + ('; '.join(subjects) or 'no new commit subjects')
            + ('; canonical Vision/roadmap changed' if plan_changed else '')
            + '. Read only the canonical plan at ' + str(plan_path(root)) + '.'), token


def engage_member(root, *, mark=True, with_receipt=False):
    """Catch up an existing member without creating an item or goal."""
    text, token = _engage_member(root, mark=mark)
    return (text, token) if with_receipt else text


def _engage_member(root, *, mark):
    """An existing member branch catches up even without a development item.

    This runs only on engagement. It creates neither a goal nor an item workspace.
    """
    mine = str(Path(root).resolve())
    with operation_lock(root, 'engagement-' + identity(root)):
        g = info(root)
        target = g.get('integrated')
        seen_plan = revision(root)
        completed = list(g.get('completed_items', []))
        value = g.get('engagements', {}).get(mine, {})
        workspace = board.workdir(root)
        before = git(workspace, 'rev-parse', 'HEAD', check=False)
        if not target or not before:
            return '', None
        previous = value.get('looked_at') or before
        if not value.get('looked_at'):
            value['looked_at'] = previous
            update(root, lambda g: g.setdefault('engagements', {}).__setitem__(mine, value), generation=g['generation'])
        source = Path(g.get('integration_root') or board.workdir(Path(g['canonical'])))
        if git(workspace, 'ls-files', '-u', check=False):
            return 'Finish the merge already in progress before Colony synchronizes this workspace.', None
        if workspace.resolve() != source.resolve() and git(workspace, 'merge-base', before, target, check=False) != target:
            checkpoint(workspace, 'Checkpoint agent before Colony engagement sync', mine)
            before = git(workspace, 'rev-parse', 'HEAD')
            conflict = value.get('conflict') or {}
            if conflict.get('checkpoint') == before and conflict.get('target') == target:
                return value['sync_error'], None
            result = subprocess.run(['git', '-C', str(workspace), 'merge', '--no-edit', target], capture_output=True, text=True)
            if result.returncode:
                if git(workspace, 'ls-files', '-u', check=False):
                    error = record_conflict(root, None, workspace, before, target, g['generation'], member=mine)
                    git(workspace, 'merge', '--abort')
                    return error, None
                git(workspace, 'merge', '--abort', check=False)
                return 'Engagement sync could not finish: ' + (result.stderr.strip() or result.stdout.strip()), None
        done = [iid for iid in completed if iid not in value.get('looked_items', [])
                and g['assignments'].get(iid, {}).get('owner') != mine]
        subjects = catch_up_subjects(workspace, previous, target, mine)
        plan_changed = value.get('looked_revision', value.get('plan_revision')) != seen_plan
        value.pop('sync_error', None)
        def save_look(g):
            g.setdefault('engagements', {})[mine] = value
            _looked(g, mine, target, completed, seen_plan, mark=mark)
        updated = update(root, save_look, generation=g['generation'])
        token = None if mark else updated['engagements'][mine]['pending_look']['token']
        if not subjects and not done and not plan_changed:
            return '', token
        return (f'Colony engagement catch-up to tested integration {target}. '
                + ('Completed items: ' + ', '.join(done) + '. ' if done else '')
                + '; '.join(subjects) + ('; canonical plan changed' if plan_changed else '')
                + ('. Continue only the current bounded version.' if mine == g['lead'] else
                   '. No item is assigned; continue only this conversation, without a project goal.')), token


def handback(root, item, *, actor=None):
    value = sync_item(root, item, actor=actor)
    workspace = Path(value['workspace'])
    if not clean(workspace):
        raise ValueError('Commit the finished item before handback.')
    value.update(state='handback', commit=git(workspace, 'rev-parse', 'HEAD', check=False), handed_at=board.now())
    g = update(root, lambda g: g['assignments'].__setitem__(item, value), generation=info(root)['generation'])
    notify(g, f"{item} handed back by {Path(value['owner']).name} at {value['commit'] or workspace}. "
              'The completing agent now integrates, tests and delivers it under Colony’s shared integration lock.', quiet=True,
              exclude=[value['owner']])
    return value


def integrate(root, item, tests, *, actor=None, deploy='', push=False):
    """The completing agent owns integration; other finishers wait in flock.

    Failed checks never advance the tested synchronization target. The landed
    candidate stays available for the same agent to correct and re-test.
    """
    with operation_lock(root, 'integration'):
        g, value = assignment(root, item, actor)
        if actor is not None and str(Path(actor).resolve()) != value['owner']:
            raise ValueError('The agent completing this item integrates it.')
        if g.get('handoff') or g.get('paused'):
            raise ValueError('Integration waits for the lead handoff or project pause.')
        if value['state'] not in ('handback', 'testing', 'deploying') or not tests.strip():
            raise ValueError('Hand back the item and supply its repository test command before integration.')
        source = Path(g.get('integration_root') or board.workdir(Path(g['canonical'])))
        if not clean(source):
            raise ValueError('Land the integration workspace’s tracked changes before integrating this item.')
        if value['state'] != 'deploying':
            # Sync again after the previous finisher released the integration lock.
            value = sync_item(root, item, actor=actor)
            value['commit'] = git(Path(value['workspace']), 'rev-parse', 'HEAD', check=False)
            if value['commit']:
                changed = git(Path(value['workspace']), 'diff', '--name-only', value['base'] + '...' + value['commit']).splitlines()
                if value['branch'] and 'ROADMAP.md' in changed:
                    raise ValueError('A helper changed a roadmap copy. Propose that change to the lead.')
                before = git(source, 'rev-parse', 'HEAD')
                # An unrelated in-flight integration is never included by accident.
                if g.get('integrated') and before != g['integrated'] and source != Path(value['workspace']):
                    extra = git(source, 'diff', '--name-only', g['integrated'] + '..' + before).splitlines()
                    allowed = {'ROADMAP.md'}
                    if any(path not in allowed for path in extra) and value.get('testing_commit') != before:
                        raise ValueError('The integration branch contains untested work from another item; land and test it first.')
                result = subprocess.run(['git', '-C', str(source), 'merge', '--no-edit', value['commit']], text=True, capture_output=True)
                if result.returncode:
                    if git(source, 'ls-files', '-u', check=False):
                        error = record_conflict(root, item, source, before, value['commit'], g['generation'])
                        git(source, 'merge', '--abort')
                        raise ValueError(error)
                    raise ValueError(result.stderr.strip() or result.stdout.strip())
            value.update(state='testing', testing_commit=git(source, 'rev-parse', 'HEAD', check=False))
            update(root, lambda g: g['assignments'][item].update(value), generation=g['generation'])
            result = subprocess.run(shlex.split(tests), cwd=source, text=True, capture_output=True)
            if result.returncode or not clean(source):
                error = (result.stdout + result.stderr)[-4000:] or 'Checks changed tracked files; land them and re-test.'
                update(root, lambda g: g['assignments'][item].update(test_error=error), generation=g['generation'])
                raise ValueError('Integration checks failed; the item remains unaccepted.\n' + error)
            commit = git(source, 'rev-parse', 'HEAD', check=False)
            value.update(state='deploying' if deploy or push else 'integrated', integrated=commit,
                         tests=tests, checked_at=board.now())
            value.pop('test_error', None)
            def accepted(g):
                g['assignments'][item] = value
                g['integrated'] = commit
                g['integrated_checks'] = tests
                g['integrated_at'] = board.now()
                g['completed_items'] = [i['id'] for i in board.items(board.roadmap(root)).values() if i['state'] in ('done', 'verify')]
                g.setdefault('integrations', []).append(dict(item=item, owner=value['owner'], commit=commit,
                    files=git(source, 'diff', '--name-only', value['base'] + '...' + value['commit']).splitlines() if value['commit'] else [],
                    at=board.now()))
            update(root, accepted, generation=g['generation'])
        if deploy:
            result = subprocess.run(shlex.split(deploy), cwd=source, text=True, capture_output=True)
            if result.returncode:
                raise ValueError('Delivery failed; tested work is preserved and no push was attempted.\n' + (result.stdout + result.stderr)[-4000:])
            update(root, lambda g: g['assignments'][item].update(deployed=True, deployed_commit=g['integrated']), generation=g['generation'])
        if push:
            git(source, 'push', 'origin')
        update(root, lambda g: g['assignments'][item].update(state='integrated', pushed=push), generation=g['generation'])
        return info(root)['assignments'][item]

def observe(root):
    g = group(root)
    if not g:
        return
    current = revision(root)
    if current != g.get('plan_revision'):
        update(root, lambda g: g.update(plan_revision=current), generation=g['generation'])


def role_text(root):
    g = info(root)
    mine = str(Path(root).resolve())
    if len(g['members']) == 1:
        return (f"You are this project's agent. Read {plan_path(root)} and continue only the current completed version "
                'to its human checkpoint.' if g['checkpoints'] else '')
    jobs = [a for a in g['assignments'].values() if a['owner'] == mine and a['state'] in ('working', 'handback', 'testing', 'deploying')]
    text = (f"One shared project; only {Path(g['lead']).name} is the lead. Canonical Vision and roadmap: "
            f"{plan_path(root)}. Never read/copy the roadmap in a helper branch. Ownership generation {g['generation']}.")
    if mine == g['lead']:
        text += ' You own the whole roadmap except explicit item assignments. Each completing agent integrates and delivers its item under the shared lock, within the person’s delivery permissions.'
    elif jobs:
        text += '\nYou own only: ' + '; '.join(f"{a['item']} in {a['workspace']} ({a['state']})" for a in jobs)
        text += '. Colony syncs on engagement, without a model call. Propose Vision/roadmap/other-item changes to the lead.'
    else:
        text += ' You are a helper with no active item workspace. Wait for the lead to engage an assigned item with colony item Rn --start; do not take other project work or establish a project-wide goal.'
    return text
