"""The portable layout, exercised without reaching the person's home or starting an agent."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from colony import board, codex_remote, console, handover, migrate, voice


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
        dest = self.colony / "projects" / "plants"
        said = []
        self.assertEqual(handover.move(self.project, dest, sure=True, say=said.append), 0, said)
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


if __name__ == "__main__":
    unittest.main()
