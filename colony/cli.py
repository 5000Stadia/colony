"""colony — one board for all of a person's long projects: each project's agent in its live console, what it
waits on them for, its roadmap, and a steward that can act for them.

    colony track [PATH] [--private] put a project on the board (roadmap, notes, gates, delivery hooks); private,
                                    nothing of colony's enters its git history
    colony board [--port 8790] [--lan]  one page for all tracked projects, with each project's live console
    colony gate "QUESTION" [--item R4] [--why ...] [--default ...]   put a decision in the person's hands
    colony notes [R4]               open notes from the person (the hooks deliver them by themselves)
    colony noted ID "TEXT"          mark a note as acted on, with what was done
    colony vision [--history]       read this project's vision or its dated changes
    colony restart                  reload the board with its code as it is now (consoles keep running)
    colony stop                     end the board, the steward and every project console
    colony doctor [--tests]         is everything up and wired? what to do if not
    colony projects                 every project on the board, its console state and latest line
    colony rename "NAME" [--why W]  change this project's own name on the board
    colony send NAME "TEXT" [--ask] a message to another project's agent; --ask expects an answer
    colony reply ID "TEXT"          answer a message
    colony mail [--project NAME]    a project's mail, in and out
    colony peek NAME [-n 30]        a project console's last lines
    colony tell NAME "TEXT"         send a message into a project's console, as the person would
    colony choose NAME "OPTION"     pick an option a project is showing (trust question, permission prompt)
    colony new NAME [--in DIR]      create a project, put it on the board, start its console
    colony close NAME [--cancel]    close a project safely: it lands its work, then comes off the board (files and
                                    conversation kept; colony track reopens it where it stopped)
    colony archive NAME [--why W]   archive a project: its agent packages where it stands (ARCHIVE.md), then it
                                    comes off the board; reopened (colony track), its agent hears why
    colony nest [NAME [PROJECT...]] [--leave] [--vision --file PATH --words W --context C]
                                    nests: projects that share a purpose; a nest's shared vision is the why they serve
    colony consult R4 "QUESTION" --digest FILE   two fresh views at a decision costly to change
    colony scout "BRIEF"            (steward) a fresh scout that can only search and read the web; its report
    colony settings [KEY VALUE] [--project NAME]   global options, or one project's own
    colony urls                     every address the board can be opened at
    colony remote [--check|--off]   the board from your own devices, anywhere, through Tailscale (no one else)
    colony setup                    the steward walks you through first-time setup (again)
    colony migrate --list | --pack DIR | --unpack ARCHIVE [--force]   carry colony to a fresh install: what it takes, in
                                    one archive, and back in place there as if it had just restarted
    colony bench [card MODEL | discover | fetch | key]   benchmark cards; discover asks each program its models;
                                    fetch pulls Artificial Analysis' data (API, then model pages); key reads the key from stdin
    colony models [set TIER MODEL EFFORT | reset TIER]  this project's helper tiers (routine, step-up, chores, rote)
    colony helm [on|off]            whether the steward answers routine questions for the person
    colony turbo [--nothing]        each program's pace this week, and what turbo is doing about it; --nothing (a
                                    project's agent): nothing worth doing, so no more from turbo until its roadmap changes
    colony suggest NAME "TEXT"      (steward) your own suggestion to a project's agent, for it to weigh
    colony steward COMMAND ...      the steward's commands under one name: mute, helm, posture, scout, said, suggest,
                                    decided, supports, setup (colony monitor ... is its old name and still works)

Run a project's commands from inside its folder.
"""
import argparse
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path


def _runnable(chosen):
    """Refuse a provider whose program isn't on this machine, before anything is made or wired."""
    from . import board, providers
    p = providers.get(chosen.get("provider") or board.registry()["settings"]["provider"])
    if not providers.usable(p):
        raise SystemExit(f"colony: {providers.unusable(p)}; choose another with --provider"
                         + ("" if providers.installed(p) else ", or install it"))


def cmd_track(a):
    from . import board
    if not Path(a.path).expanduser().is_dir():
        print(f"colony: no folder {a.path}", file=sys.stderr)
        return 2
    chosen = {k: getattr(a, k) for k in board.PROJECT_KEYS if getattr(a, k, None)}
    _runnable(chosen)
    if a.name and a.name != Path(a.path).expanduser().resolve().name:   # a second project in this folder
        if not a.role:
            raise SystemExit('This folder already has an agent. Choose --role lead to switch its lead, or --role helper for an assigned helper.')
        try:
            root = board.sharing(a.path, a.name, chosen, role=a.role)
        except (ValueError, KeyError) as err:
            raise SystemExit(f"colony: {err}")
        print(f"{root.name} is on the board, working in {board.workdir(root)}. Open it with: colony board")
        return 0
    if chosen:
        try:
            board.project_settings(Path(a.path).expanduser().resolve(), chosen)   # before wiring: the provider wires
        except KeyError as err:
            raise SystemExit(f"no such choice for {err}: colony settings shows the options")
    try:
        root = board.track(a.path, private=a.private)
    except ValueError as err:
        raise SystemExit(f"colony: {err}")
    print(f"{root.name} is on the board. Open it with: colony board")
    if board.is_private(root):
        for line in ["Private: colony's files stay out of its git history (listed in the repository's own .git/info/exclude)."] + board.exclude(root):
            print(line)
    return 0


def server():
    from . import board
    return board.scoped("board-server")


def _tmux_env():
    """tmux gives a new session the tmux server's environment, not ours: pass on what the board needs."""
    keep = ("COLONY_BOARD_HOME", "COLONY_CONSOLE_CMD", "PYTHONPATH", "PATH")
    return [x for k in keep if os.environ.get(k) for x in ("-e", f"{k}={os.environ[k]}")]


def _lan(a):
    from . import board
    return a.lan or (board.registry()["settings"]["lan"] and not a.local)


# Who the board answers, said the same by colony urls and colony doctor (and the README and the start script): this
# machine only unless asked, the person's phone through Tailscale or the Claude app. The person, Oct 28: "No: this
# machine only; the phone through Tailscale or the Claude app".
# A restart repeats how the running board was started: `colony board --lan` only says it is already running, and the
# lan setting was never read again. `colony settings lan on` saves the reach that the restart then takes up.
MACHINE_ONLY = ("this machine only; from your phone, Tailscale (colony remote, with HTTPS) or the Claude app; "
                "colony settings lan on, then colony restart, opens it to your home network")


def _reach(lan, private, port):
    """Who the running board answers, in a line."""
    if private:
        return f"this machine and your own devices through Tailscale (http://{private}:{port}/), not the home network"
    return "this machine and every device on your home network (--lan)" if lan else MACHINE_ONLY


def _server_args(a):
    return ["--port", str(a.port)] + (["--lan"] if _lan(a) else ["--local"]) + (["--no-monitor"] if a.no_monitor else [])


def _answers(port):
    """Is this board, not some other program, answering on the port?"""
    from . import board
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/status", headers={"Host": f"127.0.0.1:{port}"})
        return json.loads(urllib.request.urlopen(req, timeout=3).read()).get("board") == str(board.home())
    except Exception:
        return False


def _free(port, lan):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0" if lan else "127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _launch(args):
    """Start the board's server in its tmux session and wait until it answers; if it doesn't, say why."""
    from . import board
    log = board.home() / "server.log"
    log.write_text("")
    cmd = " ".join([shlex.quote(sys.executable), "-m", "colony", "board", "--foreground", *args])
    # Started from the board's own folder: from ~, a clone at ~/colony would shadow the installed package.
    subprocess.run(["tmux", "new-session", "-d", "-s", server(), "-c", str(board.home()), *_tmux_env(),
                    f"{cmd} 2>&1 | tee -a {shlex.quote(str(log))}"], check=True)
    port = args[args.index("--port") + 1]
    for _ in range(40):
        if _answers(port):
            return True
        if subprocess.run(["tmux", "has-session", "-t", server()], capture_output=True).returncode != 0:
            break
        time.sleep(0.25)
    subprocess.run(["tmux", "kill-session", "-t", server()], capture_output=True)
    print(f"colony: the board did not start on port {port}:\n" + "\n".join(log.read_text().splitlines()[-8:]),
          file=sys.stderr)
    return False


def cmd_board(a):
    """The board runs in its own tmux session, so it outlives the terminal that started it and the
    steward can restart it; --foreground runs it here instead."""
    from . import board, monitor
    fresh = not (board.home() / "board.json").exists() and not (board.home() / "setup-asked").exists()
    settled = board.settle_provider()            # the default runs on a program that is here
    if fresh and not a.no_monitor:
        board.home().mkdir(parents=True, exist_ok=True)
        monitor.setup()                          # a new install: the steward walks the person through setup
    if settled:
        print(settled)
    if a.foreground:
        board.serve(a.port or 8790, lan=_lan(a), monitor=not a.no_monitor)
        return 0
    saved = board.home() / "server.json"
    if subprocess.run(["tmux", "has-session", "-t", server()], capture_output=True).returncode == 0:
        print("the board is already running (colony urls shows where; colony restart reloads it)")
        return 0
    # The port asked for, else the one it had last time, else 8790; if another program holds it, the next free one.
    old = json.loads(saved.read_text()) if saved.exists() else []
    wanted = a.port or (int(old[old.index("--port") + 1]) if "--port" in old else 8790)
    a.port = next(p for p in range(wanted, wanted + 100) if _free(p, _lan(a)))
    if a.port != wanted:
        print(f"port {wanted} is taken by another program; using {a.port}")
    board.home().mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps(_server_args(a)))
    if not _launch(_server_args(a)):
        return 1
    print(f"the board is running (tmux session {server()}); open it at:")
    for u in board.urls(a.port, lan=_lan(a)):
        print(f"  {u}")
    return 0


def cmd_urls(a):
    """Every address the running board can be opened at: as it was started (board.reach), whatever the lan setting
    says now."""
    from . import board
    saved = board.home() / "server.json"
    args = json.loads(saved.read_text()) if saved.exists() else []
    port = int(args[args.index("--port") + 1]) if "--port" in args else a.port
    lan = board.reach()
    private = board.registry()["settings"].get("tailscale")
    print(f"On this machine:      http://127.0.0.1:{port}/")
    for u in (board.urls(port, lan=True)[1:] if lan and not private else []):
        print(f"From your network:    {u}")
    if private:
        print("From your network:    refused while remote access is on: your devices reach it through Tailscale")
    elif not lan and board.reach(saved=True):
        print("From your network:    off until colony restart, which opens it to your home network as now set")
    elif not lan:
        print(f"From your network:    off: {MACHINE_ONLY}")
    if private:
        for url in board.urls(port, lan=False):
            if url.startswith("https://"):
                print(f"From anywhere:        {url}   (your devices on Tailscale, and no one else)")
        print(f"From anywhere:        http://{private}:{port}/   (your devices on Tailscale, and no one else)")
    else:
        print("From anywhere:        off (colony remote sets it up through Tailscale: your devices only)")
    from . import providers
    # PROVIDER: each program's own app: Claude Code's Remote Control, Codex's pairing with ChatGPT (as last recorded:
    # listing addresses never makes a pairing code)
    used = {providers.key(providers.of(p)) for p in board.projects(board.registry()) if p.exists()}
    if board.registry()["settings"]["remote"] and "claude" in used and providers.installed(providers.get("claude")):
        print("From anywhere:        the Claude app, where each Claude Code session appears")
    if "codex" in used:
        from . import codex_remote
        mine = [p for p in board.projects(board.registry()) if p.exists() and providers.key(providers.of(p)) == "codex"]
        paired = [p.name for p in mine if codex_remote.pairing_choice(p) == "paired"]
        print(f"From anywhere:        the ChatGPT app, for each Codex project paired with it ("
              + (", ".join(paired) if paired else "none paired yet: a project's Settings connects it") + ")")
    return 0


def cmd_voice(a):
    """Replies read aloud in a natural voice made on this machine (Kokoro): --setup installs it, once."""
    from . import voice
    if a.setup:
        voice.setup()
        return 0
    print("Kokoro: " + ("ready; Chat reads replies in its voice" if voice.ready() else
                        "not installed; Chat uses the phone's own voice. `colony voice --setup` installs it (about 500 MB)"))
    return 0


def cmd_migrate(a):
    """Carry colony to a fresh install under the same username (colony/migrate.py): --list what a pack takes, --pack it
    into one archive, --unpack it on the new machine."""
    from . import migrate
    if a.list:
        migrate.listing()
    elif a.pack:
        migrate.pack(a.pack)
    else:
        migrate.unpack(a.unpack, force=a.force)
    return 0


