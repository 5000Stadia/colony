"""Shared vision: live text, scoped migration, and delivery without approval state."""
import concurrent.futures
import html
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from colony import board, cli, console, consult, lead, monitor, vision
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

    def test_file_changes_are_neutral_and_wait_for_engagement(self):
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
        send.assert_not_called()
        board.record_ask(self.root, 'ask', 'Which chapter should come next?', explicit=True)
        result = self.cli('notes', '--deliver')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Observed file changes (no author or agreement inferred)', result.stdout)
        self.assertNotIn('full approval', result.stdout)
        self.assertNotIn('The person left notes', result.stdout)
        self.assertEqual(len(board.asks(self.root)), 1)
        vision.observe(self.root)
        self.assertEqual(len(board.notes(self.root)), 1)

    def test_stamped_own_commit_is_not_echoed_but_external_edit_is(self):
        vision.save(self.root, 'First.', how='conversation', words='Yes.')
        self.commit('Agreed baseline')
        self.path.write_text(vision.replace(self.path.read_text(), 'My revision.'))
        # The watcher can see a change before the agent commits it.
        vision.observe(self.root)
        self.git('add', 'ROADMAP.md')
        self.git('commit', '-m', 'My revision', '--trailer', 'Colony-Agent: ' + self.root.name)
        result = self.cli('notes', '--deliver')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Observed file changes', result.stdout)
        self.assertEqual(board.open_notes(self.root), [])
        self.path.write_text(vision.replace(self.path.read_text(), 'Outside revision.'))
        self.commit('Unstamped outside edit')
        vision.observe(self.root)
        result = self.cli('notes', '--deliver')
        self.assertIn('Outside revision.', result.stdout)
        self.assertNotIn('Outside revision.', self.cli('notes', '--deliver').stdout)

    def test_idle_changes_make_one_complete_catch_up_and_keep_history(self):
        vision.save(self.root, 'First.', how='conversation', words='Yes.')
        for number in range(5):
            vision.save(self.root, f'Revision {number}.', how='board')
        original_history = vision.history(self.root)
        with patch.object(console, 'snapshot', return_value={'state': 'idle'}), patch.object(console, 'type_into') as send:
            monitor.Watcher(enabled=False).mail()
            send.assert_not_called()
        result = self.cli('notes', '--deliver')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('5 pending change(s)', result.stdout)
        self.assertEqual(result.stdout.count('Current Vision:'), 1)
        self.assertIn('Before:\nFirst.', result.stdout)
        self.assertIn('Current Vision:\nRevision 4.', result.stdout)
        self.assertEqual(vision.history(self.root), original_history)
        self.assertTrue(all(n['delivered_at'] for n in board.notes(self.root)))
        representative = board.notes(self.root)[-1]['id']
        self.assertEqual(self.cli('noted', representative, 'Applied the current direction.').returncode, 0)
        self.assertEqual(board.open_notes(self.root), [])

    def test_persons_board_edit_is_still_delivered_when_lead_commits_it(self):
        vision.save(self.root, 'First.', how='conversation', words='Yes.')
        self.commit('Baseline')
        vision.save(self.root, 'The person changed this.', how='board')
        self.git('add', 'ROADMAP.md')
        self.git('commit', '-m', 'Record board edit', '--trailer', 'Colony-Agent: ' + self.root.name)
        result = self.cli('notes', '--deliver')
        self.assertIn('The person saved the vision on the board.', result.stdout)
        self.assertIn('The person changed this.', result.stdout)

    def test_historical_own_stamp_does_not_claim_new_unstamped_repeated_edit(self):
        vision.save(self.root, 'First.', how='conversation', words='Yes.')
        self.commit('Baseline')
        self.path.write_text(vision.replace(self.path.read_text(), 'My revision.'))
        self.git('add', 'ROADMAP.md')
        self.git('commit', '-m', 'My revision', '--trailer', 'Colony-Agent: ' + self.root.name)
        vision.observe(self.root)
        self.assertEqual(board.notes(self.root), [])
        for text in ('First.', 'My revision.'):
            self.path.write_text(vision.replace(self.path.read_text(), text))
            vision.observe(self.root)
            self.assertIsNone(vision.history(self.root)[-1]['source'])
        result = self.cli('notes', '--deliver')
        self.assertIn('2 pending change(s)', result.stdout)
        self.assertIn('Current Vision:\nMy revision.', result.stdout)

    def test_failed_hook_output_does_not_consume_changes(self):
        vision.save(self.root, 'Retry this update.', how='board')
        args = SimpleNamespace(deliver=True, session=False, console=None)
        with patch.object(board, 'root_of', return_value=self.root), patch.object(cli, '_hook_input', return_value={}), patch('builtins.print', side_effect=BrokenPipeError):
            with self.assertRaises(BrokenPipeError):
                cli.cmd_notes(args)
        self.assertIsNone(board.notes(self.root)[0]['delivered_at'])
        result = self.cli('notes', '--deliver')
        self.assertIn('Retry this update.', result.stdout)
        self.assertIsNotNone(board.notes(self.root)[0]['delivered_at'])

    def test_conversation_source_does_not_echo_to_lead_but_reaches_helper(self):
        helper = board.sharing(self.root, 'garden-review', {'provider': 'codex'})
        before = {n['id'] for n in board.notes(helper)}
        lead_notes = board.notes(self.root)
        event = vision.save(self.root, 'The new shared horizon.', how='conversation', words='Yes.')
        self.assertEqual(event['source'], str(self.root))
        self.assertEqual(board.notes(self.root), lead_notes)
        changes = [n for n in board.notes(helper) if n['id'] not in before]
        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0]['quiet'])
        self.assertIn('The new shared horizon.', changes[0]['text'])

    def test_item_notes_wait_until_relevant_work_begins(self):
        note = board.add_note(self.root, {'item': 'R3'}, 'Needed when reminders begin.', quiet=True, author='person')
        self.assertNotIn(note['id'], [n['id'] for n in board.open_notes(self.root)])
        self.path.write_text(self.path.read_text().replace('[ ] R3', '[~] R3'))
        self.assertIn(note['id'], [n['id'] for n in board.open_notes(self.root)])

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
        self.assertTrue(all(n.get('quiet') for n in board.notes(shared)))
        self.assertIn(str(self.root / 'ROADMAP.md'), (self.root / 'AGENTS.md').read_text())
        self.assertFalse((shared / 'ROADMAP.md').exists(), 'helper reads the canonical plan')
        vision.save(shared, 'The shared project horizon.', how='board')
        self.assertIn('The shared project horizon.', self.path.read_text())
        self.assertEqual(vision.history(self.root)[-1]['text'], 'The shared project horizon.')
        self.assertEqual(vision.history(shared)[-1]['text'], 'The shared project horizon.')

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
            send.assert_not_called()
        self.assertTrue(all(n.get('quiet') for n in board.notes(self.root)))
        self.assertIn('Changed outside.', self.cli('notes', '--deliver').stdout)

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

    def test_http_multiline_save_noop_stale_recovery_and_quiet_update(self):
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
            self.assertTrue(board.notes(self.root)[0]['quiet'])
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
