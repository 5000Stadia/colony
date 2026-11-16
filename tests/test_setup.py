"""Setup without an interview (R166): what the person still has to choose stays on the front page until it is set or
skipped, each line saying what colony found on this machine; the drives a copy could go on are offered; reading aloud
is set up from Settings."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from colony import backup, board, voice

NONE = {"installed": False, "running": False, "name": "", "ip": ""}


class SetupTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        env = patch.dict(os.environ, {"COLONY_BOARD_HOME": str(self.base / "home")})
        env.start()
        self.addCleanup(env.stop)
        board.save_registry({"roots": [], "settings": {"messaging": False, "active_hours": "off", "lan": False}})
        self.says = {"tailscale": dict(NONE), "starts": False, "can_start": True}
        for name, value in (("machine_says", lambda fresh=False: self.says), ("reach", lambda saved=False: False)):
            p = patch.object(board, name, side_effect=value)
            p.start()
            self.addCleanup(p.stop)

    def keys(self):
        return [key for key, _, _ in board.setup_left()]

    def test_what_is_unset_stays_on_the_front_page_until_set_or_skipped(self):
        self.assertEqual(self.keys(), ["backup", "phone", "autostart"])
        page = board.home_list(board.registry())
        self.assertIn("Start here", page)
        self.assertIn("Left to set up", page)
        self.assertNotIn("set-backup", board.start_here(), "said once, on the card that outlasts the first project")
        plants = self.base / "plants"
        plants.mkdir()
        reg = board.registry()
        reg["projects"] = [str(plants)]
        board.save_registry(reg)
        page = board.home_list(board.registry())
        self.assertNotIn("Start here", page)
        self.assertIn("Left to set up", page, "it stays once a project is there")
        board.set_setting("backup", str(self.base / "disk"))
        self.assertEqual(self.keys(), ["phone", "autostart"], "set: gone")
        board.setup_skip("phone")
        self.assertEqual(self.keys(), ["autostart"], "skipped: gone")
        board.setup_skip("nothing-of-colonys")
        self.assertEqual(board.registry()["setup_skipped"], ["phone"])
        self.says["starts"] = True
        self.assertEqual(board.setup_card(), "", "nothing left: no card")
        board.set_setting("backup", "off")
        self.assertEqual(self.keys(), ["backup"], "unset again, it is said again")
        board.setup_skip("backup")
        self.assertEqual(board.setup_card(), "")

    def test_the_cards_buttons_skip_a_line_and_start_colony_with_the_machine(self):
        import threading
        import urllib.parse
        import urllib.request
        from http.server import ThreadingHTTPServer
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        send = lambda **form: urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/setup", data=urllib.parse.urlencode(form).encode())).read().decode()
        self.assertIn("Left to set up", send(skip="phone"), "back on the front page")
        self.assertEqual(self.keys(), ["backup", "autostart"])
        turned = []
        with patch("colony.autostart.turn", side_effect=lambda on: turned.append(on) or self.says.update(starts=True)):
            send(do="autostart")
        self.assertEqual(turned, [True])
        self.assertEqual(self.keys(), ["backup"])

    def test_each_line_says_where_this_machine_stands(self):
        phone = lambda: dict((k, line) for k, line, _ in board.setup_left())["phone"]
        self.assertIn("isn&#x27;t installed here", phone())
        self.assertIn("curl -fsSL https://tailscale.com/install.sh", board.phone_card(), "the steps, in Settings")
        self.says["tailscale"] = dict(NONE, installed=True)
        self.assertIn("installed here but not signed in", phone())
        self.says["tailscale"] = dict(NONE, installed=True, running=True, name="box.tail1.ts.net")
        self.assertIn("connected here as box.tail1.ts.net", phone())
        self.assertIn("colony remote", board.phone_card())
        reg = board.registry()
        reg["settings"]["tailscale"] = "box.tail1.ts.net"
        board.save_registry(reg)
        self.assertNotIn("phone", self.keys(), "remote access on: nothing left to say")
        self.assertEqual(board.phone_card(), "")
        self.says["can_start"] = False                     # a machine colony can't start with (no systemd)
        self.assertNotIn("autostart", self.keys())

    def test_the_other_drives_here_are_offered_for_the_copy(self):
        usb, locked, kept, spaced = (self.base / n for n in ("usb", "locked", "kept", "my drive"))
        for d in (usb, locked, kept / "colony", spaced):
            d.mkdir(parents=True)
        mounts = "\n".join(["/ ext4", "/snap/core/1 squashfs", "/boot/efi vfat", f"{usb} exfat", f"{locked} ext4",
                            f"{kept} ext4", str(spaced).replace(" ", "\\x20") + " ext4", f"{usb} exfat"]) + "\n"
        os.chmod(locked, 0o555)
        os.chmod(kept, 0o555)                              # its top isn't ours, the colony folder on it is
        self.addCleanup(lambda: [os.chmod(d, 0o755) for d in (locked, kept)])
        listed = subprocess.CompletedProcess([], 0, stdout=mounts, stderr="")
        with patch.object(backup.subprocess, "run", return_value=listed), \
                patch.object(backup, "_same_drive", side_effect=lambda p: str(p) == "/"), \
                patch.object(backup, "_dev", side_effect=lambda p: str(p)), \
                patch.object(backup.shutil, "which", return_value="/usr/bin/findmnt"):
            found = [path for path, _ in backup.places()]
            self.assertEqual(found, [str(usb / "colony"), str(kept / "colony"), str(spaced / "colony")],
                             "never colony's own drive, a system mount, or one it can't write to; each drive once")
            card = board.backup_card()
            self.assertIn(f"name='folder' value='{usb / 'colony'}'>Use ", card)
            board.set_setting("backup", str(usb / "colony"))
            self.assertNotIn(">Use ", board.backup_card(), "chosen: nothing more is offered")
        with patch.object(backup.shutil, "which", return_value=None):
            self.assertEqual(backup.places(), [], "a machine that can't say offers nothing")

    def test_reading_aloud_is_set_up_from_settings(self):
        self.assertFalse(voice.ready())
        self.assertIn("action='/voice/setup'", board.voice_setup_card())
        self.assertFalse(voice.setting_up())
        held = voice._setup_lock()
        self.assertTrue(voice.setting_up())
        self.assertIn("Setting up the voice now", board.voice_setup_card())
        said = []
        voice.setup(say=said.append)
        self.assertEqual(said, ["The voice is already being set up."], "never two at once")
        held.close()
        (voice.folder() / "setup.log").write_text("Installing kokoro-onnx…\nOSError: no route to host\n")
        self.assertIn("OSError: no route to host", board.voice_setup_card(), "a try that stopped says why")
        with patch.object(voice.subprocess, "Popen") as began:
            voice.start_setup()
            self.assertEqual(began.call_args[0][0][-2:], ["voice", "--setup"])
        self.assertEqual(voice.setup_failed(), "", "a new try clears the last one's words")


if __name__ == "__main__":
    unittest.main()