def cmd_remote(a):
    """The board from the person's own devices, anywhere, through Tailscale; --off undoes it; --check says where it stands."""
    from . import board, tailscale
    st, settings = tailscale.status(), board.registry()["settings"]
    port = _running_port(a.port)
    if a.check:
        print(f"Tailscale: {'not installed' if not st['installed'] else 'connected as ' + st['name'] if st['running'] else 'not connected'}")
        private = settings.get("tailscale")
        print("The board through it: " + (f"http://{private}:{port}/" if private else "off"))
        return 0
    if a.off:
        before = settings.get("tailscale_lan_before")
        if before is not None:
            board.set_setting("lan", "on" if before else "off")
            _saved_reach(before)
        # The guard stays on until the board runs with the reach it had before: no moment answers the home network
        # by accident. If the restart fails, remote access stays on.
        restarted = _restart_board()
        if restarted.returncode:
            print("The board didn't restart, so remote access stays on: " + (restarted.stdout + restarted.stderr).strip()[-400:])
            return restarted.returncode
        reg = board.registry()
        reg["settings"]["tailscale"], reg["settings"]["tailscale_lan_before"] = "", None
        board.save_registry(reg)
        print("Remote access is off; the board answers as it did before.")
        return 0
    if not st["installed"]:
        print(tailscale.INSTALL)
        return 1
    if not (st["running"] and st["name"]):
        print(tailscale.LOGIN)
        return 1
    if not settings.get("tailscale"):            # what's running now, to put back with --off
        saved = board.home() / "server.json"
        running = json.loads(saved.read_text()) if saved.exists() else []
        reg = board.registry()
        reg["settings"]["tailscale_lan_before"] = "--lan" in running if running else bool(settings["lan"])
        board.save_registry(reg)
    board.set_setting("tailscale", st["name"])
    board.set_setting("lan", "on")              # it listens beyond this machine, and answers only your devices
    _saved_reach(True)
    restarted = _restart_board()
    from . import serving
    secure = serving.ensure_board(port)
    print(f"The board is reachable from your own devices, anywhere: " + (f"{secure} (or " if secure else "")
          + f"http://{st['name']}:{port}/" + (")" if secure else f" (or http://{st['ip']}:{port}/)")
          + "\nOpen it with the Tailscale app connected. Nothing is public: only devices "
          "logged in to your Tailscale account reach it, and the open home network no longer does."
          + ("" if secure else f"\nHTTPS: {serving.ready() or serving.trouble or 'not served yet'}."))
    if restarted.returncode:
        print("The board didn't restart cleanly: " + (restarted.stdout + restarted.stderr).strip()[-400:])
    return restarted.returncode


def _restart_board():
    return subprocess.run([sys.executable, "-m", "colony", "restart"], capture_output=True, text=True)


def _saved_reach(lan):
    """The running board's saved start: answering the home network or only this machine, for the next restart."""
    from . import board
    saved = board.home() / "server.json"
    if saved.exists():
        args = [x for x in json.loads(saved.read_text()) if x not in ("--lan", "--local")]
        saved.write_text(json.dumps(args + ["--lan" if lan else "--local"]))


def _settle_reach(key):
    """`colony settings lan on|off`, said in so many words, is how the board answers from its next restart, even
    where the setting already said so and the board was started otherwise (--local): `colony urls` tells the person
    to do just that."""
    from . import board
    if board.SETTING_ALIASES.get(key, key) == "lan":
        board.save_reach(board.registry()["settings"]["lan"])


def _running_port(default):
    from . import board
    saved = board.home() / "server.json"
    args = json.loads(saved.read_text()) if saved.exists() else []
    return int(args[args.index("--port") + 1]) if "--port" in args else default


def cmd_stop(a):
    """End the board, the steward and every project console this board started, and nothing else."""
    from . import board, console, monitor
    names = [server(), monitor.name()] + [console.session_name(p) for p in board.projects()]
    ended = [n for n in names if subprocess.run(["tmux", "kill-session", "-t", n], capture_output=True).returncode == 0]
    print(f"stopped {len(ended)} session(s): {', '.join(ended) or 'none were running'}")
    return 0


def cmd_restart(a):
    """Reload the board with its code as it is now. Project consoles and the steward keep running."""
    from . import board
    saved = board.home() / "server.json"
    args = json.loads(saved.read_text()) if saved.exists() else []
    if "--port" not in args:
        print("colony: the board has not been started here yet: colony board", file=sys.stderr)
        return 1
    subprocess.run(["tmux", "kill-session", "-t", server()], capture_output=True)
    for _ in range(20):                   # let the old server let go of the port
        if not _answers(args[args.index("--port") + 1]):
            break
        time.sleep(0.25)
    if not _launch(args):
        return 1
    print("the board restarted; consoles and the steward were not touched")
    return 0


def cmd_doctor(a):
    """Is everything up and wired? Prints what is wrong and what to do; exit 1 if anything is."""
    from . import board, console, monitor, providers
    import urllib.request
    problems = []
    here = [p for p in providers.PROVIDERS.values() if providers.installed(p)]
    for p in providers.PROVIDERS.values():
        print(f"{'ok  ' if p in here else 'note'}  {p.label} ({p.program}) {'installed' if p in here else 'not installed'}"
              + ("" if providers.enabled(p) else "; off in colony's Settings"))
        # what it does its own way and what it leaves to colony's default, read off its code (Provider)
        shapes = providers.shapes(p)
        left = [n for n, how in shapes if how == "colony's default"]
        print(f"      {p.label} supplies {sum(how == 'its own' for _, how in shapes)} of {len(shapes)} shapes"
              + (f"; colony's default for {', '.join(left)}" if left else ""))
        problems += [f"{p.label} doesn't supply {n}, which every program must (providers.Provider)"
                     for n, how in shapes if how == "missing"]
    for p in here:
        trouble = providers.enabled(p) and p.sandbox_problem()
        if trouble:
            problems.append(trouble)
    if not here:
        problems.append("no agent program is installed: Claude Code (https://claude.com/claude-code) or Codex "
                        "(https://developers.openai.com/codex)")
    saved = board.home() / "server.json"
    args = json.loads(saved.read_text()) if saved.exists() else []
    port = args[args.index("--port") + 1] if "--port" in args else "8790"
    if _answers(port):
        print(f"ok    board answers on port {port}: "
              + _reach(board.reach(), board.registry()["settings"].get("tailscale"), port))
    else:
        problems.append(f"the board does not answer on port {port}: colony board, or colony restart"
                        + ("" if _free(int(port), False) else " (another program holds that port: colony stop, then colony board)"))
    if "--no-monitor" not in args:
        (print("ok    steward session running") if monitor.snapshot()["state"] != "off"
         else problems.append("the steward session is not running: colony restart starts it"))
    for p in board.projects():
        if not p.exists():
            problems.append(f"{p}: the folder is gone; remove it from {board.home() / 'board.json'}")
            continue
        if not providers.installed(providers.of(p)):
            problems.append(f"{p.name}: {providers.missing(providers.of(p))}; install it, or choose another: "
                            f"colony settings --project {p.name} provider NAME")
        elif not (providers.of(p).wired(board.workdir(p), private=True) if board.is_private(p)
                  else providers.of(p).wired(board.workdir(p))):
            problems.append(f"{p.name}: its board wiring is missing; colony track {p} restores it")
        else:
            print(f"ok    {p.name}: wired; console {console.snapshot(p, lines=1)['state']}")
            if providers.key(providers.of(p)) == "codex":
                from . import codex_remote
                trouble = codex_remote.remote_problem(p, board.project_settings(p)[0])
                if trouble:
                    problems.append(f"{p.name}: no app access: {trouble}")
    try:
        from . import intelligence
        trouble, notes, summary = intelligence.health()
    except Exception as err:                 # the model data never stops the doctor
        trouble, notes, summary = [], [f"couldn't be read ({err.__class__.__name__}: {err})"], None
    if summary and not trouble:
        print(f"ok    model data: {summary}")
    for note in notes:
        print(f"note  model data: {note}")
    problems += [f"model data: {t}: colony's page reader (colony/intelligence.py, page_point) needs a look"
                 for t in trouble]
    from . import turbo
    for k, p in providers.PROVIDERS.items():
        if providers.usable(p):
            print(f"note  turbo: {turbo.line(k)}")
    from . import serving
    if board.registry()["settings"].get("tailscale"):
        why = serving.ready()
        secure = not why and serving.ensure_board(_running_port(8790))
        print(f"ok    https: {secure}" if secure else f"note  https: {why or serving.trouble or 'the board is not served yet'}")
    from . import autostart
    if autostart.available():
        print(f"{'ok  ' if autostart.on() else 'note'}  colony "
              + ("starts with the machine" + ("" if autostart.lingers() else ", once you log in") if autostart.on()
                 else "doesn't start with the machine: Settings › Start with the machine (colony settings autostart on)"))
    from . import lingering
    try:
        behind = lingering.left()
    except Exception:
        behind = []                                # what's left behind is a note: its trouble hides nothing else
    for g in behind:                               # what consoles that have ended still run (no tokens: /proc)
        whose = f"{g['root'].name}'s agent is told once" if g["root"] else "no project on the board: Stop it on All projects"
        print(f"note  left running by an ended console: {board.short(' '.join(g['what'].split()), 70)}"
              + (f" and {g['more']} more" if g["more"] else "") + f" ({g['rss'] // 2**20} MB"
              + (f", {', '.join(g['ports'])}" if g["ports"] else "") + f"; {whose})")
    if a.tests:
        home = Path(__file__).resolve().parent.parent
        r = subprocess.run([sys.executable, "-m", "unittest", "tests.test_board", "tests.test_selection", "tests.test_effort", "tests.test_codex_remote", "tests.test_vision", "tests.test_context", "tests.test_catalog", "tests.test_catalog_freshness", "tests.test_lead", "tests.test_continuation", "tests.test_turbo"], cwd=home,
                           capture_output=True, text=True)
        (print("ok    the test suite passes") if r.returncode == 0
         else problems.append("the test suite fails:\n" + r.stderr[-1500:]))
    for pr in problems:
        print("PROBLEM " + pr)
    return 1 if problems else 0


def cmd_gate(a):
    from . import board
    root = board.root_of()
    if a.withdraw:                       # it no longer applies: off the person's list, as the agent's own record
        try:
            board.withdraw_gate(root, a.withdraw, a.question)
        except StopIteration:
            print(f"no gate {a.withdraw}", file=sys.stderr)
            return 2
        except ValueError as err:
            print(str(err), file=sys.stderr)
            return 2
        print(f"gate {a.withdraw} withdrawn: {a.question}")
        return 0
    if a.answered:                       # the person settled it in conversation: record it, and it stops waiting
        try:
            if a.consult or a.points:
                raise ValueError("An answered gate already has its consultation and points; do not replace them.")
            if a.default:
                raise ValueError("A default goes with a new gate's question, not an answer.")
            decisions = {}
            for value, ids in (("accept", a.accept), ("reject", a.reject)):
                for ident in ids:
                    if ident in decisions:
                        raise ValueError("Give each consultant point exactly one decision.")
                    decisions[ident] = value
            text = a.question + (f"\nReading: {a.context.strip()}" if a.context and a.context.strip() else '')
            board.answer_gate(root, a.answered, text, tell=False, decisions=decisions or None)
        except StopIteration:
            print(f"no gate {a.answered}", file=sys.stderr)
            return 2
        except ValueError as err:
            print(str(err), file=sys.stderr)
            return 2
        print(f"gate {a.answered} answered: {a.question}")
        return 0
    try:
        if a.accept or a.reject:
            raise ValueError("Use --answered to record the person's point decisions.")
        if bool(a.consult) != bool(a.points):
            raise ValueError("Use --consult ID and --points FILE together.")
        points = json.loads(sys.stdin.read() if a.points == "-" else Path(a.points).read_text()) if a.points else None
        gate = board.add_gate(root, a.question, a.item, a.why, consultation=a.consult, points=points, when=a.when,
                              default=a.default, options=a.option, recommended=a.recommended, reframes=a.reframes,
                              told=a.told or "")
    except (ValueError, OSError) as err:
        print(str(err), file=sys.stderr)
        return 2
    gid = gate["id"]
    if a.reframes:                       # the old question comes off their list: this one says it whole
        try:
            board.withdraw_gate(root, a.reframes, f"Asked again as {gid}, with what it was missing")
        except (StopIteration, ValueError):
            pass
    if a.when and next(g for g in board.gates(root) if g["id"] == gid).get("waits_for"):
        print(f"gate {gid} waits until {a.when} starts, off the person's list; carry on meanwhile")
        return 0
    from . import hours
    # A default stands only for a decision the person skips: until then, the gate is theirs like any other.
    skipped = (f" If they skip it, its default stands at {hours.at_day(board.stands_at(root, gate))} and arrives as their "
               "answer." if gate.get("default") else "")
    from . import lead, continuation
    # Only the member at work on the gate's item hears of it, on its next turn: no one is woken for it.
    job = lead.info(root)['assignments'].get(a.item) if a.item else None
    owner = Path(job['owner']) if job and job['state'] in continuation.ASSIGNMENT_ACTIVE else None
    if owner and owner != Path(root).resolve() and owner.exists():
        board.add_note(owner, None, f"Costly decision for {a.item}: {a.question}. Reason: {a.why}. "
                       f"Gate {gid} is owned by {root.name}; keep work affected by it stopped.", author='colony', quiet=True)
    if gate.get("held_until"):
        # Outside the person's active hours it isn't their question yet, so it holds back only what depends on it:
        # the version carries on meanwhile, and stops for the answer once the hours begin and the gate is theirs.
        print(f"gate {gid} is held for the person's active hours: it reaches them with any others at "
              f"{hours.at_clock(board.epoch(gate['held_until']))}. Don't proceed on it until the answer arrives as a note; "
              f"meanwhile keep on with what doesn't depend on it.{skipped}")
        return 0
    continuation.tick(root, forced_hold='blocking decision')
    print(f"gate {gid} is waiting on the person; do not proceed on it until the answer arrives as a note.{skipped}")
    return 0


