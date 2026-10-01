"""colony — drive a long project toward a person's goal with one builder, short-lived specialists,
located signals and a project memory.

    colony init DIR                 make DIR a colony project (git, design/, .colony/)
    colony door --goal "..."        draft the spine, specialists and questions from a goal
    colony approve                  the person approves design/spine.md; runs may start
    colony run [--rows N] [--cap USD]
    colony wait [--timeout S]        block until the run stops, then say how it went
    colony status                   the rows, NOW and the open signals
    colony cost                     dollars and tokens, per row and per agent
    colony checkpoint               workflow, progress and tokens since the last checkpoint; questions for you
    colony answer KIND TEXT [--always]  answer a checkpoint question; --always keeps it as a rule
    colony track [PATH]             put a project on the board (roadmap, notes, gates, delivery hooks)
    colony board [--port 8790] [--lan]  one page for all tracked projects, with each project's live console
    colony gate "QUESTION" [--item R4] [--why ...]   put a decision in the person's hands
    colony notes [R4]               open notes from the person (the hooks deliver them by themselves)
    colony noted ID "TEXT"          mark a note as acted on, with what was done
    colony vision [--history]       read this project's vision or its dated changes
    colony restart                  reload the board with its code as it is now (consoles keep running)
    colony stop                     end the board, the monitor and every project console
    colony doctor [--tests]         is everything up and wired? what to do if not
    colony projects                 every project on the board, its console state and latest line
    colony send NAME "TEXT" [--ask] a message to another project's agent; --ask expects an answer
    colony reply ID "TEXT"          answer a message
    colony mail [--project NAME]    a project's mail, in and out
    colony peek NAME [-n 30]        a project console's last lines
    colony tell NAME "TEXT"         send a message into a project's console, as the person would
    colony choose NAME "OPTION"     pick an option a project is showing (trust question, permission prompt)
    colony new NAME [--in DIR]      create a project, put it on the board, start its console
    colony consult R4 "QUESTION" --digest FILE   two fresh views at a decision costly to change
    colony settings [KEY VALUE] [--project NAME]   global options, or one project's own
    colony urls                     every address the board can be opened at
    colony setup                    the monitor walks you through first-time setup (again)
    colony bench [card MODEL | discover | fetch | key]   benchmark cards; discover asks each program its models;
                                    fetch pulls Artificial Analysis' data; key reads the key from stdin
    colony models [set TIER MODEL EFFORT | reset TIER]  this project's helper tiers (routine, step-up, chores)
    colony helm [on|off]            whether the monitor answers routine questions for the person
    colony page [--port 8788]       the project at a glance, for the person, with a note box on every row
    colony map [QUERY]              rebuild the map; with QUERY, what exists that bears on it
    colony field view|signal|resolve   the channel agents use (their name, row and wave are set for them)

Run it from the project root, or set COLONY_ROOT.
"""
import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import clock, field, mapper, memory, specialists
from .claude import call
from .project import Project

PROMPTS = Path(__file__).parent / "prompts"


def add_lines(path, lines):
    have = path.read_text() if path.exists() else ""
    missing = [l for l in lines if l not in have.splitlines()]
    if missing:
        path.write_text(have + ("" if not have or have.endswith("\n") else "\n") + "\n".join(missing) + "\n")


def cmd_init(a):
    project = Project(a.dir)
    project.root.mkdir(parents=True, exist_ok=True)
    if (project.state).exists():
        print(f"colony: {project.root} is already a colony project", file=sys.stderr)
        return 2
    subprocess.run(["git", "-C", str(project.root), "init", "-q", "-b", "main"], check=False)
    project.design.mkdir(exist_ok=True)
    project.state.mkdir()
    # An existing project keeps its own files: colony adds its lines and replaces nothing.
    add_lines(project.root / ".gitignore", [".colony/transcripts/", ".colony/map.md", ".colony/map.json",
                                            "scratch/", "__pycache__/"])
    specialists.ensure_defaults(project)
    for name in ("AGENTS.md", "CLAUDE.md"):
        add_lines(project.root / name, ["The goal and the plan are in design/spine.md. Ask the map "
                                        "(`python3 -m colony map QUERY`) before making anything new."])
    clock.commit(project, "colony init")
    print(f"initialised {project.root}; next: colony door --goal \"...\"")
    return 0


def cmd_door(a):
    project = Project.here()
    specialists.ensure_defaults(project)
    record = call(project, (PROMPTS / "door.md").read_text().format(goal=a.goal),
                  agent="door", row=0, wave=0, budget=a.budget)
    memory.ledger(project, "door", goal=a.goal, cost_usd=record["cost_usd"])
    clock.commit(project, "door: draft spine")
    print(f"drafted design/spine.md, design/questions.md and {len(specialists.load(project))} reviewer(s) in .claude/agents "
          f"(${record['cost_usd']:.2f}). Read and correct them, then: colony approve")
    return 0


