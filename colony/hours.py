"""The clock as the person reads it, and their day: midnight to midnight on this machine's clock. The day paces each
project's page (the day's decisions numbered, what landed since an earlier day). Nothing here holds anything back
by the hour: active hours, which did, were taken out (R167)."""
import time


def clock():
    """The time now, as a timestamp (tests fix it)."""
    return time.time()


def _starts(now, after):
    """Midnight nearest `now` on one side: the first after it, or the last at or before it."""
    now = clock() if now is None else now
    t = time.localtime(now)
    for day in (range(3) if after else range(0, -3, -1)):   # mktime carries a day past the month's end
        at = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + day, 0, 0, 0, 0, 0, -1))
        if (at > now) if after else (at <= now):
            return at


def day_start(now=None):
    """When the person's day that `now` falls in began."""
    return _starts(now, after=False)


def next_day(now=None):
    """When the person's next day begins after `now`."""
    return _starts(now, after=True)


def at_clock(ts):
    """A moment as the person reads a clock: 7:30 AM."""
    return time.strftime("%-I:%M %p", time.localtime(ts))


def at_day(ts):
    """A moment on another day, as the person reads it: Sat 7:30 AM."""
    return time.strftime("%a %-I:%M %p", time.localtime(ts))
