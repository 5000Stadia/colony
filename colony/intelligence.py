"""Goal-first Auto choices from comparable AA Intelligence Index/task-cost evidence.

The free Artificial Analysis API gives every pair it measures its Intelligence Index and that index's cost per task,
current each day, within the index version the reply names. A pair it doesn't measure (an older model's lower
settings, an effort it never runs) is no candidate: nothing is estimated or carried, and no page is read.
"""
import json
import math
import re
from pathlib import Path

LEVELS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
# Each role's goal: this far below the shared ceiling at Balanced. Rote alone has none: mechanical work takes
# the most points per task-dollar (the person's call).
OFFSETS = {'main': 4, 'routine': 10, 'chores': 20, 'step-up': 0, 'consultant': 0}
POSITIONS = ('Intelligence', 'Intelligence + 1', 'Intelligence + 2', 'Balanced', 'Economy − 2', 'Economy − 1', 'Economy')
TOLERANCE = 1
PRICE = 'Price per 1M tokens (blended 3:1)'             # 3 input tokens to 1 output


def position(value):
    try:
        n = int(str(value))
    except (ValueError, TypeError):
        raise ValueError('Auto balance must be a whole number from 0 to 6')
    if not 0 <= n <= 6:
        raise ValueError('Auto balance must be a whole number from 0 to 6')
    return n


def records(points):
    out = []
    for p in points:
        for metric, domain, title, unit in (('score', 'overall', 'Intelligence Index', 'points'),
                                           ('cost', 'cost', 'Cost per Intelligence Index task', 'usd'),
                                           ('price', 'cost', PRICE, 'usd')):
            if p.get(metric) is not None:
                out.append(dict(model=p['model'], effort=p['effort'], source='Artificial Analysis', kind='independent',
                                benchmark=title, version=p['version'], domain=domain, value=p[metric], unit=unit,
                                date=p['date'], url=p['url'], note='Primary model-page measurement'))
    return out


def bundled_records():
    return records(json.loads(Path(__file__).with_name('data').joinpath('aa-pairs.json').read_text()))


def valid(value, metric):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and (
        0 <= value <= 100 if metric == 'score' else value > 0)


def candidates(rows, lineup):
    """Each lineup pair's Intelligence Index and cost per task, as Artificial Analysis measured them.

    The API's rows, once it has spoken (else the records colony ships, for a board with no key yet): one source,
    its newest index version, each pair's newest measurement of that version. A pair needs its index; its cost
    comes with it, and one without (rare) stays out of every choice that weighs cost.
    """
    from .bench import API_SOURCE, COST
    models = {m: (family, levels or [None]) for family, m, _, levels in lineup}
    found = {}
    for r in rows:
        if r['model'] not in models or not r['source'].startswith('Artificial Analysis') or r['kind'] != 'independent':
            continue
        name = r['benchmark'].lower()
        metric = ('score' if r['domain'] == 'overall' and r['unit'] == 'points' and 'intelligence index' in name else
                  'cost' if r['domain'] == 'cost' and r['unit'] == 'usd' and (r['benchmark'] == COST or (
                      'intelligence index' in name and 'per' in name and 'task' in name)) else None)
        if not metric or not valid(r['value'], metric):
            continue
        effort = r.get('effort')
        if effort not in LEVELS and not (effort is None and models[r['model']][1] == [None]
                                         and '(reasoning)' in (r.get('note') or '').lower()):
            continue
        found.setdefault(r['source'] == API_SOURCE, []).append((metric, effort, r))
    chosen = found.get(True) if any(m == 'score' for m, _, _ in found.get(True, [])) else found.get(False, [])
    number = lambda v: (tuple(map(int, re.findall(r'\d+', v or ''))), v or '')
    versions = {r.get('version') for m, _, r in chosen if m == 'score'}
    if not versions:
        return []
    version = max(versions, key=number)
    newest = {}
    for metric, effort, r in chosen:
        # A cost row the reply didn't version is its index's: the API gives the two together.
        if (r.get('version') or version) != version:
            continue
        slot = newest.setdefault((r['model'], effort), {})
        if metric not in slot or (r['date'], str(r.get('note'))) > (slot[metric]['date'], str(slot[metric].get('note'))):
            slot[metric] = r
    out = []
    for model, (family, levels) in models.items():
        for effort in levels:
            point = newest.get((model, effort), {})
            if 'score' not in point:
                continue
            score, cost = point['score'], point.get('cost')
            if cost and cost['date'] != score['date']:
                cost = None                                   # never a cost from another day's measurement
            evidence = {k: dict(value=v['value'], url=v['url'], date=v['date'], source=v['source'])
                        for k, v in (('score', score), ('cost', cost)) if v}
            out.append(dict(model=model, effort=effort, family=family, score=score['value'],
                            cost=cost['value'] if cost else None, version=version,
                            evidence=dict(score=evidence['score'], cost=evidence.get('cost'))))
    return out


