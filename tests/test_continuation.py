"""Goal lifecycle checks with a local RPC double and isolated board records."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from colony import board, console, context, continuation, lead, progress, providers
from colony.codex_rpc import RPCError
from colony.codex_transfer import atomic_json


class Goals:
    """No native process, account, network or model request is made."""
    def __init__(self):
        self.goals = {}
        self.calls = []
        self.before_get = None
        self.before_set = None
        self.lost = False
        self.reject = False
        self.result_status = None
        self.sequence = 0

    def call(self, method, params):
        self.calls.append((method, deepcopy(params)))
        tid = params['threadId']
        if method == 'thread/goal/get':
            if self.before_get:
                self.before_get(self, tid)
            return dict(goal=deepcopy(self.goals.get(tid)))
        if method == 'thread/goal/clear':
            if self.before_set:
                self.before_set(self, tid, params)
            if self.reject:
                raise RPCError('Native clear rejected the operation')
            self.goals.pop(tid, None)
            if self.lost:
                self.lost = False
                raise RPCError('Lost clear response; operation not retried')
            return {}
        if method != 'thread/goal/set':
            raise AssertionError('Unexpected RPC: ' + method)
        if self.before_set:
            self.before_set(self, tid, params)
        if self.reject:
            raise RPCError('Native setter rejected the operation')
        previous = self.goals.get(tid)
        self.sequence += 1
        value = dict(objective=params.get('objective', previous['objective'] if previous else None),
                     status=self.result_status or params.get('status', 'paused'),
                     createdAt=previous['createdAt'] if previous else self.sequence)
        self.goals[tid] = value
        if self.lost:
            self.lost = False
            raise RPCError('Lost response; operation not retried')
        return dict(goal=deepcopy(value))

    def setters(self):
        return [params for method, params in self.calls if method == 'thread/goal/set']

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class ContinuationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='colony-continuation-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root, self.helper = [self.base / name for name in ('lead', 'helper')]
        for root in (self.root, self.helper):
            (root / '.board').mkdir(parents=True)
            (root / '.board' / 'settings.json').write_text(json.dumps(dict(provider='codex')))
            (root / 'ROADMAP.md').write_text('# Test\n\n## Vision\n\nA useful completed version.\n\n'
                                          '## M1 — Version\n\n- [~] R1 First\n- [ ] R2 Second\n')
        self.env = patch.dict(os.environ, COLONY_BOARD_HOME=str(self.base / 'board'),
                              COLONY_PROJECT=str(self.root), COLONY_CONSOLE=console.session_name(self.root))
        self.env.start()
        self.addCleanup(self.env.stop)
        board.save_registry(dict(roots=[str(self.root), str(self.helper)], settings=dict(provider='codex', messaging=False)))
        self.checkpoint = dict(id='M1', milestone='M1', outcome='A usable version', definition='Both items work',
                               items=['R1', 'R2'], state='active', run=1)
        lead.update(self.root, lambda g: g.update(members=[str(self.root), str(self.helper)],
                    checkpoints=[deepcopy(self.checkpoint)], active_checkpoint='M1'))
        self.client = Goals()
        self.socket = self.base / 'rpc.sock'
        self.socket.touch()
        self.tid = 'lead-thread'
        for root, tid in ((self.root, self.tid), (self.helper, 'helper-thread')):
            atomic_json(context.file(root, 'context-session.json'), dict(provider='codex', id=tid, path=str(root / 'rollout.jsonl')))
        self.patches = [patch.object(continuation, 'safe_arm', return_value=True),
                        patch('colony.codex_rpc.Client', return_value=self.client),
                        patch('colony.codex_remote.socket_for', return_value=self.socket)]
        for mocked in self.patches:
            mocked.start()
            self.addCleanup(mocked.stop)
        continuation._last.clear()

    def objective(self, root=None):
        root = root or self.root
        return progress.objective(root, root)

    def own(self, state='active', *, root=None, tid=None, objective=None, **receipt):
        root, tid = root or self.root, tid or self.tid
        goal = dict(objective=objective or self.objective(root), status=state, createdAt=17)
        self.client.goals[tid] = goal
        value = dict(thread=tid, objective=goal['objective'], native_created=17, last_status=state,
                     outcome='confirmed', scope=continuation.scope(lead.info(root), root), **receipt)
        continuation.save(root, value, lead.info(root)['generation'])
        return goal

    def reconcile(self, root=None, tid=None, **kw):
        root, tid = root or self.root, tid or self.tid
        atomic_json(context.file(root, 'context-session.json'), dict(provider='codex', id=tid, path=str(root / 'rollout.jsonl')))
        return continuation.reconcile(root, self.client, tid, **kw)

    def assigned(self):
        value = dict(item='R1', owner=str(self.helper), state='working', workspace=str(self.helper),
                     generation=0, base='', branch=None)
        lead.update(self.root, lambda g: g.update(owners={'R1': str(self.helper)}, assignments={'R1': value}))
        return value

    def test_creates_once_reuses_and_persists_intent_before_setter(self):
        intents = []
        def persisted(client, tid, params):
            receipt = continuation.status(self.root)
            self.assertEqual(receipt['pending'], {k: v for k, v in params.items() if k != 'threadId'})
            self.assertEqual(receipt['pending_thread'], tid)
            self.assertEqual(receipt['outcome'], 'unknown')
            intents.append(receipt)
        self.client.before_set = persisted
        first = self.reconcile()
        second = self.reconcile()
        self.assertEqual(len(self.client.setters()), 1)
        self.assertEqual(first['native_created'], second['native_created'])
        self.assertEqual(first['objective'], self.objective())
        self.assertNotIn('pending', second)
        self.assertEqual(len(intents), 1)

    def test_does_not_adopt_foreign_goal_even_with_matching_objective(self):
        for objective in ('A different personal goal', self.objective()):
            with self.subTest(objective=objective):
                self.client.goals[self.tid] = dict(objective=objective, status='active', createdAt=41)
                result = self.reconcile()
                self.assertTrue(result['foreign'])
                self.assertFalse(result['native_stopped'])
                self.assertIn('person', result['error'])
                self.assertEqual(self.client.setters(), [])

    def test_receipt_without_native_identity_cannot_claim_existing_goal(self):
        goal = self.own()
        receipt = continuation.status(self.root)
        receipt.pop('native_created')
        continuation.save(self.root, receipt, 0)
        self.assertTrue(self.reconcile()['foreign'])
        self.assertEqual(self.client.setters(), [])
        self.assertEqual(self.client.goals[self.tid], goal)

    def test_replaced_native_goal_is_foreign(self):
        self.own()
        self.client.goals[self.tid]['createdAt'] += 1
        self.assertTrue(self.reconcile(forced_hold='blocking decision')['foreign'])
        self.assertEqual(self.client.setters(), [])

    def test_explicit_adoption_does_not_automatically_resume_person_pause(self):
        self.client.goals[self.tid] = dict(objective='Personal goal', status='paused', createdAt=44)
        adopted = continuation.adopt(self.root)
        self.assertEqual(adopted['objective'], 'Personal goal')
        self.assertTrue(adopted['manual_stop'])
        self.reconcile()
        self.assertEqual(self.client.setters(), [])
        continuation.resume(self.root)
        self.assertEqual(self.client.setters()[-1]['status'], 'active')
        self.assertEqual(self.client.goals[self.tid]['objective'], self.objective())

    def test_explicit_resume_never_adopts_foreign_goal(self):
        self.client.goals[self.tid] = dict(objective='Personal', status='paused', createdAt=44)
        result = continuation.resume(self.root)
        self.assertTrue(result['foreign'])
        self.assertEqual(self.client.setters(), [])

    def test_lost_activation_response_is_read_not_retried(self):
        self.client.lost = True
        with self.assertRaises(RPCError):
            self.reconcile()
        pending = continuation.status(self.root)
        self.assertEqual(pending['pending']['status'], 'active')
        self.assertEqual(pending['outcome'], 'unknown')
        result = self.reconcile()
        self.assertNotIn('pending', result)
        self.assertEqual(result['outcome'], 'confirmed')
        self.assertEqual(len(self.client.setters()), 1)

    def test_lost_activation_accepts_native_completion_pause_and_limits(self):
        for state in ('complete', 'paused', 'blocked', 'usageLimited', 'budgetLimited'):
            with self.subTest(state=state):
                continuation.save(self.root, {}, 0)
                self.client.goals.clear()
                self.client.calls.clear()
                self.client.lost = True
                self.client.result_status = state
                with self.assertRaises(RPCError):
                    self.reconcile()
                result = self.reconcile()
                self.assertNotIn('pending', result)
                self.assertEqual(result['last_status'], state)
                self.assertEqual(len(self.client.setters()), 1)

    def test_unconfirmed_activation_is_never_blindly_retried(self):
        self.client.reject = True
        with self.assertRaises(RPCError):
            self.reconcile()
        self.client.reject = False
        for _ in range(3):
            result = self.reconcile()
            self.assertIn('uncertain', result['error'])
            self.assertIn('pending', result)
        self.assertEqual(len(self.client.setters()), 1)
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            continuation.resume(self.root)

    def test_uncertain_pause_requires_actual_stopped_goal(self):
        self.own()
        self.client.reject = True
        with self.assertRaises(RPCError):
            self.reconcile(forced_hold='blocking decision')
        self.client.reject = False
        self.assertIn('uncertain', self.reconcile(forced_hold='blocking decision')['error'])
        self.assertEqual(len(self.client.setters()), 1)
        self.client.goals[self.tid]['status'] = 'paused'
        confirmed = self.reconcile(forced_hold='blocking decision')
        self.assertNotIn('pending', confirmed)
        self.assertTrue(confirmed['native_stopped'])
        self.assertEqual(confirmed['paused_by'], 'blocking decision')

    def test_explicit_clear_is_receipted_and_stops_until_explicit_continue(self):
        self.own()
        def persisted(client, tid, params):
            receipt = continuation.status(self.root)
            self.assertEqual(receipt['pending'], dict(clear=True))
            self.assertEqual(receipt['pending_thread'], tid)
            self.assertEqual(receipt['outcome'], 'unknown')
        self.client.before_set = persisted
        result = continuation.clear(self.root)
        self.assertTrue(result['cleared'])
        self.assertTrue(result['manual_stop'])
        self.assertNotIn(self.tid, self.client.goals)
        self.client.before_set = None
        self.reconcile()
        self.assertEqual(self.client.setters(), [])
        continuation.resume(self.root)
        self.assertEqual(self.client.goals[self.tid]['status'], 'active')

    def test_explicit_clear_refuses_foreign_goal(self):
        self.client.goals[self.tid] = dict(objective='Personal', status='active', createdAt=100)
        with self.assertRaisesRegex(ValueError, 'belongs to the person'):
            continuation.clear(self.root)
        self.assertEqual([m for m, _ in self.client.calls if m != 'thread/goal/get'], [])

    def test_explicit_clear_allows_an_explicit_new_scope_to_start(self):
        self.own()
        continuation.clear(self.root)
        lead.update(self.root, lambda g: g['checkpoints'][0].update(run=2))
        self.reconcile()
        self.assertEqual(self.client.goals[self.tid]['status'], 'active')
        self.assertEqual(len(self.client.setters()), 1)

    def test_lost_clear_response_reconciles_absence_without_retry(self):
        self.own()
        self.client.lost = True
        with self.assertRaises(RPCError):
            continuation.clear(self.root)
        self.assertEqual(continuation.status(self.root)['pending'], dict(clear=True))
        result = self.reconcile()
        self.assertTrue(result['cleared'])
        self.assertNotIn('pending', result)
        self.assertEqual(len([m for m, _ in self.client.calls if m == 'thread/goal/clear']), 1)
        self.assertEqual(self.client.setters(), [])

    def test_uncertain_clear_is_not_retried_and_missing_activation_can_be_recovered(self):
        self.own()
        self.client.reject = True
        with self.assertRaises(RPCError):
            continuation.clear(self.root)
        self.client.reject = False
        self.assertIn('uncertain', self.reconcile()['error'])
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            continuation.clear(self.root)
        self.assertEqual(len([m for m, _ in self.client.calls if m == 'thread/goal/clear']), 1)
        continuation.save(self.root, {}, 0)
        self.client.goals.clear()
        self.client.reject = True
        with self.assertRaises(RPCError):
            self.reconcile()
        self.client.reject = False
        result = continuation.clear(self.root)
        self.assertTrue(result['manual_stop'])
        self.assertNotIn('pending', result)
        self.reconcile()
        self.assertNotIn(self.tid, self.client.goals)

    def test_active_pause_response_keeps_uncertain_intent(self):
        self.own()
        self.client.result_status = 'active'
        with self.assertRaisesRegex(ValueError, 'confirmed its pause'):
            self.reconcile(forced_hold='context refresh')
        result = self.reconcile(forced_hold='context refresh')
        self.assertIn('pending', result)
        self.assertFalse(result['native_stopped'])
        self.assertEqual(len(self.client.setters()), 1)

    def test_stale_generation_cannot_dispatch_or_save_receipt(self):
        value = dict(continuation.status(self.root))
        lead.update(self.root, lambda g: g.update(generation=1))
        with self.assertRaisesRegex(ValueError, 'Lead changed'):
            continuation.dispatch(self.root, self.client, self.tid, dict(objective=self.objective(), status='active'), value, 0)
        with self.assertRaisesRegex(ValueError, 'lead changed'):
            continuation.save(self.root, value, 0)
        self.assertEqual(self.client.calls, [])

    def test_changed_goal_or_native_pause_before_setter_is_preserved(self):
        for state in ('foreign', 'paused', 'budgetLimited'):
            with self.subTest(state=state):
                self.client.calls.clear()
                self.own()
                lead.update(self.root, lambda g: g['checkpoints'][0].update(outcome='Changed wording ' + state))
                reads = []
                def change(client, tid):
                    reads.append(tid)
                    if len(reads) == 2:
                        if state == 'foreign':
                            client.goals[tid] = dict(objective='Personal replacement', status='active', createdAt=99)
                        else:
                            client.goals[tid]['status'] = state
                self.client.before_get = change
                with self.assertRaisesRegex(ValueError, 'changed before dispatch'):
                    self.reconcile()
                self.assertEqual(self.client.setters(), [])
                self.client.before_get = None

    def test_new_hold_or_busy_input_before_dispatch_prevents_activation(self):
        with patch.object(progress, 'hold', side_effect=[None, 'blocking decision']):
            with self.assertRaisesRegex(ValueError, 'boundary changed'):
                self.reconcile()
        self.assertEqual(self.client.setters(), [])
        with patch.object(continuation, 'safe_arm', side_effect=[True, False]):
            with self.assertRaisesRegex(ValueError, 'became busy'):
                self.reconcile()
        self.assertEqual(self.client.setters(), [])

    def test_changed_authoritative_thread_before_dispatch_is_not_armed(self):
        receipt = continuation.status(self.root)
        session = context.session(self.root)
        atomic_json(context.file(self.root, 'context-session.json'), dict(session, id='replacement-thread'))
        with self.assertRaisesRegex(ValueError, 'authoritative conversation changed'):
            continuation.dispatch(self.root, self.client, self.tid,
                                  dict(objective=self.objective(), status='active'), receipt, 0)
        self.assertEqual(self.client.calls, [])

    def test_person_pause_and_clear_are_not_automatically_overridden(self):
        for cleared in (False, True):
            with self.subTest(cleared=cleared):
                self.client.calls.clear()
                self.own()
                if cleared:
                    self.client.goals.pop(self.tid)
                else:
                    self.client.goals[self.tid]['status'] = 'paused'
                result = self.reconcile()
                self.assertTrue(result['manual_stop'])
                self.reconcile()
                self.assertEqual(self.client.setters(), [])
                continuation.resume(self.root)
                self.assertEqual(self.client.goals[self.tid]['status'], 'active')
                self.assertEqual(len(self.client.setters()), 1)

    def test_native_limits_are_preserved_even_on_explicit_resume(self):
        for state in ('blocked', 'usageLimited', 'budgetLimited'):
            with self.subTest(state=state):
                self.client.calls.clear()
                self.own(state)
                self.assertEqual(self.reconcile()['native_stop'], state)
                result = continuation.resume(self.root)
                self.assertEqual(result['native_stop'], state)
                self.assertEqual(self.client.goals[self.tid]['status'], state)
                self.assertEqual(self.client.setters(), [])

    def test_completed_scope_is_not_reopened_by_generation_or_wording(self):
        self.own('complete')
        self.reconcile()
        lead.update(self.root, lambda g: g.update(generation=1))
        lead.update(self.root, lambda g: g['checkpoints'][0].update(outcome='Refined wording'))
        result = self.reconcile()
        self.assertEqual(result['last_status'], 'complete')
        self.assertEqual(self.client.setters(), [])
        lead.update(self.root, lambda g: g['checkpoints'][0].update(run=2))
        self.reconcile()
        self.assertEqual(self.client.setters()[-1]['status'], 'active')

    def test_context_hold_pauses_once_and_lifts_with_the_live_refresh_job(self):
        self.own()
        atomic_json(context.file(self.root), dict(job=dict(session=self.tid, phase='requested')))
        self.assertTrue(continuation.hold(self.root))
        paused = continuation.status(self.root)
        self.assertEqual(paused['paused_by'], 'context refresh')
        self.assertEqual(self.client.goals[self.tid]['status'], 'paused')
        self.assertTrue(continuation.hold(self.root))
        continuation.tick(self.root, force=True)
        self.assertEqual(len(self.client.setters()), 1)
        atomic_json(context.file(self.root), dict(job=dict(session=self.tid, phase='restored')))
        continuation.tick(self.root)
        self.assertEqual(self.client.goals[self.tid]['status'], 'active')
        self.assertEqual(len(self.client.setters()), 2)

    def test_context_refresh_cannot_adopt_foreign_or_lift_person_pause(self):
        self.client.goals[self.tid] = dict(objective='Personal', status='active', createdAt=55)
        self.assertFalse(continuation.hold(self.root))
        continuation.tick(self.root, force=True)
        self.assertEqual(self.client.setters(), [])
        self.own('paused')
        self.assertTrue(continuation.hold(self.root))
        continuation.tick(self.root, force=True)
        self.assertTrue(continuation.status(self.root)['manual_stop'])
        self.assertEqual(self.client.setters(), [])

    def rollout(self, tokens, *exchange):
        events = [dict(type='response_item', payload=dict(type='message', role='user', content=[dict(type='input_text', text=exchange[0])])),
                  dict(type='event_msg', payload=dict(type='item_completed', item=dict(type='AgentMessage', content=[dict(type='Text', text=exchange[1])])))
                  ] if exchange else []
        events.append(dict(type='event_msg', payload=dict(type='token_count', info=dict(
            last_token_usage=dict(total_tokens=tokens), model_context_window=200_000))))
        (self.root / 'rollout.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))

    def test_new_conversation_mid_refresh_leaves_no_hold(self):
        self.own()
        self.rollout(10)
        atomic_json(context.file(self.root), dict(job=dict(id='0123456789abcdef', session=self.tid,
                                                          phase='requested', asked=time.time())))
        self.assertTrue(continuation.hold(self.root))
        self.assertEqual(self.client.goals[self.tid]['status'], 'paused')
        result = self.reconcile(tid='new-thread')
        self.assertIsNone(progress.hold(self.root, self.root))
        self.assertEqual(self.client.goals.get('new-thread', {}).get('status'), 'active')
        self.assertIsNone(result['hold'])
        context.tick(self.root)
        self.assertNotIn('job', context.read(context.file(self.root)))

    def test_refresh_that_stays_above_threshold_does_not_flap_continuation(self):
        self.own()
        self.rollout(150_000)
        atomic_json(context.file(self.root), dict(last=time.time(), retry_after=0, ineffective='Still above.',
                                                  job=dict(id='0123456789abcdef', session=self.tid, phase='restored')))
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into') as typed:
            for _ in range(3):
                context.tick(self.root)
                continuation.tick(self.root)
        typed.assert_not_called()
        self.assertEqual(self.client.setters(), [])
        self.assertEqual(self.client.goals[self.tid]['status'], 'active')

    def test_due_refresh_holds_continuation_until_its_request_is_sent(self):
        self.own()
        self.rollout(150_000, 'Keep going.', 'Done.')
        with patch.object(context, 'safe', return_value=False), patch.object(console, 'type_into') as typed:
            context.tick(self.root)
            for _ in range(3):
                continuation.tick(self.root)  # Busy input finishing its turn; nothing may re-arm meanwhile.
            typed.assert_not_called()
        self.assertEqual(context.read(context.file(self.root))['job']['phase'], 'due')
        self.assertEqual(self.client.goals[self.tid]['status'], 'paused')
        self.assertEqual(len(self.client.setters()), 1)
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into', return_value=True) as typed:
            context.tick(self.root)
        self.assertIn('Context carry-over', typed.call_args.args[1])
        self.assertEqual(context.read(context.file(self.root))['job']['phase'], 'requested')
        self.assertEqual(len(self.client.setters()), 1)

    def test_new_conversation_drains_owned_old_goal_before_activation(self):
        self.own()
        checked = []
        def persisted(client, tid, params):
            receipt = continuation.status(self.root)
            self.assertEqual(receipt['pending_thread'], tid)
            checked.append(tid)
            if tid == 'new-thread':
                self.assertEqual(client.goals[self.tid]['status'], 'paused')
        self.client.before_set = persisted
        result = self.reconcile(tid='new-thread')
        self.assertEqual(checked, [self.tid, 'new-thread'])
        self.assertEqual(result['thread'], 'new-thread')
        self.assertEqual(self.client.goals['new-thread']['status'], 'active')

    def test_new_conversation_does_not_escape_pause_completion_or_native_limit(self):
        for state in ('paused', 'complete', 'budgetLimited'):
            with self.subTest(state=state):
                self.client.calls.clear()
                self.client.goals.clear()
                self.own(state)
                result = self.reconcile(tid='new-thread')
                self.assertEqual(self.client.setters(), [])
                self.assertNotIn('new-thread', self.client.goals)
                self.assertTrue(result.get('manual_stop') or result.get('native_stop') or result.get('completed_scope'))

    def test_new_conversation_never_pauses_foreign_old_goal(self):
        self.own()
        self.client.goals[self.tid] = dict(objective='Personal replacement', status='active', createdAt=100)
        result = self.reconcile(tid='new-thread')
        self.assertTrue(result['foreign'])
        self.assertFalse(result['native_stopped'])
        self.assertEqual(self.client.setters(), [])

    def test_new_conversation_and_explicit_resume_cannot_evade_native_limit(self):
        self.own('budgetLimited')
        self.reconcile(tid='new-thread')
        continuation.resume(self.root)
        self.assertEqual(self.client.setters(), [])
        self.assertEqual(self.client.goals[self.tid]['status'], 'budgetLimited')

    def test_previous_conversation_goal_is_paused_once_then_forgotten(self):
        self.own()
        self.reconcile(tid='new-thread')
        self.assertEqual(self.client.goals[self.tid]['status'], 'paused')
        self.client.calls.clear()
        self.client.goals[self.tid]['status'] = 'active'  # The person resumed it there themselves.
        lead.update(self.root, lambda g: g['checkpoints'][0].update(run=2))
        result = self.reconcile(tid='new-thread')
        self.assertNotIn(self.tid, [params['threadId'] for _, params in self.client.calls])
        self.assertNotIn('retired', result)
        self.assertEqual(self.client.goals['new-thread']['objective'], self.objective())
        self.assertEqual(self.client.goals[self.tid]['status'], 'active')

    def test_helper_delivery_or_sync_conflict_pauses_owned_continuation(self):
        self.assigned()
        self.own(root=self.helper, tid='helper-thread')
        lead.update(self.root, lambda g: g['assignments']['R1'].update(state='integrated'))
        result = continuation.tick(self.helper)
        self.assertEqual(result['last_status'], 'paused')
        self.assertIn('delivered', result['hold'])
        calls = len(self.client.calls)
        for _ in range(3):
            continuation.tick(self.helper)
        self.assertEqual(len(self.client.calls), calls)
        lead.update(self.root, lambda g: g['assignments']['R1'].update(state='working', sync_error='Engagement sync conflict'))
        self.client.calls.clear()
        self.own(root=self.helper, tid='helper-thread')
        result = continuation.tick(self.helper)
        self.assertEqual(result['last_status'], 'paused')
        self.assertIn('sync', result['hold'])

    def test_helper_continues_same_goal_through_its_own_integration_and_delivery(self):
        self.assigned()
        self.own(root=self.helper, tid='helper-thread')
        before = continuation.scope(lead.info(self.helper), self.helper)
        for state in ('working', 'handback', 'testing', 'deploying'):
            with self.subTest(state=state):
                lead.update(self.root, lambda g: g['assignments']['R1'].update(state=state))
                self.assertEqual(continuation.scope(lead.info(self.helper), self.helper), before)
                result = continuation.tick(self.helper)
                self.assertEqual(result['last_status'], 'active')
                self.assertIsNone(result['hold'])
                self.assertEqual(self.client.setters(), [])

    def test_lone_project_without_selected_checkpoint_has_no_provider_or_rpc_calls(self):
        lead.update(self.root, lambda g: g.update(members=[str(self.root)], checkpoints=[], goals={}))
        lead.update(self.root, lambda g: g.pop('active_checkpoint', None))
        with patch.object(providers, 'of') as provider, patch('colony.codex_rpc.Client') as rpc:
            continuation.tick(self.root)
            continuation.after_turn(self.root)
            continuation.tick_all()
            continuation.hold(self.root)
            provider.assert_not_called()
            rpc.assert_not_called()

    def test_dormant_unassigned_helper_has_no_provider_or_rpc_calls(self):
        with patch.object(providers, 'of') as provider, patch('colony.codex_rpc.Client') as rpc:
            continuation.tick(self.helper)
            continuation.after_turn(self.helper)
            continuation.hold(self.helper)
            provider.assert_not_called()
            rpc.assert_not_called()

    def test_terminal_review_or_native_stop_does_not_poll_or_wake(self):
        for state in ('paused', 'complete', 'budgetLimited'):
            with self.subTest(state=state):
                self.own(state, paused_by='version waiting for human review' if state == 'paused' else None)
                lead.update(self.root, lambda g: g['checkpoints'][0].update(state='review'))
                continuation.tick(self.root, force=True)
                self.client.calls.clear()
                with patch.object(console, 'type_into') as typed:
                    for _ in range(4):
                        continuation.tick(self.root)
                    self.assertEqual(self.client.calls, [])
                    typed.assert_not_called()

    def test_console_lead_is_woken_again_only_after_its_work_changes(self):
        (self.root / '.board' / 'settings.json').write_text(json.dumps(dict(provider='claude')))
        (self.root / '.gitignore').write_text('.board/\n')
        for args in (('init', '-q'), ('config', 'user.name', 'Fixture'),
                     ('config', 'user.email', 'fixture@users.noreply.github.com'), ('add', '--all'), ('commit', '-qm', 'Start')):
            lead.git(self.root, *args)
        plan = self.root / 'ROADMAP.md'
        with patch.object(console, 'type_into', return_value=True) as typed:
            continuation.tick(self.root)
            for _ in range(3):
                continuation.after_turn(self.root)  # Turns that changed nothing earn no wake.
            self.assertEqual(typed.call_count, 1)
            self.assertNotIn('Read the canonical', typed.call_args.args[1])
            (self.root / 'work.txt').write_text('progress')
            lead.git(self.root, 'add', '--all'); lead.git(self.root, 'commit', '-qm', 'Progress')
            continuation.after_turn(self.root)
            plan.write_text(plan.read_text().replace('[ ] R2', '[~] R2'))
            continuation.after_turn(self.root)
            lead.update(self.root, lambda g: g['assignments'].update(R9=dict(item='R9', owner=str(self.helper), state='working')))
            continuation.after_turn(self.root)
            self.assertEqual(typed.call_count, 4)
            progress.pause(self.root, True)
            continuation.after_turn(self.root)
            self.assertEqual(continuation.status(self.root)['hold'], 'person paused')
            progress.pause(self.root, False)
            continuation.tick(self.root)
            continuation.after_turn(self.root)
            self.assertEqual(typed.call_count, 5)
        self.assertEqual(self.client.calls, [])

    def handoff_record(self):
        lead.update(self.root, lambda g: g.update(generation=1, handoff=dict(outgoing=str(self.root),
                    incoming=str(self.helper), generation=1, state='landing', words='', at='now')))

    def test_handoff_requires_fresh_native_stop_including_foreign_goal(self):
        self.handoff_record()
        for state in ('active', 'paused'):
            self.client.goals[self.tid] = dict(objective='Personal', status=state, createdAt=55)
            with patch.object(console, 'running', return_value=False), patch.object(lead, 'clean', return_value=True), \
                    patch.object(lead, 'finish_handoff') as finish:
                result = continuation.handoff(self.root)
                self.assertEqual(result, state == 'paused')
                self.assertEqual(finish.called, state == 'paused')
        self.assertEqual(self.client.setters(), [])

    def test_handoff_drains_owned_active_goal_and_waits_for_idle_clean_workspace(self):
        self.own()
        self.handoff_record()
        with patch.object(console, 'running', return_value=True), patch.object(console, 'snapshot', return_value={'state': 'working'}), \
                patch.object(console, 'drafting', return_value=False), patch.object(console, 'attached', return_value=False), \
                patch.object(lead, 'clean', return_value=True), patch.object(lead, 'finish_handoff') as finish:
            self.assertFalse(continuation.handoff(self.root))
            self.assertEqual(self.client.goals[self.tid]['status'], 'paused')
            finish.assert_not_called()
        with patch.object(console, 'running', return_value=False), patch.object(lead, 'clean', return_value=False), \
                patch.object(lead, 'finish_handoff') as finish:
            self.assertFalse(continuation.handoff(self.root))
            finish.assert_not_called()
        with patch.object(console, 'running', return_value=False), patch.object(lead, 'clean', return_value=True), \
                patch.object(lead, 'finish_handoff') as finish:
            self.assertTrue(continuation.handoff(self.root))
            finish.assert_called_once_with(self.root, 1)
        self.assertEqual(len(self.client.setters()), 1)

    def test_handoff_cannot_use_stale_paused_receipt_when_socket_or_session_missing(self):
        self.own('paused', paused_by='lead handoff')
        self.handoff_record()
        self.socket.unlink()
        with patch.object(lead, 'finish_handoff') as finish:
            self.assertFalse(continuation.handoff(self.root))
            finish.assert_not_called()
            self.socket.touch()
            context.file(self.root, 'context-session.json').unlink()
            with patch.object(context, 'session', return_value=None):
                self.assertFalse(continuation.handoff(self.root))
            finish.assert_not_called()

    def test_handoff_blocks_unconfirmed_pause(self):
        self.own()
        self.handoff_record()
        self.client.reject = True
        with patch.object(lead, 'finish_handoff') as finish:
            self.assertFalse(continuation.handoff(self.root))
            self.client.reject = False
            self.assertFalse(continuation.handoff(self.root))
            finish.assert_not_called()
        self.client.goals[self.tid]['status'] = 'paused'
        with patch.object(console, 'running', return_value=False), patch.object(lead, 'clean', return_value=True), \
                patch.object(lead, 'finish_handoff') as finish:
            self.assertTrue(continuation.handoff(self.root))
            finish.assert_called_once_with(self.root, 1)


if __name__ == '__main__':
    unittest.main()
