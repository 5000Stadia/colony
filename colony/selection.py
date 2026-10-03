"""Concrete model choices shared by every launcher, with colony-wide adoption decisions.

Benchmark recommendations are evidence, not launch settings. The accepted ledger is the
boundary: a pending question can never change a command merely because it was rendered.
Keys include the provider; model approval is colony-wide, pins remain role-local.
"""
import contextlib
import copy
import fcntl
import json
import hashlib
import os
import secrets
import threading
from pathlib import Path

from . import board, bench, providers

ROLES = ('main', 'routine', 'step-up', 'chores', 'consultant', 'monitor')
_LOCK = threading.RLock()


class Unavailable(ValueError):
    pass


def pair(value):
    return {k: value.get(k) for k in ('model', 'effort')} if value else None


def same(a, b):
    return pair(a) == pair(b)


def key(family, role, root=None):
    suffix = '@' + hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16] if root else ''
    return family + ':' + role + suffix


def scope(root):
    return root if root and 'auto_balance' in board.project_settings(root)[1] else None


def empty():
    return dict(version=1, accepted={}, approved=[], rejected=[], blocked={}, pending={}, history=[])


def read():
    path = board.home() / 'model-selection.json'
    for p in (path, path.with_suffix('.backup')):
        try:
            state = json.loads(p.read_text())
            if isinstance(state, dict) and state.get('version') == 1 and all(isinstance(state.get(k), type(v)) for k, v in empty().items()):
                if p != path and path.exists():
                    state['recovery'] = 'Model choices recovered from the previous snapshot after an unreadable ledger'
                return state
        except (OSError, ValueError, TypeError):
            pass
    if path.exists():
        state = empty()
        state['recovery'] = 'Model choices were unreadable; rebuilt from current evidence'
        return state
    return empty()


