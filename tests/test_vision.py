"""Shared vision: live text, scoped migration, and delivery without approval state."""
import concurrent.futures
import re
import html
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
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
        vision.save(self.root, value, how='conversation', words='Yes, that is the vision.', context='Agreed in conversation.')
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
        self.assertNotEqual(result.returncode, 0, 'the agent reading is required beside the words')
        self.assertIn('--context', result.stderr)
        result = self.cli('vision', '--file', str(draft), '--words', 'Yes, that describes it.',
                          '--context', 'We settled on a companion that stays gentle over a long horizon.')
        self.assertEqual(result.returncode, 0, result.stderr)
        event = vision.history(self.root)[-1]
        self.assertEqual(event['words'], 'Yes, that describes it.')
        self.assertEqual(event['context'], 'We settled on a companion that stays gentle over a long horizon.')
        self.assertIn('Reading: We settled', self.cli('vision', '--history').stdout)
        self.assertEqual(event['how'], 'conversation')
        self.assertTrue(event['at'].endswith('Z'))
        self.assertEqual(board.notes(self.root), [])
        vision.observe(self.root)
        self.assertEqual(board.notes(self.root), [])

    def test_file_changes_are_neutral_and_wait_for_engagement(self):
        vision.save(self.root, 'First.', how='conversation', words='First is agreed.', context='Agreed in conversation.')
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
        vision.save(self.root, 'First.', how='conversation', words='Yes.', context='Agreed in conversation.')
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
        vision.save(self.root, 'First.', how='conversation', words='Yes.', context='Agreed in conversation.')
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
        vision.save(self.root, 'First.', how='conversation', words='Yes.', context='Agreed in conversation.')
        self.commit('Baseline')
        vision.save(self.root, 'The person changed this.', how='board')
        self.git('add', 'ROADMAP.md')
        self.git('commit', '-m', 'Record board edit', '--trailer', 'Colony-Agent: ' + self.root.name)
        result = self.cli('notes', '--deliver')
        self.assertIn('The person saved the vision on the board.', result.stdout)
        self.assertIn('The person changed this.', result.stdout)

    def test_historical_own_stamp_does_not_claim_new_unstamped_repeated_edit(self):
        vision.save(self.root, 'First.', how='conversation', words='Yes.', context='Agreed in conversation.')
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
        event = vision.save(self.root, 'The new shared horizon.', how='conversation', words='Yes.', context='Agreed in conversation.')
        self.assertEqual(event['source'], str(self.root))
        self.assertEqual(board.notes(self.root), lead_notes)
        changes = [n for n in board.notes(helper) if n['id'] not in before]
        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0]['quiet'])
        self.assertIn('The new shared horizon.', changes[0]['text'])
        self.assertEqual(changes[0]['author'], 'colony')
        self.assertTrue(changes[0]['text'].startswith('The lead recorded a vision change agreed with the person'))

    def test_board_save_notice_is_colonys_and_the_change_stays_the_persons(self):
        vision.save(self.root, 'A garden that waters itself.', how='board')
        [note] = board.notes(self.root)
        self.assertEqual(note['author'], 'colony')
        self.assertIn('The person saved the vision on the board.', note['text'])
        self.assertIn('A garden that waters itself.', monitor.catch_up(self.root), "the monitor still sees the person's edit")
        result = self.cli('notes', '--deliver')
        self.assertIn('Colony, the harness the person set up and trusts', result.stdout)
        self.assertNotIn('The person left notes', result.stdout)

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
        vision.save(self.root, 'Agreed.', how='conversation', words='Agreed.', context='Agreed in conversation.')
        self.path.write_text(ROADMAP)
        vision.observe(self.root)
        self.assertEqual(vision.history(self.root)[-1]['text'], '')
        vision.save(self.root, 'Restored.', how='conversation', words='Restore it.', context='Agreed in conversation.')
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
        vision.save(self.root, 'Agreed.', how='conversation', words='Agreed.', context='Agreed in conversation.')
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
        vision.save(self.root, 'Already agreed.', how='conversation', words='Yes.', context='Agreed in conversation.')
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
        self.assertIn('do not publish a new roadmap path before you clearly agree', note['text'])
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
        self.assertIn("When the work shows the vision differently, propose a revision", " ".join(board.PROTOCOL.split()))
        for text in (board.PROTOCOL, vision.MIGRATION, monitor.ROLE):
            self.assertIn('brainstorming', text)
        for text in (vision.MIGRATION, monitor.ROLE):
            self.assertIn('specifications', text)
        self.assertIn('detail that matters to a few items lives in those items', ' '.join(board.PROTOCOL.split()))
        self.assertIn('## Vision', board.SKELETON)

    def test_http_multiline_save_noop_stale_recovery_and_quiet_update(self):
        vision.save(self.root, 'First.\nSecond.', how='conversation', words='Yes.', context='Agreed in conversation.')
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), board.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{httpd.server_address[1]}'
        def post(before, text):
            data = urllib.parse.urlencode(dict(p=0, before=before, text=text)).encode()
            return urllib.request.urlopen(urllib.request.Request(base + '/vision', data=data))
        try:
            with patch.object(console, 'type_into') as typed:
                with post('First.\r\nSecond.', 'New.\r\nMore.') as response:
                    self.assertEqual(response.status, 200)
            typed.assert_not_called()
            self.assertTrue(board.notes(self.root)[0]['quiet'])
            self.assertEqual(board.notes(self.root)[0]['author'], 'colony')
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


