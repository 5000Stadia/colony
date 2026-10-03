"""Reconcile bounded continuation without taking over a person's native goal.

Every setter has a durable intent before dispatch. A lost response is resolved
by observing the native goal, never by retrying the setter. Native stops and
person pauses remain stops until an explicit continuation request.

PROVIDER: the native goal protocol here is Codex's thread goals (thread/goal/get,
set, clear), reached through providers.Codex.goal_client. A program without
native goals continues by a wake typed into its idle console instead.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path
import time

from . import board, console, context, lead, progress, providers
from .codex_rpc import RPCError

_last = {}
NATIVE_STOPS = ('blocked', 'usageLimited', 'budgetLimited')
ASSIGNMENT_ACTIVE = ('working', 'handback', 'testing', 'landing', 'deploying')


def save(root, value, generation):
    value = deepcopy(value)
    return lead.update(root, lambda g: g['goals'].__setitem__(str(Path(root).resolve()), value), generation=generation)


def status(root):
    return deepcopy(lead.info(root)['goals'].get(str(Path(root).resolve()), {}))


@contextmanager
def locked(root):
    """Serialize watcher, hooks and explicit requests for this logical seat."""
    path = board.home() / 'continuation' / (lead.identity(root) + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def safe_arm(root):
    from . import mail
    name = console.session_name(root)
    return (console.running(name) and console.snapshot(root, lines=1)['state'] == 'idle'
            and not console.drafting(name) and not console.attached(name)
            and not any(not n['delivered_at'] and not n.get('quiet') for n in board.open_notes(root))
            and not any(not m['delivered_at'] for m in mail.inbox(root)))


def scope(g, root):
    """A generation or wording edit does not reopen a completed version."""
    checkpoint = next((c for c in g['checkpoints'] if c['id'] == g.get('active_checkpoint')), None)
    if not checkpoint:
        return None
    mine = str(Path(root).resolve())
    value = dict(checkpoint=checkpoint['id'], run=checkpoint.get('run', 0), role='lead')
    if mine != g['lead']:
        assignment = next((a for a in g['assignments'].values()
                           if a['owner'] == mine and a['state'] in ASSIGNMENT_ACTIVE
                           and a['item'] in checkpoint['items']), None)
        if not assignment:
            return None
        value.update(role='helper', item=assignment['item'], workspace=assignment['workspace'])
    return value


def owned(goal, receipt, tid):
    return bool(goal and receipt.get('thread') == tid and receipt.get('objective') == goal['objective']
                and receipt.get('native_created') is not None
                and receipt['native_created'] == goal.get('createdAt'))


def same_goal(first, second, *, state=False):
    if not first or not second:
        return first is None and second is None
    return (first.get('objective') == second.get('objective')
            and first.get('createdAt') == second.get('createdAt')
            and (not state or first.get('status') == second.get('status')))


def observe(receipt, tid, goal):
    state = goal['status'] if goal else 'none'
    receipt.setdefault('observations', {})[tid] = dict(status=state, at=time.time())
    receipt.update(observed_thread=tid, observed_status=state)
    receipt['native_stopped'] = all(o['status'] != 'active' for o in receipt['observations'].values())


def confirm(receipt, tid, goal):
    receipt.update(thread=tid, objective=goal['objective'], native_created=goal['createdAt'],
                   last_status=goal['status'], outcome='confirmed')
    for key in ('pending', 'pending_before', 'pending_thread', 'error', 'foreign'):
        receipt.pop(key, None)
    observe(receipt, tid, goal)


def confirm_clear(receipt, tid):
    for key in ('objective', 'native_created', 'pending', 'pending_before', 'pending_thread',
                'error', 'foreign', 'paused_by', 'completed_scope', 'native_stop', 'native_stop_thread',
                'resume_requested', 'settled'):
        receipt.pop(key, None)
    receipt.update(thread=tid, last_status='none', outcome='confirmed', manual_stop=True,
                   hold='explicitly cleared', cleared=True)
    observe(receipt, tid, None)


def dispatch(root, client, tid, params, receipt, generation, *, before=None):
    """Check ownership again under the lead lock and persist intent first."""
    mine = str(Path(root).resolve())
    with lead.locked() as data:
        live = lead.info_from(data, root)
        if live['generation'] != generation:
            raise ValueError('Lead changed before goal dispatch; no mutation sent.')
        if params.get('status') == 'active':
            if (live.get('handoff') or live.get('paused')
                    or progress.hold(root, root) or progress.objective(root, root) != params.get('objective')):
                raise ValueError('The project stopped or its boundary changed before goal dispatch; no continuation started.')
            session = context.session(root)
            if not session or session.get('provider') != 'codex' or session['id'] != tid:
                raise ValueError('The authoritative conversation changed before dispatch; no continuation started.')
            if not safe_arm(root):
                raise ValueError('Input became busy before goal dispatch; no continuation started.')
        actual = client.call('thread/goal/get', {'threadId': tid}).get('goal')
        if not same_goal(actual, before, state=True):
            raise ValueError('Native goal changed before dispatch; no mutation sent.')
        if actual and not owned(actual, receipt, tid):
            raise ValueError('This native goal is not Colony-owned; no mutation sent.')
        receipt.update(thread=tid, pending=dict(params), pending_before=deepcopy(actual),
                       pending_thread=tid, outcome='unknown', native_stopped=False)
        if params.get('status') == 'active':
            receipt.update(scope=scope(live, root), paused_by=None, hold=None)
            receipt.pop('cleared', None)
        live['goals'][mine] = deepcopy(receipt)
        data[live['id']] = live
        from .codex_transfer import atomic_json
        atomic_json(lead.location(), data)
        result = client.call('thread/goal/set', dict(threadId=tid, **params)).get('goal')
        expected = params.get('objective', receipt.get('objective'))
        if not result or result.get('objective') != expected or result.get('createdAt') is None:
            raise ValueError('Native goal response did not confirm this update; reconcile before continuing.')
        if params.get('status') == 'paused' and result.get('status') == 'active':
            raise ValueError('Native goal has not confirmed its pause; reconcile before continuing.')
        confirm(receipt, tid, result)
        if params.get('status') == 'paused' and result['status'] == 'paused':
            receipt['paused_by'] = receipt.get('hold') or 'Colony hold'
        if params.get('status') == 'active':
            receipt.pop('resume_requested', None)
        live['goals'][mine] = deepcopy(receipt)
        return result


def settle_pending(receipt, tid, goal):
    pending = receipt.get('pending')
    if not pending:
        return True
    if receipt.get('pending_thread', receipt.get('thread')) != tid:
        return False
    if pending.get('clear'):
        if goal is None:
            confirm_clear(receipt, tid)
            return True
        receipt['error'] = 'The native goal clear has an uncertain outcome; it was not retried.'
        return False
    expected = pending.get('objective', receipt.get('objective'))
    before = receipt.get('pending_before')
    identity = (goal and goal.get('createdAt') is not None and goal['objective'] == expected
                and (not before or before.get('objective') != expected
                     or before.get('createdAt') == goal['createdAt']))
    # Activation may have completed/paused/limited before its response is read.
    # A pause is confirmed only by observing an actual stopped native goal.
    stopped = pending.get('status') != 'paused' or bool(goal and goal['status'] != 'active')
    if identity and stopped:
        confirm(receipt, tid, goal)
        if pending.get('status') == 'paused' and goal['status'] == 'paused':
            receipt['paused_by'] = receipt.get('hold') or 'Colony hold'
        if pending.get('status') == 'active':
            receipt.pop('resume_requested', None)
        return True
    receipt['error'] = ('A previous native goal update has an uncertain outcome. '
                        'Read/adopt or clear it explicitly before continuing; it was not retried.')
    return False


def native_stop(receipt, goal, *, missing=False):
    """Record a person's pause/clear separately from a controller hold."""
    if goal and goal['status'] in NATIVE_STOPS:
        receipt.update(native_stop=goal['status'], last_status=goal['status'],
                       native_stop_thread=receipt.get('thread'),
                       error='Native goal is ' + goal['status'] + '; Colony will not override that stop.')
        return True
    if goal and goal['status'] == 'active':
        # Only a native/user transition can lift a native limit; never our setter.
        receipt.pop('native_stop', None)
        receipt.pop('native_stop_thread', None)
        receipt['manual_stop'] = False
    if receipt.get('native_stop') and not receipt.get('resume_requested'):
        receipt['error'] = 'Native goal stopped; continue explicitly after the native stop is resolved.'
        return True
    paused = goal and goal['status'] == 'paused'
    if ((paused and not receipt.get('paused_by')) or missing) and not receipt.get('resume_requested'):
        receipt['manual_stop'] = True
    if receipt.get('manual_stop') and not receipt.get('resume_requested'):
        receipt.update(last_status=goal['status'] if goal else 'none',
                       error='Native goal was paused or cleared by the person; continue explicitly.')
        return True
    return False


