"""Turbo: when a program's weekly usage runs behind pace, colony turns its projects up so the week isn't wasted.

From day 2 of its window a program should have used its elapsed share of the week, aiming for 99% by the hour
before the reset. More than 5 points behind turns turbo on for that program's projects; catching up turns it off.
Each project ticks what turbo means for it (stronger models, going deeper on its queued work, research on a topic
the person typed), and only projects with queued work, or research ticked, are turned up.

It only ever adds to a project's work. A note wakes an idle console with nothing typed in it and nothing waiting on
the person; stronger models come through the idle reload, once a turbo episode, and leave without one; nothing is
written into any instructions. Off, it is nothing at all. It runs in the watcher's minute with no tokens, and when
a program's reading is missing or old it stays off.
"""
import json
import secrets
import time
from pathlib import Path

from . import board, console, providers, usage

HOUR, DAY = 3600, 86400
WEEK = 7 * DAY
TARGET = 99              # % of the week used by the hour before it resets
BEHIND = 5               # points behind pace that turn turbo on; catching up turns it off
FROM = 2 * DAY           # no judgement before day 2 of the window
STALE = 3 * HOUR         # an older reading isn't trusted: turbo stays off
RAISE = 2                # positions toward Intelligence for a project that ticks stronger models
# What turbo waits out (progress.hold): the person's pause, a hand-off, a refresh, a version or decision in their hands.
HOLDS = ("lead handoff", "person paused", "unresolved synchronization conflict", "version waiting for human review",
         "version stopped", "usage pause", "context refresh", "blocking decision", "question for the person")


# ---------------------------------------------------------------- pace and cadence

def expected(resets_at, now, length=WEEK):
    """How much of a window should be used by now to reach 99% the hour before it resets; None before day 2."""
    elapsed = now - (resets_at - length)
    if elapsed < FROM or now >= resets_at:
        return None
    return min(TARGET, TARGET * elapsed / (length - HOUR))


def pace(reading, now, on=False):
    """A program's pace from its latest reading ({at, windows}): its weekly use against what it would have used by
    now, and whether turbo is on. More than 5 points behind turns it on; on, it stays on until use catches up."""
    w = ((reading or {}).get("windows") or {}).get("weekly") or {}
    used, resets = w.get("used"), w.get("resets_at")
    out = dict(on=False, used=used, expected=None, resets_at=resets)
    if used is None or not resets or not reading.get("at"):
        return dict(out, why="no weekly usage reading")
    if now - reading["at"] > STALE:
        return dict(out, why=f"no usage reading in the last {STALE // HOUR} hours")
    exp = expected(resets, now)
    if exp is None:
        return dict(out, why="its week has just turned over" if now >= resets else "before day 2 of its week")
    on = used < exp - BEHIND or (bool(on) and used < exp)
    return dict(out, on=on, expected=exp, why="behind pace" if on else "on pace" if used >= exp
                else f"less than {BEHIND} points behind pace")


def due(last, now, resets_at):
    """Whether a turned-up project is due its note: at once, then daily; every 6 hours in the final two days."""
    return not last or now - last >= (6 * HOUR if resets_at - now <= 2 * DAY else DAY)


def raised(balance):
    """Stronger models: the Auto balance two positions toward Intelligence (0), never past it."""
    from .intelligence import position
    return max(0, position(balance) - RAISE)


def note(label, resets_at, deeper=False, research=""):
    """What a turned-up project is told: spare capacity until the reset, and how to spend it, alongside its work."""
    parts = [f"{label} has spare capacity this week, until it resets {usage.when(resets_at)}. Finish or continue your "
             "current item first: what follows comes alongside it or after it, never instead of it, and changes no "
             "item's scope."]
    if deeper:
        parts.append("Go deeper on your queued work within its agreed scope: prepare upcoming items, tests, reviews, "
                     "prior-art passes, parallel helpers on separate files, each committing only its own paths. Nothing "
                     "outside the agreed scope; Later still waits for the person.")
    if research:
        from .supports import NOTICE
        parts.append(f"Research the person asked for here: {research}. Give it to a read-only helper (it searches and "
                     "reads, and changes nothing), and keep its report in this project, under research/, ending with "
                     f"this line: {NOTICE}")
    return " ".join(parts + ["Only work worth doing: if there's none, say so and stop."])


