import copy
import unittest
from colony import intelligence as policy


class EffortPolicyTest(unittest.TestCase):
    def setUp(self):
        self.rows = policy.bundled_records()
        self.lineup = [(family, model, model, ['low','medium','high','xhigh','max','ultra'])
                       for family, model in [('claude','claude-opus-5-5'), ('claude','claude-sonnet-5-5'),
                                             ('codex','gpt-6-astra'), ('codex','gpt-6.1-sol')]]
        self.points = policy.candidates(self.rows, self.lineup)

    def test_shared_goal_and_one_tolerance_choose_sol_max_instead_of_astra(self):
        main = policy.pick('codex', 'main', self.points)
        self.assertEqual((main['model'],main['effort']), ('gpt-6.1-sol','max'))
        self.assertAlmostEqual(main['evidence']['ceiling'],57.6223698102963)
        self.assertAlmostEqual(main['evidence']['goal'],53.6223698102963)
        self.assertAlmostEqual(main['evidence']['target'],51.673669395513)
        self.assertIn('below goal',main['why'])
        claude = policy.pick('claude', 'main', self.points)
        self.assertEqual((claude['model'],claude['effort']), ('claude-opus-5-5','high'))
        self.assertEqual(policy.pick('claude','consultant',self.points)['effort'],'max')

    def test_sonnet_low_uses_own_mean_score_gap_and_positive_cost_ratios(self):
        low = next(p for p in self.points if p['model']=='claude-sonnet-5-5' and p['effort']=='low')
        self.assertAlmostEqual(low['score'],40.7386909594307 - (55.9779549012591-40.7386909594307)/3)
        self.assertGreater(low['cost'],0)
        self.assertTrue(low['estimated'])
        self.assertEqual(low['evidence']['score']['derivation']['method'],'own average score gap')

    def test_exactly_one_effort_uses_same_provider_nearest_measured_donor(self):
        # The new model is anchored near Sol, so Sol's spread wins over Astra.
        model='single'
        rows=self.rows+[dict(r,model=model,value=50 if r['domain']=='overall' else 1)
                        for r in self.rows if r['model']=='gpt-6.1-sol' and r['effort']=='high']
        points=policy.candidates(rows,self.lineup+[('codex',model,model,['low','high'])])
        low=next(p for p in points if p['model']==model and p['effort']=='low')
        self.assertEqual(low['evidence']['score']['derivation']['donor'],'gpt-6.1-sol')
        self.assertAlmostEqual(low['score'],50*42.0835618555848/50.2377777519769)
        isolated=policy.candidates(rows,[('third',model,model,['low','high'])]+self.lineup)
        self.assertFalse(any(p['model']==model and p['effort']=='low' for p in isolated))

    def test_missing_cost_never_uses_token_price_or_weaker_bargain(self):
        points=copy.deepcopy(self.points)
        for p in points:
            if p['family']=='codex' and p['score']>=51.67:p['cost']=None
        self.assertIsNone(policy.pick('codex','main',points))

    def test_slider_goals_monotone_and_no_usage_input(self):
        goals=[policy.pick('codex','main',self.points,i)['evidence']['goal'] for i in range(7)]
        self.assertEqual(goals,sorted(goals,reverse=True))
        self.assertEqual(goals[0],57.6223698102963)
        self.assertEqual(goals[6],47.6223698102963)

    def test_economy_keeps_judgement_at_the_ceiling(self):
        for role in ('consultant', 'step-up'):
            balanced = policy.pick('claude', role, self.points, 3)
            for position in range(7):
                pick = policy.pick('claude', role, self.points, position)
                self.assertEqual((pick['model'], pick['effort']), (balanced['model'], balanced['effort']))
                self.assertEqual(pick['evidence']['goal'], pick['evidence']['ceiling'])
        for role in ('main', 'routine', 'chores', 'monitor'):
            self.assertLess(policy.pick('claude', role, self.points, 6)['evidence']['goal'],
                            policy.pick('claude', role, self.points, 3)['evidence']['goal'])

    def test_rote_takes_the_most_points_per_task_dollar_whatever_the_balance(self):
        for family in ('claude', 'codex'):
            priced = [p for p in self.points if p['family'] == family and p['cost']]
            best = max(priced, key=lambda p: p['score'] / p['cost'])
            for position in range(7):
                rote = policy.pick(family, 'rote', self.points, position)
                self.assertEqual((rote['model'], rote['effort']), (best['model'], best['effort']), f'{family} at {position}')
                self.assertIsNone(rote['evidence']['goal'], 'no goal: value alone')
                self.assertTrue(rote['why'].startswith('Most Intelligence Index points per task-dollar'))
        self.assertEqual(policy.pick('codex', 'rote', self.points)['effort'], 'low')

    def test_chores_takes_the_cheapest_pair_within_twenty_points_and_moves_with_the_balance(self):
        ceiling = max(p['score'] for p in self.points)
        claude = [p for p in self.points if p['family'] == 'claude' and p['cost']]
        picks = {}
        for position in range(7):
            chores = policy.pick('claude', 'chores', self.points, position)
            goal = ceiling - max(0, 20 + 2 * (position - 3))
            self.assertAlmostEqual(chores['evidence']['goal'], goal)
            fits = [p for p in claude if p['score'] >= min(goal, max(x['score'] for x in claude)) - 1]
            cheapest = min(fits, key=lambda p: p['cost'])
            self.assertEqual((chores['model'], chores['effort']), (cheapest['model'], cheapest['effort']), f'at {position}')
            picks[position] = (chores['model'], chores['effort'])
        self.assertEqual(picks[3], ('claude-opus-5-5', 'low'), 'Balanced: twenty points below the ceiling, cheapest first')
        self.assertEqual(picks[0], ('claude-sonnet-5-5', 'high'), 'toward Intelligence the goal rises')
        self.assertEqual(picks[6], ('claude-sonnet-5-5', 'low'), 'toward Economy it falls')

    def test_minor_discernment_gets_a_smarter_pair_than_the_value_pick_and_a_cheaper_one_than_before(self):
        # Today's shape: the value pick lands on Haiku low; chores on Haiku high, above the Sonnet low the old
        # value pick once held, and a fraction of its cost.
        points = [dict(model=m, effort=e, score=s, cost=c, family='claude', version='v4.3.2', estimated=False, evidence={})
                  for m, e, s, c in [('claude-opus-5-5', 'max', 57.62, 5.982), ('claude-sonnet-5-5', 'low', 35.87, 0.417),
                                     ('claude-haiku-5-5', 'low', 29.45, 0.0245), ('claude-haiku-5-5', 'medium', 34.46, 0.047),
                                     ('claude-haiku-5-5', 'high', 37.82, 0.0794), ('claude-haiku-5-5', 'xhigh', 41.25, 0.1238)]]
        rote, chores = (policy.pick('claude', role, points) for role in ('rote', 'chores'))
        self.assertEqual((rote['model'], rote['effort']), ('claude-haiku-5-5', 'low'))
        self.assertEqual((chores['model'], chores['effort']), ('claude-haiku-5-5', 'high'))
        self.assertAlmostEqual(chores['evidence']['goal'], 37.62)

    def test_tolerance_does_not_chain_through_intermediate_pairs(self):
        points=[dict(model=str(i),effort='high',score=50-i*.8,cost=10-i,family='codex',version='v1',estimated=False,evidence={}) for i in range(4)]
        pick=policy.pick('codex','consultant',points)
        self.assertEqual(pick['model'],'1')
        self.assertEqual(policy.pick('codex','consultant',points,blocked=[{'model':'1','effort':'high'}])['model'],'0')

    def test_ultra_and_unsupported_efforts_do_not_raise_ceiling(self):
        rows=self.rows+[dict(self.rows[0],effort='ultra',value=99)]
        lineup=[(f,m,n,['high']) for f,m,n,_ in self.lineup]
        points=policy.candidates(rows,lineup)
        self.assertEqual({p['effort'] for p in points},{'high'})
        self.assertLess(max(p['score'] for p in points),54)

    def test_versions_are_never_spliced_and_measured_replaces_estimated(self):
        rows=self.rows+[dict(r,effort='low',value=34 if r['domain']=='overall' else .3)
                        for r in self.rows if r['model']=='claude-sonnet-5-5' and r['effort']=='medium']
        low=next(p for p in policy.candidates(rows,self.lineup) if p['model']=='claude-sonnet-5-5' and p['effort']=='low')
        self.assertFalse(low['estimated'])
        self.assertEqual(low['score'],34)
        rows=[dict(r,version='v99') if r['domain']=='overall' else r for r in self.rows]
        points=policy.candidates(rows,self.lineup)
        self.assertTrue(all(p['cost'] is None for p in points))
        self.assertIsNone(policy.pick('codex','main',points))


