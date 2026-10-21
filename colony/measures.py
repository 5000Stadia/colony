"""Measures: a number a project steers by, taken by colony, never reported by the agent doing the work.

A part of the plan (or the plan above its first milestone) may carry a line

  Measure: words / 80000 · count: wc -w chapters/*.md
  Measure: fidelity / 0.95 · run: python evals/fidelity.py
  Measure: chapters revised / 24 · judge: each chapter in chapters/ against its outline in outline.md

its name, its target after a slash (if it has one), and how it is taken: a command (count or run) run in the
project as it stood at that commit, the first number on the last line it prints being the value; or a judgement
(judge) by a small separate model reading what the line names, which answers with a number and a reason. It is
taken at each push (the branch's upstream moving; without one, each commit) and each milestone finished, in colony's
own copy of the project at that commit (its tracked files), never the folder an agent is editing, and time-limited.
Every value taken is kept on this machine with its commit and method: a changed method shows as a new start, a
failure shows as a failure, never an old value as new. A command's values can be taken again at old commits; a
judgement can't, so its record is the only one.
"""
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import threading
import io
from pathlib import Path

from . import board

LINE = re.compile(r"^\s*Measure:\s*(.+?)\s*·\s*(count|run|judge):\s*(.+?)\s*$", re.I)
NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?|-?\.\d+")
TIMEOUT = 120                   # seconds a command or a judgement may take
SPARK = "▁▂▃▄▅▆▇█"
_running = set()
_lock = threading.Lock()


def number(text):
    m = NUMBER.search(text or "")
    return float(m.group(0).replace(",", "")) if m else None


def read(text):
    """The plan's measures, in order: [{name, target, how, what, part}], outside its Vision."""
    from . import vision
    part = vision.section(text)
    if part['start'] is not None:
        text = text[:part['start']] + text[part['end']:]
    out, where = [], None
    for line in text.splitlines():
        if m := board.MILESTONE.match(line):
            where = m.group(1)
        elif line.startswith("## "):
            where = line[3:].strip()
        elif m := LINE.match(line):
            name, _, target = m.group(1).partition("/")
            out.append({"name": name.strip(), "target": number(target) if target.strip() else None,
                        "how": m.group(2).lower(), "what": m.group(3), "part": where})
    return out


def folder(root):
    return board.home() / "measures" / hashlib.sha1(str(Path(root).resolve()).encode()).hexdigest()[:12]


def taken(root):
    """Every value taken for the project, oldest first."""
    path = folder(root) / "values.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def check(root):
    """Called every few seconds per project: take the plan's measures when the branch has been pushed or a milestone
    finished since they were last taken, in the background, one run per project at a time."""
    from . import lead
    path = lead.plan_path(root)
    if not path.exists():
        return
    text = path.read_text()
    if "Measure:" not in text:
        return
    wanted = read(text)
    if not wanted:
        return
    tip = git(root, "rev-parse", "@{u}") or git(root, "rev-parse", "HEAD")
    if not tip:
        return
    finished = sorted(m["id"] for m in board.roadmap(root, text)["milestones"]
                      if m["items"] and all(i["state"] == "done" for i in m["items"]))
    state_path = folder(root) / "state.json"
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    methods = sorted(f"{w['name']}|{w['how']}|{w['what']}" for w in wanted)
    if state.get("tip") == tip and state.get("finished") == finished and state.get("methods") == methods:
        return
    with _lock:
        if str(root) in _running:
            return
        _running.add(str(root))
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"tip": tip, "finished": finished, "methods": methods}))
    threading.Thread(target=_take_all, args=(Path(root), tip, wanted), daemon=True).start()


def _take_all(root, tip, wanted):
    try:
        take(root, tip, wanted)
    except Exception as err:                  # the copy at the commit failed: each measure says so
        path = folder(root) / "values.jsonl"
        with path.open("a") as f:
            for w in wanted:
                f.write(json.dumps({"at": board.now(), "commit": tip[:12], "name": w["name"], "target": w["target"],
                                    "how": w["how"], "what": w["what"], "value": None, "reason": None,
                                    "error": f"couldn't take it: {err.__class__.__name__}"}) + "\n")
    finally:
        with _lock:
            _running.discard(str(root))


