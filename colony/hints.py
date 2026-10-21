"""Shape hints: where a plan may have drifted from how its work behaves, read from the plan and its history, by rule.

No model reads anything, and each hint is a plain fact: a milestone written as a sequence that keeps being reordered,
finished work left under Later, an item marked in progress far longer than this project's usual, a measure that
failed. Shown on the Roadmap tab; when a milestone finishes, the agent hears the open ones once, quietly. A hint the
agent judges and keeps (colony hint keep KEY "why") is not raised again, its reasoning kept beside it, until the
vision changes: the shape that served one vision may not serve the next.
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
    """The plan's shape hints now, each a plain fact: [(key, milestone or None, text)]. The key stays the same while
    the same thing holds, so a hint the agent has settled is known again."""
    from . import measures
    road = board.roadmap(root)
    out = []
    moved = reorders(root)
    for m in road["milestones"]:
        later = "later" in (m["id"] + " " + m["title"]).lower()
        if m.get("kind", "sequence") == "sequence" and moved.get(m["id"], 0) >= REORDERS and not later:
            out.append((f"reordered:{m['id']}", m["id"], f"{m['id']}: a sequence reordered {moved[m['id']]} times this month."))
        done = sum(i["state"] == "done" for i in m["items"])
        if later and done >= LATER_DONE:
            out.append((f"later-done:{m['id']}", m["id"], f"{m['id']}{' — ' + m['title'] if m['title'] else ''}: "
                                                         f"{done} finished items."))
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
            out.append((f"long:{iid}", its[iid]["milestone"], f"{iid}: marked in progress {took / 86400:.0f} days"
                        + (f" (usual: {usual / 3600:.0f} h)." if usual else ".")))
    for x in measures.summary(root):
        last = x["last"]
        if last and last["value"] is None:
            out.append((f"measure:{x['name']}", x["part"], f"Measure '{x['name']}' failed last time: {(last['error'] or '')[:80]}"))
    return out


def vision_mark(root):
    """Which vision a settled hint was settled under: when the vision changes, settled hints are open again."""
    import hashlib
    road = board.roadmap(root)
    return hashlib.sha1(" ".join((road["vision"] or road["goal"] or "").split()).encode()).hexdigest()[:12]


def settled(root):
    """Hints the agent settled under the present vision, each with its reasoning: {key: {"why", "at"}}."""
    mark, out = vision_mark(root), {}
    for e in board.read(root, "hints-kept.jsonl"):
        if e.get("vision") == mark:
            out[e["key"]] = e
    return out


def keep(root, key, why):
    """The agent's reasoned judgement that a hint's shape is right: not raised again until the vision changes."""
    board.append(root, "hints-kept.jsonl", {"type": "keep", "key": key, "why": why.strip(), "at": board.now(),
                                            "vision": vision_mark(root)})


def open_hints(root, every=None):
    kept = settled(root)
    return [h for h in (every if every is not None else hints(root)) if h[0] not in kept]


def check(root):
    """Called every few seconds per project: when a milestone has newly finished, the agent hears the plan's open
    shape hints once, quietly, as plain facts (nothing when there are none, or all are settled)."""
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
    if told.get("finished") != finished:
        path.write_text(json.dumps({"finished": finished}))
    if not new:
        return
    found = open_hints(root)
    if found:
        board.add_note(root, None, f"{', '.join(new)} finished. Shape hints, read by rule from the plan (yours to judge; "
                       "skip any that don't fit, or settle one with colony hint keep KEY \"why\" and it stays quiet "
                       "until the vision changes):\n" + "\n".join(f"- {k}: {t}" for k, _, t in found),
                       author="colony", quiet=True)