def cmd_approve(a):
    project = Project.here()
    text = project.spine.read_text()
    if memory.approved(project):
        print("already approved")
        return 0
    text = re.sub(r"^\*\*Approved:\*\*.*$", "**Approved:** yes", text, flags=re.M) \
        if re.search(r"^\*\*Approved:\*\*", text, re.M) else text.rstrip() + "\n\n**Approved:** yes\n"
    project.spine.write_text(text)
    memory.ledger(project, "approved", by=os.environ.get("USER", "person"))
    clock.commit(project, "spine approved")
    print(review_plan(project))
    print("approved; next: colony run")
    return 0


def review_plan(project):
    """Exactly what will be reviewed and what will not, while it can still be changed: the breadth of
    review decides most of a project's cost, and nobody should have to infer it."""
    cfg, risky = project.config(), memory.risky(project)
    rows = [(n, memory.impact(project, n)) for n, _, _ in memory.rows(project)]
    if cfg["review"] in ("never", "always"):
        return f"Review is set to {cfg['review']}: {'every' if cfg['review'] == 'always' else 'no'} row will be reviewed."
    at = cfg.get("review_at_impact")
    lines = ["Review plan (a reviewed row cost 1.2 to 1.7 times an unreviewed one in testing):"]
    lines.append("- any change touching " + ", ".join(risky) if risky else "- no risky areas: no change is reviewed for where it lands")
    if at is not None:
        lines.append(f"- rows at impact {at} or above, whatever they touch: "
                     + (", ".join(str(n) for n, i in rows if i is not None and i >= at) or "none yet"))
    high = [n for n, i in rows if i is not None and i >= 9 and (at is None or i < at)]
    if high:
        lines.append(f"- rows {', '.join(map(str, high))} have impact 9 or 10 but are reviewed only where they touch a "
                     "risky area; to review them regardless, set \"review_at_impact\": 9 in .colony/config.json")
    return "\n".join(lines)


def running(project):
    """The run in progress, or None: a lock whose process is gone is only a run that was killed."""
    lock = project.state / "run.json"
    if not lock.exists():
        return None
    run = json.loads(lock.read_text())
    try:
        os.kill(run["pid"], 0)
        return run
    except OSError:
        return None


def cmd_run(a):
    """A run belongs to the project, not to the session that started it: it detaches, so closing the
    session cannot kill a row halfway, and `colony wait` follows it from anywhere."""
    project = Project.here()
    live = running(project)
    if live and live["pid"] != os.getpid():
        print(f"a run is already going (since {live['started']}); `colony wait` follows it")
        return 0
    if not a.attached:
        args = [sys.executable, "-m", "colony", "run", "--attached"]
        args += (["--rows", str(a.rows)] if a.rows else []) + (["--cap", str(a.cap)] if a.cap else [])
        home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, COLONY_ROOT=str(project.root),
                   PYTHONPATH=os.pathsep.join(filter(None, [home, os.environ.get("PYTHONPATH")])))
        with open(project.state / "run.log", "a") as log:
            child = subprocess.Popen(args, cwd=project.root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True)
        # The lock is written before returning, so `colony wait` right after can never miss the run.
        (project.state / "run.json").write_text(json.dumps({"pid": child.pid, "started": time.strftime("%Y-%m-%d %H:%M")}))
        print("running in the background, safe from this session closing. `colony wait` blocks until it "
              "stops and says how it went; `colony page` shows it live.")
        return 0
    lock = project.state / "run.json"
    lock.write_text(json.dumps({"pid": os.getpid(), "started": time.strftime("%Y-%m-%d %H:%M")}))
    before = clock.spent(project)
    try:
        n = clock.run(project, max_rows=a.rows, cap=a.cap)
        stopped = None
    except clock.Stop as stop:
        n, stopped = None, str(stop)
        memory.ledger(project, "run-stopped", row=memory.next_row(project), reason=stopped)
    finally:
        lock.unlink(missing_ok=True)
    closed = [e for e in project.read("ledger.jsonl") if e["kind"] == "row-closed"]
    memory.ledger(project, "run-finished", closed=n if n is not None else None, stopped=stopped,
                  spent_usd=round(clock.spent(project) - before, 2), last_closed=closed[-1]["row"] if closed else None)
    print(finished_text(project))
    return 3 if stopped else 0


