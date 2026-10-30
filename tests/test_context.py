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
        self.payload = dict(session_id='exact-id', transcript_path=str(self.path), hook_event_name='PreCompact', cwd=str(self.root))
        context._cache.clear()

    def event(self, kind, payload):
        with self.path.open('a') as f:
            f.write(json.dumps(dict(type=kind, payload=payload)) + '\n')

    def message(self, role, text, phase='final_answer'):
        self.event('response_item', dict(type='message', role=role, content=[dict(type='input_text', text=text)]))
        if role == 'assistant':
            self.event('event_msg', dict(type='item_completed', item=dict(type='AgentMessage', phase=phase,
                       content=[dict(type='Text', text=text)])))

    def task(self, kind, ident='personal-turn'):
        self.event('event_msg', dict(type=kind, turn_id=ident))

    def assert_refresh_waits(self):
        from colony import continuation
        with patch.object(context, 'safe', return_value=True), patch.object(continuation, 'hold') as held, \
                patch.object(console, 'type_into') as typed:
            context.tick(self.root)
        held.assert_not_called()
        typed.assert_not_called()
        self.assertFalse(context.refreshing(self.root), 'unfinished work must not acquire a refresh hold')

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

    def test_unfinished_exchange_postpones_and_a_large_one_keeps_its_start_and_end(self):
        self.message('user', 'START of the request. ' + 'x' * 6000); self.message('assistant', 'y' * 3000 + ' Done, at the end.')
        recent = context.tail(context.transcript(self.s))
        self.assertEqual([m['role'] for m in recent], ['user', 'trimmed', 'assistant'])
        self.assertTrue(recent[0]['text'].startswith('START of the request. '))
        self.assertTrue(recent[2]['text'].endswith(' Done, at the end.'))
        self.assertIn('[colony trimmed this exchange', recent[1]['text'])
        self.assertEqual(recent[1]['left'], 9040 - len(recent[0]['text']) - len(recent[2]['text']), 'what it says it left out')
        self.assertLessEqual(sum(len(m['text'].encode()) + 64 for m in recent), context.TAIL_BUDGET)
        self.message('user', 'pending')
        with self.assertRaisesRegex(ValueError, 'not completed'):
            context.tail(context.transcript(self.s))

    def test_codex_commentary_and_tools_cannot_start_refresh_even_if_screen_looks_idle(self):
        self.task('task_started')
        self.message('user', 'Explain whether this project can delegate its survey.')
        self.message('assistant', 'I will inspect the survey setup.', phase='commentary')
        self.event('response_item', dict(type='function_call', name='exec_command', arguments='{}'))
        self.message('assistant', 'The survey has several outstanding items.', phase='commentary')
        self.usage()
        self.assert_refresh_waits()
        self.message('assistant', 'Yes, it can delegate those items, for these reasons.')
        self.assert_refresh_waits()  # An emitted final message can precede the native turn's completion.
        self.task('task_complete', 'stale-turn')
        self.assert_refresh_waits()
        self.task('task_complete')
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into', return_value=True) as typed:
            context.tick(self.root)
        self.assertIn('Context carry-over', typed.call_args.args[1])

    def test_maintenance_answer_cannot_end_an_active_native_turn_before_task_complete(self):
        self.message('user', 'Earlier request.'); self.message('assistant', 'Earlier answer.')
        self.task('task_started')
        self.message('user', 'Can the survey delegate this work?')
        self.message('assistant', 'I am checking.', phase='commentary')
        self.message('user', '[colony] Context carry-over (fedcba0987654321).')
        self.message('assistant', '<colony-carry id="fedcba0987654321">Answer the latest question directly.</colony-carry>')
        self.usage()
        self.assert_refresh_waits()
        with self.assertRaisesRegex(ValueError, 'not completed'):
            context.tail(context.transcript(self.s))
        context.before_compact(self.root, 'codex', self.payload)
        job = context.read(context.file(self.root))['job']
        self.assertTrue(job['automatic'], 'native emergency compaction remains allowed')
        self.assertEqual(job['phase'], 'prepared')

    def test_answer_resumed_under_maintenance_mail_does_not_leave_a_permanent_refresh_hold(self):
        self.task('task_started'); self.message('user', 'Answer the survey question.')
        self.message('assistant', 'I am checking.', phase='commentary'); self.task('task_complete')
        self.task('task_started', 'resumed-turn')
        self.message('user', '[colony] Mail: finish the original answer.')
        self.message('assistant', 'Here is the original answer, with its evidence.')
        self.task('task_complete', 'resumed-turn'); self.usage()
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into', return_value=True) as typed:
            context.tick(self.root)
        self.assertIn('Context carry-over', typed.call_args.args[1])

    def test_codex_completion_survives_incremental_cache_reload_and_old_cache_is_rebuilt(self):
        self.task('task_started'); self.message('user', 'Give a complete answer.')
        self.message('assistant', 'Still investigating.', phase='commentary'); self.usage()
        snapshot = context.transcript(self.s)
        cache_file = next((board.home() / 'context-cache').glob('*.json'))
        # The old cursor already consumed the phase and turn events, but its prose alone cannot prove completion.
        legacy = {k: snapshot[k] for k in ('inode', 'offset', 'exchanges', 'current', 'tokens', 'window', 'usage_at',
                                         'markers', 'summary', 'compact', 'carries', 'preserved')}
        legacy['markers'] = list(legacy['markers'])
        atomic_json(cache_file, legacy); context._cache.clear()
        self.assert_refresh_waits()
        self.message('assistant', 'Here is the complete answer.')
        context._cache.clear()
        self.assert_refresh_waits()
        self.task('task_complete'); context._cache.clear()
        self.assertEqual(context.tail(context.transcript(self.s))[-1]['text'], 'Here is the complete answer.')

    def test_ready_refresh_rechecks_new_personal_input_before_compact_dispatch(self):
        self.message('user', 'First request.'); self.message('assistant', 'First answer.')
        self.state(carry_turn='earlier-carry-turn')
        calls = 0
        def looks_idle(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.task('task_started', 'new-turn')
                self.message('user', 'Wait, first answer this follow-up.')
                self.message('assistant', 'I will check that too.', phase='commentary')
            return True
        with patch.object(context, 'safe', side_effect=looks_idle), patch.object(console, 'type_into') as typed:
            context.tick(self.root)
        typed.assert_not_called()
        self.assertFalse(context.refreshing(self.root))

    def test_carry_stop_waits_for_its_native_completion_without_requesting_another_carry(self):
        self.carry_stop_waits(turn_id='carry-turn')

    def test_carry_stop_without_turn_id_waits_for_the_active_native_turn(self):
        self.carry_stop_waits()

    def carry_stop_waits(self, **stop):
        from colony import continuation
        self.message('user', 'Answer this first.'); self.message('assistant', 'Here is the answer.'); self.usage()
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into', return_value=True) as typed:
            context.tick(self.root)
            job = context.read(context.file(self.root))['job']
            self.task('task_started', 'carry-turn')
            self.message('user', f'[colony] Context carry-over ({job["id"]}).')
            carry = f'<colony-carry id="{job["id"]}">Nothing needs repeating.</colony-carry>'
            self.message('assistant', carry)
            self.assertTrue(context.carry_reply(self.root, 'codex', dict(self.payload, hook_event_name='Stop',
                            last_assistant_message=carry, **stop)))
            with patch.object(continuation, 'hold') as held:
                context.tick(self.root); context.tick(self.root)
            held.assert_not_called()
            self.assertEqual(context.read(context.file(self.root))['job']['phase'], 'ready')
            self.assertEqual(len(typed.call_args_list), 1, 'no second carry while its native turn finishes')
            self.task('task_complete', 'carry-turn')
            context.tick(self.root)
        self.assertEqual([call.args[1] for call in typed.call_args_list][1:], ['/compact'])

    def test_claude_tool_use_prose_waits_for_an_end_turn_reply(self):
        self.s['provider'] = 'claude'
        atomic_json(context.file(self.root, 'context-session.json'), self.s)
        board.save_registry({'projects': [], 'settings': {'provider': 'claude', 'messaging': False}})
        def append(event):
            with self.path.open('a') as stream:
                stream.write(json.dumps(event) + '\n')
        append(dict(type='user', message=dict(content='Answer the survey question.')))
        append(dict(type='assistant', message=dict(content=[dict(type='text', text='I will inspect the files.'),
                    dict(type='tool_use', id='tool-1', name='Read', input={})], stop_reason='tool_use',
                    usage=dict(input_tokens=125000))))
        context.usage(self.root, dict(session_id='exact-id', context_window=dict(context_window_size=200000,
                      current_usage=dict(input_tokens=125000))))
        self.assert_refresh_waits()
        append(dict(type='assistant', message=dict(content=[dict(type='text', text='Here is the answer.')],
                    stop_reason='end_turn')))
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into', return_value=True) as typed:
            context.tick(self.root)
        self.assertIn('Context carry-over', typed.call_args.args[1])

    def test_trimming_keeps_whole_messages_where_they_fit_and_never_splits_a_character(self):
        exchange = [dict(role='user', text='Fix the export, then tell me.'),
                    *[dict(role='assistant', text=f'Step {n}: ' + 'Ωé😀' * 60) for n in range(20)],
                    dict(role='assistant', text='Fixed and pushed; nothing else changed.')]
        recent = context.tail(dict(exchanges=[exchange]), source='/x/rollout.jsonl')
        self.assertEqual(recent[0], exchange[0], 'the request, whole')
        self.assertEqual(recent[-1], exchange[-1], 'the latest reply, whole')
        mark = next(m for m in recent if m['role'] == 'trimmed')
        self.assertIn('the whole exchange is in /x/rollout.jsonl', mark['text'])
        kept = [m for m in recent if m['role'] != 'trimmed']
        for m in kept:
            self.assertTrue(any(m['text'] in e['text'] for e in exchange), 'every kept piece is verbatim')
        self.assertEqual(mark['left'], sum(len(e['text'].encode()) for e in exchange) - sum(len(m['text'].encode()) for m in kept))
        self.assertLessEqual(sum(len(m['text'].encode()) + 64 for m in recent), context.TAIL_BUDGET)
        one = [dict(role='user', text='Begin here. ' + 'Ω' * 20_000 + ' The question at the end?'), dict(role='assistant', text='Yes.')]
        recent = context.tail(dict(exchanges=[one]))
        self.assertEqual([m['role'] for m in recent], ['user', 'trimmed', 'user', 'assistant'], 'one long message: its start and its end')
        self.assertTrue(recent[0]['text'].startswith('Begin here. ') and recent[2]['text'].endswith('The question at the end?'))
        context.log(self.root, dict(tail=recent), 'restored')
        self.assertEqual(board.read(self.root, 'context-history.jsonl')[-1]['exchanges'], 1, 'still one exchange')

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

    def test_the_monitor_types_nothing_for_its_context_and_disabled_monitor_watcher(self):
        self.message('user', 'keep blue'); self.message('assistant', 'yes')
        atomic_json(context.file(self.root), dict(last=time.time() - 30 * 3600))
        with patch.object(context, 'safe', return_value=True), patch.object(context, 'is_monitor', return_value=True), patch.object(console, 'type_into', return_value=True) as typed:
            context.tick(self.root); typed.assert_not_called()   # it compacts natively: nothing in the person's view
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

    def test_claude_summary_carry_is_not_reinjected_but_verbatim_tail_survives(self):
        self.s['provider'] = 'claude'
        atomic_json(context.file(self.root, 'context-session.json'), self.s)
        self.state('prepared', tail=[dict(role='user', text='Keep the exact recent words.')], boundary=0)
        carry = '<colony-carry id="1234567890abcdef">Already sent the invoice; do not repeat.</colony-carry>'
        events = [dict(type='assistant', message=dict(content=carry)),
                  dict(type='system', subtype='compact_boundary'),
                  dict(type='user', isCompactSummary=True, message=dict(content='Native summary: ' + carry))]
        self.path.write_text(''.join(json.dumps(e) + '\n' for e in events))
        payload = dict(self.payload, hook_event_name='SessionStart', source='compact')
        with patch.object(context, 'register', return_value=True):
            restored = context.on_prompt(self.root, 'claude', payload)
        self.assertNotIn('Already sent the invoice', restored)
        self.assertIn('Keep the exact recent words.', restored)
        self.assertIn('native summary already retains', restored)
        context._cache.clear()
        self.assertIn('Already sent the invoice', context.transcript(self.s)['summary'])

    def test_claude_compaction_keeping_the_carry_reply_word_for_word_does_not_repeat_it(self):
        self.s['provider'] = 'claude'
        atomic_json(context.file(self.root, 'context-session.json'), self.s)
        self.state('prepared', tail=[], boundary=0)
        carry = '<colony-carry id="1234567890abcdef">Already sent the invoice; do not repeat.</colony-carry>'
        events = [dict(type='assistant', uuid='u-old', message=dict(content='<colony-carry id="fedcba0987654321">Older.</colony-carry>')),
                  dict(type='assistant', uuid='u-carry', message=dict(content=[dict(type='text', text=carry)])),
                  dict(type='system', subtype='compact_boundary',
                       compactMetadata=dict(preservedMessages=dict(uuids=['u-carry'], allUuids=['u-x', 'u-carry']))),
                  dict(type='user', isCompactSummary=True, message=dict(content='Native summary, the carry reworded.'))]
        self.path.write_text(''.join(json.dumps(e) + '\n' for e in events))
        with patch.object(context, 'register', return_value=True):
            restored = context.on_prompt(self.root, 'claude', dict(self.payload, hook_event_name='SessionStart', source='compact'))
        self.assertNotIn('Already sent the invoice', restored)
        self.assertIn('kept word for word', restored)
        context._cache.clear()
        self.assertEqual(context.transcript(self.s)['preserved'], ['1234567890abcdef'], 'only the one kept, read back from the cache')

    def test_claude_summary_quoting_only_colonys_request_still_restores_the_carry(self):
        self.s['provider'] = 'claude'
        atomic_json(context.file(self.root, 'context-session.json'), self.s)
        self.state('prepared', tail=[], boundary=0)
        request = '[colony] Context carry-over (1234567890abcdef). Reply only inside <colony-carry id="1234567890abcdef">...</colony-carry>.'
        events = [dict(type='system', subtype='compact_boundary'),
                  dict(type='user', isCompactSummary=True, message=dict(content='All user messages: ' + request))]
        self.path.write_text(''.join(json.dumps(e) + '\n' for e in events))
        with patch.object(context, 'register', return_value=True):
            restored = context.on_prompt(self.root, 'claude', dict(self.payload, hook_event_name='SessionStart', source='compact'))
        self.assertIn('Already sent the invoice; do not repeat.', restored)

    def test_codex_compaction_keeping_colonys_request_verbatim_still_restores_the_carry(self):
        request = '[colony] Context carry-over (1234567890abcdef). Reply only inside <colony-carry id="1234567890abcdef">...</colony-carry>.'
        self.message('user', 'Keep blue.'); self.message('assistant', 'Already sent.')
        self.message('user', request)
        self.message('assistant', '<colony-carry id="1234567890abcdef">Already sent the invoice; do not repeat.</colony-carry>')
        self.state()
        context.before_compact(self.root, 'codex', self.payload)
        kept = [dict(type='message', role='user', content=[dict(type='input_text', text=t)]) for t in ('Keep blue.', request)]
        self.event('compacted', dict(message='', replacement_history=kept + [dict(type='compaction', encrypted_content='opaque')]))
        prompt = dict(self.payload, hook_event_name='UserPromptSubmit')
        text = context.on_prompt(self.root, 'codex', prompt)
        self.assertIn('Already sent the invoice; do not repeat.', text)
        self.assertNotIn('already retains', text)
        self.event('compacted', dict(message='Summary: Already sent the\ninvoice;  do not repeat.'))
        self.assertIn('already retains', context.on_prompt(self.root, 'codex', prompt), 'the summary itself holds it')

    def test_original_carry_before_compaction_is_not_mistaken_for_summary(self):
        self.message('user', 'Original words')
        self.message('assistant', '<colony-carry id="1234567890abcdef">Original carry.</colony-carry>')
        self.event('compacted', dict(message='A native summary without that carry.'))
        self.assertEqual(context.transcript(self.s)['summary'], 'A native summary without that carry.')
        self.assertFalse(context.retained('Original carry.', context.transcript(self.s)['summary']))

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

    def test_deliberately_new_session_does_not_inherit_old_usage_guard_or_carry(self):
        st = self.state('restored')
        st.update(await_usage=999999, ineffective='old session', carry=dict(text='old intent', at='yesterday'))
        atomic_json(context.file(self.root), st)
        atomic_json(context.file(self.root, 'context-session.json'), dict(self.s, id='new-session'))
        with patch.object(context, 'safe', return_value=True):
            context.tick(self.root)
        st = context.read(context.file(self.root))
        self.assertEqual(set(st), {'last', 'session', 'carries'})
        self.assertEqual(st['carries'], {'exact-id': dict(text='old intent', at='yesterday')}, 'the old conversation keeps its own')
        self.assertEqual(board.read(self.root, 'context-history.jsonl')[-1]['status'], 'new conversation')

    def test_a_long_last_exchange_is_restored_trimmed_within_the_hook_limit_and_the_refresh_goes_ahead(self):
        self.message('user', 'Keep the colour periwinkle. ' + 'Ω😀 and more. ' * 900)
        self.message('assistant', 'Starting. ' + 'step done; ' * 700 + 'All finished: the invoice is already sent.')
        self.usage()                                   # 125,000 of a 200,000 window: over the threshold
        carry = 'Ω' * (context.CARRY_BUDGET // 2)      # the largest carry-over it takes
        with patch.object(context, 'safe', return_value=True), patch.object(console, 'type_into', return_value=True) as typed:
            context.tick(self.root)
            self.assertIn('Context carry-over', typed.call_args.args[1], 'not deferred: the carry-over is asked for')
            job = context.read(context.file(self.root))['job']
            self.assertTrue(context.carry_reply(self.root, 'codex', dict(self.payload, hook_event_name='Stop',
                            last_assistant_message=f'<colony-carry id="{job["id"]}">{carry}</colony-carry>')))
            context.tick(self.root)
            self.assertEqual(typed.call_args.args[1], '/compact')
        context.before_compact(self.root, 'codex', self.payload)
        self.event('compacted', dict(message='A native summary.'))
        prompt = dict(self.payload, hook_event_name='UserPromptSubmit')
        text = context.on_prompt(self.root, 'codex', prompt)
        self.assertIn('Keep the colour periwinkle.', text, 'its start')
        self.assertIn('All finished: the invoice is already sent.', text, 'its end')
        self.assertIn('[colony trimmed this exchange to fit the restoration budget', text)
        self.assertIn(carry, text)
        self.assertLessEqual(len(text.encode()), context.OUTPUT_BUDGET)
        self.assertLess(len(text), 10_000, "within Claude Code's hook output limit")
        context.delivered(self.root, prompt)
        self.message('developer', text)
        context.on_prompt(self.root, 'codex', prompt)
        rows = board.read(self.root, 'context-history.jsonl')
        self.assertEqual([r['status'] for r in rows], ['restored'], 'nothing deferred on the way')
        self.assertEqual((rows[0]['trigger'], rows[0]['carry_bytes'], rows[0]['exchanges']), ('scheduled', len(carry.encode()), 1))
        self.assertGreater(rows[0]['trimmed'], 0)
        self.assertIn('restored trimmed', rows[0]['reason'])

    def test_a_conversation_left_and_taken_up_again_restores_its_own_carry_over_and_the_history_says_why(self):
        """Colony's console went to Codex and back on 22-23 October: the switch cleared the refresh state, the Claude
        conversation's carry-over with it, and the 12 native compactions after it restored none, with no reason logged."""
        self.message('user', 'Keep blue.'); self.message('assistant', 'Kept.')
        self.state('requested')
        self.assertTrue(context.carry_reply(self.root, 'codex', dict(self.payload, hook_event_name='Stop',
                        last_assistant_message='<colony-carry id="1234567890abcdef">Already sent the invoice.</colony-carry>')))
        other = self.root / 'other.jsonl'
        other.write_text(''.join(json.dumps(dict(type='response_item', payload=p)) + '\n' for p in (
            dict(type='message', role='user', content=[dict(type='input_text', text='Hello again.')]),)))
        mine, theirs = self.payload, dict(self.payload, session_id='other-id', transcript_path=str(other))
        def compact(payload, path):
            """A native compaction in that conversation, and what it restores."""
            context.before_compact(self.root, 'codex', payload)
            with path.open('a') as f:
                f.write(json.dumps(dict(type='compacted', payload=dict(message='summary'))) + '\n')
            prompt = dict(payload, hook_event_name='UserPromptSubmit')
            text = context.on_prompt(self.root, 'codex', prompt)
            context.delivered(self.root, prompt)
            with path.open('a') as f:
                f.write(json.dumps(dict(type='response_item', payload=dict(type='message', role='developer',
                        content=[dict(type='input_text', text=text)]))) + '\n')
            context.on_prompt(self.root, 'codex', prompt)
            return text
        for payload in (theirs, mine):                 # away to another conversation, and back
            self.assertTrue(context.register(self.root, 'codex', dict(payload, hook_event_name='SessionStart')))
            with patch.object(context, 'safe', return_value=False):
                context.tick(self.root)
            restored = compact(payload, other if payload is theirs else self.path)
            if payload is theirs:
                self.assertIn('(none)', restored, 'a new conversation inherits nothing')
        self.assertIn('Already sent the invoice.', restored, 'its own carry-over, back with it')
        rows = board.read(self.root, 'context-history.jsonl')
        self.assertEqual([r['status'] for r in rows], ['new conversation', 'restored', 'new conversation', 'restored'])
        self.assertEqual((rows[1]['trigger'], rows[1]['carry_bytes']), ('native', 0))
        self.assertIn('no carry-over is recorded in this conversation yet', rows[1]['reason'])
        self.assertEqual((rows[3]['trigger'], rows[3]['reason']), ('native', ''))
        self.assertTrue(rows[3]['carry_at'] and rows[3]['carry_bytes'])

    def test_a_carry_over_recorded_before_they_were_kept_apart_stays_with_its_conversation(self):
        st = self.state('restored')
        st['carry'] = dict(text='Older single carry-over.', at='earlier')
        atomic_json(context.file(self.root), st)
        self.message('user', 'blue'); self.message('assistant', 'yes')
        context.before_compact(self.root, 'codex', self.payload)
        st = context.read(context.file(self.root))
        self.assertEqual((st['job']['carry'], st['job']['written']), ('Older single carry-over.', 'earlier'))
        self.assertEqual(st['carries'], {'exact-id': dict(text='Older single carry-over.', at='earlier')})
        self.assertNotIn('carry', st)

    def test_a_carry_over_too_large_is_deferred_in_the_history_with_its_size(self):
        self.state('requested')
        payload = dict(self.payload, hook_event_name='Stop',
                       last_assistant_message='<colony-carry id="1234567890abcdef">' + 'x' * 3001 + '</colony-carry>')
        self.assertTrue(context.carry_reply(self.root, 'codex', payload))
        row = board.read(self.root, 'context-history.jsonl')[-1]
        self.assertEqual((row['status'], row['reason']), ('deferred', 'Carry-over was empty or too large (3,001 bytes).'))
