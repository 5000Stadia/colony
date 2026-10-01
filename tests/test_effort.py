import unittest
from unittest.mock import patch
from colony import bench, providers


class EffortPolicyTest(unittest.TestCase):
    def setUp(self):
        self.model = 'claude-opus-5-5'
        scores = [42.31, 51.24, 53.58, 55.99, 57.62]
        costs = [.55, 1.34, 1.82, 3.46, 5.98]
        self.curve = [dict(effort=e, score=s, cost=c, version='v4.3.2', date='2026-09-28', url='https://artificialanalysis.ai/')
                      for e, s, c in zip(('low','medium','high','xhigh','max'), scores,costs)]
        self.entries = [dict(model=self.model, effort=e['effort'], variant=e['effort'], index=e['score'], comparable=True,
                             cost={'value':2,'benchmark':'Price per 1M tokens','unit':'usd'}, task_curve=self.curve) for e in self.curve]

    def test_measured_knee_and_rare_frequent_judgement_use_same_smartest_model(self):
        for role, effort in [('main','high'),('routine','high'),('runtime','high'),('step-up','max'),('consultant','max'),('monitor','xhigh')]:
            pick=bench.role_pick('claude',role,self.entries)
            self.assertEqual((pick['model'],pick['effort']),(self.model,effort))
        knee=bench.role_pick('claude','main',self.entries)
        self.assertFalse(knee['estimated'])
        self.assertAlmostEqual(knee['evidence']['jump_ratio'],3.3174,places=3)
        self.assertIn('v4.3.2',knee['why'])

    def test_missing_or_non_increasing_curve_uses_labelled_five_point_estimate(self):
        for curve in ([], [dict(e,cost=1) for e in self.curve]):
            entries=[dict(e,task_curve=curve) for e in self.entries]
            pick=bench.role_pick('claude','main',entries)
            self.assertTrue(pick['estimated'])
            self.assertEqual(pick['effort'],'high')
            self.assertIn('Estimated',pick['why'])

    def test_routine_keeps_cheaper_model_in_reach_while_main_uses_smartest(self):
        other = 'claude-sonnet-5-5'
        entries = self.entries + [dict(self.entries[0], model=other, effort=e, variant=e,
                                      index=s, task_curve=[], cost={'value': 1, 'benchmark': 'Price per 1M tokens', 'unit': 'usd'})
                                 for e, s in [('medium',40.7),('high',46.7),('xhigh',51.9),('max',56)]]
        lineup = [('claude', self.model, '', []), ('claude', other, '', [])]
        with patch.object(bench, 'lineup', return_value=lineup):
            routine = bench.role_pick('claude', 'routine', entries)
            self.assertEqual(routine['model'], other)
            self.assertEqual(routine['effort'], 'xhigh', 'apply the knee after choosing the R61 model')
            self.assertEqual(bench.role_pick('claude', 'main', entries)['model'], self.model)

    def test_ultra_cannot_change_the_smartest_auto_model_and_nearest_ties_round_down(self):
        entries=self.entries+[dict(self.entries[-1],model='gpt-6-astra',effort='ultra',variant='ultra',index=1000)]
        with patch.object(providers,'efforts_of',return_value=['low','high','xhigh','ultra']):
            self.assertEqual(bench.nearest_effort('claude',self.model,'max'),'xhigh')
            self.assertEqual(bench.nearest_effort('claude',self.model,'medium'),'low')
            self.assertNotEqual(bench.role_pick('claude','consultant',entries)['effort'],'ultra')
        self.assertEqual(bench.scored('codex',entries),{})

    def test_api_ranking_keeps_version_matched_researched_task_curve(self):
        rows=[]
        for e in self.curve:
            for domain, benchmark, value,unit in [('overall','Intelligence Index',e['score'],'points'),('cost','Cost per Intelligence Index task',e['cost'],'usd')]:
                rows.append(dict(model=self.model, effort=e['effort'], source='Artificial Analysis', kind='independent', version=e['version'],
                                 date=e['date'],domain=domain,benchmark=benchmark,value=value,unit=unit,url=e['url'],note=''))
        rows.append(dict(rows[0],source=bench.API_SOURCE,version=None,value=43,date='2026-09-29'))
        entries=bench.standings(rows)
        self.assertEqual(entries[0]['index'],43)
        self.assertEqual(entries[0]['task_curve'][0]['score'],42.31,'do not splice newer API scores into old cost curve')
        mismatched=[dict(r,version='different') if r['domain']=='cost' else r for r in rows]
        self.assertEqual(bench.task_curves(mismatched),{})


if __name__ == '__main__':unittest.main()