def finished_text(project):
    runs = [e for e in project.read("ledger.jsonl") if e["kind"] == "run-finished"]
    if not runs:
        return "no run has finished yet"
    r = runs[-1]
    head = f"stopped: {r['stopped']}" if r["stopped"] else f"{r['closed']} row(s) closed"
    return f"{head}\n${r['spent_usd']:.2f} this run; `colony cost` for the detail, `colony page` for the whole picture"


def cmd_wait(a):
    """Block until the run stops, then say how it went."""
    project = Project.here()
    deadline = time.time() + a.timeout if a.timeout else None
    while running(project):
        if deadline and time.time() > deadline:
            print("still running; `colony wait` again to keep following it")
            return 124
        time.sleep(3)
    if (project.state / "run.json").exists():
        (project.state / "run.json").unlink()
        print(f"the last run was killed before it finished; `colony run` starts again at row {memory.next_row(project)}")
        return 3
    print(finished_text(project))
    runs = [e for e in project.read("ledger.jsonl") if e["kind"] == "run-finished"]
    return 3 if runs and runs[-1]["stopped"] else 0


def cmd_status(a):
    project = Project.here()
    print("Rows:")
    for n, target, _ in memory.rows(project):
        print(f"  {n}. {target}")
    live = field.signals(project, wave=int(os.environ.get("COLONY_WAVE", "0")))
    print("\nOpen signals:" + ("".join(f"\n  {field.render(s)}" for s in live) if live else " none"))
    return 0


def cmd_cost(a):
    project = Project.here()
    rows, agents = {}, {}
    keys = ("cost_usd", "input", "output", "thinking", "cache_write", "cache_read")
    for r in project.read("usage.jsonl"):
        for bucket, key in ((rows, r["row"]), (agents, r["agent"].split("@")[0])):
            agg = bucket.setdefault(key, {k: 0 for k in keys} | {"calls": 0})
            agg["calls"] += 1
            for k in keys:
                agg[k] += r.get(k, 0)
    total = {k: sum(v[k] for v in rows.values()) for k in keys}
    print(json.dumps({"total": total, "by_row": rows, "by_agent": agents}, indent=2))
    return 0


def cmd_checkpoint(a):
    """A broad look at effectiveness since the last checkpoint, computed from the records: no tokens."""
    from . import health
    project = Project.here()
    text, row, questions = health.overview(project)
    print(text)
    memory.ledger(project, "checkpoint", row=row, overview=text, questions=questions)
    return 0


def cmd_answer(a):
    """The person's answer to a checkpoint question: it reaches the next row's builder, and with --always
    it becomes a rule in the spine, so the question is not asked again."""
    project = Project.here()
    memory.answer(project, a.kind, a.text, a.always)
    print(f"answered {a.kind}" + (" — kept as a rule in the spine" if a.always else ""))
    return 0


def cmd_page(a):
    from . import page
    page.serve(Project.here(), a.port)
    return 0


def cmd_map(a):
    project = Project.here()
    _, changed = mapper.build(project)
    if a.query:
        print(mapper.render(mapper.query(project, " ".join(a.query))))
    else:
        print(f".colony/map.md refreshed ({len(changed)} file(s) re-read)")
    return 0


