"""Bounded completed versions and candidate-bound human stopping points."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import urllib.request

from . import board, lead

LATER = re.compile(r'\blater\b', re.I)


def current(root):
    g = lead.info(root)
    return next((c for c in g['checkpoints'] if c['id'] == g.get('active_checkpoint')), None)


def unscheduled(root):
    """Items no default version takes: under a heading that is no M-numbered milestone, or one named Later."""
    path, out, section, sub = lead.plan_path(root), set(), '', ''
    for line in (path.read_text() if path.exists() else '').splitlines():
        if line.startswith('## '):
            section, sub = line, ''
        elif line.startswith('###'):
            sub = line
        elif (m := board.ITEM.match(line)) and (not board.MILESTONE.match(section) or LATER.search(section + sub)):
            out.add(m[2])
    return out


def define(root, milestone, outcome, definition, check='', *, items=None, mode='approval', words='', actor=None):
    g = lead.require_lead(root, actor)
    m = next((m for m in board.roadmap(root)['milestones'] if m['id'] == milestone), None)
    if not m or LATER.search(m['title']):
        raise ValueError('Choose an authorized milestone; Later does not start automatically.')
    if not items and not re.fullmatch(r'M\d+(?:\.\d+)?', milestone):
        raise ValueError('Only an M-numbered milestone supplies its items by default; name them with --items.')
    unfinished = [i['id'] for i in m['items'] if i['state'] != 'done']
    selected = list(items) if items else [i for i in unfinished if i not in unscheduled(root)]
    if not selected or not set(selected) <= set(unfinished):
        raise ValueError('The checkpoint must include unfinished items in that milestone.')
    if not outcome.strip() or not definition.strip() or mode not in ('approval', 'check-in'):
        raise ValueError('Name the completed form, its definition of done and approval/check-in mode.')
    ident = milestone + (('-' + '-'.join(selected)) if items else '')
    old = next((c for c in g['checkpoints'] if c['id'] == ident), None)
    if old and old['state'] not in ('planned', 'accepted'):
        raise ValueError('Pause and settle the current version before changing its boundary.')
    checkpoint = dict(id=ident, milestone=milestone, outcome=outcome.strip(), definition=definition.strip(),
                      check=check.strip(), items=selected, mode=mode, words=words.strip(), assumed=not bool(words.strip()),
                      state='planned', run=0, at=board.now())
    def change(g):
        g['checkpoints'] = [c for c in g['checkpoints'] if c['id'] != ident] + [checkpoint]
    lead.update(root, change, generation=g['generation'])
    return checkpoint


def start(root, checkpoint, *, actor=None, source=None):
    g = lead.require_lead(root, actor)
    value = next((c for c in g['checkpoints'] if c['id'] == checkpoint), None)
    if not value or value['state'] not in ('planned', 'corrections'):
        raise ValueError('Choose a planned version, or finish the current review.')
    active = current(root)
    if active and active['id'] != checkpoint and active['state'] != 'accepted':
        raise ValueError('The current version must finish before another starts.')
    def change(g):
        g['active_checkpoint'] = checkpoint
        g['paused'] = False
        for c in g['checkpoints']:
            if c['id'] == checkpoint:
                c.update(state='active', run=c['run'] + 1, started_at=board.now(), scope_revision=lead.revision(root))
    g = lead.update(root, change, generation=g['generation'])
    if source is None or str(Path(source).resolve()) != g['lead']:
        board.add_note(Path(g['lead']), None, 'Continue only to this completed version: ' + value['outcome']
                       + '. Definition of done: ' + value['definition'] + '. Included items: ' + ', '.join(value['items'])
                       + '. Stop there for the person; do not start Later or another version.', author='colony')
    return current(root)


def objective(root, member):
    g, c = lead.info(root), current(root)
    if not c:
        return None
    member = str(Path(member).resolve())
    scope = c['items']
    if member != g['lead']:
        job = next((a for a in g['assignments'].values() if a['owner'] == member and a['state'] in ('working', 'handback', 'testing', 'landing', 'deploying')
                    and a['item'] in scope), None)
        if not job:
            return None
        return (f"Colony {g['id']} generation {g['generation']} {c['id']} run {c['run']}: complete only {job['item']} "
                f"in {job['workspace']}, verify it, commit and hand it back with colony item {job['item']} --handback, "
                'then integrate, test and deliver it with colony item --integrate under the shared lock. '
                "Stop after delivery; do not take another item.")
    coordination = (' Respect assigned helpers; completing item agents integrate, test and deliver their work. '
                    'Wait for assigned hand-ins. ' if len(g['members']) > 1 else ' ')
    return (f"Colony {g['id']} generation {g['generation']} {c['id']} run {c['run']}: {c['outcome']}. "
            f"Done means: {c['definition']}. Work only on {', '.join(scope)}." + coordination +
            'Stop at the integrated completed version for human review with colony progress --ready. '
            'Respect open decisions and usage resets. '
            'Do not continue into another version or Later without the person’s direction.')


def hold(root, member):
    g, c = lead.info(root), current(root)
    member = str(Path(member).resolve())
    if g.get('handoff'):
        return 'lead handoff'
    if g.get('paused'):
        return 'person paused'
    if any(a.get('sync_error') for a in g['assignments'].values()
           if a['owner'] == member or member == g['lead']):
        return 'unresolved synchronization conflict'
    if any(a.get('sync_error') for who, a in g.get('engagements', {}).items()
           if who == member or member == g['lead']):
        return 'unresolved synchronization conflict'
    if not c:
        return 'no bounded version selected'
    if c['state'] not in ('active', 'corrections'):
        return 'version waiting for human review' if c['state'] == 'review' else 'version stopped'
    from . import usage, context
    if member in usage.paused():
        return 'usage pause'
    if context.refreshing(member):
        return 'context refresh'
    for source in g['members']:
        if any(board.due(gate) and (not gate.get('item') or gate['item'] in c['items']) for gate in board.gates(Path(source))):
            return 'blocking decision'
        if source == member and board.asks(Path(source)):
            return 'question for the person'
    if member != g['lead']:
        actionable = any(a['owner'] == member and a['item'] in c['items'] and
                         a['state'] in ('working', 'handback', 'testing', 'landing', 'deploying') for a in g['assignments'].values())
        return None if actionable else 'helper assignment delivered'
    if any(a['owner'] == member and a['item'] in c['items'] and a['state'] in ('handback', 'testing', 'landing', 'deploying')
           for a in g['assignments'].values()):
        return None
    items = board.items(board.roadmap(root))
    remaining = [items[i] for i in c['items'] if i in items and items[i]['state'] not in ('done', 'verify')]
    if not remaining:
        return 'waiting for integration/deployment and review evidence'
    runnable = []
    for item in remaining:
        if str(lead.owner(root, item['id'])) != member:
            assigned = g['assignments'].get(item['id'])
            if (not assigned or assigned['state'] == 'integrated') and all(
                    items.get(previous, {}).get('state') in (None, 'done', 'verify') for previous in item['after']):
                return None  # The lead can engage/start its helper, without doing the helper's item.
            continue
        if all(items.get(previous, {}).get('state') in (None, 'done', 'verify') for previous in item['after']):
            runnable.append(item)
    if not runnable:
        return 'waiting for an assigned helper or predecessor'
    return None


def held_items(root):
    c = current(root)
    return set(c['items']) if c and c['state'] in ('active', 'corrections', 'review') else set()


def artifact_identity(artifact):
    path = Path(artifact).expanduser()
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return None


def ready(root, artifact, commit, checks, *, deployed=False, actor=None):
    g, c = lead.info(root), current(root)
    completers = {a['owner'] for a in g['assignments'].values() if a['state'] == 'integrated'}
    if actor is not None and str(Path(actor).resolve()) not in ({g['lead']} | completers):
        raise ValueError('The lead or an agent completing this version presents its integrated candidate.')
    if not c or c['state'] not in ('active', 'corrections', 'review'):
        raise ValueError('There is no current version to present.')
    items = board.items(board.roadmap(root))
    if any(items.get(i, {}).get('state') not in ('done', 'verify') for i in c['items']):
        raise ValueError('Finish and verify every included item before presenting the version.')
    if any(a['item'] in c['items'] and a['state'] != 'integrated'
           for a in g['assignments'].values()):
        raise ValueError('Integrate and test the helper hand-ins before this checkpoint.')
    if not artifact.strip() or not checks.strip():
        raise ValueError('Provide the usable deliverable and the checks that passed.')
    if commit and commit != g.get('integrated'):
        raise ValueError('The candidate must be the recorded tested integration.')
    source = Path(g.get('integration_root') or board.workdir(Path(g['canonical'])))
    if lead.git(source, 'rev-parse', '--is-inside-work-tree', check=False) == 'true' and not commit:
        raise ValueError('Name the exact tested commit for this version.')
    if artifact.startswith(('http://', 'https://')):
        receipt = g.get('deployed_commit') == commit or any(a.get('deployed_commit') == commit for a in g['assignments'].values())
        if not deployed or not receipt:
            raise ValueError('Record deployment of this exact tested commit before offering running behavior for review.')
        with urllib.request.urlopen(artifact, timeout=3) as response:
            if response.status >= 400:
                raise ValueError('The deployed deliverable is unavailable.')
    elif artifact_identity(artifact) is None:
        raise ValueError('The completed readable artifact must exist before review.')
    evidence = dict(artifact=artifact, artifact_hash=artifact_identity(artifact), commit=commit, checks=checks,
                    deployed=deployed, plan_revision=lead.revision(root))
    ident = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()[:16]
    def change(g):
        for value in g['checkpoints']:
            if value['id'] == c['id']:
                value.update(state='review', candidate=dict(id=ident, **evidence), ready_at=board.now())
    lead.update(root, change, generation=g['generation'])
    return current(root)


def decide(root, candidate, action, *, text='', next_checkpoint=None, source=None):
    with lead.operation_lock(root, 'integration'):
        return _decide(root, candidate, action, text=text, next_checkpoint=next_checkpoint, source=source)


def _decide(root, candidate, action, *, text='', next_checkpoint=None, source=None):
    g, c = lead.info(root), current(root)
    if not c or c['state'] != 'review' or c['candidate']['id'] != candidate:
        raise ValueError('This review is no longer current; inspect the current completed candidate.')
    evidence = c['candidate']
    if (evidence['plan_revision'] != lead.revision(root) or evidence['artifact_hash'] != artifact_identity(evidence['artifact'])
            or evidence['commit'] != g.get('integrated', '') or
            (evidence['deployed'] and evidence['commit'] != g.get('deployed_commit') and not any(
                a.get('deployed_commit') == evidence['commit'] for a in g['assignments'].values()))):
        raise ValueError('The plan or artifact changed since this candidate; present it again before approval.')
    if action not in ('approve', 'changes'):
        raise ValueError('Approve this candidate or request its corrections explicitly.')
    if next_checkpoint:
        if action != 'approve' or not any(v['id'] == next_checkpoint and v['state'] == 'planned' for v in g['checkpoints']):
            raise ValueError('Choose an explicitly planned next version after approving this one.')
    def change(g):
        # The guarded record comes first: a landing handoff or a new lead leaves the plan untouched.
        if g.get('handoff'):
            raise ValueError('The lead handoff is still landing; decide after it lands.')
        active = next((value for value in g['checkpoints'] if value['id'] == g.get('active_checkpoint')), None)
        if not active or active['state'] != 'review' or active['candidate']['id'] != candidate:
            raise ValueError('The review changed; inspect its current candidate.')
        for value in g['checkpoints']:
            if value['id'] == c['id']:
                value.update(state='accepted' if action == 'approve' else 'corrections', decision=text,
                             decided_at=board.now(), decided_candidate=candidate)
                if action == 'changes':
                    value['run'] += 1
        if action == 'approve':
            g.pop('active_checkpoint', None)
    updated = lead.update(root, change, generation=g['generation'])
    if action == 'approve':
        try:
            approve_plan(root, c, source)  # The person's explicit bundled approval, never a dismissed gate.
        except Exception:
            # The plan could not be committed: put the review back as it was, so the same approval can be retried.
            def restore(g):
                g['checkpoints'] = [deepcopy(c) if value['id'] == c['id'] else value for value in g['checkpoints']]
                g['active_checkpoint'] = c['id']
            lead.update(root, restore)
            raise
    # Keep the decision in the checkpoint history without echoing it to the
    # agent that just recorded it. An outside decision still reaches the lead;
    # only corrections wake it, and the words in it are the person's own.
    if source is None or str(Path(source).resolve()) != updated['lead']:
        board.add_note(Path(updated['lead']), None, f"The person {'approved' if action == 'approve' else 'requests corrections to'} "
                       f"{c['outcome']} (candidate {candidate}). {text}"
                       + (' Stop here; no next version was released.' if action == 'approve' and not next_checkpoint else ''),
                       author='person' if text.strip() else 'colony', quiet=action == 'approve')
    if next_checkpoint:
        start(root, next_checkpoint, source=source)
    return updated


def approve_plan(root, c, source):
    path = lead.plan_path(root)
    old_bytes = path.read_bytes()
    old = old_bytes.decode('utf-8')
    lines = []
    for line in old.splitlines(keepends=True):
        match = board.ITEM.match(line.rstrip('\n'))
        if match and match[2] in c['items'] and match[1] == '?':
            line = line.replace('[?]', '[x]', 1)
        lines.append(line)
    approved = ''.join(lines)
    if approved == old:
        return
    path.write_text(approved, encoding='utf-8')
    if lead.git(path.parent, 'rev-parse', '--is-inside-work-tree', check=False) != 'true':
        return
    try:
        lead.commit_plan(root, 'Approve ' + c['id'] + ' completed version', source=source,
                         before_revision=hashlib.sha256(old_bytes).hexdigest(),
                         expected_revision=hashlib.sha256(approved.encode('utf-8')).hexdigest())
    except ValueError:
        # A plan left dirty would block integration and the handoff that interrupted it.
        path.write_bytes(old_bytes)
        lead.git(path.parent, 'reset', '-q', '--', 'ROADMAP.md', check=False)
        raise


def pause(root, on):
    return lead.update(root, lambda g: g.update(paused=on))


def waiting(root):
    c = current(root)
    if not c or c['state'] != 'review':
        return []
    return [dict(kind='checkpoint', key='checkpoint:' + c['id'] + ':' + c['candidate']['id'], checkpoint=c,
                 summary='has a completed version ready: ' + c['outcome'])]