@contextlib.contextmanager
def transaction():
    """Serialize board, watcher and CLI writers; retain the last readable snapshot."""
    with _LOCK:
        board.home().mkdir(parents=True, exist_ok=True)
        with (board.home() / 'model-selection.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = read()
            before = copy.deepcopy(state)
            yield state
            if state != before:
                path = board.home() / 'model-selection.json'
                for target, content in ((path.with_suffix('.backup'), before), (path, state)):
                    tmp = target.with_name(target.name + '.' + secrets.token_hex(4))
                    try:
                        with tmp.open('w') as fh:
                            json.dump(content, fh, indent=2)
                            fh.flush()
                            os.fsync(fh.fileno())
                        os.replace(tmp, target)
                    finally:
                        tmp.unlink(missing_ok=True)


def concrete(family, value):
    """Validate the provider's exact ID and make a legacy omitted effort explicit."""
    if not value or not value.get('model'):
        return None
    p = providers.get(family, strict=True)
    model = value['model']
    if model not in dict(providers.available(p)):
        return None
    levels = providers.efforts_of(p, model)
    effort = value.get('effort')
    if levels and effort not in levels:
        if effort:
            return None
        effort = 'medium' if 'medium' in levels else levels[0]
    if not levels:
        effort = None                         # the provider exposes no effort control for this model
    return dict(value, model=model, effort=effort)


def recommendations(state):
    """Rank eligible evidence without changing the ledger; a rejection applies to all roles."""
    from . import intelligence
    all_entries = bench.standings()
    ceiling = max((p['score'] for p in intelligence.pairs(all_entries)), default=None)
    entries = [e for e in all_entries if e['model'] not in state['rejected']]
    settings = board.registry()['settings']
    scopes = [(None, settings.get('auto_balance', 3))]
    scopes += [(root, board.project_settings(root)[1]['auto_balance']) for root in project_roots()
               if root.exists() and scope(root)]
    out = {}
    for root, balance in scopes:
        for family, provider in providers.PROVIDERS.items():
            if not providers.usable(provider):
                continue
            for role in ROLES:
                if root and (role in ('consultant', 'monitor') or providers.key(providers.of(root)) != family):
                    continue
                ident = key(family, role, root)
                blocked = state['blocked'].get(ident, [])
                chosen = bench.role_pick(family, role, entries, balance=balance, ceiling=ceiling, blocked=blocked)
                chosen = concrete(family, chosen)
                if chosen and pair(chosen) not in blocked:
                    out[ident] = dict(chosen, scope=str(root)) if root else chosen
    return out


def bootstrap(family, rejected=()):
    """No scores yet: freeze a concrete catalog entry, visibly labelled as unmeasured."""
    p = providers.get(family, strict=True)
    defaults = p.own_defaults() if hasattr(p, 'own_defaults') else {}
    chosen = concrete(family, defaults)
    if chosen and chosen['model'] in rejected:
        chosen = None
    if not chosen:
        chosen = next((concrete(family, {'model': m}) for m, _ in providers.available(p) if m not in rejected), None)
    if chosen:
        return dict(chosen, why='Initial concrete choice; no benchmark evidence available')
    return None


def price_change(before, after):
    def price(value):
        return bench.token_price(value['model']) if value else None
    old, new = price(before), price(after)
    result = {'before': list(old) if old is not None else None, 'after': list(new) if new is not None else None}
    if any((v or {}).get('policy') == 'intelligence-goal-v2' for v in (before, after)):
        result['task'] = {label: (value or {}).get('evidence', {}).get('cost') for label, value in (('before', before), ('after', after))}
        result['task_estimated'] = any((v or {}).get('estimated') for v in (before, after))
    return result


def switch(state, ident, chosen, reason):
    old = state['accepted'].get(ident)
    if same(old, chosen):
        return
    state['accepted'][ident] = dict(chosen)
    state['history'].append(dict(id=secrets.token_hex(6), at=board.now(), role=ident,
                                 before=old, after=dict(chosen), reason=reason,
                                 prices=price_change(old, chosen)))


def reconcile():
    """Discover recommendation transitions, group once per model, adopt according to policy."""
    migrate()
    for root in project_roots():
        if (root / '.board').exists():
            migrate(root)
    with transaction() as state:
        # A seat whose role colony no longer has (the retired unattended runtime had one) is dropped, not kept up.
        for ident in [i for i in state['accepted'] if i.split(':', 1)[1].split('@')[0] not in ROLES]:
            del state['accepted'][ident]
        proposed = recommendations(state)
        initial = not state['accepted']
        for family, provider in providers.PROVIDERS.items():
            for role in ROLES:
                ident = key(family, role)
                if ident not in state['accepted']:
                    # First installation has no previous choice to hold. Subsequent new roles
                    # start on an already eligible model while a new-model question waits.
                    choice = proposed.get(ident) if initial else None
                    if not choice:
                        choice = next((concrete(family, v) for v in state['accepted'].values()
                                       if v['model'] in state['approved'] and v['model'] not in state['rejected'] and concrete(family, v)), None)
                    if not choice:
                        choice = bootstrap(family)
                    if choice and choice['model'] not in state['rejected']:
                        switch(state, ident, choice, 'Initial concrete choice')
                        if choice['model'] not in state['approved']:
                            state['approved'].append(choice['model'])
        for root in project_roots():
            if not root.exists() or not scope(root):
                continue
            family = providers.key(providers.of(root))
            for role in ('main', *bench.TIERS):
                ident = key(family, role, root)
                inherited = state['accepted'].get(key(family, role))
                if ident not in state['accepted'] and inherited:
                    switch(state, ident, dict(inherited, scope=str(root)), 'Project inherits accepted choice before adoption')
        pending = {m: p for m, p in state['pending'].items() if p.get('emergency')}
        for ident, held in list(state['accepted'].items()):
            family = ident.split(':', 1)[0]
            if concrete(family, held) and held['model'] not in state['rejected'] and not state.get('recovery'):
                continue
            approved = copy.deepcopy(state)
            approved['rejected'] += [m for m, _ in providers.available(providers.get(family)) if m not in state['approved']]
            replacement = recommendations(approved).get(ident)
            if not replacement:
                replacement = next((concrete(family, v) for v in state['accepted'].values()
                                    if v['model'] in state['approved'] and v['model'] not in state['rejected']
                                    and concrete(family, v)), None)
            emergency = not replacement or bool(state.get('recovery'))
            replacement = replacement or proposed.get(ident) or bootstrap(family, state['rejected'])
            if replacement:
                reason = state.get('recovery') or 'The accepted model became unavailable'
                switch(state, ident, replacement, reason)
                if emergency:
                    model = replacement['model']
                    proposal = pending.setdefault(model, dict(model=model, roles={}, at=board.now(), emergency=reason))
                    proposal['roles'][ident] = dict(before=held, after=replacement, prices=price_change(held, replacement))
        state.pop('recovery', None)
        policy = board.registry()['settings'].get('model_adoption', 'automatic')
        for ident, chosen in proposed.items():
            old = state['accepted'].get(ident)
            if same(old, chosen):
                state['accepted'][ident] = dict(chosen)  # refresh evidence without inventing a launch change
                continue
            model = chosen['model']
            if model in state['approved'] or policy == 'automatic':
                if model not in state['approved']:
                    state['approved'].append(model)
                switch(state, ident, chosen, 'Benchmark recommendation')
            else:
                proposal = pending.setdefault(model, dict(model=model, roles={}, at=board.now()))
                proposal['roles'][ident] = dict(before=old, after=chosen, prices=price_change(old, chosen))
        for model, proposal in pending.items():
            for ident, change in proposal['roles'].items():
                change['seats'] = affected(ident, change['before'], change['after'])
            previous = state['pending'].get(model)
            proposal['id'] = previous['id'] if previous and previous['roles'] == proposal['roles'] else secrets.token_hex(6)
        state['pending'] = pending
        return copy.deepcopy(state)


def auto(family, role, root=None):
    state = read()
    ident = key(family, role, scope(root))
    if state.get('recovery') or ident not in state['accepted'] or not concrete(family, state['accepted'].get(ident)):
        state = reconcile()
    chosen = concrete(family, state['accepted'].get(ident))
    if not chosen:
        raise Unavailable(f'No available accepted model for {family} {role}; choose a concrete model in Settings.')
    return dict(chosen, own=False, why=chosen.get('why') or 'Benchmark recommendation')


def resolve(family, role, pin=None, root=None):
    if pin and pin.get('model'):
        chosen = concrete(family, pin)
        if not chosen:
            raise Unavailable(f"The chosen model or effort is unavailable for {family}: {pin['model']}")
        return dict(chosen, own=True, why='Chosen by you')
    return auto(family, role, root)


def decide(model, proposal_id, action):
    """Only current board proposals can be accepted or rejected. Never write a role pin."""
    reconcile()
    with transaction() as state:
        proposal = state['pending'].get(model)
        if not proposal or proposal['id'] != proposal_id:
            raise ValueError('This recommendation changed. Review the current proposal first.')
        if action == 'approve':
            if model not in state['approved']:
                state['approved'].append(model)
        elif action == 'reject':
            for ident in proposal['roles']:
                family = ident.split(':', 1)[0]
                if not any(m != model and m not in state['rejected'] for m, _ in providers.available(providers.get(family))):
                    raise ValueError('No other available model remains for this provider. Choose another available model before declining this one.')
            state['rejected'].append(model)
        else:
            raise ValueError('Unknown model decision')
        del state['pending'][model]
    return reconcile()


def returnable(state, event):
    latest = next((e for e in reversed(state['history']) if e['role'] == event['role']), None)
    return bool(event.get('before') and latest and latest['id'] == event['id']
                and same(state['accepted'].get(event['role']), event['after']))


def rollback(event_id):
    """Return without pinning. Reject models, or just an effort transition within a model."""
    with transaction() as state:
        event = next((e for e in state['history'] if e['id'] == event_id), None)
        if not event or not event['before']:
            raise ValueError('No previous choice for this change')
        ident = event['role']
        if not returnable(state, event):
            raise ValueError('This role has changed again. Review its latest choice first.')
        family = ident.split(':', 1)[0]
        if not concrete(family, event['before']):
            raise ValueError('The previous choice is no longer available')
        model = event['after']['model']
        if model == event['before']['model']:
            state['blocked'][ident] = [p for p in state['blocked'].get(ident, []) if p != pair(event['before'])]
            state['blocked'][ident].append(pair(event['after']))
            switch(state, ident, event['before'], 'Returned to previous effort')
        else:
            state['rejected'] = [m for m in state['rejected'] if m != event['before']['model']]
            if model not in state['rejected']:
                state['rejected'].append(model)
            # A model-level rejection restores every affected Auto seat, not just the clicked row.
            for role, current in list(state['accepted'].items()):
                if current['model'] != model:
                    continue
                prior = next((e['before'] for e in reversed(state['history']) if e['role'] == role
                              and e['before'] and e['before']['model'] not in state['rejected']
                              and concrete(role.split(':', 1)[0], e['before'])), None)
                if prior:
                    switch(state, role, prior, 'Not this model; returned to previous choice')
            state['pending'].pop(model, None)
    return reconcile()


def alias(model):
    # PROVIDER: Claude Code's documented floating aliases. Exact unknown IDs are not aliases.
    return model in ('opus', 'sonnet', 'haiku', 'opusplan', 'default', 'auto') or bool(model and model.endswith('[1m]') and model.split('[')[0] in ('opus', 'sonnet'))


def migrate(root=None):
    """One-time removal of inherited form values. Later deliberate equal-value pins survive."""
    reg = board.registry()
    settings = reg['settings']
    if not settings.get('model_selection_version'):
        settings['legacy_model_default'] = {'provider': settings['provider'], 'model': settings.get('model'),
                                           'effort': settings.get('effort')}
        if alias(settings.get('model')):
            settings['model'] = settings['effort'] = ''
        if alias((settings.get('monitor_model') or {}).get('model')):
            settings['monitor_model'] = {}
        settings['consultants'] = {k: v for k, v in (settings.get('consultants') or {}).items() if not alias(v.get('model'))}
        settings.setdefault('main_models', {})[settings['provider']] = pair(settings) if settings.get('model') else {}
        settings['model_selection_version'] = 1
        board.save_registry(reg)
    if root is None:
        return
    path = Path(root) / '.board' / 'settings.json'
    own = json.loads(path.read_text()) if path.exists() else {}
    if own.get('model_selection_version'):
        return
    family = own.get('provider') or settings['provider']
    provider = providers.get(family)
    defaults = provider.own_defaults() if hasattr(provider, 'own_defaults') else {}
    legacy = settings.get('legacy_model_default') or {}
    inherited = legacy.get('model') if legacy.get('provider') == family else None
    inherited = inherited or defaults.get('model')
    old = pair(own)
    if alias(own.get('model')) or (own.get('model') and own['model'] == inherited):
        own.pop('model', None)
        own.pop('effort', None)
    # An effort without a model came from the same old form; Auto is always a whole pair.
    if not own.get('model'):
        own.pop('effort', None)
    if old != pair(own) and old and (old['model'] or old['effort']):
        board.append(root, 'model-migrations.jsonl', dict(at=board.now(), before=old, after='Auto'))
    for role, chosen in bench.plan(root).items():
        if alias(chosen.get('model')):
            bench.set_plan(root, role, None, None, 'Legacy floating alias becomes Auto')
    own['model_selection_version'] = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(own, indent=2) + '\n')


