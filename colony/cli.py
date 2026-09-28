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
    colony settings [KEY VALUE] [--project NAME]   global options, or one project's own
    colony urls                     every address the board can be opened at
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


def cmd_track(a):
    from . import board
    if not Path(a.path).expanduser().is_dir():
        print(f"colony: no folder {a.path}", file=sys.stderr)
        return 2
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
    from . import board
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
    if board.registry()["settings"]["remote"]:
        # PROVIDER: Remote Control and the Claude app are Claude Code's; name each provider's own way here.
        print("From anywhere:        the Claude app, where each project's session and the monitor appear")
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
        if not providers.of(p).wired(p):
            problems.append(f"{p.name}: its board wiring is missing; colony track {p} restores it")
        else:
            print(f"ok    {p.name}: wired; console {console.snapshot(p, lines=1)['state']}")
    if a.tests:
        home = Path(__file__).resolve().parent.parent
        r = subprocess.run([sys.executable, "-m", "unittest", "tests.test_colony", "tests.test_board"], cwd=home,
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


def cmd_turn(a):
    """The provider's end-of-turn hook: if the turn asked the person something, it waits on them."""
    from . import board, providers
    root = board.root_of()
    if not (root / ".board").exists():
        return 0
    key, text = providers.of(root).turn_text(_hook_input())
    board.record_ask(root, key, text)
    return 0


def cmd_notes(a):
    from . import board
    root = board.root_of()
    if not (root / ".board").exists():
        return 0                                  # not on the board: the hooks stay silent
    if a.deliver:
        # Printed for the provider to put in the agent's context: Claude Code's hooks do (providers.py wire()).
        from . import mail
        prompt = str(_hook_input().get("prompt") or "")
        if prompt and not prompt.startswith("[colony]"):
            board.answer_asks(root, "in the console")        # the person answered there themselves
        fresh, still = board.deliver(root, session=a.session)
        new_mail, open_asks = mail.deliver(root, session=a.session)
        text = "\n\n".join(filter(None, [
            board.render_notes(fresh, "The person left notes for you on the board:"),
            board.render_notes(still, "Still open from earlier (delivered, not yet acted on):"),
            mail.render(new_mail, "Mail from other projects in the colony:"),
            mail.render(open_asks, "Questions from the colony you haven't answered yet:")]))
    else:
        text = board.render_notes(board.open_notes(root, a.item), "Open notes from the person:") or "No open notes."
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
        goal = board.roadmap(p)["goal"] if p.exists() else ""
        print(f"{mail.address(p):22} {snap['state']:10} {str(waiting) + ' waiting on you' if waiting else '':16} {goal[:90]}")
    return 0


def cmd_peek(a):
    from . import console
    snap = console.snapshot(_project(a.name), lines=a.lines)
    print(f"[{snap['state']}]")
    print("\n".join(snap["lines"]))
    return 0


def cmd_tell(a):
    """The monitor speaks for the person: as a note from them, delivered through the hooks into the agent's own
    context (typed text arrives as a paste, which an agent rightly doesn't take as the person's word), and a
    one-line nudge if the session is idle. It shows on the board like any note."""
    from . import board, console
    root = _project(a.name)
    board.add_note(root, None, a.text, author="monitor")
    name = console.ensure(root)
    if console.snapshot(root, lines=1)["state"] == "idle":
        console.type_into(name, "[colony] You have a note from the person on the board.")
    print(f"sent to {a.name} as a note from the person, via the monitor")
    return 0


def cmd_new(a):
    from . import board, console
    root = Path(a.within or board.registry()["new_root"]).expanduser() / a.name
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"{root} already exists and is not empty")
    root.mkdir(parents=True, exist_ok=True)
    chosen = {k: getattr(a, k) for k in board.PROJECT_KEYS if getattr(a, k, None)}
    try:
        board.project_settings(root, chosen)            # before wiring: the chosen provider does the wiring
    except KeyError as err:
        raise SystemExit(f"no such choice for {err}: colony settings shows the options")
    board.track(root)
    console.ensure(root)
    print(f"{a.name} created at {root}, on the board, with its console running")
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
            print(f"{k:12} {str(merged[k]) or '(provider default)':24} {'set for this project' if k in own else 'global'}")
        return 0
    if a.key:
        try:
            board.set_setting(a.key, a.value or "")
        except KeyError:
            raise SystemExit(f"no setting {a.key}; the settings are: {', '.join(board.DEFAULT_SETTINGS)}, new-folder")
    reg = board.registry()
    for k, v in reg["settings"].items():
        shown = ("on" if v else "off") if isinstance(v, bool) else (v or "(the provider's default)")
        print(f"{k:10} {shown:28} {board.SETTING_HELP[k]}")
    print(f"{'new-folder':10} {reg['new_root']:28} where new projects are created")
    print(f"{'folders':10} {', '.join(reg['roots']) or '(none)'}")
    return 0


def cmd_choose(a):
    """Pick an option a project's agent is showing (a trust question, a permission prompt) by its text:
    `colony tell` would type the text and press Enter on whatever is highlighted."""
    from . import console, providers
    root = _project(a.name)
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
        print(f"{a.project}: " + ("the monitor holds the helm" if monitor.helm_for(root) else "the helm is with the person"))
        return 0
    if a.state:
        monitor.helm(a.state == "on")
    print("the monitor holds the helm" if monitor.helm() else "the helm is with the person")
    return 0


def cmd_posture(a):
    """The monitor's stance toward each project: helm, the person's direction, the current focus."""
    from . import board, monitor
    if a.name and a.direction is not None:
        monitor.set_posture(_project(a.name), direction=a.direction)
    for p in [_project(a.name)] if a.name else board.projects():
        f, pos = board.focus(p), monitor.posture(p)
        print(f"{p.name}: helm {'on' if monitor.helm_for(p) else 'off'}{'' if pos['helm'] is not None else ' (board-wide)'}")
        print(f"  direction: {pos['direction'] or '(none given)'}")
        print(f"  focus: {f['milestone'] or '(no roadmap)'}")
        for label in ("doing", "verify", "next"):
            if f[label]:
                print(f"    {label}: " + "; ".join(f[label]))
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


def cmd_decided(a):
    from . import monitor
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
    p = sub.add_parser("track"); p.add_argument("path", nargs="?", default="."); p.set_defaults(fn=cmd_track)
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
    p = sub.add_parser("notes"); p.add_argument("item", nargs="?"); p.add_argument("--deliver", action="store_true")
    p.add_argument("--session", action="store_true"); p.set_defaults(fn=cmd_notes)
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
    sub.add_parser("turn", help="(hook) a turn ended; record it if it asks the person something").set_defaults(fn=cmd_turn)
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
    p.add_argument("name", nargs="?"); p.add_argument("--direction"); p.set_defaults(fn=cmd_posture)
    p = sub.add_parser("ready", help="tell the person, plainly, what's ready for their OK and how to check it")
    p.add_argument("item"); p.add_argument("what"); p.add_argument("--check"); p.set_defaults(fn=cmd_ready)
    p = sub.add_parser("remove", help="take a project off the board (its files stay)"); p.add_argument("name"); p.set_defaults(fn=cmd_remove)
    p = sub.add_parser("delete", help="delete a project: its folder moves to colony's trash"); p.add_argument("name"); p.set_defaults(fn=cmd_delete)
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
