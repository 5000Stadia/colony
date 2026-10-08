"""Turbo: when a program's weekly usage runs behind pace, colony turns its projects up so the week isn't wasted.

From day 2 of its window a program should have used its elapsed share of the week, aiming for 99% by the hour
before the reset. More than 5 points behind turns turbo on for that program's projects; catching up turns it off.
Each project ticks what turbo means for it (stronger models, going deeper on its queued work, research on a topic
the person typed), and only projects with queued work, or research ticked, are turned up.

It only ever adds to a project's work. Its note is quiet, and turbo itself wakes a console only at a moment it has
checked: idle, nothing typed, no one at it, nothing waiting on the person; a note that waits is let go once that
changes, and an agent that answers there's nothing worth doing hears no more until its roadmap changes shape.
Stronger models come through the idle reload, once a turbo episode, and leave without one; nothing is written into
any instructions. Off, it is nothing at all. It runs in the watcher's minute with no tokens; when a program's reading
is missing or old it stays off, and when the watcher can't keep it, it stands down.
"""
import hashlib
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
STALE = 12 * HOUR        # an older reading isn't trusted: turbo stays off (Claude's refreshes only while a console runs,
                         # so overnight it ages; an old reading only understates use, and the safe pause still guards)
FRESH = 15 * 60          # a turbo the watcher hasn't kept this long stands down
RAISE = 2                # positions toward Intelligence for a project that ticks stronger models
NUDGE = "[colony] You have an update from Colony."              # the watcher's own words for a colony note


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
    return " ".join(parts + ["Only work worth doing. If there's nothing worth doing, run `colony turbo --nothing` and stop."])


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
    """Work queued for this project's agent: an item of its own to do or under way in a milestone (never Later, nor
    a heading no version takes), and within the version under way where there is one."""
    from . import progress
    me, version, aside = str(Path(root).resolve()), progress.current(root), progress.unscheduled(root)
    return any(i["state"] in ("todo", "doing") and i.get("owner", me) == me and i["id"] not in aside
               and (not version or i["id"] in version["items"]) for i in board.items(board.roadmap(root)).values())


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
    a choice on its screen, a version or an item for their eye), or held for them: their pause, a hand-off, a context
    refresh, or a project working in versions that isn't continuing one now (one finished with no next released, a
    helper's item delivered). Turbo never buries what is in their hands, nor starts what they stopped."""
    from . import context, continuation, lead, progress
    if str(root) in usage.paused():
        return "paused at a usage limit"
    if board.waiting_items(root, snap):
        return "waiting on the person"
    if context.refreshing(root):
        return "refreshing its context"
    if lead.group(root):
        why = progress.hold(root, root)
        if why and (why != "no bounded version selected" or lead.info(root)["checkpoints"]):
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


def withdraw(root, nid, why):
    """A turbo note that never reached its agent is let go, so nothing hands it over after its moment."""
    board.append(root, "notes.jsonl", {"type": "addressed", "of": nid, "at": board.now(),
                                       "text": f"Let go by colony before it reached you ({why}): nothing to do."})


# ---------------------------------------------------------------- nothing worth doing

def shape(root):
    """What turbo's notes to a project are about, as a fingerprint: its roadmap's items (where each sits, what it
    says, its state, whose it is), the version under way, and the research topic the person typed. Rewording the
    vision or an item's description leaves it; adding, removing, re-stating or moving an item changes it."""
    from . import progress
    opts = options(root)
    items = [[i["milestone"], i["id"], i["state"], i["text"], i.get("owner")]
             for i in board.items(board.roadmap(root)).values()]
    version = (progress.current(root) or {}).get("items")
    topic = opts["turbo_topic"].strip() if opts["turbo_research"] else ""
    return hashlib.sha256(json.dumps([items, version, topic]).encode()).hexdigest()[:16]


def nothing_path(root):
    return Path(root) / ".board" / "turbo-nothing.json"