def cmd_field(a):
    project = Project.here()
    me = os.environ.get("COLONY_AGENT", "person")
    row = int(os.environ.get("COLONY_ROW", "0"))
    wave = int(os.environ.get("COLONY_WAVE", "0"))
    try:
        if a.action == "view":
            live = field.signals(project, row=row or None, wave=wave)
            print("\n".join(field.render(s) for s in live) or "the field is quiet")
        elif a.action == "signal":
            print(f"#{field.post(project, by=me, row=row, wave=wave, kind=a.kind, severity=a.severity, at=a.at, text=a.text)}")
        else:
            if me != "builder":
                raise ValueError("only the builder answers signals")
            if a.fixed == a.declined:
                raise ValueError("say --fixed or --declined")
            print(f"#{field.resolve(project, by=me, row=row, wave=wave, of=a.id, text=a.text, fixed=a.fixed)}")
    except ValueError as e:
        print(f"colony: {e}", file=sys.stderr)
        return 2
    return 0


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
        try:
            root = board.sharing(a.path, a.name, chosen)
        except (ValueError, KeyError) as err:
            raise SystemExit(f"colony: {err}")
        print(f"{root.name} is on the board, working in {board.workdir(root)}. Open it with: colony board")
        return 0
    if chosen:
        try:
            board.project_settings(Path(a.path).expanduser().resolve(), chosen)   # before wiring: the provider wires
        except KeyError as err:
            raise SystemExit(f"no such choice for {err}: colony settings shows the options")
    root = board.track(a.path)
    print(f"{root.name} is on the board. Open it with: colony board")
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
    monitor can restart it; --foreground runs it here instead."""
    from . import board, monitor
    fresh = not (board.home() / "board.json").exists() and not (board.home() / "setup-asked").exists()
    settled = board.settle_provider()            # the default runs on a program that is here
    if fresh and not a.no_monitor:
        board.home().mkdir(parents=True, exist_ok=True)
        monitor.setup()                          # a new install: the monitor walks the person through setup
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
    for u in board.urls(a.port) if _lan(a) else [f"http://127.0.0.1:{a.port}/"]:
        print(f"  {u}")
    return 0


def cmd_urls(a):
    """Every address the running board can be opened at."""
    from . import board
    saved = board.home() / "server.json"
    args = json.loads(saved.read_text()) if saved.exists() else []
    port = int(args[args.index("--port") + 1]) if "--port" in args else a.port
    lan = "--lan" in args or (not args and board.registry()["settings"]["lan"])
    print(f"On this machine:      http://127.0.0.1:{port}/")
    for u in (board.urls(port)[1:] if lan else []):
        print(f"From your network:    {u}")
    if not lan:
        print("From your network:    off (colony settings lan on, then colony restart)")
    from . import providers
    if board.registry()["settings"]["remote"] and providers.installed(providers.get("claude")):
        # PROVIDER: Remote Control and the Claude app are Claude Code's; name each provider's own way here.
        print("From anywhere:        the Claude app, where each Claude Code session appears")
    return 0


def cmd_stop(a):
    """End the board, the monitor and every project console this board started, and nothing else."""
    from . import board, console, monitor
    names = [server(), monitor.name()] + [console.session_name(p) for p in board.projects()]
    ended = [n for n in names if subprocess.run(["tmux", "kill-session", "-t", n], capture_output=True).returncode == 0]
    print(f"stopped {len(ended)} session(s): {', '.join(ended) or 'none were running'}")
    return 0


def cmd_restart(a):
    """Reload the board with its code as it is now. Project consoles and the monitor keep running."""
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
    print("the board restarted; consoles and the monitor were not touched")
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
    for p in here:
        trouble = providers.enabled(p) and hasattr(p, "sandbox_problem") and p.sandbox_problem()
        if trouble:
            problems.append(trouble)
    if not here:
        problems.append("no agent program is installed: Claude Code (https://claude.com/claude-code) or Codex "
                        "(https://developers.openai.com/codex)")
    saved = board.home() / "server.json"
    args = json.loads(saved.read_text()) if saved.exists() else []
    port = args[args.index("--port") + 1] if "--port" in args else "8790"
    if _answers(port):
        print(f"ok    board answers on port {port}")
    else:
        problems.append(f"the board does not answer on port {port}: colony board, or colony restart"
                        + ("" if _free(int(port), False) else " (another program holds that port: colony stop, then colony board)"))
    if "--no-monitor" not in args:
        (print("ok    monitor session running") if monitor.snapshot()["state"] != "off"
         else problems.append("the monitor session is not running: colony restart starts it"))
    for p in board.projects():
        if not p.exists():
            problems.append(f"{p}: the folder is gone; remove it from {board.home() / 'board.json'}")
            continue
        if not providers.installed(providers.of(p)):
            problems.append(f"{p.name}: {providers.missing(providers.of(p))}; install it, or choose another: "
                            f"colony settings --project {p.name} provider NAME")
        elif not providers.of(p).wired(board.workdir(p)):
            problems.append(f"{p.name}: its board wiring is missing; colony track {p} restores it")
        else:
            print(f"ok    {p.name}: wired; console {console.snapshot(p, lines=1)['state']}")
    if a.tests:
        home = Path(__file__).resolve().parent.parent
        r = subprocess.run([sys.executable, "-m", "unittest", "tests.test_colony", "tests.test_board", "tests.test_selection", "tests.test_effort", "tests.test_codex_remote", "tests.test_vision"], cwd=home,
                           capture_output=True, text=True)
        (print("ok    the test suite passes") if r.returncode == 0
         else problems.append("the test suite fails:\n" + r.stderr[-1500:]))
    for pr in problems:
        print("PROBLEM " + pr)
    return 1 if problems else 0


def cmd_gate(a):
    from . import board
    root = board.root_of()
    if a.answered:                       # the person settled it in conversation: record it, and it stops waiting
        try:
            board.answer_gate(root, a.answered, a.question, tell=False)
        except StopIteration:
            print(f"no gate {a.answered}", file=sys.stderr)
            return 2
        print(f"gate {a.answered} answered: {a.question}")
        return 0
    gid = "g" + __import__("secrets").token_hex(3)
    board.append(root, "gates.jsonl", {"type": "gate", "id": gid, "at": board.now(), "question": a.question,
                                       "item": a.item, "why": a.why})
    print(f"gate {gid} is waiting on the person; do not proceed on it until the answer arrives as a note")
    return 0


def cmd_consult(a):
    """Consult two fresh models from different families at a decision costly to change; or record which of their
    points the person accepted; or show a consultation again."""
    from . import board, consult
    root = board.root_of()
    if a.adopt:
        try:
            consult.adopt(root, a.adopt, a.decision)
        except KeyError:
            print(f"no consultation {a.adopt}", file=sys.stderr)
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
        pin = pins.add(root, a.target, a.title or "", a.why or "", by="agent")
    except FileNotFoundError:
        print(f"no file {a.target} in this project", file=sys.stderr)
        return 2
    print(f"pinned {pin['id']}: {pin['title']}")
    return 0


def cmd_unpin(a):
    from . import board, pins
    root = board.root_of()
    if not pins.get(root, a.id):
        print(f"no pin {a.id}", file=sys.stderr)
        return 2
    pins.remove(root, a.id)
    print(f"unpinned {a.id}")
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
        from . import selection
        selection.reconcile()
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
    """Ask the monitor to walk the person through first-time setup again."""
    from . import monitor
    monitor.setup()
    print("the monitor will walk you through setup when it's next free (the board's monitor page)")
    return 0


def cmd_pins(a):
    from . import board, pins
    for p in pins.pins(board.root_of()):
        print(f"{p['id']}  {p['kind']:6} {p['by']:6} {p['title']}  ({p['target']})")
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


def _console_hook_allowed(root, provider):
    """Optional hook scope: a project's board console running the expected provider, never cwd alone."""
    from . import context, providers
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
    if a.console == 'codex':
        from . import codex_remote
        if not codex_remote.accepts_hook(root, payload):
            return 0
    from . import context
    payload.setdefault("hook_event_name", "Stop")
    if context.carry_reply(root, a.console or providers.key(context.program(root)), payload):
        return 0
    key, text = context.program(root).turn_text(payload)
    board.record_ask(root, key, text)
    board.said_reply(root, text)                    # the agent's answer to the person's own words, if they spoke
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
        if a.console == 'codex':
            from . import codex_remote
            if not codex_remote.accepts_hook(root, payload):
                return 0
        from . import vision, context
        payload.setdefault("hook_event_name", "SessionStart" if a.session else "UserPromptSubmit")
        context.register(root, a.console or providers.key(context.program(root)), payload)
        vision.observe(root)
        seen = getattr(providers.of(root), "conversation", None)
        if seen and os.environ.get("COLONY_CONSOLE") == console.session_name(root):   # the board's console, no other here
            console.remember(root, *seen(payload))
        prompt = str(payload.get("prompt") or "")
        if prompt and not prompt.startswith("[colony]"):
            board.answer_asks(root, "in the console")        # the person answered there themselves
            board.said(root, prompt)                         # their own words, for the monitor to catch up on
        from . import bench
        standing = bench.plan_text(root) if a.session else ""       # the helper tiers, every session
        fresh, still = board.deliver(root, session=a.session)
        if any(not n.get("quiet") and not n["anchor"] and n.get('author') != 'observation' for n in fresh):
            board.answer_asks(root, "by a note")                 # the person (or their monitor) wrote back
        new_mail, open_asks = mail.deliver(root, session=a.session)
        text = "\n\n".join(filter(None, [
            standing,
            board.render_notes([n for n in fresh if n.get("author") not in ("colony", "observation")], "The person left notes for you on the board:"),
            board.render_notes([n for n in fresh if n.get("author") == "colony"], "Colony, the harness the person set up and trusts, tells you (with their full approval):"),
            board.render_notes([n for n in fresh if n.get("author") == "observation"], "Observed file changes (no author or agreement inferred):"),
            board.render_notes(still, "Still open from earlier (delivered, not yet acted on):"),
            mail.render(new_mail, "Mail from other projects in the colony:"),
            mail.render(open_asks, "Questions from the colony you haven't answered yet:")]))
    else:
        text = board.render_notes(board.open_notes(root, a.item), "Open notes:") or "No open notes."
    if text:
        print(text)
    return 0


