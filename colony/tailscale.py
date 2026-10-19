"""Remote access through Tailscale: the board reached from the person's own devices, anywhere, and from no one else.

Tailscale puts this machine and the person's phone on a private encrypted network only their devices join.
`tailscale serve` hands that network's HTTPS address for this machine to the board on 127.0.0.1, so the board
itself answers nothing but this machine: nothing is made public, no port is opened, and the open home network
no longer reaches it. The board accepts that one address (settings `tailscale`) as its own page.
"""
import json
import shutil
import subprocess

INSTALL = """Tailscale isn't installed here. To reach the board from your phone, anywhere, and from no one else:
  1. On this machine: curl -fsSL https://tailscale.com/install.sh | sh
  2. Then: sudo tailscale up --operator=$USER   (log in with the account you'll use on your phone)
  3. On your phone: install the Tailscale app and log in with the same account.
  4. Then: colony remote"""

LOGIN = """Tailscale is installed but not connected. Run:
  sudo tailscale up --operator=$USER
and log in (the same account as on your phone), then: colony remote"""


def _run(*args, timeout=30):
    return subprocess.run(["tailscale", *args], capture_output=True, text=True, timeout=timeout)


def status():
    """{"installed", "running", "name" (this machine's address on the private network), "ip"}."""
    if not shutil.which("tailscale"):
        return {"installed": False, "running": False, "name": "", "ip": ""}
    try:
        r = _run("status", "--json")
        data = json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        data = {}
    me = data.get("Self") or {}
    return {"installed": True, "running": data.get("BackendState") == "Running",
            "name": (me.get("DNSName") or "").rstrip("."), "ip": (me.get("TailscaleIPs") or [""])[0]}


def serve(port):
    """Hand this machine's private HTTPS address to the board on this machine; (ok, what tailscale said).
    Before Serve is enabled for the network, tailscale prints the link to enable it and waits: that link is what
    the person needs, so a wait ends with what it printed."""
    try:
        r = _run("serve", "--bg", f"http://127.0.0.1:{port}", timeout=20)
    except subprocess.TimeoutExpired as e:
        said = "".join(x.decode() if isinstance(x, bytes) else x for x in (e.stdout or "", e.stderr or "")).strip()
        return False, said or "tailscale serve didn't answer"
    except OSError as e:
        return False, str(e)
    said = (r.stdout + r.stderr).strip()
    return r.returncode == 0, said


def unserve():
    try:
        r = _run("serve", "--https=443", "off")
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout + r.stderr).strip()
