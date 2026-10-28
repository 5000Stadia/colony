"""Colony starting with the machine: a systemd user service that runs `colony board` at boot, before anyone logs in
where the account lingers, on the port it had last. What was running when the machine went down then comes back, each
console in its own conversation (recovery.rebooted). The person, 2026-10-28: "because of the nature of colony if the
system is restarted, I want colony to automatically load." Linux with systemd; elsewhere it says it can't.
"""
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

def unit_name():
    """colony.service for the person's own board; another home's (a test's, a demo's) its own, so neither touches the
    other's, as their tmux sessions don't (board.scoped)."""
    from . import board
    return board.scoped("colony") + ".service"


def path():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / unit_name()


def available():
    """Whether this machine runs systemd, whose user services can start colony."""
    return bool(shutil.which("systemctl")) and Path("/run/systemd/system").is_dir()


def search_path():
    """The PATH a service needs to find claude and codex: the person's own, as their login shell sets it (not what a
    program running colony added for itself, such as Codex's temporary directories), each folder once, only those there."""
    try:
        said = subprocess.run(["bash", "-lic", 'printf "\\n%s" "$PATH"'], capture_output=True, text=True, timeout=15,
                              stdin=subprocess.DEVNULL).stdout.rsplit("\n", 1)[-1]
    except (OSError, subprocess.SubprocessError):
        said = ""
    folders = (said or os.environ.get("PATH", "")).split(":")
    return ":".join(f for f in dict.fromkeys(folders) if f and os.path.isdir(f))


def unit():
    """The service, as colony runs here now: this checkout, this Python, the PATH it finds its programs on (a service
    starts with a bare one, where neither claude nor codex would be found), and its own home if it has one."""
    here = Path(__file__).resolve().parent.parent
    home = os.environ.get("COLONY_BOARD_HOME")
    return ("[Unit]\n"
            "Description=colony: the board, then its steward and the projects' consoles\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            "RemainAfterExit=yes\n"
            "# the board's tmux session, and the consoles in it, outlive the start: stopping this ends none of them\n"
            "KillMode=process\n"
            f"WorkingDirectory={here}\n"
            f"Environment=\"PATH={search_path()}\"\n"
            + (f"Environment=\"COLONY_BOARD_HOME={home}\"\n" if home else "")
            + f"ExecStart={sys.executable} -m colony board\n\n"
            "[Install]\n"
            "WantedBy=default.target\n")


def _systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def on():
    """Whether colony starts with the machine."""
    return available() and path().exists() and _systemctl("is-enabled", unit_name()).stdout.strip() == "enabled"


def lingers():
    """Whether this account's services start at boot, before anyone logs in (loginctl enable-linger)."""
    r = subprocess.run(["loginctl", "show-user", getpass.getuser(), "-p", "Linger", "--value"], capture_output=True, text=True)
    return r.stdout.strip() == "yes"


def turn(want):
    """Start with the machine, or no longer: the service written and enabled (not started: the board runs already), or
    disabled and removed. What it does now, in words."""
    if not available():
        raise RuntimeError("starting with the machine needs systemd (Linux)")
    p = path()
    if want:
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists() or p.read_text() != unit():
            p.write_text(unit())
        _systemctl("daemon-reload")
        r = _systemctl("enable", unit_name())
        if r.returncode:
            raise RuntimeError(f"systemctl --user enable {unit_name()}: {r.stderr.strip()}")
        return "colony starts with the machine" + ("" if lingers() else
                                                   " once you log in (loginctl enable-linger starts it at boot)")
    if p.exists():
        _systemctl("disable", unit_name())
        p.unlink()
        _systemctl("daemon-reload")
    return "colony no longer starts with the machine"