def said_nothing(root):
    """When the agent answered there's nothing worth doing (colony turbo --nothing), while its roadmap keeps the
    shape it had then; else None."""
    try:
        said = json.loads(nothing_path(root).read_text())
        return said["at"] if said["shape"] == shape(root) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def nothing(root, now=None):
    """An agent's answer to turbo's note, colony turbo --nothing: nothing worth doing. The note is answered, and turbo
    sends it no more until its roadmap changes shape; stronger models, which ask nothing of it, go on."""
    now = time.time() if now is None else now
    path = nothing_path(root)
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": now, "shape": shape(root)}))
    tmp.replace(path)
    for n in board.notes(root):
        if n["author"] == "colony" and n.get("kind") == "turbo" and not n["addressed_at"]:
            board.append(root, "notes.jsonl", {"type": "addressed", "of": n["id"], "at": board.now(),
                                               "text": "Nothing worth doing: no more from turbo until the roadmap changes."})


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
        if (selection.pin_of(root, role) or {}).get("model"):
            continue                                            # the person's pin is theirs, turbo or not
        ident = selection.key(family, role, selection.scope(root))
        chosen = selection.concrete(family, bench.role_pick(family, role, entries, balance=raised(base), ceiling=ceiling,
                                                            blocked=state["blocked"].get(ident, [])))
        if chosen and not selection.same(chosen, state["accepted"].get(ident)):
            out[role] = dict(selection.pair(chosen), why=f"Turbo: {intelligence.POSITIONS[raised(base)]} while "
                             f"{providers.get(family).label} has spare weekly capacity")
    return out


def pick(root, family, role):
    """Turbo's stronger pick for a project's seat while its program's turbo is on, or None (selection asks)."""
    rec = load().get(family) or {}
    return ((rec.get("projects") or {}).get(str(root)) or {}).get("boost", {}).get(role) if rec.get("on") else None


def release(state, roots, change):
    """Stronger models end without a reload: a console keeps what it runs until it restarts for a reason of its own,
    and its record moves on with them, so this alone never reloads it. Each project's part is its own: one's
    trouble leaves the others' as they should be."""
    from . import bench

    def fingerprint(r):
        try:
            return console.fingerprint(r)
        except Exception as err:
            trouble(err)
            return None
    was = {r: fingerprint(r) for r in roots if r.exists() and console.running(console.session_name(r))}
    change()
    save(state)
    for r in (r for r in roots if r.exists()):
        try:
            bench.write_helpers(r)
        except Exception as err:
            trouble(err)
        try:
            name = console.session_name(r)
            if was.get(r) and console.started(name) == was[r] and (now := fingerprint(r)):
                console.adopt(name, now)
        except Exception as err:
            trouble(err)


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
        p = (dict(on=False, why="turned off in Settings") if not s["turbo_by"].get(key, True)
             else dict(on=False, why="its program is off in Settings or not installed") if not providers.usable(prov)
             else pace(usage.reading(key), now, rec.get("on")))
        if not p["on"]:
            if rec.get("on"):
                try:
                    done += end(state, key, dict(p, at=now))
                except Exception as err:                # its record is off already: the rest goes on
                    trouble(err)
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
    """One project while its program's turbo is on: (what turbo does for it, what it did now). Its note is quiet:
    turbo wakes the console itself, only at a moment it has checked, and lets a note that still waits go once the
    project is held, no longer turned up, or its console stopped, so nothing else ever types it in later. An agent
    that said there's nothing worth doing hears no more until its roadmap changes shape."""
    from . import bench
    mine, done = rec["projects"].setdefault(str(root), {}), []
    opts = options(root)
    if mine.get("boost") and not opts["turbo_models"]:
        release(state, [root], lambda: mine.update(boost={}))      # unticked: they go, without a reload
    want = {} if str(root) in paused else wanted(root, opts)
    boost = want.get("models") and "boost" not in mine
    wake = ((want.get("deeper") or want.get("research")) and due(mine.get("noted"), now, rec["resets_at"])
            and not said_nothing(root))
    pending = unheard(root, mine.get("note"))
    if not (boost or wake or pending):
        return want, done
    snap = console.snapshot(root, lines=4)
    why = held(root, snap) if want else "no longer turned up"
    if pending and (why or snap["state"] == "off"):
        withdraw(root, mine.pop("note"), why or "its console was stopped")
        for k in ("noted", "woke"):
            mine.pop(k, None)                                   # it never heard: it may hear when it can
        pending = False
    if why:
        return want, done
    if boost:
        mine["boost"] = picks(root)                             # once a turbo episode, kept until it ends
        save(state)
        if mine["boost"]:
            bench.write_helpers(root)                           # with the main pick: one idle reload brings both
            done.append((root, "stronger models"))
    if ((pending and not mine.get("woke")) or (wake and not pending)) and ready(root, snap) and settled(root):
        if not pending:
            n = board.add_note(root, None, note(label, rec["resets_at"], want.get("deeper"), want.get("research", "")),
                               author="colony", quiet=True, kind="turbo")
            mine.update(noted=now, note=n["id"])
            done.append((root, "noted"))
        # a draft that appeared just now keeps it for the next free moment (once woken, never again), or its next turn
        mine["woke"] = console.type_into(console.session_name(root), NUDGE)
    return want, done


