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
                patch.object(console, 'room', return_value=True), patch.object(console, 'reload') as reload:
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
                patch.object(console, 'room', return_value=True), patch.object(console, 'reload') as reload:
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


class ProviderHistoryTest(unittest.TestCase):
    """R127: native paths follow a move, while messages and unrelated database values do not."""
    def setUp(self):
        import sqlite3
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'before'
        (self.root / '.board').mkdir(parents=True)
        self.env = patch.dict(os.environ, COLONY_BOARD_HOME=str(self.base / 'board'),
                              CLAUDE_CONFIG_DIR=str(self.base / 'claude'), CODEX_HOME=str(self.base / 'personal'))
        self.env.start()
        self.addCleanup(self.env.stop)
        board.save_registry({'projects': [str(self.root)], 'roots': [], 'settings': {'provider': 'codex', 'messaging': False}})
        board.ident(self.root)
        from colony import codex_remote
        self.home = codex_remote.home_for(self.root)
        self.home.mkdir(parents=True)
        self.tid = '01930000-0000-7000-8000-000000000001'
        self.rollout = self.home / 'sessions' / '2026' / '10' / '31' / ('rollout-' + self.tid + '.jsonl')
        self.rollout.parent.mkdir(parents=True)
        cwd = str(self.root)
        rows = [dict(type='session_meta', payload=dict(id=self.tid, cwd=cwd, runtime_workspace_roots=[cwd])),
                dict(type='turn_context', payload=dict(cwd=cwd, sandbox_policy=dict(type='workspace-write', writable_roots=[cwd + '/src']))),
                dict(type='world_state', payload=dict(state=dict(environments=dict(local=dict(cwd=cwd))))),
                dict(type='event_msg', payload=dict(type='thread_settings_applied', thread_settings=dict(cwd=cwd, runtime_workspace_roots=[cwd]))),
                dict(type='response_item', payload=dict(type='message', role='user', content=[dict(text=cwd + ' café')]))]
        lines = [(json.dumps(row, ensure_ascii=False) + '\n').encode() for row in rows]
        self.rollout.write_bytes(b''.join(lines))
        self.message = lines[-1]
        self.turn_offset = sum(map(len, lines[:-1]))
        self.db = sqlite3.connect(self.home / 'state_5.sqlite')
        self.db.executescript('''CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT NOT NULL, rollout_path TEXT NOT NULL,
            sandbox_policy TEXT NOT NULL, title TEXT, updated_at INTEGER, archived INTEGER, agent_path TEXT);
            CREATE TABLE project_roots (project_id TEXT, position INTEGER, path TEXT, PRIMARY KEY(project_id,position));
            CREATE TABLE rollout_migration_skipped_rollouts (migration_id TEXT, rollout_path TEXT, skip_reason TEXT,
                PRIMARY KEY(migration_id,rollout_path));
            CREATE TABLE unrelated (value TEXT);''')
        self.db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)',
                        (self.tid, cwd, str(self.rollout), json.dumps(dict(type='workspace-write', writable_roots=[cwd + '/src'])), cwd, 73, 0, 'root/agent'))
        self.db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)',
                        ('01930000-0000-7000-8000-000000000002', cwd + '-other', '/elsewhere/rollout.jsonl', '{}', 'unrelated', 91, 1, None))
        self.db.execute('INSERT INTO project_roots VALUES (?,?,?)', ('project', 0, cwd))
        self.db.execute('INSERT INTO rollout_migration_skipped_rollouts VALUES (?,?,?)', ('migration', str(self.rollout), cwd))
        self.db.execute('INSERT INTO unrelated VALUES (?)', (cwd,))
        self.db.commit()
        self.before = list(self.db.iterdump())
        self.db.close()
        with sqlite3.connect(self.home / 'thread_history_1.sqlite') as db:
            db.executescript('''CREATE TABLE thread_turns (thread_id TEXT, rollout_byte_offset INTEGER, rollout_end_byte_offset INTEGER);
                CREATE TABLE thread_history_projection_state (thread_id TEXT, next_rollout_byte_offset INTEGER);''')
            db.execute('INSERT INTO thread_turns VALUES (?,?,?)', (self.tid, self.turn_offset, sum(map(len, lines))))
            db.execute('INSERT INTO thread_history_projection_state VALUES (?,?)', (self.tid, sum(map(len, lines))))
        db.close()
        (self.home / 'config.toml').write_text('sqlite_home=' + json.dumps(str(self.home)) + '\n')
        (self.home / 'colony-thread.json').write_text(json.dumps(dict(thread=self.tid, cwd=cwd)))
        console.remember(self.root, self.tid, str(self.rollout))
        self.provider = providers.get('codex')

    def move(self):
        from colony import repoint, codex_remote
        old = self.root
        self.root = self.base / 'a-longer-new-project'
        old.rename(self.root)
        said = repoint.repoint(old, self.root)
        self.home = codex_remote.home_for(self.root)
        return old, said

    def test_native_paths_move_without_changing_messages_or_other_columns(self):
        import sqlite3
        from contextlib import closing
        from colony import codex_remote, repoint
        old, said = self.move()
        self.assertTrue(self.provider.verify_history(self.root))
        self.assertTrue(any('Codex' in line for line in said))
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db:
            rows = list(db.execute('SELECT * FROM threads ORDER BY id'))
            self.assertEqual(rows[0][1], str(self.root))
            rollout = Path(rows[0][2])
            self.assertTrue(rollout.is_file())
            self.assertEqual(json.loads(rows[0][3])['writable_roots'], [str(self.root / 'src')])
            self.assertEqual(rows[0][4:], (str(old), 73, 0, 'root/agent'))
            self.assertEqual(rows[1][1:], (str(old) + '-other', '/elsewhere/rollout.jsonl', '{}', 'unrelated', 91, 1, None))
            self.assertEqual(db.execute('SELECT value FROM unrelated').fetchone()[0], str(old))
            self.assertEqual(db.execute('SELECT path FROM project_roots').fetchone()[0], str(self.root))
            self.assertEqual(db.execute('SELECT rollout_path,skip_reason FROM rollout_migration_skipped_rollouts').fetchone(),
                             (str(rollout), str(old)))
        lines = rollout.read_bytes().splitlines(keepends=True)
        self.assertEqual(lines[-1], self.message)
        self.assertNotIn(str(old).encode(), b''.join(lines[:-1]))
        with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite')) as db:
            start, end = db.execute('SELECT rollout_byte_offset,rollout_end_byte_offset FROM thread_turns').fetchone()
            self.assertEqual(rollout.read_bytes()[start:end], self.message)
            self.assertEqual(db.execute('SELECT next_rollout_byte_offset FROM thread_history_projection_state').fetchone()[0], end)
        self.assertEqual(codex_remote.user_config(self.home)['sqlite_home'], str(self.home))
        self.assertEqual(console.saved_conversation(self.root)['transcript'], str(rollout))
        before = {str(p): p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(repoint.repoint(old, self.root), [])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.home.rglob('*') if p.is_file()})

    def test_gather_keeps_history_linked_through_a_move(self):
        dest = self.base / 'histories' / 'codex'
        auth = self.base / 'auth.json'
        auth.write_text('dummy sign-in')
        (self.home / 'auth.json').symlink_to(auth)
        self.provider.gather(self.root, dest)
        self.provider.gather(self.root, dest)
        self.assertTrue((self.home / 'sessions').is_symlink())
        self.assertTrue((self.home / 'state_5.sqlite').is_symlink())
        self.assertTrue((self.home / 'thread_history_1.sqlite').is_symlink())
        self.assertFalse((dest / 'auth.json').exists())
        self.move()
        self.assertTrue(self.provider.verify_history(self.root))
        self.assertTrue((self.home / 'state_5.sqlite').is_symlink())

    def test_live_host_and_native_writer_refuse_before_any_history_changes(self):
        import fcntl
        from colony import codex_remote
        before = self.rollout.read_bytes()
        with patch.object(codex_remote, 'alive', return_value=True):
            with self.assertRaisesRegex(ValueError, 'still running'):
                self.provider.repoint_history(self.root, self.base / 'new')
            with self.assertRaisesRegex(ValueError, 'still running'):
                self.provider.gather(self.root, self.base / 'history')
        locks = self.home / 'thread-writer-locks'
        locks.mkdir(exist_ok=True)
        with (locks / (self.tid + '.lock')).open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(RuntimeError, 'Codex writer'):
                self.provider.repoint_history(self.root, self.base / 'new')
        self.assertEqual(self.rollout.read_bytes(), before)
        self.assertTrue(self.home.is_dir())

    def test_verification_rejects_a_missing_or_wrong_native_thread(self):
        import sqlite3
        self.assertTrue(self.provider.verify_history(self.root))
        with sqlite3.connect(self.home / 'state_5.sqlite') as db:
            db.execute('UPDATE threads SET cwd=? WHERE id=?', (str(self.base / 'wrong'), self.tid))
        db.close()
        self.assertFalse(self.provider.verify_history(self.root))
        self.rollout.unlink()
        self.assertFalse(self.provider.verify_history(self.root))

    def test_claude_gather_and_repeated_moves_keep_resume_lookup_and_old_links(self):
        from colony.migrate import claude_key
        claude = providers.get('claude')
        path = claude.config_home() / 'projects' / claude_key(self.root)
        path.mkdir(parents=True)
        (path / 's1.jsonl').write_text('{"type":"user","message":{"content":"hello"}}\n')
        console.remember(self.root, 's1', str(path / 's1.jsonl'))
        claude.gather(self.root, self.base / 'histories' / 'claude')
        old, _ = self.move()
        self.assertTrue(path.is_symlink())
        self.assertTrue(claude.verify_history(self.root))
        claude.gather(self.root, self.base / 'histories' / 'claude')
        pairs, said = claude.repoint_history(old, self.root)
        self.assertEqual(said, [])
        self.assertTrue(pairs)

    def test_move_verifies_before_launch_and_discards_only_the_failed_bookmark(self):
        from colony import handover
        dest = self.base / 'moved'
        events, said = [], []
        other = self.base / 'claude-session.jsonl'
        other.write_text('{"type":"user","message":{"content":"keep me"}}\n')
        console.remember(self.root, 'claude-session', str(other))
        def verify(root):
            events.append('verify')
            return False
        def ensure(root):
            events.append('ensure')
            self.assertIsNone(console.saved_conversation(root, 'codex'))
            self.assertEqual(console.saved_conversation(root, 'claude')['conversation'], 'claude-session')
        with patch.object(handover, 'outside', return_value=[]), patch.object(handover, 'settle', return_value=[]), \
                patch.object(console, 'stop'), patch.object(console, 'ensure', side_effect=ensure), \
                patch.object(self.provider, 'verify_history', side_effect=verify), patch.object(board, 'resume_project'):
            self.assertEqual(handover.move(self.root, dest, say=said.append), 0)
        self.assertEqual(events, ['verify', 'ensure'])
        self.assertIn("can't find", ' '.join(said))
        self.assertIn('fresh conversation', said[-1])
        self.assertTrue(dest.is_dir())

    def test_invalid_configuration_leaves_native_history_unchanged(self):
        (self.home / 'config.toml').write_text('broken = [')
        before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        with self.assertRaises(ValueError):
            self.provider.repoint_history(self.root, self.base / 'new')
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data, str(path))

    def test_a_failed_history_edit_restores_rollouts_and_both_databases(self):
        from colony import codex_history
        before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        real_offsets = codex_history._offsets
        def fail_after_offsets(*args):
            real_offsets(*args)
            raise OSError('injected failure after history offsets were changed')
        with patch.object(codex_history, '_offsets', side_effect=fail_after_offsets):
            with self.assertRaisesRegex(OSError, 'injected failure'):
                self.provider.repoint_history(self.root, self.base / 'new')
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data, str(path))
        self.assertTrue(self.provider.verify_history(self.root))

    def test_an_archived_thread_is_not_offered_for_resume(self):
        import sqlite3
        with sqlite3.connect(self.home / 'state_5.sqlite') as db:
            db.execute('UPDATE threads SET archived=1 WHERE id=?', (self.tid,))
        db.close()
        self.assertFalse(self.provider.verify_history(self.root))

    def test_malformed_rollout_metadata_fails_verification(self):
        for value in ([], dict(type='session_meta', payload=[]), dict(type='session_meta', payload=None)):
            with self.subTest(value=value):
                self.rollout.write_text(json.dumps(value) + '\n')
                self.assertFalse(self.provider.verify_history(self.root))