def reconcile(root, client, tid, *, forced_hold=None):
    with locked(root):
        return _reconcile(root, client, tid, forced_hold=forced_hold)


def _reconcile(root, client, tid, *, forced_hold=None):
    g = lead.info(root)
    receipt = status(root)
    receipt['observations'] = {}
    receipt['native_stopped'] = False
    receipt.pop('error', None)
    desired = progress.objective(root, root)
    reason = forced_hold or progress.hold(root, root)
    current_scope = scope(g, root)
    if (receipt.get('cleared') and current_scope is not None and receipt.get('scope') is not None
            and receipt['scope'] != current_scope):
        # Opening a different bounded version is an explicit start. A clear
        # stops its own scope, without vetoing that later authorized version.
        receipt.update(manual_stop=False, resume_requested=True)

    def finish():
        receipt['settled'] = dict(thread=tid, objective=desired, reason=reason, scope=current_scope)
        save(root, receipt, g['generation'])
        return receipt

    # A new authoritative conversation cannot leave the previous owned goal on.
    # It is paused once, here, and then forgotten.
    previous = receipt.get('thread')
    if previous and previous != tid and (receipt.get('objective') or receipt.get('pending')):
        old = client.call('thread/goal/get', {'threadId': previous}).get('goal')
        observe(receipt, previous, old)
        if not settle_pending(receipt, previous, old):
            return finish()
        mine = owned(old, receipt, previous)
        if old and not mine:
            receipt.update(foreign=True, error='The previous conversation has a goal belonging to the person; Colony leaves it unchanged.')
            current = client.call('thread/goal/get', {'threadId': tid}).get('goal')
            observe(receipt, tid, current)
            return finish()
        stop = native_stop(receipt, old, missing=old is None and receipt.get('outcome') == 'confirmed')
        if mine and old['status'] == 'complete':
            receipt['completed_scope'] = receipt.get('scope')
        if mine and old['status'] == 'active':
            receipt['hold'] = 'conversation changed'
            old = dispatch(root, client, previous, {'status': 'paused'}, receipt, g['generation'], before=old)
            if old['status'] == 'active':
                receipt['error'] = 'The previous native goal has not stopped; continuation waits.'
                return finish()
        for key in ('thread', 'objective', 'native_created', 'last_status', 'paused_by', 'foreign'):
            receipt.pop(key, None)
        if stop:
            receipt['manual_stop'] = receipt.get('manual_stop', False)

    goal = client.call('thread/goal/get', {'threadId': tid}).get('goal')
    observe(receipt, tid, goal)
    if not settle_pending(receipt, tid, goal):
        return finish()
    mine = owned(goal, receipt, tid)
    if goal and not mine:
        receipt.update(foreign=True, error='An existing goal belongs to the person; Colony leaves it unchanged.')
        return finish()
    receipt.pop('foreign', None)
    # An explicit continue cannot evade a native budget/usage stop merely by
    # opening a different thread. The origin must first leave that stop itself.
    stopped_thread = receipt.get('native_stop_thread')
    if receipt.get('native_stop') and stopped_thread and stopped_thread != tid:
        stopped = client.call('thread/goal/get', {'threadId': stopped_thread}).get('goal')
        observe(receipt, stopped_thread, stopped)
        if stopped and (stopped['status'] in NATIVE_STOPS or stopped['status'] == 'active'):
            receipt['error'] = 'The previous conversation still has a native stop or active goal; resolve it before continuing.'
            return finish()
    if native_stop(receipt, goal, missing=goal is None and receipt.get('thread') == tid
                   and receipt.get('outcome') == 'confirmed'):
        return finish()
    if mine and goal['status'] == 'complete':
        receipt['completed_scope'] = receipt.get('scope') or current_scope
        receipt['last_status'] = 'complete'
    if reason:
        receipt['hold'] = reason
        if mine and goal['status'] == 'active':
            goal = dispatch(root, client, tid, {'status': 'paused'}, receipt, g['generation'], before=goal)
        receipt.update(last_status=goal['status'] if goal else 'none', hold=reason)
        return finish()
    if receipt.get('completed_scope') == current_scope and current_scope is not None:
        receipt.update(hold='native goal completed; present integration and review evidence', last_status='complete')
        return finish()
    if not desired or not safe_arm(root):
        if mine:
            receipt['last_status'] = goal['status']
        return finish()
    if not goal or goal['objective'] != desired or goal['status'] == 'paused':
        receipt.update(hold=None, manual_stop=False)
        if receipt.get('resume_requested'):
            receipt.pop('native_stop', None)
        goal = dispatch(root, client, tid, dict(objective=desired, status='active'), receipt, g['generation'], before=goal)
        if goal['status'] == 'complete':
            receipt.update(completed_scope=current_scope, hold='native goal completed; present integration and review evidence')
        elif goal['status'] in NATIVE_STOPS:
            native_stop(receipt, goal)
        elif goal['status'] == 'paused':
            native_stop(receipt, goal)
    else:
        receipt.update(hold=None, last_status=goal['status'])
        receipt.pop('resume_requested', None)
    return finish()


