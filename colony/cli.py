"""colony — drive a long project toward a person's goal with one builder, short-lived specialists,
located signals and a project memory.

    colony init DIR                 make DIR a colony project (git, design/, .colony/)
    colony door --goal "..."        draft the spine, specialists and questions from a goal
    colony approve                  the person approves design/spine.md; runs may start
    colony run [--rows N] [--cap USD]
    colony status                   the rows, NOW and the open signals
    colony cost                     dollars and tokens, per row and per agent
    colony calibration              the builder's own forecast beside what review then found
    colony checkpoint               effectiveness since the last checkpoint, from the records (no tokens)
    colony page [--port 8788]       the project at a glance, for the person, with a note box on every row
    colony map [QUERY]              rebuild the map; with QUERY, what exists that bears on it
    colony field view|signal|resolve   the channel agents use (their name, row and wave are set for them)

Run it from the project root, or set COLONY_ROOT.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import clock, field, mapper, memory, specialists
from .claude import call
from .project import Project

PROMPTS = Path(__file__).parent / "prompts"


def cmd_init(a):
    project = Project(a.dir)
    project.root.mkdir(parents=True, exist_ok=True)
    if (project.state).exists():
        print(f"colony: {project.root} is already a colony project", file=sys.stderr)
        return 2
    subprocess.run(["git", "-C", str(project.root), "init", "-q", "-b", "main"], check=False)
    project.design.mkdir(exist_ok=True)
    project.state.mkdir()
    (project.root / ".gitignore").write_text(".colony/transcripts/\n.colony/map.md\n.colony/map.json\nscratch/\n__pycache__/\n")
    specialists.ensure_defaults(project)
    for name in ("AGENTS.md", "CLAUDE.md"):
        (project.root / name).write_text("Read design/spine.md, then design/now.md. Ask the map "
                                         "(`python3 -m colony map QUERY`) before making anything new.\n")
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
    risky = memory.risky(project)
    if risky:
        # The breadth of the risky areas decides most of a project's cost; say so while it can be changed.
        print("Every change touching these risky areas will be reviewed: " + ", ".join(risky) + ".\n"
              "In testing, a reviewed row cost about four times an unreviewed one. Narrow the list in\n"
              "design/spine.md to what a mistake would really cost you, or set review to never in\n"
              ".colony/config.json.")
    print("approved; next: colony run")
    return 0


def cmd_run(a):
    project = Project.here()
    try:
        n = clock.run(project, max_rows=a.rows, cap=a.cap)
        print(f"{n} row(s) closed; ${clock.spent(project):.2f} spent in all")
        return 0
    except clock.Stop as stop:
        memory.ledger(project, "run-stopped", reason=str(stop))
        print(f"stopped: {stop}")
        return 3


def cmd_status(a):
    project = Project.here()
    print("Rows:")
    for n, target, _ in memory.rows(project):
        print(f"  {n}. {target}")
    print("\n" + memory.now_text(project).strip())
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


def cmd_calibration(a):
    """How the builder's own assessments compared with what review then found, row by row."""
    project = Project.here()
    ledger = project.read("ledger.jsonl")
    reviews = {e["row"]: e for e in ledger if e["kind"] == "review"}
    closed = {e["row"]: e for e in ledger if e["kind"] == "row-closed"}
    rows = []
    for n in sorted(reviews):
        a_ = reviews[n].get("assessment") or {}
        rows.append({"row": n, "confidence": a_.get("confidence"), "impact": a_.get("impact"), "risk": a_.get("risk"),
                     "reviewed": reviews[n]["review"], "review_fixes": closed.get(n, {}).get("review_fixes"),
                     "note": a_.get("note")})
    moves = [e for e in ledger if e["kind"] == "threshold"]
    print(json.dumps({"rows": rows, "threshold_moves": moves}, indent=2))
    return 0


def cmd_checkpoint(a):
    """A broad look at effectiveness since the last checkpoint, computed from the records: no tokens."""
    from . import health
    project = Project.here()
    text, row = health.overview(project)
    print(text)
    memory.ledger(project, "checkpoint", row=row, overview=text)
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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="colony", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("dir"); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("door"); p.add_argument("--goal", required=True); p.add_argument("--budget", type=float, default=3.0)
    p.set_defaults(fn=cmd_door)
    sub.add_parser("approve").set_defaults(fn=cmd_approve)
    p = sub.add_parser("run"); p.add_argument("--rows", type=int); p.add_argument("--cap", type=float); p.set_defaults(fn=cmd_run)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("cost").set_defaults(fn=cmd_cost)
    sub.add_parser("calibration").set_defaults(fn=cmd_calibration)
    sub.add_parser("checkpoint").set_defaults(fn=cmd_checkpoint)
    p = sub.add_parser("page"); p.add_argument("--port", type=int, default=8788); p.set_defaults(fn=cmd_page)
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
