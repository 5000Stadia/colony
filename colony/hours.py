"""Active hours: when the person is around, on this machine's clock (07:30-23:45 unless they set their own; off:
always). A question put to them outside those hours only interrupts or waits unseen, so then agents keep on with
what doesn't depend on it, the gates they open are held, and what was held reaches the person together when the
hours begin. Work goes on at any hour; the person's own words, and colony's safety notices, are never held.

    colony settings active_hours 07:30-23:45     a range may cross midnight (22:00-06:00); off: always
"""
import re
import time

DEFAULT = "07:30-23:45"
RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*[-–—]\s*(\d{1,2}):(\d{2})")
UNREAD = ("Active hours are a start and an end on a 24-hour clock, HH:MM-HH:MM (07:30-23:45, or 22:00-06:00 across "
          "midnight), or off for always.")


def parse(text):
    """The hours as (start, end) in minutes after midnight, or None when off. ValueError for anything else."""
    text = str(text or "").strip().lower()
    if text == "off":
        return None
    m = RANGE.fullmatch(text)
    if not m:
        raise ValueError(UNREAD)
    h1, m1, h2, m2 = map(int, m.groups())
    if not all((h < 24 and mi < 60) or (h, mi) == (24, 0) for h, mi in ((h1, m1), (h2, m2))):   # 24:00 is midnight
        raise ValueError(UNREAD)
    start, end = (h1 * 60 + m1) % 1440, (h2 * 60 + m2) % 1440
    if start == end:
        raise ValueError(UNREAD + " Its start and end can't be the same.")
    return start, end


def normal(text):
    """The hours as they are kept: HH:MM-HH:MM, or off."""
    span = parse(text)
    return "off" if span is None else "-".join(f"{t // 60:02d}:{t % 60:02d}" for t in span)


def setting():
    from . import board
    return board.registry()["settings"].get("active_hours", DEFAULT)


def span(hours=None):
    """The hours in force (the person's setting unless given): (start, end) minutes, or None when off. A setting
    edited by hand into something unreadable counts as colony's own."""
    try:
        return parse(setting() if hours is None else hours)
    except ValueError:
        return parse(DEFAULT)


def clock():
    """The time now, as a timestamp (tests fix it)."""
    return time.time()


def active(now=None, hours=None):
    """Whether the person's active hours hold at `now` (a timestamp; this moment unless given). Always, when off."""
    s = span(hours)
    if s is None:
        return True
    t = time.localtime(clock() if now is None else now)
    minute, (start, end) = t.tm_hour * 60 + t.tm_min, s
    return start <= minute < end if start < end else minute >= start or minute < end


def next_start(now=None, hours=None):
    """When the active hours next begin after `now`, as a timestamp; None when they're off."""
    s = span(hours)
    if s is None:
        return None
    now = clock() if now is None else now
    t = time.localtime(now)
    for day in range(3):                    # today's start, else tomorrow's (mktime carries a day past the month's end)
        at = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + day, s[0] // 60, s[0] % 60, 0, 0, 0, -1))
        if at > now:
            return at


def at_clock(ts):
    """A moment as the person reads a clock: 7:30 AM."""
    return time.strftime("%-I:%M %p", time.localtime(ts))


def held_until(now=None, hours=None):
    """For a question to the person made at `now`: outside their active hours, when it should reach them, on the
    board's clock ("...Z"); inside them, or with hours off, None."""
    now = clock() if now is None else now
    if active(now, hours):
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(next_start(now, hours)))


def notice(now=None, hours=None):
    """The line an agent hears with each turn outside the person's active hours; nothing inside them."""
    now = clock() if now is None else now
    hours = setting() if hours is None else hours
    if active(now, hours):
        return ""
    return (f"It's outside the person's active hours (back at {at_clock(next_start(now, hours))}). Don't end your "
            "turn on a question to them: keep on with what doesn't depend on the answer, and record what you need "
            "from them with colony gate; they'll see it together when their hours begin.")