def cmd_noted(a):
    from . import board
    root = board.root_of()
    if a.id not in {n["id"] for n in board.notes(root)}:
        print(f"no note {a.id}", file=sys.stderr)
        return 2
    board.append(root, "notes.jsonl", {"type": "addressed", "of": a.id, "at": board.now(), "text": a.text})
    note = next(n for n in board.notes(root) if n["id"] == a.id)
    if note.get("author") == "suggestion":            # the monitor made it, so the answer is the monitor's to hear
        from . import monitor
        monitor.queue(f"{root.name} answered your suggestion ({note['text'][:80]}...): {a.text}")
    print(f"{a.id} marked as acted on")
    return 0


def _project(name):
    from . import board
    for p in board.projects():
        if p.name == name:
            return p
    raise SystemExit(f"no project named {name} on the board; `colony projects` lists them")


def cmd_projects(a):
    from . import board, console, mail
    for p in board.projects():
        snap = console.snapshot(p, lines=1) if p.exists() else {"state": "missing", "lines": []}
        waiting = len(board.moments(p))
        goal = ' '.join(board.roadmap(p)["goal"].split()) if p.exists() else ""
        print(f"{mail.address(p):22} {snap['state']:10} {str(waiting) + ' waiting on you' if waiting else '':16} {goal[:90]}")
    return 0