def pairs(entries):
    return [e['pair'] for e in entries if e.get('pair')]


def pick(family, role, points, balance=3, ceiling=None, blocked=()):
    balance = position(balance)
    ceiling = max((p['score'] for p in points), default=None) if ceiling is None else ceiling
    mine = [p for p in points if p['family'] == family and
            {'model': p['model'], 'effort': p['effort']} not in blocked]
    if not mine or ceiling is None:
        return None
    priced = [p for p in mine if p['cost'] is not None and p['cost'] > 0]
    if not priced:
        return None
    best = max(p['score'] for p in mine)
    shift = 0 if role in ('consultant', 'step-up') else 2 * (balance - 3)
    goal = None if role == 'rote' else max(0, ceiling - max(0, OFFSETS[role] + shift))
    if role == 'rote':
        chosen = min(priced, key=lambda p: (-p['score'] / p['cost'], p['cost'], p['model'], p['effort']))
        reason = 'Most Intelligence Index points per task-dollar'
        target = None
    else:
        target = min(goal, best) - TOLERANCE
        fits = [p for p in priced if p['score'] >= target]
        if not fits:
            return None                   # unknown cost cannot justify a cheaper/weaker substitution
        chosen = min(fits, key=lambda p: (p['cost'], -p['score'], p['model'], p['effort']))
        reason = f"Goal {goal:.2f}; ceiling {ceiling:.2f}; one-point tolerance"
        if chosen['score'] < goal:
            reason += f"; {goal - chosen['score']:.2f} below goal"
    evidence = dict(chosen, ceiling=ceiling, goal=goal, target=target, family_best=best, balance=balance, tolerance=TOLERANCE)
    return dict(model=chosen['model'], effort=chosen['effort'], policy='intelligence-goal-v2', evidence=evidence,
                why=f"{reason}; Index {chosen['score']:.2f}, ${chosen['cost']:.4g}/task ({chosen['version']})")


def health(entries=None, today=None):
    """Model-data health in short lines: (problems, notes, summary). A problem: Artificial Analysis hasn't answered
    for over two days with a key connected, so scores and prices stand as of the day they last came (the address it
    retired on Nov 4, 2026 went unnoticed for five). Notes: a lineup model with no cost per task, which Auto
    therefore can't choose; the older models left out because a newer version of theirs is scored."""
    import datetime
    from . import bench, board
    last = bench.fetched()
    problems, notes = [], []
    came = str(last.get('last') or bench.snapshot()[1] or '')
    try:
        days = (datetime.date.fromisoformat(today or board.now()[:10]) - datetime.date.fromisoformat(came)).days
    except ValueError:
        days = None
    if last.get('error') and bench.aa_key() and (days is None or days > 2):
        problems.append(f"Artificial Analysis hasn't answered since {came or 'the first try'} ({last['error']}); "
                        "Auto chooses on the scores and prices of that day")
    points = pairs(bench.standings() if entries is None else entries)
    priced = {p['model'] for p in points if p['cost'] is not None}
    notes += [f"{label} has no cost per task from Artificial Analysis yet, so Auto can't choose it"
              for _, model, label, _ in bench.lineup() if model not in priced]
    old = bench.superseded()
    if old:
        notes.append("left out for a newer version of their own: " + ', '.join(sorted(bench.name(m) for m in old)))
    summary = (f"{len({p['model'] for p in points})} models, {len(points)} settings, scored by Artificial Analysis"
               + (f" (Intelligence Index {points[0]['version']})" if points else '') + (f" on {came}" if came else '')
               if points else 'no Artificial Analysis data yet')
    return problems, notes, summary