def main(root=None):
    migrate(root)
    settings = board.registry()['settings']
    family = providers.key(providers.of(root))
    own = board.project_settings(root)[1] if root else {}
    pin = own if own.get('model') else settings if family == settings['provider'] else (settings.get('main_models') or {}).get(family)
    return resolve(family, 'main', pin, root)


def helper(root, role):
    migrate(root)
    family = providers.key(providers.of(root))
    pin = bench.plan(root).get(role) or (board.registry()['settings'].get('helper_models') or {}).get(key(family, role))
    value = resolve(family, role, pin, root)
    value['own'] = role in bench.plan(root)
    if pin and not value['own']:
        value['why'] = 'Colony helper choice in Settings'
    return value


def consultant(family, automatic=False):
    migrate()
    pin = None if automatic else (board.registry()['settings'].get('consultants') or {}).get(family)
    return resolve(family, 'consultant', pin)


def monitor():
    migrate()
    s = board.registry()['settings']
    return resolve(s['provider'], 'monitor', s.get('monitor_model'))


def scout(family):
    """The monitor's scout reads at its program's routine tier: reading, not judgement."""
    migrate()
    return resolve(family, 'routine', (board.registry()['settings'].get('helper_models') or {}).get(key(family, 'routine')))


def project_roots():
    """Read seats without registering projects (registration itself resolves helper models)."""
    reg = board.registry()
    roots = [Path(p) for p in reg['projects']]
    for folder in map(Path, reg['roots']):
        if folder.is_dir():
            roots += [p for p in folder.iterdir() if p.is_dir() and not p.name.startswith('.')
                      and str(p) not in reg['hidden'] and p not in roots]
    return roots