def cmd_peek(a):
    from . import console
    snap = console.snapshot(_project(a.name), lines=a.lines)
    print(f"[{snap['state']}]")
    print("\n".join(snap["lines"]))
    return 0


def _caught_up(root):
    """Run by the monitor's own console: if the person has spoken to this project directly since the monitor last
    caught up, nothing is sent; their words are shown instead, so the monitor never acts on a stale picture of
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
    monitor last caught up on it: their own words only."""
    from . import monitor
    words = monitor.catch_up(_project(a.name))
    print(words or f"Nothing new from the person in {a.name} since you last caught up.")
    return 0


def cmd_tell(a):
    """The monitor speaks for the person: as a note from them, delivered through the hooks into the agent's own
    context (typed text arrives as a paste, which an agent rightly doesn't take as the person's word), and a
    one-line nudge if the session is idle. It shows on the board like any note."""
    from . import board, console
    root = _project(a.name)
    if not _caught_up(root):
        return 3
    board.add_note(root, None, a.text, author="monitor")
    name = console.ensure(root)
    if console.snapshot(root, lines=1)["state"] == "idle":   # held while someone is typing there; the watcher nudges later
        console.type_into(name, "[colony] You have a note from the person on the board.")
    print(f"sent to {a.name} as a note from the person, via the monitor")
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
            print(f"{k:12} {str(merged[k]) or '(colony Auto)':24} {'set for this project' if k in own else 'global'}")
        return 0
    if a.key:
        try:
            board.set_setting(a.key, a.value or "")
        except KeyError:
            raise SystemExit(f"no setting {a.key}; the settings are: {', '.join(board.DEFAULT_SETTINGS)}, new-folder")
    reg = board.registry()
    from . import providers
    for k in board.DEFAULT_SETTINGS:
        v = reg["settings"][k]
        shown = (("on" if v else "off") if isinstance(v, bool) else ", ".join(v or providers.PROVIDERS) if k == "providers"
                 else ", ".join(f"{f}={c['model']}:{c['effort']}" for f, c in v.items()) or "(from the benchmark cards)"
                 if k == "consultants" else v or "(colony Auto)")
        print(f"{k:14} {str(shown):28} {board.SETTING_HELP.get(k, '')}")
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
    except PermissionError as err:
        raise SystemExit(str(err))
    print(f"sent {m['id']} to {a.to}" + (" (it will answer with colony reply)" if a.ask else ""))
    return 0


def cmd_reply(a):
    from . import mail
    try:
        m = mail.reply(a.id, mail.read_text(a.text))
    except KeyError:
        raise SystemExit(f"no message {a.id} here; `colony mail` lists them")
    print(f"answered {a.id}: sent {m['id']} to {m['to']}")
    return 0


def cmd_mail(a):
    from . import board, mail
    root = _project(a.project) if a.project else board.root_of()
    ms = mail.messages(root)
    if not ms:
        print("no mail")
    me = mail.address(root)
    for m in ms[-a.last:]:
        way = f"from {m['from']}" if m["to"] == me else f"to {m['to']}"
        flag = (" [answered]" if m["answer"] else " [asks]") if m["ask"] else ""
        print(f"{m['id']}  {m['at'][:16]}  {way}{flag}: {m['text']}")
    return 0


def cmd_helm(a):
    from . import monitor
    if a.project:
        root = _project(a.project)
        if a.state:
            monitor.set_posture(root, helm=a.state == "on")
        included = monitor.posture(root)["helm"] is not False
        print(f"{a.project}: " + ("the monitor holds the helm" if monitor.helm_for(root) else
                                  "included in the helm, which is with the person" if included else "left out of the helm"))
        return 0
    if a.state:
        monitor.helm(a.state == "on")
    print("the monitor holds the helm" if monitor.helm() else "the helm is with the person")
    return 0


