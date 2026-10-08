"""Adoption is persistent policy, independent of provider launch syntax."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from colony import board, selection, providers


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, COLONY_BOARD_HOME=self.tmp.name)
        env.start()
        self.addCleanup(env.stop)
        self.a = {'model': 'a-1', 'effort': 'low', 'why': 'Measured'}
        self.b = {'model': 'b-2', 'effort': 'high', 'why': 'Measured'}
        self.picks = {'test:main': self.a, 'test:monitor': self.a}
        class Provider:
            label = 'Test provider'
            models = [('a-1', 'A'), ('b-2', 'B')]
            efforts = ['low', 'high']
            own_defaults = lambda s: {'model': 'a-1', 'effort': 'low'}
        for target, name, value in ((providers, 'PROVIDERS', {'test': Provider()}),
                                    (providers, 'usable', lambda p: True),
                                    (providers, 'DEFAULT', 'test'),
                                    (selection, 'ROLES', ('main', 'monitor')),
                                    (selection.bench, 'token_price', lambda m: (1, 2) if m == 'a-1' else (2, 4)),
                                    (selection, 'recommendations', lambda s: {k: copy.deepcopy(v) for k, v in self.picks.items()
                                     if v['model'] not in s['rejected'] and selection.pair(v) not in s['blocked'].get(k, [])})):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        reg = board.registry()
        reg['roots'] = []
        reg['settings']['model_adoption'] = 'ask'
        board.save_registry(reg)

    def test_ask_holds_launch_choice_and_groups_all_roles_across_restarts(self):
        selection.reconcile()
        self.picks = {k: self.b for k in self.picks}
        first = selection.reconcile()
        self.assertEqual(selection.auto('test', 'main')['model'], 'a-1')
        proposal = first['pending']['b-2']
        self.assertEqual(set(proposal['roles']), {'test:main', 'test:monitor'})
        self.assertEqual(selection.reconcile()['pending']['b-2']['id'], proposal['id'])
        selection.decide('b-2', proposal['id'], 'approve')
        self.assertEqual(selection.auto('test', 'monitor')['model'], 'b-2')
        self.assertFalse(selection.read()['pending'])

    def test_rejection_survives_restart_and_automatic_policy(self):
        selection.reconcile()
        self.picks['test:main'] = self.b
        st = selection.reconcile()
        selection.decide('b-2', st['pending']['b-2']['id'], 'reject')
        reg = board.registry()
        reg['settings']['model_adoption'] = 'automatic'
        board.save_registry(reg)
        self.assertEqual(selection.reconcile()['accepted']['test:main']['model'], 'a-1')
        self.assertFalse(selection.read()['pending'])

    def test_automatic_return_restores_all_roles_without_pins(self):
        selection.reconcile()
        reg = board.registry()
        reg['settings']['model_adoption'] = 'automatic'
        board.save_registry(reg)
        self.picks = {k: self.b for k in self.picks}
        st = selection.reconcile()
        event = st['history'][-1]
        self.assertEqual(event['prices'], {'before': [1, 2], 'after': [2, 4]})
        selection.rollback(event['id'])
        self.assertTrue(all(v['model'] == 'a-1' for v in selection.read()['accepted'].values()))
        self.assertFalse(selection.auto('test', 'main')['own'])

    def test_effort_return_does_not_exclude_the_model_or_reapply_change(self):
        selection.reconcile()
        self.picks['test:main'] = dict(self.a, effort='high')
        st = selection.reconcile()
        selection.rollback(st['history'][-1]['id'])
        self.assertEqual(selection.reconcile()['accepted']['test:main']['effort'], 'low')
        self.assertNotIn('a-1', selection.read()['rejected'])
        self.assertEqual(selection.auto('test', 'monitor')['model'], 'a-1')

    def test_return_can_be_reversed_but_an_old_button_cannot_replay(self):
        selection.reconcile()
        self.picks['test:main'] = dict(self.a, effort='high')
        st = selection.reconcile()
        upgrade = st['history'][-1]['id']
        st = selection.rollback(upgrade)
        returned = st['history'][-1]['id']
        with self.assertRaisesRegex(ValueError, 'changed again'):
            selection.rollback(upgrade)
        selection.rollback(returned)
        self.assertEqual(selection.reconcile()['accepted']['test:main']['effort'], 'high')

    def test_stale_approval_cannot_accept_changed_effort(self):
        selection.reconcile()
        self.picks['test:main'] = self.b
        old = selection.reconcile()['pending']['b-2']['id']
        self.picks['test:main'] = dict(self.b, effort='low')
        with self.assertRaisesRegex(ValueError, 'changed'):
            selection.decide('b-2', old, 'approve')
        self.assertEqual(selection.auto('test', 'main')['model'], 'a-1')

    def test_real_pin_survives_adoption_and_invalid_family_is_not_launched(self):
        selection.reconcile()
        self.assertEqual(selection.resolve('test', 'main', self.b)['model'], 'b-2')
        with self.assertRaises(selection.Unavailable):
            selection.resolve('test', 'main', {'model': 'opus'})

    def test_read_recovers_backup_but_never_resets_an_unreadable_ledger(self):
        selection.reconcile()
        self.picks['test:main'] = self.b
        selection.reconcile()
        path = Path(self.tmp.name) / 'model-selection.json'
        path.write_text('broken')
        self.assertEqual(selection.read()['accepted']['test:main']['model'], 'a-1')
        path.with_suffix('.backup').write_text('broken too')
        self.assertIn('recovery', selection.read())
        self.assertTrue(selection.reconcile()['pending'])

    def test_a_retired_roles_seat_is_dropped_and_live_seats_stay(self):
        selection.reconcile()
        with selection.transaction() as state:
            state['accepted']['test:runtime'] = dict(self.a)
        accepted = selection.reconcile()['accepted']
        self.assertNotIn('test:runtime', accepted)
        self.assertEqual(set(accepted), {'test:main', 'test:monitor'})


from tests.test_board import BoardBase


class IntegrationTest(BoardBase):
    def test_inactive_codex_roles_reconcile_without_changing_the_running_provider(self):
        board.set_setting('provider', 'claude')
        def picked(family, role, entries, **kwargs):
            return {'model': 'gpt-6-astra' if family == 'codex' else 'claude-opus-5-5',
                    'effort': 'xhigh' if role == 'monitor' else 'high', 'why': 'Fixture role policy'}
        with patch.object(providers, 'usable', return_value=True), \
                patch.object(selection.bench, 'standings', return_value=[]), \
                patch.object(selection.bench, 'role_pick', side_effect=picked):
            selection.reconcile()
            with selection.transaction() as state:
                for role in ('monitor', 'main'):
                    state['accepted']['codex:' + role]['effort'] = 'medium'
            settled = selection.reconcile()
            self.assertEqual(settled['accepted']['codex:monitor']['effort'], 'xhigh')
            self.assertEqual(settled['accepted']['codex:main']['effort'], 'high')
            self.assertEqual(board.registry()['settings']['provider'], 'claude')
            self.assertEqual(selection.monitor()['model'], 'claude-opus-5-5')
            self.assertEqual(selection.main()['model'], 'claude-opus-5-5')

    def test_legacy_form_pins_and_aliases_migrate_once_real_pins_survive(self):
        root = self.root
        (root / '.board').mkdir(exist_ok=True)
        settings = root / '.board' / 'settings.json'
        settings.write_text(json.dumps({'model': 'claude-opus-5-5', 'effort': 'high'}))
        provider = providers.get('claude')
        with patch.object(provider, 'own_defaults', return_value={'model': 'claude-opus-5-5', 'effort': 'high'}):
            selection.migrate(root)
            self.assertNotIn('model', board.project_settings(root)[1])
            self.assertEqual(board.read(root, 'model-migrations.jsonl')[0]['before']['model'], 'claude-opus-5-5')
            board.project_settings(root, {'model': 'claude-opus-5-5', 'effort': 'high'})
            selection.migrate(root)
            self.assertEqual(selection.main(root)['model'], 'claude-opus-5-5')
            self.assertTrue(selection.main(root)['own'], 'later deliberate same-value pin survives')
        settings.write_text(json.dumps({'model': 'claude-sonnet-5', 'effort': 'low'}))
        selection.migrate(root)
        self.assertEqual(selection.main(root)['model'], 'claude-sonnet-5')
        settings.write_text(json.dumps({'model': 'opus'}))
        selection.bench.set_plan(root, 'routine', 'sonnet', 'high')
        selection.migrate(root)
        self.assertFalse(selection.main(root)['own'])
        self.assertNotIn('routine', selection.bench.plan(root))

    def test_global_and_project_pins_are_scoped_to_their_provider(self):
        board.set_setting('model', 'claude-opus-5-5')
        board.set_setting('effort', 'high')
        board.set_setting('monitor_model', 'claude-sonnet-5:low')
        board.project_settings(self.root, {'provider': 'codex'})
        self.assertTrue(selection.main(self.root)['model'].startswith('gpt-'))
        board.set_setting('provider', 'codex')
        self.assertTrue(selection.monitor()['model'].startswith('gpt-'))
        board.set_setting('model', 'gpt-6-astra')
        board.set_setting('effort', 'max')
        board.set_setting('provider', 'claude')
        self.assertEqual(selection.main()['model'], 'claude-opus-5-5')
        self.assertEqual(selection.monitor()['model'], 'claude-sonnet-5')

    def test_proposal_includes_pinned_project_and_cannot_leak_to_monitor_queue(self):
        from colony import console
        board.track(self.root)
        board.project_settings(self.root, {'model': 'claude-sonnet-5', 'effort': 'low'})
        board.set_setting('model_adoption', 'ask')
        candidate = {'model': 'claude-opus-5-5', 'effort': 'high', 'why': 'Highest measured score'}
        with patch.object(selection.bench, 'role_pick', return_value=candidate):
            state = selection.reconcile()
            proposal = state['pending']['claude-opus-5-5']
            self.assertTrue(any(s['pinned'] for c in proposal['roles'].values() for s in c['seats']))
            page = board.needs_you(board.registry())
            self.assertIn('claude-opus-5-5', page)
            self.assertIn('Your pin stays', page)
            self.assertFalse(board.gates(self.root))
            self.assertNotIn('model', {i['kind'] for i in board.waiting_items(self.root)})
            selection.decide('claude-opus-5-5', proposal['id'], 'approve')
            self.assertEqual(selection.main(self.root)['model'], 'claude-sonnet-5')
            with patch.object(console, 'COMMAND', None):
                self.assertIn('--model claude-sonnet-5 --effort low', console.command('plants', self.root))

    def test_unavailable_accepted_model_uses_concrete_replacement_with_visible_question(self):
        selection.reconcile()
        board.set_setting('model_adoption', 'ask')
        original = providers.available
        def catalog(provider):
            return [('claude-opus-5-5', 'Opus')] if providers.key(provider) == 'claude' else original(provider)
        candidate = {'model': 'claude-opus-5-5', 'effort': 'high', 'why': 'Measured'}
        with patch.object(providers, 'available', side_effect=catalog), \
                patch.object(selection, 'recommendations', side_effect=lambda state: {
                    'claude:' + role: candidate for role in selection.ROLES
                } if candidate['model'] not in state['rejected'] else {}):
            choice = selection.auto('claude', 'main')
            self.assertEqual(choice['model'], 'claude-opus-5-5')
            pending = selection.read()['pending']['claude-opus-5-5']
            self.assertIn('unavailable', pending['emergency'])
            self.assertIn('already in use', board.model_changes())

    def test_project_settings_http_auto_does_not_pin_when_saving_safe_pause(self):
        import threading
        import urllib.request
        import urllib.parse
        from http.server import ThreadingHTTPServer
        board.track(self.root)
        server = ThreadingHTTPServer(('127.0.0.1', 0), board.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f'http://127.0.0.1:{server.server_address[1]}'
            form = {'p': '0', 'model_pick': 'auto', 'provider': 'claude', 'safe_pause': '90'}
            with urllib.request.urlopen(urllib.request.Request(url + '/project-settings', data=urllib.parse.urlencode(form).encode())):
                pass
            self.assertNotIn('model', board.project_settings(self.root)[1])
            self.assertFalse(selection.main(self.root)['own'])
            self.assertEqual(board.project_settings(self.root)[1]['safe_pause'], '90')
        finally:
            server.shutdown()
            server.server_close()

    def test_monitor_consultant_and_codex_helpers_use_the_same_accepted_pairs(self):
        from colony import bench, console, consult, monitor
        board.track(self.root)
        with patch.object(console, 'COMMAND', None):
            self.assertEqual(monitor.choice()[:2], tuple(selection.pair(selection.auto('claude', 'monitor')).values()))
            self.assertEqual(consult.pick('codex')[:2], tuple(selection.pair(selection.auto('codex', 'consultant')).values()))
            board.project_settings(self.root, {'provider': 'codex'})
            bench.write_helpers(self.root)
            cmd = console.command('plants', self.root)
            main = selection.main(self.root)
            self.assertIn(main['model'], cmd)
            self.assertIn(main['effort'], cmd)
            for role, filename in [('routine', 'routine'), ('step-up', 'stepup'), ('chores', 'chores'), ('rote', 'rote')]:
                chosen = selection.helper(self.root, role)
                contents = (self.root / '.codex' / 'agents' / f'colony-{filename}.toml').read_text()
                self.assertIn(chosen['model'], contents)
                self.assertIn(chosen['effort'], contents)

    def test_new_question_token_tracks_a_pin_change_and_prices_are_visible(self):
        board.track(self.root)
        board.set_setting('model_adoption', 'ask')
        candidate = {'model': 'claude-opus-5-5', 'effort': 'high', 'why': 'Measured'}
        with patch.object(selection.bench, 'role_pick', return_value=candidate), \
                patch.object(selection.bench, 'token_price', side_effect=lambda m: (2, 8) if m == candidate['model'] else (1, 4)):
            old = selection.reconcile()['pending'][candidate['model']]['id']
            board.project_settings(self.root, {'model': 'claude-sonnet-5', 'effort': 'high'})
            with self.assertRaisesRegex(ValueError, 'changed'):
                selection.decide(candidate['model'], old, 'approve')
            page = board.model_changes(pending_only=True)
            self.assertIn('+100%', page)
            self.assertIn('USD per million tokens', page)
            self.assertEqual(selection.main(self.root)['model'], 'claude-sonnet-5')


if __name__ == '__main__':
    unittest.main()


class GoalSliderTest(BoardBase):
    def setUp(self):
        super().setUp()
        from colony import bench, intelligence
        self.bench = bench
        rows = intelligence.records(json.loads(Path(bench.__file__).with_name('data').joinpath('aa-pairs.json').read_text()))
        models = [('claude','claude-opus-5-5'), ('claude','claude-sonnet-5-5'),
                  ('codex','gpt-6-astra'), ('codex','gpt-6.1-sol')]
        levels = ['low','medium','high','xhigh','max']
        lineup = [(f,m,m,levels) for f,m in models]
        for obj,name,value in [(bench,'records',lambda: rows), (bench,'lineup',lambda: lineup),
                               (providers,'available',lambda p: [(m,m) for f,m in models if f==providers.key(p)]),
                               (providers,'efforts_of',lambda p,m: levels)]:
            patcher=patch.object(obj,name,value);patcher.start();self.addCleanup(patcher.stop)
        board.track(self.root)
        board.project_settings(self.root, {'provider':'codex'})
        self.other=Path(self.tmp.name)/'other'
        self.other.mkdir()
        board.track(self.other)
        board.project_settings(self.other, {'provider':'codex'})
        selection.reconcile()

    def test_project_slider_changes_main_helpers_only_for_that_project(self):
        self.assertEqual(selection.main(self.root)['effort'],'max')
        board.project_settings(self.root, {'auto_balance':'6'})
        self.assertEqual(selection.main(self.root)['effort'],'medium')
        self.assertEqual(selection.main(self.other)['effort'],'max')
        ident=selection.key('codex','main',self.root)
        affected=selection.affected(ident, None, selection.main(self.root))
        self.assertEqual([s['root'] for s in affected],[str(self.root)])
        board.project_settings(self.root, {'auto_balance':''})
        self.assertEqual(selection.main(self.root)['effort'],'max')

    def test_slider_keeps_pins_and_scopes_effort_rollback(self):
        board.project_settings(self.root, {'model':'gpt-6-astra','effort':'max','auto_balance':'6'})
        self.assertEqual(selection.main(self.root)['model'],'gpt-6-astra')
        board.project_settings(self.root, {'model':'','effort':''})
        ident=selection.key('codex','main',self.root)
        event=next(e for e in reversed(selection.read()['history']) if e['role']==ident)
        selection.rollback(event['id'])
        self.assertNotEqual(selection.main(self.root)['effort'],'medium')
        self.assertEqual(selection.main(self.other)['effort'],'max')
        self.assertIn({'model':'gpt-6.1-sol','effort':'medium'},selection.read()['blocked'][ident])

    def test_preview_has_seven_positions_and_does_not_change_accepted_choices(self):
        before=selection.read()
        page=board.auto_balance_fields(self.root)
        self.assertEqual(page.count('data-balance='),7)
        self.assertIn('Use colony setting',page)
        self.assertIn('adoption',page)
        self.assertEqual(selection.read(),before)
        models=board.models_page(board.registry())
        self.assertNotIn('By domain',models)
        self.assertIn('Shared runnable intelligence ceiling',models)
        for value in ('-1','7','1.5','nan'):
            with self.assertRaises(KeyError):board.set_setting('auto_balance',value)

    def test_global_balance_changes_inherited_but_not_overridden_projects(self):
        board.project_settings(self.root, {'auto_balance':'0'})
        board.set_setting('auto_balance','6')
        self.assertEqual(selection.main(self.root)['effort'],'max')
        self.assertEqual(selection.main(self.other)['effort'],'medium')
        self.assertEqual(selection.read()['accepted']['codex:main']['evidence']['balance'],6)

    def test_scoped_new_model_approval_is_invalidated_when_slider_changes(self):
        board.set_setting('model_adoption','ask')
        board.project_settings(self.root, {'provider':'claude'})
        with selection.transaction() as state:
            state['approved'].remove('claude-sonnet-5-5')
            held=state['accepted']['claude:main']
            for ident,value in list(state['accepted'].items()):
                if value['model']=='claude-sonnet-5-5':state['accepted'][ident]=copy.deepcopy(held)
        board.project_settings(self.root, {'auto_balance':'6'})
        state=selection.read()
        proposal=state['pending']['claude-sonnet-5-5']
        ident=selection.key('claude','main',self.root)
        self.assertIn(ident,proposal['roles'])
        self.assertEqual(selection.main(self.root)['model'],'claude-opus-5-5')
        old=proposal['id']
        board.project_settings(self.root, {'auto_balance':'5'})
        with self.assertRaisesRegex(ValueError,'changed'):
            selection.decide('claude-sonnet-5-5',old,'approve')
        self.assertEqual(selection.main(self.other)['effort'],'max')


class FourthTierTest(BoardBase):
    """The person split chores in two: rote, new, keeps the value pick for mechanical work; chores, simple work
    that takes minor discernment, gets a goal like routine's. A ledger from the three tiers loses no seat."""

    def setUp(self):
        super().setUp()
        from colony import bench, intelligence
        self.bench = bench
        rows = intelligence.records(json.loads(Path(bench.__file__).with_name('data').joinpath('aa-pairs.json').read_text()))
        models = [('claude', 'claude-opus-5-5'), ('claude', 'claude-sonnet-5-5'), ('codex', 'gpt-6-astra'), ('codex', 'gpt-6.1-sol')]
        levels = ['low', 'medium', 'high', 'xhigh', 'max']
        for obj, name, value in [(bench, 'records', lambda: rows),
                                 (bench, 'lineup', lambda: [(f, m, m, levels) for f, m in models]),
                                 (providers, 'available', lambda p: [(m, m) for f, m in models if f == providers.key(p)]),
                                 (providers, 'efforts_of', lambda p, m: levels)]:
            patcher = patch.object(obj, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        board.track(self.root)
        board.project_settings(self.root, {'auto_balance': '5'})          # its own seats, beside colony's
        self.returned = {'model': 'gpt-6.1-sol', 'effort': 'low'}
        with selection.transaction() as state:
            # The ledger as the three tiers left it: no rote seat, each chores seat on the value pick, Codex's on
            # the one after Sol low, which the person returned from; no history yet.
            for ident in [i for i in state['accepted'] if ':rote' in i]:
                state['accepted'][ident.replace(':rote', ':chores')] = state['accepted'].pop(ident)
            state['accepted']['codex:chores'] = selection.concrete('codex', bench.role_pick(
                'codex', 'rote', bench.standings(), blocked=[self.returned]))
            state['blocked'] = {'codex:chores': [self.returned]}
            state['history'] = []
        self.before = selection.read()['accepted']

    def test_a_three_tier_ledger_keeps_every_seat_and_rote_starts_from_what_chores_held(self):
        self.assertFalse([i for i in self.before if ':rote' in i])
        self.assertEqual(selection.pair(self.before['codex:chores']), {'model': 'gpt-6.1-sol', 'effort': 'medium'})
        after = selection.reconcile()
        self.assertLessEqual(set(self.before), set(after['accepted']), 'no seat lost')
        chores = [i for i in self.before if i.partition(':')[2].split('@')[0] == 'chores']
        self.assertEqual(len(chores), 3, "colony's for each program, and the project's own")
        for ident in chores:
            rote = ident.replace(':chores', ':rote')
            self.assertEqual(selection.pair(after['accepted'][rote]), selection.pair(self.before[ident]),
                             f'{rote}: the value pick chores held')
        seeded = [e for e in after['history'] if ':rote' in e['role']]
        self.assertEqual([e['reason'] for e in seeded], ['Rote, the new tier, takes over the value pick chores held'] * 3,
                         'one start each, never a detour through another seat')
        self.assertEqual(after['blocked']['codex:rote'], [self.returned], 'what the person returned from stays returned from')
        self.assertEqual(selection.pair(selection.auto('codex', 'rote')), selection.pair(self.before['codex:chores']))
        self.assertEqual(selection.pair(after['accepted']['claude:chores']), {'model': 'claude-opus-5-5', 'effort': 'low'},
                         'chores moves to its own goal, by the adoption policy')
        self.assertNotEqual(selection.pair(self.before['claude:chores']), selection.pair(after['accepted']['claude:chores']))
        self.assertEqual(selection.reconcile()['history'], after['history'], 'once: a second look changes nothing')
        self.bench.write_helpers(self.root)
        agents = self.root / '.claude' / 'agents'
        for tier, name in (('rote', 'colony-rote'), ('chores', 'colony-chores')):
            seat = selection.helper(self.root, tier)
            self.assertIn(f"model: {seat['model']}\neffort: {seat['effort']}\n", (agents / f'{name}.md').read_text())

    def test_every_place_the_tiers_are_shown_has_all_four(self):
        import argparse
        import contextlib
        import html
        import io
        from colony import cli
        selection.reconcile()
        self.assertEqual(list(self.bench.effective(self.root)), ['routine', 'step-up', 'chores', 'rote'])
        fields = board.tier_fields(self.root)
        for tier in ('routine', 'step-up', 'chores', 'rote'):
            self.assertIn(f"name='tier_{tier}'", fields, 'a project can pin each tier')
            self.assertIn(f"name='helper_claude:{tier}'", board.global_role_fields(), "and colony's Settings each")
        models = html.unescape(board.models_page(board.registry()))
        for family in ('claude', 'codex'):
            for role in ('chores', 'rote'):
                self.assertIn(f'{family} · {role}', models)
        self.assertIn('Rote keeps its value pick at every position', board.auto_balance_fields(self.root))
        out = io.StringIO()
        with patch.object(board, 'root_of', return_value=self.root), contextlib.redirect_stdout(out):
            cli.cmd_models(argparse.Namespace(what=None, args=[], why=None))
        self.assertEqual([line.split()[0] for line in out.getvalue().splitlines()[:4]], ['routine', 'step-up', 'chores', 'rote'])
        self.assertIn('Helpers run at four tiers (routine, step-up, chores, rote)', ' '.join(board.PROTOCOL.split()))

    def test_turbo_leaves_rote_and_chores_unboosted(self):
        from colony import intelligence, turbo
        selection.reconcile()
        entries = self.bench.standings()
        ceiling = max(p['score'] for p in intelligence.pairs(entries))
        up = turbo.picks(self.root)
        self.assertIn('main', up, 'turbo raises this project two positions toward Intelligence')
        self.assertLessEqual(set(up), {'main', 'routine'})
        held = selection.read()['accepted'][selection.key('claude', 'chores', self.root)]
        raised = self.bench.role_pick('claude', 'chores', entries, balance=turbo.raised(5), ceiling=ceiling)
        self.assertNotEqual(selection.pair(raised), selection.pair(held), 'raised, chores would have moved; it stays')