def role_label(ident):
    family, role = ident.split(':', 1)
    role, _, project = role.partition('@')
    project_name = next((r.name for r in project_roots() if key(family, role, r) == ident), project)
    label = {'main': 'main agents'}.get(role, role)
    if role in bench.TIERS:
        label += ' helpers'
    return providers.get(family).label + ' · ' + label + (' · ' + project_name if project else '')


def affected(ident, before, after):
    """Expand a default role into its seats, retaining explicit pins as comparisons only."""
    family, role = ident.split(':', 1)
    role = role.split('@', 1)[0]
    out = []
    settings = board.registry()['settings']
    for root in project_roots():
        if not root.exists() or providers.key(providers.of(root)) != family:
            continue
        if key(family, role, scope(root)) != ident:
            continue
        if role == 'main':
            own = board.project_settings(root)[1]
            pin = own if own.get('model') else settings if family == settings['provider'] else (settings.get('main_models') or {}).get(family) or {}
        elif role in bench.TIERS:
            pin = bench.plan(root).get(role) or (settings.get('helper_models') or {}).get(key(family, role)) or {}
        else:
            continue
        if pin.get('model') and same(pin, after):
            continue
        out.append(dict(label=f'{root.name}: {role}', before=pair(pin) if pin.get('model') else before,
                        after=after, pinned=bool(pin.get('model')), root=str(root)))
    if role in ('consultant', 'monitor'):
        pin = ((settings.get('consultants') or {}).get(family) if role == 'consultant' else
               settings.get('monitor_model') if family == settings['provider'] else None) or {}
        out.append(dict(label=f'{family} {role}', before=pair(pin) if pin.get('model') else before,
                        after=after, pinned=bool(pin.get('model'))))
    return out or [dict(label=role_label(ident), before=before, after=after, pinned=False)]