def cmd_posture(a):
    """The monitor's stance toward each project: helm, the person's direction, the current focus."""
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


def cmd_vision(a):
    from . import board, vision
    root = board.root_of()
    if a.file:
        text = sys.stdin.read() if a.file == '-' else Path(a.file).expanduser().read_text()
        try:
            event = vision.save(root, text, how='conversation', words=a.words or '')
        except ValueError as err:
            raise SystemExit(str(err))
        print('Vision updated from the agreed conversation.' if event else 'Vision is unchanged.')
    else:
        vision.observe(root)
        if a.history:
            for event in vision.history(root):
                print(f"{event['at']} · {event['how']}\n{event['text']}\n{event.get('words', '')}\n")
        else:
            print(board.roadmap(root)['goal'] or 'Shape the vision with the person.')
    return 0


def cmd_ready(a):
    """Tell the person, in plain words, what's ready for their OK and how to check it."""
    from . import board
    root = board.root_of()
    board.mark_ready(root, a.item, a.what, a.check or "")
    print(f"{a.item}: the person will see it ready for their OK")
    return 0


def cmd_remove(a):
    from . import board
    board.remove_project(_project(a.name))
    print(f"{a.name} is off the board; its files are where they were")
    return 0


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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="colony", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("dir"); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("door"); p.add_argument("--goal", required=True); p.add_argument("--budget", type=float, default=3.0)
    p.set_defaults(fn=cmd_door)
    sub.add_parser("approve").set_defaults(fn=cmd_approve)
    p = sub.add_parser("run"); p.add_argument("--rows", type=int); p.add_argument("--cap", type=float)
    p.add_argument("--attached", action="store_true", help="stay in the foreground"); p.set_defaults(fn=cmd_run)
    p = sub.add_parser("wait"); p.add_argument("--timeout", type=float, help="seconds"); p.set_defaults(fn=cmd_wait)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("cost").set_defaults(fn=cmd_cost)
    sub.add_parser("checkpoint").set_defaults(fn=cmd_checkpoint)
    p = sub.add_parser("answer"); p.add_argument("kind"); p.add_argument("text"); p.add_argument("--always", action="store_true")
    p.set_defaults(fn=cmd_answer)
    p = sub.add_parser("page"); p.add_argument("--port", type=int, default=8788); p.set_defaults(fn=cmd_page)
    p = sub.add_parser("track"); p.add_argument("path", nargs="?", default=".")
    p.add_argument("--name", help="a second project in a folder that has its own, under this name")
    for k in ("provider", "model", "effort", "permissions"):
        p.add_argument(f"--{k}", help="for this project (default: the global setting)")
    p.set_defaults(fn=cmd_track)
    p = sub.add_parser("board"); p.add_argument("--port", type=int)
    p.add_argument("--lan", action="store_true", help="also answer other machines on the network")
    p.add_argument("--no-monitor", action="store_true", help="don't start the monitor session and its watcher")
    p.add_argument("--foreground", action="store_true", help="run here instead of in its tmux session")
    p.add_argument("--local", action="store_true", help="answer only this machine, whatever the settings say")
    p.set_defaults(fn=cmd_board)
    sub.add_parser("restart").set_defaults(fn=cmd_restart)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    p = sub.add_parser("urls"); p.add_argument("--port", type=int, default=8790); p.set_defaults(fn=cmd_urls)
    p = sub.add_parser("doctor"); p.add_argument("--tests", action="store_true", help="also run the test suite")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("gate"); p.add_argument("question"); p.add_argument("--item"); p.add_argument("--why", default="")
    p.add_argument("--answered", metavar="ID", help="the person answered gate ID in conversation; QUESTION is their answer")
    p.set_defaults(fn=cmd_gate)
    sub.add_parser("statusline", help="(Claude Code's status line) record its usage limits").set_defaults(fn=cmd_statusline)
    p = sub.add_parser("consult", help="two fresh models from different families, at a decision costly to change")
    p.add_argument("decision", help="the roadmap item (R12) or a short name for the decision")
    p.add_argument("question", nargs="?", help="the decision, in a sentence or two")
    p.add_argument("--digest", metavar="FILE", help="your digest of the facts, each with its source (- for stdin)")
    p.add_argument("--plan", metavar="FILE", help="round two: your revised approach, for the consultants to check")
    p.add_argument("--adopt", metavar="ID", help="record which points of consultation ID the person accepted (DECISION is their words)")
    p.add_argument("--show", metavar="ID", help="show consultation ID again")
    p.set_defaults(fn=cmd_consult)
    p = sub.add_parser("notes"); p.add_argument("item", nargs="?"); p.add_argument("--deliver", action="store_true")
    p.add_argument("--console", metavar="PROVIDER", help="(hook) deliver only in this provider's matching board console")
    p.add_argument("--session", action="store_true"); p.set_defaults(fn=cmd_notes)
    p = sub.add_parser("said", help="(monitor) what the person said to a project directly since you last caught up")
    p.add_argument("name"); p.set_defaults(fn=cmd_said)
    p = sub.add_parser("noted"); p.add_argument("id"); p.add_argument("text"); p.set_defaults(fn=cmd_noted)
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
    p = sub.add_parser("pin", help="pin a file in the project or a URL for the person")
    p.add_argument("target"); p.add_argument("--title"); p.add_argument("--why"); p.set_defaults(fn=cmd_pin)
    p = sub.add_parser("unpin"); p.add_argument("id"); p.set_defaults(fn=cmd_unpin)
    sub.add_parser("pins", help="what is pinned for the person").set_defaults(fn=cmd_pins)
    sub.add_parser("setup", help="the monitor walks you through first-time setup").set_defaults(fn=cmd_setup)
    p = sub.add_parser("bench", help="benchmark cards for the models the board can run")
    p.add_argument("what", nargs="?", choices=("card", "import", "pending", "discover", "fetch", "key")); p.add_argument("arg", nargs="?")
    p.set_defaults(fn=cmd_bench)
    p = sub.add_parser("models", help="this project's helper tiers: routine, step-up, chores")
    p.add_argument("what", nargs="?", choices=("set", "reset")); p.add_argument("args", nargs="*"); p.add_argument("--why")
    p.set_defaults(fn=cmd_models)
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
    p = sub.add_parser("posture", help="the monitor's stance toward each project: helm, direction, focus")
    p.add_argument("name", nargs="?"); p.add_argument("--direction")
    p.add_argument("--scout", help="every how many hours to look for supports here (0: never)")
    p.add_argument("--favour", help="what scouting here should favour")
    p.add_argument("--scouting", choices=("on", "off"), help="scout for this project at all"); p.set_defaults(fn=cmd_posture)
    p = sub.add_parser('vision', help='read the vision or record a clearly agreed conversation change')
    p.add_argument('--file', help='new vision text (- reads stdin)')
    p.add_argument('--words', help='the person’s words agreeing the change; required with --file')
    p.add_argument('--history', action='store_true'); p.set_defaults(fn=cmd_vision)
    p = sub.add_parser("ready", help="tell the person, plainly, what's ready for their OK and how to check it")
    p.add_argument("item"); p.add_argument("what"); p.add_argument("--check"); p.set_defaults(fn=cmd_ready)
    p = sub.add_parser("remove", help="take a project off the board (its files stay)"); p.add_argument("name"); p.set_defaults(fn=cmd_remove)
    p = sub.add_parser("delete", help="delete a project: its folder moves to colony's trash"); p.add_argument("name"); p.set_defaults(fn=cmd_delete)
    p = sub.add_parser("supports", help="(monitor) tools offered to projects when their work calls for one, and how far each is trusted")
    p.add_argument("action", nargs="?", choices=("add", "set", "ask", "approve", "suggest", "check", "sources", "source", "block")); p.add_argument("name", nargs="?", help="add: its name; set: its id")
    p.add_argument("status", nargs="?"); p.add_argument("--for", dest="symptom", default=""); p.add_argument("--gives", default="")
    p.add_argument("--source", default=""); p.add_argument("--cost", default=""); p.add_argument("--remove", default="")
    p.add_argument("--evidence", default=""); p.add_argument("--project"); p.add_argument("--text", default=""); p.add_argument("--trust"); p.add_argument("--reference", action="store_true", help="add: a project to learn from, not a tool to install"); p.set_defaults(fn=cmd_supports)
    p = sub.add_parser("decided", help="(monitor) record what it settled for a project"); p.add_argument("name"); p.add_argument("text")
    p.set_defaults(fn=cmd_decided)
    p = sub.add_parser("map"); p.add_argument("query", nargs="*"); p.set_defaults(fn=cmd_map)
    f = sub.add_parser("field"); fs = f.add_subparsers(dest="action", required=True); f.set_defaults(fn=cmd_field)
    fs.add_parser("view")
    s = fs.add_parser("signal")
    s.add_argument("--kind", required=True, choices=field.KINDS); s.add_argument("--severity", required=True, choices=tuple(field.RANK))
    s.add_argument("--at", required=True); s.add_argument("--text", required=True)
    r = fs.add_parser("resolve"); r.add_argument("id", type=int); r.add_argument("--text", required=True)
    r.add_argument("--fixed", action="store_true"); r.add_argument("--declined", action="store_true")
    a = ap.parse_args(argv)
    return a.fn(a)
