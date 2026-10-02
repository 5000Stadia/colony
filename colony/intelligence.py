"""Goal-first Auto choices from comparable AA Intelligence Index/task-cost evidence."""
import json
import math
import re
from pathlib import Path

LEVELS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
OFFSETS = {'main': 4, 'runtime': 4, 'routine': 10, 'step-up': 0, 'consultant': 0, 'monitor': 2}
POSITIONS = ('Intelligence', 'Intelligence + 1', 'Intelligence + 2', 'Balanced', 'Economy − 2', 'Economy − 1', 'Economy')
TOLERANCE = 1


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
                                           ('cost', 'cost', 'Cost per Intelligence Index task', 'usd')):
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
    """One benchmark version, dated per-model cohorts; no API/token-price splicing."""
    models = {m: (family, levels or [None]) for family, m, _, levels in lineup}
    groups = {}
    for r in rows:
        if (r['model'] not in models or not r['source'].startswith('Artificial Analysis')
                or r['kind'] != 'independent' or not r.get('version')
                or 'intelligence index' not in r['benchmark'].lower()
                or (r.get('effort') not in LEVELS and not (r.get('effort') is None and models[r['model']][1] == [None]
                    and '(reasoning)' in (r.get('note') or '').lower()))):
            continue
        metric = ('score' if r['domain'] == 'overall' and r['unit'] == 'points' else
                  'cost' if r['domain'] == 'cost' and r['unit'] == 'usd'
                  and 'per' in r['benchmark'].lower() and 'task' in r['benchmark'].lower() else None)
        if metric and valid(r['value'], metric):
            cohort = groups.setdefault((r['model'], r['version'], r['date'], r['source']), {})
            cohort.setdefault(r['effort'], {})[metric] = dict(value=r['value'], url=r['url'], date=r['date'])
    versions = {ver for (_, ver, _, _), points in groups.items() if any('score' in p for p in points.values())}
    if not versions:
        return []
    version = max(versions, key=lambda v: (tuple(map(int, re.findall(r'\d+', v))), v))
    measured = {}
    for (model, ver, date, source), points in sorted(groups.items(), key=lambda x: x[0][2:]):
        if ver != version:
            continue
        mine = measured.setdefault(model, {})
        for effort, point in points.items():
            # Refresh pages independently, retaining failed/missing efforts. Prefer a
            # coherent complete pair to splicing a newer score into an older cost.
            if ('score' in point and 'cost' in point) or not all(k in mine.get(effort, {}) for k in ('score', 'cost')):
                mine[effort] = point

    def estimate(model, effort, metric):
        mine = measured.get(model, {})
        known = sorted(((LEVELS.index(e), p[metric]) for e, p in mine.items() if metric in p and e in LEVELS), key=lambda x: x[0])
        if not known or effort not in LEVELS:
            return None
        target = LEVELS.index(effort)
        anchor, point = min(known, key=lambda x: (abs(x[0] - target), x[0]))
        if len(known) >= 2:
            steps = [((b['value'] - a['value']) / (j - i) if metric == 'score' else
                      (b['value'] / a['value']) ** (1 / (j - i))) for (i, a), (j, b) in zip(known, known[1:])]
            gap = sum(steps) / len(steps)
            value = point['value'] + gap * (target - anchor) if metric == 'score' else point['value'] * gap ** (target - anchor)
            evidence = dict(method='own average score gap' if metric == 'score' else 'own average cost ratio',
                            anchor=LEVELS[anchor], step=gap, measured=[dict(effort=LEVELS[i], **p) for i, p in known])
        else:
            # Donor proximity is always intelligence at the anchor, even for cost estimates.
            anchor_score = mine.get(LEVELS[anchor], {}).get('score')
            if not anchor_score:
                return None
            donors = []
            for donor, points in measured.items():
                a, b = points.get(LEVELS[anchor], {}), points.get(effort, {})
                if (donor != model and models[donor][0] == models[model][0] and 'score' in a
                        and metric in a and metric in b and a[metric]['value'] > 0):
                    donors.append((abs(a['score']['value'] - anchor_score['value']), donor, a, b))
            if not donors:
                return None
            _, donor, a, b = min(donors, key=lambda x: x[:2])
            ratio = b[metric]['value'] / a[metric]['value']
            value = point['value'] * ratio
            evidence = dict(method='same-provider percentage spread', donor=donor, anchor=LEVELS[anchor],
                            ratio=ratio, donor_anchor=a[metric], donor_target=b[metric], measured=point)
        below = [p['value'] for i, p in known if i < target]
        above = [p['value'] for i, p in known if i > target]
        if below and above and not min(below[-1], above[0]) <= value <= max(below[-1], above[0]):
            evidence['warning'] = 'Estimate falls outside its measured neighbours'
        return dict(value=value, estimated=True, derivation=evidence) if valid(value, metric) else None

    out = []
    for model, (family, levels) in models.items():
        for effort in levels:
            if effort is not None and effort not in LEVELS:
                continue
            point = measured.get(model, {}).get(effort, {})
            score = point.get('score') or estimate(model, effort, 'score')
            cost = point.get('cost') or estimate(model, effort, 'cost')
            if score:
                out.append(dict(model=model, effort=effort, family=family, score=score['value'],
                                cost=cost['value'] if cost else None, version=version,
                                estimated=bool(score.get('estimated') or (cost or {}).get('estimated')),
                                evidence=dict(score=score, cost=cost)))
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
    goal = None if role == 'chores' else max(0, ceiling - max(0, OFFSETS[role] + shift))
    if role == 'chores':
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
    warnings = sorted({v.get('derivation', {}).get('warning') for v in chosen['evidence'].values()
                       if v and v.get('derivation', {}).get('warning')})
    return dict(model=chosen['model'], effort=chosen['effort'], policy='intelligence-goal-v2',
                estimated=chosen['estimated'], evidence=evidence,
                why=f"{reason}; Index {chosen['score']:.2f}, ${chosen['cost']:.4g}/task ({chosen['version']})"
                    + ('; estimated evidence' if chosen['estimated'] else '; measured evidence')
                    + (('; ' + '; '.join(warnings)) if warnings else ''))


