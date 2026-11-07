"""Q Branch, the research department, as colony runs it (R131): research handed to a roadmap item's agent at the
item's start, and only where the item asks for it; curated guides handed over where an item's words match them, and
kept by what the agents who used them found.

The core is colony/qbranch/ (Q Branch's own, generic); this is the seam: the roadmap flag, when passes run, delivery,
upkeep, settings and what the board shows. Every model call goes through the research room (colony/qroom.py runs it
for the core), on Claude: Codex is kept for second-provider judging and isn't used for research.

The flag, by the item's own agent: `(research)` on its roadmap line, beside `(related: ...)`, and a `## Research`
section in its details file (roadmap/R#.md):

    ## Research
    Trigger: Stale: memory tooling for coding agents moved this year.
    Subject: how do long-running coding agents keep and recover what they learned across sessions?
    Requirements: runs offline on one laptop; no paid services
    Decisions:
    - Which store for recalled notes? Default: plain Markdown files in the repository.
    - When to compact? Default: at the harness's own limit.

The pass gets that section's subject and requirements, and nothing else of the project. Requirements may also be
listed one to a line under "Requirements:". A decision's default follows "Default:" (or sits in "(default: ...)").

When passes run: when any item in a project goes [~], the next flagged item in roadmap order without a guide has its
pass queued; a flagged item started with no guide has its pass then, and its agent is told one is coming. One pass at
a time, machine-wide. A decision line added to the section after its pass (compared with what that pass was given)
runs Q Branch's one-decision mode for that decision alone, appended to the guide; a rewording, a changed default or
new detail runs nothing. Each guide is kept in the board's home (qbranch/guides/), free of project context, and in the
research library; the agent gets a one-line note at the item's start.

Curated guides (the shelf, seeded from colony/qbranch/shelf/ into the board's home) carry `match:` terms; any one term,
as a whole word or phrase, in an item's line or details, when the item before it starts, hands the guide over at the
item's start, flagged or not, and runs no pass. Its hand-over asks the agent to search the web itself beyond the guide as
well, and at the item's [x] it is asked once, after both, whether anything it found belongs in the guide (the person,
2026-11-06: the question only works after both); what it writes in a `## For the guide` section goes to Q Branch's
learn and weave.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import board

CORE = Path(__file__).resolve().parent / "qbranch" / "qbranch.py"
RUNNER = Path(__file__).resolve().parent / "qroom.py"
SEED = Path(__file__).resolve().parent / "qbranch" / "shelf"
FLAG = re.compile(r"\s*\(research\)", re.I)
# PROVIDER: the program and models Q Branch's method was measured on: the pass on Claude Sonnet (an alias Claude Code
# resolves to its current Sonnet), the weave on the light tier, learn on the chores tier.
FAMILY, PASS_MODEL, WEAVE_ROLE, LEARN_ROLE = "claude", "sonnet", "rote", "chores"
TIMEOUT = 3600                  # one core call, its passes and screens together
MAX_CANDIDATES = 3
_worker, _wlock = None, threading.Lock()


# ---------------------------------------------------------------- the Research section, as the agent writes it

def section(text, name):
    """The body of the `## name` section of a details file, up to the next heading of its level or above; None
    without one."""
    m = re.search(rf"^##\s+{re.escape(name)}\s*$", text or "", re.M | re.I)
    if not m:
        return None
    rest = text[m.end():]
    end = re.search(r"^#{1,2}\s", rest, re.M)
    return rest[:end.start()] if end else rest


def _decision(line):
    line = line.strip()
    m = re.match(r"(.*?)\s*\(default:\s*(.+?)\)\s*\.?$", line, re.I)
    if m:
        return {"decision": m[1].strip(), "default": m[2].strip()}
    parts = re.split(r"\s*(?:[—–-]\s*)?\bdefault\s*:\s*", line, maxsplit=1, flags=re.I)
    if len(parts) == 2:
        return {"decision": parts[0].strip(" |—–-"), "default": parts[1].strip().rstrip(".")}
    if " | " in line:
        a, b = line.split(" | ", 1)
        return {"decision": a.strip(), "default": b.strip()}
    return {"decision": line, "default": "none stated"}


def parse(body):
    """{"trigger", "subject", "requirements": [...], "decisions": [{"decision", "default"}]} from a Research section's
    body, or None when it names no subject."""
    if body is None:
        return None
    out, key = {"trigger": "", "subject": "", "requirements": [], "decisions": []}, None
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(?:[-*]\s*)?\**(trigger|why|subject|requirements|decisions)\**\s*:\s*(.*)$", line, re.I)
        if m:
            key = {"why": "trigger"}.get(m[1].lower(), m[1].lower())
            value = m[2].strip()
            if key in ("trigger", "subject"):
                out[key] = value
            elif key == "requirements" and value:
                out[key] += [r.strip() for r in value.split(";") if r.strip()]
            elif key == "decisions" and value:
                out[key].append(_decision(value))
            continue
        bullet = re.match(r"^[-*]\s+(.*)$", line)
        item = bullet[1].strip() if bullet else line
        if key == "requirements":
            out[key].append(item)
        elif key == "decisions" and bullet:
            out[key].append(_decision(item))
        elif key in ("trigger", "subject") and not bullet:
            out[key] = (out[key] + " " + item).strip()
    out["decisions"] = [d for d in out["decisions"] if d["decision"]]
    return out if out["subject"] else None


def research_of(root, it):
    """The item's parsed Research section, if it is flagged and the section names a subject; else None."""
    if not it.get("research"):
        return None
    return parse(section(board.item_details(root, it["id"]), "Research"))