def cmd_consult(a):
    """Consult two fresh models from different families at a decision costly to change; or record which of their
    points the person accepted; or show a consultation again."""
    from . import board, consult
    root = board.root_of()
    if a.adopt:
        try:
            consult.adopt(root, a.adopt, a.decision + (f"\nReading: {a.context.strip()}" if a.context and a.context.strip() else ''))
        except KeyError:
            print(f"no consultation {a.adopt}", file=sys.stderr)
            return 2
        except ValueError as err:
            print(str(err), file=sys.stderr)
            return 2
        print(f"recorded for {a.adopt}: {a.decision}")
        return 0
    if a.show:
        rec = next((r for r in consult.records(root) if r["id"] == a.show), None)
        if not rec:
            print(f"no consultation {a.show}", file=sys.stderr)
            return 2
        print(consult.report(rec))
        return 0
    if not a.question or not a.digest:
        raise SystemExit("colony consult DECISION \"QUESTION\" --digest FILE (- for stdin); see colony consult -h")
    read = lambda f: sys.stdin.read() if f == "-" else Path(f).expanduser().read_text()
    try:
        rec = consult.run(root, a.decision, a.question, read(a.digest), read(a.plan) if a.plan else None,
                          2 if a.plan else 1)
    except (ValueError, OSError) as err:
        print(err, file=sys.stderr)
        return 2
    print(consult.report(rec))
    return 0


def cmd_scout(a):
    """(steward) One fresh scout on a brief, apart from you: it can only search and read the web; prints its report."""
    from . import monitor
    try:
        text = (Path(a.file).expanduser().read_text() if a.file and a.file != "-" else a.brief if a.brief not in (None, "-")
                else "" if sys.stdin.isatty() else sys.stdin.read())
        if not text.strip():
            raise SystemExit('colony scout "the brief" (or the brief on stdin, or --file FILE)')
        print(monitor.scout(text, a.provider))
    except (ValueError, OSError) as err:
        print(f"colony scout: {err}", file=sys.stderr)
        return 2
    return 0


def cmd_statusline(a):
    """(Claude Code's status line) Record the usage limits Claude Code hands its status line, then show the
    person's own status line if they set one, else a short line of their own usage."""
    from . import usage
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        payload = {}
    try:
        usage.record_claude(payload)
        from . import board, context
        context.usage(board.root_of(), payload)
    except OSError:
        pass
    from .providers import get
    try:
        own = (json.loads((get("claude").config_home() / "settings.json").read_text()).get("statusLine") or {}).get("command")
    except (OSError, ValueError, AttributeError):
        own = None
    if own and "colony statusline" not in own:
        r = subprocess.run(own, shell=True, input=raw, capture_output=True, text=True, timeout=10)
        print(r.stdout.rstrip("\n"))
        return 0
    model = (payload.get("model") or {}).get("display_name") or ""
    print(" · ".join(filter(None, [model, usage.line("claude")])))
    return 0


def cmd_pin(a):
    """Pin something for the person: a file in the project or a URL, shown at the top of its page."""
    from . import board, pins
    root = board.root_of()
    try:
        pin = pins.add(root, a.target, a.title or "", a.why or "", by="agent", group=a.group or "")
    except FileNotFoundError:
        print(f"no file {a.target} in this project", file=sys.stderr)
        return 2
    print(f"pinned {pin['id']}: {pin['title']}" + (f" under \"{pin['group']}\"" if pin.get("group") else ""))
    # the headings in use, so the next pin can go under one of them (before this, an agent had to read pins.jsonl)
    print("Headings in use: " + _headings(pins.pins(root)))
    return 0


def _headings(kept):
    """The Library's headings in use, each with how many it holds; those under none, counted last."""
    counts = {}
    for p in kept:
        counts[p.get("group") or ""] = counts.get(p.get("group") or "", 0) + 1
    named = [f"\"{g}\" ({n})" for g, n in counts.items() if g]
    return ", ".join(named + ([f"under none ({counts['']})"] if "" in counts else [])) or "none yet"


def cmd_show(a):
    """Put a deliverable in the project's showcase for the person to see and comment on, if they like."""
    from . import board, showcase
    root = board.root_of()
    dead = _dead_link(a.link or "")
    if dead:
        print(f"colony: {a.link} doesn't open ({dead}); the person would tap a dead link. Start it, or show it once it runs.",
              file=sys.stderr)
        return 2
    try:
        x = showcase.add(root, a.title, a.what, a.try_ or "", a.link or "", a.item)
    except FileNotFoundError:
        print(f"no file {a.link} in this project", file=sys.stderr)
        return 2
    except ValueError as err:
        print(f"colony: {err}", file=sys.stderr)
        return 2
    print(f"shown {x['id']}: {x['title']} (the person may comment; it arrives as a note)")
    before = showcase.earlier(root, x)
    if before:                       # a new version of a deliverable: one card, the earlier showings kept under it
        print(f"It becomes the card for {x['link']}, its earlier showings ({len(before)}) folded under it, each kept with its "
              "date, story and comments.")
    return 0


def _dead_link(link, timeout=8):
    """Why a web link doesn't open, or "" if it does (or isn't a web link)."""
    import urllib.error
    import urllib.request
    if not link.startswith(("http://", "https://")):
        return ""
    try:
        with urllib.request.urlopen(urllib.request.Request(link, headers={"User-Agent": "colony-check"}), timeout=timeout):
            return ""
    except urllib.error.HTTPError as err:
        return "" if err.code in (401, 403) else f"it answers {err.code}"     # behind a login is still there
    except (urllib.error.URLError, OSError, ValueError) as err:
        return str(getattr(err, "reason", err))[:120]


def _uncommitted(root):
    """Tracked files with changes not committed, in the project's git (none if it isn't one)."""
    import subprocess
    r = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=str(root), capture_output=True, text=True)
    return [l[3:] for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []


def cmd_hint(a):
    """The plan's shape hints: the open ones, or settle one with your reasoning (quiet until the vision changes)."""
    from . import board, hints
    root = board.root_of()
    if a.action == "keep":
        if not a.key or not a.why:
            print('usage: colony hint keep KEY "why its shape stays"', file=sys.stderr)
            return 2
        hints.keep(root, a.key, a.why)
        print(f"settled {a.key}: not raised again until the vision changes")
        return 0
    for key, _, text in hints.open_hints(root):
        print(f"{key}: {text}")
    return 0


def cmd_regroup(a):
    """Organize the Library: one entry under a heading, or every entry under one heading renamed to another."""
    from . import board, pins
    root = board.root_of()
    if a.rename:
        moved = [p for p in pins.pins(root) if (p.get("group") or "") == a.target]
        for p in moved:
            pins.regroup(root, p["id"], a.heading)
        print(f"renamed '{a.target}' to '{a.heading}' ({len(moved)} entries)")
        return 0
    if not pins.get(root, a.target):
        print(f"no Library entry {a.target}", file=sys.stderr)
        return 2
    pins.regroup(root, a.target, a.heading)
    print(f"{a.target} is now under '{a.heading}'")
    return 0


def cmd_unpin(a):
    from . import board, pins
    root = board.root_of()
    if not pins.get(root, a.id):
        print(f"no pin {a.id}", file=sys.stderr)
        return 2
    pins.archive(root, a.id, a.why or "", by="agent")
    print(f"archived {a.id}: folded away in the Library's archive, out of your context")
    return 0


def cmd_bench(a):
    """The benchmark cards: a summary of the lineup, one model's card, or records researched for a new model."""
    from . import bench
    if a.what == "import":
        rows = [json.loads(l) for l in Path(a.arg).read_text().splitlines() if l.strip()]
        added, bad = bench.add(rows)
        if added:
            from . import selection
            selection.reconcile()
        print(f"{added} new record(s) kept; {len(rows) - added - len(bad)} already kept")
        for line, wrong in bad:
            print(f"line {line} skipped: {'; '.join(wrong)}", file=sys.stderr)
        return 1 if bad else 0
    if a.what == "key":                     # from stdin, so it never sits in shell history
        bench.set_key(sys.stdin.read())
        print("Artificial Analysis key " + ("saved" if bench.aa_key() else "removed"))
        return 0
    if a.what == "fetch":
        out = bench.refresh()
        from . import intelligence, selection
        pages = intelligence.refresh() if intelligence.refresh_due() else None     # the volumes, read with the prices
        selection.reconcile()
        if pages:
            print(f"{pages['measured']} of {pages['pages']} model pages read"
                  + (f"; {len(pages['errors'])} failed, the first: {pages['errors'][0]['detail']}" if pages["errors"] else ""))
        if out["error"]:
            print(f"colony: {out['error']}", file=sys.stderr)
            return 1
        print(f"{out['models']} models from Artificial Analysis; {out['added']} new record(s); matched: "
              f"{', '.join(out['matched']) or 'none'}" + (f"; not in their data: {', '.join(out['missing'])}" if out["missing"] else ""))
        return 0
    if a.what == "discover":
        from . import providers
        looked = providers.discover(force=True)
        for k in looked:
            ms = providers.available(providers.get(k))
            print(f"{providers.get(k).label}: {', '.join(m for m, _ in ms) or 'none confirmed'}")
        return 0
    if a.what == "pending":
        print("\n".join(bench.pending()) or "no card waits for independent scores")
        return 0
    if a.what == "card":
        c = bench.card(a.arg)
        print(f"{c['name']} ({c['model']})" + ("  PENDING: no independent overall score yet" if c["pending"] else ""))
        for e in c["entries"]:
            doms = ", ".join(f"{d} {s}" for d, s in e["domains"].items() if d != "overall")
            head = f"overall {e['overall']} ({', '.join(e['headline'])})" if e["overall"] is not None else "overall: no data"
            cost = f"; {e['cost']['benchmark']}: {e['cost']['value']} {e['cost']['unit']}" if e["cost"] else "; cost: no data"
            print(f"  {e['effort'] or '(effort not stated)':10} {head}; {doms or 'no domain scores'}{cost}")
            for r in e["raw"]:
                print(f"      {r['source']} {r['benchmark']} {r['version'] or ''}: {r['value']} {r['unit']} "
                      f"({r['kind']}, {r['date']})")
        if c["untested"]:
            print(f"  no independent data at: {', '.join(c['untested'])}")
        for n in c["notes"]:
            print(f"  - {n}")
        return 0
    for e in bench.standings():
        print(f"{bench.entry_name(e):32} overall {e['overall'] if e['overall'] is not None else '—':>3}  "
              + ", ".join(f"{d} {s}" for d, s in e["domains"].items() if d != "overall"))
    if bench.pending():
        print("pending (no independent overall score yet): " + ", ".join(bench.pending()))
    return 0


def cmd_models(a):
    """This project's helper tiers: colony's default from the cards, or the project's own choice for a tier."""
    from . import bench, board
    root = board.root_of()
    if a.what in ("set", "reset"):
        tier, model, effort = (a.args + [None, None, None])[:3]
        if tier not in bench.TIERS or (a.what == "set" and not model):
            raise SystemExit(f"colony models set TIER MODEL [EFFORT], or colony models reset TIER; tiers: {', '.join(bench.TIERS)}")
        bench.set_plan(root, tier, model if a.what == "set" else None, effort, a.why or "")
        bench.write_helpers(root)
        print(f"{root.name}: {tier} → " + (f"{model}" + (f" at {effort}" if effort else "") if a.what == "set" else "colony's default"))
        return 0
    for tier, r in bench.effective(root).items():
        print(f"{tier:8} {r['model']} {r['effort'] or ''}".rstrip() + ("   (this project's choice)" if r["own"] else f"   {r['why']}"))
    from . import providers
    waiting = bench.unmeasured(providers.key(providers.of(root)))
    if waiting:
        print("not yet measured (not picked until they are): " + ", ".join(waiting))
    return 0


def cmd_setup(a):
    """Ask the steward to walk the person through first-time setup again."""
    from . import board, monitor
    monitor.setup()
    print(f"{board.the_steward()} will walk you through setup when it's next free (the board's {board.steward_name()} page)")
    return 0


def cmd_research(a):
    """The colony's research library: look before researching; add what others may reuse."""
    from . import board, research
    if a.add:
        try:
            r = research.add(a.add, a.title or "", a.question or "", board.root_of(), a.recheck, a.tag)
        except (ValueError, OSError) as err:
            print(err, file=sys.stderr)
            return 2
        print(f"Kept for every project as {r['id']}; look again by {r['recheck']}.")
        return 0
    if a.show:
        try:
            print(research.show(a.show))
        except KeyError:
            print(f"no entry {a.show}", file=sys.stderr)
            return 2
        return 0
    found = research.look(" ".join(a.words)) if a.words else research.entries()[:20]
    for r in found:
        print(research.line(r))
    if not found:
        print("Nothing in the library on that." if a.words else "The library is empty.")
    else:
        print("Read one: colony research --show ID")
    return 0


def cmd_lesson(a):
    """Lessons: propose one from evidence (steward or agent), mark one suspect, or list them."""
    from . import board, lessons
    try:
        if a.propose:
            action, text = a.propose
            project = None
            if a.project:
                project = next((p for p in board.projects() if p.name == a.project), None)
                if project is None:
                    raise ValueError(f"no project {a.project}")
            r = lessons.propose(action, text, a.evidence or "", board.root_of(), project)
            same = [x for x in lessons.standing(action, project, ("adopted", "proposed")) if x["id"] != r["id"]]
            print(f"Proposed {r['id']} for colony {action} ({'every project' if r['scope'] == 'colony' else Path(r['scope']).name}); "
                  "it waits for the person's tap on Needs you.")
            if same:
                print("Already standing or proposed for that action; if yours repeats or contradicts one, say which "
                      "should go (colony lesson --suspect ID \"why\"):\n" + "".join(f"- {x['id']} ({x['state']}): {x['text']}\n" for x in same), end="")
            if len(lessons.standing(action, project)) >= lessons.MOST:
                print(f"{lessons.MOST} are already adopted for it, and only {lessons.MOST} are shown: merge or retire one first.")
            return 0
        if a.recurred:
            adopted = sum(1 for r in lessons.load() if r.get("decided") and r["state"] != "declined")
            print(f"Of {adopted} adopted, the mistake came back despite (the measure is the first number below):")
            print(sum(1 for r in lessons.load() if r.get("suspected")))
            return 0
        if a.suspect:
            lessons.suspect(a.suspect, a.why or "")
            print(f"{a.suspect} marked suspect; the person keeps or retires it.")
            return 0
    except (ValueError, StopIteration) as err:
        print(err or "no such lesson", file=sys.stderr)
        return 2
    for r in lessons.load():
        if r["state"] in ("adopted", "proposed", "suspect"):
            where = "every project" if r["scope"] == "colony" else Path(r["scope"]).name
            print(f"{r['id']} [{r['state']}] colony {r['action']} · {where} · shown {r.get('shown', 0)}: {r['text']}")
    return 0


def cmd_rule(a):
    """The person's standing rules for this project: record one they gave, retire one they lifted, or list them."""
    from . import board, rules
    root = board.root_of()
    try:
        if a.retire:
            rules.retire(root, a.retire, a.text or "")
            print(f"{a.retire} retired.")
            return 0
        if a.text:
            r = rules.add(root, a.text, a.words or "", why=a.why or "", context=a.context or "", given=a.given)
            print(f"Kept as {r['id']}: it comes back to you at every session start, after a compaction too.")
            return 0
    except (ValueError, KeyError) as err:
        print(err if isinstance(err, ValueError) else f"no rule {a.retire}", file=sys.stderr)
        return 2
    for r in rules.rules(root):
        print(f"{r['id']}  {r['text']}  (their words: {r['words']})" + (f"  why: {r['why']}" if r.get("why") else ""))
    if not rules.rules(root):
        print("No standing rules here.")
    return 0


def cmd_wake(a):
    """A timed wake: a note to this project (or NAME, or the steward) when it comes due, in place of a sleeping process
    or a cron job; or the ones waiting (--list), or one cancelled (--cancel ID)."""
    from . import board, monitor, wakes
    words = list(a.words)
    if len(words) > 2:
        raise SystemExit('colony wake: one project name at most, then why in quotes: colony wake --in 2h "why"')
    making = not (a.list or a.cancel)
    name = words.pop(0) if len(words) == 2 or (words and not making) else None
    why = words[0] if words else ""
    caller = board.root_of()
    if not name:
        root = caller
    elif name in ("steward", monitor.name()):
        root = monitor.home()
    else:
        root = _project(name)
    steward, mine = monitor.home().resolve(), Path(root).resolve() == Path(caller).resolve()
    by = "agent" if mine else "steward" if Path(caller).resolve() == steward else Path(caller).name
    try:
        if a.cancel:
            wakes.cancel(root, a.cancel, by=by)
            print(f"{a.cancel} cancelled.")
            return 0
        if making:
            w = wakes.make(root, why, at=a.at, within=a.within, every=a.every, by=by)
            print(f"Kept as {w['id']}: due {wakes.when(w)}" + (f", then every {a.every} after each firing" if a.every else "")
                  + ". It arrives as a note once the console is idle, inside the person's active hours; nothing runs "
                    "meanwhile. `colony wake --cancel ID` if it is no longer needed.")
            return 0
    except (ValueError, KeyError) as err:
        print(err if isinstance(err, ValueError) else f"no waiting wake {a.cancel}", file=sys.stderr)
        return 2
    found = wakes.waiting(root)
    for w in found:
        print(f"{w['id']}  {wakes.when(w)}" + (f"  every {wakes.span(w['every'])}" if w.get("every") else "") + f"  {w['why']}")
    if not found:
        print("No wakes waiting.")
    return 0


def cmd_pins(a):
    """What the Library holds, under its headings as the board shows them; those under none last."""
    from . import board, pins
    kept = pins.pins(board.root_of())
    heads = list(dict.fromkeys(p.get("group") or "" for p in sorted(kept, key=lambda p: p["at"])))
    for g in [h for h in heads if h] + [""] * ("" in heads):
        print(f"{g or 'Under no heading'}:")
        for p in (p for p in kept if (p.get("group") or "") == g):
            print(f"  {p['id']}  {p['kind']:6} {p['by']:6} {p['title']}  ({p['target']})")
    return 0


def _hook_input():
    """What a hook was handed on stdin, if anything: never waits on a terminal or an open pipe."""
    import select
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0.2)
        return json.loads(sys.stdin.read() or "{}") if ready else {}
    except (ValueError, OSError):
        return {}