class NestTest(BoardBase):
    """Projects that share a purpose share a nest: each knows its nestmates and the shared vision; others see none."""
    def project(self, name):
        root = Path(self.tmp.name) / name
        root.mkdir()
        (root / "ROADMAP.md").write_text(ROADMAP)
        board.track(root)
        return root

    def test_nestmates_know_each_other_and_the_shared_vision_while_a_project_outside_sees_none(self):
        from colony import nest
        board.track(self.root)
        photonic, outside = self.project("photonic"), self.project("dnd")
        self.assertEqual(self.cli("nest", "holodeck").returncode, 0)                     # this project, by default
        self.assertIn("photonic is in the holodeck nest", self.cli("nest", "holodeck", "photonic").stdout)
        self.assertEqual(nest.nest_of(photonic), "holodeck")
        self.assertEqual([p.name for p in nest.nestmates(self.root)], ["photonic"])
        self.assertIsNone(nest.nest_of(outside))
        shared = Path(self.tmp.name) / "shared.md"
        shared.write_text("Walk into any story and live it.")
        r = self.cli("nest", "holodeck", "--vision", "--file", str(shared), "--words", "Yes, that's it.", "--context", "Agreed.")
        self.assertEqual(r.returncode, 0, r.stderr)
        for member in (self.root, photonic):                  # every member hears the change, before and after
            heard = [n["text"] for n in board.notes(member) if "shared vision" in n["text"] and "After:" in n["text"]]
            self.assertTrue(heard and "Walk into any story" in heard[-1])
        self.assertFalse([n for n in board.notes(outside) if "nest" in n["text"]])
        start = self.cli("notes", "--deliver", "--session").stdout
        self.assertIn("in the holodeck nest", start)
        self.assertIn("photonic", start)
        self.assertIn("Walk into any story", start)
        self.assertEqual(nest.nests()["holodeck"]["history"][-1]["words"], "Yes, that's it.")
        listing = self.cli("nest").stdout
        self.assertIn("holodeck: plants, photonic", listing)
        from colony import pins, showcase
        showcase.add(photonic, "A ballroom of people", "Forty generated guests.", link="https://example.com/ballroom")
        pins.add(self.root, "https://example.com/emitter", title="Emitter demo")
        showcase.add(outside, "Session zero map", "The campaign map.")
        page = board.nest_page(board.registry(), "holodeck")          # the nest's own page gathers its members' work
        for shown in ("Walk into any story", "A ballroom of people", "Emitter demo", ">photonic<", ">plants<"):
            self.assertIn(shown, page)
        self.assertNotIn("Session zero map", page)
        self.assertIn("No nest named", board.nest_page(board.registry(), "nowhere"))
        side = board.sidebar(board.registry(), 0)                     # the board groups its projects by nest
        self.assertIn("/nest?n=holodeck", side)
        self.assertLess(side.index("holodeck nest"), side.index(">photonic<"))
        self.assertLess(side.index(">photonic<"), side.index("other projects"))
        self.assertLess(side.index("other projects"), side.index(">dnd<"))

    def test_a_project_is_in_one_nest_and_keeps_it_when_closed_and_reopened(self):
        from colony import nest
        board.track(self.root)
        nest.join(self.root, "holodeck")
        nest.join(self.root, "family")
        self.assertEqual(nest.nest_of(self.root), "family")
        self.assertNotIn("holodeck", nest.nests())                     # an empty nest with no vision is gone
        board.remove_project(self.root)
        board.track(self.root)
        self.assertEqual(nest.nest_of(self.root), "family")
        self.assertIn("left the family nest", self.cli("nest", "--leave").stdout)
        self.assertIsNone(nest.nest_of(self.root))
        self.assertIn("No nests yet", self.cli("nest").stdout)
        self.assertNotEqual(self.cli("nest", "nowhere", "--vision", "--file", "x").returncode, 0)

    def test_the_steward_keeps_a_nests_standards_in_the_persons_words_and_a_newer_one_replaces_an_older(self):
        from colony import nest
        board.track(self.root)
        nest.join(self.root, "holodeck")
        self.assertEqual(self.cli("nest", "holodeck", "--standard", "no break-fixing", "--from", "holo-emitter").returncode, 0)
        self.cli("nest", "holodeck", "--standard", "generation needs no human sign-off", "--from", "holo-emitter")
        self.cli("nest", "holodeck", "--standard", "fixes go to the workflow, never one asset", "--replaces", "1")
        listing = self.cli("nest").stdout
        self.assertNotIn("no break-fixing", listing)
        self.assertIn('standard 2: "fixes go to the workflow, never one asset"', listing)
        self.assertIn("said in holo-emitter", listing)
        self.assertIn("generation needs no human sign-off", board.nest_page(board.registry(), "holodeck"))
        self.assertFalse([n for n in board.notes(self.root) if "sign-off" in n["text"]])   # held, not pushed into context
        told = self.cli("nest", "holodeck", "--tell", "2", "--to", "plants")
        self.assertIn("is told, once", told.stdout)
        again = self.cli("nest", "holodeck", "--tell", "2", "--to", "plants")              # never badgers
        self.assertIn("isn't told twice", again.stdout)
        heard = [n for n in board.notes(self.root) if "workflow, never one asset" in n["text"]]
        self.assertEqual(len(heard), 1)
        self.assertEqual(heard[0]["author"], "monitor")
        self.assertNotEqual(self.cli("nest", "holodeck", "--tell", "2", "--to", "nobody").returncode, 0)
        self.cli("nest", "holodeck", "--drop", "1")
        self.assertEqual([r["words"] for r in nest.nests()["holodeck"]["standards"]], ["fixes go to the workflow, never one asset"])
        self.assertNotEqual(self.cli("nest", "holodeck", "--drop", "5").returncode, 0)


