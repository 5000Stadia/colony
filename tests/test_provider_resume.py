"""Provider switches keep their own bookmarks and never resume a foreign conversation."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from colony import board, console, context, providers, selection


class ProviderResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'
        (self.root / '.board').mkdir(parents=True)
        env = patch.dict(os.environ, COLONY_BOARD_HOME=str(Path(self.tmp.name) / 'home'),
                         COLONY_PROJECT=str(self.root), COLONY_CONSOLE=console.session_name(self.root))
        env.start()
        self.addCleanup(env.stop)
        board.save_registry({'projects': [], 'settings': {'provider': 'claude', 'messaging': False}})
        self.claude = self.root / 'old.jsonl'
        self.claude.write_text(json.dumps({'type': 'user', 'message': {'content': 'Original work'}}) + '\n')
        self.codex = self.root / 'new.jsonl'
        self.codex.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'codex-id'}}) + '\n')

    def choose(self, family):
        reg = board.registry()
        reg['settings']['provider'] = family
        board.save_registry(reg)

    def legacy(self, provider=None):
        row = dict(conversation='claude-id', transcript=str(self.claude))
        if provider:
            row['provider'] = provider
        board.save_json(console._conversations(), {str(self.root): row})

    def test_codex_launch_omits_the_legacy_claude_resume_id(self):
        self.legacy()
        self.choose('codex')
        with patch.object(console, 'COMMAND', None), patch.object(selection, 'migrate'), \
                patch.object(selection, 'main', return_value={'model': 'gpt-6-luna', 'effort': 'low'}), \
                patch.object(providers.get('codex'), 'command', return_value='codex') as launched:
            self.assertEqual(console.command('project', self.root), 'codex')
        self.assertNotIn('resume', launched.call_args.kwargs)
        self.assertIsNone(console.last_conversation(self.root))

    def test_switching_back_restores_each_providers_original_conversation(self):
        original = self.claude.read_bytes()
        self.legacy()
        self.choose('codex')
        console.remember(self.root, 'codex-id', str(self.codex))
        self.assertEqual(console.last_conversation(self.root), 'codex-id')
        board.save_json(context.file(self.root, 'context-session.json'),
                        dict(provider='codex', id='codex-id', path=str(self.codex)))
        self.choose('claude')
        self.assertEqual(console.last_conversation(self.root), 'claude-id')
        self.assertEqual(context.session(self.root),
                         dict(provider='claude', id='claude-id', path=str(self.claude)))
        self.choose('codex')
        self.assertEqual(console.last_conversation(self.root), 'codex-id')
        self.assertEqual(self.claude.read_bytes(), original)

    def test_mislabeled_context_is_read_as_claude_and_cannot_drive_codex(self):
        self.choose('codex')
        self.legacy(provider='codex')
        board.save_json(context.file(self.root, 'context-session.json'),
                        dict(provider='codex', id='claude-id', path=str(self.claude)))
        board.save_json(context.file(self.root),
                        dict(job={'session': 'claude-id', 'phase': 'requested'}))
        self.assertIsNone(console.last_conversation(self.root))
        self.assertEqual(context.session(self.root)['provider'], 'claude')
        self.assertFalse(context.refreshing(self.root))
        with patch.object(context, 'transcript') as inspected, \
                patch.object(console, 'type_into') as typed:
            context.tick(self.root)
        inspected.assert_not_called()
        typed.assert_not_called()
        self.assertFalse(context.register(self.root, 'codex', dict(
            session_id='claude-id', transcript_path=str(self.claude), hook_event_name='SessionStart')))

    def test_generic_legacy_bookmarks_keep_same_provider_compatibility(self):
        path = self.root / 'empty.jsonl'
        path.write_text('{}\n')
        console.remember(self.root, 'legacy-generic', str(path))
        self.assertEqual(console.last_conversation(self.root), 'legacy-generic')
        self.choose('codex')
        self.assertIsNone(console.last_conversation(self.root))
        console.remember(self.root, 'codex-id', str(self.codex))
        self.choose('claude')
        self.assertEqual(console.last_conversation(self.root), 'legacy-generic')

    def test_remember_preserves_an_unrelated_project_bookmark(self):
        other = str(Path(self.tmp.name) / 'other-project')
        sentinel = dict(conversation='unrelated', transcript='/does/not/exist')
        board.save_json(console._conversations(), {other: sentinel})
        console.remember(self.root, 'claude-id', str(self.claude))
        rows = json.loads(console._conversations().read_text())
        self.assertEqual(rows[other], sentinel)

    def test_existing_codex_conversation_still_resumes(self):
        self.choose('codex')
        console.remember(self.root, 'codex-id', str(self.codex))
        self.assertEqual(console.last_conversation(self.root), 'codex-id')
        with patch.object(console, 'COMMAND', None), patch.object(selection, 'migrate'), \
                patch.object(selection, 'main', return_value={'model': 'gpt-6-luna', 'effort': 'low'}), \
                patch.object(providers.get('codex'), 'command', return_value='codex') as launched:
            console.command('project', self.root)
        self.assertEqual(launched.call_args.kwargs['resume'], 'codex-id')

    def test_a_switch_is_seen_from_how_the_console_was_started(self):
        name = console.session_name(self.root)
        console._started(name, 'fp', 'codex')
        with patch.object(console, 'running', return_value=True):
            self.assertEqual(console.switching(self.root), ('claude', 'codex'), "set to Claude, still running Codex")
            self.choose('codex')
            self.assertIsNone(console.switching(self.root))

    def test_a_requested_switch_waits_for_nothing_but_a_safe_moment(self):
        from colony import monitor
        m = monitor.Watcher(enabled=False)
        name = console.session_name(self.root)
        busy = {'why': 'a command it started is still running'}
        with patch.object(board, 'projects', return_value=[self.root]), \
                patch.object(monitor, 'name', return_value='steward-seat'), \
                patch.object(console, 'running', side_effect=lambda n: n == name), \
                patch.object(console, 'switching', side_effect=lambda r, n, l: ('claude', 'codex') if n == name else None), \
                patch.object(console, 'snapshot', return_value={'state': 'idle'}), \
                patch.object(console, 'drafting', return_value=False), patch.object(console, 'attached', return_value=False), \
                patch.object(console, 'busy', side_effect=lambda *a: busy['why']), \
                patch.object(console, 'reload') as reload:
            m.current()
            reload.assert_not_called()                  # idle on screen, but a background command still runs
            busy['why'] = None
            m.current_checked = 0
            m.current()                                 # no five minutes idle first: the person asked for it
            reload.assert_called_once()
        self.assertIn('switching from codex to claude', (board.home() / 'reloads.jsonl').read_text())

    def test_an_update_still_waits_a_few_idle_minutes(self):
        from colony import monitor
        m = monitor.Watcher(enabled=False)
        name = console.session_name(self.root)
        with patch.object(board, 'projects', return_value=[self.root]), \
                patch.object(monitor, 'name', return_value='steward-seat'), \
                patch.object(console, 'running', side_effect=lambda n: n == name), \
                patch.object(console, 'switching', return_value=None), \
                patch.object(console, 'snapshot', return_value={'state': 'idle'}), \
                patch.object(console, 'drafting', return_value=False), patch.object(console, 'attached', return_value=False), \
                patch.object(console, 'stale', return_value='Claude Code 1 → 2'), patch.object(console, 'busy', return_value=None), \
                patch.object(console, 'reload') as reload:
            m.current_checked = 0
            m.current()
            reload.assert_not_called()
            m.idle_since[name] -= monitor.RELOAD_AFTER
            m.current_checked = 0
            m.current()
            reload.assert_called_once()

    def test_claude_counts_helpers_its_footer_shows_as_still_at_work(self):
        screen = "done\n────────\n❯ \n────────\n  Opus · weekly 0%\n  ⏵⏵ bypass permissions on · 2 agents\n"
        claude = providers.get('claude')
        with patch.object(console, 'screen', return_value=screen), \
                patch('subprocess.run', return_value=type('R', (), {'stdout': ''})()):
            self.assertEqual(claude.busy(self.root, 'x'), '2 agents still at work')
        with patch.object(console, 'screen', return_value=screen.replace(' · 2 agents', '')), \
                patch('subprocess.run', return_value=type('R', (), {'stdout': ''})()):
            self.assertIsNone(claude.busy(self.root, 'x'))

    def test_back_on_a_program_it_hears_what_the_other_one_did(self):
        import subprocess
        import time
        git = lambda *a: subprocess.run(['git', '-C', str(self.root), *a], capture_output=True, check=True)
        git('init', '-q')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'Keep each history')
        then = time.time() - 3600
        os.utime(self.claude, (then, then))
        board.save_json(console._conversations(), {str(self.root): {
            'conversation': 'codex-id', 'transcript': str(self.codex), 'provider': 'codex',
            'by_provider': {'claude': {'conversation': 'claude-id', 'transcript': str(self.claude), 'provider': 'claude'},
                            'codex': {'conversation': 'codex-id', 'transcript': str(self.codex), 'provider': 'codex'}}}})
        board.said(self.root, 'switch it back please')
        board.append(self.root, 'notes.jsonl', {'type': 'addressed', 'of': 'n123456', 'at': board.now(), 'text': 'pushed the fix'})
        with patch.object(board, 'workdir', return_value=self.root):
            note = console.catch_up(self.root)
            self.assertEqual(note['kind'], 'catch-up')
            for part in ('Back on Claude Code: Codex ran', 'Keep each history', '"switch it back please"', '[n123456] pushed the fix'):
                self.assertIn(part, note['text'])
            self.assertIsNone(console.catch_up(self.root), "once a switch, not at every restart")

    def test_codex_reads_what_claude_learned_and_keeps_the_persons_rules_in_colony(self):
        index = Path(self.tmp.name) / 'MEMORY.md'
        index.write_text('- [Fix bugs](fix.md) — at once\n')
        with patch.object(providers, 'claude_memory', return_value=index):
            providers.get('codex').wire(self.root, board.protocol(self.root))
        told = (self.root / 'AGENTS.md').read_text()
        self.assertIn(f"learned working here is in `{index}`", told)
        # one source for the person's orders (colony-codex's audit): never added to Claude's memory from Codex
        self.assertIn("standing rules are not kept there but by colony", told)
        self.assertNotIn("add a new one there", told)

    def test_colonys_own_instructions_reach_both_programs(self):
        here = Path(__file__).resolve().parent.parent
        part = lambda f: (here / f).read_text().split('## This project is part of a colony')[0]
        self.assertEqual(part('AGENTS.md'), part('CLAUDE.md'), "Codex works on colony by the same project rules as Claude")
        self.assertIn('tests.test_provider_resume', part('AGENTS.md'))


if __name__ == '__main__':
    unittest.main()
