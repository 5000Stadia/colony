"""Shared vision: live text, scoped migration, and delivery without approval state."""
import concurrent.futures
import html
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from colony import board, console, consult, monitor, vision
from tests.test_board import BoardBase, ROADMAP


class VisionTest(BoardBase):
    def setUp(self):
        super().setUp()
        board.track(self.root)
        self.path = self.root / 'ROADMAP.md'

    def test_multiline_reader_keeps_vision_out_of_items_and_all_consumers_agree(self):
        value = 'A quiet garden companion.\n\n- [ ] R99 This is a fundamental, not a task.\n### Feel\nUnhurried.'
        vision.save(self.root, value, how='conversation', words='Yes, that is the vision.')
        road = board.roadmap(self.root)
        self.assertEqual(road['goal'], value)
        self.assertEqual(set(board.items(road)), {'R1', 'R2', 'R3'})
        self.assertIn(value, consult.brief(self.root, 'R2', 'A decision', 'Facts'))
        self.assertIn(html.escape(value, quote=True), board.vision_box(self.root, 0))
        self.assertIn('A quiet garden companion.', self.cli('projects').stdout)
        self.assertIn(value.replace('\n', '\n    '), self.cli('posture').stdout)
        self.assertIn(value, self.cli('vision').stdout)

    def test_legacy_intro_is_preserved_outside_vision(self):
        original = ROADMAP.replace('A tool for my plants.', 'A tool for my plants.\n\nRows are the project’s own work units.')
        self.path.write_text(original)
        self.assertEqual(board.roadmap(self.root)['goal'], 'A tool for my plants.')
        event = vision.save(self.root, 'The finished garden.', how='board', before='')
        result = self.path.read_text()
        self.assertTrue(result.startswith(original.split('## M1')[0]))
        self.assertTrue(result.endswith(original[original.index('## M1'):]))
        self.assertEqual(vision.section(result)['text'], 'The finished garden.')
        self.assertEqual(event['before'], 'A tool for my plants.')
        count = len(vision.history(self.root))
        vision.observe(self.root)
        self.assertEqual(len(vision.history(self.root)), count)
        self.assertEqual(len(board.notes(self.root)), 1)

    def test_fences_subheadings_and_empty_vision(self):
        original = '# Roadmap\n\nOld goal.\n\n```md\n## Vision\nFake\n```\n\n## M1 — Build\n- [ ] R1 Work\n'
        value = 'A finished thing.\n\n```md\n## Example\n```\n\n### Feel\nCalm.'
        changed = vision.replace(original, value)
        self.assertEqual(vision.section(changed)['text'], value)
        self.assertIn('## Vision\nFake', changed)
        self.assertEqual(board.roadmap(None, vision.replace(original, ''))['goal'], 'Old goal.')
        self.assertEqual(board.roadmap(None, board.SKELETON)['goal'], '')

    def test_stale_edits_and_section_injection_do_not_change_roadmap(self):
        vision.save(self.root, 'First.', how='board')
        original = self.path.read_text()
        with self.assertRaisesRegex(ValueError, 'changed while'):
            vision.save(self.root, 'Stale.', how='board', before='')
        with self.assertRaisesRegex(ValueError, 'subheadings'):
            vision.save(self.root, 'New.\n## M2 — injected', how='board')
        self.assertEqual(self.path.read_text(), original)
        self.path.write_text(original + '\n## Vision\nDuplicate.\n')
        with self.assertRaisesRegex(ValueError, 'more than one'):
            vision.save(self.root, 'New.', how='board')

    def test_conversation_requires_words_and_does_not_notify_itself(self):
        draft = Path(self.tmp.name) / 'draft.txt'
        draft.write_text('A gentle companion.\nThe long horizon.')
        result = self.cli('vision', '--file', str(draft))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('brainstorming', result.stderr)
        self.assertEqual(self.path.read_text(), ROADMAP)
        result = self.cli('vision', '--file', str(draft), '--words', 'Yes, that describes it.')
        self.assertEqual(result.returncode, 0, result.stderr)
        event = vision.history(self.root)[-1]
        self.assertEqual(event['words'], 'Yes, that describes it.')
        self.assertEqual(event['how'], 'conversation')
        self.assertTrue(event['at'].endswith('Z'))
        self.assertEqual(board.notes(self.root), [])
        vision.observe(self.root)
        self.assertEqual(board.notes(self.root), [])

    def test_file_changes_are_neutral_through_history_hook_board_and_wake(self):
        vision.save(self.root, 'First.', how='conversation', words='First is agreed.')
        self.path.write_text(vision.replace(self.path.read_text(), 'An external revision.'))
        vision.observe(self.root)
        [note] = board.notes(self.root)
        self.assertEqual(note['author'], 'observation')
        self.assertIn('Before:\nFirst.', note['text'])
        self.assertIn('After:\nAn external revision.', note['text'])
        self.assertIn('file change observed', board.thread([note]))
        with patch.object(console, 'snapshot', return_value={'state': 'idle'}), patch.object(console, 'type_into', return_value=True) as send:
            monitor.Watcher(enabled=False).mail()
        self.assertIn('observed file change', send.call_args.args[1])
        self.assertNotIn('person', send.call_args.args[1])
        board.record_ask(self.root, 'ask', 'Which chapter should come next?', explicit=True)
        result = self.cli('notes', '--deliver')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Observed file changes (no author or agreement inferred)', result.stdout)
        self.assertNotIn('full approval', result.stdout)
        self.assertNotIn('The person left notes', result.stdout)
        self.assertEqual(len(board.asks(self.root)), 1)
        vision.observe(self.root)
        self.assertEqual(len(board.notes(self.root)), 1)

    def test_missing_notification_recovers_and_replay_never_reopens_handled_note(self):
        append = board.append
        def fail_note(root, file, event):
            if file == 'notes.jsonl':
                raise OSError('simulated interruption')
            return append(root, file, event)
        with patch.object(board, 'append', side_effect=fail_note):
            with self.assertRaises(OSError):
                vision.save(self.root, 'Saved before interruption.', how='board')
        vision.observe(self.root)
        [note] = board.notes(self.root)
        board.deliver(self.root)
        board.append(self.root, 'notes.jsonl', dict(type='addressed', of=note['id'], at=board.now(), text='Acted.'))
        # Even a repeated raw event cannot clear delivery/addressed state.
        board.append(self.root, 'notes.jsonl', {k: v for k, v in note.items() if k not in ('reply', 'delivered_at', 'addressed_at')})
        vision.observe(self.root)
        [handled] = board.notes(self.root)
        self.assertIsNotNone(handled['delivered_at'])
        self.assertEqual(handled['reply'], 'Acted.')
        self.assertEqual(board.open_notes(self.root), [])

    def test_two_observers_make_one_note_and_one_stale_editor_loses(self):
        self.path.write_text(vision.replace(ROADMAP, 'An edit.'))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: vision.observe(self.root), range(2)))
        self.assertEqual(len(board.notes(self.root)), 1)
        def write(value):
            try:
                vision.save(self.root, value, how='board', before='An edit.')
                return 'saved'
            except ValueError:
                return 'stale'
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(write, ['A.', 'B.']), ['saved', 'stale'])
        self.assertEqual(len(board.notes(self.root)), 2)

    def test_removing_section_or_file_is_observed(self):
        vision.save(self.root, 'Agreed.', how='conversation', words='Agreed.')
        self.path.write_text(ROADMAP)
        vision.observe(self.root)
        self.assertEqual(vision.history(self.root)[-1]['text'], '')
        vision.save(self.root, 'Restored.', how='conversation', words='Restore it.')
        self.path.unlink()
        vision.observe(self.root)
        self.assertEqual(vision.history(self.root)[-1]['before'], 'Restored.')
        self.assertEqual(len(board.notes(self.root)), 2)

    def test_migration_is_quiet_once_scoped_and_does_not_publish_a_draft(self):
        vision.install(self.root)
        [note] = board.notes(self.root)
        self.assertTrue(note['quiet'])
        self.assertIn('Starting draft', note['text'])
        self.assertIn('A tool for my plants.', note['text'])
        self.assertIn('handful of items', note['text'])
        self.assertEqual(self.path.read_text(), ROADMAP)
        board.append(self.root, 'notes.jsonl', dict(type='addressed', of=note['id'], at=board.now(), text='Discussed.'))
        (self.root / '.board' / 'vision-installed').unlink()
        vision.install(self.root)
        board.track(self.root)
        self.assertEqual(len(board.notes(self.root)), 1)
        shared = board.sharing(self.root, 'garden-review', {'provider': 'codex'})
        vision.install(shared)
        self.assertEqual(len(board.notes(shared)), 1)
        self.assertIn(str(shared / 'ROADMAP.md'), (self.root / 'AGENTS.md').read_text())
        vision.save(shared, 'A reviewer’s own horizon.', how='board')
        self.assertEqual(self.path.read_text(), ROADMAP)
        self.assertEqual(vision.history(self.root)[-1]['text'], '')
        self.assertEqual(vision.history(shared)[-1]['text'], 'A reviewer’s own horizon.')

    def test_observation_and_delivery_run_with_monitor_off_but_never_interrupt_work(self):
        vision.install(self.root)
        vision.save(self.root, 'Agreed.', how='conversation', words='Agreed.')
        self.path.write_text(vision.replace(self.path.read_text(), 'Changed outside.'))
        watcher = monitor.Watcher(enabled=False)
        with patch.object(console, 'snapshot', return_value={'state': 'working'}), patch.object(console, 'type_into') as send, patch.object(monitor, 'ensure') as ensure:
            watcher.tick()
            send.assert_not_called()
            ensure.assert_not_called()
        with patch.object(console, 'snapshot', return_value={'state': 'idle'}), patch.object(console, 'type_into', return_value=True) as send:
            watcher.tick()
            watcher.tick()
            send.assert_called_once()
        self.assertEqual(len([n for n in board.notes(self.root) if not n.get('quiet')]), 1)

    def test_existing_vision_receives_quiet_mechanics_note_without_redrafting(self):
        vision.save(self.root, 'Already agreed.', how='conversation', words='Yes.')
        original = self.path.read_text()
        vision.install(self.root)
        vision.install(self.root)
        [note] = board.notes(self.root)
        self.assertTrue(note['quiet'])
        self.assertIn('stays as written', note['text'])
        self.assertNotIn('Starting draft', note['text'])
        self.assertEqual(self.path.read_text(), original)

    def test_joining_with_a_roadmap_starts_with_conversation_and_preserves_the_plan(self):
        joined = Path(self.tmp.name) / 'joined'
        joined.mkdir()
        (joined / 'ROADMAP.md').write_text(ROADMAP)
        board.track(joined)
        [note] = board.notes(joined)
        self.assertFalse(note.get('quiet', False))
        self.assertIn('read how this project already plans', note['text'])
        self.assertIn('do not publish a new roadmap path before we clearly agree', note['text'])
        self.assertIn('colony vision --file', note['text'])
        self.assertIn("project's own agent", note['text'])
        self.assertEqual((joined / 'ROADMAP.md').read_text(), ROADMAP)
        board.deliver(joined)
        board.append(joined, 'notes.jsonl', dict(type='addressed', of=note['id'], at=board.now(), text='Conversation begun.'))
        board.track(joined)
        vision.install(joined)
        self.assertEqual(len(board.notes(joined)), 1, 'no repeat onboarding or second migration note')
        self.assertEqual(board.open_notes(joined), [])

    def test_protocol_preserves_revision_prompt_and_scopes_local_details(self):
        self.assertIn("When the work shows the vision differently than it's written, propose a revision to the person.", board.PROTOCOL)
        for text in (board.PROTOCOL, vision.MIGRATION, monitor.ROLE):
            self.assertIn('brainstorming', text)
            self.assertIn('specifications', text)
        self.assertIn('move item-level detail from Vision', board.PROTOCOL)
        self.assertIn('## Vision', board.SKELETON)

    def test_http_multiline_save_noop_stale_recovery_and_immediate_wake(self):
        vision.save(self.root, 'First.\nSecond.', how='conversation', words='Yes.')
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), board.Handler)
        httpd.watcher = Mock()
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{httpd.server_address[1]}'
        def post(before, text):
            data = urllib.parse.urlencode(dict(p=0, before=before, text=text)).encode()
            return urllib.request.urlopen(urllib.request.Request(base + '/vision', data=data))
        try:
            with post('First.\r\nSecond.', 'New.\r\nMore.') as response:
                self.assertEqual(response.status, 200)
            httpd.watcher.mail.assert_called_once()
            self.assertEqual(board.roadmap(self.root)['vision'], 'New.\nMore.')
            self.assertIsNone(vision.save(self.root, 'New.\r\nMore.', how='board', before='New.\r\nMore.'))
            self.assertEqual(len(board.notes(self.root)), 1)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                post('First.\r\nSecond.', 'Keep my unsaved draft <here>.')
            self.assertEqual(caught.exception.code, 409)
            self.assertIn('Keep my unsaved draft &lt;here&gt;', caught.exception.read().decode())
            self.assertEqual(board.roadmap(self.root)['vision'], 'New.\nMore.')
            page = urllib.request.urlopen(base + '/?p=0&view=roadmap').read().decode()
            self.assertIn('Edit vision', page)
            self.assertIn('Vision history', page)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == '__main__':
    unittest.main()