def _engaged(g, root):
    if not g.get('active_checkpoint'):
        return False
    mine = str(Path(root).resolve())
    if mine == g['lead']:
        return True
    return any(a['owner'] == mine and a['state'] in ASSIGNMENT_ACTIVE for a in g['assignments'].values())


def _draining(receipt):
    return bool(receipt.get('pending') or (receipt.get('objective') and not receipt.get('foreign')
                and receipt.get('last_status') in (None, 'active')))


def _quiet(root, g, receipt, tid, desired, reason):
    """A settled stop needs no socket, RPC or record write on each watcher tick."""
    if _draining(receipt):
        return False
    settled = dict(thread=tid, objective=desired, reason=reason, scope=scope(g, root))
    if receipt.get('settled') != settled:
        return False
    return bool(receipt.get('foreign') or receipt.get('manual_stop') or receipt.get('native_stop')
                or receipt.get('last_status') == 'complete'
                or (reason and receipt.get('native_stopped')))


def tick(root, *, forced_hold=None, force=False):
    g = lead.group(root)
    if not g:
        return {}
    receipt = status(root)
    # This check precedes provider/session discovery. A lone project with no
    # selected version and unassigned helpers cost no native/provider calls.
    if not forced_hold and not _engaged(g, root) and not _draining(receipt):
        return receipt
    desired = progress.objective(root, root)
    reason = forced_hold or progress.hold(root, root)
    provider = providers.of(root)
    if not hasattr(provider, 'goal_client'):
        return wake(root, g, receipt, desired, reason)
    s = context.session(root)
    if not s or s['provider'] != providers.key(provider):
        value = dict(receipt, native_stopped=False, error='The authoritative Codex conversation is unavailable.')
        save(root, value, g['generation'])
        return value
    if not force and not forced_hold and _quiet(root, g, receipt, s['id'], desired, reason):
        return receipt
    try:
        with provider.goal_client(root, timeout=3) as client:
            return reconcile(root, client, s['id'], forced_hold=forced_hold)
    except (RPCError, OSError, ValueError, KeyError) as error:
        value = dict(status(root), native_stopped=False, error=str(error))
        try:
            save(root, value, g['generation'])
        except ValueError:
            pass  # A newer owner controls the record now.
        return value