def ask(label, rec, turned):
    """The monitor's one look while turbo is on: where research could bring a gain nobody has considered."""
    names = "; ".join(r.name + (f" (the person's research topic: {t})" if t else "") for r, t in turned)
    return (f"Turbo is on for {label} until it resets {usage.when(rec['resets_at'])}: {rec['used']:g}% of its week "
            f"used, {rec['expected']:.0f}% expected by now. Turned up: {names}. Glance at each (colony posture, colony "
            "peek NAME, its roadmap and vision) and judge, by your own lights, where research could bring a gain nobody "
            "has considered yet. Offer each such project one research suggestion with colony suggest NAME \"...\": "
            "yours, never the person's word, beside any topic they typed and never replacing it. Where nothing stands "
            "out, suggest nothing.")


# ---------------------------------------------------------------- which projects, and when

def options(root):
    """What turbo means for a project, as it ticked: stronger models, deeper work, research and its topic."""
    s = board.project_settings(root)[0]
    return {k: s.get(k, v) for k, v in board.TURBO.items()}


def queued(root):
    """Work queued for this project's agent: an item of its own to do or under way, outside any Later section."""
    from .progress import LATER
    me = str(Path(root).resolve())
    return any(i["state"] in ("todo", "doing") and i.get("owner", me) == me
               for m in board.roadmap(root)["milestones"] if not LATER.search(f"{m['id']} {m['title']}")
               for i in m["items"])


def wanted(root, opts=None):
    """What turbo does for a project, {models, deeper, research}: only for one with queued work, or research ticked
    with a topic; nothing for one whose options are all off."""
    opts = opts or options(root)
    topic = opts["turbo_topic"].strip() if opts["turbo_research"] else ""
    work = queued(root)
    if not (work or topic):
        return {}
    return {k: v for k, v in (("models", opts["turbo_models"]), ("deeper", work and opts["turbo_deeper"]),
                              ("research", topic)) if v}


def held(root, snap=None):
    """Why turbo leaves a project be now, or None: paused at a usage limit, waiting on the person (a gate, a question,
    a choice on its screen, a version or an item for their eye), or held for them (their pause, a hand-off, a
    context refresh). Turbo never buries what is in their hands."""
    from . import context, continuation, lead, progress
    if str(root) in usage.paused():
        return "paused at a usage limit"
    if board.waiting_items(root, snap):
        return "waiting on the person"
    if context.refreshing(root):
        return "refreshing its context"
    if lead.group(root):
        why = progress.hold(root, root)
        if why in HOLDS:
            return why
        stopped = continuation.status(root)
        if stopped.get("manual_stop") or stopped.get("native_stop"):
            return "its continuation is stopped"
    return None


def ready(root, snap):
    """Whether a note may wake the project now: its console idle, with nothing typed and no one at it."""
    name = console.session_name(root)
    return (snap["state"] == "idle" and not snap.get("scrolled") and not console.drafting(name)
            and not console.attached(name))


def settled(root):
    """No reload is on its way, so stronger models arrive before the deeper work starts."""
    return not board.registry()["settings"]["auto_update"] or not console.stale(root)


def unheard(root, nid):
    """Whether turbo's last note there still waits to be handed over."""
    return bool(nid) and any(n["id"] == nid and not n["delivered_at"] and not n["addressed_at"] for n in board.notes(root))


# ---------------------------------------------------------------- stronger models

def picks(root):
    """Stronger models for a project: its main agent and routine helpers as Auto picks them two positions toward
    Intelligence, from the same evidence and rules, never a model the person rejected or one awaiting their approval.
    Only the seats that change; the person's pins stay theirs."""
    from . import bench, intelligence, selection
    family = providers.key(providers.of(root))
    base = intelligence.position(board.project_settings(root)[0]["auto_balance"])
    if raised(base) == base:
        return {}
    state, every = selection.read(), bench.standings()
    ceiling = max((x["score"] for x in intelligence.pairs(every)), default=None)
    ask_first = board.registry()["settings"]["model_adoption"] == "ask"
    entries = [x for x in every if x["model"] not in state["rejected"] and (not ask_first or x["model"] in state["approved"])]
    out = {}
    for role in ("main", "routine"):
        ident = selection.key(family, role, selection.scope(root))
        chosen = selection.concrete(family, bench.role_pick(family, role, entries, balance=raised(base), ceiling=ceiling,
                                                            blocked=state["blocked"].get(ident, [])))
        if chosen and not selection.same(chosen, state["accepted"].get(ident)):
            out[role] = dict(selection.pair(chosen), why=f"Turbo: {intelligence.POSITIONS[raised(base)]} while "
                             f"{providers.get(family).label} has spare weekly capacity")
    return out