if __name__=='__main__':unittest.main()


class RefreshEvidenceTest(unittest.TestCase):
    def test_partial_refresh_preserves_other_measured_efforts(self):
        rows=policy.bundled_records()
        model='claude-opus-5-5'
        lineup=[('claude',model,model,['low','medium','high','xhigh','max'])]
        before=policy.candidates(rows,lineup)
        rows += [dict(r,date='2026-10-08') for r in rows if r['model']==model and r['effort']=='low']
        after=policy.candidates(rows,lineup)
        self.assertEqual([(p['score'],p['cost'],p['estimated']) for p in before],[(p['score'],p['cost'],p['estimated']) for p in after])
        self.assertEqual(after[0]['evidence']['score']['date'],'2026-10-08')
        self.assertEqual(after[-1]['evidence']['score']['date'],'2026-10-01')

    def test_page_parser_does_not_take_a_comparison_models_cost(self):
        import json
        own=dict(slug='sonnet-low',effort={'slug':'low'},intelligenceIndex=None,intelligenceIndexCostPerTask=None)
        html='Intelligence Index v4.3.2 "currentModel":'+json.dumps(own,separators=(',',':'))+',"other":{"intelligenceIndex":99,"intelligenceIndexCostPerTask":{"cost":{"total":1}}}'
        self.assertIsNone(policy.page_point(html,'sonnet','low','https://artificialanalysis.ai/models/sonnet-low','2026-10-01'))
        own.update(intelligenceIndex=35,intelligenceIndexCostPerTask={'cost':{'total':.3}})
        html='Intelligence Index v4.3.2 "currentModel":'+json.dumps(own,separators=(',',':'))
        with self.assertRaisesRegex(ValueError,'whether its index is measured'):
            policy.page_point(html,'sonnet','low','https://artificialanalysis.ai/models/sonnet-low','2026-10-01')
        own.update(intelligenceIndexIsEstimated=False)
        html='Intelligence Index v4.3.2 "currentModel":'+json.dumps(own,separators=(',',':'))
        self.assertEqual(policy.page_point(html,'sonnet','low','https://artificialanalysis.ai/models/sonnet-low','2026-10-01')['cost'],.3)
        with self.assertRaises(ValueError):policy.page_point(html,'sonnet','max','https://artificialanalysis.ai/models/sonnet-low','2026-10-01')

    def test_hidden_middle_effort_is_deterministic_and_flagged_if_outside_neighbours(self):
        rows=[r for r in policy.bundled_records() if not(r['model']=='gpt-6.1-sol' and r['effort']=='xhigh')]
        lineup=[('codex','gpt-6.1-sol','Sol',['low','medium','high','xhigh','max'])]
        points=policy.candidates(rows,lineup)
        estimated=next(p for p in points if p['effort']=='xhigh')
        self.assertGreater(estimated['score'],next(p['score'] for p in points if p['effort']=='max'))
        self.assertIn('warning',estimated['evidence']['score']['derivation'])
        self.assertEqual(points,policy.candidates(list(reversed(rows)),lineup))

    def test_family_fallback_uses_only_unblocked_candidates(self):
        points=[dict(model=m,effort='high',score=s,cost=c,family='codex',version='v1',estimated=False,evidence={})
                for m,s,c in [('top',60,2),('other',50,1)]]
        pick=policy.pick('codex','monitor',points,blocked=[{'model':'top','effort':'high'}])
        self.assertEqual(pick['model'],'other')
        self.assertEqual(pick['evidence']['ceiling'],60)
        self.assertEqual(pick['evidence']['family_best'],50)
        self.assertIn('8.00 below goal',pick['why'])