def mark(root, g, desired):
    """What a wake answers to: the objective, the HEAD of the member's work, the plan and the item states."""
    work = (scope(g, root) or {}).get('workspace') or board.workdir(root)
    value = [desired, lead.git(work, 'rev-parse', 'HEAD', check=False), lead.revision(root),
             {a['item']: a['state'] for a in g['assignments'].values()}]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def wake(root, g, receipt, desired, reason):
    """Without native goals, an idle console is woken once per change in the work, never after a turn that
    changed nothing. A hold ends the current wake, so its lifting wakes again."""
    if reason:
        if 'wake' in receipt or receipt.get('hold') != reason:
            receipt.pop('wake', None)
            receipt['hold'] = reason
            save(root, receipt, g['generation'])
    elif desired and safe_arm(root):
        # Hooks and the watcher race here: decide and send under the seat's lock, against the current lead record.
        with locked(root):
            now, receipt = lead.info(root), status(root)
            if now['generation'] != g['generation'] or now.get('handoff') or progress.objective(root, root) != desired:
                return receipt
            value = mark(root, now, desired)
            if receipt.get('wake') != value and console.type_into(console.session_name(root), '[colony] ' + desired):
                receipt.update(wake=value, hold=None)
                save(root, receipt, now['generation'])
    return receipt


