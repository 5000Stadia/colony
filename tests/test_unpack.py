"""R127: an archive packed under one home folder, unpacked under another, comes back whole and re-pointed. Both homes
are temporary (HOME, XDG_*, CLAUDE_CONFIG_DIR and CODEX_HOME all inside them); no program is run, fixtures stand in
for Claude Code's and Codex's stores."""
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from colony import board, codex_remote, console, handover, measures, migrate, providers, repoint


class UnpackElsewhereTest(unittest.TestCase):
    TID = "01930000-0000-7000-8000-0000000000aa"
    SID = "5e55a0b1-0000-4000-8000-000000000001"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.old, self.new = self.base / "migrator", self.base / "restored"
        self.disk = self.base / "disk"
        self.outside = self.base / "srv" / "kept"                 # a project kept outside the home folder
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        for obj, name, result in ((migrate, "_board_running", False), (migrate, "program", None),
                                  (migrate, "_crontab", ""), (migrate, "_units", []),
                                  (migrate, "_command", "here"), (codex_remote, "alive", False)):
            stand_in = patch.object(obj, name, return_value=result)
            stand_in.start()
            self.addCleanup(stand_in.stop)

    def live_in(self, home):
        """This process signed in with `home` as its home folder."""
        for key in ("COLONY_BOARD_HOME", "COLONY_SECRETS_HOME", "XDG_DATA_HOME"):
            os.environ.pop(key, None)
        home.mkdir(parents=True, exist_ok=True)
        os.environ.update(HOME=str(home), USER="fixture", XDG_CONFIG_HOME=str(home / ".config"),
                          XDG_CACHE_HOME=str(home / ".cache"), XDG_STATE_HOME=str(home / ".local" / "state"),
                          CLAUDE_CONFIG_DIR=str(home / ".claude"), CODEX_HOME=str(home / ".codex"),
                          COLONY_CODEX_SOURCE_HOME=str(home / ".codex"), GIT_CONFIG_NOSYSTEM="1")

    def git(self, folder, *args):
        run = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def repo(self, folder):
        folder.mkdir(parents=True)
        self.git(folder, "init", "-q", "-b", "main")
        self.git(folder, "config", "user.name", "Fixture")
        self.git(folder, "config", "user.email", "fixture@example.invalid")
        (folder / "readme.txt").write_text("work\n")
        self.git(folder, "add", "readme.txt")
        self.git(folder, "commit", "-qm", "start")
        board.ident(folder)
        return folder

    def claude_talk(self, root):
        talk = providers.get("claude").config_home() / "projects" / migrate.claude_key(root) / (self.SID + ".jsonl")
        talk.parent.mkdir(parents=True, exist_ok=True)
        talk.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "remember marigold"}}) + "\n")
        console.remember(root, self.SID, str(talk))

    def codex_home(self, root):
        """An owned Codex home holding one thread, shaped as Codex 0.166 keeps it (codex-history-findings.md)."""
        home = codex_remote.home_for(root)
        rollout = home / "sessions" / "2026" / "10" / "31" / f"rollout-2026-10-31T11-16-28-{self.TID}.jsonl"
        rollout.parent.mkdir(parents=True)
        cwd = str(root)
        rows = [dict(type="session_meta", payload=dict(id=self.TID, cwd=cwd, runtime_workspace_roots=[cwd])),
                dict(type="turn_context", payload=dict(cwd=cwd, sandbox_policy=dict(type="workspace-write",
                                                                                     writable_roots=[cwd]))),
                dict(type="event_msg", payload=dict(type="thread_settings_applied", thread_settings=dict(cwd=cwd))),
                dict(type="response_item", payload=dict(type="message", role="user", content=[dict(text="saffron")]))]
        lines = [(json.dumps(row) + "\n").encode() for row in rows]
        rollout.write_bytes(b"".join(lines))
        with closing(sqlite3.connect(home / "state_5.sqlite")) as db, db:
            db.executescript("""CREATE TABLE threads (id TEXT PRIMARY KEY, cwd TEXT NOT NULL, rollout_path TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL, title TEXT, archived INTEGER);
                CREATE TABLE project_roots (project_id TEXT, position INTEGER, path TEXT);""")
            db.execute("INSERT INTO threads VALUES (?,?,?,?,?,?)", (self.TID, cwd, str(rollout),
                       json.dumps(dict(type="workspace-write", writable_roots=[cwd])), "saffron", 0))
            db.execute("INSERT INTO project_roots VALUES ('p', 0, ?)", (cwd,))
        with closing(sqlite3.connect(home / "thread_history_1.sqlite")) as db, db:
            db.execute("CREATE TABLE thread_turns (thread_id TEXT, rollout_byte_offset INTEGER, rollout_end_byte_offset INTEGER)")
            db.execute("INSERT INTO thread_turns VALUES (?,?,?)", (self.TID, sum(map(len, lines[:-1])), sum(map(len, lines))))
        (home / "config.toml").write_text("sqlite_home=" + json.dumps(str(home)) + "\n")
        (home / "colony-thread.json").write_text(json.dumps(dict(thread=self.TID, cwd=cwd)))
        sign_in = Path(os.environ["CODEX_HOME"]) / "auth.json"
        sign_in.parent.mkdir(parents=True, exist_ok=True)
        sign_in.write_text("SECRET-codex-sign-in")
        (home / "auth.json").symlink_to(sign_in)
        console.remember(root, self.TID, str(rollout))
        return home, lines[-1]

    def fixture(self, colony_layout):
        """A board under the old home: a Claude Code project with a saved conversation and a linked item worktree, a
        Codex project with its owned home, a project kept outside the home, and the steward's Codex home."""
        self.live_in(self.old)
        if colony_layout:
            board.save_json(self.old / "Colony" / "COLONY.json", {"format": 1, "generation": 3})
            self.assertEqual(board.home(), self.old / "Colony" / "home")
        projects = (self.old / "Colony" / "projects") if colony_layout else (self.old / "Projects")
        self.plants, self.beta = self.repo(projects / "plants"), self.repo(projects / "beta")
        self.repo(self.outside)
        board.save_registry({"projects": [str(self.plants), str(self.beta), str(self.outside)], "roots": [],
                             "ids": {board.ident(p): str(p) for p in (self.plants, self.beta, self.outside)},
                             "settings": {"lan": False, "messaging": False, "active_hours": "off"}})
        board.save_json(self.beta / ".board" / "settings.json", {"provider": "codex"})
        self.claude_talk(self.plants)
        self.claude_talk(self.outside)
        self.codex, self.prose = self.codex_home(self.beta)
        steward = board.codex_homes() / "steward"
        steward.mkdir(parents=True)
        (steward / "config.toml").write_text("sqlite_home=" + json.dumps(str(steward)) + "\n")
        self.item = board.items_home() / "group" / "R1-1"
        self.item.parent.mkdir(parents=True)
        self.git(self.plants, "worktree", "add", "-q", "-b", "work-1", str(self.item))
        board.save_json(self.item / ".board" / "context-session.json", {"path": str(self.item / "history.jsonl")})
        board.save_json(board.home() / "leads.json", {"group": {"canonical": str(self.plants), "lead": str(self.plants),
                                                               "items": {"R1": str(self.item)}}})
        values = measures.folder(self.plants) / "values.jsonl"
        values.parent.mkdir(parents=True)
        values.write_text('{"value": 7}\n')
        (board.home() / "monitor").mkdir(parents=True, exist_ok=True)
        if colony_layout:                                   # a key kept on the machine, its link in the project
            key = board.secrets() / "project-keys" / board.ident(self.plants) / ".env"
            key.parent.mkdir(parents=True)
            key.write_text("API_KEY=SECRET-project-key")
            (self.plants / ".env").symlink_to(key)
        self.old_colony = board.home()

    def pack_and_move(self, colony_layout):
        self.fixture(colony_layout)
        archive = migrate.pack(self.disk, say=lambda line: None)
        shutil.rmtree(self.old)                             # the old machine is gone, and the outside project with it
        shutil.rmtree(self.outside)
        self.live_in(self.new)
        return archive

    def at(self, path):
        return Path(str(path).replace(str(self.old), str(self.new), 1))

    def check_restored(self, said):
        reg = board.registry()
        plants, beta = self.at(self.plants), self.at(self.beta)
        self.assertEqual(reg["projects"], [str(plants), str(beta), str(self.outside)])
        self.assertEqual(sorted(reg["ids"].values()), sorted([str(plants), str(beta), str(self.outside)]))
        for root in (plants, beta, self.outside):
            self.assertTrue((root / "readme.txt").is_file(), root)
        self.assertTrue(providers.get("claude").verify_history(plants))
        self.assertTrue(providers.get("claude").verify_history(self.outside))
        self.assertTrue(providers.get("codex").verify_history(beta))
        self.assertEqual(console.saved_conversation(beta, "codex")["conversation"], self.TID)
        # Codex's home is the one named for the new path, and every path inside says where things are now
        home = codex_remote.home_for(beta)
        self.assertTrue(home.is_dir())
        self.assertEqual(sorted(p.name for p in board.codex_homes().iterdir()), sorted([home.name, "steward"]))
        with closing(sqlite3.connect(home / "state_5.sqlite")) as db:
            cwd, rollout = db.execute("SELECT cwd, rollout_path FROM threads").fetchone()
            self.assertEqual((cwd, Path(rollout).parent.parent.parent.parent.parent), (str(beta), home))
            self.assertEqual(db.execute("SELECT path FROM project_roots").fetchone()[0], str(beta))
        with closing(sqlite3.connect(home / "thread_history_1.sqlite")) as db:
            start, end = db.execute("SELECT rollout_byte_offset, rollout_end_byte_offset FROM thread_turns").fetchone()
        self.assertEqual(Path(rollout).read_bytes()[start:end], self.prose, "the saved turn's offsets followed")
        self.assertEqual(codex_remote.user_config(home)["sqlite_home"], str(home))
        steward = board.codex_homes() / "steward"
        self.assertEqual(codex_remote.user_config(steward)["sqlite_home"], str(steward))
        self.assertEqual(os.readlink(home / "auth.json"), str(self.new / ".codex" / "auth.json"),
                         "the link to the person's sign-in names this home's")
        self.assertTrue((measures.folder(plants) / "values.jsonl").is_file(), "the folder named by a hash followed")
        # no record, file or link names the old home
        for top in (self.new, self.outside):
            for folder, dirs, files in os.walk(top):
                for name in dirs + files:
                    path = Path(folder) / name
                    if path.is_symlink():
                        self.assertNotIn(str(self.old), os.readlink(path), path)
                    elif path.is_file() and ".sqlite" not in name:
                        self.assertNotIn(str(self.old).encode(), path.read_bytes(), path)
        # git worktrees work from where they are now
        item = self.at(self.item)
        listed = self.git(plants, "worktree", "list", "--porcelain")
        self.assertIn(f"worktree {item}", listed)
        self.assertNotIn("prunable", listed)
        self.assertEqual(self.git(item, "rev-parse", "--abbrev-ref", "HEAD").strip(), "work-1")
        self.assertTrue(board.restore_held())
        self.assertFalse(migrate._journal().exists(), "the journal goes once the pass is done")
        self.assertIn(f"Re-pointed from {self.old} to {self.new}", said)

    def unpack(self, archive, **kw):
        said = []
        migrate.unpack(archive, say=said.append, **kw)
        return "\n".join(said)

    def test_an_old_layout_archive_unpacks_under_another_home(self):
        archive = self.pack_and_move(colony_layout=False)
        said = self.unpack(archive)
        self.assertEqual(board.home(), self.new / ".config" / "colony")
        self.check_restored(said)

    def test_a_colony_folder_archive_unpacks_under_another_home(self):
        archive = self.pack_and_move(colony_layout=True)
        said = self.unpack(archive)
        self.assertEqual(board.home(), self.new / "Colony" / "home")
        self.assertEqual(board.codex_homes(), self.new / ".local" / "state" / "colony" / "codex")
        self.check_restored(said)
        key = board.secrets(create=False) / "project-keys" / board.ident(self.at(self.plants)) / ".env"
        self.assertEqual(os.readlink(self.at(self.plants) / ".env"), str(key), "its link expects the key here")
        self.assertFalse(key.exists(), "the key itself never travelled")
        self.assertIn(f"plants's own keys, .env, into {key.parent}", said)

    def test_interrupted_after_unpacking_it_finishes_when_run_again(self):
        archive = self.pack_and_move(colony_layout=False)
        real = repoint.repoint
        calls = []

        def fail_once(old, new, backup=None):
            calls.append(new)
            if len(calls) == 1:
                raise ValueError("power cut")
            return real(old, new, backup)
        with patch.object(repoint, "repoint", side_effect=fail_once):
            with self.assertRaises(SystemExit) as stopped:
                self.unpack(archive)
            self.assertIn("again finishes it", str(stopped.exception))
            self.assertTrue(migrate._journal().exists())
            self.assertTrue(board.restore_held(), "held before anything is re-pointed")
            said = self.unpack(archive)                     # no --force: it carries on from its journal
        self.check_restored(said)
        talks = sorted(p.name for p in (self.new / ".claude" / "projects").iterdir() if not p.is_symlink())
        self.assertEqual(talks, sorted(migrate.claude_key(p) for p in (self.at(self.plants), self.outside)),
                         "each conversation folder once, at its new name")
        rows = json.loads((board.home() / "conversations.json").read_text())
        self.assertEqual(len(rows), 3, "one bookmark row per project")

    def test_a_part_outside_the_home_that_cant_be_written_stops_before_anything_is_unpacked(self):
        archive = self.pack_and_move(colony_layout=False)
        self.outside.parent.chmod(0o500)
        self.addCleanup(self.outside.parent.chmod, 0o700)
        if os.access(self.outside.parent, os.W_OK):
            self.skipTest("this user can write anywhere (root)")
        with self.assertRaises(SystemExit) as stopped:
            self.unpack(archive)
        self.assertIn(str(self.outside), str(stopped.exception))
        self.assertFalse((self.new / ".config" / "colony").exists(), "nothing unpacked")
        self.assertFalse(migrate._journal().exists())


if __name__ == "__main__":
    unittest.main()
