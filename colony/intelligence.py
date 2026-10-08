"""Goal-first Auto choices from comparable AA Intelligence Index/task-cost evidence.

Cost per task is token volume per task x price. The free Artificial Analysis API gives every pair's index and
price, current each day. AA's public model pages give the token volume (their cost per task over the price it was
measured at), read once a day as a best effort. A pair whose page hasn't read since today's price is carried: its
last measured volume at today's price, dated. A pair no page ever measured is estimated from the same provider and
labelled so. A page break thus freezes only the volumes, which change rarely, while index and price stay current.
"""
import json
import math
import re
from pathlib import Path

LEVELS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
# Each role's goal: this far below the shared ceiling at Balanced. Rote alone has none: mechanical work takes
# the most points per task-dollar (the person's call).
OFFSETS = {'main': 4, 'routine': 10, 'chores': 20, 'step-up': 0, 'consultant': 0, 'monitor': 2}
POSITIONS = ('Intelligence', 'Intelligence + 1', 'Intelligence + 2', 'Balanced', 'Economy − 2', 'Economy − 1', 'Economy')
TOLERANCE = 1
PAGES = 'https://artificialanalysis.ai/models/'
SLUG = re.compile(r'[a-z0-9][a-z0-9.-]{0,126}')        # a page slug as AA writes them; nothing else is visited
VERSION = re.compile(r'Intelligence Index (v\d+(?:\.\d+)*)')
PRICE = 'Price per 1M tokens (blended 3:1)'             # the API's price_1m_blended_3_to_1: 3 input tokens to 1 output


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
    """Each lineup pair's Intelligence Index and cost per task, within one benchmark version.

    The index is the page's on the day its page was read, else the API's, which is current. The cost is token
    volume x price, and says which it is: measured (the page's own cost per task, read no earlier than today's
    price), carried (the last measured volume at today's price, dated) or estimated (no page ever measured the pair:
    the volume comes from the model's own other efforts where it has them, else from the nearest measured pair of
    the same provider). Volumes come only from page measurements of the chosen version: versions are never spliced.
    """
    from .bench import API_SOURCE
    models = {m: (family, levels or [None]) for family, m, _, levels in lineup}
    groups, api = {}, {}
    for r in rows:
        if r['model'] not in models or not r['source'].startswith('Artificial Analysis') or r['kind'] != 'independent':
            continue
        name = r['benchmark'].lower()
        metric = ('score' if r['domain'] == 'overall' and r['unit'] == 'points' and 'intelligence index' in name else
                  'cost' if r['domain'] == 'cost' and r['unit'] == 'usd' and 'intelligence index' in name
                  and 'per' in name and 'task' in name else
                  'price' if r['domain'] == 'cost' and r['unit'] == 'usd' and 'per 1m tokens' in name else None)
        if not metric or not valid(r['value'], 'score' if metric == 'score' else 'cost'):
            continue
        if r['source'] == API_SOURCE:
            # Today's index and price. A score must name its effort: a variant row could be non-reasoning.
            if metric == 'price' or (metric == 'score' and r.get('effort') in LEVELS):
                slot = api.setdefault(r['model'], {}).setdefault(r.get('effort'), {})
                rank = lambda x: (x['date'], str(x.get('note')), x['value'])     # the newest; any order of rows
                if metric not in slot or rank(r) > rank(slot[metric]):
                    slot[metric] = r
            continue
        if not r.get('version') or (r.get('effort') not in LEVELS and not (
                r.get('effort') is None and models[r['model']][1] == [None]
                and '(reasoning)' in (r.get('note') or '').lower())):
            continue
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

    def price(model, effort):
        """Today's price for the pair from the API (a model's efforts share one), or None."""
        slots = api.get(model, {})
        return (slots.get(effort) or {}).get('price') or next(
            (s['price'] for _, s in sorted(slots.items(), key=lambda x: str(x[0])) if 'price' in s), None)

    for model, points in measured.items():
        stated = [p['price'] for p in points.values() if 'price' in p]
        latest = max(stated, key=lambda x: x['date'])['value'] if stated else None
        for effort, p in points.items():
            if 'cost' in p:
                # Token volume per task: the cost over the price it was measured at (the page's own; else today's,
                # else the model's latest stated). With no price at all it stays in the model's own cost units:
                # right for ratios within the model, never lent to another.
                then = (p.get('price') or price(model, effort) or {}).get('value') or latest
                p['volume'] = dict(value=p['cost']['value'] / (then or 1), url=p['cost']['url'],
                                   date=p['cost']['date'], price=then)

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
            evidence = dict(method='own average score gap' if metric == 'score' else f'own average {metric} ratio',
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

    # The API rounds its index to a tenth and names no version: where it stands in for a page, it says whether it
    # agrees with the pages that were read.
    gaps = sorted(abs(measured[m][e]['score']['value'] - slot['score']['value']) for m, slots in api.items()
                  for e, slot in slots.items() if 'score' in slot and 'score' in measured.get(m, {}).get(e, {}))
    middle = gaps[len(gaps) // 2] if gaps else None
    drift = (None if middle is not None and middle <= 0.1 else
             'API index not yet checked against any page measurement' if middle is None else
             f'API index differs from page measurements by {middle:.2f} points (median): '
             'a new index version, or pages out of date')

    def index(model, effort, point):
        listed = (api.get(model, {}).get(effort) or {}).get('score')
        if 'score' in point and not (listed and listed['date'] > point['score']['date']):
            return point['score']                       # the page's own, on the day it was read
        if listed:                                      # current, from the API
            out = dict(value=listed['value'], url=listed['url'], date=listed['date'], source=API_SOURCE,
                       note=listed.get('note'))
            if drift:
                out['derivation'] = dict(method='Artificial Analysis API index', warning=drift)
            return out
        return estimate(model, effort, 'score')

    def lent(model, effort, score, today):
        """Never measured: the volume of the nearest measured pair of the same provider (the same effort first,
        then the nearest; among those the closest index), at this model's own price today."""
        if today is None or score is None or effort not in LEVELS:
            return None
        donors = [(abs(LEVELS.index(e) - LEVELS.index(effort)), abs(p['score']['value'] - score['value']), donor, e,
                   p['volume']) for donor, points in measured.items() if models[donor][0] == models[model][0]
                  for e, p in points.items() if e in LEVELS and 'score' in p and 'volume' in p and p['volume']['price']]
        if not donors:
            return None
        _, _, donor, e, volume = min(donors, key=lambda d: d[:4])
        value = volume['value'] * today['value']
        return dict(value=value, estimated=True, derivation=dict(
            method='same-provider token volume', donor=donor, donor_effort=e, volume=volume['value'],
            measured=volume['date'], price=today['value'], price_date=today['date'])) if valid(value, 'cost') else None

    def cost_of(model, effort, point, score, today):
        if 'volume' in point:
            volume = point['volume']
            if today is None or volume['date'] >= today['date']:
                return point['cost']                    # measured: the page's own cost per task, on its day
            return dict(value=volume['value'] * today['value'], url=volume['url'], date=volume['date'],
                        carried=volume['date'], derivation=dict(
                            method='carried token volume', volume=volume['value'], measured=volume['date'],
                            price_then=volume['price'], price=today['value'], price_date=today['date']))
        own = estimate(model, effort, 'volume')         # never measured: the model's own other efforts first
        if own:
            unit = today['value'] if today else measured[model][own['derivation']['anchor']]['volume']['price']
            return dict(value=own['value'] * (unit or 1), estimated=True,
                        derivation=dict(own['derivation'], volume=own['value'], price=unit))
        return lent(model, effort, score, today)

    out = []
    for model, (family, levels) in models.items():
        for effort in levels:
            if effort is not None and effort not in LEVELS:
                continue
            point = measured.get(model, {}).get(effort, {})
            score = index(model, effort, point)
            cost = cost_of(model, effort, point, score, price(model, effort))
            if score:
                out.append(dict(model=model, effort=effort, family=family, score=score['value'],
                                cost=cost['value'] if cost else None, version=version,
                                estimated=bool(score.get('estimated') or (cost or {}).get('estimated')),
                                carried=(cost or {}).get('carried'), evidence=dict(score=score, cost=cost)))
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
    warnings = sorted({v.get('derivation', {}).get('warning') for v in chosen['evidence'].values()
                       if v and v.get('derivation', {}).get('warning')})
    return dict(model=chosen['model'], effort=chosen['effort'], policy='intelligence-goal-v2',
                estimated=chosen['estimated'], evidence=evidence,
                why=f"{reason}; Index {chosen['score']:.2f}, ${chosen['cost']:.4g}/task ({chosen['version']})"
                    + ('; estimated evidence' if chosen['estimated'] else '; measured evidence')
                    + (f"; token volume carried from {chosen['carried']}" if chosen.get('carried') else '')
                    + (('; ' + '; '.join(warnings)) if warnings else ''))


# ---------------------------------------------------------------- reading AA's public model pages

def _views(html):
    """Where a page may carry its data: the payload Next.js streams (decoded), the HTML with its escaped quotes
    undone, and the HTML as it is (data in a plain JSON script)."""
    decoder, chunks = json.JSONDecoder(), []
    for m in re.finditer(r'self\.__next_f\.push\(', html):
        try:
            item, _ = decoder.raw_decode(html, m.end())
        except ValueError:
            continue
        if isinstance(item, list) and len(item) > 1 and isinstance(item[1], str):
            chunks.append(item[1])
    return [''.join(chunks), html.replace('\\"', '"'), html]


def _enclosing(text, at, decoder):
    """The innermost JSON object around position `at`: the nearest '{' before it whose object reaches past it."""
    start = at
    for _ in range(2000):
        start = text.rfind('{', 0, start)
        if start < 0:
            return None
        try:
            found, end = decoder.raw_decode(text, start)
        except ValueError:
            continue
        if end > at:
            return found if isinstance(found, dict) else None
    return None


def _field(own, key):
    """A field of a page's own model object, wherever it sits inside it: (found, value). Never one read from
    another model nested in it."""
    queue = [own]
    while queue:
        node = queue.pop(0)
        if key in node:
            return True, node[key]
        for value in node.values():
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, dict) and not ('slug' in child and 'intelligenceIndex' in child):
                    queue.append(child)
    return False, None


def _number(value, paths, what):
    """The number at the first of `paths` inside value that leads to one; None where the page states none yet;
    ValueError for any other shape, so a moved field is loud."""
    for path in paths:
        node = value
        for key in path:
            if not isinstance(node, dict) or key not in node:
                break
            node = node[key]
        else:
            if node is None or (isinstance(node, (int, float)) and not isinstance(node, bool)):
                return node
    raise ValueError(f'{what} is in a shape colony does not read')


def _effort(value):
    if isinstance(value, dict):
        value = next((value[k] for k in ('slug', 'id', 'label', 'name', 'value') if isinstance(value.get(k), str)), None)
    return value.strip().lower() if isinstance(value, str) else None


def _page_price(own):
    """The page's own blended price per 1M tokens, 3 input to 1 output: the API's price_1m_blended_3_to_1."""
    _, blended = _field(own, 'price1mBlended0To3To1')
    if valid(blended, 'cost'):
        return blended
    _, given = _field(own, 'price1mInputTokens')
    _, taken = _field(own, 'price1mOutputTokens')
    return (3 * given + taken) / 4 if valid(given, 'cost') and valid(taken, 'cost') else None


def page_point(html, model, effort, url, date):
    """A model page's own measurement of the pair it is for, found by content: the object whose slug is the page's
    and whose effort is the one asked for, never a comparison model elsewhere on the page. None where the page says
    the pair isn't measured yet; ValueError where it carries nothing usable, so a redesign is loud, never silent."""
    from urllib.parse import urlparse
    slug = urlparse(url).path.rstrip('/').rsplit('/', 1)[-1]
    decoder, key = json.JSONDecoder(), re.compile(r'"slug"\s*:\s*"' + re.escape(slug) + '"')
    views, found = _views(html), []
    for text in views:
        for m in key.finditer(text):
            own = _enclosing(text, m.start(), decoder)
            if own is not None and own.get('slug') == slug and _field(own, 'intelligenceIndex')[0]:
                found.append(own)
        if found:
            break
    if not found:
        raise ValueError(f'No {slug} measurement on the page')
    mine = [own for own in found if _effort(_field(own, 'effort')[1]) == effort]
    if not mine:
        raise ValueError(f"The page's {slug} is at effort {_effort(_field(found[0], 'effort')[1])}, not {effort}")
    version = next((m[1] for m in map(VERSION.search, (html, views[0])) if m), None)
    if not version:
        raise ValueError('No explicit Intelligence Index version on the page')
    readings = set()
    for own in mine:
        score = _number(_field(own, 'intelligenceIndex')[1], ((), ('value',)), 'The Intelligence Index')
        listed, cost = _field(own, 'intelligenceIndexCostPerTask')
        if not listed:
            raise ValueError('No cost per Intelligence Index task on the page')
        cost = _number(cost, (('cost', 'total'), ('total',), ('cost',), ()), 'The cost per task')
        flagged, estimated = _field(own, 'intelligenceIndexIsEstimated')
        readings.add((score, cost, estimated if flagged and isinstance(estimated, bool) else 'unstated', _page_price(own)))
    if len(readings) > 1:
        raise ValueError(f'The page carries conflicting measurements for {slug}')
    score, cost, estimated, price = readings.pop()
    if score is None or cost is None:
        return None
    if not isinstance(estimated, bool):
        raise ValueError('The page does not say whether its index is measured')
    if estimated:
        raise ValueError('The page marks its index as estimated, not measured')
    if not valid(score, 'score') or not valid(cost, 'cost'):
        raise ValueError('Invalid measured score or task cost')
    return dict(model=model, effort=effort, score=score, cost=cost, price=price, version=version, date=date, url=url)


def page_jobs(rows, lineup):
    """The model pages to read, {(model, effort): url}: every lineup pair the free API lists, at the page its own
    slug names, so a new model is read the day it appears; then pages known from earlier records for the rest."""
    from urllib.parse import urlparse
    from . import bench
    efforts = {m: [e for e in levels if e in LEVELS] for _, m, _, levels in lineup}
    jobs = {}
    for entry in bench.snapshot()[0]:
        hit, slug = bench.claim(entry, list(efforts)), entry.get('slug')
        if hit and hit[1] in efforts[hit[0]] and isinstance(slug, str) and SLUG.fullmatch(slug):
            jobs.setdefault(hit, PAGES + slug)
    for r in rows:
        url = urlparse(r.get('url') or '')
        if r['model'] in efforts and url.hostname == 'artificialanalysis.ai' and url.path.startswith('/models/'):
            root = re.sub(r'-(?:low|medium|high|xhigh|max)$', '', url.path.rstrip('/').rsplit('/', 1)[-1])
            for effort in efforts[r['model']] if SLUG.fullmatch(root) else []:
                jobs.setdefault((r['model'], effort), PAGES + root + ('' if effort == 'max' else '-' + effort))
    return jobs


def refresh_due():
    """Due with each day's API fetch, so a page read shares its day with the price, or a day after the last try;
    never more than hourly, however often the API is fetched."""
    import time
    from . import board
    try:
        attempted = json.loads((board.home() / 'bench' / 'pairs-refresh.json').read_text())['attempted_at']
    except (OSError, ValueError, KeyError, TypeError):
        return True
    try:
        fetched = board.epoch(json.loads((board.home() / 'bench' / 'fetched.json').read_text())['at'])
    except (OSError, ValueError, KeyError, TypeError):
        fetched = 0
    since = time.time() - attempted
    return since >= 0.9 * 86400 or (fetched > attempted and since >= 3600)


def refresh(opener=None):
    """Daily public-page read, a best effort: each pair's index, cost per task and the price it was measured at.
    A page that fails leaves its pair's last measurement, and so its token volume, in place."""
    import concurrent.futures
    import time
    import urllib.request
    from . import board, bench
    jobs = sorted(page_jobs(bench.records(), bench.lineup()).items())
    day = board.now()[:10]

    def fetch(job):
        (model, effort), url = job
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'colony benchmark refresh'})
            with (opener or urllib.request.urlopen)(request, timeout=10) as response:
                html = response.read(8 * 1024 * 1024).decode()
            return page_point(html, model, effort, url, day), None
        except Exception as error:              # a page is untrusted input: whatever it does, it is one failed read
            return None, dict(model=model, effort=effort, url=url, error=type(error).__name__, detail=str(error)[:200])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fetched = list(pool.map(fetch, jobs))
    points = [p for p, _ in fetched if p]
    added, bad = bench.add(records(points))
    result = dict(attempted_at=time.time(), at=board.now(), pages=len(jobs), measured=len(points), added=added,
                  unmeasured=[dict(model=m, effort=e, url=u) for ((m, e), u), (p, err) in zip(jobs, fetched)
                              if p is None and err is None],
                  errors=[e for _, e in fetched if e], invalid=bad)
    path = board.home() / 'bench' / 'pairs-refresh.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2))
    return result


