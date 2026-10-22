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


if __name__ == '__main__':
    unittest.main()
