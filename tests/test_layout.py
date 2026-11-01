"""The portable layout, exercised without reaching the person's home or starting an agent."""
import getpass
import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from colony import board, codex_remote, console, handover, migrate, providers, voice


class LayoutTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.person = Path(temp.name) / "person"
        self.person.mkdir()
        env = patch.dict(os.environ, {
            "PATH": os.environ["PATH"], "HOME": str(self.person), "USER": "layout-fixture",
            "XDG_CONFIG_HOME": str(self.person / ".config"),
            "XDG_CACHE_HOME": str(self.person / "cache"),
            "XDG_STATE_HOME": str(self.person / "state"),
            "CLAUDE_CONFIG_DIR": str(self.person / ".claude"),
            "CODEX_HOME": str(self.person / ".codex"),
            "COLONY_CODEX_SOURCE_HOME": str(self.person / ".codex"),
            "GIT_CONFIG_NOSYSTEM": "1",
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        for obj, name, result in ((console, "running", False), (console, "stop", None),
                                  (console, "ensure", None), (codex_remote, "alive", False),
                                  (handover, "outside", []), (handover, "settle", []),
                                  (migrate, "_crontab", ""), (migrate, "_units", [])):
            p = patch.object(obj, name, return_value=result)
            p.start()
            self.addCleanup(p.stop)
        self.old_home = self.person / ".config" / "colony"
        self.colony = self.person / "Colony"
        self.project = self.person / "outside" / "plants"
        self.project.mkdir(parents=True)
        self.git(self.project, "init", "-q", "-b", "main")
        self.git(self.project, "config", "user.name", "Fixture")
        self.git(self.project, "config", "user.email", "fixture@example.invalid")
        (self.project / "readme.txt").write_text("A project that stays outside until asked.\n")
        self.git(self.project, "add", "readme.txt")
        self.git(self.project, "commit", "-qm", "start")
        self.project_id = board.ident(self.project)
        self.registry([self.project])

    def git(self, folder, *args):
        run = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def registry(self, projects):
        board.save_registry({"projects": [str(p) for p in projects], "roots": [],
                             "ids": {board.ident(p): str(p) for p in projects},
                             "settings": {"lan": False, "messaging": False, "active_hours": "off"}})

    def mark_layout(self):
        board.save_json(self.colony / "COLONY.json", {
            "format": 1, "written_by": {"machine": "fixture", "user": "layout-fixture"}, "generation": 7,
        })

    def stop_still(self):
        self.assertEqual(handover.stop_still(lambda: None, say=lambda s: None), 0)

    def migrate(self):
        said = []
        result = migrate.into_colony(say=said.append)
        self.assertEqual(result, 0, said)
        return said

    def assert_no_keys(self, secrets):
        # A portable copy preserves links, so inspect carried regular files, not their external targets.
        for folder, dirs, files in os.walk(self.colony, followlinks=False):
            for name in files:
                path = Path(folder) / name
                if not path.is_symlink():
                    data = path.read_bytes()
                    for value in secrets:
                        self.assertNotIn(value.encode(), data, str(path))

    def test_without_marker_paths_stay_where_they_were(self):
        self.colony.mkdir()
        self.assertIsNone(board.colony_folder())
        self.assertEqual(board.home(), self.old_home)
        self.assertEqual(board.cache(), self.old_home)
        self.assertEqual(voice.folder(), self.old_home / "voice")
        self.assertEqual(board.codex_homes(), self.old_home / "codex-remote")
        self.assertEqual(codex_remote.home_for(self.project).parent, self.old_home / "codex-remote")
        self.assertEqual(board.agents_home(), self.old_home / "agents")
        self.assertEqual(board.items_home(), self.old_home / "items")

    def test_marker_routes_data_and_keeps_machine_files_outside(self):
        self.mark_layout()
        self.assertEqual(board.home(), self.colony / "home")
        self.assertEqual(board.cache(), self.person / "cache" / "colony")
        self.assertEqual(voice.folder(), self.person / "cache" / "colony" / "voice")
        self.assertEqual(board.codex_homes(), self.person / "state" / "colony" / "codex")
        self.assertEqual(board.agents_home(), self.colony / "projects" / ".agents")
        self.assertEqual(board.items_home(), self.colony / "projects" / ".items")
        self.assertEqual(board.registry()["new_root"], str(self.colony / "projects"))
        self.assertEqual(board.secrets(create=False), self.old_home)
        with patch.dict(os.environ, COLONY_BOARD_HOME=str(self.colony / "home")):
            self.assertEqual(board.machine_state(), self.person / "state" / "colony")
            self.assertEqual(board.secrets(create=False), self.old_home)

    def test_an_explicit_board_home_does_not_adopt_another_layout(self):
        self.mark_layout()
        other = self.person / "test-board"
        with patch.dict(os.environ, COLONY_BOARD_HOME=str(other)):
            self.assertEqual(board.home(), other)
            self.assertEqual(board.cache(), other)
            self.assertEqual(board.codex_homes(), other / "codex-remote")
            self.assertEqual(board.secrets(create=False), self.person / "test-board-secrets")
            self.assertEqual(board.machine_state(), self.person / "test-board-machine")

    def test_change_over_refuses_without_a_still_stop(self):
        before = (self.old_home / "board.json").read_bytes()
        said = []
        self.assertEqual(migrate.into_colony(say=said.append), 1)
        self.assertIn("colony stop --still", " ".join(said))
        self.assertFalse((self.colony / "COLONY.json").exists())
        self.assertEqual((self.old_home / "board.json").read_bytes(), before)

    def fixture(self):
        agent = self.old_home / "agents" / "gardener"
        agent.mkdir(parents=True)
        board.save_json(agent / ".board" / "settings.json", {"workdir": str(self.project)})
        self.registry([self.project, agent])
        worktrees = [self.old_home / "items" / "group" / name for name in ("R1-1", "integration")]
        for number, tree in enumerate(worktrees):
            tree.parent.mkdir(parents=True, exist_ok=True)
            self.git(self.project, "worktree", "add", "-q", "-b", f"work-{number}", str(tree))
            board.ident(tree)
            board.save_json(tree / ".board" / "context-session.json", {"path": str(tree / "history.jsonl")})
        board.save_json(self.old_home / "leads.json", {"group": {
            "id": "group", "canonical": str(self.project), "lead": str(self.project),
            "members": [str(self.project), str(agent)], "assignments": {
                "R1": {"workspace": str(worktrees[0])}}, "integration": str(worktrees[1]),
        }})
        for cache in ("voice", "context-cache", "line-scratch"):
            p = self.old_home / cache / "payload"
            p.parent.mkdir(parents=True)
            p.write_text(f"cached {cache}")
        remote = codex_remote.home_for(self.project)
        remote.mkdir(parents=True)
        (remote / "auth.json").write_text("distinctive-codex-auth")
        (remote / "history.jsonl").write_text("a remembered conversation\n")
        self.conversations = {self.project: self.codex_conversation(self.project)}
        values = ["distinctive-codex-auth"]
        for number, secret in enumerate(board.SECRET_PATHS):
            p = self.old_home / secret
            if secret == "connections":
                p /= "fixture.env"
            p.parent.mkdir(parents=True, exist_ok=True)
            value = f"distinctive-colony-secret-{number}"
            p.write_text(value)
            values.append(value)
        self.stop_still()
        return agent, worktrees, remote, values

    def codex_conversation(self, root):
        """A saved Codex conversation in the project's owned home, shaped as Codex 0.166 keeps it: the index's
        rollout path, the runtime records and the database home all name the home, and the history's byte offsets
        point at the turn's line, which comes after records whose length the change-over alters."""
        import sqlite3
        from contextlib import closing
        home = codex_remote.home_for(root)
        tid = "01930000-0000-7000-8000-0000000000" + ("01" if root == self.project else "02")
        rollout = home / "sessions" / "2026" / "10" / "31" / f"rollout-{tid}.jsonl"
        rollout.parent.mkdir(parents=True)
        cwd = str(board.workdir(root))
        policy = dict(type="workspace-write", writable_roots=[cwd, str(home / "memories")])
        rows = [dict(type="session_meta", payload=dict(id=tid, cwd=cwd, runtime_workspace_roots=[cwd, str(home)])),
                dict(type="turn_context", payload=dict(cwd=cwd, sandbox_policy=policy)),
                dict(type="world_state", payload=dict(state=dict(environments=dict(local=dict(cwd=cwd))))),
                dict(type="event_msg", payload=dict(type="thread_settings_applied", thread_settings=dict(
                    cwd=cwd, runtime_workspace_roots=[cwd, str(home)], sandbox_policy=policy))),
                dict(type="response_item", payload=dict(type="message", role="user",
                                                        content=[dict(text="Remember marigold-otter-127, café.")]))]
        lines = [(json.dumps(row, ensure_ascii=False) + "\n").encode() for row in rows]
        rollout.write_bytes(b"".join(lines))
        with closing(sqlite3.connect(home / "state_5.sqlite")) as db, db:
            db.executescript("""CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT NOT NULL, rollout_path TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL, title TEXT, archived INTEGER);
                CREATE TABLE rollout_migration_skipped_rollouts (migration_id TEXT, rollout_path TEXT, skip_reason TEXT);""")
            db.execute("INSERT INTO threads VALUES (?,?,?,?,?,?)",
                       (tid, cwd, str(rollout), json.dumps(policy), "marigold", 0))
            db.execute("INSERT INTO rollout_migration_skipped_rollouts VALUES (?,?,?)", ("m", str(rollout), "fixture"))
        with closing(sqlite3.connect(home / "thread_history_1.sqlite")) as db, db:
            db.executescript("""CREATE TABLE thread_turns (thread_id TEXT, rollout_byte_offset INTEGER,
                rollout_end_byte_offset INTEGER);
                CREATE TABLE thread_history_projection_state (thread_id TEXT, next_rollout_byte_offset INTEGER);""")
            db.execute("INSERT INTO thread_turns VALUES (?,?,?)", (tid, sum(map(len, lines[:-1])), sum(map(len, lines))))
            db.execute("INSERT INTO thread_history_projection_state VALUES (?,?)", (tid, sum(map(len, lines))))
        (home / "config.toml").write_text("sqlite_home=" + json.dumps(str(home)) + "\n")
        (home / "colony-thread.json").write_text(json.dumps(dict(thread=tid, rollout=str(rollout))))
        console.remember(root, tid, str(rollout))
        self.assertTrue(providers.get("codex").verify_history(root))
        return home, tid, lines[-1]

    def assert_codex_conversation(self, root, old_home, tid, message):
        """The moved home names only itself, Codex's lookup finds the conversation, and its turn reads the same."""
        import sqlite3
        from contextlib import closing
        home = codex_remote.home_for(root)
        self.assertTrue(home.is_dir())
        self.assertTrue(providers.get("codex").verify_history(root))
        for path in home.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix == ".sqlite":
                with closing(sqlite3.connect(path)) as db:
                    text = "\n".join(db.iterdump())
            else:
                text = path.read_text(errors="replace")
            self.assertNotIn(str(old_home), text, str(path))
        with closing(sqlite3.connect(home / "state_5.sqlite")) as db:
            rollout = Path(db.execute("SELECT rollout_path FROM threads WHERE id=?", (tid,)).fetchone()[0])
        self.assertEqual(rollout.parent.parent.parent.parent.parent, home)
        with closing(sqlite3.connect(home / "thread_history_1.sqlite")) as db:
            start, end = db.execute("SELECT rollout_byte_offset,rollout_end_byte_offset FROM thread_turns").fetchone()
            following = db.execute("SELECT next_rollout_byte_offset FROM thread_history_projection_state").fetchone()[0]
        self.assertEqual(rollout.read_bytes()[start:end], message)
        self.assertEqual(following, end)
        self.assertEqual(codex_remote.user_config(home)["sqlite_home"], str(home))
        self.assertEqual(json.loads((home / "colony-thread.json").read_text())["rollout"], str(rollout))

    def test_codex_homes_are_told_their_new_place_once_after_an_interruption_following_the_moves(self):
        from colony import layout

        class Interrupted(BaseException):
            pass

        fixture = self.fixture()
        generation = json.loads((self.old_home / "stopped.json").read_text())["generation"]
        step, rehome = layout._step, providers.get("codex").rehome_history
        calls = []

        def stop_after_moves(journal, note, name, action):
            if name.startswith("rehome:") and name not in note["done"]:
                self.assertTrue(all(f"move:{i}" in note["done"] for i in range(len(note["moves"]))))
                raise Interrupted()
            return step(journal, note, name, action)

        def counted(home, old_home, backup=None):
            calls.append(home)
            return rehome(home, old_home, backup)

        with patch.object(layout, "_step", side_effect=stop_after_moves):
            with self.assertRaises(Interrupted):
                self.migrate()
        self.assertFalse(providers.get("codex").verify_history(self.project), "the moved home still names the old one")
        with patch.object(providers.get("codex"), "rehome_history", side_effect=counted):
            self.migrate()
        self.assertEqual(calls, [board.codex_homes() / codex_remote.home_for(self.project).name])
        self.assert_changed_over(*fixture, generation)
        backups = board.machine_state() / "backups" / "into-colony"
        old_state = self.old_home / "codex-remote" / calls[0].name / "state_5.sqlite"
        self.assertTrue((backups / str(board.codex_homes() / calls[0].name / "state_5.sqlite").lstrip("/")).is_file())
        self.assertFalse(old_state.exists())

    def test_an_agent_codex_home_is_rehomed_and_then_follows_its_moved_agent(self):
        agent, *_ = self.fixture()
        old_home = codex_remote.home_for(agent)
        self.conversations[agent] = self.codex_conversation(agent)
        self.migrate()
        moved_agent = board.agents_home() / agent.name
        self.assertNotEqual(codex_remote.home_for(moved_agent).name, old_home.name)
        self.assert_codex_conversation(moved_agent, old_home, *self.conversations[agent][1:])

    def test_change_over_preserves_projects_and_repairs_both_worktrees(self):
        agent, worktrees, remote, values = self.fixture()
        generation = json.loads((self.old_home / "stopped.json").read_text())["generation"]
        self.migrate()
        self.assert_changed_over(agent, worktrees, remote, values, generation)

    def assert_changed_over(self, agent, worktrees, remote, values, generation):
        self.assertEqual(board.home(), self.colony / "home")
        marker = json.loads((self.colony / "COLONY.json").read_text())
        self.assertEqual(marker["generation"], generation)
        self.assertTrue(marker.get("format"))
        moved_agent = self.colony / "projects" / ".agents" / agent.name
        self.assertEqual(set(board.projects(board.registry(), adopt=False)), {self.project, moved_agent})
        self.assertEqual(board.workdir(moved_agent), self.project)
        self.assertFalse(agent.exists())
        self.assertTrue(self.project.exists(), "the person's project itself moves only when asked")
        self.assertEqual(board.ident(self.project, make=False), self.project_id)
        listing = self.git(self.project, "worktree", "list", "--porcelain")
        self.assertNotIn("prunable", listing)
        for tree in worktrees:
            moved = self.colony / "projects" / ".items" / tree.relative_to(self.old_home / "items")
            self.assertIn(str(moved), listing)
            self.assertFalse(tree.exists())
            self.git(moved, "status", "--porcelain")
            context = json.loads((moved / ".board" / "context-session.json").read_text())
            self.assertEqual(context["path"], str(moved / "history.jsonl"))
        leads = (board.home() / "leads.json").read_text()
        self.assertNotIn(str(self.old_home / "items"), leads)
        self.assertNotIn(str(agent), leads)
        for cache in ("voice", "context-cache", "line-scratch"):
            self.assertEqual((board.cache() / cache / "payload").read_text(), f"cached {cache}")
            self.assertFalse((board.home() / cache).exists())
        moved_remote = board.codex_homes() / remote.name
        for root, (home, tid, message) in self.conversations.items():
            self.assert_codex_conversation(root, home, tid, message)
        self.assertEqual((moved_remote / "auth.json").read_text(), "distinctive-codex-auth")
        self.assertEqual((moved_remote / "history.jsonl").read_text(), "a remembered conversation\n")
        for secret in board.SECRET_PATHS:
            self.assertTrue((self.old_home / secret).exists(), secret)
            self.assertFalse((board.home() / secret).exists(), secret)
        self.assert_no_keys(values)
        backups = board.machine_state() / "backups"
        self.assertTrue(backups.is_dir())
        self.assertFalse((board.home() / "backups").exists())
        journals = [p for p in board.machine_state().glob("*.json") if "journal" in p.name or "into" in p.name]
        self.assertEqual(journals, [])
        self.assertFalse((self.old_home / "board.json").exists())
        self.assertFalse((self.old_home / "agents").exists())
        self.assertFalse((self.old_home / "items").exists())

    def interrupted_change_over(self, checkpoint_written):
        from colony import layout

        class Interrupted(BaseException):
            pass

        step, names = layout._step, []

        def record(journal, note, name, action):
            if name not in note["done"]:
                names.append(name)
            return step(journal, note, name, action)

        self.fixture()
        with patch.object(layout, "_step", side_effect=record):
            self.migrate()
        self.assertTrue(names)
        for target in names:
            with self.subTest(step=target):
                case = LayoutTest()
                try:
                    case.setUp()
                    fixture = case.fixture()
                    generation = json.loads((case.old_home / "stopped.json").read_text())["generation"]
                    interrupted = []

                    def stop(journal, note, name, action):
                        if name != target or interrupted:
                            return step(journal, note, name, action)
                        interrupted.append(name)
                        if checkpoint_written:
                            step(journal, note, name, action)
                            raise Interrupted()

                        def before_checkpoint():
                            action()
                            raise Interrupted()
                        return step(journal, note, name, before_checkpoint)

                    with patch.object(layout, "_step", side_effect=stop):
                        with case.assertRaises(Interrupted):
                            case.migrate()
                    case.assertEqual(interrupted, [target])
                    case.assertTrue(layout.journal_path().exists())
                    case.migrate()
                    case.assert_changed_over(*fixture, generation)
                finally:
                    case.doCleanups()

    def test_interrupted_after_every_action_resumes_before_its_checkpoint(self):
        self.interrupted_change_over(checkpoint_written=False)

    def test_interrupted_after_every_checkpoint_resumes_without_duplicating(self):
        self.interrupted_change_over(checkpoint_written=True)

    def test_repeating_a_finished_change_over_does_not_recreate_old_data(self):
        self.fixture()
        self.migrate()
        before = sorted(str(p.relative_to(self.colony)) for p in self.colony.rglob("*"))
        self.migrate()
        self.assertEqual(sorted(str(p.relative_to(self.colony)) for p in self.colony.rglob("*")), before)
        self.assertFalse((self.old_home / "board.json").exists())
        self.assertFalse((self.old_home / "agents").exists())
        self.assertFalse((self.old_home / "items").exists())

    def test_move_in_keeps_project_keys_on_the_machine_and_lists_them(self):
        self.stop_still()
        self.migrate()
        values = {".env": "distinctive-project-env", "secrets/x.key": "distinctive-project-key",
                  "credentials-work.json": "distinctive-project-credentials"}
        for name, value in values.items():
            p = self.project / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(value)
        (self.project / ".env.example").write_text("EXAMPLE=fill-this-in")
        (self.project / ".gitignore").write_text("secrets/\n")          # matches a folder, not the link left there
        self.git(self.project, "add", ".gitignore")
        self.git(self.project, "commit", "-qm", "ignore")
        dest = self.colony / "projects" / "plants"
        said = []
        self.assertEqual(handover.move(self.project, dest, sure=True, say=said.append), 0, said)
        untracked = {line[3:] for line in self.git(dest, "status", "--porcelain", "--untracked-files=all").splitlines()}
        self.assertIn(".env.example", untracked)
        for name in (".env", "secrets", "credentials-work.json"):
            self.assertNotIn(name, untracked, "a key's link is never committed by `git add -A`")
        keys = self.old_home / "project-keys" / self.project_id
        for name, value in values.items():
            self.assertEqual((keys / name).read_text(), value)
            carried = dest / name
            self.assertTrue(carried.is_symlink() or any(p.is_symlink() for p in carried.parents if p != dest))
            self.assertEqual(carried.resolve(), (keys / name).resolve())
            self.assertEqual(carried.read_text(), value)
        self.assertFalse((dest / ".env.example").is_symlink())
        self.assert_no_keys(values.values())
        with patch.object(migrate, "program", return_value=None), \
                patch.object(migrate, "machine", return_value={"crontab": ""}):
            migrate.listing(say=said.append)
        for name in values:
            self.assertIn(name, "\n".join(said))

    def test_files_git_tracks_stay_in_the_project(self):
        """A source folder called secrets/ or a committed test key is the project's code: moving it out would change the
        repository under it, and what's committed is in its history anyway. Only untracked keys leave."""
        git = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=self.project,
                                        capture_output=True, check=True)
        git("init", "-q")
        (self.project / "secrets").mkdir()
        (self.project / "secrets" / "__init__.py").write_text("def hide(x): return x\n")
        (self.project / "tests").mkdir()
        (self.project / "tests" / "fixture.key").write_text("not a real key")
        git("add", "secrets", "tests")
        git("commit", "-qm", "code")
        (self.project / ".env").write_text("TOKEN=distinctive-untracked")
        found = {p.relative_to(self.project).as_posix() for p in migrate.project_keys(self.project)}
        self.assertEqual(found, {".env"})

    def test_agent_history_item_keys_and_retained_secret_paths_survive(self):
        agent, trees, _, _ = self.fixture()
        remote = codex_remote.home_for(agent)
        transcript = remote / "sessions" / "one.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text('{"message": "remembered"}\n')
        board.save_json(board.home() / "conversations.json", {
            board.ident(agent): {"transcript": str(transcript), "conversation": "one"}})
        key = self.old_home / "connections" / "fixture.env"
        board.save_json(board.home() / "references.json", {"key": str(key)})
        (trees[0] / ".env").write_text("item-key-distinctive")
        tree_id = board.ident(trees[0])
        self.migrate()
        moved_agent = board.agents_home() / agent.name
        saved = json.loads((board.home() / "conversations.json").read_text())[board.ident(moved_agent)]
        self.assertEqual(Path(saved["transcript"]), codex_remote.home_for(moved_agent) / "sessions" / "one.jsonl")
        self.assertIn("remembered", Path(saved["transcript"]).read_text())
        self.assertEqual(json.loads((board.home() / "references.json").read_text())["key"], str(key))
        moved_tree = board.items_home() / "group" / "R1-1"
        self.assertTrue((moved_tree / ".env").is_symlink())
        self.assertEqual((moved_tree / ".env").resolve(), self.old_home / "project-keys" / tree_id / ".env")
        self.assert_no_keys(["item-key-distinctive"])

    def test_conversion_refuses_an_overridden_board_without_moving_it(self):
        with patch.dict(os.environ, COLONY_BOARD_HOME=str(self.person / "other-board")):
            self.registry([self.project])
            self.stop_still()
            said = []
            self.assertEqual(migrate.into_colony(say=said.append), 1)
            self.assertIn("COLONY_BOARD_HOME", " ".join(said))
            self.assertTrue((board.home() / "board.json").exists())
            self.assertFalse(self.colony.exists())

    def test_an_interrupted_cross_disk_link_copy_resumes(self):
        from colony import layout
        old, new = self.person / "old-link", self.person / "new-link"
        old.symlink_to("missing-target")
        new.symlink_to("missing-target")
        layout._transfer(old, new)
        self.assertFalse(old.is_symlink())
        self.assertEqual(os.readlink(new), "missing-target")

    def test_the_folder_generation_tracks_stops_and_detects_an_older_copy(self):
        self.stop_still()
        self.migrate()
        marker_path = self.colony / "COLONY.json"
        marker = json.loads(marker_path.read_text())
        self.assertFalse(handover.started())
        self.assertFalse(json.loads(marker_path.read_text())["still"])
        board.save_json(marker_path, dict(json.loads(marker_path.read_text()),
                                          written_by={"machine": "old-box", "user": "migrator"}))
        self.stop_still()
        self.assertEqual(json.loads(marker_path.read_text())["generation"], marker["generation"] + 1)
        self.assertEqual(json.loads(marker_path.read_text())["written_by"],
                         {"machine": socket.gethostname(), "user": getpass.getuser()}, "the last writer: this machine")
        board.save_json(marker_path, marker)
        self.assertTrue(handover.started(), "an older portable marker triggers the restore hold")

    def test_cross_disk_transfer_preserves_permissions_and_recovers_between_copy_and_remove(self):
        import errno
        from colony import layout
        old, new = self.person / "old-tree", self.person / "new-tree"
        old.mkdir(mode=0o700)
        (old / "file").write_text("whole payload")
        (old / "link").symlink_to("file")
        rename, unlink = Path.rename, Path.unlink
        interrupted = []

        def across(path, target):
            if path == old or old in path.parents:
                raise OSError(errno.EXDEV, "fixture: different disks")
            return rename(path, target)

        def remove(path, *args, **kwargs):
            if path == old / "file" and not interrupted:
                interrupted.append(True)
                raise OSError("fixture: stopped after copying")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "rename", across), patch.object(Path, "unlink", remove):
            with self.assertRaisesRegex(OSError, "stopped after copying"):
                layout._transfer(old, new)
            layout._transfer(old, new)
        self.assertFalse(old.exists())
        self.assertEqual((new / "file").read_text(), "whole payload")
        self.assertEqual((new / "link").read_text(), "whole payload")
        self.assertEqual(new.stat().st_mode & 0o777, 0o700)

    def test_start_finishes_an_interrupted_change_over_before_resuming(self):
        from colony import layout
        self.fixture()
        save = layout._save

        def interrupted(path, value):
            save(path, value)
            if path.name == "COLONY.json":
                raise KeyboardInterrupt("fixture: stopped after marker")

        with patch.object(layout, "_save", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                migrate.into_colony(say=lambda s: None)
        with patch("builtins.print"):
            self.assertFalse(handover.started())
        self.assertFalse(layout.journal_path().exists())
        self.assertFalse((self.old_home / "board.json").exists())
        self.assertFalse(json.loads((self.colony / "COLONY.json").read_text())["still"])
        self.assertEqual(len(board.registry()["projects"]), 2)


if __name__ == "__main__":
    unittest.main()