def health(entries=None, today=None):
    """Model-data health in short lines: (problems, notes, summary). A problem: most of the last day's page reads
    failed, a redesign or a block colony must follow up. Notes: token volumes carried, with the oldest's age; a
    lineup model with no cost per task at all, which Auto therefore can't choose."""
    import datetime
    from . import bench, board
    try:
        last = json.loads((board.home() / 'bench' / 'pairs-refresh.json').read_text())
    except (OSError, ValueError):
        last = {}
    last = last if isinstance(last, dict) else {}
    pages, errors, day = last.get('pages') or 0, last.get('errors') or [], str(last.get('at') or '')[:10]
    problems, notes = [], []
    if pages and 2 * len(errors) > pages:
        first = errors[0] if isinstance(errors[0], dict) else {}
        problems.append(f"{len(errors)} of {pages} Artificial Analysis model-page reads failed on {day} "
                        f"({first.get('detail') or first.get('error') or 'no detail'}); token volumes are carried "
                        "at today's prices until the pages read again")
    points = pairs(bench.standings() if entries is None else entries)
    carried = sorted(p['carried'] for p in points if p.get('carried'))
    if carried:
        try:
            age = (datetime.date.fromisoformat(today or board.now()[:10]) - datetime.date.fromisoformat(carried[0])).days
            age = f" ({age} day{'s' * (age != 1)} ago)"
        except ValueError:
            age = ''
        notes.append(f"token volume carried at today's price for {len(carried)} pair{'s' * (len(carried) != 1)}, "
                     f"the oldest measured {carried[0]}{age}")
    priced = {p['model'] for p in points if p['cost'] is not None}
    notes += [f"{label} has no cost per task, measured or estimated, so Auto can't choose it"
              for _, model, label, _ in bench.lineup() if model not in priced]
    summary = (f"{last.get('measured', 0)} of {pages} model-page reads succeeded on {day}" if pages else
               'no model-page read yet')
    return problems, notes, summary
