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


# --- HTTPS through Tailscale Serve: a real certificate for this machine's tailnet name, issued by Tailscale (Let's
# Encrypt), proxied to a port on this machine, so a phone gets a secure context (a camera, the clipboard) with no
# warning and nothing installed. Reached only from the person's own devices, as the rest. The tailnet's certificates
# are the person's to turn on (its admin: DNS > HTTPS Certificates); colony serves once they are. The board at 443;
# a project that serves, on a port of its own from FIRST (colony serve). The config is tailscaled's, kept across
# restarts (--bg). R119; the person, via Bookflow: "Yes: turn it on, Colony builds shared https serving".
FIRST = 8443                 # a project's first https port; the board's is 443


def certs():
    """Whether the tailnet issues HTTPS certificates to this machine."""
    if not shutil.which("tailscale"):
        return False
    try:
        return bool(json.loads(_run("status", "--json").stdout or "{}").get("CertDomains"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def served():
    """{https port: local port} of what Tailscale Serve proxies here now."""
    import re
    try:
        conf = json.loads(_run("serve", "status", "--json").stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    out = {}
    for where, web in (conf.get("Web") or {}).items():
        proxy = ((web.get("Handlers") or {}).get("/") or {}).get("Proxy", "")
        local = re.search(r":(\d+)/?$", proxy)
        if local and ":" in where:
            out[int(where.rsplit(":", 1)[1])] = int(local.group(1))
    return out


def serve(https, local):
    """Serve this machine's local port at https://<its tailnet name>:<https>/, kept across restarts. The error, or None."""
    try:
        r = _run("serve", "--bg", f"--https={https}", f"http://127.0.0.1:{local}")
    except (OSError, subprocess.TimeoutExpired) as err:
        return str(err)
    return None if r.returncode == 0 else (r.stderr or r.stdout).strip()[:300]


def unserve(https):
    try:
        _run("serve", f"--https={https}", "off")
    except (OSError, subprocess.TimeoutExpired):
        pass


def url(https):
    name = status().get("name", "")
    return f"https://{name}/" if https == 443 else f"https://{name}:{https}/"