def page_point(html, model, effort, url, date):
    """Decode this page's currentModel, never a comparison model later in the HTML."""
    from urllib.parse import urlparse
    text = html.replace('\\"', '"')
    marker = '"currentModel":'
    start = text.find(marker)
    if start < 0:
        raise ValueError('No currentModel evidence')
    own, _ = json.JSONDecoder().raw_decode(text[start + len(marker):])
    expected = urlparse(url).path.rstrip('/').rsplit('/', 1)[-1]
    if own.get('slug') != expected or (own.get('effort') or {}).get('slug') != effort:
        raise ValueError('Page model or effort does not match request')
    version = re.search(r'Intelligence Index (v\d+\.\d+\.\d+)', text)
    if not version or own.get('intelligenceIndexIsEstimated'):
        raise ValueError('No explicit measured benchmark version')
    score = own.get('intelligenceIndex')
    cost = ((own.get('intelligenceIndexCostPerTask') or {}).get('cost') or {}).get('total')
    if score is None or cost is None:
        return None
    if not valid(score, 'score') or not valid(cost, 'cost'):
        raise ValueError('Invalid measured score or task cost')
    return dict(model=model, effort=effort, score=score, cost=cost, version=version[1], date=date, url=url)


def refresh_due():
    import time
    from . import board
    path = board.home() / 'bench' / 'pairs-refresh.json'
    try:
        return time.time() - json.loads(path.read_text())['attempted_at'] >= 86400
    except (OSError, ValueError, KeyError, TypeError):
        return True


def refresh(opener=None):
    """Daily public-page refresh; failed pages preserve previous per-effort measurements."""
    import concurrent.futures
    import time
    import urllib.request
    from urllib.parse import urlparse
    from . import board, bench
    rows = bench.records()
    models = {m: efforts for _, m, _, efforts in bench.lineup()}
    roots = {}
    for r in rows:
        url = urlparse(r.get('url') or '')
        if r['model'] in models and url.hostname == 'artificialanalysis.ai' and url.path.startswith('/models/'):
            slug = url.path.rstrip('/').rsplit('/', 1)[-1]
            roots[r['model']] = re.sub(r'-(?:low|medium|high|xhigh|max)$', '', slug)
    jobs = [(model, effort, 'https://artificialanalysis.ai/models/' + slug + ('' if effort == 'max' else '-' + effort))
            for model, slug in roots.items() for effort in models[model] if effort in LEVELS]
    def fetch(job):
        model, effort, url = job
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'colony benchmark refresh'})
            with (opener or urllib.request.urlopen)(request, timeout=10) as response:
                html = response.read(8 * 1024 * 1024).decode()
            point = page_point(html, model, effort, url, board.now()[:10])
            return point, None
        except (OSError, ValueError, KeyError, TypeError) as error:
            return None, dict(model=model, effort=effort, error=type(error).__name__)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fetched = list(pool.map(fetch, jobs))
    points = [p for p, _ in fetched if p]
    added, bad = bench.add(records(points))
    result = dict(attempted_at=time.time(), at=board.now(), pages=len(jobs), measured=len(points), added=added,
                  errors=[e for _, e in fetched if e], invalid=bad)
    path = board.home() / 'bench' / 'pairs-refresh.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2))
    return result
