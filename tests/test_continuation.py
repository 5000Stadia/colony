"""Goal lifecycle checks with a local RPC double and isolated board records."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from colony import board, console, context, continuation, lead, progress, providers
from colony import hours, monitor, recovery, vision
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


# ---------------------------------------------------------------- work a network drop stopped (recovery.py)
# Lines trimmed from real transcripts: their shapes and error strings as the programs wrote them, nothing else of
# the conversations. Claude Code's are holo-emitter's, 2026-10-07, when the network dropped at night.

def when(at):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(at)) + '.000Z'


EAI_AGAIN = "Can't reach the API server — check your internet or DNS (EAI_AGAIN)"


def cc_reply(at):
    return dict(parentUuid='p', isSidechain=False, type='assistant', uuid=f'reply-{at}', timestamp=when(at),
                message=dict(model='claude-opus-5-5', role='assistant', type='message', stop_reason='tool_use',
                             content=[dict(type='text', text='Running the checks.')]))


def cc_error(at, text='API Error: ' + EAI_AGAIN):
    """A turn's end once Claude Code's retries gave up."""
    return dict(parentUuid='p', isSidechain=False, type='assistant', uuid=f'error-{at}', timestamp=when(at),
                message=dict(model='<synthetic>', role='assistant', type='message', stop_reason='stop_sequence',
                             stop_sequence='', content=[dict(type='text', text=text)]),
                error='server_error', isApiErrorMessage=True)


def cc_report(at, task, name, status='failed',
              error=f'Agent terminated early due to an API error: API Error: {EAI_AGAIN} (error type server_error)'):
    """A background helper's report to its agent."""
    summary = f'Agent "{name}" failed: {error}' if status == 'failed' else f'Agent "{name}" finished'
    content = (f'<task-notification>\n<task-id>{task}</task-id>\n<tool-use-id>toolu_01</tool-use-id>\n'
               f'<output-file>/tmp/tasks/{task}.output</output-file>\n<status>{status}</status>\n<summary>{summary}'
               '</summary>\n<note>A task-notification fires each time this agent stops with no live background children '
               'of its own. The user can send it another message and resume it, so the same task-id may notify more '
               'than once.</note>\n<result>Checking the setup first.</result>\n</task-notification>')
    return dict(parentUuid='p', isSidechain=False, type='user', uuid=f'report-{task}-{at}', timestamp=when(at),
                message=dict(role='user', content=content), promptSource='system',
                origin=dict(kind='task-notification', producer='session-task'))


def cc_typed(at, text='Continue'):
    return dict(parentUuid='p', isSidechain=False, type='user', uuid=f'typed-{at}', timestamp=when(at),
                message=dict(role='user', content=text), origin=dict(kind='human'))


def cc_end(at, ms=372355):
    return dict(type='system', subtype='turn_duration', durationMs=ms, timestamp=when(at), uuid=f'end-{at}')


def night(t):
    """The drop, as it went (its times shifted to end at t): the agent at work, four helpers fail one by one, and its
    turn and each turn their reports start end on the same error after six minutes of retries."""
    return [cc_reply(t - 1249), dict(type='queue-operation', operation='enqueue', timestamp=when(t - 805)),
            cc_error(t - 758), cc_end(t - 758, 3442699),
            cc_report(t - 757, 'a1dc2f44ba8931af3', 'Street package'),
            cc_error(t - 372), cc_end(t - 372),
            cc_report(t - 372, 'a8f969af25cef90bc', 'Texture worker'),
            cc_report(t - 372, 'ae66e2e223efd43aa', 'Hillside'),
            cc_report(t - 371, 'a567ab2df35234e64', 'Checks'),
            cc_error(t), cc_end(t)]


# Codex's: Bookflow's, 2026-09-06, a helper lost to "Selected model is at capacity". No Codex session here has met a
# network drop, so where one is needed its error is Codex 0.162's own words for it.
STREAM = 'stream disconnected before completion: error sending request for url (https://chatgpt.com/backend-api/codex/responses)'
CAPACITY = 'Selected model is at capacity. Please try a different model.'
HELPER = '01a074a0-516b-79f0-b0d8-b06065e71aa0'
REACHABLE = recovery.reachable                  # the real check: the tests below stand in for it