def _console_hook_allowed(root, provider, payload=None):
    """Optional hook scope: a project's board console running the expected provider, never cwd alone."""
    from . import context, providers
    if (provider or (payload or {}).get('session_id')) and not context.in_project(root, payload):
        return False
    return (not provider or (os.environ.get("COLONY_CONSOLE") == context.seat(root)
                            and providers.PROVIDERS.get(provider) is context.program(root)))


HOOK_SCOPE_MESSAGE = ("Colony hook skipped outside the matching project's board console. Use that console for "
                      "automatic delivery. For manual delivery, explicitly choose the intended project with "
                      "COLONY_PROJECT=/absolute/project/path colony notes --deliver; do not infer it from a shared folder.")


def cmd_context(a):
    """Context status, or provider compaction hooks scoped to this exact console."""
    from . import board, context
    root = board.root_of()
    if not a.hook:
        print(json.dumps(context.read(context.file(root)), indent=2))
        return 0
    payload = _hook_input()
    try:
        if a.hook == 'before':
            context.before_compact(root, a.console, payload)
        else:
            text = context.on_prompt(root, a.console, payload)
            if text:
                output = json.dumps({'hookSpecificOutput': {'hookEventName': payload['hook_event_name'],
                                    'additionalContext': text}}) if a.console == 'codex' else text
                print(output, flush=True)
                context.delivered(root, payload)
            return 0
    except (OSError, ValueError, RuntimeError) as err:
        print(f"Colony context restoration pending: {err}", file=sys.stderr)
    if a.console == 'codex':
        print('{}')
    return 0


def cmd_turn(a):
    """The provider's end-of-turn hook: if the turn asked the person something, it waits on them."""
    from . import board, providers
    root = board.root_of()
    if not _console_hook_allowed(root, a.console):
        print(HOOK_SCOPE_MESSAGE, file=sys.stderr)
        return 0
    if not (root / ".board").exists():
        return 0
    payload = _hook_input()
    if not _console_hook_allowed(root, a.console, payload):
        return 0
    if a.console == 'codex':
        from . import codex_remote
        if not codex_remote.accepts_hook(root, payload):
            return 0
    from . import context
    payload.setdefault("hook_event_name", "Stop")
    if context.carry_reply(root, a.console or providers.key(context.program(root)), payload):
        return 0
    key, text = context.program(root).turn_text(payload)      # PROVIDER: its last words as handed over, else its transcript
    from . import monitor
    steward = Path(root).resolve() == monitor.home().resolve()
    if steward and text.strip():
        monitor.said_last(text)                     # the steward's last word, at the top of its page
    # the turn asks the person when its agent said so (colony needs-you); the steward's, when it ends asking. A turn
    # whose words can't be read keeps the agent's word for the next one that can be, never spends it on nothing.
    if not text.strip():
        board.hold_needs_you(root)
    elif board.take_needs_you(root) or (steward and board.asks_question(text)):
        board.record_ask(root, key, text, explicit=True)
    board.said_reply(root, text)                    # the agent's answer to the person's own words, if they spoke
    from . import continuation
    continuation.after_turn(root)
    return 0


def cmd_notes(a):
    from . import board
    root = board.root_of()
    if not _console_hook_allowed(root, a.console):
        print(HOOK_SCOPE_MESSAGE)
        return 0
    if not (root / ".board").exists():
        return 0                                  # not on the board: the hooks stay silent
    if a.deliver:
        # Printed for the provider to put in the agent's context: Claude Code's hooks do (providers.py wire()).
        from . import mail, console, providers
        payload = _hook_input()
        if not _console_hook_allowed(root, a.console, payload):
            return 0
        if a.console == 'codex':
            from . import codex_remote
            if not codex_remote.accepts_hook(root, payload):
                return 0
        from . import vision, context, lead, nest, rules
        payload.setdefault("hook_event_name", "SessionStart" if a.session else "UserPromptSubmit")
        context.register(root, a.console or providers.key(context.program(root)), payload)
        if a.session and payload.get('source') != 'compact':
            providers.discover(calls=False, only=a.console or providers.key(context.program(root)))
        vision.observe(root)
        seen = os.environ.get("COLONY_CONSOLE") == console.session_name(root) and providers.of(root).conversation(payload)
        if seen:                                                                      # the board's console, no other here
            console.remember(root, *seen)
        prompt = str(payload.get("prompt") or "")
        if _persons(prompt):
            from . import hours
            hours.seen()                                     # they're around, whatever the clock says
            board.answer_asks(root, "in the console")        # the person answered there themselves
            board.drop_held_needs_you(root)
            board.said(root, prompt)                         # their own words, for the steward to catch up on
        from . import bench, monitor as _monitor
        own_monitor = Path(root).resolve() == _monitor.home().resolve()
        standing = bench.plan_text(root) if a.session and not own_monitor else ""   # the helper tiers, every session
        # colony's words for the steward: held for this hand-off when delivering, so an overlapping hook skips them.
        # Only with a prompt, as for a project's notes (board.deliver): handed over as its session started, the first-
        # time setup sat in a steward no turn had woken, and the watcher had nothing left to tell it.
        handed = (_monitor.claim() if a.deliver else _monitor.inbox()) if own_monitor and not a.session else []
        fresh, still = board.deliver(root, session=a.session, mark=False)
        fresh, own = vision.delivery(root, fresh)
        still, earlier_own = vision.delivery(root, still)
        if any(board.answers_ask(n) for n in fresh):
            board.answer_asks(root, "by a note")                 # the person (or their steward) wrote back
            board.drop_held_needs_you(root)
        new_mail, open_asks = mail.deliver(root, session=a.session, mark=False)
        try:
            engagement, engagement_receipt = lead.engage(root, mark=False, with_receipt=True)
        except Exception as error:                      # a sync problem never costs the turn its notes and mail
            engagement, engagement_receipt = f"Colony could not sync this workspace: {error}", None
        from . import hours, monitor
        # Outside the person's active hours, a project's agent hears it on every turn; inside them, nothing. The
        # steward's own console isn't a project: its brief says what the hours mean for it.
        # The person typing to it now is awake: their own turn gets no such line.
        night = ("" if Path(root).resolve() == monitor.home().resolve() or _persons(prompt)
                 else hours.notice())
        spoke = _persons(prompt) or any(n.get("author") not in ("colony", "observation", "monitor", "suggestion")
                                        for n in fresh)          # the person, in the console or on the board
        text = "\n\n".join(filter(None, [
            standing,
            _monitor.render_inbox(handed),
            board.open_gates_line(root) if spoke and not own_monitor else "",
            lead.role_text(root) if a.session else '',
            rules.session_text(root) if a.session else '',
            nest.session_text(root) if a.session and not own_monitor else '',
            engagement,
            board.render_notes([n for n in fresh if n.get("author") not in ("colony", "observation", "monitor", "suggestion")], "The person left notes for you on the board:"),
            board.render_notes([n for n in fresh if n.get("author") == "monitor"], f"{board.steward_role()[0].upper()}{board.steward_role()[1:]}, acting for them, left notes for you:"),
            board.render_notes([n for n in fresh if n.get("author") == "suggestion" and n.get("kind") != "advice"],
                               "Suggestions from " + (board.steward_role() if board.steward_named() else "the steward") + ":"),
            board.render_notes([n for n in fresh if n.get("author") == "suggestion" and n.get("kind") == "advice"], "Research advice colony asked for, apart from you:"),
            board.render_notes([n for n in fresh if n.get("author") == "colony"], "Colony, the harness the person set up and trusts, tells you (with their full approval):"),
            board.render_notes([n for n in fresh if n.get("author") == "observation"], "Observed file changes (no author or agreement inferred):"),
            board.render_notes(still, "Still open from earlier (delivered, not yet acted on):"),
            mail.render(new_mail, "Mail from other projects in the colony:"),
            mail.render(open_asks, "Questions from the colony you haven't answered yet:"),
            night]))
    else:
        text = board.render_notes(board.open_notes(root, a.item), "Open notes:") or "No open notes."
    if text:
        print(text, flush=True)
    if a.deliver:
        if handed:
            _monitor.taken(handed)
        board.delivered(root, fresh, own + earlier_own)
        mail.delivered(root, new_mail)
        lead.acknowledge_engagement(root, engagement_receipt)
    return 0


def cmd_rename(a):
    """An agent may name its own logical project, including one sharing another project's folder."""
    from . import board, project_names
    chosen = os.environ.get('COLONY_PROJECT')
    if chosen is not None:
        if not chosen or not Path(chosen).is_absolute():
            print('colony rename: COLONY_PROJECT must name your registered project by its absolute path.', file=sys.stderr)
            return 2
        root = Path(chosen).resolve()
    else:
        root = board.root_of().resolve()
    known = {p.resolve() for p in board.projects(adopt=False)}
    if root not in known or not root.is_dir() or not (root / '.board').is_dir():
        print('colony rename: run from your registered project or its board console.', file=sys.stderr)
        return 2
    try:
        changed = project_names.rename(root, a.name, changed_by='agent', why=a.why)
    except ValueError as err:
        print(f'colony rename: {err}', file=sys.stderr)
        return 2
    print(f"{'Renamed to' if changed else 'Already named'} {project_names.name_of(root)}.")
    if changed:
        print(project_names.guidance(root))
    return 0