# ---------------------------------------------------------------- reading AA's pages; cost as volume x price

import argparse
import contextlib
import html
import io
import json
import os
import re
import socket
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import patch

from tests.test_board import BoardBase

FIXTURE = Path(__file__).with_name('fixtures') / 'aa-claude-sonnet-5-5-high.html'
PAGE = 'https://artificialanalysis.ai/models/'
EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']


def fixture_models():
    """The fixture's two model objects as the page streams them: its own (Sonnet 5.5 at high), then a comparison."""
    html = FIXTURE.read_text()
    payload = ''.join(json.loads(m[1])[1] for m in re.finditer(r'self\.__next_f\.push\((.*?)\)</script>', html))

    def holders(x):
        if isinstance(x, dict):
            if 'initialModels' in x:
                yield x
            for v in x.values():
                yield from holders(v)
        elif isinstance(x, list):
            for v in x:
                yield from holders(v)
    line = next(l for l in payload.split('\n') if '"initialModels"' in l)
    return next(holders(json.loads(line[line.index(':') + 1:])))['initialModels']


def streamed(*models, version='v4.3.2'):
    """A model page as AA serves it: its version line, and Next.js payload chunks carrying the models."""
    chunk = '38:' + json.dumps(['$', '$L48', None, {'initialModels': list(models)}], separators=(',', ':')) + '\n'
    head = f'<span>Artificial Analysis Intelligence Index {version} incorporates 10 evaluations</span>' if version else ''
    return f'<html><body>{head}<script>self.__next_f.push({json.dumps([1, chunk])})</script></body></html>'