def cx(at, kind, **payload):
    return dict(timestamp=when(at), type=kind, payload=payload)


def cx_wait(at):
    return cx(at, 'response_item', type='function_call', name='wait', arguments=json.dumps(dict(ids=[HELPER])), call_id='call_1')


def cx_lost(at, error=STREAM):
    return cx(at, 'event_msg', type='item_completed', thread_id='01a0703a-2359-7951-b2a1-8e057c42085e',
              item=dict(type='CollabAgentToolCall', id='exec-9c71684b-0951-4fc3-9c4f-6319e1c191e9', tool='wait',
                        status='failed', sender_thread_id='01a0703a-2359-7951-b2a1-8e057c42085e',
                        receiver_thread_ids=[HELPER], receiver_agents=[dict(thread_id=HELPER, agent_nickname='Hilbert')],
                        agents_states={HELPER: dict(errored=error)}))


def cx_end(at, error=STREAM, kind='response_stream_disconnected'):
    return cx(at, 'event_msg', type='task_complete', turn_id='01a07593-4a81-7610-8fb4-bcb9436a580c',
              last_agent_message=None, error=dict(message=error, codex_error_info=kind) if error else None)


class RecoveryTest(unittest.TestCase):
    """No console, network or model is touched: the screen, the typing and the network check are stand-ins."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='colony-recovery-')
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root, self.transcript = base / 'project', base / 'session.jsonl'
        (self.root / '.board').mkdir(parents=True)
        env = patch.dict(os.environ, COLONY_BOARD_HOME=str(base / 'board'))
        env.start()
        self.addCleanup(env.stop)
        board.save_registry(dict(projects=[str(self.root)], roots=[], settings=dict(messaging=False, active_hours='off')))
        atomic_json(context.file(self.root, 'context-session.json'), dict(provider='claude', id='s1', path=str(self.transcript)))
        self.state, self.draft, self.online, self.typed = 'idle', False, True, []
        for target, name, value in ((console, 'snapshot', lambda root, lines=6, name=None: dict(state=self.state, lines=[])),
                                    (console, 'drafting', lambda name: self.draft),
                                    (console, 'type_into', lambda name, text: self.typed.append(text) or True),
                                    (recovery, 'reachable', lambda host: self.online)):
            p = patch.object(target, name, side_effect=value)
            p.start()
            self.addCleanup(p.stop)
        recovery._scans.clear()

    def write(self, entries, path=None):
        Path(path or self.transcript).write_text(''.join(json.dumps(e) + '\n' for e in entries))

    def test_claude_code_reads_what_the_drop_stopped_from_its_transcript(self):
        t = 1791361623.0                              # 2026-10-07T08:27:03Z, its last turn's end
        self.write(night(t))
        found = providers.get('claude').network_stop(self.transcript)
        self.assertEqual((found['key'], found['at'], found['error'], found['ok_at']), (f'error-{t}', t, EAI_AGAIN, t - 1249))
        self.assertEqual([(h['id'], h['name'], h['at']) for h in found['helpers']],
                         [('a1dc2f44ba8931af3', 'Street package', t - 757), ('a8f969af25cef90bc', 'Texture worker', t - 372),
                          ('ae66e2e223efd43aa', 'Hillside', t - 372), ('a567ab2df35234e64', 'Checks', t - 371)])
        self.write([cc_reply(t - 9), cc_report(t - 8, 'a1', 'Street package',
                                               error='Agent terminated early due to an API error: API Error: 400 prompt is too long'),
                    cc_error(t, 'API Error: The response stopped arriving. The response above may be incomplete.')])
        found = providers.get('claude').network_stop(self.transcript)
        self.assertEqual((found['error'], found['helpers']),
                         ('The response stopped arriving. The response above may be incomplete.', []),
                         'a reply cut off mid-stream is a stop; a helper stopped by something else is not lost to it')

    def test_claude_code_sees_no_stop_where_the_network_did_not_end_the_last_turn(self):
        t = 1791361623.0
        cases = {'a helper that ended normally': [cc_reply(t - 9), cc_report(t - 8, 'a1', 'Hillside', status='completed'), cc_reply(t)],
                 'a turn that ended well after the drop': night(t - 60) + [cc_typed(t - 50), cc_reply(t)],
                 'a turn begun since, still under way': night(t - 60) + [cc_typed(t)],
                 'a server error, not the network': [cc_reply(t - 9), cc_error(t, 'API Error: 529 Overloaded')],
                 'a sign-in that ran out': [cc_reply(t - 9), cc_error(t, 'API Error: 401 Invalid authentication credentials')]}
        for case, entries in cases.items():
            self.write(entries)
            self.assertIsNone(providers.get('claude').network_stop(self.transcript), case)

    def test_codex_reads_the_same_from_its_rollout(self):
        t, codex = 1788681814.0, providers.get('codex')     # 2026-09-06T08:03:34Z, the real turn's end
        self.write([cx(t - 300, 'event_msg', type='task_started', turn_id='t1'), cx_wait(t - 120), cx_lost(t - 60), cx_end(t)])
        found = codex.network_stop(self.transcript)
        self.assertEqual((found['key'], found['error'], found['ok_at']), ('01a07593-4a81-7610-8fb4-bcb9436a580c', STREAM, t - 120))
        self.assertEqual([(h['id'], h['name']) for h in found['helpers']], [(HELPER, 'Hilbert')])
        self.write([cx_wait(t - 120), cx_lost(t - 60, "You've hit your usage limit."), cx_end(t)])
        self.assertEqual(codex.network_stop(self.transcript)['helpers'], [], 'a helper at a usage limit waits for the safe pause')
        cases = {'the real loss, to capacity rather than the network': [cx_wait(t - 120), cx_lost(t - 60, CAPACITY),
                                                                         cx_end(t, CAPACITY, 'server_overloaded')],
                 'a turn that ended well': [cx_wait(t - 120), cx_end(t, None)],
                 'a turn under way': [cx_wait(t - 120), cx_end(t - 60), cx(t, 'event_msg', type='task_started', turn_id='t2')]}
        for case, entries in cases.items():
            self.write(entries)
            self.assertIsNone(codex.network_stop(self.transcript), case)

    def test_an_idle_console_is_nudged_once_the_network_is_back_and_once_only(self):
        self.write(night(time.time() - 600))
        later = (time.localtime().tm_hour + 2) % 24
        board.save_registry(dict(board.registry(), settings=dict(board.registry()['settings'],
                                                                 active_hours=f'{later:02d}:00-{later:02d}:30')))
        self.assertFalse(hours.active(), 'the night: it is work, not a question, so it goes on')
        self.online = False
        self.assertIsNone(recovery.tick(self.root), 'the network still down: a nudge now would only fail again')
        self.online = True
        text = recovery.tick(self.root)
        first = recovery.stop(self.root)['helpers'][0]['at']
        self.assertEqual(text, f'[colony] 4 helpers stopped on a network error at {hours.at_clock(first)}: Street package '
                               '(a1dc2f44ba8931af3); Texture worker (a8f969af25cef90bc); Hillside (ae66e2e223efd43aa); '
                               'Checks (a567ab2df35234e64). The network is back: resume or relaunch them and carry on.')
        self.assertEqual(self.typed, [text])
        recovery._scans.clear()                       # the board restarted
        self.assertIsNone(recovery.tick(self.root))
        self.assertEqual(self.typed, [text], 'once per loss, across restarts')

    def test_no_nudge_while_busy_typed_into_asked_paused_or_just_stopped(self):
        self.write(night(time.time() - 30))
        self.assertIsNone(recovery.tick(self.root), 'just stopped: a person at the console has the first move')
        self.write(night(time.time() - 600))
        self.state = 'working'
        self.assertIsNone(recovery.tick(self.root), 'busy')
        self.state, self.draft = 'idle', True
        self.assertIsNone(recovery.tick(self.root), 'the person is typing there')
        self.draft = False
        board.record_ask(self.root, 'k1', 'Which of the two layouts do you want?')
        self.assertIsNone(recovery.tick(self.root), 'its turn ended on a question to the person')
        board.answer_asks(self.root, 'in the console')
        progress.pause(self.root, True)
        self.assertIsNone(recovery.tick(self.root), 'the person paused it')
        progress.pause(self.root, False)
        paused = board.home() / 'usage' / 'paused.json'
        paused.parent.mkdir(parents=True, exist_ok=True)
        paused.write_text(json.dumps({str(self.root): dict(provider='claude', window='weekly', resets_at=None)}))
        self.assertIsNone(recovery.tick(self.root), 'the safe pause holds it')
        paused.unlink()
        self.assertEqual(self.typed, [])
        self.assertTrue(recovery.tick(self.root))

    def test_no_nudge_for_helpers_that_ended_normally(self):
        now = time.time()
        self.write([cc_reply(now - 900), cc_report(now - 800, 'ae66e2e223efd43aa', 'Hillside', status='completed'),
                    cc_reply(now - 700)])
        self.assertIsNone(recovery.tick(self.root))
        self.assertEqual(self.typed, [])

    def test_a_nudge_the_network_stops_again_waits_longer_each_time(self):
        now = time.time()
        entries = night(now - 900)
        self.write(entries)
        self.assertIn('4 helpers', recovery.tick(self.root))

        def again(rewind, reply=False):
            entries.extend([cc_typed(now - 300, self.typed[-1])] + ([cc_reply(now - 290)] if reply else [])
                           + [cc_error(now - 200 + len(entries))])
            self.write(entries)
            rows = recovery.records()
            rows[str(self.root)]['at'] -= rewind      # as if the last nudge was that long ago
            (board.home() / 'recovery.json').write_text(json.dumps(rows))
            return recovery.tick(self.root)
        self.assertIsNone(again(0), 'its turn stopped too, with no reply: it waits before the next')
        text = again(recovery.RETRY)
        self.assertTrue(text.startswith('[colony] Your turn stopped on a network error at '), 'the helpers are named once')
        self.assertTrue(text.endswith(f'({EAI_AGAIN}). The network is back: carry on where you were.'))
        self.assertIsNone(again(recovery.RETRY), 'twice stopped: the wait has doubled')
        self.assertTrue(again(recovery.RETRY))
        self.assertIsNone(again(400), 'three times: it waits twenty minutes now')
        self.assertTrue(again(400, reply=True), 'a reply since the last nudge: the waits start over')

    def test_the_watcher_resumes_work_whether_or_not_the_monitor_runs(self):
        for enabled in (False, True):
            with patch.object(recovery, 'tick_all') as resumed, patch.object(vision, 'observe_all'), \
                    patch.object(continuation, 'tick_all'), patch.object(context, 'tick_all'), \
                    patch.object(monitor.Watcher, 'mail'), patch.object(monitor.Watcher, 'models'), \
                    patch.object(monitor.Watcher, 'usage'), patch.object(monitor.Watcher, 'current'), \
                    patch.object(monitor, 'snapshot', return_value=dict(state='working', lines=[])), \
                    patch.object(monitor, 'muted', return_value=True):
                monitor.Watcher(enabled=enabled).tick()
                resumed.assert_called_once_with()

    def test_the_network_check_runs_apart_and_counts_the_network_down_until_it_answers(self):
        tried, answer = [], threading.Event()

        class Connection:
            def close(self):
                pass

        def connect(address, timeout):
            answer.wait(5)                            # a network slow to answer
            tried.append(address)
            return Connection()

        def settled(host):
            for _ in range(500):
                if not recovery._probes[host]['busy']:
                    return True
                time.sleep(0.01)
        recovery._probes.clear()
        with patch.object(recovery.socket, 'create_connection', side_effect=connect):
            self.assertFalse(REACHABLE('api.example'), 'not known yet: counted down, and the watcher did not wait')
            answer.set()
            self.assertTrue(settled('api.example'))
            self.assertTrue(REACHABLE('api.example'))
            self.assertTrue(REACHABLE('api.example'))
        self.assertEqual(tried, [('api.example', 443)], 'tried once a minute at most, never each tick')
        recovery._probes['api.example']['at'] -= recovery.PROBE          # a minute on: due again
        with patch.object(recovery.socket, 'create_connection', side_effect=OSError('down')):
            REACHABLE('api.example')
            self.assertTrue(settled('api.example'))
            self.assertFalse(REACHABLE('api.example'))
        recovery._probes.clear()


if __name__ == '__main__':
    unittest.main()
