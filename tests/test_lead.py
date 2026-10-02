"""Shared-plan ownership, Git engagement and candidate review in isolated projects."""
import json
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from colony import board, cli, console, continuation, lead, progress, providers, vision

PLAN = '''# Roadmap

## Vision
A usable garden.

## M1 — Useful version
- [x] R1 Foundation
- [ ] R2 Water log (after R1)
- [ ] R3 Reminders (after R1)

## M2 — Complete version
- [ ] R4 Sharing

## M9 — Later
- [ ] R9 Experiments
'''


class LeadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'COLONY_BOARD_HOME': str(self.base / 'home')})
        self.env.start(); self.addCleanup(self.env.stop)
        for key in ('COLONY_PROJECT', 'COLONY_CONSOLE'):
            if key in os.environ:
                old = os.environ.pop(key)
                self.addCleanup(os.environ.__setitem__, key, old)
        self.root = self.base / 'garden'; self.root.mkdir()
        self.helper = self.base / 'garden-codex'; self.helper.mkdir()
        self.other = self.base / 'garden-other'; self.other.mkdir()
        for root in (self.root, self.helper, self.other):
            (root / '.board').mkdir()
        board.save_registry(dict(roots=[], projects=[str(self.root), str(self.helper), str(self.other)], settings={'messaging': False}))
        lead.git(self.root, 'init', '-q', '-b', 'main')
        lead.git(self.root, 'config', 'user.name', 'Fixture')
        lead.git(self.root, 'config', 'user.email', 'fixture@users.noreply.github.com')
        (self.root / 'ROADMAP.md').write_text(PLAN)
        (self.root / 'file.txt').write_text('original\n')
        (self.root / '.gitignore').write_text('.board/\n.codex/\n.claude/\n')
        self.commit(self.root, 'Foundation')
        vision.observe(self.root)
        self.initial = lead.git(self.root, 'rev-parse', 'HEAD')

    def commit(self, root, text):
        lead.git(root, 'add', '--all')
        lead.git(root, 'commit', '-qm', text)
        return lead.git(root, 'rev-parse', 'HEAD')

    def pair(self):
        lead.pair(self.root, self.helper, actor=self.root)
        lead.tested(self.root, self.initial, 'fixture passed', actor=self.root)

    def helper_job(self, item='R2', member=None):
        member = member or self.helper
        lead.assign(self.root, item, member, actor=self.root)
        return lead.start_item(self.root, item, actor=member)

    def completed_candidate(self):
        self.pair()
        checkpoint = progress.define(self.root, 'M1', 'Water log works', 'R2 works', items=['R2'])
        progress.start(self.root, checkpoint['id'], source=self.root)
        path = self.root / 'ROADMAP.md'
        path.write_text(path.read_text().replace('[ ] R2', '[?] R2'))
        commit = self.commit(self.root, 'Built water log')
        lead.tested(self.root, commit, 'fixture passed')
        artifact = self.base / 'deliverable.txt'
        artifact.write_text('A completed water log')
        return progress.ready(self.root, str(artifact), commit, 'fixture passed')

    def late_change_survives(self, boundary):
        self.pair()
        if boundary == 'sync_item':
            lead.start_item(self.root, 'R2', actor=self.root)
        original = getattr(lead, boundary)
        def change_during_catch_up(*args, **kwargs):
            path = self.root / 'ROADMAP.md'
            path.write_text(path.read_text().replace('[ ] R3', '[x] R3'))
            lead.update(self.root, lambda g: g.update(completed_items=['R1', 'R3']))
            return original(*args, **kwargs)
        with patch.object(lead, boundary, side_effect=change_during_catch_up):
            text, token = lead.engage(self.root, mark=False, with_receipt=True)
        self.assertEqual(text, '', 'the late change was not in this output')
        lead.acknowledge_engagement(self.root, token)
        seat = lead.info(self.root)['engagements'][str(self.root)]
        self.assertNotIn('R3', seat['looked_items'])
        self.assertNotEqual(seat['looked_revision'], lead.revision(self.root))
        following, token = lead.engage(self.root, mark=False, with_receipt=True)
        self.assertIn('R3', following)
        self.assertIn('changed', following)
        lead.acknowledge_engagement(self.root, token)
        self.assertEqual(lead.engage(self.root), '')

    def test_change_arriving_during_item_sync_is_not_acknowledged_before_announcement(self):
        self.late_change_survives('sync_item')

    def test_change_arriving_during_member_catch_up_is_not_acknowledged_before_announcement(self):
        self.late_change_survives('catch_up_subjects')

    def test_single_agent_needs_no_pairing_or_sync_setup(self):
        self.assertIsNone(lead.group(self.root))
        self.assertEqual(lead.owner(self.root, 'R2'), self.root)
        with patch.object(providers, 'of', side_effect=AssertionError('secondary provider access')):
            self.assertEqual(lead.engage(self.root), '')
            self.assertEqual(board.progress_panel(self.root, 0), '')
        self.assertFalse(lead.location().exists())
        self.assertFalse((board.home() / 'items').exists())

    def test_one_plan_owner_inheritance_and_generation(self):
        self.pair()
        active = lead.start_item(self.root, 'R2', actor=self.root)
        lead.assign(self.root, 'R3', self.root, actor=self.root)
        (self.helper / 'ROADMAP.md').write_text('stale unrelated branch plan')
        self.assertEqual(board.roadmap(self.helper), board.roadmap(self.root))
        with self.assertRaisesRegex(ValueError, 'Only garden'):
            vision.save(self.helper, 'An unapproved helper vision', how='conversation', words='Yes')
        g = lead.switch(self.root, self.helper)
        self.assertEqual(g['lead'], str(self.root))
        lead.finish_handoff(self.root, g['generation'])
        self.assertEqual(lead.owner(self.root, 'R2'), self.helper)
        self.assertEqual(lead.owner(self.root, 'R3'), self.root)
        self.assertEqual(lead.info(self.root)['assignments']['R2']['owner'], str(self.helper))
        self.assertEqual(lead.info(self.root)['assignments']['R2']['workspace'], active['workspace'])
        with self.assertRaisesRegex(ValueError, 'lead changed'):
            lead.update(self.root, lambda g: g.update(lead=str(self.root)), generation=0)
        self.assertEqual(lead.plan_path(self.helper), self.root / 'ROADMAP.md')

    def test_engagement_commits_dirty_helper_and_uses_only_tested_revision(self):
        self.pair(); job = self.helper_job(); work = Path(job['workspace'])
        self.assertFalse((work / 'ROADMAP.md').exists())
        (work / 'new.txt').write_text('unfinished work preserved')
        (self.root / 'safe.txt').write_text('completed lead work')
        tested = self.commit(self.root, 'Completed safe work')
        lead.tested(self.root, tested, 'fixture passed')
        (self.root / 'untested.txt').write_text('unfinished lead work')
        untested = self.commit(self.root, 'Still in progress')
        with patch.dict(os.environ, COLONY_PROJECT=str(self.helper)):
            self.assertEqual(board.root_of(work), self.helper)
        note = lead.engage(self.helper)
        self.assertIn('Completed safe work', note)
        self.assertNotIn('Still in progress', note)
        self.assertTrue((work / 'new.txt').exists())
        self.assertTrue((work / 'safe.txt').exists())
        self.assertFalse((work / 'untested.txt').exists())
        self.assertIn('Checkpoint R2', lead.git(work, 'log', '--format=%s'))
        self.assertIn('Colony-Agent: ' + self.helper.name, lead.git(work, 'log', '--format=%B'))
        self.assertEqual(lead.engage(self.helper), '')
        self.assertNotEqual(tested, untested)

    def test_engagement_retry_keeps_item_catch_up_until_output_receipt(self):
        self.pair(); job = self.helper_job()
        (self.root / 'safe.txt').write_text('completed lead work')
        target = self.commit(self.root, 'Completed safe work')
        lead.tested(self.root, target, 'passed')
        first = lead.engage(self.helper, mark=False)
        self.assertIn('Completed safe work', first)
        self.assertEqual(lead.info(self.root)['assignments']['R2']['looked_at'], job['base'])
        second, token = lead.engage(self.helper, mark=False, with_receipt=True)
        self.assertIn('Completed safe work', second)
        lead.acknowledge_engagement(self.helper, token)
        self.assertEqual(lead.engage(self.helper), '')

    def test_checkpoint_command_does_not_notify_its_own_console(self):
        self.pair()
        c = progress.define(self.root, 'M1', 'Water log works', 'R2 works', items=['R2'], actor=self.root)
        before = board.notes(self.root)
        progress.start(self.root, c['id'], actor=self.root, source=self.root)
        self.assertEqual(board.notes(self.root), before)
        self.assertEqual(progress.current(self.root)['state'], 'active')
        # Starting from the board or an outside terminal still engages the lead.
        progress.define(self.root, 'M2', 'Sharing works', 'R4 works', items=['R4'], actor=self.root)
        lead.update(self.root, lambda g: g.update(active_checkpoint=None))
        progress.start(self.root, 'M2-R4', actor=self.root)
        self.assertEqual(len(board.notes(self.root)), len(before) + 1)

    def test_own_approval_keeps_history_without_echoing_note_or_plan(self):
        c = self.completed_candidate()
        lead.start_item(self.root, 'R2', actor=self.root)
        lead.engage(self.root)
        _, token = lead.engage(self.root, mark=False, with_receipt=True)
        before = lead.info(self.root)
        notes = board.notes(self.root)
        lead_revision = before['engagements'][str(self.root)]['looked_revision']
        progress.decide(self.root, c['candidate']['id'], 'approve', text='Yes, this works.', source=self.root)
        g = lead.info(self.root)
        self.assertEqual(board.notes(self.root), notes)
        self.assertEqual(g['checkpoints'][0]['decision'], 'Yes, this works.')
        self.assertEqual(g['checkpoints'][0]['state'], 'accepted')
        self.assertNotEqual(lead.revision(self.root), lead_revision)
        for value in (g['engagements'][str(self.root)], g['assignments']['R2']):
            self.assertEqual(value['looked_revision'], lead.revision(self.root))
            self.assertEqual(value['pending_look']['token'], token)
        self.assertEqual(g['engagements'][str(self.root)]['looked_at'], before['engagements'][str(self.root)]['looked_at'])
        lead.acknowledge_engagement(self.root, token)
        self.assertEqual(lead.engage(self.root), '')
        (self.helper / '.board' / 'settings.json').write_text(json.dumps(dict(workdir=str(self.root))))
        self.assertIn('canonical plan changed', lead.engage(self.helper))

    def test_outside_approval_is_a_colony_receipt_and_still_reaches_lead(self):
        c = self.completed_candidate()
        lead.engage(self.root)
        before = len(board.notes(self.root))
        progress.decide(self.root, c['candidate']['id'], 'approve', text='Approved on the board.')
        [note] = board.notes(self.root)[before:]
        self.assertEqual(note['author'], 'colony')
        self.assertIn('Approved on the board.', note['text'])
        self.assertIn('canonical plan changed', lead.engage(self.root))

    def test_own_approval_and_next_version_do_not_send_two_self_notices(self):
        c = self.completed_candidate()
        following = progress.define(self.root, 'M2', 'Sharing works', 'R4 works', items=['R4'])
        before = board.notes(self.root)
        progress.decide(self.root, c['candidate']['id'], 'approve', next_checkpoint=following['id'], source=self.root)
        self.assertEqual(board.notes(self.root), before)
        self.assertEqual(progress.current(self.root)['id'], following['id'])
        self.assertEqual(progress.current(self.root)['state'], 'active')

    def test_progress_cli_suppresses_only_the_matching_consoles_correction_receipt(self):
        c = self.completed_candidate()
        for seat, expected in ((console.session_name(self.root), 0), (console.session_name(self.helper), 1), ('', 1)):
            with self.subTest(seat=seat):
                before = len(board.notes(self.root))
                with (patch.object(board, 'root_of', return_value=self.root),
                      patch.dict(os.environ, COLONY_CONSOLE=seat),
                      patch.object(continuation, 'tick'), redirect_stdout(io.StringIO())):
                    self.assertEqual(cli.main(['progress', '--changes', c['candidate']['id'], '--text', 'Fix the wording.']), 0)
                self.assertEqual(len(board.notes(self.root)), before + expected)
                if expected:
                    self.assertEqual(board.notes(self.root)[-1]['author'], 'colony')
                self.assertEqual(progress.current(self.root)['decision'], 'Fix the wording.')
                progress.start(self.root, c['id'], source=self.root)
                c = progress.ready(self.root, c['candidate']['artifact'], c['candidate']['commit'], 'fixture passed')

    def test_unread_outside_plan_change_is_not_hidden_by_a_following_own_edit(self):
        self.pair()
        lead.start_item(self.root, 'R2', actor=self.root)
        lead.engage(self.root)
        path = self.root / 'ROADMAP.md'
        path.write_text(path.read_text().replace('A usable garden.', 'A garden with an outside change.'))
        before = lead.revision(self.root)
        path.write_text(path.read_text() + '\nMy own progress note.\n')
        expected = lead.revision(self.root)
        commit = lead.commit_plan(self.root, 'Record own progress', source=self.root,
                                  before_revision=before, expected_revision=expected)
        lead.tested(self.root, commit, 'fixture passed')
        first, old_token = lead.engage(self.root, mark=False, with_receipt=True)
        self.assertIn('canonical Vision/roadmap changed', first)
        lead.update(self.root, lambda g: g['assignments']['R2'].update(state='integrated'))
        lead.start_item(self.root, 'R3', actor=self.root)
        second, token = lead.engage(self.root, mark=False, with_receipt=True)
        self.assertIn('canonical Vision/roadmap changed', second)
        lead.acknowledge_engagement(self.root, old_token)
        self.assertNotEqual(lead.info(self.root)['engagements'][str(self.root)]['looked_revision'], expected)
        lead.acknowledge_engagement(self.root, token)
        self.assertEqual(lead.engage(self.root), '')

    def test_retired_pending_receipt_cannot_replace_a_new_member_catch_up(self):
        (self.helper / '.board' / 'settings.json').write_text(json.dumps(dict(workdir=str(self.root))))
        self.pair()
        self.helper_job()
        (self.root / 'first.txt').write_text('first outside change')
        first = self.commit(self.root, 'First outside change')
        lead.tested(self.root, first, 'fixture passed')
        _, retired = lead.engage(self.helper, mark=False, with_receipt=True)
        lead.update(self.root, lambda g: g['assignments']['R2'].update(state='integrated'))
        (self.root / 'second.txt').write_text('second outside change')
        latest = self.commit(self.root, 'Second outside change')
        lead.tested(self.root, latest, 'fixture passed')
        text, token = lead.engage(self.helper, mark=False, with_receipt=True)
        self.assertIn('First outside change', text)
        self.assertIn('Second outside change', text)
        lead.acknowledge_engagement(self.helper, retired)
        lead.acknowledge_engagement(self.helper)
        self.assertEqual(lead.info(self.root)['engagements'][str(self.helper)]['looked_at'], self.initial)
        lead.acknowledge_engagement(self.helper, token)
        g = lead.info(self.root)
        self.assertEqual(g['engagements'][str(self.helper)]['looked_at'], latest)
        self.assertEqual(g['assignments']['R2']['pending_look']['token'], retired)
        self.assertEqual(lead.engage(self.helper), '')
        self.helper_job('R3')
        self.assertEqual(lead.engage(self.helper), '')

    def test_git_catch_up_filters_own_sources_without_truncating_others(self):
        self.pair()
        before = self.initial
        lead.git(self.root, 'commit', '--allow-empty', '-m', 'My own checkpoint',
                 '--trailer', 'Colony-Agent: ' + self.helper.name)
        for number in range(14):
            lead.git(self.root, 'commit', '--allow-empty', '-m', f'Outside completed work {number}')
        target = lead.git(self.root, 'rev-parse', 'HEAD')
        subjects = lead.catch_up_subjects(self.root, before, target, self.helper)
        self.assertEqual(len(subjects), 14)
        self.assertNotIn('My own checkpoint', subjects)
        self.assertIn('Outside completed work 0', subjects)
        self.assertIn('Outside completed work 13', subjects)
        (self.helper / '.board' / 'settings.json').write_text(json.dumps(dict(workdir=str(self.root))))
        g = lead.switch(self.root, self.helper)
        lead.finish_handoff(self.root, g['generation'])
        lead.tested(self.helper, target, 'passed', actor=self.helper)
        note = lead.engage(self.helper)
        self.assertNotIn('My own checkpoint', note)
        self.assertIn('Outside completed work 0', note)
        self.assertIn('Outside completed work 13', note)

    def test_real_conflict_routes_hashes_to_recent_file_developer_and_aborts(self):
        self.pair(); job = self.helper_job(); work = Path(job['workspace'])
        (work / 'file.txt').write_text('helper change\n')
        (self.root / 'file.txt').write_text('lead change\n')
        target = self.commit(self.root, 'Lead R3 file change')
        lead.tested(self.root, target, 'fixture passed')
        lead.update(self.root, lambda g: g.update(integrations=[dict(item='R3', owner=str(self.root), files=['file.txt'], commit=target)]))
        with self.assertRaisesRegex(ValueError, 'Sync conflict'):
            lead.sync_item(self.helper, 'R2', actor=self.helper)
        value = lead.info(self.root)['assignments']['R2']; evidence = value['conflict']; entry = evidence['files'][0]
        self.assertEqual(entry['path'], 'file.txt')
        self.assertEqual(entry['recipient'], str(self.root))
        self.assertEqual(entry['item'], 'R3')
        self.assertTrue(entry['both_changed'])
        self.assertEqual(len({entry['base'], entry['ours'], entry['theirs']}), 3)
        self.assertEqual(lead.git(work, 'rev-parse', 'HEAD'), evidence['checkpoint'])
        self.assertFalse(lead.git(work, 'ls-files', '-u'))
        self.assertEqual((work / 'file.txt').read_text(), 'helper change\n')
        notices = len(board.notes(self.root))
        with self.assertRaises(ValueError):
            lead.sync_item(self.helper, 'R2', actor=self.helper)
        self.assertEqual(len(board.notes(self.root)), notices, 'repeated hooks do not repeat conflict mail')
        # The routed developer deliberately resolves the preserved versions.
        (work / 'file.txt').write_text('lead change\n')
        self.commit(work, 'Resolve R2 against tested target')
        lead.sync_item(self.helper, 'R2', actor=self.helper)
        self.assertNotIn('sync_error', lead.info(self.root)['assignments']['R2'])

    def test_unassigned_existing_helper_syncs_only_when_engaged(self):
        branch = self.base / 'helper-work'
        lead.git(self.root, 'worktree', 'add', '-b', 'helper-existing', str(branch))
        (self.helper / '.board' / 'settings.json').write_text(json.dumps(dict(workdir=str(branch))))
        self.pair()
        (self.root / 'completed.txt').write_text('completed')
        target = self.commit(self.root, 'Completed garden feature')
        lead.tested(self.root, target, 'passed')
        self.assertFalse((branch / 'completed.txt').exists(), 'dormant helper is untouched')
        (branch / 'unfinished.txt').write_text('preserve')
        note = lead.engage(self.helper, mark=False)
        self.assertIn('Completed garden feature', note)
        self.assertIn('No item is assigned', note)
        self.assertTrue((branch / 'completed.txt').exists())
        self.assertTrue((branch / 'unfinished.txt').exists())
        self.assertEqual(lead.info(self.root)['assignments'], {})
        second, token = lead.engage(self.helper, mark=False, with_receipt=True)
        self.assertIn('Completed garden feature', second)
        lead.acknowledge_engagement(self.helper, token)
        self.assertEqual(lead.engage(self.helper), '')

    def test_primary_catches_up_in_its_branch_and_preserves_dirty_work(self):
        self.pair()
        work = self.base / 'primary-work'
        lead.git(self.root, 'worktree', 'add', '-b', 'primary-existing', str(work))
        (self.root / '.board' / 'settings.json').write_text(json.dumps(dict(workdir=str(work))))
        (work / 'file.txt').write_text('unfinished primary work\n')
        (work / 'draft.txt').write_text('new unfinished work')
        (work / 'AGENTS.md').write_text('generated instructions')
        (self.root / 'helper-feature.txt').write_text('completed helper work')
        target = self.commit(self.root, 'Helper completed reminders')
        lead.tested(self.root, target, 'passed')
        (self.root / 'untested.txt').write_text('unfinished integration work')
        self.commit(self.root, 'Still untested')
        note = lead.engage(self.root, mark=False)
        self.assertIn('Helper completed reminders', note)
        self.assertNotIn('Still untested', note)
        self.assertNotIn('without a project goal', note)
        self.assertTrue((work / 'helper-feature.txt').exists())
        self.assertFalse((work / 'untested.txt').exists())
        self.assertEqual((work / 'file.txt').read_text(), 'unfinished primary work\n')
        self.assertTrue((work / 'draft.txt').exists())
        self.assertNotIn('AGENTS.md', lead.git(work, 'ls-files'))
        second, token = lead.engage(self.root, mark=False, with_receipt=True)
        self.assertIn('Helper completed reminders', second)
        lead.acknowledge_engagement(self.root, token)
        self.assertEqual(lead.engage(self.root), '')

    def test_outgoing_lead_receives_changes_already_landed_on_canonical_main(self):
        self.pair()
        g = lead.switch(self.root, self.helper)
        lead.finish_handoff(self.root, g['generation'])
        (self.root / 'new.txt').write_text('incoming lead completed work')
        target = self.commit(self.root, 'Incoming lead completed feature')
        lead.tested(self.helper, target, 'passed', actor=self.helper)
        note = lead.engage(self.root)
        self.assertIn('Incoming lead completed feature', note)
        self.assertEqual(lead.engage(self.root), '')

    def test_assignment_keeps_unread_catch_up_and_acknowledges_member_cursor(self):
        self.pair()
        (self.helper / '.board' / 'settings.json').write_text(json.dumps(dict(workdir=str(self.root))))
        (self.root / 'completed.txt').write_text('completed before assignment')
        path = self.root / 'ROADMAP.md'
        path.write_text(path.read_text().replace('[ ] R2', '[x] R2'))
        target = self.commit(self.root, 'Completed water log before engagement')
        lead.tested(self.root, target, 'passed')
        self.assertIn('Completed water log before engagement', lead.engage(self.helper, mark=False))
        (self.root / 'another.txt').write_text('another completed change')
        target = self.commit(self.root, 'Another completed change before assignment')
        lead.tested(self.root, target, 'passed')
        job = self.helper_job('R3')
        self.assertEqual(job['base'], target)
        self.assertEqual(job['looked_at'], self.initial)
        second, token = lead.engage(self.helper, mark=False, with_receipt=True)
        self.assertIn('Completed water log before engagement', second)
        self.assertEqual(lead.info(self.root)['engagements'][str(self.helper)]['looked_at'], self.initial)
        lead.acknowledge_engagement(self.helper, token)
        self.assertEqual(lead.info(self.root)['engagements'][str(self.helper)]['looked_at'], target)
        lead.pair(self.root, self.helper)
        self.assertEqual(lead.info(self.root)['engagements'][str(self.helper)]['looked_at'], target)
        self.assertEqual(lead.engage(self.helper), '')

    def test_checkpoint_excludes_already_staged_generated_files(self):
        self.pair(); job = self.helper_job(); work = Path(job['workspace'])
        (work / 'real.txt').write_text('work to preserve')
        for name in ('AGENTS.md', 'CLAUDE.md', '.board/cache', '.claude/settings.json', '.codex/config.toml'):
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('generated state')
            lead.git(work, 'add', '-f', '--', name)
        lead.checkpoint(work, 'Checkpoint only work', self.helper)
        names = lead.git(work, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines()
        self.assertEqual(names, ['real.txt'])
        staged = lead.git(work, 'diff', '--cached', '--name-only').splitlines()
        self.assertEqual(set(staged), {'AGENTS.md', 'CLAUDE.md', '.board/cache', '.claude/settings.json', '.codex/config.toml'})

    def test_unassigned_engagement_keeps_an_existing_merge_untouched(self):
        self.pair()
        work = self.base / 'existing-merge'
        lead.git(self.root, 'worktree', 'add', '-b', 'existing-merge', str(work))
        (self.helper / '.board' / 'settings.json').write_text(json.dumps(dict(workdir=str(work))))
        (work / 'file.txt').write_text('my change\n')
        self.commit(work, 'My change')
        (self.root / 'file.txt').write_text('other change\n')
        target = self.commit(self.root, 'Other change')
        lead.tested(self.root, target, 'passed')
        lead.git(work, 'merge', '--no-edit', target, check=False)
        stages = lead.git(work, 'ls-files', '-u')
        self.assertTrue(stages)
        self.assertIn('merge already in progress', lead.engage(self.helper))
        self.assertEqual(lead.git(work, 'ls-files', '-u'), stages)

    def test_unrelated_worktree_is_not_a_logical_agent_scope(self):
        self.pair(); work = self.base / 'unrelated'
        lead.git(self.root, 'worktree', 'add', '-b', 'unrelated', str(work))
        with patch.dict(os.environ, COLONY_PROJECT=str(self.helper)):
            self.assertNotEqual(board.root_of(work), self.helper)

    def test_delete_modify_conflict_records_absent_version(self):
        self.pair(); job = self.helper_job(); work = Path(job['workspace'])
        (work / 'file.txt').unlink()
        (self.root / 'file.txt').write_text('modified main file\n')
        target = self.commit(self.root, 'Modify R3 file')
        lead.tested(self.root, target, 'passed')
        with self.assertRaisesRegex(ValueError, 'Sync conflict'):
            lead.sync_item(self.helper, 'R2')
        file = lead.info(self.root)['assignments']['R2']['conflict']['files'][0]
        self.assertIsNone(file['ours'])
        self.assertTrue(file['base'] and file['theirs'] and file['both_changed'])

    def test_a_gate_quietly_reaches_only_the_member_at_work_on_its_item(self):
        self.pair()
        lead.pair(self.root, self.other, actor=self.root)
        self.helper_job('R2')
        members = (self.root, self.helper, self.other)
        before = {m: len(board.notes(m)) for m in members}
        def gate(root, *args):
            with patch.object(board, 'root_of', return_value=root), patch.object(continuation, 'tick'), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['gate', *args]), 0)
        gate(self.root, 'Keep the log format?', '--item', 'R2', '--why', 'R2 stores it')
        gate(self.root, 'Email or SMS?', '--item', 'R3', '--why', 'Nobody works on R3 yet')
        gate(self.root, 'Rename the project?', '--why', 'No item')
        gate(self.helper, 'Index the log?', '--item', 'R2', '--why', 'Its own item')
        [note] = board.notes(self.helper)[before[self.helper]:]
        self.assertEqual((note['author'], note.get('quiet')), ('colony', True))
        self.assertIn('Keep the log format?', note['text'])
        self.assertEqual([len(board.notes(m)) for m in (self.root, self.other)], [before[self.root], before[self.other]])

    def test_single_agent_checkpoint_does_not_set_up_integration_locks(self):
        c = progress.define(self.root, 'M1', 'Usable garden', 'Works', items=['R2'])
        progress.start(self.root, c['id'])
        self.assertNotIn('helper', progress.objective(self.root, self.root).lower())
        self.assertNotIn('helper', lead.role_text(self.root).lower())
        with lead.operation_lock(self.root, 'integration'):
            self.assertFalse((board.home() / 'locks').exists())

    def test_failed_delivery_never_pushes(self):
        self.pair(); job = self.helper_job(); work = Path(job['workspace'])
        (work / 'feature.txt').write_text('ready')
        self.commit(work, 'Complete R2')
        lead.handback(self.helper, 'R2')
        real_git, pushed = lead.git, []
        def git(root, *args, **kwargs):
            if args and args[0] == 'push':
                pushed.append(args)
            return real_git(root, *args, **kwargs)
        with patch.object(lead, 'git', side_effect=git):
            with self.assertRaisesRegex(ValueError, 'Delivery failed'):
                lead.integrate(self.helper, 'R2', 'true', actor=self.helper, deploy='false', push=True)
        self.assertFalse(pushed)
        self.assertEqual(lead.info(self.root)['assignments']['R2']['state'], 'deploying')

    def test_failed_checks_do_not_advance_target_and_completer_can_retry(self):
        self.pair(); job = self.helper_job(); work = Path(job['workspace'])
        (work / 'feature.txt').write_text('ready')
        self.commit(work, 'Complete R2')
        lead.handback(self.helper, 'R2', actor=self.helper)
        with self.assertRaisesRegex(ValueError, 'completing'):
            lead.integrate(self.root, 'R2', 'true', actor=self.root)
        with self.assertRaisesRegex(ValueError, 'checks failed'):
            lead.integrate(self.helper, 'R2', 'false', actor=self.helper)
        self.assertEqual(lead.info(self.root)['integrated'], self.initial)
        self.assertEqual(lead.info(self.root)['assignments']['R2']['state'], 'testing')
        value = lead.integrate(self.helper, 'R2', 'true', actor=self.helper)
        self.assertEqual(value['state'], 'integrated')
        self.assertEqual(lead.info(self.root)['integrated'], lead.git(self.root, 'rev-parse', 'HEAD'))
        self.assertEqual((self.root / 'feature.txt').read_text(), 'ready')
        with patch.dict(os.environ, COLONY_PROJECT=str(self.helper)):
            self.assertEqual(board.root_of(work), self.helper, 'Stop hook keeps its identity after delivery')
        reopened = lead.start_item(self.helper, 'R2', actor=self.helper)
        self.assertEqual(reopened['workspace'], str(work), 'corrections reuse only their own item workspace')
        self.assertEqual(reopened['base'], lead.info(self.root)['integrated'])

    def test_parallel_finishers_wait_sync_and_test_in_order(self):
        self.pair(); lead.pair(self.root, self.other)
        for iid, who in (('R2', self.helper), ('R3', self.other)):
            job = self.helper_job(iid, who); work = Path(job['workspace'])
            (work / (iid + '.txt')).write_text(iid)
            self.commit(work, 'Complete ' + iid)
            lead.handback(who, iid, actor=who)
        entered, release = threading.Event(), threading.Event()
        real_run, calls, results = subprocess.run, [], []
        def run(args, **kwargs):
            if args == ['fixture-check']:
                calls.append(lead.git(self.root, 'rev-parse', 'HEAD'))
                if len(calls) == 1:
                    entered.set(); release.wait(3)
                return subprocess.CompletedProcess(args, 0, '', '')
            return real_run(args, **kwargs)
        def finish(who, iid):
            try: results.append(lead.integrate(who, iid, 'fixture-check', actor=who))
            except Exception as e: results.append(e)
        with patch.object(subprocess, 'run', side_effect=run):
            one = threading.Thread(target=finish, args=(self.helper, 'R2')); one.start()
            self.assertTrue(entered.wait(3))
            two = threading.Thread(target=finish, args=(self.other, 'R3')); two.start()
            self.assertEqual(len(calls), 1)
            release.set(); one.join(4); two.join(4)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(isinstance(v, dict) for v in results), results)
        self.assertEqual(len(calls), 2)
        self.assertTrue((self.root / 'R2.txt').exists() and (self.root / 'R3.txt').exists())
        self.assertEqual(lead.info(self.root)['assignments']['R3']['base'], calls[0])

    def test_checkpoint_bundles_review_and_dismissal_never_approves(self):
        self.pair()
        c = progress.define(self.root, 'M1', 'Useful garden', 'Both functions work', 'Open the document')
        progress.start(self.root, c['id'])
        path = self.root / 'ROADMAP.md'
        path.write_text(PLAN.replace('- [ ] R2', '- [?] R2').replace('- [ ] R3', '- [?] R3'))
        self.commit(self.root, 'Built version')
        commit = lead.git(self.root, 'rev-parse', 'HEAD'); lead.tested(self.root, commit, 'all passed')
        artifact = self.root / 'deliverable.txt'; artifact.write_text('A coherent document')
        idle = dict(state='idle', lines=[])
        with patch.object(board.console, 'snapshot', return_value=idle):
            self.assertEqual(board.waiting_items(self.root), [])
            c = progress.ready(self.root, str(artifact), commit, 'all passed')
            [waiting] = board.waiting_items(self.root)
            self.assertEqual(waiting['kind'], 'checkpoint')
            self.assertEqual(board.waiting_items(self.helper), [])
            board.clear_waiting(self.root, waiting['key'])
            self.assertEqual(progress.current(self.root)['state'], 'review')
            self.assertIn('human review', progress.hold(self.root, self.root))
            self.assertEqual(board.waiting_items(self.root), [])
        with self.assertRaisesRegex(ValueError, 'no longer current'):
            progress.decide(self.root, 'stale-candidate', 'approve')
        artifact.write_text('Changed after review')
        with self.assertRaisesRegex(ValueError, 'changed since'):
            progress.decide(self.root, c['candidate']['id'], 'approve')
        c = progress.ready(self.root, str(artifact), commit, 'all passed')
        progress.decide(self.root, c['candidate']['id'], 'approve')
        self.assertIsNone(progress.current(self.root))
        self.assertEqual(board.items(board.roadmap(self.root))['R2']['state'], 'done')
        self.assertEqual(lead.info(self.root)['checkpoints'][0]['state'], 'accepted')

    def test_later_and_completed_items_cannot_be_released(self):
        with self.assertRaises(ValueError):
            progress.define(self.root, 'M9', 'Experiments', 'Do experiments')
        with self.assertRaises(ValueError):
            progress.define(self.root, 'M1', 'Again', 'Redo', items=['R1'])

    def test_browser_duplicate_folder_asks_role_before_mutation(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), board.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            request = urllib.request.Request(url + '/add', data=urllib.parse.urlencode(dict(path=str(self.root), provider='codex')).encode())
            with urllib.request.urlopen(request) as response:
                page = response.read().decode()
            self.assertIn('Add helper', page)
            self.assertIn('Switch lead', page)
            self.assertIsNone(lead.group(self.root))
            self.assertEqual(board.project_settings(self.root)[0]['provider'], 'claude')
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_candidate_can_be_read_and_decided_from_the_browser(self):
        self.pair()
        c = progress.define(self.root, 'M1', 'Useful garden', 'Works', items=['R2'])
        progress.start(self.root, c['id'])
        path = self.root / 'ROADMAP.md'; path.write_text(PLAN.replace('- [ ] R2', '- [?] R2'))
        commit = self.commit(self.root, 'Completed R2'); lead.tested(self.root, commit, 'all passed')
        artifact = self.root / 'deliverable.txt'; artifact.write_text('Completed garden document')
        c = progress.ready(self.root, str(artifact), commit, 'all passed')
        candidate = c['candidate']['id']
        server = ThreadingHTTPServer(('127.0.0.1', 0), board.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            with urllib.request.urlopen(url + '/progress/artifact?p=0&candidate=' + candidate) as response:
                self.assertEqual(response.read().decode(), 'Completed garden document')
            request = urllib.request.Request(url + '/progress/decision', data=urllib.parse.urlencode(dict(p=0, candidate='old', action='approve')).encode())
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                urllib.request.urlopen(request)
            self.assertEqual(rejected.exception.code, 409)
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args): return None
            request = urllib.request.Request(url + '/progress/decision', data=urllib.parse.urlencode(dict(p=0, candidate=candidate, action='approve')).encode())
            with self.assertRaises(urllib.error.HTTPError) as redirect:
                urllib.request.build_opener(NoRedirect).open(request)
            self.assertEqual(redirect.exception.code, 303)
            self.assertIsNone(progress.current(self.root))
            self.assertEqual(board.items(board.roadmap(self.root))['R2']['state'], 'done')
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