def _norm(text):
    return " ".join(re.findall(r"[a-z0-9]+", str(text).lower()))


def added(given, now):
    """The decisions added since a pass was given `given`: only when there are more than it had, and only those whose
    question it didn't have (the last ones, if a rewording elsewhere also changed one). A rewording, a changed default,
    or a removal adds nothing."""
    extra = len(now) - len(given)
    if extra <= 0:
        return []
    had = {_norm(d["decision"]) for d in given}
    fresh = [d for d in now if _norm(d["decision"]) not in had]
    return fresh[-extra:]


def candidates(body):
    """The `## For the guide` section's findings, [{text, url}], at most three: each a bullet (or paragraph) carrying its
    source URL. "Nothing", "none" or no URL: none."""
    if body is None:
        return []
    chunks = [c for c in re.split(r"\n(?=\s*[-*]\s)|\n\s*\n", body) if c.strip()]
    out = []
    for c in chunks:
        url = re.search(r"https?://[^\s<>()\[\]\"']+", c)
        if not url:
            continue
        link = url[0].rstrip(".,;:")
        text = c.replace(url[0], " ")
        text = re.sub(r"^\s*[-*]\s+", "", text)
        text = re.sub(r"\(\s*\)|\[\s*\]|\bsource\s*:\s*", " ", text, flags=re.I)
        text = " ".join(text.split()).strip(" -–—:;,(")
        if text:
            out.append({"text": text, "url": link})
    return out[:MAX_CANDIDATES]


# ---------------------------------------------------------------- curated guides on the shelf, matched by code

def folder():
    return board.home() / "qbranch"


def shelf_dir():
    """The living shelf, in the board's home: seeded with colony's curated guides (and their pinned editions) where
    missing; what learn and weave change there stays on this machine."""
    live = folder() / "shelf"
    for src in SEED.rglob("*.md"):
        dest = live / src.relative_to(SEED)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    return live


def header(path):
    head = {}
    try:
        text = Path(path).read_text()
    except OSError:
        return head
    for line in text.partition("\n\n")[0].splitlines():
        k, sep, v = line.partition(":")
        if sep and re.fullmatch(r"[a-z_]+", k.strip()):
            head[k.strip()] = v.strip()
    return head


def curated():
    """{id: {"path", "title", "terms"}} for every curated shelf entry that carries match terms."""
    out = {}
    for path in sorted(shelf_dir().glob("*.md")):
        h = header(path)
        if h.get("status") == "curated":
            out[path.stem] = {"path": path, "title": h.get("title", path.stem),
                              "terms": [t.strip() for t in h.get("match", "").split(",") if t.strip()]}
    return out


def term_hit(term, text):
    """Whether a match term appears in text as a whole word or phrase, any case (never inside another word)."""
    words = term.split()
    if not words:
        return False
    pattern = r"(?<![\w])" + r"\s+".join(re.escape(w) for w in words) + r"(?![\w])"
    return re.search(pattern, text, re.I) is not None


