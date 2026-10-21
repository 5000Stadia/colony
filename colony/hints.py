"""Shape hints: where a plan has drifted from how its work behaves, read from the plan and its history, by rule.

No model reads anything: a milestone written as a sequence that keeps being reordered (a menu?), finished work left
under Later, an item in progress far longer than this project's items usually take (a raised hand), a measure that
failed or was never taken. Shown as a hint on the Roadmap tab; when a milestone finishes, the agent hears the ones
that apply once, quietly. The agent judges; the person sees them only if it brings them up.
"""
import json
import statistics
import subprocess
import time
from pathlib import Path

from . import board

REORDERS = 3                 # a sequence's items reordered this often in the last month: perhaps a menu
LATER_DONE = 3               # finished items left under Later
STUCK_TIMES = 3              # in progress this many times longer than the project's usual item
STUCK_FLOOR = 3 * 86400      # and at least three days


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def reorders(root, days=30):
    """How often each milestone's items changed order (among those present before and after) in the last month."""
    from . import lead
    rel = str(lead.plan_path(root).relative_to(root)) if lead.plan_path(root).is_relative_to(root) else "ROADMAP.md"
    commits = git(root, "log", f"--since={days} days ago", "-n", "60", "--format=%h", "--", rel).split()
    orders = []
    for c in reversed(commits):
        road = board.roadmap(root, git(root, "show", f"{c}:{rel}"))
        orders.append({m["id"]: [i["id"] for i in m["items"]] for m in road["milestones"]})
    out = {}
    for before, after in zip(orders, orders[1:]):
        for mid, ids in after.items():
            was = before.get(mid, [])
            common = [i for i in was if i in ids]
            if common and common != [i for i in ids if i in was]:
                out[mid] = out.get(mid, 0) + 1
    return out


_cache = {}


def cached(root):
    """hints(), read again when the plan changes or an hour has passed."""
    from . import lead
    path = lead.plan_path(root)
    key = (str(root), path.stat().st_mtime if path.exists() else 0, int(time.time() // 3600))
    if key not in _cache:
        _cache.clear() if len(_cache) > 64 else None
        _cache[key] = hints(root)
    return _cache[key]


def hints(root):
    """The plan's shape hints now: [(milestone or None, text)]."""
    from . import measures
    road = board.roadmap(root)
    out = []
    moved = reorders(root)
    for m in road["milestones"]:
        if m.get("kind", "sequence") == "sequence" and moved.get(m["id"], 0) >= REORDERS and not m["id"].lower().startswith("later"):
            out.append((m["id"], f"{m['id']} is written as a sequence but was reordered {moved[m['id']]} times this month: "
                                 "its items may be a menu (any order), or lanes."))
        later = "later" in (m["id"] + " " + m["title"]).lower()
        done = sum(i["state"] == "done" for i in m["items"])
        if later and done >= LATER_DONE:
            out.append((m["id"], f"{m['id']}{' — ' + m['title'] if m['title'] else ''} holds {done} finished items: "
                                 "finished work belongs with the milestone it served, or folds away."))
    times = board.item_times(root)
    worked = [r.get("worked") for r in times.values() if r.get("state") == "done" and r.get("worked")]
    usual = statistics.median(worked) if len(worked) >= 5 else None
    now = time.time()
    its = board.items(road)
    for iid, r in times.items():
        if r.get("state") != "doing" or iid not in its or not r.get("since"):
            continue
        took = (r.get("worked") or 0) + now - board.epoch(r["since"])
        if took > max(STUCK_FLOOR, STUCK_TIMES * (usual or 0)):
            out.append((its[iid]["milestone"], f"{iid} has been marked in progress for {took / 86400:.0f} days"
                        + (f", against {usual / 3600:.0f} h for this project's usual item" if usual else "")
                        + ": stuck, or bigger than one item?"))
    for x in measures.summary(root):
        last = x["last"]
        if last and last["value"] is None:
            out.append((x["part"], f"The measure '{x['name']}' couldn't be taken last time ({(last['error'] or '')[:80]}): "
                                   "its method may need fixing."))
    return out


def check(root):
    """Called every few seconds per project: when a milestone has newly finished, the agent hears the plan's shape
    hints once, quietly (nothing when there are none)."""
    path = Path(root) / ".board" / "hints.json"
    road = board.roadmap(root)
    finished = sorted(m["id"] for m in road["milestones"] if m["items"] and all(i["state"] == "done" for i in m["items"]))
    try:
        told = json.loads(path.read_text())
    except (OSError, ValueError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"finished": finished}))      # first look: what was finished before isn't news
        return
    new = [m for m in finished if m not in told.get("finished", [])]
    if not new:
        if told.get("finished") != finished:
            path.write_text(json.dumps({"finished": finished}))
        return
    path.write_text(json.dumps({"finished": finished}))
    found = hints(root)
    if found:
        board.add_note(root, None, f"{', '.join(new)} finished. The plan's shape, as colony reads it from the plan and "
                       "its history (yours to judge; nothing here is the person's):\n" + "\n".join(f"- {t}" for _, t in found),
                       author="colony", quiet=True)