def adopt(root):
    """Only an explicit request can make an existing native goal ours."""
    with locked(root):
        s, g = context.session(root), lead.info(root)
        if not s or s['provider'] != 'codex':
            raise ValueError('Start this project’s Codex conversation before adopting its goal.')
        with providers.get(s['provider']).goal_client(root) as client:
            goal = client.call('thread/goal/get', {'threadId': s['id']}).get('goal')
        if not goal:
            raise ValueError('This conversation has no goal to adopt.')
        receipt = dict(scope=scope(g, root), hold='explicitly adopted', manual_stop=goal['status'] == 'paused')
        confirm(receipt, s['id'], goal)
        if goal['status'] in NATIVE_STOPS:
            native_stop(receipt, goal)
        if goal['status'] == 'complete':
            receipt['completed_scope'] = receipt['scope']
        save(root, receipt, g['generation'])
        return receipt


def resume(root):
    """Explicit continuation permits a person's pause, never an uncertain retry."""
    with locked(root):
        g = lead.info(root)
        value = status(root)
        if value.get('pending'):
            raise ValueError('The previous native goal update is uncertain; read/adopt or clear it before continuing.')
        value.update(manual_stop=False, resume_requested=True, hold='explicit resume')
        for key in ('error', 'settled', 'wake'):
            value.pop(key, None)
        save(root, value, g['generation'])
    progress.pause(root, False)
    return tick(root, force=True)


