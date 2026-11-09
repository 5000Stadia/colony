"""Status reports (R150): on the person's button, or on a schedule they set, a fresh model writes them a report on every
project on the board. It covers what each project is at and what moved, and lays out each thing waiting on them: the
situation, whether one choice is obvious, the likely options with their pros and cons, and a recommendation. Each
item is answerable where it stands, with the board's own controls. The reports are kept in a feed, newest first.

Colony gathers the facts itself, by the board's own definitions (what waits on the person is waiting_items). The
writer is handed only those facts, with no tools: it can't act, and has nothing to wander into. What it is handed is
complete by construction: an item it leaves out is still listed under its own question, and a reference it invents
is dropped.

Off by default. A schedule is every N hours from a start time, or once a day at a time, either one on the days
ticked."""
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import board

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
EVERY = (1, 2, 3, 4, 6, 8, 12)
DEFAULT = {"mode": "off", "every": 4, "at": "12:00", "days": [0, 1, 2, 3, 4], "since": None}
GRACE = 3600              # a slot the board was down for runs once if it is back within the hour, never later
STALE = 30 * 60           # a report still writing after this (or begun by a board since restarted) is given up
LATELY = 7 * 86400        # with no report before it, "lately" is the last week
SHOWN = 30                # the reports the feed shows; older ones stay in the file
KEPT = 200                # reports kept in all
RUNS_KEPT = 40            # the briefs kept beside them, for reading what a writer was handed
CUT = {"said": 1500, "ask": 1500, "why": 900, "goal": 400, "point": 400}

_lock = threading.Lock()
_running = False


def _secs(at):
    """A board time ("...Z", or any ISO time with its offset) as epoch seconds; 0 for none."""
    if not at:
        return 0
    try:
        return board.epoch(at)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0


def folder():
    return board.home() / "reports"


def _path():
    return folder() / "reports.jsonl"


def reports():
    """Every report kept, oldest first."""
    out = []
    try:
        lines = _path().read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _keep(rep):
    folder().mkdir(parents=True, exist_ok=True)
    every = reports()[-(KEPT - 1):] + [rep]
    tmp = _path().with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in every))
    tmp.replace(_path())
    runs = sorted((folder() / "runs").glob("*")) if (folder() / "runs").exists() else []
    for old in runs[:-RUNS_KEPT]:
        for f in old.glob("*"):
            f.unlink()
        old.rmdir()


