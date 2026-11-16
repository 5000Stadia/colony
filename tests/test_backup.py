"""Backup (R165): the folder is a setting, the watcher starts one copy a night, the copy goes only to the drive the
person chose, the newest four are kept, and a copy not made is told once. No real pack is made: a stand-in writes
the archive a pack would."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from colony import backup, board, migrate


class BackupTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        env = patch.dict(os.environ, {"COLONY_BOARD_HOME": str(self.base / "home")})
        env.start()
        self.addCleanup(env.stop)
        self.plants = self.base / "plants"
        self.plants.mkdir()
        board.save_registry({"roots": [], "settings": {"messaging": False, "active_hours": "off"}})
        taken = patch.object(migrate, "parts", return_value=[{"kind": "project", "name": "plants", "path": self.plants, "inside": None}])
        taken.start()
        self.addCleanup(taken.stop)
        self.disk = self.base / "disk" / "copies"
        self.made = 0

    def packs(self, dest, say=print):
        """What a pack leaves: an archive named by its moment (a minute apart here) and its manifest."""
        self.made += 1
        stamp = f"2026-11-15-03{self.made:02d}"
        (Path(dest) / f"colony-{stamp}.manifest.json").write_text("{}")
        archive = Path(dest) / f"colony-{stamp}.tar.zst"
        archive.write_bytes(b"x" * 10)
        return archive

    def test_the_folder_is_a_setting_and_one_the_copy_itself_takes_is_refused(self):
        self.assertEqual(backup.line(), "Off: no copy of colony is kept", "none is chosen for the person")
        self.assertFalse(backup.due(), "and none is made")
        with self.assertRaisesRegex(ValueError, "whole path"):
            board.set_setting("backup", "copies")
        with self.assertRaisesRegex(ValueError, "which the copy itself takes"):
            board.set_setting("backup", str(self.plants / "backup"))
        self.assertFalse((self.plants / "backup").exists(), "a refused folder is not made")
        board.set_setting("backup", str(self.disk))
        self.assertEqual(backup.chosen(), str(self.disk.resolve()))
        self.assertTrue(self.disk.is_dir(), "the folder is made as it is chosen")
        self.assertIn("no copy made yet", backup.line())
        self.assertTrue(backup.same_drive(), "the test's folder shares a drive with its records, and Settings says so")
        self.assertIn("same drive as colony itself", board.backup_card())
        page = board.settings_page(board.registry())
        self.assertIn("id='set-backup'", page)
        self.assertIn(f"name='folder' value='{self.disk.resolve()}'", page)
        board.set_setting("backup", "off")
        self.assertEqual(backup.chosen(), "")
        self.assertNotIn("Copy now", board.backup_card(), "nothing to copy to")
        self.assertIn("Settings → Backup", board.start_here(), "a new board says no copy is kept until a place is chosen")

    def test_a_folder_on_another_drive_is_never_stood_in_for_by_colonys_own(self):
        with patch.object(backup, "_same_drive", return_value=False), patch.object(backup, "_findmnt", return_value=""):
            board.set_setting("backup", str(self.disk))                  # another drive, on a machine that can't name it
            self.assertEqual(backup.where(), (self.disk.resolve(), ""))
        self.assertEqual(backup.where(), (None, backup.AWAY), "the same path, now on colony's own drive: the drive is away")
        with patch.object(migrate, "pack", side_effect=self.packs):
            self.assertEqual(backup.run(say=lambda *a: None), 1)
        self.assertEqual(self.made, 0, "nothing is packed anywhere else")
        self.assertEqual(len(board.notices()), 1)
        self.assertIn("its drive isn't attached", board.notices()[0]["text"])

        def findmnt(*args):                                              # a drive known by its id, mounted at /media/x
            return {"UUID": "abc-1", "TARGET": str(self.base / "disk")}[args[0]] if args[1] == "-T" else self.where_now
        with patch.object(backup, "_same_drive", return_value=False), patch.object(backup, "_findmnt", side_effect=findmnt):
            board.set_setting("backup", str(self.disk))
            self.assertEqual(backup.state()["drive"], {"other": True, "uuid": "abc-1", "under": "copies"})
            self.where_now = str(self.base / "elsewhere")                # mounted somewhere else after a restart
            self.assertEqual(backup.where(), (self.base / "elsewhere" / "copies", ""), "found by its id, where it is now")
            self.where_now = ""
            with patch.object(backup.shutil, "which", side_effect=lambda name: None if name == "udisksctl" else "/usr/bin/" + name):
                self.assertEqual(backup.where(), (None, backup.AWAY))

    def test_one_copy_a_night_started_by_the_watcher(self):
        board.set_setting("backup", str(self.disk))
        night = time.mktime((2026, 11, 15, 3, 30, 0, 0, 0, -1))
        self.assertTrue(backup.due(night + 60), "a folder just chosen gets its first copy at once")
        with patch.object(backup.subprocess, "Popen") as began, patch.object(backup, "running", return_value=False):
            self.assertTrue(backup.tick())
            self.assertFalse(backup.tick(), "tried tonight: not again")
            self.assertEqual(began.call_count, 1)
            self.assertEqual(began.call_args[0][0][-2:], ["backup", "--now"])
        tried = backup.state()["tried"]
        self.assertFalse(backup.due(tried + 60))
        next_night = time.mktime(time.localtime(tried)[:3] + (3, 30, 0, 0, 0, -1)) + (86400 if time.localtime(tried)[3:5] >= (3, 30) else 0)
        self.assertFalse(backup.due(next_night - 60), "not before the hour")
        self.assertTrue(backup.due(next_night + 60), "the next night, or whenever the machine is next on after it")
        backup._keep(tried=0)
        with patch.object(backup, "start") as began, patch.object(backup, "running", return_value=True):
            self.assertFalse(backup.tick(), "never two at once")
            began.assert_not_called()

    def test_the_newest_four_are_kept_and_the_oldest_make_room(self):
        board.set_setting("backup", str(self.disk))
        with patch.object(migrate, "pack", side_effect=self.packs):
            for _ in range(6):
                self.assertEqual(backup.run(say=lambda *a: None), 0)
        names = [a.name for a in backup.archives(self.disk)]
        self.assertEqual(names, [f"colony-2026-11-15-03{n:02d}.tar.zst" for n in (6, 5, 4, 3)])
        self.assertEqual(len(list(self.disk.glob("*.manifest.json"))), 4, "a dropped archive's manifest goes with it")
        self.assertEqual(backup.state()["last"]["archive"], str(self.disk.resolve() / "colony-2026-11-15-0306.tar.zst"))
        self.assertIn("last copy", backup.line())
        self.assertIn("Copy now", board.backup_card())
        full = iter([migrate.NoRoom("colony: no room"), migrate.NoRoom("colony: no room")])

        def tight(dest, say=print):
            for stop in full:
                raise stop
            return self.packs(dest, say)
        with patch.object(migrate, "pack", side_effect=tight):
            self.assertEqual(backup.run(say=lambda *a: None), 0)
        self.assertEqual([a.name[-11:-8] for a in backup.archives(self.disk)], ["307", "306", "305"],
                         "short of room, the two oldest went for tonight's")

    def test_a_copy_not_made_is_told_once_for_its_reason_and_never_costs_the_only_archive(self):
        board.set_setting("backup", str(self.disk))
        with patch.object(migrate, "pack", side_effect=self.packs):
            backup.run(say=lambda *a: None)
        with patch.object(migrate, "pack", side_effect=migrate.NoRoom("colony: the disk has 1 GB free")):
            self.assertEqual(backup.run(say=lambda *a: None), 1)
            self.assertEqual(backup.run(say=lambda *a: None), 1)
        self.assertEqual(len(backup.archives(self.disk)), 1, "the only copy there is stays")
        told = [n["text"] for n in board.notices()]
        self.assertEqual(len(told), 1, "the second night's is the same reason: not told again")
        self.assertIn("wasn't made: the disk has 1 GB free. Settings → Backup", told[0])
        self.assertIn("not made since: the disk has 1 GB free", backup.line())
        self.assertIn("The last copy wasn't made:", board.backup_card())
        with patch.object(migrate, "pack", side_effect=RuntimeError("read error")):
            backup.run(say=lambda *a: None)
        self.assertEqual(len(board.notices()), 2, "another reason is told")
        with patch.object(migrate, "pack", side_effect=self.packs):
            self.assertEqual(backup.run(say=lambda *a: None), 0)
        self.assertNotIn("failed", backup.state(), "a copy made clears it")


if __name__ == "__main__":
    unittest.main()