def measured(slug, effort, index, cost, price):
    return dict(slug=slug, name=slug, effort=dict(slug=effort, label=effort, level=40), intelligenceIndex=index,
                intelligenceIndexIsEstimated=False, price1mBlended0To3To1=price, intelligenceIndexCost=dict(total=999.0),
                intelligenceIndexCostPerTask=dict(cost=dict(total=cost)))


def api_entry(name, slug, effort, index, price):
    """One entry of the free API's reply, as Artificial Analysis words it."""
    return dict(name=f"{name} ({effort.capitalize()}, Default Fallback)", slug=slug + ('' if effort == 'max' else '-' + effort),
                model_creator=dict(slug='anthropic'), evaluations=dict(artificial_analysis_intelligence_index=index),
                pricing=dict(price_1m_blended_3_to_1=price, price_1m_input_tokens=price / 2, price_1m_output_tokens=price * 2.5))


class Reply:
    def __init__(self, body):
        self.body = body

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@unittest.skipUnless(FIXTURE.exists(), 'a real model page is kept on this machine only, not in the public repository')
class PagePointTest(unittest.TestCase):
    """A model page is read by content: the object that is the page's own pair, never a comparison model."""
    def read(self, html, slug, effort, model='claude-sonnet-5-5'):
        return policy.page_point(html, model, effort, PAGE + slug, '2026-10-07')

    def test_a_real_page_gives_its_own_pair_never_a_comparison_or_a_whole_run(self):
        html = FIXTURE.read_text()
        point = self.read(html, 'claude-sonnet-5-5-high', 'high')
        self.assertEqual((point['score'], point['cost'], point['price'], point['version']),
                         (46.7533528066958, 0.8844498581005511, 4, 'v4.3.2'), 'not the run total (1027.40) or max (5.46)')
        self.assertEqual({r['benchmark'] for r in policy.records([point])},
                         {'Intelligence Index', 'Cost per Intelligence Index task', policy.PRICE}, 'the price it was measured at')
        other = self.read(html, 'claude-sonnet-5-5', 'max')
        self.assertEqual((other['score'], other['cost']), (56.0001286580794, 5.4608525948098325),
                         'chosen by slug and effort; the release entries sharing that slug carry no measurement')
        with self.assertRaisesRegex(ValueError, 'at effort high, not max'):
            self.read(html, 'claude-sonnet-5-5-high', 'max')
        with self.assertRaisesRegex(ValueError, 'No claude-opus-5-5-high measurement'):
            self.read(html, 'claude-opus-5-5-high', 'high', 'claude-opus-5-5')

    def test_a_field_moving_inside_the_payload_still_reads(self):
        own, other = fixture_models()
        expect = (46.7533528066958, 0.8844498581005511)
        old = 'Intelligence Index v4.3.2 "currentModel":' + json.dumps(own) + ',"comparisons":[' + json.dumps(other) + ']'
        script = ('<p>Artificial Analysis Intelligence Index v4.3.2</p><script type="application/json">'
                  + json.dumps({'props': {'page': {'models': [other, {'model': own}]}}}) + '</script>')
        moved = {k: v for k, v in own.items() if k not in ('intelligenceIndex', 'intelligenceIndexIsEstimated', 'effort')}
        moved.update(effort='High', intelligenceIndexCostPerTask={'total': expect[1]},
                     intelligence={'intelligenceIndex': expect[0], 'intelligenceIndexIsEstimated': False})
        for name, html in (('the old marker', old), ('a plain JSON script', script), ('fields moved', streamed(other, moved))):
            with self.subTest(name):
                point = self.read(html, 'claude-sonnet-5-5-high', 'high')
                self.assertEqual((point['score'], point['cost']), expect)

    def test_a_page_with_nothing_usable_fails_loudly_and_an_unmeasured_pair_is_no_failure(self):
        own, other = fixture_models()
        changed = lambda **change: streamed(other, {**own, **change})
        without = lambda key: streamed(other, {k: v for k, v in own.items() if k != key})
        cases = {'No claude-sonnet-5-5-high measurement': '<html><body>Just a moment...</body></html>',
                 'No explicit Intelligence Index version': streamed(own, other, version=None),
                 'whether its index is measured': without('intelligenceIndexIsEstimated'),
                 'estimated, not measured': changed(intelligenceIndexIsEstimated=True),
                 'No cost per Intelligence Index task': without('intelligenceIndexCostPerTask'),
                 'shape colony does not read': changed(intelligenceIndexCostPerTask={'usd': 0.88}),
                 'Invalid measured score': changed(intelligenceIndex=140),
                 'conflicting measurements': streamed(own, {**own, 'intelligenceIndex': 50.0})}
        for message, html in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                self.read(html, 'claude-sonnet-5-5-high', 'high')
        for cost in (None, {'cost': {'total': None}}):
            self.assertIsNone(self.read(changed(intelligenceIndexCostPerTask=cost), 'claude-sonnet-5-5-high', 'high'))