def copy_at(root, tip):
    """The project's tracked files as they stood at the commit, in colony's own folder for it."""
    at = folder(root) / "at"
    if at.exists():
        shutil.rmtree(at)
    at.mkdir(parents=True)
    data = subprocess.run(["git", "-C", str(root), "archive", tip], capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(at, filter="data")
    return at


def take(root, tip, wanted, judge=None, run=None):
    """Take each measure at the commit and keep what came of it."""
    at = copy_at(root, tip)
    out = []
    for w in wanted:
        rec = {"at": board.now(), "commit": tip[:12], "name": w["name"], "target": w["target"], "how": w["how"],
               "what": w["what"], "value": None, "error": None, "reason": None}
        if w["how"] in ("count", "run"):
            try:
                r = (run or subprocess.run)(w["what"], shell=True, cwd=at, capture_output=True, text=True, timeout=TIMEOUT)
                lines = [x for x in (r.stdout or "").splitlines() if x.strip()]
                rec["value"] = number(lines[-1]) if lines else None
                if rec["value"] is None:
                    rec["error"] = (r.stderr or r.stdout or f"exit {r.returncode}").strip()[-200:] or "no number printed"
            except subprocess.TimeoutExpired:
                rec["error"] = f"took over {TIMEOUT} s"
        else:
            said = (judge or judged)(root, at, w)
            rec["value"], rec["reason"], rec["error"] = said
        out.append(rec)
    path = folder(root) / "values.jsonl"
    with path.open("a") as f:
        for rec in out:
            f.write(json.dumps(rec) + "\n")
    return out


def judged(root, at, w):
    """PROVIDER: a small separate model (the project's chores helper) reads what the measure names, read-only, and
    answers with a number and a reason: (value, reason, error)."""
    from . import providers, selection
    try:
        pick = selection.helper(root, "chores")
        p = providers.of(root)
    except Exception as err:                          # no model to judge with: a failure, shown as one
        return None, None, f"no model to judge with ({err.__class__.__name__})"
    brief = (f"You judge one measure of a project, whose files are at {at}. Read only what you need there.\n"
             f"Measure: {w['name']}" + (f" (target {w['target']:g})" if w["target"] is not None else "") + "\n"
             f"Judge: {w['what']}\n"
             "Reply with one line only: the number, then ' — ' and one short reason.")
    said = p.consult(brief, pick["model"], pick.get("effort") or "low", at)
    if said.get("error"):
        return None, None, said["error"]
    text = (said.get("text") or "").strip().splitlines()
    value = number(text[0]) if text else None
    reason = text[0].split("—", 1)[1].strip() if text and "—" in text[0] else None
    return value, reason, None if value is not None else "no number in the answer"


def summary(root):
    """Each measure in the plan with its values: [{name, target, how, what, part, last, values, error, since}] where
    values are the numbers taken by its present method, oldest first, and last is the latest record."""
    from . import lead
    path = lead.plan_path(root)
    if not path.exists():
        return []
    every = taken(root)
    out = []
    for w in read(path.read_text()):
        mine = [r for r in every if r["name"] == w["name"]]
        same = [r for r in mine if r["how"] == w["how"] and r["what"] == w["what"]]
        last = mine[-1] if mine else None
        out.append(dict(w, last=last, values=[r["value"] for r in same if r["value"] is not None],
                        changed=bool(mine) and len(same) < len(mine)))
    return out


def spark(values):
    vs = values[-12:]
    if len(vs) < 2:
        return ""
    lo, hi = min(vs), max(vs)
    return "".join(SPARK[0 if hi == lo else round((v - lo) / (hi - lo) * (len(SPARK) - 1))] for v in vs)


def fmt(v):
    return "" if v is None else f"{v:,.0f}" if float(v).is_integer() and abs(v) >= 10 else f"{v:g}"
