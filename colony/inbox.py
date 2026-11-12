"""The Inbox (R160): what waits on the person, one item at a time, in an order that is built in.

It reads the one definition of what waits (board.waiting_items, as the moments the person answers) and keeps only
what the person does to the stack, in each project's .board/inbox.jsonl: an item put at the bottom (Not now), one set
aside until a date (Later), and when an item with no time of its own was first seen.

The order: an item with a deadline leads once inside its window (the day before, unless its agent said longer), the
nearest deadline first; then the rest, the one that has waited longest first. Not now orders an item as if it had
just arrived; a deadline brings it back to the top within the hour, so nothing is missed at the bottom of the stack.
There is no urgent flag and no choice of sort: the order is the same for everyone who looks.
"""
import re
import time
import urllib.parse
from pathlib import Path

from . import board, hours

RISE = 86400                 # a deadline leads the stack from this long before it, unless its gate says longer
BACK = 3600                  # a deadline item put at the bottom is back on top this soon, sooner as the deadline nears
SOONEST = 600


def clock():
    return board.epoch(board.now())


def stamp(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def moment(text, end=False):
    """A date or time an agent or the person wrote, as a timestamp on this machine's clock: HH:MM (today, or tomorrow
    once past), a local date-time (2026-11-13T16:00, or with a space), or a date alone (its start; its end for a
    deadline)."""
    from . import wakes
    text = str(text or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return time.mktime((y, mo, d, 23 if end else 0, 59 if end else 0, 0, 0, 0, -1))
        except (ValueError, OverflowError):
            raise ValueError(f"{text} isn't a date: write it as 2026-11-13.")
    return wakes.moment(text, now=clock())


def window(text):
    """How long before its deadline an item leads the stack: 2h, 3d, 7d; a day when not said."""
    from . import wakes
    return wakes.duration(text) if text else RISE


def state(root):
    """What the person did to the stack in one project: {key: when put last}, {key: set aside until}, {key: first
    seen}."""
    last, later, seen = {}, {}, {}
    for ev in board.read(root, "inbox.jsonl"):
        k = ev.get("key")
        if ev["type"] == "last":
            last[k] = board.epoch(ev["at"])
        elif ev["type"] == "later":
            later[k] = board.epoch(ev["until"])
        elif ev["type"] == "back":
            later.pop(k, None)
        elif ev["type"] == "seen":
            seen.setdefault(k, board.epoch(ev["at"]))
    return last, later, seen


def away(root):
    """What the person set aside until a date that has not come: {key: until}. board.waiting_items leaves these out,
    so every view agrees: off the list until then, back by itself."""
    if not (Path(root) / ".board" / "inbox.jsonl").exists():
        return {}
    now = clock()
    return {k: until for k, until in state(root)[1].items() if until > now}


def put_last(root, keys):
    """Not now: the item goes behind the others and comes round again. Nothing is answered or hidden."""
    for key in keys:
        board.append(root, "inbox.jsonl", {"type": "last", "key": key, "at": board.now()})


def until(text):
    """When Later ends: "tomorrow" or "week" (as the person's day begins), or a date."""
    text = str(text or "").strip().lower()
    now = clock()
    if text in ("", "tomorrow"):
        return hours.next_day(now)
    if text == "week":
        return hours.next_day(now + 6 * 86400)
    at = hours.next_day(moment(text) - 1)
    if at <= now:
        raise ValueError("Later needs a day after today.")
    return at


def put_later(root, keys, text):
    """Later: the item is off the person's list everywhere until the day they chose, then back by itself. Its agent
    hears quietly, on its next turn, so it neither waits on an answer today nor asks again."""
    at = until(text)
    waiting = {w["key"]: w for w in board.waiting_items(root)}
    for key in keys:
        if key in waiting:
            board.append(root, "inbox.jsonl", {"type": "later", "key": key, "until": stamp(at), "at": board.now()})
    said = [waiting[k]["summary"] for k in keys if k in waiting]
    if said:
        gate = next((waiting[k]["gate"]["id"] for k in keys if k in waiting and waiting[k]["kind"] == "gate"), None)
        board.add_note(root, {"gate": gate} if gate else None,
                       f"The person set this aside until {day(at)}: {board.short(said[0], 200)} It is off their list until "
                       "then and comes back by itself: don't ask it again meanwhile, and carry on with what doesn't "
                       "depend on it.", author="colony", quiet=True)
    return at


def bring_back(root, keys):
    for key in keys:
        board.append(root, "inbox.jsonl", {"type": "back", "key": key, "at": board.now()})


def day(ts):
    """A day as the person reads it: today's and tomorrow's by name, another's with its date."""
    now, t = time.localtime(clock()), time.localtime(ts)
    if (t.tm_year, t.tm_yday) == (now.tm_year, now.tm_yday):
        return "today " + hours.at_clock(ts)
    return time.strftime("%a %b %-d, %-I:%M %p", t)


def waited(since, now):
    secs = max(0, now - since)
    if secs < 3600:
        return "under an hour"
    if secs < 2 * 86400:
        return f"{round(secs / 3600)} hour{'s' * (round(secs / 3600) != 1)}"
    return f"{round(secs / 86400)} days"


def _since(root, w, seen, told, now):
    """When one waiting item became the person's; one with no time of its own, when the Inbox first saw it."""
    if w["kind"] == "gate":
        return board.due_since(root, w["gate"])
    if w["kind"] == "ask":
        return board.epoch(w["ask"]["at"])
    if w["kind"] == "verify" and told.get(w["item"]["id"], {}).get("at"):
        return board.epoch(told[w["item"]["id"]]["at"])
    if w["key"] not in seen:
        board.append(root, "inbox.jsonl", {"type": "seen", "key": w["key"], "at": board.now()})
        seen[w["key"]] = now
    return seen[w["key"]]


def rank(x, now):
    """Where an entry stands: (0, its deadline) once inside its window, else (1, when it joined the back of the
    stack)."""
    by, last = x["by"], x["last"]
    if by is not None and now >= by - x["rise"]:
        if not last or now >= last + min(BACK, max(SOONEST, (by - last) / 2)):
            return (0, by)
    return (1, max(x["since"], last))


def stack(reg=None):
    """Every moment waiting on the person, across the projects that are running, in the Inbox's order."""
    reg = reg or board.registry()
    now, out = clock(), []
    for pid, p, _ in board.arranged(board.projects(reg)):
        if not p.exists() or board.paused(p):
            continue
        groups = []
        for group in board.moments(p):                   # one item a card: sign-offs with no question beside them
            groups += [[w] for w in group] if all(w["kind"] == "verify" for w in group) else [group]   # come one by one
        if not groups:
            continue
        last, _, seen = state(p)
        told = board.ready_notes(p)
        for group in groups:
            gate = group[0]["gate"] if group[0]["kind"] == "gate" else {}
            out.append({"pid": pid, "root": p, "group": group, "keys": [w["key"] for w in group],
                        "since": min(_since(p, w, seen, told, now) for w in group),
                        "last": max((last.get(w["key"], 0) for w in group), default=0),
                        "by": board.epoch(gate["by"]) if gate.get("by") else None, "rise": gate.get("rise") or RISE})
    out.sort(key=lambda x: rank(x, now))
    return out


def set_aside(reg=None):
    """What is off the list until a date, soonest back first: the person's Later, and a gate its agent dated from a
    day to come. [(project, what, until, keys the person can bring back)]"""
    reg = reg or board.registry()
    now, out = clock(), []
    for pid, p, _ in board.arranged(board.projects(reg)):
        if not p.exists() or board.paused(p):
            continue
        gone = away(p)
        if gone:
            every = {w["key"]: w for w in board.waiting_items(p, shelved=True)}
            for key, at in gone.items():
                if key in every:
                    out.append((pid, p, every[key]["summary"], at, key))
        for g in board.gates(p):
            if not g["answer"] and g.get("not_yet"):
                out.append((pid, p, "has a question for a day to come: " + g["question"], board.epoch(g["not_yet"]), ""))
    return sorted(out, key=lambda x: x[3])


KIND = {"gate": "a decision", "ask": "a question", "checkpoint": "a version to review", "choice": "a choice in its console",
        "screen": "its console", "verify": "ready for your OK"}


def shown_for(pid, root, iid):
    """What the project put in its showcase for this item, newest first, each one tap away: the report or the demo
    made for the person is on the card, not somewhere to go and find."""
    from . import opener, showcase
    out = []
    for x in showcase.versions(root):
        if x.get("item") != iid or not x.get("link"):
            continue
        got = opener.entry_link(pid, root, x)
        if got:
            out.append(f"<a class='open' href='{board.e(got[0])}'{opener.NEW_TAB if got[1] else ''}>"
                       f"Open: {board.e(x['title'])}</a>")
    return "".join(out[:3])


def card(x, back="/inbox"):
    """One entry as the person meets it: whose it is, how long it has waited, its deadline, everything to judge it by,
    its answers, then the two ways to put it off."""
    e, now = board.e, clock()
    pid, p, group, w = x["pid"], x["root"], x["group"], x["group"][0]
    hidden = (f"<input type='hidden' name='p' value='{pid}'><input type='hidden' name='back' value='{e(back)}'>"
              f"<input type='hidden' name='key' value='{e(','.join(x['keys']))}'>")
    due = ""
    if x["by"] is not None:
        due = (f"<span class='due{' near' if now >= x['by'] - x['rise'] else ''}'>"
               f"{'Was due' if now > x['by'] else 'Due'} {e(day(x['by']))}</span>")
    head = (f"<div class='inhead'><a class='inproj' href='{board.project_url(p)}'>{e(board.name_of(p))}</a>"
            f"<span class='muted'>{e(KIND.get(w['kind'], w['kind']))} · waiting {e(waited(x['since'], now))}</span>{due}</div>")
    item = w["gate"].get("item") if w["kind"] == "gate" else w["item"]["id"] if w["kind"] == "verify" else None
    extra = ""
    if item:
        it = board.items(board.roadmap(p)).get(item)
        shown = shown_for(pid, p, item)
        extra = ("<div class='inabout'>" + (f"<a href='/item?{board.project_query(p)}&id={e(item)}'>About: "
                 f"{e(board.short(board.plain(it['text']), 110))}</a>" if it else "") + shown + "</div>")
    body = board.moment_html(pid, p, group, back, label=False, fold=False)
    foot = (f"<div class='infoot'><form method='post' action='/inbox/last'>{hidden}"
            "<button class='quiet' title='Send it to the bottom of the stack: it comes round again'>Not now</button></form>"
            f"<form method='post' action='/inbox/later' class='inlater'>{hidden}"
            "<button class='quiet' name='until' value='tomorrow' title='Off your list until tomorrow'>Later: tomorrow</button>"
            "<input type='date' name='date' aria-label='or until this day'>"
            "<button class='quiet' name='until' value='date'>until that day</button></form></div>")
    return f"<div class='card incard'>{head}{extra}{body}{foot}</div>"


def html(reg):
    """The Inbox's own part of its page: the count, the top card, and what is set aside."""
    e = board.e
    entries = stack(reg)
    aside = set_aside(reg)
    own = board.own_rows()
    out = [board.notices_html("/inbox")]
    if entries:
        n = len(entries)
        out.append(f"<p class='incount'><b>1 of {n}</b>" + (f" · then {n - 1} more" if n > 1 else "")
                   + " · <a href='/needs-you'>See all</a></p>")
        out.append(card(entries[0]))
    else:
        out.append("<p class='muted'>Nothing is waiting on you.</p>")
    if own:
        out.append(f"<p class='muted'>{own} from colony itself (a model change, a lesson to keep) "
                   "<a href='/needs-you'>in the list</a>.</p>")
    if aside:
        rows = "".join(
            f"<div class='inaside'><span>{e(board.name_of(p))} {e(board.short(what, 120))} "
            f"<span class='muted'>· back {e(day(at))}</span></span>"
            + (f"<form method='post' action='/inbox/back'><input type='hidden' name='p' value='{pid}'>"
               f"<input type='hidden' name='back' value='/inbox'><input type='hidden' name='key' value='{e(key)}'>"
               "<button class='quiet'>Bring back</button></form>" if key else "") + "</div>"
            for pid, p, what, at, key in aside)
        out.append(f"<details class='more' data-k='inaside'><summary>{len(aside)} set aside until a day to come</summary>{rows}</details>")
    return "".join(out)


def page(reg):
    return board.shell(reg, board.NEEDS_PAGE,
                       f"<h1>Inbox</h1><div id='inbox'>{html(reg)}</div>"
                       f"<script>{board.KEEP_CURRENT}if (!document.querySelector('.incard')) "
                       "keepCurrent(document.getElementById('inbox'), '/inbox/feed');</script>")