def cmd_whoami(a):
    """The board project this runs in, by name, for colony's commit stamp; nothing outside one."""
    from . import board
    root = board.root_of()
    if (root / ".board").is_dir() and root.resolve() in {p.resolve() for p in board.projects()}:
        print(root.name)
        return 0
    return 1


def cmd_pair(a):
    """A fresh ChatGPT pairing code for a Codex project, for the person to enter in the app; --check confirms it."""
    from . import board, codex_remote, providers
    root = _project(a.name) if a.name else board.root_of()
    if providers.of(root) is not providers.get("codex"):
        raise SystemExit(f"{root.name} runs on {providers.of(root).label}; only Codex projects pair with ChatGPT")
    try:
        if a.check:
            if codex_remote.pair(root, a.check).get("claimed"):
                codex_remote.pairing_choice(root, "paired")
                print(f"{root.name} is paired with ChatGPT")
            else:
                print("not claimed yet: enter the code in the ChatGPT app, or get a fresh one if it expired")
            return 0
        result = codex_remote.pair(root)
    except codex_remote.RemoteError as err:
        raise SystemExit(str(err))
    codex_remote.pairing_choice(root, "accepted")
    expires = time.strftime("%-I:%M %p", time.localtime(result["expiresAt"]))
    print(f"{result['manualPairingCode']}  (expires {expires}): in the ChatGPT app, signed in to the same account, "
          "open Codex → Add manually and enter it")
    return 0


# Prompts the agent program or colony makes, never the person: colony's own nudges, and a background helper's
# result, which Claude Code hands back as a prompt.
MADE = ("[colony]", "<task-notification>", "<agent-message", "<system-reminder>", "<command-name>", "<local-command")


def _persons(prompt):
    """Whether a prompt is the person's own words."""
    return bool(prompt) and not prompt.lstrip().startswith(MADE)


def cmd_needs_you(a):
    """The agent says its turn asks the person something: when the turn ends, it waits on their list."""
    from . import board
    board.mark_needs_you(board.root_of())
    print("This turn will wait on the person when it ends: ask your question in it.")
    return 0


def cmd_connect(a):
    """Outside services: what this project reaches and how, or (add) a new one the person then gives its keys."""
    import shlex
    from . import board, connections
    if a.with_:
        command = a.rest[1:] if a.rest[:1] == ["--"] else a.rest
        if not command:
            raise SystemExit("colony connect --with NAME -- COMMAND")
        try:
            return connections.with_keys(a.with_, board.root_of(), command)
        except ValueError as err:
            print(err, file=sys.stderr)
            return 2
    if a.audit:
        from . import audit
        r = audit.run(a.audit)
        print(f"{a.audit}: {r['state']} {r.get('safe', '')} {r.get('fits', '')} {r.get('why', '')}".strip())
        return 0
    if a.remove:
        c = connections.load()["connections"].get(a.remove)
        if not c or c.get("approved", True):
            print(f"{a.remove} isn't a connection waiting on the person's approval; the person removes approved ones on the board.",
                  file=sys.stderr)
            return 2
        connections.remove(a.remove)
        print(f"{a.remove} removed.")
        return 0
    if a.add:
        from . import audit
        try:
            c = connections.add(a.add, a.title, shlex.split(a.command or ""), needs=a.key, hold=a.hold or None,
                                tools=a.tool, about=a.about, service=a.service, by=board.root_of())
        except ValueError as err:
            print(err, file=sys.stderr)
            return 2
        if audit.due(c):
            audit.start(a.add)
        checked = (" First the steward audits it (its code read, not run, by a fresh model, and weighed against this "
                   "project's work); if it refuses it, you hear why. Its --about should say what the work needs it for.")
        if not c["command"]:
            print(f"{c['title']} kept: keys only ({', '.join(c['keys'])}).{checked} Once the person approves it and enters them "
                  f"on the board's Connections page, run your script with them: colony connect --with {a.add} -- COMMAND (they "
                  f"reach it as environment variables, and its output comes back with them taken out).")
            return 0
        print(f"{c['title']} kept.{checked} It runs nowhere until the person approves it and enters its keys "
              f"({', '.join(c['keys']) or 'none'}) on the board's Connections page; then every project has it, each console "
              f"from its next idle restart. Waits for their tap: "
              f"{', '.join(c['hold']) if c['hold'] else 'everything its server does not mark read-only'}.")
        return 0
    root, data = board.root_of(), connections.load()
    for svc, s in connections.services().items():
        way = connections.path_for(svc, root)
        c = data["connections"].get(s.get("colony")) or {}
        why = (" (keys missing)" if s.get("colony") and connections.is_on(c, root) and connections.missing(s["colony"], c, root)
               else "")
        if way == "colony" and not c.get("command"):
            print(f"{s['title']}: keys for your scripts here: colony connect --with {s['colony']} -- COMMAND")
            continue
        print(f"{s['title']}: " + {"colony": "through colony; what it holds waits for the person's tap",
                                   "claude": "through the person's Claude connector",
                                   "chatgpt": "through the person's ChatGPT app (goes straight out, nothing held)",
                                   None: "not here" + why}[way])
    if not connections.services():
        print("No connections yet.")
    return 0


def cmd_noted(a):
    from . import board
    root = board.root_of()
    if a.id not in {n["id"] for n in board.notes(root)}:
        print(f"no note {a.id}", file=sys.stderr)
        return 2
    note = next(n for n in board.notes(root) if n["id"] == a.id)
    for ident in note.get('batch_ids') or [a.id]:
        board.append(root, "notes.jsonl", {"type": "addressed", "of": ident, "at": board.now(), "text": a.text})
    if note.get("author") == "suggestion" and note.get("kind") != "advice":   # the steward made it: its answer is the steward's to hear
        from . import monitor
        monitor.queue(f"{root.name} answered your suggestion ({note['text'][:80]}...): {a.text}")
    print(f"{a.id} marked as acted on")
    return 0


def _project(name):
    from . import board
    projects = board.projects()
    for p in projects:
        if p.name == name:
            return p
    named = [p for p in projects if board.name_of(p) == name]
    if len(named) == 1:
        return named[0]
    if named:
        raise SystemExit(f"more than one project named {name}; use its address from `colony projects`")
    raise SystemExit(f"no project named {name} on the board; `colony projects` lists them")


def cmd_projects(a):
    from . import board, console, mail
    for p in board.projects():
        snap = console.snapshot(p, lines=1) if p.exists() else {"state": "missing", "lines": []}
        waiting = len(board.moments(p))
        goal = ' '.join(board.roadmap(p)["goal"].split()) if p.exists() else ""
        label = board.name_of(p)
        name = f" · {label}" if label != p.name else ""
        print(f"{mail.address(p):22} {snap['state']:10} {str(waiting) + ' waiting on you' if waiting else '':16} {goal[:90]}{name}")
    return 0


def cmd_peek(a):
    from . import console
    snap = console.snapshot(_project(a.name), lines=a.lines)
    print(f"[{snap['state']}]")
    print("\n".join(snap["lines"]))
    return 0


def _caught_up(root):
    """Run by the steward's own console: if the person has spoken to this project directly since the steward last
    caught up, nothing is sent; their words are shown instead, so the steward never acts on a stale picture of
    what they want. True when it may go on."""
    from . import console, monitor
    if os.environ.get("COLONY_CONSOLE") != monitor.name():
        return True
    words = monitor.catch_up(root)
    if not words:
        return True
    print(f"Nothing was sent. The person has spoken to {root.name} directly since you last caught up:\n{words}\n"
          "If what you were doing still fits what they said there, run it again; if not, or if you can't tell, "
          "bring it to them instead.")
    return False


def cmd_said(a):
    """What the person has said to a project directly (in its console, or in a note on the board) since the
    steward last caught up on it: their own words only."""
    from . import monitor
    words = monitor.catch_up(_project(a.name))
    print(words or f"Nothing new from the person in {a.name} since you last caught up.")
    return 0


def cmd_tell(a):
    """The steward speaks for the person: as an attributed steward note, delivered through hooks into the agent's own
    context (typed text arrives as a paste, which an agent rightly doesn't take as the person's word), and a
    one-line nudge if the session is idle. It shows on the board like any note."""
    from . import board, console
    root = _project(a.name)
    if not _caught_up(root):
        return 3
    board.add_note(root, None, a.text, author="monitor")
    try:
        name = console.ensure(root)
        if console.snapshot(root, lines=1)["state"] == "idle":   # held while someone is typing there; the watcher nudges later
            console.type_into(name, f"[colony] You have a note from {board.steward_role()}.")
    except (OSError, subprocess.CalledProcessError):
        # The note is kept either way: sending it again would deliver it twice.
        print(f"sent to {a.name}; its console isn't ready, so the note waits for its next turn")
        return 0
    print(f"sent to {a.name} as a note from the steward, acting for the person")
    return 0


def cmd_new(a):
    from . import board, console
    root = Path(a.within or board.registry()["new_root"]).expanduser() / a.name
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"{root} already exists and is not empty")
    chosen = {k: getattr(a, k) for k in board.PROJECT_KEYS if getattr(a, k, None)}
    _runnable(chosen)
    root.mkdir(parents=True, exist_ok=True)
    try:
        board.project_settings(root, chosen)            # before wiring: the chosen provider does the wiring
    except KeyError as err:
        raise SystemExit(f"no such choice for {err}: colony settings shows the options")
    board.track(root)
    console.ensure(root)
    print(f"{a.name} created at {root}, on the board, with its console running. Your agent starts by shaping the vision with you.")
    return 0


def cmd_settings(a):
    from . import board
    if a.project:
        root = _project(a.project)
        try:
            merged, own = board.project_settings(root, {a.key: a.value or ""} if a.key else None)
        except KeyError:
            raise SystemExit(f"a project can set: {', '.join(board.PROJECT_KEYS)}")
        for k in board.PROJECT_KEYS:
            v = merged.get(k, "green" if k == "color" else "")      # a colour is the project's alone: the board's green else
            shown = ("on" if v else "off") if isinstance(v, bool) else str(v) or ("(none)" if k in board.TURBO else "(colony Auto)")
            print(f"{k:14} {shown:24} {'set for this project' if k in own else 'default' if k in board.TURBO or k == 'color' else 'global'}")
        return 0
    if a.key:
        try:
            board.set_setting(a.key, a.value or "")
        except KeyError:
            raise SystemExit(f"no setting {a.key}; the settings are: {', '.join(board.SETTING_NAMES.get(k, k) for k in board.DEFAULT_SETTINGS)}, autostart, new-folder")
        except ValueError as err:                     # a setting that says what reads (active_hours)
            raise SystemExit(f"colony: {err}")
        _settle_reach(a.key)
    reg = board.registry()
    from . import providers
    for k in board.DEFAULT_SETTINGS:
        v = reg["settings"][k]
        shown = (("on" if v else "off") if isinstance(v, bool) else ", ".join(v or providers.PROVIDERS) if k == "providers"
                 else ", ".join(f"{f}={c['model']}:{c['effort']}" for f, c in v.items()) or "(from the benchmark cards)"
                 if k == "consultants" else ",".join(f"{f}={'on' if v.get(f, True) else 'off'}" for f in providers.PROVIDERS)
                 if k == "turbo_by" else board.steward_name() if k == "steward_name" else v or "(colony Auto)")
        print(f"{board.SETTING_NAMES.get(k, k):14} {str(shown):28} {board.setting_help(k)}")
    from . import autostart
    if autostart.available():                         # kept by systemd: shown as it holds there
        print(f"{'autostart':14} {'on' if autostart.on() else 'off':28} {board.SETTING_HELP['autostart']}")
    print(f"{'new-folder':14} {reg['new_root']:28} where new projects are created")
    print(f"{'folders':14} {', '.join(reg['roots']) or '(none)'}")
    return 0


def cmd_choose(a):
    """Pick an option a project's agent is showing (a trust question, a permission prompt) by its text:
    `colony tell` would type the text and press Enter on whatever is highlighted."""
    from . import console, providers
    root = _project(a.name)
    if not _caught_up(root):
        return 3
    name = console.session_name(root)
    keys = providers.of(root).choose(console.screen(name), a.option)
    if not keys:
        print(f"{a.name} shows no choice containing \"{a.option}\"; colony peek {a.name} shows its screen", file=sys.stderr)
        return 1
    console.press(name, keys)
    time.sleep(2)
    snap = console.snapshot(root, lines=4)
    print(f"chose \"{a.option}\" in {a.name}; it is now {snap['state']}:")
    print("\n".join("  " + l for l in snap["lines"]))
    return 0


def cmd_send(a):
    from . import mail
    try:
        m = mail.send(a.to, mail.read_text(a.text), ask=a.ask, urgent=a.urgent)
    except KeyError:
        raise SystemExit(f"no project named {a.to}; `colony projects` lists them")
    except OSError as err:
        raise SystemExit(str(err))
    delivery = m.get('delivery', {})
    print(f"queued {m['id']} to {a.to} for session handoff" + (" (answer expected via colony reply)" if a.ask else ""))
    if delivery.get('status') == 'delayed':
        print(f"delivery delayed: {delivery['reason']}")
    return 0