class PageRefreshTest(unittest.TestCase):
    """The daily page read: which pages, how politely, what it keeps and what it says; never the live colony home."""
    def setUp(self):
        from colony import bench, board
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        lineup = [('claude', 'claude-sonnet-5-5', 'Sonnet 5.5', EFFORTS), ('claude', 'claude-haiku-5-5', 'Haiku 5.5', EFFORTS),
                  ('codex', 'gpt-6-luna', 'GPT-6 Luna', EFFORTS)]
        for p in (patch.dict(os.environ, COLONY_BOARD_HOME=self.tmp.name), patch.object(policy, 'bundled_records', return_value=[]),
                  patch.object(bench, 'lineup', return_value=lineup)):
            p.start()
            self.addCleanup(p.stop)
        self.bench, self.today = bench, board.now()[:10]
        # Today's free-API reply lists Haiku 5.5, new, at every effort (one entry's slug isn't a page name), and
        # Sonnet 5.5 at max and high; an earlier page record knows Sonnet 5.5's pages; GPT-6 Luna is in neither.
        haiku = {'max': 43.4, 'xhigh': 41.2, 'high': 37.8, 'medium': 34.5, 'low': 29.4}
        entries = ([dict(api_entry('Claude Haiku 5.5', 'claude-haiku-5-5', 'low', 29.4, 0.2), slug='claude-haiku-5-5/low')]
                   + [api_entry('Claude Haiku 5.5', 'claude-haiku-5-5', e, i, 0.2) for e, i in haiku.items()]
                   + [api_entry('Claude Sonnet 5.5', 'claude-sonnet-5-5', e, i, 4) for e, i in (('max', 56.0), ('high', 46.8))])
        bench.snapshots_dir().mkdir(parents=True)
        (bench.snapshots_dir() / f'{self.today}T000000.json').write_text(json.dumps(entries))
        bench.add([dict(model='claude-sonnet-5-5', effort='low', source='Artificial Analysis', kind='independent',
                        benchmark='Intelligence Index', version='v4.3.2', domain='overall', value=35.9, unit='points',
                        date='2026-10-01', url=PAGE + 'claude-sonnet-5-5-low', note='Primary model-page measurement')])
        self.seen = []
        comparison = measured('claude-opus-5-5', 'max', 57.6, 6.0, 8)
        self.pages = {PAGE + 'claude-haiku-5-5' + ('' if e == 'max' else '-' + e): streamed(
            measured('claude-haiku-5-5' + ('' if e == 'max' else '-' + e), e, i, None if e == 'low' else i / 500, 0.2), comparison)
            for e, i in haiku.items()}
        self.pages.update({PAGE + 'claude-sonnet-5-5': streamed(comparison, measured('claude-sonnet-5-5', 'max', 56.0, 5.46, 4)),
                           PAGE + 'claude-sonnet-5-5-high': streamed(measured('claude-sonnet-5-5-high', 'high', 46.75, 0.88, 4)),
                           PAGE + 'claude-sonnet-5-5-low': streamed(measured('claude-sonnet-5-5-low', 'low', 35.87, 0.42, 4)),
                           PAGE + 'claude-sonnet-5-5-medium': '<html><body>Just a moment...</body></html>',
                           PAGE + 'claude-sonnet-5-5-xhigh': urllib.error.HTTPError(PAGE, 403, 'Forbidden', {}, None)})

    def opener(self, request, timeout):
        self.seen.append((request.full_url, request.get_header('User-agent')))
        page = self.pages.get(request.full_url, urllib.error.HTTPError(request.full_url, 404, 'Not Found', {}, None))
        if isinstance(page, Exception):
            raise page
        return Reply(page.encode())

    def test_a_new_model_is_read_the_day_the_api_lists_it_politely_and_its_failures_say_why(self):
        result = policy.refresh(self.opener)
        self.assertEqual(sorted(u for u, _ in self.seen), sorted(self.pages), 'API slugs for Haiku, then known pages')
        self.assertEqual({agent for _, agent in self.seen}, {'colony benchmark refresh'})
        self.assertEqual((result['pages'], result['measured']), (10, 7))
        self.assertEqual([(u['model'], u['effort']) for u in result['unmeasured']], [('claude-haiku-5-5', 'low')])
        why = {(f['model'], f['effort']): f['detail'] for f in result['errors']}
        self.assertIn('403', why[('claude-sonnet-5-5', 'xhigh')])
        self.assertIn('No claude-sonnet-5-5-medium measurement', why[('claude-sonnet-5-5', 'medium')])
        points = {(p['model'], p['effort']): p for p in policy.pairs(self.bench.standings())}
        top = points[('claude-haiku-5-5', 'max')]
        self.assertEqual((top['score'], top['cost'], top['estimated'], top['carried']), (43.4, 43.4 / 500, False, None))
        low = points[('claude-haiku-5-5', 'low')]
        self.assertEqual((low['score'], low['estimated']), (29.4, True), "the API's index; a volume from its own efforts")
        self.assertEqual(low['evidence']['cost']['derivation']['method'], 'own average volume ratio')
        self.assertEqual(policy.health(today=self.today)[0], [], 'two of ten failing is no alarm')
        self.pages = {}
        policy.refresh(self.opener)
        problems, _, _ = policy.health(today=self.today)
        self.assertEqual(len(problems), 1)
        self.assertIn('10 of 10 Artificial Analysis model-page reads failed', problems[0])
        self.assertIn('HTTP Error 404', problems[0])
        after = {(p['model'], p['effort']): p for p in policy.pairs(self.bench.standings())}
        self.assertEqual(after[('claude-haiku-5-5', 'max')]['cost'], top['cost'], 'a failed read keeps what was measured')

    def test_the_page_read_follows_each_api_fetch_and_otherwise_comes_daily(self):
        import time
        from colony import board
        self.assertTrue(policy.refresh_due(), 'never read')
        path = board.home() / 'bench' / 'pairs-refresh.json'
        path.write_text(json.dumps(dict(attempted_at=time.time() - 7200)))
        self.assertFalse(policy.refresh_due())
        (board.home() / 'bench' / 'fetched.json').write_text(json.dumps(dict(at=board.now())))
        self.assertTrue(policy.refresh_due(), 'the API was fetched since: read the pages with it')
        path.write_text(json.dumps(dict(attempted_at=time.time() - 600)))
        self.assertFalse(policy.refresh_due(), 'however often the API is fetched, pages at most hourly')
        path.write_text(json.dumps(dict(attempted_at=time.time() + 60)))
        self.assertFalse(policy.refresh_due())
        path.write_text(json.dumps(dict(attempted_at=time.time() - 0.95 * 86400)))
        self.assertTrue(policy.refresh_due())


