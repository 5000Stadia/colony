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
        for role in ('main', 'routine', 'chores'):
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
        self.assertEqual(picks[6], ('claude-opus-5-5', 'low'), 'toward Economy it falls, to the same cheapest pair here')

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

    def test_an_unmeasured_setting_is_no_candidate_and_versions_are_never_spliced(self):
        self.assertFalse(any(p['model'] == 'claude-sonnet-5-5' and p['effort'] == 'low' for p in self.points),
                         'Sonnet 5.5 at low has no measurement: nothing is estimated for it')
        self.assertEqual(len(self.points), 19)
        rows = [dict(r, version='v99') if r['domain'] == 'overall' else r for r in self.rows]
        points = policy.candidates(rows, self.lineup)
        self.assertTrue(all(p['cost'] is None and p['version'] == 'v99' for p in points), "no v4.3.2 cost under a v99 index")
        self.assertIsNone(policy.pick('codex', 'main', points))


if __name__=='__main__':unittest.main()


class RefreshEvidenceTest(unittest.TestCase):
    def test_partial_refresh_preserves_other_measured_efforts(self):
        rows=policy.bundled_records()
        model='claude-opus-5-5'
        lineup=[('claude',model,model,['low','medium','high','xhigh','max'])]
        before=policy.candidates(rows,lineup)
        rows += [dict(r,date='2026-10-08') for r in rows if r['model']==model and r['effort']=='low']
        after=policy.candidates(rows,lineup)
        self.assertEqual([(p['score'],p['cost']) for p in before],[(p['score'],p['cost']) for p in after])
        self.assertEqual(after[0]['evidence']['score']['date'],'2026-10-08')
        self.assertEqual(after[-1]['evidence']['score']['date'],'2026-10-01')

    def test_family_fallback_uses_only_unblocked_candidates(self):
        points=[dict(model=m,effort='high',score=s,cost=c,family='codex',version='v1',estimated=False,evidence={})
                for m,s,c in [('top',60,2),('other',50,1)]]
        pick=policy.pick('codex','main',points,blocked=[{'model':'top','effort':'high'}])
        self.assertEqual(pick['model'],'other')
        self.assertEqual(pick['evidence']['ceiling'],60)
        self.assertEqual(pick['evidence']['family_best'],50)
        self.assertIn('6.00 below goal',pick['why'])


# ---------------------------------------------------------------- Artificial Analysis' free list

import argparse
import contextlib
import html
import io
import json
import socket
import time
import urllib.error
from unittest.mock import patch

from tests.test_board import BoardBase

EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']


def entry(name, slug, effort, index, cost, price_in=2, price_out=10, maker='OpenAI'):
    """One model of the free list as Artificial Analysis gives it since Nov 2026: its index and that index's cost."""
    return dict(id=slug, name=f"{name} ({effort.capitalize()})", slug=slug + ('' if effort == 'max' else '-' + effort),
                release_date='2026-09-29', model_creator=dict(id='x', name=maker),
                evaluations=dict(artificial_analysis_intelligence_index=index, artificial_analysis_coding_index=None,
                                 artificial_analysis_legal_index=40.0),
                artificial_analysis_intelligence_index_cost=None if cost is None else dict(
                    total_cost=cost * 900, cost_per_task=dict(total_cost=cost)),
                pricing=dict(price_1m_input_tokens=price_in, price_1m_output_tokens=price_out, price_1m_cache_hit_tokens=None),
                performance=dict(median_output_tokens_per_second=80.0, median_time_to_first_token_seconds=2.0))