def cmd_reply(a):
    from . import mail
    try:
        m = mail.reply(a.id, mail.read_text(a.text))
    except KeyError:
        raise SystemExit(f"no message {a.id} here; `colony mail` lists them")
    except OSError as err:
        raise SystemExit(str(err))
    print(f"reply to {a.id}: queued {m['id']} to {m['to']} for session handoff")
    if m.get('delivery', {}).get('status') == 'delayed':
        print(f"delivery delayed: {m['delivery']['reason']}")
    return 0


def cmd_mail(a):
    from . import board, mail
    root = _project(a.project) if a.project else board.root_of()
    ms = mail.messages(root)
    if not ms:
        print("no mail")
    me = mail.address(root)
    for m in ms[-a.last:]:
        way = f"from {mail.shown(m['from'])}" if m["to"] == me else f"to {m['to']}"
        flag = (" [answered]" if m["answer"] else " [asks]") if m["ask"] else ""
        delivery = m.get('delivery', {})
        state = delivery.get('status', 'handed' if m.get('delivered_at') else 'queued')
        detail = f" [{state}" + (f": {delivery['reason']}" if delivery.get('reason') else "") + "]"
        print(f"{m['id']}  {m['at'][:16]}  {way}{flag}{detail}: {m['text']}")
    return 0


def cmd_mute(a):
    from . import monitor
    try:
        if a.state:
            monitor.muted(a.state == "on")
    except ValueError as err:
        raise SystemExit(str(err))
    print("the steward is muted: nothing wakes it, and it spends nothing unless you talk to it" if monitor.muted()
          else "the steward is awake" + (", holding the helm" if monitor.helm() else ""))
    return 0


def cmd_helm(a):
    from . import monitor
    if a.project:
        root = _project(a.project)
        if a.state:
            monitor.set_posture(root, helm=a.state == "on")
        included = monitor.posture(root)["helm"] is not False
        print(f"{a.project}: " + ("the steward holds the helm" if monitor.helm_for(root) else
                                  "included in the helm, which is with the person" if included else "left out of the helm"))
        return 0
    if a.state:
        monitor.helm(a.state == "on")
    print("the steward holds the helm" if monitor.helm() else "the helm is with the person")
    return 0


def cmd_posture(a):
    """The steward's stance toward each project: helm, the person's direction, the current focus."""
    from . import board, monitor
    if a.name and (a.direction is not None or a.scout is not None or a.favour is not None or a.scouting):
        monitor.set_posture(_project(a.name), direction=a.direction, scout=a.scout, scout_note=a.favour,
                            scouting=None if not a.scouting else a.scouting == "on")
    for p in [_project(a.name)] if a.name else board.projects():
        f, pos = board.focus(p), monitor.posture(p)
        print(f"{p.name}: helm {'on' if monitor.helm_for(p) else 'off'}{' (left out of the helm)' if pos['helm'] is False else ''}")
        print(f"  direction: {pos['direction'] or '(none given)'}")
        print('  vision: ' + (board.roadmap(p)['goal'] or '(not shaped yet)').replace('\n', '\n    '))
        print(f"  scouting: " + (f"every {pos['scout']} hours if worked on" if pos["scout"] and pos["scouting"] else "off")
              + (f"; favour: {pos['scout_note']}" if pos["scout_note"] else ""))
        print(f"  focus: {f['milestone'] or '(no roadmap)'}")
        for label in ("doing", "verify", "next"):
            if f[label]:
                print(f"    {label}: " + "; ".join(f[label]))
    return 0


def cmd_lead(a):
    from . import board, lead, continuation
    root = _project(a.project) if a.project else board.root_of()
    try:
        if a.pair:
            lead.pair(root, _project(a.pair), actor=root)
        if a.switch:
            lead.switch(root, _project(a.switch), words=a.words or '')
            continuation.handoff(root)
        if a.commit_plan:
            commit = lead.commit_plan(root, a.commit_plan, actor=root)
            if commit is None:
                print('Private project: ROADMAP.md recorded locally in .board/plan.jsonl; no Git commit was created.')
        if a.tested:
            lead.tested(root, a.tested, a.checks or '', actor=root)
            if a.deployed:
                lead.update(root, lambda g: g.update(deployed_commit=g['integrated']))
        print(json.dumps(lead.info(root), indent=2))
    except (OSError, ValueError) as err:
        raise SystemExit(f'colony: {err}')
    return 0


def cmd_item(a):
    from . import board, lead, continuation
    root = board.root_of()
    try:
        if a.owner is not None:
            lead.assign(root, a.item, _project(a.owner) if a.owner != 'inherit' else None, actor=root)
        if a.start:
            lead.start_item(root, a.item, actor=root)
        if a.sync:
            lead.sync_item(root, a.item, actor=root)
        if a.handback:
            lead.handback(root, a.item, actor=root)
        if a.integrate:
            lead.integrate(root, a.item, a.integrate, actor=root, deploy=a.deploy or '', push=a.push)
        print(json.dumps(lead.info(root)['assignments'].get(a.item) or dict(item=a.item, owner=str(lead.owner(root, a.item))), indent=2))
        continuation.tick(root)
    except (OSError, ValueError) as err:
        raise SystemExit(f'colony: {err}')
    return 0


def cmd_progress(a):
    from . import board, lead, progress, continuation, console
    root = board.root_of()
    source = root if os.environ.get('COLONY_CONSOLE') == console.session_name(root) else None
    try:
        if a.define:
            c = progress.define(root, a.define, a.outcome or '', a.done or '', a.check or '',
                                items=a.items.split(',') if a.items else None, mode=a.mode,
                                words=a.words or '', actor=root)
            print('Planned checkpoint: ' + c['id'])
        if a.start:
            progress.start(root, a.start, actor=root, source=source)
        if a.ready:
            progress.ready(root, a.ready, a.commit or '', a.checks or '', deployed=a.deployed, actor=root)
        if a.approve or a.changes:
            progress.decide(root, a.approve or a.changes, 'approve' if a.approve else 'changes',
                            text=a.text or '', next_checkpoint=a.next, source=source)
        if a.pause:
            progress.pause(root, True)
        if a.adopt_goal:
            continuation.adopt(root)
        if a.clear_goal:
            continuation.clear(root)
        if a.resume:
            continuation.resume(root)
        else:
            continuation.tick(root)
        print(json.dumps(dict(checkpoints=lead.info(root)['checkpoints'], current=progress.current(root),
                              continuation=continuation.status(root)), indent=2))
    except (OSError, ValueError, RuntimeError) as err:
        raise SystemExit(f'colony: {err}')
    return 0


def cmd_vision(a):
    from . import board, vision
    root = board.root_of()
    if a.file:
        text = sys.stdin.read() if a.file == '-' else Path(a.file).expanduser().read_text()
        try:
            event = vision.save(root, text, how='conversation', words=a.words or '', context=a.context or '')
        except ValueError as err:
            raise SystemExit(str(err))
        print('Vision updated from the agreed conversation.' if event else 'Vision is unchanged.')
    else:
        vision.observe(root)
        if a.history:
            for event in vision.history(root):
                print(f"{event['at']} · {event['how']}\n{event['text']}\n{event.get('words', '')}"
                      + (f"\nReading: {event['context']}" if event.get('context') else '') + '\n')
        else:
            print(board.roadmap(root)['goal'] or 'Shape the vision with the person.')
    return 0


def cmd_ready(a):
    """Tell the person, in plain words, what's ready for their OK and how to check it."""
    from . import board
    root = board.root_of()
    board.mark_ready(root, a.item, a.what, a.check or "")
    print(f"{a.item}: the person will see it ready for their OK")
    left = _uncommitted(root)
    if left:
        print(f"Colony's check: {len(left)} tracked file{'s' * (len(left) != 1)} not committed ({', '.join(left[:5])}"
              f"{', …' if len(left) > 5 else ''}). If they're this item's work, commit them now; it isn't ready until it's in.")
    return 0


def cmd_serve(a):
    """A project's own server over HTTPS, through Tailscale: https://<this machine's tailnet name>:<its port>/ to its
    port on this machine (serving.py). Run from the project's console, or with --project."""
    from . import board, serving
    here = Path.cwd().resolve()
    root = _project(a.project) if a.project else Path(os.environ["COLONY_PROJECT"]) if os.environ.get("COLONY_PROJECT") \
        else max((p for p in board.projects() if board.workdir(p).resolve() in (here, *here.parents)),
                 key=lambda p: len(board.workdir(p).resolve().parts), default=None)   # the deepest: projects/ sits in colony's
    if root is None:
        raise SystemExit("colony serve: run it from a project's folder or console, or name it with --project")
    if a.off:
        print(f"{Path(root).name}: https off" if serving.remove(root) else f"{Path(root).name} wasn't served over https")
        return 0
    if a.port is None:
        print(serving.of(root) or f"{Path(root).name} isn't served over https (colony serve PORT)")
        return 0
    try:
        url = serving.add(root, a.port)
    except ValueError as err:
        raise SystemExit(f"colony serve: {err}")
    print(f"{url}  ->  http://127.0.0.1:{a.port}/\nBind 127.0.0.1 only, and take your external origin (links, redirects, "
          "Origin checks) from this URL. It's reached only from the person's own devices, kept across restarts, and ends "
          "with colony serve --off or when the project leaves the board.")
    return 0


def cmd_reboot(a):
    """Restart the machine, or shut it down, once the projects have parked their work (an hour at most): the person's
    to start, from a terminal of theirs or Settings, never an agent's (machine.py)."""
    import os
    from . import autostart, machine
    if a.status:
        print(machine.line() or "no restart under way")
        return 0
    if os.environ.get("COLONY_CONSOLE"):
        raise SystemExit("colony: restarting the machine is the person's to start (Settings › This machine), not an agent's")
    if a.cancel:
        print("cancelled: each project asked hears it can carry on" if machine.cancel() else "no restart was under way")
        return 0
    machine.start(off=a.off, within=a.within * 60)
    print(machine.line() + ". Each project at work is asked in turn, a minute apart; colony reboot --cancel stops it.")
    if not autostart.on() and autostart.available():
        print("note: colony won't start by itself afterwards (colony settings autostart on)")
    return 0


def cmd_remove(a):
    from . import board
    board.remove_project(_project(a.name))
    print(f"{a.name} is off the board; its files are where they were")
    return 0


def cmd_close(a):
    from . import board
    try:
        out = board.close_project(_project(a.name), cancel=a.cancel)
    except ValueError as e:
        print(e)
        return 1
    print({"kept": f"{a.name} stays on the board",
           "closed": f"{a.name} is closed: nothing was running, so it is off the board now; its files and conversation are kept",
           "closing": f"{a.name} is landing its work; once its turn ends with nothing running it comes off the board, its "
                      "files and conversation kept (colony track reopens it)"}[out])
    return 0


def cmd_nest(a):
    from . import board, nest
    if a.vision:
        if not (a.name and a.file):
            raise SystemExit("colony nest NAME --vision --file PATH --words \"the person's words\" --context \"your reading\"")
        try:
            changed = nest.set_vision(a.name, Path(a.file).read_text(), a.words or "", a.context or "")
        except (ValueError, OSError) as e:
            print(e)
            return 1
        print(f"The {a.name} nest's shared vision is {'saved; every member hears the change' if changed else 'unchanged'}")
        return 0
    if a.tell:
        if not (a.name and a.to):
            raise SystemExit("colony nest NAME --tell N --to PROJECT")
        if not _caught_up(_project(a.to)):
            return 1
        try:
            before = nest.tell(a.name, a.tell, _project(a.to))
        except ValueError as e:
            print(e)
            return 1
        print(f"{a.to} already heard it on {before[:10]} and keeps it as a note: it isn't told twice" if before
              else f"{a.to} is told, once")
        return 0
    if a.standard is not None or a.drop:
        if not a.name:
            raise SystemExit('colony nest NAME --standard "the person\'s words" --from PROJECT [--replaces N]')
        try:
            nest.standard(a.name, a.standard or "", a.source or "", a.drop or a.replaces)
        except ValueError as e:
            print(e)
            return 1
        print(f"The {a.name} nest's standards are saved")
        return 0
    roots = [_project(n) for n in a.projects] or ([board.root_of()] if a.name or a.leave else [])
    if a.leave:
        for r in roots:
            left = nest.leave(r)
            print(f"{r.name} left the {left} nest" if left else f"{r.name} is in no nest")
        return 0
    if a.name:
        for r in roots:
            print(f"{r.name} is in the {a.name} nest" + ("" if nest.join(r, a.name) else " already"))
        return 0
    data = nest.nests()
    if not data:
        print("No nests yet. colony nest NAME PROJECT... puts projects that share a purpose in one.")
    for name, n in data.items():
        first = (n.get("vision") or "").strip().splitlines()
        print(f"{name}: {', '.join(Path(p).name for p in n.get('members', [])) or '(no projects)'}"
              + (f"\n  shared vision: {first[0][:100]}" if first else ""))
        for k, row in enumerate(n.get("standards", []), 1):
            print(f"  standard {k}: \"{row['words']}\"" + (f" (said in {row['from']})" if row.get("from") else ""))
    return 0


def cmd_archive(a):
    from . import board
    try:
        out = board.archive_together([_project(a.name)], why=a.why or "")
    except ValueError as e:
        print(e)
        return 1
    for name, state in out.items():
        print(f"{name} is landing the turn it's in; then it comes off the board, its files and conversation kept. "
              "All projects → Archived brings it back, and its agent hears why it was archived."
              if state == "closing" else f"{name} is archived" if state == "closed" else f"{name}: {state}")
    return 0 if all(v in ("closing", "closed") for v in out.values()) else 1


def cmd_delete(a):
    from . import board
    dest = board.delete_project(_project(a.name))
    print(f"{a.name} deleted: its folder is in the trash at {dest}")
    return 0