def _state():
    try:
        return json.loads((folder() / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_state(**changes):
    folder().mkdir(parents=True, exist_ok=True)
    board.save_json(folder() / "state.json", dict(_state(), **changes))


# --- the schedule

def schedule():
    """The person's schedule, as saved in Settings: off unless they set one."""
    saved = board.registry()["settings"].get("status_reports") or {}
    out = dict(DEFAULT, **{k: v for k, v in saved.items() if k in DEFAULT})
    out["days"] = sorted({int(d) for d in out.get("days") or [] if str(d).isdigit() and 0 <= int(d) < 7})
    return out


def set_schedule(mode, every=4, at="12:00", days=()):
    """Save the person's schedule. It counts from now: a slot already past never fires for a schedule just set."""
    mode = mode if mode in ("off", "hours", "daily") else "off"
    every = int(every) if str(every).isdigit() and int(every) in EVERY else DEFAULT["every"]
    at = at if re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", at or "") else DEFAULT["at"]
    days = sorted({int(d) for d in days if 0 <= int(d) < 7}) or list(range(7))
    reg = board.registry()
    reg["settings"]["status_reports"] = {"mode": mode, "every": every, "at": at, "days": days, "since": board.now()}
    board.save_registry(reg)
    return schedule()


def _clock(at):
    h, m = (int(x) for x in at.split(":"))
    return h, m


def describe(sched):
    """The schedule as the person would say it: Off; Weekdays at 12:00 PM; Every 4 hours from 8:00 AM, every day."""
    if sched["mode"] not in ("hours", "daily"):
        return "Off"
    h, m = _clock(sched["at"])
    at = datetime(2000, 1, 1, h, m).strftime("%-I:%M %p")
    days = sched["days"]
    on = ("every day" if len(days) == 7 else "weekdays" if days == [0, 1, 2, 3, 4] else "weekends" if days == [5, 6]
          else ", ".join(DAYS[d] for d in days))
    if sched["mode"] == "daily":
        return f"{on[0].upper() + on[1:]} at {at}"
    every = sched["every"]
    return f"Every {'hour' if every == 1 else f'{every} hours'} from {at}, {on}"


def slots(sched, start, end):
    """The moments a schedule fires in (start, end], as epoch seconds, by the machine's clock. Every N hours runs from
    its start time to the day's end, on each day ticked: every 4 hours from 8:00 AM is 8, 12, 4 and 8."""
    if sched["mode"] not in ("hours", "daily") or end <= start:
        return []
    h, m = _clock(sched["at"])
    step = 24 if sched["mode"] == "daily" else sched["every"]
    out = []
    day = datetime.fromtimestamp(start).date() - timedelta(days=1)
    last = datetime.fromtimestamp(end).date()
    while day <= last:
        if day.weekday() in sched["days"]:
            t = datetime(day.year, day.month, day.day, h, m)
            while t.date() == day:
                ts = t.timestamp()
                if start < ts <= end:
                    out.append(ts)
                t += timedelta(hours=step)
        day += timedelta(days=1)
    return out


def check(now=None, writer=None):
    """Called from the board's watcher: a scheduled report, when its slot has come. Each slot is taken once; one the
    board was down for runs if the board is back within GRACE, and is passed over after. True when one began."""
    if os.environ.get("COLONY_TEST_FORBIDS") and writer is None:   # the suite: a watcher's look starts no model
        return False
    sched = schedule()
    if sched["mode"] == "off":
        return False
    now = now or time.time()
    st = _state()
    since = max(_secs(sched["since"]) if sched.get("since") else now, st.get("slot") or 0)
    due = slots(sched, since, now)
    if not due:
        return False
    _save_state(slot=due[-1])
    if now - due[-1] > GRACE:
        return False
    return start("schedule", writer=writer)


# --- writing one

def writing():
    """The report being written now ({"at", "trigger"}), or None. One begun by a board since restarted, or still at it
    after STALE, was given up."""
    w = _state().get("writing")
    if not w:
        return None
    if w.get("pid") != os.getpid() or time.time() - _secs(w["at"]) > STALE:
        return None
    return w


def start(trigger, writer=None, wait=False):
    """Begin a report in the background (trigger: "button" or "schedule"); False when one is already being written."""
    global _running
    with _lock:
        if _running or writing():
            return False
        _running = True
        _save_state(writing={"at": board.now(), "trigger": trigger, "pid": os.getpid()})

    def work():
        global _running
        try:
            run(trigger, writer=writer)
        except Exception as err:                      # kept as a report that failed, never a silent nothing
            _keep({"id": "r" + secrets.token_hex(4), "at": board.now(), "trigger": trigger, "needs": [],
                   "projects": [], "writer": None, "text": "", "error": f"{err.__class__.__name__}: {err}"[:300]})
        finally:
            with _lock:
                _running = False
                _save_state(writing=None)
    t = threading.Thread(target=work, daemon=True)
    t.start()
    if wait:
        t.join()
    return True


def run(trigger, writer=None):
    """Gather, write and keep one report; returns it."""
    began = time.time()
    rid = "r" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    before = reports()
    since = before[-1]["at"] if before else None
    facts = gather(since)
    brief = BRIEF + "\n\n" + facts["text"]
    here = folder() / "runs" / rid
    here.mkdir(parents=True, exist_ok=True)
    (here / "brief.txt").write_text(brief)
    said = (writer or write)(brief, here)
    (here / "reply.txt").write_text(said.get("text") or "")
    parsed = parse(said.get("text") or "") if not said.get("error") else None
    rep = {"id": rid, "at": board.now(), "trigger": trigger, "since": since, "needs": facts["needs"],
           "projects": facts["projects"], "writer": parsed, "text": "" if parsed else (said.get("text") or ""),
           "error": said.get("error") or (None if parsed or not said.get("text") else "the writer's reply wasn't readable"),
           "model": said.get("model"), "effort": said.get("effort"), "family": said.get("family"),
           "cost": said.get("cost"), "took": round(time.time() - began)}
    _keep(rep)
    return rep


def write(brief, here):
    """PROVIDER: one fresh, stateless call on colony's routine tier, handed the brief alone: in an empty folder,
    none of the person's settings, connectors or hooks, and nothing to edit or run."""
    from . import providers, room, tokens
    p = providers.of(None)
    family = providers.key(p)
    model, effort = room.tier(family, "routine")
    try:
        said = p.consult(brief, model, effort or "medium", here)
    except NotImplementedError:
        return {"text": "", "error": f"{p.label} can't write a report here", "model": model, "family": family}
    tokens.note(family, said.get("usage"), "report")
    return dict(said, model=model, effort=effort, family=family)


def parse(text):
    """The writer's reply as {"overview", "projects": [{name, status}], "needs": [{ref, title, situation, options:
    [{choice, pros, cons}], recommend (an option's own words, or empty), obvious (True when one choice is), why}]};
    None when it can't be read."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m[0])
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    text_of = lambda v: v.strip() if isinstance(v, str) else ""
    projects = [{"name": text_of(x.get("name")), "status": text_of(x.get("status"))}
                for x in d.get("projects") or [] if isinstance(x, dict) and text_of(x.get("name"))]
    needs = []
    for x in d.get("needs") or []:
        if not isinstance(x, dict) or not text_of(x.get("ref")):
            continue
        opts = [{"choice": text_of(o.get("choice")), "pros": text_of(o.get("pros")), "cons": text_of(o.get("cons"))}
                for o in x.get("options") or [] if isinstance(o, dict) and text_of(o.get("choice"))]
        needs.append({"ref": text_of(x["ref"]), "title": text_of(x.get("title")), "situation": text_of(x.get("situation")),
                      "options": opts, "recommend": text_of(x.get("recommend")), "obvious": x.get("obvious") is True,
                      "why": text_of(x.get("why"))})
    if not text_of(d.get("overview")) and not projects and not needs:
        return None
    return {"overview": text_of(d.get("overview")), "projects": projects, "needs": needs}


# --- what the writer is handed

BRIEF = """You write the person's status report for their colony: their set of projects, each with its own AI agent,
which they follow and steer from one board. They read it when they have fallen behind and may have lost their own
thread: it should orient them, show at a glance where each project stands, and lay out everything waiting on them so
they can answer it from the report (each item you write about gets its answer controls beneath it).

For each thing waiting on them: describe the situation that calls for their answer; say whether there's an obvious
choice; give the likely options with the pros and cons of each; and give your recommendation, so that where they agree
they can answer in a tap. The page shows each option as a button with its pros and cons on it, marks the one you
recommend, and shows when a default stands: don't repeat those in the situation.

How to write:
- Plain words for someone who may have lost the thread: no jargon, no file names, no ids. Name roadmap items by what
  they are; a number like R12 alone means nothing to them.
- Address the person as "you". Use only the facts below; where they leave something out, don't guess, and say what
  isn't known if it matters.
- Where a deadline has passed, a question no longer applies, or two items are the same question, say so.
- Where an item lists its options, use those options, in their own words exactly, so each tap matches; "recommend"
  is one of them, word for word. Where the item's own agent recommends something and you would choose otherwise,
  say why.
- Where the choice itself is the person's to weigh (how they value their time or money, a financial commitment,
  what they're comfortable sharing, their taste, their family), leave "recommend" empty and say in "why" what it turns on. Otherwise don't hold
  back: a question that only asks for facts they hold is no such choice, so recommend how to answer it (what to
  send, the quickest way). Use people's names as the facts give them.
- Be brief: a situation in two to four sentences, each pro and con in a short phrase.

Reply with one JSON object and nothing else:
{"overview": "two to four sentences: the big picture: what's moving, what's stuck, what most needs them",
 "projects": [{"name": "the project's name exactly as given", "status": "one to three sentences: what it is doing
   or at, what moved lately, and what it waits on; for one that is off or paused, say so"}],
 "needs": [{"ref": "the item's ref exactly as given, like n3", "title": "a few words naming the decision",
   "situation": "...", "options": [{"choice": "...", "pros": "...", "cons": "..."}],
   "recommend": "the option you recommend, word for word, or empty", "obvious": true when the choice is obvious,
   "why": "one sentence: why that one (or, with none, what the choice turns on)"}]}
List every project, in the order given. List every item under WAITING, by its ref, most pressing first: a deadline
near, work blocked on it, or a default about to stand."""


def _cut(text, n, end=False):
    text = " ".join(str(text or "").split())
    if len(text) <= n:
        return text
    return "…" + text[-(n - 1):] if end else text[:n - 1].rstrip() + "…"


def _last_words(p, working):
    """What the project's agent last told the person: the closing prose of its last finished turn (and, while it
    works, what it is saying now), with when."""
    from . import chat, context
    try:
        s = context.session(p)
        turns = chat.turns(chat.events(s["provider"], s["path"])) if s else []
    except Exception:
        return "", "", ""
    said = lambda t: [x for k, x in t["items"] if k == "say" and x.strip()]
    now = ""
    if working and turns and said(turns[-1]):
        now = _cut(said(turns[-1])[-1], 300)
        turns = turns[:-1]
    for t in reversed(turns):
        if said(t):
            return _cut(said(t)[-1], CUT["said"], end=True), t.get("at") or "", now
    return "", "", now


def _when(iso):
    try:
        return time.strftime("%a %b %-d, %-I:%M %p", time.localtime(_secs(iso)))
    except Exception:
        return ""


def _need_text(ref, p, group):
    """One moment as the writer reads it."""
    from . import hours
    w = group[0]
    head = f"[{ref}] {board.name_of(p)} · "
    if w["kind"] == "gate":
        g = w["gate"]
        out = [head + f"a decision its agent put to you · opened {_when(g['at'])}", f"Question: {g['question']}"]
        if g.get("why"):
            out.append("Why: " + _cut(g["why"], CUT["why"]))
        if g.get("options"):
            out.append("Options: " + " | ".join(g["options"]))
        for pt in g.get("points") or []:
            out.append(f"Point {pt['id']} (accept or reject): " + _cut(pt["text"], CUT["point"]))
        if g.get("recommended"):
            out.append(f"Its agent recommends: {g['recommended']}")
        if g.get("default"):
            at = board.stands_at(p, g)
            out.append(f"If you skip it: {g['default']}" + (f" (stands {hours.at_day(at)})" if at else ""))
        else:
            out.append("No default: it waits for your answer.")
        return "\n".join(out)
    if w["kind"] == "ask":
        a = w["ask"]
        qs = board.question_lines(a["text"])
        out = [head + f"a question its agent asked in its console · {_when(a['at'])}"]
        if qs:
            out.append("Its question: " + " ".join(qs[-3:]))
        out.append("Its words (the end): " + _cut(a["text"], CUT["ask"], end=True))
        for x in group[1:]:
            out.append(f"Also ready for your OK: {x['item']['what']}")
        return "\n".join(out)
    if w["kind"] == "checkpoint":
        c = w["checkpoint"]
        return "\n".join([head + "a finished version for you to review", f"What it is: {c['outcome']}",
                          f"Done means: {c['definition']}", f"To check: {c['check']}"])
    if w["kind"] == "choice":
        return "\n".join([head + "a choice on its console's screen", "Question: " + " ".join(w["question"]),
                          "Options: " + " | ".join(w["options"])])
    if w["kind"] == "screen":
        return head + "its console needs you, in a way colony can't read: " + _cut(" / ".join(w["lines"]), 300)
    return head + "ready for your OK: " + "; ".join(
        x["item"]["what"] + (f" (to check: {x['item']['check']})" if x["item"].get("check") else "") for x in group)


def gather(since=None):
    """The facts a report is written from: {"text": as the writer reads them, "projects": [{path, name, state}],
    "needs": [{ref, project, name, kind, key, gate, title}]}. Every project on the board in its order; every moment
    waiting on the person (paused projects' too, said to be held), each with a ref."""
    from . import console
    reg = board.registry()
    cutoff = _secs(since) if since else time.time() - LATELY
    lines, projects, needs = [], [], []
    for _, p, _ in board.arranged(board.projects(reg)):
        if not p.exists():
            continue
        try:
            snap = console.snapshot(p, lines=1)
            state = "paused" if board.paused(p) else snap["state"]
            road = board.roadmap(p)
            its = board.items(road)
            times = board.item_times(p)
            groups = board.moments_of(board.waiting_items(p, paused_too=True))
        except Exception:
            continue
        doing = [board.plain(it["text"]) for it in its.values() if it["state"] == "doing"]
        finished = [board.plain(it["text"]) for iid, it in its.items()
                    if it["state"] == "done" and _secs((times.get(iid) or {}).get("since") or "") > cutoff]
        since_arg = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(cutoff))
        log = [l for l in board.git(p, "log", f"--since={since_arg}", "--no-merges", "--format=%s", "-n", "8").splitlines() if l]
        count = board.git(p, "rev-list", "--count", "--no-merges", f"--since={since_arg}", "HEAD").strip()
        said, said_at, now_saying = _last_words(p, state == "working")
        refs = []
        for group in groups:
            ref = f"n{len(needs) + 1}"
            w = group[0]
            refs.append(ref)
            title = (w["gate"]["question"] if w["kind"] == "gate" else
                     (board.question_lines(w["ask"]["text"]) or [w["ask"]["text"][-200:]])[-1] if w["kind"] == "ask" else
                     w["checkpoint"]["outcome"] if w["kind"] == "checkpoint" else
                     " ".join(w.get("question") or []) if w["kind"] == "choice" else
                     "Its console needs you" if w["kind"] == "screen" else
                     "Ready for your OK: " + "; ".join(x["item"]["what"] for x in group))
            needs.append({"ref": ref, "project": str(p), "name": board.name_of(p), "kind": w["kind"], "key": w["key"],
                          "gate": w["gate"]["id"] if w["kind"] == "gate" else None, "title": _cut(title, 200),
                          "text": _need_text(ref, p, group)})
        projects.append({"path": str(p), "name": board.name_of(p), "state": state, "waiting": len(refs)})
        block = [f"## {board.name_of(p)} · {state}" + (" (paused by you: it holds what waits on you until you resume it)"
                                                       if state == "paused" else "")]
        if road.get("goal"):
            block.append("Vision: " + _cut(road["goal"], CUT["goal"]))
        at = board.at_work(p)
        if at:
            block.append(f"At: {at}")
        if doing:
            block.append("In progress: " + "; ".join(_cut(d, 140) for d in doing[:6]))
        if finished:
            block.append("Finished lately: " + "; ".join(_cut(d, 120) for d in finished[:8]))
        block.append(f"Commits lately: {count or 0}" + (" · latest: " + "; ".join(_cut(s, 90) for s in log) if log else ""))
        if said:
            block.append(f"Its agent last told you ({_when(said_at)}): {said}")
        if now_saying:
            block.append(f"Saying now: {now_saying}")
        block.append("Waiting on you: " + (", ".join(refs) if refs else "nothing"))
        lines.append("\n".join(block))
    head = (f"Now: {time.strftime('%a %b %-d, %-I:%M %p')} (the person's clock). \"Lately\" means since "
            + (f"the last report, {_when(since)}." if since else "a week ago."))
    text = (head + "\n\nPROJECTS, in the board's order\n\n" + "\n\n".join(lines)
            + "\n\nWAITING ON THE PERSON: every item, by ref\n\n"
            + ("\n\n".join(n.pop("text") for n in needs) if needs else "Nothing is waiting on them."))
    for n in needs:
        n.pop("text", None)
    return {"text": text, "projects": projects, "needs": needs}


# --- the page




def seen():
    """The person opened the reports: the newest is no longer new to them."""
    every = reports()
    if every and board.PERSONS_LOOK.get():
        _save_state(seen=every[-1]["id"])


def unseen():
    """A report the person hasn't opened yet: the menu marks its link."""
    every = reports()
    return bool(every) and _state().get("seen") != every[-1]["id"]


def _group(need, cache):
    """What still waits on the person for this item, as the board groups it, or None once nothing does."""
    p = Path(need["project"])
    if p not in cache:
        try:
            cache[p] = board.moments_of(board.waiting_items(p, paused_too=True))
        except Exception:
            cache[p] = []
    return next((g for g in cache[p] if g[0]["key"] == need["key"]), None)


def _ordered(rep):
    """The report's items, each with what the writer said of it: in the writer's order, then any it left out."""
    by_ref = {n["ref"]: n for n in rep.get("needs") or []}
    written = {}
    for x in (rep.get("writer") or {}).get("needs") or []:
        if x["ref"] in by_ref:
            written.setdefault(x["ref"], x)
    return [(by_ref[r], written.get(r)) for r in [*written, *(r for r in by_ref if r not in written)]]


def _controls(need, x, here, plist, cache):
    """The live answer to one item: its own controls while it waits, what was answered once it doesn't. A decision
    with options gets them as taps, each with the writer's pros and cons on it. (Whether it drew the writer's options,
    the html.)"""
    e = board.e
    p = Path(need["project"])
    pid = next((i for i, q in enumerate(plist) if q.resolve() == p.resolve()), None)
    if pid is None or not p.exists():
        return False, "<p class='muted'>This project is no longer on the board.</p>"
    group = _group(need, cache)
    if group and group[0]["kind"] == "gate" and group[0]["gate"].get("options") and not group[0]["gate"].get("points"):
        g = group[0]["gate"]
        hidden = f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='back' value='{e(here)}'>"
        clear = f"<div class='needfoot'>{board.clear_form(hidden, need['key'], 'Clear without an answer')}</div>"
        return True, (_taps(pid, p, g, x, here)
                      + board.gate_body(p, pid, g, here, bare=True, taps=False, tail=clear))
    if group:
        return False, board.moment_html(pid, p, group, here, label=False, bare=True)
    if need.get("gate"):
        g = next((g for g in board.gates(p) if g["id"] == need["gate"]), None)
        if g and g.get("answer"):
            said = ("Cleared without an answer." if g.get("cleared") else
                    g["answer"] if g.get("answered_by") in ("colony", "agent") else f"Your answer: {g['answer']}")
            return False, f"<div class='pre'>{e(said)}</div>"
    return False, "<p class='muted'>No longer waiting on you.</p>"


def _taps(pid, p, g, x, here):
    """A decision's options as taps: the recommended one first and marked (the writer's pick; with no word from the
    writer on it, its agent's), each with the writer's pros and cons, the default saying when it stands."""
    from . import hours
    e = board.e
    opts = list(g["options"])
    written = {o["choice"]: o for o in (x or {}).get("options") or []}
    rec = (x.get("recommend") if x else g.get("recommended")) or None
    rec = rec if rec in opts else None
    default = g.get("default") if g.get("default") in opts else None
    stands = ""
    if default and not g.get("waits_for"):
        at = board.stands_at(p, g)
        stands = (f"stands {hours.at_day(at)} if you skip it" if at and at > time.time() else
                  "stands if you skip it, once you've had a day")
    hidden = (f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='gate' value='{e(g['id'])}'>"
              f"<input type='hidden' name='back' value='{e(here)}'>")
    out = []
    for o in ([rec] if rec else []) + [o for o in opts if o != rec]:
        w = written.get(o) or {}
        out.append(f"<form method='post' action='/answer'>{hidden}<input type='hidden' name='text' value='{e(o)}'>"
                   f"<button class='choice{' rec' if o == rec else ''}'>"
                   + ("<span class='tag'>Recommended</span>" if o == rec else "") + f"<b>{e(o)}</b>"
                   + (f"<span class='rpro'>+ {e(w['pros'])}</span>" if w.get("pros") else "")
                   + (f"<span class='rcon'>− {e(w['cons'])}</span>" if w.get("cons") else "")
                   + (f"<span class='rstands'>{e(stands)}</span>" if o == default and stands else "") + "</button></form>")
    return "<div class='gatechoices rtaps'>" + "".join(out) + "</div>"


def _kind(need):
    return {"gate": "decision", "ask": "question", "checkpoint": "version to review", "choice": "choice on screen",
            "screen": "console", "verify": "ready for your OK"}.get(need["kind"], need["kind"])


def report_html(rep, plist, cache, here="/reports"):
    """One report: its overview, what waits on the person (numbered, each with its answer live), then each project."""
    e, prose = board.e, board.chat_prose
    w = rep.get("writer") or {}
    out = []
    if rep.get("error") and not w:
        out.append(f"<p class='muted'>The report couldn't be written: {e(rep['error'])}. What colony gathered is below.</p>")
    if w.get("overview"):
        out.append(f"<div class='roverview'>{prose(w['overview'])}</div>")
    elif rep.get("text"):
        out.append(f"<details><summary>What the writer said</summary>{prose(rep['text'])}</details>")
    items = []
    for need, x in _ordered(rep):
        tapped, controls = _controls(need, x, here, plist, cache)
        body = ""
        if x:
            body += prose(x["situation"]) if x["situation"] else ""
            if x["options"] and not tapped:
                body += "<ul class='ropts'>" + "".join(
                    f"<li><b>{e(o['choice'])}</b>" + (f"<span class='rpro'>+ {e(o['pros'])}</span>" if o["pros"] else "")
                    + (f"<span class='rcon'>− {e(o['cons'])}</span>" if o["cons"] else "") + "</li>" for o in x["options"]) + "</ul>"
            label, why = _recommended(x)
            body += f"<p class='rrec'><b>{label}:</b> {e(why)}</p>" if why else ""
        title = (x and x["title"]) or need["title"]
        items.append(f"<div class='rneed'><div class='rhead'><span class='num'>{len(items) + 1}</span>"
                     f"<b>{e(title)}</b> <span class='muted'>{e(need['name'])} · {e(_kind(need))}</span></div>"
                     f"{body}<div class='ranswer'>{controls}</div></div>")
    out.append(f"<h2>Needs you <span class='muted'>({len(items)})</span></h2>" + ("".join(items) if items else
               "<p class='muted'>Nothing was waiting on you.</p>"))
    status = {x["name"]: x["status"] for x in w.get("projects") or []}
    rows = []
    for pr in rep.get("projects") or []:
        state = pr["state"] + (f" · {pr['waiting']} waiting on you" if pr.get("waiting") else "")
        rows.append(f"<div class='rproj'><div><b>{e(pr['name'])}</b> <span class='muted'>{e(state)}</span></div>"
                    + (prose(status[pr["name"]]) if status.get(pr["name"]) else "") + "</div>")
    if rows:
        out.append("<h2>Projects</h2>" + "".join(rows))
    return "".join(out)


def _recommended(x):
    """(its label, why) for the writer's word on one item, as the page shows it."""
    old = x.get("obvious") if isinstance(x.get("obvious"), str) else ""      # a report kept before why and recommend
    why = x.get("why") or old or x.get("recommendation") or x.get("recommend") or ""
    return ("Obvious choice" if x.get("obvious") else "Recommend" if x.get("recommend") or x.get("recommendation")
            else "Your call"), why


def _one_line(text):
    """A line of the writer's as one line of a spoken document: no line break or heading mark to split a section."""
    return re.sub(r"\s+", " ", text or "").lstrip("#").strip()


def _flat(text):
    """The writer's prose kept in one section: its paragraphs, without the heading marks that would start another."""
    text = re.sub(r"(?m)^\s{0,3}(?:=+|-{2,})\s*$", "", text or "")
    return re.sub(r"(?m)^\s{0,3}#+\s*", "", text)


def audio(rep, plist):
    """One report for the board's document player: the overview, then each item waiting on the person, then each
    project, a section to each, so ⏭ and ⏮ step through them. An item answered since says so, without its options."""
    from . import document_audio
    w, cache = rep.get("writer") or {}, {}
    title = "Status report, " + time.strftime("%A %B %-d, %-I:%M %p", time.localtime(_secs(rep["at"])))
    items = _ordered(rep)
    head = (f"{len(items)} {'thing waits' if len(items) == 1 else 'things wait'} on you." if items
            else "Nothing was waiting on you.")
    out = [f"# {title}", _flat(w.get("overview") or rep.get("text")), head]
    for n, (need, x) in enumerate(items, 1):
        live = Path(need["project"]).exists() and _group(need, cache) is not None
        out.append(f"## {n}. {_one_line((x and x['title']) or need['title'])}")
        out.append(f"{need['name']}, {_kind(need)}." + ("" if live else " No longer waiting on you."))
        if x:
            out.append(_one_line(x.get("situation")))
            if live:
                out += [f"{_one_line(o['choice'])}." + (f" For: {_one_line(o['pros'])}." if o.get("pros") else "")
                        + (f" Against: {_one_line(o['cons'])}." if o.get("cons") else "") for o in x.get("options") or []]
            label, why = _recommended(x)
            out.append(f"{label}: {_one_line(why)}" if why else "")
    status = {x["name"]: x["status"] for x in w.get("projects") or []}
    for pr in rep.get("projects") or []:
        out.append(f"## {_one_line(pr['name'])}, {pr['state']}"
                   + (f", {pr['waiting']} waiting on you" if pr.get("waiting") else ""))
        out.append(_flat(status.get(pr["name"])))
    return document_audio.ingest("\n\n".join(s for s in out if s.strip()), title)


def _listen(rep):
    return (f"<button type='button' class='repplay' data-url='/reports/audio?r={board.e(rep['id'])}' "
            f"aria-label='Read this report aloud'>{board.ICONS['play']}<span>Listen</span></button>")


def _meta(rep):
    bits = [time.strftime("%a %b %-d, %-I:%M %p", time.localtime(_secs(rep["at"]))),
            "on schedule" if rep.get("trigger") == "schedule" else "from the button"]
    n = len(rep.get("needs") or [])
    bits.append(f"{n} waiting on you" if n else "nothing waiting")
    if rep.get("cost"):
        bits.append(f"about {max(1, round(rep['cost'] * 100))}¢")
    return " · ".join(bits)


def feed_html(reg):
    """The feed: a report being written, then the newest in full, then the earlier ones folded."""
    plist, cache = board.projects(reg), {}
    out = []
    w = writing()
    if w:
        out.append(f"<div class='card rwriting'>Writing a report, begun {board.e(time.strftime('%-I:%M %p', time.localtime(_secs(w['at']))))}"
                   " · it takes a minute or two.</div>")
    every = reports()[-SHOWN:][::-1]
    if not every and not w:
        out.append("<p class='muted'>No reports yet. New report writes one now.</p>")
    for i, rep in enumerate(every):
        if i == 0:
            out.append(f"<article class='card report'><div class='rmeta muted'><span>{board.e(_meta(rep))}</span>{_listen(rep)}</div>"
                       f"{report_html(rep, plist, cache)}</article>")
        else:
            out.append(f"<details class='card report old' data-k='rep-{board.e(rep['id'])}'><summary><span>{board.e(_meta(rep))}</span>"
                       f"{_listen(rep)}</summary>"
                       f"{report_html(rep, plist, cache)}</details>")
    return "".join(out)


def schedule_form(sched):
    e = board.e
    on = lambda mode: " checked" if sched["mode"] == mode else ""
    every = "".join(f"<option value='{n}'{' selected' if n == sched['every'] else ''}>{n}</option>" for n in EVERY)
    hours_at = sched["at"] if sched["mode"] == "hours" else "08:00"
    daily_at = sched["at"] if sched["mode"] != "hours" else DEFAULT["at"]
    days = "".join(f"<label class='rday'><input type='checkbox' name='day{d}' value='1'{' checked' if d in sched['days'] else ''}>"
                   f"{DAYS[d]}</label>" for d in range(7))
    return ("<form class='rschedform' method='post' action='/reports/schedule'>"
            f"<label><input type='radio' name='mode' value='off'{on('off')}> Off: only when you press New report</label>"
            f"<label><input type='radio' name='mode' value='hours'{on('hours')}> Every <select name='every'>{every}</select> hours, "
            f"from <input type='time' name='at_hours' value='{e(hours_at)}'></label>"
            f"<label><input type='radio' name='mode' value='daily'{on('daily')}> Once a day at "
            f"<input type='time' name='at_daily' value='{e(daily_at)}'></label>"
            f"<div class='rdays'><span class='muted'>On</span> {days}</div>"
            "<button>Save</button></form>")


# Listen on a report: the board's document player reads it, a section to each part (its bar steps and holds). In the
# folded summary the tap only plays, never opens or folds the report.
LISTEN = """
document.getElementById('reports').addEventListener('click', async (ev) => {
  const b = ev.target.closest('button.repplay'); if (!b) return;
  ev.preventDefault(); ev.stopPropagation();
  if (!window.colonyPlayer || b.disabled) return;
  const label = b.querySelector('span'); b.disabled = true; label.textContent = 'Loading…';
  const ok = await colonyPlayer.document(b.dataset.url);
  b.disabled = false; label.textContent = ok ? 'Listen' : 'Try again';
});
"""


def page(reg):
    from .board import KEEP_CURRENT, shell
    seen()
    sched = schedule()
    bar = ("<div class='card rbar'><form method='post' action='/reports/new'><button>New report</button></form>"
           f"<details class='rsched'><summary>Automatic: {board.e(describe(sched))}</summary>{schedule_form(sched)}</details></div>")
    return shell(reg, board.REPORTS_PAGE, "<h1>Status reports</h1>" + bar + f"<div id='reports'>{feed_html(reg)}</div>"
                 f"<script>{KEEP_CURRENT}keepCurrent(document.getElementById('reports'), '/reports/feed');{LISTEN}</script>")