def clear(root):
    """Explicit recovery: clear our tracked goal, never a foreign goal or a retry."""
    with locked(root):
        s, g = context.session(root), lead.info(root)
        if not s or s['provider'] != 'codex':
            raise ValueError('Start this project’s Codex conversation before clearing its goal.')
        receipt = status(root)
        receipt.setdefault('scope', scope(g, root))
        tid = receipt.get('pending_thread') or receipt.get('thread') or s['id']
        with providers.get(s['provider']).goal_client(root) as client:
            goal = client.call('thread/goal/get', {'threadId': tid}).get('goal')
            observe(receipt, tid, goal)
            if receipt.get('pending', {}).get('clear'):
                if not settle_pending(receipt, tid, goal):
                    save(root, receipt, g['generation'])
                    raise ValueError(receipt['error'])
            elif goal is None:
                # Resolves an uncertain activation without repeating it.
                confirm_clear(receipt, tid)
            else:
                if receipt.get('pending'):
                    settle_pending(receipt, tid, goal)
                if not owned(goal, receipt, tid):
                    raise ValueError('This native goal belongs to the person; Colony will not clear it. Adopt it explicitly first.')
                with lead.locked() as data:
                    live = lead.info_from(data, root)
                    if live['generation'] != g['generation']:
                        raise ValueError('Lead changed before goal clear; no mutation sent.')
                    actual = client.call('thread/goal/get', {'threadId': tid}).get('goal')
                    if not same_goal(actual, goal, state=True):
                        raise ValueError('Native goal changed before clear; no mutation sent.')
                    receipt.update(pending=dict(clear=True), pending_before=deepcopy(actual),
                                   pending_thread=tid, outcome='unknown', native_stopped=False)
                    mine = str(Path(root).resolve())
                    live['goals'][mine] = deepcopy(receipt)
                    data[live['id']] = live
                    from .codex_transfer import atomic_json
                    atomic_json(lead.location(), data)
                    client.call('thread/goal/clear', {'threadId': tid})
                    actual = client.call('thread/goal/get', {'threadId': tid}).get('goal')
                    observe(receipt, tid, actual)
                    if not settle_pending(receipt, tid, actual):
                        raise ValueError(receipt['error'])
                    live['goals'][mine] = deepcopy(receipt)
        save(root, receipt, g['generation'])
        return receipt


def hold(root, reason='context refresh'):
    """Pause only owned continuation now; return fresh proof that native work stopped.
    What keeps it paused is the hold itself (progress.hold), never a record of this request."""
    g = lead.group(root)
    if not g or (not _engaged(g, root) and not _draining(status(root))):
        return True
    receipt = tick(root, forced_hold=reason, force=True)
    return (not hasattr(providers.of(root), 'goal_client')
            or bool(receipt.get('native_stopped') and not receipt.get('pending')))


def handoff(root):
    g = lead.info(root)
    hand = g.get('handoff')
    if not hand:
        return False
    outgoing = Path(hand['outgoing'])
    receipt = tick(outgoing, forced_hold='lead handoff', force=True)
    if hasattr(providers.of(outgoing), 'goal_client'):
        # A stale receipt, absent socket, foreign active goal or uncertain pause
        # is never evidence that the outgoing continuation has stopped.
        if not receipt.get('native_stopped') or receipt.get('pending'):
            return False
    name = console.session_name(outgoing)
    if console.running(name) and (console.snapshot(outgoing, lines=1)['state'] != 'idle'
            or console.drafting(name) or console.attached(name)):
        return False
    workspaces = {board.workdir(outgoing), *lead.workspaces(outgoing)}
    if any(not lead.clean(workspace) for workspace in workspaces):
        return False
    lead.finish_handoff(root, g['generation'])
    return True


def after_turn(root):
    """A finished turn reconciles at once; it earns a new wake only if it changed the work."""
    return tick(root, force=True)


def tick_all():
    now = time.monotonic()
    for g in lead.records().values():
        root = Path(g['canonical'])
        if not g.get('active_checkpoint') and not g.get('handoff') and not any(_draining(v) for v in g['goals'].values()):
            continue
        lead.observe(root)
        handoff(root)
        for member in g['members']:
            if now - _last.get(member, 0) < 5:
                continue
            _last[member] = now
            tick(Path(member))