def cmd_supports(a):
    from . import supports
    try:
        if a.action == "add":
            r = supports.add(a.name, a.symptom, a.gives, a.source, a.cost, a.remove, a.evidence, "reference" if a.reference else "tool",
                             _project(a.project) if a.project else None)
            print(f"{r['id']} added as a candidate")
        elif a.action == "set":
            r = supports.update(a.name, a.status, a.evidence, _project(a.project) if a.project else None)
            print(f"{r['id']} is {r['status']}")
        elif a.action == "suggest":
            if not (a.project and a.text):
                raise SystemExit("colony supports suggest ID --project NAME --text \"the need you saw and why this fits\"")
            r = supports.suggest(a.name, _project(a.project), a.text)
            print(f"{r['name']} suggested to {a.project}: it reaches the agent on its next turn, as a suggestion to check")
        elif a.action == "ask":
            if not (a.project and a.text):
                raise SystemExit("colony supports ask ID --project NAME --text \"the need, the candidate, its value and cost, plainly\"")
            r = supports.ask(a.name, _project(a.project), a.text)
            print(f"{r['name']} for {a.project} is in the person's Needs you; their answer will reach you")
        elif a.action == "approve":
            r = supports.approve(a.name, a.evidence)
            print(f"{r['name']}: approved by the person; it can now be suggested")
        elif a.action == "block":
            b = supports.block(a.name, a.evidence, a.source)
            print(f"{b['name']} is blocked for good; delete anything you fetched of it")
        elif a.action == "sources":
            print(supports.sources_text(_project(a.project) if a.project else None))
        elif a.action == "source":
            if not (a.name and a.status and a.trust):
                raise SystemExit("colony supports source NAME WHERE --trust official|reviewed|broad|vendor|research [--project NAME]")
            supports.add_source(a.name, a.status, a.trust, _project(a.project) if a.project else None)
            print(f"{a.name} added to the sources as {a.trust}" + (f", for {a.project}" if a.project else ""))
        elif a.action == "check":
            print(supports.check_now())
        else:
            print(supports.text(_project(a.project) if a.project else None) or "nothing found for it yet")
    except (KeyError, ValueError) as err:
        raise SystemExit(f"{err.args[0] if err.args else err} (statuses: {', '.join(supports.STATUSES)})")
    return 0


def cmd_decided(a):
    from . import monitor
    if not _caught_up(_project(a.name)):
        return 3
    monitor.decided(_project(a.name), a.text)
    print(f"recorded for {a.name}")
    return 0


def cmd_suggest(a):
    """(steward) Your own suggestion to a project's agent, never the person's word: it weighs it against its work and
    answers (colony noted). It wakes the project only if its console sits idle, with nothing typed, no one at it and
    nothing waiting on the person; otherwise the agent hears it on its next turn. Nothing is started for it."""
    from . import board, console, turbo
    root = _project(a.name)
    if not a.text.strip():
        raise SystemExit('colony suggest NAME "your suggestion, and why it may help"')
    snap = console.snapshot(root, lines=4)
    now = turbo.ready(root, snap) and not turbo.held(root, snap)
    board.add_note(root, None, a.text, author="suggestion", quiet=True, kind="idea")
    woken = now and console.type_into(console.session_name(root), "[colony] You have a suggestion from "
                                       + (board.steward_role() if board.steward_named() else "the steward") + ".")
    print(f"suggested to {a.name}, as yours: " + ("its agent has it now" if woken else "its agent hears it on its next turn"))
    return 0