def pick(root, family, role):
    """Turbo's stronger pick for a project's seat while its program's turbo lasts, or None (selection asks)."""
    rec = load().get(family) or {}
    return ((rec.get("projects") or {}).get(str(root)) or {}).get("boost", {}).get(role) if rec.get("on") else None


def release(state, roots, change):
    """Stronger models end without a reload: a console keeps what it runs until it restarts for a reason of its own,
    and its record moves on with them, so this alone never reloads it."""
    from . import bench

    def fingerprint(r):
        try:
            return console.fingerprint(r)
        except (OSError, ValueError):
            return None
    was = {r: fingerprint(r) for r in roots if r.exists() and console.running(console.session_name(r))}
    change()
    save(state)
    for r in (r for r in roots if r.exists()):
        bench.write_helpers(r)
        name = console.session_name(r)
        if was.get(r) and console.started(name) == was[r] and fingerprint(r):
            console.adopt(name, fingerprint(r))


# ---------------------------------------------------------------- the watcher's minute

def path():
    return board.home() / "turbo.json"


def load():
    try:
        got = json.loads(path().read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def save(state):
    board.home().mkdir(parents=True, exist_ok=True)
    tmp = path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(path())


def tick(now=None):
    """The watcher's minute, with no tokens: each program's pace, and while turbo is on, what it does for that
    program's projects. What changed, as (program or project, what)."""
    from . import monitor
    now = time.time() if now is None else now
    state, done = load(), []
    s = board.registry()["settings"]
    roots, paused = [p for p in board.projects() if p.exists()], usage.paused()
    for key, prov in providers.PROVIDERS.items():
        rec = state.get(key) or {}
        p = (pace(usage.reading(key), now, rec.get("on")) if s["turbo_by"].get(key, True)
             else dict(on=False, why="turned off in Settings"))
        if not p["on"]:
            if rec.get("on"):
                done += end(state, key, dict(p, at=now))
            state[key] = dict(p, at=now)
            continue
        if not rec.get("on"):
            rec = dict(since=now, episode=secrets.token_hex(4), projects={})
            done.append((key, "on"))
        state[key] = rec = dict(rec, **p, at=now)
        rec.setdefault("projects", {})
        turned = []
        for root in [r for r in roots if providers.key(providers.of(r)) == key]:
            try:
                want, did = visit(state, rec, root, prov.label, now, paused)
            except Exception as err:                    # one project's trouble never stops turbo for the rest
                trouble(err)
                continue
            done += did
            if want:
                turned.append((root, want.get("research", "")))
        rec["projects"] = {r: m for r, m in rec["projects"].items() if m}
        if turned and now - rec.get("monitor", 0) >= DAY:
            rec["monitor"] = now
            if s["monitor"] and not monitor.muted():
                monitor.queue(ask(prov.label, rec, turned))
                done.append((key, "asked the monitor"))
    save(state)
    return done


def visit(state, rec, root, label, now, paused):
    """One project while its program's turbo is on: (what turbo does for it, what it did now)."""
    from . import bench
    mine, done = rec["projects"].setdefault(str(root), {}), []
    opts = options(root)
    if mine.get("boost") and not opts["turbo_models"]:
        release(state, [root], lambda: mine.update(boost={}))      # unticked: they go, without a reload
    want = wanted(root, opts)
    if not want or str(root) in paused:
        return {}, done
    boost = want.get("models") and "boost" not in mine
    wake = (want.get("deeper") or want.get("research")) and due(mine.get("noted"), now, rec["resets_at"])
    if not (boost or wake):
        return want, done
    snap = console.snapshot(root, lines=4)
    if held(root, snap):
        return want, done
    if boost:
        mine["boost"] = picks(root)                             # once a turbo episode, kept until it ends
        save(state)
        if mine["boost"]:
            bench.write_helpers(root)                           # with the main pick: one idle reload brings both
            done.append((root, "stronger models"))
    if wake and not unheard(root, mine.get("note")) and ready(root, snap) and settled(root):
        n = board.add_note(root, None, note(label, rec["resets_at"], want.get("deeper"), want.get("research", "")),
                           author="colony")
        mine.update(noted=now, note=n["id"])
        done.append((root, "noted"))
    return want, done


def end(state, key, off):
    """Turbo ends for a program: stronger models leave without a reload, and a note that never reached its project
    is withdrawn."""
    mine = {Path(r): m for r, m in ((state.get(key) or {}).get("projects") or {}).items()}
    release(state, [r for r, m in mine.items() if m.get("boost")], lambda: state.update({key: off}))
    for r, m in mine.items():
        if m.get("note") and r.exists() and unheard(r, m["note"]):
            board.append(r, "notes.jsonl", {"type": "addressed", "of": m["note"], "at": board.now(),
                                            "text": "Turbo ended before this reached you: nothing to do."})
    return [(key, "off")]


def trouble(err):
    """Turbo is extra: whatever goes wrong in it does nothing, and is kept for colony turbo to show."""
    board.home().mkdir(parents=True, exist_ok=True)
    (board.home() / "turbo-error.json").write_text(json.dumps({"at": time.time(), "error": f"{type(err).__name__}: {err}"}))


# ---------------------------------------------------------------- what the person sees

def line(key, now=None, named=True):
    """A program's pace in a line: its use against what's expected by now, turbo on or off, and the reset."""
    now = time.time() if now is None else now
    head = f"{providers.get(key).label}: " if named else ""
    if not board.registry()["settings"]["turbo_by"].get(key, True):
        return f"{head}turbo is off in Settings"
    p = pace(usage.reading(key), now, (load().get(key) or {}).get("on"))
    if p["expected"] is None:
        used = f"{p['used']:g}% of its week used; " if p["used"] is not None and p["why"].startswith("before") else ""
        return f"{head}{used}turbo off ({p['why']})"
    return (f"{head}{p['used']:g}% of its week used, {p['expected']:.0f}% expected by now: "
            + (f"turbo on until it resets {usage.when(p['resets_at'])}" if p["on"]
               else f"turbo off, {p['why']} (it resets {usage.when(p['resets_at'])})"))


def project_line(root):
    """What turbo does for a project now, in a line."""
    key = providers.key(providers.of(root))
    rec = load().get(key) or {}
    label = providers.get(key).label
    if not rec.get("on"):
        return f"Turbo is off for {label} now" + (f" ({rec['why']})." if rec.get("why") else ".")
    want, mine = wanted(root), (rec.get("projects") or {}).get(str(root)) or {}
    if not want:
        return f"Turbo is on for {label}, but this project has no queued work or research topic, so it isn't turned up."
    up = "; ".join(f"{role} {v['model']}" + (f" at {v['effort']}" if v.get("effort") else "")
                   for role, v in (mine.get("boost") or {}).items())
    what = [f"stronger models ({up})" if up else "stronger models" if want.get("models") else "",
            "going deeper on its queued work" if want.get("deeper") else "",
            f"research on {want['research']}" if want.get("research") else ""]
    why = held(root)
    return (f"Turbo is on for {label} until it resets {usage.when(rec['resets_at'])}: "
            + ", ".join(w for w in what if w) + (f"; left be for now: {why}." if why else "."))


WORDS = {"models": "stronger models", "deeper": "going deeper", "research": "research"}


def report(now=None):
    """Each program's pace, and for one in turbo, what each of its projects gets or why it is left be."""
    out = []
    for key in providers.PROVIDERS:
        out.append(line(key, now))
        rec = load().get(key) or {}
        if not rec.get("on"):
            continue
        for root in [r for r in board.projects() if r.exists() and providers.key(providers.of(r)) == key]:
            want = wanted(root)
            why = "no queued work or research topic" if not want else held(root)
            mine = (rec.get("projects") or {}).get(str(root)) or {}
            noted = f"; last note {time.strftime('%a %-I:%M %p', time.localtime(mine['noted']))}" if mine.get("noted") else ""
            out.append(f"  {root.name}: " + (f"left be: {why}" if why else ", ".join(WORDS[k] for k in want) + noted))
    try:
        err = json.loads((board.home() / "turbo-error.json").read_text())
        if time.time() - err["at"] < DAY:
            out.append(f"Last trouble, {time.strftime('%a %-I:%M %p', time.localtime(err['at']))}: {err['error']}")
    except (OSError, ValueError, KeyError):
        pass
    return "\n".join(out)
