"""The research room: one locked-down runner that every web research in colony goes through. Prompt in, text out.

A pass gets a generic prompt and nothing else of the project: it can search the web and read pages, and nothing more
(no shell, no edits, no local files, no MCP servers or logged-in connectors), held there by its program (the first
layer) and, on Linux where bubblewrap is installed, by an OS sandbox that shows it only its own empty workspace, its
program and that program's libraries, the network, and its login read-only (the second). Every pass keeps a folder
under the board's home, room/<run id>/, for audit: the prompt, a log.jsonl of every event and tool call, the raw
result, and the hand-off screen's report; folders older than 30 days are pruned as new ones start. A pass that runs
past its time limit is killed with its whole process group and reported "hung". What a pass hands back to an agent
with tools is screened first (screen.py), on a different model from the pass, unless its caller screens it itself.

    research(prompt, family=None, model=None, effort=None, timeout=1800, screen=True)
      -> {"text", "envelope", "report", "status": "done" | "held" | "hung" | "error", "run_dir", "error", ...}

What each program needs (its command, its login, how its events name tool calls, where its answer is) is its own,
in providers.py (room_command, room_login, room_events, room_result); everything else here is provider-agnostic.
"""
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import board, providers

TIMEOUT = 1800                     # a pass's time limit, in seconds
SCREEN_TIMEOUT = 600               # each call of the screen's model
KEEP_DAYS = 30
HOME_IN = "/room"                  # the workspace, inside the sandbox: its HOME and working folder
# The least of /etc a pass needs to resolve names and check certificates (each bound at its real path, if present).
ETC = ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/gai.conf", "/etc/ssl/certs/ca-certificates.crt")
# Passed into the sandbox when set (a network that needs a proxy); every other variable of the board's is left out.
PASS_ENV = ("LANG", "LC_ALL", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy")
URL = re.compile(r'https?://[^\s<>\[\]"\x27`\\]+', re.I)


def folder():
    return board.home() / "room"


def prune(now=None):
    """Remove run folders older than KEEP_DAYS."""
    cut = (now or time.time()) - KEEP_DAYS * 86400
    try:
        old = [d for d in folder().iterdir() if d.is_dir() and d.stat().st_mtime < cut]
    except OSError:
        return
    for d in old:
        shutil.rmtree(d, ignore_errors=True)


def bwrap():
    """bubblewrap's path where the OS layer can run (Linux, installed, not turned off by COLONY_ROOM_SANDBOX=none);
    else None, and a pass runs on its program's restriction alone."""
    if not sys.platform.startswith("linux") or os.environ.get("COLONY_ROOM_SANDBOX") == "none":
        return None
    return shutil.which("bwrap")


def runtime(program):
    """The files a program needs to start, as (real path on this machine, path inside the sandbox): itself, its
    interpreter if it is a script, and the shared libraries each loads. Nothing but these."""
    real = os.path.realpath(shutil.which(program) or program)
    found = [(real, real)]
    try:
        with open(real, "rb") as fh:
            head = fh.read(256)
    except OSError:
        return found
    if head.startswith(b"#!"):
        words = head[2:].split(b"\n", 1)[0].decode(errors="replace").split()
        interp = words[0] if words else ""
        if os.path.basename(interp) == "env" and len(words) > 1:
            interp = shutil.which(words[-1]) or interp
        named = [(os.path.realpath(interp), interp)] if interp and os.path.realpath(interp) != interp else []
        found += [b for b in runtime(interp) + named if b not in found]     # at the path its first line names, too
        return found
    try:
        listed = subprocess.run(["ldd", real], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        listed = ""
    for path in re.findall(r"(/\S+) \(0x", listed):
        pair = (os.path.realpath(path), path)
        if pair not in found:
            found.append(pair)
    return found


def sandbox_args(bw, workspace, program, login_files=(), env=None):
    """bubblewrap's arguments for one pass, up to the command: every namespace unshared but the network; a tmpfs
    /tmp, /proc and /dev; the program and its libraries read-only; the least of /etc; the workspace, writable, as
    HOME and the working folder; the login's files read-only under it; and no variable of the board's environment.
    It never binds / or any folder of the person's: only the files named."""
    args = [bw, "--unshare-all", "--share-net", "--die-with-parent", "--new-session", "--clearenv",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    binds = list(runtime(program)) + [(os.path.realpath(e), e) for e in ETC if os.path.exists(e)]
    for src, dest in binds:
        args += ["--ro-bind", src, dest]
    args += ["--bind", str(workspace), HOME_IN]
    for src, rel in login_files:
        args += ["--ro-bind", str(src), f"{HOME_IN}/{rel}"]
    for k, v in {"HOME": HOME_IN, "PATH": f"{os.path.dirname(os.path.realpath(shutil.which(program) or program))}:/usr/bin:/bin",
                 "TMPDIR": "/tmp", "TERM": "dumb", **{k: os.environ[k] for k in PASS_ENV if os.environ.get(k)},
                 **(env or {})}.items():
        args += ["--setenv", k, v]
    args += ["--chdir", HOME_IN, "--"]
    sources = [args[i + 1] for i, a in enumerate(args) if a in ("--ro-bind", "--bind")]
    dests = [args[i + 2] for i, a in enumerate(args) if a in ("--ro-bind", "--bind")]
    if "/" in sources or "/" in dests:
        raise ValueError("the room never binds / as a whole")
    return args


class _Log:
    """The run's log.jsonl, one event to a line, written as it happens (so a hung pass shows how far it got)."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()

    def write(self, **row):
        with self.lock, open(self.path, "a") as fh:
            fh.write(json.dumps(dict(at=round(time.time(), 3), **row), ensure_ascii=False) + "\n")


def _redacted(args):
    """A command for the log, with every value set in the sandbox's environment left out (a token among them)."""
    return [("<set>" if i and args[i - 2] == "--setenv" else a) for i, a in enumerate(args)]


def _pass(p, prompt, model, effort, here, timeout, tools=True, spawn=None):
    """One pass of program p in the folder here; {"status", "text", "usage", "error", "tools", "refused", "urls",
    "sandbox"}."""
    here = Path(here)
    work = here / "work"
    work.mkdir(parents=True, exist_ok=True)
    (here / "prompt.txt").write_text(prompt)
    log = _Log(here / "log.jsonl")
    bw = bwrap() if p.room_sandbox else None
    sandbox = "bwrap" if bw else "none" if p.room_sandbox else "its own"
    inner = p.room_command(model, effort, HOME_IN if bw else work, here, tools=tools)
    if bw:
        login_env, login_files = p.room_login()
        cmd = sandbox_args(bw, work, inner[0], login_files, login_env) + [os.path.realpath(shutil.which(inner[0]) or inner[0]), *inner[1:]]
        env = {}
    else:
        login_env, login_files = p.room_login() if p.room_sandbox else ({}, [])
        cmd, env = inner, dict(os.environ, **login_env)
        env.pop("CLAUDECODE", None)
    log.write(event="start", sandbox=sandbox, tools=bool(tools), model=model, effort=effort, cmd=_redacted(cmd),
              login=[str(rel) for _, rel in login_files] + sorted(login_env))
    found = dict(events=[], tools=[], refused=[], urls=[])
    try:
        proc = (spawn or subprocess.Popen)(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, cwd=str(work), env=env, start_new_session=True)
    except OSError as err:
        log.write(event="end", status="error", error=str(err))
        return dict(status="error", text="", usage=None, error=f"{err.__class__.__name__}: {err}", tools=[], refused=[],
                    urls=[], sandbox=sandbox)

    def feed():
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    def read():
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                log.write(raw=line[:4000])
                continue
            if not isinstance(e, dict):
                continue
            found["events"].append(e)
            log.write(event=e)
            for c in p.room_events(e):
                if "tool" in c:
                    found["tools"].append(c)
                    log.write(tool=c["tool"], input=c.get("input"))
                elif "refused" in c:
                    found["refused"].append(c)
                    log.write(refused=c["refused"], input=c.get("input"))
                found["urls"] += c.get("urls") or []
                found["urls"] += URL.findall(json.dumps(c.get("input") or ""))

    errors = []
    threads = [threading.Thread(target=f, daemon=True) for f in (feed, read)]
    threads.append(threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True))
    for t in threads:
        t.start()
    status = None
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        status = "hung"
        try:
            os.killpg(proc.pid, signal.SIGKILL)       # the whole group: the sandbox and everything in it
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait()
    for t in threads:
        t.join(timeout=10)
    for f in (proc.stdout, proc.stderr):
        try:
            f.close()
        except (OSError, AttributeError):
            pass
    (here / "stderr.txt").write_text("".join(e or "" for e in errors))
    if status == "hung":
        last = found["tools"][-1] if found["tools"] else None
        why = (f"no answer in {timeout:g}s; {len(found['tools'])} tool calls logged"
               + (f", the last {last['tool']} {json.dumps(last.get('input'))[:200]}" if last else ""))
        r = dict(text="", usage=None, error=why)
    else:
        r = p.room_result(found["events"], here)
        if not r.get("error") and not r["text"].strip():
            r["error"] = "an empty answer"
        if r.get("error") and not found["events"]:   # it never started: what it said on stderr
            said = "".join(e or "" for e in errors).strip().splitlines()
            r["error"] = (said[-1] if said else f"exit {proc.returncode}")[:300]
        status = "error" if r.get("error") else "done"
    out = dict(status=status, text=r["text"] if status == "done" else "", usage=r.get("usage"), error=r.get("error"),
               tools=found["tools"], refused=found["refused"], urls=list(dict.fromkeys(found["urls"])), sandbox=sandbox)
    (here / "result.json").write_text(json.dumps(dict(out, raw=r["text"]), indent=1, ensure_ascii=False))
    log.write(event="end", status=status, error=out["error"], exit=proc.returncode)
    return out


def tier(family, role):
    """A program's model and effort at one of colony's tiers (the person's pick in Settings, else the Auto one)."""
    from . import selection
    selection.migrate()
    pins = board.registry()["settings"].get("helper_models") or {}
    t = selection.resolve(family, role, pins.get(selection.key(family, role)))
    return t["model"], t.get("effort")


def _unfence(reply):
    """A reply wrapped whole in one Markdown code fence, unwrapped; nothing else changed."""
    m = re.fullmatch(r"\s*```[a-zA-Z]*\n(.*?)\n?```\s*", reply, re.S)
    return (m[1] if m else reply).strip()


def screener(family, pass_model, run_dir, timeout=SCREEN_TIMEOUT, spawn=None):
    """The screen's model, as ask(prompt) -> reply: a pass with no tools at all, in the room, on a different model
    from the research pass: the rote tier, or chores where rote is the same model. It runs on Claude where Claude is
    usable (a trick aimed at one model rarely works on another); a program the person names in Settings
    (room_screen_provider) instead, for screening across programs."""
    from .screen import RunnerFailed
    named = board.registry()["settings"].get("room_screen_provider")
    fam = named if named in providers.PROVIDERS else "claude" if providers.usable(providers.PROVIDERS["claude"]) else family
    p = providers.PROVIDERS[fam]
    try:
        model, effort = tier(fam, "rote")
        if model == pass_model:
            model, effort = tier(fam, "chores")
    except ValueError as err:
        raise RunnerFailed(f"no model to screen with ({err})")
    if model == pass_model:                  # every cheap tier on the pass's model: the last other one it offers
        others = [m for m, _ in providers.available(p) if m != pass_model]
        if not others:
            raise RunnerFailed(f"the screen's model would be the pass's own ({model})")
        model, effort = others[-1], None
    calls = []

    def ask(q):
        from . import tokens
        calls.append(q)
        r = _pass(p, q, model, effort, Path(run_dir) / f"screen-{len(calls)}", timeout, tools=False, spawn=spawn)
        tokens.note(fam, r["usage"], "screen")
        if r["status"] != "done":
            raise RunnerFailed(f"the screen's model: {r['status']} ({r['error']})")
        return _unfence(r["text"])
    ask.model, ask.family = model, fam
    return ask


def research(prompt, family=None, model=None, effort=None, timeout=TIMEOUT, screen=True, spawn=None, kind="research",
             ask=None):
    """One pass in the room on a generic prompt (no project context belongs in it). family: the program (default:
    colony's); model and effort: default its routine tier. screen=False hands back the pass's text unscreened, for a
    caller that screens it itself; ask: the screen's model, as ask(prompt) -> reply (default: screener()).

    {"text": the screened text (or the pass's own, unscreened), "envelope": the text inside the screen's envelope
    ('' unscreened or held), "report": the screen's report (None unscreened), "status": "done", "held" (the screen
    held it), "hung" (killed at the time limit) or "error", "run_dir", "error", "tools": the tool calls logged,
    "refused": those the program refused, "sandbox": "bwrap", "none" or "its own", "family", "model"}."""
    from . import screen as screening
    from . import tokens
    family = family or providers.key(providers.of(None))
    if family not in providers.PROVIDERS:
        raise ValueError(f"{family} isn't one of colony's programs ({', '.join(providers.PROVIDERS)})")
    p = providers.PROVIDERS[family]
    if model is None:
        model, routine = tier(family, "routine")
        effort = effort or routine
    prune()
    run_dir = folder() / f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    run_dir.mkdir(parents=True)
    r = _pass(p, prompt, model, effort, run_dir, timeout, spawn=spawn)
    tokens.note(family, r["usage"], kind)            # a pass keeps no transcript: what it spent is noted here
    out = dict(text=r["text"], envelope="", report=None, status=r["status"], run_dir=str(run_dir), error=r["error"],
               tools=r["tools"], refused=r["refused"], sandbox=r["sandbox"], family=family, model=model)
    if r["status"] == "done" and screen:
        try:
            ask = ask or screener(family, model, run_dir, min(timeout, SCREEN_TIMEOUT), spawn)
        except screening.RunnerFailed as err:
            ask = _refuse(str(err))
        s = screening.screen(r["text"], sources=r["urls"], ask=ask)
        (run_dir / "screen.json").write_text(json.dumps(dict(s["report"], model=dict(
            s["report"]["model"], name=getattr(ask, "model", None))), indent=1, ensure_ascii=False))
        out.update(text=s["text"], envelope=s["envelope"], report=s["report"])
        if s["report"]["status"] == "held":
            out.update(status="held", error=f"the screen held it: {s['report'].get('error')}")
    (run_dir / "summary.json").write_text(json.dumps({k: v for k, v in out.items() if k not in ("text", "envelope")},
                                                     indent=1, ensure_ascii=False))
    return out


def _refuse(why):
    from .screen import RunnerFailed

    def ask(q):
        raise RunnerFailed(why)
    return ask
