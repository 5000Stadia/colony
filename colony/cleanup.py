"""The cleanup check (R139): when a pass that makes part of a project cheaper to read would pay for itself, said once
to the project's agent at a seam, for the person to take or skip. Nothing pauses on its own.

The measure is context per change: what an agent must read to change one thing. What it reads is in its transcripts,
so colony keeps, for each project, what every session read of each of its files (code and documents alike), counted
for every later model call that re-read it from context ("carried"), and which files sessions read at their start.
Those files, read by a good share of sessions before they do anything, are one hotspot (the start-up reading: every
session pays for it); any other file taking a large share of the reading is one of its own.

A pass on a hotspot pays when its cost is below what it saves a week times the weeks of work still to come. At every
finished item, by arithmetic on those records, the roadmap and git (no model):
  saved a week  = the hotspot's carried reading a week x the share a pass saves (start-up 30-50%, a file 10-30%)
  pass cost     = the share of its lines a pass touches (15-50%) x its lines x this project's input per changed line
  payback weeks = pass cost / saved a week, taken at its worst (most cost, least saved)
It speaks when even the worst case pays back within PAYBACK_MAX weeks and within the work still to come (open items,
grown as this plan has been growing, at this project's pace), and the pass would save at least MIN_SAVED of the
project's reading (below that, a quick payback is still not worth the person's attention). The findings
(design/r139-context-per-change.md) put Bookflow's start-up documents at 0.2-2 weeks and colony's board.py at 1-120:
the bar sits between them; design/r139-check.md replays it over both projects' history.

It names the best hotspot in one note, at the nearest seam: a milestone's end when that is at most FEW items away
(it waits for it), otherwise now, between two items. Since it speaks only when even the worst case pays, the agent
takes the pass there without a gate and tells the person the numbers (their choice, Nov 9: when the effort pays
for itself, the pass is automatic); an older suggestion's gate still counts as their choice. Each hotspot is
said once until it changes (its size by RESAY, after a full window of new readings). What was said, what the person
chose (their answer to the gate that cites it), whether a pass was taken (the hotspot shrank), and what it then
saved are kept in .board/cleanup.jsonl; a taken pass is measured after MEASURE_AFTER and told to the agent once, and
its real cost corrects the next estimate.

PROVIDER: each program's transcripts are read through its transcript_steps (providers.py); the rest is colony's.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

from . import board

DAY, WEEK = 86400, 7 * 86400
WINDOW = 14 * DAY            # the readings a decision rests on: the last two weeks (the pace swings week to week)
MIN_SPAN = 7 * DAY           # at least a week of readings before it says anything
MIN_SESSIONS = 10            # and this many sessions in the window
START_CALLS = 10             # a read within a session's first model calls is start-up reading
START_SHARE = 0.2            # a document read at the start of this share of sessions is start-up reading
FILE_SHARE = 0.05            # a file taking this share of the project's reading is a hotspot of its own
SHARE = {"startup": (0.3, 0.4, 0.5), "file": (0.1, 0.2, 0.3)}    # what a pass saves of its reading (low, central, high)
TOUCH = (0.15, 0.3, 0.5)     # the share of its lines a pass touches (least, central, most)
PER_LINE = 60_000            # input per changed line when the project's own can't be measured (colony's, measured)
PER_LINE_RANGE = (5_000, 200_000)
GROWTH = 0.7                 # items added per item finished, where a project's own can't be read (measured 0.64-0.78)
GROWTH_MAX = 0.8             # a plan growing as fast as it's worked counts as five times what is open, not forever
PAYBACK_MAX = 4.0            # weeks, at worst
MIN_SAVED = 0.01             # and a pass worth the person's attention saves at least 1% of the project's reading
FEW = 2                      # a milestone's end this many items away is the seam to wait for
RESAY = 0.25                 # a said hotspot is said again only once its size has changed by this much
SHRINK = 0.10                # a hotspot this much smaller than when said: a pass was taken
MEASURE_AFTER = 7 * DAY      # a taken pass is measured on this much reading after it
PASS_SPAN = 3 * DAY          # a pass is a seam's work: a shrink later than this after the answer has no cost of its own
DOCS = (".md", ".txt", ".rst", ".org", ".adoc")

READ_CMD = re.compile(r"(?:^|[\s;&|(])(cat|sed|head|tail|nl|awk|less|wc|bat|jq)\b")
SEARCH_CMD = re.compile(r"(?:^|[\s;&|(])(rg|grep|egrep|find|ls|tree|fd|git grep)\b")
GIT_CMD = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(show|diff|log|blame)\b")
NOT_READ = re.compile(r"unittest|pytest|npm (run )?test|node --test|vitest|jest|make test|cargo test|apply_patch|"
                      r"cat\s*>|cat\s*<<|tee ")
TOKEN = re.compile(r"[\w./@+-]+")
WORKTREE = re.compile(r"^\.claude/worktrees/[^/]+/")
_lock = threading.Lock()


# ---------------------------------------------------------------- the reading ledger, kept as transcripts grow

def ledger_path():
    return board.home() / "cleanup" / "reads.json"


def load():
    try:
        return json.loads(ledger_path().read_text())
    except (OSError, ValueError):
        return {"files": {}, "projects": {}}


def _day(ts):
    return str(int(ts // DAY * DAY)) if ts else None


class _Paths:
    """A path an agent named, as the project file it is (relative to the project), or None: absolute in the project,
    one of its worktrees or the session's own folder, or relative to that folder. Only files that exist now count."""

    def __init__(self):
        self.seen = {}

    def file(self, root, rel):
        k = (root, rel)
        if k not in self.seen:
            if len(self.seen) > 200_000:
                self.seen.clear()
            self.seen[k] = bool(rel) and os.path.isfile(os.path.join(root, rel))
        return self.seen[k]

    def rel(self, root, cwd, token):
        token = token.split(":")[0]
        if not token.startswith("/"):
            if cwd:
                token = os.path.normpath(os.path.join(cwd, token))
            else:
                return token if self.file(root, token) else None
        for base in (root, cwd):
            if base and token.startswith(base.rstrip("/") + "/"):
                r = WORKTREE.sub("", token[len(base.rstrip("/")) + 1:])
                if self.file(root, r):
                    return r
        return None

    def named(self, root, cwd, text):
        out = []
        for t in TOKEN.findall(text or ""):
            if ("/" in t or "." in t) and len(t) > 3 and (r := self.rel(root, cwd, t)) and r not in out:
                out.append(r)
        return out