class Reply:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self, limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class FreeListTest(BoardBase):
    runnable = [('codex', 'gpt-6.1-sol', 'GPT-6.1 Sol', EFFORTS + ['ultra']), ('codex', 'gpt-6-sol', 'GPT-6 Sol', EFFORTS),
                ('codex', 'gpt-5.6-sol', 'GPT-5.6 Sol', EFFORTS), ('codex', 'gpt-6-luna', 'GPT-6 Luna', EFFORTS),
                ('codex', 'gpt-6.2-luna', 'GPT-6.2 Luna', EFFORTS), ('claude', 'claude-opus-5-5', 'Opus 5.5', EFFORTS)]

    def setUp(self):
        super().setUp()
        from colony import bench
        patcher = patch.object(bench, 'runnable', lambda: list(self.runnable))
        patcher.start()
        self.addCleanup(patcher.stop)
        bench.set_key('test-key')
        self.pages = [
            [entry('GPT-6.1 Sol', 'gpt-6-1-sol', e, i, c) for e, i, c in (('low', 34.8, 0.16), ('high', 50.3, 0.59), ('max', 54.6, 1.27))]
            + [entry('GPT-6 Sol', 'gpt-6-sol', 'max', 44.2, 2.47), entry('GPT-6 Sol', 'gpt-6-sol', 'high', None, None)],
            [entry('GPT-5.6 Sol', 'gpt-5-6-sol', 'max', None, None), entry('GPT-6 Luna', 'gpt-6-luna', 'max', 27.9, 0.15),
             entry('Claude Opus 5.5', 'claude-opus-5-5', 'max', 59.1, 10.04, 4, 20, 'Anthropic'),
             entry('GLM-5.3', 'glm-5-3', 'max', 50.1, 5.01, maker='Z AI'), entry('Tiny', 'tiny', 'max', 12.0, None)]]
        self.asked = []

    def opener(self, req, timeout):
        self.asked.append((req.full_url, req.headers.get('X-api-key')))
        page = int(req.full_url.split('page=')[1]) if 'page=' in req.full_url else 1
        return Reply(dict(tier='free', intelligence_index_version=5, data=self.pages[page - 1],
                          pagination=dict(page=page, page_size=5, total_pages=len(self.pages), has_more=page < len(self.pages))))

    def test_the_free_list_is_read_page_by_page_and_each_pair_is_its_own_measured_index_and_cost(self):
        from colony import bench
        out = bench.refresh(self.opener)
        self.assertIsNone(out['error'])
        self.assertEqual([u for u, _ in self.asked], [bench.AA_URL, bench.AA_URL + '?page=2'])
        self.assertEqual({k for _, k in self.asked}, {'test-key'})
        self.assertEqual((out['models'], out['version'], out['unmatched']), (10, 'v5', 5), 'the left-out models count as unmatched')
        self.assertIn('artificial_analysis_legal_index', out['unknown_fields'], 'a field colony has no domain for is listed')
        points = {(p['model'], p['effort']): p for p in policy.pairs(bench.standings())}
        self.assertEqual(sorted(points), [('claude-opus-5-5', 'max'), ('gpt-6-luna', 'max'), ('gpt-6.1-sol', 'high'),
                                          ('gpt-6.1-sol', 'low'), ('gpt-6.1-sol', 'max')],
                         'only measured pairs; GPT-6 Sol is left out once GPT-6.1 Sol is scored, nothing is estimated')
        sol = points[('gpt-6.1-sol', 'high')]
        self.assertEqual((sol['score'], sol['cost'], sol['version']), (50.3, 0.59, 'v5'))
        self.assertEqual(sol['evidence']['cost']['source'], bench.API_SOURCE)
        price = next(r for r in bench.api_records() if r['model'] == 'claude-opus-5-5' and r['benchmark'] == policy.PRICE)
        self.assertEqual(price['value'], (3 * 4 + 20) / 4, 'the 3:1 blend, from the input and output prices')
        self.assertEqual(policy.pick('codex', 'main', list(points.values()))['why'].split('; ')[-1], 'Index 54.60, $1.27/task (v5)')
        self.assertEqual(bench.market(), [('GPT-6 Sol (Max)', 'OpenAI', 44.2, 2.47), ('GLM-5.3 (Max)', 'Z AI', 50.1, 5.01)],
                         'the field beyond what colony weighs, a model left out among it: scored and costed')

    def test_an_older_version_of_a_line_is_left_out_once_a_newer_one_is_scored_and_stays_runnable_by_name(self):
        from colony import bench, board
        bench.refresh(self.opener)
        self.assertEqual(bench.superseded(), {'gpt-6-sol', 'gpt-5.6-sol'},
                         'GPT-6 Luna stays: GPT-6.2 Luna is offered but not scored yet')
        self.assertEqual([m for _, m, _, _ in bench.lineup()], ['gpt-6.1-sol', 'gpt-6-luna', 'gpt-6.2-luna', 'claude-opus-5-5'])
        self.assertEqual(bench.name('gpt-5.6-sol'), 'GPT-5.6 Sol', 'still named where a project chose it')
        self.assertEqual(bench.line('gpt-6.1-sol'), (('gpt', 'sol'), (6, 1)))
        self.assertEqual(bench.line('claude-haiku-4-5-20251001'), (('claude', 'haiku'), (4, 5)))
        _, notes, summary = policy.health()
        self.assertIn('left out for a newer version of their own: GPT-5.6 Sol, GPT-6 Sol', notes)
        self.assertIn("GPT-6.2 Luna has no cost per task from Artificial Analysis yet, so Auto can't choose it", notes)
        self.assertTrue(summary.startswith('3 models, 5 settings, scored by Artificial Analysis (Intelligence Index v5) on '))
        page = board.models_page(board.registry())
        self.assertIn('Left out for a newer version of their own, still runnable by name: GPT-5.6 Sol, GPT-6 Sol.', page)
        self.assertIn("class='chartall'", page)
        self.assertIn('Every model Artificial Analysis scores (2 more, in grey', page)
        self.assertIn('<title>GLM-5.3 (Max), Z AI: Intelligence Index 50.1, $5.01 cost per task</title>', page)
        self.assertNotIn("id='gpt-6-sol'", page, 'no card for a model left out')

    def test_a_retired_address_says_so_and_the_doctor_sees_the_data_go_stale(self):
        from colony import bench, board, cli
        bench.refresh(self.opener)
        gone = lambda req, timeout: (_ for _ in ()).throw(urllib.error.HTTPError(req.full_url, 410, 'Gone', {}, None))
        out = bench.refresh(gone)
        self.assertIn('retired this address (410)', out['error'])
        today = board.now()[:10]
        self.assertEqual(bench.fetched()['last'], today, 'the day data last came is kept through a failure')
        self.assertEqual(policy.health(today=today)[0], [], 'a day or two of failure is no problem yet')
        later = time.strftime('%Y-%m-%d', time.gmtime(time.time() + 5 * 86400))
        self.assertEqual(policy.health(today=later)[0], [
            f"Artificial Analysis hasn't answered since {today} (Artificial Analysis retired this address (410): "
            "colony/bench.py AA_URL needs its new one); Auto chooses on the scores and prices of that day"])
        self.assertEqual(len(policy.pairs(bench.standings())), 5, 'the last reply still stands')
        # where model health is seen: the Models page and the doctor, once the data is days old
        stale = dict(bench.fetched(), last=time.strftime('%Y-%m-%d', time.gmtime(time.time() - 5 * 86400)))
        (board.home() / 'bench' / 'fetched.json').write_text(json.dumps(stale))
        page = html.unescape(board.models_page(board.registry()))
        self.assertIn("Model data:</strong> Artificial Analysis hasn't answered since " + stale['last'], page)
        self.assertIn("GPT-6.2 Luna has no cost per task from Artificial Analysis yet", page)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = str(sock.getsockname()[1])
        (board.home() / 'server.json').write_text(json.dumps(['--port', port]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.cmd_doctor(argparse.Namespace(tests=False))
        self.assertEqual(code, 1)
        self.assertIn(f"PROBLEM model data: Artificial Analysis hasn't answered since {stale['last']}", out.getvalue())
        self.assertIn("note  model data: GPT-6.2 Luna has no cost per task", out.getvalue())
        shapeless = lambda req, timeout: Reply(dict(status=200, models=[]))
        self.assertIn("shape colony doesn't know", bench.refresh(shapeless)['error'])