def end(state, key, off):
    """Turbo ends for a program: stronger models leave without a reload, a note that never reached its project is let
    go, and the monitor's look, if it hasn't happened yet, with them."""
    from . import monitor
    mine = {Path(r): m for r, m in ((state.get(key) or {}).get("projects") or {}).items()}
    release(state, [r for r, m in mine.items() if m.get("boost")], lambda: state.update({key: off}))
    for r, m in mine.items():
        try:
            if m.get("note") and r.exists() and unheard(r, m["note"]):
                withdraw(r, m["note"], "turbo ended")
        except Exception as err:
            trouble(err)
    monitor.unqueue(lambda text: text.startswith(f"Turbo is on for {providers.get(key).label} "))
    return [(key, "off")]


def stand_down(why, stale=False, now=None):
    """Turbo stands down when the watcher can't keep it: the monitor off (turbo runs with it), the safe pause unable to
    read usage, or turbo's own trouble lasting (stale: only a turbo not kept for 15 minutes). Every program's turbo
    ends as catching up would end it, and says why. Never raises."""
    now = time.time() if now is None else now
    try:
        state = load()
        for key in providers.PROVIDERS:
            rec = state.get(key) or {}
            if stale and not (rec.get("on") and now - rec.get("at", 0) > FRESH):
                continue
            off = dict(on=False, why=why, down=why, at=now)
            try:
                if rec.get("on"):
                    end(state, key, off)
            except Exception as err:
                trouble(err)
            state[key] = off
        save(state)
    except Exception as err:
        trouble(err)


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
    rec = load().get(key) or {}
    if rec.get("down") or (rec and now - rec.get("at", 0) > FRESH):
        return f"{head}turbo isn't running ({rec.get('down') or 'the watcher that runs it has stopped'})"
    p = pace(usage.reading(key), now, rec.get("on"))
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
    said = (want.get("deeper") or want.get("research")) and said_nothing(root)
    return (f"Turbo is on for {label} until it resets {usage.when(rec['resets_at'])}: " + ", ".join(w for w in what if w)
            + (f"; left be for now: {why}." if why
               else "; its agent found nothing worth doing, so no note until its roadmap changes." if said else "."))


WORDS = {"models": "stronger models", "deeper": "going deeper", "research": "research"}


def stamp(t):
    return time.strftime("%a %-I:%M %p", time.localtime(t))


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
            snap = console.snapshot(root, lines=4)
            why = "no queued work or research topic" if not want else held(root, snap)
            mine = (rec.get("projects") or {}).get(str(root)) or {}
            nudged = want.get("deeper") or want.get("research")
            said = nudged and said_nothing(root)
            noted = (f"; it found nothing worth doing ({stamp(said)}), so no note until its roadmap changes" if said
                     else f"; last note {stamp(mine['noted'])}" if mine.get("noted")
                     else f"; its note waits for its console, now {snap['state']}, to sit idle and untouched"
                     if nudged and not ready(root, snap) else "")
            out.append(f"  {root.name}: " + (f"left be: {why}" if why else ", ".join(WORDS[k] for k in want) + noted))
    try:
        err = json.loads((board.home() / "turbo-error.json").read_text())
        if time.time() - err["at"] < DAY:
            out.append(f"Last trouble, {stamp(err['at'])}: {err['error']}")
    except (OSError, ValueError, KeyError):
        pass
    return "\n".join(out)
