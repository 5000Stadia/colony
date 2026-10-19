"""Remote access through Tailscale: the board reached from the person's own devices, anywhere, and from no one else.

Tailscale puts this machine and the person's phone on a private encrypted network only their devices join.
Its addresses are private: no one outside the person's own logged-in devices can reach them. While it's on
(settings `tailscale`, this machine's name there), the board answers only this machine and those devices, and
refuses everything else, the open home network included. Nothing is made public and no port is opened.
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


# The addresses Tailscale gives the person's devices (its shared address range, and its IPv6 prefix).
RANGES = ("100.64.0.0/10", "fd7a:115c:a1e0::/48")


def own_device(ip):
    """Whether a request comes from this machine or one of the person's devices on their Tailscale network. A device
    on the open home network (or anywhere else) is neither."""
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip.removeprefix("::ffff:"))
    except ValueError:
        return False
    return addr.is_loopback or any(addr in ipaddress.ip_network(r) for r in RANGES)