def match(text):
    """The first curated entry one of whose terms is in text, as (id, term), or None."""
    for eid, entry in curated().items():
        for term in entry["terms"]:
            if term_hit(term, text):
                return eid, term
    return None


# ---------------------------------------------------------------- settings

def on(root):
    """Research for this project: on unless its agent's person turned it off."""
    return board.project_settings(root)[1].get("research") != "off"


def sourced():
    """Whether passes use Q Branch's sourced method (its source plan) rather than the plain one: off by default."""
    return bool(board.registry()["settings"].get("research_sourced"))


def standing_line(root):
    """The one line in the project's instructions that points its agent to its standing source entry, or ""."""
    eid = board.project_settings(root)[1].get("research_source")
    if not eid:
        return ""
    path = shelf_dir() / f"{eid}.md"
    if not path.exists():
        return ""
    title = header(path).get("title", eid)
    return (f"\nStanding source: this project's facts rest on Q Branch's guide \"{title}\" ({path}). Where the work "
            f"turns on a fact it covers, read its section before you rely on the fact.\n")


# ---------------------------------------------------------------- the project's record

def _state_path(root):
    return Path(root) / ".board" / "qbranch.json"


_LOADED = {}                  # a record as read for the board's pages, kept until it changes: read once per item row


def load(root, cached=False):
    """The project's record, or None. cached: as last read while its file looks unchanged (pages only: a write within
    the clock's tick can look unchanged, so nothing that decides or writes reads it this way)."""
    import copy
    path = _state_path(root)
    if not cached:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        got = _LOADED.get(str(path))
        if got is None or got[0] != stamp:
            got = _LOADED[str(path)] = (stamp, json.loads(path.read_text()))
    except (OSError, ValueError):
        return None
    return copy.deepcopy(got[1])


_locks = {}


def _lock(root):
    return _locks.setdefault(str(root), threading.Lock())


def update(root, change):
    """Read, change and write the project's record as one step (the watcher and the worker both write it)."""
    with _lock(root):
        state = load(root) or {"states": {}, "items": {}}
        out = change(state)
        board.save_json(_state_path(root), state)
        return out


def log(root, row):
    board.append(root, "qbranch-log.jsonl", dict(at=board.now(), **row))


def private(root):
    """The names no guide may carry: the project's, and its nest's and nestmates'."""
    from . import nest
    from .project_names import name_of
    names = {Path(root).name, name_of(root)}
    try:
        n = nest.nest_of(root)
        if n:
            names.add(n)
            names.update(p.name for p in nest.nestmates(root))
    except Exception:
        pass
    return sorted(x for x in names if x and len(x) > 2)


def redact(text, names):
    for name in names:
        text = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", "the project", text, flags=re.I)
    return text


def note(root, iid, text, quiet=True):
    """Quiet by default: an item's agent is working when its guide arrives, so it reads it on its next turn."""
    return board.add_note(root, {"item": iid}, text, author="colony", quiet=quiet, kind="qbranch")


# ---------------------------------------------------------------- the machine-wide queue

def _queue_path():
    return folder() / "queue.json"


def queue():
    try:
        return json.loads(_queue_path().read_text())
    except (OSError, ValueError):
        return []


def enqueue(job):
    with board.board_lock("qbranch-queue"):
        jobs = queue()
        same = lambda j: all(j.get(k) == job.get(k) for k in ("kind", "root", "item", "decision"))
        if any(same(j) for j in jobs):
            return False
        jobs.append(dict(job, id="q" + secrets.token_hex(4), at=time.time()))
        board.save_json(_queue_path(), jobs)
        return True


def _take():
    with board.board_lock("qbranch-queue"):
        jobs = queue()
        if not jobs:
            return None
        job = jobs.pop(0)
        board.save_json(_queue_path(), jobs)
        return job


def drain(wait=False):
    """Start the worker if there is work and none runs here; it runs the queue one job at a time, holding the machine's
    lock (another board process holding it means it runs there instead). wait: run it in this thread (tests)."""
    global _worker
    with _wlock:
        if _worker and _worker.is_alive():
            return _worker
        if not queue():
            return None
        _worker = threading.Thread(target=_work, daemon=True, name="qbranch")
        if wait:
            _worker.run()
            return None
        _worker.start()
        return _worker