def page_rows(model, effort, score, cost, price, date):
    """A model page's measurement as colony keeps it: index, cost per task and the price it was measured at."""
    return policy.records([dict(model=model, effort=effort, score=score, cost=cost, price=price, version='v4.3.2',
                                date=date, url=PAGE + model + ('' if effort == 'max' else '-' + effort))])


class TokenVolumeTest(unittest.TestCase):
    """Cost per task is token volume x today's price: measured, carried or estimated, and always said which."""
    lineup = [('claude', m, m, EFFORTS) for m in ('claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5')] + [
        ('codex', 'gpt-6-luna', 'gpt-6-luna', EFFORTS)]

    def api(self, date, entries):
        from colony import bench
        return bench.records_from(entries, [m for _, m, _, _ in self.lineup], date)[0]

    def pairs(self, rows, model):
        return {p['effort']: p for p in policy.candidates(rows, self.lineup) if p['model'] == model}

    def test_a_page_that_stops_reading_carries_its_volume_at_todays_price_until_it_reads_again(self):
        rows = page_rows('claude-opus-5-5', 'high', 53.58, 1.8, 8, '2026-10-01') + page_rows('claude-opus-5-5', 'max', 57.62, 6.0, 8, '2026-10-01')
        rows += self.api('2026-10-07', [api_entry('Claude Opus 5.5', 'claude-opus-5-5', e, i, 4) for e, i in (('high', 53.6), ('max', 57.6))])
        high = self.pairs(rows, 'claude-opus-5-5')['high']
        self.assertAlmostEqual(high['cost'], 1.8 / 8 * 4, msg="the last volume at today's halved price")
        self.assertEqual((high['carried'], high['estimated'], high['score']), ('2026-10-01', False, 53.6), 'the index stays current')
        self.assertEqual(high['evidence']['cost']['derivation']['method'], 'carried token volume')
        step_up = policy.pick('claude', 'step-up', list(self.pairs(rows, 'claude-opus-5-5').values()))
        self.assertEqual(step_up['effort'], 'max')
        self.assertIn('measured evidence; token volume carried from 2026-10-01', step_up['why'])
        rows += page_rows('claude-opus-5-5', 'high', 53.61, 0.95, 4, '2026-10-07')
        high = self.pairs(rows, 'claude-opus-5-5')['high']
        self.assertEqual((high['cost'], high['carried'], high['score']), (0.95, None, 53.61), 'read again: measured')

    def test_a_measurement_kept_without_its_price_is_carried_at_todays(self):
        rows = [r for r in page_rows('claude-opus-5-5', 'high', 53.58, 1.8, 8, '2026-10-01') if r['benchmark'] != policy.PRICE]
        rows += self.api('2026-10-07', [api_entry('Claude Opus 5.5', 'claude-opus-5-5', 'high', 53.6, 8)])
        high = self.pairs(rows, 'claude-opus-5-5')['high']
        self.assertAlmostEqual(high['cost'], 1.8)
        self.assertEqual((high['carried'], high['evidence']['cost']['derivation']['price_then']), ('2026-10-01', 8))

    def test_a_model_no_page_has_measured_borrows_the_nearest_same_provider_volume(self):
        day = '2026-10-07'
        rows = (page_rows('claude-opus-5-5', 'max', 57.6, 6.0, 8, day) + page_rows('claude-sonnet-5-5', 'max', 56.0, 7.6, 4, day)
                + page_rows('claude-sonnet-5-5', 'high', 46.75, 1.12, 4, day)
                + page_rows('gpt-6-luna', 'max', 38.1, 0.0677, 0.2, day) + page_rows('gpt-6-luna', 'low', 21.5, 0.0045, 0.2, day))
        rows += self.api(day, [api_entry('Claude Haiku 5.5', 'claude-haiku-5-5', e, i, 0.2) for e, i in (('max', 43.4), ('high', 37.8), ('low', 29.4))]
                         + [api_entry('Claude Opus 5.5', 'claude-opus-5-5', 'max', 57.6, 8)]
                         + [api_entry('Claude Sonnet 5.5', 'claude-sonnet-5-5', e, i, 4) for e, i in (('max', 56.0), ('high', 46.8))])
        haiku = self.pairs(rows, 'claude-haiku-5-5')
        self.assertEqual(sorted(haiku), ['high', 'low', 'max'], 'the efforts the API lists; none invented')
        expect = {'max': ('claude-sonnet-5-5', 'max', 7.6 / 4), 'high': ('claude-sonnet-5-5', 'high', 1.12 / 4),
                  'low': ('claude-sonnet-5-5', 'high', 1.12 / 4)}     # no Claude pair measured at low: the nearest effort
        for effort, (donor, at, volume) in expect.items():
            with self.subTest(effort):
                point, how = haiku[effort], haiku[effort]['evidence']['cost']['derivation']
                self.assertTrue(point['estimated'])
                self.assertEqual((how['method'], how['donor'], how['donor_effort']), ('same-provider token volume', donor, at),
                                 "the same provider only: GPT-6 Luna's nearer index never lends its volume")
                self.assertAlmostEqual(point['cost'], volume * 0.2)
        self.assertEqual(haiku['max']['score'], 43.4)
        rows += page_rows('claude-haiku-5-5', 'max', 43.42, 0.09, 0.2, day)
        top = self.pairs(rows, 'claude-haiku-5-5')['max']
        self.assertEqual((top['cost'], top['estimated'], top['score']), (0.09, False, 43.42), 'a page measurement replaces the estimate')

    def test_an_api_index_that_disagrees_with_the_pages_is_flagged(self):
        day = '2026-10-07'
        rows = page_rows('claude-opus-5-5', 'max', 57.6, 6.0, 8, day)
        rows += self.api(day, [api_entry('Claude Opus 5.5', 'claude-opus-5-5', 'max', 61.0, 8),
                               api_entry('Claude Haiku 5.5', 'claude-haiku-5-5', 'max', 43.4, 0.2)])
        top = self.pairs(rows, 'claude-haiku-5-5')['max']
        self.assertIn('differs from page measurements by 3.40 points', top['evidence']['score']['derivation']['warning'])
        self.assertIn('differs from page measurements', policy.pick('claude', 'rote', [top])['why'])


