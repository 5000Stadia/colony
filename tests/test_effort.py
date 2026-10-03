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
        for role in ('main', 'routine', 'monitor'):
            self.assertLess(policy.pick('claude', role, self.points, 6)['evidence']['goal'],
                            policy.pick('claude', role, self.points, 3)['evidence']['goal'])

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