def targets(paths, root, cwd, kind, what):
    """What one tool step read of the project: [file, ...] with "" for a search over the tree; [] when it read nothing
    of it (a test run, an edit, something outside)."""
    if kind == "read":
        r = paths.rel(root, cwd, what or "")
        return [r] if r else []
    if kind == "search":
        if not what:
            return [""]
        r = paths.rel(root, cwd, what)
        inside = what.startswith((root.rstrip("/") + "/", root)) or (cwd and what.startswith(cwd)) or not what.startswith("/")
        return [r] if r else [""] if inside else []
    cmd = what or ""
    if not cmd or NOT_READ.search(cmd) and not READ_CMD.search(cmd.split("&&")[-1]):
        return []
    files = paths.named(root, cwd, cmd)
    if GIT_CMD.search(cmd):
        return files or [""]
    if READ_CMD.search(cmd):
        return files
    if SEARCH_CMD.search(cmd):
        return files or [""]
    return []


def feed(st, data, steps, root, paths):
    """One session's new steps into its project's records: input by day and hour, each file's reading (first time,
    carried, sessions reading it at their start), and the tree-wide searches' carried cost."""
    for step in steps:
        if step[0] == "call":
            _, ts, inp = step
            if not ts:
                continue
            day = data["days"].setdefault(_day(ts), {"input": 0, "sessions": 0, "files": {}, "tree": 0})
            day["input"] += inp
            hour = str(int(ts // 3600 * 3600))
            data["hours"][hour] = data["hours"].get(hour, 0) + inp
            if not st.get("counted"):
                st["counted"] = True
                day["sessions"] += 1
            if st.get("last") and inp < st["last"] / 2:            # compacted: what was read is out of context
                _flush(st, data)
                st["live"] = {}
            st["n"] = st.get("n", 0) + 1
            st["last"], st["day"] = inp, _day(ts)
        elif step[0] == "tool":
            _, ts, kind, what, chars = step
            got = targets(paths, root, st.get("cwd") or "", kind, what)
            if not got or not ts:
                continue
            day = data["days"].setdefault(_day(ts), {"input": 0, "sessions": 0, "files": {}, "tree": 0})
            tok = chars / 4 / len(got)
            live, starts = st.setdefault("live", {}), st.setdefault("starts", [])
            for f in got:
                if f:
                    rec = day["files"].setdefault(f, [0, 0, 0])
                    rec[0] += tok
                    if st.get("n", 0) <= START_CALLS and f not in starts:
                        starts.append(f)
                        rec[2] += 1
                held = live.get(f)
                if held:                                            # carried so far, then held from here with this too
                    _carry(data, st, f, held)
                live[f] = [(held or [0])[0] + tok, st.get("n", 0)]
    _flush(st, data)


def _carry(data, st, f, held):
    n = st.get("n", 0)
    if n > held[1] and st.get("day"):
        day = data["days"].setdefault(st["day"], {"input": 0, "sessions": 0, "files": {}, "tree": 0})
        if f:
            day["files"].setdefault(f, [0, 0, 0])[1] += held[0] * (n - held[1])
        else:
            day["tree"] += held[0] * (n - held[1])
    held[1] = max(held[1], n)


def _flush(st, data):
    for f, held in (st.get("live") or {}).items():
        _carry(data, st, f, held)


def update(budget=20, now=None):
    """Read what the transcripts gained since the last look, within the budget, into each project's records; drop
    what is older than the window. True once every transcript has been read up to date."""
    if not _lock.acquire(blocking=False):
        return False
    import fcntl
    ledger_path().parent.mkdir(parents=True, exist_ok=True)
    held = open(ledger_path().parent / "reads.lock", "a")
    try:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        held.close()
        _lock.release()
        return False
    try:
        from . import providers, tokens
        now = now or time.time()
        state, end, done = load(), time.time() + budget, True
        roots = [Path(p) for p in board.projects(board.registry(), adopt=False)]      # a look that only reads
        by_name = {r.name: str(r) for r in roots}
        place, paths = tokens._Places(roots, tokens.old_steward()), _Paths()
        found = tokens.sources(keep=WINDOW + DAY)
        for f, prog in found:
            if time.time() > end:
                done = False
                break
            st = state["files"].setdefault(str(f), {})
            try:
                steps = providers.get(prog).transcript_steps(f, st)
            except Exception:
                continue
            if not steps:
                continue
            if any(s[0] == "cwd" for s in steps) or "root" not in st:
                who = tokens.owner(place(st, f), roots, tokens.old_steward()) if st.get("cwd") else ("outside", "")
                st["root"] = by_name.get(who[1], "") if who[0] == "project" else ""
            if st["root"]:
                data = state["projects"].setdefault(st["root"], {"days": {}, "hours": {}})
                feed(st, data, steps, st["root"], paths)
        old = now - WINDOW - DAY
        for data in state["projects"].values():
            data["days"] = {d: v for d, v in data["days"].items() if int(d) >= old}
            data["hours"] = {h: v for h, v in data["hours"].items() if int(h) >= old}
        keep = {str(f) for f, _ in found}
        state["files"] = {k: v for k, v in state["files"].items() if k in keep}
        if done:
            state.setdefault("complete", now)
        board.save_json(ledger_path(), state)
        return done
    finally:
        held.close()
        _lock.release()


# ---------------------------------------------------------------- the arithmetic, on the window's records

def measure(data, now):
    """The window's totals for one project: {"files": {f: [read, carried, starts]}, "tree", "carried" (all project
    reading), "input", "sessions", "weeks" (how much reading they span)}."""
    since = now - WINDOW
    days = {int(d): v for d, v in (data or {}).get("days", {}).items() if int(d) + DAY > since}
    out = {"files": {}, "tree": 0, "input": 0, "sessions": 0, "weeks": 0}
    for v in days.values():
        out["input"] += v["input"]
        out["sessions"] += v["sessions"]
        out["tree"] += v["tree"]
        for f, (r, c, s) in v["files"].items():
            rec = out["files"].setdefault(f, [0, 0, 0])
            rec[0] += r
            rec[1] += c
            rec[2] += s
    out["carried"] = out["tree"] + sum(v[1] for v in out["files"].values())
    out["weeks"] = (now - min(days)) / WEEK if days else 0
    return out


def hotspots(m):
    """[(key, kind, files)]: the start-up reading first (files read at the start of START_SHARE of sessions), then each
    other file taking FILE_SHARE of the reading."""
    out, n = [], m["sessions"]
    start = sorted(f for f, v in m["files"].items() if f.endswith(DOCS) and n >= MIN_SESSIONS and v[2] >= START_SHARE * n)
    if start:
        out.append(("startup", "startup", start))
    for f, v in sorted(m["files"].items(), key=lambda x: -x[1][1]):
        if f not in start and m["carried"] and v[1] >= FILE_SHARE * m["carried"]:
            out.append((f"file:{f}", "file", [f]))
    return out


def payback(weekly, kind, lines, per_line, calib=1.0):
    """(best, central, worst) weeks to pay back a pass, and its central cost in tokens."""
    lo, mid, hi = SHARE[kind]
    cost = [t * lines * per_line * calib for t in TOUCH]
    if weekly <= 0:
        return (float("inf"),) * 3, cost[1]
    return (cost[0] / (weekly * hi), cost[1] / (weekly * mid), cost[2] / (weekly * lo)), cost[1]


def assess(m, size, per_line, ahead, calib=1.0):
    """Each hotspot, weighed: [{key, kind, files, share, weekly, lines, tokens, weeks (best, central, worst), cost,
    pays}], best first. size(files) -> (lines, tokens) as they are now; horizon: weeks of work still to come."""
    out = []
    if m["weeks"] * WEEK < MIN_SPAN or m["sessions"] < MIN_SESSIONS:
        return out
    for key, kind, files in hotspots(m):
        carried = sum(m["files"][f][1] for f in files)
        weekly = carried / max(m["weeks"], 1e-9)
        lines, toks = size(files)
        if not lines:
            continue
        weeks, cost = payback(weekly, kind, lines, per_line, calib)
        out.append({"key": key, "kind": kind, "files": files, "share": carried / m["carried"], "weekly": weekly,
                    "lines": lines, "tokens": toks, "weeks": weeks, "cost": cost,
                    "starts": max(m["files"][f][2] for f in files) / max(m["sessions"], 1),
                    "pays": weeks[2] <= min(PAYBACK_MAX, ahead) and carried / m["carried"] * SHARE[kind][1] >= MIN_SAVED})
    return sorted(out, key=lambda h: h["weeks"][2])


def horizon(open_items, done_recent, added_recent=None, age=0):
    """Weeks of work still to come: the open items, grown as this plan has grown over the last four weeks (each item
    finished bringing `added/done` more, so open / (1 - that) in all), at the pace of those weeks. A plan written just
    ahead of the work shows few open items while it keeps growing: one still growing at half its pace or more is
    expected to run at least as long again as it has run (`age`, seconds), the visible items notwithstanding."""
    g = GROWTH if added_recent is None or not done_recent else added_recent / done_recent
    weeks = open_items / (1 - min(max(g, 0), GROWTH_MAX)) / max(done_recent / 4, 0.25)
    return max(weeks, age / WEEK) if g >= 0.5 else weeks


def growth(root, days=28):
    """Items added to the plan and items finished over the last four weeks, from its versions in git."""
    from . import lead
    path = lead.plan_path(root)
    rel = str(path.relative_to(root)) if path.is_relative_to(root) else "ROADMAP.md"
    old = board.git(root, "rev-list", "-1", f"--before={days} days ago", "HEAD", "--", rel).strip()
    before = board.items(board.roadmap(None, board.git(root, "show", f"{old}:{rel}"))) if old else {}
    now = board.items(board.roadmap(root))
    added = sum(1 for i in now if i not in before)
    done = sum(1 for i, it in now.items() if it["state"] == "done" and (before.get(i) or {}).get("state") != "done")
    return added, done


def per_line(input_tokens, lines_changed):
    """This project's input per changed line over the window, within reason; colony's own when too little changed."""
    if lines_changed < 100 or not input_tokens:
        return PER_LINE
    return min(max(input_tokens / lines_changed, PER_LINE_RANGE[0]), PER_LINE_RANGE[1])


# ---------------------------------------------------------------- the check, at every finished item

def _state_path(root):
    return Path(root) / ".board" / "cleanup.json"


def _size(root):
    def size(files):
        lines = toks = 0
        for f in files:
            try:
                b = (Path(root) / f).read_bytes()
            except OSError:
                continue
            lines, toks = lines + b.count(b"\n"), toks + len(b) // 4
        return lines, toks
    return size


def _lines_changed(root, days=14):
    out = 0
    for line in board.git(root, "log", f"--since={days} days ago", "--no-merges", "--numstat", "--format=").splitlines():
        a, _, rest = line.partition("\t")
        d, _, _ = rest.partition("\t")
        if a.isdigit() and d.isdigit():
            out += int(a) + int(d)
    return out


def label(files):
    return files[0] if len(files) == 1 else ", ".join(files[:-1]) + " and " + files[-1]


def _short(n):
    return f"{n / 1e9:.1f}B" if n >= 1e9 else f"{n / 1e6:.0f}M" if n >= 1e6 else f"{n / 1e3:.0f}k"


def _weeks(w):
    return "under a day" if w < 1 / 7 else f"{w:.1f} weeks" if w < 10 else f"{w:.0f} weeks"


def wording(h, seam):
    """The note: the hotspot, its share of reading, its payback, the suggested pass and the seam."""
    docs = all(f.endswith(DOCS) for f in h["files"])
    what = (f"the start-up reading ({label(h['files'])}, read at the start of {h['starts']:.0%} of sessions)"
            if h["kind"] == "startup" else label(h["files"]))
    how = ("shorter, nothing said twice, what a session needs first up front" if docs
           else "split or trimmed so one change reads less of it, duplication cut")
    best, mid, worst = h["weeks"]
    return (f"Cleanup check (colony's arithmetic on this project's reading, no model): {what} takes {h['share']:.0%} of this "
            f"project's reading, about {_short(h['weekly'])} tokens a week, re-reads counted. A pass on it alone ({how}) "
            f"would cost about {_short(h['cost'])} tokens and pay back in about {_weeks(mid)} ({_weeks(best)} to "
            f"{_weeks(worst)}), against about {_weeks(h['horizon'])} of roadmap still to come. Even at worst it pays, so take "
            f"the pass {seam}, without a gate (the person's choice: a pass that pays for itself is automatic), "
            f"leaving the Vision as it is (it is their words; a change to it is theirs), and tell them in a line with "
            f"these numbers when you next report. Colony measures what it saved and tells you.")


def _log(root, rec):
    board.append(root, "cleanup.jsonl", dict(rec, at=rec.get("at") or board.now()))


def history(root):
    """{key: the latest suggestion, with what came of it: chose, taken, measured}."""
    out = {}
    for e in board.read(root, "cleanup.jsonl"):
        if e["type"] == "suggested":
            out[e["key"]] = dict(e)
        elif e["key"] in out:
            out[e["key"]][e["type"]] = e
    return out


def _quiet(said, h, now):
    """A hotspot said before stays quiet until it has changed: its size (the start-up reading's, whichever documents
    it holds now) moved by RESAY, and a window of reading has passed since, so the numbers are of it as it is. A brief
    that comes and goes from the start-up reading doesn't make it news."""
    if not said:
        return False
    if now - board.epoch(said["at"]) < WINDOW:
        return True
    return abs(h["tokens"] - said["tokens"]) < RESAY * max(said["tokens"], 1)


def shrunk_at(root, files, since, tokens):
    """When the pass landed: the first commit since `since` whose files are SHRINK smaller than `tokens`, or None
    while the shrink is only in the working tree. Not the files' last commit: a file edited daily (the roadmap) has
    one, unrelated, before the pass is committed (seen Nov 2) and many after it."""
    for line in board.git(root, "log", "--reverse", f"--since={int(since)}", "--format=%H %ct", "--", *files).splitlines():
        sha, _, at = line.partition(" ")
        toks = sum(len(board.git(root, "show", f"{sha}:{f}").encode()) // 4 for f in files)
        if toks <= (1 - SHRINK) * tokens:
            return int(at)
    return None


def pass_cost(hours, t0, t1):
    """The project's input from t0 to t1: each hour's by the part of it inside, not whole hours (Nov 2's 12-minute
    pass was charged its whole hour of every session's work, 100M tokens, which put the check's estimates up 4x)."""
    return sum(v * max(0, min(int(h) + 3600, t1) - max(int(h), t0)) / 3600 for h, v in hours.items())


def _follow(root, past, data, size, now, state):
    """What came of each suggestion: the person's answer to the gate citing it, a pass taken (it shrank), and once
    MEASURE_AFTER has passed, what the pass cost and saved, told to the agent once."""
    gates = None
    for key, s in past.items():
        if "measured" in s:
            continue
        files, said_at = s["files"], board.epoch(s["at"])
        if "chose" not in s:
            gates = board.gates(root) if gates is None else gates
            names = [s["note"]] + [Path(f).name for f in files]
            for g in gates:
                text = f"{g['question']} {g.get('why', '')}"
                if g["answer"] and board.epoch(g["at"]) >= said_at - 60 and any(n in text for n in names):
                    s["chose"] = {"type": "chose", "key": key, "gate": g["id"], "answer": g["answer"],
                                  "by": g.get("answered_by") or "person", "answered": g["answered_at"]}
                    _log(root, s["chose"])
                    break
        if "taken" not in s:
            _, toks = size(files)
            t0 = board.epoch(s["chose"]["answered"]) if s.get("chose", {}).get("answered") else said_at
            t1 = shrunk_at(root, files, t0, s["tokens"]) if toks <= (1 - SHRINK) * s["tokens"] else None
            if t1:
                cost = pass_cost(data.get("hours", {}), t0, t1) if t1 - t0 <= PASS_SPAN else None   # shrunk in the
                s["taken"] = {"type": "taken", "key": key, "when": t1, "cost": cost, "tokens": toks}  # course of other work:
                _log(root, s["taken"])                                                                # no cost of its own
        if "taken" in s and now - s["taken"]["when"] >= MEASURE_AFTER:
            days = {d: v for d, v in data.get("days", {}).items() if int(d) >= s["taken"]["when"]}
            if not days:
                continue
            span = (now - min(int(d) for d in days)) / WEEK
            weekly = sum(v["files"].get(f, [0, 0, 0])[1] for v in days.values() for f in files) / span
            saved, cost = s["weekly"] - weekly, s["taken"]["cost"]
            got = {"type": "measured", "key": key, "before": s["weekly"], "after": weekly, "cost": cost,
                   "weeks": cost / saved if cost and saved > 0 else None, "estimate": s["cost"]}
            _log(root, got)
            s["measured"] = got
            if cost and s["cost"]:
                state["calib"] = min(max(cost / s["cost"], 0.25), 4.0)
            paid = (f"the pass cost about {_short(cost)} and paid back in about {_weeks(got['weeks'])}." if got["weeks"]
                    else f"the pass cost about {_short(cost)} and has not paid back on this reading." if cost
                    else "it shrank in the course of other work, so the pass's own cost isn't known.")
            board.add_note(root, None, f"Cleanup check: {label(files)}, measured since it shrank. Its reading went from about "
                           f"{_short(s['weekly'])} to {_short(weekly)} tokens a week; {paid} Tell the person in a line "
                           "when you next report; nothing to decide.", author="colony", quiet=True, kind="cleanup")


def check(root, now=None):
    """Called every few seconds per project: at each newly finished item, the hotspots are weighed, what came of past
    suggestions recorded, and the best one that pays is said once, at the nearest seam."""
    now = now or time.time()
    path = _state_path(root)
    road = board.roadmap(root)
    its = board.items(road)
    done = sorted(i for i, it in its.items() if it["state"] == "done")
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        path.parent.mkdir(parents=True, exist_ok=True)
        board.save_json(path, {"done": done})             # first look: what was finished before isn't news
        return None
    new = [i for i in done if i not in state.get("done", [])]
    if set(done) != set(state.get("done", [])):
        state["done"] = done
        board.save_json(path, state)
    if not new:
        return None
    ledger = load()
    if not ledger.get("complete"):
        return None                                       # the transcripts aren't read up to date yet
    data = ledger["projects"].get(str(root)) or {}
    size, past = _size(root), history(root)
    _follow(root, past, data, size, now, state)
    m = measure(data, now)
    added, recent = growth(root)
    left = sum(1 for it in its.values() if it["state"] in ("todo", "doing"))
    first = board.git(root, "log", "--reverse", "--format=%ct").split("\n", 1)[0].strip()
    ahead = horizon(left, recent, added, now - int(first) if first.isdigit() else 0)
    found = assess(m, size, per_line(m["input"], _lines_changed(root)), ahead, state.get("calib", 1.0))
    pick = next((h for h in found if h["pays"] and not _quiet(past.get(h["key"]), h, now)), None)
    board.save_json(path, state)                          # with a measured pass's correction to the estimates
    if not pick:
        return None
    ms = its[new[-1]]["milestone"]
    left_in = sum(1 for it in its.values() if it["milestone"] == ms and it["state"] in ("todo", "doing"))
    if 0 < left_in <= FEW:
        return None                                       # its milestone's end is near: the seam to wait for
    seam = f"now, at {ms}'s end" if left_in == 0 else "between items: once the item in hand is done, before the next"
    pick["horizon"] = ahead
    note = board.add_note(root, None, wording(pick, seam), author="colony", quiet=True, kind="cleanup")
    _log(root, {"type": "suggested", "key": pick["key"], "note": note["id"], "files": pick["files"],
                "kind": pick["kind"], "share": pick["share"], "weekly": pick["weekly"], "tokens": pick["tokens"],
                "lines": pick["lines"], "weeks": list(pick["weeks"]), "cost": pick["cost"], "horizon": ahead,
                "seam": seam, "after": new[-1]})
    return pick