class ModelDataHealthTest(BoardBase):
    def test_a_bad_day_of_page_reads_carried_volumes_and_an_unpriced_model_are_said_where_model_health_is_seen(self):
        from colony import bench, board, cli, providers
        lineup = [('claude', 'claude-opus-5-5', 'Opus 5.5', EFFORTS), ('claude', 'claude-haiku-5-5', 'Haiku 5.5', EFFORTS)]
        rows = page_rows('claude-opus-5-5', 'high', 53.58, 1.8, 8, '2026-10-01') + page_rows('claude-opus-5-5', 'max', 57.62, 6.0, 8, '2026-10-01')
        rows += bench.records_from([api_entry('Claude Opus 5.5', 'claude-opus-5-5', e, i, 8) for e, i in (('high', 53.6), ('max', 57.6))],
                                   [m for _, m, _, _ in lineup], '2026-10-03')[0]
        for obj, name, value in ((bench, 'records', lambda: rows), (bench, 'lineup', lambda: lineup),
                                 (providers, 'available', lambda p: [(m, label) for f, m, label, _ in lineup if f == providers.key(p)]),
                                 (providers, 'efforts_of', lambda p, m: EFFORTS)):
            patcher = patch.object(obj, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (board.home() / 'bench').mkdir(parents=True, exist_ok=True)
        (board.home() / 'bench' / 'pairs-refresh.json').write_text(json.dumps(dict(
            attempted_at=0, at='2026-10-07T19:52:43Z', pages=30, measured=5,
            errors=[dict(model='claude-opus-5-5', effort='high', error='ValueError', detail='No claude-opus-5-5-high measurement on the page')] * 25)))
        problems, notes, _ = policy.health(today='2026-10-07')
        self.assertEqual(problems, ['25 of 30 Artificial Analysis model-page reads failed on 2026-10-07 (No claude-opus-5-5-high '
                                    "measurement on the page); token volumes are carried at today's prices until the pages read again"])
        self.assertIn("token volume carried at today's price for 2 pairs, the oldest measured 2026-10-01 (6 days ago)", notes)
        self.assertIn("Haiku 5.5 has no cost per task, measured or estimated, so Auto can't choose it", notes)
        page = html.unescape(board.models_page(board.registry()))
        for line in ('Model data:', '25 of 30 Artificial Analysis model-page reads failed', 'the oldest measured 2026-10-01',
                     'Haiku 5.5 has no cost per task', 'Carried', 'measured 2026-10-01, at today'):
            self.assertIn(line, page)
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = str(s.getsockname()[1])
        (board.home() / 'server.json').write_text(json.dumps(['--port', port, '--no-monitor']))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.cmd_doctor(argparse.Namespace(tests=False))
        self.assertEqual(code, 1)
        self.assertIn('PROBLEM model data: 25 of 30 Artificial Analysis model-page reads failed', out.getvalue())
        self.assertIn("note  model data: Haiku 5.5 has no cost per task", out.getvalue())