def _work():
    import fcntl
    folder().mkdir(parents=True, exist_ok=True)
    with open(folder() / "run.lock", "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return                                   # one pass at a time, machine-wide: another process runs it
        while True:
            job = _take()
            if not job:
                return
            try:
                run_job(job)
            except Exception as err:                 # one job's fault never stops the queue
                try:
                    log(job["root"], {"event": "job-failed", "job": job, "error": f"{type(err).__name__}: {err}"[:300]})
                except Exception:
                    pass


# ---------------------------------------------------------------- the core, as a separate python3 -I process

def runners():
    py = sys.executable or "python3"
    cmd = lambda *a: " ".join(map(_q, (py, "-I", str(RUNNER), *a)))
    return {"research": {"cmd": cmd("research", FAMILY, PASS_MODEL), "timeout": TIMEOUT},
            "check": {"cmd": cmd("screen", FAMILY, PASS_MODEL), "timeout": 900},
            "weave": {"cmd": cmd("ask", FAMILY, WEAVE_ROLE), "timeout": 900},
            "learn": {"cmd": cmd("ask", FAMILY, LEARN_ROLE), "timeout": 900}}


def _q(s):
    import shlex
    return shlex.quote(str(s))


def core(command, inp, usage, run=None):
    """One call of the core: (exit code, stdout, stderr). usage: the file each model call's spend is added to."""
    env = dict(os.environ, COLONY_QB_USAGE=str(usage))
    env.pop("CLAUDECODE", None)
    try:
        p = (run or subprocess.run)([sys.executable or "python3", "-I", str(CORE), command], input=json.dumps(inp),
                                    capture_output=True, text=True, env=env, timeout=TIMEOUT + 300)
    except subprocess.TimeoutExpired:
        return 3, "", "the core ran out of time"
    return p.returncode, p.stdout, p.stderr


def spent(path):
    """The tokens and cost the calls recorded in a usage file added up: {"input", "cache_read", "cache_write",
    "output", "cost_usd", "calls"}."""
    total = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "cost_usd": 0.0, "calls": 0}
    try:
        rows = Path(path).read_text().splitlines()
    except OSError:
        return total
    for line in rows:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        u = r.get("usage") or {}
        total["input"] += int(u.get("input_tokens") or 0)
        total["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
        total["cache_write"] += int(u.get("cache_creation_input_tokens") or 0)
        total["output"] += int(u.get("output_tokens") or 0)
        total["cost_usd"] = round(total["cost_usd"] + float(r.get("cost_usd") or 0), 4)
        total["calls"] += 1
    return total


def _scratch():
    d = folder() / "work" / secrets.token_hex(4)
    d.mkdir(parents=True)
    return d


def _record_run(root, iid, kind, status, used, **extra):
    def change(state):
        rec = state["items"].setdefault(iid, {})
        rec.setdefault("runs", []).append(dict(kind=kind, status=status, at=board.now(), tokens=used, **extra))
    update(root, change)
    log(root, dict(event=kind, item=iid, status=status, tokens=used, **extra))


def run_job(job, run=None):
    root, iid = Path(job["root"]), job["item"]
    if not root.exists() or not on(root):
        return
    {"pass": _pass_job, "decision": _decision_job, "learn": _learn_job}[job["kind"]](root, iid, job, run)


def _pass_job(root, iid, job, run):
    it = board.items(board.roadmap(root)).get(iid)
    given = research_of(root, it) if it else None
    if not given:
        update(root, lambda s: s["items"].setdefault(iid, {}).update(status=None))
        return
    names = private(root)
    gid = "g" + secrets.token_hex(4)
    out = folder() / "guides" / gid
    work = _scratch()
    usage = work / "usage.jsonl"
    update(root, lambda s: s["items"].setdefault(iid, {}).update(status="running", given=given))
    inp = {"subject": redact(given["subject"], names), "requirements": [redact(r, names) for r in given["requirements"]],
           "out": str(out), "runner": runners(), "screen": True, "cwd": str(work / "cwd"), "sourced": sourced(),
           "source_ledger": str(folder() / "ledger.json")}
    (work / "cwd").mkdir()
    code, _, err = core("brief", inp, usage, run)
    used = spent(usage)
    shutil.rmtree(work, ignore_errors=True)
    if code != 0 or not (out / "brief.md").exists():
        update(root, lambda s: s["items"][iid].update(status="failed", error=(err.strip().splitlines() or ["exit"])[-1][:300]))
        _record_run(root, iid, "pass", "failed", used, error=err.strip()[-300:])
        return
    (out / "given.json").write_text(json.dumps({"subject": inp["subject"], "requirements": inp["requirements"]}, indent=1))
    _library(out, inp["subject"])
    _record_run(root, iid, "pass", "ready", used, guide=gid)

    def ready(state):
        rec = state["items"][iid]
        rec.update(status="ready", guide=gid, path=str(out / "brief.md"), error=None)
        return rec
    rec = update(root, ready)
    it = board.items(board.roadmap(root)).get(iid)
    if it and it["state"] == "doing":               # started while its pass ran: told it was coming, told it's here
        _deliver_guide(root, iid, rec)


def _library(out, subject, rid=None):
    """The guide in the research library, so any project finds it (`colony research`); by Q Branch, not a project."""
    from . import research
    try:
        meta = json.loads((out / "log.json").read_text())
    except (OSError, ValueError):
        meta = {}
    path = folder() / "library-entry.md"
    path.write_text((out / "brief.md").read_text())
    try:
        entry = research.add(path, meta.get("domain") or subject[:80], subject, "Q Branch", tags=("qbranch",))
    finally:
        path.unlink(missing_ok=True)
    (out / "library.json").write_text(json.dumps({"id": entry["id"]}))
    return entry


def _refresh_library(out):
    from . import research
    try:
        rid = json.loads((out / "library.json").read_text())["id"]
        f = research.folder() / f"{rid}.json"
        entry = json.loads(f.read_text())
        entry["text"] = (out / "brief.md").read_text().strip() + "\n\n" + research.supports.NOTICE
        f.write_text(json.dumps(entry, indent=1))
    except (OSError, ValueError, KeyError):
        pass


def _decision_job(root, iid, job, run):
    state = load(root) or {"items": {}}
    rec = state["items"].get(iid) or {}
    if rec.get("status") != "ready" or not rec.get("path"):
        return
    d, names = job["decision"], private(root)
    out = folder() / "guides" / rec["guide"]
    given = json.loads((out / "given.json").read_text())
    work = _scratch()
    usage = work / "usage.jsonl"
    (work / "cwd").mkdir()
    inp = {"subject": given["subject"], "requirements": given["requirements"],
           "decision": redact(" ".join(d["decision"].split()), names), "default": redact(" ".join(d["default"].split()), names),
           "guide": rec["path"], "out": str(work / "out"), "runner": runners(), "screen": True, "cwd": str(work / "cwd"),
           "source_ledger": str(folder() / "ledger.json")}
    code, _, err = core("decision", inp, usage, run)
    used = spent(usage)
    addendum = work / "out" / "addendum.md"
    if code != 0 or not addendum.exists():
        _record_run(root, iid, "decision", "failed", used, decision=d["decision"], error=err.strip()[-300:])
        shutil.rmtree(work, ignore_errors=True)
        return
    text = addendum.read_text()
    with open(rec["path"], "a") as fh:
        fh.write("\n" + text.rstrip("\n") + "\n")
    shutil.rmtree(work, ignore_errors=True)
    _refresh_library(out)
    holds = text.startswith("- ") and "the default holds" in text
    _record_run(root, iid, "decision", "default holds" if holds else "added", used, decision=d["decision"])
    note(root, iid, f"Q Branch on the decision you added to {iid} (\"{d['decision']}\"): "
                    + ("the default holds; that line is added to its guide" if holds else "a section is added to its guide")
                    + f", at {rec['path']}.")


def _learn_job(root, iid, job, run):
    state = load(root) or {"items": {}}
    rec = state["items"].get(iid) or {}
    cur = rec.get("curated") or {}
    if not cur.get("out"):
        return
    it = board.items(board.roadmap(root)).get(iid) or {"text": iid}
    details = board.item_details(root, iid)
    for name in ("For the guide", "Research"):
        body = section(details, name)
        if body is not None:
            details = details.replace(body, "\n")
    work = _scratch()
    usage = work / "usage.jsonl"
    (work / "cwd").mkdir()
    names = private(root)
    inp = {"out": cur["out"], "shelf_dir": str(shelf_dir()), "outcome": {"built": f"{it['text']}\n\n{details}"[:6000],
                                                                       "rework": ""},
           "label": name_or(root), "private": names, "candidates": job["candidates"], "runner": runners(),
           "screen": True, "cwd": str(work / "cwd"), "source_ledger": str(folder() / "ledger.json")}
    code, out, err = core("learn", inp, usage, run)
    used = spent(usage)
    shutil.rmtree(work, ignore_errors=True)
    try:
        result = json.loads(out) if code == 0 else {}
    except ValueError:
        result = {}
    _record_run(root, iid, "learn", "done" if code == 0 else "failed", used, woven=result.get("woven", []),
                not_woven=result.get("not_woven", []), error=err.strip()[-300:] if code else None)
    update(root, lambda s: s["items"][iid]["curated"].update(learned=board.now()))


def name_or(root):
    from .project_names import name_of
    return name_of(root)


# ---------------------------------------------------------------- the watcher's look at one project

COMING = ("Q Branch is researching {iid} (it is flagged research): its guide is coming, and a note will point to it "
          "when it's ready. Begin with what doesn't turn on the Research section's decisions.")
READY = "Q Branch's research guide for {iid} is at {path}: read it before you design; the calls are yours."
# A pointer: the hand-over at the top of the guide says to search beyond it too, before designing (the person).
CURATED = ("A curated Q Branch guide bears on {iid}: \"{title}\", at {path}. Read it and its hand-over before you "
           "design; the calls are yours.")
# The question at [x] asks what the hand-over said it would, in its words (Memory Research's 9f92e21), and only after
# the agent has both used the guide and searched beyond it (the person, 2026-11-06; "nothing backs it" from
# Memory Research's 68d3a79).
UPKEEP = ("{iid} is done, and it was handed the curated guide \"{title}\". Answer this only after both using the guide "
          "and searching the web yourself beyond it; if you didn't search while building, run a short search now on what "
          "{iid} needed. Did {found} turn up anything that would change what the next agent decides "
          "after reading the section it belongs in? It fails if the guide already says it, if "
          "it fits only your project, or if nothing backs it: a finding from your build must have held up in it, and "
          "one from your search needs a primary source (the maker's own docs, code or paper) that your build didn't "
          "contradict. Where the guide's advice failed you, that is a finding too. If something passes, add a "
          "`## For the guide` section to {details}: each finding in one or two generic sentences with its source URL, "
          "saying whether your build tested it, at most three. If not, add nothing (the default).")


def _deliver_guide(root, iid, rec):
    if rec.get("told_ready"):
        return
    note(root, iid, READY.format(iid=iid, path=rec["path"]))
    update(root, lambda s: s["items"][iid].update(told_ready=board.now()))


def _hand_over(root, iid, it, run=None):
    """Match a curated guide against an item once; a match's hand-over is made then, delivered at the item's start."""
    state = load(root) or {"items": {}}
    if "curated" in (state["items"].get(iid) or {}):
        return
    text = it["text"] + "\n" + it.get("desc", "") + "\n" + board.item_details(root, iid)
    hit = match(text)
    cur = None
    if hit:
        eid, term = hit
        out = Path(root) / ".board" / "qbranch" / iid
        work = _scratch()
        code, _, err = core("brief", {"entry": eid, "shelf": [{"id": eid, "path": str(shelf_dir() / f"{eid}.md")}],
                                      "out": str(out), "item": iid, "question": it["text"], "screen": False}, work / "u",
                            run)
        shutil.rmtree(work, ignore_errors=True)
        if code == 0:
            cur = {"entry": eid, "term": term, "title": curated()[eid]["title"], "out": str(out),
                   "path": str(out / "brief.md")}
        log(root, {"event": "match", "item": iid, "entry": eid, "term": term, "ok": code == 0,
                   **({"error": err.strip()[-300:]} if code else {})})
        with board.board_lock("qbranch-matches"):
            folder().mkdir(parents=True, exist_ok=True)
            with open(folder() / "matches.jsonl", "a") as fh:
                fh.write(json.dumps({"at": board.now(), "project": str(root), "item": iid, "entry": eid, "term": term})
                         + "\n")
    update(root, lambda s: s["items"].setdefault(iid, {}).update(curated=cur))


def check(root, run=None):
    """Called every few seconds per project by the watcher: what moved since the last look, and what it starts."""
    root = Path(root)
    road = board.roadmap(root)
    its = board.items(road)
    now = {i: it["state"] for i, it in its.items()}
    state = load(root)
    if state is None:                         # first look: what was before isn't news
        update(root, lambda s: s.update(states=now))
        return
    before = state.get("states", {})
    if before != now:
        update(root, lambda s: s.update(states=now))
    if not on(root):
        return
    order = list(its)
    started = [i for i in order if now[i] == "doing" and before.get(i) != "doing"]
    finished = [i for i in order if now[i] == "done" and before.get(i) not in ("done", None)]
    for iid in started:
        _started(root, its, order, iid, run)
    for iid in finished:
        _finished(root, its, iid)
    _look(root, its)
    _recover(root)
    if queue():
        drain()


def busy():
    """Whether a worker anywhere on this machine holds the run lock."""
    import fcntl
    folder().mkdir(parents=True, exist_ok=True)
    with open(folder() / "run.lock", "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False


def _recover(root):
    """A pass left running when its board stopped is queued again: nothing runs it now."""
    state = load(root) or {"items": {}}
    stuck = [i for i, r in state["items"].items() if r.get("status") == "running"]
    if not stuck or busy():
        return
    for iid in stuck:
        update(root, lambda s, iid=iid: s["items"][iid].update(status=None))
        _queue_pass(root, iid)


def _started(root, its, order, iid, run):
    it = its[iid]
    for other in [iid] + [i for i in order[order.index(iid) + 1:] if its[i]["state"] == "todo"][:1]:
        _hand_over(root, other, its[other], run)
    rec = (load(root) or {"items": {}})["items"].get(iid) or {}
    if rec.get("curated") and not rec["curated"].get("told"):
        note(root, iid, CURATED.format(iid=iid, title=rec["curated"]["title"], path=rec["curated"]["path"]))
        update(root, lambda s: s["items"][iid]["curated"].update(told=board.now()))
    if research_of(root, it):
        if rec.get("status") == "ready":
            _deliver_guide(root, iid, rec)
        else:
            if rec.get("status") not in ("queued", "running"):
                _queue_pass(root, iid)
            if not rec.get("told_coming"):
                note(root, iid, COMING.format(iid=iid))
                update(root, lambda s: s["items"].setdefault(iid, {}).update(told_coming=board.now()))
    for other in order:                          # the next flagged item, in roadmap order, without a guide
        if other == iid or its[other]["state"] != "todo":
            continue
        r = (load(root) or {"items": {}})["items"].get(other) or {}
        if r.get("status") in ("queued", "running", "ready"):
            continue
        if research_of(root, its[other]):
            _queue_pass(root, other)
            break


def _queue_pass(root, iid):
    if enqueue({"kind": "pass", "root": str(root), "item": iid}):
        update(root, lambda s: s["items"].setdefault(iid, {}).update(status="queued"))


def _finished(root, its, iid):
    rec = (load(root) or {"items": {}})["items"].get(iid) or {}
    cur = rec.get("curated")
    if not cur or not cur.get("told") or cur.get("asked"):
        return
    from . import lead
    try:
        details = lead.details_path(root, iid)
    except Exception:
        details = f"roadmap/{iid}.md"
    # A flagged item had a research pass too: it maintains the guide as much as the agent's own search does (the person).
    found = "your research guide, your own search or your build" if rec.get("status") == "ready" else \
        "your search or your build"
    # Not quiet: an agent often goes idle once its item is done, and the question is for while the work is fresh.
    note(root, iid, UPKEEP.format(iid=iid, title=cur["title"], details=details, found=found), quiet=False)
    update(root, lambda s: s["items"][iid]["curated"].update(asked=board.now()))


def _look(root, its):
    """Every look: a decision added to a researched item's section, and an answer to the [x] question."""
    state = load(root) or {"items": {}}
    for iid, rec in state["items"].items():
        it = its.get(iid)
        if not it:
            continue
        if rec.get("status") in ("ready", "running") and rec.get("given"):
            now = research_of(root, it)
            if now:
                given = rec["given"]["decisions"] + rec.get("asked_decisions", [])
                for d in added(given, now["decisions"]):
                    if enqueue({"kind": "decision", "root": str(root), "item": iid, "decision": d}):
                        update(root, lambda s, d=d: s["items"][iid].setdefault("asked_decisions", []).append(d))
        cur = rec.get("curated") or {}
        if cur.get("asked") and not cur.get("answered"):
            body = section(board.item_details(root, iid), "For the guide")
            if body is None:
                continue
            found = candidates(body)
            if found:
                enqueue({"kind": "learn", "root": str(root), "item": iid, "candidates": found})
            update(root, lambda s, n=len(found): s["items"][iid]["curated"].update(answered=board.now(), candidates=n))


# ---------------------------------------------------------------- what the board shows

def item_state(root, iid):
    """The item's research as the board shows it: {"flag", "guide": coming|queued|running|ready|failed|None,
    "curated": title or None, "term", "tokens", "cost_usd", "path"}."""
    rec = ((load(root, cached=True) or {}).get("items") or {}).get(iid) or {}
    runs = rec.get("runs", [])
    tok = sum(sum(r["tokens"].get(k, 0) for k in ("input", "cache_read", "cache_write", "output")) for r in runs)
    cur = rec.get("curated") or {}
    return {"guide": rec.get("status"), "path": rec.get("path"), "curated": cur.get("title"), "term": cur.get("term"),
            "curated_path": cur.get("path"), "tokens": tok, "runs": runs, "error": rec.get("error"),
            "cost_usd": round(sum(r["tokens"].get("cost_usd", 0) for r in runs), 4)}


GUIDE_LABEL = {"queued": "coming", "running": "coming", "ready": "ready", "failed": "failed"}


def badge(root, it):
    """A short marker for the item's row: its research flag and its guide's state, and a curated match."""
    if not it.get("research") and not ((load(root, cached=True) or {}).get("items") or {}).get(it["id"], {}).get("curated"):
        return ""
    s = item_state(root, it["id"])
    from .board import e
    out = []
    if it.get("research"):
        label = GUIDE_LABEL.get(s["guide"], "flagged")
        out.append(f"<span class='badge' title='Research: {e(label)}"
                   + (f", {s['tokens']:,} tokens" if s["tokens"] else "") + f"'>research: {e(label)}</span>")
    if s["curated"]:
        out.append(f"<span class='badge' title='Curated guide matched on \"{e(s['term'])}\"'>guide: {e(s['curated'])}</span>")
    return " " + " ".join(out)


def item_card(root, it):
    """The item page's card: the flag, the guide's state and where it is, the curated match and the term that hit,
    and each pass's tokens."""
    s = item_state(root, it["id"])
    if not it.get("research") and not s["curated"]:
        return ""
    from .board import e
    from . import tokens
    rows = []
    if it.get("research"):
        label = GUIDE_LABEL.get(s["guide"], "flagged, no pass yet")
        rows.append(f"<p><b>Research</b>: {e(label)}" + (f" · <code>{e(s['path'])}</code>" if s["path"] else "")
                    + (f" <span class='muted'>({e(s['error'])})</span>" if s["guide"] == "failed" and s["error"] else "")
                    + "</p>")
    if s["curated"]:
        rows.append(f"<p><b>Curated guide</b>: {e(s['curated'])} <span class='muted'>(matched on “{e(s['term'])}”)</span>"
                    + (f" · <code>{e(s['curated_path'])}</code>" if s["curated_path"] else "") + "</p>")
    for r in s["runs"]:
        t = r["tokens"]
        rows.append(f"<p class='muted small'>{e(r['kind'])} · {e(r['status'])} · {e(r['at'])} · "
                    f"{tokens.short(t.get('input', 0) + t.get('cache_read', 0) + t.get('cache_write', 0))} in, "
                    f"{tokens.short(t.get('output', 0))} out"
                    + (f" · ${t['cost_usd']:.2f}" if t.get("cost_usd") else "") + "</p>")
    return "<div class='card'><h3>Q Branch</h3>" + "".join(rows) + "</div>"