def cmd_turbo(a):
    """Each program's pace this week: its use against what's expected by now, turbo on or off, the reset, and what
    turbo is doing for each of its projects. --nothing, from a project's agent answering turbo's note: nothing worth
    doing, so turbo sends it no more until its roadmap changes shape."""
    from . import board, turbo
    if a.nothing:
        root = board.root_of()
        if not (root / ".board").is_dir() or root.resolve() not in {p.resolve() for p in board.projects()}:
            raise SystemExit("colony turbo --nothing: run it in a project on the board")
        turbo.nothing(root)
        print(f"noted for {root.name}: nothing more from turbo until its roadmap changes")
        return 0
    print(turbo.report())
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="colony", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("track"); p.add_argument("path", nargs="?", default=".")
    p.add_argument("--name", help="a second project in a folder that has its own, under this name")
    p.add_argument('--role', choices=('lead', 'helper'), help='when adding another agent, switch lead or add a helper')
    for k in ("provider", "model", "effort", "permissions"):
        p.add_argument(f"--{k}", help="for this project (default: the global setting)")
    p.add_argument("--private", action="store_true", help="keep everything of colony's out of the project's git "
                   "history (for a public or shared repository); it stays private at every later wiring")
    p.set_defaults(fn=cmd_track)
    p = sub.add_parser("board"); p.add_argument("--port", type=int)
    p.add_argument("--lan", action="store_true", help="also answer every device on your home network (the default is this machine only)")
    p.add_argument("--no-steward", "--no-monitor", dest="no_monitor", action="store_true", help="don't start the steward session and its watcher")
    p.add_argument("--foreground", action="store_true", help="run here instead of in its tmux session")
    p.add_argument("--local", action="store_true", help="answer only this machine, whatever the settings say")
    p.set_defaults(fn=cmd_board)
    sub.add_parser("restart").set_defaults(fn=cmd_restart)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    p = sub.add_parser("urls"); p.add_argument("--port", type=int, default=8790); p.set_defaults(fn=cmd_urls)
    p = sub.add_parser("remote", help="the board from your own devices, anywhere, through Tailscale (no one else reaches it)")
    p.add_argument("--off", action="store_true", help="stop: the board is reached as it was before")
    p.add_argument("--check", action="store_true", help="say where Tailscale and the board stand")
    p.add_argument("--port", type=int, default=8790); p.set_defaults(fn=cmd_remote)
    p = sub.add_parser("voice", help="replies read aloud in a natural voice made on this machine (Kokoro)")
    p.add_argument("--setup", action="store_true", help="install it: its own Python and the model, once")
    p.set_defaults(fn=cmd_voice)
    p = sub.add_parser("doctor"); p.add_argument("--tests", action="store_true", help="also run the test suite")
    p.set_defaults(fn=cmd_doctor)
    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)
    p = sub.add_parser('rename', help="change this project's own name on the board")
    p.add_argument('name'); p.add_argument('--why', default=''); p.set_defaults(fn=cmd_rename)
    p = sub.add_parser("mute", help="mute the steward (on) so nothing wakes it, or wake it again (off)")
    p.add_argument("state", nargs="?", choices=("on", "off")); p.set_defaults(fn=cmd_mute)
    p = sub.add_parser("pair", help="a ChatGPT pairing code for a Codex project (--check CODE confirms it)")
    p.add_argument("name", nargs="?"); p.add_argument("--check", metavar="CODE"); p.set_defaults(fn=cmd_pair)
    p = sub.add_parser("gate"); p.add_argument("question"); p.add_argument("--item"); p.add_argument("--why", default="")
    p.add_argument("--default", help="only for a reversible choice: what stands if the person skips it, once it has "
                   "been on their page a whole day; a costly or irreversible decision has no default")
    p.add_argument("--option", action="append", default=[], help="an answer the person can tap (repeat for each)")
    p.add_argument("--recommended", help="the option you recommend: shown first, marked, one tap to choose")
    p.add_argument("--reframes", metavar="ID", help="this question replaces gate ID, asked again with what it was missing")
    p.add_argument("--told", help="with --reframes: your answer to the person's question, shown with the decision")
    p.add_argument("--answered", metavar="ID", help="the person answered gate ID in conversation; QUESTION is their answer")
    p.add_argument("--context", help="with --answered: your reading of what they meant")
    p.add_argument("--withdraw", metavar="ID", help="gate ID no longer applies (superseded, moot); QUESTION says why")
    p.add_argument("--when", metavar="ITEM", help="the question waits, off the person's list, until roadmap ITEM starts")
    p.add_argument("--consult", metavar="ID", help="the first-round consultation that supplied this gate's points")
    p.add_argument("--points", metavar="FILE", help="JSON list of points with text and consultant numbers (- for stdin)")
    p.add_argument("--accept", action="append", default=[], metavar="POINT", help="with --answered: a point the person accepted, e.g. P1")
    p.add_argument("--reject", action="append", default=[], metavar="POINT", help="with --answered: a point the person rejected, e.g. P2")
    p.set_defaults(fn=cmd_gate)
    sub.add_parser("statusline", help="(Claude Code's status line) record its usage limits").set_defaults(fn=cmd_statusline)
    p = sub.add_parser("consult", help="two fresh models from different families, at a decision costly to change")
    p.add_argument("decision", help="the roadmap item (R12) or a short name for the decision")
    p.add_argument("question", nargs="?", help="the decision, in a sentence or two")
    p.add_argument("--digest", metavar="FILE", help="your digest of the facts, each with its source (- for stdin)")
    p.add_argument("--plan", metavar="FILE", help="round two: your revised approach, for the consultants to check")
    p.add_argument("--adopt", metavar="ID", help="record which points of consultation ID the person accepted (DECISION is their words)")
    p.add_argument("--context", help="with --adopt: your reading of what they meant")
    p.add_argument("--show", metavar="ID", help="show consultation ID again")
    p.set_defaults(fn=cmd_consult)
    p = sub.add_parser("scout", help="(steward) a fresh scout that can only search and read the web, on a brief")
    p.add_argument("brief", nargs="?", help="the need and where to look (- or none: read from stdin)")
    p.add_argument("--file", help="read the brief from a file")
    p.add_argument("--provider", help="the program to run it on (default: the steward's)")
    p.set_defaults(fn=cmd_scout)
    p = sub.add_parser("notes"); p.add_argument("item", nargs="?"); p.add_argument("--deliver", action="store_true")
    p.add_argument("--console", metavar="PROVIDER", help="(hook) deliver only in this provider's matching board console")
    p.add_argument("--session", action="store_true"); p.set_defaults(fn=cmd_notes)
    p = sub.add_parser("said", help="(steward) what the person said to a project directly since you last caught up")
    p.add_argument("name"); p.set_defaults(fn=cmd_said)
    p = sub.add_parser("connect", help="outside services (an API, a mailbox): what's granted here, or --add one")
    p.add_argument("--add", metavar="NAME"); p.add_argument("--title", default="")
    p.add_argument("--command", help="what starts its MCP server, its version pinned (none: keys for your own script)")
    p.add_argument("--with", dest="with_", metavar="NAME", help="run a command with a keys-only connection's keys: --with NAME -- COMMAND")
    p.add_argument("--remove", metavar="NAME", help="drop a connection added here that the person hasn't approved")
    p.add_argument("--audit", metavar="NAME", help=argparse.SUPPRESS)
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.add_argument("--key", action="append", default=[], help="a key the person enters in Settings (repeat)")
    p.add_argument("--hold", action="append", default=[], help="a tool that runs only on the person's tap (repeat)")
    p.add_argument("--tool", action="append", default=[], help="offer only these tools (repeat)")
    p.add_argument("--about", default=""); p.add_argument("--service", help="the service it reaches, if not its name (gmail)")
    p.set_defaults(fn=cmd_connect)
    p = sub.add_parser("noted"); p.add_argument("id"); p.add_argument("text"); p.set_defaults(fn=cmd_noted)
    sub.add_parser("needs-you", help="this turn asks the person something: it waits on their list when it ends").set_defaults(fn=cmd_needs_you)
    sub.add_parser("projects").set_defaults(fn=cmd_projects)
    p = sub.add_parser("send"); p.add_argument("to"); p.add_argument("text", help="the message, or - to read it from stdin")
    p.add_argument("--ask", action="store_true", help="it expects an answer")
    p.add_argument("--urgent", action="store_true", help="deliver even mid-turn, not when the turn ends")
    p.set_defaults(fn=cmd_send)
    p = sub.add_parser("reply"); p.add_argument("id"); p.add_argument("text"); p.set_defaults(fn=cmd_reply)
    p = sub.add_parser("mail"); p.add_argument("--project"); p.add_argument("--last", type=int, default=20)
    p.set_defaults(fn=cmd_mail)
    p = sub.add_parser("peek"); p.add_argument("name"); p.add_argument("-n", "--lines", type=int, default=30)
    p.set_defaults(fn=cmd_peek)
    p = sub.add_parser("tell"); p.add_argument("name"); p.add_argument("text"); p.set_defaults(fn=cmd_tell)
    p = sub.add_parser("pin", help="keep a file in the project or a URL in its Library, for the person and you")
    p.add_argument("target"); p.add_argument("--title"); p.add_argument("--why")
    p.add_argument("--group", help="the Library heading it goes under"); p.set_defaults(fn=cmd_pin)
    p = sub.add_parser("wake", help="a note to this project at a later time, to check back on something (no sleeping process, no cron)")
    p.add_argument("words", nargs="*", metavar="[NAME] WHY", help="the project (or steward; this one by default), then what to check")
    p.add_argument("--at", help="HH:MM (today, or tomorrow once past) or a local date-time, 2026-10-29T09:00")
    p.add_argument("--in", dest="within", metavar="DURATION", help="30m, 2h, 1d")
    p.add_argument("--every", metavar="DURATION", help="again this long after each firing")
    p.add_argument("--list", action="store_true"); p.add_argument("--cancel", metavar="ID")
    p.set_defaults(fn=cmd_wake)
    p = sub.add_parser("rule", help="the person's standing rules for this project: kept by colony, back at every session start")
    p.add_argument("text", nargs="?", help="the rule (with --retire: why it no longer holds)")
    p.add_argument("--words", help="the person's own words for it"); p.add_argument("--retire", metavar="ID")
    p.add_argument("--why", help="what led to it: the moment or problem behind their words")
    p.add_argument("--context", help="your reading of what it asks in practice")
    p.add_argument("--given", help="the day they said it, where that was before now (YYYY-MM-DD)")
    p.set_defaults(fn=cmd_rule)
    p = sub.add_parser("lesson", help="lessons keyed to colony's actions: propose one from evidence, for the person to adopt")
    p.add_argument("--propose", nargs=2, metavar=("ACTION", "TEXT"), help="the action it rides on (gate, show, ready, connect, ...) and one sentence")
    p.add_argument("--evidence", help="what happened that taught it, and where (project, note or gate id)")
    p.add_argument("--project", help="only for this project (default: every project)")
    p.add_argument("--suspect", metavar="ID"); p.add_argument("--why", help="what recurred, or how the person overrode it")
    p.add_argument("--recurred", action="store_true", help="how many adopted lessons the mistake came back despite (a measure)")
    p.set_defaults(fn=cmd_lesson)
    p = sub.add_parser("research", help="the colony's research library: look before researching, add what others may reuse")
    p.add_argument("words", nargs="*", help="what you're looking for (none: the newest)")
    p.add_argument("--add", metavar="FILE", help="keep a report for every project"); p.add_argument("--title")
    p.add_argument("--question", help="the question it answers, in plain words"); p.add_argument("--tag", action="append", default=[])
    p.add_argument("--recheck", type=int, default=90, metavar="DAYS", help="when to look again (default 90)")
    p.add_argument("--show", metavar="ID"); p.set_defaults(fn=cmd_research)
    p = sub.add_parser("hint", help="the plan's shape hints; keep KEY \"why\" settles one until the vision changes")
    p.add_argument("action", nargs="?", choices=("keep",)); p.add_argument("key", nargs="?"); p.add_argument("why", nargs="?")
    p.set_defaults(fn=cmd_hint)
    p = sub.add_parser("regroup", help="organize the Library: an entry under a heading, or --rename a heading")
    p.add_argument("target", help="an entry's id, or with --rename the heading to rename"); p.add_argument("heading")
    p.add_argument("--rename", action="store_true"); p.set_defaults(fn=cmd_regroup)
    p = sub.add_parser("unpin", help="put a Library entry in its archive: its part of the work is done")
    p.add_argument("id"); p.add_argument("--why"); p.set_defaults(fn=cmd_unpin)
    sub.add_parser("pins", help="what is pinned for the person").set_defaults(fn=cmd_pins)
    p = sub.add_parser("show", help="put a deliverable worth seeing in the project's showcase")
    p.add_argument("title"); p.add_argument("--what", required=True, help="what it is, plainly")
    p.add_argument("--try", dest="try_", help="how to see or try it"); p.add_argument("--link", help="a URL or a file in the project")
    p.add_argument("--item", help="the roadmap item it comes from"); p.set_defaults(fn=cmd_show)
    sub.add_parser("setup", help="the steward walks you through first-time setup").set_defaults(fn=cmd_setup)
    p = sub.add_parser("migrate", help="carry colony to a fresh install: what it takes, packed in one archive, and back "
                       "in place there as if it had just restarted")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="what a pack takes, with sizes, and what it leaves out")
    g.add_argument("--pack", metavar="DIR", help="pack it all into one archive in DIR (another disk)")
    g.add_argument("--unpack", metavar="ARCHIVE", help="on the new machine: everything back in place, then what "
                   "belongs to the machine made again")
    p.add_argument("--force", action="store_true", help="unpack over a colony already here")
    p.set_defaults(fn=cmd_migrate)
    p = sub.add_parser("bench", help="benchmark cards for the models the board can run")
    p.add_argument("what", nargs="?", choices=("card", "import", "pending", "discover", "fetch", "key")); p.add_argument("arg", nargs="?")
    p.set_defaults(fn=cmd_bench)
    p = sub.add_parser("models", help="this project's helper tiers: routine, step-up, chores, rote")
    p.add_argument("what", nargs="?", choices=("set", "reset")); p.add_argument("args", nargs="*"); p.add_argument("--why")
    p.set_defaults(fn=cmd_models)
    p = sub.add_parser('lead', help='shared project lead and canonical plan')
    p.add_argument('--project'); p.add_argument('--pair'); p.add_argument('--switch'); p.add_argument('--words')
    p.add_argument('--commit-plan'); p.add_argument('--tested'); p.add_argument('--checks'); p.add_argument('--deployed', action='store_true')
    p.set_defaults(fn=cmd_lead)
    p = sub.add_parser('item', help='scoped item ownership, engagement sync and completing-agent integration')
    p.add_argument('item'); p.add_argument('--owner'); p.add_argument('--start', action='store_true')
    p.add_argument('--sync', action='store_true'); p.add_argument('--handback', action='store_true')
    p.add_argument('--integrate', metavar='TEST_COMMAND'); p.add_argument('--deploy', metavar='COMMAND'); p.add_argument('--push', action='store_true')
    p.set_defaults(fn=cmd_item)
    p = sub.add_parser('progress', help='a completed version and its human stopping point')
    p.add_argument('--define', metavar='MILESTONE'); p.add_argument('--outcome'); p.add_argument('--done'); p.add_argument('--items')
    p.add_argument('--check'); p.add_argument('--mode', choices=('approval', 'check-in'), default='approval'); p.add_argument('--words')
    p.add_argument('--start'); p.add_argument('--ready', metavar='ARTIFACT_OR_URL'); p.add_argument('--commit'); p.add_argument('--checks')
    p.add_argument('--deployed', action='store_true'); p.add_argument('--approve', metavar='CANDIDATE'); p.add_argument('--changes', metavar='CANDIDATE')
    p.add_argument('--next'); p.add_argument('--text'); p.add_argument('--pause', action='store_true'); p.add_argument('--resume', action='store_true')
    p.add_argument('--adopt-goal', action='store_true'); p.add_argument('--clear-goal', action='store_true'); p.set_defaults(fn=cmd_progress)
    p = sub.add_parser("context", help="context refresh status")
    p.add_argument("--hook", choices=["before", "restore"])
    p.add_argument("--console", choices=["claude", "codex"])
    p.set_defaults(fn=cmd_context)
    p = sub.add_parser("turn", help="(hook) a turn ended; record it if it asks the person something")
    p.add_argument("--console", metavar="PROVIDER", help="(hook) record only in this provider's matching board console")
    p.set_defaults(fn=cmd_turn)
    p = sub.add_parser("choose"); p.add_argument("name"); p.add_argument("option"); p.set_defaults(fn=cmd_choose)
    p = sub.add_parser("new"); p.add_argument("name"); p.add_argument("--in", dest="within")
    for k in ("provider", "model", "effort", "permissions"):
        p.add_argument(f"--{k}", help="for this project (default: the global setting)")
    p.set_defaults(fn=cmd_new)
    p = sub.add_parser("settings"); p.add_argument("key", nargs="?"); p.add_argument("value", nargs="?")
    p.add_argument("--project", help="a project's own settings instead of the global ones")
    p.set_defaults(fn=cmd_settings)
    p = sub.add_parser("helm"); p.add_argument("state", nargs="?", choices=("on", "off")); p.add_argument("--project")
    p.set_defaults(fn=cmd_helm)
    p = sub.add_parser("posture", help="the steward's stance toward each project: helm, direction, focus")
    p.add_argument("name", nargs="?"); p.add_argument("--direction")
    p.add_argument("--scout", help="every how many hours to look for supports here (0: never)")
    p.add_argument("--favour", help="what scouting here should favour")
    p.add_argument("--scouting", choices=("on", "off"), help="scout for this project at all"); p.set_defaults(fn=cmd_posture)
    p = sub.add_parser('vision', help='read the vision or record a clearly agreed conversation change')
    p.add_argument('--file', help='new vision text (- reads stdin)')
    p.add_argument('--words', help='the person’s words agreeing the change; required with --file')
    p.add_argument('--context', help='your reading: what was discussed, what they meant, what it changes; required with --file')
    p.add_argument('--history', action='store_true'); p.set_defaults(fn=cmd_vision)
    p = sub.add_parser("ready", help="tell the person, plainly, what's ready for their OK and how to check it")
    p.add_argument("item"); p.add_argument("what"); p.add_argument("--check"); p.set_defaults(fn=cmd_ready)
    p = sub.add_parser("remove", help="take a project off the board (its files stay)"); p.add_argument("name"); p.set_defaults(fn=cmd_remove)
    p = sub.add_parser("serve", help="serve a project's own local port over https, through Tailscale")
    p.add_argument("port", nargs="?", type=int, help="the local port it listens on (127.0.0.1)")
    p.add_argument("--off", action="store_true", help="stop serving it")
    p.add_argument("--project", help="the project (default: the one this runs in)")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("reboot", help="restart the machine once every project has parked its work (an hour at most)")
    p.add_argument("--off", action="store_true", help="shut it down instead")
    p.add_argument("--within", type=int, default=60, help="minutes the projects have to park (default 60)")
    p.add_argument("--cancel", action="store_true", help="stop it: each project asked carries on")
    p.add_argument("--status", action="store_true", help="how far the parking is")
    p.set_defaults(fn=cmd_reboot)
    p = sub.add_parser("close", help="close a project safely: it lands its work, then comes off the board (files and conversation kept)")
    p.add_argument("name"); p.add_argument("--cancel", action="store_true", help="keep it on the board after all")
    p.set_defaults(fn=cmd_close)
    p = sub.add_parser("archive", help="archive a project: its agent packages where it stands, then it comes off the board")
    p.add_argument("name"); p.add_argument("--why", help="why, in the person's words: its agent reads it if it reopens")
    p.set_defaults(fn=cmd_archive)
    p = sub.add_parser("nest", help="nests: projects that share a purpose, and the shared vision they serve")
    p.add_argument("name", nargs="?"); p.add_argument("projects", nargs="*", help="projects to put in it (default: this one)")
    p.add_argument("--leave", action="store_true", help="take the projects (default: this one) out of their nest")
    p.add_argument("--vision", action="store_true", help="set the nest's shared vision, agreed with the person")
    p.add_argument("--file"); p.add_argument("--words"); p.add_argument("--context")
    p.add_argument("--standard", help="(steward) a standard the person stated, in their words, for this nest")
    p.add_argument("--from", dest="source", help="the project they said it in")
    p.add_argument("--replaces", type=int, help="the number of the older standard it replaces")
    p.add_argument("--drop", type=int, help="remove a standard by its number")
    p.add_argument("--tell", type=int, help="(steward) carry standard N to a nestmate where its work meets it: once")
    p.add_argument("--to", help="the project to tell")
    p.set_defaults(fn=cmd_nest)
    p = sub.add_parser("delete", help="delete a project: its folder moves to colony's trash"); p.add_argument("name"); p.set_defaults(fn=cmd_delete)
    p = sub.add_parser("supports", help="(steward) tools offered to projects when their work calls for one, and how far each is trusted")
    p.add_argument("action", nargs="?", choices=("add", "set", "ask", "approve", "suggest", "check", "sources", "source", "block")); p.add_argument("name", nargs="?", help="add: its name; set: its id")
    p.add_argument("status", nargs="?"); p.add_argument("--for", dest="symptom", default=""); p.add_argument("--gives", default="")
    p.add_argument("--source", default=""); p.add_argument("--cost", default=""); p.add_argument("--remove", default="")
    p.add_argument("--evidence", default=""); p.add_argument("--project"); p.add_argument("--text", default=""); p.add_argument("--trust"); p.add_argument("--reference", action="store_true", help="add: a project to learn from, not a tool to install"); p.set_defaults(fn=cmd_supports)
    p = sub.add_parser("decided", help="(steward) record what it settled for a project"); p.add_argument("name"); p.add_argument("text")
    p.set_defaults(fn=cmd_decided)
    p = sub.add_parser("suggest", help="(steward) your own suggestion to a project's agent, for it to weigh and answer")
    p.add_argument("name"); p.add_argument("text"); p.set_defaults(fn=cmd_suggest)
    p = sub.add_parser("turbo", help="each program's pace this week, and what turbo is doing about it")
    p.add_argument("--nothing", action="store_true",
                   help="(a project's agent) nothing worth doing: no more from turbo until the roadmap changes")
    p.set_defaults(fn=cmd_turbo)
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] in (["steward"], ["monitor"]):       # the steward's commands under one name; `monitor` is its old name
        if len(argv) == 1:
            print("colony steward COMMAND ...: the steward's commands are mute, helm, posture, scout, said, suggest, decided, supports, setup")
            return 0
        argv = argv[1:]
    a, extra = ap.parse_known_args(argv)
    if extra and a.cmd == "wake" and not any(x.startswith("-") for x in extra):
        a.words += extra                             # colony wake NAME --in 2h "why": its words either side of the options
    elif extra:
        a = ap.parse_args(argv)                      # says what it doesn't know, as ever
    code = a.fn(a)
    if a.cmd in ("gate", "show", "ready", "connect", "consult", "vision", "send", "reply", "pin", "research",
                 "needs-you", "progress", "nest", "noted") and not code:
        try:                                            # what the person adopted for this action, as it is taken
            from . import board, lessons
            said = lessons.remind(a.cmd, board.root_of())
            if said:
                print("\n" + said, end="")
        except Exception:
            pass                                        # a lesson never stands in the way of the action itself
    return code