class PageTest(BoardBase):
    def test_the_vision_starts_folded_to_its_first_sentence_and_flows_without_the_files_line_breaks(self):
        board.track(self.root)
        text = ("**holo-emitter: walk into any place, and it's there.** The north star is the\nholodeck, on a phone first.\n\n"
                "Three projects make it:\n- construct is the narrator, built on the\n  roots of storytelling\n- pattern-buffer holds the world")
        vision.save(self.root, text, how='conversation', words='Yes.', context='Agreed.')
        box = board.vision_box(self.root, 0)
        self.assertTrue(box.startswith("<details class='vision'><summary><b>Vision</b> · holo-emitter: walk into any place, and it&#x27;s there.</summary>"))
        self.assertIn("<p><strong>holo-emitter: walk into any place, and it&#x27;s there.</strong> The north star is the holodeck, on a phone first.</p>", box)
        self.assertIn("<li>construct is the narrator, built on the roots of storytelling</li><li>pattern-buffer holds the world</li>", box)

    def test_the_status_leads_a_projects_page_and_the_last_project_to_start_working_comes_first(self):
        board.track(self.root)
        other = Path(self.tmp.name) / "photonic"
        other.mkdir()
        (other / "ROADMAP.md").write_text(ROADMAP)
        board.track(other)
        page = board.render(board.registry(), 0)
        self.assertLess(page.index("class='status'"), page.index("class='vision'"))
        names = lambda: [n for n in re.findall(r"</span>([\w-]+)<span class='badge", board.sidebar(board.registry(), 0))]
        board.woke(other)
        self.assertEqual(names()[0], "photonic")
        board.woke(self.root)
        self.assertEqual(names()[0], "plants")
