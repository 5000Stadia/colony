"""Same-chat refresh boundaries and recovery, with isolated provider transcripts."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from colony import board, console, context, monitor
from colony.codex_transfer import atomic_json


class ContextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'
        (self.root / '.board').mkdir(parents=True)
        self.env = patch.dict(os.environ, COLONY_BOARD_HOME=str(Path(self.tmp.name) / 'home'),
                              COLONY_PROJECT=str(self.root), COLONY_CONSOLE=console.session_name(self.root))
        self.env.start(); self.addCleanup(self.env.stop)
        board.save_registry({'projects': [], 'settings': {'provider': 'codex', 'messaging': False}})
        self.path = self.root / 'rollout.jsonl'; self.path.touch()
        self.s = dict(provider='codex', id='exact-id', path=str(self.path))
        atomic_json(context.file(self.root, 'context-session.json'), self.s)
        self.payload = dict(session_id='exact-id', transcript_path=str(self.path), hook_event_name='PreCompact')
        context._cache.clear()

    def event(self, kind, payload):
        with self.path.open('a') as f:
            f.write(json.dumps(dict(type=kind, payload=payload)) + '\n')

    def message(self, role, text):
        self.event('response_item', dict(type='message', role=role, content=[dict(type='input_text', text=text)]))
        if role == 'assistant':
            self.event('event_msg', dict(type='item_completed', item=dict(type='AgentMessage', content=[dict(type='Text', text=text)])))

    def usage(self, tokens=125_000, total=900_000):
        self.event('event_msg', dict(type='token_count', info=dict(last_token_usage=dict(total_tokens=tokens),
                     total_token_usage=dict(total_tokens=total), model_context_window=200_000)))

    def state(self, phase='ready', **kw):
        st = dict(last=time.time(), job=dict(id='1234567890abcdef', session='exact-id', phase=phase,
                  carry='Already sent the invoice; do not repeat.', written='now', asked=time.time(), **kw))
        atomic_json(context.file(self.root), st)
        return st

    def test_exact_tail_excludes_tools_and_maintenance_and_preserves_whitespace(self):
        self.message('user', '<environment_context>setup</environment_context>')
        self.message('user', '  Original\n\nΩ  text. ')
        self.event('response_item', dict(type='function_call_output', output='huge tool output'))
        self.message('assistant', '  Answer\nverbatim ')
        self.message('user', '[colony] prepare carry-over')
        self.message('assistant', 'maintenance answer')
        self.usage(123, 999999)
        snap = context.transcript(self.s)
        self.assertEqual(context.tail(snap), [dict(role='user', text='  Original\n\nΩ  text. '),
                                              dict(role='assistant', text='  Answer\nverbatim ')])
        self.assertEqual(snap['tokens'], 123)
        self.assertEqual(context.transcript(self.s)['exchanges'], snap['exchanges'])

    def test_large_exchange_and_unfinished_exchange_postpone(self):
        self.message('user', 'x' * 6000); self.message('assistant', 'done')
        with self.assertRaisesRegex(ValueError, 'not truncated'):
            context.tail(context.transcript(self.s))
        self.message('user', 'pending')
        with self.assertRaisesRegex(ValueError, 'not completed'):
            context.tail(context.transcript(self.s))

    def test_usage_is_current_and_no_window_never_guesses(self):
        self.message('user', 'hello'); self.message('assistant', 'done'); self.usage(10)
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into') as typed:
            context.tick(self.root); typed.assert_not_called()
            self.usage()
            context.tick(self.root); self.assertIn('Context carry-over', typed.call_args.args[1])

    def test_missing_carry_times_out_without_compaction(self):
        self.state('requested')['job']
        st = context.read(context.file(self.root)); st['job']['asked'] = time.time() - context.TIMEOUT - 1
        atomic_json(context.file(self.root), st)
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into') as typed:
            context.tick(self.root); typed.assert_not_called()
        self.assertEqual(context.read(context.file(self.root))['job']['phase'], 'deferred')

    def test_final_safety_race_and_unsent_input_do_not_strand_compacting(self):
        self.state(); self.message('user', 'hello'); self.message('assistant', 'done')
        with patch.object(context, 'safe', side_effect=[True, False]), patch.object(console, 'type_into') as typed:
            context.tick(self.root); typed.assert_not_called()
        self.assertEqual(context.read(context.file(self.root))['job']['phase'], 'deferred')

    def test_unknown_dispatch_is_never_retried(self):
        self.state(); self.message('user', 'hello'); self.message('assistant', 'done')
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into', side_effect=OSError('lost response')):
            context.tick(self.root)
        st = context.read(context.file(self.root)); self.assertEqual(st['job']['phase'], 'unknown')
        st['retry_after'] = 0; atomic_json(context.file(self.root), st); self.usage()
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into') as typed:
            context.tick(self.root); typed.assert_not_called()

    def test_only_successful_compaction_can_restore_and_lost_output_is_recoverable(self):
        self.state(); self.message('user', 'Keep blue.'); self.message('assistant', 'Already sent.')
        context.before_compact(self.root, 'codex', self.payload)
        prompt = dict(self.payload, hook_event_name='UserPromptSubmit')
        self.assertEqual(context.on_prompt(self.root, 'codex', prompt), '')
        self.event('compacted', dict(message='summary'))
        text = context.on_prompt(self.root, 'codex', prompt)
        self.assertIn('Keep blue.', text)
        self.assertEqual(context.on_prompt(self.root, 'codex', prompt), text, 'lost stdout can be retried')
        context.delivered(self.root, prompt)
        self.assertEqual(context.read(context.file(self.root))['job']['phase'], 'emitted')
        self.message('developer', text)
        self.assertEqual(context.on_prompt(self.root, 'codex', prompt), '')
        self.assertEqual(context.read(context.file(self.root))['job']['phase'], 'restored')

    def test_duplicate_precompact_keeps_nonce_and_helpers_cannot_replace_identity(self):
        self.state(); self.message('user', 'blue'); self.message('assistant', 'yes')
        context.before_compact(self.root, 'codex', self.payload)
        context.before_compact(self.root, 'codex', self.payload)
        self.assertEqual(context.read(context.file(self.root))['job']['id'], '1234567890abcdef')
        self.assertFalse(context.register(self.root, 'codex', dict(self.payload, agent_id='helper', session_id='wrong')))
        self.assertFalse(context.register(self.root, 'codex', dict(self.payload, session_id='wrong')))
        self.assertEqual(context.session(self.root)['id'], 'exact-id')

    def test_carry_is_captured_even_when_agent_cannot_write_files(self):
        self.state('requested')
        payload = dict(self.payload, hook_event_name='Stop', last_assistant_message='<colony-carry id="1234567890abcdef">Do not resend.</colony-carry>')
        self.assertTrue(context.carry_reply(self.root, 'codex', payload))
        self.assertEqual(context.read(context.file(self.root))['job']['phase'], 'ready')
        self.assertIn('Do not resend.', context.file(self.root, 'context-carried.md').read_text())

    def test_daily_monitor_and_disabled_monitor_watcher(self):
        self.message('user', 'keep blue'); self.message('assistant', 'yes')
        atomic_json(context.file(self.root), dict(last=time.time() - context.DAILY - 1))
        with patch.object(context, 'safe', return_value=True), patch.object(context, 'is_monitor', return_value=True), patch.object(console, 'type_into', return_value=True) as typed:
            context.tick(self.root); self.assertIn('Context carry-over', typed.call_args.args[1])
        with patch.object(context, 'tick_all') as refresh, patch.object(monitor.Watcher, 'mail'), patch('colony.vision.observe_all'):
            watcher = monitor.Watcher(enabled=False); watcher.tick(); refresh.assert_called_once()

    def test_claude_usage_cache_and_compaction_attachment(self):
        self.s['provider'] = 'claude'
        atomic_json(context.file(self.root, 'context-session.json'), self.s)
        events = [dict(type='user', message=dict(content='Preserve  two spaces.')),
                  dict(type='assistant', message=dict(content=[dict(type='text', text='Done.')],
                       usage=dict(input_tokens=100, cache_read_input_tokens=200, cache_creation_input_tokens=300))),
                  dict(type='attachment', attachment=dict(type='hook_success', content='[colony-restored-1234567890abcdef]'))]
        self.path.write_text(''.join(json.dumps(e) + '\n' for e in events))
        context.usage(self.root, dict(session_id='exact-id', context_window=dict(context_window_size=200000,
                      current_usage=dict(input_tokens=100, cache_read_input_tokens=200, cache_creation_input_tokens=300))))
        snap = context.transcript(self.s)
        self.assertEqual(context.occupancy(self.root, self.s, snap), (600, 200000))
        self.assertIn('1234567890abcdef', snap['markers'])
        context._cache.clear()  # A separate hook process resumes the persisted cursor.
        self.assertEqual(context.transcript(self.s)['offset'], snap['offset'])
        self.assertEqual(context.tail(context.transcript(self.s))[0]['text'], 'Preserve  two spaces.')

    def test_safety_checks_busy_attached_drafts_pauses_and_pending_delivery(self):
        from colony import usage, mail
        with patch.object(console, 'running', return_value=True), patch.object(console, 'attached', return_value=False), \
                patch.object(console, 'drafting', return_value=False), patch.object(console, 'snapshot', return_value={'state': 'idle'}) as screen, \
                patch.object(usage, 'paused', return_value={}) as paused, patch.object(board, 'open_notes', return_value=[]) as notes, \
                patch.object(mail, 'inbox', return_value=[]):
            self.assertTrue(context.safe(self.root))
            screen.return_value = {'state': 'working'}; self.assertFalse(context.safe(self.root))
            screen.return_value = {'state': 'idle'}
            paused.return_value = {str(self.root): {}}; self.assertFalse(context.safe(self.root))
            paused.return_value = {}
            notes.return_value = [dict(delivered_at=None)]; self.assertFalse(context.safe(self.root))
            notes.return_value = []
            with patch.object(console, 'drafting', return_value=True): self.assertFalse(context.safe(self.root))
            with patch.object(console, 'attached', return_value=True): self.assertFalse(context.safe(self.root))

    def test_fresh_usage_releases_guard_without_waiting_for_cooldown(self):
        self.state('restored'); self.usage(50000)
        st = context.read(context.file(self.root)); st.update(await_usage=1, retry_after=time.time()+3600)
        atomic_json(context.file(self.root), st)
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into') as typed:
            context.tick(self.root); typed.assert_not_called()
        self.assertNotIn('await_usage', context.read(context.file(self.root)))
        self.assertEqual(board.read(self.root, 'context-history.jsonl')[-1]['after'], 50000)

    def test_monitor_launch_resumes_its_exact_registered_codex_session(self):
        atomic_json(context.file(monitor.home(), 'context-session.json'), self.s)
        with patch.object(console, 'COMMAND', None), patch('colony.selection.migrate'), \
                patch.object(monitor, 'choice', return_value=('gpt-6.1-sol', 'medium', 'test')):
            self.assertIn('resume exact-id', console.command('monitor', folder=monitor.home()))

    def test_ineffective_refresh_does_not_loop(self):
        self.state('restored'); self.usage(150000)
        st = context.read(context.file(self.root)); st.update(await_usage=1, retry_after=0)
        atomic_json(context.file(self.root), st)
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into') as typed:
            context.tick(self.root); context.tick(self.root); typed.assert_not_called()
        self.assertIn('ineffective', context.read(context.file(self.root)))
